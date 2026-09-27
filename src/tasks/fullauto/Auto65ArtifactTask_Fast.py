import time

from qfluentwidgets import FluentIcon

from ok import Logger, TaskDisabledException
from src.tasks.DNAOneTimeTask import DNAOneTimeTask
from src.tasks.CommissionsTask import CommissionsTask
from src.tasks.BaseCombatTask import BaseCombatTask

from src.tasks.AutoDefence import AutoDefence
from src.tasks.trigger.AutoLiseUltimateTask import AutoLiseUltimateTask

logger = Logger.get_logger(__name__)


class Auto65ArtifactTask_Fast(DNAOneTimeTask, CommissionsTask, BaseCombatTask):
    """
    自动30/65级魔之楔本；可组合执行录制移动、角色复位与黎瑟Q。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.icon = FluentIcon.FLAG
        self.name = "自动30/65级魔之楔本"
        self.description = "全自动"
        self.group_name = "全自动"
        self.group_icon = FluentIcon.CAFE

        self.setup_commission_config()
        substrings_to_remove = ["轮次"]
        keys_to_delete = [key for key in self.default_config for sub in substrings_to_remove if sub in key]
        for key in keys_to_delete:
            self.default_config.pop(key, None)

        self.default_config["是否移动"] = False
        self.config_description["是否移动"] = "进本后执行原版约20秒录制移动路线"
        self.default_config["是否重置角色位置"] = True
        self.config_description["是否重置角色位置"] = "移动步骤完成后打开设置并复位角色位置"
        self.default_config["是否黎瑟"] = False
        self.config_description["是否黎瑟"] = "移动/复位步骤完成3秒后按Q，并持续监控Q状态"
        self.default_config["最多场数"] = 10
        self.config_description["最多场数"] = "最多完成多少个战斗轮次（最小为1）"

        self.action_timeout = 10
        self._lise_ultimate_task = None
        self._lise_auto_started = False

    def run(self):
        """主运行方法"""
        DNAOneTimeTask.run(self)
        self.move_mouse_to_safe_position(save_current_pos=False)
        self.set_check_monthly_card()
        _to_do_task = self
        original_info_set = None
        try:
            _to_do_task = self.get_task_by_class(AutoDefence)
            self._lise_auto_started = False
            self._lise_ultimate_task = None
            if self.config.get("是否黎瑟", False):
                self._lise_ultimate_task = self.get_task_by_class(AutoLiseUltimateTask)
            immediate = self.should_start_immediately()
            _to_do_task.config_external_movement(
                self.walk_to_aim,
                self.config,
                immediate=immediate,
                restart_on_combat_timeout=False,
                runtime_tick=self.run_lise_ultimate_tick if self._lise_ultimate_task else None,
            )
            original_info_set = _to_do_task.info_set
            _to_do_task.info_set = self.info_set
            return _to_do_task.do_run()
        except TaskDisabledException:
            pass
        except Exception as e:
            logger.error("AutoMyDungeonTask error", e)
            raise
        finally:
            self._lise_auto_started = False
            if _to_do_task is not self:
                _to_do_task.external_runtime_tick = None
                if original_info_set is not None:
                    _to_do_task.info_set = original_info_set
            if self._lise_ultimate_task is not None:
                self._lise_ultimate_task.reset_detection()

    # def do_run(self):
    #     """执行任务的核心逻辑"""
    #     # 加载角色信息
    #     self.load_char()

    #     # 初始化变量
    #     _start_time = 0  # 任务开始时间
    #     _skill_time = 0  # 上次释放技能的时间
    #     _count = 0  # 完成次数计数器

    #     # 如果已经在队伍中，先放弃当前任务
    #     if self.in_team():
    #         self.log_info("检测到已在队伍中，先放弃当前任务")
    #         self.give_up_mission()
    #         self.wait_until(lambda: not self.in_team(), time_out=30, settle_time=1)

    #     # 主循环
    #     while True:
    #         # 在队伍中时的逻辑（战斗中）
    #         if self.in_team():
    #             # 第一次进入队伍时记录开始时间
    #             if _start_time == 0:
    #                 _start_time = time.time()
    #                 self.log_info(f"开始第 {_count + 1} 次任务")

    #             # 持续释放技能
    #             _skill_time = self.use_skill(_skill_time)

    #             # 检查是否超时
    #             elapsed = time.time() - _start_time
    #             if elapsed >= self.config.get("超时时间", 180):
    #                 logger.warning(f"任务超时 ({elapsed:.1f}秒)，重新开始...")
    #                 self.give_up_mission()
    #                 self.wait_until(lambda: not self.in_team(), time_out=30, settle_time=1)
    #                 _start_time = 0  # 重置计时器

    #         # 处理任务界面
    #         _status = self.handle_mission_interface()

    #         if _status == Mission.START:
    #             # 任务完成
    #             elapsed = time.time() - _start_time if _start_time > 0 else 0
    #             _count += 1
    #             self.log_info(
    #                 f"任务完成 [{_count}/{self.config.get('刷几次', 999)}] 用时: {elapsed:.1f}秒"
    #             )

    #             # 检查是否达到目标次数
    #             if _count >= self.config.get("刷几次", 999):
    #                 self.log_info(f"已完成全部 {_count} 次任务")
    #                 self.soundBeep()
    #                 return

    #             # 等待重新进入队伍
    #             self.wait_until(self.in_team, time_out=30)

    #             # 重置计时器
    #             _start_time = time.time()

    #             # 走到目标位置
    #             try:
    #                 self.walk_to_aim()
    #             except TaskDisabledException:
    #                 raise
    #             except Exception as e:
    #                 logger.error(f"移动到目标位置失败: {e}")
    #                 self.give_up_mission()
    #                 self.wait_until(lambda: not self.in_team(), time_out=30, settle_time=1)
    #                 _start_time = 0
    #             _skill_time = 0
    #         # 短暂休眠
    #         self.sleep(0.2)

    def walk_to_aim(self, delay=0):
        """按配置依次执行移动、复位、黎瑟Q。"""
        logger.info("进入副本，开始执行魔之楔前置步骤")
        self._lise_auto_started = False
        try:
            if self.config.get("是否移动", False):
                self.run_recorded_movement(delay=delay)

            if self.config.get("是否重置角色位置", True):
                if not self.reset_and_transport(verify_in_team=False):
                    logger.info("复位失败，重开任务...")
                    self.give_up_mission()
                    return False
                logger.info("复位完成")

            if self.config.get("是否黎瑟", False):
                logger.info("黎瑟模式：3秒后开启终结技")
                self.sleep(3)
                self.send_key(self.get_ultimate_key())
                logger.info("黎瑟模式：已发送终结技按键")
                if self._lise_ultimate_task is not None:
                    self._lise_ultimate_task.reset_detection()
                    self._lise_auto_started = True
                    logger.info("黎瑟模式：已启动Q自动开启监控")
            return True

        except TaskDisabledException:
            raise
        except Exception as e:
            logger.error("魔之楔前置步骤出错，重开任务", e)
            self.give_up_mission()
            return False

    def should_start_immediately(self):
        """只有无需移动且需要复位时，才由打开ESC菜单本身等待加载。"""
        return (not self.config.get("是否移动", False)
                and self.config.get("是否重置角色位置", True))

    def run_recorded_movement(self, delay=0):
        """恢复旧版扼守30/65录制路线；本方法只移动，不执行复位。"""
        logger.info("开始移动到目标位置")
        move_start = time.time()
        try:
            self.send_key_down("lalt")

            self.sleep(delay)
            self.send_key_down("w")

            self.sleep(0.59)
            self.send_key_down(self.get_dodge_key())

            self.sleep(0.22)
            self.send_key_down("a")

            self.sleep(1.08)
            self.send_key_up("w")

            self.sleep(1.44)
            self.send_key_down("w")

            self.sleep(0.09)
            self.send_key_up("a")

            self.sleep(0.90)
            self.send_key_down("a")

            self.sleep(0.38)
            self.send_key_up(self.get_dodge_key())
            self.sleep(0.24)
            self.send_key(self.get_dodge_key(), down_time=0.35)
            self.sleep(0.79)
            self.send_key(self.get_dodge_key(), down_time=0.41)
            self.sleep(0.80)
            self.send_key_down(self.get_dodge_key())

            self.sleep(1.27)
            self.send_key_up("w")

            self.sleep(0.47)
            self.send_key_down("w")

            self.sleep(0.35)
            self.send_key_up("w")

            self.sleep(0.79)
            self.send_key("space", down_time=0.09)

            self.sleep(2.04)
            self.send_key("s", down_time=0.09)

            self.sleep(0.40)
            self.send_key("w", down_time=0.10)

            self.sleep(0.44)
            self.send_key("s", down_time=0.10)

            self.sleep(4.93)
            self.send_key_up(self.get_dodge_key())
            self.sleep(0.10)
            self.send_key_up("a")

            self.send_key_up("lalt")
            elapsed = time.time() - move_start
            logger.info(f"移动完成，用时 {elapsed:.1f}秒")
        finally:
            self.release_recorded_movement_keys()

    def release_recorded_movement_keys(self):
        """确保录制路线结束、失败或被停止时不会留下按键。"""
        self.send_key_up("w")
        self.send_key_up("a")
        self.send_key_up("s")
        self.send_key_up("d")
        self.send_key_up(self.get_dodge_key())
        self.send_key_up("lalt")

    def run_lise_ultimate_tick(self):
        """仅在当前魔之楔任务完成首次Q后复用黎瑟自动开启检测。"""
        if not self._lise_auto_started or self._lise_ultimate_task is None:
            return
        return self._lise_ultimate_task.run()
