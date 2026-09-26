import re
import time

import cv2
from ok import Logger, TriggerTask

from src.scene.DNAScene import DNAScene
from src.tasks.BaseDNATask import BaseDNATask


logger = Logger.get_logger(__name__)

NUMBER_PATTERN = re.compile(r"[0-9]+")
ENERGY_THRESHOLD = 100
REQUIRED_CONFIRMATIONS = 2
CHECK_INTERVAL = 0.35
PRESS_COOLDOWN = 2.0


def normalize_lise_number(cv_image):
    """放大 HUD 上只有十几像素高的数字，供 OCR 识别。"""
    return cv2.resize(cv_image, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)


def normalize_lise_q_cost(cv_image):
    """Q 消耗字号更小，实测 6 倍放大才能稳定识别 0、9 和多位数。"""
    return cv2.resize(cv_image, None, fx=6, fy=6, interpolation=cv2.INTER_CUBIC)


def first_ocr_number(texts):
    """合并 OCR 按水平位置切开的重叠数字片段。

    实测底部的 ``186`` 会被检测器切成 ``18`` 和 ``86`` 两个重叠框，
    所以不能只取第一项。这里按 x 排序后做最长首尾重叠合并；一个框时
    与普通的整数解析相同。
    """
    parts = []
    for index, text in enumerate(texts or ()):
        value = NUMBER_PATTERN.search(text.name)
        if value:
            parts.append((getattr(text, "x", index), value.group()))
    if not parts:
        return None

    parts.sort(key=lambda item: item[0])
    merged = parts[0][1]
    for _, part in parts[1:]:
        if part in merged:
            continue
        overlap = 0
        for size in range(1, min(len(merged), len(part)) + 1):
            if merged[-size:] == part[:size]:
                overlap = size
        merged += part[overlap:]
    return int(merged)


class AutoLiseUltimateTask(BaseDNATask, TriggerTask):
    """黎瑟 Q 自动开启。

    Q 图标上的增益消耗为 0 时代表已开启，不做任何操作。只有在消耗大于 0、
    且黄色能量严格大于 100 时才按终结技键。连续两次识别到相同条件才执行，
    避免 HUD 动画或瞬时 OCR 误读把已开启的 Q 关掉。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "黎瑟 Q 自动开启"
        self.description = "Q 关闭且黄色能量大于 100 时自动按 Q"
        self.scene: DNAScene | None = None
        self._confirmations = 0
        self._next_check_at = 0.0
        self._cooldown_until = 0.0
        self._last_status = None

    def enable(self):
        self.reset_detection()
        return super().enable()

    def reset_detection(self):
        """重置连续确认和冷却状态，供独立启用与其他任务内嵌运行复用。"""
        self._reset_detection()

    def _reset_detection(self):
        self._confirmations = 0
        self._next_check_at = 0.0
        self._cooldown_until = 0.0
        self._last_status = None

    def _read_hud_number(self, box_name):
        processor = normalize_lise_q_cost if box_name == "LISE_Q_COST_OCR" else normalize_lise_number
        texts = self.ocr(
            box=self.screen_box(box_name),
            match=NUMBER_PATTERN,
            frame_processor=processor,
            name=box_name.lower(),
        )
        return first_ocr_number(texts)

    def _log_status(self, status, message):
        """只在状态变化时记录，方便实机诊断且不会每帧刷屏。"""
        if status == self._last_status:
            return
        self._last_status = status
        self.log_info(message)

    def run(self):
        now = time.monotonic()
        if now < self._next_check_at or now < self._cooldown_until:
            return
        self._next_check_at = now + CHECK_INTERVAL

        if not self.scene.in_team(self.in_team_and_world):
            self._confirmations = 0
            self._last_status = None
            return

        q_cost = self._read_hud_number("LISE_Q_COST_OCR")
        if q_cost is None:
            self._confirmations = 0
            self._log_status("cost_unreadable", "黎瑟 Q：无法识别增益消耗，本次不操作")
            return
        if q_cost == 0:
            self._confirmations = 0
            self._log_status("active", "黎瑟 Q：当前已开启（消耗 0）")
            return

        energy = self._read_hud_number("LISE_ENERGY_OCR")
        if energy is None:
            self._confirmations = 0
            self._log_status("energy_unreadable", f"黎瑟 Q：消耗 {q_cost}，但无法识别黄色能量")
            return
        if energy <= ENERGY_THRESHOLD:
            self._confirmations = 0
            self._log_status("energy_low", f"黎瑟 Q：消耗 {q_cost}，当前能量 {energy}（需要大于 {ENERGY_THRESHOLD}）")
            return

        self._confirmations += 1
        if self._confirmations < REQUIRED_CONFIRMATIONS:
            self._log_status("confirming", f"黎瑟 Q：消耗 {q_cost}，能量 {energy}，等待二次确认")
            return

        self._confirmations = 0
        self._cooldown_until = now + PRESS_COOLDOWN
        self._log_status("pressed", f"黎瑟 Q 未开启（消耗 {q_cost}），当前能量 {energy}，开启 Q")
        self.send_key(self.key_config["Ultimate Key"])
        return True
