import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.compat.source_mode import disable_source_update_check


class TestSourceMode(unittest.TestCase):

    def test_source_mode_disables_update_api(self):
        fake = SimpleNamespace(get_version_list=lambda: [])
        with patch.dict(os.environ, {}, clear=True), patch.dict(sys.modules, {"pyappify": fake}):
            self.assertTrue(disable_source_update_check())
        self.assertIsNone(fake.get_version_list)

    def test_packaged_mode_keeps_update_api(self):
        original = lambda: []
        fake = SimpleNamespace(get_version_list=original)
        with patch.dict(os.environ, {"PYAPPIFY_VERSION": "1.2.3"}, clear=True), \
                patch.dict(sys.modules, {"pyappify": fake}):
            self.assertFalse(disable_source_update_check())
        self.assertIs(fake.get_version_list, original)


if __name__ == "__main__":
    unittest.main()
