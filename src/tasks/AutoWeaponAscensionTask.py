import random
import re
import time

from ok import TaskDisabledException
from qfluentwidgets import FluentIcon

from src.dna_ui.Defs import COORD
from src.tasks.CommissionsTask import CommissionsTask, Mission
from src.tasks.DNAOneTimeTask import DNAOneTimeTask


class AutoWeaponAscensionTask(DNAOneTimeTask, CommissionsTask):
    """调停固定路线：暴露引信、黎瑟 Q 等待、冲刺撤离、再次进行。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "自动武器突破材料60级（调停）"
        self.description = "在调停选择界面或结算页启动，保留当前等级，成功或失败结算后均再次进行，持续重刷直到手动停止"
        self.group_name = "半自动"
        self.group_icon = FluentIcon.VIEW
        self.icon = FluentIcon.FLAG

    def set_phase(self, phase):
        self.phase = phase
        self.info_set("当前阶段", phase)

    def update_run_statistics(self):
        unknown = getattr(self, "unknown_runs", 0)
        total = self.successful_runs + self.failed_runs + unknown
        self.info_set("局数统计", f"已完成 {total} 局，成功 {self.successful_runs} 局，失败 {self.failed_runs} 局，未确认 {unknown} 局")

    def record_result(self):
        # Route timeouts are recovery signals, not evidence of a failed mission.
        deadline = time.monotonic() + 2
        while True:
            text = self.read_text("MEDIATION_RESULT_OCR")
            if "委托完成" in text:
                self.successful_runs += 1
                break
            if any(label in text for label in ("再接再厉", "委托失败", "挑战失败")):
                self.failed_runs += 1
                break
            if time.monotonic() >= deadline:
                self.unknown_runs += 1
                self.log_info("结算结果未识别，记为未确认，继续再次进行")
                break
            self.sleep(0.2)
            self.next_frame()
        self.update_run_statistics()

    def read_text(self, box_name):
        # Only join text inside the relevant region. In particular, the objective
        # also says 暴露引信 before combat, but is not an interaction prompt.
        texts = self.ocr(box=self.screen_box(box_name), name=box_name.lower())
        return re.sub(r"\s+", "", "".join(text.name for text in texts or ()))

    def is_mediation_start(self):
        return bool(self.find_start_interface()) and "调停" in self.read_text("MEDIATION_TITLE_OCR")

    def find_result_again_btn(self, threshold=0):
        button = super().find_result_again_btn(threshold=threshold)
        if button:
            return button
        # Success and failure share this button row; ignore the result title.
        box = self.box_of_screen_scaled(1600, 900, 1000, 730, 1570, 850,
                                        name="mediation_result_buttons", hcenter=True)
        words = self.ocr(box=box)
        text = re.sub(r"\s+", "", "".join(word.name for word in words or ()))
        return box if "再次进行" in text and "退出委托" in text else None

    def start_mission(self, timeout=0):
        self.mission_status = Mission.START
        deadline = time.monotonic() + (timeout or self.action_timeout)
        while time.monotonic() < deadline:
            self.next_frame()
            if self.find_result_again_btn():
                self.set_phase("结算页：再次进行")
                self.click_ui_coord(COORD.RESULT_AGAIN, name="mediation_again", after_sleep=0.2)
            elif self.find_start_interface():
                self.click_ui_coord(COORD.START_SCREEN_BTN, name="mediation_start", after_sleep=0.2)
            elif self.find_manual_select_btn() or self.find_letter_interface() or self.in_team():
                return
            else:
                self.sleep(0.2)
        raise RuntimeError("等待再次进行或开始调停超时")

    def open_in_mission_menu(self, time_out=10, raise_if_not_found=True):
        # Never repeat Esc: if settlement appears, it can activate 退出委托.
        self.set_phase("重试：等待确认对局界面")
        team_frames = 0
        esc_sent = False
        while True:
            self.next_frame()
            if self.find_result_again_btn():
                return False
            if self.find_start_interface() or self.find_manual_select_btn() or self.find_letter_interface():
                return False
            if self.find_esc_menu():
                return True
            if not esc_sent:
                team_frames = team_frames + 1 if self.in_team() else 0
                if team_frames >= 2:
                    self.send_key("esc")
                    esc_sent = True
                    self.set_phase("重试：等待菜单或结算页")
            # Unknown frames and slow menu transitions are not fatal. Framework
            # sleep/next_frame still propagate a manual task stop.
            self.sleep(0.2)

    def exit_mission(self, timeout=0):
        if not self.open_in_mission_menu():
            return False
        deadline = time.monotonic() + (timeout or self.action_timeout)
        confirmed = False
        while time.monotonic() < deadline:
            self.next_frame()
            # Settlement always takes priority, including before confirmation.
            if self.find_result_again_btn():
                return True
            if self.find_start_interface() or self.find_manual_select_btn() or self.find_letter_interface():
                return True
            box = self.find_center_confirm(threshold=0.9)
            if box:
                self._click_detected(box, name="mediation_giveup_confirm", after_sleep=0.25)
                confirmed = True
            elif self.find_esc_menu():
                self.click_ui_coord(COORD.ESC_GIVEUP, name="mediation_giveup", after_sleep=0.25)
            elif confirmed:
                return True
            else:
                self.sleep(0.2)
        raise RuntimeError("放弃挑战未确认完成，停止任务")

    def has_fuse_prompt(self):
        return "暴露引信" in self.read_text("MEDIATION_INTERACT_OCR")

    def objective_text(self):
        return self.read_text("MEDIATION_OBJECTIVE_OCR")

    @staticmethod
    def combat_objective(text):
        return all(part in text for part in ("破坏", "大烟花", "引信"))

    def release_movement(self):
        # Nested finally blocks also release the other keys if one release fails.
        try:
            self.send_key_up("w")
        finally:
            try:
                self.send_key_up("a")
            finally:
                self.send_key_up("lshift")

    def run(self):
        # Pause before starting the worker: even camera jitter while waiting for
        # combat would change the subsequent straight-line evacuation route.
        previous_pause = self.set_fidget_actions_paused(True)
        try:
            DNAOneTimeTask.run(self)
            if not self.find_letter_interface():
                self.move_mouse_to_safe_position(save_current_pos=False)
            self.set_check_monthly_card()
            self.do_run()
        except TaskDisabledException:
            pass
        finally:
            try:
                self.release_movement()
            finally:
                self.set_fidget_actions_paused(previous_pause)

    def do_run(self):
        if not (self.find_result_again_btn() or self.is_mediation_start() or self.find_letter_interface()):
            raise RuntimeError("请先停在「调停」选择界面、结算页或密函选择页再启动任务（不会自动切换等级）")
        self.successful_runs = 0
        self.failed_runs = 0
        self.unknown_runs = 0
        pending_result = False
        self.update_run_statistics()
        while True:
            self.set_phase("等待进入调停")
            if self.find_result_again_btn() or self.find_start_interface():
                if pending_result:
                    if self.find_result_again_btn():
                        self.record_result()
                    else:
                        self.unknown_runs += 1
                        self.update_run_statistics()
                    pending_result = False
                if not self.find_result_again_btn() and not self.is_mediation_start():
                    raise RuntimeError("当前选择界面不是调停，停止任务")
                self.mission_status = Mission.START
                self.start_mission()
            elif self.find_manual_select_btn():
                self.mission_status = Mission.START
                self.choose_drop_rate()
            elif self.find_letter_interface():
                self.mission_status = Mission.START
                self.confirm_current_letter()
            elif self.in_team():
                self.mission_status = None
                succeeded = self.run_attempt()
                pending_result = True
                if not succeeded:
                    self.retry_mission()
            else:
                self.wait_until(
                    lambda: self.find_result_again_btn() or self.find_start_interface()
                    or self.find_manual_select_btn() or self.find_letter_interface() or self.in_team(),
                    time_out=30, raise_if_not_found=True,
                )
            self.sleep(0.1)

    def confirm_current_letter(self):
        """Keep the selected letter, independently of the common letter config."""
        self.set_phase("密函选择：确认当前选择")
        return self._confirm_letter_once()

    def wait_for_result(self, duration):
        """Keep refreshing during holds; a result ends movement immediately."""
        deadline = time.monotonic() + duration
        while True:
            self.next_frame()
            if self.find_result_again_btn():
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            self.sleep(min(0.05, remaining))

    def hold_until_result(self, key, duration):
        try:
            self.send_key_down(key)
            return self.wait_for_result(duration)
        finally:
            self.send_key_up(key)

    def advance_to_fuse(self):
        duration = random.uniform(6.8, 6.9)
        self.last_fuse_interaction = None
        try:
            self.send_key_down("w")
            deadline = time.monotonic() + duration
            if self.wait_for_result(max(0, deadline - 1 - time.monotonic())):
                return True
            self.set_phase("前进最后1秒：连续点按交互键")
            next_press = deadline - 1
            while True:
                self.next_frame()
                if self.find_result_again_btn():
                    return True
                now = time.monotonic()
                if deadline - now <= 1e-9:
                    return False
                if now >= next_press:
                    self.last_fuse_interaction = now
                    self.send_key(self.key_config["Interact Key"])
                    # Do not catch up with bursts if recognition or input is slow.
                    next_press = now + 0.1
                remaining = min(deadline, next_press) - time.monotonic()
                if remaining > 0:
                    self.sleep(remaining)
        finally:
            self.send_key_up("w")

    def run_attempt(self):
        self.reset_detection()
        try:
            self.set_phase("前进至引信")
            if self.advance_to_fuse():
                return True
            self.set_phase("识别引信交互后的状态")
            state_deadline = time.monotonic() + 2
            combat_seen = False
            while True:
                self.next_frame()
                if self.find_result_again_btn():
                    return True
                objective = self.objective_text()
                if "可前往撤离点" in objective:
                    return self.evacuate()
                if self.combat_objective(objective):
                    combat_seen = True
                    break
                if self.has_fuse_prompt():
                    self.set_phase("操作引信")
                    self.send_key(self.key_config["Interact Key"])
                    if self.wait_for_result(random.uniform(0.3, 0.7)):
                        return True
                    self.last_fuse_interaction = time.monotonic()
                    self.send_key(self.key_config["Interact Key"])
                    break
                remaining = state_deadline - time.monotonic()
                if remaining <= 0:
                    self.log_info("2秒内未识别到交互、战斗或撤离状态，放弃重来")
                    return False
                self.sleep(min(0.05, remaining))
            deadline = (self.last_fuse_interaction if self.last_fuse_interaction is not None
                        else time.monotonic()) + 70
            self.set_phase("等待战斗及撤离提示")
            while time.monotonic() < deadline:
                self.next_frame()
                if self.find_result_again_btn():
                    return True
                objective = self.objective_text()
                if "可前往撤离点" in objective:
                    return self.evacuate()
                combat_seen = combat_seen or self.combat_objective(objective)
                if combat_seen:
                    self.check_lise_ultimate()
                self.sleep(0.05)
            self.log_info("最后一次操作引信后70秒内未出现撤离提示，放弃重来")
            return False
        finally:
            self.release_movement()

    def evacuate(self):
        self.set_phase("冲刺撤离")
        duration = random.uniform(20, 22)
        shift_delay = random.uniform(0, 1)
        try:
            self.send_key_down("w")
            deadline = time.monotonic() + duration
            if self.wait_for_result(shift_delay):
                return True
            self.send_key_down("lshift")
            if self.wait_for_result(max(0, deadline - time.monotonic())):
                return True
        finally:
            self.release_movement()
        for attempt in range(1, 4):
            self.set_phase(f"左移绕墙（{attempt}/3）")
            if self.hold_until_result("a", random.uniform(0.3, 0.4)):
                return True
            self.set_phase(f"补救前进（{attempt}/3）")
            if self.hold_until_result("w", 3):
                return True
        self.release_movement()
        self.set_phase("撤离补救结束：等待结算（最多5秒）")
        if self.wait_for_result(5):
            return True
        self.log_info("三组撤离补救后额外等待5秒仍未结算，放弃重来")
        return False

    def retry_mission(self):
        self.release_movement()
        self.next_frame()
        if self.find_result_again_btn():
            self.set_phase("结算页：准备再次进行")
            return
        self.set_phase("放弃并重试")
        self.give_up_mission()
        self.set_phase("重试：等待结算或开局界面")
        while True:
            self.next_frame()
            if (self.find_result_again_btn() or self.find_start_interface()
                    or self.find_manual_select_btn() or self.find_letter_interface()):
                return
            self.sleep(0.2)
