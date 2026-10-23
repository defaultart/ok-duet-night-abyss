"""Letter confirmation does not depend on how the selection page was reached."""
import unittest
from unittest.mock import Mock, patch
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from threading import Event
from types import SimpleNamespace

import numpy as np

from ok import TaskDisabledException
from src.tasks.AutoDefence import AutoDefence
from src.tasks.AutoWeaponAscensionTask import AutoWeaponAscensionTask
from src.tasks.BaseDNATask import BaseDNATask
from src.tasks.DNAOneTimeTask import DNAOneTimeTask

from src.dna_ui.Defs import COORD
from src.tasks.CommissionsTask import CommissionsTask, Mission
from src.tasks.config.CommissionConfig import (
    LETTER_HANDLE_AUTO_SELECT_FIRST, LETTER_HANDLE_START_DIRECTLY, LETTER_HANDLE_WAIT_USER,
)


class TestLetterConfirmation(unittest.TestCase):
    def make_task(self, mode, status=None):
        task = object.__new__(CommissionsTask)
        task.config = {}
        task.commission_config = {"自动处理密函": mode}
        task.mission_status = status
        task.action_timeout = 5
        task.fidget_params = {"paused": False}
        for name in ("sleep", "log_info", "log_info_notify", "soundBeep", "click_ui_area"):
            setattr(task, name, Mock())
        task.screen_box = Mock(return_value="safe")
        task.click_ui_coord = Mock()
        task.send_key = Mock()
        task.find_letter_interface = lambda: task.click_ui_coord.call_count == 0
        for name in ("click", "click_box_random", "move_mouse_to_safe_position",
                     "move_back_from_safe_position", "scroll", "move"):
            setattr(task, name, Mock(side_effect=AssertionError("Mouse input is forbidden")))

        def wait_until(condition, post_action=None, **kwargs):
            for _ in range(4):
                if condition():
                    return True
                if post_action:
                    post_action()
            self.fail("Selection page did not close after confirmation")

        task.wait_until = Mock(side_effect=wait_until)
        return task

    def test_all_states_and_automatic_modes_only_click_confirm_once(self):
        for status in (None, Mission.START, Mission.CONTINUE):
            for mode in (LETTER_HANDLE_AUTO_SELECT_FIRST, LETTER_HANDLE_START_DIRECTLY):
                with self.subTest(status=status, mode=mode):
                    task = self.make_task(mode, status)
                    task.choose_letter()
                    task.click_ui_coord.assert_called_once_with(COORD.LETTER_CONFIRM, name="letter_confirm", use_safe_move=False)
                    task.send_key.assert_not_called()
                    self.assertNotIn("post_action", task.wait_until.call_args.kwargs)
                    task.click_ui_area.assert_not_called()
                    self.assertFalse(task.fidget_params["paused"])
                    self.assertEqual(task.mission_status, status)

    def test_legacy_mode_and_default_use_confirmation_only(self):
        for mode in (LETTER_HANDLE_AUTO_SELECT_FIRST, None):
            task = self.make_task(mode)
            if mode is None:
                task.commission_config = {}
            self.assertEqual(task.get_letter_handle_mode(), LETTER_HANDLE_START_DIRECTLY)
            task.choose_letter()
            task.click_ui_area.assert_not_called()
            task.click_ui_coord.assert_called_once()
            task.send_key.assert_not_called()

    def test_wait_user_does_not_click_any_selection_or_confirmation(self):
        task = self.make_task(LETTER_HANDLE_WAIT_USER)
        task.find_letter_interface = Mock(side_effect=[True, False])
        task.choose_letter()
        task.click_ui_coord.assert_not_called()
        task.click_ui_area.assert_not_called()
        task.send_key.assert_not_called()
        self.assertEqual(task.wait_until.call_args.kwargs["time_out"], 300)

    def test_no_selection_page_does_not_click_confirm(self):
        task = self.make_task(LETTER_HANDLE_START_DIRECTLY)
        task.find_letter_interface = Mock(return_value=False)
        task.choose_letter()
        task.click_ui_coord.assert_not_called()
        task.send_key.assert_not_called()

    def test_start_and_result_transitions_keep_start_status(self):
        for entry in ("start", "result"):
            with self.subTest(entry=entry):
                task = self.make_task(LETTER_HANDLE_START_DIRECTLY)
                page = {"name": entry}
                task.in_team = Mock(return_value=False)
                task.check_for_monthly_card = Mock()
                task.find_letter_reward_btn = Mock(return_value=False)
                task.find_manual_select_btn = Mock(return_value=False)
                task.find_letter_interface = lambda: page["name"] == "letter"
                task.find_start_interface = lambda: page["name"] == "start"
                task.find_result_again_btn = lambda: page["name"] == "result"
                task.start_mission = Mock(side_effect=lambda: page.update(name="letter"))
                task.click_ui_coord.side_effect = lambda *args, **kwargs: page.update(name="game")
                task.handle_mission_interface()
                self.assertEqual(task.mission_status, Mission.START)
                self.assertEqual(task.handle_mission_interface(), Mission.START)
                self.assertEqual(page["name"], "game")
                task.click_ui_coord.assert_called_once()
                task.send_key.assert_not_called()

    def test_continue_status_is_returned_after_confirmation(self):
        task = self.make_task(LETTER_HANDLE_START_DIRECTLY, Mission.CONTINUE)
        task.in_team = Mock(return_value=False)
        task.check_for_monthly_card = Mock()
        task.find_letter_reward_btn = Mock(return_value=False)
        self.assertEqual(task.handle_mission_interface(), Mission.CONTINUE)
        self.assertIsNone(task.mission_status)

    def test_pending_close_is_observed_without_any_further_clicks(self):
        task = self.make_task(LETTER_HANDLE_START_DIRECTLY)
        task.find_letter_interface = Mock(side_effect=[True, True, True, False])
        task.choose_letter()
        task.click_ui_coord.assert_called_once()
        task.send_key.assert_not_called()
        self.assertEqual(task.find_letter_interface.call_count, 4)

    def test_timeout_stops_after_one_click_and_restores_pause(self):
        task = self.make_task(LETTER_HANDLE_START_DIRECTLY)
        task.wait_until = Mock(return_value=False)
        with self.assertRaises(TaskDisabledException):
            task.choose_letter()
        task.click_ui_coord.assert_called_once()
        task.send_key.assert_not_called()
        task.log_info.assert_called_once_with("密函确认后界面未关闭，停止任务以避免重复点击")
        self.assertFalse(task.fidget_params["paused"])

    def test_pause_stays_active_through_click_and_wait_and_restores_on_error(self):
        for prior in (False, True):
            for error in (RuntimeError("capture failed"), TaskDisabledException()):
                with self.subTest(prior=prior, error=type(error)):
                    task = self.make_task(LETTER_HANDLE_START_DIRECTLY)
                    task.fidget_params["paused"] = prior
                    task.click_ui_coord.side_effect = lambda *a, **kw: self.assertTrue(task.fidget_params["paused"])

                    def wait(*args, **kwargs):
                        self.assertTrue(task.fidget_params["paused"])
                        raise error

                    task.wait_until = wait
                    with self.assertRaises(type(error)):
                        task.choose_letter()
                    self.assertEqual(task.fidget_params["paused"], prior)

    def test_pause_drains_inflight_inputs_and_releases_modifier(self):
        task = self.make_task(LETTER_HANDLE_START_DIRECTLY)
        active, release = Event(), Event()
        task._fidget_pause_hook = Mock()

        def input_sequence():
            with task.fidget_input_lock:
                active.set()
                release.wait(2)

        with ThreadPoolExecutor(max_workers=2) as pool:
            action = pool.submit(input_sequence)
            self.assertTrue(active.wait(1))
            pause = pool.submit(task.set_fidget_actions_paused, True)
            try:
                with self.assertRaises(TimeoutError):
                    pause.result(timeout=0.05)
                task._fidget_pause_hook.assert_not_called()
            finally:
                release.set()
            self.assertFalse(pause.result(timeout=1))
            action.result(timeout=1)
        task._fidget_pause_hook.assert_called_once()

    def test_defence_start_only_logs_without_beep_or_notification(self):
        task = object.__new__(AutoDefence)
        from src.tasks.CommissionsTask import _default_movement
        task.external_movement = _default_movement
        task.sleep = Mock()
        task.log_info = Mock()
        task.log_info_notify = Mock()
        task.soundBeep = Mock()
        task.handle_mission_start()
        task.log_info.assert_called_once_with("任务开始")
        task.log_info_notify.assert_not_called()
        task.soundBeep.assert_not_called()

    def test_starting_on_letter_page_does_not_reposition_mouse(self):
        for cls in (AutoDefence, AutoWeaponAscensionTask):
            with self.subTest(task=cls.__name__):
                task = object.__new__(cls)
                task.fidget_params = {"paused": False}
                task.find_letter_interface = Mock(return_value=True)
                task.move_mouse_to_safe_position = Mock()
                task.set_check_monthly_card = Mock()
                task.release_movement = Mock()
                task.do_run = Mock()
                with patch.object(DNAOneTimeTask, "run") as start:
                    start.side_effect = lambda _: self.assertTrue(task.fidget_params["paused"])
                    task.run()
                task.move_mouse_to_safe_position.assert_not_called()
                task.do_run.assert_called_once()
                self.assertFalse(task.fidget_params["paused"])

    def test_real_click_path_only_moves_to_button_and_clicks_without_restore(self):
        for foreground in (False, True):
            with self.subTest(foreground=foreground):
                task = self.make_task(LETTER_HANDLE_START_DIRECTLY)
                task._executor = SimpleNamespace(device_manager=SimpleNamespace(
                    hwnd_window=SimpleNamespace(is_foreground=lambda: foreground)))
                task.__dict__["pydirect_interaction"] = Mock()
                task.click = Mock()
                task._perform_random_click(1220, 583, use_safe_move=False)
                if foreground:
                    task.pydirect_interaction.move.assert_called_once_with(1220, 583)
                    task.pydirect_interaction.click.assert_called_once()
                    task.click.assert_not_called()
                else:
                    task.click.assert_called_once()
                    self.assertEqual(task.click.call_args.args, (1220, 583))
                    task.pydirect_interaction.move.assert_not_called()
                task.move_mouse_to_safe_position.assert_not_called()
                task.move_back_from_safe_position.assert_not_called()
                task.scroll.assert_not_called()


class TestFidgetMenuGuard(unittest.TestCase):
    def test_only_current_positive_game_frame_allows_background_input(self):
        task = object.__new__(BaseDNATask)
        current = np.zeros((10, 10, 3), dtype=np.uint8)
        task._executor = SimpleNamespace(_frame=None)
        task.in_team = Mock(return_value=True)
        self.assertFalse(task.fidget_in_game())
        task.in_team.assert_not_called()
        task.executor._frame = np.empty((0, 0, 3))
        self.assertFalse(task.fidget_in_game())
        task.executor._frame = current
        self.assertTrue(task.fidget_in_game())
        task.in_team.assert_called_once_with(frame=current)
        task.in_team.return_value = False
        self.assertFalse(task.fidget_in_game())
        task.in_team.side_effect = RuntimeError("resolution changing")
        self.assertFalse(task.fidget_in_game())

    def test_worker_keeps_menus_still_and_releases_alt_after_leaving_game(self):
        task = object.__new__(BaseDNATask)
        exit_event, menu_observed, modifier_released = Event(), Event(), Event()
        game = np.ones((2, 2, 3), dtype=np.uint8)
        menu = np.zeros((2, 2, 3), dtype=np.uint8)
        interaction = Mock()
        interaction.get_key_by_str.side_effect = lambda key: key
        task._executor = SimpleNamespace(current_task=task, exit_event=exit_event,
                                         paused=False, _frame=menu, interaction=interaction)
        task.enable_fidget_action = True
        task.fidget_params = {"paused": False, "hold_lalt": True}
        task.afk_config = {"鼠标抖动": True, "鼠标抖动锁定在窗口范围": True}
        task.key_config = {"Geniemon Key": "z"}
        task.get_spiral_dive_key = Mock(return_value="v")
        task.log_info = Mock()
        task.log_debug = Mock()
        task.set_mouse_in_window = Mock()
        task.__dict__["genshin_interaction"] = Mock()

        def identify(frame):
            if frame is menu:
                menu_observed.set()
                return False
            return True

        task.in_team = identify

        def posted(event, key, lparam):
            import win32con
            if key == "lalt" and event == win32con.WM_KEYUP:
                modifier_released.set()
            elif key != "lalt" and event == win32con.WM_KEYUP:
                task.executor._frame = menu

        interaction.post.side_effect = posted
        with ThreadPoolExecutor(max_workers=1) as pool, \
             patch.object(BaseDNATask, "thread_pool_executor", property(lambda _: pool)):
            try:
                task.setup_fidget_action()
                self.assertTrue(menu_observed.wait(1))
                with task.fidget_input_lock:
                    interaction.post.assert_not_called()
                    task.set_mouse_in_window.assert_not_called()
                    task.genshin_interaction.do_move_mouse_relative.assert_not_called()
                    task.executor._frame = game
                self.assertTrue(modifier_released.wait(3))
                with task.fidget_input_lock:
                    task.genshin_interaction.do_move_mouse_relative.assert_called_once()
                    # The worker has returned to the menu; no second jitter/key sequence.
                    self.assertFalse(task.fidget_in_game())
            finally:
                exit_event.set()
