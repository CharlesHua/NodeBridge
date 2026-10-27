import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QLabel, QMenu, QWidget

from nodebridge.i18n import LANGUAGE_KEY, add_language_menu, install_language, selected_language, tr
from scripts.update_translations import validate


class TranslationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = QSettings(str(Path(self.temp.name) / "settings.ini"), QSettings.Format.IniFormat)
        self.settings.setValue(LANGUAGE_KEY, "en")
        install_language(self.app, self.settings)

    def tearDown(self):
        self.settings.setValue(LANGUAGE_KEY, "zh_CN")
        install_language(self.app, self.settings)
        self.temp.cleanup()

    def test_catalog_and_opaque_values(self):
        self.assertGreater(validate(), 400)
        self.assertEqual(tr("文件"), "File")
        self.assertIn("中文目录/{abc}", tr("本地目录不存在或无法访问：\n{0}", "中文目录/{abc}"))

    def test_saved_language_and_system_fallback(self):
        self.assertEqual(selected_language(self.settings, "zh_CN"), "en")
        self.settings.setValue(LANGUAGE_KEY, "invalid")
        self.assertEqual(selected_language(self.settings, "zh_TW"), "zh_CN")
        self.assertEqual(selected_language(self.settings, "de_DE"), "en")

    def test_menu_saves_and_switches_immediately(self):
        parent = QWidget()
        menu = add_language_menu(parent, QMenu(parent), self.settings)
        with patch("PySide6.QtWidgets.QMessageBox.information"):
            next(a for a in menu.actions() if a.data() == "zh_CN").trigger()
        self.assertEqual(self.settings.value(LANGUAGE_KEY), "zh_CN")
        self.assertEqual(tr("文件"), "文件")
        self.assertEqual(menu.title(), "语言")
        parent.close()

    def test_window_and_logs_keep_data_separate(self):
        from nodebridge.window import MainWindow
        path = Path(self.temp.name) / "activity.log"
        window = MainWindow(self.settings, log_path=path)
        try:
            self.assertEqual([window.workspace_tabs.tabText(i) for i in range(3)],
                             ["File", "Terminal", "Tasks"])
            self.assertLess(window.minimumSizeHint().width(), 1600)
            window._record_activity("file", "复制文件", "成功", "中文目录/任务.py")
            record = json.loads(path.read_text(encoding="utf-8").splitlines()[-1])
            self.assertEqual((record["action"], record["state"], record["detail"]),
                             ("复制文件", "成功", "中文目录/任务.py"))
            record = dict(key="test", user="用户", host="host", port=22, node="节点",
                          name="session", state="待检查", label="用户组", path="/中文目录")
            window.tasks_workspace.set_records([record])
            self.assertEqual(record["state"], "待检查")
            self.assertEqual(window.tasks_workspace.table.item(0, 4).text(), "Not checked")
            self.assertEqual(window.tasks_workspace.table.item(0, 5).text(), "/中文目录")
            # Live language changes preserve the exact widgets and user-entered data.
            terminal = window.terminal_workspace
            terminal.set_nodes([("worker:cft02", "File")])
            session_id = terminal.add_terminal("worker:cft02")
            view = terminal._views[session_id]
            group_id = terminal._sessions[session_id].group_id
            terminal.broadcast_input.setPlainText("echo 中文")
            window.host.setText("File")
            window.workspace_tabs.setCurrentIndex(1)
            log_before = path.read_bytes()
            host_label = next(label for label in window.findChildren(QLabel) if label.text() == "Host:")
            for language, labels in (("zh_CN", ["文件", "终端", "任务"]),
                                     ("en", ["File", "Terminal", "Tasks"])):
                with patch("sys.excepthook") as exceptions:
                    next(a for a in window.language_menu.actions() if a.data() == language).trigger()
                exceptions.assert_not_called()
                self.assertEqual([window.workspace_tabs.tabText(i) for i in range(3)], labels)
                self.assertIs(window.terminal_workspace, terminal)
                self.assertIs(terminal._views[session_id], view)
                self.assertEqual(terminal._sessions[session_id].group_id, group_id)
                self.assertEqual(terminal.node_label("worker:cft02"), "File")
                self.assertEqual(terminal.broadcast_input.toPlainText(), "echo 中文")
                self.assertEqual(window.host.text(), "File")
                self.assertEqual(window.workspace_tabs.currentIndex(), 1)
                self.assertEqual(window.tasks_workspace.table.item(0, 5).text(), "/中文目录")
                self.assertEqual(window.tasks_workspace.table.item(0, 4).text(),
                                 "待检查" if language == "zh_CN" else "Not checked")
                self.assertEqual(host_label.text(), "主机:" if language == "zh_CN" else "Host:")
                self.assertEqual(path.read_bytes(), log_before)
            self.assertEqual(window.tasks_workspace.table.item(0, 4).text(), "Not checked")
        finally:
            window.close()


if __name__ == "__main__":
    unittest.main()

