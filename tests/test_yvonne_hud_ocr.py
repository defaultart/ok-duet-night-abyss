"""Read the supplied Yvonne screenshots using the real HUD OCR backend."""
from pathlib import Path
from unittest.mock import Mock, patch

from ok.test.TaskTestCase import TaskTestCase

from src.compat.window_client_area import ClientAreaTransform
from src.config import config
from src.tasks.trigger.AutoYvonneUltimateTask import AutoYvonneUltimateTask


class TestYvonneHudOcr(TaskTestCase):
    task_class = AutoYvonneUltimateTask
    config = dict(config)

    def test_supplied_hud_numbers_do_not_trigger_q(self):
        for image_name, cost, energy in (
            ("hud_yvonne_q_active_windowed.png", 0, 218),
            ("hud_yvonne_q_inactive_windowed.png", 14, 92),
        ):
            for caption_height in (0, 29):
                with self.subTest(image=image_name, caption_height=caption_height):
                    self.set_image(str(Path(__file__).parent / "images" / image_name))
                    task = self.task
                    frame = task.frame
                    transform = ClientAreaTransform(frame.shape[1], frame.shape[0], top=caption_height)
                    task.executor._frame = transform.normalize_frame(frame)
                    task.scene.reset()
                    task.reset_detection()

                    self.assertFalse(task.enabled)
                    self.assertEqual(task.name, "伊薇 Q 自动开启")
                    self.assertTrue(task.in_team_and_world())
                    self.assertEqual(task._read_hud_number("LISE_Q_COST_OCR"), cost)
                    self.assertEqual(task._read_hud_number("LISE_ENERGY_OCR"), energy)
                    with patch.object(task, "send_key", Mock()) as send_key:
                        for timestamp in (10.0, 10.4, 12.5):
                            with patch("src.tasks.trigger.AutoLiseUltimateTask.time.monotonic",
                                       return_value=timestamp):
                                self.assertIsNone(task.run())
                        send_key.assert_not_called()
