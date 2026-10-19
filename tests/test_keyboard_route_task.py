import copy
import subprocess
import sys
import tempfile
import time
import unittest
from queue import Queue
from threading import Event, Thread, current_thread
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from src.routes.core import Interrupted, Result, RouteStore
from src.tasks.KeyboardRouteTask import (FocusGate, KeyboardRouteTask, KeyPoller, TaskAdapter, RouteCaptureWorker,
                                         arm_route_task, crop, node_frame, same_game_foreground,
                                         startup_expired, write_image)
from src.compat.route_capture_bootstrap import install_route_capture_bootstrap
from tests.test_keyboard_routes import route, rule


class RetryTests(unittest.TestCase):
    def setup_flow(self, results, operation='auto', limit=3):
        value = route()
        task = SimpleNamespace(
            session=SimpleNamespace(operation=operation, route=value, routes=[value], max_failures=limit),
            enter_mission=Mock(), check_route=Mock(), exit_mission=Mock(return_value=True),
            wait_until=Mock(return_value=True))
        player = Mock()
        player.stable.return_value = value['id']
        player.play.side_effect = results
        return task, player

    def test_fail_then_success_exits_before_reentry(self):
        task, player = self.setup_flow([Result.NODE_FAILED, Result.SUCCESS])
        self.assertEqual(KeyboardRouteTask.play_routes(task, player), Result.SUCCESS)
        self.assertEqual(task.enter_mission.call_count, 2)
        task.exit_mission.assert_called_once()
        task.wait_until.assert_called_once()

    def test_failure_limit_stops_instead_of_infinite_retries(self):
        task, player = self.setup_flow([Result.NODE_FAILED] * 3)
        self.assertEqual(KeyboardRouteTask.play_routes(task, player), Result.NODE_FAILED)
        self.assertEqual(task.exit_mission.call_count, 2)
        self.assertEqual(task.enter_mission.call_count, 3)

    def test_exit_action_failure_never_reenters(self):
        task, player = self.setup_flow([Result.NODE_FAILED])
        task.exit_mission.return_value = False
        with self.assertRaisesRegex(ValueError, '退出菜单'):
            KeyboardRouteTask.play_routes(task, player)
        task.wait_until.assert_not_called()
        task.enter_mission.assert_called_once()

    def test_unverified_exit_never_reenters(self):
        task, player = self.setup_flow([Result.NODE_FAILED])
        task.wait_until.return_value = False
        with self.assertRaisesRegex(ValueError, '未确认退出'):
            KeyboardRouteTask.play_routes(task, player)
        task.enter_mission.assert_called_once()

    def test_esc_menu_alone_is_not_verified_exit(self):
        task, player = self.setup_flow([Result.NODE_FAILED])
        task.in_team = Mock(return_value=True)
        task.find_start_interface = Mock(return_value=False)
        task.find_result_again_btn = Mock(return_value=False)
        task.find_action_dialog_continue = Mock(return_value=False)
        task.wait_until.side_effect = lambda condition, **kwargs: condition()
        with self.assertRaisesRegex(ValueError, '未确认退出'):
            KeyboardRouteTask.play_routes(task, player)

    def test_map_conflict_never_moves_and_saves_diagnostics(self):
        task, player = self.setup_flow([], limit=1)
        player.stable.return_value = None
        self.assertEqual(KeyboardRouteTask.play_routes(task, player), Result.MAP_FAILED)
        player.play.assert_not_called()
        player.io.failure.assert_called_once()

    def test_user_stop_and_trial_failure_do_not_exit(self):
        for result, operation in ((Result.STOPPED, 'auto'), (Result.NODE_FAILED, 'test')):
            task, player = self.setup_flow([result], operation)
            self.assertEqual(KeyboardRouteTask.play_routes(task, player), result)
            task.exit_mission.assert_not_called()


class RecordingFlowTests(unittest.TestCase):
    def test_f8_callback_starts_without_map_and_poller_precedes_capture(self):
        with tempfile.TemporaryDirectory() as folder:
            value = route()
            value['segments'], value['map_rule'], value['start_image'] = [], None, ''
            session = SimpleNamespace(route=value, hotkeys=('f8', 'f7', 'f10'), segment=None,
                                      commands=Queue(), status=Mock(), image=Mock(), recorded=Mock())
            task = SimpleNamespace(session=session, key_config={'Interact Key': 'f'},
                                   store=RouteStore(folder), foreground=lambda: True,
                                   check_route=Mock(), logger=Mock())
            listening = []
            class Poller:
                def __init__(self, names, read_down, on_change, on_ready=None, interval=0.01):
                    self.on_change = on_change
                def start(self):
                    listening.append(True)
                    for name, down in (('f8', True), ('f8', False), ('w', True), ('Q', True),
                                       ('q', False), ('w', False), ('7', True), ('7', False),
                                       ('f4', True), ('f4', False), ('esc', True), ('esc', False),
                                       ('mouse_left', True), ('mouse_left', False), ('f10', True)):
                        self.on_change(name, down)
                def wait_ready(self, timeout=2):
                    pass
                def is_alive(self):
                    return True
                def stop(self):
                    pass
            def frame():
                self.assertEqual(listening, [True])
                return np.zeros((90, 160, 3), np.uint8)
            with patch('src.tasks.KeyboardRouteTask.KeyPoller', Poller), patch('src.tasks.KeyboardRouteTask.recording_feedback'):
                KeyboardRouteTask.record(task, SimpleNamespace(frame=frame))
            saved, errors = task.store.load_all()
            self.assertFalse(errors)
            self.assertIsNone(saved[0]['map_rule'])
            self.assertEqual([e['key'] for e in saved[0]['segments'][0]['events']],
                             ['w', 'q', 'q', 'w', '7', '7'])

    def test_nodes_save_images_without_dialog_or_interrupting_held_movement(self):
        with tempfile.TemporaryDirectory() as folder:
            value = route()
            value['segments'] = []
            value['start_image'] = ''
            commands = Queue()
            def keys(sequence):
                for item in sequence:
                    commands.put(('key', *item, True))
            keys([('f8', True, 1), ('w', True, 2), ('f7', True, 4), ('f7', True, 6),
                  ('w', False, 8), ('a', True, 11), ('a', False, 12), ('f10', True, 13)])
            session = SimpleNamespace(route=value, hotkeys=('f8', 'f7', 'f10'), segment=None,
                                      commands=commands, status=Mock(), image=Mock(), recorded=Mock())
            task = SimpleNamespace(session=session, key_config={'Interact Key': 'f'},
                                   store=RouteStore(folder), foreground=lambda: True, check_route=Mock(), logger=Mock())
            io = SimpleNamespace(frame=lambda: np.zeros((90, 160, 3), np.uint8))
            class IdlePoller:
                def __init__(self, *args, **kwargs):
                    pass
                def start(self):
                    pass
                def wait_ready(self, timeout=2):
                    pass
                def is_alive(self):
                    return True
                def stop(self):
                    pass
            with patch('src.tasks.KeyboardRouteTask.KeyPoller', IdlePoller), patch('src.tasks.KeyboardRouteTask.recording_feedback'):
                KeyboardRouteTask.record(task, io)
            saved, errors = task.store.load_all()
            self.assertEqual(errors, [])
            session.image.emit.assert_not_called()
            self.assertEqual([s['duration'] for s in saved[0]['segments']], [3, 2, 7])
            for segment in saved[0]['segments'][:2]:
                self.assertTrue(segment['checkpoint_pending'])
                self.assertTrue(task.store.asset(value['id'], segment['image']).is_file())
            self.assertIsNone(saved[0]['segments'][2]['checkpoint'])
            self.assertEqual(saved[0]['segments'][2]['events'][1], {'key': 'w', 'type': 'key_up', 'time': 2})

    def test_failed_node_capture_keeps_events_and_session(self):
        with tempfile.TemporaryDirectory() as folder:
            value = route()
            value['segments'] = []
            value['start_image'] = ''
            commands = Queue()
            notes = []
            for item in (('f8', True, 1), ('w', True, 2), ('w', False, 3), ('f7', True, 4), ('f7', True, 5), ('f10', True, 20)):
                commands.put(('key', *item))
            calls = {'n': 0}

            def frame():
                calls['n'] += 1
                if calls['n'] == 2:
                    raise RuntimeError('argument 2: OverflowError: int too long to convert')
                return np.zeros((90, 160, 3), np.uint8)

            def info(message):
                notes.append(message)

            started = time.perf_counter()

            def check(*args, **kwargs):
                if time.perf_counter() - started > 2:
                    raise Interrupted('录制测试没有在时限内结束')

            session = SimpleNamespace(route=value, hotkeys=('f8', 'f7', 'f10'), segment=None,
                                      commands=commands, status=Mock(), image=Mock(),
                                      recorded=Mock())
            task = SimpleNamespace(session=session, key_config={'Interact Key': 'f'},
                                   store=RouteStore(folder), foreground=lambda: True,
                                   check_route=check, logger=Mock(info=info))
            class IdlePoller:
                def __init__(self, *args, **kwargs):
                    pass
                def start(self):
                    pass
                def wait_ready(self, timeout=2):
                    pass
                def is_alive(self):
                    return True
                def stop(self):
                    pass
            with patch('src.tasks.KeyboardRouteTask.KeyPoller', IdlePoller), patch('src.tasks.KeyboardRouteTask.recording_feedback'):
                KeyboardRouteTask.record(task, SimpleNamespace(frame=frame))
            self.assertTrue(any('节点截图失败，录制还在' in note for note in notes))
            saved, errors = task.store.load_all()
            self.assertEqual(errors, [])
            self.assertEqual(len(saved[0]['segments']), 2)
            self.assertEqual([event['key'] for event in saved[0]['segments'][0]['events']], ['w', 'w'])
            session.image.emit.assert_not_called()

    def test_editing_deferred_node_makes_route_playable(self):
        from PySide6.QtWidgets import QDialog
        from src.dna_ui.KeyboardRoutesTab import KeyboardRoutesTab
        from src.routes.core import validate_route
        with tempfile.TemporaryDirectory() as folder:
            value = route()
            value['segments'][0].update(checkpoint=None, checkpoint_pending=True, image='node.png')
            store = RouteStore(folder)
            write_image(store.asset(value['id'], 'node.png'), np.zeros((90, 160, 3), np.uint8))
            store.save(value)
            page = SimpleNamespace(selected=lambda: value, index=lambda: 0, store=store,
                                   save_rule=Mock(return_value=rule()), refresh=Mock())
            dialog = SimpleNamespace(exec=lambda: QDialog.Accepted, rule=rule())
            with patch('src.dna_ui.KeyboardRoutesTab.RuleDialog', return_value=dialog):
                KeyboardRoutesTab.edit_node(page)
            saved, errors = store.load_all()
            self.assertFalse(errors)
            self.assertNotIn('checkpoint_pending', saved[0]['segments'][0])
            validate_route(saved[0], runnable=True)

    def test_ignored_key_is_logged_and_held_q_blocks_start(self):
        with tempfile.TemporaryDirectory() as folder:
            value = route()
            value['segments'], value['start_image'] = [], ''
            commands = Queue()
            commands.put(('seed', ('q',)))
            commands.put(('toggle',))
            stopped = Event()
            def emit(text):
                if '松开所有录制按键' in text:
                    stopped.set()
            session = SimpleNamespace(route=value, hotkeys=('f8', 'f7', 'f10'), segment=None,
                                      commands=commands, status=Mock(emit=emit), image=Mock(), recorded=Mock(),
                                      stopped=stopped)
            def check(*args, **kwargs):
                if stopped.is_set():
                    raise Interrupted('stop')
            task = SimpleNamespace(session=session, key_config={'Interact Key': 'f'},
                                   store=RouteStore(folder), foreground=lambda: True,
                                   check_route=check, logger=Mock())
            class IdlePoller:
                def __init__(self, *args, **kwargs):
                    pass
                def start(self):
                    pass
                def wait_ready(self, timeout=2):
                    pass
                def is_alive(self):
                    return True
                def stop(self):
                    pass
            with patch('src.tasks.KeyboardRouteTask.KeyPoller', IdlePoller), patch('src.tasks.KeyboardRouteTask.recording_feedback'):
                with self.assertRaises(Interrupted):
                    KeyboardRouteTask.record(task, SimpleNamespace(frame=lambda: np.zeros((8, 8, 3), np.uint8)))
            self.assertTrue(stopped.is_set())
            logged = ' '.join(str(call.args[0]) for call in task.logger.info.call_args_list)
            self.assertIn('仍按着', logged)
            self.assertIn('Q', logged)

            commands = Queue()
            for item in (('f8', True, 1), ('f4', True, 2), ('w', True, 3), ('w', False, 4), ('f10', True, 5)):
                commands.put(('key', *item))
            session = SimpleNamespace(route=copy.deepcopy(value), hotkeys=('f8', 'f7', 'f10'), segment=None,
                                      commands=commands, status=Mock(), image=Mock(), recorded=Mock())
            task = SimpleNamespace(session=session, key_config={'Interact Key': 'f'},
                                   store=RouteStore(folder), foreground=lambda: True,
                                   check_route=Mock(), logger=Mock())
            with patch('src.tasks.KeyboardRouteTask.KeyPoller', IdlePoller), patch('src.tasks.KeyboardRouteTask.recording_feedback'):
                KeyboardRouteTask.record(task, SimpleNamespace(frame=lambda: np.zeros((8, 8, 3), np.uint8)))
            logged = ' '.join(str(call.args[0]) for call in task.logger.info.call_args_list)
            self.assertIn('已忽略：不支持的录制按键', logged)
            self.assertIn('已记入', logged)


class RecordingSupportTests(unittest.TestCase):
    def test_same_process_counts_as_game_foreground(self):
        self.assertFalse(same_game_foreground(0, [10]))
        self.assertTrue(same_game_foreground(10, [10, 11]))
        self.assertTrue(same_game_foreground(99, [10], foreground_pid=7, captured_pid=7))
        self.assertFalse(same_game_foreground(99, [10], foreground_pid=7, captured_pid=8))

    def test_startup_watch_waits_and_ignores_a_finished_run(self):
        self.assertFalse(startup_expired(10, 10.4, False, False))
        self.assertTrue(startup_expired(10, 26, False, False))
        self.assertFalse(startup_expired(10, 26, True, False))
        self.assertFalse(startup_expired(10, 26, False, True))

    def test_arm_route_task_enables_before_waking_executor(self):
        order = []
        task = SimpleNamespace(enable=lambda: order.append('enable'), start=lambda: order.append('start'))
        arm_route_task(task)
        self.assertEqual(order, ['enable', 'start'])

    def test_focus_gate_ignores_a_short_flicker(self):
        clock = SimpleNamespace(now=0)
        gate = FocusGate(grace=0.3, clock=lambda: clock.now)
        self.assertEqual(gate.update(False), (True, None))
        clock.now = 0.2
        self.assertEqual(gate.update(False), (True, None))
        clock.now = 0.31
        self.assertEqual(gate.update(False), (False, 'lost'))
        self.assertEqual(gate.update(True), (True, 'back'))

    def test_poller_reports_changes_and_not_keys_already_held(self):
        state = {'w': True, 'a': False}
        changes, ready = [], []
        poller = KeyPoller(['w', 'a'], lambda name: state[name],
                           lambda name, down: changes.append((name, down)),
                           on_ready=lambda held: ready.append(held), interval=0.01)
        poller.start()
        try:
            poller.wait_ready()
            self.assertEqual(ready, [('w',)])
            self.assertEqual(changes, [])
            state['a'] = True
            state['w'] = False
            deadline = time.perf_counter() + 1
            while time.perf_counter() < deadline and len(changes) < 2:
                time.sleep(0.02)
        finally:
            poller.stop()
        self.assertIn(('a', True), changes)
        self.assertIn(('w', False), changes)


class MatchingTests(unittest.TestCase):
    def test_template_matches_identical_roi_and_rejects_changed_pixels(self):
        with tempfile.TemporaryDirectory() as folder:
            value = route()
            store = RouteStore(folder)
            frame = np.full((100, 100, 3), 20, np.uint8)
            condition = {'kind': 'template', 'rect': [.1, .1, .5, .5], 'template': 'template.png', 'threshold': .9}
            write_image(store.asset(value['id'], 'template.png'), crop(frame, condition['rect']))
            io = TaskAdapter(SimpleNamespace(store=store, check_route=Mock()))
            self.assertTrue(io.match(frame, value['id'], condition)[0])
            self.assertFalse(io.match(np.full_like(frame, 240), value['id'], condition)[0])

    def test_ocr_requires_all_keywords(self):
        task = SimpleNamespace(check_route=Mock(), ocr=Mock(return_value=[SimpleNamespace(name='目标')]))
        io = TaskAdapter(task)
        condition = copy.deepcopy(rule())
        condition['keywords'].append('机关')
        matched, text = io.match(np.zeros((100, 100, 3), np.uint8), route()['id'], condition)
        self.assertFalse(matched)
        self.assertEqual(text, '目标')


class CleanupTests(unittest.TestCase):
    def test_exception_restores_triggers_and_fidget_flags(self):
        task = object.__new__(KeyboardRouteTask)
        task._enabled, task._paused = True, False
        task.session = SimpleNamespace(operation='record', stopped=Event(), stop=Mock(), finished=Mock(), status=Mock())
        session = task.session
        trigger = SimpleNamespace(enabled=True, disable=Mock(), enable=Mock(), try_disconnect_listener=Mock())
        other = SimpleNamespace(fidget_params={}, set_fidget_actions_paused=Mock(return_value=False))
        task._executor = SimpleNamespace(trigger_tasks=[trigger], onetime_tasks=[other], paused=False,
                                         interaction=Mock(), exit_event=Event())
        task.logger = Mock()
        task.foreground = Mock(return_value=True)
        task.log_error = Mock()
        task.record = Mock(side_effect=RuntimeError('recording failed'))
        with patch('src.tasks.KeyboardRouteTask.time.sleep'):
            task.run()
        trigger.disable.assert_called_once()
        trigger.try_disconnect_listener.assert_called_once()
        trigger.enable.assert_called_once()
        self.assertEqual(other.set_fidget_actions_paused.call_args_list[0].args, (True,))
        self.assertEqual(other.set_fidget_actions_paused.call_args_list[-1].args, (False,))
        session.finished.emit.assert_called_once_with('error', 'recording failed')
        self.assertIsNone(task.session)


class NodeFrameTests(unittest.TestCase):
    def test_capture_errors_stay_recoverable_and_do_not_hide_stop(self):
        with self.assertRaisesRegex(ValueError, '节点截图失败，录制还在'):
            node_frame(SimpleNamespace(frame=Mock(side_effect=RuntimeError('overflow'))))
        with self.assertRaisesRegex(ValueError, '节点截图失败，录制还在'):
            node_frame(SimpleNamespace(frame=lambda: np.zeros((0, 0, 3), np.uint8)))
        with self.assertRaises(Interrupted):
            node_frame(SimpleNamespace(frame=Mock(side_effect=Interrupted('stop'))))


class BootstrapTests(unittest.TestCase):
    def test_page_f8_arms_recording_from_inside_game(self):
        from pynput import keyboard
        from src.dna_ui.KeyboardRoutesTab import KeyboardRoutesTab
        task = SimpleNamespace(foreground=Mock(return_value=True))
        tab = SimpleNamespace(session=None, isVisible=lambda: False, _last_start_key=0,
                              _recording_hotkeys_armed=True, logger=Mock(), hotkey_feedback=Mock(),
                              hotkeys=[SimpleNamespace(text=lambda: 'f8'), SimpleNamespace(text=lambda: 'f7'), SimpleNamespace(text=lambda: 'f10')], launch=Mock(), status=Mock(),
                              executor=SimpleNamespace(current_task=None, onetime_tasks=[],
                                                       get_task_by_class=lambda cls: task))
        KeyboardRoutesTab.on_global_press(tab, keyboard.Key.f8)
        tab.launch.assert_called_once_with('record', start_immediately=True)

    def test_f10_without_recording_reports_that_nothing_was_saved(self):
        from pynput import keyboard
        from src.dna_ui.KeyboardRoutesTab import KeyboardRoutesTab
        tab = SimpleNamespace(session=None, _last_start_key=0, _recording_hotkeys_armed=True,
                              logger=Mock(), hotkey_feedback=Mock(), launch=Mock(),
                              hotkeys=[SimpleNamespace(text=lambda: 'f8'), SimpleNamespace(text=lambda: 'f7'), SimpleNamespace(text=lambda: 'f10')],
                              executor=SimpleNamespace(get_task_by_class=lambda cls: SimpleNamespace(foreground=lambda: False)))
        KeyboardRoutesTab.on_global_press(tab, keyboard.Key.f10)
        tab.launch.assert_not_called()
        self.assertIn('没有录制内容可保存', tab.hotkey_feedback.call_args.args[0])

    def test_f8_wrong_foreground_reports_reason_instead_of_silently_ignoring(self):
        from pynput import keyboard
        from src.dna_ui.KeyboardRoutesTab import KeyboardRoutesTab
        tab = SimpleNamespace(session=None, _last_start_key=0, _recording_hotkeys_armed=True,
                              logger=Mock(), hotkey_feedback=Mock(), launch=Mock(),
                              hotkeys=[SimpleNamespace(text=lambda: 'f8'), SimpleNamespace(text=lambda: 'f7'), SimpleNamespace(text=lambda: 'f10')],
                              executor=SimpleNamespace(get_task_by_class=lambda cls: SimpleNamespace(foreground=lambda: False)))
        KeyboardRoutesTab.on_global_press(tab, keyboard.Key.f8)
        tab.launch.assert_not_called()
        self.assertIn('游戏窗口不在前台', tab.hotkey_feedback.call_args.args[0])

    def test_route_initialization_does_not_wait_for_executor_prefetch(self):
        class Executor:
            current_task = None
            def next_frame(self, **kwargs):
                raise RuntimeError('prefetch called')
        install_route_capture_bootstrap(Executor)
        executor = Executor()
        executor.current_task = SimpleNamespace(_route_owns_capture=True, session=object(), _route_in_run=False)
        self.assertIsNone(executor.next_frame(time_out=4))
        executor.current_task._route_in_run = True
        with self.assertRaisesRegex(RuntimeError, 'prefetch called'):
            executor.next_frame()
        executor.current_task = SimpleNamespace()
        with self.assertRaisesRegex(RuntimeError, 'prefetch called'):
            executor.next_frame()

    def test_blocked_capture_can_be_cancelled(self):
        task = object.__new__(KeyboardRouteTask)
        unblock, entered = Event(), Event()
        def capture():
            entered.set()
            unblock.wait(2)
            return np.zeros((90, 160, 3), np.uint8)
        task._executor = SimpleNamespace(method=SimpleNamespace(get_frame=capture))
        def check():
            if entered.is_set():
                from src.routes.core import Interrupted
                raise Interrupted('stop')
        task.check_route = check
        try:
            from src.routes.core import Interrupted
            with self.assertRaises(Interrupted):
                task.next_frame()
        finally:
            unblock.set()
            task._route_capture_worker.stop()

    def test_capture_reuses_one_thread_and_empty_frame_is_not_retried(self):
        task = object.__new__(KeyboardRouteTask)
        threads = []

        def capture():
            threads.append(current_thread())
            return np.zeros((8, 8, 3), np.uint8)

        task._executor = SimpleNamespace(method=SimpleNamespace(get_frame=capture), _frame=None, _last_frame_time=0)
        task.check_route = lambda *args, **kwargs: None
        try:
            first = task.next_frame()
            second = task.next_frame()
            self.assertEqual(first.shape, second.shape)
            self.assertEqual({item.ident for item in threads}, {task._route_capture_worker.thread.ident})
            self.assertEqual(task._route_capture_worker.thread.name, 'KeyboardRouteCapture')
        finally:
            task._route_capture_worker.stop()

        empty = object.__new__(KeyboardRouteTask)
        calls = []

        def empty_capture():
            calls.append(1)
            return None

        empty._executor = SimpleNamespace(method=SimpleNamespace(get_frame=empty_capture), _frame=None, _last_frame_time=0)
        empty.check_route = lambda *args, **kwargs: None
        try:
            with self.assertRaisesRegex(ValueError, '未获取到新的游戏画面'):
                empty.next_frame()
            self.assertEqual(calls, [1])
        finally:
            empty._route_capture_worker.stop()

    def test_second_capture_does_not_start_another_thread_while_busy(self):
        task = object.__new__(KeyboardRouteTask)
        entered, release = Event(), Event()
        threads = []

        def capture():
            threads.append(current_thread())
            entered.set()
            release.wait(2)
            return np.zeros((4, 4, 3), np.uint8)

        task._executor = SimpleNamespace(method=SimpleNamespace(get_frame=capture), _frame=None, _last_frame_time=0)
        task.check_route = lambda *args, **kwargs: None
        holder = {}

        def run():
            holder['frame'] = task.next_frame()

        waiter = Thread(target=run)
        waiter.start()
        try:
            self.assertTrue(entered.wait(1))
            with self.assertRaisesRegex(ValueError, '仍未返回'):
                task.next_frame()
            self.assertEqual(len(threads), 1)
        finally:
            release.set()
            waiter.join(2)
            task._route_capture_worker.stop()


class CaptureOwnershipTests(unittest.TestCase):
    def test_native_gdi_lifecycle_in_isolated_process(self):
        process = subprocess.run([sys.executable, '-m', 'tests.support.native_route_capture'],
                                 capture_output=True, timeout=30)
        self.assertEqual(process.returncode, 0,
                         (process.stdout + process.stderr).decode('utf-8', errors='replace')[-3000:])
        self.assertIn(b'PASS: native GDI', process.stdout)

    def make_backend(self):
        created = []

        class GdiCapture:
            def __init__(self, window):
                self.hwnd_window = window
                self.owner = current_thread()
                self.exit_event = None
                self.reads = 0
                self.closes = 0
                self.fail = False
                created.append(self)

            def get_frame(inner):
                self.assertIs(current_thread(), inner.owner)
                self.assertEqual(inner.closes, 0)
                inner.reads += 1
                if inner.fail:
                    raise ValueError('capture failed')
                return np.zeros((12, 16, 3), np.uint8)

            def close(inner):
                self.assertIs(current_thread(), inner.owner)
                inner.closes += 1

        return GdiCapture, created

    def test_start_stop_restart_uses_fresh_owned_contexts(self):
        backend, created = self.make_backend()
        source = backend(object())
        source.exit_event = Event()
        task = object.__new__(KeyboardRouteTask)
        task._executor = SimpleNamespace(method=source, _frame=None, _last_frame_time=0)
        task.check_route = lambda *a, **k: None
        with patch('ok.device.capture_methods.bitblt.BitBltCaptureMethod', backend):
            for session in range(3):
                try:
                    # Start image plus multiple checkpoint images.
                    for _ in range(3):
                        self.assertEqual(task.next_frame().shape, (12, 16, 3))
                finally:
                    task._route_capture_worker.stop()
                self.assertFalse(task._route_capture_worker.thread.is_alive())
        self.assertEqual(len(created), 4)
        self.assertEqual((source.reads, source.closes), (0, 0))
        for private in created[1:]:
            self.assertIs(private.hwnd_window, source.hwnd_window)
            self.assertIs(private.exit_event, source.exit_event)
            self.assertEqual((private.reads, private.closes), (3, 1))

    def test_backend_change_closes_old_private_context_on_owner(self):
        backend, created = self.make_backend()
        a, b = backend(object()), backend(object())
        with patch('ok.device.capture_methods.bitblt.BitBltCaptureMethod', backend):
            worker = RouteCaptureWorker()
            try:
                for source in (a, b):
                    result = Queue()
                    worker.submit(source, result)
                    self.assertTrue(result.get(timeout=2)[0])
                self.assertEqual(created[2].closes, 1)
            finally:
                worker.stop()
        self.assertEqual(created[3].closes, 1)
        self.assertEqual((a.closes, b.closes), (0, 0))

    def test_exception_still_releases_private_capture(self):
        backend, created = self.make_backend()
        source = backend(object())
        def factory(method):
            private = backend(method.hwnd_window)
            private.fail = True
            return private
        worker = RouteCaptureWorker(capture_factory=factory)
        try:
            result = Queue()
            worker.submit(source, result)
            success, error = result.get(timeout=2)
            self.assertFalse(success)
            self.assertIsInstance(error, ValueError)
        finally:
            worker.stop()
        self.assertEqual(created[1].closes, 1)
        self.assertEqual(source.closes, 0)

    def test_borrowed_non_gdi_backend_is_never_closed(self):
        source = SimpleNamespace(get_frame=lambda: np.zeros((12, 16, 3), np.uint8), close=Mock())
        worker = RouteCaptureWorker()
        try:
            result = Queue()
            worker.submit(source, result)
            self.assertTrue(result.get(timeout=2)[0])
        finally:
            worker.stop()
        source.close.assert_not_called()
        with self.assertRaisesRegex(ValueError, '退出'):
            worker.submit(source, Queue())


if __name__ == '__main__':
    unittest.main()
