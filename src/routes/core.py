"""Framework-independent route data, recording and execution.

All timestamps are monotonic seconds within a segment. Checkpoint time never
advances the following segment's clock. No executable code is loaded from routes.
"""
from __future__ import annotations

import copy
import json
import math
import re
import shutil
import string
import threading
import time
import uuid
from enum import Enum
from pathlib import Path


MOVEMENT_KEYS = frozenset(('w', 'a', 's', 'd', 'up', 'down', 'left', 'right',
                           'space', 'lshift', 'rshift', 'lctrl', 'rctrl', 'lalt', 'ralt'))
RECORDABLE_KEYS = MOVEMENT_KEYS | frozenset(string.ascii_lowercase + string.digits)


def normalize_key(key):
    name = getattr(key, 'char', None) or getattr(key, 'name', None) or str(key)
    vk = getattr(key, 'vk', None)
    if len(name) == 1 and ord(name) < 32 and vk is not None and 65 <= vk <= 90:
        name = chr(vk)
    name = name.lower().removeprefix('key.')
    return {'shift': 'lshift', 'shift_l': 'lshift', 'shift_r': 'rshift',
            'ctrl': 'lctrl', 'ctrl_l': 'lctrl', 'ctrl_r': 'rctrl',
            'alt': 'lalt', 'alt_l': 'lalt', 'alt_r': 'ralt', 'alt_gr': 'ralt'}.get(name, name)


def new_route(name):
    return {'version': 1, 'id': uuid.uuid4().hex, 'name': name.strip(),
            'resolution': [], 'key_config': {}, 'start_image': '',
            'map_rule': None, 'segments': []}


def number(value, minimum=0, maximum=86400):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError('数值无效或超出范围')


def validate_rule(rule):
    if not isinstance(rule, dict) or rule.get('kind') not in ('ocr', 'template'):
        raise ValueError('请配置 OCR 或截图校验规则')
    rect = rule.get('rect', [])
    if len(rect) != 4:
        raise ValueError('请框选检查区域')
    for n in rect:
        number(n, 0, 1)
    if rect[0] >= rect[2] or rect[1] >= rect[3]:
        raise ValueError('检查区域不能为空')
    number(rule.get('timeout', 5), 1, 60)
    if rule['kind'] == 'ocr':
        words = rule.get('keywords', [])
        if not isinstance(words, list) or not words or any(not isinstance(w, str) or not w.strip() for w in words):
            raise ValueError('请填写至少一个必需关键词')
    else:
        if not rule.get('template'):
            raise ValueError('缺少截图模板')
        number(rule.get('threshold', .9), .5, 1)


def validate_route(route, runnable=False, require_map=True):
    if not isinstance(route, dict):
        raise ValueError('路线数据必须为对象')
    if route.get('version') != 1 or not re.fullmatch(r'[a-f0-9]{32}', route.get('id', '')):
        raise ValueError('路线版本或 ID 无效')
    if not isinstance(route.get('name'), str) or not route['name'].strip():
        raise ValueError('路线名称不能为空')
    if route.get('map_rule') is not None or (runnable and require_map):
        validate_rule(route.get('map_rule'))
    if not isinstance(route.get('segments'), list) or not isinstance(route.get('key_config'), dict):
        raise ValueError('路线段或键位数据无效')
    if runnable and (not route['segments'] or len(route.get('resolution', [])) != 2):
        raise ValueError('请先录制路线')
    interaction = route.get('key_config', {}).get('Interact Key', 'f')
    if not isinstance(interaction, str) or not re.fullmatch('[a-z0-9]', interaction):
        raise ValueError('交互键必须为单个字母或数字')
    allowed = RECORDABLE_KEYS
    for segment in route['segments']:
        if not isinstance(segment, dict) or not isinstance(segment.get('events'), list):
            raise ValueError('移动段数据无效')
        number(segment['duration'])
        held, previous = set(), 0
        for event in segment['events']:
            number(event['time'], previous, segment['duration'])
            previous = event['time']
            key = event['key']
            if key not in allowed:
                raise ValueError(f'不支持的录制按键：{key}')
            if event['type'] == 'key_down' and key not in held:
                held.add(key)
            elif event['type'] == 'key_up' and key in held:
                held.remove(key)
            else:
                raise ValueError('按键按下和松开事件不匹配')
        if held or segment.get('invalid'):
            raise ValueError('该段需要重录：存在未松开的按键或失焦记录')
        if runnable and segment.get('checkpoint_pending'):
            raise ValueError('存在尚未配置的检查节点，请先在「编辑所选节点」中设置条件')
        if segment.get('checkpoint'):
            validate_rule(segment['checkpoint'])


class RouteStore:
    def __init__(self, root='mod/keyboard_routes'):
        self.root = Path(root)

    def folder(self, route_id):
        if not re.fullmatch(r'[a-f0-9]{32}', route_id):
            raise ValueError('路线 ID 无效')
        folder = self.root / route_id
        if not folder.resolve().is_relative_to(self.root.resolve()):
            raise ValueError('路线目录超出存储目录')
        return folder

    def asset(self, route_id, name):
        base = self.folder(route_id).resolve()
        target = (base / name).resolve()
        if not target.is_relative_to(base) or target == base:
            raise ValueError('路线资源路径无效')
        return target

    def save(self, route):
        validate_route(route)
        folder = self.folder(route['id'])
        folder.mkdir(parents=True, exist_ok=True)
        temporary = folder / 'route.tmp'
        temporary.write_text(json.dumps(route, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(folder / 'route.json')

    def load_all(self):
        routes, errors = [], []
        for path in sorted(self.root.glob('*/route.json')):
            try:
                route = json.loads(path.read_text(encoding='utf-8'))
                validate_route(route)
                if route['id'] != path.parent.name:
                    raise ValueError('目录与路线 ID 不匹配')
                self.folder(route['id'])
                routes.append(route)
            except (ValueError, KeyError, TypeError, OSError) as error:
                errors.append(f'{path}: {error}')
        return routes, errors

    def delete(self, route_id):
        shutil.rmtree(self.folder(route_id).resolve())


class Recorder:
    def __init__(self, allowed, clock=time.perf_counter):
        self.allowed = set(allowed)
        self.clock = clock
        self.events = []
        self.held = set()
        self.elapsed = 0.0
        self.started = None
        self.invalid = False

    def resume(self, now=None):
        if self.invalid:
            raise ValueError('当前段失焦时有按键未松开，请重录当前段')
        if self.started is None:
            self.started = self.clock() if now is None else now

    def feed(self, key, down, now=None):
        if self.started is None or key not in self.allowed:
            return
        now = self.clock() if now is None else now
        if down and key not in self.held:
            self.held.add(key)
        elif not down and key in self.held:
            self.held.remove(key)
        else:
            return
        self.events.append({'time': self.elapsed + max(0, now - self.started),
                            'type': 'key_down' if down else 'key_up', 'key': key})

    def pause(self, now=None, lost_focus=False):
        if self.held and not lost_focus:
            raise ValueError('请先松开所有录制按键')
        if self.started is not None:
            now = self.clock() if now is None else now
            self.elapsed += max(0, now - self.started)
            self.started = None
        if lost_focus and self.held:
            self.invalid = True
            self.held.clear()

    def segment(self, now=None):
        self.pause(now)
        if self.invalid:
            raise ValueError('当前段需要重录')
        return {'duration': self.elapsed, 'events': copy.deepcopy(self.events), 'checkpoint': None}

    def split(self, now=None):
        """Mark a checkpoint without stopping the clock or requiring key release.

        Each saved segment must have balanced keys for independent playback.
        Boundary releases/presses represent held keys across the checkpoint;
        they do not inject input or alter the physical recording state.
        """
        if self.started is None or self.invalid:
            raise ValueError('请先开始或继续录制，再插入检查节点')
        now = self.clock() if now is None else now
        duration = self.elapsed + max(0, now - self.started)
        events = copy.deepcopy(self.events)
        events.extend({'time': duration, 'type': 'key_up', 'key': key} for key in sorted(self.held))
        self.events = [{'time': 0.0, 'type': 'key_down', 'key': key} for key in sorted(self.held)]
        self.elapsed = 0.0
        self.started = now
        return {'duration': duration, 'events': events, 'checkpoint': None}


class Result(str, Enum):
    SUCCESS = 'success'
    MAP_FAILED = 'map_failed'
    NODE_FAILED = 'node_failed'
    STOPPED = 'stopped'
    ERROR = 'error'


class Interrupted(Exception):
    pass


class Player:
    """Adapter supplies frame/OCR matching, cancellation, and key delivery."""
    def __init__(self, adapter, clock=time.perf_counter):
        self.io = adapter
        self.clock = clock
        self.held = set()
        self.lock = threading.RLock()
        self.last_observation = []

    def release(self):
        with self.lock:
            errors = []
            for key in tuple(self.held):
                try:
                    self.io.key_up(key)
                    self.held.remove(key)
                except Exception as error:
                    errors.append(error)
            if errors:
                raise errors[0]

    def wait(self, duration):
        deadline = self.clock() + duration
        while True:
            self.io.check()
            left = deadline - self.clock()
            if left <= 0:
                return
            self.io.sleep(min(left, .02))

    def stable(self, rules, timeout):
        deadline, previous, hits = self.clock() + timeout, None, 0
        while self.clock() < deadline:
            self.io.check()
            frame = self.io.frame()
            matches = []
            self.last_observation = []
            for ident, rule in rules:
                matched, detail = self.io.match(frame, ident, rule)
                self.last_observation.append({'id': ident, 'matched': matched, 'detail': detail})
                if matched:
                    matches.append(ident)
            if self.clock() >= deadline:
                return None
            current = matches[0] if len(matches) == 1 else None
            hits = hits + 1 if current is not None and current == previous else (1 if current is not None else 0)
            previous = current
            if hits >= 2:
                self.io.check()
                return current
            self.wait(.5)
        return None

    def play(self, route):
        try:
            validate_route(route, runnable=True, require_map=False)
            self.io.check()
            self.io.verify_environment(route)
            for index, segment in enumerate(route['segments']):
                self.io.status(f'移动段 {index + 1}/{len(route["segments"])}')
                start = self.clock()
                for event in segment['events']:
                    self.wait(max(0, start + event['time'] - self.clock()))
                    with self.lock:
                        self.io.check()
                        if event['type'] == 'key_down':
                            self.held.add(event['key'])
                            self.io.key_down(event['key'])
                        else:
                            self.io.key_up(event['key'])
                            self.held.discard(event['key'])
                self.wait(max(0, start + segment['duration'] - self.clock()))
                self.release()
                if rule := segment.get('checkpoint'):
                    self.io.status(f'检查节点 {index + 1}')
                    if self.stable([(route['id'], rule)], rule.get('timeout', 5)) is None:
                        self.io.failure(f'节点 {index + 1} 校验失败', self.last_observation)
                        return Result.NODE_FAILED
            return Result.SUCCESS
        except Interrupted:
            return Result.STOPPED
        finally:
            self.release()
