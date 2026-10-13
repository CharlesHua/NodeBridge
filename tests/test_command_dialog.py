import json
import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication

from nodebridge.command_dialog import CommandManagerDialog, DEFAULT_COMMANDS, load_commands


class CommandDialogTests(unittest.TestCase):
    def test_edit_visibility_and_persist_custom_command(self):
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "settings.ini")
            settings = QSettings(path, QSettings.Format.IniFormat)
            self.assertEqual(load_commands(settings), DEFAULT_COMMANDS)
            dialog = CommandManagerDialog(load_commands(settings))
            dialog.table.item(0, 0).setCheckState(Qt.CheckState.Unchecked)
            dialog.add_command({"command": "hostname", "description": "主机名", "visible": True})
            settings.setValue("terminal/commands", json.dumps(dialog.commands(), ensure_ascii=False))
            settings.sync()
            saved = load_commands(QSettings(path, QSettings.Format.IniFormat))
            self.assertFalse(saved[0]["visible"])
            self.assertEqual(saved[-1]["command"], "hostname")
            self.assertEqual(saved[-1]["description"], "主机名")
            dialog.close()
