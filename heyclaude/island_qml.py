"""The M5 island: Qt Quick / QML, "Ember & Thread" (docs/island-motion.md).

`QmlIsland` keeps the interface of the QPainter `island.Island` (the same
Bridge signals in, the same Qt signals out, attach_input / toggle_input /
flash / show_tasks_until ...), so the assistant code barely changes. The
drawing and all motion live in ui/island/ (Island.qml + engine.js, a port of
the approved preview); this module only:

- owns the one fixed, transparent, never-focused window (600 x 400 at the top
  centre) and its click-through mask;
- routes assistant modes to engine commands (`route()`, pure, tested offline);
- carries the extras: M9 effects, response widgets (widgets.py), service
  icons for tools in use (service_icons.py), approval cards and session
  events for M7, plan usage, file drops;
- handles the talk-or-type field's focus (taken at open, given back on close).

Python emits nothing per frame. In ready the engine ticks itself at 8 fps,
in off at 0.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from PySide6.QtCore import QObject, QPoint, QRect, QTimer, QUrl, Property, Qt, Signal, Slot
from PySide6.QtGui import QCursor, QGuiApplication, QImage, QRegion
from PySide6.QtQuick import QQuickImageProvider, QQuickView, QQuickWindow, QSGRendererInterface

from . import characters as CH
from .config import ROOT

QML_DIR = ROOT / "ui" / "island"
WIN_W, WIN_H = 600, 400
NOT_ME_S = 120
FIELD_IDLE_S = 20      # an empty talk-or-type field that heard nothing closes after this (it blocks the wake word)


# ----------------------------------------------------------------- mode routing
def route(mode: str, text: str, ctx: dict) -> list[tuple[str, list]]:
    """An assistant mode (Bridge.state) -> engine commands.

    ctx: {"engine": the engine's current state name, "paused_until": epoch s
    or 0, "paused_since": epoch s, "now": epoch s, "woke_at": epoch s of the
    last wake word (0 = none), "input": the talk-or-type field is open}.
    """
    st = ctx.get("engine", "ready")
    now = ctx.get("now", time.time())
    rest_like = st in ("ready", "off", "paused", "loading", "mismatch", "error")
    if mode == "idle":
        return [("go", ["ready", {}])]
    if mode == "off":
        until = float(ctx.get("paused_until") or 0)
        if until > now:
            since = float(ctx.get("paused_since") or now)
            return [("go", ["paused", {"until": until * 1000, "since": since * 1000}])]
        return [("go", ["off", {}])]
    if mode == "loading":
        return [("go", ["loading", {"text": text or "loading…"}])]
    if mode == "dictating":
        # M4d. No theme draws "dictating" yet: ui/states.json falls back to listening
        # (the level meter), with the label saying it's dictation.
        cmds = [] if st == "listening" else [("go", ["listening", {}])]
        return cmds + [("setPartial", [text or "dictating…"])]
    if mode == "listening":
        if ctx.get("input"):
            return []                                  # the field already shows the mic
        if st in ("woke",):
            return []                                  # the catch hands over to listening itself
        if st in ("listening",):
            return []
        if rest_like or st in ("thinking", "speaking"):
            return [("go", ["woke", {}])]              # hotkey / click / barge-in: the catch
        return [("go", ["listening", {}])]            # followup / holding -> relabel
    if mode == "holding":
        muted = text == "muted"
        return [("go", ["holding", {"muted": muted}])]
    if mode == "followup":
        try:
            secs = float(text or 5)
        except ValueError:
            secs = 5.0
        return [("go", ["followup", {"seconds": secs}])]
    if mode == "thinking":
        if ctx.get("input"):
            return []
        if st == "thinking":
            return [("setThinkText", [text or "…"])]
        return [("go", ["thinking", {"text": text or "…"}])]
    if mode == "speaking":
        if st == "speaking":
            return [("setSpeakText", [text or ""])]
        return [("go", ["speaking", {"text": text or ""}])]
    if mode == "error":
        return [("go", ["error", {"text": text or "something went wrong"}])]
    if mode == "approval_needed":
        # M7: a card waits for a click. The theme shows approval_needed, or the state its
        # fallback chain reaches (Ember & Thread: confused -> idle, so the resting state it
        # was in: ready, or off / paused when listening is off; the card itself says it).
        from .states import mode_for
        shown = mode_for("approval_needed", ctx.get("implements") or theme_implements())
        rest = ctx.get("rest") if ctx.get("rest") in ("idle", "off") else "idle"
        if shown in ("idle", "off", "approval_needed"):
            return route(rest, text, ctx)
        return route(shown, text, ctx)
    return [("go", ["thinking", {"text": text or mode}])]


_IMPLEMENTS: list | None = None


def theme_implements() -> list:
    """The states the QML island's theme (Ember & Thread) draws itself."""
    global _IMPLEMENTS
    if _IMPLEMENTS is None:
        try:
            from .states import load_theme
            _IMPLEMENTS = list(load_theme("ember-thread")["implements"])
        except Exception:
            _IMPLEMENTS = ["idle", "off", "listening", "thinking", "talking"]
    return _IMPLEMENTS


SCALE_MIN, SCALE_MAX = 0.8, 2.0


def resolve_scale(setting, screen=None) -> float:
    """island.scale: a number (0.8-2.0, rounded to 0.05) or "auto". Auto is 1.0,
    or 1.25 on a large screen (27" and up, seen from further away) when Windows
    reports its physical size."""
    s = str(setting if setting is not None else "auto").strip().lower().rstrip("%x")
    if s not in ("", "auto"):
        try:
            v = float(s)
            if v > 4:                      # "125" meaning 125 %
                v /= 100.0
            return round(max(SCALE_MIN, min(SCALE_MAX, v)) * 20) / 20
        except ValueError:
            pass
    try:
        if screen is not None:
            mm = screen.physicalSize()
            diag_in = (mm.width() ** 2 + mm.height() ** 2) ** 0.5 / 25.4
            if diag_in >= 26.5 and screen.devicePixelRatio() <= 1.0:
                return 1.25
    except Exception:
        pass
    return 1.0


def text_render_type(scale: float):
    """Native (hinted) text is the sharpest at exactly 1.0; scaled, it would be a
    blown-up bitmap, so any other scale uses Qt's distance-field text, which stays
    sharp at any size (measured on a 96 DPI screen)."""
    return QQuickWindow.NativeTextRendering if abs(scale - 1.0) < 1e-6 else QQuickWindow.QtTextRendering


def stt_short(label: str) -> str:
    """"gpu (turbo)" -> "gpu"; "cpu (gpu busy 83%)" -> "cpu · gpu busy 83%"."""
    dev, _, inside = (label or "").partition(" (")
    inside = inside.rstrip(")").replace("fallback: ", "")
    return f"{dev} · {inside}" if " " in inside or inside == "premiere" else dev


def badge_dir(old: str, new: str) -> int:
    """+1 an upgrade (to the GPU), -1 a downgrade, 0 neither."""
    o, n = old.split(" ")[0] if old else "", new.split(" ")[0] if new else ""
    if o == n or not o:
        return 0
    return 1 if n == "gpu" else -1 if o == "gpu" else 0


def resolve_motion(setting: str | None) -> str:
    """island.motion: auto (calm when Windows animations are off) | full | calm | off."""
    s = str(setting or "auto").strip().lower()
    if s in ("full", "calm", "off"):
        return s
    try:
        from .winsys import client_area_animation
        return "full" if client_area_animation() else "calm"
    except Exception:
        return "full"


def task_rows(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows[-5:]:
        st = r.get("status", "")
        right = r.get("activity") if st in ("starting", "running") else (r.get("summary") or st)
        out.append({"id": str(r.get("id")), "name": f"{r.get('project', '')}: {r.get('prompt', '')}",
                    "status": "running" if st in ("starting", "running") else st,
                    "right": str(right or st or ""), "elapsed": str(r.get("elapsed", ""))})
    return out


SESSION_STATUS = {"working": "running", "waiting": "waiting", "done": "done", "error": "failed", "idle": "idle"}
SESSION_ACTIONS = ("diff", "jump", "desktop", "dismiss")


def session_label(s: dict) -> str:
    """What a watched session is called on the island: its title, else its folder."""
    return str(s.get("title") or s.get("project") or "session").strip() or "session"


def session_rows(sessions: list[dict]) -> list[dict]:
    """SessionHub snapshot rows (newest activity first) -> hover-list rows (newest last).

    name = the title or project, right = what it does now (the ticker line; the
    final line once done), elapsed = "+N −M"; plus the session's id, diff count and
    desktop flag for the row's actions."""
    out = []
    for s in reversed(sessions or []):
        sid = str(s.get("id") or "")
        if not sid:
            continue
        st = str(s.get("status") or "idle")
        status = SESSION_STATUS.get(st, "idle")
        right = s.get("activity") or ""
        if st == "done":
            right = s.get("last") or right or "finished"
        elif st == "waiting" and not right:
            right = "waiting for you"
        plus, minus = int(s.get("plus") or 0), int(s.get("minus") or 0)
        out.append({"id": f"s:{sid}", "kind": "session", "sid": sid, "name": session_label(s),
                    "status": status, "right": " ".join(str(right).split())[:200],
                    "elapsed": f"+{plus} \u2212{minus}" if (plus or minus) else "",
                    "plus": plus, "minus": minus, "diffs": int(s.get("diffs") or 0), "desktop": bool(s.get("desktop"))})
    return out


def merged_rows(tasks: list[dict], sessions: list[dict], cap: int = 5) -> list[dict]:
    """hey-claude's own tasks and the watched sessions in one hover list (at most `cap`
    rows; sessions keep at least 2 places, tasks the rest)."""
    t = task_rows(tasks)
    s = session_rows(sessions)
    keep_s = min(len(s), max(2, cap - len(t)))
    s = s[len(s) - keep_s:] if keep_s else []
    room = cap - len(s)
    t = t[len(t) - room:] if room > 0 and t else []
    return t + s


# ----------------------------------------------------------------- QML side
class _ArtProvider(QQuickImageProvider):
    """image://island/<key>: album art and other in-memory images."""

    def __init__(self):
        super().__init__(QQuickImageProvider.Image)
        self.images: dict[str, QImage] = {}

    def requestImage(self, key, size, requested):
        img = self.images.get(key.split("?")[0], QImage())
        if size is not None:
            try:
                size.setWidth(img.width())
                size.setHeight(img.height())
            except Exception:
                pass
        return img


class IslandModel(QObject):
    """What Island.qml sees as `model`."""
    cmd = Signal(str, "QVariantList")
    # extras (Extras.qml)
    effectShown = Signal("QVariantMap")
    effectArt = Signal(str, str)
    widgetShown = Signal("QVariantMap")
    widgetCleared = Signal()
    questionShown = Signal(str)
    answerShown = Signal(str)
    approvalShown = Signal("QVariantMap")
    approvalCleared = Signal(str)
    toolsChanged = Signal("QVariantList")
    usageChanged = Signal()
    configChanged = Signal()
    characterChanged = Signal()                  # M6: the pack (url, manifest, budgets)
    characterStateChanged = Signal()
    charCmd = Signal(str, "QVariantList")        # M6: play / interrupt an antic, suspend

    def __init__(self, owner: "QmlIsland"):
        super().__init__()
        self.o = owner
        self._usage: dict = {}
        self._band = 0.0
        self._top_margin = -1
        self._software = False
        self._usage_on = True
        self._scale = 1.0
        self._char_url = ""
        self._char_man: dict = {}
        self._char_budgets: dict = {}
        self._char_state = "idle"
        self._char_mood = "neutral"
        self._char_count = 0

    # properties -------------------------------------------------------------
    characterUrl = Property(str, lambda self: self._char_url, notify=characterChanged)
    characterManifest = Property("QVariantMap", lambda self: self._char_man, notify=characterChanged)
    characterBudgets = Property("QVariantMap", lambda self: self._char_budgets, notify=characterChanged)
    characterState = Property(str, lambda self: self._char_state, notify=characterStateChanged)
    characterMood = Property(str, lambda self: self._char_mood, notify=characterStateChanged)
    characterCount = Property(int, lambda self: self._char_count, notify=characterStateChanged)

    @Slot(str, bool)
    def characterAnticDone(self, aid, caught):
        self.o.char.antic_done(aid, caught)

    def _get_band(self):
        return self._band

    band = Property(float, _get_band, notify=configChanged)

    def _get_tm(self):
        return self._top_margin

    topMargin = Property(int, _get_tm, notify=configChanged)

    def _get_sw(self):
        return self._software

    software = Property(bool, _get_sw, notify=configChanged)

    def _get_clock(self):
        return "real"

    clockMode = Property(str, _get_clock, constant=True)

    def _get_scale(self):
        return self._scale

    uiScale = Property(float, _get_scale, notify=configChanged)

    def _get_usage(self):
        return self._usage

    usage = Property("QVariantMap", _get_usage, notify=usageChanged)

    def _get_usage_on(self):
        return self._usage_on and bool(self._usage)

    usageOn = Property(bool, _get_usage_on, notify=usageChanged)

    def _get_widget_urls(self):
        try:
            from .widgets import registry
            return [m["qml"] for m in registry().values()]
        except Exception:
            return []

    widgetUrls = Property("QVariantList", _get_widget_urls, constant=True)

    # slots from QML ------------------------------------------------------------
    @Slot()
    def ready(self):
        self.o._qml_ready()

    @Slot(str)
    def log(self, msg):
        print(msg)

    @Slot(float)
    def frame(self, gap):
        self.o._frame(gap)

    @Slot(int, int, int, int)
    def setMask(self, x, y, w, h):
        self.o._set_mask(x, y, w, h)

    @Slot(bool)
    def hover(self, on):
        self.o.hovering = on
        if on:
            self.o.char.pointer()

    @Slot()
    def activate(self):
        self.o.activate.emit()

    @Slot()
    def stopSpeaking(self):
        self.o.stop_speaking.emit()

    @Slot()
    def interruptTalk(self):
        self.o.interrupt_talk.emit()

    @Slot()
    def notMe(self):
        self.o._not_me_clicked()

    @Slot()
    def contextMenu(self):
        self.o._menu(QCursor.pos())

    @Slot(int)
    def openTask(self, i):
        self.o._open_task(i)

    @Slot(str)
    def openRow(self, row_id):
        self.o._open_row(row_id)

    @Slot(str, str)
    def sessionAction(self, sid, action):
        self.o._session_action(sid, action)

    @Slot("QVariantList")
    def filesDropped(self, urls):
        self.o._files_dropped([str(u) for u in urls])

    @Slot(str, bool, result=bool)
    def inputEnter(self, text, shift):
        s = self.o.session
        return bool(s and s.key_enter(text, shift))

    @Slot()
    def inputEscape(self):
        if self.o.session is not None:
            print("[input] Esc")
            self.o.session.key_escape()

    @Slot(str)
    def inputEdited(self, text):
        self.o._field_text = text
        self.o._last_edit = time.monotonic()
        if self.o.session is not None:
            self.o.session.edited(text)

    @Slot(str, str)
    def effectClicked(self, kind, ev_id):
        self.o._effect_clicked(kind, ev_id)

    @Slot(str, str)
    def approvalDecided(self, ap_id, action):
        self.o._approval_decided(ap_id, action)

    @Slot(str, "QVariantMap")
    def approvalAnswered(self, ap_id, answers):
        self.o._approval_answered(ap_id, answers)

    @Slot(str, str, "QVariant")
    def widgetAction(self, wid, action, payload):
        self.o._widget_action(wid, action, payload)

    @Slot(str)
    def openUrl(self, url):
        self.o._open_url(url)

    @Slot(str)
    def copyText(self, text):
        QGuiApplication.clipboard().setText(text)


class QmlIsland(QObject):
    """Drop-in replacement for island.Island, drawn in QML."""
    activate = Signal()
    stop_speaking = Signal()
    interrupt_talk = Signal()
    effect_undo = Signal(str)
    effect_confirm = Signal(str)
    effect_cancel = Signal(str)
    quit_requested = Signal()
    not_me = Signal()
    # new in M5
    files_dropped = Signal(list)                 # local paths
    widget_action = Signal(str, str, object)     # widget id, action, payload
    approval_decided = Signal(str, str)          # M7: approval id, action: allow | deny | always | pass
    approval_answered = Signal(str, dict)        # M7: approval id, {question text: label | [labels]}
    session_action = Signal(str, str)            # M7: session id, action: diff | jump | desktop | dismiss

    NOT_ME_S = NOT_ME_S

    def __init__(self, bridge, cfg: dict, task_manager=None, wake_label: str = "hey jarvis",
                 software: bool | None = None, show: bool = True):
        super().__init__()
        self.cfg = cfg
        self.bridge = bridge
        self.tm = task_manager
        self._wake_label = wake_label
        self.session = None
        self.input_open = False
        self.hovering = False
        self.mode, self.text = "loading", "starting up…"
        self.last_wake_t = 0.0
        self.wake_listening = False
        self.partial = ""
        self.paused_until = 0.0
        self.paused_since = 0.0
        self.stt_label = ""
        self.voice_label = ""
        self.task_rows: list[dict] = []
        self.log_windows: dict = {}
        self.engine_state = "loading"
        self._prev_fg = 0
        self._restore_focus = True
        self._away_since = 0.0
        self._ready = False
        self._queue: list[tuple[str, list]] = []
        self._mask = (0, 0, 0, 0)
        self._last_gap = None
        self.frames = 0
        self.effect: dict | None = None
        self.widget: dict | None = None
        self.approvals: dict[str, dict] = {}
        self.tools: dict[str, dict] = {}
        self.sessions: list[dict] = []           # M7: SessionHub's snapshot (Bridge.sessions)
        self.island_state = "loading"            # the mode shown: the assistant's, or approval_needed while a card waits
        self.diff_windows: dict = {}             # title -> DiffWindow (kept so they aren't collected)

        self.model = IslandModel(self)
        # M6: the character (heyclaude/characters.py drives it; ui/CharacterHost.qml draws it)
        self.char = CH.Driver(send=self._char_send, later=lambda ms, fn: QTimer.singleShot(ms, fn))
        self._input_watch = CH.InputWatch()
        self._char_timer = QTimer(self, interval=1000, timeout=self._char_tick)
        self._char_id = None
        self.companion = None                     # the desktop companion, when island.character.where wants it
        self.model._usage_on = bool(cfg.get("usage_pill", True))
        if software is None:
            software = str(cfg.get("renderer", "auto")).lower() == "software"
        if software:
            QQuickWindow.setGraphicsApi(QSGRendererInterface.Software)
        self.model._software = bool(software)
        QQuickWindow.setDefaultAlphaBuffer(True)
        self.scale = resolve_scale(cfg.get("scale", "auto"), self._screen())
        self.model._scale = self.scale
        QQuickWindow.setTextRenderType(text_render_type(self.scale))
        self._reloading = False

        self.view = QQuickView()
        self.view.setColor(Qt.transparent)
        self.view.setFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                           | Qt.WindowDoesNotAcceptFocus | Qt.NoDropShadowWindowHint)
        self.view.setTitle("hey-claude island")
        self.view.setResizeMode(QQuickView.SizeRootObjectToView)
        self.art = _ArtProvider()
        self.view.engine().addImageProvider("island", self.art)
        self.view.engine().addImportPath(str(CH.KIT_IMPORT_DIR))
        self.view.rootContext().setContextProperty("model", self.model)
        self.view.statusChanged.connect(self._status)
        self.view.setSource(QUrl.fromLocalFile(str(QML_DIR / "Island.qml")))
        if self.view.status() == QQuickView.Error:
            errs = "; ".join(e.toString() for e in self.view.errors())
            raise RuntimeError(f"island QML failed to load: {errs}")
        self.root = self.view.rootObject()
        self.view.resize(round(WIN_W * self.scale), round(WIN_H * self.scale))
        self.apply_config()
        self.view.sceneGraphError.connect(lambda err, msg: print(f"[island] scene graph error: {msg}"))

        # inputs
        bridge.state.connect(self.set_state)
        bridge.level.connect(self.push_level)
        bridge.tasks.connect(self.set_tasks)
        bridge.listening.connect(self._listening)
        bridge.stt.connect(self.set_stt)
        bridge.heard.connect(self._heard)
        bridge.partial.connect(self.set_partial)
        bridge.voice.connect(self.set_voice)
        if hasattr(bridge, "note"):
            bridge.note.connect(lambda t: self.flash(t, 3.5))   # M4c: speech fallbacks / switches
        bridge.wake.connect(self.set_wake_label)
        bridge.wake_hit.connect(self._wake_hit)
        bridge.effect.connect(self.show_effect)
        bridge.effect_art.connect(self._effect_art)
        for name, slot in (("tts_level", self.push_tts), ("widget", self.show_widget), ("tool", self.tool_event),
                           ("usage", self.set_usage), ("approval", self.show_approval),
                           ("approval_withdrawn", self.withdraw_approval), ("session_event", self.session_event),
                           ("mismatch", self.voice_mismatch), ("turn", self.start_turn), ("spoken", self.spoken),
                           ("widget_clear", self.clear_timer_widget), ("sessions", self.set_sessions),
                           ("mood", self.set_mood)):
            sig = getattr(bridge, name, None)
            if sig is not None:
                sig.connect(slot)

        self._focus_timer = QTimer(self, interval=150, timeout=self._check_focus)
        self._env_timer = QTimer(self, interval=5000, timeout=self._check_env)
        self._env_timer.start()
        self._suspended = False
        self._battery = None
        self._check_env()
        if show:
            self.show()

    # -- window ---------------------------------------------------------------------
    def _status(self, st):
        if st == QQuickView.Error:
            for e in self.view.errors():
                print(f"[island] QML: {e.toString()}")

    def _screen(self):
        screens = QGuiApplication.screens()
        sel = self.cfg.get("screen", "primary")
        return screens[int(sel)] if str(sel).isdigit() and int(sel) < len(screens) else QGuiApplication.primaryScreen()

    def apply_config(self):
        """Position, top margin, motion, hide-when-idle: re-read from cfg (Settings changes apply live)."""
        s = self._screen()
        self.set_scale(resolve_scale(self.cfg.get("scale", "auto"), s))
        g, ag = s.geometry(), s.availableGeometry()
        band = max(0, ag.y() - g.y())
        m = self.cfg.get("top_margin", "auto")
        self.model._top_margin = int(m) if str(m).strip().lstrip("-").isdigit() else -1
        self.model._band = float(band)
        self.model.configChanged.emit()
        self.view.setScreen(s)
        # `window_y` moves the whole window down (the motion lab, so it never sits on a running island)
        w = round(WIN_W * self.scale)
        self.view.setPosition(g.x() + (g.width() - w) // 2, g.y() + int(self.cfg.get("window_y", 0) or 0))
        self.motion = resolve_motion(self.cfg.get("motion", "auto"))
        self._send("setMotion", [self.motion])
        self._send("setHideIdle", [bool(self.cfg.get("hide_when_idle", False))])
        self.model._usage_on = bool(self.cfg.get("usage_pill", True))
        self.model.usageChanged.emit()
        self._send("setUsageRow", [self.model._usage_on and bool(self.model._usage)])
        self.apply_character()

    # -- M6: the character -------------------------------------------------------------
    def apply_character(self, pack: str | None = None):
        """Load island.character.pack (or `pack`, Settings' live preview) into the pill."""
        cc = self.cfg.get("character") or {}
        pid = pack if pack is not None else str(cc.get("pack", CH.NONE) or CH.NONE)
        where = str(cc.get("where", "island"))
        man = CH.registry().get(pid) if pid != CH.NONE else None
        if pid != CH.NONE and man is None:
            print(f"[character] no pack {pid!r}; showing the ember")
        in_pill = man is not None and where in ("island", "both")
        m = self.model
        if pid != self._char_id or (in_pill != bool(m._char_url)):
            self._char_id = pid
            m._char_url = QUrl.fromLocalFile(man["qml"]).toString() if in_pill else ""
            m._char_man = {"bounds": man["bounds"], "desktop_bounds": man["desktop_bounds"],
                           "oneshot": man.get("oneshot", {})} if man else {}
            from . import states as _S
            m._char_budgets = dict(_S.load().get("budgets", {}))
            m.characterChanged.emit()
        antics = str(cc.get("antics", "normal"))
        on_desk = man is not None and where in ("desktop", "both")
        # with a companion on the desktop the antics play there (more room); the pill stays calm
        self.char.configure(man if in_pill else None, frequency="off" if on_desk else antics,
                            disabled=cc.get("antics_off") or [], where="pill")
        if on_desk:
            try:
                if self.companion is None:
                    from .companion import Companion
                    self.companion = Companion(self)
                self.companion.configure(man, size=int(cc.get("desktop_size", 120) or 120), frequency=antics,
                                         disabled=cc.get("antics_off") or [])
                self.companion.set_reduced(getattr(self, "motion", "full") != "full")
                self.companion.driver.set_mode(self.mode)
            except Exception as e:
                print(f"[character] desktop companion failed: {e}")
        elif self.companion is not None:
            self.companion.configure(None)
        if in_pill or on_desk:
            self.char.set_mode(self.mode)
            if not self._char_timer.isActive():
                self._char_timer.start()
        else:
            self._char_timer.stop()

    def _char_send(self, fn: str, args: list):
        if fn == "state":
            self.model._char_state = args[0]
            self.model._char_mood = self.char.mood
            self.model._char_count = self.char.tasks
            self.model.characterStateChanged.emit()
        else:
            self.model.charCmd.emit(fn, args)

    def _char_tick(self):
        now = time.time()
        idle, typing = self._input_watch.poll(now)
        try:
            from .winsys import battery_saver
            saver = battery_saver()
        except Exception:
            saver = False
        hidden = (bool(self.cfg.get("hide_when_idle", False)) and not self.hovering) or not self.view.isVisible()
        wx, wt = getattr(self, "_weather_seen", ("", 0.0))
        ctx = CH.context_now(user_idle_s=idle, typing=typing, fullscreen=self._suspended,
                             reduced_motion=getattr(self, "motion", "full") != "full", battery_saver=saver,
                             hidden=hidden, weather=wx if now - wt < 7200 else "")
        self.char.tick(ctx)
        if self.companion is not None and self.companion.man is not None:
            hidden_d = not self.companion.view.isVisible()
            self.companion.set_running(not self._suspended)
            self.companion.driver.tick(CH.context_now(user_idle_s=idle, typing=typing, fullscreen=self._suspended,
                                                      reduced_motion=ctx.reduced_motion, battery_saver=saver,
                                                      hidden=hidden_d, weather=ctx.weather))

    def _chars(self):
        """The drivers to tell about a mode or an event: the pill's, and the companion's."""
        out = [self.char]
        if self.companion is not None and self.companion.man is not None:
            out.append(self.companion.driver)
        return out

    def set_mood(self, mood: str):
        """M6: the answer's mood. The character shows it as it finishes speaking."""
        for d in self._chars():
            d.set_mood(mood)

    def preview_character(self, pack):
        """Settings' gallery: show a pack now, unsaved (None = back to the saved one)."""
        self.apply_character(None if pack is None else str(pack))

    def play_antic(self, aid: str) -> bool:
        """Settings' "play now"."""
        ok = self.char.play_now(aid)
        if self.companion is not None and self.companion.man is not None:
            ok = self.companion.driver.play_now(aid) or ok
        return ok

    _place = apply_config           # island.Island's name for it (__main__ calls it on Settings changes)

    def set_scale(self, scale: float):
        """Resize everything (live). Crossing 1.0 reloads the QML so text switches
        between native and distance-field rendering (see text_render_type)."""
        scale = round(max(SCALE_MIN, min(SCALE_MAX, float(scale))) * 20) / 20
        if abs(scale - self.scale) < 1e-6 and abs(self.model._scale - scale) < 1e-6:
            return
        reload = text_render_type(scale) != text_render_type(self.scale)
        self.scale = scale
        self.model._scale = scale
        self.view.resize(round(WIN_W * scale), round(WIN_H * scale))
        g = self._screen().geometry()
        self.view.setPosition(g.x() + (g.width() - round(WIN_W * scale)) // 2, self.view.y())
        if reload:
            self._reload()
        else:
            self.model.configChanged.emit()

    def preview_scale(self, scale):
        """Settings' slider: show a scale now, without saving it (None = back to the config)."""
        self.set_scale(resolve_scale(self.cfg.get("scale", "auto") if scale is None else scale, self._screen()))

    def _reload(self):
        """Load Island.qml again (text render type changed) and replay what it shows."""
        QQuickWindow.setTextRenderType(text_render_type(self.scale))
        self._ready = False
        self._reloading = True
        self.view.setSource(QUrl())          # drop the old tree (no clearComponentCache: types stay valid)
        self.view.setSource(QUrl.fromLocalFile(str(QML_DIR / "Island.qml")))
        self.root = self.view.rootObject()

    def _replay(self):
        self._reloading = False
        self.engine_state = "ready"
        self._send("setMotion", [self.motion])
        self._send("setHideIdle", [bool(self.cfg.get("hide_when_idle", False))])
        if self.stt_label:
            self._send("setBadge", [stt_short(self.stt_label), 0])
        self._send("setWakeLabel", [self._wake_label])
        self._send("setTasks", [self._rows()])
        self._send("setUsageRow", [self.model._usage_on and bool(self.model._usage)])
        self.set_state(self.mode, self.text)

    def show(self):
        from .winsys import set_no_activate
        self.view.show()
        set_no_activate(int(self.view.winId()), True)

    def raise_(self):
        self.view.raise_()

    def hide(self):
        self.view.hide()

    def winId(self):
        return self.view.winId()

    def _set_mask(self, x, y, w, h):
        self._mask = (x, y, w, h)
        self.view.setMask(QRegion(QRect(x, y, max(1, w), max(1, h))))

    def _frame(self, gap):
        self._last_gap = gap          # the driver tier changed: ms between frames, 0 = live, -1 = stopped

    def _send(self, fn: str, args: list):
        if not self._ready:
            self._queue.append((fn, args))
            return
        self.model.cmd.emit(fn, args)

    def _qml_ready(self):
        self._ready = True
        q, self._queue = self._queue, []
        for fn, args in q:
            self.model.cmd.emit(fn, args)
        if self._reloading:
            self._replay()

    def _check_env(self):
        """Every 5 s: suspend in full-screen apps / when the screen is locked; 30 fps on battery."""
        try:
            from .winsys import fullscreen_app, on_battery
            susp = fullscreen_app() and self.mode in ("idle", "off") and not self.input_open
            batt = on_battery()
        except Exception:
            return
        if susp != self._suspended:
            self._suspended = susp
            self._send("suspend", [susp])
        if batt != self._battery:
            self._battery = batt
            self._send("setLiveCap", [30 if (batt or self.model._software) else 60])

    # -- inputs from the assistant --------------------------------------------------
    def set_state(self, mode: str, text: str = ""):
        prev = self.mode
        self.mode, self.text = mode, text
        if mode not in ("listening", "holding", "followup", "dictating"):
            self.partial = ""
        if mode != "listening":
            self.wake_listening = False
        if mode == "thinking":
            text = self._status_line(text)
        elif mode == "speaking" and self.question and text:
            self.model.answerShown.emit(text)          # it stays on the panel after it's been spoken
        shown = self._shown_mode(mode)
        self.island_state = shown
        for d in self._chars():
            d.set_mode(shown, paused=mode == "off" and self.paused_until > time.time())
        if self.companion is not None and mode != prev:
            self.companion.levels(0.0, 0.0)
        for fn, args in route(shown, text, self._route_ctx()):
            self._send(fn, args)
            if fn == "go":
                self.engine_state = "listening" if args[0] == "woke" else args[0]
        self._update_not_me()
        if mode == "thinking" and prev != "thinking":
            self.tools.clear()
            self._send("setTool", ["", ""])
        if mode in ("idle", "off") and self.widget and self.widget.get("_expire_on_rest"):
            pass
        snap = os.environ.get("HEYCLAUDE_SNAP")
        if snap:
            QTimer.singleShot(450, lambda m=mode: self.grab().save(os.path.join(snap, f"{time.strftime('%H%M%S')}-{m}.png")))

    def _route_ctx(self) -> dict:
        return {"engine": self.engine_state, "paused_until": self.paused_until, "paused_since": self.paused_since,
                "now": time.time(), "input": self.input_open, "rest": self.mode}

    def _shown_mode(self, mode: str) -> str:
        """M7: while an approval card waits, a resting island shows approval_needed."""
        return "approval_needed" if self.approvals and mode in ("idle", "off") else mode

    def _approvals_changed(self):
        """The first card came / the last one went: approval_needed in or out (resting only;
        a voice state keeps going, and the card waits under it)."""
        shown = self._shown_mode(self.mode)
        if shown == self.island_state:
            return
        self.island_state = shown
        for d in self._chars():
            d.set_mode(shown)
        if self.input_open or self.engine_state not in ("ready", "off", "paused"):
            return
        for fn, args in route(shown, self.text, self._route_ctx()):
            if fn == "go" and args[0] == self.engine_state:
                continue                       # already there (Ember: approval_needed falls back to rest)
            self._send(fn, args)
            if fn == "go":
                self.engine_state = args[0]

    # -- one request = one open panel (M5 turn continuity) -----------------------------
    question = ""

    def start_turn(self, info: dict):
        """A request starts: its question stays on the panel until the panel collapses."""
        self.question = str((info or {}).get("question") or "").strip()
        self.model.questionShown.emit(self.question)

    def spoken(self, text: str, secs: float):
        """A sentence of the answer starts playing: reveal it in step with the voice."""
        self._send("spoken", [text, int(max(0.2, float(secs)) * 1000)])

    def _status_line(self, text: str) -> str:
        """The thinking head's line while a question is up: a status, never the question again."""
        q = self.question
        t = (text or "").strip()
        if not q:
            return t or "…"
        if t == q or not t or t == "…":
            return "thinking…"
        if t.endswith(q):
            return t[: -len(q)].strip() or "thinking…"
        return t

    def _listening(self, on: bool, until: float):
        if until and until > time.time():
            self.paused_until = float(until)
            self.paused_since = time.time()
        else:
            self.paused_until = 0.0
        if self.engine_state == "paused" and self.paused_until:
            self._send("setPaused", [self.paused_until * 1000])

    def push_level(self, v: float):
        self._send("pushLevel", [float(v)])
        if self.companion is not None:
            self.companion.levels(level=v if self.mode in ("listening", "holding", "followup", "dictating") else 0.0)

    def push_tts(self, v: float):
        self._send("pushTts", [float(v)])
        if self.companion is not None:
            self.companion.levels(amplitude=v if self.mode == "speaking" else 0.0)

    def set_tasks(self, rows: list):
        before = {r["id"]: r["status"] for r in self.task_rows}
        self.task_rows = list(rows)
        self._send("setTasks", [self._rows()])
        running = sum(1 for r in rows if r.get("status") == "running")
        for d in self._chars():
            if any(before.get(r["id"]) == "running" and r.get("status") == "done" for r in rows):
                d.event("done")
            elif any(before.get(r["id"]) == "running" and r.get("status") == "failed" for r in rows):
                d.event("error")
            if running != d.tasks:
                d.set_mode(self.island_state, tasks=running)
        if any(before.get(r["id"]) != r["status"] for r in rows):
            self._send("popList", [6000])
        for r in rows:
            lw = self.log_windows.get(r["id"])
            if lw:
                lw.update_task(r)

    def _rows(self) -> list[dict]:
        return merged_rows(self.task_rows, self.sessions)

    # -- M7: watched Claude Code sessions -----------------------------------------------
    def set_sessions(self, sessions: list):
        """SessionHub's snapshot: they join the hover list and the satellites (working =
        running). The list pops up for a new session or one that now waits, finished or
        failed; working <-> idle churn doesn't."""
        before = {str(s.get("id")): s.get("status") for s in self.sessions}
        self.sessions = [dict(s) for s in (sessions or []) if isinstance(s, dict) and s.get("id")]
        self._send("setTasks", [self._rows()])
        for s in self.sessions:
            old, new = before.get(str(s["id"])), s.get("status")
            if old is None or (old != new and new in ("waiting", "done", "error")):
                self._send("popList", [6000])
                break

    def find_session(self, sid: str) -> dict | None:
        for s in self.sessions:
            if str(s.get("id")) == sid:
                return s
        return None

    def _session_action(self, sid: str, action: str):
        if action not in SESSION_ACTIONS or not sid:
            return
        print(f"[sessions] {action} {sid[:8]}")
        if action == "dismiss":                    # gone at once; the hub's next snapshot agrees
            self.sessions = [s for s in self.sessions if str(s.get("id")) != sid]
            self._send("setTasks", [self._rows()])
        self.session_action.emit(sid, action)

    def open_diff(self, title: str, diffs: list):
        """A session's edits in a window (hookrelay/diffview.py); one window per title."""
        from .hookrelay.diffview import DiffWindow
        w = self.diff_windows.get(title)
        if w is not None:
            try:
                w.set_diffs(diffs)
            except RuntimeError:                   # its C++ side is gone (closed)
                w = None
        if w is None:
            w = DiffWindow(title, diffs)
            self.diff_windows[title] = w
            w.destroyed.connect(lambda *_, t=title: self.diff_windows.pop(t, None))
        w.show()
        w.raise_()
        w.activateWindow()
        return w

    def set_stt(self, label: str):
        old = self.stt_label
        self.stt_label = label
        self._send("setBadge", [stt_short(label), badge_dir(old, label)])

    def set_voice(self, label: str):
        self.voice_label = label

    def set_wake_label(self, label: str):
        self.wake_label = label

    def _get_wake_label(self):
        return self._wake_label

    def _set_wake_label(self, v):
        self._wake_label = v
        self._send("setWakeLabel", [v])

    wake_label = property(_get_wake_label, _set_wake_label)

    def _get_show_until(self):
        return 0.0

    def _set_show_until(self, t):
        ms = max(0, int((float(t) - time.time()) * 1000))
        if ms:
            self._send("popList", [ms])

    show_tasks_until = property(_get_show_until, _set_show_until)

    def set_partial(self, text: str):
        self.partial = text if self.mode in ("listening", "holding", "followup") else ""
        self._send("setPartial", [self.partial])
        self._update_not_me()

    def _wake_hit(self, t: float):
        self.last_wake_t = t
        self.wake_listening = True
        for d in self._chars():
            d.event("woke")
        if not self.input_open:
            self._send("go", ["woke", {}])
            self.engine_state = "listening"
        self._update_not_me()

    def voice_mismatch(self, *_):
        for d in self._chars():
            d.event("voice_mismatch")
        self._send("go", ["mismatch", {}])
        self.engine_state = "ready"

    def _update_not_me(self):
        on = self.mode == "listening" and self.wake_listening and not self.partial
        self._send("setNotMe", [on])

    def _not_me_clicked(self):
        self.wake_listening = False
        self._update_not_me()
        self.not_me.emit()

    def flash(self, text: str, secs: float = 2.5):
        self._send("flashLabel", [text, int(secs * 1000)])

    # -- talk-or-type ------------------------------------------------------------------
    def attach_input(self, open_mic, cancel_mic, submit, closed=None):
        from .inputbox import InputSession
        self._on_closed = closed
        self._submit_cb = submit
        self.session = InputSession(open_mic, cancel_mic, self._submit_with_attachment,
                                    show=self._show_input, close=self._hide_input)
        self.attachment: list[str] = []

    def _submit_with_attachment(self, text: str):
        att, self.attachment = getattr(self, "attachment", []), []
        wrap, self._wrap = getattr(self, "_wrap", None), None
        if wrap is not None:
            text = wrap(text)
        if att:
            from .widgets import attachment_prompt
            text = attachment_prompt(att, text)
        self._submit_cb(text)

    def open_input(self, prefill: str = "", wrap=None):
        """Open the talk-or-type field with some text in it; `wrap(text)` turns what
        the user sends into the request (e.g. a Telegram draft being edited)."""
        if self.session is None:
            return
        self._wrap = wrap
        if not self.session.is_open:
            self.session.hotkey()
        if prefill:
            self._send("inputText", [prefill])

    def toggle_input(self):
        if self.session is not None:
            if self.session.is_open:
                print("[input] hotkey pressed again: closing the field")
            self.session.hotkey()

    def _show_input(self):
        from .winsys import force_foreground, foreground_window, set_no_activate
        self._prev_fg = foreground_window()
        self._restore_focus = True
        self._away_since = 0.0
        self._opened_at = time.monotonic()
        self._field_text, self._last_edit = "", self._opened_at
        self.input_open = True
        hwnd = int(self.view.winId())
        set_no_activate(hwnd, False)
        self._send("openInput", [", ".join(Path(p).name for p in getattr(self, "attachment", []))])
        self.engine_state = "input"
        self.view.requestActivate()
        ok = force_foreground(hwnd)
        self._focus_timer.start()
        print(f"[input] open (focus {'ok' if ok else 'NOT taken'})")

    def _hide_input(self, submitted: bool):
        from .winsys import restore_foreground, set_no_activate
        self.input_open = False
        self._focus_timer.stop()
        self._send("closeInput", [bool(submitted)])
        set_no_activate(int(self.view.winId()), True)
        if getattr(self, "_on_closed", None):
            self._on_closed()
        if self._restore_focus and self._prev_fg:
            restore_foreground(self._prev_fg)
        self._prev_fg = 0
        if not submitted:
            self.attachment = []
            self._wrap = None
        print(f"[input] {'sent' if submitted else 'cancelled'}")
        if not submitted:
            self.engine_state = "ready"
            self.set_state(self.mode, self.text)        # back to whatever the assistant shows
        else:
            self.engine_state = "thinking"
            self._send("go", ["thinking", {"text": "…"}])

    def _heard(self, text: str):
        if self.session is None or not self.input_open:
            return
        if text.strip():
            self._send("inputText", [text])
        self.session.heard(text)

    def _check_focus(self):
        if not self.input_open or time.monotonic() - self._opened_at < 0.5:
            return
        s = self.session
        if (s is not None and s.state == "open" and not getattr(self, "_field_text", "").strip()
                and time.monotonic() - max(self._opened_at, getattr(self, "_last_edit", 0.0)) > FIELD_IDLE_S):
            # Heard nothing, nothing typed: close it, so the wake word works again
            # (while the field is open the wake word is off: you might be typing).
            print(f"[input] nothing said or typed for {FIELD_IDLE_S}s: closing the field")
            s.cancel()
            return
        from .winsys import foreground_is_ours, foreground_window, window_title
        if foreground_is_ours():
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

    # -- file drop ---------------------------------------------------------------------
    def _files_dropped(self, urls: list[str]):
        paths = []
        for u in urls:
            p = QUrl(u).toLocalFile() if u.startswith("file:") else ""
            if p and os.path.exists(p):
                paths.append(os.path.normpath(p))
        if not paths:
            self.flash("only files can be dropped here")
            return
        print(f"[drop] {len(paths)} file(s): {', '.join(Path(p).name for p in paths)}")
        self.files_dropped.emit(paths)
        if self.session is not None:
            self.attachment = paths
            if not self.session.is_open:
                self.session.hotkey()               # opens the field (mic on) with the file attached
            else:
                self._send("openInput", [", ".join(Path(p).name for p in paths)])

    # -- M9 effects ----------------------------------------------------------------------
    def show_effect(self, ev: dict):
        self.effect = dict(ev)
        self.model.effectShown.emit(_qml_safe(ev))

    def _effect_art(self, effect_id: str, data):
        img = QImage()
        if data and img.loadFromData(bytes(data)):
            self.art.images[f"art-{effect_id}"] = img
            self.model.effectArt.emit(effect_id, f"image://island/art-{effect_id}")

    def _effect_clicked(self, kind: str, ev_id: str):
        print(f"[effects] {kind} clicked")
        sig = {"undo": self.effect_undo, "confirm": self.effect_confirm, "cancel": self.effect_cancel}.get(kind)
        if sig is not None:
            sig.emit(ev_id)

    # -- response widgets ---------------------------------------------------------------
    def show_widget(self, spec: dict):
        from .widgets import for_qml
        w = for_qml(spec)
        if w is None:
            return
        self.widget = w
        self.model.widgetShown.emit(w)
        if w.get("widget") == "weather":            # M6: weather-conditioned antics read the last forecast shown
            icon = str((w.get("data") or {}).get("icon", ""))
            self._weather_seen = (CH.ICON_WEATHER.get(icon, ""), time.time())

    def clear_widget(self):
        self.widget = None
        self.model.widgetCleared.emit()

    def clear_timer_widget(self):
        """M10: a timer cancelled by voice: its card goes (any other card stays)."""
        if (self.widget or {}).get("widget") == "timer":
            self.clear_widget()

    def _widget_action(self, wid: str, action: str, payload):
        print(f"[widget] {action} on {wid}")
        w = self.widget or {}
        if action in ("dismiss", "cancel"):
            self.widget = None
        elif action == "edit" and w.get("widget") == "telegram_draft":
            chat = (w.get("data") or {}).get("chat", "")
            self.open_input(str(payload or ""), wrap=lambda t, c=chat: f"Change the Telegram draft to {c} to this "
                            f"(show it again as a telegram_draft card, don't send it): {t}")
        elif action == "send":
            self.flash("sending needs approvals (coming in M7)", 3.0)
        self.widget_action.emit(wid, action, payload)

    def _open_url(self, url: str):
        from .widgets import safe_open
        safe_open(url)

    # -- service icons for tools in use ------------------------------------------------
    def tool_event(self, ev: dict):
        """{"id", "name", "phase": "start" | "end", "input"?} from the tool agent (or an M7 session)."""
        from .service_icons import describe
        tid = str(ev.get("id") or ev.get("name"))
        if ev.get("phase") == "end":
            self.tools.pop(tid, None)
        else:
            d = describe(ev.get("name", ""), ev.get("input") or {}, self.cfg.get("service_icons") or {},
                         fetch=bool(self.cfg.get("fetch_favicons", False)))
            if d is None:
                return
            self.tools[tid] = d
        self.model.toolsChanged.emit(list(self.tools.values()) if self.cfg.get("icons", True) else [])
        if self.tools:
            d = list(self.tools.values())[-1]
            n = len(self.tools)
            label = d["label"] + (f"  +{n - 1}" if n > 1 else "")
            self._send("setTool", [label, ""])
        elif self.mode != "thinking":
            self._send("setTool", ["", ""])
        # else: the last tool's label stays until the answer starts (going back to
        # "Let me check." / "checking with your tools…" would look like it started over)

    # -- plan usage ----------------------------------------------------------------------
    def set_usage(self, info: dict):
        self.model._usage = dict(info or {})
        self.model.usageChanged.emit()
        self._send("setUsageRow", [self.model._usage_on and bool(self.model._usage)])

    # -- M7: approval cards and session events ------------------------------------------
    def show_approval(self, req: dict):
        """{"id", "title", "detail"?, "tool"?, "session"?, "actions": [{"id", "label", "primary"?}], "ttl_s"?}"""
        if not req.get("id"):
            return
        req = dict(req)
        req.setdefault("actions", [{"id": "allow", "label": "Allow", "primary": True}, {"id": "deny", "label": "Deny"}])
        if req.get("tool"):
            from .service_icons import describe
            d = describe(str(req["tool"]), {}, self.cfg.get("service_icons") or {})
            if d:
                req.setdefault("icon_url", d.get("icon_url", ""))
                req.setdefault("service", d.get("service", ""))
        self.approvals[req["id"]] = req
        self.model.approvalShown.emit(_qml_safe(req))
        self._approvals_changed()

    def withdraw_approval(self, ap_id: str):
        if self.approvals.pop(ap_id, None) is not None:
            self.model.approvalCleared.emit(ap_id)
            self._approvals_changed()

    APPROVAL_ACTIONS = ("allow", "deny", "always", "pass")

    def _approval_decided(self, ap_id: str, action: str):
        if action not in self.APPROVAL_ACTIONS or ap_id not in self.approvals:
            return
        self.approvals.pop(ap_id, None)
        self.model.approvalCleared.emit(ap_id)          # however it was answered, the card goes
        print(f"[approval] {ap_id}: {action}")
        self.approval_decided.emit(ap_id, action)
        self._approvals_changed()

    def _approval_answered(self, ap_id: str, answers):
        """A question card's Submit: {question text: label | [labels]} (the relay validates it again)."""
        if ap_id not in self.approvals:
            return
        ans = {}
        for k, v in dict(_v(answers) or {}).items():
            v = _v(v)
            ans[str(k)] = [str(x) for x in v] if isinstance(v, (list, tuple)) else str(v)
        self.approvals.pop(ap_id, None)
        self.model.approvalCleared.emit(ap_id)
        print(f"[approval] {ap_id}: answered {len(ans)} question(s)")
        self.approval_answered.emit(ap_id, ans)
        self._approvals_changed()

    def session_event(self, ev: dict):
        """{"session", "kind": tool | tool_done | idle | done | error | started, "tool"?, "text"?}"""
        kind = ev.get("kind")
        sid = f"session:{ev.get('session', '')}"
        if kind == "tool" and ev.get("tool"):
            self.tool_event({"id": sid, "name": ev["tool"], "phase": "start", "input": ev.get("input") or {}})
        elif kind in ("tool_done", "idle", "done", "error"):
            self.tool_event({"id": sid, "phase": "end"})
        if kind == "done":
            # the one-shot "check" with "done · <title>" at rest (a tint only in a voice state)
            self._send("sessionDone", [self._event_label(ev)])
        elif kind == "error":
            label = self._event_label(ev)
            text = " ".join(str(ev.get("text") or "").split())
            msg = f"{label}: {text}" if text else f"{label} stopped"
            if (self.mode in ("idle", "off") and not self.input_open and not self.approvals
                    and self.engine_state in ("ready", "off", "error")):
                self._send("go", ["error", {"text": msg}])   # the flinch; it settles back to rest by itself
                self.engine_state = "error"
            elif self.mode == "idle":
                self.flash(msg, 3.0)

    def _event_label(self, ev: dict) -> str:
        if ev.get("title"):
            return str(ev["title"])
        s = self.find_session(str(ev.get("session") or ""))
        return session_label(s) if s else (str(ev.get("session") or "session")[:8] or "session")

    # -- tasks: log windows, menu ----------------------------------------------------------
    def _open_row(self, row_id: str):
        for r in self.task_rows:
            if str(r.get("id")) == row_id:
                self.open_log(r)
                return

    def _open_task(self, i: int):
        rows = self.task_rows[-5:]
        if 0 <= i < len(rows):
            self.open_log(rows[i])

    def open_log(self, row: dict):
        from .island import LogWindow
        w = self.log_windows.get(row["id"])
        if w is None:
            w = LogWindow(row, self.tm)
            self.log_windows[row["id"]] = w
        w.show()
        w.raise_()
        w.activateWindow()

    def _menu(self, at: QPoint):
        from PySide6.QtWidgets import QMenu
        m = QMenu()
        m.addAction("Talk now", self.activate.emit)
        m.addAction("Stop speaking", self.stop_speaking.emit)
        if self.last_wake_t and time.time() - self.last_wake_t < self.NOT_ME_S:
            a = m.addAction(f"That wasn't me (the wake {int(time.time() - self.last_wake_t)} s ago)", self._not_me_clicked)
            a.setToolTip("Saves the audio that woke it as a \"not the wake word\" example for the next "
                         "Settings → Wake word tune-up.")
        if self.widget is not None:
            m.addAction("Dismiss the card", self.clear_widget)
        if self.stt_label:
            m.addAction(f"Speech-to-text: {self.stt_label}").setEnabled(False)
        if self.voice_label:
            m.addAction(f"Voice: {self.voice_label}").setEnabled(False)
        m.addSeparator()
        m.addAction("Open logs folder", lambda: os.startfile(str(ROOT / "state" / "logs")))
        m.addSeparator()
        m.addAction("Quit", self.quit_requested.emit)
        self._menu_ref = m
        m.exec(at)

    # -- debugging / tests ---------------------------------------------------------------
    def grab(self) -> QImage:
        return self.view.grabWindow()

    def call(self, fn: str, *args):
        """Call a function of Island.qml (tests, the motion lab)."""
        from PySide6.QtCore import QMetaObject, Q_RETURN_ARG, Q_ARG
        ret = QMetaObject.invokeMethod(self.root, fn, Qt.DirectConnection, Q_RETURN_ARG("QVariant"),
                                       *[Q_ARG("QVariant", a) for a in args])
        return ret

    def close(self):
        self._char_timer.stop()
        if self.companion is not None:
            self.companion.close()
        self._env_timer.stop()
        self._focus_timer.stop()
        self.view.close()


def _v(x):
    return x.toVariant() if hasattr(x, "toVariant") else x


def _qml_safe(v):
    """JSON-ish values only (QVariantMap can't carry bytes or sets)."""
    if isinstance(v, dict):
        return {str(k): _qml_safe(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_qml_safe(x) for x in v]
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    return str(v)
