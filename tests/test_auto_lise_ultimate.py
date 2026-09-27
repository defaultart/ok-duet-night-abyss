from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from src.config import config
from src.tasks.trigger.AutoLiseUltimateTask import (
    AutoLiseUltimateTask,
    first_ocr_number,
)


def make_task(numbers):
    task = object.__new__(AutoLiseUltimateTask)
    task.scene = SimpleNamespace(in_team=lambda _: True)
    task.in_team_and_world = Mock(return_value=True)
    task._confirmations = 0
    task._next_check_at = 0.0
    task._cooldown_until = 0.0
    task._last_status = None
    task._read_hud_number = Mock(side_effect=numbers)
    task.key_config = {"Ultimate Key": "q"}
    task.send_key = Mock()
    task.log_info = Mock()
    task.log_debug = Mock()
    return task


def run_at(task, timestamp):
    with patch("src.tasks.trigger.AutoLiseUltimateTask.time.monotonic", return_value=timestamp):
        return task.run()


class TestAutoLiseUltimate(unittest.TestCase):

    def test_public_reset_detection_clears_embedded_runtime_state(self):
        task = make_task([])
        task._confirmations = 2
        task._next_check_at = 10.0
        task._cooldown_until = 20.0
        task._last_status = "pressed"

        task.reset_detection()

        self.assertEqual(task._confirmations, 0)
        self.assertEqual(task._next_check_at, 0.0)
        self.assertEqual(task._cooldown_until, 0.0)
        self.assertIsNone(task._last_status)

    def test_registered_in_gui_without_reordering_existing_tasks(self):
        trigger_tasks = config["trigger_tasks"]
        entry = ["src.tasks.trigger.AutoLiseUltimateTask", "AutoLiseUltimateTask"]

        self.assertEqual(trigger_tasks.count(entry), 1)
        entry_index = trigger_tasks.index(entry)
        self.assertEqual(
            trigger_tasks[entry_index - 1],
            ["src.tasks.trigger.AutoCombatTask", "AutoCombatTask"],
        )
        self.assertEqual(
            trigger_tasks[entry_index + 1],
            ["src.tasks.trigger.AutoMoveTask", "AutoMoveTask"],
        )

    def test_first_ocr_number_extracts_digits_and_rejects_missing_result(self):
        self.assertEqual(first_ocr_number([SimpleNamespace(name="9")]), 9)
        self.assertEqual(first_ocr_number([SimpleNamespace(name="energy: 186")]), 186)
        self.assertEqual(first_ocr_number([
            SimpleNamespace(name="86", x=20),
            SimpleNamespace(name="18", x=10),
        ]), 186)
        self.assertIsNone(first_ocr_number([SimpleNamespace(name="noise")]))
        self.assertIsNone(first_ocr_number([]))

    def test_enabled_q_cost_zero_does_nothing_and_skips_energy_read(self):
        task = make_task([0])

        run_at(task, 1.0)

        task.send_key.assert_not_called()
        task._read_hud_number.assert_called_once_with("LISE_Q_COST_OCR")

    def test_closed_q_with_energy_over_100_presses_after_two_confirmations(self):
        task = make_task([9, 136, 9, 136])

        self.assertIsNone(run_at(task, 1.0))
        self.assertTrue(run_at(task, 1.4))

        task.send_key.assert_called_once_with("q")

    def test_multi_digit_positive_cost_also_presses_q(self):
        task = make_task([10, 101, 10, 101])

        self.assertIsNone(run_at(task, 1.0))
        self.assertTrue(run_at(task, 1.4))

        task.send_key.assert_called_once_with("q")

    def test_energy_must_be_strictly_greater_than_100(self):
        task = make_task([9, 100, 9, 99])

        run_at(task, 1.0)
        run_at(task, 1.4)

        task.send_key.assert_not_called()

    def test_ocr_failure_breaks_confirmation_sequence(self):
        task = make_task([9, 136, None, 9, 136, 9, 136])

        run_at(task, 1.0)
        run_at(task, 1.4)
        run_at(task, 1.8)
        self.assertEqual(task.send_key.call_count, 0)
        self.assertTrue(run_at(task, 2.2))
        self.assertEqual(task.send_key.call_count, 1)

    def test_unreadable_cost_is_only_a_debug_status(self):
        task = make_task([None])

        run_at(task, 1.0)

        task.send_key.assert_not_called()
        task.log_info.assert_not_called()
        task.log_debug.assert_called_once()

    def test_transient_not_in_team_does_not_clear_log_deduplication(self):
        task = make_task([])
        task._last_status = "active"
        task.scene = SimpleNamespace(in_team=lambda _: False)

        run_at(task, 1.0)

        self.assertEqual(task._last_status, "active")

    def test_press_cooldown_blocks_immediate_retry(self):
        task = make_task([5, 150, 5, 150, 5, 150, 5, 150])

        run_at(task, 1.0)
        self.assertTrue(run_at(task, 1.4))
        self.assertIsNone(run_at(task, 1.8))
        self.assertEqual(task.send_key.call_count, 1)

        run_at(task, 3.5)
        self.assertTrue(run_at(task, 3.9))
        self.assertEqual(task.send_key.call_count, 2)


if __name__ == "__main__":
    unittest.main()
