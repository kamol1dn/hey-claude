"""Notification-area (tray) icon and its menu.

No assistant logic lives here: the tray only shows state it is told about
(`set_listening`, `set_hotkey_label`) and emits signals when the user acts.
Left-click the icon = talk now; right-click = menu.
"""
from __future__ import annotations

import time

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

ACCENT = QColor("#D97757")   # same palette as island.py
DIM = QColor("#9A9A9A")
BG = QColor(20, 20, 20)

PAUSE_SECONDS = 3600


def _draw(size: int, on: bool) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    s = float(size)
    # dark rounded square
    bg = QPainterPath()
    bg.addRoundedRect(QRectF(0.5, 0.5, s - 1, s - 1), s * 0.24, s * 0.24)
    p.fillPath(bg, BG)
    p.setPen(QPen(QColor(255, 255, 255, 40), max(1.0, s / 32)))
    p.drawPath(bg)
    # dot
    r = s * 0.27
    c = QPointF(s / 2, s / 2)
    p.setPen(Qt.NoPen)
    p.setBrush(ACCENT if on else DIM)
    p.drawEllipse(c, r, r)
    if not on:
        # slash: a background-coloured cut with a grey stroke inside it
        a, b = QPointF(s * 0.22, s * 0.78), QPointF(s * 0.78, s * 0.22)
        p.setPen(QPen(BG, s * 0.16, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(a, b)
        p.setPen(QPen(DIM, s * 0.07, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(a, b)
    p.end()
    return pm


def make_icon(on: bool) -> QIcon:
    icon = QIcon()
    for size in (16, 32, 64):
        icon.addPixmap(_draw(size, on))
    return icon


def _pretty_hotkey(text: str) -> str:
    """'ctrl+alt+space' -> 'Ctrl+Alt+Space'; already-cased text is left alone."""
    if text and text == text.lower():
        return "+".join(part[:1].upper() + part[1:] for part in text.split("+"))
    return text


class Tray(QObject):
    talk_now = Signal()
    listening_toggled = Signal(bool)   # user flipped "Listening" (or chose "Resume listening" -> True)
    pause_requested = Signal(int)      # seconds
    settings_requested = Signal()
    logs_requested = Signal()
    record_samples_requested = Signal()
    quit_requested = Signal()

    def __init__(self, parent=None, hotkey_label: str = ""):
        super().__init__(parent)
        self._icons = {True: make_icon(True), False: make_icon(False)}
        self._on = True
        self._paused_until: float | None = None
        self._last_click = 0.0

        self.menu = QMenu()
        self.act_talk = QAction("Talk now", self.menu, triggered=lambda: self.talk_now.emit())
        self.act_listen = QAction("Listening", self.menu, checkable=True, checked=True)
        self.act_listen.triggered.connect(self._listen_clicked)   # user clicks only, not setChecked
        self.act_pause = QAction("Pause for 1 hour", self.menu, triggered=self._pause_clicked)
        self.act_settings = QAction("Settings…", self.menu, triggered=lambda: self.settings_requested.emit())
        self.act_logs = QAction("Open logs", self.menu, triggered=lambda: self.logs_requested.emit())
        self.act_record = QAction("Record wake samples…", self.menu,
                                  triggered=lambda: self.record_samples_requested.emit())
        self.act_quit = QAction("Quit", self.menu, triggered=lambda: self.quit_requested.emit())
        for a in (self.act_talk, self.act_listen, self.act_pause):
            self.menu.addAction(a)
        self.menu.addSeparator()
        self.menu.addAction(self.act_settings)
        self.menu.addAction(self.act_record)
        self.menu.addAction(self.act_logs)
        self.menu.addSeparator()
        self.menu.addAction(self.act_quit)

        self.icon = QSystemTrayIcon(self._icons[True], self)
        self.icon.setContextMenu(self.menu)
        self.icon.activated.connect(self._activated)
        self.icon.messageClicked.connect(self._balloon_clicked)
        self._balloon_click = None
        self.set_hotkey_label(hotkey_label)
        self.set_listening(True)
        self.icon.show()

    # -- state in ------------------------------------------------------------------
    def set_listening(self, on: bool, paused_until: float | None = None):
        paused = paused_until is not None and paused_until > time.time()
        self._on, self._paused_until = on, (paused_until if paused else None)
        active = on and not paused
        self.act_listen.setChecked(active)          # setChecked doesn't fire `triggered`
        if paused:
            self.act_pause.setText("Resume listening")
            self.act_pause.setEnabled(True)
            tip = f"hey-claude — paused until {time.strftime('%H:%M', time.localtime(paused_until))}"
        else:
            self.act_pause.setText("Pause for 1 hour")
            self.act_pause.setEnabled(active)
            tip = "hey-claude — listening" if active else "hey-claude — listening off"
        self.icon.setToolTip(tip)
        self.icon.setIcon(self._icons[active])

    def set_hotkey_label(self, text: str):
        text = _pretty_hotkey((text or "").strip())
        self.act_talk.setText(f"Talk now\t{text}" if text else "Talk now")

    def notify_action(self, title: str, msg: str, on_click):
        """A balloon that runs on_click when it's clicked (e.g. open Settings)."""
        self.notify(title, msg)
        self._balloon_click = on_click

    def _balloon_clicked(self):
        cb, self._balloon_click = getattr(self, "_balloon_click", None), None
        if cb:
            cb()

    def notify(self, title: str, msg: str):
        self._balloon_click = None
        self.icon.showMessage(title, msg, QSystemTrayIcon.Information, 5000)

    def hide(self):
        """Remove the icon now (otherwise Windows may leave a ghost until hovered)."""
        self.icon.hide()

    # -- user actions ------------------------------------------------------------
    def _listen_clicked(self):
        self.listening_toggled.emit(self.act_listen.isChecked())

    def _pause_clicked(self):
        if self._paused_until is not None:
            self.listening_toggled.emit(True)
        else:
            self.pause_requested.emit(PAUSE_SECONDS)

    def _activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            # A double-click also delivers a Trigger; only act once per gesture.
            now = time.monotonic()
            if now - self._last_click > 0.6:
                self._last_click = now
                self.talk_now.emit()
