import random
import time

from ok import TaskDisabledException
from ok.device.interaction_methods.post_message import PostMessageInteraction
from qfluentwidgets import FluentIcon

from src.dna_ui.Defs import COORD
from src.tasks.CommissionsTask import CommissionsTask, Mission
from src.tasks.DNAOneTimeTask import DNAOneTimeTask


# None means a stationary wait. Only the first duration excludes the walking lead-in.
ROUTE = (
    ("w", 3.0, 3.1),
    ("d", 0.6, 0.7),
    ("w", 1.0, 1.2),
    (None, 1.0, 1.2),
    ("d", 1.8, 2.0),
    (None, 9.0, 10.0),
    ("w", 2.7, 2.8),
    (None, 9.0, 10.0),
    ("w", 2.9, 3.0),
    (None, 7.0, 8.0),
    ("w", 2.0, 2.1),
    (None, 4.0, 5.0),
    ("d", 0.8, 0.9),
    ("w", 7.0, 7.5),
)


class AutoWeaponExp60Task(DNAOneTimeTask, CommissionsTask):
    """从已选关卡开始，按固定路线循环刷取武器经验。"""

    failure_message = "60级武器经验任务无法继续，请检查当前界面"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "60级武器经验"
        self.description = "在已选好60级的挑战界面或结算页启动，保留当前选项，持续重刷直到手动停止"
        self.group_name = "半自动"
        self.group_icon = FluentIcon.VIEW
        self.icon = FluentIcon.FLAG
        self.lise_enabled = False

    def set_phase(self, phase):
        self.info_set("当前阶段", phase)

    def release_movement(self):
        try:
            self.send_key_up("w")
        finally:
            try:
                self.send_key_up("d")
            finally:
                try:
                    self.send_key_up("shift")
                finally:
                    self.send_key_up("space")

    def run(self):
        previous_pause = self.set_fidget_actions_paused(True)
        try:
            DNAOneTimeTask.run(self)
            self.move_mouse_to_safe_position(save_current_pos=False)
            self.set_check_monthly_card()
            self.do_run()
        except TaskDisabledException:
            pass
        except Exception:
            self.soundBeep()
            self.log_info_notify(self.failure_message)
            raise
        finally:
            self.lise_enabled = False
            try:
                self.release_movement()
            finally:
                self.set_fidget_actions_paused(previous_pause)

    def confirm_start(self):
        """只确认开始，不读取委托配置或更改手册选项。"""
        self.mission_status = Mission.START

        def click_confirm():
            if self.find_manual_select_btn():
                self.click_ui_coord(COORD.MANUAL_CONFIRM, name="weapon_exp_start", after_sleep=0.25)

        self.wait_until(
            lambda: not self.find_manual_select_btn(),
            post_action=click_confirm,
            time_out=self.action_timeout,
            raise_if_not_found=True,
        )

    def do_run(self):
        if not (self.find_result_again_btn() or self.find_start_interface()):
            raise RuntimeError("请先停在已选好60级的挑战界面或结算页再启动任务")
        while True:
            self.set_phase("等待进入挑战")
            if self.find_result_again_btn() or self.find_start_interface():
                self.mission_status = Mission.START
                self.start_mission()
            elif self.find_manual_select_btn():
                self.confirm_start()
                self.wait_until(self.in_team, time_out=60, raise_if_not_found=True)
            elif self.in_team():
                if not self.run_attempt():
                    self.retry_mission()
            else:
                self.wait_until(
                    lambda: self.find_result_again_btn() or self.find_start_interface()
                    or self.find_manual_select_btn() or self.in_team(),
                    time_out=60, raise_if_not_found=True,
                )
            self.sleep(0.1)

    def wait_until_result(self, deadline):
        """按绝对截止时间等待，识别耗时也计入当前动作。"""
        while True:
            self.next_frame()
            if self.find_result_again_btn():
                self.lise_enabled = False
                return True
            if time.monotonic() >= deadline:
                return False
            if self.lise_enabled:
                self.check_lise_ultimate()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            self.sleep(min(0.05, remaining))

    def send_key_down_without_activation(self, key):
        interaction = self.executor.interaction
        if isinstance(interaction, PostMessageInteraction):
            key = self.validate_key(key)
            self.executor.reset_scene()
            interaction.send_key_down(key, activate=False)
        else:
            self.send_key_down(key)

    def sprint(self, key, duration, shift_delay=(0.05, 0.15), duration_after_shift=False):
        try:
            self.send_key_down(key)
            started = time.monotonic()
            if duration_after_shift:
                self.log_debug(f"首段纯W开始：t={started:.3f}")
            if self.wait_until_result(started + random.uniform(*shift_delay)):
                return True
            if duration_after_shift:
                self.send_key_down_without_activation("shift")
                started = time.monotonic()
                self.log_debug(f"首段Shift已发送：t={started:.3f}")
                self.send_key_down_without_activation(key)
                self.log_debug(f"首段W已补发：t={time.monotonic():.3f}")
            else:
                self.send_key_down("shift")
            return self.wait_until_result(started + duration)
        finally:
            try:
                self.send_key_up(key)
            finally:
                self.send_key_up("shift")
                if duration_after_shift:
                    self.log_debug(f"首段W和Shift已释放：t={time.monotonic():.3f}")

    def jump_five_times(self):
        try:
            self.send_key_down("w")
            for index in range(5):
                self.set_phase(f"跳跃（{index + 1}/5）")
                self.send_key_down("space")
                pressed_at = time.monotonic()
                try:
                    if self.wait_until_result(pressed_at + 0.1):
                        return True
                finally:
                    self.send_key_up("space")
                if index < 4 and self.wait_until_result(pressed_at + random.uniform(0.2, 0.4)):
                    return True
        finally:
            self.release_movement()
        self.set_phase("等待结算")
        return self.wait_until_result(time.monotonic() + 3)

    def run_attempt(self):
        self.lise_enabled = False
        self.reset_detection()
        try:
            for index, (key, low, high) in enumerate(ROUTE):
                duration = random.uniform(low, high)
                self.set_phase(f"路线（{index + 1}/{len(ROUTE)}）：{key.upper() if key else '等待'}")
                if key:
                    if index == 0:
                        ended = self.sprint(key, duration, shift_delay=(3.5, 3.7), duration_after_shift=True)
                    else:
                        ended = self.sprint(key, duration)
                else:
                    ended = self.wait_until_result(time.monotonic() + duration)
                if ended:
                    return True
                if index == 0:
                    self.lise_enabled = True
            return self.jump_five_times()
        finally:
            self.lise_enabled = False
            self.release_movement()

    def retry_mission(self):
        self.lise_enabled = False
        self.set_phase("未结算，放弃并重试")
        self.release_movement()
        self.give_up_mission()
        self.wait_until(
            lambda: self.find_result_again_btn() or self.find_start_interface(),
            time_out=60, raise_if_not_found=True,
        )
