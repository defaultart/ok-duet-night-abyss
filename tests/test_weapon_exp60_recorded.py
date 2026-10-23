import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cv2
import numpy as np
from ok import Box, TaskDisabledException
from ok.test.TaskTestCase import TaskTestCase

from src.compat.window_client_area import ClientAreaTransform
from src.config import config
from src.dna_ui.Defs import COORD
from src.routes.weapon_exp60_gate import GateMatch, GateMatcher, GATE_TEMPLATE_PATH
from src.routes.weapon_exp60_t01 import T01_DURATION, T01_EVENTS, T01_GATE_CHECKPOINT
from src.tasks.AutoWeaponExp60RecordedTask import (
    AutoWeaponExp60RecordedTask, ROUTE_KEYS, build_t01_schedule,
)
from src.tasks.CommissionsTask import CommissionsTask


MODULE = 'src.tasks.AutoWeaponExp60RecordedTask'


class TestRecordedResultScreen(TaskTestCase):
    task_class = AutoWeaponExp60RecordedTask
    config = dict(config)

    def test_start_interface_with_and_without_caption(self):
        screenshot = str(Path(__file__).parent / 'images/weapon_exp60_start.png')
        for caption in (0, 30):
            with self.subTest(caption=caption):
                self.set_image(screenshot)
                frame = self.task.frame
                self.task.executor._frame = ClientAreaTransform(
                    frame.shape[1], frame.shape[0], top=caption).normalize_frame(frame)
                self.assertTrue(self.task.find_start_interface())
                self.assertFalse(self.task.find_manual_select_btn())
                self.assertFalse(self.task.find_result_again_btn())
                self.assertFalse(self.task.in_team())

    def test_user_result_screenshot_with_icon_missing_and_caption_normalized(self):
        screenshot = str(Path(__file__).parent / 'images/weapon_exp60_result_again.png')
        for caption in (0, 30):
            with self.subTest(caption=caption):
                self.set_image(screenshot)
                frame = self.task.frame
                self.task.executor._frame = ClientAreaTransform(
                    frame.shape[1], frame.shape[0], top=caption).normalize_frame(frame)
                with patch.object(CommissionsTask, 'find_result_again_btn', return_value=None):
                    self.assertTrue(self.task.find_result_again_btn())


class TestGateMatcher(unittest.TestCase):
    def setUp(self):
        self.matcher = GateMatcher()
        self.template = cv2.imdecode(np.fromfile(GATE_TEMPLATE_PATH, dtype=np.uint8), cv2.IMREAD_COLOR)

    def gate_frame(self, left, top, scale=1.0):
        frame = np.zeros((900, 1600, 3), dtype=np.uint8)
        height, width = self.template.shape[:2]
        template = cv2.resize(self.template, (round(width * scale), round(height * scale)))
        height, width = template.shape[:2]
        frame[top:top + height, left:left + width] = template
        return frame

    def test_gate_matches_shifted_positions_and_scales(self):
        for left, top, scale in ((398, 82, 1.0), (518, 142, 0.8), (300, 42, 1.2)):
            with self.subTest(left=left, top=top, scale=scale):
                self.assertTrue(self.matcher.match(self.gate_frame(left, top, scale)).found)

    def test_frame_resolution_is_normalized(self):
        frame = cv2.resize(self.gate_frame(398, 82), (1920, 1080))
        self.assertTrue(self.matcher.match(frame).found)

    def test_missing_or_out_of_area_gate_fails(self):
        self.assertFalse(self.matcher.match(np.zeros((900, 1600, 3), dtype=np.uint8)).found)
        self.assertFalse(self.matcher.match(self.gate_frame(1100, 500)).found)

    def test_original_and_strong_effect_scores_at_eighty_percent_threshold(self):
        for filename, caption, expected_found in (('weapon_exp60_gate_original.png', 0, True),
                                                  ('weapon_exp60_gate_strong_effect.png', 30, False)):
            with self.subTest(filename=filename):
                image = cv2.imdecode(np.fromfile(Path(__file__).parent / 'images' / filename,
                                                dtype=np.uint8), cv2.IMREAD_COLOR)
                image = ClientAreaTransform(image.shape[1], image.shape[0], top=caption).normalize_frame(image)
                result = self.matcher.match(image)
                self.assertEqual(result.found, expected_found)
                self.assertGreaterEqual(result.upper_score, 0.70)
                self.assertGreaterEqual(result.inner_score, 0.70)
                self.assertAlmostEqual(result.score, min(result.upper_score, result.inner_score))

    def test_text_heavy_start_and_result_screens_do_not_pass(self):
        for filename, caption in (('weapon_exp60_start.png', 0),
                                  ('weapon_exp60_result_again.png', 30)):
            with self.subTest(filename=filename):
                image = cv2.imdecode(np.fromfile(Path(__file__).parent / 'images' / filename,
                                                dtype=np.uint8), cv2.IMREAD_COLOR)
                image = ClientAreaTransform(image.shape[1], image.shape[0], top=caption).normalize_frame(image)
                self.assertFalse(self.matcher.match(image).found)

    def test_single_frame_threshold_is_inclusive_at_eighty_percent(self):
        self.assertFalse(GateMatch(0.79999).found)
        self.assertTrue(GateMatch(0.80).found)


class TestT01Schedule(unittest.TestCase):
    def test_reproduces_complete_configured_route(self):
        schedule, duration = build_t01_schedule()
        self.assertEqual(schedule, list(T01_EVENTS))
        self.assertEqual(duration, T01_DURATION)
        self.assertEqual(len(schedule), 52)
        self.assertAlmostEqual(duration, 70.1301083999997)
        self.assertEqual(schedule[-1][1:], ('d', False))
        self.assertAlmostEqual(duration - schedule[-1][0], 2.71950360000028)



class TestRecordedWeaponExpFlow(unittest.TestCase):
    def setUp(self):
        self.task = object.__new__(AutoWeaponExp60RecordedTask)
        self.now = 0.0
        self.events = []
        self.held = set()
        task = self.task
        task.lise_enabled = False
        task.action_timeout = 15
        task.set_phase = Mock()
        task.info_set = Mock()
        task.log_info = Mock()
        task.screenshot = Mock()
        task.check_gate_checkpoint = Mock(return_value=True)
        task.reset_statistics()
        task.next_frame = Mock()
        task.find_result_again_btn = Mock(return_value=False)
        task.check_lise_ultimate = Mock()
        task.send_key_down = lambda key: self.send(key, True)
        task.send_key_up = lambda key: self.send(key, False)
        task.sleep = self.advance
        self.clock = patch(f'{MODULE}.time', SimpleNamespace(
            monotonic=lambda: self.now, time_ns=lambda: round(self.now * 1e9)))
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def advance(self, seconds):
        self.now += seconds

    def send(self, key, down):
        self.events.append((self.now, key, down))
        if down:
            self.held.add(key)
        else:
            self.held.discard(key)

    def test_only_recorded_weapon_exp_task_is_registered(self):
        self.assertIn([MODULE, 'AutoWeaponExp60RecordedTask'], config['onetime_tasks'])
        self.assertNotIn(['src.tasks.AutoWeaponExp60Task', 'AutoWeaponExp60Task'],
                         config['onetime_tasks'])

    def test_checks_fresh_result_after_complete_route_and_releases(self):
        def fresh_frame():
            self.assertFalse(self.held)
            self.assertAlmostEqual(self.now, 1.5 + T01_DURATION)
            self.task.find_result_again_btn.return_value = True

        self.task.next_frame.side_effect = fresh_frame
        self.assertTrue(self.task.run_attempt())
        self.assertEqual(self.events[:len(T01_EVENTS)],
                         [(1.5 + timestamp, key, down) for timestamp, key, down in T01_EVENTS])
        self.task.next_frame.assert_called_once()
        self.task.find_result_again_btn.assert_called_once()
        self.task.check_lise_ultimate.assert_not_called()
        self.task.check_gate_checkpoint.assert_called_once()
        self.assertEqual(self.task.success_count, 1)
        self.assertEqual(self.task.failure_count, 0)
        self.assertFalse(self.task.lise_enabled)
        self.assertFalse(self.held)

    def test_missing_result_is_failure(self):
        self.assertFalse(self.task.run_attempt())
        self.assertGreater(self.task.next_frame.call_count, 1)
        self.assertAlmostEqual(self.now, 1.5 + T01_DURATION + 10.0)
        self.assertEqual(self.task.failure_count, 1)
        self.assertEqual(self.task.gate_failure_count, 0)
        self.assertEqual(self.task.success_count, 0)
        self.assertFalse(self.held)

    def test_checkpoint_pauses_route_clock_and_preserves_following_wait(self):
        def check():
            self.assertAlmostEqual(self.now, 1.5 + T01_GATE_CHECKPOINT)
            self.assertFalse(self.held)
            self.advance(0.6)
            return True

        self.task.check_gate_checkpoint.side_effect = check
        self.task.run_attempt()
        expected = [(1.5 + timestamp + (0.6 if timestamp > T01_GATE_CHECKPOINT else 0), key, down)
                    for timestamp, key, down in T01_EVENTS]
        for actual, recorded in zip(self.events[:len(T01_EVENTS)], expected):
            self.assertAlmostEqual(actual[0], recorded[0])
            self.assertEqual(actual[1:], recorded[1:])
        self.assertAlmostEqual(self.now, 1.5 + 0.6 + T01_DURATION + 10.0)

    def test_delayed_settlement_is_success_and_counted_only_once(self):
        end = 1.5 + T01_DURATION
        self.task.find_result_again_btn.side_effect = lambda: self.now >= end + 0.6
        self.assertTrue(self.task.run_attempt())
        self.assertGreater(self.task.next_frame.call_count, 1)
        self.assertEqual(self.task.success_count, 1)
        self.assertEqual(self.task.failure_count, 0)
        self.assertLessEqual(self.now, end + 0.801)

    def test_checkpoint_failure_ends_route_and_counts_once(self):
        self.task.check_gate_checkpoint.return_value = False
        self.assertFalse(self.task.run_attempt())
        self.assertFalse(self.held)
        self.assertTrue(all(t <= 1.5 + T01_GATE_CHECKPOINT for t, _, _ in self.events))
        self.task.find_result_again_btn.assert_not_called()
        self.assertEqual(self.task.failure_count, 0)
        self.assertEqual(self.task.gate_failure_count, 1)
        self.task.info_set.assert_any_call('门框OCR失败次数', 1)
        self.task.give_up_mission = Mock()
        self.task.wait_until = Mock()
        self.task.retry_mission()
        self.assertEqual(self.task.failure_count, 0)
        self.assertEqual(self.task.gate_failure_count, 1)

    def test_manual_stop_during_checkpoint_is_not_a_failure(self):
        self.task.check_gate_checkpoint.side_effect = TaskDisabledException()
        with self.assertRaises(TaskDisabledException):
            self.task.run_attempt()
        self.assertFalse(self.held)
        self.assertEqual(self.task.failure_count, 0)
        self.assertEqual(self.task.gate_failure_count, 0)
        self.assertEqual(self.task.success_count, 0)

    def prepare_gate_detection(self, scores):
        del self.task.check_gate_checkpoint
        self.task._executor = SimpleNamespace(frame=np.zeros((900, 1600, 3), dtype=np.uint8))
        self.task.gate_matcher = Mock()
        self.task.gate_matcher.match.side_effect = [GateMatch(score) for score in scores]

    def test_gate_passes_immediately_on_first_matching_frame(self):
        self.prepare_gate_detection([0.9, 0.3, 0.85, 0.87])
        self.assertTrue(self.task.check_gate_checkpoint())
        self.assertEqual(self.task.next_frame.call_count, 1)
        self.assertAlmostEqual(self.now, 0.0)
        self.task.info_set.assert_any_call('门框轮廓分数', '0.900')
        self.task.screenshot.assert_not_called()

    def test_gate_waits_for_a_single_hit_after_low_score(self):
        self.prepare_gate_detection([0.3, 0.80, 0.2])
        self.assertTrue(self.task.check_gate_checkpoint())
        self.assertEqual(self.task.next_frame.call_count, 2)
        self.assertAlmostEqual(self.now, 0.2)
        self.task.screenshot.assert_not_called()

    def test_gate_timeout_saves_failure_frame(self):
        self.prepare_gate_detection([0.2] * 20)
        self.assertFalse(self.task.check_gate_checkpoint())
        self.assertAlmostEqual(self.now, 3.0)
        self.task.screenshot.assert_called_once()
        self.assertEqual(self.task.screenshot.call_args.kwargs['frame'].shape, (900, 1600, 3))

    def test_send_overhead_does_not_accumulate_into_route(self):
        def send(key):
            self.send(key, True)
            self.advance(0.002)

        self.task.send_key_down = send
        self.assertFalse(self.task.run_attempt())
        self.assertAlmostEqual(self.now, 1.5 + T01_DURATION + 10.0)
        self.assertFalse(self.held)

    def test_stop_and_errors_release_all_route_keys(self):
        for error in (TaskDisabledException, RuntimeError):
            for stop_at in (0.1, 3.4, 53, 64, 66):
                with self.subTest(error=error.__name__, stop_at=stop_at):
                    self.now = 0
                    self.events.clear()
                    self.held.clear()

                    def sleep(seconds):
                        self.advance(seconds)
                        if self.now >= stop_at:
                            raise error()

                    self.task.sleep = sleep
                    with self.assertRaises(error):
                        self.task.run_attempt()
                    self.assertFalse(self.held)
                    self.task.find_result_again_btn.assert_not_called()

    def test_release_attempts_remaining_keys_after_one_key_errors(self):
        attempted = []

        def release(key):
            attempted.append(key)
            if key == ROUTE_KEYS[0]:
                raise RuntimeError('release failed')

        self.task.send_key_up = release
        with self.assertRaisesRegex(RuntimeError, 'release failed'):
            self.task.release_movement()
        self.assertEqual(attempted, list(ROUTE_KEYS))

    def test_failed_route_exits_then_successful_route_restarts(self):
        task = self.task
        page = {'name': 'start', 'attempts': 0, 'attempt_end': 0, 'settlement_pending': False}
        task.find_start_interface = lambda: page['name'] == 'start'

        def result():
            if page['name'] == 'game' and page['settlement_pending'] and self.now >= page['attempt_end']:
                page['name'] = 'result'
                page['settlement_pending'] = False
            return page['name'] == 'result'

        task.find_result_again_btn = result
        task.find_manual_select_btn = lambda: page['name'] == 'manual'
        task.in_team = lambda: page['name'] == 'game'
        task.start_mission = Mock(side_effect=lambda: page.update(name='manual'))
        task.confirm_start = Mock(side_effect=lambda: page.update(name='game'))
        task.give_up_mission = Mock(side_effect=lambda: page.update(name='result'))
        task.wait_until = Mock(side_effect=lambda condition, **kwargs: condition())
        run_attempt = task.run_attempt

        def attempt():
            page['attempts'] += 1
            if page['attempts'] == 3:
                raise TaskDisabledException()
            page['attempt_end'] = self.now + 1.5 + T01_DURATION
            page['settlement_pending'] = page['attempts'] == 2
            return run_attempt()

        task.run_attempt = attempt
        with self.assertRaises(TaskDisabledException):
            task.do_run()
        task.give_up_mission.assert_called_once()
        self.assertEqual(task.start_mission.call_count, 3)
        self.assertEqual(task.confirm_start.call_count, 3)
        self.assertGreater(task.next_frame.call_count, 2)
        self.assertEqual(task.failure_count, 1)
        self.assertEqual(task.success_count, 1)
        self.assertFalse(self.held)

    def test_run_restores_fidget_after_stop_or_error(self):
        task = self.task
        for error in (TaskDisabledException, RuntimeError):
            with self.subTest(error=error.__name__):
                task.fidget_params = {'paused': False}
                task.move_mouse_to_safe_position = Mock()
                task.set_check_monthly_card = Mock()
                task.soundBeep = Mock()
                task.log_info_notify = Mock()
                task.do_run = Mock(side_effect=error())
                task.success_count = 5
                task.failure_count = 4
                task.gate_failure_count = 3
                with patch(f'{MODULE}.DNAOneTimeTask.run'):
                    if error is RuntimeError:
                        with self.assertRaises(error):
                            task.run()
                        task.log_info_notify.assert_called_once_with(task.failure_message)
                    else:
                        task.run()
                self.assertFalse(task.fidget_params['paused'])
                self.assertFalse(self.held)
                self.assertEqual(task.success_count, 0)
                self.assertEqual(task.failure_count, 0)
                self.assertEqual(task.gate_failure_count, 0)


class TestRecordedRecovery(unittest.TestCase):
    def setUp(self):
        self.task = object.__new__(AutoWeaponExp60RecordedTask)
        self.now = 0.0
        self.task.action_timeout = 1.0
        for name in ('set_phase', 'info_set', 'next_frame', 'screenshot', 'send_key', 'send_key_up'):
            setattr(self.task, name, Mock())
        for name in ('find_result_again_btn', 'find_start_interface', 'find_manual_select_btn',
                     'find_letter_interface', 'in_team', 'find_esc_menu', 'find_center_confirm'):
            setattr(self.task, name, Mock(return_value=False))
        self.task.click_ui_coord = Mock(side_effect=lambda *args, **kwargs: self.advance(kwargs.get('after_sleep', 0)))
        self.task._click_detected = Mock()
        self.task.sleep = self.advance
        self.task.reset_statistics()
        clock = patch(f'{MODULE}.time', SimpleNamespace(
            monotonic=lambda: self.now, time_ns=lambda: round(self.now * 1e9)))
        clock.start()
        self.addCleanup(clock.stop)

    def advance(self, seconds):
        self.now += seconds

    def test_confirmation_preserves_the_current_commission_selection(self):
        task = self.task
        page = {'manual': True}
        task.find_manual_select_btn.side_effect = lambda: page['manual']
        task.choose_drop_rate_item = Mock()
        task.click_ui_coord.side_effect = lambda *args, **kwargs: page.update(manual=False)

        def wait(condition, post_action, **kwargs):
            self.assertFalse(condition())
            post_action()
            self.assertTrue(condition())

        task.wait_until = wait
        task.confirm_start()
        task.click_ui_coord.assert_called_once_with(
            COORD.MANUAL_CONFIRM, name='weapon_exp_start', after_sleep=0.25)
        task.choose_drop_rate_item.assert_not_called()

    def test_ocr_fallback_requires_both_buttons_and_accepts_split_words(self):
        task = self.task
        del task.find_result_again_btn
        box = Box(1000, 730, 570, 120)
        task.box_of_screen_scaled = Mock(return_value=box)
        task.ocr = Mock(return_value=[SimpleNamespace(name=name)
                                     for name in ('再次', '进行', '退出 委托')])
        with patch.object(CommissionsTask, 'find_result_again_btn', return_value=None):
            self.assertIs(task.find_result_again_btn(), box)
            task.ocr.return_value = [SimpleNamespace(name='再次进行')]
            self.assertIsNone(task.find_result_again_btn())
            task.ocr.return_value = [SimpleNamespace(name='再接再厉')]
            self.assertIsNone(task.find_result_again_btn())

    def test_result_page_clicks_again_and_waits_for_next_page_without_esc(self):
        task = self.task
        page = {'result': True}
        task.find_result_again_btn.side_effect = lambda: page['result']
        task.find_manual_select_btn.side_effect = lambda: not page['result']

        def click(coord, **kwargs):
            self.assertEqual(coord, COORD.RESULT_AGAIN)
            page['result'] = False
            self.advance(kwargs['after_sleep'])

        task.click_ui_coord.side_effect = click
        task.start_mission()
        task.click_ui_coord.assert_called_once()
        task.send_key.assert_not_called()
        self.assertEqual(task.success_count, 0)
        self.assertEqual(task.failure_count, 0)

    def test_retry_and_exit_on_settlement_do_not_send_esc_or_count_again(self):
        task = self.task
        task.success_count = 1
        task.find_result_again_btn.return_value = True
        task.retry_mission()
        self.assertFalse(task.exit_mission())
        task.send_key.assert_not_called()
        task.click_ui_coord.assert_not_called()
        self.assertEqual(task.success_count, 1)
        self.assertEqual(task.failure_count, 0)

    def test_settlement_appearing_immediately_before_esc_cancels_esc(self):
        task = self.task
        task.find_result_again_btn.side_effect = [False, True]
        task.in_team.return_value = True
        self.assertFalse(task.open_in_mission_menu())
        task.send_key.assert_not_called()

    def test_unknown_page_stops_with_screenshot_without_esc(self):
        with self.assertRaisesRegex(RuntimeError, '未确认仍在对局内'):
            self.task.open_in_mission_menu()
        self.task.screenshot.assert_called_once()
        self.task.send_key.assert_not_called()

    def test_menu_timeout_never_repeats_esc(self):
        self.task.in_team.return_value = True
        with self.assertRaisesRegex(RuntimeError, '不重复发送Esc'):
            self.task.open_in_mission_menu(time_out=0.6)
        self.task.send_key.assert_called_once_with('esc')
        self.task.screenshot.assert_called_once()

    def test_settlement_during_abandon_stops_menu_clicks(self):
        task = self.task
        task.open_in_mission_menu = Mock(return_value=True)
        page = {'result': False}
        task.find_result_again_btn.side_effect = lambda: page['result']
        task.find_esc_menu.return_value = True

        def click(coord, **kwargs):
            self.assertEqual(coord, COORD.ESC_GIVEUP)
            page['result'] = True

        task.click_ui_coord.side_effect = click
        self.assertTrue(task.exit_mission())
        task.click_ui_coord.assert_called_once()
        task._click_detected.assert_not_called()
        task.send_key.assert_not_called()

    def test_restart_timeout_saves_screenshot_without_esc(self):
        with self.assertRaisesRegex(RuntimeError, '超时'):
            self.task.start_mission(timeout=0.6)
        self.task.send_key.assert_not_called()
        self.task.screenshot.assert_called_once()


if __name__ == '__main__':
    unittest.main()
