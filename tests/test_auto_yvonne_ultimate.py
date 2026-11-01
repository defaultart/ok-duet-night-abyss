from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from src.config import config
from src.tasks.trigger.AutoLiseUltimateTask import PRESS_DOWN_TIME
from src.tasks.trigger.AutoYvonneUltimateTask import AutoYvonneUltimateTask


def make_task(numbers):
    task = object.__new__(AutoYvonneUltimateTask)
    task.scene = SimpleNamespace(in_team=lambda _: True)
    task.in_team_and_world = Mock(return_value=True)
    task.reset_detection()
    task._read_hud_number = Mock(side_effect=numbers)
    task.key_config = {"Ultimate Key": "q"}
    task.send_key = Mock()
    task.log_info = Mock()
    return task


def run_at(task, timestamp):
    with patch("src.tasks.trigger.AutoLiseUltimateTask.time.monotonic", return_value=timestamp):
        return task.run()


class TestAutoYvonneUltimate(unittest.TestCase):
    def test_registered_once_at_end_without_reordering_existing_tasks(self):
        entries = config["trigger_tasks"]
        entry = ["src.tasks.trigger.AutoYvonneUltimateTask", "AutoYvonneUltimateTask"]
        self.assertEqual(entries.count(entry), 1)
        self.assertEqual(entries[-1], entry)
        self.assertEqual([item[1] for item in entries[:-1]], [
            "AutoRouletteTask", "AutoMazeTask", "AutoPickTask", "AutoCombatTask",
            "AutoLiseUltimateTask", "AutoMoveTask", "AutoAimTask", "AutoRogueDialogTask",
        ])

    def test_active_skips_energy_and_never_presses(self):
        task = make_task([0, 0])
        run_at(task, 1.0)
        run_at(task, 1.4)
        run_at(task, 3.1)
        task.send_key.assert_not_called()
        self.assertEqual(task._read_hud_number.call_count, 2)
        task._read_hud_number.assert_called_with("LISE_Q_COST_OCR")

    def test_positive_cost_requires_energy_strictly_over_150_and_two_confirmations(self):
        for cost in (1, 14, 21, 100):
            for energy in (149, 150, 151):
                with self.subTest(cost=cost, energy=energy):
                    task = make_task([cost, energy, cost, energy])
                    self.assertIsNone(run_at(task, 1.0))
                    task.send_key.assert_not_called()
                    result = run_at(task, 1.4)
                    if energy > 150:
                        self.assertTrue(result)
                        task.send_key.assert_called_once_with("q", down_time=PRESS_DOWN_TIME)
                        self.assertIn("伊薇 Q", task.log_info.call_args.args[0])
                    else:
                        self.assertIsNone(result)
                        task.send_key.assert_not_called()

    def test_uses_configured_ultimate_key(self):
        task = make_task([14, 151, 14, 151])
        task.key_config["Ultimate Key"] = "r"
        run_at(task, 1.0)
        run_at(task, 1.4)
        task.send_key.assert_called_once_with("r", down_time=PRESS_DOWN_TIME)

    def test_unreadable_or_non_world_hud_does_not_press(self):
        for numbers in ([None, None], [14, None, 14, None]):
            with self.subTest(numbers=numbers):
                task = make_task(numbers)
                run_at(task, 1.0)
                run_at(task, 1.4)
                task.send_key.assert_not_called()
        task = make_task([])
        task.scene.in_team = lambda _: False
        run_at(task, 1.0)
        task._read_hud_number.assert_not_called()
        task.send_key.assert_not_called()

    def test_unreadable_frame_preserves_fresh_confirmation(self):
        task = make_task([14, 151, None, 14, 151])
        run_at(task, 1.0)
        run_at(task, 1.4)
        self.assertTrue(run_at(task, 1.8))
        task.send_key.assert_called_once()

    def test_low_energy_resets_after_two_reads_and_old_confirmation_expires(self):
        task = make_task([14, 151, 14, 150, 14, 149, 14, 151, 14, 151, 14, 151])
        for timestamp in (1.0, 1.4, 1.8, 2.2, 4.0):
            self.assertIsNone(run_at(task, timestamp))
        task.send_key.assert_not_called()
        self.assertTrue(run_at(task, 4.4))

    def test_failed_send_retries_after_cooldown(self):
        task = make_task([14, 151] * 4)
        task.send_key.side_effect = [False, None]
        run_at(task, 1.0)
        self.assertIsNone(run_at(task, 1.4))
        self.assertFalse(task._awaiting_active)
        run_at(task, 1.8)
        task.send_key.assert_called_once()
        run_at(task, 2.1)
        self.assertTrue(run_at(task, 2.5))
        self.assertEqual(task.send_key.call_count, 2)
        self.assertTrue(task._awaiting_active)

    def test_missed_press_retries_until_zero_confirms_active(self):
        task = make_task([14, 151] * 4 + [0, 0])
        for timestamp in (1.0, 1.4, 2.1, 2.5):
            run_at(task, timestamp)
        self.assertEqual(task.send_key.call_count, 2)
        self.assertTrue(task._awaiting_active)
        run_at(task, 3.2)
        self.assertFalse(task._awaiting_active)
        self.assertEqual(task._last_status, "active_confirmed")
        run_at(task, 3.6)
        run_at(task, 5.3)
        self.assertEqual(task.send_key.call_count, 2)

    def test_enable_resets_previous_detection_state(self):
        task = make_task([14, 151])
        run_at(task, 1.0)
        task._cooldown_until = 20.0
        task._awaiting_active = True
        with patch("ok.TriggerTask.enable") as enable:
            task.enable()
        enable.assert_called_once()
        self.assertEqual(task._confirmations, 0)
        self.assertEqual(task._next_check_at, 0.0)
        self.assertEqual(task._cooldown_until, 0.0)
        self.assertEqual(task._last_good_at, 0.0)
        self.assertEqual(task._low_energy_streak, 0)
        self.assertFalse(task._awaiting_active)
        self.assertEqual(task._status_logged_at, {})


if __name__ == "__main__":
    unittest.main()
