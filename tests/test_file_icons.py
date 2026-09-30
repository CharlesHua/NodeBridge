import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from nodebridge.file_icons import FileIcons


class FileIconTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_python_and_text_files_have_distinct_icons(self):
        icons = FileIcons()
        self.assertNotEqual(
            icons.for_filename("script.py").cacheKey(),
            icons.for_filename("notes.txt").cacheKey(),
        )
        self.assertEqual(
            icons.for_filename("script.py").cacheKey(),
            icons.for_filename("other.PY").cacheKey(),
        )
