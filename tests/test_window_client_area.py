import ctypes
import unittest
from unittest.mock import patch

import numpy as np

from src.compat import window_client_area as client_area
from src.compat.window_client_area import ClientAreaTransform


class TestPrintWindowHandles(unittest.TestCase):
    def test_hdc_above_32bit_passes_argument_conversion(self):
        hdc = 2**32 + 7
        raw = ctypes.WinDLL('user32', use_last_error=True).PrintWindow
        with self.assertRaises(ctypes.ArgumentError) as caught:
            raw(0, hdc, 0)
        self.assertIn('argument 2', str(caught.exception))

        client_area.install_printwindow_handle_fix()
        function = ctypes.windll.user32.PrintWindow
        self.assertIs(function.argtypes[1], ctypes.c_void_p)
        seen = {}

        @ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint)
        def receiver(hwnd, received, flags):
            seen['hdc'] = received
            return 1

        receiver(0, hdc, 2)
        self.assertEqual(seen['hdc'], hdc)


class TestClientAreaTransform(unittest.TestCase):

    def test_normalizes_1600x900_caption_without_changing_shape(self):
        frame = np.zeros((900, 1600, 3), dtype=np.uint8)
        frame[:29] = 17
        frame[29:] = 203
        transform = ClientAreaTransform(1600, 900, top=29)

        normalized = transform.normalize_frame(frame)

        self.assertEqual(normalized.shape, frame.shape)
        self.assertTrue(np.all(normalized[0] == 203))

    def test_normalizes_1920x1080_caption_without_changing_shape(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        frame[:29] = 17
        frame[29:] = 203
        transform = ClientAreaTransform(1920, 1080, top=29)

        normalized = transform.normalize_frame(frame)

        self.assertEqual(normalized.shape, frame.shape)
        self.assertTrue(np.all(normalized[0] == 203))

    def test_coordinate_round_trip_is_within_one_pixel(self):
        for width, height in ((1600, 900), (1920, 1080)):
            transform = ClientAreaTransform(width, height, top=29)
            points = ((0, 0), (width - 1, height - 1), (1170, 814), (800, 450))
            for x, y in points:
                raw = transform.normalized_to_raw(x, y)
                restored = transform.raw_to_normalized(*raw)
                self.assertLessEqual(abs(restored[0] - x), 1)
                self.assertLessEqual(abs(restored[1] - y), 1)

    def test_identity_transform_does_not_modify_frame_or_coordinates(self):
        frame = np.zeros((900, 1600, 3), dtype=np.uint8)
        transform = ClientAreaTransform(1600, 900)
        self.assertIs(transform.normalize_frame(frame), frame)
        self.assertEqual(transform.normalized_to_raw(100, 200), (100, 200))


class _FakeWindow:
    def __init__(self):
        self.hwnd = 123
        self.x = 10
        self.y = 20

    def get_abs_cords(self, x, y):
        return self.x + x, self.y + y


class _FakeCapture:
    def __init__(self, hwnd_window):
        self.hwnd_window = hwnd_window

    def do_get_frame(self):
        return np.zeros((900, 1600, 3), dtype=np.uint8)


class _FakePostMessage:
    def __init__(self, hwnd_window):
        self.hwnd_window = hwnd_window

    def update_mouse_pos(self, x, y, activate=True):
        return x, y, activate


class TestRuntimePatch(unittest.TestCase):

    def setUp(self):
        # Installation marks a class permanently, so each test uses fresh types.
        self.capture_class = type("FakeCapture", (_FakeCapture,), {})
        self.post_class = type("FakePost", (_FakePostMessage,), {})
        self.window_class = type("FakeWindow", (_FakeWindow,), {})

    def test_patch_is_idempotent(self):
        client_area._install_runtime_classes(
            self.capture_class, self.post_class, self.window_class, "auto"
        )
        first = self.capture_class.do_get_frame
        client_area._install_runtime_classes(
            self.capture_class, self.post_class, self.window_class, "auto"
        )
        self.assertIs(self.capture_class.do_get_frame, first)

    def test_capture_and_both_input_paths_share_transform(self):
        transform = ClientAreaTransform(1600, 900, top=29)
        window = self.window_class()
        capture = self.capture_class(window)
        post = self.post_class(window)

        client_area._install_runtime_classes(
            self.capture_class, self.post_class, self.window_class, "auto"
        )
        with patch.object(client_area, "_detect_transform", return_value=transform):
            self.assertEqual(capture.do_get_frame().shape, (900, 1600, 3))

        expected = transform.normalized_to_raw(1170, 814)
        self.assertEqual(post.update_mouse_pos(1170, 814)[:2], expected)
        self.assertEqual(
            window.get_abs_cords(1170, 814),
            (window.x + expected[0], window.y + expected[1]),
        )

    def test_detection_skips_disabled_non_unreal_and_borderless_windows(self):
        frame = np.zeros((900, 1600, 3), dtype=np.uint8)
        window = _FakeWindow()
        self.assertFalse(client_area._detect_transform(window, frame, "off").enabled)

        with patch.object(client_area.win32gui, "IsWindow", return_value=True), \
                patch.object(client_area.win32gui, "GetClassName", return_value="OtherWindow"):
            self.assertFalse(client_area._detect_transform(window, frame).enabled)

        with patch.object(client_area.win32gui, "IsWindow", return_value=True), \
                patch.object(client_area.win32gui, "GetClassName", return_value="UnrealWindow"), \
                patch.object(client_area.win32gui, "GetWindowLong", return_value=0):
            self.assertFalse(client_area._detect_transform(window, frame).enabled)

    def test_detects_zero_offset_caption_and_rejects_normal_native_offset(self):
        frame = np.zeros((900, 1600, 3), dtype=np.uint8)
        window = _FakeWindow()

        with patch.object(client_area.win32gui, "IsWindow", return_value=True), \
                patch.object(client_area.win32gui, "GetClassName", return_value="UnrealWindow"), \
                patch.object(
                    client_area.win32gui,
                    "GetWindowLong",
                    return_value=client_area.win32con.WS_CAPTION,
                ), \
                patch.object(client_area.win32gui, "GetWindowRect", return_value=(0, 0, 1600, 900)), \
                patch.object(client_area.win32gui, "ClientToScreen", return_value=(0, 0)), \
                patch.object(client_area, "_window_dpi", return_value=120), \
                patch.object(client_area, "_caption_height_for_dpi", return_value=29):
            transform = client_area._detect_transform(window, frame)
            self.assertTrue(transform.enabled)
            self.assertEqual(transform.top, 29)

        with patch.object(client_area.win32gui, "IsWindow", return_value=True), \
                patch.object(client_area.win32gui, "GetClassName", return_value="UnrealWindow"), \
                patch.object(
                    client_area.win32gui,
                    "GetWindowLong",
                    return_value=client_area.win32con.WS_CAPTION,
                ), \
                patch.object(client_area.win32gui, "GetWindowRect", return_value=(0, 0, 1600, 929)), \
                patch.object(client_area.win32gui, "ClientToScreen", return_value=(0, 29)):
            self.assertFalse(client_area._detect_transform(window, frame).enabled)


if __name__ == "__main__":
    unittest.main()
