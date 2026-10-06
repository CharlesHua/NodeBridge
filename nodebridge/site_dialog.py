"""Site manager dialog for connection profiles."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from nodebridge.sites import LockedSiteStore, Site, SiteStore


class SiteManagerDialog(QDialog):
    def __init__(
        self, store: SiteStore, parent: QWidget | None = None,
        jump_available: bool = False, connect_available: bool = True,
    ):
        super().__init__(parent)
        self.setWindowTitle("NodeBridge — 站点管理器")
        self.resize(820, 500)
        self.store = store
        try:
            self.sites = store.load()
        except LockedSiteStore:
            while True:
                password, accepted = QInputDialog.getText(
                    parent, "解锁站点密码", "请输入 NodeBridge 主密码：", QLineEdit.EchoMode.Password,
                )
                if not accepted:
                    choice = QMessageBox.question(
                        parent, "忘记主密码", "是否清除全部已保存密码并保留站点资料？\n"
                        "清除后无法恢复原密码。选择“否”将取消打开站点管理器。",
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                        QMessageBox.StandardButton.No,
                    )
                    if choice != QMessageBox.StandardButton.Yes:
                        raise ValueError("已取消解锁站点资料。")
                    store.reset_encrypted_passwords()
                    self.sites = store.load()
                    break
                try:
                    store.unlock(password)
                    self.sites = store.load()
                    break
                except ValueError as exc:
                    QMessageBox.warning(parent, "解锁失败", str(exc))
        self.selected_site: Site | None = None
        self.connection_password: str | None = None
        self._editing_id: str | None = None
        self._change_master = False

        layout = QVBoxLayout(self)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter, 1)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(QLabel("我的站点"))
        self.site_list = QListWidget()
        for site in self.sites:
            self.site_list.addItem(site.name)
        self.site_list.currentRowChanged.connect(self._select_row)
        left_layout.addWidget(self.site_list, 1)
        site_actions = QHBoxLayout()
        for label, callback in (
            ("新站点", self._new_site),
            ("复制", self._duplicate_site),
            ("删除", self._delete_site),
        ):
            button = QPushButton(label)
            button.clicked.connect(callback)
            site_actions.addWidget(button)
        left_layout.addLayout(site_actions)
        splitter.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        form = QFormLayout()
        self.name_edit = QLineEdit()
        self.host_edit = QLineEdit()
        self.port_edit = QSpinBox()
        self.port_edit.setRange(1, 65535)
        self.port_edit.setValue(22)
        self.user_edit = QLineEdit()
        self.key_edit = QLineEdit()
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("站点名称", self.name_edit)
        form.addRow("协议", QLabel("SFTP - SSH File Transfer Protocol"))
        form.addRow("主机", self.host_edit)
        form.addRow("端口", self.port_edit)
        form.addRow("用户", self.user_edit)
        form.addRow("私钥路径（可选）", self.key_edit)
        form.addRow("密码", self.password_edit)
        right_layout.addLayout(form)
        password_options = QHBoxLayout()
        self.password_mode = QComboBox()
        for label, mode in (("保存密码（明文）", "plain"), ("使用主密码加密", "master"), ("不保存密码", "none")):
            self.password_mode.addItem(label, mode)
        self.password_mode.setCurrentIndex(self.password_mode.findData(store.mode))
        self.password_mode.currentIndexChanged.connect(self._update_password_mode)
        password_options.addWidget(QLabel("密码保存："))
        password_options.addWidget(self.password_mode, 1)
        self.change_master_button = QPushButton("更换主密码…")
        self.change_master_button.clicked.connect(self._request_master_change)
        password_options.addWidget(self.change_master_button)
        right_layout.addLayout(password_options)
        self.password_note = QLabel()
        self.password_note.setWordWrap(True)
        right_layout.addWidget(self.password_note)
        storage_location = QLabel(f"站点资料：{store.path}")
        storage_location.setWordWrap(True)
        storage_location.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        right_layout.addWidget(storage_location)
        self._update_password_mode()
        right_layout.addStretch()
        splitter.addWidget(right)
        splitter.setSizes([300, 520])

        bottom = QHBoxLayout()
        bottom.addStretch()
        connect_button = QPushButton("经当前站点连接" if jump_available else "连接")
        connect_button.setEnabled(connect_available)
        connect_button.clicked.connect(self._connect_site)
        save_button = QPushButton("保存")
        save_button.clicked.connect(self._save_and_close)
        cancel_button = QPushButton("取消")
        cancel_button.clicked.connect(self.reject)
        bottom.addWidget(connect_button)
        bottom.addWidget(save_button)
        bottom.addWidget(cancel_button)
        layout.addLayout(bottom)

        self._set_form_enabled(False)
        if self.sites:
            self.site_list.setCurrentRow(0)

    def _update_password_mode(self) -> None:
        mode = self.password_mode.currentData()
        self.change_master_button.setEnabled(mode == "master" and self.store.mode == "master")
        self.password_note.setText({
            "plain": "密码将明文保存在站点资料中；请勿分享该文件。",
            "master": "密码经主密码加密后保存；忘记主密码将无法恢复已保存密码。",
            "none": "密码不会写入磁盘；连接时需要重新输入。",
        }[mode])

    def _request_master_change(self) -> None:
        self._change_master = True
        QMessageBox.information(self, "更换主密码", "保存站点资料时将要求输入新主密码。")

    def _new_master_password(self) -> str | None:
        first, accepted = QInputDialog.getText(self, "设置主密码", "输入新主密码：", QLineEdit.EchoMode.Password)
        if not accepted:
            return None
        second, accepted = QInputDialog.getText(self, "设置主密码", "再次输入主密码：", QLineEdit.EchoMode.Password)
        if not accepted:
            return None
        if not first or first != second:
            QMessageBox.warning(self, "设置主密码", "主密码不能为空，且两次输入必须一致。")
            return None
        return first

    def _set_form_enabled(self, enabled: bool) -> None:
        for widget in (
            self.name_edit, self.host_edit, self.port_edit,
            self.user_edit, self.key_edit, self.password_edit,
        ):
            widget.setEnabled(enabled)

    def _commit_form(self) -> None:
        if self._editing_id is None:
            return
        for index, site in enumerate(self.sites):
            if site.id == self._editing_id:
                updated = replace(
                    site,
                    name=self.name_edit.text().strip() or "未命名站点",
                    host=self.host_edit.text().strip(),
                    port=self.port_edit.value(),
                    username=self.user_edit.text().strip(),
                    key_file=self.key_edit.text().strip(),
                    password=self.password_edit.text(),
                )
                self.sites[index] = updated
                self.site_list.item(index).setText(updated.name)
                break

    def _select_row(self, row: int) -> None:
        self._commit_form()
        self._editing_id = None
        if row < 0 or row >= len(self.sites):
            self.password_edit.clear()
            self._set_form_enabled(False)
            return
        site = self.sites[row]
        self._editing_id = site.id
        self.name_edit.setText(site.name)
        self.host_edit.setText(site.host)
        self.port_edit.setValue(site.port)
        self.user_edit.setText(site.username)
        self.key_edit.setText(site.key_file)
        self.password_edit.setText(site.password)
        self._set_form_enabled(True)

    def _new_site(self) -> None:
        site = Site.create()
        self.sites.append(site)
        self.site_list.addItem(site.name)
        self.site_list.setCurrentRow(len(self.sites) - 1)
        self.name_edit.selectAll()
        self.name_edit.setFocus()

    def _duplicate_site(self) -> None:
        row = self.site_list.currentRow()
        if row < 0:
            return
        self._commit_form()
        original = self.sites[row]
        clone = replace(original, id=Site.create().id, name=f"{original.name} 副本")
        self.sites.append(clone)
        self.site_list.addItem(clone.name)
        self.site_list.setCurrentRow(len(self.sites) - 1)

    def _delete_site(self) -> None:
        row = self.site_list.currentRow()
        if row < 0:
            return
        site = self.sites[row]
        answer = QMessageBox.question(self, "删除站点", f"删除站点“{site.name}”？")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._editing_id = None
        self.sites.pop(row)
        self.site_list.takeItem(row)
        if self.sites:
            self.site_list.setCurrentRow(min(row, len(self.sites) - 1))
        else:
            self._select_row(-1)

    def _persist(self) -> bool:
        self._commit_form()
        mode = self.password_mode.currentData()
        master_password = None
        if mode == "master" and (self.store.mode != "master" or self._change_master):
            master_password = self._new_master_password()
            if master_password is None:
                return False
        try:
            self.store.save(self.sites, mode=mode, master_password=master_password)
            self._change_master = False
            return True
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "站点保存失败", str(exc))
            return False

    def _save_and_close(self) -> None:
        if self._persist():
            self.accept()

    def _connect_site(self) -> None:
        self._commit_form()
        row = self.site_list.currentRow()
        if row < 0:
            QMessageBox.warning(self, "NodeBridge", "请先选择站点。")
            return
        site = self.sites[row]
        if not site.host or not site.username:
            QMessageBox.warning(self, "NodeBridge", "请填写主机和用户名。")
            return
        if not self._persist():
            return
        self.selected_site = site
        password = site.password
        if self.store.mode == "none" and not password and not site.key_file:
            password, accepted = QInputDialog.getText(
                self, "站点密码", f"请输入“{site.name}”的连接密码：", QLineEdit.EchoMode.Password,
            )
            if not accepted:
                self.selected_site = None
                return
        self.connection_password = password or None
        self.accept()
