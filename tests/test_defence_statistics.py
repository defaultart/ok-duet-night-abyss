"""Scoped HUD statistics and round-limit regression coverage for AutoDefence."""
import unittest
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
from ok.test.TaskTestCase import TaskTestCase

from src.compat.window_client_area import ClientAreaTransform
from src.config import config
from src.tasks.AutoDefence import (
    AutoDefence, DefenceHudReading, parse_defence_round, parse_defence_wave,
)
from src.tasks.AutoExploration import AutoExploration
from src.tasks.CommissionsTask import CommissionsTask, Mission, _default_movement


def texts(*names):
    return [SimpleNamespace(name=name) for name in names]


def completed(reading):
    future = Future()
    future.set_result(reading)
    return future


class TestDefenceStatistics(unittest.TestCase):
    def make_task(self):
        task = object.__new__(AutoDefence)
        task.external_movement = _default_movement
        task.current_round = 0
        task.current_wave = -1
        task.completed_mission_rounds = 0
        task.successful_missions = 0
        task._mission_success_counted = False
        task._round_dialog_counted = False
        task.wave_future = None
        task._defence_hud_future = None
        task.config = {"轮次": 5}
        task.action_timeout = 15
        task._executor = SimpleNamespace(frame=np.zeros((10, 10, 3), dtype=np.uint8))
        task.in_team = Mock(return_value=True)
        task.info_set = Mock()
        task.log_debug = Mock()
        task.log_info = Mock()
        task.ocr = Mock()
        task.screen_box = Mock(side_effect=lambda name: name)
        return task

    def test_parses_all_results_multi_digit_numbers_and_spaces(self):
        self.assertEqual(parse_defence_round(texts("噪声", "当前轮次：12")), 12)
        self.assertEqual(parse_defence_round(texts("0", "轮次 05")), 5)
        self.assertIsNone(parse_defence_round(texts("abc", "-3")))
        self.assertEqual(parse_defence_wave(texts("噪声", "10 / 12")), 10)
        self.assertEqual(parse_defence_wave(texts("13/12", "0／1")), 0)
        self.assertIsNone(parse_defence_wave(texts("12/0", "2/1", "noise")))

    def test_both_reads_use_the_same_snapshot_and_fixed_boxes(self):
        task = self.make_task()
        task.ocr.side_effect = [texts("4"), texts("0 / 1")]
        frame = object()
        self.assertEqual(task._read_defence_hud(frame, "round", "wave"), DefenceHudReading(4, 0))
        self.assertTrue(all(call.kwargs["frame"] is frame for call in task.ocr.call_args_list))
        self.assertEqual([c.kwargs["box"] for c in task.ocr.call_args_list], ["round", "wave"])

    def test_submission_copies_frame_and_uses_existing_pool(self):
        task = self.make_task()
        pool = Mock()
        with patch.object(AutoDefence, "thread_pool_executor", property(lambda _: pool)):
            task.get_wave_info()
        pool.submit.assert_called_once()
        callback, snapshot, round_box, wave_box = pool.submit.call_args.args
        self.assertEqual(callback, task._read_defence_hud)
        self.assertIsNot(snapshot, task.frame)
        task.frame[:] = 255
        self.assertFalse(snapshot.any())
        self.assertEqual((round_box, wave_box), ("DEFENCE_HUD_ROUND_OCR", "DEFENCE_HUD_WAVE_OCR"))

    def test_statistics_progress_from_zero_through_rounds_3_4_5(self):
        task = self.make_task()
        for number in (3, 4, 5):
            task._defence_hud_future = completed(DefenceHudReading(number, 0))
            task.get_wave_info()
            self.assertEqual(task.current_round, number)
            self.assertEqual(task.current_wave, 0)
        self.assertEqual(task.info_set.call_count, 4)
        task._defence_hud_future = completed(DefenceHudReading(5, 0))
        task.get_wave_info()
        self.assertEqual(task.info_set.call_count, 4)

    def test_noise_and_old_results_do_not_increment_or_overwrite(self):
        task = self.make_task()
        task.current_round, task.current_wave = 5, 0
        for reading in (DefenceHudReading(None, None), DefenceHudReading(3, 1)):
            task._defence_hud_future = completed(reading)
            task.get_wave_info()
            self.assertEqual((task.current_round, task.current_wave), (5, 0))
        task.info_set.assert_not_called()
        task._defence_hud_future = Future()
        task._defence_hud_future.set_exception(RuntimeError("OCR failed"))
        task.get_wave_info()
        self.assertEqual((task.current_round, task.current_wave), (5, 0))

    def test_reset_discards_running_future_and_new_mission_accepts_round_one(self):
        task = self.make_task()
        old = Future()
        old.set_running_or_notify_cancel()
        task._defence_hud_future = old
        task.current_round = 5
        task.skill_tick = Mock()
        task.init_all()
        self.assertIsNone(task._defence_hud_future)
        self.assertEqual((task.current_round, task.current_wave), (0, -1))
        old.set_result(DefenceHudReading(5, 0))
        self.assertEqual(task.current_round, 0)
        task._defence_hud_future = completed(DefenceHudReading(1, 0))
        task.get_wave_info()
        self.assertEqual(task.current_round, 1)

    def test_dialog_counts_actual_completed_round_and_does_not_repeat_increment(self):
        task = self.make_task()
        task.in_team.return_value = False
        task.find_action_dialog_continue = Mock(return_value=True)
        task.find_action_dialog_retreat = Mock(return_value=False)
        task.current_round = 81  # Real regression: noisy HUD previously forced retreat.
        for count in range(1, 6):
            task._round_dialog_counted = False  # Re-entered gameplay before the next dialog.
            self.assertEqual(task.stop_func(), count == 5)
            self.assertEqual(task.stop_func(), count == 5)
            self.assertEqual(task.completed_mission_rounds, count)
        self.assertEqual(task.info_set.call_count, 5)
        task.info_set.assert_called_with("完成轮次", "5/5")
        task.ocr.assert_not_called()

    def test_non_completion_pages_and_live_hud_never_count(self):
        task = self.make_task()
        task.current_round = 81
        task.find_action_dialog_continue = Mock(return_value=False)
        task.find_action_dialog_retreat = Mock(return_value=False)
        for in_team in (True, False):
            task.in_team.return_value = in_team
            self.assertFalse(task.stop_func())
        self.assertEqual(task.completed_mission_rounds, 0)

    def test_continue_keeps_count_and_latch_until_actual_gameplay(self):
        task = self.make_task()
        task.in_team.return_value = False
        task.find_action_dialog_continue = Mock(return_value=True)
        task.find_action_dialog_retreat = Mock(return_value=False)
        task.stop_func()
        task.init_for_next_round()
        self.assertFalse(task.stop_func())
        self.assertEqual(task.completed_mission_rounds, 1)
        task.skill_tick = Mock()
        task.init_all()  # Retry or another fresh mission.
        self.assertEqual(task.completed_mission_rounds, 0)
        self.assertFalse(task._round_dialog_counted)
        task.info_set.assert_any_call("完成轮次", "0/5")

    def test_five_round_main_loop_continues_four_times_then_retires(self):
        task = self.make_task()
        page = {"name": "game"}
        task.mission_status = None
        task.load_char = Mock()
        task.skill_tick = Mock()
        task.move_on_begin = Mock(return_value=True)
        task.check_for_monthly_card = Mock()
        task.sleep = Mock()
        task.in_team = lambda: page["name"] == "game"

        def complete_round():
            task.current_round = 81
            page["name"] = "choice"

        task.handle_in_mission = Mock(side_effect=complete_round)
        for name in ("find_letter_reward_btn", "find_letter_interface", "find_start_interface",
                     "find_result_again_btn", "find_action_dialog_retreat", "find_esc_menu"):
            setattr(task, name, Mock(return_value=False))
        task.find_action_dialog_continue = lambda: page["name"] == "choice"
        task.find_manual_select_btn = lambda: page["name"] == "manual"
        task.continue_mission = Mock(side_effect=lambda: page.update(name="manual"))
        task.choose_drop_rate = Mock(side_effect=lambda: page.update(name="game"))
        task.wait_until = Mock(side_effect=lambda condition, **kwargs: condition())

        class Withdrawn(Exception):
            pass

        task.quit_mission = Mock(side_effect=lambda: page.update(name="result"))

        def sleep(_):
            if page["name"] == "result":
                raise Withdrawn

        task.sleep.side_effect = sleep
        task.successful_missions = 7  # Starting the task begins a fresh session total.
        with self.assertRaises(Withdrawn):
            task.do_run()
        self.assertEqual(task.completed_mission_rounds, 5)
        self.assertEqual(task.handle_in_mission.call_count, 5)
        self.assertEqual(task.continue_mission.call_count, 4)
        self.assertEqual(task.choose_drop_rate.call_count, 4)
        task.quit_mission.assert_called_once()
        task.info_set.assert_any_call("完成轮次", "5/5")
        task.info_set.assert_any_call("累计成功场数", 0)
        task.info_set.assert_any_call("累计成功场数", 1)
        self.assertEqual(task.successful_missions, 1)

    def test_success_total_survives_new_missions_without_duplicate_counting(self):
        task = self.make_task()
        task.skill_tick = Mock()
        task.completed_mission_rounds = 5
        task._record_successful_mission()
        task._record_successful_mission()
        self.assertEqual(task.successful_missions, 1)
        task.init_for_next_round()
        self.assertEqual(task.successful_missions, 1)
        task.init_all()
        self.assertEqual(task.successful_missions, 1)
        task._record_successful_mission()  # Fresh/retried mission has no completions.
        self.assertEqual(task.successful_missions, 1)
        task.completed_mission_rounds = 5
        task._record_successful_mission()
        self.assertEqual(task.successful_missions, 2)
        task.info_set.assert_called_with("累计成功场数", 2)

    def test_incomplete_or_external_run_does_not_count_as_semi_auto_success(self):
        task = self.make_task()
        task.completed_mission_rounds = 4
        task._record_successful_mission()
        self.assertEqual(task.successful_missions, 0)
        task.external_movement = Mock()
        task.completed_mission_rounds = 5
        task._record_successful_mission()
        self.assertEqual(task.successful_missions, 0)

    def test_fifth_round_is_not_stopped_mid_combat_but_retreated_at_dialog(self):
        task = self.make_task()
        task.current_round = 81
        task.completed_mission_rounds = 4
        stop = Mock(return_value=True)
        self.assertFalse(task.handle_mission_interface(stop_func=stop))
        stop.assert_not_called()

        task.in_team.return_value = False
        task.check_for_monthly_card = Mock()
        for name in ("find_letter_reward_btn", "find_letter_interface", "find_manual_select_btn",
                     "find_start_interface", "find_result_again_btn", "find_action_dialog_retreat"):
            setattr(task, name, Mock(return_value=False))
        task.find_action_dialog_continue = Mock(return_value=True)
        task.ocr.return_value = texts("05")
        task.continue_mission = Mock()
        self.assertEqual(task.handle_mission_interface(stop_func=task.stop_func), Mission.STOP)
        task.continue_mission.assert_not_called()

    def test_external_path_delegates_and_other_tasks_keep_public_methods(self):
        task = self.make_task()
        task.external_movement = object()
        with patch.object(CommissionsTask, "get_wave_info", return_value="wave") as wave, \
             patch.object(CommissionsTask, "get_round_info", return_value="round") as rounds:
            self.assertEqual(task.get_wave_info(), "wave")
            self.assertEqual(task.get_round_info(), "round")
            wave.assert_called_once()
            rounds.assert_called_once()
        task.ocr.assert_not_called()
        self.assertIs(AutoExploration.get_wave_info, CommissionsTask.get_wave_info)
        self.assertIs(AutoExploration.get_round_info, CommissionsTask.get_round_info)


class TestDefenceStatisticsScreens(TaskTestCase):
    task_class = AutoDefence
    config = dict(config)

    def test_real_screens_through_async_round_and_wave_pipeline(self):
        task = self.task
        with ThreadPoolExecutor(max_workers=1) as pool, \
             patch.object(AutoDefence, "thread_pool_executor", property(lambda _: pool)):
            for top in (0, 29):
                task.init_all()
                for number in (3, 4, 5):
                    with self.subTest(round=number, caption_crop=top):
                        self.set_image(str(Path(__file__).parent / "images" / f"defence_hud_round{number}.png"))
                        raw = task.frame
                        task.executor._frame = ClientAreaTransform(
                            raw.shape[1], raw.shape[0], top=top).normalize_frame(raw)
                        task.get_wave_info()
                        self.assertIsNotNone(task._defence_hud_future)
                        self.assertEqual(task._defence_hud_future.result(timeout=10), DefenceHudReading(number, 0))
                        task.get_wave_info()
                        self.assertEqual((task.current_round, task.current_wave), (number, 0))

    def test_real_completed_dialog_and_empty_read_preserve_last_confirmed_round(self):
        task = self.task
        for top in (0, 29):
            with self.subTest(caption_crop=top):
                self.set_image(str(Path(__file__).parent / "images" / "action_dialog_defence.png"))
                raw = task.frame
                task.executor._frame = ClientAreaTransform(
                    raw.shape[1], raw.shape[0], top=top).normalize_frame(raw)
                task.current_round = 0
                task.get_round_info()
                self.assertEqual(task.current_round, 1)
                with patch.object(task, "ocr", return_value=[]):
                    task.get_round_info()
                self.assertEqual(task.current_round, 1)
