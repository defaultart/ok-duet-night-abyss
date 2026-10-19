"""Deterministic route tests: no game, hooks or timing-sensitive sleeps."""
import copy
import tempfile
import unittest
from types import SimpleNamespace

from src.routes.core import (Interrupted, MOVEMENT_KEYS, RECORDABLE_KEYS, Player, Recorder, Result,
                             RouteStore, new_route, normalize_key, validate_route)


def rule():
    return {'kind': 'ocr', 'rect': [.1, .1, .5, .5], 'keywords': ['目标'], 'timeout': 5}


def route():
    value = new_route('测试路线')
    value.update(resolution=[1600, 900], key_config={'Interact Key': 'f'}, map_rule=rule())
    value['segments'] = [{'duration': 1, 'events': [
        {'time': 0, 'type': 'key_down', 'key': 'w'},
        {'time': 1, 'type': 'key_up', 'key': 'w'}], 'checkpoint': rule()}]
    return value


class Clock:
    def __init__(self):
        self.now = 0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class IO:
    def __init__(self):
        self.clock = Clock()
        self.keys = []
        self.matches = iter([True] * 100)
        self.failures = []
        self.cancel_at = float('inf')
        self.frame_count = 0
        self.environment_ok = True
        self.frame_marker = 0

    def check(self):
        if self.clock() >= self.cancel_at:
            raise Interrupted('失焦或停止')

    def sleep(self, seconds):
        self.clock.sleep(seconds)

    def key_down(self, key):
        self.keys.append(('down', key, self.clock()))

    def key_up(self, key):
        self.keys.append(('up', key, self.clock()))

    def frame(self):
        self.frame_count += 1
        return self.frame_count

    def match(self, frame, ident, condition):
        return next(self.matches), '目标'

    def verify_environment(self, value):
        if not self.environment_ok:
            raise ValueError('环境不一致')

    def status(self, value):
        pass

    def failure(self, reason, observations):
        self.failures.append((reason, copy.deepcopy(observations)))


class RecorderTests(unittest.TestCase):
    def test_combinations_repeat_and_long_hold(self):
        recorder = Recorder(MOVEMENT_KEYS)
        recorder.resume(10)
        recorder.feed('w', True, 11)
        recorder.feed('w', True, 12)
        recorder.feed('lshift', True, 12)
        recorder.feed('w', False, 15)
        recorder.feed('lshift', False, 16)
        events = recorder.segment(17)['events']
        self.assertEqual([e['time'] for e in events], [1, 2, 5, 6])

    def test_pause_excludes_editor_time_and_keeps_idle(self):
        recorder = Recorder(MOVEMENT_KEYS)
        recorder.resume(10)
        recorder.feed('w', True, 11)
        recorder.feed('w', False, 12)
        recorder.pause(13)
        recorder.resume(100)
        recorder.feed('a', True, 101)
        recorder.feed('a', False, 102)
        segment = recorder.segment(104)
        self.assertEqual(segment['duration'], 7)
        self.assertEqual([e['time'] for e in segment['events']], [1, 2, 4, 5])

    def test_cannot_pause_or_finish_with_held_keys(self):
        recorder = Recorder(RECORDABLE_KEYS)
        recorder.resume(1)
        recorder.feed('q', True, 2)
        with self.assertRaises(ValueError):
            recorder.pause(3)
        with self.assertRaises(ValueError):
            recorder.segment(3)
        recorder.feed('q', False, 4)
        self.assertEqual(len(recorder.segment(5)['events']), 2)

    def test_focus_loss_invalidates_held_segment(self):
        recorder = Recorder(RECORDABLE_KEYS)
        recorder.resume(1)
        recorder.feed('q', True, 2)
        recorder.pause(3, lost_focus=True)
        self.assertTrue(recorder.invalid)
        with self.assertRaises(ValueError):
            recorder.resume(4)

    def test_focus_loss_without_held_keys_can_resume(self):
        recorder = Recorder(MOVEMENT_KEYS)
        recorder.resume(1)
        recorder.pause(3, lost_focus=True)
        recorder.resume(20)
        self.assertEqual(recorder.segment(21)['duration'], 3)

    def test_control_keys_and_esc_are_not_recorded(self):
        recorder = Recorder(RECORDABLE_KEYS)
        recorder.resume(0)
        for key in (*(f'f{n}' for n in range(1, 13)), 'esc', 'mouse_left', '.', 'num0'):
            recorder.feed(key, True, 1)
            recorder.feed(key, False, 2)
        self.assertEqual(recorder.events, [])
        self.assertEqual(normalize_key('Key.shift_r'), 'rshift')
        self.assertEqual(normalize_key(SimpleNamespace(char='\x17', vk=87)), 'w')
        self.assertEqual(normalize_key('Q'), 'q')

    def test_q_combination_repeat_and_split(self):
        recorder = Recorder(RECORDABLE_KEYS)
        recorder.resume(0)
        recorder.feed('w', True, 1)
        recorder.feed('q', True, 2)
        recorder.feed('q', True, 3)
        first = recorder.split(4)
        self.assertEqual(first['events'], [
            {'key': 'w', 'type': 'key_down', 'time': 1},
            {'key': 'q', 'type': 'key_down', 'time': 2},
            {'key': 'q', 'type': 'key_up', 'time': 4},
            {'key': 'w', 'type': 'key_up', 'time': 4}])
        self.assertEqual(recorder.held, {'w', 'q'})
        recorder.feed('q', False, 5)
        recorder.feed('w', False, 6)
        last = recorder.segment(7)
        value = route()
        value['segments'] = [first, last]
        validate_route(value, runnable=True)
        self.assertEqual(last['events'], [
            {'key': 'q', 'type': 'key_down', 'time': 0},
            {'key': 'w', 'type': 'key_down', 'time': 0},
            {'key': 'q', 'type': 'key_up', 'time': 1},
            {'key': 'w', 'type': 'key_up', 'time': 2}])


    def test_checkpoint_split_preserves_keys_and_continuous_time(self):
        recorder = Recorder(MOVEMENT_KEYS)
        recorder.resume(10)
        recorder.feed('w', True, 11)
        first = recorder.split(12)
        self.assertEqual(first['events'][-1], {'key': 'w', 'type': 'key_up', 'time': 2})
        self.assertEqual(recorder.held, {'w'})
        self.assertEqual(recorder.started, 12)
        recorder.feed('w', False, 15)
        last = recorder.segment(16)
        self.assertEqual(last['events'], [{'key': 'w', 'type': 'key_down', 'time': 0},
                                         {'key': 'w', 'type': 'key_up', 'time': 3}])
        self.assertEqual(first['duration'] + last['duration'], 6)


class PlayerTests(unittest.TestCase):
    def player(self):
        io = IO()
        return io, Player(io, io.clock)

    def test_checkpoint_delay_does_not_shift_next_segment(self):
        io, player = self.player()
        value = route()
        value['segments'].append(copy.deepcopy(value['segments'][0]))
        self.assertEqual(player.play(value), Result.SUCCESS)
        self.assertAlmostEqual(io.keys[2][2] - io.keys[1][2], .5)
        self.assertAlmostEqual(io.keys[3][2] - io.keys[2][2], 1)
        self.assertFalse(player.held)

    def test_requires_two_consecutive_fresh_frames(self):
        io, player = self.player()
        io.matches = iter([True, False, True, True])
        self.assertEqual(player.stable([('map', rule())], 5), 'map')
        self.assertEqual(io.frame_count, 4)
        self.assertAlmostEqual(io.clock(), 1.5)

    def test_ambiguous_maps_never_choose_first(self):
        io, player = self.player()
        self.assertIsNone(player.stable([('a', rule()), ('b', rule())], 1))

    def test_map_candidate_change_resets_hits(self):
        io, player = self.player()
        io.matches = iter([True, False, False, True, False, True])
        self.assertEqual(player.stable([('a', rule()), ('b', rule())], 5), 'b')
        self.assertEqual(io.frame_count, 3)

    def test_timeout_records_failure_and_stops_next_segment(self):
        io, player = self.player()
        io.matches = iter([False] * 100)
        value = route()
        value['segments'].append(copy.deepcopy(value['segments'][0]))
        self.assertEqual(player.play(value), Result.NODE_FAILED)
        self.assertEqual(len(io.keys), 2)
        self.assertEqual(len(io.failures), 1)
        self.assertFalse(player.held)

    def test_focus_loss_releases_held_keys(self):
        io, player = self.player()
        io.cancel_at = .25
        self.assertEqual(player.play(route()), Result.STOPPED)
        self.assertEqual([e[:2] for e in io.keys], [('down', 'w'), ('up', 'w')])
        self.assertFalse(player.held)

    def test_interruption_releases_q_and_movement(self):
        io, player = self.player()
        value = route()
        value['segments'][0]['events'] = [
            {'key': key, 'type': kind, 'time': when}
            for kind, when in (('key_down', 0), ('key_up', 1)) for key in ('w', 'q')]
        io.cancel_at = .25
        self.assertEqual(player.play(value), Result.STOPPED)
        self.assertEqual([e[:2] for e in io.keys[:2]], [('down', 'w'), ('down', 'q')])
        self.assertCountEqual([e[:2] for e in io.keys[2:]], [('up', 'w'), ('up', 'q')])
        self.assertFalse(player.held)

    def test_input_exception_still_releases_key(self):
        io, player = self.player()
        def failed_down(key):
            raise RuntimeError('输入失败')
        io.key_down = failed_down
        with self.assertRaises(RuntimeError):
            player.play(route())
        self.assertEqual(io.keys[0][:2], ('up', 'w'))

    def test_environment_mismatch_sends_no_keys(self):
        io, player = self.player()
        io.environment_ok = False
        with self.assertRaises(ValueError):
            player.play(route())
        self.assertEqual(io.keys, [])

    def test_trial_does_not_require_map_identification(self):
        io, player = self.player()
        value = route()
        value['map_rule'] = None
        self.assertEqual(player.play(value), Result.SUCCESS)


class StorageTests(unittest.TestCase):
    def test_all_letters_and_digits_record_store_and_play(self):
        for key in 'abcdefghijklmnopqrstuvwxyz0123456789':
            with self.subTest(key=key), tempfile.TemporaryDirectory() as root:
                recorder = Recorder(RECORDABLE_KEYS)
                recorder.resume(0)
                recorder.feed(normalize_key(key.upper()), True, .1)
                recorder.feed(key, False, .2)
                value = route()
                value['segments'] = [recorder.segment(.3)]
                self.assertEqual(value['segments'][0]['events'], [
                    {'key': key, 'type': 'key_down', 'time': .1},
                    {'key': key, 'type': 'key_up', 'time': .2}])
                store = RouteStore(root)
                store.save(value)
                saved, errors = store.load_all()
                self.assertEqual((saved, errors), ([value], []))
                io = IO()
                self.assertEqual(Player(io, io.clock).play(saved[0]), Result.SUCCESS)
                self.assertEqual([event[:2] for event in io.keys], [('down', key), ('up', key)])
                self.assertAlmostEqual(io.keys[0][2], .1)
                self.assertAlmostEqual(io.keys[1][2], .2)

    def test_unsupported_keys_rejected(self):
        for key in ('f7', 'f8', 'f9', 'f10', 'f4', 'esc', 'mouse_left', '.', 'num0'):
            with self.subTest(key=key), self.assertRaises(ValueError):
                value = route()
                for event in value['segments'][0]['events']:
                    event['key'] = key
                validate_route(value, runnable=True)

    def test_pending_checkpoint_can_be_saved_but_cannot_be_played(self):
        value = route()
        value['segments'][0].update(checkpoint=None, checkpoint_pending=True, image='node.png')
        validate_route(value)
        with self.assertRaisesRegex(ValueError, '尚未配置'):
            validate_route(value, runnable=True)
        value['segments'][0]['checkpoint'] = rule()
        value['segments'][0].pop('checkpoint_pending')
        validate_route(value, runnable=True)

    def test_round_trip_and_delete(self):
        with tempfile.TemporaryDirectory() as root:
            store, value = RouteStore(root), route()
            store.save(value)
            self.assertEqual(store.load_all(), ([value], []))
            store.delete(value['id'])
            self.assertEqual(store.load_all(), ([], []))

    def test_bad_files_reported_and_paths_contained(self):
        with tempfile.TemporaryDirectory() as root:
            store, value = RouteStore(root), route()
            store.save(value)
            for path in ('../outside.png', 'C:/outside.png'):
                with self.assertRaises(ValueError):
                    store.asset(value['id'], path)
            with self.assertRaises(ValueError):
                store.folder('../')
            (store.folder(value['id']) / 'route.json').write_text('{broken')
            routes, errors = store.load_all()
            self.assertEqual(routes, [])
            self.assertEqual(len(errors), 1)

    def test_unbalanced_esc_and_invalid_timing_rejected(self):
        for change in ('unbalanced', 'esc', 'timing', 'invalid', 'version'):
            value = route()
            if change == 'unbalanced':
                value['segments'][0]['events'].pop()
            elif change == 'esc':
                value['segments'][0]['events'][0]['key'] = 'esc'
            elif change == 'timing':
                value['segments'][0]['events'][1]['time'] = float('nan')
            elif change == 'invalid':
                value['segments'][0]['invalid'] = True
            else:
                value['version'] = 99
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_route(value, runnable=True)


if __name__ == '__main__':
    unittest.main()
