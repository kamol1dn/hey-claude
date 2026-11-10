"""The timers' snapshot -> the reminders card's data (widgets/reminders).

The result comes from heyclaude/intents/timers.py `Timers.snapshot()` ("what timers do I
have", "how long left on the tea timer"), handed over as the tool `intents__list_timers`:
{"now": epoch, "timers": [{id, label, kind, seconds, left_s, ends_at}]}. Soonest first; the
timer asked about (args["label"]) is marked `focus`. Reminders and alarms will use the
same rows (`kind`) once they exist.
"""
from __future__ import annotations

import json
from datetime import datetime

MAX_ROWS = 6


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _hhmm(epoch) -> str:
    e = _num(epoch)
    if e is None or e < 1e9:                    # not a wall-clock time (a test's monotonic clock)
        return ""
    try:
        return datetime.fromtimestamp(e).strftime("%H:%M")
    except (OverflowError, OSError, ValueError):
        return ""


def adapt(args: dict, result, raw: str) -> dict | None:
    r = result if isinstance(result, dict) else None
    if r is None:
        try:
            r = json.loads(raw) if raw.lstrip().startswith("{") else None
        except ValueError:
            r = None
    if not isinstance(r, dict) or not isinstance(r.get("timers"), list):
        return None
    want = str((args or {}).get("label") or "").strip().lower()
    items = []
    for t in r["timers"]:
        if not isinstance(t, dict) or _num(t.get("left_s")) is None:
            continue
        left = max(0.0, float(t["left_s"]))
        total = max(1.0, float(_num(t.get("seconds")) or 0), left)
        label = " ".join(str(t.get("label") or "").split())[:40]
        kind = t.get("kind") if t.get("kind") in ("timer", "alarm", "reminder") else "timer"
        it = {"label": label[:1].upper() + label[1:], "kind": kind, "left_s": round(left, 1), "total_s": round(total, 1)}
        ends = _hhmm(t.get("ends_at"))
        if ends:
            it["ends"] = ends
        if want and label.lower() == want:
            it["focus"] = True
        items.append(it)
    if not items:
        return None
    items.sort(key=lambda i: i["left_s"])
    n = len(items)
    kinds = {i["kind"] for i in items}
    noun = ("Timer" if n == 1 else "Timers") if kinds == {"timer"} else ("Reminder" if n == 1 else "Reminders")
    data = {"title": f"{noun} · {n} running", "items": items[:MAX_ROWS]}
    if n > MAX_ROWS:
        data["more"] = n - MAX_ROWS
    return data
