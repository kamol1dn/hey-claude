"""Tool calls -> island effect events (ROADMAP M9 section 3).

This is the data contract, kept apart from the painting so M5's QML island
can reuse it unchanged. An effect event is a plain dict:

    id        unique id (the Undo / Confirm buttons send it back)
    effect    generic | volume | mute | brightness | theme | media | flash | confirm
    tool      the full tool name, e.g. mcp__windows__set_volume
    icon      a small glyph name (speaker, sun, moon, image, wifi, ...)
    label     short text, e.g. "Volume"
    from, to  raw values (numbers, booleans, strings, track dicts)
    text_from, text_to   the same as display text ("45%", "on", "dark", "flower.jpg")
    undo      true when there is an Undo button
    reverse   true when this event shows an undo (the effect plays backwards)
    ttl_s     how long it stays on the island
    extra     any other data fields from effects.json (e.g. "action")

`effects.json` lives in ui/; see the notes at its top.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from functools import lru_cache

from .config import ROOT

EFFECTS_FILE = ROOT / "ui" / "effects.json"
KINDS = ("generic", "volume", "mute", "brightness", "theme", "media", "flash", "confirm")


@lru_cache(maxsize=4)
def load(path: str | None = None) -> dict:
    with open(path or EFFECTS_FILE, encoding="utf-8") as f:
        return json.load(f)


def parse_result(content) -> object:
    """A tool_result's content (a string, or a list of content blocks) -> the
    JSON value the tool returned, or None when it isn't JSON (images, text)."""
    if isinstance(content, list):
        texts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
        content = texts[0] if texts else None
    if not isinstance(content, str):
        return content if isinstance(content, dict) else None
    try:
        v = json.loads(content)
    except (json.JSONDecodeError, ValueError):
        return None
    if isinstance(v, dict) and set(v) == {"result"}:      # SDK wrapper for non-object returns
        v = v["result"]
    return v


def result_text(content) -> str:
    """A tool_result's text (the first text block), for tools that answer in markdown."""
    if isinstance(content, list):
        texts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
        content = texts[0] if texts else ""
    return content if isinstance(content, str) else ""


def _get(scope: dict, path: str):
    cur = scope
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def _label(template: str, scope: dict) -> str:
    def sub(m):
        v = _get(scope, m.group(1))
        return "" if v is None else str(v)
    out = re.sub(r"\{([\w.]+)\}", sub, template or "").strip()
    return out[:1].upper() + out[1:] if out else ""


def fmt_value(v, fmt: str) -> str:
    if v is None or fmt == "none":
        return ""
    if fmt == "percent" and isinstance(v, (int, float)):
        return f"{round(v)}%"
    if fmt == "onoff" and isinstance(v, bool):
        return "on" if v else "off"
    if fmt == "basename" and isinstance(v, str):
        return os.path.basename(v) or "none"
    if fmt == "mode" and isinstance(v, str):
        return v.replace("_", " ")
    if fmt == "track" and isinstance(v, dict):
        t, a = v.get("title") or "", v.get("artist") or ""
        return f"{t} · {a}" if t and a else t or a
    if isinstance(v, bool):
        return "on" if v else "off"
    return str(v)


def build(tool: str, args: dict | None, result, *, undoable: bool = False, mapping: dict | None = None) -> dict | None:
    """The effect event for one tool call, or None if it shows nothing."""
    m = mapping or load()
    if tool in m.get("silent", ()):
        return None
    spec = m.get("tools", {}).get(tool)
    if spec is None:
        if not (isinstance(result, dict) and "old" in result and "new" in result):
            return None
        spec = m["fallback"]
    short = tool.split("__")[-1].replace("_", " ")
    scope = {"input": args or {}, "result": result if result is not None else {}, "tool_short": short}
    data = {k: _get(scope, p) for k, p in (spec.get("data") or {}).items()}
    fmt = spec.get("fmt", "text")
    ev = {
        "id": uuid.uuid4().hex[:12],
        "effect": spec.get("effect", "generic") if spec.get("effect") in KINDS else "generic",
        "tool": tool,
        "icon": spec.get("icon", "gear"),
        "label": _label(spec.get("label", ""), scope) or short.capitalize(),
        "from": data.pop("from", None),
        "to": data.pop("to", None),
        "undo": bool(undoable),
        "reverse": False,
        "ttl_s": float(spec.get("ttl_s", m.get("ttl_s", 8))),
        "extra": data,
    }
    ev["text_from"] = fmt_value(ev["from"], fmt)
    ev["text_to"] = fmt_value(ev["to"], fmt)
    if ev["effect"] == "media" and isinstance(ev["to"], dict):
        ev["extra"].setdefault("title", ev["to"].get("title") or "")
        ev["extra"].setdefault("artist", ev["to"].get("artist") or "")
        ev["extra"].setdefault("status", ev["to"].get("status") or "")
        if ev["to"].get("duration_s"):
            ev["extra"]["progress"] = min(1.0, (ev["to"].get("position_s") or 0) / ev["to"]["duration_s"])
    return ev


def reversed_event(ev: dict, result=None) -> dict:
    """The effect for undoing `ev`: same widget, from/to swapped, played backwards."""
    out = dict(ev, id=uuid.uuid4().hex[:12], reverse=True, undo=False,
               **{"from": ev.get("to"), "to": ev.get("from"),
                  "text_from": ev.get("text_to", ""), "text_to": ev.get("text_from", "")})
    out["extra"] = dict(ev.get("extra") or {})
    if ev.get("effect") == "media" and isinstance(result, dict) and isinstance(result.get("new"), dict):
        new = result["new"]
        out["to"] = new
        out["extra"].update(title=new.get("title") or "", artist=new.get("artist") or "",
                            status=new.get("status") or "")
        out["text_to"] = fmt_value(new, "track")
    out["label"] = f"Undone · {ev.get('label', '')}".strip(" ·")
    out["ttl_s"] = min(float(ev.get("ttl_s", 8)), 4.0)
    return out
