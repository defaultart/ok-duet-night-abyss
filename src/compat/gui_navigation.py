"""Keep the GUI on the capture page when the executor starts.

ok-script 2.0.6 automatically switches from its first (capture) page to the
first task page whenever the executor changes from paused to running.  That
navigation is surprising in this application because the capture page is also
where users select and verify the screenshot method.

The patch is installed before ``ok.OK`` creates the main window, so Qt connects
the executor signal to this replacement handler.  Only the automatic page
switch is removed; the original success notifications are retained.
"""

from __future__ import annotations

from typing import Any


_PATCH_MARKER = "_ok_dna_gui_navigation_fix_installed"
_ORIGINAL_ATTR = "_ok_dna_gui_navigation_original"


def _install_runtime_class(main_window_class: type[Any]) -> bool:
    """Patch a MainWindow-compatible class once.

    This helper is separate from :func:`install_gui_navigation_fix` so the
    behavior can be tested without constructing a Qt window.
    """
    if getattr(main_window_class, _PATCH_MARKER, False):
        return False

    original_executor_paused = main_window_class.executor_paused

    def executor_paused_without_auto_navigation(self: Any, paused: bool) -> Any:
        message = "Pause Success." if paused else "Start Success."
        return self.show_notification(self.tr(message), tray=False)

    setattr(executor_paused_without_auto_navigation, _ORIGINAL_ATTR, original_executor_paused)
    main_window_class.executor_paused = executor_paused_without_auto_navigation
    setattr(main_window_class, _PATCH_MARKER, True)
    return True


def install_gui_navigation_fix() -> bool:
    """Install the project-local ok-script main-window navigation fix."""
    from ok.ui.qt.MainWindow import MainWindow

    return _install_runtime_class(MainWindow)
