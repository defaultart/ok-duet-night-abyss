import unittest
from unittest.mock import Mock

from ok import Box

from src.tasks.AutoDefence import AutoDefence
from src.tasks.CommissionsTask import (CommissionsTask, Mission, _default_movement,
                                       reset_action_box_for_row)
from src.tasks.fullauto.Auto65ArtifactTask_Fast import Auto65ArtifactTask_Fast


class TestAuto65ArtifactDirectReset(unittest.TestCase):

    def make_task(self, reset_result=True, is_lise=False, is_move=False, is_reset=True):
        task = object.__new__(Auto65ArtifactTask_Fast)
        task.config = {
            "是否移动": is_move,
            "是否重置角色位置": is_reset,
            "是否黎瑟": is_lise,
        }
        task._lise_ultimate_task = None
        task._lise_auto_started = False
        task.run_recorded_movement = Mock()
        task.reset_and_transport = Mock(return_value=reset_result)
        task.give_up_mission = Mock()
        task.get_ultimate_key = Mock(return_value="q")
        task.sleep = Mock()
        task.send_key = Mock()
        task.send_key_down = Mock()
        task.send_key_up = Mock()
        return task

    def test_entering_mission_resets_immediately_without_movement(self):
        task = self.make_task(reset_result=True)

        result = task.walk_to_aim(delay=2)

        self.assertTrue(result)
        task.reset_and_transport.assert_called_once_with(verify_in_team=False)
        task.run_recorded_movement.assert_not_called()
        task.give_up_mission.assert_not_called()
        task.sleep.assert_not_called()
        task.send_key.assert_not_called()
        task.send_key_down.assert_not_called()
        task.send_key_up.assert_not_called()

    def test_reset_without_team_verification_never_calls_ensure_main(self):
        task = object.__new__(CommissionsTask)
        task.open_in_mission_menu = Mock()
        reset_row = Box(114, 640, 85, 26, name="重置位置")
        task.wait_until = Mock(side_effect=[Mock(), Mock(), Mock(), reset_row, Mock(), Mock()])
        task.screen_box = Mock(return_value=Mock())
        task.sleep = Mock()
        task.find_esc_menu = Mock()
        task.click_ui_coord = Mock()
        task.find_one = Mock()
        task.calculate_color_percentage = Mock()
        task.click_box_random = Mock()
        task.find_reset_confirm = Mock()
        task.find_reset_position_row = Mock(return_value=Box(114, 640, 85, 26, name="重置位置"))
        task.scroll_relative = Mock()
        task._executor = Mock()
        task._executor.method.width = 1600
        task._executor.method.height = 900
        task.in_team = Mock(return_value=False)
        task.ensure_main = Mock()

        result = task.reset_and_transport(verify_in_team=False)

        self.assertTrue(result)
        task.in_team.assert_not_called()
        task.ensure_main.assert_not_called()
        task.scroll_relative.assert_not_called()

    def test_reset_temporarily_pauses_fidget_and_restores_it_on_error(self):
        task = object.__new__(CommissionsTask)
        task.fidget_params = {"paused": False}
        task.sleep = Mock()

        def fail_while_paused(**_):
            self.assertTrue(task.fidget_params["paused"])
            raise RuntimeError("reset failed")

        task._reset_and_transport_impl = Mock(side_effect=fail_while_paused)

        with self.assertRaisesRegex(RuntimeError, "reset failed"):
            task.reset_and_transport(verify_in_team=False)

        self.assertFalse(task.fidget_params["paused"])
        task.sleep.assert_called_once_with(1.0)

    def test_reset_action_follows_ocr_row_instead_of_fixed_y(self):
        top = reset_action_box_for_row(Box(114, 640, 85, 26, name="重置位置"), 1600, 900)
        scrolled_bottom = reset_action_box_for_row(Box(114, 244, 85, 26, name="重置位置"), 1600, 900)

        self.assertEqual((top.x, top.y, top.width, top.height), (1166, 643, 303, 20))
        self.assertEqual((scrolled_bottom.x, scrolled_bottom.y, scrolled_bottom.width, scrolled_bottom.height),
                         (1166, 247, 303, 20))
        self.assertLess(scrolled_bottom.y + scrolled_bottom.height, 640)

    def test_reset_action_scales_to_1080p(self):
        action = reset_action_box_for_row(Box(137, 768, 102, 31, name="重置位置"), 1920, 1080)

        self.assertEqual((action.x, action.width), (1399, 364))
        self.assertLessEqual(abs((action.y + action.height // 2) - (768 + 31 // 2)), 1)

    def test_reset_action_rejects_missing_or_unsafe_ocr(self):
        with self.assertRaises(ValueError):
            reset_action_box_for_row(None, 1600, 900)
        with self.assertRaises(ValueError):
            reset_action_box_for_row(Box(114, 640, 85, 26, name="注销账号"), 1600, 900)
        with self.assertRaises(ValueError):
            reset_action_box_for_row(Box(114, 760, 85, 26, name="重置位置"), 1600, 900)

    def test_reset_row_requires_two_stable_frames(self):
        task = object.__new__(CommissionsTask)
        task.sleep = Mock()
        task.log_info = Mock()
        task.find_reset_position_row = Mock(side_effect=[
            Box(114, 244, 83, 24, name="重置位置"),
            Box(114, 260, 83, 24, name="重置位置"),
        ])

        self.assertIsNone(task.find_stable_reset_position_row())
        task.log_info.assert_called_once()

        task.find_reset_position_row = Mock(side_effect=[
            Box(114, 244, 83, 24, name="重置位置"),
            Box(114, 246, 83, 24, name="重置位置"),
        ])
        stable = task.find_stable_reset_position_row()
        self.assertEqual(stable.y, 246)

    def test_missing_reset_row_aborts_without_clicking(self):
        task = object.__new__(CommissionsTask)
        task.open_in_mission_menu = Mock()
        task.screen_box = Mock(return_value=Mock())
        task.sleep = Mock()
        task.find_esc_menu = Mock()
        task.find_one = Mock()
        task.calculate_color_percentage = Mock()
        task.scroll_relative = Mock()
        task.find_reset_position_row = Mock(return_value=None)
        task.click_box_random = Mock()
        task.wait_until = Mock(side_effect=[Mock(), Mock(), Mock(), RuntimeError("未找到重置位置")])

        with self.assertRaisesRegex(RuntimeError, "未找到重置位置"):
            task.reset_and_transport(verify_in_team=False)

        task.click_box_random.assert_not_called()

    def test_missing_reset_confirmation_never_clicks_confirm(self):
        task = object.__new__(CommissionsTask)
        reset_row = Box(114, 640, 85, 26, name="重置位置")
        task.open_in_mission_menu = Mock()
        task.screen_box = Mock(return_value=Mock())
        task.sleep = Mock()
        task.find_esc_menu = Mock()
        task.find_one = Mock()
        task.calculate_color_percentage = Mock()
        task.scroll_relative = Mock()
        task.find_reset_position_row = Mock(return_value=reset_row)
        task.find_reset_confirm = Mock(return_value=None)
        task.click_box_random = Mock()
        task.click_ui_coord = Mock()
        task._executor = Mock()
        task._executor.method.width = 1600
        task._executor.method.height = 900

        call_index = 0

        def wait_side_effect(*args, **kwargs):
            nonlocal call_index
            call_index += 1
            if call_index <= 3:
                return Mock()
            if call_index == 4:
                return reset_row
            kwargs["post_action"]()
            raise RuntimeError("未找到复位确认弹窗")

        task.wait_until = Mock(side_effect=wait_side_effect)

        with self.assertRaisesRegex(RuntimeError, "未找到复位确认弹窗"):
            task.reset_and_transport(verify_in_team=False)

        task.click_box_random.assert_called_once()
        task.click_ui_coord.assert_not_called()
        _, click_kwargs = task.click_box_random.call_args
        self.assertEqual(click_kwargs["exact_down_time"], 0.01)
        self.assertEqual(click_kwargs["safe_move_box"].y, 643)

    def test_lise_mode_presses_ultimate_three_seconds_after_reset(self):
        task = self.make_task(reset_result=True, is_lise=True)

        task.walk_to_aim(delay=2)

        task.reset_and_transport.assert_called_once_with(verify_in_team=False)
        task.sleep.assert_called_once_with(3)
        task.get_ultimate_key.assert_called_once_with()
        task.send_key.assert_called_once_with("q")

    def test_lise_monitor_starts_only_after_initial_q(self):
        task = self.make_task(reset_result=True, is_lise=True)
        events = []
        monitor = Mock()
        monitor.reset_detection.side_effect = lambda: events.append("reset_detection")
        monitor.run.side_effect = lambda: events.append("monitor")
        task._lise_ultimate_task = monitor
        task.reset_and_transport.side_effect = lambda **_: events.append("reset") or True
        task.sleep.side_effect = lambda seconds: events.append(f"sleep:{seconds}")
        task.send_key.side_effect = lambda key: events.append(f"key:{key}")

        task.walk_to_aim(delay=2)
        task.run_lise_ultimate_tick()

        self.assertEqual(events, ["reset", "sleep:3", "key:q", "reset_detection", "monitor"])
        self.assertTrue(task._lise_auto_started)

    def test_all_enabled_runs_move_then_reset_then_lise(self):
        task = self.make_task(reset_result=True, is_lise=True, is_move=True, is_reset=True)
        events = []
        monitor = Mock()
        task._lise_ultimate_task = monitor
        task.run_recorded_movement.side_effect = lambda **_: events.append("move")
        task.reset_and_transport.side_effect = lambda **_: events.append("reset") or True
        task.sleep.side_effect = lambda seconds: events.append(f"sleep:{seconds}")
        task.send_key.side_effect = lambda key: events.append(f"key:{key}")
        monitor.reset_detection.side_effect = lambda: events.append("monitor_reset")

        self.assertTrue(task.walk_to_aim(delay=2))

        self.assertEqual(events, ["move", "reset", "sleep:3", "key:q", "monitor_reset"])
        task.run_recorded_movement.assert_called_once_with(delay=2)

    def test_each_optional_step_can_be_disabled_independently(self):
        move_only = self.make_task(is_move=True, is_reset=False)
        self.assertTrue(move_only.walk_to_aim(delay=2))
        move_only.run_recorded_movement.assert_called_once_with(delay=2)
        move_only.reset_and_transport.assert_not_called()
        move_only.send_key.assert_not_called()

        lise_only = self.make_task(is_lise=True, is_move=False, is_reset=False)
        self.assertTrue(lise_only.walk_to_aim(delay=2))
        lise_only.run_recorded_movement.assert_not_called()
        lise_only.reset_and_transport.assert_not_called()
        lise_only.sleep.assert_called_once_with(3)
        lise_only.send_key.assert_called_once_with("q")

        no_steps = self.make_task(is_lise=False, is_move=False, is_reset=False)
        self.assertTrue(no_steps.walk_to_aim(delay=2))
        no_steps.run_recorded_movement.assert_not_called()
        no_steps.reset_and_transport.assert_not_called()
        no_steps.sleep.assert_not_called()
        no_steps.send_key.assert_not_called()

    def test_immediate_start_only_for_reset_without_movement(self):
        cases = (
            ({"是否移动": False, "是否重置角色位置": True}, True),
            ({"是否移动": True, "是否重置角色位置": True}, False),
            ({"是否移动": True, "是否重置角色位置": False}, False),
            ({"是否移动": False, "是否重置角色位置": False}, False),
        )
        task = object.__new__(Auto65ArtifactTask_Fast)
        for config, expected in cases:
            with self.subTest(config=config):
                task.config = config
                self.assertEqual(task.should_start_immediately(), expected)

    def test_recorded_movement_preserves_original_input_sequence(self):
        task = object.__new__(Auto65ArtifactTask_Fast)
        events = []
        task.get_dodge_key = Mock(return_value="shift")
        task.sleep = Mock(side_effect=lambda seconds: events.append(("sleep", seconds)))
        task.send_key_down = Mock(side_effect=lambda key: events.append(("down", key)))
        task.send_key_up = Mock(side_effect=lambda key: events.append(("up", key)))
        task.send_key = Mock(side_effect=lambda key, **kwargs: events.append(("press", key, kwargs["down_time"])))

        task.run_recorded_movement(delay=2)

        route_end = events.index(("up", "lalt")) + 1
        self.assertEqual(events[:route_end], [
            ("down", "lalt"), ("sleep", 2), ("down", "w"),
            ("sleep", 0.59), ("down", "shift"),
            ("sleep", 0.22), ("down", "a"),
            ("sleep", 1.08), ("up", "w"),
            ("sleep", 1.44), ("down", "w"),
            ("sleep", 0.09), ("up", "a"),
            ("sleep", 0.90), ("down", "a"),
            ("sleep", 0.38), ("up", "shift"),
            ("sleep", 0.24), ("press", "shift", 0.35),
            ("sleep", 0.79), ("press", "shift", 0.41),
            ("sleep", 0.80), ("down", "shift"),
            ("sleep", 1.27), ("up", "w"),
            ("sleep", 0.47), ("down", "w"),
            ("sleep", 0.35), ("up", "w"),
            ("sleep", 0.79), ("press", "space", 0.09),
            ("sleep", 2.04), ("press", "s", 0.09),
            ("sleep", 0.40), ("press", "w", 0.10),
            ("sleep", 0.44), ("press", "s", 0.10),
            ("sleep", 4.93), ("up", "shift"),
            ("sleep", 0.10), ("up", "a"), ("up", "lalt"),
        ])
        self.assertEqual(events[route_end:], [
            ("up", "w"), ("up", "a"), ("up", "s"),
            ("up", "d"), ("up", "shift"), ("up", "lalt"),
        ])

    def test_movement_failure_releases_keys_and_skips_later_steps(self):
        task = self.make_task(is_lise=True, is_move=True, is_reset=True)
        task.run_recorded_movement.side_effect = RuntimeError("route failed")

        self.assertFalse(task.walk_to_aim(delay=2))

        task.give_up_mission.assert_called_once_with()
        task.reset_and_transport.assert_not_called()
        task.send_key.assert_not_called()

    def test_recorded_movement_releases_all_keys_when_input_raises(self):
        task = object.__new__(Auto65ArtifactTask_Fast)
        task.get_dodge_key = Mock(return_value="shift")
        task.sleep = Mock()
        task.send_key_down = Mock(side_effect=[None, RuntimeError("input failed")])
        task.send_key_up = Mock()
        task.send_key = Mock()

        with self.assertRaisesRegex(RuntimeError, "input failed"):
            task.run_recorded_movement(delay=2)

        released = [call.args[0] for call in task.send_key_up.call_args_list]
        self.assertEqual(released, ["w", "a", "s", "d", "shift", "lalt"])

    def test_lise_monitor_tick_is_gated_and_does_not_enable_trigger(self):
        task = self.make_task(reset_result=True, is_lise=True)
        monitor = Mock()
        task._lise_ultimate_task = monitor

        task.run_lise_ultimate_tick()
        monitor.run.assert_not_called()
        monitor.enable.assert_not_called()
        monitor.disable.assert_not_called()

        task._lise_auto_started = True
        task.run_lise_ultimate_tick()
        monitor.run.assert_called_once_with()
        monitor.enable.assert_not_called()
        monitor.disable.assert_not_called()

    def test_failed_reset_gives_up_for_the_outer_loop_to_restart(self):
        task = self.make_task(reset_result=False, is_lise=True)
        monitor = Mock()
        task._lise_ultimate_task = monitor

        result = task.walk_to_aim(delay=2)

        self.assertFalse(result)
        task.reset_and_transport.assert_called_once_with(verify_in_team=False)
        task.give_up_mission.assert_called_once_with()
        task.sleep.assert_not_called()
        task.send_key.assert_not_called()
        monitor.reset_detection.assert_not_called()
        monitor.run.assert_not_called()


class TestAuto65ArtifactRoundLimit(unittest.TestCase):

    def make_defence(self, limit=10):
        task = object.__new__(AutoDefence)
        task.external_movement = Mock()
        task._external_config = {"最多场数": limit}
        task._base_config = {}
        task._merged_config_cache = None
        task.completed_mission_rounds = 0
        task._round_dialog_counted = False
        task.info_set = Mock()
        task.find_result_again_btn = Mock(return_value=False)
        task.find_start_interface = Mock(return_value=False)
        return task

    def test_tenth_completed_round_stops(self):
        task = self.make_defence(limit=10)

        for completed in range(1, 11):
            task._round_dialog_counted = False
            should_stop = task.stop_func()
            self.assertEqual(should_stop, completed == 10)

        self.assertEqual(task.completed_mission_rounds, 10)
        task.info_set.assert_called_with("完成场数", "10/10")

    def test_same_action_dialog_is_only_counted_once(self):
        task = self.make_defence(limit=10)

        self.assertFalse(task.stop_func())
        self.assertFalse(task.stop_func())

        self.assertEqual(task.completed_mission_rounds, 1)

    def test_invalid_or_non_positive_limit_falls_back_to_ten(self):
        for value in ("bad", 0, -3, None):
            with self.subTest(value=value):
                task = self.make_defence(limit=value)
                self.assertEqual(task.external_round_limit(), 10)

    def test_normal_auto_defence_uses_actual_completions_instead_of_hud(self):
        task = self.make_defence(limit=10)
        task.log_info = Mock()
        task.external_movement = _default_movement
        task.current_round = 3
        task._base_config = {"轮次": 3}
        task._merged_config_cache = None
        task.get_round_info = Mock()

        task.in_team = Mock(return_value=False)
        task.find_action_dialog_continue = Mock(return_value=True)
        task.find_action_dialog_retreat = Mock(return_value=False)
        self.assertFalse(task.stop_func())
        task.get_round_info.assert_not_called()
        self.assertEqual(task.completed_mission_rounds, 1)

    def test_limited_task_returns_after_withdrawing_last_round(self):
        task = self.make_defence(limit=1)
        task.init_all = Mock()
        task.load_char = Mock()
        task.in_team = Mock(return_value=False)
        task.quit_mission = Mock()
        task.log_info = Mock()
        task.soundBeep = Mock()
        task.sleep = Mock()

        def finish_first_round(stop_func):
            self.assertTrue(stop_func())
            return Mission.STOP

        task.handle_mission_interface = Mock(side_effect=finish_first_round)

        task.do_run()

        task.quit_mission.assert_called_once_with()
        task.soundBeep.assert_called_once_with()
        task.sleep.assert_not_called()

    def test_result_screen_counts_round_and_updates_display(self):
        task = self.make_defence(limit=15)
        task._external_round_started = True
        task.find_result_again_btn = Mock(return_value=True)

        self.assertFalse(task.handle_external_round_completion())

        self.assertEqual(task.completed_mission_rounds, 1)
        task.info_set.assert_any_call("完成场数", "1/15")

    def test_return_to_start_screen_counts_round_but_initial_screen_does_not(self):
        task = self.make_defence(limit=15)
        task.find_start_interface = Mock(return_value=True)

        task._external_round_started = False
        self.assertFalse(task.handle_external_round_completion())
        self.assertEqual(task.completed_mission_rounds, 0)

        # 模拟一轮已经成功复位，重新出现开始页时才计数。
        task._external_round_started = True
        self.assertFalse(task.handle_external_round_completion())
        self.assertEqual(task.completed_mission_rounds, 1)
        task.info_set.assert_any_call("完成场数", "1/15")

    def test_last_result_screen_stops_without_clicking_current_page(self):
        task = self.make_defence(limit=1)
        task.find_result_again_btn = Mock(return_value=True)
        task._external_round_started = True
        task.log_info = Mock()
        task.soundBeep = Mock()
        task.click_ui_coord = Mock()

        self.assertTrue(task.handle_external_round_completion())

        self.assertEqual(task.completed_mission_rounds, 1)
        task.info_set.assert_any_call("完成场数", "1/1")
        task.soundBeep.assert_called_once_with()
        task.click_ui_coord.assert_not_called()

    def test_immediate_external_start_skips_thirty_second_team_wait(self):
        task = self.make_defence(limit=1)
        task.external_movement_immediate = True
        task.restart_on_external_combat_timeout = False
        task.init_all = Mock()
        task.load_char = Mock()
        task.in_team = Mock(return_value=False)
        task.wait_until = Mock()
        task.handle_mission_start = Mock()
        task.quit_mission = Mock()
        task.log_info = Mock()
        task.soundBeep = Mock()
        task.sleep = Mock()

        statuses = iter((Mission.START, Mission.STOP))

        def next_status(stop_func):
            status = next(statuses)
            if status == Mission.STOP:
                self.assertTrue(stop_func())
            return status

        task.handle_mission_interface = Mock(side_effect=next_status)

        task.do_run()

        task.wait_until.assert_not_called()
        task.handle_mission_start.assert_called_once_with()

    def test_external_combat_timeout_does_not_open_esc_when_disabled(self):
        task = self.make_defence(limit=10)
        task.external_movement = Mock(return_value=True)
        task.restart_on_external_combat_timeout = False
        task.action_timeout = 0
        task.wait_until = Mock(return_value=False)
        task.find_esc_menu = Mock(return_value=False)
        task.get_wave_info = Mock()
        task.open_in_mission_menu = Mock()
        task.log_info = Mock()
        task.current_wave = -1

        task.handle_mission_start()

        task.open_in_mission_menu.assert_not_called()
        task.log_info.assert_called_with("等待战斗开始超时，继续观察（不会自动打开ESC）")

    def test_external_runtime_tick_runs_during_combat_start_wait_only_in_team(self):
        task = self.make_defence(limit=10)
        task.external_runtime_tick = Mock()
        task.get_wave_info = Mock()
        task.in_team = Mock(return_value=True)

        task._update_external_start_wait()

        task.get_wave_info.assert_called_once_with()
        task.external_runtime_tick.assert_called_once_with()

        task.external_runtime_tick.reset_mock()
        task.in_team.return_value = False
        task._update_external_start_wait()
        task.external_runtime_tick.assert_not_called()

    def test_external_runtime_tick_runs_from_main_loop_while_in_team(self):
        task = self.make_defence(limit=10)
        task.external_runtime_tick = Mock()
        task.init_all = Mock()
        task.load_char = Mock()
        task.in_team = Mock(return_value=True)
        task.open_in_mission_menu = Mock()
        task.move_on_begin = Mock(return_value=False)
        task.handle_external_round_completion = Mock(return_value=False)
        task.handle_mission_interface = Mock(side_effect=StopIteration)

        with self.assertRaises(StopIteration):
            task.do_run()

        task.external_runtime_tick.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
