"""mcp__info__next_class / mcp__info__timetable_today -> the timetable card's data.

The tool's JSON (heyclaude/mcp_info/timetable.py) is already nearly the card: this keeps
the fields the card draws, clips long names, and drops a result that isn't a timetable
(not set up, an error) so the answer stays text. The group code stays off the card.
"""
from __future__ import annotations

import json
import re

_HHMM = re.compile(r"^\d\d:\d\d$")
STATES = ("done", "now", "next", "later")


def _clip(s, n: int) -> str:
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _parse(result, raw: str):
    if isinstance(result, dict):
        return result
    try:
        v = json.loads(raw) if raw.lstrip().startswith("{") else None
    except ValueError:
        v = None
    return v if isinstance(v, dict) else None


def adapt(args: dict, result, raw: str) -> dict | None:
    v = _parse(result, raw)
    if not v or not isinstance(v.get("classes"), list) or not v.get("label"):
        return None
    classes = []
    for c in v["classes"][:8]:
        if not isinstance(c, dict) or not _HHMM.match(str(c.get("start", ""))) or not c.get("title"):
            continue
        end = str(c.get("end") or "")
        x = {"start": c["start"], "end": end if _HHMM.match(end) else c["start"], "title": _clip(c["title"], 60),
             "state": c.get("state") if c.get("state") in STATES else "later"}
        for k, n in (("code", 24), ("room", 24), ("teacher", 40)):
            if c.get(k):
                x[k] = _clip(c[k], n)
        classes.append(x)
    focus = v.get("focus") if isinstance(v.get("focus"), int) and not isinstance(v.get("focus"), bool) else -1
    if not -1 <= focus < len(classes):
        focus = -1
    state = v.get("focus_state") if v.get("focus_state") in ("now", "next") and focus >= 0 else "none"
    out = {"label": _clip(v["label"], 24), "classes": classes, "focus": focus if state != "none" else -1,
           "focus_state": state}
    if _num(v.get("focus_in_s")) is not None:
        out["focus_in_s"] = max(0, min(1209600, v["focus_in_s"]))
    for k in ("day", "date"):
        if isinstance(v.get(k), str):
            out[k] = v[k]
    if isinstance(v.get("now"), str) and _HHMM.match(v["now"]) and _num(v.get("now_min")) is not None:
        out["now"], out["now_min"] = v["now"], max(0, min(1440, v["now_min"]))
    a = v.get("after")
    if isinstance(a, dict) and a.get("title") and _HHMM.match(str(a.get("start", ""))):
        aa = {"label": _clip(a.get("label") or "", 24), "start": a["start"], "title": _clip(a["title"], 60)}
        if a.get("room"):
            aa["room"] = _clip(a["room"], 24)
        if _num(a.get("in_s")) is not None:
            aa["in_s"] = max(0, min(1209600, a["in_s"]))
        out["after"] = aa
    return out
