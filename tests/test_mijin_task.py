import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from src.config import config
from src.tasks.fullauto.AutoMijinTask import AutoMijinTask


def make_task():
    task = object.__new__(AutoMijinTask)
    task.log_info = Mock()
    task.info_set = Mock()
    task.reset_statistics()
    task.send_key = Mock()
    task.click_ui_coord = Mock()
    task.in_team = Mock(return_value=True)
    task.key_config = {'Ultimate Key': 'q'}
    task.has_text = Mock(return_value=False)
    task._read_hud_number = Mock(return_value=10)
    task.reset_runtime()
    return task


class TestMijinTask(unittest.TestCase):
    def test_registered(self):
        self.assertIn(['src.tasks.fullauto.AutoMijinTask', 'AutoMijinTask'], config['onetime_tasks'])

    def test_q_confirmed_cycle_without_energy_check(self):
        task = make_task()
        task.tick_q(0)
        task.send_key.assert_not_called()
        task.tick_q(.4)
        self.assertEqual(task.send_key.call_count, 1)
        task._read_hud_number.return_value = 0
        task.tick_q(.8)
        task.tick_q(1.2)  # 开启确认，从这里计五秒。
        task.tick_q(6.1)
        self.assertEqual(task.send_key.call_count, 1)
        task.tick_q(6.5)
        self.assertEqual(task.send_key.call_count, 2)
        task._read_hud_number.return_value = 10
        task.tick_q(6.9)
        task.tick_q(7.3)  # 关闭确认，从这里计三秒。
        task.tick_q(10.2)
        self.assertEqual(task.send_key.call_count, 2)
        task.tick_q(10.6)
        self.assertEqual(task.send_key.call_count, 3)
        self.assertTrue(all(call.args == ('LISE_Q_COST_OCR',)
                            for call in task._read_hud_number.call_args_list))

    def test_unknown_requires_two_new_reads_and_failed_send_retries(self):
        task = make_task()
        task.send_key.return_value = False
        for now in (0, .4):
            task.tick_q(now)
        task._read_hud_number.return_value = None
        task.tick_q(.8)
        task._read_hud_number.return_value = 10
        task.tick_q(1.2)
        self.assertEqual(task.send_key.call_count, 1)
        task.tick_q(1.6)
        self.assertEqual(task.send_key.call_count, 2)
        task.tick_q(2.0)
        self.assertEqual(task.send_key.call_count, 2)

    def test_already_active_waits_five_seconds(self):
        task = make_task()
        task._read_hud_number.return_value = 0
        for now in (0, .4, 5.3):
            task.tick_q(now)
        task.send_key.assert_not_called()
        task.tick_q(5.7)
        task.send_key.assert_called_once()

    def show(self, task, *texts):
        task.has_text.side_effect = lambda area, text: text in texts

    def test_finish_requires_candle_then_later_continue(self):
        task = make_task()
        self.show(task, '继续探索')
        task.tick(1)
        self.assertIsNone(task.round_success)
        self.assertFalse(task.candle_seen)
        self.show(task, '获得烛芯', '继续探索')
        task.tick(2)
        self.assertEqual(task.stage, 'candle')
        self.assertTrue(task.candle_seen)
        self.assertIsNone(task.round_success)
        self.show(task)
        task.tick(3)
        self.assertIsNone(task.round_success)
        self.show(task, '继续探索')
        task.tick(4)
        self.assertTrue(task.round_success)
        task.send_key.assert_called_once_with('esc')
        task._read_hud_number.assert_not_called()

    def test_timeout_exits_only_on_recognized_world_and_never_switches_q(self):
        task = make_task()
        task.battle_since = 0
        self.show(task, '战斗')
        task.tick(60)
        self.assertFalse(task.round_success)
        task._read_hud_number.assert_not_called()
        task.send_key.assert_called_once_with('esc')
        task.tick(60.2)
        self.assertEqual(task.send_key.call_count, 1)
        self.show(task)
        task.in_team.return_value = False
        task.tick(100)
        self.assertEqual(task.send_key.call_count, 1)
        self.show(task, '战斗')
        task.in_team.return_value = True
        task.tick(101)
        self.assertEqual(task.send_key.call_count, 2)
        task._read_hud_number.assert_not_called()

    def test_revive_resets_only_after_hud_returns(self):
        task = make_task()
        task.battle_since = 0
        self.show(task, '复苏', '战斗')
        task.tick(10)
        self.assertEqual(task.stage, 'revive')
        self.assertEqual(task.battle_since, 0)
        task.send_key.assert_called_once_with('x')
        task.tick(10.2)
        self.assertEqual(task.send_key.call_count, 1)
        task._read_hud_number.assert_not_called()
        self.show(task)
        task.in_team.return_value = False
        task.tick(11)
        self.assertTrue(task.revive_pending)
        self.show(task, '战斗')
        task.tick(12)
        self.assertEqual(task.battle_since, 0)
        task.in_team.return_value = True
        task.tick(12.4)
        self.assertEqual(task.battle_since, 12.4)
        self.assertFalse(task.revive_pending)
        task.tick(12.8)
        self.assertEqual(task.send_key.call_args.args, ('q',))

    def test_failed_revive_keeps_original_deadline(self):
        task = make_task()
        task.battle_since = 0
        self.show(task, '复苏')
        task.tick(59)
        task.send_key.reset_mock()
        task.tick(60)
        self.assertFalse(task.round_success)
        task.send_key.assert_called_once_with('x')
        self.show(task, '战斗')
        task.tick(61)
        self.assertEqual(task.battle_since, 0)
        self.assertFalse(task.round_success)
        task._read_hud_number.assert_not_called()
        self.assertEqual(task.send_key.call_args.args, ('esc',))

    def test_starting_from_every_node(self):
        from src.dna_ui.Defs import COORD
        for text, expected, coord, key in (
            ('坠入深渊', 'home', COORD.MIJIN_ENTER, None),
            ('开始探索', 'start', COORD.MIJIN_START, None),
            ('战斗', 'fight', None, None),
            ('复苏', 'revive', None, 'x'),
            ('获得烛芯', 'candle', COORD.MIJIN_BLANK, None),
            ('继续探索', 'continue', None, None),
            ('退出并结算', 'exit', COORD.MIJIN_EXIT, None),
            ('是否结束当前探索并结算', 'confirm', COORD.MIJIN_CONFIRM, None),
            ('探索失败', 'result', COORD.MIJIN_BLANK, None),
        ):
            with self.subTest(text=text):
                task = make_task()
                self.show(task, text)
                task.tick(0)
                self.assertEqual(task.stage, expected)
                if coord is not None:
                    self.assertEqual(task.click_ui_coord.call_args.args, (coord,))
                else:
                    task.click_ui_coord.assert_not_called()
                if key is not None:
                    task.send_key.assert_called_once_with(key)
                else:
                    task.send_key.assert_not_called()
                if expected in ('exit', 'confirm', 'result'):
                    self.assertFalse(task.round_success)

    def test_direct_failure_in_fight_or_revive_counts_once_on_home(self):
        for previous in ('fight', 'revive'):
            with self.subTest(previous=previous):
                task = make_task()
                self.show(task, '战斗' if previous == 'fight' else '复苏')
                task.tick(0)
                task.send_key.reset_mock()
                task._read_hud_number.reset_mock()
                self.show(task, '探索失败', '战斗', '复苏')
                task.tick(1)
                self.assertEqual(task.stage, 'result')
                self.assertFalse(task.round_success)
                task.send_key.assert_not_called()
                task._read_hud_number.assert_not_called()
                task.tick(1.2)
                self.assertEqual(task.failure_count, 0)
                self.show(task, '坠入深渊')
                task.tick(2)
                task.tick(2.2)
                self.assertEqual((task.success_count, task.failure_count), (0, 1))

    def test_statistics_preserve_success_on_failure_titled_result(self):
        task = make_task()
        task.begin_exit(0, True)
        self.show(task, '探索失败')
        task.tick(1)
        self.assertTrue(task.round_success)
        self.show(task, '坠入深渊')
        task.tick(2)
        task.tick(2.2)
        self.assertEqual((task.success_count, task.failure_count), (1, 0))
        task.info_set.assert_any_call('成功次数', 1)
        task.info_set.assert_any_call('失败次数', 0)
        task.reset_statistics()
        self.assertEqual((task.success_count, task.failure_count), (0, 0))

    def test_full_cycle_can_skip_confirm_and_resume_any_visible_node(self):
        task = make_task()
        for now, visible, expected in (
            (0, '开始探索', 'start'),
            (1, '战斗', 'fight'),
            (2, '获得烛芯', 'candle'),
            (3, '继续探索', 'continue'),
            (4, '退出并结算', 'exit'),
            (5, '探索失败', 'result'),
            (6, '坠入深渊', 'home'),
            (7, '开始探索', 'start'),
            (8, '战斗', 'fight'),
        ):
            self.show(task, visible)
            task.tick(now)
            self.assertEqual(task.stage, expected)
        self.assertFalse(task.candle_seen)
        self.assertIsNone(task.round_success)
        self.assertEqual(task.battle_since, 8)
        self.assertEqual((task.success_count, task.failure_count), (1, 0))

    def test_unknown_waits_indefinitely_without_input_and_logs_are_throttled(self):
        task = make_task()
        task.in_team.return_value = False
        task.log_info.reset_mock()
        for now in (0, .2, 1, 5, 30, 300, 3600):
            task.tick(now)
        self.assertEqual(task.stage, 'unknown')
        task.send_key.assert_not_called()
        task.click_ui_coord.assert_not_called()
        task._read_hud_number.assert_not_called()
        self.assertEqual(task.log_info.call_count, 5)

    def test_unknown_preserves_battle_time_and_timeout_exit_intent(self):
        task = make_task()
        self.show(task, '战斗')
        task.tick(0)
        task._read_hud_number.reset_mock()
        self.show(task)
        task.in_team.return_value = False
        task.tick(20)
        self.assertEqual(task.battle_since, 0)
        self.assertIsNone(task.round_success)
        task.tick(100)
        self.assertFalse(task.round_success)
        task.send_key.assert_not_called()
        self.show(task, '是否结束当前探索并结算')
        task.tick(101)
        self.assertEqual(task.stage, 'confirm')
        self.assertFalse(task.round_success)
        task._read_hud_number.assert_not_called()

    def test_repeated_fight_preserves_time_and_q_cycle(self):
        task = make_task()
        self.show(task, '战斗')
        task.tick(0)
        task.tick(.4)
        task._read_hud_number.return_value = 0
        task.tick(.8)
        task.tick(1.2)
        task.tick(5.8)
        self.assertEqual(task.battle_since, 0)
        self.assertEqual(task.q_since, 1.2)
        self.assertEqual(task.send_key.call_count, 1)
        task.tick(6.3)
        self.assertEqual(task.send_key.call_count, 2)

    def test_repeated_nodes_and_unknown_gaps_keep_click_cooldown(self):
        task = make_task()
        self.show(task, '退出并结算')
        task.tick(0)
        task.tick(.2)
        self.show(task)
        task.in_team.return_value = False
        task.tick(.3)
        self.show(task, '是否结束当前探索并结算')
        task.tick(.4)
        self.assertEqual(task.click_ui_coord.call_count, 1)
        task.tick(1)
        self.assertEqual(task.click_ui_coord.call_count, 2)
        self.assertEqual(task.last_action, 1)

    def test_starting_home_and_repeated_home_do_not_count_rounds(self):
        task = make_task()
        self.show(task, '坠入深渊')
        for now in (0, .2, 1, 2):
            task.tick(now)
        self.assertEqual((task.success_count, task.failure_count), (0, 0))
        self.assertEqual(task.click_ui_coord.call_count, 3)

    def test_stalled_known_nodes_retry_without_stopping(self):
        for text in ('退出并结算', '是否结束当前探索并结算', '探索失败', '开始探索'):
            with self.subTest(text=text):
                task = make_task()
                self.show(task, text)
                for now in (0, .2, 1, 31, 3600):
                    task.tick(now)
                self.assertEqual(task.click_ui_coord.call_count, 4)
                task.send_key.assert_not_called()
                self.assertEqual((task.success_count, task.failure_count), (0, 0))

    def test_exit_can_use_confirmed_world_without_objective_label(self):
        task = make_task()
        task.begin_exit(0, False)
        self.show(task)
        task.tick(1)
        self.assertEqual(task.stage, 'world')
        task.send_key.assert_called_once_with('esc')
        task._read_hud_number.assert_not_called()

    def test_exit_intent_never_resumes_q_or_changes_result(self):
        task = make_task()
        task.begin_exit(0, False)
        self.show(task, '获得烛芯')
        task.tick(1)
        self.show(task, '继续探索')
        task.tick(2)
        self.show(task, '战斗')
        task.tick(3)
        self.assertFalse(task.round_success)
        task._read_hud_number.assert_not_called()
        self.assertTrue(all(call.args == ('esc',) for call in task.send_key.call_args_list))

    def test_missing_hud_breaks_q_confirmation(self):
        task = make_task()
        self.show(task, '战斗')
        task.tick(1)
        task.in_team.return_value = False
        task.tick(2)
        task.in_team.return_value = True
        task.tick(3)
        task.send_key.assert_not_called()
        task.tick(3.4)
        task.send_key.assert_called_once()

    def test_invalid_q_cost_never_sends(self):
        task = make_task()
        for cost in (None, -1, 21, 90):
            task.reset_q_cycle()
            task._read_hud_number.return_value = cost
            for now in (0, .4, 1, 10):
                task.tick_q(now)
        task.send_key.assert_not_called()

    def test_interrupted_run_has_no_q_cleanup(self):
        from unittest.mock import patch
        from ok import TaskDisabledException

        task = make_task()
        task.sleep = Mock(side_effect=TaskDisabledException)
        with patch('src.tasks.fullauto.AutoMijinTask.DNAOneTimeTask.run'):
            with self.assertRaises(TaskDisabledException):
                task.run()
        task.send_key.assert_not_called()

    def test_text_merges_split_boxes(self):
        task = make_task()
        del task.has_text
        task.screen_box = Mock(return_value='box')
        task.ocr = Mock(return_value=[SimpleNamespace(name='获得'), SimpleNamespace(name='烛芯！')])
        self.assertTrue(task.has_text('CANDLE', '获得烛芯'))


if __name__ == '__main__':
    unittest.main()
