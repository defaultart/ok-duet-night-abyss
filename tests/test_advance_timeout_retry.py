"""Retry after the auto-advance timeout, including result and manual screens."""
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ok import TaskDisabledException
from ok.test.TaskTestCase import TaskTestCase

from src.compat.window_client_area import ClientAreaTransform
from src.config import config
from src.dna_ui.Defs import COORD
from src.tasks.AutoDefence import AutoDefence
from src.tasks.CommissionsTask import CommissionsTask, Mission, _default_movement


class TestTimeoutRetryScreens(TaskTestCase):
    task_class = CommissionsTask
    config = dict(config)

    def test_result_and_manual_screens_before_and_after_caption_crop(self):
        for name, expected in (
            ("result_defence_after_advance_timeout.png", "result"),
            ("manual_select_defence_after_advance_timeout.png", "manual"),
        ):
            for top in (0, 29):
                with self.subTest(name=name, top=top):
                    self.set_image(str(Path(__file__).parent / "images" / name))
                    raw = self.task.frame
                    self.task.executor._frame = ClientAreaTransform(
                        raw.shape[1], raw.shape[0], top=top).normalize_frame(raw)
                    self.assertEqual(bool(self.task.find_result_again_btn()), expected == "result")
                    self.assertEqual(bool(self.task.find_manual_select_btn()), expected == "manual")
                    self.assertFalse(self.task.in_team())


class TestAdvanceTimeoutRetry(unittest.TestCase):
    def test_common_advance_timeout_is_20_seconds_but_defence_is_10(self):
        for cls, timeout, frames in ((CommissionsTask, 20, 3), (AutoDefence, 10, 1)):
            with self.subTest(task=cls.__name__):
                task = object.__new__(cls)
                for name in ("send_key_down", "send_key_up", "next_frame",
                             "give_up_mission", "log_info"):
                    setattr(task, name, Mock())
                task.is_in_combat = Mock(return_value=False)
                clock = Mock(side_effect=[0, 9.9, 10.0, 19.9, 20.0])
                with patch("src.tasks.CommissionsTask.time", SimpleNamespace(time=clock)):
                    self.assertFalse(task.advance_until_combat())
                self.assertEqual(task.next_frame.call_count, frames)
                task.give_up_mission.assert_called_once()
                task.log_info.assert_called_once_with(f"前进 {timeout} 秒仍未进入战斗，重开任务")

    def test_timeout_restarts_through_result_manual_and_initial_movement(self):
        task = object.__new__(AutoDefence)
        task.external_movement = _default_movement
        task.external_movement_immediate = False
        task.config = {"挂机模式": "自动前进到开战", "轮次": 4}
        task.commission_config = {"委托手册": "不使用"}
        task.action_timeout = 5
        task.mission_status = Mission.CONTINUE  # Must not leak into the new manual dialog.
        task.skill_tick = Mock()
        for name in ("load_char", "reset_wave_info", "log_info", "log_info_notify",
                     "soundBeep", "check_for_monthly_card", "sleep", "next_frame", "info_set"):
            setattr(task, name, Mock())
        state = {"page": "ingame", "attempt": 1, "held": False, "clock": 0}
        events, waits = [], []

        def clock():
            state["clock"] += 1
            return state["clock"]

        task.in_team = lambda: state["page"] == "ingame"
        task.is_in_combat = lambda: state["attempt"] == 2
        task.find_result_again_btn = lambda: state["page"] == "result"
        task.find_manual_select_btn = lambda: state["page"] == "manual"
        for name in ("find_start_interface", "find_letter_interface", "find_letter_reward_btn",
                     "find_action_dialog_continue", "find_action_dialog_retreat", "find_esc_menu"):
            setattr(task, name, Mock(return_value=False))
        task.screen_box = Mock(return_value=None)

        def key_down(key):
            state["held"] = True
            events.append("w down")

        def key_up(key):
            state["held"] = False
            events.append("w up")

        def exit_mission(timeout=0):
            self.assertFalse(state["held"], "W must be released before opening ESC")
            self.assertIsNone(task.mission_status)
            events.append("esc giveup confirm")
            state["page"] = "result"
            return True

        def wait_until(condition, post_action=None, **kwargs):
            for probes in range(1, 5):
                result = condition()
                if result:
                    waits.append((kwargs.get("time_out"), probes))
                    return result
                if post_action:
                    post_action()
            self.fail("Recovery waited without recognizing the current screen")

        def click(coord, **kwargs):
            if coord == COORD.RESULT_AGAIN:
                self.assertEqual(state["page"], "result")
                events.append("again")
                state["page"] = "manual"
            elif coord == COORD.MANUAL_CONFIRM:
                self.assertEqual(state["page"], "manual")
                events.append("start challenge")
                state.update(page="ingame", attempt=2)
            else:
                self.fail(f"Unexpected button: {coord}")

        task.send_key_down = key_down
        task.send_key_up = key_up
        task.exit_mission = exit_mission
        task.wait_until = wait_until
        task.click_ui_coord = click

        class ReenteredCombat(Exception):
            pass
        task.handle_in_mission = Mock(side_effect=ReenteredCombat)
        with patch("src.tasks.CommissionsTask.time", SimpleNamespace(time=clock)):
            with self.assertRaises(ReenteredCombat):
                task.do_run()

        self.assertEqual(events, ["w down", "w up", "esc giveup confirm",
                                  "again", "start challenge", "w down", "w up"])
        self.assertIn((60, 1), waits, "Result page must end give_up_mission's wait immediately")
        self.assertIsNone(task.mission_status)
        self.assertTrue(task._mission_started)
        self.assertEqual(task.skill_tick.reset.call_count, 2)

    def test_giveup_accepts_manual_and_letter_without_waiting_for_start_screen(self):
        for ready in ("find_result_again_btn", "find_manual_select_btn", "find_letter_interface"):
            with self.subTest(ready=ready):
                task = object.__new__(CommissionsTask)
                task.mission_status = Mission.CONTINUE
                task.exit_mission = Mock()
                for name in ("find_result_again_btn", "find_manual_select_btn", "find_letter_interface",
                             "find_start_interface", "find_action_dialog_continue", "find_esc_menu"):
                    setattr(task, name, Mock(return_value=name == ready))
                task.wait_until = lambda condition, **kwargs: self.assertTrue(condition())
                task.give_up_mission()
                self.assertIsNone(task.mission_status)

    def test_movement_always_releases_w_when_interrupted_or_frame_fails(self):
        for error in (TaskDisabledException(), RuntimeError("capture failed")):
            with self.subTest(error=type(error).__name__):
                task = object.__new__(CommissionsTask)
                task.send_key_down = Mock()
                task.send_key_up = Mock()
                task.next_frame = Mock(side_effect=error)
                task.advance_failed = Mock()
                with self.assertRaises(type(error)):
                    task.advance_until_combat()
                task.send_key_up.assert_called_once_with("w")
                task.advance_failed.assert_not_called()

    def test_normal_continue_still_uses_centered_manual_confirmation(self):
        task = object.__new__(CommissionsTask)
        task.mission_status = Mission.CONTINUE
        task.action_timeout = 5
        task.sleep = Mock()
        task.choose_drop_rate_item = Mock()
        task.find_manual_select_btn = Mock(side_effect=[True, True, False])
        task.click_ui_coord = Mock()

        def wait_until(condition, post_action, **kwargs):
            self.assertFalse(condition())
            post_action()
            self.assertTrue(condition())

        task.wait_until = wait_until
        task.choose_drop_rate()
        task.click_ui_coord.assert_called_once_with(
            COORD.MANUAL_CONFIRM_NEXT, name="manual_confirm", after_sleep=0.25)
        self.assertEqual(task.mission_status, Mission.CONTINUE)
