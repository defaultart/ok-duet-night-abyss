"""Exclusive keyboard-route task, with the existing mission UI adapter."""
import copy
import json
import logging
import time
import uuid
import winsound
from queue import Empty, Queue
from threading import Event, Lock, Thread

import cv2
import numpy as np
import win32api
import win32con
import win32gui
import win32process
from ok import Box, TaskDisabledException
from qfluentwidgets import FluentIcon

from src.routes.core import (Interrupted, RECORDABLE_KEYS, Player, Recorder, Result,
                             RouteStore, normalize_key, validate_route)
from src.tasks.CommissionsTask import CommissionsTask
from src.compat.route_capture_bootstrap import install_route_capture_bootstrap


def write_image(path, frame):
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, data = cv2.imencode('.png', frame)
    if not ok:
        raise ValueError('无法保存截图')
    data.tofile(str(path))


def read_image(path):
    frame = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        raise ValueError(f'无法读取截图：{path}')
    return frame


def crop(frame, rect):
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = rect
    return frame[int(y1*h):max(int(y1*h)+1, int(y2*h)), int(x1*w):max(int(x1*w)+1, int(x2*w))]


MOUSE_KEYS = {
    'mouse_left': 0x01,
    'mouse_right': 0x02,
    'mouse_middle': 0x04,
    'mouse_x1': 0x05,
    'mouse_x2': 0x06,
}
KEY_LABELS = {
    'mouse_left': '鼠标左键', 'mouse_right': '鼠标右键', 'mouse_middle': '鼠标中键',
    'mouse_x1': '鼠标侧键1', 'mouse_x2': '鼠标侧键2', 'space': '空格',
    'lshift': '左Shift', 'rshift': '右Shift', 'lctrl': '左Ctrl', 'rctrl': '右Ctrl',
    'lalt': '左Alt', 'ralt': '右Alt', 'up': '上', 'down': '下', 'left': '左', 'right': '右',
}
FOCUS_LOSS_GRACE = 0.3
# The hotkey that armed recording is still down when the poller starts.
# Ignore that same press so it cannot immediately pause the new recording.
ARMING_KEY_SUPPRESS = 0.45


def key_label(name):
    return KEY_LABELS.get(name, str(name).upper())


def vk_of(name):
    if name in MOUSE_KEYS:
        return MOUSE_KEYS[name]
    if name in ('up', 'down', 'left', 'right', 'space', 'lshift', 'rshift', 'lctrl', 'rctrl', 'lalt', 'ralt'):
        return {'up': 0x26, 'down': 0x28, 'left': 0x25, 'right': 0x27, 'space': 0x20,
                'lshift': 0xA0, 'rshift': 0xA1, 'lctrl': 0xA2, 'rctrl': 0xA3,
                'lalt': 0xA4, 'ralt': 0xA5}[name]
    if isinstance(name, str) and len(name) == 1 and name.isalnum():
        return ord(name.upper())
    if isinstance(name, str) and name.startswith('f') and name[1:].isdigit():
        number = int(name[1:])
        if 1 <= number <= 12:
            return 0x6F + number
    return None


def watched_names(allowed, hotkeys):
    names = set(allowed) | set(hotkeys) | set(MOUSE_KEYS)
    names.update(chr(code) for code in range(ord('a'), ord('z') + 1))
    names.update(str(digit) for digit in range(10))
    names.update(f'f{number}' for number in range(1, 13))
    return [name for name in sorted(names) if vk_of(name) is not None]


def read_key_down(name):
    vk = vk_of(name)
    if vk is None:
        return False
    return bool(win32api.GetAsyncKeyState(vk) & 0x8000)


def same_game_foreground(foreground_hwnd, captured_hwnds, foreground_pid=0, captured_pid=0):
    if foreground_hwnd and int(foreground_hwnd) in {int(hwnd) for hwnd in captured_hwnds if hwnd}:
        return True
    return bool(foreground_pid and captured_pid and int(foreground_pid) == int(captured_pid))


def describe_focus(foreground_hwnd, foreground_title, captured_hwnd, captured_title):
    return (f'前台窗口「{foreground_title or "无"}」句柄 {foreground_hwnd}，'
            f'捕获窗口「{captured_title or "无"}」句柄 {captured_hwnd}')


def win32security_token(process_handle):
    import win32security
    return win32security.OpenProcessToken(process_handle, win32con.TOKEN_QUERY)


def token_is_elevated(token):
    import win32security
    value = win32security.GetTokenInformation(token, win32security.TokenElevation)
    if isinstance(value, (tuple, list)):
        value = value[0]
    return bool(value)


def elevated_game_warning(tool_is_admin, game_is_elevated):
    if tool_is_admin or not game_is_elevated:
        return ''
    return '游戏进程权限高于本程序。若游戏里仍然读不到按键，请用相同权限启动本程序。'


def startup_expired(created_at, now, in_run, entered_run, limit=15):
    if in_run or entered_run:
        return False
    return now - created_at > limit


def arm_route_task(task):
    """Queue the hidden route task, then wake a paused executor."""
    task.enable()
    task.start()


class FocusGate:
    """Ignore a brief focus flicker so one missed sample does not void a segment."""

    def __init__(self, grace=FOCUS_LOSS_GRACE, clock=time.perf_counter):
        self.grace = grace
        self.clock = clock
        self.lost_since = None
        self.focused = True

    def update(self, focused):
        if focused:
            self.lost_since = None
            returned = not self.focused
            self.focused = True
            return True, 'back' if returned else None
        now = self.clock()
        if self.lost_since is None:
            self.lost_since = now
        if now - self.lost_since < self.grace:
            return self.focused, None
        lost = self.focused
        self.focused = False
        return False, 'lost' if lost else None


class KeyPoller:
    """Read key state outside a low-level hook so a focused game still reports keys."""

    def __init__(self, names, read_down, on_change, on_ready=None, interval=0.01):
        self.names = list(names)
        self.read_down = read_down
        self.on_change = on_change
        self.on_ready = on_ready or (lambda held: None)
        self.interval = interval
        self._stop = Event()
        self._thread = None
        self.ready = Event()

    def start(self):
        self._thread = Thread(target=self._run, name='KeyboardRoutePoll', daemon=True)
        self._thread.start()

    def wait_ready(self, timeout=2):
        if not self.ready.wait(timeout):
            raise ValueError('键盘轮询未能启动')

    def is_alive(self):
        return bool(self._thread and self._thread.is_alive())

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)

    def _run(self):
        state = {}
        try:
            for name in self.names:
                try:
                    state[name] = bool(self.read_down(name))
                except Exception:
                    state[name] = False
            self.on_ready(tuple(name for name, down in state.items() if down))
        finally:
            self.ready.set()
        while not self._stop.wait(self.interval):
            for name in self.names:
                try:
                    down = bool(self.read_down(name))
                except Exception:
                    continue
                if down != state.get(name, False):
                    state[name] = down
                    try:
                        self.on_change(name, down)
                    except Exception:
                        continue


def make_route_capture(method):
    """Give the route worker its own MFC/GDI resources, never borrowed ones.

    The executor's capture object can already contain DCs created by its device
    checks or an earlier session. Sharing that object is unsafe even if all
    frames in the new session use one thread. Only window metadata is shared.
    Other capture backends retain their existing get_frame API and ownership.
    """
    from ok.device.capture_methods.bitblt import BitBltCaptureMethod, ForegroundBitBltCaptureMethod
    if isinstance(method, (BitBltCaptureMethod, ForegroundBitBltCaptureMethod)):
        private = type(method)(method.hwnd_window)
        private.exit_event = method.exit_event
        return private
    return method


class RouteCaptureWorker:
    """Create, use and close private GDI captures on the same worker thread."""

    def __init__(self, capture_factory=None):
        from src.compat.window_client_area import install_printwindow_handle_fix
        install_printwindow_handle_fix()
        self._requests = Queue()
        self._busy = False
        self._stopping = False
        self._capture_factory = capture_factory or make_route_capture
        self._lock = Lock()
        self.thread = Thread(target=self._run, name='KeyboardRouteCapture', daemon=True)
        self.thread.start()

    @property
    def busy(self):
        with self._lock:
            return self._busy

    def submit(self, method, results):
        with self._lock:
            if self._stopping:
                raise ValueError('上一轮截图线程仍在退出，请稍后重试')
            if self._busy:
                raise ValueError('上一次游戏取帧仍未返回，请在捕获页面切换截图方式后重试')
            self._busy = True
            self._requests.put((method, results))

    def _run(self):
        original = capture = None

        def release():
            nonlocal original, capture
            previous, owner = capture, original
            capture = original = None
            if previous is not None and previous is not owner:
                try:
                    previous.close()
                except Exception:
                    logging.getLogger(__name__).exception('路线私有截图资源释放失败')

        try:
            while True:
                item = self._requests.get()
                if item is None:
                    return
                method, results = item
                try:
                    if method is not original:
                        release()
                        capture = self._capture_factory(method)
                        original = method
                    outcome = (True, capture.get_frame())
                except Exception as error:
                    outcome = (False, error)
                with self._lock:
                    self._busy = False
                results.put(outcome)
        finally:
            release()

    def stop(self):
        with self._lock:
            if not self._stopping:
                self._stopping = True
                self._requests.put(None)
        self.thread.join(timeout=0.5)


def node_frame(io):
    """Capture a checkpoint image without committing the segment first."""
    try:
        frame = io.frame()
    except Interrupted:
        raise
    except Exception as error:
        raise ValueError(f'节点截图失败，录制还在，请再按插节点键：{error}') from error
    if frame is None or not getattr(frame, 'size', 0):
        raise ValueError('节点截图失败，录制还在，请再按插节点键')
    return frame


def route_note(task, message, phase=None, status=True):
    logger = getattr(task, 'logger', None)
    if logger is not None and hasattr(logger, 'info'):
        logger.info(message)
    session = getattr(task, 'session', None)
    if session is None:
        return
    for signal_name, payload, enabled in (
            ('log', message, True),
            ('status', message, status),
            ('phase', phase, phase is not None)):
        signal = getattr(session, signal_name, None)
        emit = getattr(signal, 'emit', None)
        if enabled and emit is not None:
            emit(payload)


def observe_focus(task, gate, recorder):
    focused, change = gate.update(task.foreground())
    if change == 'lost':
        diagnostic = task.focus_diagnostic() if hasattr(task, 'focus_diagnostic') else ''
        if recorder.started is not None:
            recorder.pause(lost_focus=True)
            extra = '当前段需要重录。' if recorder.invalid else '回到游戏后按开始热键继续。'
            route_note(task, f'失焦已暂停。{extra}{diagnostic}', phase='pause')
            recording_feedback('pause')
        else:
            route_note(task, f'游戏不在前台。{diagnostic}', phase='pause')
    elif change == 'back':
        phase = 'record' if recorder.started is not None else 'listen'
        route_note(task, '游戏回到前台，按开始热键继续', phase=phase)
    return focused


def pull_seed(commands):
    pending, seeded = [], None
    while True:
        try:
            command = commands.get_nowait()
        except Empty:
            break
        if command and command[0] == 'seed' and seeded is None:
            seeded = command[1]
        else:
            pending.append(command)
    for command in pending:
        commands.put(command)
    return seeded


def recording_feedback(kind='ok'):
    # Distinct tones stay audible while the editor is behind the game.
    def play():
        try:
            if kind == 'start':
                winsound.Beep(880, 90)
                winsound.Beep(1175, 120)
            elif kind == 'pause':
                winsound.Beep(520, 110)
            elif kind == 'save':
                winsound.Beep(784, 70)
                winsound.Beep(1046, 140)
            elif kind == 'error':
                winsound.MessageBeep(winsound.MB_ICONHAND)
            elif kind == 'ready':
                winsound.MessageBeep(winsound.MB_ICONASTERISK)
            else:
                winsound.MessageBeep(winsound.MB_OK)
        except RuntimeError:
            pass

    Thread(target=play, name='KeyboardRouteBeep', daemon=True).start()


class TaskAdapter:
    def __init__(self, task):
        self.task = task
        self.templates = {}

    def check(self):
        self.task.check_route()

    def sleep(self, seconds):
        time.sleep(seconds)

    def status(self, text):
        self.task.session.status.emit(text)

    def key_down(self, key):
        self.check()
        self.task.executor.interaction.send_key_down(key)

    def key_up(self, key):
        # Cleanup must work even after the task was disabled or the window lost focus.
        self.task.executor.interaction.send_key_up(key)

    def frame(self):
        self.check()
        self.task.next_frame()
        self.check()
        return self.task.frame.copy()

    def verify_environment(self, route):
        h, w = self.frame().shape[:2]
        if [w, h] != route['resolution'] or dict(self.task.key_config) != route['key_config']:
            raise ValueError('分辨率或游戏键位与录制时不同，请恢复设置或重新录制')

    def match(self, frame, route_id, rule):
        self.check()
        roi = crop(frame, rule['rect'])
        if rule['kind'] == 'ocr':
            h, w = frame.shape[:2]
            x1, y1, x2, y2 = rule['rect']
            box = Box(int(w*x1), int(h*y1), max(1, int(w*(x2-x1))), max(1, int(h*(y2-y1))))
            texts = self.task.ocr(frame=frame, box=box)
            text = ''.join(getattr(item, 'name', '') for item in (texts or []))
            self.check()
            return all(word in text for word in rule['keywords']), text
        path = self.task.store.asset(route_id, rule['template'])
        if str(path) not in self.templates:
            self.templates[str(path)] = read_image(path)
        template = self.templates[str(path)]
        if roi.shape[:2] != template.shape[:2]:
            return False, '截图区域尺寸与模板不同'
        # Same-position comparison also handles flat UI backgrounds without the
        # constant-image false positives of normalized correlation.
        score = 1 - float(np.mean(cv2.absdiff(roi, template))) / 255
        self.check()
        return score >= rule.get('threshold', .9), score

    def failure(self, reason, observation):
        folder = self.task.store.root / 'failures' / uuid.uuid4().hex
        folder.mkdir(parents=True, exist_ok=True)
        write_image(folder / 'screen.png', self.frame())
        (folder / 'failure.json').write_text(json.dumps(
            {'reason': reason, 'observation': observation}, ensure_ascii=False, indent=2), encoding='utf-8')
        self.status(f'{reason}，诊断已保存至 {folder}')


class KeyboardRouteTask(CommissionsTask):
    _route_owns_capture = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        install_route_capture_bootstrap(type(self.executor))
        self._route_in_run = False
        self.name = '路线录制与回放'
        self.description = '请在「键盘路线」页面创建、录制和运行路线'
        self.group_name = '路线工具'
        self.icon = FluentIcon.FLAG
        self.visible = False
        self.enable_fidget_action = False
        self.sleep_check_interval = -1
        self.session = None
        self.store = RouteStore()
        self.guard = False

    def captured_hwnds(self):
        window = self.hwnd
        if not window:
            return []
        found = []
        for attr in ('hwnd', 'top_hwnd'):
            value = getattr(window, attr, None)
            if value:
                found.append(int(value))
        for item in getattr(window, 'hwnds', []) or []:
            if item and item[0]:
                found.append(int(item[0]))
        return found

    def foreground(self):
        window = self.hwnd
        if not window:
            return False
        try:
            foreground_hwnd = int(win32gui.GetForegroundWindow() or 0)
            captured = self.captured_hwnds()
            foreground_pid = captured_pid = 0
            if foreground_hwnd:
                _, foreground_pid = win32process.GetWindowThreadProcessId(foreground_hwnd)
            game_hwnd = next((hwnd for hwnd in captured if hwnd), 0)
            if game_hwnd:
                _, captured_pid = win32process.GetWindowThreadProcessId(game_hwnd)
            if same_game_foreground(foreground_hwnd, captured, foreground_pid, captured_pid):
                return True
            return bool(getattr(window, 'is_foreground', lambda: False)())
        except Exception:
            return bool(getattr(window, 'is_foreground', lambda: False)())

    def focus_diagnostic(self):
        try:
            foreground_hwnd = int(win32gui.GetForegroundWindow() or 0)
            captured = next((hwnd for hwnd in self.captured_hwnds() if hwnd), 0)
            foreground_title = win32gui.GetWindowText(foreground_hwnd) if foreground_hwnd else ''
            captured_title = win32gui.GetWindowText(captured) if captured else ''
            return describe_focus(foreground_hwnd, foreground_title, captured, captured_title)
        except Exception as error:
            return f'无法读取前台窗口：{error}'

    def privilege_warning(self):
        try:
            from ok.util.process import is_admin
            captured = next((hwnd for hwnd in self.captured_hwnds() if hwnd), 0)
            if not captured:
                return ''
            _, pid = win32process.GetWindowThreadProcessId(captured)
            if not pid:
                return ''
            handle = win32api.OpenProcess(0x1000, False, pid)
            try:
                token = win32security_token(handle)
                try:
                    elevated = token_is_elevated(token)
                finally:
                    win32api.CloseHandle(token)
            finally:
                win32api.CloseHandle(handle)
            return elevated_game_warning(bool(is_admin()), elevated)
        except Exception:
            return ''

    def note(self, message, phase=None, status=True):
        route_note(self, message, phase, status)

    def check_route(self, require_focus=True):
        if self.session.stopped.is_set() or not self.enabled or self.paused or self.executor.paused or self.executor.exit_event.is_set():
            raise Interrupted('已停止')
        if require_focus and not self.foreground():
            raise Interrupted('游戏窗口失去焦点，已停止；请回到起点再试')

    def next_frame(self):
        # The waiter must keep polling check_route so Stop can abandon a hung
        # capture. Only the session worker calls get_frame, so GDI objects stay
        # on one thread.
        method = getattr(self.executor, 'method', None)
        if method is None:
            raise ValueError('游戏截图未连接，请先在捕获页面选择游戏窗口')
        worker = getattr(self, '_route_capture_worker', None)
        if worker is None or not worker.thread.is_alive():
            worker = RouteCaptureWorker()
            self._route_capture_worker = worker
        results = Queue(maxsize=1)
        worker.submit(method, results)
        deadline = time.perf_counter() + 6
        while time.perf_counter() < deadline:
            self.check_route()
            try:
                success, frame = results.get(timeout=.02)
            except Empty:
                continue
            if not success:
                raise ValueError(f'游戏取帧失败：{frame}')
            if frame is not None and getattr(frame, 'size', 0):
                self.executor._frame = frame
                self.executor._last_frame_time = time.time()
                return frame
            raise ValueError('未获取到新的游戏画面，请切换截图方式后重试')
        raise ValueError('游戏取帧超时，请切换截图方式后重试')

    @property
    def frame(self):
        self.check_route()
        frame = self.executor._frame
        return self.next_frame() if frame is None else frame

    def wait_until(self, condition, time_out=0, pre_action=None, post_action=None,
                   settle_time=-1, raise_if_not_found=False):
        deadline = time.perf_counter() + (time_out or 15)
        settled = None
        while time.perf_counter() < deadline:
            self.check_route()
            if pre_action:
                pre_action()
            self.next_frame()
            result = condition()
            if result:
                if settle_time <= 0:
                    return result
                if settled is None:
                    settled = time.perf_counter()
                if time.perf_counter() - settled >= settle_time:
                    return result
            else:
                settled = None
                if post_action:
                    post_action()
            self.sleep(.02)
        if raise_if_not_found:
            raise ValueError('等待副本界面超时，已停止')
        return None

    def sleep(self, seconds):
        if not self.guard:
            return super().sleep(seconds)
        deadline = time.perf_counter() + max(0, seconds)
        while time.perf_counter() < deadline:
            self.check_route()
            time.sleep(min(.02, max(0, deadline - time.perf_counter())))

    def click(self, *args, **kwargs):
        if self.guard:
            self.check_route()
        return super().click(*args, **kwargs)

    def send_key(self, *args, **kwargs):
        if self.guard:
            self.check_route()
        return super().send_key(*args, **kwargs)

    def run(self):
        if self.session is None:
            self.log_info_notify(self.description)
            return
        session = self.session
        self._route_in_run = True
        session.entered_run = True
        self.note(f'路线会话开始：{session.operation}', phase='listen')
        enabled_triggers = [task for task in self.executor.trigger_tasks if task.enabled]
        fidget_states = [(task, task.set_fidget_actions_paused(True))
                         for task in self.executor.onetime_tasks + self.executor.trigger_tasks
                         if hasattr(task, 'fidget_params')]
        player, monitor = Player(TaskAdapter(self)), Event()
        result, detail = Result.ERROR, ''
        try:
            for task in enabled_triggers:
                task.disable()
                if hasattr(task, 'try_disconnect_listener'):
                    task.try_disconnect_listener()
            # Deliberately bypass DNAOneTimeTask.run(): it enables fidget actions.
            if hasattr(self.executor.interaction, 'activate'):
                self.executor.interaction.activate()
            # Let any already-running fidget keystroke finish before capturing
            # or recording; its worker observes the pause flag on its next tick.
            time.sleep(1)
            deadline = time.perf_counter() + 15
            self.note(f'等待游戏窗口在前台，请切回游戏… {self.focus_diagnostic()}', phase='listen')
            while not self.foreground():
                self.check_route(require_focus=False)
                if time.perf_counter() >= deadline:
                    raise ValueError(f'等待游戏前台超时，请切回游戏后重试。{self.focus_diagnostic()}')
                time.sleep(.02)
            self.check_route()

            def watch():
                while not monitor.wait(.02):
                    try:
                        self.check_route(require_focus=session.operation != 'record')
                    except Interrupted:
                        session.stop()
                        player.release()
                        return

            watcher = Thread(target=watch, name='KeyboardRouteCleanup', daemon=True)
            watcher.start()
            if session.operation == 'snapshot':
                session.image.emit('map', player.io.frame(), session.route, None)
                result = Result.SUCCESS
            elif session.operation == 'record':
                self.record(player.io)
                result = Result.SUCCESS
            else:
                self.guard = True
                result = self.play_routes(player)
        except (Interrupted, TaskDisabledException) as error:
            result, detail = Result.STOPPED, str(error) or '已停止'
        except Exception as error:
            detail = str(error)
            self.log_error(detail)
            self.note(f'路线会话失败：{detail}', phase='fail')
            recording_feedback('error')
        finally:
            monitor.set()
            if 'watcher' in locals():
                watcher.join(timeout=1)
            try:
                player.release()
            finally:
                self.guard = False
                worker = getattr(self, '_route_capture_worker', None)
                if worker is not None:
                    worker.stop()
                for task in enabled_triggers:
                    task.enable()
                for task, paused in fidget_states:
                    task.set_fidget_actions_paused(paused)
                session.finished.emit(result.value, detail)
                self.session = None
                self._route_in_run = False

    def record(self, io):
        session, route = self.session, copy.deepcopy(self.session.route)
        allowed = RECORDABLE_KEYS
        recorder = Recorder(allowed)
        controls = dict(zip(session.hotkeys, ('toggle', 'node', 'finish')))
        physical = set()
        gate = FocusGate()
        suppress_start_until = 0.0
        # Re-recording a single segment preserves its existing checkpoint.
        replacement = session.segment
        checkpoint = route['segments'][replacement].get('checkpoint') if replacement is not None else None
        route['key_config'] = dict(self.key_config)
        environment_checked = False

        def enqueue(key, down):
            session.commands.put(('key', normalize_key(key), down, time.perf_counter()))

        def on_ready(already_down):
            session.commands.put(('seed', tuple(already_down)))

        poller = KeyPoller(watched_names(allowed, session.hotkeys), read_key_down, enqueue, on_ready)
        poller.start()
        try:
            poller.wait_ready()
            seeded = pull_seed(session.commands)
            if seeded:
                physical.update(seeded)
            warning = self.privilege_warning() if hasattr(self, 'privilege_warning') else ''
            if warning:
                route_note(self, warning, phase='listen')
            route_note(self, f'键盘轮询已启动。按 {session.hotkeys[0].upper()} 开始，'
                       f'{session.hotkeys[1].upper()} 插节点，{session.hotkeys[2].upper()} 保存', phase='listen')
            recording_feedback('ready')
            while True:
                self.check_route(require_focus=False)
                if not poller.is_alive():
                    raise ValueError('键盘轮询已停止，请重新启动录制')
                try:
                    command = session.commands.get(timeout=.02)
                except Empty:
                    observe_focus(self, gate, recorder)
                    continue
                action = command[0]
                now = time.perf_counter()
                from_key = False
                try:
                    if action == 'seed':
                        physical.update(command[1])
                        continue
                    if action == 'key':
                        _, key, down, now = command[:4]
                        from_key = True
                        label, verb = key_label(key), '按下' if down else '松开'
                        if down:
                            physical.add(key)
                        else:
                            physical.discard(key)
                        if str(key).startswith('mouse_'):
                            if recorder.started is not None and observe_focus(self, gate, recorder):
                                route_note(self, f'[按键] {label} {verb} → 已忽略：不录鼠标', status=False)
                            continue
                        if not observe_focus(self, gate, recorder):
                            route_note(self, f'[按键] {label} {verb} → 已忽略：游戏不在前台', status=False)
                            continue
                        if key not in controls:
                            before = len(recorder.events)
                            recorder.feed(key, down, now)
                            if len(recorder.events) > before:
                                route_note(self, f'[按键] {label} {verb} → 已记入（第 {len(recorder.events)} 个事件）', status=False)
                            else:
                                if recorder.started is None:
                                    reason = '录制尚未开始'
                                elif key not in allowed:
                                    reason = '不支持的录制按键'
                                else:
                                    reason = '状态没有变化'
                                route_note(self, f'[按键] {label} {verb} → 已忽略：{reason}', status=False)
                            continue
                        if not down:
                            route_note(self, f'[按键] {label} 松开 → 控制键，不写入路线', status=False)
                            continue
                        if key == session.hotkeys[0] and time.perf_counter() < suppress_start_until:
                            route_note(self, f'[按键] {label} 按下 → 已忽略：这是启动录制的那一下', status=False)
                            continue
                        route_note(self, f'[按键] {label} 按下 → 控制键生效')
                        action = controls[key]
                    if action == 'reset_segment':
                        recorder = Recorder(allowed)
                        route_note(self, '当前未保存段已清空，回到起点按开始热键重录', phase='listen')
                    elif action == 'toggle':
                        if not from_key:
                            suppress_start_until = time.perf_counter() + ARMING_KEY_SUPPRESS
                        self.check_route()
                        if recorder.started is not None:
                            recorder.pause(now)
                            route_note(self, '录制已暂停', phase='pause')
                            recording_feedback('pause')
                        else:
                            if physical.intersection(allowed):
                                held_names = '、'.join(key_label(key) for key in sorted(physical.intersection(allowed)))
                                raise ValueError(f'请先松开所有录制按键再开始录制（仍按着：{held_names}）')
                            if not environment_checked:
                                route_note(self, '已收到开始按键，正在获取起点画面…', phase='listen')
                                start_frame = io.frame()
                                h, w = start_frame.shape[:2]
                                if replacement is not None and route['resolution'] != [w, h]:
                                    raise ValueError('分辨率已改变，请恢复录制时分辨率或重录整条路线')
                                route['resolution'] = [w, h]
                                environment_checked = True
                            if not route.get('start_image'):
                                image_name = f'{uuid.uuid4().hex}.png'
                                write_image(self.store.asset(route['id'], image_name), start_frame)
                                route['start_image'] = image_name
                            recorder.resume(now)
                            route_note(self, '正在录制，按节点键直接保存截图；结束前请松开全部录制按键', phase='record')
                            recording_feedback('start')
                    elif action in ('node', 'finish'):
                        self.check_route()
                        if action == 'node' and replacement is not None:
                            raise ValueError('单段重录请按结束热键，检查条件将保留')
                        if action == 'node':
                            if recorder.started is None or recorder.invalid:
                                raise ValueError('请先开始或继续录制，再插入检查节点')
                            frame = node_frame(io)
                            image_name = f'{uuid.uuid4().hex}.png'
                            image_path = self.store.asset(route['id'], image_name)
                            try:
                                write_image(image_path, frame)
                            except (OSError, ValueError) as error:
                                raise ValueError(f'节点图片保存失败，录制继续：{error}') from error
                            segment = recorder.split(now)
                            segment.update(image=image_name, checkpoint_pending=True)
                            route['segments'].append(segment)
                            route_note(self, f'节点 {len(route["segments"])} 图片已保存，录制继续；结束后编辑检查条件。{image_path}', phase='record')
                            recording_feedback('ok')
                        else:
                            segment = recorder.segment(now)
                            if replacement is not None:
                                if not segment['events']:
                                    raise ValueError('请先录制该段的按键操作')
                                segment['checkpoint'] = checkpoint
                                segment['image'] = route['segments'][replacement].get('image', '')
                                if route['segments'][replacement].get('checkpoint_pending'):
                                    segment['checkpoint_pending'] = True
                                route['segments'][replacement] = segment
                            elif segment['events'] or segment['duration'] > 0:
                                route['segments'].append(segment)
                            if not route['segments']:
                                raise ValueError('请先录制按键操作')
                            validate_route(route)
                            self.store.save(route)
                            message = f'已保存路线「{route["name"]}」：{len(route["segments"])} 段，{sum(len(s["events"]) for s in route["segments"])} 个按键事件'
                            route_note(self, message, phase='ok')
                            recording_feedback('save')
                            session.recorded.emit(route)
                            return
                except ValueError as error:
                    route_note(self, str(error), phase='record' if action == 'node' and recorder.started is not None else 'fail')
                    recording_feedback('error')
        finally:
            poller.stop()

    def enter_mission(self):
        deadline = time.perf_counter() + 60
        while time.perf_counter() < deadline:
            self.check_route()
            self.next_frame()
            if self.in_team():
                return
            self.handle_mission_interface()
            self.sleep(.1)
        raise ValueError('60 秒内未进入副本，已停止')

    def play_routes(self, player):
        session = self.session
        routes = session.routes
        for route in routes:
            validate_route(route, runnable=True, require_map=session.operation == 'auto')
        failures = 0
        while True:
            if session.operation == 'auto':
                self.enter_mission()
                player.io.status('正在识别地图')
                ident = player.stable([(r['id'], r['map_rule']) for r in routes], 10)
                if ident is None:
                    player.io.failure('地图未识别或存在冲突', player.last_observation)
                    result = Result.MAP_FAILED
                else:
                    result = player.play(next(r for r in routes if r['id'] == ident))
            else:
                result = player.play(session.route)
            if result in (Result.SUCCESS, Result.STOPPED) or session.operation != 'auto':
                player.io.status('路线完成，已停止移动' if result == Result.SUCCESS else result.value)
                return result
            failures += 1
            if failures >= session.max_failures:
                player.io.status(f'连续失败 {failures} 次，已停止')
                return result
            player.release()
            self.check_route()
            player.io.status(f'本次失败，正在退出副本（{failures}/{session.max_failures}）')
            if not self.exit_mission():
                raise ValueError('退出菜单操作失败，已停止')
            # ESC menu alone is not proof of successful exit.
            if not self.wait_until(lambda: not self.in_team() and bool(
                    self.find_start_interface() or self.find_result_again_btn() or self.find_action_dialog_continue()),
                    time_out=60):
                raise ValueError('未确认退出到可重开界面，已停止')
