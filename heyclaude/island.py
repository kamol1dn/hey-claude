"""The "island": a black pill at the top-centre of the screen.

States: loading, idle (tiny), off (muted: listening switched off, mic
closed), listening (level meter), thinking (dots + what you said), speaking
(the reply), and a task list that drops down under
the pill while tasks exist. Clicking a task opens its live log.

Left-click the idle or muted pill = talk without the wake word. Right-click = menu.
"""
from __future__ import annotations

import math
import os
import subprocess
import time
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QObject, QPointF, QRect, QRectF, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QAction, QColor, QCursor, QFont, QFontMetrics, QGuiApplication, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QMenu, QPlainTextEdit, QPushButton, QVBoxLayout,
                               QWidget)

ACCENT = QColor("#D97757")   # Claude orange
FG = QColor("#F2F2F2")
DIM = QColor("#9A9A9A")
OK = QColor("#6CCB7A")
BAD = QColor("#E5646A")


class Bridge(QObject):
    """Thread-safe way for worker threads to poke the UI (signals are queued)."""
    state = Signal(str, str)        # state, text
    level = Signal(float)
    tasks = Signal(list)            # list[dict]
    listening = Signal(bool, float)  # wake word on/off, paused-until epoch (0 = not paused)
    talk = Signal()                 # global hotkey pressed (from the hotkey thread)
    hush = Signal()                 # stop key pressed while it talks (from the hotkey thread)
    stt = Signal(str)               # active speech-to-text device, e.g. "gpu (turbo)"
    heard = Signal(str)             # the hotkey session's spoken request was transcribed ("" = nothing)
    partial = Signal(str)           # live transcript while you talk ("" = none yet)
    voice = Signal(str)             # active text-to-speech engine/device, e.g. "kokoro gpu"
    wake = Signal(str)              # the wake phrase that's loaded ("hey claude", or the fallback)
    wake_hit = Signal(float)        # the wake word fired (time.time()); "not me" becomes possible
    effect = Signal(dict)           # M9: a tool effect event (effects.py), e.g. volume 45 -> 30
    effect_art = Signal(str, object)  # M9: album art bytes for the media card (effect id, bytes)
    quit = Signal()


ROW_H = 30
PILL_H = 40
CTRL_W = 66        # mic + stop buttons on the right while it thinks or talks
BTN_R = 12
INPUT_W, INPUT_H, INPUT_LINE = 560, 46, 18


class _Entry(QPlainTextEdit):
    """The hotkey's text field: Enter sends, Shift+Enter is a new line, Esc cancels."""

    def __init__(self, island):
        super().__init__(island)
        self.island = island
        self.setPlaceholderText("Speak, or type and press Enter  ·  Esc to cancel")
        self.setFrameStyle(0)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setStyleSheet("QPlainTextEdit { background: transparent; color: #F2F2F2; border: none; "
                           "font-family: 'Segoe UI Variable Text'; font-size: 11pt; "
                           "selection-background-color: #D97757; }")
        self.hide()

    def keyPressEvent(self, e):
        s = self.island.session
        if s is not None:
            if e.key() in (Qt.Key_Return, Qt.Key_Enter):
                if s.key_enter(self.toPlainText(), bool(e.modifiers() & Qt.ShiftModifier)):
                    return
            elif e.key() == Qt.Key_Escape:
                print("[input] Esc")
                s.key_escape()
                return
        super().keyPressEvent(e)


class Island(QWidget):
    activate = Signal()             # user clicked the pill to talk
    stop_speaking = Signal()
    interrupt_talk = Signal()       # mic button while it talks: stop and listen to the user
    effect_undo = Signal(str)       # M9: the Undo button of an effect (its id)
    effect_confirm = Signal(str)    # M9: Confirm on a sleep / restart / shutdown request
    effect_cancel = Signal(str)
    quit_requested = Signal()
    not_me = Signal()               # "that wasn't me": the last wake was a false one

    NOT_ME_S = 120                  # how long after a wake the menu item stays available

    def __init__(self, bridge: Bridge, cfg: dict, task_manager=None, wake_label: str = "hey jarvis"):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                         | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.cfg = cfg
        self.tm = task_manager
        self.wake_label = wake_label
        self.mode, self.text = "loading", "starting up…"
        self.levels = [0.0] * 24
        self.task_rows: list[dict] = []
        self.show_tasks_until = 0.0
        self.hover = False
        self.log_windows: dict[str, LogWindow] = {}
        self.font_main = QFont("Segoe UI Variable Text", 10)
        self.font_small = QFont("Segoe UI Variable Text", 9)
        self.t0 = time.perf_counter()
        self.setMouseTracking(True)

        bridge.state.connect(self.set_state)
        bridge.level.connect(self.push_level)
        bridge.tasks.connect(self.set_tasks)
        bridge.stt.connect(self.set_stt)
        bridge.heard.connect(self._heard)
        bridge.partial.connect(self.set_partial)
        bridge.voice.connect(self.set_voice)
        bridge.wake.connect(self.set_wake_label)
        bridge.wake_hit.connect(self._wake_hit)
        self.last_wake_t = 0.0          # when the wake word last fired (wall clock)
        self.wake_listening = False     # the current "listening" came from the wake word
        from .island_effects import EffectPlayer
        self.fx = EffectPlayer()                    # M9 tool effects: one row under the head
        bridge.effect.connect(self.show_effect)
        bridge.effect_art.connect(self._effect_art)
        self.stt_label = ""
        self.voice_label = ""
        self.partial = ""               # what you've said so far (listening modes)

        # Hotkey "talk or type" field (see inputbox.py); attach_input() turns it on.
        self.session = None
        self.input_open = False
        self._prev_fg = 0
        self._restore_focus = True
        self._setting_text = False
        self.entry = _Entry(self)
        self.entry.textChanged.connect(self._entry_changed)

        self.anim = QVariantAnimation(self, duration=260, easingCurve=QEasingCurve.OutCubic)
        self.anim.valueChanged.connect(self._apply_geom)
        self.cur = QRectF(0, 0, 150, 30)
        self.tick = QTimer(self, interval=33, timeout=self._tick)
        self.tick.start()
        self._place(immediate=True)
        self.show()

    # -- geometry ------------------------------------------------------------
    def _screen_rect(self) -> QRect:
        screens = QGuiApplication.screens()
        sel = self.cfg.get("screen", "primary")
        s = screens[int(sel)] if str(sel).isdigit() and int(sel) < len(screens) else QGuiApplication.primaryScreen()
        return s.geometry()

    def _top_band(self) -> int:
        """Height of a taskbar docked at the top of the island's screen (0 if none)."""
        screens = QGuiApplication.screens()
        sel = self.cfg.get("screen", "primary")
        s = screens[int(sel)] if str(sel).isdigit() and int(sel) < len(screens) else QGuiApplication.primaryScreen()
        return max(0, s.availableGeometry().y() - s.geometry().y())

    def _top_offset(self, h: int) -> int:
        """`island.top_margin`: a number of px, or "auto" (default): centred in a top taskbar, else 8 px."""
        m = self.cfg.get("top_margin", "auto")
        if str(m).strip().lstrip("-").isdigit():
            return int(m)
        band = self._top_band()
        return max(0, (band - h) // 2) if band >= 20 else 8

    # -- hotkey input field ------------------------------------------------------
    def attach_input(self, open_mic, cancel_mic, submit, closed=None):
        """Enable the hotkey's talk-or-type field. Callbacks go to the assistant."""
        from .inputbox import InputSession
        self._on_closed = closed
        self.session = InputSession(open_mic, cancel_mic, submit, show=self._show_input, close=self._hide_input)

    def toggle_input(self):
        if self.session is not None:
            if self.session.is_open:
                print("[input] hotkey pressed again: closing the field")
            self.session.hotkey()

    def _show_input(self):
        from .winsys import force_foreground, foreground_window
        self._prev_fg = foreground_window()
        self._restore_focus = True
        self._away_since = 0.0
        self._opened_at = time.monotonic()
        self.input_open = True
        self._setting_text = True
        self.entry.clear()
        self._setting_text = False
        self.setAttribute(Qt.WA_ShowWithoutActivating, False)
        self._place(immediate=True)
        self.entry.show()
        self.show()
        self.raise_()
        self.activateWindow()
        ok = force_foreground(int(self.winId()))
        self.entry.setFocus(Qt.ShortcutFocusReason)
        print(f"[input] open (focus {'ok' if ok else 'NOT taken'})")
        self.update()

    def _hide_input(self, submitted: bool):
        from .winsys import restore_foreground
        self.input_open = False
        self.entry.hide()
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        if getattr(self, "_on_closed", None):
            self._on_closed()
        if self._restore_focus and self._prev_fg:
            restore_foreground(self._prev_fg)
        self._prev_fg = 0
        print(f"[input] {'sent' if submitted else 'cancelled'}")
        self._place()
        self.update()

    def _entry_changed(self):
        if self._setting_text or self.session is None:
            return
        self.session.edited(self.entry.toPlainText())
        self._place(immediate=True)
        self.update()

    def _heard(self, text: str):
        if self.session is None or not self.input_open:
            return
        if text.strip():
            self._setting_text = True
            self.entry.setPlainText(text)           # what was heard, for a moment
            self._setting_text = False
        self.session.heard(text)
        self.update()

    def _check_focus(self):
        """Clicking somewhere else closes the field without pulling focus back.
        Uses Windows' foreground window: Qt's isActiveWindow() can lag behind
        a SetForegroundWindow done outside Qt."""
        from .winsys import foreground_is_ours, foreground_window, window_title
        if foreground_is_ours():                 # the island, or another hey-claude window
            self._away_since = 0.0
            return
        now = time.monotonic()
        if not self._away_since:
            self._away_since = now
        elif now - self._away_since > 0.3 and self.session is not None:
            self._away_since = 0.0
            print(f"[input] focus moved to {window_title(foreground_window())!r}: closing the field")
            self._restore_focus = False
            self.session.cancel()

    def _input_size(self) -> tuple[int, int]:
        lines = max(1, min(4, self.entry.document().blockCount()))
        return INPUT_W, INPUT_H + (lines - 1) * INPUT_LINE

    def _target_size(self) -> tuple[int, int]:
        if self.input_open:
            return self._input_size()
        fm = QFontMetrics(self.font_main)
        n_running = sum(1 for t in self.task_rows if t["status"] in ("starting", "running"))
        if self.mode == "idle":
            label = self._idle_label()
            w, h = max(120, QFontMetrics(self.font_small).horizontalAdvance(label) + 46), 28
        elif self.mode == "off":
            w, h = (170 if n_running or "until" in self.text else 130), 28
        elif self.mode in ("listening", "holding", "followup"):
            w, h = 300, PILL_H
        elif self.mode in ("thinking", "speaking", "loading", "error"):
            w = min(560, max(300, fm.horizontalAdvance(self.text) + 80))
            if self.mode in ("thinking", "speaking"):
                w += CTRL_W                     # room for the mic and stop buttons
            h = PILL_H if fm.horizontalAdvance(self.text) < 480 else PILL_H + 20
        else:
            w, h = 200, PILL_H
        if self.fx.visible():
            from .island_effects import EFFECT_H
            w = max(w, self.fx.width())
            h = (h if h > PILL_H or self.mode in ("idle", "off") else PILL_H) + EFFECT_H
        if self._tasks_visible():
            w = max(w, 460)
            h += 6 + ROW_H * len(self._visible_rows()) + 6
        return w, h

    def _visible_rows(self):
        return self.task_rows[-5:]

    def _tasks_visible(self):
        if not self.task_rows:
            return False
        return self.hover or time.time() < self.show_tasks_until

    def _place(self, immediate=False):
        w, h = self._target_size()
        sr = self._screen_rect()
        target = QRectF(sr.x() + (sr.width() - w) / 2, sr.y() + self._top_offset(h), w, h)
        if immediate:
            self._apply_geom(target)
            return
        if self.anim.endValue() == target and self.anim.state() == QVariantAnimation.Running:
            return
        self.anim.stop()
        self.anim.setStartValue(QRectF(self.geometry()))
        self.anim.setEndValue(target)
        self.anim.start()

    def _apply_geom(self, r):
        self.setGeometry(QRect(round(r.x()), round(r.y()), round(r.width()), round(r.height())))
        self.update()

    # -- inputs from the assistant --------------------------------------------
    def set_state(self, mode: str, text: str):
        if mode not in ("listening", "holding", "followup"):
            self.partial = ""
        if mode != "listening":
            self.wake_listening = False
        self.mode, self.text = mode, text
        self.mode_at = time.perf_counter()
        self._place()
        self.update()
        snap = os.environ.get("HEYCLAUDE_SNAP")   # debug: save a png of each state
        if snap:
            QTimer.singleShot(450, lambda m=mode: self.grab().save(
                os.path.join(snap, f"{time.strftime('%H%M%S')}-{m}.png")))

    def set_stt(self, label: str):
        self.stt_label = label
        self._tooltip()
        self._place()
        self.update()

    def set_wake_label(self, label: str):
        self.wake_label = label
        self._place()
        self.update()

    def _wake_hit(self, t: float):
        self.last_wake_t = t
        self.wake_listening = True

    def _not_me_rect(self, head=None):
        """The "not me" chip at the right end of the head row while listening after a wake."""
        h = head.height() if head is not None else self._head_h()
        cy = min(h, PILL_H) / 2
        return QRectF(self.width() - 14 - 62, cy - 11, 62, 22)

    def _show_not_me(self) -> bool:
        return self.mode == "listening" and self.wake_listening and not self.partial

    def flash(self, text: str, secs: float = 2.5):
        """A short note in the idle pill (e.g. after "that wasn't me")."""
        self.flash_text, self.flash_until = text, time.time() + secs
        self._place()
        self.update()
        QTimer.singleShot(int(secs * 1000) + 50, lambda: (self._place(), self.update()))

    flash_text, flash_until = "", 0.0

    def set_voice(self, label: str):
        self.voice_label = label
        self._tooltip()

    # -- M9 effects --------------------------------------------------------------------
    def show_effect(self, ev: dict):
        """A tool changed something: show it (volume slider, sun/moon flip, ...) for ~8 s."""
        self.fx.show(ev)
        self._place()
        self.update()
        snap = os.environ.get("HEYCLAUDE_SNAP")   # debug: a png mid-entry and once settled
        if snap:
            for ms, tag in ((330, "a"), (1100, "b")):
                QTimer.singleShot(ms, lambda t=tag, e=ev["effect"]: self.grab().save(
                    os.path.join(snap, f"{time.strftime('%H%M%S')}-effect-{e}{'-undo' if ev.get('reverse') else ''}-{t}.png")))

    def _effect_art(self, effect_id: str, data):
        self.fx.set_art(effect_id, data)
        self.update()

    def _effect_rect(self) -> QRectF:
        from .island_effects import EFFECT_H
        return QRectF(0, self._head_h(), self.width(), EFFECT_H)

    def _tooltip(self):
        lines = (f"speech-to-text: {self.stt_label}" if self.stt_label else "",
                 f"voice: {self.voice_label}" if self.voice_label else "")
        self.setToolTip("\n".join(x for x in lines if x))

    def set_partial(self, text: str):
        """Live transcript while listening (M4 hook; drawn after the level meter)."""
        self.partial = text if self.mode in ("listening", "holding", "followup") else ""
        self.update()

    def _stt_short(self) -> str:
        # "gpu (turbo)" -> "gpu"; "cpu (gpu busy 83%)" -> "cpu · gpu busy 83%"
        dev, _, inside = self.stt_label.partition(" (")
        inside = inside.rstrip(")").replace("fallback: ", "")
        return f"{dev} · {inside}" if " " in inside or inside == "premiere" else dev

    def push_level(self, v: float):
        self.levels = self.levels[1:] + [v]

    def set_tasks(self, rows: list):
        before = {r["id"]: r["status"] for r in self.task_rows}
        self.task_rows = rows
        # Pop the list open for a few seconds when something starts or finishes.
        if any(before.get(r["id"]) != r["status"] for r in rows):
            self.show_tasks_until = time.time() + 6
        for r in rows:
            lw = self.log_windows.get(r["id"])
            if lw:
                lw.update_task(r)
        self._place()
        self.update()

    def _tick(self):
        if self.input_open and time.monotonic() - self._opened_at > 0.5:
            self._check_focus()
        busy = self.input_open or self.mode not in ("idle", "off") or any(t["status"] in ("starting", "running") for t in self.task_rows)
        if self.fx.ev is not None:
            now = time.perf_counter()
            if self.fx.expired(now):
                self.fx.clear()
                self._place()
                self.update()
            elif not busy and self.fx.needs_frame(now):
                self.update()               # entry at the tick rate, then 4 fps for the countdown only
        was = getattr(self, "_was_visible", False)
        vis = self._tasks_visible()
        if vis != was:
            self._was_visible = vis
            self._place()
        if busy:
            self.update()

    # -- painting -------------------------------------------------------------
    def _paint_input(self, p, t):
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(r, 20, 20)
        p.fillPath(path, QColor(8, 8, 8, 248))
        p.setPen(QPen(QColor(217, 119, 87, 120), 1))
        p.drawPath(path)
        cy = INPUT_H / 2
        if self.session is not None and self.session.mic_on:     # mic open: pulsing dot + level
            lvl = max(self.levels[-3:]) if self.levels else 0.0
            self._dot(p, 22, cy, 4 + 1.5 * math.sin(t * 6) + 4 * lvl, ACCENT)
        else:                                                    # typing: mic off
            self._dot(p, 22, cy, 4, QColor(110, 110, 110))
            p.setPen(QPen(QColor(8, 8, 8), 2.2))
            p.drawLine(17, int(cy + 5), 27, int(cy - 5))
            p.setPen(QPen(DIM, 1.3))
            p.drawLine(17, int(cy + 5), 27, int(cy - 5))
        self.entry.setGeometry(44, 11, self.width() - 60, self.height() - 16)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self.input_open:
            self._paint_input(p, time.perf_counter() - self.t0)
            return
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        rad = min(20.0, r.height() / 2) if not (self._tasks_visible() or self.fx.visible()) else 18.0
        path = QPainterPath()
        path.addRoundedRect(r, rad, rad)
        p.fillPath(path, QColor(8, 8, 8, 245))
        p.setPen(QPen(QColor(255, 255, 255, 22), 1))
        p.drawPath(path)

        top_h = 28 if self.mode in ("idle", "off") else (PILL_H if self.height() < PILL_H + 30 or self._tasks_visible() is False else PILL_H)
        fx_on = self.fx.visible()
        expanded = self._tasks_visible() or fx_on
        head = QRectF(0, 0, self.width(), min(self.height(), top_h if not expanded else self._head_h()))
        t = time.perf_counter() - self.t0
        getattr(self, "_paint_" + self.mode, self._paint_text)(p, head, t)
        below = head.bottom()
        if fx_on:
            fr = self._effect_rect()
            p.save()
            p.setClipPath(path)
            self.fx.paint(p, fr, time.perf_counter())
            p.restore()
            below = fr.bottom()
        if self._tasks_visible():
            self._paint_tasks(p, below + 6, t)
        flash = self.fx.flash_alpha(time.perf_counter())
        if flash > 0:                                   # screenshot: a shutter flash over the pill
            p.fillPath(path, QColor(255, 255, 255, int(255 * flash)))

    def _head_h(self):
        fm = QFontMetrics(self.font_main)
        if self.mode in ("idle", "off"):
            return 28
        if self.mode in ("thinking", "speaking", "loading", "error") and fm.horizontalAdvance(self.text) >= 480:
            return PILL_H + 20
        return PILL_H

    def _dot(self, p, x, y, r, c):
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        p.drawEllipse(QRectF(x - r, y - r, 2 * r, 2 * r))

    def _idle_label(self) -> str:
        if time.time() < self.flash_until:
            return self.flash_text
        running = [x for x in self.task_rows if x["status"] in ("starting", "running")]
        if running:
            return f"{len(running)} task{'s' if len(running) != 1 else ''} running"
        return self.wake_label + (f" · {self._stt_short()}" if self.stt_label else "")

    def _paint_idle(self, p, head, t):
        cy = head.center().y()
        self._dot(p, 18, cy, 4, ACCENT)
        running = [x for x in self.task_rows if x["status"] in ("starting", "running")]
        p.setFont(self.font_small)
        p.setPen(DIM if not running else FG)
        label = self._idle_label()
        p.drawText(QRectF(30, 0, head.width() - 38, head.height()), Qt.AlignVCenter | Qt.AlignLeft, label)

    def _paint_off(self, p, head, t):
        cy = head.center().y()
        self._dot(p, 18, cy, 4, QColor(110, 110, 110))
        p.setPen(QPen(QColor(8, 8, 8), 2.2))           # the "muted" slash
        p.drawLine(13, int(cy + 5), 23, int(cy - 5))
        p.setPen(QPen(DIM, 1.3))
        p.drawLine(13, int(cy + 5), 23, int(cy - 5))
        running = [x for x in self.task_rows if x["status"] in ("starting", "running")]
        p.setFont(self.font_small)
        p.setPen(DIM)
        label = f"off · {len(running)} running" if running else (self.text or "listening off")
        p.drawText(QRectF(30, 0, head.width() - 38, head.height()), Qt.AlignVCenter | Qt.AlignLeft, label)

    def _paint_listening(self, p, head, t):
        cy = head.center().y()
        pulse = 4 + 1.5 * math.sin(t * 6)
        self._dot(p, 20, cy, pulse, ACCENT)
        n = len(self.levels)
        bw = 5
        x0 = 40
        p.setBrush(FG)
        for i, v in enumerate(self.levels):
            hh = 3 + v * (head.height() - 16)
            p.drawRoundedRect(QRectF(x0 + i * (bw + 3), cy - hh / 2, bw, hh), 2, 2)
        p.setFont(self.font_small)
        p.setPen(DIM)
        tx = x0 + n * (bw + 3) + 4
        if self.partial:                 # live transcript, newest words visible
            avail = head.width() - tx - 10
            text = QFontMetrics(self.font_small).elidedText(self.partial, Qt.ElideLeft, int(avail))
            p.setPen(FG)
            p.drawText(QRectF(tx, 0, avail, head.height()), Qt.AlignVCenter | Qt.AlignLeft, text)
        else:
            p.drawText(QRectF(tx, 0, 60, head.height()), Qt.AlignVCenter, "listening")
        if self._show_not_me():
            r = self._not_me_rect(head)
            hover = self.mapFromGlobal(QCursor.pos()) if self.hover else None
            on = hover is not None and r.contains(hover.toPointF())
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 46 if on else 20))
            p.drawRoundedRect(r, 11, 11)
            p.setPen(FG if on else DIM)
            p.drawText(r, Qt.AlignCenter, "not me")

    def _paint_holding(self, p, head, t):
        """Hold key down: still listening, pauses don't end it. Ctrl+Shift: muted, still holding."""
        cy = head.center().y()
        muted = self.text == "muted"
        ring = DIM if muted else ACCENT
        self._dot(p, 20, cy, 5, ring)
        p.setPen(QPen(ring, 1.5))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QRectF(20 - 9, cy - 9, 18, 18))
        if muted:                                   # the slash, like the muted pill
            p.setPen(QPen(QColor(8, 8, 8), 2.2))
            p.drawLine(QPointF(14, cy - 6), QPointF(26, cy + 6))
        bw, x0 = 5, 40
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(110, 110, 110) if muted else FG)
        for i, v in enumerate(self.levels):
            hh = 3 if muted else 3 + v * (head.height() - 16)
            p.drawRoundedRect(QRectF(x0 + i * (bw + 3), cy - hh / 2, bw, hh), 2, 2)
        p.setFont(self.font_small)
        p.setPen(DIM if muted else ACCENT)
        p.drawText(QRectF(x0 + len(self.levels) * (bw + 3) + 4, 0, 70, head.height()), Qt.AlignVCenter,
                   "muted" if muted else "holding")

    def _paint_followup(self, p, head, t):
        """Listening again without the wake word; the ring drains as the window runs out."""
        cy = head.center().y()
        self._dot(p, 20, cy, 4, ACCENT)
        try:
            total = float(self.text or 5)
        except ValueError:
            total = 5.0
        left = max(0.0, 1 - (time.perf_counter() - getattr(self, "mode_at", 0)) / max(total, 0.1))
        p.setPen(QPen(ACCENT, 1.6))
        p.setBrush(Qt.NoBrush)
        p.drawArc(QRectF(20 - 9, cy - 9, 18, 18), 90 * 16, int(left * 360 * 16))
        bw, x0 = 5, 40
        p.setPen(Qt.NoPen)
        p.setBrush(FG)
        for i, v in enumerate(self.levels):
            hh = 3 + v * (head.height() - 16)
            p.drawRoundedRect(QRectF(x0 + i * (bw + 3), cy - hh / 2, bw, hh), 2, 2)
        p.setFont(self.font_small)
        p.setPen(DIM)
        p.drawText(QRectF(x0 + len(self.levels) * (bw + 3) + 4, 0, 70, head.height()), Qt.AlignVCenter, "go on…")

    def _paint_thinking(self, p, head, t):
        cy = head.center().y()
        for i in range(3):
            a = 0.35 + 0.65 * max(0.0, math.sin(t * 5 - i * 0.7))
            c = QColor(ACCENT)
            c.setAlphaF(a)
            self._dot(p, 16 + i * 10, cy, 3.2, c)
        self._text(p, QRectF(54, 0, head.width() - 66 - CTRL_W, head.height()), self.text, DIM)
        self._paint_controls(p, head)

    def _paint_speaking(self, p, head, t):
        cy = head.center().y()
        p.setPen(Qt.NoPen)
        p.setBrush(ACCENT)
        for i in range(4):
            hh = 6 + 10 * abs(math.sin(t * 7 + i * 1.3))
            p.drawRoundedRect(QRectF(14 + i * 7, cy - hh / 2, 4, hh), 2, 2)
        self._text(p, QRectF(54, 0, head.width() - 66 - CTRL_W, head.height()), self.text, FG)
        self._paint_controls(p, head)

    def _ctrl_rects(self, head=None):
        """(mic, stop) button circles at the right end of the head row."""
        h = head.height() if head is not None else self._head_h()
        cy = min(h, PILL_H) / 2
        stop = QRectF(self.width() - 14 - 2 * BTN_R, cy - BTN_R, 2 * BTN_R, 2 * BTN_R)
        mic = stop.translated(-(2 * BTN_R + 6), 0)
        return mic, stop

    def _paint_controls(self, p, head):
        mic, stop = self._ctrl_rects(head)
        hover = self.mapFromGlobal(QCursor.pos()) if self.hover else None
        for r in (mic, stop):
            on = hover is not None and r.contains(hover.toPointF())
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 46 if on else 20))
            p.drawEllipse(r)
        # mic: capsule + cradle + stem
        c = mic.center()
        p.setBrush(FG)
        p.drawRoundedRect(QRectF(c.x() - 3, c.y() - 7, 6, 10), 3, 3)
        p.setPen(QPen(FG, 1.6))
        p.setBrush(Qt.NoBrush)
        p.drawArc(QRectF(c.x() - 6, c.y() - 4, 12, 9), 200 * 16, 140 * 16)
        p.drawLine(QPointF(c.x(), c.y() + 5), QPointF(c.x(), c.y() + 8))
        # stop: rounded square
        s = stop.center()
        p.setPen(Qt.NoPen)
        p.setBrush(FG)
        p.drawRoundedRect(QRectF(s.x() - 5, s.y() - 5, 10, 10), 2, 2)

    def mouseMoveEvent(self, e):
        if self.fx.visible():
            hov = self.fx.hit(self._effect_rect(), e.position())
            if hov != self.fx.hover:
                self.fx.hover = hov
                self.setCursor(Qt.PointingHandCursor if hov else Qt.ArrowCursor)
                self.update()
        if self.mode in ("thinking", "speaking", "listening"):
            self.update()                       # button hover highlight

    def _paint_loading(self, p, head, t):
        self._paint_thinking(p, head, t)

    def _paint_error(self, p, head, t):
        self._dot(p, 20, head.center().y(), 5, BAD)
        self._text(p, QRectF(36, 0, head.width() - 48, head.height()), self.text, FG)

    def _paint_text(self, p, head, t):
        self._text(p, QRectF(16, 0, head.width() - 32, head.height()), self.text, FG)

    def _text(self, p, rect, text, color):
        p.setFont(self.font_main)
        p.setPen(color)
        fm = QFontMetrics(self.font_main)
        if rect.height() > PILL_H:
            p.drawText(rect.adjusted(0, 6, 0, -6), Qt.AlignVCenter | Qt.TextWordWrap,
                       fm.elidedText(text, Qt.ElideRight, int(rect.width() * 2 - 20)))
        else:
            p.drawText(rect, Qt.AlignVCenter, fm.elidedText(text, Qt.ElideRight, int(rect.width())))

    def _paint_tasks(self, p, y, t):
        p.setPen(QPen(QColor(255, 255, 255, 25), 1))
        p.drawLine(14, int(y - 3), self.width() - 14, int(y - 3))
        fm = QFontMetrics(self.font_small)
        for i, r in enumerate(self._visible_rows()):
            ry = y + i * ROW_H
            cy = ry + ROW_H / 2
            st = r["status"]
            if st in ("starting", "running"):
                p.setPen(QPen(ACCENT, 2))
                p.setBrush(Qt.NoBrush)
                a = int((t * 360) % 360) * 16
                p.drawArc(QRectF(14, cy - 6, 12, 12), -a, 270 * 16)
            else:
                self._dot(p, 20, cy, 4, OK if st == "done" else (BAD if st == "failed" else DIM))
            p.setFont(self.font_small)
            p.setPen(FG)
            name = f"{r['project']}: {r['prompt']}"
            p.drawText(QRectF(34, ry, 230, ROW_H), Qt.AlignVCenter, fm.elidedText(name, Qt.ElideRight, 230))
            p.setPen(DIM)
            right = r.get("activity") if st in ("starting", "running") else (r.get("summary") or st)
            el = r.get("elapsed", "")
            p.drawText(QRectF(270, ry, self.width() - 270 - 52, ROW_H), Qt.AlignVCenter,
                       fm.elidedText(right or st, Qt.ElideRight, self.width() - 270 - 52))
            p.drawText(QRectF(self.width() - 50, ry, 38, ROW_H), Qt.AlignVCenter | Qt.AlignRight, el)

    # -- mouse ----------------------------------------------------------------
    def enterEvent(self, _):
        self.hover = True
        self._place()

    def leaveEvent(self, _):
        self.hover = False
        QTimer.singleShot(400, self._place)

    def mousePressEvent(self, e):
        pos = e.position()
        if e.button() == Qt.RightButton:
            self._menu(e.globalPosition().toPoint())
            return
        head_h = self._head_h()
        if self.fx.visible():
            fr = self._effect_rect()
            if fr.contains(pos):
                hit = self.fx.hit(fr, pos)
                ev_id = self.fx.ev["id"]
                if hit:
                    print(f"[effects] {hit} clicked")
                    {"undo": self.effect_undo, "confirm": self.effect_confirm, "cancel": self.effect_cancel}[hit].emit(ev_id)
                    self.fx.clear()
                    self._place()
                    self.update()
                return
            head_h = fr.bottom()
        if self._tasks_visible() and pos.y() > head_h + 6:
            i = int((pos.y() - head_h - 6) // ROW_H)
            rows = self._visible_rows()
            if 0 <= i < len(rows):
                self.open_log(rows[i])
            return
        if self._show_not_me() and self._not_me_rect().adjusted(-3, -3, 3, 3).contains(pos):
            self.wake_listening = False
            self.not_me.emit()
            return
        if self.mode in ("thinking", "speaking") and pos.y() <= PILL_H:
            mic, stop = self._ctrl_rects()
            if mic.adjusted(-3, -3, 3, 3).contains(pos):
                self.interrupt_talk.emit()
                return
            if stop.adjusted(-3, -3, 3, 3).contains(pos):
                self.stop_speaking.emit()
                return
        if self.mode == "speaking":
            self.stop_speaking.emit()
        elif self.mode in ("idle", "off"):
            self.activate.emit()

    def _menu(self, at):
        m = QMenu(self)
        m.addAction("Talk now", self.activate.emit)
        m.addAction("Stop speaking", self.stop_speaking.emit)
        if self.last_wake_t and time.time() - self.last_wake_t < self.NOT_ME_S:
            a = m.addAction(f"That wasn't me (the wake {int(time.time() - self.last_wake_t)} s ago)", self.not_me.emit)
            a.setToolTip("Saves the audio that woke it as a \"not the wake word\" example for the next "
                         "Settings → Wake word tune-up.")
        if self.stt_label:
            m.addAction(f"Speech-to-text: {self.stt_label}").setEnabled(False)
        if self.voice_label:
            m.addAction(f"Voice: {self.voice_label}").setEnabled(False)
        m.addSeparator()
        m.addAction("Open logs folder", lambda: os.startfile(str(Path(__file__).resolve().parent.parent / "state" / "logs")))
        m.addSeparator()
        m.addAction("Quit", self.quit_requested.emit)
        m.exec(at)

    def open_log(self, row: dict):
        w = self.log_windows.get(row["id"])
        if w is None:
            w = LogWindow(row, self.tm)
            self.log_windows[row["id"]] = w
        w.show()
        w.raise_()
        w.activateWindow()


class LogWindow(QWidget):
    def __init__(self, row: dict, tm=None):
        super().__init__(None, Qt.Window)
        self.row, self.tm = row, tm
        self.setWindowTitle(f"{row['project']} — {row['prompt'][:60]}")
        self.resize(760, 520)
        self.setStyleSheet("""
            QWidget { background: #141414; color: #EDEDED; font-family: 'Segoe UI'; }
            QPlainTextEdit { background: #0C0C0C; border: 1px solid #2A2A2A; font-family: Consolas, monospace; font-size: 10pt; }
            QPushButton { background: #262626; border: 1px solid #3A3A3A; padding: 6px 10px; border-radius: 6px; }
            QPushButton:hover { background: #333; }
        """)
        lay = QVBoxLayout(self)
        self.view = QPlainTextEdit(readOnly=True)
        lay.addWidget(self.view)
        bar = QHBoxLayout()
        self.b_copy = QPushButton("Copy resume command", clicked=self.copy_resume)
        self.b_term = QPushButton("Resume in terminal", clicked=self.resume_terminal)
        self.b_stop = QPushButton("Stop task", clicked=self.stop_task)
        self.b_folder = QPushButton("Open folder", clicked=lambda: os.startfile(self.row["path"]))
        for b in (self.b_copy, self.b_term, self.b_folder, self.b_stop):
            bar.addWidget(b)
        bar.addStretch()
        lay.addLayout(bar)
        self._last = ""
        self.timer = QTimer(self, interval=700, timeout=self.refresh)
        self.timer.start()
        self.update_task(row)
        self.refresh()

    def update_task(self, row):
        self.row = row
        running = row["status"] in ("starting", "running")
        self.b_stop.setEnabled(running)
        self.b_copy.setEnabled(bool(row.get("session_id")))
        self.b_term.setEnabled(bool(row.get("session_id")) and not running)

    def refresh(self):
        try:
            txt = Path(self.row["log"]).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        if txt != self._last:
            at_end = self.view.verticalScrollBar().value() >= self.view.verticalScrollBar().maximum() - 4
            self._last = txt
            self.view.setPlainText(txt)
            if at_end:
                self.view.verticalScrollBar().setValue(self.view.verticalScrollBar().maximum())

    def copy_resume(self):
        QApplication.clipboard().setText(f'cd /d "{self.row["path"]}" && claude --resume {self.row["session_id"]}')

    def resume_terminal(self):
        exe = self.tm.exe if self.tm else "claude"
        sid = self.row["session_id"]
        # A new console window running the interactive session in the task's folder.
        from .claude_cli import child_env
        subprocess.Popen(["cmd", "/k", exe, "--resume", sid], cwd=self.row["path"], env=child_env(),
                         creationflags=subprocess.CREATE_NEW_CONSOLE)

    def stop_task(self):
        if self.tm:
            self.tm.stop(self.row["id"])
