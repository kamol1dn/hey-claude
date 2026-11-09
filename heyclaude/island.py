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

from PySide6.QtCore import QEasingCurve, QObject, QRect, QRectF, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QAction, QColor, QFont, QFontMetrics, QGuiApplication, QPainter, QPainterPath, QPen
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
    stt = Signal(str)               # active speech-to-text device, e.g. "gpu (turbo)"
    quit = Signal()


ROW_H = 30
PILL_H = 40


class Island(QWidget):
    activate = Signal()             # user clicked the pill to talk
    stop_speaking = Signal()
    quit_requested = Signal()

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
        self.stt_label = ""

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

    def _target_size(self) -> tuple[int, int]:
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
            h = PILL_H if fm.horizontalAdvance(self.text) < 480 else PILL_H + 20
        else:
            w, h = 200, PILL_H
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
        target = QRectF(sr.x() + (sr.width() - w) / 2, sr.y() + int(self.cfg.get("top_margin", 8)), w, h)
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
        self.setToolTip(f"speech-to-text: {label}")
        self._place()
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
        busy = self.mode not in ("idle", "off") or any(t["status"] in ("starting", "running") for t in self.task_rows)
        was = getattr(self, "_was_visible", False)
        vis = self._tasks_visible()
        if vis != was:
            self._was_visible = vis
            self._place()
        if busy:
            self.update()

    # -- painting -------------------------------------------------------------
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        rad = min(20.0, r.height() / 2) if not self._tasks_visible() else 18.0
        path = QPainterPath()
        path.addRoundedRect(r, rad, rad)
        p.fillPath(path, QColor(8, 8, 8, 245))
        p.setPen(QPen(QColor(255, 255, 255, 22), 1))
        p.drawPath(path)

        top_h = 28 if self.mode in ("idle", "off") else (PILL_H if self.height() < PILL_H + 30 or self._tasks_visible() is False else PILL_H)
        head = QRectF(0, 0, self.width(), min(self.height(), top_h if not self._tasks_visible() else self._head_h()))
        t = time.perf_counter() - self.t0
        getattr(self, "_paint_" + self.mode, self._paint_text)(p, head, t)
        if self._tasks_visible():
            self._paint_tasks(p, head.bottom() + 6, t)

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
        p.drawText(QRectF(x0 + n * (bw + 3) + 4, 0, 60, head.height()), Qt.AlignVCenter, "listening")

    def _paint_holding(self, p, head, t):
        """Hold key down: still listening, pauses don't end it."""
        cy = head.center().y()
        self._dot(p, 20, cy, 5, ACCENT)
        p.setPen(QPen(ACCENT, 1.5))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QRectF(20 - 9, cy - 9, 18, 18))
        bw, x0 = 5, 40
        p.setPen(Qt.NoPen)
        p.setBrush(FG)
        for i, v in enumerate(self.levels):
            hh = 3 + v * (head.height() - 16)
            p.drawRoundedRect(QRectF(x0 + i * (bw + 3), cy - hh / 2, bw, hh), 2, 2)
        p.setFont(self.font_small)
        p.setPen(ACCENT)
        p.drawText(QRectF(x0 + len(self.levels) * (bw + 3) + 4, 0, 60, head.height()), Qt.AlignVCenter, "holding")

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
        self._text(p, QRectF(54, 0, head.width() - 66, head.height()), self.text, DIM)

    def _paint_speaking(self, p, head, t):
        cy = head.center().y()
        p.setPen(Qt.NoPen)
        p.setBrush(ACCENT)
        for i in range(4):
            hh = 6 + 10 * abs(math.sin(t * 7 + i * 1.3))
            p.drawRoundedRect(QRectF(14 + i * 7, cy - hh / 2, 4, hh), 2, 2)
        self._text(p, QRectF(54, 0, head.width() - 66, head.height()), self.text, FG)

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
        if self._tasks_visible() and pos.y() > head_h + 6:
            i = int((pos.y() - head_h - 6) // ROW_H)
            rows = self._visible_rows()
            if 0 <= i < len(rows):
                self.open_log(rows[i])
            return
        if self.mode == "speaking":
            self.stop_speaking.emit()
        elif self.mode in ("idle", "off"):
            self.activate.emit()

    def _menu(self, at):
        m = QMenu(self)
        m.addAction("Talk now", self.activate.emit)
        m.addAction("Stop speaking", self.stop_speaking.emit)
        if self.stt_label:
            m.addAction(f"Speech-to-text: {self.stt_label}").setEnabled(False)
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
        subprocess.Popen(["cmd", "/k", exe, "--resume", sid], cwd=self.row["path"],
                         creationflags=subprocess.CREATE_NEW_CONSOLE)

    def stop_task(self):
        if self.tm:
            self.tm.stop(self.row["id"])
