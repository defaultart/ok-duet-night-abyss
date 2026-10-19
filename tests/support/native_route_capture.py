"""Exercise real MFC/GDI ownership in a child process without capturing a window.

A 32-bit in-memory DC substitutes for GetWindowDC. Everything after that point
uses the actual ok-script BitBlt implementation and the installed win32ui DLLs.
"""
import ctypes
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch

import win32gui

from ok.device.capture_methods.bitblt import BitBltCaptureMethod
from src.tasks.KeyboardRouteTask import KeyboardRouteTask


def main():
    ctypes.windll.kernel32.SetErrorMode(3)
    bitmaps = []

    def memory_dc(hwnd):
        dc = win32gui.CreateCompatibleDC(0)
        bitmap = win32gui.CreateBitmap(32, 32, 1, 32, None)
        win32gui.SelectObject(dc, bitmap)
        bitmaps.append(bitmap)
        return dc

    window = SimpleNamespace(real_x_offset=0, real_y_offset=0, window_width=32,
                             window_height=32, width=32, height=32,
                             real_width=32, real_height=32, hwnd=1, x=0, y=0,
                             hwnds=[(1, 0, 32, 32, 0, 0, 0, 0, 1),
                                    (2, 0, 32, 32, 0, 0, 0, 0, 1)])
    with patch('ok.device.capture_methods.bitblt.render_full', False), \
            patch.object(win32gui, 'GetWindowDC', side_effect=memory_dc), \
            patch.object(win32gui, 'ReleaseDC', return_value=1):
        source = BitBltCaptureMethod(window)
        source.exit_event = Event()
        task = object.__new__(KeyboardRouteTask)
        task._executor = SimpleNamespace(method=source, _frame=None, _last_frame_time=0)
        task.check_route = lambda *args, **kwargs: None
        try:
            assert source.get_frame().shape == (32, 32, 3)
            original_hdc = source.dc_object.GetSafeHdc()
            original_child_hdc = source.contexts[2].dc_object.GetSafeHdc()
            for _ in range(20):
                for _ in range(5):
                    assert task.next_frame().shape == (32, 32, 3)
                task._route_capture_worker.stop()
                assert not task._route_capture_worker.thread.is_alive()
                assert source.dc_object.GetSafeHdc() == original_hdc
                assert source.contexts[2].dc_object.GetSafeHdc() == original_child_hdc
            assert source.get_frame().shape == (32, 32, 3)
        finally:
            worker = getattr(task, '_route_capture_worker', None)
            if worker:
                worker.stop()
            source.close()
            for bitmap in bitmaps:
                win32gui.DeleteObject(bitmap)
    print('PASS: native GDI, 20 sessions x 5 frames; original capture remains usable')


if __name__ == '__main__':
    main()
