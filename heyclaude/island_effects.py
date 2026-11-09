"""Painting the M9 tool effects on the QPainter island.

The data comes from effects.py (the contract M5's QML island will reuse);
this module only draws it. An effect shows as one row under the island's
head for ~8 s: an icon, the change (old -> new), and an Undo button.

Motion follows docs/island-motion.md: growing values "inhale" (a small
overshoot), shrinking ones "exhale" (no overshoot); text rolls (old up and
out, new in from below); an undo plays the same effect backwards. Nothing
animates once the ~0.6 s entry is over: the hold is a still frame plus a
countdown hairline redrawn at 4 fps, and the row is gone after its ttl.
Brightness "rays" are a ring of dots, so nothing reads as an asterisk or a
logo (island-motion.md section 1).
"""
from __future__ import annotations

import math
import time

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainterPath, QPen, QPixmap

LIVE = QColor("#D97757")
HOT = QColor("#EC9A78")
FG = QColor("#F2F2F2")
DIM = QColor("#9A9A9A")
BG = QColor(8, 8, 8)

EFFECT_H = 44
ENTRY_DELAY = 0.12        # let the row open first
ENTRY_S = 0.52
FLASH_S = 0.38
HOLD_FPS = 4

_inhale = QEasingCurve(QEasingCurve.OutBack)
_inhale.setOvershoot(0.75)            # ~4.5 % overshoot
_exhale = QEasingCurve(QEasingCurve.OutCubic)


def _num(v):
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    return float(v) if isinstance(v, (int, float)) else None


def _a(c: QColor, alpha: float) -> QColor:
    c = QColor(c)
    c.setAlphaF(max(0.0, min(1.0, alpha)))
    return c


def _mix(a: QColor, b: QColor, u: float) -> QColor:
    u = max(0.0, min(1.0, u))
    return QColor.fromRgbF(a.redF() + (b.redF() - a.redF()) * u, a.greenF() + (b.greenF() - a.greenF()) * u,
                           a.blueF() + (b.blueF() - a.blueF()) * u, a.alphaF() + (b.alphaF() - a.alphaF()) * u)


class EffectPlayer:
    """The one effect on screen (a newer one replaces it)."""

    def __init__(self):
        self.ev: dict | None = None
        self.t0 = 0.0
        self.art: QPixmap | None = None
        self.hover: str | None = None
        self._last_hold_frame = 0.0
        self.font = QFont("Segoe UI Variable Text", 10)
        self.font_small = QFont("Segoe UI Variable Text", 8)

    # -- state ---------------------------------------------------------------------
    def show(self, ev: dict, now: float | None = None):
        self.ev, self.t0 = ev, now if now is not None else time.perf_counter()
        self.art = None
        self._last_hold_frame = 0.0

    def set_art(self, effect_id: str, data: bytes):
        if self.ev and self.ev.get("id") == effect_id and data:
            pm = QPixmap()
            if pm.loadFromData(bytes(data)):
                self.art = pm

    def clear(self):
        self.ev, self.art, self.hover = None, None, None

    def visible(self, now: float | None = None) -> bool:
        if self.ev is None:
            return False
        now = now if now is not None else time.perf_counter()
        return now - self.t0 < float(self.ev.get("ttl_s", 8))

    def expired(self, now: float) -> bool:
        return self.ev is not None and not self.visible(now)

    def progress(self, now: float) -> float:
        """0..1 entry progress, eased: inhale when the value grows, exhale when it shrinks."""
        x = (now - self.t0 - ENTRY_DELAY) / ENTRY_S
        x = max(0.0, min(1.0, x))
        a, b = _num(self.ev.get("from")) if self.ev else None, _num(self.ev.get("to")) if self.ev else None
        curve = _exhale if (a is not None and b is not None and b < a) else _inhale
        return curve.valueForProgress(x)

    def needs_frame(self, now: float) -> bool:
        """A frame is due: 60/30 fps during the entry, HOLD_FPS for the countdown, none after."""
        if self.ev is None:
            return False
        if now - self.t0 < ENTRY_DELAY + ENTRY_S + 0.05:
            return True
        if now - self._last_hold_frame >= 1.0 / HOLD_FPS:
            self._last_hold_frame = now
            return True
        return False

    def flash_alpha(self, now: float) -> float:
        if not self.ev or self.ev.get("effect") != "flash" or self.ev.get("reverse"):
            return 0.0
        x = (now - self.t0) / FLASH_S
        return 0.0 if x >= 1 else 0.55 * (1 - x) ** 2

    def width(self) -> int:
        if not self.ev:
            return 0
        return {"media": 400, "confirm": 380, "volume": 360, "brightness": 360}.get(self.ev["effect"], 340)

    # -- hit testing ---------------------------------------------------------------
    def buttons(self, rect: QRectF) -> dict[str, QRectF]:
        if not self.ev:
            return {}
        cy = rect.center().y() - 1
        if self.ev["effect"] == "confirm":
            ok = QRectF(rect.right() - 14 - 76, cy - 12, 76, 24)
            return {"confirm": ok, "cancel": QRectF(ok.left() - 8 - 60, cy - 12, 60, 24)}
        if self.ev.get("undo"):
            return {"undo": QRectF(rect.right() - 14 - 58, cy - 12, 58, 24)}
        return {}

    def hit(self, rect: QRectF, pos: QPointF) -> str | None:
        for name, r in self.buttons(rect).items():
            if r.adjusted(-3, -3, 3, 3).contains(pos):
                return name
        return None

    # -- painting ------------------------------------------------------------------
    def paint(self, p, rect: QRectF, now: float):
        ev = self.ev
        if ev is None:
            return
        u = self.progress(now)
        p.save()
        p.setClipRect(rect, Qt.IntersectClip)       # the island already clips to its rounded pill
        p.setPen(QPen(QColor(255, 255, 255, 25), 1))
        p.drawLine(QPointF(14, rect.top() + 0.5), QPointF(rect.right() - 14, rect.top() + 0.5))
        getattr(self, "_paint_" + ev["effect"], self._paint_generic)(p, rect, ev, u, now)
        self._paint_buttons(p, rect)
        self._paint_countdown(p, rect, now)
        p.restore()

    def _right_edge(self, rect: QRectF) -> float:
        b = self.buttons(rect)
        return (min(r.left() for r in b.values()) - 10) if b else rect.right() - 16

    def _paint_buttons(self, p, rect):
        for name, r in self.buttons(rect).items():
            on = self.hover == name
            primary = name == "confirm"
            p.setPen(Qt.NoPen)
            p.setBrush(_a(LIVE, 0.85 if primary else (0.30 if on else 0.16)) if name != "cancel"
                       else QColor(255, 255, 255, 46 if on else 20))
            p.drawRoundedRect(r, 12, 12)
            p.setFont(self.font_small)
            p.setPen(BG if primary else (FG if name == "cancel" else HOT))
            p.drawText(r, Qt.AlignCenter, {"undo": "Undo", "confirm": "Confirm", "cancel": "Cancel"}[name])

    def _paint_countdown(self, p, rect, now):
        ttl = float(self.ev.get("ttl_s", 8))
        left = max(0.0, 1 - (now - self.t0) / ttl)
        if left <= 0 or not (self.ev.get("undo") or self.ev["effect"] == "confirm"):
            return
        p.setPen(QPen(_a(LIVE, 0.35), 1.2))
        y = rect.bottom() - 3
        x0, x1 = 20, rect.width() - 20
        p.drawLine(QPointF(x0, y), QPointF(x0 + (x1 - x0) * left, y))

    def _roll(self, p, r: QRectF, old: str, new: str, u: float, color=FG, font=None):
        """Old text rolls up and out, new text rolls in from below (island-motion.md)."""
        p.setFont(font or self.font)
        fm = QFontMetrics(font or self.font)
        u = max(0.0, min(1.0, u))
        if old and u < 1:
            p.setPen(_a(color, 1 - u))
            p.drawText(r.translated(0, -5 * u), Qt.AlignVCenter | Qt.AlignLeft,
                       fm.elidedText(old, Qt.ElideMiddle, int(r.width())))
        if new:
            p.setPen(_a(color, u if old else min(1.0, u * 1.4)))
            p.drawText(r.translated(0, 5 * (1 - u)), Qt.AlignVCenter | Qt.AlignLeft,
                       fm.elidedText(new, Qt.ElideMiddle, int(r.width())))

    def _label(self, p, rect, ev, x):
        p.setFont(self.font_small)
        p.setPen(DIM)
        p.drawText(QRectF(x, rect.top() + 5, self._right_edge(rect) - x, 14), Qt.AlignLeft | Qt.AlignVCenter,
                   QFontMetrics(self.font_small).elidedText(ev.get("label", ""), Qt.ElideRight,
                                                            int(self._right_edge(rect) - x)))

    # generic: service icon + old -> new
    def _paint_generic(self, p, rect, ev, u, now):
        cy = rect.center().y() - 1
        icon(p, ev.get("icon", "gear"), 30, cy, LIVE, u, ev)
        x = 52
        right = self._right_edge(rect)
        self._label(p, rect, ev, x)
        r = QRectF(x, rect.top() + 18, right - x, 20)
        old, new = ev.get("text_from") or "", ev.get("text_to") or ""
        if old and new and old != new:
            fm = QFontMetrics(self.font)
            ow = min(fm.horizontalAdvance(old), (right - x) * 0.42)
            p.setFont(self.font)
            p.setPen(_a(DIM, 0.4 + 0.6 * u))
            p.drawText(QRectF(x, r.top(), ow, r.height()), Qt.AlignVCenter | Qt.AlignLeft,
                       fm.elidedText(old, Qt.ElideMiddle, int(ow)))
            ax = x + ow + 8
            p.setPen(QPen(_a(LIVE, u), 1.4))                 # the arrow draws out
            p.drawLine(QPointF(ax, r.center().y()), QPointF(ax + 12 * u, r.center().y()))
            if u > 0.6:
                p.drawLine(QPointF(ax + 12, r.center().y()), QPointF(ax + 8, r.center().y() - 3))
                p.drawLine(QPointF(ax + 12, r.center().y()), QPointF(ax + 8, r.center().y() + 3))
            self._roll(p, QRectF(ax + 20, r.top(), right - ax - 20, r.height()), "", new, u)
        else:
            self._roll(p, r, "", new or old, u)

    # volume: the row becomes a slider that glides to the new level
    def _slider(self, p, rect, ev, u, color, x0):
        a, b = _num(ev.get("from")), _num(ev.get("to"))
        a = b if a is None else a
        v = (a + (b - a) * u) if b is not None else 0.0
        right = self._right_edge(rect) - 42
        y = rect.top() + 29
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 30))
        p.drawRoundedRect(QRectF(x0, y - 2, right - x0, 4), 2, 2)
        fx = x0 + (right - x0) * max(0.0, min(1.0, v / 100))
        p.setBrush(color)
        p.drawRoundedRect(QRectF(x0, y - 2, max(4.0, fx - x0), 4), 2, 2)
        p.setBrush(FG)
        p.drawEllipse(QPointF(fx, y), 5, 5)
        p.setFont(self.font)
        p.setPen(FG)
        p.drawText(QRectF(right + 8, rect.top() + 18, 40, 22), Qt.AlignVCenter | Qt.AlignLeft, f"{round(v)}%")
        return v

    def _paint_volume(self, p, rect, ev, u, now):
        cy = rect.center().y() - 1
        a, b = _num(ev.get("from")) or 0, _num(ev.get("to")) or 0
        icon(p, "speaker", 30, cy, LIVE, u, dict(ev, level=a + (b - a) * u))
        self._label(p, rect, ev, 52)
        self._slider(p, rect, ev, u, LIVE, 52)

    # mute: a speaker with its slash drawn in (or out)
    def _paint_mute(self, p, rect, ev, u, now):
        cy = rect.center().y() - 1
        muted_to = bool(ev.get("to"))
        trim = u if muted_to else 1 - u
        icon(p, "speaker", 30, cy, LIVE, 1, dict(ev, level=0 if muted_to else 60, slash=trim))
        self._label(p, rect, ev, 52)
        self._roll(p, QRectF(52, rect.top() + 18, self._right_edge(rect) - 52, 20), "",
                   "muted" if muted_to else "sound on", u)

    # brightness: the sun's ring of dots grows or shrinks
    def _paint_brightness(self, p, rect, ev, u, now):
        cy = rect.center().y() - 1
        a, b = _num(ev.get("from")) or 0, _num(ev.get("to")) or 0
        icon(p, "sun", 30, cy, HOT, u, dict(ev, level=a + (b - a) * u))
        self._label(p, rect, ev, 52)
        self._slider(p, rect, ev, u, HOT, 52)

    # dark mode: sun <-> moon flip while the row's background crossfades
    def _paint_theme(self, p, rect, ev, u, now):
        to_dark = str(ev.get("to", "")).lower() == "dark"
        from_dark = str(ev.get("from", "")).lower() == "dark"
        light = (0.0 if from_dark else 1.0) + ((0.0 if to_dark else 1.0) - (0.0 if from_dark else 1.0)) * u
        if light > 0.01:                           # a warm light wash for light mode
            p.fillRect(rect.adjusted(1, 1, -1, -1), _a(QColor("#FFF1E8"), 0.10 * light))
        cy = rect.center().y() - 1
        flip = abs(math.cos(math.pi * u))          # a coin flip: the glyph swaps at the edge-on moment
        show_dark = to_dark if u >= 0.5 else from_dark
        p.save()
        p.translate(30, cy)
        p.scale(max(0.06, flip), 1.0)
        icon(p, "moon" if show_dark else "sun", 0, 0, FG if show_dark else HOT, 1, dict(ev, level=70))
        p.restore()
        self._label(p, rect, ev, 52)
        self._roll(p, QRectF(52, rect.top() + 18, self._right_edge(rect) - 52, 20),
                   ev.get("text_from", ""), (ev.get("text_to", "") or "") + " mode", u)

    # media: the album art flips in with a progress line; skip slides the title
    def _paint_media(self, p, rect, ev, u, now):
        cy = rect.center().y() - 1
        art = QRectF(16, cy - 15, 30, 30)
        p.save()
        p.translate(art.center())
        p.scale(max(0.05, min(1.0, u)), 1.0)
        r = QRectF(-15, -15, 30, 30)
        path = QPainterPath()
        path.addRoundedRect(r, 6, 6)
        p.setClipPath(path)
        if self.art is not None:
            p.drawPixmap(r.toRect(), self.art)
        else:
            p.fillPath(path, QColor(255, 255, 255, 22))
            icon(p, "music", 0, 0, LIVE, 1, ev)
        p.restore()
        x, right = 56, self._right_edge(rect)
        ex = ev.get("extra") or {}
        title, artist = ex.get("title") or ev.get("text_to") or "", ex.get("artist") or ""
        old = ev.get("from") if isinstance(ev.get("from"), dict) else {}
        action = str(ex.get("action") or "")
        skipped = action in ("next", "previous") and old.get("title") and old.get("title") != title
        fm = QFontMetrics(self.font)
        tr = QRectF(x, rect.top() + 5, right - x, 18)
        p.setFont(self.font)
        if skipped:                                 # old title slides out, new one in
            d = -1 if (action == "next") != bool(ev.get("reverse")) else 1
            p.setPen(_a(FG, 1 - u))
            p.drawText(tr.translated(d * 30 * u, 0), Qt.AlignVCenter | Qt.AlignLeft,
                       fm.elidedText(old.get("title", ""), Qt.ElideRight, int(tr.width())))
            p.setPen(_a(FG, u))
            p.drawText(tr.translated(-d * 30 * (1 - u), 0), Qt.AlignVCenter | Qt.AlignLeft,
                       fm.elidedText(title, Qt.ElideRight, int(tr.width())))
        else:
            p.setPen(_a(FG, min(1.0, u * 1.3)))
            p.drawText(tr, Qt.AlignVCenter | Qt.AlignLeft, fm.elidedText(title or "nothing playing", Qt.ElideRight,
                                                                         int(tr.width())))
        status = ex.get("status") or ""
        sub = " · ".join(s for s in (artist, status if status not in ("playing", "") else "") if s)
        p.setFont(self.font_small)
        p.setPen(DIM)
        p.drawText(QRectF(x, rect.top() + 22, right - x, 14), Qt.AlignVCenter | Qt.AlignLeft,
                   QFontMetrics(self.font_small).elidedText(sub, Qt.ElideRight, int(right - x)))
        prog = ex.get("progress")
        if prog is not None:
            y = rect.bottom() - 7
            p.setPen(QPen(QColor(255, 255, 255, 30), 1.5))
            p.drawLine(QPointF(x, y), QPointF(right, y))
            p.setPen(QPen(LIVE, 1.5))
            p.drawLine(QPointF(x, y), QPointF(x + (right - x) * float(prog) * min(1.0, u), y))

    # screenshot: a shutter flash (the island overlay) and a camera
    def _paint_flash(self, p, rect, ev, u, now):
        self._paint_generic(p, rect, ev, u, now)

    # sleep / restart / shutdown: only a click goes ahead
    def _paint_confirm(self, p, rect, ev, u, now):
        cy = rect.center().y() - 1
        icon(p, "power", 30, cy, LIVE, u, ev)
        action = str((ev.get("extra") or {}).get("action") or ev.get("label") or "").lower()
        q = {"sleep": "Put the PC to sleep?", "restart": "Restart the PC?", "shutdown": "Shut down the PC?"}
        p.setFont(self.font)
        p.setPen(_a(FG, min(1.0, u * 1.3)))
        right = self._right_edge(rect)
        p.drawText(QRectF(52, rect.top(), right - 52, rect.height() - 2), Qt.AlignVCenter | Qt.AlignLeft,
                   QFontMetrics(self.font).elidedText(q.get(action, f"{action}?"), Qt.ElideRight, int(right - 52)))


def icon(p, name: str, cx: float, cy: float, color: QColor, u: float = 1.0, ev: dict | None = None):
    """Small glyphs (about 18 px), drawn with plain strokes. `u` is the entry progress."""
    ev = ev or {}
    p.save()
    p.translate(cx, cy)
    pen = QPen(color, 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    on = ev.get("to")
    if name == "speaker":
        level = float(ev.get("level", 60) or 0)
        body = QPainterPath()
        body.moveTo(-8, -3)
        body.lineTo(-4, -3)
        body.lineTo(1, -7)
        body.lineTo(1, 7)
        body.lineTo(-4, 3)
        body.lineTo(-8, 3)
        body.closeSubpath()
        p.setBrush(color)
        p.drawPath(body)
        p.setBrush(Qt.NoBrush)
        waves = 0 if level <= 0 else 1 if level < 34 else 2 if level < 67 else 3
        for i in range(3):
            r = 4 + 3.2 * i
            p.setPen(QPen(_a(color, 1.0 if i < waves else 0.18), 1.5, Qt.SolidLine, Qt.RoundCap))
            p.drawArc(QRectF(1 - r, -r, 2 * r, 2 * r), -45 * 16, 90 * 16)
        slash = float(ev.get("slash", 0) or 0)
        if slash > 0:
            p.setPen(QPen(BG, 3.4, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(-9, -8), QPointF(-9 + 19 * slash, -8 + 16 * slash))
            p.setPen(QPen(FG, 1.6, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(-9, -8), QPointF(-9 + 19 * slash, -8 + 16 * slash))
    elif name == "sun":
        level = max(0.0, min(1.0, float(ev.get("level", 60) or 0) / 100))
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawEllipse(QPointF(0, 0), 4.2, 4.2)
        ring = 6.5 + 3.5 * level                  # a ring of dots, never radiating strokes
        for i in range(8):
            ang = i * math.pi / 4
            p.setBrush(_a(color, 0.35 + 0.65 * level))
            p.drawEllipse(QPointF(ring * math.cos(ang), ring * math.sin(ang)), 0.8 + 0.9 * level, 0.8 + 0.9 * level)
    elif name == "moon":
        path = QPainterPath()
        path.addEllipse(QPointF(0, 0), 7, 7)
        cut = QPainterPath()
        cut.addEllipse(QPointF(4.2, -3.2), 6, 6)
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawPath(path.subtracted(cut))
    elif name == "image":
        p.drawRoundedRect(QRectF(-9, -7, 18, 14), 3, 3)
        p.drawPolyline([QPointF(-6, 4), QPointF(-2, -1), QPointF(1, 2), QPointF(3, 0), QPointF(6, 4)])
        p.setBrush(color)
        p.drawEllipse(QPointF(4, -3), 1.4, 1.4)
    elif name == "wifi":
        fill = 3.0 if on is None else 3 * u if on else 3 * (1 - u)     # the bars animate on or off
        for i in range(3):
            r = 3.5 + 3.5 * i
            p.setPen(QPen(_a(color, 0.2 + 0.8 * max(0.0, min(1.0, fill - i))), 1.6, Qt.SolidLine, Qt.RoundCap))
            p.drawArc(QRectF(-r, 5 - r, 2 * r, 2 * r), 45 * 16, 90 * 16)
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawEllipse(QPointF(0, 5), 1.5, 1.5)
    elif name == "bluetooth":
        p.setPen(QPen(_a(color, 1.0 if on is not False else 0.45), 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPolyline([QPointF(-4, -4), QPointF(4, 4), QPointF(0, 8), QPointF(0, -8), QPointF(4, -4), QPointF(-4, 4)])
    elif name == "bolt":
        path = QPainterPath()
        for i, (x, y) in enumerate([(2, -8), (-5, 1), (0, 1), (-2, 8), (5, -1), (0, -1)]):
            path.moveTo(x, y) if i == 0 else path.lineTo(x, y)
        path.closeSubpath()
        p.setBrush(color)
        p.drawPath(path)
    elif name == "window":
        p.drawRoundedRect(QRectF(-9, -7, 18, 14), 2.5, 2.5)
        p.drawLine(QPointF(-9, -3), QPointF(9, -3))
    elif name == "app":
        s = 0.6 + 0.4 * u                          # zooms out of the island
        for dx in (-4.5, 4.5):
            for dy in (-4.5, 4.5):
                p.drawRoundedRect(QRectF(dx * s - 3, dy * s - 3, 6, 6), 1.5, 1.5)
    elif name == "clipboard":
        p.drawRoundedRect(QRectF(-6, -7, 12, 15), 2, 2)
        p.drawRoundedRect(QRectF(-3, -9, 6, 4), 1, 1)
    elif name == "lock":
        p.drawRoundedRect(QRectF(-6, -1, 12, 9), 2, 2)
        p.drawArc(QRectF(-4, -8, 8, 10), 0, 180 * 16)
    elif name == "power":
        p.drawArc(QRectF(-7, -6, 14, 14), 120 * 16, 300 * 16)
        p.drawLine(QPointF(0, -8), QPointF(0, -1))
    elif name == "camera":
        p.drawRoundedRect(QRectF(-9, -5, 18, 12), 2.5, 2.5)
        p.drawRect(QRectF(-3, -8, 6, 3))
        p.drawEllipse(QPointF(0, 1), 3.5, 3.5)
    elif name == "music":
        p.setBrush(color)
        p.drawEllipse(QPointF(-3, 5), 2.6, 2.2)
        p.setBrush(Qt.NoBrush)
        p.drawLine(QPointF(-0.6, 5), QPointF(-0.6, -7))
        p.drawLine(QPointF(-0.6, -7), QPointF(5, -5))
    else:                                           # gear: a ring and a hub
        p.drawEllipse(QPointF(0, 0), 7, 7)
        p.setBrush(color)
        p.drawEllipse(QPointF(0, 0), 2.5, 2.5)
    p.restore()
