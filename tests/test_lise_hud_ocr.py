"""Regression coverage using the real HUD crop and OCR backend."""
from pathlib import Path
from unittest.mock import Mock, patch

from ok.test.TaskTestCase import TaskTestCase

from src.config import config
from src.compat.window_client_area import ClientAreaTransform
from src.tasks.trigger.AutoLiseUltimateTask import AutoLiseUltimateTask, PRESS_DOWN_TIME


class TestLiseHudOcr(TaskTestCase):
    task_class = AutoLiseUltimateTask
    config = dict(config)

    def test_real_hud_numbers_trigger_q_after_confirmation(self):
        for image_name, energy in (
            ("hud_defence_lise_lv60_windowed.png", 186),
            ("hud_defence_lise_lv70_energy202_windowed.png", 202),
        ):
            for caption_height in (0, 29):
                with self.subTest(image=image_name, caption_height=caption_height):
                    self.check_hud(image_name, energy, caption_height)

    def check_hud(self, image_name, energy, caption_height):
        image = Path(__file__).parent / "images" / image_name
        self.set_image(str(image))
        task = self.task
        frame = task.frame
        transform = ClientAreaTransform(frame.shape[1], frame.shape[0], top=caption_height)
        task.executor._frame = transform.normalize_frame(frame)
        task.scene.reset()
        task.reset_detection()

        self.assertTrue(task.in_team_and_world())
        self.assertEqual(task._read_hud_number("LISE_Q_COST_OCR"), 9)
        self.assertEqual(task._read_hud_number("LISE_ENERGY_OCR"), energy)

        with patch.object(task, "send_key", Mock()) as send_key:
            with patch("src.tasks.trigger.AutoLiseUltimateTask.time.monotonic", return_value=10.0):
                self.assertIsNone(task.run())
            send_key.assert_not_called()
            with patch("src.tasks.trigger.AutoLiseUltimateTask.time.monotonic", return_value=10.4):
                self.assertTrue(task.run())
            send_key.assert_called_once_with(task.key_config["Ultimate Key"], down_time=PRESS_DOWN_TIME)
