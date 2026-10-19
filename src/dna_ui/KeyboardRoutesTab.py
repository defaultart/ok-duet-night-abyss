"""Route editor. Screenshot selection uses game-client coordinates."""
import copy
import json
import re
import time
import uuid

import cv2
from PySide6.QtCore import QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QFont, QGuiApplication, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QInputDialog, QLabel,
                               QListWidgetItem, QMessageBox, QVBoxLayout, QWidget)
from qfluentwidgets import (BodyLabel, CaptionLabel, ComboBox, DoubleSpinBox, FluentIcon,
                            LineEdit, ListWidget, PrimaryPushButton, PushButton, SpinBox,
                            StrongBodyLabel, SubtitleLabel, TextEdit)
from ok import og
from ok.ui.qt.widget.CustomTab import CustomTab

from src.routes.core import RouteStore, new_route, normalize_key, validate_route, validate_rule
from src.routes.session import RouteSession
from src.tasks.KeyboardRouteTask import (KeyboardRouteTask, arm_route_task, crop, read_image,
                                         recording_feedback, startup_expired, write_image)

PHASES = {
    'idle': ('未开始', '#5c5c5c'),
    'listen': ('监听中', '#0f6cbd'),
    'record': ('录制中', '#c42b1c'),
    'pause': ('已暂停', '#8a4b08'),
    'fail': ('失败', '#c42b1c'),
    'ok': ('已完成', '#0f7b0f'),
}


class RegionImage(QLabel):
    def __init__(self, frame, rect=None):
        super().__init__()
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w = rgb.shape[:2]
        self.setPixmap(QPixmap.fromImage(QImage(rgb.data, w, h, rgb.strides[0], QImage.Format_RGB888).copy()).scaled(
            880, 480, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self.setFixedSize(self.pixmap().size())
        self.selection = QRect()
        self.origin = QPoint()
        if rect:
            x, y = round(rect[0]*self.width()), round(rect[1]*self.height())
            self.selection = QRect(x, y, round(rect[2]*self.width())-x, round(rect[3]*self.height())-y)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.origin = event.position().toPoint()
            self.selection = QRect(self.origin, self.origin)
            self.update()

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton:
            self.selection = QRect(self.origin, event.position().toPoint()).normalized().intersected(self.rect())
            self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setPen(QPen(Qt.red, 2))
        painter.drawRect(self.selection)

    def region(self):
        r = self.selection
        if r.width() < 3 or r.height() < 3:
            raise ValueError('请在截图上拖动框选检查区域')
        return [max(0, r.left()/self.width()), max(0, r.top()/self.height()),
                min(1, (r.right()+1)/self.width()), min(1, (r.bottom()+1)/self.height())]


class RecordingHud(QWidget):
    """Small topmost status that stays visible while the game covers the editor."""

    def __init__(self):
        flags = Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus
        super().__init__(None, flags)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setObjectName('routeHud')
        self.dot = QLabel('●')
        self.label = QLabel('录制')
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 8, 16, 8)
        layout.setSpacing(8)
        layout.addWidget(self.dot)
        layout.addWidget(self.label)
        self.setStyleSheet(
            '#routeHud { background: rgba(22, 22, 22, 230); border-radius: 12px; }'
            'QLabel { color: white; font-size: 13px; }')
        self._click_through = False

    def present(self, phase, text):
        colors = {'record': '#ff5c5c', 'pause': '#ffb020', 'listen': '#4da3ff',
                  'fail': '#ff5c5c', 'ok': '#3dd68c'}
        self.dot.setStyleSheet(f'color: {colors.get(phase, "#ffffff")}; font-size: 16px; background: transparent;')
        self.label.setText(text)
        self.label.setStyleSheet('color: white; font-size: 13px; background: transparent;')
        self.adjustSize()
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            self.move(max(8, area.right() - self.width() - 28), area.top() + 28)
        self.show()
        self.raise_()
        if not self._click_through:
            self._make_click_through()

    def _make_click_through(self):
        try:
            import win32con
            import win32gui
            hwnd = int(self.winId())
            style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
            win32gui.SetWindowLong(
                hwnd, win32con.GWL_EXSTYLE,
                style | win32con.WS_EX_TRANSPARENT | win32con.WS_EX_NOACTIVATE)
            self._click_through = True
        except Exception:
            self._click_through = False

    def dismiss(self):
        self.hide()


class RuleDialog(QDialog):
    def __init__(self, frame, rule=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle('框选区域并配置校验条件')
        self.rule = None
        rule = rule or {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)
        hint = BodyLabel('拖动框选游戏截图区域。OCR 关键词用分号分隔，必须全部出现；截图按所选区域比较。')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.image = RegionImage(frame, rule.get('rect'))
        layout.addWidget(self.image)
        self.kind = ComboBox()
        self.kind.addItems(['OCR 文字', '截图模板'])
        self.kind.setCurrentIndex(rule.get('kind') == 'template')
        self.words = LineEdit()
        self.words.setText('；'.join(rule.get('keywords', [])))
        self.words.setPlaceholderText('多个必需关键词用分号分隔，例如：启动；机关')
        self.timeout = SpinBox()
        self.timeout.setRange(1, 60)
        self.timeout.setValue(int(rule.get('timeout', 5)))
        self.threshold = DoubleSpinBox()
        self.threshold.setRange(.5, 1)
        self.threshold.setSingleStep(.01)
        self.threshold.setValue(rule.get('threshold', .9))
        for label, widget in (('校验类型', self.kind), ('必需关键词', self.words),
                              ('最长等待（秒）', self.timeout), ('模板相似度', self.threshold)):
            row = QHBoxLayout()
            caption = BodyLabel(label)
            caption.setMinimumWidth(120)
            row.addWidget(caption)
            row.addWidget(widget, 1)
            layout.addLayout(row)
        self.error = CaptionLabel('')
        self.error.setStyleSheet('color: #c42b1c;')
        layout.addWidget(self.error)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = PushButton('取消')
        ok = PrimaryPushButton('保存')
        cancel.clicked.connect(self.reject)
        ok.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(ok)
        layout.addLayout(buttons)

    def accept(self):
        try:
            self.rule = {'kind': 'ocr' if self.kind.currentIndex() == 0 else 'template',
                         'rect': self.image.region(), 'timeout': self.timeout.value(),
                         'threshold': self.threshold.value(), 'template': 'pending.png',
                         'keywords': [word.strip() for word in re.split('[;；\n]', self.words.text()) if word.strip()]}
            validate_rule(self.rule)
        except ValueError as error:
            self.error.setText(str(error))
            return
        super().accept()


class KeyboardRoutesTab(CustomTab):
    def __init__(self):
        super().__init__()
        self.store = RouteStore()
        self.session = None
        self._recording_hotkeys_armed = False
        self._phase = 'idle'
        self.routes = []
        self.edit_buttons = []
        self.hud = RecordingHud()

        header = QWidget()
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(4)
        header_layout.addWidget(SubtitleLabel('键盘路线'))
        intro = CaptionLabel('固定起点和视角，分段移动，再到节点检查。勾选的路线参与自动识图；自动重开只用于避险副本。')
        intro.setWordWrap(True)
        header_layout.addWidget(intro)
        self.add_widget(header)

        route_body = self._card_body('路线')
        self.routes_list = ListWidget()
        self.routes_list.setMinimumHeight(112)
        self.routes_list.currentRowChanged.connect(self.show_segments)
        route_body.addWidget(self.routes_list)
        self.row([('新建', self.create_route), ('重命名', self.rename_route), ('删除', self.delete_route),
                  ('配置地图识别', lambda: self.launch('snapshot'))], parent=route_body)

        segment_body = self._card_body('移动段')
        self.segments = ListWidget()
        self.segments.setMinimumHeight(96)
        segment_body.addWidget(self.segments)
        self.row([('查看起点截图', self.show_start), ('编辑所选节点', self.edit_node),
                  ('删除所选段', self.delete_segment), ('重录所选段', lambda: self.launch('record', True))],
                 parent=segment_body)

        control_body = self._card_body('录制与运行')
        status_row = QHBoxLayout()
        self.phase_chip = StrongBodyLabel(PHASES['idle'][0])
        self.phase_chip.setAlignment(Qt.AlignCenter)
        self.phase_chip.setMinimumWidth(72)
        self.set_phase('idle')
        self.status = BodyLabel('新建或选择路线，回到游戏按 F8 开始录制。默认 F7 插节点，F10 保存。')
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        status_row.addWidget(self.phase_chip, 0, Qt.AlignTop)
        status_row.addWidget(self.status, 1)
        control_body.addLayout(status_row)
        self.last_key = CaptionLabel('最近按键：尚无')
        self.last_key.setWordWrap(True)
        control_body.addWidget(self.last_key)

        self.hotkeys = []
        for label, key in (('开始 / 继续 / 暂停', 'f8'), ('插入检查节点', 'f7'), ('结束并保存', 'f10')):
            widget = LineEdit()
            widget.setText(key)
            widget.setMaximumWidth(120)
            self.hotkeys.append(widget)
            control_body.addLayout(self._labeled_row(label, widget))
        settings_path = self.store.root / 'settings.json'
        try:
            settings = json.loads(settings_path.read_text(encoding='utf-8'))
            for widget, key in zip(self.hotkeys, settings['hotkeys']):
                widget.setText(key)
        except (OSError, ValueError, KeyError, TypeError):
            pass
        self.max_failures = SpinBox()
        self.max_failures.setRange(1, 20)
        self.max_failures.setValue(3)
        self.max_failures.setMaximumWidth(120)
        control_body.addLayout(self._labeled_row('连续失败停止次数', self.max_failures))
        self.row([('录制整条路线', lambda: self.launch('record'), True),
                  ('试运行所选路线', lambda: self.launch('test'), False),
                  ('自动识图并运行', lambda: self.launch('auto'), False)], parent=control_body)
        self.row([('清空当前未保存段', self.reset_segment), ('停止，不保存', self.stop)],
                 editing=False, parent=control_body)

        log_body = self._card_body('实时日志', stretch=1)
        self.log_view = TextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMinimumHeight(160)
        self.log_view.setFont(QFont('Consolas', 10))
        self.log_view.setPlaceholderText('录制状态和每一次按键结果会写在这里，并同步到程序日志。')
        log_body.addWidget(self.log_view)

        self.refresh()
        self.startup_watch = QTimer(self)
        self.startup_watch.timeout.connect(self.check_startup)
        self.startup_watch.start(500)
        self._last_start_key = 0
        if og.my_app is not None:
            og.my_app.pressed.connect(self.on_global_press)

    def _card_body(self, title, stretch=0):
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.add_card(title, body, stretch=stretch)
        return layout

    def _labeled_row(self, label, widget):
        row = QHBoxLayout()
        caption = BodyLabel(label)
        caption.setMinimumWidth(148)
        row.addWidget(caption)
        row.addWidget(widget)
        row.addStretch(1)
        return row

    def set_phase(self, phase):
        self._phase = phase if phase in PHASES else 'idle'
        text, color = PHASES[self._phase]
        self.phase_chip.setText(text)
        self.phase_chip.setStyleSheet(
            f'background: {color}; color: white; border-radius: 8px; padding: 4px 10px;')

    def append_log(self, message):
        stamp = time.strftime('%H:%M:%S')
        self.log_view.append(f'{stamp}  {message}')
        bar = self.log_view.verticalScrollBar()
        bar.setValue(bar.maximum())
        if message.startswith('[按键]') or message.startswith('[热键]'):
            self.last_key.setText(message)
        self.refresh_hud(message)

    def apply_status(self, message, phase=None):
        self.status.setText(message)
        if phase:
            self.set_phase(phase)
        self.refresh_hud(message)

    def console(self, message, phase=None):
        self.logger.info(message)
        self.append_log(message)
        self.apply_status(message, phase)

    def on_route_log(self, message):
        self.append_log(message)

    def on_route_phase(self, phase):
        self.set_phase(phase)
        self.refresh_hud(self.status.text())

    def refresh_hud(self, text):
        if self._phase in ('listen', 'record', 'pause'):
            summary = text.replace('\n', ' ')
            if len(summary) > 48:
                summary = summary[:47] + '…'
            self.hud.present(self._phase, f'{PHASES[self._phase][0]}  {summary}')
        else:
            self.hud.dismiss()

    @property
    def name(self):
        return '键盘路线'

    def showEvent(self, event):
        super().showEvent(event)
        # Remember that the user opened this tool. Hiding/minimizing the main
        # window to return to the game must not disable its recording hotkeys.
        self._recording_hotkeys_armed = True

    def hotkey_feedback(self, message):
        self.console(message, phase='fail')
        recording_feedback('error')
        task = self.executor.get_task_by_class(KeyboardRouteTask)
        task.notification(message, title='键盘路线', tray=True)

    @property
    def icon(self):
        return FluentIcon.FLAG

    def row(self, actions, editing=True, parent=None):
        layout = QHBoxLayout()
        layout.setSpacing(8)
        for action in actions:
            label, callback = action[0], action[1]
            primary = len(action) > 2 and action[2]
            button = PrimaryPushButton(label) if primary else PushButton(label)
            button.clicked.connect(lambda checked=False, fn=callback: self.safe(fn))
            layout.addWidget(button)
            if editing:
                self.edit_buttons.append(button)
        layout.addStretch(1)
        if parent is None:
            self.addLayout(layout)
        else:
            parent.addLayout(layout)

    def safe(self, callback):
        try:
            callback()
        except Exception as error:
            QMessageBox.warning(self, '键盘路线', str(error))

    def refresh(self, ident=None):
        self.routes, errors = self.store.load_all()
        self.routes_list.clear()
        for route in self.routes:
            item = QListWidgetItem(route['name'])
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if route['segments'] and route['map_rule'] else Qt.Unchecked)
            self.routes_list.addItem(item)
        index = next((i for i, r in enumerate(self.routes) if r['id'] == ident), 0)
        self.routes_list.setCurrentRow(index)
        if errors:
            self.status.setText('跳过损坏路线：\n' + '\n'.join(errors))

    def selected(self):
        index = self.routes_list.currentRow()
        if index < 0 or index >= len(self.routes):
            raise ValueError('请先新建或选择一条路线')
        return self.routes[index]

    def show_segments(self, _=None):
        self.segments.clear()
        if self.routes_list.currentRow() < 0:
            return
        for i, segment in enumerate(self.selected()['segments']):
            rule = segment.get('checkpoint')
            description = ('OCR：' + '；'.join(rule['keywords']) if rule['kind'] == 'ocr' else '截图模板') if rule else ('已存截图，待配置检查条件' if segment.get('checkpoint_pending') else '无检查节点')
            self.segments.addItem(f'{i+1}. {segment["duration"]:.2f} 秒 / {len(segment["events"])} 个按键事件 → {description}')

    def create_route(self):
        name, ok = QInputDialog.getText(self, '新建路线', '路线名称')
        if ok:
            route = new_route(name)
            self.store.save(route)
            self.refresh(route['id'])

    def rename_route(self):
        route = copy.deepcopy(self.selected())
        name, ok = QInputDialog.getText(self, '重命名', '路线名称', text=route['name'])
        if ok:
            route['name'] = name.strip()
            self.store.save(route)
            self.refresh(route['id'])

    def delete_route(self):
        route = self.selected()
        if QMessageBox.question(self, '删除路线', f'删除「{route["name"]}」及其截图？') == QMessageBox.Yes:
            self.store.delete(route['id'])
            self.refresh()

    def index(self):
        index = self.segments.currentRow()
        if index < 0:
            raise ValueError('请先选择一个移动段')
        return index

    def show_start(self):
        route = self.selected()
        if not route.get('start_image'):
            raise ValueError('尚未录制起点截图')
        dialog = QDialog(self)
        dialog.setWindowTitle('起点参考：请人工核对角色位置与视角')
        QVBoxLayout(dialog).addWidget(RegionImage(read_image(self.store.asset(route['id'], route['start_image']))))
        dialog.exec()

    def save_rule(self, route, frame, rule):
        if rule['kind'] == 'template':
            rule['template'] = f'{uuid.uuid4().hex}.png'
            write_image(self.store.asset(route['id'], rule['template']), crop(frame, rule['rect']))
        return rule

    def edit_node(self):
        route, index = copy.deepcopy(self.selected()), self.index()
        segment = route['segments'][index]
        if not segment.get('image'):
            raise ValueError('该段没有节点截图，请通过录制插入检查节点')
        frame = read_image(self.store.asset(route['id'], segment['image']))
        dialog = RuleDialog(frame, segment.get('checkpoint'), self)
        if dialog.exec() == QDialog.Accepted:
            segment['checkpoint'] = self.save_rule(route, frame, dialog.rule)
            segment.pop('checkpoint_pending', None)
            self.store.save(route)
            self.refresh(route['id'])

    def delete_segment(self):
        route, index = copy.deepcopy(self.selected()), self.index()
        if QMessageBox.question(self, '删除移动段', '删除后路线将直接衔接前后两段，请确认移动仍然有效。') == QMessageBox.Yes:
            del route['segments'][index]
            self.store.save(route)
            self.refresh(route['id'])

    def launch(self, operation, single=False, start_immediately=False):
        if self.session:
            raise ValueError('请先停止当前操作')
        task = self.executor.get_task_by_class(KeyboardRouteTask)
        enabled_others = [item for item in self.executor.onetime_tasks if item.enabled and item is not task]
        current = self.executor.current_task
        if (current and getattr(current, 'running', False) and current is not task) or enabled_others:
            raise ValueError('请先停止其他任务并清空已启用的一次性任务')
        if current is task and getattr(task, 'running', False):
            raise ValueError('请先停止当前录制或回放')
        if task.enabled and not getattr(task, 'running', False):
            task.disable()
        route = copy.deepcopy(self.selected())
        hotkeys = tuple(w.text().strip().lower() for w in self.hotkeys)
        if operation == 'record':
            if len(set(hotkeys)) != 3 or any(not re.fullmatch(r'f([1-9]|1[0-2])', k) for k in hotkeys):
                raise ValueError('录制控制键必须是三个不同的 F1～F12 按键')
            reserved = str(self.executor.basic_options.get('Start/Stop', '')).lower()
            if reserved in hotkeys:
                raise ValueError(f'{reserved.upper()} 已用于程序启停，请在本页修改冲突的录制热键后重试')
            if single:
                index = self.index()
            else:
                index = None
                if route['segments'] and QMessageBox.question(self, '重新录制', '成功保存后会替换整条键盘路线，继续？') != QMessageBox.Yes:
                    return
                route['segments'] = []
                route['start_image'] = ''
            if single and (dict(self.executor.get_task_by_class(KeyboardRouteTask).key_config) != route['key_config']):
                raise ValueError('键位已改变，请恢复录制时键位或重录整条路线')
            self.store.root.mkdir(parents=True, exist_ok=True)
            (self.store.root / 'settings.json').write_text(json.dumps({'hotkeys': hotkeys}), encoding='utf-8')
        else:
            index = None
        routes = copy.deepcopy([r for i, r in enumerate(self.routes)
                                if self.routes_list.item(i).checkState() == Qt.Checked] if operation == 'auto' else [route])
        if operation in ('test', 'auto'):
            if not routes:
                raise ValueError('请勾选至少一条已录制的路线')
            for candidate in routes:
                validate_route(candidate, runnable=True, require_map=operation == 'auto')
        session = RouteSession(operation, route, routes, index, hotkeys, self.max_failures.value())
        session.created_at = time.perf_counter()
        if start_immediately:
            session.commands.put(('toggle',))
        session.status.connect(lambda text: self.apply_status(text))
        session.log.connect(self.on_route_log)
        session.phase.connect(self.on_route_phase)
        session.image.connect(lambda purpose, frame, draft, idx: self.safe(
            lambda: self.configure_image(session, purpose, frame, draft, idx)))
        session.recorded.connect(lambda draft: self.refresh(draft['id']))
        session.finished.connect(self.finished)
        self.session = session
        for button in self.edit_buttons:
            button.setEnabled(False)
        task.session = session
        self.console('正在排队启动路线任务…', phase='listen')
        try:
            arm_route_task(task)
        except Exception as error:
            self._abort_launch(session)
            raise ValueError(f'路线任务启动失败：{error}') from error

    def _console(self, message, phase=None):
        fn = getattr(self, 'console', None)
        if callable(fn):
            fn(message, phase)
        else:
            logger = getattr(self, 'logger', None)
            if logger is not None:
                logger.info(message)

    def on_global_press(self, key):
        # The page's start key also arms a selected route from inside the game.
        # Once armed, the recording poller alone handles control keys.
        name = normalize_key(key)
        controls = tuple(w.text().strip().lower() for w in self.hotkeys)
        if self.session or not self.executor or name not in controls:
            return
        report = getattr(self, '_console', None)
        message = f'[热键] 收到 {name.upper()}，录制页已打开：{self._recording_hotkeys_armed}'
        if callable(report):
            report(message)
        else:
            self.logger.info(message)
        if not self._recording_hotkeys_armed:
            if callable(report):
                report('录制快捷键尚未启用，请先打开「键盘路线」页面选择路线', phase='fail')
            else:
                self.status.setText('录制快捷键尚未启用，请先打开「键盘路线」页面选择路线')
            return
        if time.perf_counter() - self._last_start_key < .5:
            return
        self._last_start_key = time.perf_counter()
        task = self.executor.get_task_by_class(KeyboardRouteTask)
        if name != controls[0]:
            self.hotkey_feedback('没有录制内容可保存：录制尚未开始，请先按开始键 ' + controls[0].upper())
            return
        if not task.foreground():
            detail = task.focus_diagnostic() if hasattr(task, 'focus_diagnostic') else ''
            self.hotkey_feedback('开始录制失败：程序选中的游戏窗口不在前台，请在捕获页面确认窗口，切回游戏后再按 '
                                 + controls[0].upper() + (f'。{detail}' if detail else ''))
            return
        try:
            self.launch('record', start_immediately=True)
        except Exception as error:
            self.hotkey_feedback(f'开始录制失败：{error}')

    def _abort_launch(self, session):
        if self.session is session:
            self.session = None
        for button in self.edit_buttons:
            button.setEnabled(True)
        self.hud.dismiss()
        if not self.executor:
            return
        task = self.executor.get_task_by_class(KeyboardRouteTask)
        if getattr(task, 'session', None) is session:
            task.session = None
        if getattr(task, 'enabled', False) and not getattr(task, 'running', False):
            task.disable()

    def configure_image(self, session, purpose, frame, route, index):
        rule = route.get('map_rule') if purpose == 'map' else None
        dialog = RuleDialog(frame, rule, self)
        if dialog.exec() != QDialog.Accepted:
            if purpose == 'node':
                session.commands.put(('rule', None, None))
            return
        rule = self.save_rule(route, frame, dialog.rule)
        image_name = f'{uuid.uuid4().hex}.png'
        write_image(self.store.asset(route['id'], image_name), frame)
        if purpose == 'map':
            route['map_rule'] = rule
            route['map_image'] = image_name
            self.store.save(route)
            self.refresh(route['id'])
        else:
            session.commands.put(('rule', rule, image_name))

    def reset_segment(self):
        if self.session and self.session.operation == 'record':
            self.session.commands.put(('reset_segment',))

    def stop(self):
        if self.session:
            self.session.stop()
            self.status.setText('正在停止并释放按键…')

    def check_startup(self):
        # Only a session that never reached run() can be a startup failure.
        # A finished task is disabled and not running; that is not a failed start.
        session = self.session
        if not session or not self.executor:
            return
        task = self.executor.get_task_by_class(KeyboardRouteTask)
        in_run = bool(getattr(task, '_route_in_run', False))
        if in_run:
            session.entered_run = True
        if not startup_expired(session.created_at, time.perf_counter(), in_run,
                               getattr(session, 'entered_run', False)):
            return
        session.stop()
        if getattr(task, 'session', None) is session:
            task.session = None
        if getattr(task, 'enabled', False):
            task.disable()
        self.finished('error', '任务启动超时，已取消。请确认游戏连接和截图方式后重试')

    def finished(self, result, detail):
        self.session = None
        self.hud.dismiss()
        for button in self.edit_buttons:
            button.setEnabled(True)
        if detail or result != 'success':
            message = detail or f'本次运行结束：{result}'
            self.console(message, phase='fail' if result != 'stopped' else 'pause')
        else:
            self.set_phase('ok')
            if '正在' in self.status.text() or self.status.text().startswith('路线会话'):
                self.apply_status('已完成', phase='ok')
