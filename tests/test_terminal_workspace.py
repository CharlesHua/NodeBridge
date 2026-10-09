import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QCheckBox, QWidget

from nodebridge.terminal_workspace import TerminalWorkspace


class TerminalWorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.workspace = TerminalWorkspace()

    def tearDown(self):
        self.workspace.close()

    def test_nodes_share_one_list_and_allow_multiple_terminal_tabs(self):
        workspace = self.workspace
        workspace.set_nodes([("worker:cft02", "cft02"), ("primary", "cft01")])
        self.assertEqual(
            [workspace.node_tree.topLevelItem(index).text(0) for index in range(2)],
            ["cft01", "cft02"],
        )
        first = workspace.add_terminal("worker:cft02")
        second = workspace.add_terminal("worker:cft02")
        self.assertNotEqual(first, second)
        self.assertEqual(workspace.node_tree.topLevelItem(1).childCount(), 2)
        self.assertEqual(workspace.terminal_tabs.count(), 2)
        self.assertTrue(workspace.broadcast_input.isEnabled())
        self.assertFalse(workspace.send_button.isEnabled())

        workspace.terminal_tabs.tabCloseRequested.emit(0)
        self.assertEqual(workspace.terminal_tabs.count(), 1)
        self.assertEqual(workspace.node_tree.topLevelItem(1).childCount(), 2)
        workspace._item_clicked(workspace.node_tree.topLevelItem(1).child(0), 0)
        self.assertEqual(workspace.terminal_tabs.count(), 2)

    def test_batch_group_is_isolated_and_one_terminal_per_node(self):
        workspace = self.workspace
        workspace.set_nodes([("primary", "cft01"), ("worker:cft02", "cft02")])
        first_group = workspace.create_group(["primary", "worker:cft02"])
        primary = workspace.add_terminal("primary", group_id=first_group)
        worker = workspace.add_terminal("worker:cft02", group_id=first_group)
        second_group = workspace.create_group(["worker:cft02"])
        later = workspace.add_terminal("worker:cft02", group_id=second_group)
        self.assertNotEqual(first_group, second_group)
        self.assertEqual(workspace.broadcast_targets(), {"worker:cft02": later})
        workspace.group_selector.setCurrentIndex(workspace.group_selector.findData(first_group))
        self.assertEqual(workspace.broadcast_targets(), {
            "primary": primary, "worker:cft02": worker,
        })
        self.assertFalse(workspace.valid_broadcast(second_group, {"worker:cft02": later}))
        workspace.broadcast_input.setText("pending command")
        self.assertFalse(workspace.valid_broadcast(first_group, {"worker:cft02": later}))
        self.assertTrue(workspace.valid_broadcast(first_group, workspace.broadcast_targets()))
        workspace.group_selector.setCurrentIndex(workspace.group_selector.findData(second_group))
        self.assertEqual(workspace.broadcast_input.text(), "")
        self.assertEqual(workspace.broadcast_targets(), {"worker:cft02": later})

    def test_group_name_is_compact_editable_and_tabs_are_short(self):
        workspace = self.workspace
        workspace.set_nodes([(f"worker:cft{index:02}", f"cft{index:02}")
                             for index in range(2, 5)])
        group_id = workspace.create_group([f"worker:cft{index:02}" for index in range(2, 5)])
        workspace.add_terminal("worker:cft02", group_id=group_id)
        workspace.add_terminal("worker:cft03", group_id=group_id)
        workspace.add_terminal("worker:cft04", group_id=group_id)
        self.assertEqual(workspace.group_name(group_id), "终端组1(cft02..04)")
        self.assertEqual(workspace.group_selector.currentText(), "终端组1(cft02..04)")
        self.assertEqual(workspace.terminal_tabs.tabText(0), "终端组1 · cft02")
        with patch("nodebridge.terminal_workspace.QInputDialog.getText",
                   return_value=("实验组", True)):
            workspace.rename_group_button.click()
        self.assertEqual(workspace.group_name(group_id), "实验组(cft02..04)")
        self.assertEqual(workspace.terminal_tabs.tabText(0), "实验组 · cft02")

    def test_node_tab_titles_show_terminal_number_only_for_multiple_sessions(self):
        workspace = self.workspace
        workspace.set_nodes([("worker:cft20", "cft20")])
        first = workspace.add_terminal("worker:cft20")
        first_view = workspace._views[first]
        self.assertEqual(workspace.terminal_tabs.tabText(0), "终端组1 · cft20")

        second = workspace.add_terminal("worker:cft20")
        second_view = workspace._views[second]
        self.assertEqual(workspace.terminal_tabs.tabText(
            workspace.terminal_tabs.indexOf(first_view)), "终端组1 · cft20 （终端1）")
        self.assertEqual(workspace.terminal_tabs.tabText(
            workspace.terminal_tabs.indexOf(second_view)), "终端组2 · cft20 （终端2）")

        workspace._hide_tab(workspace.terminal_tabs.indexOf(first_view))
        workspace._show_tab(first)
        self.assertEqual(workspace.terminal_tabs.tabText(
            workspace.terminal_tabs.indexOf(first_view)), "终端组1 · cft20 （终端1）")

        workspace.remove_terminal(second)
        self.assertEqual(workspace.terminal_tabs.tabText(
            workspace.terminal_tabs.indexOf(first_view)), "终端组1 · cft20")

    def test_batch_tabs_load_webengine_only_when_selected(self):
        workspace = self.workspace
        workspace.set_nodes([("worker:cft02", "cft02"), ("worker:cft03", "cft03")])
        group_id = workspace.create_group(["worker:cft02", "worker:cft03"])
        workspace.show_group_combined(group_id)
        first = workspace.add_terminal("worker:cft02", group_id=group_id, activate=False)
        second = workspace.add_terminal("worker:cft03", group_id=group_id, activate=False)
        workspace.append_output(first, "first output\r\n")
        workspace.show()
        self.app.processEvents()

        self.assertEqual(workspace.terminal_tabs.count(), 3)
        self.assertIs(workspace.terminal_tabs.currentWidget(), workspace._combined_views[group_id])
        self.assertFalse(workspace._views[first].output._load_started)
        self.assertFalse(workspace._views[second].output._load_started)
        self.assertEqual(workspace._views[first].output._pending_output, ["first output\r\n"])

        workspace.terminal_tabs.setCurrentWidget(workspace._views[first])
        self.app.processEvents()
        self.assertTrue(workspace._views[first].output._load_started)
        self.assertFalse(workspace._views[second].output._load_started)

    def test_fifty_terminal_tabs_and_broadcast_stay_bounded(self):
        workspace = self.workspace
        nodes = [(f"worker:cft{index:02}", f"cft{index:02}") for index in range(1, 51)]
        workspace.set_nodes(nodes)
        group_id = workspace.create_group([node_id for node_id, _ in nodes])
        for node_id, _ in nodes:
            workspace.add_terminal(node_id, group_id=group_id)
        workspace.show_group_combined(group_id)
        workspace.resize(1800, 850)
        workspace.show()
        self.app.processEvents()

        self.assertEqual(len(workspace.broadcast_targets()), 50)
        self.assertIn("50 个目标", workspace.broadcast_summary.text())
        self.assertIn("cft01..50", workspace.broadcast_summary.text())
        self.assertIn("cft50", workspace.broadcast_summary.toolTip())
        self.assertLess(workspace.broadcast_summary.minimumSizeHint().width(), 1000)
        self.assertEqual(workspace.terminal_tabs.count(), 51)
        tabs = workspace.terminal_tabs
        bar = tabs.tabBar()
        self.assertTrue(bar.usesScrollButtons())
        for width in (1800, 1200, 1800):
            workspace.resize(width, 850)
            self.app.processEvents()
            self.assertLessEqual(bar.sizeHint().width(), tabs.width())
            self.assertLessEqual(workspace.tab_list_button.geometry().left() - bar.geometry().right(), 3)
        workspace.begin_broadcast_round(group_id, "ls", workspace.broadcast_targets())
        for session_id in workspace.broadcast_targets().values():
            workspace.append_output(session_id, "ls\nresult\n")
        workspace._refresh_combined_views()
        self.app.processEvents()
        self.assertLessEqual(workspace.tab_list_button.geometry().left() - bar.geometry().right(), 3)
        self.assertLess(workspace.minimumSizeHint().width(), 1920)

    def test_tab_dropdown_lists_all_open_views_and_selects_by_widget(self):
        workspace = self.workspace
        workspace.set_nodes([("worker:cft02", "cft02"), ("worker:cft03", "cft03")])
        group_id = workspace.create_group(["worker:cft02", "worker:cft03"])
        first = workspace.add_terminal("worker:cft02", group_id=group_id)
        workspace.add_terminal("worker:cft03", group_id=group_id)
        workspace.show_group_combined(group_id)
        self.assertIs(workspace.terminal_tabs.cornerWidget(Qt.Corner.TopRightCorner),
                      workspace.tab_list_button)
        workspace._populate_tab_list()
        actions = workspace.tab_list_menu.actions()
        self.assertEqual([action.text() for action in actions], [
            workspace.terminal_tabs.tabText(index)
            for index in range(workspace.terminal_tabs.count())
        ])
        self.assertEqual([action.isChecked() for action in actions], [False, False, True])

        with patch("nodebridge.terminal_workspace.QInputDialog.getText",
                   return_value=("项目组", True)):
            workspace.rename_group_button.click()
        workspace._populate_tab_list()
        self.assertTrue(all("项目组" in action.text()
                            for action in workspace.tab_list_menu.actions()))

        first_pane = workspace._views[first]
        workspace.tab_list_menu.actions()[0].trigger()
        self.assertIs(workspace.terminal_tabs.currentWidget(), first_pane)
        workspace._hide_tab(workspace.terminal_tabs.indexOf(first_pane))
        workspace._populate_tab_list()
        self.assertEqual(len(workspace.tab_list_menu.actions()), 2)
        self.assertFalse(any("cft02" in action.text()
                             for action in workspace.tab_list_menu.actions()))

    def test_combined_view_stacks_real_shell_replies_in_node_order_with_colors(self):
        workspace = self.workspace
        workspace.set_nodes([("worker:cft10", "cft10"), ("worker:cft02", "cft02")])
        group_id = workspace.create_group(["worker:cft10", "worker:cft02"])
        later = workspace.add_terminal("worker:cft10", group_id=group_id)
        earlier = workspace.add_terminal("worker:cft02", group_id=group_id)
        workspace.append_output(later, "user@cft10:/work$ ")
        workspace.append_output(earlier, "user@cft02:/work$ ")
        workspace.begin_broadcast_round(group_id, "ls", {
            "worker:cft10": later, "worker:cft02": earlier,
        })
        workspace.append_output(later, "ls\r\n\x1b[01;34mlater-node-file.py\x1b[0m\r\n"
                                "user@cft10:/work$ ")
        workspace.append_output(earlier, "ls\r\nearlier-node-file.py\r\nuser@cft02:/work$ ")
        workspace.show_group_combined(group_id)
        combined = workspace.terminal_tabs.currentWidget()
        output = combined._rendered.replace("\r\n", "\n")
        self.assertTrue(output.startswith("user@cft02:/work$ ls\nearlier-node-file.py\n"), repr(output))
        self.assertLess(output.index("user@cft02:/work$ ls"),
                        output.index("user@cft10:/work$ ls"))
        self.assertNotIn("[cft", output)
        self.assertNotIn("第 1 次", output)
        self.assertIn("earlier-node-file.py", output)
        self.assertIn("later-node-file.py", output)
        self.assertTrue(combined.output._read_only)
        self.assertIn("\x1b[01;34mlater-node-file.py\x1b[0m", combined._rendered)
        workspace.begin_broadcast_round(group_id, "pwd", {
            "worker:cft10": later, "worker:cft02": earlier,
        })
        workspace.append_output(later, "pwd\r\nnew-result.txt\r\nuser@cft10:/work$ ")
        workspace.append_output(earlier, "pwd\r\n/work\r\nuser@cft02:/work$ ")
        workspace._refresh_combined_views()
        output = combined._rendered.replace("\r\n", "\n")
        self.assertLess(output.index("user@cft10:/work$ ls"),
                        output.index("user@cft02:/work$ pwd"))
        self.assertIn("user@cft02:/work$ pwd", output)
        self.assertLess(output.index("earlier-node-file.py"), output.index("later-node-file.py"))
        self.assertLess(output.index("/work"), output.index("new-result.txt"))
        self.assertEqual(output.count("later-node-file.py"), 1)
        other_group = workspace.create_group(["worker:cft02"])
        other = workspace.add_terminal("worker:cft02", group_id=other_group)
        workspace.append_output(other, "user@cft02:/work$ ")
        workspace.begin_broadcast_round(other_group, "ls", {"worker:cft02": other})
        workspace.append_output(other, "ls\r\nother-group-only.txt\r\nuser@cft02:/work$ ")
        workspace._refresh_combined_views()
        self.assertNotIn("other-group-only.txt", combined._rendered)
        workspace.show_group_combined(other_group)
        self.assertIn("other-group-only.txt",
                      workspace.terminal_tabs.currentWidget()._rendered)

    def test_broadcast_panel_aligns_with_terminal_area(self):
        workspace = self.workspace
        workspace.resize(1200, 700)
        workspace.show()
        self.app.processEvents()
        panel = workspace.findChild(QWidget, "broadcastPanel")
        self.assertIs(panel.parentWidget(), workspace.terminal_tabs.parentWidget())
        self.assertEqual(panel.mapTo(workspace, panel.rect().topLeft()).x(),
                         workspace.terminal_tabs.mapTo(workspace,
                                                       workspace.terminal_tabs.rect().topLeft()).x())
        self.assertIs(workspace.command_toolbar.parentWidget(), panel.parentWidget())
        self.assertGreater(workspace.command_toolbar.y(), panel.y() + panel.height() - 1)
        self.assertEqual(panel.findChildren(QCheckBox), [])

    def test_combined_round_ends_when_shell_prompt_returns(self):
        workspace = self.workspace
        workspace.set_nodes([("worker:cft02", "cft02")])
        group_id = workspace.create_group(["worker:cft02"])
        session_id = workspace.add_terminal("worker:cft02", group_id=group_id)
        workspace.append_output(session_id, "user@cft02:/work$ ")
        workspace.begin_broadcast_round(group_id, "ls", {"worker:cft02": session_id})
        workspace.append_output(session_id, "ls\r\nresult.txt\r\nuser@cft02:/work$ ")
        workspace.append_output(session_id, "later unrelated output\r\n")
        workspace.show_group_combined(group_id)
        combined = workspace.terminal_tabs.currentWidget()._rendered
        self.assertIn("result.txt", combined)
        self.assertNotIn("later unrelated output", combined)
        self.assertIn("user@cft02:/work$ ls", combined)
        self.assertNotIn("[cft02", combined)

    def test_batch_node_checks_are_separate_from_broadcast_and_clear_after_action(self):
        workspace = self.workspace
        workspace.set_nodes([
            ("worker:cft02", "cft02"),
            ("worker:cft03", "cft03"),
            ("worker:cft04", "cft04"),
        ])
        requested = []
        workspace.connectManyRequested.connect(requested.append)
        workspace.node_tree.topLevelItem(0).setCheckState(2, Qt.CheckState.Checked)
        workspace.node_tree.topLevelItem(1).setCheckState(2, Qt.CheckState.Checked)
        self.assertEqual(workspace.broadcast_targets(), {})
        workspace.connect_checked_button.click()
        self.assertEqual(requested, [["worker:cft02", "worker:cft03"]])
        self.assertEqual(workspace.checked_nodes(), [])

    def test_shift_check_selects_terminal_node_range(self):
        workspace = self.workspace
        workspace.set_nodes([(f"worker:cft{index:02}", f"cft{index:02}") for index in range(2, 6)])
        workspace.resize(1200, 600)
        workspace.show()
        self.app.processEvents()

        def click_check(row, modifiers=Qt.KeyboardModifier.NoModifier):
            tree = workspace.node_tree
            item = tree.topLevelItem(row)
            point = tree.visualItemRect(item).center()
            point.setX(tree.columnViewportPosition(2) + 15)
            QTest.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton, modifiers, pos=point)

        click_check(0)
        click_check(3, Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(workspace.checked_nodes(), [
            "worker:cft02", "worker:cft03", "worker:cft04", "worker:cft05",
        ])

    def test_disconnect_removes_only_that_nodes_terminal_tabs(self):
        workspace = self.workspace
        workspace.set_nodes([("primary", "cft01"), ("worker:cft02", "cft02")])
        workspace.add_terminal("primary")
        workspace.add_terminal("worker:cft02")
        workspace.set_nodes([("primary", "cft01")])
        self.assertEqual(workspace.node_tree.topLevelItemCount(), 1)
        self.assertEqual(workspace.terminal_tabs.count(), 1)

    def test_unexpected_close_keeps_output_but_disables_draft(self):
        workspace = self.workspace
        workspace.set_nodes([("primary", "cft01")])
        session_id = workspace.add_terminal("primary")
        workspace.append_output(session_id, "permission denied\r\n")
        workspace.mark_terminal_closed(session_id)
        self.assertEqual(workspace.node_tree.topLevelItem(0).child(0).text(1), "已断开")
        pane = workspace.terminal_tabs.widget(0)
        self.assertIn("permission denied", pane._recent_output)
        self.assertFalse(pane.output._input_enabled)
        self.assertEqual(workspace.broadcast_targets(), {})

    def test_command_helpers_replace_broadcast_draft_without_sending(self):
        workspace = self.workspace
        workspace.set_nodes([("primary", "cft01")])
        workspace.add_terminal("primary")
        workspace.show()
        self.app.processEvents()
        workspace.broadcast_input.setFocus()
        sent = []
        workspace.broadcastRequested.connect(lambda *args: sent.append(args))
        workspace.broadcast_input.setText("prefix suffix")
        workspace.broadcast_input.setCursorPosition(len("prefix "))
        self.assertEqual(workspace.command_toolbar.orientation(), Qt.Orientation.Horizontal)
        self.assertEqual([button.text() for button in workspace.command_helpers], [
            "cd（进入目录）", "mkdir（新建目录）", "rm -r（删除目录及子目录）",
            "cd ..（退回上级）", "ls（列出文件）", "ls -a（含隐藏）",
            "ls -lh（详细列表）", "pwd（当前路径）",
        ])
        workspace.command_helpers[0].click()
        self.assertEqual(workspace.broadcast_input.text(), "cd ")
        workspace.broadcast_input.clear()
        for button, expected in zip(workspace.command_helpers,
                                    ("cd ", "mkdir ", "rm -r ", "cd ..",
                                     "ls", "ls -a", "ls -lh", "pwd")):
            button.click()
            self.assertEqual(workspace.broadcast_input.text(), expected)
            workspace.broadcast_input.clear()
        self.assertEqual(sent, [])

    def test_command_helpers_replace_single_terminal_draft_without_running_it(self):
        workspace = self.workspace
        workspace.set_nodes([("primary", "cft01")])
        session_id = workspace.add_terminal("primary")
        workspace.append_output(session_id, "user@cft01:~$ cd old")
        sent = []
        workspace.inputRequested.connect(lambda sid, data: sent.append((sid, data)))
        workspace.show()
        self.app.processEvents()
        workspace._views[session_id].output.setFocus()
        self.assertTrue(workspace.command_helpers[1].isEnabled())
        workspace.command_helpers[1].click()
        self.assertEqual(sent, [(session_id, b"\x05\x15mkdir ")])
        self.assertNotIn(b"\r", sent[0][1])

    def test_command_helper_follows_last_input_focus_across_terminals_and_broadcast(self):
        workspace = self.workspace
        workspace.set_nodes([("primary", "cft01"), ("worker:cft02", "cft02")])
        first = workspace.add_terminal("primary")
        second = workspace.add_terminal("worker:cft02")
        workspace.append_output(first, "user@cft01:~$ old")
        workspace.append_output(second, "user@cft02:~$ old")
        sent = []
        workspace.inputRequested.connect(lambda sid, data: sent.append((sid, data)))
        workspace.show()
        self.app.processEvents()
        workspace._show_tab(first)
        workspace.command_helpers[4].click()
        workspace._show_tab(second)
        workspace.command_helpers[5].click()
        self.assertEqual(sent, [(first, b"\x05\x15ls"), (second, b"\x05\x15ls -a")])
        workspace.broadcast_input.setFocus()
        workspace.broadcast_input.setText("old draft")
        workspace.command_helpers[7].click()
        self.assertEqual(workspace.broadcast_input.text(), "pwd")
        self.assertEqual(len(sent), 2)
