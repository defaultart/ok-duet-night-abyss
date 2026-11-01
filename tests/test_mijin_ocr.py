"""附件真实 OCR 回归；覆盖原图、客户区归一化及支持的分辨率。"""
from pathlib import Path

import cv2
from ok.test.TaskTestCase import TaskTestCase

from src.compat.window_client_area import ClientAreaTransform
from src.config import config
from src.dna_ui.Defs import COORD
from src.tasks.fullauto.AutoMijinTask import AutoMijinTask


class TestMijinOcr(TaskTestCase):
    task_class = AutoMijinTask
    config = dict(config)

    def test_attached_screens(self):
        cases = (
            ('home', 'ENTER', '坠入深渊'),
            ('start', 'START', '开始探索'),
            ('fight', 'OBJECTIVE', '战斗'),
            ('candle', 'CANDLE', '获得烛芯'),
            ('continue', 'OBJECTIVE', '继续探索'),
            ('exit', 'EXIT', '退出并结算'),
            ('confirm', 'CONFIRM', '是否结束当前探索并结算'),
            ('result', 'RESULT', '探索失败'),
            ('result_direct', 'RESULT', '探索失败'),
            ('revive', 'REVIVE', '复苏'),
        )
        for width, height in ((1600, 900), (1920, 1080), (2560, 1440)):
            for caption in (0, 30):
                for image, area, text in cases:
                    with self.subTest(image=image, width=width, caption=caption):
                        path = Path(__file__).parent / 'images' / f'mijin_{image}.png'
                        self.set_image(str(path))
                        frame = self.task.frame
                        frame = ClientAreaTransform(1600, 900, top=caption).normalize_frame(frame)
                        self.task.executor._frame = cv2.resize(frame, (width, height))
                        self.task.executor.method._size = (width, height)
                        self.assertTrue(self.task.has_text(area, text))
                        expected_node = {'result_direct': 'result'}.get(image, image)
                        self.assertEqual(self.task.detect_node(), expected_node)
                        self.assertFalse(self.task.enable_fidget_action)
                        if image != 'candle':
                            self.assertFalse(self.task.has_text('CANDLE', '获得烛芯'))
                        if image != 'revive':
                            self.assertFalse(self.task.has_text('REVIVE', '复苏'))
                        if image == 'fight':
                            self.assertEqual(self.task._read_hud_number('LISE_Q_COST_OCR'), 10)
                        if image == 'revive':
                            self.assertFalse(self.task.in_team())

    def test_clicks_stay_inside_buttons(self):
        # 所有坐标在原截图及裁去 30px 标题栏后的按钮交集内。
        for coord, bounds in (
            (COORD.MIJIN_ENTER, (1380, 720, 1530, 765)),
            (COORD.MIJIN_START, (1310, 790, 1510, 830)),
            (COORD.MIJIN_EXIT, (1440, 780, 1545, 860)),
            (COORD.MIJIN_CONFIRM, (815, 520, 1110, 555)),
        ):
            x, y = coord
            x0, y0, x1, y1 = bounds
            self.assertTrue(x0 <= x <= x1 and y0 <= y <= y1)
