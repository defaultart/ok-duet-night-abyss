import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ok import TaskDisabledException
from ok.test.TaskTestCase import TaskTestCase

from src.compat.window_client_area import ClientAreaTransform
from src.config import config
from src.dna_ui.Defs import COORD
from src.tasks.AutoWeaponAscensionTask import AutoWeaponAscensionTask


MODULE = "src.tasks.AutoWeaponAscensionTask"


class TestMediationScreens(TaskTestCase):
    task_class = AutoWeaponAscensionTask
    config = dict(config)

    def test_attached_screens_with_and_without_caption(self):
        for caption in (0, 30):
            for name in ("start", "manual", "letter", "fuse", "combat", "evacuate", "wall", "result"):
                with self.subTest(caption=caption, screen=name):
                    self.set_image(str(Path(__file__).parent / "images" / f"mediation_{name}.png"))
                    frame = self.task.frame
                    self.task.executor._frame = ClientAreaTransform(
                        frame.shape[1], frame.shape[0], top=caption).normalize_frame(frame)
                    task = self.task
                    self.assertEqual(bool(task.is_mediation_start()), name == "start")
                    self.assertEqual(bool(task.find_manual_select_btn()), name == "manual")
                    self.assertEqual(bool(task.find_letter_interface()), name == "letter")
                    self.assertEqual(bool(task.find_result_again_btn()), name == "result")
                    self.assertEqual(task.has_fuse_prompt(), name == "fuse")
                    objective = task.objective_text()
                    self.assertEqual(task.combat_objective(objective), name == "combat")
                    self.assertEqual("可前往撤离点" in objective, name in ("evacuate", "wall"))
                    self.assertEqual(bool(task.in_team()), name in ("fuse", "combat", "evacuate", "wall"))
                    if name == "result":
                        self.assertIn("委托完成", task.read_text("MEDIATION_RESULT_OCR"))


class TestMediationFlow(unittest.TestCase):
    def setUp(self):
        self.task = object.__new__(AutoWeaponAscensionTask)
        self.now = 0.0
        self.events = []
        task = self.task
        for name in ("info_set", "log_info", "reset_detection", "next_frame"):
            setattr(task, name, Mock())
        task.send_key_down = lambda key: self.events.append((self.now, "down", key))
        task.send_key_up = lambda key: self.events.append((self.now, "up", key))
        task.send_key = lambda key: self.events.append((self.now, "press", key))
        task.key_config = {"Interact Key": "f"}
        task.sleep = self.advance
        task.find_result_again_btn = Mock(return_value=False)
        task.find_start_interface = Mock(return_value=False)
        task.find_manual_select_btn = Mock(return_value=False)
        task.find_letter_interface = Mock(return_value=False)
        task.wait_until = Mock(side_effect=lambda condition, **kwargs: condition())
        task.has_fuse_prompt = Mock(return_value=True)
        task.objective_text = Mock(return_value="")
        task.check_lise_ultimate = Mock()
        self.clock = patch(f"{MODULE}.time", SimpleNamespace(monotonic=lambda: self.now))
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def advance(self, seconds):
        self.now += seconds

    def test_registration(self):
        self.assertIn([MODULE, "AutoWeaponAscensionTask"], config["onetime_tasks"])

    def test_unknown_state_waits_two_seconds_without_extra_interaction_or_q(self):
        self.task.has_fuse_prompt.return_value = False
        with patch(f"{MODULE}.random.uniform", return_value=6.85) as rng:
            self.assertFalse(self.task.run_attempt())
        rng.assert_called_once_with(6.8, 6.9)
        presses = [t for t, action, _ in self.events if action == "press"]
        self.assertEqual(len(presses), 10)
        self.assertTrue(all(5.85 - 1e-8 <= t < 6.85 for t in presses))
        self.task.check_lise_ultimate.assert_not_called()
        self.assertAlmostEqual(self.now, 8.85)

    def test_no_combat_times_out_70_seconds_after_second_f(self):
        with patch(f"{MODULE}.random.uniform", side_effect=[6.85, 0.5]):
            self.assertFalse(self.task.run_attempt())
        presses = [t for t, action, key in self.events if action == "press"]
        self.assertEqual(len(presses), 12)
        self.assertAlmostEqual(presses[-2], 6.85)
        self.assertAlmostEqual(presses[-1] - presses[-2], 0.5)
        self.assertGreaterEqual(self.now - presses[-1], 70)
        self.assertLess(self.now - presses[-1], 70.1)
        self.task.check_lise_ultimate.assert_not_called()

    def test_q_only_after_combat_and_stops_at_evacuation(self):
        task = self.task
        task.objective_text.side_effect = ["", "破坏“大烟花”的引信，阻止爆炸", "", "可前往撤离点"]
        task.evacuate = Mock(return_value=True)
        with patch(f"{MODULE}.random.uniform", side_effect=[6.85, 0.5]):
            self.assertTrue(task.run_attempt())
        self.assertEqual(task.check_lise_ultimate.call_count, 2)
        task.evacuate.assert_called_once_with()

    def test_combat_does_not_reset_deadline(self):
        self.task.objective_text.return_value = "破坏大烟花的引信"
        with patch(f"{MODULE}.random.uniform", side_effect=[6.85, 0.5]):
            self.assertFalse(self.task.run_attempt())
        self.assertGreater(self.task.check_lise_ultimate.call_count, 0)
        presses = [t for t, action, _ in self.events if action == "press"]
        self.assertEqual(len(presses), 10)
        self.assertGreaterEqual(self.now - presses[-1], 70)
        self.assertLess(self.now - presses[-1], 70.1)

    def test_initial_w_interactions_only_in_last_second_at_both_bounds(self):
        for duration in (6.8, 6.9):
            with self.subTest(duration=duration):
                self.now = 0
                self.events.clear()
                self.task.key_config["Interact Key"] = "e"
                with patch(f"{MODULE}.random.uniform", return_value=duration):
                    self.assertFalse(self.task.advance_to_fuse())
                self.assertAlmostEqual(self.now, duration)
                self.assertEqual(self.events[0], (0, "down", "w"))
                self.assertAlmostEqual(self.events[-1][0], duration)
                self.assertEqual(self.events[-1][1:], ("up", "w"))
                presses = [(t, key) for t, action, key in self.events if action == "press"]
                self.assertEqual(len(presses), 10)
                for index, (timestamp, key) in enumerate(presses):
                    self.assertAlmostEqual(timestamp, duration - 1 + index * 0.1)
                    self.assertEqual(key, "e")

    def test_initial_w_result_interrupts_before_or_during_interactions(self):
        for finish_at in (3, 5.9, 6.4):
            with self.subTest(finish_at=finish_at):
                self.now = 0
                self.events.clear()
                self.task.find_result_again_btn.side_effect = lambda: self.now >= finish_at
                with patch(f"{MODULE}.random.uniform", return_value=6.8):
                    self.assertTrue(self.task.run_attempt())
                self.assertTrue(all(t < finish_at for t, action, _ in self.events if action == "press"))
                self.assertIn((self.now, "up", "w"), self.events)
                self.assertLessEqual(self.now, finish_at + 0.101)

    def test_stop_during_initial_interactions_releases_w(self):
        def frame():
            if self.now >= 6.2:
                raise TaskDisabledException()

        self.task.next_frame.side_effect = frame
        with patch(f"{MODULE}.random.uniform", return_value=6.8):
            with self.assertRaises(TaskDisabledException):
                self.task.run_attempt()
        self.assertEqual([key for _, action, key in self.events[-3:] if action == "up"],
                         ["w", "a", "lshift"])

    def test_slow_interaction_does_not_extend_initial_movement(self):
        self.task.send_key = lambda key: self.advance(0.02)
        with patch(f"{MODULE}.random.uniform", return_value=6.8):
            self.assertFalse(self.task.advance_to_fuse())
        self.assertAlmostEqual(self.now, 6.8)

    def test_early_evacuation_skips_extra_interaction_and_combat(self):
        self.task.objective_text.return_value = "可前往撤离点"
        self.task.evacuate = Mock(return_value=True)
        with patch(f"{MODULE}.random.uniform", return_value=6.8):
            self.assertTrue(self.task.run_attempt())
        self.assertEqual(sum(action == "press" for _, action, _ in self.events), 10)
        self.task.evacuate.assert_called_once_with()
        self.task.has_fuse_prompt.assert_not_called()
        self.task.check_lise_ultimate.assert_not_called()

    def test_delayed_combat_recognition_skips_missing_prompt_failure(self):
        self.task.has_fuse_prompt.return_value = False
        self.task.objective_text.side_effect = lambda: "破坏大烟花的引信" if self.now >= 7.8 else ""
        with patch(f"{MODULE}.random.uniform", return_value=6.8):
            self.assertFalse(self.task.run_attempt())
        self.assertEqual(sum(action == "press" for _, action, _ in self.events), 10)
        self.assertGreater(self.task.check_lise_ultimate.call_count, 0)
        self.assertLess(self.now, 76.8)

    def test_result_during_post_movement_state_recognition(self):
        self.task.has_fuse_prompt.return_value = False
        self.task.find_result_again_btn.side_effect = lambda: self.now >= 7.5
        with patch(f"{MODULE}.random.uniform", return_value=6.8):
            self.assertTrue(self.task.run_attempt())
        self.assertEqual(sum(action == "press" for _, action, _ in self.events), 10)
        self.task.check_lise_ultimate.assert_not_called()

    def test_letter_confirmation_rechecks_and_never_changes_selection(self):
        task = self.task
        task.action_timeout = 10
        task.find_letter_interface.side_effect = [True, False]
        task.click_ui_coord = Mock()
        task.confirm_current_letter()
        task.click_ui_coord.assert_called_once_with(COORD.LETTER_CONFIRM, name="letter_confirm", use_safe_move=False)
        self.assertEqual(self.events, [])
        self.events.clear()
        task.find_letter_interface.side_effect = None
        task.find_letter_interface.return_value = False
        task.click_ui_coord.reset_mock()
        task.confirm_current_letter()
        task.click_ui_coord.assert_not_called()
        self.assertEqual(self.events, [])

    def test_letter_click_timeout_stops_without_keyboard_fallback(self):
        task = self.task
        task.action_timeout = 10
        task.fidget_params = {"paused": False}
        task.find_letter_interface.return_value = True
        task.click_ui_coord = Mock()
        with self.assertRaises(TaskDisabledException):
            task.confirm_current_letter()
        task.click_ui_coord.assert_called_once()
        self.assertEqual(self.events, [])
        self.assertFalse(task.fidget_params["paused"])

    def test_letter_page_is_safe_during_retry_and_exit_confirmation(self):
        task = self.task
        task.find_letter_interface.return_value = True
        self.assertFalse(task.open_in_mission_menu())
        task.open_in_mission_menu = Mock(return_value=True)
        task.action_timeout = 10
        task._click_detected = Mock()
        task.click_ui_coord = Mock()
        self.assertTrue(task.exit_mission())
        task.give_up_mission = Mock()
        task.retry_mission()
        task._click_detected.assert_not_called()
        task.click_ui_coord.assert_not_called()
        self.assertEqual([action for _, action, _ in self.events], ["up", "up", "up"])

    def test_letter_entry_and_start_result_manual_transitions(self):
        for entry, manual in (("letter", False), ("start", False),
                              ("result", False), ("start", True)):
            with self.subTest(entry=entry, manual=manual):
                task = self.task
                self.now = 0
                self.events.clear()
                page = {"name": entry}
                task.action_timeout = 10
                task.find_result_again_btn = lambda: page["name"] == "result"
                task.find_start_interface = lambda: page["name"] == "start"
                task.is_mediation_start = task.find_start_interface
                task.find_manual_select_btn = lambda: page["name"] == "manual"
                task.find_letter_interface = lambda: page["name"] == "letter"
                task.in_team = lambda: page["name"] == "game"

                def click(coord, **kwargs):
                    if coord == COORD.LETTER_CONFIRM:
                        self.assertEqual(page["name"], "letter")
                        page["name"] = "game"
                        return
                    self.assertIn(coord, (COORD.START_SCREEN_BTN, COORD.RESULT_AGAIN))
                    page["name"] = "manual" if manual else "letter"

                def press(key):
                    self.assertEqual(key, "space")
                    self.assertEqual(page["name"], "letter")
                    page["name"] = "game"

                task.click_ui_coord = Mock(side_effect=click)
                task.send_key = Mock(side_effect=press)
                task.choose_drop_rate = Mock(side_effect=lambda: page.update(name="letter"))
                task.run_attempt = Mock(side_effect=TaskDisabledException())
                with self.assertRaises(TaskDisabledException):
                    task.do_run()
                self.assertEqual(page["name"], "game")
                task.send_key.assert_not_called()
                self.assertEqual(task.click_ui_coord.call_count, 1 + int(entry != "letter"))
                self.assertEqual(task.click_ui_coord.call_args.args, (COORD.LETTER_CONFIRM,))
                self.assertEqual(self.events, [])

    def test_evacuation_timing_and_failed_fallback_at_both_bounds(self):
        for duration, delay, sideways in ((20, 0, (0.3, 0.35, 0.4)), (22, 1, (0.4, 0.35, 0.3))):
            with self.subTest(duration=duration, delay=delay):
                self.now = 0
                self.events.clear()
                with patch(f"{MODULE}.random.uniform", side_effect=[duration, delay, *sideways]) as rng:
                    self.assertFalse(self.task.evacuate())
                self.assertEqual(rng.call_args_list[0].args, (20, 22))
                self.assertEqual(rng.call_args_list[1].args, (0, 1))
                self.assertEqual([call.args for call in rng.call_args_list[2:]], [(0.3, 0.4)] * 3)
                downs = [(t, key) for t, action, key in self.events if action == "down"]
                movement_downs = [(t, key) for t, key in downs if key != "lshift"]
                shift_downs = [t for t, key in downs if key == "lshift"]
                self.assertEqual([key for _, key in movement_downs], ["w", "a", "w", "a", "w", "a", "w"])
                self.assertAlmostEqual(shift_downs[0], delay)
                self.assertEqual(len(shift_downs), 1)
                self.assertFalse(any(action == "up" and key == "lshift" and t < duration - 1e-6
                                     for t, action, key in self.events))
                expected_times = [0]
                next_start = duration
                for left_duration in sideways:
                    expected_times.extend([next_start, next_start + left_duration])
                    next_start += left_duration + 3
                for actual, expected in zip([t for t, _ in movement_downs], expected_times):
                    self.assertAlmostEqual(actual, expected)
                self.assertAlmostEqual(self.now, duration + sum(sideways) + 9 + 5)
                # Both sprint keys are released before lateral movement.
                for key in ("w", "lshift"):
                    self.assertTrue(any(abs(t - duration) < 1e-6 and action == "up" and k == key
                                        for t, action, k in self.events))
                self.task.check_lise_ultimate.assert_not_called()

    def test_result_ends_each_movement_stage_early(self):
        # Before Shift, during sprint, then during A and W in each of 3 groups.
        for finish_at in (0.2, 10, 20.2, 21, 23.5, 24, 26.9, 27.5):
            with self.subTest(finish_at=finish_at):
                self.now = 0
                self.events.clear()
                self.task.find_result_again_btn = lambda: self.now >= finish_at
                with patch(f"{MODULE}.random.uniform", side_effect=[20, 1, 0.35, 0.35, 0.35]):
                    self.assertTrue(self.task.evacuate())
                self.assertLessEqual(self.now, finish_at + 0.051)
                self.assertEqual(self.events[-1][1], "up")
                held = set()
                for _, action, key in self.events:
                    if action == "down":
                        held.add(key)
                    elif action == "up":
                        held.discard(key)
                self.assertEqual(held, set())

    def test_result_during_extra_five_seconds_has_no_held_keys(self):
        # 20s sprint + three (0.35s left + 3s forward) groups.
        movement_end = 30.05
        for delay in (0.1, 2.5, 5):
            with self.subTest(delay=delay):
                self.now = 0
                self.events.clear()
                held = set()

                def result():
                    if self.now >= movement_end + 0.001:
                        self.assertEqual(held, set())
                    return self.now >= movement_end + delay - 1e-8

                def down(key):
                    held.add(key)
                    self.events.append((self.now, "down", key))

                def up(key):
                    held.discard(key)

                self.task.send_key_down = down
                self.task.send_key_up = up
                self.task.find_result_again_btn = result
                with patch(f"{MODULE}.random.uniform", side_effect=[20, 1, 0.35, 0.35, 0.35]):
                    self.assertTrue(self.task.evacuate())
                self.assertLessEqual(self.now, movement_end + delay + 0.051)
                self.assertTrue(all(t <= movement_end for t, _, _ in self.events))
                self.task.log_info.assert_not_called()

    def test_stop_and_exception_release_all_movement(self):
        for error in (TaskDisabledException(), RuntimeError("capture failed")):
            with self.subTest(error=type(error).__name__):
                self.events.clear()
                self.task.next_frame.side_effect = error
                with self.assertRaises(type(error)):
                    self.task.run_attempt()
                self.assertEqual([key for _, action, key in self.events[-3:] if action == "up"],
                                 ["w", "a", "lshift"])

    def test_stop_and_exception_during_sprint_and_each_fallback(self):
        for error_type in (TaskDisabledException, RuntimeError):
            for stop_at in (0.2, 10, 20.2, 21, 23.5, 24, 26.9, 27.5):
                with self.subTest(error=error_type.__name__, stop_at=stop_at):
                    self.now = 0
                    self.events.clear()

                    def frame():
                        if self.now >= stop_at:
                            raise error_type()

                    self.task.next_frame.side_effect = frame
                    with patch(f"{MODULE}.random.uniform", side_effect=[20, 1, 0.35, 0.35, 0.35]):
                        with self.assertRaises(error_type):
                            self.task.evacuate()
                    held = set()
                    for _, action, key in self.events:
                        if action == "down":
                            held.add(key)
                        elif action == "up":
                            held.discard(key)
                    self.assertEqual(held, set())

    def test_run_restores_fidget_pause_on_error_and_stop(self):
        task = self.task
        task.fidget_params = {"paused": False}
        task.move_mouse_to_safe_position = Mock()
        task.set_check_monthly_card = Mock()
        task.do_run = Mock(side_effect=TaskDisabledException())
        with patch(f"{MODULE}.DNAOneTimeTask.run"):
            task.run()
        self.assertFalse(task.fidget_params["paused"])
        task.do_run.side_effect = RuntimeError("failed")
        with patch(f"{MODULE}.DNAOneTimeTask.run"), self.assertRaises(RuntimeError):
            task.run()
        self.assertFalse(task.fidget_params["paused"])

    def test_retry_releases_before_common_giveup_and_requires_recovery(self):
        task = self.task
        task.give_up_mission = Mock(side_effect=lambda: self.events.append((self.now, "giveup", "")))
        task.find_result_again_btn.side_effect = [False, True]
        task.retry_mission()
        self.assertEqual([action for _, action, _ in self.events], ["up", "up", "up", "giveup"])
        task.wait_until.assert_not_called()

    def test_retry_on_result_never_gives_up(self):
        self.task.find_result_again_btn.return_value = True
        self.task.give_up_mission = Mock()
        self.task.retry_mission()
        self.task.give_up_mission.assert_not_called()
        self.assertFalse(any(action == "press" for _, action, _ in self.events))

    def test_result_appears_before_or_after_esc_no_exit_clicks(self):
        for detections in ([True], [False, True], [False, False, True]):
            with self.subTest(detections=detections):
                self.events.clear()
                self.task.find_result_again_btn.side_effect = detections
                self.task.in_team = Mock(return_value=True)
                self.task.find_esc_menu = Mock(return_value=False)
                self.task.click_ui_coord = Mock()
                self.task._click_detected = Mock()
                self.assertFalse(self.task.exit_mission(timeout=10))
                self.task.click_ui_coord.assert_not_called()
                self.task._click_detected.assert_not_called()
                self.assertLessEqual(sum(action == "press" for _, action, _ in self.events), 1)

    def test_result_during_giveup_confirmation_prevents_exit(self):
        task = self.task
        task.open_in_mission_menu = Mock(return_value=True)
        task.find_result_again_btn.return_value = True
        task.find_center_confirm = Mock(return_value=object())
        task._click_detected = Mock()
        task.click_ui_coord = Mock()
        self.assertTrue(task.exit_mission(timeout=10))
        task.find_center_confirm.assert_not_called()
        task._click_detected.assert_not_called()
        task.click_ui_coord.assert_not_called()

    def test_retry_waits_for_two_consecutive_hud_frames(self):
        task = self.task
        task.find_esc_menu = Mock(side_effect=lambda: any(e[1] == "press" for e in self.events))
        task.in_team = Mock(side_effect=[False, True, False, True, True])
        self.assertTrue(task.open_in_mission_menu())
        self.assertEqual(self.events, [(0.8, "press", "esc")])

    def test_missing_hud_keeps_waiting_and_can_be_stopped(self):
        task = self.task
        task.find_esc_menu = Mock(return_value=False)
        task.in_team = Mock(return_value=False)

        def frame():
            if self.now >= 15:
                raise TaskDisabledException()

        task.next_frame.side_effect = frame
        with self.assertRaises(TaskDisabledException):
            task.open_in_mission_menu(time_out=1)
        self.assertEqual(self.events, [])
        self.assertGreater(task.in_team.call_count, 50)

    def test_after_esc_waits_past_timeout_without_repeating_esc(self):
        task = self.task
        task.find_esc_menu = Mock(return_value=False)
        task.in_team = Mock(return_value=True)
        task.find_result_again_btn.side_effect = lambda: self.now >= 15
        self.assertFalse(task.open_in_mission_menu(time_out=1))
        self.assertEqual(self.events, [(0.2, "press", "esc")])

    def test_unknown_retry_frames_can_recover_to_any_restart_page(self):
        task = self.task
        task.find_esc_menu = Mock(return_value=False)
        task.in_team = Mock(return_value=False)
        for detector in ("find_result_again_btn", "find_start_interface", "find_manual_select_btn"):
            with self.subTest(detector=detector):
                self.now = 0
                setattr(task, detector, Mock(side_effect=lambda: self.now >= 1))
                self.assertFalse(task.open_in_mission_menu())
                self.assertEqual(self.events, [])
                setattr(task, detector, Mock(return_value=False))

    def test_retry_recovery_accepts_manual_page_after_long_unknown_transition(self):
        task = self.task
        task.give_up_mission = Mock()
        task.find_manual_select_btn.side_effect = lambda: self.now >= 65
        task.retry_mission()
        self.assertGreaterEqual(self.now, 65)
        self.assertFalse(any(action == "press" for _, action, _ in self.events))

    def test_start_prioritizes_result_over_stale_hud(self):
        task = self.task
        task.find_result_again_btn.side_effect = [True, False]
        task.find_start_interface = Mock(return_value=False)
        task.find_manual_select_btn = Mock(return_value=True)
        task.click_ui_coord = Mock()
        task.start_mission(timeout=10)
        self.assertEqual(task.click_ui_coord.call_args.kwargs["name"], "mediation_again")

    def test_statistics_use_settlement_title_and_leave_unknown_unclassified(self):
        for title, expected in (("委托完成", (1, 0, 0)), ("再接再厉", (0, 1, 0)),
                                ("委托失败", (0, 1, 0)), ("", (0, 0, 1))):
            with self.subTest(title=title):
                task = self.task
                task.successful_runs = task.failed_runs = task.unknown_runs = 0
                task.read_text = Mock(return_value=title)
                task.record_result()
                self.assertEqual((task.successful_runs, task.failed_runs, task.unknown_runs), expected)

    def test_continuous_runs_restart_after_success_and_failure(self):
        for outcomes in ((False, True), (True, True)):
            with self.subTest(outcomes=outcomes):
                task = self.task
                page = {"name": "start", "attempts": 0}
                task.is_mediation_start = lambda: page["name"] == "start"
                task.find_start_interface = lambda: page["name"] == "start"
                task.find_result_again_btn = lambda: page["name"] == "result"
                task.find_manual_select_btn = lambda: page["name"] == "manual"
                task.in_team = lambda: page["name"] == "game"
                task.start_mission = Mock(side_effect=lambda: page.update(name="manual"))
                task.choose_drop_rate = Mock(side_effect=lambda: page.update(name="game"))
                task.give_up_mission = Mock(side_effect=lambda: page.update(name="result"))
                task.read_text = Mock(return_value="委托完成")

                def attempt():
                    index = page["attempts"]
                    if index == len(outcomes):
                        raise TaskDisabledException()
                    page["attempts"] += 1
                    if outcomes[index]:
                        page["name"] = "result"
                    return outcomes[index]

                task.run_attempt = attempt
                with self.assertRaises(TaskDisabledException):
                    task.do_run()
                self.assertEqual(task.start_mission.call_count, 3)
                self.assertEqual(task.choose_drop_rate.call_count, 3)
                self.assertEqual(task.give_up_mission.call_count, outcomes.count(False))
                self.assertEqual((task.successful_runs, task.failed_runs), (2, 0))

    def test_late_success_or_failure_after_esc_restarts_without_exit_click(self):
        for title, expected in (("委托完成", (1, 0)), ("再接再厉", (0, 1))):
            with self.subTest(title=title):
                self.now = 0
                self.events.clear()
                task = self.task
                page = {"name": "start", "attempts": 0}

                def frame():
                    if self.now >= 15 and any(e[1] == "press" for e in self.events):
                        if page["name"] == "game" and page["attempts"] == 1:
                            page["name"] = "result"

                def click(coord, **kwargs):
                    self.assertIn(coord, (COORD.START_SCREEN_BTN, COORD.RESULT_AGAIN))
                    page["name"] = "manual"

                def attempt():
                    page["attempts"] += 1
                    if page["attempts"] == 2:
                        raise TaskDisabledException()
                    return False

                task.next_frame = Mock(side_effect=frame)
                task.action_timeout = 10
                task.find_result_again_btn = lambda: page["name"] == "result"
                task.find_start_interface = lambda: page["name"] == "start"
                task.is_mediation_start = task.find_start_interface
                task.find_manual_select_btn = lambda: page["name"] == "manual"
                task.in_team = lambda: page["name"] == "game"
                task.find_esc_menu = Mock(return_value=False)
                task.click_ui_coord = Mock(side_effect=click)
                task._click_detected = Mock()
                task.choose_drop_rate = Mock(side_effect=lambda: page.update(name="game"))
                task.read_text = Mock(return_value=title)
                task.run_attempt = attempt
                with self.assertRaises(TaskDisabledException):
                    task.do_run()
                self.assertEqual((task.successful_runs, task.failed_runs), expected)
                self.assertEqual(task.unknown_runs, 0)
                self.assertEqual([e[2] for e in self.events if e[1] == "press"], ["esc"])
                self.assertEqual([c.args[0] for c in task.click_ui_coord.call_args_list],
                                 [COORD.START_SCREEN_BTN, COORD.RESULT_AGAIN])
                task._click_detected.assert_not_called()

    def test_wrong_entry_stops_without_clicking(self):
        self.task.is_mediation_start = Mock(return_value=False)
        self.task.start_mission = Mock()
        with self.assertRaisesRegex(RuntimeError, "调停"):
            self.task.do_run()
        self.task.start_mission.assert_not_called()


if __name__ == "__main__":
    unittest.main()
