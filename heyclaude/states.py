"""The versioned state registry (ui/states.json), shared by island themes (M5)
and character packs (M6).

    resolve("dictating", {"idle", "off", "listening", "thinking", "talking"})  # -> "listening"
    resolve("sending", theme["implements"])     # sending -> working -> thinking

A state a theme or pack doesn't implement walks its fallback chain until one
it does; the core states have no fallback, so every chain ends in one of them.
"""
from __future__ import annotations

import json
from functools import lru_cache

from .config import ROOT

REGISTRY_FILE = ROOT / "ui" / "states.json"
THEMES_DIR = ROOT / "ui" / "themes"


@lru_cache(maxsize=4)
def load(path: str | None = None) -> dict:
    with open(path or REGISTRY_FILE, encoding="utf-8") as f:
        reg = json.load(f)
    problems = validate(reg)
    if problems:
        raise ValueError("ui/states.json: " + "; ".join(problems))
    return reg


def _by_id(reg: dict) -> dict[str, dict]:
    return {s["id"]: s for s in reg["states"]}


def validate(reg: dict) -> list[str]:
    out = []
    ids = [s.get("id") for s in reg.get("states", [])]
    if len(ids) != len(set(ids)):
        out.append("duplicate state ids")
    by = {s.get("id"): s for s in reg.get("states", [])}
    core = set(reg.get("core", []))
    inputs = set(reg.get("inputs", {}))
    aliases: dict[str, str] = {}
    for s in reg.get("states", []):
        sid = s.get("id")
        if s.get("kind") not in ("loop", "oneshot"):
            out.append(f"{sid}: kind must be loop or oneshot")
        for i in s.get("inputs", []):
            if i not in inputs:
                out.append(f"{sid}: unknown input {i!r}")
        for a in s.get("aliases", []):
            if a in aliases or a in by:
                out.append(f"alias {a!r} is used twice")
            aliases[a] = sid
        fb = s.get("fallback")
        if sid in core:
            if fb is not None:
                out.append(f"{sid}: a core state has no fallback")
            continue
        if fb is None:
            out.append(f"{sid}: only core states may lack a fallback")
            continue
        if fb not in by:
            out.append(f"{sid}: fallback {fb!r} isn't a state")
            continue
        seen, cur = {sid}, fb
        while cur is not None and cur not in core:
            if cur in seen:
                out.append(f"{sid}: fallback cycle through {cur!r}")
                break
            seen.add(cur)
            cur = by.get(cur, {}).get("fallback")
    for c in core:
        if c not in by:
            out.append(f"core state {c!r} missing")
    if "." not in str(reg.get("version", "")):
        out.append("version must be major.minor")
    return out


def canonical(name: str, reg: dict | None = None) -> str:
    """A state id from an id or an alias ("speaking" -> "talking")."""
    reg = reg or load()
    if name in _by_id(reg):
        return name
    for s in reg["states"]:
        if name in s.get("aliases", []):
            return s["id"]
    return name


def resolve(name: str, implemented, reg: dict | None = None) -> str:
    """The state to show for `name` with only `implemented` available."""
    reg = reg or load()
    by = _by_id(reg)
    cur = canonical(name, reg)
    if cur not in by:
        cur = "idle"                       # unknown to this registry: rest
    impl = set(implemented)
    while cur not in impl:
        fb = by[cur].get("fallback")
        if fb is None:
            break
        cur = fb
    return cur


def chain(name: str, reg: dict | None = None) -> list[str]:
    reg = reg or load()
    by = _by_id(reg)
    cur, out = canonical(name, reg), []
    while cur is not None and cur in by:
        out.append(cur)
        cur = by[cur].get("fallback")
    return out


def compatible(pack_version: str, reg: dict | None = None) -> bool:
    """A theme or pack built for registry major.minor works here if the major
    matches: minor bumps only add states, and those fall back."""
    reg = reg or load()
    try:
        return str(pack_version).split(".")[0] == str(reg["version"]).split(".")[0]
    except (IndexError, KeyError):
        return False


def load_theme(theme_id: str = "ember-thread") -> dict:
    with open(THEMES_DIR / theme_id / "theme.json", encoding="utf-8") as f:
        t = json.load(f)
    reg = load()
    if not compatible(t.get("registry", ""), reg):
        raise ValueError(f"theme {theme_id} is for registry {t.get('registry')}, this is {reg['version']}")
    missing = set(reg["core"]) - set(t.get("implements", []))
    if missing:
        raise ValueError(f"theme {theme_id} lacks core states: {', '.join(sorted(missing))}")
    return t


# Registry ids -> the assistant modes island_qml.route() understands.
TO_MODE = {"idle": "idle", "off": "off", "listening": "listening", "thinking": "thinking", "talking": "speaking",
           "holding": "holding", "holding_muted": "holding", "followup": "followup", "loading": "loading",
           "error": "error", "paused": "off"}


def mode_for(mode: str, implemented) -> str:
    """An assistant mode, possibly newer than this theme ("dictating"), -> one it shows."""
    reg = load()
    sid = canonical(mode, reg)
    if sid not in _by_id(reg):
        return mode
    return TO_MODE.get(resolve(sid, implemented, reg), mode)
