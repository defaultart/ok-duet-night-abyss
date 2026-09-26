from qfluentwidgets import FluentIcon
import time
from typing import Callable

from ok import Logger, TaskDisabledException
from src.tasks.DNAOneTimeTask import DNAOneTimeTask
from src.tasks.BaseCombatTask import BaseCombatTask
from src.tasks.CommissionsTask import CommissionsTask, Mission, QuickAssistTask, _default_movement

logger = Logger.get_logger(__name__)


class AutoDefence(DNAOneTimeTask, CommissionsTask, BaseCombatTask):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.icon = FluentIcon.FLAG
        self.name = "自动扼守"
        self.description = "半自动"
        self.group_name = "半自动"
        self.group_icon = FluentIcon.VIEW

        self.setup_commission_config()
        self.setup_mission_start_config()
        self.setup_combat_detection_config()

        self.config_description.update({
            "超时时间": "波次超时后将发出提示",
        })

        self.quick_assist_task = QuickAssistTask(self)
        self.external_movement = _default_movement
        self._external_config = None
        self._merged_config_cache = None
        self.external_movement_immediate = False
        self.restart_on_external_combat_timeout = True
        self.external_runtime_tick = None
        self.skill_tick = self.create_skill_ticker()

    @property
    def config(self):
        if self.external_movement == _default_movement or not self._external_config:
            return getattr(self, '_base_config', None)

        if self._merged_config_cache is None:
            base = getattr(self, '_base_config', {})
            self._merged_config_cache = base.copy() if base else {}
            self._merged_config_cache.update(self._external_config)
        return self._merged_config_cache

    @config.setter
    def config(self, value):
        self._base_config = value
        self._merged_config_cache = None

    def config_external_movement(self, func: Callable, config: dict, *, immediate=False,
                                 restart_on_combat_timeout=True, runtime_tick=None):
        if callable(func):
            self.external_movement = func
        else:
            self.external_movement = _default_movement
        self.external_movement_immediate = bool(immediate)
        self.restart_on_external_combat_timeout = bool(restart_on_combat_timeout)
        self.external_runtime_tick = runtime_tick if callable(runtime_tick) else None
        self._merged_config_cache = None
        self._external_config = config

    def run(self):
        DNAOneTimeTask.run(self)
        self.move_mouse_to_safe_position(save_current_pos=False)
        self.set_check_monthly_card()
        self.external_movement = _default_movement
        self.external_runtime_tick = None
        try:
            return self.do_run()
        except TaskDisabledException:
            pass
        except Exception as e:
            logger.error("AutoDefence error", e)
            raise

    def do_run(self):
        self.completed_mission_rounds = 0
        self._round_dialog_counted = False
        self._external_round_started = False
        self.init_all()
        self.load_char()

        if (round_limit := self.external_round_limit()) is not None:
            self.info_set("完成场数", f"0/{round_limit}")

        if self.external_movement is not _default_movement and self.in_team():
            self.open_in_mission_menu()

        while True:
            if self.in_team():
                self._run_external_runtime_tick()
                # 下一轮已经真正回到局内后，允许下一个行动抉择弹窗计数。
                self._round_dialog_counted = False
                if self.move_on_begin():
                    self.handle_in_mission()

            # 魔之楔实测完成一轮后可能进入“再次进行”结算页，也可能直接
            # 回到开始页。只有成功复位过的轮次才允许在这里计数，避免把
            # 程序刚启动时看到的初始开始页误算成第 1 场。
            if self.handle_external_round_completion():
                return

            _status = self.handle_mission_interface(stop_func=self.stop_func)
            if _status == Mission.START:
                if not self.external_movement_immediate:
                    self.wait_until(self.in_team, time_out=30)
                self.init_all()
                self.handle_mission_start()
            elif _status == Mission.STOP:
                self.log_info("任务中止")
                self.quit_mission()
                if self.reached_external_round_limit():
                    round_limit = self.external_round_limit()
                    self.log_info(f"已完成全部 {round_limit} 场，任务结束")
                    self.soundBeep()
                    return
            elif _status == Mission.CONTINUE:
                self.log_info("任务继续")
                self.init_for_next_round()
                self._round_dialog_counted = False
                self._external_round_started = True
                self.wait_until(self.in_team, time_out=self.action_timeout)

            self.sleep(0.1)

    def init_all(self):
        self.init_for_next_round()
        self.skill_tick.reset()
        self.current_round = 0
        self._mission_started = False

    def init_for_next_round(self):
        self.init_runtime_state()

    def init_runtime_state(self):
        self.runtime_state = {"wave_start_time": 0, "wave": -1, "wait_next_wave": False}
        self.reset_wave_info()

    def handle_in_mission(self):
        """处理在副本中的逻辑"""
        self.get_wave_info()
        in_combat = self.current_wave != -1
        if in_combat:
            # 如果是新的波次，重置状态
            if self.current_wave != self.runtime_state["wave"]:
                self.runtime_state.update(
                    {"wave": self.current_wave, "wave_start_time": time.time(), "wait_next_wave": False})
                self.quick_assist_task.reset()

            # 检查波次是否超时
            if not self.runtime_state["wait_next_wave"] and self.combat_detection_enabled() and time.time() - self.runtime_state[
                "wave_start_time"] >= self.config.get("超时时间", 120):
                if self.external_movement is not _default_movement:
                    self.log_info("任务超时")
                    self.give_up_mission()
                    return
                else:
                    self.log_info_notify("任务超时")
                    self.soundBeep()
                    self.runtime_state["wait_next_wave"] = True

        else:
            if self.runtime_state["wave"] > 0:
                self.init_runtime_state()
            # 如果不在战斗波次中，执行移动任务
            self.quick_assist_task.run()

        # 战斗侦测关掉时不等波次，局内就按技能
        if not self.runtime_state["wait_next_wave"] and self.skills_ready(in_combat):
            self.skill_tick()

    def handle_mission_start(self):
        """处理任务开始的逻辑"""
        if self.external_movement is not _default_movement:
            self.log_info("任务开始，执行外部移动逻辑")
            movement_result = self.external_movement(delay=2)
            # 复位失败时 walk_to_aim 会调用 give_up_mission() 退出副本后直接 return，
            # 此时已不在队伍中，应立即返回让主循环重新识别界面并重开任务
            if movement_result is False or (movement_result is not True and not self.in_team()):
                self.log_info("外部移动后未在队伍中（可能复位失败已退出副本），等待重开任务")
                return
            self._round_dialog_counted = False
            self._external_round_started = True
            time_out = self.action_timeout + 10
            self.log_info(f"外部移动执行完毕，等待战斗开始，{time_out}秒后超时")
            if not self.wait_until(lambda: self.current_wave != -1 or self.find_esc_menu(),
                                   post_action=self._update_external_start_wait,
                                   time_out=time_out):
                if self.restart_on_external_combat_timeout:
                    self.log_info("等待战斗开始超时，重开任务")
                    self.open_in_mission_menu()
                else:
                    self.log_info("等待战斗开始超时，继续观察（不会自动打开ESC）")
            else:
                self.log_info("战斗开始")
        else:
            self.sleep(2)
            self.log_info_notify("任务开始")
            self.soundBeep()

    def _run_external_runtime_tick(self):
        runtime_tick = getattr(self, "external_runtime_tick", None)
        if callable(runtime_tick):
            return runtime_tick()

    def _update_external_start_wait(self):
        """等待开战时继续刷新波次，并让外部任务的局内监控保持运行。"""
        self.get_wave_info()
        if self.in_team():
            self._run_external_runtime_tick()

    def stop_func(self):
        if (round_limit := self.external_round_limit()) is not None:
            if not self._round_dialog_counted:
                self.completed_mission_rounds += 1
                self._round_dialog_counted = True
                self.info_set(
                    "完成场数",
                    f"{self.completed_mission_rounds}/{round_limit}",
                )
            return self.completed_mission_rounds >= round_limit

        self.get_round_info()
        n = self.config.get("轮次", 3)
        if self.current_round >= n:
            return True

    def handle_external_round_completion(self):
        """在魔之楔完成页计数；达到上限时停止任务且不再点击当前画面。"""
        if self.external_round_limit() is None or not self._external_round_started:
            return False

        on_result = bool(self.find_result_again_btn())
        on_start = not on_result and bool(self.find_start_interface())
        if not (on_result or on_start):
            return False

        reached_limit = self.stop_func()
        self._external_round_started = False
        if not reached_limit:
            return False

        round_limit = self.external_round_limit()
        page_name = "结算界面" if on_result else "开始界面"
        self.log_info(f"处理任务界面: 达到场数上限，停留在{page_name}")
        self.log_info(f"已完成全部 {round_limit} 场，任务结束")
        self.soundBeep()
        return True

    def external_round_limit(self):
        """全自动外部路线的轮次上限；普通半自动扼守不启用。"""
        if self.external_movement is _default_movement:
            return None
        if not isinstance(self._external_config, dict) or "最多场数" not in self._external_config:
            return None
        try:
            value = int(self.config.get("最多场数", 10))
        except (TypeError, ValueError):
            value = 10
        return value if value >= 1 else 10

    def reached_external_round_limit(self):
        round_limit = self.external_round_limit()
        return round_limit is not None and self.completed_mission_rounds >= round_limit
