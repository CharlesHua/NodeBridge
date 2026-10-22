"""Native rendering setup before the Windows main window is first shown."""

from PySide6.QtWidgets import QApplication


def prepare_terminal_rendering(window):
    if QApplication.platformName() != "windows":
        return None

    from PySide6.QtQuickWidgets import QQuickWidget

    # WebEngine adds a Qt Quick child on first use. Without an existing texture
    # widget, Qt changes RasterSurface to Direct3DSurface by recreating the HWND.
    # Keep this unloaded, hidden child for the lifetime of the main window so
    # Qt chooses its graphics surface before first show, including file-only use.
    surface = QQuickWidget(window)
    surface.setObjectName("terminalRenderSurface")
    surface.hide()
    return surface
