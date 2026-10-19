import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ok import TaskDisabledException
from ok.test.TaskTestCase import TaskTestCase

from src.compat.window_client_area import ClientAreaTransform
from src.config import config
from src.tasks.AutoWeaponAscensionTask import AutoWeaponAscensionTask


MODULE = "src.tasks.AutoWeaponAscensionTask"


class TestMediationScreens(TaskTestCase):
    task_class = AutoWeaponAscensionTask
    config = dict(config)

    def test_attached_screens_with_and_without_caption(self):
        for caption in (0, 30):
            for name in ("start", "manual", "fuse", "combat", "evacuate", "wall", "result"):
                with self.subTest(caption=caption, screen=name):
                    self.set_image(str(Path(__file__).parent / "images" / f"mediation_{name}.png"))
                    frame = self.task.frame
                    self.task.executor._frame = ClientAreaTransform(
                        frame.shape[1], frame.shape[0], top=caption).normalize_frame(frame)
                    task = self.task
                    self.assertEqual(bool(task.is_mediation_start()), name == "start")
                    self.assertEqual(bool(task.find_manual_select_btn()), name == "manual")
                    self.assertEqual(bool(task.find_result_again_btn()), name == "result")
                    self.assertEqual(task.has_fuse_prompt(), name == "fuse")
                    objective = task.objective_text()
                    self.assertEqual(task.combat_objective(objective), name == "combat")
                    self.assertEqual("可前往撤离点" in objective, name in ("evacuate", "wall"))
                    self.assertEqual(bool(task.in_team()), name in ("fuse", "combat", "evacuate", "wall"))


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

    def test_no_prompt_fails_without_interaction_or_q(self):
        self.task.has_fuse_prompt.return_value = False
        with patch(f"{MODULE}.random.uniform", return_value=6.85) as rng:
            self.assertFalse(self.task.run_attempt())
        rng.assert_called_once_with(6.8, 6.9)
        self.task.wait_until.assert_called_once_with(
            self.task.has_fuse_prompt, time_out=2, raise_if_not_found=False)
        self.assertFalse(any(action == "press" for _, action, _ in self.events))
        self.task.check_lise_ultimate.assert_not_called()
        self.assertAlmostEqual(self.now, 6.85)

    def test_no_combat_times_out_70_seconds_after_second_f(self):
        with patch(f"{MODULE}.random.uniform", side_effect=[6.85, 0.5]):
            self.assertFalse(self.task.run_attempt())
        presses = [t for t, action, key in self.events if action == "press"]
        self.assertAlmostEqual(presses[0], 6.85)
        self.assertAlmostEqual(presses[1] - presses[0], 0.5)
        self.assertGreaterEqual(self.now - presses[1], 70)
        self.assertLess(self.now - presses[1], 70.1)
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
        self.assertGreaterEqual(self.now, 77.35)
        self.assertLess(self.now, 77.45)

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
                self.assertAlmostEqual(self.now, duration + sum(sideways) + 9)
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
        task.find_result_again_btn.return_value = True
        task.retry_mission()
        self.assertEqual([action for _, action, _ in self.events], ["up", "up", "up", "giveup"])
        self.assertEqual(task.wait_until.call_args.kwargs, {"time_out": 60, "raise_if_not_found": True})

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

    def test_wrong_entry_stops_without_clicking(self):
        self.task.is_mediation_start = Mock(return_value=False)
        self.task.start_mission = Mock()
        with self.assertRaisesRegex(RuntimeError, "调停"):
            self.task.do_run()
        self.task.start_mission.assert_not_called()


if __name__ == "__main__":
    unittest.main()
