import random
import re
import time

from ok import TaskDisabledException
from qfluentwidgets import FluentIcon

from src.tasks.CommissionsTask import CommissionsTask, Mission
from src.tasks.DNAOneTimeTask import DNAOneTimeTask


class AutoWeaponAscensionTask(DNAOneTimeTask, CommissionsTask):
    """调停固定路线：暴露引信、黎瑟 Q 等待、冲刺撤离、再次进行。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "自动武器突破材料60级"
        self.description = "在调停选择界面或结算页启动，保留当前等级，持续重刷直到手动停止"
        self.group_name = "半自动"
        self.group_icon = FluentIcon.VIEW
        self.icon = FluentIcon.FLAG

    def set_phase(self, phase):
        self.phase = phase
        self.info_set("当前阶段", phase)

    def update_run_statistics(self):
        total = self.successful_runs + self.failed_runs
        self.info_set("局数统计", f"已完成 {total} 局，成功 {self.successful_runs} 局，失败 {self.failed_runs} 局")

    def read_text(self, box_name):
        # Only join text inside the relevant region. In particular, the objective
        # also says 暴露引信 before combat, but is not an interaction prompt.
        texts = self.ocr(box=self.screen_box(box_name), name=box_name.lower())
        return re.sub(r"\s+", "", "".join(text.name for text in texts or ()))

    def is_mediation_start(self):
        return bool(self.find_start_interface()) and "调停" in self.read_text("MEDIATION_TITLE_OCR")

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
        if not self.find_result_again_btn() and not self.is_mediation_start():
            raise RuntimeError("请先停在「调停」选择界面或结算页再启动任务（不会自动切换等级）")
        self.successful_runs = 0
        self.failed_runs = 0
        self.update_run_statistics()
        while True:
            self.set_phase("等待进入调停")
            if self.find_result_again_btn() or self.find_start_interface():
                if not self.find_result_again_btn() and not self.is_mediation_start():
                    raise RuntimeError("当前选择界面不是调停，停止任务")
                self.mission_status = Mission.START
                self.start_mission()
            elif self.find_manual_select_btn():
                self.mission_status = Mission.START
                self.choose_drop_rate()
                self.wait_until(self.in_team, time_out=30, raise_if_not_found=True)
            elif self.in_team():
                self.mission_status = None
                succeeded = self.run_attempt()
                if succeeded:
                    self.successful_runs += 1
                else:
                    self.failed_runs += 1
                self.update_run_statistics()
                if not succeeded:
                    self.retry_mission()
            else:
                self.wait_until(
                    lambda: self.find_result_again_btn() or self.find_start_interface()
                    or self.find_manual_select_btn() or self.in_team(),
                    time_out=30, raise_if_not_found=True,
                )
            self.sleep(0.1)

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

    def run_attempt(self):
        self.reset_detection()
        try:
            self.set_phase("前进至引信")
            if self.hold_until_result("w", random.uniform(6.8, 6.9)):
                return True
            self.set_phase("检测暴露引信")
            if not self.wait_until(self.has_fuse_prompt, time_out=2, raise_if_not_found=False):
                self.log_info("未识别到暴露引信交互提示，放弃重来")
                return False
            self.set_phase("操作引信")
            self.send_key(self.key_config["Interact Key"])
            if self.wait_for_result(random.uniform(0.3, 0.7)):
                return True
            self.send_key(self.key_config["Interact Key"])
            deadline = time.monotonic() + 70
            self.set_phase("等待战斗及撤离提示")
            combat_seen = False
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
            self.log_info("第二次操作引信后70秒内未出现撤离提示，放弃重来")
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
        self.log_info("三组撤离补救后仍未结算，放弃重来")
        return False

    def retry_mission(self):
        self.set_phase("放弃并重试")
        self.release_movement()
        self.give_up_mission()
        # The common give-up helper tolerates a missing result. This task must
        # confirm recovery before allowing any more movement or interactions.
        self.wait_until(
            lambda: self.find_result_again_btn() or self.is_mediation_start(),
            time_out=60, raise_if_not_found=True,
        )
