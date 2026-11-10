"""Live activities: what a plugin shows on the island without being asked (docs/plugin-api.md).

A plugin declares its activities in plugin.json (`contributes.live_activities`) and its
service pushes them through `ctx.activities` (a ScopedActivities):

    key = ctx.activities.start("track", {"title": "Song", "line": "first lyric line"})
    ctx.activities.update("track", {"line": "next line"})          # a discrete update
    ctx.activities.update("call", alert=True)                      # wants the expanded view now
    ctx.activities.end("track")

`ActivityHub` decides what the island shows (`layout()`): the highest priority wins the pill
(compact, or expanded while it alerts or the user opened it), the runner-up gets the small
minimal slot, the rest wait. Equal priority: the one that started first keeps its place (no
flapping). While the assistant is in a conversation (listening, thinking, talking, an approval
card) it owns the island: only activities above ASSISTANT_PRIORITY (an incoming call) show.

Frame-rate budget (ui/states.json budgets): the island runs at 0 fps at rest. A discrete
update animates for TRANSITION_S at the live rate, then back to 0. Continuous animation must
be declared in the manifest (fps + fps_reason) and is capped: idle_fps below "active"
priority, antic_fps above. Reduced motion (or a hidden island) turns continuous animation off.
"""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

PRIORITY = {"ambient": 10, "info": 30, "active": 50, "alert": 80, "critical": 95}
ASSISTANT_PRIORITY = 70            # a conversation in progress; only alert and above interrupt it
# What the island can draw by itself. A plugin's own QML falls back to its template, and every
# template falls back to "glance" (an icon and a line), so an activity always shows something.
TEMPLATES = {"glance": None, "progress": "glance", "countdown": "glance", "media": "progress", "call": "glance"}
TRANSITION_S = 0.4                 # a discrete update animates this long, then 0 fps
MIN_UPDATE_S = 0.25                # updates closer together than this are coalesced
ALERT_MAX_S = 30.0                 # an alert collapses to compact after this, unless the template is "call"
MAX_ACTIVITIES_PER_PLUGIN = 4
_ACT_ID = re.compile(r"^[a-z][a-z0-9_-]{0,40}$")


def _budgets() -> dict:
    try:
        from . import states
        return dict(states.load().get("budgets") or {})
    except Exception:
        return {"idle_fps": 8, "antic_fps": 30, "live_fps": 60}


def priority_value(p) -> int | None:
    if isinstance(p, bool):
        return None
    if isinstance(p, (int, float)) and 0 <= p <= 100:
        return int(p)
    return PRIORITY.get(str(p)) if isinstance(p, str) else None


def fps_cap(priority: int, budgets: dict | None = None) -> int:
    b = budgets or _budgets()
    return int(b.get("antic_fps", 30) if priority >= PRIORITY["active"] else b.get("idle_fps", 8))


def validate_spec(a, folder: Path | None = None) -> list[str]:
    """Problems with one `live_activities` entry of a manifest ([] = good)."""
    if not isinstance(a, dict):
        return ["must be an object"]
    out = []
    if not isinstance(a.get("id"), str) or not _ACT_ID.match(a["id"]):
        out.append("id must be lower-case letters, digits, - or _")
    if a.get("template", "glance") not in TEMPLATES:
        out.append(f"template is one of {', '.join(TEMPLATES)}")
    pr = priority_value(a.get("priority", "info"))
    if pr is None:
        out.append(f"priority is one of {', '.join(PRIORITY)} or 0..100")
    fps = a.get("fps", 0)
    if not isinstance(fps, int) or isinstance(fps, bool) or fps < 0:
        out.append("fps must be a whole number >= 0 (0: discrete updates only)")
    elif fps > 0:
        if not str(a.get("fps_reason") or "").strip():
            out.append("continuous animation (fps > 0) needs fps_reason")
        cap = fps_cap(pr if pr is not None else 0)
        if fps > cap:
            out.append(f"fps {fps} is above the budget for its priority ({cap})")
    q = a.get("qml")
    if q is not None:
        if not isinstance(q, str) or not q.endswith(".qml"):
            out.append("qml must be a .qml file in the plugin's folder")
        elif folder is not None and not (folder / q).is_file():
            out.append(f"no {q}")
    acts = a.get("actions", [])
    if not isinstance(acts, list):
        out.append("actions must be a list")
    else:
        for x in acts:
            if not isinstance(x, dict) or not x.get("id") or not x.get("label"):
                out.append("an action needs an id and a label")
            elif not isinstance(x.get("voice", []), list):
                out.append(f"action {x['id']!r}: voice must be a list of phrases")
    return out


@dataclass
class Activity:
    key: str                       # "<plugin>:<id>"
    plugin: str
    id: str
    template: str
    priority: int
    data: dict
    fps: int = 0
    qml: str = ""                  # file URL of the plugin's own component, or ""
    actions: list = field(default_factory=list)
    started: float = 0.0
    updated: float = 0.0
    expires: float | None = None
    alert_since: float | None = None
    seq: int = 0

    def view(self, presentation: str) -> dict:
        """What the island gets (QVariantMap-safe)."""
        return {"key": self.key, "plugin": self.plugin, "id": self.id, "template": self.template,
                "fallback": TEMPLATES.get(self.template) or "", "priority": self.priority, "data": dict(self.data),
                "qml": self.qml, "actions": [dict(a) for a in self.actions], "presentation": presentation,
                "fps": self.fps, "seq": self.seq}


class ActivityHub:
    def __init__(self, clock=time.monotonic, budgets: dict | None = None):
        self.clock = clock
        self.budgets = budgets or _budgets()
        self._acts: dict[str, Activity] = {}
        self._handlers: dict[str, object] = {}         # key -> on_action(action_id) -> str | None
        self._lock = threading.RLock()
        self._listeners: list = []
        self._busy = False
        self._reduced = False
        self._expanded: str | None = None               # the activity the user opened
        self._last_change = -1e9
        self._last_notify = -1e9
        self._dirty = False
        self._seq = 0
        self._last_layout: dict | None = None

    # -- plugins -----------------------------------------------------------------------------
    def scoped(self, plugin: str, specs: dict[str, dict]) -> "ScopedActivities":
        return ScopedActivities(self, plugin, specs)

    def start(self, plugin: str, aid: str, data: dict | None = None, *, template: str = "glance",
              priority=None, fps: int = 0, qml: str = "", actions=None, ttl_s: float | None = None,
              alert: bool = False, on_action=None) -> str:
        pr = priority_value(priority if priority is not None else "info")
        if pr is None:
            raise ValueError(f"bad priority {priority!r}")
        if template not in TEMPLATES:
            template = "glance"
        key = f"{plugin}:{aid}"
        now = self.clock()
        with self._lock:
            mine = [a for a in self._acts.values() if a.plugin == plugin and a.key != key]
            if len(mine) >= MAX_ACTIVITIES_PER_PLUGIN:
                raise RuntimeError(f"{plugin} already has {len(mine)} live activities")
            old = self._acts.get(key)
            self._seq += 1
            self._acts[key] = Activity(
                key=key, plugin=plugin, id=aid, template=template, priority=pr, data=dict(data or {}),
                fps=min(int(fps or 0), fps_cap(pr, self.budgets)), qml=qml, actions=list(actions or []),
                started=old.started if old else now, updated=now,
                expires=now + ttl_s if ttl_s else None, alert_since=now if alert else None, seq=self._seq)
            if on_action is not None:
                self._handlers[key] = on_action
        self._changed(force=True)
        return key

    def update(self, key: str, data: dict | None = None, *, replace: bool = False, priority=None,
               alert: bool | None = None, ttl_s: float | None = None) -> bool:
        """Merge `data` into the activity (replace=True: swap it). False if it isn't running."""
        now = self.clock()
        with self._lock:
            a = self._acts.get(key)
            if a is None:
                return False
            if data is not None:
                a.data = dict(data) if replace else {**a.data, **data}
            if priority is not None:
                pr = priority_value(priority)
                if pr is not None:
                    a.priority = pr
                    a.fps = min(a.fps, fps_cap(pr, self.budgets))
            if alert is not None:
                a.alert_since = now if alert else None
                if not alert and self._expanded == key:
                    self._expanded = None
            if ttl_s is not None:
                a.expires = now + ttl_s if ttl_s else None
            a.updated = now
            self._seq += 1
            a.seq = self._seq
        self._changed(force=alert is not None or priority is not None)
        return True

    def end(self, key: str, reason: str = "") -> bool:
        with self._lock:
            a = self._acts.pop(key, None)
            self._handlers.pop(key, None)
            if self._expanded == key:
                self._expanded = None
        if a is None:
            return False
        self._changed(force=True)
        return True

    def end_plugin(self, plugin: str, reason: str = ""):
        with self._lock:
            keys = [k for k, a in self._acts.items() if a.plugin == plugin]
        for k in keys:
            self.end(k, reason)

    # -- the assistant and the user ------------------------------------------------------------
    def set_busy(self, busy: bool):
        """The assistant is in a conversation (or an approval card is up): it owns the island."""
        if bool(busy) != self._busy:
            self._busy = bool(busy)
            self._changed(force=True)

    def set_reduced_motion(self, on: bool):
        if bool(on) != self._reduced:
            self._reduced = bool(on)
            self._changed(force=True)

    def expand(self, key: str | None):
        """The user opened an activity (hover / click), or closed it (None)."""
        with self._lock:
            self._expanded = key if key in self._acts else None
        self._changed(force=True)

    def act(self, key: str, action: str) -> str | None:
        """A button on the activity, or its voice phrase: the plugin's handler runs (its reply,
        if any, is spoken). A handler that raises ends the activity."""
        with self._lock:
            a = self._acts.get(key)
            fn = self._handlers.get(key)
        if a is None or not any(x.get("id") == action for x in a.actions):
            return None
        if fn is None:
            return None
        try:
            r = fn(action)
        except Exception as e:
            print(f"[activity] {key} {action}: {e}")
            self.end(key, "its action failed")
            return None
        return r if isinstance(r, str) else None

    def match_voice(self, text: str) -> tuple[str, str] | None:
        """(key, action) when `text` is one of the voice phrases of what the island shows right
        now (the primary activity; e.g. "answer" while a call rings), else None."""
        lay = self.layout()
        p = lay.get("primary")
        if not p:
            return None
        t = " ".join(re.sub(r"[^\w\s']", " ", text.lower()).split())
        for x in p["actions"]:
            for phrase in x.get("voice") or []:
                ph = " ".join(str(phrase).lower().split())
                if ph and (t == ph or re.fullmatch(rf"(?:(?:ok(?:ay)?|please|yes|hey)\s+)*{re.escape(ph)}(?:\s+(?:it|please|now))*", t)):
                    return p["key"], x["id"]
        return None

    # -- what the island shows -------------------------------------------------------------------
    def tick(self) -> bool:
        """Expire activities past their ttl, flush a coalesced update. The island calls it
        (about 4 Hz) while anything is running. True if the layout changed."""
        now = self.clock()
        with self._lock:
            gone = [k for k, a in self._acts.items() if a.expires is not None and now >= a.expires]
        for k in gone:
            self.end(k, "expired")
        if self._dirty and now - self._last_notify >= MIN_UPDATE_S:
            self._notify()
            return True
        return bool(gone)

    def running(self) -> bool:
        return bool(self._acts)

    def layout(self) -> dict:
        """{"primary": view | None, "secondary": view | None, "waiting": [keys], "hidden": [keys],
        "fps": int}. primary's presentation is compact or expanded; secondary's is minimal."""
        now = self.clock()
        with self._lock:
            acts = [a for a in self._acts.values() if a.expires is None or now < a.expires]
            for a in acts:
                if a.alert_since is not None and a.template != "call" and now - a.alert_since > ALERT_MAX_S:
                    a.alert_since = None
            hidden = [a for a in acts if self._busy and a.priority <= ASSISTANT_PRIORITY]
            shown = sorted((a for a in acts if a not in hidden), key=lambda a: (-a.priority, a.started, a.key))
            primary = shown[0] if shown else None
            secondary = shown[1] if len(shown) > 1 and not (primary and self._is_expanded(primary)) else None
            out = {"primary": primary.view("expanded" if self._is_expanded(primary) else "compact") if primary else None,
                   "secondary": secondary.view("minimal") if secondary else None,
                   "waiting": [a.key for a in shown[2 if secondary else 1:]],
                   "hidden": [a.key for a in hidden],
                   "fps": self._fps(now, [a for a in (primary, secondary) if a is not None])}
        return out

    def _is_expanded(self, a: Activity) -> bool:
        return a.alert_since is not None or self._expanded == a.key

    def _fps(self, now: float, shown: list) -> int:
        fps = 0
        if now - self._last_change < TRANSITION_S:
            fps = int(self.budgets.get("live_fps", 60))
        if not self._reduced:
            fps = max([fps] + [min(a.fps, fps_cap(a.priority, self.budgets)) for a in shown])
        return fps

    def subscribe(self, fn):
        """fn(layout) when what the island shows changes (any thread). Returns an unsubscribe."""
        self._listeners.append(fn)
        return lambda: self._listeners.remove(fn) if fn in self._listeners else None

    def _changed(self, force: bool = False):
        now = self.clock()
        self._last_change = now
        if force or now - self._last_notify >= MIN_UPDATE_S:
            self._notify()
        else:
            self._dirty = True                 # coalesced: tick() sends it

    def _notify(self):
        self._dirty = False
        self._last_notify = self.clock()
        lay = self.layout()
        self._last_layout = lay
        for fn in list(self._listeners):
            try:
                fn(lay)
            except Exception as e:
                print(f"[activity] a listener failed: {e}")


class ScopedActivities:
    """A plugin's handle on the hub: only its own declared activities, never above the
    priority or frame rate its manifest declared."""

    def __init__(self, hub: ActivityHub, plugin: str, specs: dict[str, dict]):
        self.hub, self.plugin, self.specs = hub, plugin, specs

    def _spec(self, aid: str) -> dict:
        s = self.specs.get(aid)
        if s is None:
            raise KeyError(f"{self.plugin} didn't declare a live activity {aid!r} in plugin.json")
        return s

    def start(self, aid: str, data: dict | None = None, *, priority=None, ttl_s: float | None = None,
              alert: bool = False, on_action=None) -> str:
        s = self._spec(aid)
        cap = priority_value(s.get("priority", "info"))
        pr = priority_value(priority) if priority is not None else cap
        if pr is None or pr > cap:
            pr = cap                                       # never above what the manifest declared
        qml = ""
        if s.get("qml") and s.get("dir"):
            qml = (Path(s["dir"]) / s["qml"]).resolve().as_uri()
        return self.hub.start(self.plugin, aid, data, template=s.get("template", "glance"), priority=pr,
                              fps=int(s.get("fps", 0)), qml=qml, actions=s.get("actions") or [], ttl_s=ttl_s,
                              alert=alert, on_action=on_action)

    def update(self, aid: str, data: dict | None = None, **kw) -> bool:
        s = self._spec(aid)
        if "priority" in kw and kw["priority"] is not None:
            cap = priority_value(s.get("priority", "info"))
            pr = priority_value(kw["priority"])
            kw["priority"] = cap if pr is None or pr > cap else pr
        return self.hub.update(f"{self.plugin}:{aid}", data, **kw)

    def end(self, aid: str, reason: str = "") -> bool:
        self._spec(aid)
        return self.hub.end(f"{self.plugin}:{aid}", reason)


_HUB: ActivityHub | None = None


def get() -> ActivityHub:
    """The app's hub."""
    global _HUB
    if _HUB is None:
        _HUB = ActivityHub()
    return _HUB
