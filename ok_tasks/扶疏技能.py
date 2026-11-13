import time
from threading import Event

from src.tasks.BaseDNATask import BaseDNATask


class FushuSkill(BaseDNATask):
    # 框架参数沿用任务基类，读取技能联动所需的全局挂机设置，不改写保存值。
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "扶疏技能"
        self.description = "启动E-E-E-Q；Q每20秒一次，三连E每15秒一次；按键间隔0.5秒，Q后等待2秒才能按E；联动挂机设置及随机按键"
        self.instructions = '<a href="https://github.com/ok-oldking/ok-py">ok-py</a>'
        # 框架在等待期间每0.25秒提供新画面；识别结果跨越临时画面缓存的清理供后台读取。
        self.sleep_check_interval = 0.25
        self._fidget_game_state = (False, 0.0)
        # 每次启动都会替换信号，确保已停止的运行无法恢复后台动作。
        self._fidget_stop_event = Event()

    # 定期识别框架已获取的游戏画面，无返回值；只更新后台准入状态，不在后台线程截图。
    def sleep_check(self):
        # 识别期间先关闭准入；缺帧、菜单或暂停都不能继续使用上次的局内结果。
        self._fidget_game_state = (False, 0.0)
        if self.executor.paused or self.paused or not self.enabled:
            # 暂停和停止优先于画面判断，防止暂停时框架仍执行等待检查而重新放行输入。
            return
        # 沿用公共HUD识别及缺帧、识别异常的关闭策略，避免改动其他任务的判定规则。
        in_game = super().fidget_in_game()
        self._fidget_game_state = (in_game, time.monotonic())

    # 为公共防挂机返回最近一次有效的局内状态；超过1秒未刷新时禁止后台输入。
    def fidget_in_game(self):
        in_game, checked_at = self._fidget_game_state
        return (in_game and time.monotonic() - checked_at <= 1
                and self.executor.current_task is self and self.enabled
                and not self.executor.paused and not self.paused
                and not self._fidget_stop_event.is_set())

    # 持续发送技能并联动完整防挂机，无返回值；退出时停止并等待本次后台流程，避免重启叠加。
    def run(self):
        self._fidget_stop_event = Event()
        self._fidget_game_state = (False, 0.0)
        self.fidget_params['paused'] = False
        worker = None
        try:
            # 沿用一次性任务的激活流程；等待期间由框架刷新画面后再允许后台动作。
            if hasattr(self.executor.interaction, 'activate'):
                # 支持激活的交互实现先绑定游戏窗口，避免后台输入使用未激活的目标。
                self.executor.interaction.activate()
            self.sleep(0.5)
            # 公共流程直接读取两个鼠标开关，并同时执行既有随机按键，不修改全局配置。
            worker = self.setup_fidget_action()

            # 恢复原有技能周期；单调时钟避免系统时间调整影响冷却，周期从实际开始按键时计算。
            self.logger.info("扶疏技能循环已启动：Q每20秒，三连E每15秒；已联动挂机设置及随机按键")
            # 首次先完整释放三连E再按Q，各技能周期从各自首次按键开始计算。
            next_e = time.monotonic() + 15
            for _ in range(3):
                self.send_key("e")
                self.sleep(0.5)
            next_q = time.monotonic() + 20
            self.send_key("q")
            # Q释放后预留2秒动作时间，期间到期的E保留到等待结束后执行。
            self.sleep(2)

            while True:
                now = time.monotonic()
                if now >= next_q:
                    # 同时到期时先按Q，等待2秒后再处理已到期的三连E，不丢弃E。
                    next_q = now + 20
                    self.send_key("q")
                    self.sleep(2)

                now = time.monotonic()
                if now >= next_e:
                    # 每组E以第一下为周期起点；暂停恢复后不补发积压次数。
                    next_e = now + 15
                    for _ in range(3):
                        self.send_key("e")
                        self.sleep(0.5)

                # 等待较早到期的技能；使用框架等待，使空闲期间也能停止任务。
                self.sleep(max(0.01, min(next_q, next_e) - time.monotonic()))
        finally:
            # 先关闭本次准入，再等待正在执行的输入结束；保留任务原有的停止或异常传播。
            self._fidget_stop_event.set()
            self._fidget_game_state = (False, 0.0)
            self.set_fidget_actions_paused(True)
            if worker is not None:
                # 等旧线程完全结束才允许任务返回，快速重启也不会出现多个后台循环。
                try:
                    worker.result()
                except Exception as error:
                    # 后台异常记录一次，收尾时不覆盖主任务原本的停止或失败原因。
                    self.log_error("扶疏技能防挂机线程异常", error)

