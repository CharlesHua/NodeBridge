"""Review the scope of a work-node batch file operation."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QLabel, QLineEdit, QSpinBox, QVBoxLayout, QWidget,
)


class BatchDialog(QDialog):
    MODES = (
        ("跳板节点 → 工作节点", "jump_to_workers"),
        ("本地 → 工作节点", "local_to_workers"),
        ("本地 → 跳板节点", "local_to_jump"),
        ("跳板节点 → 本地", "jump_to_local"),
        ("单个工作节点 → 跳板节点", "worker_to_jump"),
        ("单个工作节点 → 本地", "worker_to_local"),
        ("单个工作节点 → 另一工作节点", "worker_to_worker"),
        ("删除工作节点上的选中项目", "delete_workers"),
    )

    def __init__(self, aliases: list[str], available_aliases: list[str], source_counts: tuple[int, int, int],
                 paths: tuple[str, str, str], parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("批量文件操作")
        self.resize(530, 290)
        self._paths = paths
        self._aliases = aliases
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"已勾选工作节点：{', '.join(aliases) if aliases else '无'}"))
        self.source_label = QLabel()
        layout.addWidget(self.source_label)
        form = QFormLayout()
        self.mode = QComboBox()
        for label, key in self.MODES:
            self.mode.addItem(label, key)
        form.addRow("方向：", self.mode)
        self.destination = QLineEdit()
        form.addRow("目标目录：", self.destination)
        self.target_node = QComboBox()
        self.target_node.addItems([alias for alias in available_aliases if alias not in aliases])
        form.addRow("目标工作节点：", self.target_node)
        self.move = QCheckBox("复制完整成功后删除来源（移动）")
        form.addRow("", self.move)
        self.parallelism = QSpinBox()
        self.parallelism.setRange(1, 4)
        self.parallelism.setValue(4)
        form.addRow("同时操作节点：", self.parallelism)
        layout.addLayout(form)
        self.note = QLabel("同名文件将逐项询问；跳过或取消后不会删除来源。")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._source_counts = source_counts
        self.mode.currentIndexChanged.connect(self._update_mode)
        self._update_mode()

    @property
    def operation(self) -> str:
        return self.mode.currentData()

    def _update_mode(self) -> None:
        mode = self.operation
        count = self._source_counts[
            1 if mode.startswith("local_") else
            2 if mode.startswith("worker_") or mode == "delete_workers" else 0
        ]
        self.source_label.setText(f"当前方向已选中 {count} 个文件或文件夹。")
        target = self._paths[
            2 if mode in {"jump_to_local", "worker_to_local"} else
            0 if mode.endswith("to_workers") or mode in {"delete_workers", "worker_to_worker"} else 1
        ]
        self.destination.setText(target)
        self.destination.setEnabled(mode != "delete_workers")
        self.target_node.setEnabled(mode == "worker_to_worker")
        self.move.setEnabled(mode != "delete_workers" and (len(self._aliases) == 1 or not mode.endswith("to_workers")))
        if not self.move.isEnabled():
            self.move.setChecked(False)
        if mode == "delete_workers":
            self.move.setChecked(False)
            self.note.setText("删除将永久移除所选工作节点上的项目及其内容，无法撤销。")
        elif mode.endswith("to_workers") and len(self._aliases) > 1:
            self.note.setText("向多个工作节点并行复制。暂不支持向多个节点移动；来源会保留。")
        else:
            self.note.setText("同名文件将逐项询问。移动只在完整复制成功且来源未变化后删除来源。")
