from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from src.config import config
from src.tasks.trigger.AutoLiseUltimateTask import (
    PRESS_DOWN_TIME,
    AutoLiseUltimateTask,
    first_ocr_number,
)


def make_task(numbers):
    task = object.__new__(AutoLiseUltimateTask)
    task.scene = SimpleNamespace(in_team=lambda _: True)
    task.in_team_and_world = Mock(return_value=True)
    task.reset_detection()
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
        task._last_good_at = 9.0
        task._low_energy_streak = 1
        task._awaiting_active = True
        task._last_status = "pressed"
        task._status_logged_at = {"pressed": 9.0}

        task.reset_detection()

        self.assertEqual(task._confirmations, 0)
        self.assertEqual(task._next_check_at, 0.0)
        self.assertEqual(task._cooldown_until, 0.0)
        self.assertEqual(task._last_good_at, 0.0)
        self.assertEqual(task._low_energy_streak, 0)
        self.assertFalse(task._awaiting_active)
        self.assertIsNone(task._last_status)
        self.assertEqual(task._status_logged_at, {})

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

        task.send_key.assert_called_once_with("q", down_time=PRESS_DOWN_TIME)

    def test_multi_digit_positive_cost_also_presses_q(self):
        task = make_task([10, 101, 10, 101])

        self.assertIsNone(run_at(task, 1.0))
        self.assertTrue(run_at(task, 1.4))

        task.send_key.assert_called_once_with("q", down_time=PRESS_DOWN_TIME)

    def test_energy_must_be_strictly_greater_than_100(self):
        task = make_task([9, 100, 9, 99])

        run_at(task, 1.0)
        run_at(task, 1.4)

        task.send_key.assert_not_called()

    def test_unreadable_frame_between_good_reads_still_presses(self):
        task = make_task([9, 136, None, 9, 136])

        run_at(task, 1.0)
        run_at(task, 1.4)
        self.assertTrue(run_at(task, 1.8))

        task.send_key.assert_called_once_with("q", down_time=PRESS_DOWN_TIME)

    def test_implausible_cost_does_not_press_or_clear_confirmation(self):
        task = make_task([9, 136, 91, 9, 136])

        run_at(task, 1.0)
        run_at(task, 1.4)
        self.assertEqual(task._confirmations, 1)
        self.assertTrue(run_at(task, 1.8))

        task.send_key.assert_called_once_with("q", down_time=PRESS_DOWN_TIME)

    def test_single_low_energy_read_keeps_confirmation(self):
        task = make_task([9, 136, 9, 51, 9, 136])

        run_at(task, 1.0)
        run_at(task, 1.4)
        self.assertEqual(task._confirmations, 1)
        self.assertTrue(run_at(task, 1.8))

        task.send_key.assert_called_once_with("q", down_time=PRESS_DOWN_TIME)

    def test_two_low_energy_reads_drop_confirmation(self):
        task = make_task([9, 136, 9, 51, 9, 80, 9, 136])

        run_at(task, 1.0)
        run_at(task, 1.4)
        run_at(task, 1.8)
        self.assertEqual(task._confirmations, 0)
        self.assertIsNone(run_at(task, 2.2))

        task.send_key.assert_not_called()

    def test_unreadable_cost_is_logged_to_file_without_gui_notification(self):
        task = make_task([None])

        with patch("src.tasks.trigger.AutoLiseUltimateTask.logger.info") as file_log:
            run_at(task, 1.0)
            file_log.assert_called_once()
            self.assertIn("cost_unreadable", file_log.call_args.args[0])

        task.send_key.assert_not_called()
        task.log_info.assert_not_called()
        task.log_debug.assert_not_called()

    def test_not_in_team_is_logged_without_reading_hud_or_notifying_gui(self):
        task = make_task([])
        task._last_status = "active"
        task.scene = SimpleNamespace(in_team=lambda _: False)

        with patch("src.tasks.trigger.AutoLiseUltimateTask.logger.info") as file_log:
            run_at(task, 1.0)
            file_log.assert_called_once()
            self.assertIn("not_in_team", file_log.call_args.args[0])

        self.assertEqual(task._last_status, "not_in_team")
        task._read_hud_number.assert_not_called()
        task.log_info.assert_not_called()

    def test_file_status_is_throttled_even_when_states_alternate(self):
        task = make_task([None, 9, None, None, None])
        with patch("src.tasks.trigger.AutoLiseUltimateTask.logger.info") as file_log:
            run_at(task, 1.0)  # Cost unreadable.
            run_at(task, 1.4)  # Energy unreadable.
            run_at(task, 1.8)  # Same cost status within five seconds.
            self.assertEqual(file_log.call_count, 2)
            run_at(task, 6.0)
            self.assertEqual(file_log.call_count, 3)
        task.log_info.assert_not_called()

    def test_sent_key_is_only_confirmed_after_reading_zero_cost(self):
        task = make_task([9, 150, 9, 150, None, 0, 0])
        run_at(task, 1.0)
        self.assertTrue(run_at(task, 1.4))
        self.assertTrue(task._awaiting_active)
        self.assertEqual(task._last_status, "pressed")
        self.assertIn("已发送", task.log_info.call_args.args[0])

        run_at(task, 2.1)
        self.assertTrue(task._awaiting_active)
        self.assertEqual(task.log_info.call_count, 1)
        run_at(task, 2.5)
        self.assertFalse(task._awaiting_active)
        self.assertEqual(task._last_status, "active_confirmed")
        self.assertIn("已确认开启", task.log_info.call_args.args[0])
        run_at(task, 4.6)
        self.assertEqual(task.log_info.call_count, 2)
        task.send_key.assert_called_once_with("q", down_time=PRESS_DOWN_TIME)

    def test_already_active_does_not_claim_our_press_succeeded(self):
        task = make_task([0])
        run_at(task, 1.0)
        self.assertFalse(task._awaiting_active)
        self.assertEqual(task._last_status, "active")
        task.log_info.assert_not_called()

    def test_failed_send_does_not_claim_key_was_sent(self):
        task = make_task([9, 150, 9, 150])
        task.send_key.return_value = False
        run_at(task, 1.0)
        self.assertIsNone(run_at(task, 1.4))
        self.assertFalse(task._awaiting_active)
        self.assertEqual(task._last_status, "send_failed")
        task.log_info.assert_not_called()

    def test_missed_press_retries_after_short_cooldown(self):
        task = make_task([5, 150, 5, 150, 5, 150, 5, 150])

        run_at(task, 1.0)
        self.assertTrue(run_at(task, 1.4))
        self.assertIsNone(run_at(task, 1.8))
        self.assertEqual(task.send_key.call_count, 1)

        run_at(task, 2.1)
        self.assertTrue(run_at(task, 2.5))
        self.assertEqual(task.send_key.call_count, 2)

    def test_stale_confirmation_expires(self):
        task = make_task([9, 136, 9, 136, 9, 136])

        run_at(task, 1.0)
        self.assertIsNone(run_at(task, 3.0))
        self.assertTrue(run_at(task, 3.4))

        task.send_key.assert_called_once_with("q", down_time=PRESS_DOWN_TIME)

    def test_seen_active_blocks_immediate_second_press(self):
        task = make_task([5, 150, 5, 150, 0, 5, 150])

        run_at(task, 1.0)
        self.assertTrue(run_at(task, 1.4))
        run_at(task, 2.1)
        self.assertIsNone(run_at(task, 2.5))

        task.send_key.assert_called_once_with("q", down_time=PRESS_DOWN_TIME)


if __name__ == "__main__":
    unittest.main()
