"""迷津首战刷取：所有退出路径只停止输入，不额外关闭 Q。"""
import time

from qfluentwidgets import FluentIcon

from src.dna_ui.Defs import COORD
from src.tasks.BaseDNATask import BaseDNATask
from src.tasks.DNAOneTimeTask import DNAOneTimeTask
from src.tasks.trigger.AutoLiseUltimateTask import (
    CHECK_INTERVAL, MAX_PLAUSIBLE_COST, PRESS_DOWN_TIME, RETRY_COOLDOWN,
    LiseUltimateMixin,
)


class AutoMijinTask(DNAOneTimeTask, LiseUltimateMixin, BaseDNATask):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "迷津"
        self.description = "识别当前迷津页面继续循环；Q 开启 5 秒、关闭 3 秒，战斗超时 60 秒，未知画面等待"
        self.group_name = "全自动"
        self.group_icon = FluentIcon.CAFE
        self.icon = FluentIcon.FLAG
        # 此任务只由已识别节点驱动输入，防挂机线程不能在未知画面移动鼠标或随机按键。
        self.enable_fidget_action = False

    def reset_q_cycle(self):
        self.q_state = None
        self.q_candidate = None
        self.q_streak = 0
        self.q_since = None
        self.q_target = None
        self.q_initial = True
        self.q_next_check = 0.0
        self.q_last_press = float('-inf')

    def reset_round(self):
        self.candle_seen = False
        self.battle_since = None
        self.round_success = None
        self.round_active = False
        self.revive_pending = False
        self.reset_q_cycle()

    def reset_runtime(self):
        self.reset_round()
        self.stage = None
        self.last_action = float('-inf')
        self.last_wait_log = float('-inf')

    def reset_statistics(self):
        self.success_count = 0
        self.failure_count = 0
        self.update_statistics()

    def update_statistics(self):
        self.info_set('成功次数', self.success_count)
        self.info_set('失败次数', self.failure_count)

    def begin_exit(self, now, success):
        # 一旦决定退出，本轮不再恢复技能，也不被后续画面改变结果。
        if self.round_success is None:
            self.round_success = success
            self.log_info('迷津：准备结算（' + ('成功' if success else '失败') + '）')

    def set_stage(self, stage, now):
        if self.stage == stage:
            return
        self.stage = stage
        labels = {
            'home': '迷津入口', 'start': '开始探索', 'fight': '战斗',
            'revive': '复苏', 'candle': '获得烛芯', 'continue': '继续探索',
            'exit': '退出并结算', 'confirm': '退出确认', 'result': '探索失败结算',
            'world': '局内画面', 'unknown': '等待识别画面',
        }
        self.info_set('当前阶段', labels[stage])
        if stage != 'unknown':
            self.log_info(f"迷津：{labels[stage]}")

    def has_text(self, area, text):
        results = self.ocr(box=self.screen_box('MIJIN_' + area + '_OCR'))
        # OCR 可能将一行拆成多个框；先合并再去空白和标点。
        content = ''.join(item.name for item in results or ())
        return text in ''.join(char for char in content if char.isalnum())

    def act(self, now, coord=None, key=None):
        if now - self.last_action < 1.0:
            return
        self.last_action = now
        if key is not None:
            self.send_key(key)
        else:
            self.click_ui_coord(coord, name='mijin_' + self.stage)

    def tick_q(self, now):
        if now < self.q_next_check:
            return
        self.q_next_check = now + CHECK_INTERVAL
        cost = self._read_hud_number('LISE_Q_COST_OCR')
        if cost is None or not 0 <= cost <= MAX_PLAUSIBLE_COST:
            self.q_candidate = None
            self.q_streak = 0
            return
        active = cost == 0
        if active == self.q_candidate:
            self.q_streak += 1
        else:
            self.q_candidate = active
            self.q_streak = 1
        if self.q_streak < 2:
            return
        if active != self.q_state:
            self.q_state = active
            self.q_since = now
            self.log_info('迷津：Q 已确认' + ('开启' if active else '关闭'))
        if self.q_target == active:
            self.q_target = None
        if self.q_initial:
            self.q_initial = False
            if not active:
                self.q_target = True
        if self.q_target is None and now - self.q_since >= (5.0 if active else 3.0):
            self.q_target = not active
        if self.q_target is not None and now - self.q_last_press >= RETRY_COOLDOWN:
            self.q_last_press = now
            self.send_key(self.key_config['Ultimate Key'], down_time=PRESS_DOWN_TIME)
            # 每次发送后必须用新读数再次确认，未知读数不触发重试。
            self.q_candidate = None
            self.q_streak = 0

    def detect_node(self):
        # 弹窗/菜单/结算必须先于底下透出的战斗 HUD。识别期间不发送输入。
        for node, area, text in (
            ('result', 'RESULT', '探索失败'),
            ('confirm', 'CONFIRM', '是否结束当前探索并结算'),
            ('exit', 'EXIT', '退出并结算'),
            ('candle', 'CANDLE', '获得烛芯'),
            ('revive', 'REVIVE', '复苏'),
            ('home', 'ENTER', '坠入深渊'),
            ('start', 'START', '开始探索'),
            ('continue', 'OBJECTIVE', '继续探索'),
            ('fight', 'OBJECTIVE', '战斗'),
        ):
            if self.has_text(area, text):
                return node
        return 'world' if self.in_team() else 'unknown'

    def finish_round(self):
        if not self.round_active:
            return
        if self.round_success is True:
            self.success_count += 1
        else:
            self.failure_count += 1
        self.update_statistics()
        self.log_info('迷津：本轮结束，开始下一轮')
        self.reset_round()

    def tick(self, now):
        node = self.detect_node()
        self.set_stage(node, now)
        if node != 'fight':
            self.q_candidate = None
            self.q_streak = 0

        if node == 'home':
            self.finish_round()
            self.act(now, COORD.MIJIN_ENTER)
            return
        if node not in ('unknown', 'world'):
            self.round_active = True

        if node in ('exit', 'confirm', 'result'):
            # 接续菜单/结算时没有结果记录，默认失败；已决定的正常结算仍算成功。
            self.begin_exit(now, success=False)
            coord = {'exit': COORD.MIJIN_EXIT, 'confirm': COORD.MIJIN_CONFIRM,
                     'result': COORD.MIJIN_BLANK}[node]
            self.act(now, coord)
            return

        if self.round_success is None:
            if node == 'continue' and self.candle_seen:
                self.begin_exit(now, success=True)
            elif node == 'fight' and self.revive_pending and self.in_team():
                self.revive_pending = False
                self.battle_since = now
                self.reset_q_cycle()
            if (self.round_success is None and self.battle_since is not None
                    and now - self.battle_since >= 60.0):
                self.log_info('迷津：战斗超时，准备退出本轮')
                self.begin_exit(now, success=False)

        if node == 'unknown':
            if now - self.last_wait_log >= 5.0:
                self.last_wait_log = now
                self.log_info('迷津：当前画面无法识别，原地等待')
            return

        if node == 'candle':
            if not self.candle_seen:
                self.candle_seen = True
                self.reset_q_cycle()
            self.act(now, COORD.MIJIN_BLANK)
        elif node == 'revive':
            if not self.revive_pending:
                self.revive_pending = True
                self.reset_q_cycle()
            # 超时后仍可按 X 恢复到能打开退出菜单的画面，但不恢复 Q 或重置结果。
            self.act(now, key='x')
        elif self.round_success is not None:
            # 退出意图独立于页面节点。只在确认的局内画面发送 Esc。
            if node in ('fight', 'continue', 'world') and self.in_team():
                self.act(now, key='esc')
        elif node == 'start':
            self.act(now, COORD.MIJIN_START)
        elif node == 'fight':
            if self.battle_since is None:
                self.battle_since = now
            if self.in_team():
                self.tick_q(now)
            else:
                self.q_candidate = None
                self.q_streak = 0

    def run(self):
        DNAOneTimeTask.run(self)
        self.reset_statistics()
        self.reset_runtime()
        # 不注册技能 ticker 或清理 Q 的动作；停止任务只中断轮询。
        while True:
            self.tick(time.monotonic())
            self.sleep(0.2)
