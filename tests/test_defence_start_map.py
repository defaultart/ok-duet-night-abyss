"""Entry OCR must approve the map before semi-auto defence can move."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cv2
import numpy as np
from ok import TaskDisabledException
from ok.test.TaskTestCase import TaskTestCase

from src.compat.window_client_area import ClientAreaTransform
from src.config import config
from src.tasks.AutoDefence import (AutoDefence, classify_defence_start_reading,
                                   matches_defence_start_reading)
from src.tasks.CommissionsTask import _default_movement


def texts(*names):
    return [SimpleNamespace(name=name) for name in names]


class TestDefenceStartMap(unittest.TestCase):
    def make_task(self):
        task = object.__new__(AutoDefence)
        task.external_movement = _default_movement
        task.config = {"轮次": 5}
        task._mission_started = False
        task._executor = SimpleNamespace(frame=np.zeros((10, 10, 3), dtype=np.uint8))
        task.ocr = Mock()
        task.screen_box = Mock(side_effect=lambda name: name)
        task.log_info = Mock()
        task.log_debug = Mock()
        task.give_up_mission = Mock()
        task.apply_afk_mode = Mock(return_value=True)
        return task

    def test_target_and_distance_required_with_spaces_and_inclusive_limits(self):
        for distance in ("28米", "2 9 米", "30米", "36米", "3 7 米", "38米",
                         "50米", "5 1 米", "52米"):
            self.assertTrue(matches_defence_start_reading(texts("保 护 目 标"), texts(distance)))
        for target, distance in (([], texts("37米")), (texts("前往目标点"), texts("37米")),
                                 (texts("保护目标"), []), (texts("保护目标"), texts("35米")),
                                 (texts("保护目标"), texts("39米")),
                                 (texts("保护目标"), texts("27米")),
                                 (texts("保护目标"), texts("31米")),
                                 (texts("保护目标"), texts("49米")),
                                 (texts("保护目标"), texts("53米")),
                                 (texts("保护目标"), texts("137米")),
                                 (texts("保护目标"), texts("37"))):
            self.assertFalse(matches_defence_start_reading(target, distance))

    def test_classifies_distance_ranges_without_accepting_gaps(self):
        for scene in (29, 37, 51):
            for distance in range(scene - 1, scene + 2):
                self.assertEqual(classify_defence_start_reading(
                    texts("保护目标"), texts(f"{distance}米")), scene)
        for distance in (0, 27, 31, 35, 39, 49, 53, 137):
            self.assertIsNone(classify_defence_start_reading(
                texts("保护目标"), texts(f"{distance}米")))

    def test_reads_both_regions_from_same_snapshot(self):
        task = self.make_task()
        task.ocr.side_effect = [texts("保护目标"), texts("37米")]
        self.assertTrue(task.matches_defence_start_map())
        first, second = task.ocr.call_args_list
        self.assertIs(first.kwargs["frame"], second.kwargs["frame"])
        self.assertIsNot(first.kwargs["frame"], task.frame)
        self.assertEqual([first.kwargs["box"], second.kwargs["box"]],
                         ["DEFENCE_START_TARGET_OCR", "DEFENCE_START_DISTANCE_OCR"])

    def test_new_scenes_force_advance_in_every_afk_mode_once(self):
        for scene, timeout in ((29, 10), (51, 20)):
            for mode in ("开局重置角色位置", "开局向前走", "自动前进到开战"):
                with self.subTest(scene=scene, mode=mode):
                    task = self.make_task()
                    task.config = {"挂机模式": mode}
                    task.ocr.side_effect = [texts("保护目标"), texts(f"{scene}米")]
                    task.wait_until = lambda condition, **kwargs: condition()
                    task.advance_until_combat = Mock(return_value=True)
                    self.assertTrue(task.move_on_begin())
                    self.assertEqual(task.auto_advance_timeout, timeout)
                    self.assertTrue(task.move_on_begin())
                    task.advance_until_combat.assert_called_once_with()
                    task.apply_afk_mode.assert_not_called()

    def test_scene_and_timeout_reset_for_new_mission_but_not_continue(self):
        task = self.make_task()
        task.wait_until = lambda condition, **kwargs: condition()
        task.ocr.side_effect = [texts("保护目标"), texts("51米"),
                                texts("保护目标"), texts("37米"),
                                texts("保护目标"), texts("29米")]
        task.advance_until_combat = Mock(return_value=True)
        task.skill_tick = Mock()
        task.info_set = Mock()
        task.reset_wave_info = Mock()
        self.assertTrue(task.move_on_begin())
        task.init_for_next_round()
        self.assertTrue(task.move_on_begin())
        self.assertEqual(task._defence_start_scene, 51)
        self.assertEqual(task.auto_advance_timeout, 20)
        self.assertEqual(task.ocr.call_count, 2)
        task.init_all()
        self.assertIsNone(task._defence_start_scene)
        self.assertEqual(task.auto_advance_timeout, 10)
        self.assertTrue(task.move_on_begin())
        task.apply_afk_mode.assert_called_once()
        self.assertEqual(task._defence_start_scene, 37)
        self.assertEqual(task.auto_advance_timeout, 10)
        task.init_all()
        self.assertTrue(task.move_on_begin())
        self.assertEqual(task._defence_start_scene, 29)
        self.assertEqual(task.auto_advance_timeout, 10)

    def test_failed_or_cancelled_ocr_clears_previous_classification(self):
        task = self.make_task()
        task.ocr.side_effect = [texts("保护目标"), texts("51米"), [], [],
                                RuntimeError("OCR unavailable"), TaskDisabledException()]
        self.assertTrue(task.matches_defence_start_map())
        self.assertFalse(task.matches_defence_start_map())
        self.assertIsNone(task._defence_start_scene)
        self.assertFalse(task.matches_defence_start_map())
        self.assertIsNone(task._defence_start_scene)
        with self.assertRaises(TaskDisabledException):
            task.matches_defence_start_map()
        self.assertIsNone(task._defence_start_scene)

    def test_new_scene_timeout_and_release_before_restart(self):
        for scene, timeout in ((29, 10), (51, 20)):
            with self.subTest(scene=scene):
                task = self.make_task()
                task.ocr.side_effect = [texts("保护目标"), texts(f"{scene}米")]
                task.wait_until = lambda condition, **kwargs: condition()
                events = []
                task.send_key_down = lambda key: events.append(("down", key))
                task.send_key_up = lambda key: events.append(("up", key))
                task.give_up_mission.side_effect = lambda: events.append("restart")
                task.next_frame = Mock()
                task.is_in_combat = Mock(return_value=False)
                clock = Mock(side_effect=[0, timeout - 0.1, timeout])
                with patch("src.tasks.CommissionsTask.time", SimpleNamespace(time=clock)):
                    self.assertFalse(task.move_on_begin())
                self.assertEqual(task.next_frame.call_count, 1)
                self.assertEqual(events, [("down", "w"), ("up", "w"), "restart"])
                task.log_info.assert_called_with(f"前进 {timeout} 秒仍未进入战斗，重开任务")

    def test_new_scene_advances_two_extra_seconds_after_combat(self):
        task = self.make_task()
        task.ocr.side_effect = [texts("保护目标"), texts("51米")]
        task.wait_until = lambda condition, **kwargs: condition()
        task.send_key_down = Mock()
        task.send_key_up = Mock()
        task.next_frame = Mock()
        task.is_in_combat = Mock(return_value=True)
        clock = Mock(side_effect=[0, 19.9, 19.9, 20, 21.8, 21.9])
        with patch("src.tasks.CommissionsTask.time", SimpleNamespace(time=clock)):
            self.assertTrue(task.move_on_begin())
        self.assertEqual(task.next_frame.call_count, 3)
        task.log_info.assert_called_once_with("已进入战斗，再前进 2 秒")
        task.send_key_up.assert_called_once_with("w")
        task.give_up_mission.assert_not_called()

    def test_new_scenes_release_w_on_error_and_cancellation(self):
        for scene in (29, 51):
            for error in (RuntimeError("capture failed"), TaskDisabledException()):
                with self.subTest(scene=scene, error=type(error).__name__):
                    task = self.make_task()
                    task.ocr.side_effect = [texts("保护目标"), texts(f"{scene}米")]
                    task.wait_until = lambda condition, **kwargs: condition()
                    task.send_key_down = Mock()
                    task.send_key_up = Mock()
                    task.next_frame = Mock(side_effect=error)
                    with self.assertRaises(type(error)):
                        task.move_on_begin()
                    task.send_key_up.assert_called_once_with("w")
                    task.give_up_mission.assert_not_called()

    def test_all_afk_modes_check_before_movement_and_only_once(self):
        for mode in ("开局重置角色位置", "开局向前走", "自动前进到开战"):
            task = self.make_task()
            task.config = {"挂机模式": mode}
            events = []
            task.wait_until = Mock(side_effect=lambda *args, **kwargs: events.append("check") or True)
            task.apply_afk_mode.side_effect = lambda: events.append("move") or True
            self.assertTrue(task.move_on_begin())
            self.assertTrue(task.move_on_begin())
            self.assertEqual(events, ["check", "move"])
            task.wait_until.assert_called_once_with(task.matches_defence_start_map,
                                                   time_out=3, settle_time=0, raise_if_not_found=False)
            task.give_up_mission.assert_not_called()

    def test_unmatched_map_restarts_without_moving(self):
        task = self.make_task()
        task.wait_until = Mock(return_value=False)
        self.assertFalse(task.move_on_begin())
        task.give_up_mission.assert_called_once()
        task.log_info.assert_called_once_with("入场地图不匹配，重开任务")
        task.apply_afk_mode.assert_not_called()
        self.assertFalse(task._mission_started)

    def test_transient_ocr_failure_retries_and_then_moves(self):
        task = self.make_task()
        task.ocr.side_effect = [RuntimeError("OCR unavailable"), [], [],
                                texts("保护目标"), texts("37米")]

        def wait_until(condition, **kwargs):
            for _ in range(3):
                if condition():
                    return True
            return False

        task.wait_until = wait_until
        self.assertTrue(task.move_on_begin())
        task.apply_afk_mode.assert_called_once()
        task.give_up_mission.assert_not_called()

    def test_cancellation_is_not_treated_as_mismatch(self):
        task = self.make_task()
        task.ocr.side_effect = TaskDisabledException()
        with self.assertRaises(TaskDisabledException):
            task.matches_defence_start_map()
        task.give_up_mission.assert_not_called()

    def test_continue_keeps_approval_but_new_mission_resets_it(self):
        task = self.make_task()
        task.wait_until = Mock(return_value=True)
        task.skill_tick = Mock()
        task.info_set = Mock()
        task.reset_wave_info = Mock()
        task.move_on_begin()
        task.init_for_next_round()
        task.move_on_begin()
        self.assertEqual(task.wait_until.call_count, 1)
        task.init_all()
        task.move_on_begin()
        self.assertEqual(task.wait_until.call_count, 2)

    def test_external_route_skips_entry_check_and_afk_movement(self):
        task = self.make_task()
        task.external_movement = Mock()
        task.wait_until = Mock()
        self.assertTrue(task.move_on_begin())
        task.wait_until.assert_not_called()
        task.apply_afk_mode.assert_not_called()


class TestDefenceStartMapScreens(TaskTestCase):
    task_class = AutoDefence
    config = dict(config)

    def test_attached_map_original_caption_crop_and_scaled_frames(self):
        with TemporaryDirectory() as folder:
            for scene in (29, 37, 51):
                raw = cv2.imread(str(Path(__file__).parent / "images" / f"defence_start_target{scene}.png"))
                self.assertIsNotNone(raw)
                for top in (0, 30):
                    for width, height in ((1600, 900), (1280, 720), (1920, 1080)):
                        with self.subTest(scene=scene, caption_crop=top, size=(width, height)):
                            normalized = ClientAreaTransform(raw.shape[1], raw.shape[0], top=top).normalize_frame(raw)
                            path = str(Path(folder) / "scaled.png")
                            cv2.imwrite(path, cv2.resize(normalized, (width, height)))
                            self.set_image(path)
                            self.assertTrue(self.task.matches_defence_start_map())
                            self.assertEqual(self.task._defence_start_scene, scene)

    def test_other_defence_hud_does_not_match_entry_map(self):
        self.set_image(str(Path(__file__).parent / "images" / "defence_hud_round3.png"))
        self.assertFalse(self.task.matches_defence_start_map())
