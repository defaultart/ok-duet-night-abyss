import time
from functools import cached_property

from src.dna_ui.Defs import COORD
from src.routes.weapon_exp60_gate import GateMatcher
from src.routes.weapon_exp60_t01 import T01_DURATION, T01_EVENTS, T01_GATE_CHECKPOINT
from src.tasks.AutoWeaponExp60Task import AutoWeaponExp60Task
from src.tasks.CommissionsTask import Mission


ROUTE_KEYS = tuple(sorted({key for _, key, _ in T01_EVENTS}))
DIRECTION_KEYS = frozenset(('w', 'a', 's', 'd', 'up', 'down', 'left', 'right'))
KEY_LABELS = {'lshift': '左Shift', 'rshift': '右Shift', 'space': '空格'}


def build_t01_schedule():
    """Keep the configured t01 event times; checkpoint time is handled separately."""
    return list(T01_EVENTS), T01_DURATION


class AutoWeaponExp60RecordedTask(AutoWeaponExp60Task):
    failure_message = '60级武器经验录制路线任务无法继续，请检查当前界面'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = '60级武器经验录制路线'
        self.description = ('在已选好60级的挑战界面或结算页启动，确认进局后等待1.5秒，再回放t01（含录制的Q）；'
                            '检查点识别门框后继续，结算后再次挑战，未通过或未结算则放弃重试')

    def run(self):
        self.reset_statistics()
        super().run()

    def reset_statistics(self):
        self.success_count = 0
        self.failure_count = 0
        self.gate_failure_count = 0
        self.info_set('成功次数', 0)
        self.info_set('失败次数', 0)
        self.info_set('门框OCR失败次数', 0)

    def finish_attempt(self, success, reason, gate_failed=False):
        if success:
            self.success_count = getattr(self, 'success_count', 0) + 1
            self.info_set('成功次数', self.success_count)
        elif gate_failed:
            self.gate_failure_count = getattr(self, 'gate_failure_count', 0) + 1
            self.info_set('门框OCR失败次数', self.gate_failure_count)
        else:
            self.failure_count = getattr(self, 'failure_count', 0) + 1
            self.info_set('失败次数', self.failure_count)
        self.set_phase(reason)
        return success

    def find_result_again_btn(self, threshold=0):
        button = super().find_result_again_btn(threshold=threshold)
        if button:
            return button
        # Both labels must be present in the button row. Never use result-title
        # wording (e.g. 再接再厉) as a reason to exit or reject a settlement page.
        box = self.box_of_screen_scaled(1600, 900, 1000, 730, 1570, 850,
                                        name='recorded_result_buttons', hcenter=True)
        words = self.ocr(box=box)
        text = ''.join(''.join(word.name.split()) for word in (words or ()))
        if '再次进行' in text and '退出委托' in text:
            return box
        return None

    def stop_with_screenshot(self, reason):
        self.set_phase(reason)
        self.screenshot(f't01_recovery_failed_{time.time_ns()}')
        raise RuntimeError(reason)

    def wait_for_result_page(self, timeout=10.0):
        self.set_phase(f'等待结算页（最多{timeout:g}秒）')
        deadline = time.monotonic() + timeout
        while True:
            self.next_frame()
            if self.find_result_again_btn():
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            self.sleep(min(0.2, remaining))

    def wait_for_restart_page(self, timeout=60.0, allow_in_team=False):
        deadline = time.monotonic() + timeout
        while True:
            self.next_frame()
            if self.find_result_again_btn() or self.find_start_interface() or self.find_manual_select_btn():
                return
            if allow_in_team and self.in_team():
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.stop_with_screenshot('界面未确认，停止重试，避免误退出委托')
            self.sleep(min(0.2, remaining))

    def start_mission(self, timeout=0):
        self.mission_status = Mission.START
        deadline = time.monotonic() + (timeout or self.action_timeout)
        while time.monotonic() < deadline:
            self.next_frame()
            # A settlement page always wins over other UI/HUD detections.
            if self.find_result_again_btn():
                self.set_phase('结算页：点击再次进行')
                self.click_ui_coord(COORD.RESULT_AGAIN, name='recorded_result_again', after_sleep=0.2)
            elif self.find_start_interface():
                self.click_ui_coord(COORD.START_SCREEN_BTN, name='recorded_start', after_sleep=0.2)
            elif self.find_manual_select_btn() or self.find_letter_interface() or self.in_team():
                return
            else:
                self.sleep(0.2)
        self.stop_with_screenshot('再次进行或开始挑战超时，停止任务')

    def do_run(self):
        self.wait_for_restart_page(timeout=self.action_timeout)
        while True:
            self.next_frame()
            if self.find_result_again_btn() or self.find_start_interface():
                self.start_mission()
            elif self.find_manual_select_btn():
                self.confirm_start()
                self.wait_until(self.in_team, time_out=60, raise_if_not_found=True)
            elif self.in_team():
                if not self.run_attempt():
                    self.retry_mission()
            else:
                self.wait_for_restart_page(allow_in_team=True)
            self.sleep(0.1)

    def retry_mission(self):
        self.lise_enabled = False
        self.release_movement()
        self.set_phase('准备重试：优先检查再次进行')
        self.give_up_mission()

    def give_up_mission(self, timeout=0):
        self.mission_status = None
        self.next_frame()
        if self.find_result_again_btn() or self.find_start_interface() or self.find_manual_select_btn():
            return
        self.exit_mission(timeout=timeout)
        self.wait_for_restart_page()

    def open_in_mission_menu(self, time_out=10, raise_if_not_found=True):
        self.next_frame()
        if self.find_result_again_btn():
            return False
        if self.find_esc_menu():
            return True
        if not self.in_team():
            self.stop_with_screenshot('未确认仍在对局内，停止发送Esc')
        # Refresh and recheck immediately before the only Esc press. Repeating
        # Esc here can activate the settlement screen's "退出委托" shortcut.
        self.next_frame()
        if self.find_result_again_btn():
            return False
        if self.find_esc_menu():
            return True
        if not self.in_team():
            self.stop_with_screenshot('对局界面已变化，停止发送Esc')
        self.send_key('esc')
        deadline = time.monotonic() + time_out
        while time.monotonic() < deadline:
            self.next_frame()
            if self.find_result_again_btn():
                return False
            if self.find_esc_menu():
                return True
            self.sleep(0.2)
        self.stop_with_screenshot('Esc后未确认菜单，停止任务，不重复发送Esc')

    def exit_mission(self, timeout=0):
        if not self.open_in_mission_menu():
            return False
        deadline = time.monotonic() + (timeout or self.action_timeout)
        confirmed = False
        while time.monotonic() < deadline:
            self.next_frame()
            if self.find_result_again_btn():
                return True
            if self.find_start_interface() or self.find_manual_select_btn():
                return True
            # Only click controls after identifying their own UI, and recheck
            # settlement first on every fresh frame, including confirmation.
            box = self.find_center_confirm(threshold=0.9)
            if box:
                self._click_detected(box, name='recorded_giveup_confirm', after_sleep=0.25)
                confirmed = True
            elif self.find_esc_menu():
                self.click_ui_coord(COORD.ESC_GIVEUP, name='recorded_esc_giveup', after_sleep=0.25)
            elif confirmed:
                return True
            else:
                self.sleep(0.2)
        self.stop_with_screenshot('放弃挑战未确认完成，停止任务')

    @cached_property
    def gate_matcher(self):
        return GateMatcher()

    def check_gate_checkpoint(self):
        self.set_phase('t01 检查点：检测门框轮廓（最多3秒，单帧达标通过）')
        deadline = time.monotonic() + 3.0
        best_score = 0.0
        best_result = None
        last_frame = None
        while time.monotonic() < deadline:
            self.next_frame()
            frame = self.frame
            result = self.gate_matcher.match(frame)
            last_frame = frame
            if best_result is None or result.score > best_score:
                best_score, best_result = result.score, result
            self.info_set('门框轮廓分数', f'{result.score:.3f}')
            if time.monotonic() > deadline:
                break
            if result.found:
                self.set_phase(f't01 检查点通过：门框轮廓 {result.score:.3f}')
                self.log_info(f'门框轮廓检查通过：分数={result.score:.3f}，上部={result.upper_score:.3f}，'
                              f'内部={result.inner_score:.3f}，位置={result.position}，缩放={result.scale:.2f}')
                return True
            remaining = deadline - time.monotonic()
            if remaining > 0:
                self.sleep(min(0.2, remaining))
        self.log_info(f'门框轮廓检查失败：3秒内无单帧达标，最佳分数={best_score:.3f}')
        if best_result is not None:
            self.log_info(f'最佳轮廓候选：上部={best_result.upper_score:.3f}，内部={best_result.inner_score:.3f}，'
                          f'位置={best_result.position}，缩放={best_result.scale:.2f}')
        if last_frame is not None:
            self.screenshot(f't01_gate_failed_{time.time_ns()}', frame=last_frame.copy())
        return False

    def release_movement(self):
        # Attempt every key even when one interaction call fails.
        error = None
        for key in ROUTE_KEYS:
            try:
                self.send_key_up(key)
            except Exception as exc:
                if error is None:
                    error = exc
        if error is not None:
            raise error

    def wait_route_deadline(self, deadline):
        remaining = deadline - time.monotonic()
        while remaining > 0:
            self.sleep(remaining)
            remaining = deadline - time.monotonic()

    def run_attempt(self):
        self.lise_enabled = False
        try:
            # do_run calls this only after detecting the in-game team HUD.
            self.set_phase('对局已开始，等待1.5秒')
            self.sleep(1.5)
            schedule, duration = build_t01_schedule()
            total_steps = sum(down and key in DIRECTION_KEYS for _, key, down in schedule)
            step = 0
            held = set()
            checkpoint_checked = False
            self.set_phase(f't01 移动第0/{total_steps}步：等待起步 {schedule[0][0]:.3f}秒')
            started = time.monotonic()
            for index, (timestamp, key, down) in enumerate(schedule):
                if not checkpoint_checked and timestamp >= T01_GATE_CHECKPOINT:
                    self.wait_route_deadline(started + T01_GATE_CHECKPOINT)
                    if held:
                        raise RuntimeError('门框检查点仍有按键未松开，已停止回放')
                    check_started = time.monotonic()
                    if not self.check_gate_checkpoint():
                        return self.finish_attempt(False, '门框OCR失败：检查点未识别到门框', gate_failed=True)
                    # Freeze route time during recognition; preserve the recorded idle
                    # and never catch up by shortening following movements.
                    started += time.monotonic() - check_started
                    checkpoint_checked = True
                self.wait_route_deadline(started + timestamp)
                if down:
                    self.send_key_down(key)
                    held.add(key)
                    if key in DIRECTION_KEYS:
                        step += 1
                else:
                    self.send_key_up(key)
                    held.discard(key)
                label = KEY_LABELS.get(key, key.upper())
                action = f'{"按下" if down else "松开"}{label}'
                next_time = schedule[index + 1][0] if index + 1 < len(schedule) else duration
                next_boundary = (min(next_time, T01_GATE_CHECKPOINT)
                                 if not checkpoint_checked else next_time)
                interval = next_boundary - timestamp
                if held:
                    keys = '+'.join(KEY_LABELS.get(k, k.upper()) for k in sorted(held))
                    following = f'保持{keys} {interval:.3f}秒'
                else:
                    following = f'等待 {interval:.3f}秒'
                self.set_phase(f't01 移动第{step}/{total_steps}步｜'
                               f'操作第{index + 1}/{len(schedule)}：{action}；{following}')
            self.wait_route_deadline(started + duration)
        finally:
            self.release_movement()

        success = self.wait_for_result_page()
        return self.finish_attempt(success, '本局成功：已进入结算页' if success else '本局失败：未进入结算页')
