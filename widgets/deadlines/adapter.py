"""mcp__info__deadlines -> the deadlines card's data.

The tool's JSON (heyclaude/mcp_info/deadlines.py) lists every deadline in the window;
the card shows the soonest six (five and "+N more" when there are more). A result that
isn't a deadline list (not set up, an error) stays text.
"""
from __future__ import annotations

import json

URGENCY = ("overdue", "bad", "soon", "later")
ROWS = 6


def _clip(s, n: int) -> str:
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _urgency(left: float) -> str:
    return "overdue" if left < 0 else "bad" if left < 86400 else "soon" if left < 3 * 86400 else "later"


def adapt(args: dict, result, raw: str) -> dict | None:
    v = result if isinstance(result, dict) else None
    if v is None:
        try:
            v = json.loads(raw) if raw.lstrip().startswith("{") else None
        except ValueError:
            v = None
    if not isinstance(v, dict) or not isinstance(v.get("items"), list):
        return None
    items = []
    for it in v["items"]:
        left = it.get("left_s") if isinstance(it, dict) else None
        if not it or not it.get("title") or not isinstance(left, (int, float)) or isinstance(left, bool):
            continue
        left = max(-31622400, min(31622400, left))
        x = {"title": _clip(it["title"], 80), "left_s": left,
             "urgency": it.get("urgency") if it.get("urgency") in URGENCY else _urgency(left)}
        if it.get("when"):
            x["when"] = _clip(it["when"], 30)
        items.append(x)
    more = int(v.get("more") or 0) if isinstance(v.get("more"), int) else 0
    if len(items) > ROWS:
        more += len(items) - (ROWS - 1)
        items = items[: ROWS - 1]
    days = v.get("days") if isinstance(v.get("days"), (int, float)) and not isinstance(v.get("days"), bool) else 14
    window = _clip(v.get("window") or f"next {days} days", 24)
    return {"window": window, "days": max(1, min(366, days)), "items": items, "more": min(999, more)}
