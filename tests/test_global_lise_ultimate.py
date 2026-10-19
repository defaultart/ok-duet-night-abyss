"""Global Lise Q dispatch and combat task integration."""
import unittest
from unittest.mock import Mock, patch

from src.tasks.config import CommissionSkillConfig
from src.tasks.fullauto.AutoMoonHunterTask import AutoMoonHunterTask
from src.tasks.fullauto.AutoTheatreTask import AutoTheatreTask


class TestGlobalLiseUltimate(unittest.TestCase):
    def make_task(self, cls=AutoMoonHunterTask, skill="自动黎瑟终结技"):
        task = object.__new__(cls)
        task.commission_skill_config = {"技能1": skill}
        task.get_task_by_class = Mock(return_value=None)
        task.create_ticker = lambda action, **kwargs: action
        task.create_ticker_group = lambda actions: lambda: [a() for a in actions]
        task.skill_tick = task.create_skill_ticker()
        task.skills_stopped = False
        task.combat_seen_at = 0
        task.stage_pending = None
        task.in_team_and_world = Mock(return_value=True)
        task._read_hud_number = Mock(side_effect=[9, 150, 9, 150])
        task.key_config = {"Ultimate Key": "q"}
        task.send_key = Mock()
        task.log_onetime_info = Mock()
        task.log_info = Mock()
        task.log_debug = Mock()
        task.sleep = Mock()
        return task

    def test_available_in_all_four_global_slots(self):
        for n in range(1, 5):
            self.assertIn("自动黎瑟终结技",
                          CommissionSkillConfig.config_type[f"技能{n}"]["options"])

    def test_moon_hunter_and_theatre_dispatch_without_trigger_task(self):
        for cls in (AutoMoonHunterTask, AutoTheatreTask):
            with self.subTest(task=cls.__name__):
                task = self.make_task(cls)
                with patch("src.tasks.trigger.AutoLiseUltimateTask.time.monotonic", return_value=1):
                    task.tick_skills(True)
                task.send_key.assert_not_called()
                with patch("src.tasks.trigger.AutoLiseUltimateTask.time.monotonic", return_value=1.4):
                    task.tick_skills(True)
                task.send_key.assert_called_once_with("q", down_time=0.06)

    def test_stopped_skills_do_not_run_lise_detection(self):
        for cls in (AutoMoonHunterTask, AutoTheatreTask):
            with self.subTest(task=cls.__name__):
                task = self.make_task(cls)
                task.stop_skills("走位或结算")
                task.tick_skills(False)
                task._read_hud_number.assert_not_called()
                task.send_key.assert_not_called()

    def test_not_selected_does_not_run_ocr(self):
        task = self.make_task(skill="不使用")
        task.tick_skills(True)
        task._read_hud_number.assert_not_called()

    def test_enabled_realtime_switch_runs_with_all_slots_unused(self):
        task = self.make_task(skill="不使用")
        task.get_task_by_class.return_value = Mock(enabled=True)
        with patch("src.tasks.trigger.AutoLiseUltimateTask.time.monotonic", return_value=1):
            task.tick_skills(True)
        with patch("src.tasks.trigger.AutoLiseUltimateTask.time.monotonic", return_value=1.4):
            task.tick_skills(True)
        task.send_key.assert_called_once_with("q", down_time=0.06)

    def test_disabled_realtime_switch_does_not_run_ocr(self):
        task = self.make_task(skill="不使用")
        task.get_task_by_class.return_value = Mock(enabled=False)
        task.tick_skills(True)
        task._read_hud_number.assert_not_called()

    def test_explicit_slot_and_realtime_switch_do_not_duplicate_detection(self):
        task = self.make_task()
        task.get_task_by_class.return_value = Mock(enabled=True)
        task.tick_skills(True)
        self.assertEqual(task._read_hud_number.call_count, 2)
        task.send_key.assert_not_called()

    def test_realtime_switch_respects_stopped_skills(self):
        task = self.make_task(skill="不使用")
        task.get_task_by_class.return_value = Mock(enabled=True)
        task.stop_skills("走位")
        task.tick_skills(False)
        task._read_hud_number.assert_not_called()

    def test_shared_monitor_uses_current_hud_each_check(self):
        task = self.make_task()
        task.in_team_and_world.return_value = False
        task.tick_skills(True)
        task._read_hud_number.assert_not_called()
        task.send_key.assert_not_called()

    def test_active_q_is_not_toggled_off(self):
        task = self.make_task()
        task._read_hud_number.side_effect = [0]
        task.tick_skills(True)
        task._read_hud_number.assert_called_once_with("LISE_Q_COST_OCR")
        task.send_key.assert_not_called()

    def test_real_tickers_confirm_before_default_slot_interval(self):
        for cls in (AutoMoonHunterTask, AutoTheatreTask):
            with self.subTest(task=cls.__name__):
                task = self.make_task(cls)
                task.commission_skill_config = dict(CommissionSkillConfig.default_config)
                task.commission_skill_config.update({
                    "技能1": "自动黎瑟终结技",
                    "技能2": "自动黎瑟终结技",
                    "技能1_释放后等待": 3.0,
                })
                task.get_task_by_class.return_value = Mock(enabled=True)
                # Exercise the actual ticker implementations, including their clocks.
                del task.create_ticker
                del task.create_ticker_group
                task.skill_tick = task.create_skill_ticker()
                task.skill_tick.reset()
                for timestamp, reads in ((10.0, 2), (10.2, 2), (10.4, 4)):
                    with patch("src.tasks.BaseDNATask.time.perf_counter", return_value=timestamp), \
                         patch("src.tasks.trigger.AutoLiseUltimateTask.time.monotonic", return_value=timestamp):
                        task.tick_skills(True)
                    self.assertEqual(task._read_hud_number.call_count, reads)
                task.send_key.assert_called_once_with("q", down_time=0.06)
                self.assertNotIn(unittest.mock.call(3.0), task.sleep.call_args_list)


if __name__ == "__main__":
    unittest.main()
