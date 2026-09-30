import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from nodebridge.conflict_dialog import ConflictDialog
from nodebridge.transfer import ConflictAction, ConflictInfo


class ConflictDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_skip_and_apply_to_all(self):
        dialog = ConflictDialog(ConflictInfo(
            "C:/Desktop/notes.txt", 5, 1700000000,
            "/home/alice/notes.txt", 8, 1700000001,
        ))
        self.assertTrue(dialog.overwrite_button.isChecked())

        def choose():
            dialog.skip_button.setChecked(True)
            dialog.apply_all.setChecked(True)
            dialog.accept()

        QTimer.singleShot(0, choose)
        self.assertEqual(dialog.result_choice(), (ConflictAction.SKIP, True))

    def test_cancel_does_not_apply_to_all(self):
        dialog = ConflictDialog(ConflictInfo("a.txt", 1, None, "b.txt", 2, None))
        QTimer.singleShot(0, dialog.reject)
        self.assertEqual(dialog.result_choice(), (ConflictAction.CANCEL, False))
