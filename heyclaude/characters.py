"""Character packs (ROADMAP M6): a swappable character behind one interface.

A pack is a folder `characters/<id>/` (built in, by PR) or `state/characters/<id>/`
(local experiments, git-ignored) with:

    character.json   id, name, author, licence, registry, implements, bounds, antics...
    Character.qml    the QML interface (ui/qml/HeyClaude/Character/CanvasCharacter.qml)

The app talks to it in registry states (ui/states.json): `resolve()` walks a state the
pack doesn't implement down its fallback chain (logged once per pack and state), so a
registry that gains states never breaks an older pack.

Idle antics are the pack's own (character.json "antics"); `AnticScheduler` picks when
and which, and `Sleep` turns a long absence into drowsy -> asleep -> waking.
"""
from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import states as S
from .config import ROOT, STATE_DIR

BUILTIN_DIR = ROOT / "characters"
USER_DIR = STATE_DIR / "characters"
KIT_IMPORT_DIR = ROOT / "ui" / "qml"           # import HeyClaude.Character
NONE = "ember"                                 # no character: the island theme's own ember

DEFAULT_BOUNDS = [-60, -60, 120, 120]
DEFAULT_DESKTOP_BOUNDS = [-100, -80, 200, 170]
WHERE = ("pill", "desktop")
WEATHER = ("clear", "cloudy", "rain", "snow", "storm", "fog", "wind")
FREQUENCY_S = {"rare": 600.0, "normal": 240.0, "lively": 90.0}   # mean gap between antics while idle


# ------------------------------------------------------------------ manifests
def _box_inside(inner, outer) -> bool:
    x, y, w, h = inner
    X, Y, W, H = outer
    return x >= X and y >= Y and x + w <= X + W and y + h <= Y + H


def _is_box(b) -> bool:
    return isinstance(b, list) and len(b) == 4 and all(isinstance(v, (int, float)) for v in b) and b[2] > 0 and b[3] > 0


def validate(man: dict, folder: Path | None = None, reg: dict | None = None) -> list[str]:
    """Everything wrong with a manifest ([] = good). The CI checks the pack also has to
    pass at runtime (tools/character_lab.py --check) are separate."""
    reg = reg or S.load()
    out: list[str] = []
    for k in ("id", "name", "author", "licence", "registry", "implements"):
        if not man.get(k):
            out.append(f"missing {k!r}")
    pid = str(man.get("id", ""))
    if pid and (not pid.replace("-", "").replace("_", "").isalnum() or pid != pid.lower()):
        out.append("id must be lower-case letters, digits, - or _")
    if folder is not None and pid and folder.name != pid:
        out.append(f"id {pid!r} doesn't match the folder {folder.name!r}")
    if pid == NONE:
        out.append(f"id {NONE!r} is reserved for 'no character'")
    if man.get("registry") and not S.compatible(man["registry"], reg):
        out.append(f"registry {man['registry']} isn't compatible with {reg['version']}")
    known = {s["id"] for s in reg["states"]}
    impl = man.get("implements") or []
    missing = set(reg["core"]) - set(impl)
    if missing:
        out.append("core states missing: " + ", ".join(sorted(missing)))
    for st in impl:
        if st not in known:
            out.append(f"implements unknown state {st!r}")
    b = man.get("bounds", DEFAULT_BOUNDS)
    db = man.get("desktop_bounds", DEFAULT_DESKTOP_BOUNDS)
    for name, box in (("bounds", b), ("desktop_bounds", db)):
        if not _is_box(box):
            out.append(f"{name} must be [x, y, w, h] in box units")
        elif not _box_inside([-50, -50, 100, 100], box):
            out.append(f"{name} must contain the 100-unit box")
    oneshot = man.get("oneshot", {})
    if not isinstance(oneshot, dict) or not all(isinstance(v, (int, float)) and 0 < v <= 10 for v in oneshot.values()):
        out.append("oneshot must map states to seconds (0..10]")
    budgets = reg.get("budgets", {})
    max_s = float(budgets.get("antic_max_s", 20))
    seen = set()
    antics = man.get("antics", [])
    if not isinstance(antics, list):
        out.append("antics must be a list")
        antics = []
    for a in antics:
        aid = a.get("id")
        tag = f"antic {aid!r}"
        if not aid or aid in seen:
            out.append(f"{tag}: missing or duplicate id")
        seen.add(aid)
        if not a.get("name"):
            out.append(f"{tag}: no name")
        d = a.get("duration")
        if not isinstance(d, (int, float)) or not 1 <= d <= max_s:
            out.append(f"{tag}: duration must be 1..{max_s:g} s")
        if not isinstance(a.get("weight", 1), (int, float)) or a.get("weight", 1) <= 0:
            out.append(f"{tag}: weight must be > 0")
        for k in ("cooldown", "min_idle"):
            if not isinstance(a.get(k, 0), (int, float)) or a.get(k, 0) < 0:
                out.append(f"{tag}: {k} must be seconds >= 0")
        where = a.get("where", list(WHERE))
        if not where or any(w not in WHERE for w in where):
            out.append(f"{tag}: where must be pill and/or desktop")
        if not a.get("exit"):
            out.append(f"{tag}: no interrupt exit declared ('exit')")
        if a.get("fps", 0) > budgets.get("antic_fps", 30):
            out.append(f"{tag}: fps above the registry's antic budget")
        for p in a.get("props", []):
            box = p.get("box")
            if not _is_box(box):
                out.append(f"{tag}: prop {p.get('id')!r} needs a box [x, y, w, h]")
                continue
            if "pill" in where and _is_box(b) and not _box_inside(box, b):
                out.append(f"{tag}: prop {p.get('id')!r} leaves the character's bounds")
            if "desktop" in where and _is_box(db) and not _box_inside(box, db):
                out.append(f"{tag}: prop {p.get('id')!r} leaves the desktop bounds")
        w = a.get("when", {})
        if not isinstance(w, dict):
            out.append(f"{tag}: when must be an object")
            continue
        for k in w:
            if k not in ("hours", "months", "weekdays", "weather", "user_idle_min"):
                out.append(f"{tag}: unknown condition {k!r}")
        if "weather" in w and any(x not in WEATHER for x in w["weather"]):
            out.append(f"{tag}: weather is one of {', '.join(WEATHER)}")
    return out


def load_manifest(folder: Path) -> dict:
    with open(folder / "character.json", encoding="utf-8") as f:
        man = json.load(f)
    man.setdefault("bounds", list(DEFAULT_BOUNDS))
    man.setdefault("desktop_bounds", list(DEFAULT_DESKTOP_BOUNDS))
    man.setdefault("antics", [])
    man.setdefault("oneshot", {})
    man["dir"] = str(folder)
    man["qml"] = str(folder / "Character.qml")
    man["builtin"] = folder.parent.resolve() == BUILTIN_DIR.resolve()
    prev = folder / "preview.png"
    man["preview"] = str(prev) if prev.exists() else ""
    return man


_REG_CACHE: dict = {}


def registry(dirs=None, refresh: bool = False) -> dict[str, dict]:
    """id -> manifest for every pack that loads (built-in first; a user pack can't take a
    built-in's id)."""
    key = tuple(str(d) for d in (dirs or (BUILTIN_DIR, USER_DIR)))
    if key in _REG_CACHE and not refresh:
        return _REG_CACHE[key]
    out: dict[str, dict] = {}
    for base in (Path(d) for d in (dirs or (BUILTIN_DIR, USER_DIR))):
        if not base.is_dir():
            continue
        for man_file in sorted(base.glob("*/character.json")):
            folder = man_file.parent
            try:
                man = load_manifest(folder)
            except (OSError, ValueError) as e:
                print(f"[character] {folder.name}: bad character.json ({e})")
                continue
            problems = validate(man, folder)
            if not (folder / "Character.qml").exists():
                problems.append("no Character.qml")
            if problems:
                print(f"[character] {folder.name} skipped: {'; '.join(problems)}")
                continue
            if man["id"] in out:
                print(f"[character] {folder} skipped: {man['id']!r} is already installed")
                continue
            out[man["id"]] = man
    _REG_CACHE[key] = out
    return out


# ------------------------------------------------------------------ states
_MISSING_LOGGED: set[tuple[str, str]] = set()


def resolve(man: dict, state: str, log=print) -> str:
    """The state `man` shows for registry state (or alias) `state`; a missing one is
    logged once per pack and state."""
    sid = S.canonical(state)
    got = S.resolve(sid, man["implements"])
    if got != sid and (man["id"], sid) not in _MISSING_LOGGED:
        _MISSING_LOGGED.add((man["id"], sid))
        if log:
            log(f"[character] {man['id']}: no {sid!r}, showing {got!r} ({' -> '.join(S.chain(sid))})")
    return got


# Assistant modes (Bridge.state) -> registry states. Events (wake, task done, a mismatch,
# a send) are one-shots the driver plays over these.
MODE_STATE = {"idle": "idle", "ready": "idle", "off": "off", "listening": "listening", "holding": "holding",
              "holding_muted": "holding_muted", "followup": "followup", "thinking": "thinking",
              "speaking": "talking", "talking": "talking", "loading": "loading", "error": "error",
              "paused": "paused", "dictating": "dictating", "input": "input", "approval": "approval_needed",
              "approval_needed": "approval_needed", "working": "working", "sending": "sending"}

MOODS = ("neutral", "happy", "confused", "sorry", "excited")


def state_for(mode: str, *, tasks: int = 0, paused: bool = False, sleep: str = "") -> str:
    """The registry state for an assistant mode plus context."""
    st = MODE_STATE.get(mode, S.canonical(mode))
    if st == "off" and paused:
        st = "paused"
    if st == "idle":
        if tasks > 0:
            return "working"
        if sleep:
            return sleep
    return st


# ------------------------------------------------------------------ sleep
class Sleep:
    """Sleep from the user's absence (no keyboard or mouse): drowsy after ~15 min,
    asleep after ~30, and a stretch ("waking") when they come back."""

    def __init__(self, drowsy_s: float = 900.0, asleep_s: float = 1800.0):
        self.drowsy_s, self.asleep_s = drowsy_s, asleep_s
        self.stage = ""

    def update(self, user_idle_s: float) -> str:
        """'', 'drowsy', 'asleep', or 'waking' (once, as they come back from a doze)."""
        # user idle time only drops when they touch something, so leaving a stage = they're back
        new = "asleep" if user_idle_s >= self.asleep_s else "drowsy" if user_idle_s >= self.drowsy_s else ""
        if new == "" and self.stage:
            self.stage = ""
            return "waking"
        self.stage = new
        return new


# ------------------------------------------------------------------ antics
@dataclass
class Context:
    """What the scheduler knows about right now."""
    now: float = 0.0                 # wall clock, s
    idle_s: float = 0.0              # how long the assistant has been at rest (no request, no state)
    user_idle_s: float = 0.0         # how long since the last keyboard / mouse input
    typing: bool = False             # keys in the last few seconds
    fullscreen: bool = False         # a full-screen app or game is in front
    reduced_motion: bool = False
    battery_saver: bool = False
    hidden: bool = False             # the island (or companion) isn't on screen
    resting: bool = True             # the character is in a rest state (idle / drowsy)
    where: str = "pill"
    hour: int = 12
    month: int = 1
    weekday: int = 0                 # Monday = 0
    weather: str = ""                # '' = no weather source


def context_now(**kw) -> Context:
    t = time.localtime()
    kw.setdefault("now", time.time())
    return Context(hour=t.tm_hour, month=t.tm_mon, weekday=t.tm_wday, **kw)


def _in_hours(h: int, span) -> bool:
    a, b = span
    return a <= h < b if a <= b else (h >= a or h < b)


def allowed(antic: dict, ctx: Context) -> bool:
    """Whether the antic's own conditions hold (not the scheduler's gates)."""
    if ctx.where not in antic.get("where", WHERE):
        return False
    if ctx.idle_s < float(antic.get("min_idle", 0)):
        return False
    w = antic.get("when") or {}
    if "hours" in w and not _in_hours(ctx.hour, w["hours"]):
        return False
    if "months" in w and ctx.month not in w["months"]:
        return False
    if "weekdays" in w and ctx.weekday not in w["weekdays"]:
        return False
    if "weather" in w and ctx.weather not in w["weather"]:
        return False
    if "user_idle_min" in w and ctx.user_idle_s < float(w["user_idle_min"]):
        return False
    return True


def blocked(ctx: Context) -> str:
    """Why no antic may play now ('' = it may)."""
    for why, on in (("typing", ctx.typing), ("fullscreen", ctx.fullscreen), ("reduced motion", ctx.reduced_motion),
                    ("battery saver", ctx.battery_saver), ("hidden", ctx.hidden), ("busy", not ctx.resting)):
        if on:
            return why
    return ""


@dataclass
class AnticScheduler:
    """Weighted random antics while idle: never the same one twice in a row, each with its
    cooldown, at most one per `frequency` gap (rare / normal / lively / off)."""
    antics: list[dict]
    frequency: str = "normal"
    enabled: set[str] | None = None        # None = all of the pack's antics
    rng: random.Random = field(default_factory=random.Random)
    last_id: str = ""
    last_played: dict[str, float] = field(default_factory=dict)
    next_at: float = 0.0
    playing: str = ""
    ends_at: float = 0.0

    def __post_init__(self):
        self.next_at = 0.0

    def gap(self) -> float:
        mean = FREQUENCY_S.get(self.frequency, 0.0)
        return mean * self.rng.uniform(0.6, 1.4)

    def candidates(self, ctx: Context) -> list[dict]:
        out = []
        for a in self.antics:
            if self.enabled is not None and a["id"] not in self.enabled:
                continue
            if a["id"] == self.last_id and len(self.antics) > 1:
                continue
            last = self.last_played.get(a["id"])
            if last is not None and ctx.now - last < float(a.get("cooldown", 0)):
                continue
            if allowed(a, ctx):
                out.append(a)
        return out

    def interrupt(self, now: float) -> str:
        """A real state, the pointer or a gate ended the current antic. Returns its id."""
        was, self.playing = self.playing, ""
        if was:
            self.next_at = now + self.gap()
        return was

    def tick(self, ctx: Context) -> dict | None:
        """The antic to start now, or None. Call every second or so."""
        if self.frequency == "off" or self.frequency not in FREQUENCY_S:
            self.playing = ""
            return None
        if self.playing:
            if blocked(ctx):
                self.interrupt(ctx.now)
                return None
            if ctx.now >= self.ends_at:
                self.playing = ""
                self.next_at = ctx.now + self.gap()
            return None
        if blocked(ctx):
            return None
        if self.next_at == 0.0:                       # the first one after start-up: a full gap
            self.next_at = ctx.now + self.gap()
            return None
        if ctx.now < self.next_at:
            return None
        cands = self.candidates(ctx)
        if not cands:
            self.next_at = ctx.now + 30.0              # look again in a bit
            return None
        total = sum(float(a.get("weight", 1)) for a in cands)
        r = self.rng.uniform(0, total)
        pick = cands[-1]
        for a in cands:
            r -= float(a.get("weight", 1))
            if r <= 0:
                pick = a
                break
        self.start(pick, ctx.now)
        return pick

    def start(self, antic: dict, now: float):
        self.playing = antic["id"]
        self.last_id = antic["id"]
        self.last_played[antic["id"]] = now
        self.ends_at = now + float(antic.get("duration", 6))
