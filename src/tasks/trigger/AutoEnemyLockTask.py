import json
import math
import random
import time
from importlib import import_module
from threading import Event, RLock, Thread

import cv2
import numpy as np
import win32api
import win32con
from ok import Logger, TriggerTask
from ok.core.events import communicate

from src.tasks.BaseDNATask import BaseDNATask


logger = Logger.get_logger(__name__)


class AutoEnemyLockTask(BaseDNATask, TriggerTask):
    """逐帧跟随完整敌方血条中心及配置偏移，仅调整准星，不执行射击。"""

    # 每项依次声明默认值、中文含义、允许下限和上限；同一份定义用于界面及运行校验。
    PARAMETERS = {
        "锁定模式": ("持续跟踪", "每帧跟随当前敌人；随机换人仅在间隔到期时切换", None, None),
        "换人间隔": (3.1, "随机换人间隔，单位秒，不影响当前目标的逐帧跟踪", 0.001, None),
        "水平偏移": (0, "完整血条中心向右偏移，单位为基准像素，可为负数", None, None),
        "垂直偏移": (60, "完整血条中心向下偏移，单位为基准像素；零表示血条中心", None, None),
        "跟随强度": (0.3, "每次识别修正的误差比例，越大跟随越快，范围大于零至一", 0.000001, 1),
        "红色最低亮度": (110, "红色通道最低亮度，范围零至255", 0, 255),
        "红色优势倍数": (1.35, "红色须超过蓝色和绿色的倍数，降低可兼容偏淡血条", 1, None),
        "红色最小差值": (25, "红色比蓝色和绿色至少高出的亮度，范围零至255", 0, 255),
        "红段最小宽度": (6, "残血红段最小宽度，单位为基准像素", 1, 1920),
        "红段最大宽度": (260, "红色候选最大宽度，单位为基准像素", 1, 1920),
        "红段最小高度": (1, "红色候选最小高度，单位为基准像素", 1, 1080),
        "红段最大高度": (16, "红色候选最大高度，单位为基准像素", 1, 1080),
        "红段最小宽高比": (2.0, "候选宽高比下限，降低可兼容短残血", 0, None),
        "红段最小填充率": (0.4, "候选矩形内红色占比下限，范围零至一", 0, 1),
        "红段闭运算宽度": (5, "连接红段水平断口的核宽，单位基准像素；一表示不连接", 1, 1920),
        "红段开运算宽度": (5, "清理红色噪点的核宽，单位基准像素；一表示不清理", 1, 1920),
        "轨道左右搜索范围": (300, "红段左右恢复完整轨道的最大距离，单位基准像素", 1, 1920),
        "轨道上下搜索范围": (6, "红段上下寻找边框的距离，单位基准像素", 1, 1080),
        "轨道最小宽度": (24, "完整轨道最小宽度，不是残血段宽度，单位基准像素", 1, 1920),
        "轨道最大宽度": (320, "完整轨道最大宽度，兼容精英装饰条，单位基准像素", 1, 1920),
        "轨道最小高度": (3, "完整轨道内部最小高度，排除装饰细线，单位基准像素", 1, 1080),
        "轨道最大高度": (24, "完整轨道内部最大高度，单位基准像素", 1, 1080),
        "轨道最小对比": (8, "红色填充与暗色轨道的最小灰度差", 0, 255),
        "轨道灰度余量": (15, "局部暗边允许的亮度浮动，范围零至255", 0, 255),
        "轨道最大灰度": (100, "轨道暗底亮度上限，避免连入大面积背景", 0, 255),
        "轨道断口容限": (4, "边框允许补合的水平缺口宽度，单位基准像素；零表示不补", 0, 1920),
        "轨道边框偏差": (10, "上下边框左右端点允许偏差，兼容斜角，单位基准像素", 0, 1920),
        "轨道边框支持率": (0.55, "恢复边框中原始暗像素占比下限，范围零至一", 0, 1),
        "轨道红段覆盖率": (0.65, "完整轨道须包含的候选红段宽度比例，允许等级数字粘连", 0, 1),
        "轨道红段起点容差": (6, "红段起点可落后轨道左端的距离，单位基准像素，增大可兼容左端遮挡", 0, 1920),
        "允许单侧边框": (True, "上下边框仅一侧可确认完整左右边界时也接受", None, None),
        "轨道去重重叠率": (0.75, "同一高度轨道的水平重叠比例达到此值才合并", 0, 1),
        "轨道去重纵向距离": (5, "重复轨道中心纵向最大距离，单位基准像素", 0, 1080),
        "搜索区域": ("[[0,0,1920,1080]]", "矩形列表JSON，格式[[左,上,右,下]]，坐标基准1920×1080", None, None),
        "HUD屏蔽区域": ("[[0,0,360,300],[0,300,290,530],[0,0,1920,100],[0,880,1920,1080]]", "忽略的矩形列表JSON；空列表表示不屏蔽，坐标基准1920×1080", None, None),
        "关联基础距离": (90.0, "短遮挡后关联当前位置的基础距离，单位基准像素", 0, None),
        "长遮挡关联距离": (60.0, "超过遮挡时限后的基础关联距离，单位基准像素", 0, None),
        "关联速度补偿": (1.0, "关联距离附加速度乘帧间隔的系数；零表示不扩大", 0, None),
        "关联预测时长": (0.1, "位置预测及速度补偿的最大时长，单位秒；零表示不预测", 0, None),
        "关联遮挡时限": (0.3, "超过此秒数采用长遮挡关联条件", 0, None),
        "关联最小宽度比": (0.6, "短遮挡后新旧完整轨道宽度比下限", 0.000001, None),
        "关联最大宽度比": (1.6, "短遮挡后新旧完整轨道宽度比上限", 0.000001, None),
        "长遮挡最小宽度比": (0.8, "长遮挡后新旧完整轨道宽度比下限", 0.000001, None),
        "长遮挡最大宽度比": (1.25, "长遮挡后新旧完整轨道宽度比上限", 0.000001, None),
        "关联宽度权重": (0.3, "宽度变化在关联评分中的惩罚权重", 0, None),
        "关联重叠权重": (15.0, "预测轨道不重叠时的关联评分惩罚权重", 0, None),
        "关联歧义差值": (5.0, "前两名评分差小于此值时停止本帧；零表示总选最佳候选", 0, None),
        "关联速度平滑": (0.5, "新测速度的权重，范围零至一", 0, 1),
        "关联速度上限": (1200.0, "每轴最大预测速度，单位基准像素每秒", 0, None),
        "停止误差": (4.0, "每轴误差进入此距离即停止，单位基准像素", 0, None),
        "启动误差": (8.0, "每轴误差超过此距离重新跟随，单位基准像素", 0, None),
        "单帧最大修正": (40.0, "每次识别每轴修正上限，灵敏度换算前的基准像素", 0.000001, None),
        "过冲自动衰减": (False, "越过目标时是否衰减跟随强度，默认关闭以保持移动跟随能力", None, None),
        "过冲衰减系数": (0.5, "每次明显过冲后增益乘数，范围大于零至一", 0.000001, 1),
        "最低跟随增益": (0.01, "自动衰减后的增益下限，范围大于零至一", 0.000001, 1),
        "识别周期": (50.0, "截图与识别目标周期，单位毫秒，实际受处理耗时限制", 0.001, None),
        "输入周期": (8.0, "鼠标小步输入周期，单位毫秒", 0.001, None),
        "平滑时长": (50.0, "单帧修正额度分步发送时长，单位毫秒", 0.001, None),
        "指令有效期": (100.0, "超过此毫秒数无新识别则停止旧指令", 0.001, None),
        "单次时间推进上限": (16.0, "输入线程单步最多推进的时间，单位毫秒，防止卡顿补发大位移", 0.001, None),
    }

    def __init__(self, *args, **kwargs):
        """初始化独立触发任务及配置；参数交由现有任务框架处理。"""
        super().__init__(*args, **kwargs)
        self.name = "自动随机锁敌"
        self.description = "逐帧跟随完整红色血条中心及配置偏移，不自动射击"
        # 主调度只负责检查后台工作线程；识别和鼠标输入独立运行，兼容左键循环任务。
        self.default_config.update({key: spec[0] for key, spec in self.PARAMETERS.items()})
        self.config_type["锁定模式"] = {
            "type": "drop_down", "options": ["持续跟踪", "随机换人"],
        }
        # 同时向现有配置描述协议提供数值范围，桌面和网页入口使用同一套业务约束。
        for key, (default, _, lower, upper) in self.PARAMETERS.items():
            if type(default) in (int, float):
                self.config_type[key] = {name: bound for name, bound in (("min", lower), ("max", upper))
                                         if bound is not None}
        self.config_description.update({key: spec[1] for key, spec in self.PARAMETERS.items()})
        self._settings = {key: spec[0] for key, spec in self.PARAMETERS.items()}
        self.trigger_interval = self._settings["识别周期"] / 1000
        self._settings_signature = None  # 一帧使用同一份快照，防止配置修改混入半帧计算。
        # 位置和速度均使用 1920×1080 基准坐标，保证分辨率变化时门限一致。
        self._mode = None
        self._frame_size = None
        self._error_logged = False
        # 同一把锁保护目标与移动指令，保证暂停清锁后不会补发已计算的位移。
        self._motion_lock = RLock()
        self._worker_stop = Event()  # 仅在销毁任务时终止线程，禁用期间线程等待恢复。
        self._workers = []
        self._generation = 0  # 暂停、禁用及目标重置时递增，丢弃重置前正在识别的结果。
        self._motion_error_logged = False
        self._status = None  # 只在诊断状态变化时记录日志，数值刷新不逐帧刷日志。
        self._reset_target()

    def _reset_target(self):
        """清除目标与未发送移动，并使正在识别的旧帧失效。"""
        with self._motion_lock:
            self._generation += 1
            self._target = None  # 最近确认的血条矩形，格式为基准坐标 x、y、宽、高。
            self._velocity = (0.0, 0.0)  # 血条中心每秒位移，包含镜头移动的画面影响。
            self._last_seen_at = 0.0
            self._selected_at = None  # 仅首次选敌或到期切换更新，遮挡不得重置计时。
            self._reset_controller()

    def _reset_controller(self):
        """新目标或恢复任务时清除上一目标的滞回、阻尼及输入余量。"""
        self._axis_active = [False, False]  # 每轴按配置的停止与启动误差滞回，移动目标仍可再次启动。
        self._last_errors = [0.0, 0.0]  # 上一帧误差用于识别越过准星后的反向修正。
        self._axis_gain = [1.0, 1.0]  # 自适应阻尼只降低配置强度，不改写用户配置。
        self._stop_motion()

    def _stop_motion(self):
        """取消当前帧未发送的位移；保留目标身份及换人计时。"""
        with self._motion_lock:
            self._motion_total = (0, 0)  # 本帧允许发送的相对输入总量，不随等待时间增长。
            self._motion_sent = (0, 0)  # 已消耗额度，保证同一帧不会重复移动。
            self._motion_elapsed = 0.0  # 本轮平滑轨迹已推进的时间。
            self._motion_fraction = [0.0, 0.0]  # 同方向小步跨画面保留量化进度，停止时清除。
            self._motion_direction = [0, 0]  # 输入方向变化时取消上一方向的量化余量。
            self._input_remainder = [0.0, 0.0]  # 同目标低速输入保留小数精度，停止后不沿用。
            self._motion_until = 0.0  # 识别停止刷新后自动失效，避免沿旧指令持续漂移。
            self._motion_at = time.monotonic()
            self.info["剩余位移"] = (0, 0)
            # 暂停、丢锁和旧指令过期时清空定位信息，避免面板仍显示上一帧的有效锁定点。
            self.info["血条中心"] = "无"
            self.info["锁定点"] = "无"

    def _publish_status(self, status, candidates=None, errors=None):
        """更新任务面板的阻断原因和基准像素误差；只有状态变化才记录中文日志。"""
        with self._motion_lock:
            self.info["锁敌状态"] = status
            if candidates is not None:
                # 数量只来自当前识别帧，不把上一帧的候选当作当前可见敌人。
                self.info["候选数量"] = candidates
            self.info["瞄准误差"] = tuple(round(value, 2) for value in errors) if errors is not None else "无"
            self.info["剩余位移"] = tuple(total - sent for total, sent in zip(self._motion_total, self._motion_sent))
            if status != self._status:
                # 状态日志不包含截图及账号数据，候选与位移数值直接在面板查看。
                logger.info(f"自动随机锁敌状态：{status}")
                self._status = status

    def on_create(self):
        """配置加载后安装本任务数值编辑兼容，并订阅暂停事件以立即清除旧输入。"""
        super().on_create()
        # 原生浮点控件默认最大99.99且仅两位小数，需避免配置界面偷偷限制速度和周期。
        self._install_config_editor()
        communicate.executor_paused.connect(self._on_executor_paused)
        if self._enabled:
            # 恢复持久化启用状态时，也要在主线程运行其他任务前启动独立识别。
            self._ensure_workers()

    @staticmethod
    def _install_config_editor():
        """仅为本任务数值控件应用声明范围和精度，不修改依赖文件或其他任务编辑行为。"""
        cards = import_module("ok.ui.qt.tasks.ConfigCard")
        original = cards.config_widget
        if getattr(original, "_enemy_lock_editor", False):
            return  # 多实例或重复创建时只安装一次，避免叠加控件处理。

        def config_widget(config_type, config_desc, config, key, value, task):
            """构造原生配置控件，再为锁敌任务的数值字段恢复完整可编辑范围及当前值。"""
            widget = original(config_type, config_desc, config, key, value, task)
            if isinstance(task, AutoEnemyLockTask) and key in task.PARAMETERS:
                default, _, lower, upper = task.PARAMETERS[key]
                if type(default) in (int, float):
                    # 扩大范围可能使控件自动改值，阻断信号直到恢复配置，避免界面创建时覆盖用户值。
                    spin = widget.spin_box
                    blocked = spin.blockSignals(True)
                    try:
                        if isinstance(default, float):
                            spin.setDecimals(6)
                            limit = np.finfo(float).max
                        else:
                            limit = np.iinfo(np.int32).max
                        spin.setRange(lower if lower is not None else -limit,
                                      upper if upper is not None else limit)
                        spin.setValue(config[key])
                    finally:
                        # 无论设置是否成功都恢复原信号状态，异常交由既有界面初始化链路处理。
                        spin.blockSignals(blocked)
            return widget

        config_widget._enemy_lock_editor = True
        cards.config_widget = config_widget

    def on_destroy(self):
        """销毁时停止输入、终止后台线程并移除暂停订阅。"""
        self._worker_stop.set()
        self._reset_target()
        communicate.executor_paused.disconnect(self._on_executor_paused)
        for worker in self._workers:
            # 截图后端可能尚未返回，仅做有界等待；退出标记会阻止它返回后重新发布移动。
            worker.join(timeout=0.3)
        super().on_destroy()

    def _on_executor_paused(self, paused):
        """全局暂停或恢复均清除旧锁定；入参来自框架暂停事件。"""
        self._reset_target()
        self._publish_status("已暂停" if paused else "等待画面", candidates=0)

    def enable(self):
        """启用时清空锁定并启动独立识别与输入，兼容已占用主调度的左键任务。"""
        self._reset_target()
        logger.info("启用自动随机锁敌")
        result = super().enable()
        self._publish_status("等待画面", candidates=0)
        self._ensure_workers()
        return result

    def disable(self):
        """先禁用调度再清空锁定；本任务不持有任何鼠标按键。"""
        result = super().disable()
        self._reset_target()
        self._publish_status("已禁用", candidates=0)
        logger.info("关闭自动随机锁敌")
        return result

    def validate_config(self, key, value):
        """校验单项类型、数值范围和已加载参数的关联约束，返回中文错误供界面展示。"""
        if key == "锁定模式":
            if value not in ("持续跟踪", "随机换人"):
                return "锁定模式必须为持续跟踪或随机换人"
        elif key in ("搜索区域", "HUD屏蔽区域"):
            # 矩形约定为左上右下，禁止错误格式被静默当作不屏蔽。
            try:
                rectangles = json.loads(value)
                valid = isinstance(rectangles, list) and all(
                    isinstance(rect, list) and len(rect) == 4
                    and all(type(v) is int for v in rect)
                    and 0 <= rect[0] < rect[2] <= 1920 and 0 <= rect[1] < rect[3] <= 1080
                    for rect in rectangles)
                if not valid or (key == "搜索区域" and not rectangles):
                    return "请输入有效矩形列表，格式[[左,上,右,下]]，范围1920×1080；搜索区域不能为空"
            except (TypeError, ValueError):
                # 编辑中不完整的文本只反馈界面，不影响正在使用的有效配置。
                return "矩形列表必须是有效JSON，格式[[左,上,右,下]]"
        elif key in self.PARAMETERS and isinstance(self.PARAMETERS[key][0], bool):
            if not isinstance(value, bool):
                return "请输入开关值"
        elif key in self.PARAMETERS:
            default, _, lower, upper = self.PARAMETERS[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                return "请输入有限数值"
            if isinstance(default, int) and not isinstance(value, int):
                return "此参数必须为整数"
            if (lower is not None and value < lower) or (upper is not None and value > upper):
                return f"{key}超出允许范围：下限{lower}，上限{upper}"
        current = getattr(self, "config", None)
        if isinstance(current, dict) and all(name in current for name in self.PARAMETERS):
            # 加载期间单项尚未组装完成，完整关系由运行快照再校验，避免按默认值误拒绝。
            return self._settings_error({**current, key: value})

    @staticmethod
    def _settings_error(settings):
        """检查成对阈值的顺序，保证热更新不会生成无效尺寸、死区或轨迹时长。"""
        for low, high in (
            ("红段最小宽度", "红段最大宽度"), ("红段最小高度", "红段最大高度"),
            ("轨道最小宽度", "轨道最大宽度"), ("关联最小宽度比", "关联最大宽度比"),
            ("轨道最小高度", "轨道最大高度"),
            ("长遮挡最小宽度比", "长遮挡最大宽度比"), ("停止误差", "启动误差"),
            ("平滑时长", "指令有效期"),
        ):
            if not all(type(settings[key]) in (int, float) and math.isfinite(settings[key]) for key in (low, high)):
                return f"{low}和{high}必须为有限数值"
            if settings[low] > settings[high]:
                # 上下限倒置会导致永远不识别或永远到不了位，因此明确阻止生效。
                return f"{low}不能大于{high}"

    def _sync_settings(self):
        """按识别帧获取有效配置快照；变化时废弃旧输入，保留原目标和换人计时。"""
        settings = {key: self.config.get(key, spec[0]) for key, spec in self.PARAMETERS.items()}
        signature = tuple(settings.items())
        with self._motion_lock:
            if signature == self._settings_signature:
                return True
            for key, value in settings.items():
                error = self.validate_config(key, value)
                if error:
                    # 文件手动改坏时停住并展示原因，不沿用静默回退参数继续瞄准。
                    self._stop_motion()
                    self._publish_status(f"配置错误：{key}：{error}")
                    return False
            error = self._settings_error(settings)
            if error:
                self._stop_motion()
                self._publish_status(f"配置错误：{error}")
                return False
            self._settings = settings
            self.trigger_interval = settings["识别周期"] / 1000
            self._settings_signature = signature
            # 旧速度和阻尼不适用于新阈值；保留目标身份，不能借改偏移提前随机换人。
            self._velocity = (0.0, 0.0)
            self._reset_controller()
            return True

    @staticmethod
    def detect_bars(frame, settings=None):
        """按配置从红色候选恢复完整轨道，返回基准矩形；残血段不能直接作为瞄准矩形。"""
        cfg = settings if settings is not None else {key: spec[0] for key, spec in AutoEnemyLockTask.PARAMETERS.items()}
        # 统一识别尺度，血条高度、形状门限和 HUD 屏蔽区域均按同一基准解释。
        image = cv2.resize(frame, (1920, 1080), interpolation=cv2.INTER_LINEAR)
        # 红色优势和亮度分别可调，兼容提亮截图，同时保留友方绿色排除。
        blue, green, red = cv2.split(image)
        red_float = red.astype(np.float32)
        mask = cv2.inRange(red, cfg["红色最低亮度"], 255)
        for channel in (green, blue):
            limit = cv2.multiply(channel, cfg["红色优势倍数"], dtype=cv2.CV_32F)
            mask = cv2.bitwise_and(mask, cv2.compare(red_float, limit, cv2.CMP_GT))
            difference = cv2.subtract(red, channel)
            mask = cv2.bitwise_and(mask, cv2.inRange(difference, cfg["红色最小差值"], 255))
        # 区域均来自用户配置；先限制搜索，再排除HUD，避免屏幕固定区域成为隐藏漏检条件。
        allowed = np.zeros_like(mask)
        for left, top, right, bottom in json.loads(cfg["搜索区域"]):
            allowed[top:bottom, left:right] = 255
        mask = cv2.bitwise_and(mask, allowed)
        for left, top, right, bottom in json.loads(cfg["HUD屏蔽区域"]):
            mask[top:bottom, left:right] = 0
        # 补合准星遮住的极小缺口，再保留水平线，避免红字和圆形准星成为候选。
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((1, cfg["红段闭运算宽度"]), np.uint8))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((1, cfg["红段开运算宽度"]), np.uint8))
        _, _, stats, _ = cv2.connectedComponentsWithStats(mask)
        # 根据候选附近的相对亮度找边框，兼容截图提亮和抗锯齿，不要求边框接近纯黑。
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        bars = []
        for x, y, width, height, area in stats[1:]:
            # 普通敌人血条细且横向连续，排除粗大的伤害字、技能光效和大幅 UI 色块。
            if not (cfg["红段最小宽度"] <= width <= cfg["红段最大宽度"]
                    and cfg["红段最小高度"] <= height <= cfg["红段最大高度"]
                    and width >= height * cfg["红段最小宽高比"]
                    and area >= width * height * cfg["红段最小填充率"]):
                continue
            # 红色填充只用于发现血条，真正的瞄准中心必须来自完整轨道的左右边界。
            bar = AutoEnemyLockTask._complete_bar(gray, x, y, width, height, cfg)
            if bar is None:
                # 没有完整轨道证据时宁可跳过，不能按红段中心把准星拉向残血端。
                continue
            duplicate = any(
                abs(bar[1] + bar[3] / 2 - other[1] - other[3] / 2) <= cfg["轨道去重纵向距离"]
                and max(0, min(bar[0] + bar[2], other[0] + other[2]) - max(bar[0], other[0]))
                / max(bar[2], other[2]) >= cfg["轨道去重重叠率"] for other in bars)
            if not duplicate:
                # 只去掉同一轨道的重复发现；不同敌人局部重叠时仍交给逐帧关联。
                bars.append(bar)
        return bars

    @staticmethod
    def _complete_bar(gray, x, y, width, height, cfg):
        """从红段附近恢复轨道左右界；允许断口及单侧边框，返回完整轨道内部矩形。"""
        margin = cfg["轨道上下搜索范围"]
        reach = cfg["轨道左右搜索范围"]
        ox, oy = max(0, x - reach), max(0, y - margin)
        roi = gray[oy:min(gray.shape[0], y + height + margin), ox:min(gray.shape[1], x + width + reach)]
        lx, ly = x - ox, y - oy
        # 局部对比而非固定纯黑，兼容提亮；上限防止把地面背景恢复成轨道。
        samples = np.concatenate((roi[:ly, lx:lx + width].reshape(-1),
                                  roi[ly + height:, lx:lx + width].reshape(-1)))
        if not samples.size:
            return None  # 画面边缘没有任何边框证据，不按残段构造中心。
        border = float(np.min(samples))
        fill = float(np.median(roi[ly:ly + height, lx:lx + width]))
        if fill - border < cfg["轨道最小对比"]:
            return None  # 缺少暗底与红段对比，通常属于特效。
        threshold = min(cfg["轨道最大灰度"], border + cfg["轨道灰度余量"],
                        fill - cfg["轨道最小对比"])
        original = (roi <= threshold).astype(np.uint8)
        # 轨道内部包含红色填充和灰色空血段，两者都亮于黑边；先恢复内部可排除左侧等级块。
        # 不对内部做补洞，以免跨过等级与血条之间的黑色隔断，把等级区域算进中心。
        interior_runs = []
        seed_center = lx + width // 2
        for row in range(ly, ly + height):
            changes = np.flatnonzero(np.diff(np.r_[0, 1 - original[row], 0].astype(np.int16)))
            for left, end in zip(changes[::2], changes[1::2]):
                overlap = max(0, min(end, lx + width) - max(left, lx))
                if (left <= seed_center < end and overlap >= width * cfg["轨道红段覆盖率"]
                        and lx - left <= cfg["轨道红段起点容差"] and left > 0 and end < roi.shape[1]
                        and cfg["轨道最小宽度"] <= end - left <= cfg["轨道最大宽度"]):
                    interior_runs.append((left, end, row))
        if interior_runs:
            # 使用多个填充行确认左右界，避免把准星、伤害字造成的单行断裂作为新的中心。
            left = int(np.median([run[0] for run in interior_runs]))
            end = int(np.median([run[1] for run in interior_runs]))
            tops, bottoms = [], []
            for rows, found in ((range(ly - 1, -1, -1), tops),
                                (range(ly + height, roi.shape[0]), bottoms)):
                for row in rows:
                    if original[row, left:end].mean() >= cfg["轨道边框支持率"]:
                        found.append(row)
                        break  # 采用最近的暗边，防止背景横线拉高完整轨道。
            if tops and bottoms:
                track_height = bottoms[0] - tops[0] - 1
                if cfg["轨道最小高度"] <= track_height <= cfg["轨道最大高度"]:
                    return (float(ox + left), float(oy + tops[0] + 1),
                            float(end - left), float(track_height))
            elif (cfg["允许单侧边框"] and (tops or bottoms)
                  and cfg["轨道最小高度"] <= height <= cfg["轨道最大高度"]):
                # 单侧边框仍有内部两端黑色隔断，血量段长度不参与轨道宽度计算。
                return (float(ox + left), float(y), float(end - left), float(height))
        # 只在小区域补合断口；支持率仍在原图检查，避免补出的长线单独成为证据。
        dark = cv2.morphologyEx(original, cv2.MORPH_CLOSE,
                               np.ones((1, cfg["轨道断口容限"] + 1), np.uint8))
        center = lx + width // 2
        edges = [[], []]
        for side, rows in enumerate((range(ly - 1, -1, -1), range(ly + height, roi.shape[0]))):
            for row in rows:
                if not dark[row, center]:
                    continue  # 遮挡该行时尝试其余邻行，不要求整条边框连续可见。
                changes = np.flatnonzero(np.diff(np.r_[0, dark[row], 0].astype(np.int16)))
                for left, end in zip(changes[::2], changes[1::2]):
                    right = end - 1
                    span = right - left - 1
                    if not (left <= lx and right >= lx + width - 1
                            and lx - left <= cfg["轨道红段起点容差"]
                            and cfg["轨道最小宽度"] <= span <= cfg["轨道最大宽度"]
                            and left > 0 and end < roi.shape[1]):
                        continue  # 必须观测到轨道两端，截断的暗色大背景不算完整轨道。
                    support = float(original[row, left:end].mean())
                    if support >= cfg["轨道边框支持率"]:
                        edges[side].append((left, right, row))
        pairs = [(top, bottom) for top in edges[0] for bottom in edges[1]
                 if abs(top[0] - bottom[0]) <= cfg["轨道边框偏差"]
                 and abs(top[1] - bottom[1]) <= cfg["轨道边框偏差"]]
        if pairs:
            # 优先离红段最近的对应边框，斜角端点采用两侧平均，中心不受血量影响。
            top, bottom = min(pairs, key=lambda pair: pair[1][2] - pair[0][2])
            left, right = (top[0] + bottom[0]) / 2, (top[1] + bottom[1]) / 2
            track_height = bottom[2] - top[2] - 1
            if cfg["轨道最小高度"] <= track_height <= cfg["轨道最大高度"]:
                return (float(ox + left + 1), float(oy + top[2] + 1),
                        float(right - left - 1), float(track_height))
        singles = edges[0] + edges[1]
        if (cfg["允许单侧边框"] and singles
                and cfg["轨道最小高度"] <= height <= cfg["轨道最大高度"]):
            # 单侧仍须拥有完整左右端点；另一侧被特效遮挡时保留可见轨道，不使用红段宽度。
            left, right, row = min(singles, key=lambda edge: abs(edge[2] - ly - height / 2))
            return (float(ox + left + 1), float(y), float(right - left - 1), float(height))
        return None

    def _select_target(self, bars, now):
        """每帧关联并更新当前敌人位置；now为单调秒数，换人计时只控制身份切换。"""
        cfg = self._settings
        matched = None
        previous = self._target
        if previous is not None:
            elapsed = now - self._last_seen_at
            # 丢失较久仍允许恢复同一几何目标，但限制旧速度的预测时长，不能无限外推。
            short_occlusion = elapsed <= cfg["关联遮挡时限"]
            prediction_time = min(max(elapsed, 0), cfg["关联预测时长"]) if short_occlusion else 0.0
            px = previous[0] + previous[2] / 2 + self._velocity[0] * prediction_time
            py = previous[1] + previous[3] / 2 + self._velocity[1] * prediction_time
            scored = []
            # 镜头运动也会让血条快速移动，关联半径按上一速度及实际帧间隔扩展，而非固定像素门限。
            radius = (cfg["关联基础距离"] if short_occlusion else cfg["长遮挡关联距离"])
            radius += math.hypot(*self._velocity) * prediction_time * cfg["关联速度补偿"]
            min_ratio = cfg["关联最小宽度比"] if short_occlusion else cfg["长遮挡最小宽度比"]
            max_ratio = cfg["关联最大宽度比"] if short_occlusion else cfg["长遮挡最大宽度比"]
            for bar in bars:
                distance = math.hypot(bar[0] + bar[2] / 2 - px, bar[1] + bar[3] / 2 - py)
                ratio = bar[2] / previous[2]
                # 长遮挡后收紧位置和完整宽度匹配，恢复原目标不等于随机改锁旁边的敌人。
                compatible = distance <= radius and min_ratio <= ratio <= max_ratio
                if compatible:
                    # 与预测矩形的重叠作为附加身份依据，防止附近多个同宽敌人仅按距离乱跳。
                    overlap_w = max(0, min(bar[0] + bar[2], px + previous[2] / 2)
                                    - max(bar[0], px - previous[2] / 2))
                    overlap_h = max(0, min(bar[1] + bar[3], py + previous[3] / 2)
                                    - max(bar[1], py - previous[3] / 2))
                    overlap_area = overlap_w * overlap_h
                    union_area = bar[2] * bar[3] + previous[2] * previous[3] - overlap_area
                    overlap = overlap_area / union_area if union_area > 0 else 0.0
                    scored.append((distance + abs(bar[2] - previous[2]) * cfg["关联宽度权重"]
                                   + (1 - overlap) * cfg["关联重叠权重"], bar))
            scored.sort(key=lambda item: item[0])
            if scored and (len(scored) == 1 or scored[1][0] - scored[0][0] >= cfg["关联歧义差值"]):
                # 两个候选同样接近时身份不明确，停止输入，不强行采用最近的一个。
                matched = scored[0][1]

        switch_due = (self._selected_at is not None and self._mode == "随机换人"
                      and now >= self._selected_at + cfg["换人间隔"])
        if self._selected_at is None or switch_due:
            alternatives = [bar for bar in bars if bar != matched]
            # 有其他可见目标时排除当前目标；只有一个目标时继续跟随。
            if alternatives:
                matched = random.choice(alternatives)
                previous = None
                self._reset_controller()
            if matched is not None:
                # 仅选中目标才开始新周期；无候选时保持到期状态，恢复后最多换一次。
                self._selected_at = now
        if matched is None:
            # 即使出现其他敌人，也必须等待本轮配置间隔到期，不能通过丢锁绕过计时。
            return None

        if previous is not None:
            elapsed = max(now - self._last_seen_at, np.finfo(float).eps)
            # 平滑速度用于下一帧关联；上限防止识别噪声造成远距离身份跳转。
            vx = (matched[0] + matched[2] / 2 - previous[0] - previous[2] / 2) / elapsed
            vy = (matched[1] + matched[3] / 2 - previous[1] - previous[3] / 2) / elapsed
            weight, limit = cfg["关联速度平滑"], cfg["关联速度上限"]
            self._velocity = tuple(max(-limit, min(limit, old * (1 - weight) + new * weight))
                                   for old, new in zip(self._velocity, (vx, vy)))
        else:
            # 换人后不能沿用上一目标的运动趋势。
            self._velocity = (0.0, 0.0)
        self._target = matched
        self._last_seen_at = now
        return matched

    def run(self):
        """保证后台线程已启动；主调度不再承担识别或鼠标移动。"""
        self._ensure_workers()

    def _ensure_workers(self):
        """每个任务实例只启动一组后台线程，避免反复启用产生重复输入。"""
        with self._motion_lock:
            if self._workers or self._worker_stop.is_set():
                # 已启动的线程会在禁用期间等待，恢复时不重复创建。
                return
            self._workers = [
                Thread(target=self._detection_loop, name="EnemyLockDetection", daemon=True),
                Thread(target=self._motion_loop, name="EnemyLockMotion", daemon=True),
            ]
            for worker in self._workers:
                worker.start()

    def _can_move(self):
        """检查任务、全局暂停和前台状态；独立线程不依赖当前主任务身份。"""
        return (not self._worker_stop.is_set() and not self.executor.exit_event.is_set()
                and self._enabled and not self.paused and not self.executor.paused
                and self.hwnd is not None and self.hwnd.is_foreground())

    def _inactive_status(self):
        """区分无法输入的运行状态，让面板明确展示禁用、暂停或后台原因。"""
        if self._worker_stop.is_set() or self.executor.exit_event.is_set():
            # 退出阶段禁止后台线程继续发布可移动状态。
            return "已停止"
        if not self._enabled:
            return "已禁用"
        if self.paused or self.executor.paused:
            return "已暂停"
        return "游戏后台"

    def _detection_loop(self):
        """按配置周期独立获取新帧，扣除处理耗时，不受主任务及换人间隔限制。"""
        while not self._worker_stop.is_set() and not self.executor.exit_event.is_set():
            started_at = time.monotonic()
            self._detect_once()
            # 扣除截图与识别耗时，不在每次处理后额外累加固定延迟。
            self._worker_stop.wait(max(0.0, self._settings["识别周期"] / 1000
                                       - (time.monotonic() - started_at)))

    def _detect_once(self):
        """直接从截图后端识别一帧；不修改主任务的共享帧或场景状态。"""
        try:
            if not self._sync_settings():
                return  # 配置错误已显示具体原因，禁止继续发送旧指令。
            if not self._can_move():
                # 暂停、禁用和后台时均不采集画面，也不保留旧目标。
                self._reset_target()
                self._publish_status(self._inactive_status(), candidates=0)
                return
            with self._motion_lock:
                generation = self._generation
            capture = self.executor.method
            # 不读 executor.frame：左键长循环期间该缓存不一定刷新，且读取可能等待主调度。
            frame = capture.get_frame() if capture is not None else None
            if frame is None or frame.size == 0:
                # 临时截图失败只停止移动，不清掉换人计时，避免下一帧立即随机重选。
                self._stop_motion()
                self._publish_status("无可用画面", candidates=0)
                return
            if not self.in_team(frame=frame):
                # 确认已离开局内后才结束锁定会话，恢复局内时重新开始。
                self._reset_target()
                self._publish_status("非局内", candidates=0)
                return
            now = time.monotonic()
            size = frame.shape[:2]
            mode = self._settings["锁定模式"]
            bars = self.detect_bars(frame, self._settings)
            with self._motion_lock:
                if generation != self._generation or not self._can_move():
                    # 暂停或禁用发生在截图期间时，旧帧不能在恢复后重新发布指令。
                    return
                if mode != self._mode or size != self._frame_size:
                    # 模式或捕获尺寸变化后重新选敌，不将旧坐标直接用于新画面。
                    self._reset_target()
                    if mode != self._mode:
                        logger.info(f"自动随机锁敌模式：{mode}")
                    self._mode, self._frame_size = mode, size
                bar = self._select_target(bars, now)
                self.info["候选数量"] = len(bars)
                if bar is not None:
                    self._move_to_bar(bar, size)
                else:
                    # 遮挡宽限只保留目标身份，不允许输入线程继续沿旧速度移动。
                    self._stop_motion()
                    self._publish_status("目标关联失败" if bars else "无血条", candidates=len(bars))
            self._error_logged = False
        except Exception as error:
            # 截图或输入失败后停止本帧移动，保留任务以便恢复；同一连续故障仅记一次。
            self._stop_motion()
            self._publish_status("识别异常", candidates=0)
            if not self._error_logged:
                logger.error("自动随机锁敌处理失败，本帧已停止瞄准", error)
                self._error_logged = True

    def _move_to_bar(self, bar, size):
        """以完整轨道中心叠加配置偏移计算每帧额度；移动目标越过启动阈值即重新跟随。"""
        cfg = self._settings
        x, y, width, height = bar
        # 轨道必须完整，两个偏移均为零时严格指向其几何中心，不附加隐式身体修正。
        aim_x = max(0, min(1919, x + width / 2 + cfg["水平偏移"]))
        aim_y = max(0, min(1079, y + height / 2 + cfg["垂直偏移"]))
        strength = cfg["跟随强度"]
        errors = (aim_x - 960, aim_y - 540)
        # 面板直接展示识别中心及叠加偏移后的锁定点，便于区分识别偏差与配置偏移。
        self.info["血条中心"] = (round(x + width / 2, 2), round(y + height / 2, 2))
        self.info["锁定点"] = (round(aim_x, 2), round(aim_y, 2))
        steps = []
        for axis, error in enumerate(errors):
            if error * self._last_errors[axis] < 0:
                # 一旦越过目标就取消该轴旧额度及精度余量，不能继续沿原方向滑行。
                total, sent = list(self._motion_total), list(self._motion_sent)
                total[axis] = sent[axis] = 0
                self._motion_total, self._motion_sent = tuple(total), tuple(sent)
                self._input_remainder[axis] = 0.0
                self._motion_fraction[axis] = 0.0
                if (cfg["过冲自动衰减"] and self._axis_active[axis]
                        and abs(error) > cfg["启动误差"] and abs(self._last_errors[axis]) > cfg["停止误差"]):
                    # 仅跟随中的明显过冲降低强度，停住后的边缘噪声不能不断压低有效增益。
                    self._axis_gain[axis] = max(cfg["最低跟随增益"], self._axis_gain[axis] * cfg["过冲衰减系数"])
            if abs(error) <= cfg["停止误差"]:
                # 进入到位区域即停住；再次超过外圈门限才启动，避免识别噪声反复开停。
                self._axis_active[axis] = False
                self._input_remainder[axis] = 0.0
                self._motion_fraction[axis] = 0.0
            elif abs(error) > cfg["启动误差"]:
                # 已经到位的目标再次移动也必须重新启动，不能等待换人时重置状态。
                self._axis_active[axis] = True
            self._last_errors[axis] = error
            maximum = cfg["单帧最大修正"]
            steps.append(max(-maximum, min(maximum, error * strength * self._axis_gain[axis]))
                         if self._axis_active[axis] else 0.0)
        dx, dy = steps[0] * size[1] / 1920, steps[1] * size[0] / 1080
        # 右键按住时按已有开镜灵敏度换算，其余情况使用普通镜头灵敏度。
        aiming = bool(win32api.GetAsyncKeyState(win32con.VK_RBUTTON) & 0x8000)
        # 用较大基准量取得换算比例，再处理小位移，避免公共接口提前取整使低速输入永远归零。
        scale_x, scale_y = self.calculate_sensitivity(10000, 10000, use_aim_sensitivity=aiming)
        dx, dy = dx * scale_x / 10000, dy * scale_y / 10000
        with self._motion_lock:
            for axis, value in enumerate((dx, dy)):
                direction = (value > 0) - (value < 0)
                if direction != self._motion_direction[axis]:
                    # 只允许同方向继承小步进度，反向、到位或零输入必须丢弃旧方向余量。
                    self._motion_fraction[axis] = 0.0
                self._motion_direction[axis] = direction
            # 新帧替换旧额度，不相加；小数只用于量化精度，不代表可无限持续的速度。
            offsets = [value + remainder for value, remainder in zip((dx, dy), self._input_remainder)]
            self._motion_total = tuple(int(value) for value in offsets)
            self._input_remainder = [value - int(value) for value in offsets]
            self._motion_sent = (0, 0)
            self._motion_elapsed = 0.0
            self._motion_at = time.monotonic()
            self._motion_until = self._motion_at + cfg["指令有效期"] / 1000
            self._publish_status("移动中" if any(self._axis_active) else "已到位", errors=errors)

    def _motion_loop(self):
        """按输入周期发送有限额度小步，独立于识别耗时、换人间隔及左键点击周期。"""
        while not self._worker_stop.wait(self._settings["输入周期"] / 1000) and not self.executor.exit_event.is_set():
            try:
                if self._motion_tick(time.monotonic()):
                    # 只有输入真正恢复成功才结束故障链，空闲帧不能重置错误去重状态。
                    self._motion_error_logged = False
            except Exception as error:
                # 输入异常时立即取消剩余指令；连续故障不重复刷错误日志。
                self._stop_motion()
                self._publish_status("输入异常")
                if not self._motion_error_logged:
                    logger.error("自动随机锁敌平滑移动失败，已取消剩余位移", error)
                    self._motion_error_logged = True

    def _motion_tick(self, now):
        """分步消耗本帧的有限额度；耗尽即停，now 为单调时钟秒数。"""
        with self._motion_lock:
            if not self._can_move():
                # 每个输入小步都检查暂停与前台，不等待下一次识别才停止。
                self._reset_target()
                self._publish_status(self._inactive_status(), candidates=0)
                return
            if now >= self._motion_until:
                # 指令有效期独立可配，超时后不补发旧画面产生的剩余位移。
                expired = self._motion_total != self._motion_sent
                self._stop_motion()
                if expired:
                    # 有额度却未能及时执行时，面板应显示等待反馈，而不是一直停留在移动中。
                    self._publish_status("等待新画面")
                return
            # 调度停顿后按配置限制单步推进，避免一次补发大幅位移；轨迹时长也读取同一快照。
            elapsed = max(0.0, min(self._settings["单次时间推进上限"] / 1000, now - self._motion_at))
            duration = self._settings["平滑时长"] / 1000
            self._motion_at = now
            old_progress = self._motion_elapsed / duration
            old_blend = old_progress * old_progress * (3 - 2 * old_progress)
            self._motion_elapsed = min(duration, self._motion_elapsed + elapsed)
            progress = self._motion_elapsed / duration
            # 平滑曲线只决定额度分配的时间，最终累计值严格等于本帧额度，绝不重复积分。
            blend = progress * progress * (3 - 2 * progress)
            offsets = [total * (blend - old_blend) + fraction
                       for total, fraction in zip(self._motion_total, self._motion_fraction)]
            remaining = tuple(total - sent for total, sent in zip(self._motion_total, self._motion_sent))
            # 连续新画面保留不足一个像素的进度，但每帧实际输出仍不能超过它自己的剩余额度。
            steps = [max(min(0, left), min(max(0, left), int(value)))
                     for left, value in zip(remaining, offsets)]
            if progress >= 1:
                # 轨迹结束时消耗最后的整数额度，避免浮点误差将最后一个像素永久截断。
                steps = list(remaining)
            dx, dy = steps
            if dx or dy:
                # 只注入相对移动，不按下或释放鼠标键，因此可与左键任务同时工作。
                self.genshin_interaction.do_move_mouse_relative(dx, dy)
                self._motion_sent = tuple(sent + step for sent, step in zip(self._motion_sent, steps))
                self.info["剩余位移"] = tuple(left - step for left, step in zip(remaining, steps))
            # 低速量化为零的中间帧不能抹掉同方向进度；完整消费整数额度后才清空该进度。
            self._motion_fraction = [value - step if left != step or total == 0 else 0.0
                                     for value, step, left, total in zip(offsets, steps, remaining, self._motion_total)]
            if dx or dy:
                # 返回实际发送结果，供输入线程仅在恢复成功时清除异常去重状态。
                return True
