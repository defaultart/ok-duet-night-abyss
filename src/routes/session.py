"""Qt bridge: UI owns editing; executor thread owns capture and input."""
from queue import Queue
from threading import Event

from PySide6.QtCore import QObject, Signal


class RouteSession(QObject):
    status = Signal(str)
    # Executor thread emits these; the page appends them on the UI thread.
    log = Signal(str)
    phase = Signal(str)
    # purpose, game frame, route draft, optional segment index
    image = Signal(str, object, object, object)
    recorded = Signal(object)
    finished = Signal(str, str)

    def __init__(self, operation, route=None, routes=None, segment=None, hotkeys=None, max_failures=3):
        super().__init__()
        self.operation = operation
        self.route = route
        self.routes = routes or []
        self.segment = segment
        self.hotkeys = hotkeys or ('f8', 'f7', 'f10')
        self.max_failures = max_failures
        self.commands = Queue()
        self.stopped = Event()
        self.entered_run = False

    def stop(self):
        self.stopped.set()
