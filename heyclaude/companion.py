"""The desktop companion (ROADMAP M6): the character at ~120 px in the bottom-right
corner, above the taskbar. A transparent, click-through, never-focused window with a
CharacterHost (ui/Companion.qml); it follows the same states as the island and plays
the pack's desktop antics. The pointer coming close makes it look, and ends an antic.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QObject, QPointF, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QCursor, QGuiApplication
from PySide6.QtQuick import QQuickItem, QQuickView

from . import characters as CH
from . import states as S
from .config import ROOT

QML = ROOT / "ui" / "Companion.qml"
MARGIN = 16


class Companion(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.view: QQuickView | None = None
        self.host = None
        self.man: dict | None = None
        self.size = 120
        self.driver = CH.Driver(send=self._send, later=lambda ms, fn: QTimer.singleShot(ms, fn))
        self._near_was = 0.0
        self._ptr = QTimer(self, interval=100, timeout=self._pointer)

    # -- window ------------------------------------------------------------------------
    def _ensure(self):
        if self.view is not None:
            return
        v = QQuickView()
        v.setColor(QColor(0, 0, 0, 0))
        v.setFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus
                   | Qt.WindowTransparentForInput | Qt.NoDropShadowWindowHint)
        v.setTitle("hey-claude companion")
        v.setResizeMode(QQuickView.SizeRootObjectToView)
        v.engine().addImportPath(str(CH.KIT_IMPORT_DIR))
        v.setSource(QUrl.fromLocalFile(str(QML)))
        if v.status() == QQuickView.Error:
            raise RuntimeError("; ".join(e.toString() for e in v.errors()))
        self.view = v
        self.host = v.rootObject().findChild(QQuickItem, "host")
        self.host.anticFinished.connect(lambda aid, caught: self.driver.antic_done(aid, caught))

    def configure(self, man: dict | None, size: int = 120, frequency: str = "normal", disabled=()):
        """Show `man` on the desktop (None = hide the companion)."""
        if man is None:
            self.man = None
            self.driver.configure(None)
            if self.view is not None:
                self.view.hide()
            self._ptr.stop()
            return
        self._ensure()
        changed = self.man is None or self.man["id"] != man["id"] or self.size != size
        self.man, self.size = man, int(size)
        if changed:
            b = man["desktop_bounds"]
            w, h = math.ceil(self.size * b[2] / 100), math.ceil(self.size * b[3] / 100)
            root = self.view.rootObject()
            root.setProperty("boxX", -b[0] / 100 * self.size)
            root.setProperty("boxY", -b[1] / 100 * self.size)
            self.view.resize(w, h)
            h_ = self.host
            h_.setProperty("source", QUrl())
            h_.setProperty("manifest", {"bounds": man["bounds"], "desktop_bounds": man["desktop_bounds"],
                                        "oneshot": man.get("oneshot", {})})
            h_.setProperty("small", False)
            h_.setProperty("size", float(self.size))
            h_.setProperty("budgets", dict(S.load().get("budgets", {})))
            h_.setProperty("source", QUrl.fromLocalFile(man["qml"]))
            self._place()
        self.driver.configure(man, frequency=frequency, disabled=disabled, where="desktop")
        self.view.show()
        self._ptr.start()

    def _place(self):
        scr = QGuiApplication.primaryScreen()
        ag = scr.availableGeometry()
        self.view.setPosition(ag.right() - self.view.width() - MARGIN, ag.bottom() - self.view.height() - MARGIN)

    def set_reduced(self, on: bool):
        if self.host is not None:
            self.host.setProperty("reducedMotion", bool(on))

    def set_running(self, on: bool):
        if self.host is not None:
            self.host.setProperty("running", bool(on))

    def levels(self, level: float | None = None, amplitude: float | None = None):
        if self.host is None or self.man is None:
            return
        if level is not None:
            self.host.setProperty("level", float(level))
        if amplitude is not None:
            self.host.setProperty("amplitude", float(amplitude))

    def close(self):
        self._ptr.stop()
        if self.view is not None:
            self.view.close()

    # -- driver --------------------------------------------------------------------------
    def _send(self, fn: str, args: list):
        if self.host is None:
            return
        from PySide6.QtCore import QMetaObject, Q_ARG, Q_RETURN_ARG
        if fn == "state":
            self.host.setProperty("stateId", args[0])
            self.host.setProperty("count", self.driver.tasks)
            self.host.setProperty("mood", self.driver.mood)
        elif fn == "play":
            QMetaObject.invokeMethod(self.host, "playAntic", Qt.DirectConnection, Q_RETURN_ARG("QVariant"),
                                     Q_ARG("QVariant", args[0]), Q_ARG("QVariant", float(args[1])))
        elif fn == "interrupt":
            QMetaObject.invokeMethod(self.host, "interrupt", Qt.DirectConnection, Q_RETURN_ARG("QVariant"))

    def _pointer(self):
        """The pointer near the companion: it looks at it; arriving ends an antic."""
        if self.view is None or not self.view.isVisible():
            return
        p = QCursor.pos()
        g = self.view.geometry()
        root = self.view.rootObject()
        cx = g.x() + float(root.property("boxX") or 0)
        cy = g.y() + float(root.property("boxY") or 0)
        dx, dy = p.x() - cx, p.y() - cy
        d = math.hypot(dx, dy) or 1.0
        near = max(0.0, min(1.0, 1 - (d - self.size * 0.6) / (self.size * 1.5)))
        if near > 0.02 or self._near_was > 0.02:
            mag = min(1.0, d / 140)
            self.host.setProperty("look", QPointF(dx / d * mag, dy / d * mag) if near > 0.02 else QPointF(0, 0))
            self.host.setProperty("near", near)
        if near > 0.55 and self._near_was <= 0.55:
            self.driver.pointer()
        self._near_was = near
