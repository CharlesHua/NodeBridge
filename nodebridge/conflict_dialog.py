"""Per-file transfer conflict choice shown on the GUI thread."""

from __future__ import annotations

from nodebridge.i18n import tr, ui

from datetime import datetime
from pathlib import PurePath

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from nodebridge.file_icons import FileIcons
from nodebridge.transfer import ConflictAction, ConflictInfo


def _details(path: str, size: int | None, modified: float | None) -> str:
    count = tr('大小未知') if size is None else f"{size:,} B"
    timestamp = tr('修改时间未知') if modified is None else datetime.fromtimestamp(modified).strftime("%Y/%m/%d %H:%M:%S")
    return f"{count}\n{timestamp}"


class ConflictDialog(QDialog):
    def __init__(self, info: ConflictInfo, parent: QWidget | None = None):
        super().__init__(parent)
        ui(self.setWindowTitle, tr('目标文件已经存在'))
        self.resize(555, 350)
        self.setMinimumWidth(500)

        outer = QVBoxLayout(self)
        heading = ui(QLabel, tr('目标文件已经存在。\n请选择处理方法。'))
        outer.addWidget(heading)

        content = QHBoxLayout()
        files = QVBoxLayout()
        files.addWidget(self._file_section(tr('源文件：'), info.source_path, info.source_size, info.source_modified))
        files.addWidget(self._file_section(tr('目标文件：'), info.target_path, info.target_size, info.target_modified))
        content.addLayout(files, 3)

        actions = ui(QGroupBox, tr('动作：'))
        action_layout = QVBoxLayout(actions)
        self.overwrite_button = ui(QRadioButton, tr('覆盖'))
        self.skip_button = ui(QRadioButton, tr('跳过'))
        self.overwrite_button.setChecked(True)
        action_layout.addWidget(self.overwrite_button)
        action_layout.addWidget(self.skip_button)
        action_layout.addStretch()
        self.apply_all = ui(QCheckBox, tr('总是使用该操作'))
        action_layout.addWidget(self.apply_all)
        content.addWidget(actions, 2)
        outer.addLayout(content, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        ui(buttons.button(QDialogButtonBox.StandardButton.Ok).setText, tr('确定'))
        ui(buttons.button(QDialogButtonBox.StandardButton.Cancel).setText, tr('取消'))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def _file_section(self, title: str, path: str, size: int | None, modified: float | None) -> QWidget:
        section = QWidget()
        layout = QVBoxLayout(section)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(ui(QLabel, tr(title)))
        path_label = ui(QLabel, path)
        path_label.setWordWrap(True)
        path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(path_label)
        metadata = QHBoxLayout()
        icon = QLabel()
        icon.setPixmap(FileIcons().for_filename(PurePath(path).name).pixmap(26, 26))
        metadata.addWidget(icon)
        metadata.addWidget(ui(QLabel, _details(path, size, modified)), 1)
        layout.addLayout(metadata)
        return section

    def result_choice(self) -> tuple[ConflictAction, bool]:
        if self.exec() != QDialog.DialogCode.Accepted:
            return ConflictAction.CANCEL, False
        action = ConflictAction.OVERWRITE if self.overwrite_button.isChecked() else ConflictAction.SKIP
        return action, self.apply_all.isChecked()
