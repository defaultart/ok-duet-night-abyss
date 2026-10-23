from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from src.compat import window_capture_position as fix


class TestWindowCapturePosition(unittest.TestCase):
    def setUp(self):
        self.original_check = Mock(return_value=False)
        module = SimpleNamespace(check_pos=self.original_check)

        class Window:
            hwnd = 123
            width, height = 1600, 900
            real_width = real_height = 0

            def do_update_window_size(self, bounds, monitors=()):
                valid = module.check_pos(*bounds, monitors)
                if not valid:
                    self.device_manager.executor.pause()
                return valid

        class Capture:
            def __init__(self, window):
                self.hwnd_window = window
                self.read = Mock()

            def do_get_frame(self):
                return self.read()

        module.HwndWindow = Window
        self.module = module
        self.bitblt = SimpleNamespace(BitBltCaptureMethod=Capture, render_full=True)
        self.window = Window()
        self.capture = Capture(self.window)
        self.executor = SimpleNamespace(pause=Mock(), paused=False)
        self.window.device_manager = SimpleNamespace(capture_method=self.capture, executor=self.executor)
        fix._install_runtime(module, self.bitblt)
        self.frame = np.zeros((900, 1600, 3), dtype=np.uint8)
        self.frame[:, 800:] = 180
        self.capture.read.return_value = self.frame
        self.native = patch.multiple(fix.win32gui, IsWindow=Mock(return_value=True), IsIconic=Mock(return_value=False))
        self.native.start()
        self.addCleanup(self.native.stop)

    def test_offscreen_cross_screen_and_changed_monitor_layout_continue(self):
        for bounds in ((1279, 316, 1600, 900), (-800, -100, 1600, 900),
                       (1700, 0, 1600, 900), (-32000, -32000, 0, 0)):
            for monitors in ((), ((0, 0, 1920, 1080),), ((-1920, 0, 0, 1080),)):
                self.assertTrue(self.window.do_update_window_size(bounds, monitors))
        self.executor.pause.assert_not_called()
        self.original_check.assert_not_called()
        self.assertIs(self.capture.do_get_frame(), self.frame)

    def test_other_capture_modes_keep_position_check(self):
        for capture, render in ((self.capture, False), (object(), True)):
            self.window.device_manager.capture_method = capture
            self.bitblt.render_full = render
            self.assertFalse(self.window.do_update_window_size((1279, 316, 1600, 900)))
        self.assertEqual(self.executor.pause.call_count, 2)

    def test_minimize_waits_then_restores_without_pause(self):
        fix.win32gui.IsIconic.return_value = True
        self.assertTrue(self.window.do_update_window_size((-32000, -32000, 1600, 900)))
        self.assertIsNone(self.capture.do_get_frame())
        self.capture.read.assert_not_called()
        fix.win32gui.IsIconic.return_value = False
        self.assertIs(self.capture.do_get_frame(), self.frame)
        self.executor.pause.assert_not_called()

    def test_invalid_frames_and_capture_error_recover(self):
        near_blank = np.full_like(self.frame, 2)
        near_blank[:2] = 200
        for frame in (None, np.zeros((0, 0, 3), dtype=np.uint8),
                      np.zeros((100, 100, 3), dtype=np.uint8),
                      np.zeros_like(self.frame), near_blank):
            self.capture.read.return_value = frame
            self.assertIsNone(self.capture.do_get_frame())
        self.capture.read.side_effect = RuntimeError('capture unavailable')
        with self.assertLogs(fix.logger, level='ERROR'):
            self.assertIsNone(self.capture.do_get_frame())
        self.capture.read.side_effect = None
        self.capture.read.return_value = self.frame
        self.assertIs(self.capture.do_get_frame(), self.frame)
        self.executor.pause.assert_not_called()

    def test_missing_window_and_zero_dimensions_skip_native_capture(self):
        fix.win32gui.IsWindow.return_value = False
        self.assertIsNone(self.capture.do_get_frame())
        fix.win32gui.IsWindow.return_value = True
        self.window.width = 0
        self.assertIsNone(self.capture.do_get_frame())
        self.capture.read.assert_not_called()

    def test_manual_pause_is_preserved(self):
        self.executor.paused = True
        self.window.do_update_window_size((1279, 316, 1600, 900))
        self.capture.do_get_frame()
        self.assertTrue(self.executor.paused)

    def test_plain_bitblt_frames_are_unchanged(self):
        self.bitblt.render_full = False
        frame = np.zeros_like(self.frame)
        self.capture.read.return_value = frame
        self.assertIs(self.capture.do_get_frame(), frame)

    def test_installation_and_status_logs_are_not_repeated(self):
        methods = (self.module.check_pos, self.module.HwndWindow.do_update_window_size,
                   self.bitblt.BitBltCaptureMethod.do_get_frame)
        fix._install_runtime(self.module, self.bitblt)
        self.assertEqual(methods, (self.module.check_pos, self.module.HwndWindow.do_update_window_size,
                                  self.bitblt.BitBltCaptureMethod.do_get_frame))
        with patch.object(fix.logger, 'info') as log:
            self.capture.read.return_value = None
            self.capture.do_get_frame()
            self.capture.do_get_frame()
            self.capture.read.return_value = self.frame
            self.capture.do_get_frame()
            self.assertEqual(log.call_count, 2)

    def test_context_is_restored_after_exception_and_is_thread_local(self):
        import threading

        sentinel = object()
        fix._CONTEXT.window = sentinel
        self.window.device_manager.capture_method = object()
        self.original_check.side_effect = RuntimeError('update failed')
        try:
            with self.assertRaises(RuntimeError):
                self.window.do_update_window_size((0, 0, 1600, 900))
            self.assertIs(fix._CONTEXT.window, sentinel)
            seen = []
            worker = threading.Thread(target=lambda: seen.append(getattr(fix._CONTEXT, 'window', None)))
            worker.start()
            worker.join()
            self.assertEqual(seen, [None])
        finally:
            fix._CONTEXT.window = None

    def test_position_guard_composes_with_client_area_normalization(self):
        from src.compat import window_client_area as client_area

        # Startup installs the client-area wrapper first, then our guard.
        # Rebuild fresh classes to exercise that same composition order.
        class Window:
            hwnd = 123
            width, height = 1600, 900
            real_width = real_height = 0

            def get_abs_cords(self, x, y):
                return x, y

            def do_update_window_size(self):
                return module.check_pos(-800, 0, 1600, 900, ())

        class Capture:
            def __init__(self, window):
                self.hwnd_window = window

            def do_get_frame(inner):
                return self.frame

        class Post:
            def update_mouse_pos(self, x, y, activate=True):
                return x, y

        module = SimpleNamespace(HwndWindow=Window, check_pos=Mock(return_value=False))
        bitblt = SimpleNamespace(BitBltCaptureMethod=Capture, render_full=True)
        client_area._install_runtime_classes(Capture, Post, Window, 'auto')
        fix._install_runtime(module, bitblt)
        window = Window()
        capture = Capture(window)
        window.device_manager = SimpleNamespace(capture_method=capture)
        transform = client_area.ClientAreaTransform(1600, 900, top=29)
        with patch.object(client_area, '_detect_transform', return_value=transform):
            self.assertEqual(capture.do_get_frame().shape, (900, 1600, 3))
            self.assertEqual(window.get_abs_cords(800, 0), (800, 29))
        self.assertTrue(window.do_update_window_size())


if __name__ == '__main__':
    unittest.main()
