import unittest

from src.compat import gui_navigation


class _FakeStackedWidget:
    def __init__(self, current_index):
        self._current_index = current_index

    def currentIndex(self):
        return self._current_index


class _FakeMainWindow:
    """Model the ok-script 2.0.6 behavior that the compatibility patch replaces."""

    def __init__(self, current_index=0):
        self.stackedWidget = _FakeStackedWidget(current_index)
        self.task_tab = object()
        self.switch_calls = []
        self.notifications = []

    def startup_task_tab(self):
        return self.task_tab

    def switchTo(self, tab):
        self.switch_calls.append(tab)

    def tr(self, message):
        return message

    def show_notification(self, message, **kwargs):
        self.notifications.append((message, kwargs))

    def executor_paused(self, paused):
        task_tab = self.startup_task_tab()
        if not paused and self.stackedWidget.currentIndex() == 0 and task_tab:
            self.switchTo(task_tab)
        self.show_notification(
            self.tr("Start Success.") if not paused else self.tr("Pause Success."),
            tray=False,
        )


class TestGuiNavigationPatch(unittest.TestCase):

    def setUp(self):
        # Installation marks a class permanently, so every test gets a fresh type.
        self.main_window_class = type("FakeMainWindow", (_FakeMainWindow,), {})

    def test_start_on_capture_page_does_not_switch_to_tasks(self):
        gui_navigation._install_runtime_class(self.main_window_class)
        window = self.main_window_class(current_index=0)

        window.executor_paused(False)

        self.assertEqual(window.switch_calls, [])
        self.assertEqual(window.notifications, [("Start Success.", {"tray": False})])

    def test_start_on_another_page_keeps_current_page(self):
        gui_navigation._install_runtime_class(self.main_window_class)
        window = self.main_window_class(current_index=3)

        window.executor_paused(False)

        self.assertEqual(window.switch_calls, [])
        self.assertEqual(window.notifications, [("Start Success.", {"tray": False})])

    def test_pause_keeps_current_page_and_notification(self):
        gui_navigation._install_runtime_class(self.main_window_class)
        window = self.main_window_class(current_index=0)

        window.executor_paused(True)

        self.assertEqual(window.switch_calls, [])
        self.assertEqual(window.notifications, [("Pause Success.", {"tray": False})])

    def test_patch_is_idempotent(self):
        original = self.main_window_class.executor_paused

        self.assertTrue(gui_navigation._install_runtime_class(self.main_window_class))
        patched = self.main_window_class.executor_paused
        self.assertFalse(gui_navigation._install_runtime_class(self.main_window_class))

        self.assertIs(self.main_window_class.executor_paused, patched)
        self.assertIs(
            getattr(patched, gui_navigation._ORIGINAL_ATTR),
            original,
        )


if __name__ == "__main__":
    unittest.main()
