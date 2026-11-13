from qfluentwidgets import FluentIcon
import time
import random

from ok import Logger, TaskDisabledException, WaitFailedException
from src.tasks.DNAOneTimeTask import DNAOneTimeTask
from src.tasks.BaseCombatTask import BaseCombatTask
from src.tasks.CommissionsTask import CommissionsTask, Mission, MissionRecognitionTimeout

logger = Logger.get_logger(__name__)


class AutoExpulsion(DNAOneTimeTask, CommissionsTask, BaseCombatTask):

    # 驱离长期挂机允许等待界面恢复，不沿用半自动任务的识别超时停止策略。
    retry_mission_recognition = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.icon = FluentIcon.FLAG
        self.name = "自动驱离"
        self.description = "全自动"
        self.group_name = "全自动"
        self.group_icon = FluentIcon.CAFE

        self.setup_commission_config()
        self.setup_mission_start_config()
        self.default_config["黎瑟 Q 自动开启"] = False
        self.config_description["黎瑟 Q 自动开启"] = "复用实时触发：Q 关闭且黄色能量大于 100 时自动开启"
        keys_to_remove = ["轮次"]
        for key in keys_to_remove:
            self.default_config.pop(key, None)

        # 云游戏实拍帧糊、模板分低，识别弹窗会有假阴性，给足重试轮次
        self.action_timeout = 20
        
        self.skill_tick = self.create_skill_ticker()
        self.random_walk_tick = self.create_random_walk_ticker()

    def run(self):
        DNAOneTimeTask.run(self)
        self.move_mouse_to_safe_position(save_current_pos=False)
        self.set_check_monthly_card()
        try:
            return self.do_run()
        except TaskDisabledException:
            pass
        except Exception as e:
            logger.error("自动驱离执行异常", e)
            raise

    def do_run(self):
        self.init_all()
        self.load_char()
        self.count = 0
        # 恢复期间只保留开始意图，真正进入队伍后再初始化，避免重复计数或丢失计时。
        pending_start = False
        # 使用单调时钟限频，连续失败首次和每隔 30 秒提示，恢复后只提示一次。
        recovery_logged_at = None
        while True:
            recognition_failure = None
            try:
                if self.in_team():
                    if pending_start or self.mission_status == Mission.START:
                        # 只有明确进入新一局才重置；场景短暂消失后恢复到同一局不重置。
                        self.init_all()
                        self.mission_status = None
                        pending_start = False
                        self.handle_mission_start()
                    self.handle_in_mission()

                # 每次重试均读取最新画面，未知场景不主动按 ESC 或执行坐标点击。
                _status = self.handle_mission_interface(stop_func=self.stop_func)
                if _status == Mission.START:
                    # 不在加载超时后提前初始化，保留意图直到下一次识别到队伍。
                    pending_start = True
                elif _status is False and not self.in_team():
                    # False 表示未匹配界面；局内的正常返回不能误判成识别失败。
                    recognition_failure = "当前场景无法识别"
            except (MissionRecognitionTimeout, WaitFailedException) as e:
                # 框架的界面等待（如开局复位）也可能超时；只恢复识别失败，不捕获停止或其他错误。
                recognition_failure = str(e) or "等待任务界面切换超时"

            if recognition_failure is not None:
                now = time.monotonic()
                if recovery_logged_at is None or now - recovery_logged_at >= 30:
                    # 长时间未知场景持续等待，限频提示原因，不设置自动停止上限。
                    self.log_info(f"自动驱离等待恢复：{recognition_failure}，1 秒后重新识别")
                    recovery_logged_at = now
                # 使用框架 sleep 保持手动停止可响应，不在异常分支吞掉停止信号。
                self.sleep(1)
                continue

            if recovery_logged_at is not None:
                # 成功处理已知界面或回到局内后结束本次恢复周期。
                self.log_info("自动驱离已识别到任务场景，继续运行")
                recovery_logged_at = None
            self.sleep(0.1)

    def init_all(self):
        self.init_for_next_round()
        self.skill_tick.reset()
        self.reset_detection()
        self.current_round = 0
        self._mission_started = False

    def init_for_next_round(self):
        self.init_runtime_state()

    def init_runtime_state(self):
        self.runtime_state = {"start_time": 0}
        self.random_walk_tick.reset()

    def handle_in_mission(self):
        if self.runtime_state["start_time"] == 0:
            if not self.move_on_begin():
                return
            self.runtime_state["start_time"] = time.time()
            self.count += 1

        if time.time() - self.runtime_state["start_time"] >= self.config.get("超时时间", 120):
            logger.info("已经超时，重开任务...")
            self.give_up_mission()
            return

        self.random_walk_tick()
        self.skill_tick()

    def handle_mission_start(self):
        self.sleep(2)
        self.log_info("任务开始")

    def tick_enabled_lise_ultimate(self):
        if self.config.get("黎瑟 Q 自动开启", False):
            return self.check_lise_ultimate()
        return super().tick_enabled_lise_ultimate()
    
    def stop_func(self):
        pass

    def create_random_walk_ticker(self):
        """创建一个随机游走的计时器函数。"""
        def action():
            if not self.config.get("随机游走", False):
                return
            duration = random.uniform(0, 1)
            direction = random.choice(["w", "a", "s", "d"])
            self.send_key(direction, down_time=duration)

        return self.create_ticker(action, interval=5, interval_random_range=(0.8, 2))
