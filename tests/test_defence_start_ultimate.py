"""Combat activates the shared Lise Q monitor, never an unconditional Q press."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.tasks.AutoDefence import AutoDefence
from src.tasks.CommissionsTask import _default_movement


class TestDefenceStartUltimate(unittest.TestCase):
    def make_task(self, mode="开局重置角色位置", key="q"):
        task = object.__new__(AutoDefence)
        task.external_movement = _default_movement
        task._external_config = None
        task.config = {"挂机模式": mode, "战斗侦测": True, "开局向前走": 1}
        task.commission_skill_config = {f"技能{n}": "不使用" for n in range(1, 5)}
        task.key_config = {"Ultimate Key": key}
        task._mission_started = True
        task._battle_lise_active = False
        task.current_wave = -1
        task.runtime_state = {"wave": -1, "wave_start_time": 0, "wait_next_wave": False}
        task.in_team = Mock(return_value=True)
        task.in_team_and_world = task.in_team
        task.get_wave_info = Mock()
        task.reset_wave_info = Mock(side_effect=lambda: setattr(task, "current_wave", -1))
        task.quick_assist_task = Mock()
        task.skill_tick = Mock()
        task.get_task_by_class = Mock(return_value=None)
        task._read_hud_number = Mock(side_effect=[9, 150, 9, 150])
        for name in ("send_key", "send_key_down", "send_key_up", "next_frame", "info_set",
                     "log_info", "log_info_notify", "soundBeep", "sleep", "give_up_mission"):
            setattr(task, name, Mock())
        task.reset_detection()
        return task

    def check_at(self, task, timestamp):
        with patch("src.tasks.trigger.AutoLiseUltimateTask.time.monotonic", return_value=timestamp):
            task.handle_in_mission()

    def test_all_afk_modes_enable_lise_with_all_skill_slots_unused(self):
        for mode in ("开局重置角色位置", "开局向前走", "自动前进到开战"):
            with self.subTest(mode=mode):
                task = self.make_task(mode)
                self.check_at(task, 1)
                task._read_hud_number.assert_not_called()
                task.current_wave = 0
                self.check_at(task, 1.1)
                self.assertTrue(task._battle_lise_active)
                task.send_key.assert_not_called()  # First confirmation only.
                self.check_at(task, 1.5)
                task.send_key.assert_called_once_with("q", down_time=0.06)
                self.assertEqual(task._read_hud_number.call_count, 4)
                task.get_task_by_class.assert_not_called()  # No global switch required.

    def test_advance_enables_monitor_before_extra_movement_finishes(self):
        task = self.make_task("自动前进到开战")
        task.current_wave = 0
        events = []
        task.next_frame.side_effect = lambda: events.append(task._battle_lise_active)
        now = [0]

        def clock():
            now[0] += 0.5
            return now[0]

        with patch("src.tasks.CommissionsTask.time", SimpleNamespace(time=clock)):
            self.assertTrue(task.advance_until_combat())
        self.assertFalse(events[0])
        self.assertTrue(all(events[1:]))
        task._read_hud_number.assert_any_call("LISE_Q_COST_OCR")
        task.send_key.assert_not_called()

    def test_already_active_q_is_never_toggled_off_on_entry(self):
        task = self.make_task()
        task.current_wave = 0
        task._read_hud_number.side_effect = [0, 0]
        self.check_at(task, 1)
        self.check_at(task, 3.1)
        task.send_key.assert_not_called()
        self.assertTrue(task._battle_lise_active)

    def test_low_energy_does_not_press_and_energy_recovery_is_monitored(self):
        task = self.make_task()
        task.current_wave = 0
        task._read_hud_number.side_effect = [9, 100, 9, 90, 9, 150, 9, 150]
        for timestamp in (1, 1.4):
            self.check_at(task, timestamp)
            task.send_key.assert_not_called()
        for timestamp in (1.8, 2.2):
            self.check_at(task, timestamp)
        task.send_key.assert_called_once_with("q", down_time=0.06)

    def test_monitor_keeps_running_and_can_reenable_q_later_in_same_round(self):
        task = self.make_task()
        task.current_wave = 0
        task._read_hud_number.side_effect = [9, 150, 9, 150, 0, 9, 150, 9, 150]
        for timestamp in (1, 1.4, 2.1, 4.2, 4.6):
            self.check_at(task, timestamp)
        self.assertEqual(task.send_key.call_count, 2)
        task.send_key.assert_called_with("q", down_time=0.06)

    def test_wave_changes_and_short_ocr_gap_do_not_reset_confirmations(self):
        task = self.make_task()
        task.current_wave = 1
        self.check_at(task, 1)
        task.current_wave = -1
        self.check_at(task, 1.4)
        task.send_key.assert_called_once_with("q", down_time=0.06)
        task.current_wave = 2
        task._read_hud_number.side_effect = [0]
        self.check_at(task, 2.1)
        self.assertTrue(task._battle_lise_active)
        task.send_key.assert_called_once()

    def test_continue_and_new_mission_disable_monitor_until_next_combat(self):
        task = self.make_task()
        task.current_wave = 0
        self.check_at(task, 1)
        task.init_for_next_round()
        self.assertFalse(task._battle_lise_active)
        self.assertEqual(task._confirmations, 0)
        self.check_at(task, 1.4)
        self.assertEqual(task._read_hud_number.call_count, 2)
        task.current_wave = 0
        self.check_at(task, 2)
        task.send_key.assert_not_called()
        task.init_all()
        self.assertFalse(task._battle_lise_active)
        self.assertFalse(task._mission_started)

    def test_rejected_entry_and_menus_do_not_run_monitor(self):
        task = self.make_task()
        task._mission_started = False
        task.current_wave = 0
        task.wait_until = Mock(return_value=False)
        self.assertFalse(task.move_on_begin())
        task._update_battle_lise_ultimate()
        task._mission_started = True
        task.in_team.return_value = False
        task._update_battle_lise_ultimate()
        task.tick_enabled_lise_ultimate()
        task._read_hud_number.assert_not_called()
        task.send_key.assert_not_called()

    def test_shared_skill_ticker_does_not_duplicate_hud_reads(self):
        task = self.make_task()
        task.current_wave = 0
        with patch("src.tasks.trigger.AutoLiseUltimateTask.time.monotonic", return_value=1):
            task.handle_in_mission()
            task.tick_enabled_lise_ultimate()
            task.tick_enabled_lise_ultimate()
        self.assertEqual(task._read_hud_number.call_count, 2)
        self.check_at(task, 1.4)
        task.send_key.assert_called_once()

    def test_external_route_keeps_existing_skill_configuration(self):
        task = self.make_task()
        task.external_movement = Mock()
        task.current_wave = 0
        self.assertTrue(task.is_in_combat())
        task.handle_in_mission()
        task.tick_enabled_lise_ultimate()
        task._read_hud_number.assert_not_called()
        self.assertFalse(task._battle_lise_active)
        task.commission_skill_config = {"技能1": "自动黎瑟终结技"}
        with patch("src.tasks.trigger.AutoLiseUltimateTask.time.monotonic", return_value=1):
            task.tick_enabled_lise_ultimate()
        with patch("src.tasks.trigger.AutoLiseUltimateTask.time.monotonic", return_value=1.4):
            task.tick_enabled_lise_ultimate()
        task.send_key.assert_called_once_with("q", down_time=0.06)

    def test_lise_uses_configured_hotkey(self):
        task = self.make_task(key="r")
        task.current_wave = 0
        self.check_at(task, 1)
        self.check_at(task, 1.4)
        task.send_key.assert_called_once_with("r", down_time=0.06)
