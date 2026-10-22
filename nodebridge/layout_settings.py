"""Persist independent normal/maximized layouts without storing session content."""

from PySide6.QtCore import QByteArray, QEvent, QObject, QTimer, Qt


class LayoutSettings(QObject):
    def __init__(self, window, settings, splitters, headers):
        super().__init__(window)
        self.window = window
        self.settings = settings
        self.widgets = {**splitters, **headers}
        self._restoring = True
        self._ready = False
        self._mode = "normal"
        self._defaults = self._snapshot()
        self._layouts = {
            mode: {name: settings.value(f"layout/{mode}/{name}", default)
                   for name, default in self._defaults.items()}
            for mode in ("normal", "maximized")
        }
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._capture)
        for splitter in splitters.values():
            splitter.splitterMoved.connect(self._changed)
        for header in headers.values():
            header.sectionResized.connect(self._changed)
            header.sectionMoved.connect(self._changed)
        geometry = settings.value("layout/geometry")
        if isinstance(geometry, QByteArray):
            window.restoreGeometry(geometry)
        maximized = settings.value("layout/maximized", False, type=bool)
        window.setWindowState(Qt.WindowState.WindowMaximized if maximized
                              else Qt.WindowState.WindowNoState)
        self._mode = self._current_mode()
        window.installEventFilter(self)

    def _current_mode(self):
        return "maximized" if self.window.isMaximized() else "normal"

    def _snapshot(self):
        return {name: widget.saveState() for name, widget in self.widgets.items()}

    def _restore(self):
        for name, widget in self.widgets.items():
            state = self._layouts[self._mode][name]
            if not isinstance(state, QByteArray) or not widget.restoreState(state):
                widget.restoreState(self._defaults[name])
        self._restoring = False
        self._ready = True

    def _capture(self):
        if (self._ready and not self._restoring and not self.window.isMinimized()
                and self._mode == self._current_mode()):
            self._layouts[self._mode] = self._snapshot()

    def _write_layouts(self):
        for mode, layout in self._layouts.items():
            for name, state in layout.items():
                self.settings.setValue(f"layout/{mode}/{name}", state)

    def _changed(self, *_args):
        # Keep a snapshot before Qt resizes children for a window-state change.
        self._capture()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Show and not self._ready:
            self._restoring = True
            QTimer.singleShot(0, self._restore)
        elif event.type() == QEvent.Type.WindowStateChange:
            mode = self._current_mode()
            if mode != self._mode:
                self._timer.stop()
                self._restoring = True
                self._mode = mode
                QTimer.singleShot(0, self._restore)
        elif event.type() == QEvent.Type.Resize and self._ready:
            self._timer.start()
        return super().eventFilter(watched, event)

    def save(self):
        self._timer.stop()
        self._capture()
        if self._ready:
            self._write_layouts()
            self.settings.setValue("layout/geometry", self.window.saveGeometry())
            self.settings.setValue("layout/maximized", self._mode == "maximized")
            self.settings.sync()
