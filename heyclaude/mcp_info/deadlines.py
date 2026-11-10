"""The user's deadlines (ROADMAP M5b): a countdown list from their own notes file,
`info.deadlines.path`. Read-only; personal, so nothing of it is ever kept in the repo.

Formats (picked by the file's extension):
- Markdown or plain text (.md, .txt, anything else): one deadline per line,
      - 2026-11-14 18:00 Physics lab report
      - [ ] 2026-11-20 Essay draft            (no time: info.deadlines.default_time, 23:59)
      - [x] 2026-11-08 Quiz 3                 (ticked: done, skipped)
      - ~~2026-11-09 Old thing~~              (struck through: done, skipped)
      * 21.11.2026 09:30 — Lab defence        (DD.MM.YYYY works too; "-", "—", ":" after the date are fine)
  Any other line (headings, prose) is ignored.
- JSON or YAML (.json, .yaml, .yml): a list, or {"deadlines": [...]}, of
      {"due": "2026-11-14 18:00", "title": "Physics lab report", "done": false}
  (`date` + `time` instead of `due`, `name`/`what` instead of `title` are fine).
Times are local. Overdue items stay on the list for info.deadlines.overdue_h hours (48).
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path

from heyclaude import mcp_info

MAX_ITEMS = 20
NOT_SET_UP = ("Deadlines aren't set up: set info.deadlines.path in config.yaml to your deadline notes "
              "(lines like \"- 2026-11-14 18:00 Physics lab report\").")
_DATE = r"(?P<date>\d{4}-\d{1,2}-\d{1,2}|\d{1,2}\.\d{1,2}\.\d{4})"
_LINE = re.compile(r"^\s*(?:[-*+]|\d+[.)])?\s*(?:\[(?P<chk>[ xX])\]\s*)?(?P<strike>~~)?\s*" + _DATE +
                   r"(?:[ T](?P<time>\d{1,2}[:.]\d{2}))?\s*(?:[-–—:|]\s*)?(?P<title>.+?)\s*$")


def _cfg() -> dict:
    c = mcp_info.config().get("deadlines")
    return c if isinstance(c, dict) else {}


def _clock() -> datetime:
    return datetime.now()


def _when(d: str, t: str, default_time: str) -> datetime | None:
    d, t = str(d or "").strip(), str(t or "").strip().replace(".", ":") or default_time
    try:
        if re.fullmatch(r"\d{1,2}\.\d{1,2}\.\d{4}", d):
            dd, mm, yy = (int(x) for x in d.split("."))
        else:
            yy, mm, dd = (int(x) for x in d.split("-"))
        h, m = (int(x) for x in t.split(":")[:2])
        return datetime(yy, mm, dd, h, m)
    except (ValueError, TypeError):
        return None


def _clean(title: str) -> str:
    t = re.sub(r"~~", "", title).strip(" -–—:|")
    return re.sub(r"\s+", " ", t)[:80]


def _from_text(text: str, default_time: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        m = _LINE.match(line)
        if not m:
            continue
        due = _when(m["date"], m["time"] or "", default_time)
        title = _clean(m["title"])
        if not due or not title:
            continue
        done = (m["chk"] or " ").lower() == "x" or bool(m["strike"]) or m["title"].rstrip().endswith("~~")
        out.append({"due": due, "title": title, "done": done})
    return out


def _from_list(items, default_time: str) -> list[dict]:
    if isinstance(items, dict):
        items = items.get("deadlines") or items.get("items") or []
    out = []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        due = it.get("due") or it.get("date") or ""
        if isinstance(due, datetime):
            d = due
        else:
            due = str(due).strip().replace("T", " ")
            parts = due.split(" ", 1)
            d = _when(parts[0], (parts[1] if len(parts) > 1 else "") or str(it.get("time") or ""), default_time)
        title = _clean(str(it.get("title") or it.get("name") or it.get("what") or ""))
        if d and title:
            out.append({"due": d, "title": title, "done": bool(it.get("done"))})
    return out


def _read(path: Path, default_time: str) -> list[dict]:
    text = path.read_text(encoding="utf-8-sig")
    ext = path.suffix.lower()
    if ext == ".json":
        return _from_list(json.loads(text), default_time)
    if ext in (".yaml", ".yml"):
        import yaml
        return _from_list(yaml.safe_load(text), default_time)
    return _from_text(text, default_time)


def _rel(s: float) -> str:
    """Seconds left -> "in 25 min" / "in 5 h" / "in 3 days" / "3 h ago"."""
    late = s < 0
    m = max(1, round(abs(s) / 60))
    if m < 60:
        v = f"{m} min"
    elif m < 48 * 60:
        h, r = divmod(m, 60)
        v = f"{round(m / 60)} h" if h >= 10 else f"{h} h" + (f" {r} min" if r else "")
    else:
        v = f"{round(m / 1440)} days"
    return f"{v} ago" if late else f"in {v}"


def _urgency(left: float) -> str:
    return "overdue" if left < 0 else "bad" if left < 86400 else "soon" if left < 3 * 86400 else "later"


def _due_label(d: datetime, now: datetime) -> str:
    n = (d.date() - now.date()).days
    day = "today" if n == 0 else "tomorrow" if n == 1 else "yesterday" if n == -1 else \
        d.strftime("%a") if 1 < n < 7 else f"{d.strftime('%a')} {d.day} {d.strftime('%b')}"
    return f"{day} {d.strftime('%H:%M')}"


def deadlines(days: int = 14) -> dict:
    """The user's upcoming deadlines from their own notes, soonest first: everything due in the next `days`
    days (default 14), plus anything overdue in the last two days, each with when it's due and the time left.
    The island shows it as a card by itself."""
    cfg = _cfg()
    path = str(cfg.get("path") or "").strip().strip('"')
    if not path:
        return {"setup": False, "text": NOT_SET_UP}
    p = Path(os.path.expandvars(os.path.expanduser(path)))
    if not p.is_file():
        raise LookupError(f"the deadlines file {p} doesn't exist")
    days = max(1, min(366, int(days or 14)))
    default_time = str(cfg.get("default_time") or "23:59")
    overdue_h = float(cfg.get("overdue_h", 48) or 0)
    now = _clock().replace(microsecond=0)
    horizon = now + timedelta(days=days)
    rows = [r for r in _read(p, default_time) if not r["done"] and now - timedelta(hours=overdue_h) <= r["due"] <= horizon]
    rows.sort(key=lambda r: r["due"])
    items = []
    for r in rows[:MAX_ITEMS]:
        left = (r["due"] - now).total_seconds()
        items.append({"title": r["title"], "due": r["due"].strftime("%Y-%m-%d %H:%M"), "when": _due_label(r["due"], now),
                      "left_s": int(left), "left": _rel(left), "urgency": _urgency(left)})
    window = "next 24 hours" if days == 1 else f"next {days} days"
    overdue = sum(1 for i in items if i["urgency"] == "overdue")
    upcoming = len(items) - overdue
    if not items:
        text = f"No deadlines in the {window}."
    else:
        s = items[0]
        text = (f"{upcoming} deadline{'s' if upcoming != 1 else ''} in the {window}"
                + (f" and {overdue} overdue" if overdue else "")
                + f". Soonest: {s['title']}, {'was due' if s['urgency'] == 'overdue' else 'due'} {s['when']} ({s['left']}).")
    return {"now": now.strftime("%Y-%m-%d %H:%M:%S"), "days": days, "window": window, "count": upcoming,
            "overdue": overdue, "more": max(0, len(rows) - MAX_ITEMS), "items": items, "text": text}


TOOLS = [deadlines]
PROMPT = "For the user's deadlines (what's due, how long is left) use mcp__info__deadlines."
