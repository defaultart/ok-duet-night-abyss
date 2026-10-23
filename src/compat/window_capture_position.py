"""Keep RenderFull capture running across monitor boundaries.

ok-script's screen containment check also applies to PrintWindow capture, even
though that capture does not read desktop pixels. Scope the exception to the
window currently being updated, without changing foreground capture behavior.
"""

import logging
import threading
from functools import wraps

import cv2
import numpy as np
import win32gui


logger = logging.getLogger(__name__)
_MARKER = "_ok_dna_capture_position_installed"
_STATE = "_ok_dna_capture_wait_state"
_CONTEXT = threading.local()
_INSTALL_LOCK = threading.Lock()


def _status(capture, state):
    if getattr(capture, _STATE, None) != state:
        setattr(capture, _STATE, state)
        logger.info("RenderFull capture: %s (hwnd=%s)", state, capture.hwnd_window.hwnd)


def _valid_frame(frame, window):
    if not isinstance(frame, np.ndarray) or frame.ndim != 3:
        return False
    height, width, channels = frame.shape
    if height <= 10 or width <= 10 or channels not in (3, 4):
        return False
    expected_width = window.real_width or window.width
    expected_height = window.real_height or window.height
    if (width, height) != (expected_width, expected_height):
        return False
    # Bound the cost of detecting blank / almost uniform PrintWindow results.
    sample = cv2.resize(frame[:, :, :3], (64, 36), interpolation=cv2.INTER_AREA)
    pixels = sample.reshape(-1, 3).astype(np.int16)
    median = np.median(pixels, axis=0)
    return np.mean(np.all(np.abs(pixels - median) <= 8, axis=1)) < 0.97


def _install_runtime(module, bitblt_module):
    if getattr(module, _MARKER, False):
        return
    original_check = module.check_pos
    original_update = module.HwndWindow.do_update_window_size
    capture_class = bitblt_module.BitBltCaptureMethod
    original_capture = capture_class.do_get_frame

    def render_full(capture):
        return isinstance(capture, capture_class) and bitblt_module.render_full

    @wraps(original_update)
    def update(self, *args, **kwargs):
        previous = getattr(_CONTEXT, "window", None)
        _CONTEXT.window = self
        try:
            return original_update(self, *args, **kwargs)
        finally:
            _CONTEXT.window = previous

    @wraps(original_check)
    def check(x, y, width, height, monitors_bounds):
        window = getattr(_CONTEXT, "window", None)
        manager = getattr(window, "device_manager", None)
        capture = getattr(manager, "capture_method", None)
        if render_full(capture) and capture.hwnd_window is window:
            # Minimized coordinates may be negative; the capture guard below
            # waits for restore without putting the executor in manual pause.
            return True
        return original_check(x, y, width, height, monitors_bounds)

    @wraps(original_capture)
    def capture(self):
        if not render_full(self):
            return original_capture(self)
        window = self.hwnd_window
        try:
            if not window.hwnd or not win32gui.IsWindow(window.hwnd):
                _status(self, "waiting for window")
                return None
            if win32gui.IsIconic(window.hwnd):
                _status(self, "waiting for window restore")
                return None
            if (window.real_width or window.width) <= 10 or (window.real_height or window.height) <= 10:
                _status(self, "waiting for window dimensions")
                return None
            frame = original_capture(self)
            if not _valid_frame(frame, window):
                _status(self, "waiting for valid frame")
                return None
        except Exception:
            if getattr(self, _STATE, None) != "waiting after capture error":
                logger.exception("RenderFull capture failed; waiting for next frame")
            _status(self, "waiting after capture error")
            return None
        _status(self, "ready")
        return frame

    module.HwndWindow.do_update_window_size = update
    module.check_pos = check
    capture_class.do_get_frame = capture
    setattr(module, _MARKER, True)


def install_window_capture_position_fix():
    from ok.device.capture_methods import bitblt, hwnd_window

    with _INSTALL_LOCK:
        _install_runtime(hwnd_window, bitblt)
