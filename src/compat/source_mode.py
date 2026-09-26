"""Small runtime adjustments used only when launching directly from source."""

from __future__ import annotations

import os


def disable_source_update_check() -> bool:
    """Hide PyAppify update controls when no launcher version is available."""
    if os.environ.get("PYAPPIFY_VERSION"):
        return False
    try:
        import pyappify
    except ImportError:
        return False
    if not callable(getattr(pyappify, "get_version_list", None)):
        return False
    pyappify.get_version_list = None
    return True
