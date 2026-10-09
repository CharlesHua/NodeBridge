"""Persistent warnings and errors, separate from the user-facing activity log."""

from __future__ import annotations

import atexit
import faulthandler
import logging
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PySide6.QtCore import QtMsgType, qInstallMessageHandler

from nodebridge.activity_log import default_log_path


_qt_handler = None
_fault_stream = None


def install_diagnostics(log_directory: Path | None = None) -> Path:
    """Log Python/Qt warnings and unhandled failures to errors.log."""
    global _qt_handler, _fault_stream
    folder = Path(log_directory) if log_directory is not None else default_log_path().parent
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "errors.log"
    logger = logging.getLogger()
    if not any(getattr(handler, "baseFilename", None) == str(path.resolve())
               for handler in logger.handlers):
        handler = RotatingFileHandler(path, maxBytes=5_000_000, backupCount=5, encoding="utf-8")
        handler.setLevel(logging.WARNING)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s [%(threadName)s] %(name)s: %(message)s"
        ))
        logger.addHandler(handler)
    logger.setLevel(min(logger.level, logging.WARNING))

    def exception_hook(exc_type, exc_value, traceback):
        logging.getLogger("nodebridge.unhandled").critical(
            "未处理的 Python 异常", exc_info=(exc_type, exc_value, traceback)
        )

    def thread_hook(args):
        logging.getLogger("nodebridge.unhandled").critical(
            "后台线程未处理的 Python 异常", exc_info=(args.exc_type, args.exc_value, args.exc_traceback)
        )

    sys.excepthook = exception_hook
    threading.excepthook = thread_hook

    levels = {
        QtMsgType.QtWarningMsg: logging.WARNING,
        QtMsgType.QtCriticalMsg: logging.ERROR,
        QtMsgType.QtFatalMsg: logging.CRITICAL,
    }

    def qt_handler(message_type, _context, message):
        level = levels.get(message_type)
        if level is not None:
            logging.getLogger("nodebridge.qt").log(level, "%s", message)

    _qt_handler = qt_handler
    qInstallMessageHandler(_qt_handler)
    if _fault_stream is None:
        _fault_stream = (folder / "crash.log").open("a", encoding="utf-8")
        faulthandler.enable(file=_fault_stream, all_threads=True)
        atexit.register(_fault_stream.close)
    return path
