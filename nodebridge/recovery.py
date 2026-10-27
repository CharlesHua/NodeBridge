"""Persist connection intent, without credentials or replayable shell commands."""
from dataclasses import asdict
import json
import logging

from PySide6.QtCore import QObject, QTimer, Qt
from PySide6.QtWidgets import QInputDialog, QLineEdit, QMessageBox

from nodebridge.discovery_settings import DiscoverySettings
from nodebridge.i18n import tr, ui
from nodebridge.remote import NodeConfig
from nodebridge.sites import LockedSiteStore

KEY = "recovery/connections_v1"


def snapshot(window):
    chain = [session for session, _ in window._parents]
    if window._session is not None:
        chain.append(window._session)
    terminals = []
    for sid, record in window.terminal_workspace._sessions.items():
        if not window._terminal_manager.has_session(sid):
            continue
        group = window.terminal_workspace._groups.get(record.group_id)
        item = {"group": record.group_id, "label": group.name if group else ""}
        task = window._persistent_sessions.get(sid)
        if task:
            item["task"] = task
        elif record.node_id.startswith("worker:"):
            item["worker"] = record.node_id.removeprefix("worker:")
        else:
            index = next((i for i, session in enumerate(chain)
                          if record.node_id == f"primary:{id(session)}"), None)
            if index is None:
                continue
            item["site"] = index
        terminals.append(item)
    return {"version": 1, "sites": [asdict(s.config) for s in chain],
            "workers": sorted(window._workers), "terminals": terminals}


class ConnectionRecovery(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.settings = window._settings
        self.started = False
        self.exiting = False
        self.prompting = False
        self.plan = None
        self.step = 0
        self.groups = {}
        self.expected_sites = 0
        self.workers_restored = False
        self.last = None
        self.shutdown_started = False
        window._terminal_manager.terminalClosed.connect(lambda *_: self.checkpoint())
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.tick)
        self.timer.start()

    def checkpoint(self, *, force=False):
        if not self.started or (self.exiting and not force) or self.plan is not None:
            return
        data = json.dumps(snapshot(self.window), ensure_ascii=False, sort_keys=True)
        if data == self.last:
            return
        self.settings.setValue(KEY, data)
        self.settings.sync()
        if self.settings.status() != self.settings.Status.NoError:
            logging.getLogger(__name__).warning("无法保存连接恢复记录。")
            return
        self.last = data

    def begin_disconnect(self):
        if not self.shutdown_started:
            self.checkpoint(force=True)
            self.shutdown_started = True

    def start(self):
        try:
            data = json.loads(self.settings.value(KEY, "{}"))
            if not isinstance(data, dict) or data.get("version") != 1:
                data = {}
            sites = data.get("sites", [])
            for site in sites:
                if not isinstance(site["host"], str) or not isinstance(site["username"], str):
                    raise ValueError("invalid site")
                if not isinstance(site["port"], int) or not 1 <= site["port"] <= 65535:
                    raise ValueError("invalid port")
            if not all(isinstance(x, str) for x in data.get("workers", [])):
                raise ValueError("invalid workers")
            if not all(isinstance(x, dict) for x in data.get("terminals", [])):
                raise ValueError("invalid terminals")
        except (ValueError, TypeError, KeyError):
            logging.getLogger(__name__).warning("连接恢复记录无效，已忽略。")
            data = {}
        self.started = True
        if data.get("sites"):
            # Keep the prior checkpoint intact while a modal prompt is open.
            self.plan = data
            self.prompting = True
            try:
                answer = QMessageBox.question(self.window, tr("恢复上次连接"),
                    tr("上次退出时仍有连接。是否重新连接这些站点和终端？\n普通终端将新建 Shell；tmux 尝试恢复原会话。不重放命令，不恢复已主动分离的会话。"),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No)
            finally:
                self.prompting = False
            if answer == QMessageBox.StandardButton.Yes:
                return
            self.plan = None
            self.checkpoint()
            # Declining recovery must not immediately reconnect via auto-start.
            return
        self.checkpoint()
        self.window.connect_startup_site()

    def password(self, config):
        store = self.window._site_store
        try:
            try:
                sites = store.load()
            except LockedSiteStore:
                secret, ok = QInputDialog.getText(self.window, tr("恢复连接"),
                    tr("请输入 NodeBridge 主密码："), QLineEdit.EchoMode.Password)
                if not ok:
                    return None, False
                store.unlock(secret)
                sites = store.load()
            for site in sites:
                if (site.host, site.port, site.username) == (config.host, config.port, config.username):
                    if site.password:
                        return site.password, True
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self.window, tr("恢复连接"), str(exc))
            return None, False
        secret, ok = QInputDialog.getText(self.window, tr("恢复连接"),
            tr("请输入 {0}@{1} 的连接密码（使用密钥或免密登录可留空）：", config.username, config.host),
            QLineEdit.EchoMode.Password)
        return secret or None, ok

    def tick(self):
        if not self.started or self.prompting:
            return
        w = self.window
        if self.exiting:
            if not any(getattr(w, name) is not None for name in
                       ("_job", "_worker_connect_job", "_tmux_job", "_tmux_scan_job",
                        "_remote_browse_job", "_worker_browse_job")) and not w._file_tasks:
                w.close()
            return
        if self.plan is None:
            self.checkpoint()
            return
        if any(getattr(w, name) is not None for name in
               ("_job", "_worker_connect_job", "_tmux_job", "_worker_browse_job", "_remote_browse_job")):
            return
        self.prompting = True
        try:
            self.advance()
        except (ValueError, TypeError, KeyError, IndexError) as exc:
            logging.getLogger(__name__).warning("连接恢复失败：%s", exc)
            QMessageBox.warning(w, tr("恢复连接"), tr("无法继续恢复连接，请通过站点管理器重新连接。"))
            self.plan = None
            self.checkpoint()
        finally:
            self.prompting = False

    def advance(self):
        w, plan = self.window, self.plan
        chain = [s for s, _ in w._parents] + ([w._session] if w._session else [])
        if len(chain) != self.expected_sites:
            self.plan = None  # A failed or manually disconnected root cannot restore its children.
            self.checkpoint()
            return
        # File workers belong to the root site, before entering a nested saved site.
        if self.step == 1 and not self.workers_restored:
            self.workers_restored = True
            aliases = set(plan.get("workers", []))
            aliases.update(item["worker"] for item in plan.get("terminals", []) if "worker" in item)
            w.worker_browser.set_aliases(sorted(set(w.worker_browser.known_aliases()) | aliases))
            w._sync_terminal_nodes()
            for i in range(w.worker_browser.nodes.topLevelItemCount()):
                item = w.worker_browser.nodes.topLevelItem(i)
                item.setCheckState(0, Qt.CheckState.Checked if item.text(0) in plan.get("workers", [])
                                   else Qt.CheckState.Unchecked)
            w._connect_selected_workers()
            return
        if self.step < len(plan["sites"]):
            raw = plan["sites"][self.step]
            config = NodeConfig(raw["host"], raw["port"], raw["username"], raw.get("key_file"),
                                DiscoverySettings.from_dict(raw.get("discovery")))
            password, ok = self.password(config)
            if not ok:
                self.plan = None
                self.checkpoint()
                return
            self.step += 1
            self.expected_sites += 1
            w._start_connection(config, password, w._session)
            return
        if self.step == len(plan["sites"]):
            self.step += 1
        items = plan.get("terminals", [])
        if items:
            item = items.pop(0)
            if "task" in item:
                # Existing restore checks remote existence; never create a replacement tmux session.
                if item["task"] in w._tmux_records:
                    w._restore_tmux_task(item["task"])
                return
            node = ("worker:" + item["worker"] if "worker" in item
                    else f"primary:{id(chain[item['site']])}")
            group_key = item.get("group", "")
            group_id = self.groups.get(group_key)
            if group_id not in w.terminal_workspace._groups:
                group_id = w.terminal_workspace.create_group([node])
                self.groups[group_key] = group_id
                w.terminal_workspace._groups[group_id].name = item.get("label") or tr("恢复的终端")
                w.terminal_workspace._refresh_groups(select=group_id)
            w._connect_terminals([node], persistent=False, recovery_group=group_id)
            return
        self.plan = None
        self.checkpoint()

    def allow_close(self):
        if self.exiting:
            return True
        w = self.window
        live = w._session is not None or bool(w._terminal_manager._channels) or bool(w._workers)
        if live:
            box = QMessageBox(w)
            ui(box.setWindowTitle, tr("尚有活动连接"))
            ui(box.setText, tr("仍有站点或终端连接。关闭软件将断开连接；tmux 远程会话会保留。"))
            quit_button = ui(box.addButton, tr("关闭软件"), QMessageBox.ButtonRole.DestructiveRole)
            minimize = ui(box.addButton, tr("最小化到后台"), QMessageBox.ButtonRole.ActionRole)
            ui(box.addButton, QMessageBox.StandardButton.Cancel)
            box.setDefaultButton(minimize)
            self.prompting = True
            try:
                box.exec()
            finally:
                self.prompting = False
            if box.clickedButton() is minimize:
                w.showMinimized()
                return False
            if box.clickedButton() is not quit_button:
                return False
        self.checkpoint()
        self.exiting = True  # Freeze pre-disconnect state during asynchronous shutdown.
        return True
