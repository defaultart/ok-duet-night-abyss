"""Normalize captioned BitBlt frames and keep mouse input aligned with them.

Some Unreal Engine windows draw a Windows caption inside the surface returned by
``GetWindowDC`` while Win32 reports ``ClientToScreen(hwnd, (0, 0))`` at the same
position as the window origin. ok-script cannot infer that offset and includes
the caption in a ``BitBlt_RenderFull`` frame. Template coordinates then move
down by one caption height while PostMessage still consumes client coordinates.

This is deliberately narrow: only captioned ``UnrealWindow`` targets with the
anomalous zero client offset are normalized. The game content is stretched back
to the original frame size and mouse coordinates are mapped back into the raw
window surface.
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
import logging
import threading
from typing import Any

import cv2
import win32con
import win32gui


logger = logging.getLogger(__name__)

_TRANSFORM_ATTR = "_ok_dna_client_area_transform"
_LOGGED_ATTR = "_ok_dna_client_area_logged"
_PATCH_MARKER = "_ok_dna_client_area_fix_installed"
_ORIGINAL_ATTR = "_ok_dna_client_area_original"
_INSTALL_LOCK = threading.Lock()

SM_CYCAPTION = 4
MAX_INSET_RATIO = 0.10


@dataclass(frozen=True)
class ClientAreaTransform:
    """Mapping between the normalized frame and the raw captured surface."""

    frame_width: int
    frame_height: int
    left: int = 0
    top: int = 0
    right: int = 0
    bottom: int = 0

    @property
    def enabled(self) -> bool:
        return any((self.left, self.top, self.right, self.bottom))

    @property
    def content_width(self) -> int:
        return self.frame_width - self.left - self.right

    @property
    def content_height(self) -> int:
        return self.frame_height - self.top - self.bottom

    def is_valid(self) -> bool:
        if self.frame_width <= 0 or self.frame_height <= 0:
            return False
        if self.content_width <= 0 or self.content_height <= 0:
            return False
        return (
            max(self.left, self.right) <= self.frame_width * MAX_INSET_RATIO
            and max(self.top, self.bottom) <= self.frame_height * MAX_INSET_RATIO
        )

    def normalized_to_raw(self, x: float, y: float) -> tuple[int, int]:
        """Convert a task/frame coordinate to the raw window surface."""
        if not self.enabled:
            return round(x), round(y)
        raw_x = self.left + x * self.content_width / self.frame_width
        raw_y = self.top + y * self.content_height / self.frame_height
        return round(raw_x), round(raw_y)

    def raw_to_normalized(self, x: float, y: float) -> tuple[int, int]:
        """Convert a raw window-surface coordinate to the normalized frame."""
        if not self.enabled:
            return round(x), round(y)
        normalized_x = (x - self.left) * self.frame_width / self.content_width
        normalized_y = (y - self.top) * self.frame_height / self.content_height
        return round(normalized_x), round(normalized_y)

    def normalize_frame(self, frame):
        """Crop the non-game inset and restore the original frame dimensions."""
        if frame is None or not self.enabled:
            return frame
        y2 = self.frame_height - self.bottom
        x2 = self.frame_width - self.right
        content = frame[self.top:y2, self.left:x2]
        if content.size == 0:
            return frame
        return cv2.resize(
            content,
            (self.frame_width, self.frame_height),
            interpolation=cv2.INTER_LINEAR,
        )


def _identity_transform(frame) -> ClientAreaTransform:
    if frame is None:
        return ClientAreaTransform(0, 0)
    return ClientAreaTransform(frame.shape[1], frame.shape[0])


def _window_dpi(hwnd: int) -> int:
    get_dpi = getattr(ctypes.windll.user32, "GetDpiForWindow", None)
    if get_dpi is None:
        return 96
    dpi = int(get_dpi(hwnd))
    return dpi if dpi > 0 else 96


def _caption_height_for_dpi(dpi: int) -> int:
    get_metric = getattr(ctypes.windll.user32, "GetSystemMetricsForDpi", None)
    if get_metric is not None:
        return int(get_metric(SM_CYCAPTION, dpi))
    base = int(ctypes.windll.user32.GetSystemMetrics(SM_CYCAPTION))
    return round(base * dpi / 96)


def _detect_transform(hwnd_window: Any, frame, mode: str = "auto") -> ClientAreaTransform:
    """Detect the anomalous caption-in-client case without touching normal windows."""
    identity = _identity_transform(frame)
    if mode == "off" or frame is None:
        return identity

    hwnd = int(getattr(hwnd_window, "hwnd", 0) or 0)
    if hwnd <= 0 or not win32gui.IsWindow(hwnd):
        return identity
    try:
        if win32gui.GetClassName(hwnd) != "UnrealWindow":
            return identity
        style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
        if not style & win32con.WS_CAPTION:
            return identity

        window_left, window_top, _, _ = win32gui.GetWindowRect(hwnd)
        client_left, client_top = win32gui.ClientToScreen(hwnd, (0, 0))
        native_left = max(0, client_left - window_left)
        native_top = max(0, client_top - window_top)

        # Existing ok-script cropping handles ordinary non-zero client offsets.
        # Cropping again here would damage correctly captured windows.
        if native_left > 0 or native_top > 0:
            return identity

        top = _caption_height_for_dpi(_window_dpi(hwnd))
        transform = ClientAreaTransform(
            frame_width=frame.shape[1],
            frame_height=frame.shape[0],
            top=top,
        )
        if not transform.is_valid():
            logger.warning(
                "client-area normalization ignored invalid inset: hwnd=%s frame=%sx%s top=%s",
                hwnd,
                frame.shape[1],
                frame.shape[0],
                top,
            )
            return identity
        return transform
    except Exception:
        logger.exception("client-area transform detection failed; using the original frame")
        return identity


def _set_transform(hwnd_window: Any, transform: ClientAreaTransform) -> None:
    setattr(hwnd_window, _TRANSFORM_ATTR, transform)
    if getattr(hwnd_window, _LOGGED_ATTR, False):
        return
    setattr(hwnd_window, _LOGGED_ATTR, True)
    logger.info(
        "client-area normalization %s: hwnd=%s raw=%sx%s inset=(%s,%s,%s,%s) "
        "content=%sx%s normalized=%sx%s",
        "enabled" if transform.enabled else "inactive",
        getattr(hwnd_window, "hwnd", 0),
        transform.frame_width,
        transform.frame_height,
        transform.left,
        transform.top,
        transform.right,
        transform.bottom,
        transform.content_width,
        transform.content_height,
        transform.frame_width,
        transform.frame_height,
    )


def _get_transform(hwnd_window: Any) -> ClientAreaTransform | None:
    return getattr(hwnd_window, _TRANSFORM_ATTR, None)


def _install_runtime_classes(bitblt_class, post_message_class, hwnd_window_class, mode: str) -> None:
    """Patch supplied runtime classes once; split out for idempotence tests."""
    if getattr(bitblt_class, _PATCH_MARKER, False):
        return

    original_get_frame = bitblt_class.do_get_frame
    original_update_mouse_pos = post_message_class.update_mouse_pos
    original_get_abs_cords = hwnd_window_class.get_abs_cords

    def normalized_get_frame(self):
        frame = original_get_frame(self)
        transform = _detect_transform(self.hwnd_window, frame, mode)
        _set_transform(self.hwnd_window, transform)
        return transform.normalize_frame(frame)

    def mapped_update_mouse_pos(self, x, y, activate=True):
        transform = _get_transform(self.hwnd_window)
        if transform is not None and transform.enabled and x != -1 and y != -1:
            x, y = transform.normalized_to_raw(x, y)
        return original_update_mouse_pos(self, x, y, activate)

    def mapped_get_abs_cords(self, x, y):
        transform = _get_transform(self)
        if transform is not None and transform.enabled:
            x, y = transform.normalized_to_raw(x, y)
        return original_get_abs_cords(self, x, y)

    setattr(normalized_get_frame, _ORIGINAL_ATTR, original_get_frame)
    setattr(mapped_update_mouse_pos, _ORIGINAL_ATTR, original_update_mouse_pos)
    setattr(mapped_get_abs_cords, _ORIGINAL_ATTR, original_get_abs_cords)
    bitblt_class.do_get_frame = normalized_get_frame
    post_message_class.update_mouse_pos = mapped_update_mouse_pos
    hwnd_window_class.get_abs_cords = mapped_get_abs_cords
    setattr(bitblt_class, _PATCH_MARKER, True)


def install_printwindow_handle_fix() -> None:
    """Let BitBlt_RenderFull pass 64-bit device-context handles to PrintWindow.

    ``ctypes.windll`` converts undeclared arguments as 32-bit ints. A device
    context that does not fit raises ``argument 2: OverflowError`` inside
    ``capture_by_bitblt``. The cleanup that follows can access-violate and
    take the process down. ok calls this same function object, so setting
    ``argtypes`` once covers the main window and composited children.
    """
    function = ctypes.windll.user32.PrintWindow
    function.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
    function.restype = ctypes.c_int


def install_client_area_fix(app_config: dict) -> bool:
    """Install the project-local BitBlt/client-coordinate compatibility fix."""
    install_printwindow_handle_fix()
    windows_config = app_config.get("windows", {}) if isinstance(app_config, dict) else {}
    mode = windows_config.get("client_area_fix", "auto")
    if mode == "off":
        logger.info("client-area normalization disabled by configuration")
        return False
    if mode != "auto":
        logger.warning("unknown client_area_fix mode %r; using 'auto'", mode)
        mode = "auto"

    from ok.device.capture_methods.bitblt import BitBltCaptureMethod
    from ok.device.capture_methods.hwnd_window import HwndWindow
    from ok.device.interaction_methods.post_message import PostMessageInteraction

    with _INSTALL_LOCK:
        _install_runtime_classes(BitBltCaptureMethod, PostMessageInteraction, HwndWindow, mode)
    return True
