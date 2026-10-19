import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import win32con

from ok import TaskDisabledException
from ok.device.interaction_methods.post_message import PostMessageInteraction
from ok.test.TaskTestCase import TaskTestCase

from src.compat.window_client_area import ClientAreaTransform
from src.config import config
from src.dna_ui.Defs import COORD
from src.tasks.AutoWeaponExp60Task import AutoWeaponExp60Task, ROUTE


MODULE = "src.tasks.AutoWeaponExp60Task"

EXPECTED_ROUTE = (
    ("w", 3.0, 3.1), ("d", 0.6, 0.7), ("w", 1.0, 1.2),
    (None, 1.0, 1.2), ("d", 1.8, 2.0), (None, 9.0, 10.0),
    ("w", 2.7, 2.8), (None, 9.0, 10.0), ("w", 2.9, 3.0),
    (None, 7.0, 8.0), ("w", 2.0, 2.1), (None, 4.0, 5.0),
    ("d", 0.8, 0.9), ("w", 7.0, 7.5),
)


class TestWeaponExpScreens(TaskTestCase):
    task_class = AutoWeaponExp60Task
    config = dict(config)

    def test_start_with_and_without_caption(self):
        for caption in (0, 30):
            with self.subTest(caption=caption):
                self.set_image(str(Path(__file__).parent / "images" / "weapon_exp60_start.png"))
                frame = self.task.frame
                self.task.executor._frame = ClientAreaTransform(
                    frame.shape[1], frame.shape[0], top=caption).normalize_frame(frame)
                self.assertTrue(self.task.find_start_interface())
                self.assertFalse(self.task.find_manual_select_btn())
                self.assertFalse(self.task.find_result_again_btn())
                self.assertFalse(self.task.in_team())


class TestWeaponExpFlow(unittest.TestCase):
    def setUp(self):
        self.task = object.__new__(AutoWeaponExp60Task)
        self.now = 0.0
        self.events = []
        self.q_checks = []
        task = self.task
        task.lise_enabled = False
        task.action_timeout = 15
        for name in ("info_set", "next_frame", "reset_detection", "click_ui_coord", "log_debug"):
            setattr(task, name, Mock())
        task.find_result_again_btn = Mock(return_value=False)
        task.send_key_down = lambda key: self.events.append((self.now, "down", key))
        task.send_key_down_without_activation = lambda key: task.send_key_down(key)
        task.send_key_up = lambda key: self.events.append((self.now, "up", key))
        task.check_lise_ultimate = lambda: self.q_checks.append(self.now)
        task.sleep = self.advance
        self.clock = patch(f"{MODULE}.time", SimpleNamespace(monotonic=lambda: self.now))
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def advance(self, seconds):
        self.now += seconds

    def assert_released(self):
        held = set()
        for _, action, key in self.events:
            if action == "down":
                held.add(key)
            else:
                held.discard(key)
        self.assertEqual(held, set())

    def test_registration(self):
        self.assertIn([MODULE, "AutoWeaponExp60Task"], config["onetime_tasks"])

    def test_route_timing_q_and_jump_cadence(self):
        # Exercise both ends of every random range, including the Shift delay.
        for endpoint in (0, 1):
            with self.subTest(endpoint=endpoint):
                self.now = 0
                self.events.clear()
                self.q_checks.clear()
                with patch(f"{MODULE}.random.uniform", side_effect=lambda low, high: (low, high)[endpoint]) as rng:
                    self.assertFalse(self.task.run_attempt())
                cursor = 0.0
                downs = [(t, key) for t, action, key in self.events if action == "down"]
                expected = []
                expected_rng = []
                self.assertEqual(ROUTE, EXPECTED_ROUTE)
                for route_index, (key, low, high) in enumerate(EXPECTED_ROUTE):
                    expected_rng.append((low, high))
                    if key:
                        shift_delay = (3.5, 3.7) if route_index == 0 else (0.05, 0.15)
                        expected.extend([(cursor, key), (cursor + shift_delay[endpoint], "shift")])
                        if route_index == 0:
                            expected.append((cursor + shift_delay[endpoint], key))
                        expected_rng.append(shift_delay)
                        if route_index == 0:
                            cursor += shift_delay[endpoint]
                    cursor += (low, high)[endpoint]
                expected.append((cursor, "w"))
                for index in range(5):
                    expected.append((cursor + index * (0.2, 0.4)[endpoint], "space"))
                    if index < 4:
                        expected_rng.append((0.2, 0.4))
                self.assertEqual(len(downs), len(expected))
                for (actual_time, actual_key), (expected_time, expected_key) in zip(downs, expected):
                    self.assertEqual(actual_key, expected_key)
                    self.assertAlmostEqual(actual_time, expected_time)
                self.assertAlmostEqual(self.now, cursor + 4 * (0.2, 0.4)[endpoint] + 0.1 + 3)
                self.assertEqual([call.args for call in rng.call_args_list], expected_rng)
                self.assertTrue(self.q_checks)
                self.assertAlmostEqual(min(self.q_checks), (6.5, 6.8)[endpoint])
                self.assertGreater(max(self.q_checks), cursor)
                # W stays down through the initial walk and sprint; Shift stays
                # held to the end of every movement, including the new stages.
                first_w_up = next(t for t, action, key in self.events if action == "up" and key == "w")
                self.assertAlmostEqual(first_w_up, (6.5, 6.8)[endpoint])
                shift_ups = [t for t, action, key in self.events if action == "up" and key == "shift"]
                cursor = 0.0
                expected_ups = []
                for route_index, (key, low, high) in enumerate(EXPECTED_ROUTE):
                    if route_index == 0:
                        cursor += (3.5, 3.7)[endpoint]
                    cursor += (low, high)[endpoint]
                    if key:
                        expected_ups.append(cursor)
                for actual, expected_up in zip(shift_ups, expected_ups):
                    self.assertAlmostEqual(actual, expected_up)
                self.assertFalse(self.task.lise_enabled)
                self.assert_released()
        self.assertEqual(self.task.reset_detection.call_count, 2)

    def test_ocr_time_is_included_in_deadline(self):
        self.task.lise_enabled = True
        self.task.check_lise_ultimate = lambda: self.advance(0.02)
        self.assertFalse(self.task.wait_until_result(1))
        self.assertLessEqual(self.now, 1.02)

    def test_result_interrupts_movement_wait_jumps_and_final_wait(self):
        for finish_at in (0.02, 1, 1.6, 4.8, 5, 5.3, 6, 15, 35, 54, 54.55, 54.9, 57):
            with self.subTest(finish_at=finish_at):
                self.now = 0
                self.events.clear()
                self.q_checks.clear()
                self.task.find_result_again_btn = lambda: self.now >= finish_at
                with patch(f"{MODULE}.random.uniform", side_effect=lambda low, high: low):
                    self.assertTrue(self.task.run_attempt())
                self.assertLessEqual(self.now, finish_at + 0.051)
                self.assertTrue(all(t < finish_at for t in self.q_checks))
                self.assertFalse(self.task.lise_enabled)
                self.assert_released()

    def test_stop_and_capture_error_release_keys(self):
        for error in (TaskDisabledException, RuntimeError):
            for stop_at in (0.02, 1, 1.6, 5, 5.3, 6, 15, 54.55, 54.9, 57):
                with self.subTest(error=error.__name__, stop_at=stop_at):
                    self.now = 0
                    self.events.clear()

                    def frame():
                        if self.now >= stop_at:
                            raise error()

                    self.task.next_frame.side_effect = frame
                    with patch(f"{MODULE}.random.uniform", side_effect=lambda low, high: low):
                        with self.assertRaises(error):
                            self.task.run_attempt()
                    self.assertFalse(self.task.lise_enabled)
                    self.assert_released()

    def test_sprint_and_cleanup_send_standard_shift_messages(self):
        interaction = object.__new__(PostMessageInteraction)
        interaction.try_activate = Mock()
        interaction.post = Mock()
        task = self.task
        task.send_key_down = interaction.send_key_down
        task.send_key_up = interaction.send_key_up
        with patch(f"{MODULE}.random.uniform", return_value=1.5):
            self.assertFalse(task.sprint("w", 4.6, shift_delay=(1.5, 1.7)))
        messages = [call.args for call in interaction.post.call_args_list]
        self.assertEqual(messages, [
            (win32con.WM_KEYDOWN, 0x57, 0x00110001),
            (win32con.WM_KEYDOWN, win32con.VK_SHIFT, 0x002A0001),
            (win32con.WM_KEYUP, 0x57, 0xC0110001),
            (win32con.WM_KEYUP, win32con.VK_SHIFT, 0xC02A0001),
        ])
        interaction.post.reset_mock()
        task.release_movement()
        self.assertIn(
            (win32con.WM_KEYUP, win32con.VK_SHIFT, 0xC02A0001),
            [call.args for call in interaction.post.call_args_list],
        )

    def test_confirmation_does_not_choose_options(self):
        task = self.task
        page = {"manual": True}
        task.find_manual_select_btn = lambda: page["manual"]
        task.choose_drop_rate_item = Mock()
        task.click_ui_coord.side_effect = lambda *args, **kwargs: page.update(manual=False)

        def wait(condition, post_action, **kwargs):
            self.assertFalse(condition())
            post_action()
            self.assertTrue(condition())

        task.wait_until = wait
        task.confirm_start()
        task.click_ui_coord.assert_called_once_with(
            COORD.MANUAL_CONFIRM, name="weapon_exp_start", after_sleep=0.25)
        task.choose_drop_rate_item.assert_not_called()

    def test_continuous_runs_and_failure_recovery(self):
        task = self.task
        page = {"name": "start", "attempts": 0}
        task.find_start_interface = lambda: page["name"] == "start"
        task.find_result_again_btn = lambda: page["name"] == "result"
        task.find_manual_select_btn = lambda: page["name"] == "manual"
        task.in_team = lambda: page["name"] == "game"
        task.start_mission = Mock(side_effect=lambda: page.update(name="manual"))
        task.confirm_start = Mock(side_effect=lambda: page.update(name="game"))
        task.give_up_mission = Mock(side_effect=lambda: page.update(name="result"))
        task.wait_until = Mock(side_effect=lambda condition, **kwargs: condition())

        def attempt():
            page["attempts"] += 1
            if page["attempts"] == 3:
                raise TaskDisabledException()
            if page["attempts"] == 2:
                page["name"] = "result"
                return True
            return False

        task.run_attempt = attempt
        with self.assertRaises(TaskDisabledException):
            task.do_run()
        self.assertEqual(task.start_mission.call_count, 3)
        self.assertEqual(task.confirm_start.call_count, 3)
        task.give_up_mission.assert_called_once()
        self.assert_released()

    def test_recovery_timeout_propagates(self):
        task = self.task
        task.lise_enabled = True
        task.give_up_mission = Mock()
        task.wait_until = Mock(side_effect=RuntimeError("recovery timeout"))
        with self.assertRaisesRegex(RuntimeError, "recovery timeout"):
            task.retry_mission()
        self.assertFalse(task.lise_enabled)
        self.assertTrue(task.wait_until.call_args.kwargs["raise_if_not_found"])
        self.assert_released()

    def test_run_restores_fidget_after_stop_and_error(self):
        task = self.task
        for error in (TaskDisabledException, RuntimeError):
            task.fidget_params = {"paused": False}
            task.move_mouse_to_safe_position = Mock()
            task.set_check_monthly_card = Mock()
            task.soundBeep = Mock()
            task.log_info_notify = Mock()
            task.do_run = Mock(side_effect=error())
            with patch(f"{MODULE}.DNAOneTimeTask.run"):
                if error is RuntimeError:
                    with self.assertRaises(error):
                        task.run()
                else:
                    task.run()
            self.assertFalse(task.fidget_params["paused"])
            self.assertFalse(task.lise_enabled)
            self.assert_released()


if __name__ == "__main__":
    unittest.main()
