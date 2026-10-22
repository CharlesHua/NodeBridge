import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QByteArray, QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMainWindow, QSplitter, QTreeWidget, QWidget

from nodebridge.layout_settings import LayoutSettings


class LayoutSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = str(Path(self.directory.name) / "settings.ini")
        self.windows = []
        self.addCleanup(self._close_windows)

    def _close_windows(self):
        for window in self.windows:
            window.close()

    def create_window(self):
        window = QMainWindow()
        window.resize(620, 460)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        tree = QTreeWidget()
        tree.setHeaderLabels(["节点", "状态", "CPU", "内存"])
        splitter.addWidget(tree)
        splitter.addWidget(QWidget())
        splitter.setSizes([300, 320])
        window.setCentralWidget(splitter)
        settings = QSettings(self.path, QSettings.Format.IniFormat)
        layout = LayoutSettings(window, settings, {"panes": splitter}, {"columns": tree.header()})
        self.windows.append(window)
        window.show()
        QTest.qWait(30)
        return window, splitter, tree, layout

    def test_reopen_restores_window_size_columns_and_splitter(self):
        window, splitter, tree, layout = self.create_window()
        window.resize(680, 510)
        QTest.qWait(20)
        splitter.setSizes([220, 456])
        tree.setColumnWidth(0, 163)
        tree.setColumnWidth(2, 84)
        expected = splitter.sizes()
        layout.save()
        window.close()
        reopened, panes, nodes, _ = self.create_window()
        self.assertEqual(reopened.size(), window.size())
        self.assertEqual(nodes.columnWidth(0), 163)
        self.assertEqual(nodes.columnWidth(2), 84)
        self.assertEqual(panes.sizes(), expected)

    def test_modes_keep_separate_layouts_and_maximized_startup(self):
        window, splitter, tree, layout = self.create_window()
        normal_size = window.size()
        splitter.setSizes([210, 406])
        tree.setColumnWidth(0, 151)
        normal_panes = splitter.sizes()
        window.showMaximized()
        QTest.qWait(30)
        splitter.setSizes([420, 376])
        tree.setColumnWidth(0, 237)
        maximized_panes = splitter.sizes()
        window.showNormal()
        QTest.qWait(30)
        self.assertEqual(tree.columnWidth(0), 151)
        self.assertEqual(splitter.sizes(), normal_panes)
        window.showMaximized()
        QTest.qWait(30)
        self.assertEqual(tree.columnWidth(0), 237)
        self.assertEqual(splitter.sizes(), maximized_panes)
        layout.save()
        window.close()
        reopened, panes, nodes, _ = self.create_window()
        self.assertTrue(reopened.isMaximized())
        self.assertEqual(nodes.columnWidth(0), 237)
        self.assertEqual(panes.sizes(), maximized_panes)
        reopened.showNormal()
        QTest.qWait(30)
        self.assertEqual(reopened.size(), normal_size)
        self.assertEqual(nodes.columnWidth(0), 151)
        self.assertEqual(panes.sizes(), normal_panes)

    def test_minimized_save_preserves_layout_and_reopens_normally(self):
        window, _, tree, layout = self.create_window()
        tree.setColumnWidth(0, 177)
        window.showMinimized()
        QTest.qWait(20)
        layout.save()
        window.close()
        reopened, _, nodes, _ = self.create_window()
        self.assertFalse(reopened.isMinimized())
        self.assertEqual(nodes.columnWidth(0), 177)

        reopened.showMaximized()
        QTest.qWait(20)
        nodes.setColumnWidth(0, 231)
        reopened.showMinimized()
        QTest.qWait(20)
        reopened.findChild(LayoutSettings).save()
        reopened.close()
        maximized, _, nodes, _ = self.create_window()
        self.assertTrue(maximized.isMaximized())
        self.assertFalse(maximized.isMinimized())
        self.assertEqual(nodes.columnWidth(0), 231)

    def test_invalid_saved_state_falls_back_to_defaults(self):
        settings = QSettings(self.path, QSettings.Format.IniFormat)
        settings.setValue("layout/normal/columns", QByteArray(b"invalid"))
        settings.setValue("layout/geometry", "invalid")
        settings.sync()
        window, _, tree, _ = self.create_window()
        self.assertFalse(window.isMaximized())
        self.assertGreater(tree.columnWidth(0), 0)

    def test_main_window_restores_columns_in_inactive_tabs(self):
        from nodebridge.window import MainWindow

        def create():
            window = MainWindow(QSettings(self.path, QSettings.Format.IniFormat),
                                log_path=Path(self.directory.name) / "activity.log")
            self.windows.append(window)
            window.show()
            QTest.qWait(30)
            return window

        window = create()
        window.terminal_workspace.node_tree.setColumnWidth(0, 213)
        window.terminal_workspace.node_tree.setColumnWidth(3, 91)
        window.tasks_workspace.table.setColumnWidth(0, 183)
        window.local_table.setColumnWidth(0, 207)
        self.assertTrue(window.close())
        reopened = create()
        self.assertEqual(reopened.terminal_workspace.node_tree.columnWidth(0), 213)
        self.assertEqual(reopened.terminal_workspace.node_tree.columnWidth(3), 91)
        self.assertEqual(reopened.tasks_workspace.table.columnWidth(0), 183)
        self.assertEqual(reopened.local_table.columnWidth(0), 207)


if __name__ == "__main__":
    unittest.main()
