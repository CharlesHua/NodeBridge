"""Site manager dialog for connection profiles."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QHBoxLayout,
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

from nodebridge.sites import Site, SiteStore


class SiteManagerDialog(QDialog):
    def __init__(
        self, store: SiteStore, parent: QWidget | None = None,
        jump_available: bool = False, connect_available: bool = True,
    ):
        super().__init__(parent)
        self.setWindowTitle("NodeBridge — 站点管理器")
        self.resize(820, 500)
        self.store = store
        self.sites = store.load()
        self.selected_site: Site | None = None
        self.connection_password: str | None = None
        self._editing_id: str | None = None

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
        warning = QLabel("提示：密码当前会明文保存在本机用户配置文件中。")
        warning.setWordWrap(True)
        right_layout.addWidget(warning)
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
        try:
            self.store.save(self.sites)
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
        self.connection_password = site.password or None
        self.accept()
