"""The user's class timetable (ROADMAP M5b): the next class, its room and the time left,
and the rest of a day. Read-only, from the user's own timetable data, never copied
anywhere: `info.timetable.path` is a folder (the inha_timetable project) or one file.

Data it reads (the inha_timetable formats):
- a Python data module (`backend/clean_timetables.py`, `tools/regular/clean_timetables.py`):
  `classes = {code: {"title": ...}}` and `group_timetables = {group: [{"code", "day": "Mon",
  "start": "09:00", "end": "10:30", "room"}]}`. It is parsed as literal data (`ast`), never run.
- the same as JSON (`tools/regular/clean_timetables.json`), or the README's JSON form
  `{"groups": {group: {"Mon": [{"start", "end", "code", "title", "room"}]}}}`.
Weeks repeat (no odd/even weeks); Saturday and Sunday are free unless the data says otherwise.

Settings (`info.timetable`): path, group (your group code as the data spells it), drop
(subject codes you don't take), extra (your own classes: {day, start, end, title, room}),
tz (an IANA zone, e.g. Asia/Tashkent; empty = this PC's clock).
"""
from __future__ import annotations

import ast
import difflib
import json
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path

from heyclaude import mcp_info

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
# Where the data lives inside the inha_timetable project, most authoritative first
# (the backend's module is what the bot serves).
CANDIDATES = ("backend/clean_timetables.py", "tools/regular/clean_timetables.json",
              "tools/regular/clean_timetables.py", "clean_timetables.json", "clean_timetables.py", "timetable.json")
MAX_CLASSES = 8
NOT_SET_UP = ("The timetable isn't set up: set info.timetable.path in config.yaml to the timetable project "
              "folder (or its clean_timetables file) and info.timetable.group to your group.")

_cache: dict[tuple, tuple[dict, dict]] = {}


def _cfg() -> dict:
    c = mcp_info.config().get("timetable")
    return c if isinstance(c, dict) else {}


def _clock(cfg: dict | None = None) -> datetime:
    """Now, as naive local time (the zone in info.timetable.tz when this Python knows it)."""
    tz = str((cfg or {}).get("tz") or "").strip()
    if tz:
        try:
            from zoneinfo import ZoneInfo
            return datetime.now(ZoneInfo(tz)).replace(tzinfo=None)
        except Exception:                               # no tzdata on this machine: the PC's clock
            pass
    return datetime.now()


# ------------------------------------------------------------------ loading
def _source(path: str) -> Path:
    p = Path(os.path.expandvars(os.path.expanduser(path.strip().strip('"'))))
    if p.is_dir():
        for c in CANDIDATES:
            if (p / c).is_file():
                return p / c
        raise LookupError(f"no timetable data in {p} (looked for {', '.join(CANDIDATES)})")
    if p.is_file():
        return p
    raise LookupError(f"the timetable path {p} doesn't exist")


def _literal_module(text: str) -> dict:
    """Top-level `name = {...}` / `name: T = {...}` literals of a data module (never executed)."""
    out = {}
    for node in ast.parse(text).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name, value = node.targets[0].id, node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            name, value = node.target.id, node.value
        else:
            continue
        if name in ("classes", "group_timetables", "groups", "classrooms"):
            try:
                out[name] = ast.literal_eval(value)
            except ValueError:
                pass
    return out


def _day(s) -> str:
    """"Mon" / "monday" / "Tuesday" -> "Mon"/"Tue"...; "" when it isn't a weekday."""
    t = str(s or "").strip().lower()[:3]
    for d in DAYS:
        if d.lower() == t:
            return d
    return ""


def _hhmm(s) -> str:
    m = re.match(r"^\s*(\d{1,2})[:.](\d{2})", str(s or ""))
    return f"{int(m[1]):02d}:{m[2]}" if m and int(m[1]) < 24 and int(m[2]) < 60 else ""


def _load(file: Path) -> tuple[dict, dict]:
    """(classes {code: {title}}, groups {group: [entry...]}) from a data file, cached by mtime."""
    key = (str(file), file.stat().st_mtime_ns)
    if key in _cache:
        return _cache[key]
    text = file.read_text(encoding="utf-8-sig")
    raw = _literal_module(text) if file.suffix.lower() == ".py" else json.loads(text)
    if not isinstance(raw, dict):
        raise ValueError(f"{file.name}: not a timetable (expected an object)")
    classes = raw.get("classes") if isinstance(raw.get("classes"), dict) else {}
    groups: dict[str, list] = {}
    if isinstance(raw.get("group_timetables"), dict):
        for g, items in raw["group_timetables"].items():
            groups[str(g)] = [e for e in items if isinstance(e, dict)] if isinstance(items, list) else []
    elif isinstance(raw.get("groups"), dict):
        for g, days in raw["groups"].items():
            items = []
            if isinstance(days, dict):
                for d, es in days.items():
                    items += [dict(e, day=d) for e in (es or []) if isinstance(e, dict)]
            groups[str(g)] = items
    else:
        raise ValueError(f"{file.name}: no group_timetables or groups in it")
    _cache.clear()
    _cache[key] = (classes, groups)
    return classes, groups


def _entry(e: dict, classes: dict) -> dict | None:
    day, start = _day(e.get("day")), _hhmm(e.get("start"))
    if not day or not start:
        return None
    code = str(e.get("code") or "").strip()
    title = str(e.get("title") or ((classes.get(code) or {}).get("title") if code else "") or code or "Class").strip()
    out = {"day": day, "start": start, "end": _hhmm(e.get("end")), "title": title, "code": code,
           "room": str(e.get("room") or "").strip()}
    if e.get("teacher"):
        out["teacher"] = str(e["teacher"]).strip()
    return out


def _week(cfg: dict) -> tuple[str, list[dict]]:
    """(group, every class of the week) for the configured group, with drops and extras applied."""
    classes, groups = _load(_source(str(cfg["path"])))
    group = str(cfg.get("group") or "").strip()
    if not group:
        if len(groups) != 1:
            eg = ", ".join(sorted(groups)[:4])
            raise LookupError(f"set info.timetable.group to your group (the data has {len(groups)} groups, e.g. {eg})")
        group = next(iter(groups))
    if group not in groups:
        by_upper = {g.upper(): g for g in groups}
        if group.upper() in by_upper:
            group = by_upper[group.upper()]
        else:
            close = difflib.get_close_matches(group, list(groups), n=3, cutoff=0.6)
            raise LookupError(f"no group {group!r} in the timetable" + (f" (did you mean {', '.join(close)}?)" if close else ""))
    drop = {str(c).strip().upper() for c in (cfg.get("drop") or []) if str(c).strip()}
    out = []
    for e in groups[group]:
        x = _entry(e, classes)
        if x and x["code"].upper() not in drop:
            out.append(x)
    for e in cfg.get("extra") or []:
        x = _entry(e, classes) if isinstance(e, dict) else None
        if x:
            out.append(x)
    out.sort(key=lambda x: (DAYS.index(x["day"]), x["start"]))
    return group, out


# ------------------------------------------------------------------ a day
def _mins(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _at(d: date, hhmm: str) -> datetime:
    return datetime(d.year, d.month, d.day) + timedelta(minutes=_mins(hhmm))


def _end(c: dict) -> str:
    if c["end"] and _mins(c["end"]) > _mins(c["start"]):
        return c["end"]
    m = min(_mins(c["start"]) + 90, 23 * 60 + 59)
    return f"{m // 60:02d}:{m % 60:02d}"


def _label(d: date, today: date) -> str:
    n = (d - today).days
    if n == 0:
        return "Today"
    if n == 1:
        return "Tomorrow"
    if n == -1:
        return "Yesterday"
    if 1 < n < 7:
        return DAY_NAMES[d.weekday()]
    return f"{DAYS[d.weekday()]} {d.day} {d.strftime('%b')}"


def _span(s: float) -> str:
    """Seconds -> "25 min" / "2 h 5 min" / "3 days"."""
    m = max(0, round(s / 60))
    if m < 60:
        return f"{m} min"
    if m < 24 * 60:
        h, r = divmod(m, 60)
        return f"{h} h" + (f" {r} min" if r else "")
    d = round(m / (24 * 60))
    return f"{d} day" + ("s" if d != 1 else "")


def _on(week: list[dict], d: date) -> list[dict]:
    return [dict(c, end=_end(c)) for c in week if c["day"] == DAYS[d.weekday()]][:MAX_CLASSES]


def _after(week: list[dict], d: date, now: datetime) -> dict | None:
    """The first class on a day after `d` (within a week), from now."""
    for k in range(1, 8):
        nd = d + timedelta(days=k)
        day = _on(week, nd)
        if day:
            c = day[0]
            out = {"label": _label(nd, now.date()), "start": c["start"], "title": c["title"],
                   "in_s": max(0, int((_at(nd, c["start"]) - now).total_seconds()))}
            if c["room"]:
                out["room"] = c["room"]
            return out
    return None


def _view(group: str, week: list[dict], d: date, now: datetime) -> dict:
    today = now.date()
    is_today = d == today
    nm = now.hour * 60 + now.minute + now.second / 60
    classes = []
    for c in _on(week, d):
        s, e = _mins(c["start"]), _mins(c["end"])
        if is_today:
            state = "done" if e <= nm else "now" if s <= nm else "later"
        else:
            state = "done" if d < today else "later"
        classes.append(dict({k: v for k, v in c.items() if k != "day" and v != ""}, state=state))
    focus, fstate, fin = -1, "none", 0
    for i, c in enumerate(classes):
        if c["state"] == "now":
            focus, fstate = i, "now"
            fin = int((_at(d, c["end"]) - now).total_seconds())
            break
        if c["state"] == "later":
            focus, fstate = i, "next"
            fin = int((_at(d, c["start"]) - now).total_seconds())
            break
    if focus >= 0 and fstate == "next":
        classes[focus]["state"] = "next"
    out = {"label": _label(d, today), "day": DAYS[d.weekday()], "day_name": DAY_NAMES[d.weekday()],
           "date": d.isoformat(), "group": group, "classes": classes, "focus": focus, "focus_state": fstate,
           "focus_in_s": max(0, fin)}
    if is_today:
        out["now"] = now.strftime("%H:%M")
        out["now_min"] = round(nm, 2)
    if focus < 0 and d >= today:
        a = _after(week, d, now)
        if a:
            out["after"] = a
    out["text"] = _say(out)
    return out


def _say(v: dict) -> str:
    cls, f = v["classes"], v["focus"]
    when = v["label"].lower() if v["label"] in ("Today", "Tomorrow", "Yesterday") else "on " + v["label"]

    def where(c):
        return f" in {c['room']}" if c.get("room") else ""
    if not cls:
        s = f"No classes {when}."
    elif f < 0:
        s = f"{'Classes are over' if v['label'] == 'Today' else 'Classes were'} {when}: {len(cls)} class{'es' if len(cls) != 1 else ''}."
    elif v["focus_state"] == "now":
        c = cls[f]
        s = f"Now: {c['title']}{where(c)} until {c['end']} ({_span(v['focus_in_s'])} left)."
    else:
        c = cls[f]
        lead = "Next" if v["label"] == "Today" else f"First {when}"
        s = f"{lead}: {c['title']}{where(c)} at {c['start']} (in {_span(v['focus_in_s'])})."
    rest = [c for c in cls[f + 1:]] if f >= 0 else []
    if rest:
        s += " Then " + ", ".join(f"{c['title']} at {c['start']}" + (f" ({c['room']})" if c.get("room") else "")
                                  for c in rest[:4]) + "."
    a = v.get("after")
    if a:
        s += f" Next class: {a['title']}{' in ' + a['room'] if a.get('room') else ''}, {a['label'].lower() if a['label'] in ('Tomorrow',) else a['label']} at {a['start']}."
    return s


def _date_for(day: str, today: date) -> date:
    t = str(day or "").strip().lower()
    if t in ("", "today", "now"):
        return today
    if t == "tomorrow":
        return today + timedelta(days=1)
    if t == "yesterday":
        return today - timedelta(days=1)
    if re.fullmatch(r"\d{4}-\d\d-\d\d", t):
        return date.fromisoformat(t)
    t = re.sub(r"^(this|next|on)\s+", "", t)
    d = _day(t)
    if d and t[:3] == d.lower():
        return today + timedelta(days=(DAYS.index(d) - today.weekday()) % 7)
    raise ValueError(f"not a day: {day!r} (say today, tomorrow, a weekday or YYYY-MM-DD)")


def _setup() -> dict | None:
    cfg = _cfg()
    return cfg if str(cfg.get("path") or "").strip() else None


# ------------------------------------------------------------------ the tools
def next_class() -> dict:
    """The user's next class: what, which room, when it starts and how long until then (or, during a class,
    how long it has left), plus the rest of that day. When today's classes are over it is the first class of
    the next day with classes. The island shows it as a card by itself."""
    cfg = _setup()
    if cfg is None:
        return {"setup": False, "text": NOT_SET_UP}
    group, week = _week(cfg)
    now = _clock(cfg)
    v = _view(group, week, now.date(), now)
    if v["focus"] >= 0:
        return v
    for k in range(1, 8):
        d = now.date() + timedelta(days=k)
        if _on(week, d):
            return _view(group, week, d, now)
    return v


def timetable_today(day: str = "") -> dict:
    """The user's classes for a day (default today; or "tomorrow", a weekday like "thursday", or YYYY-MM-DD):
    each class's time, subject and room, which one is next or on now and the time left. The island shows it
    as a card by itself."""
    cfg = _setup()
    if cfg is None:
        return {"setup": False, "text": NOT_SET_UP}
    group, week = _week(cfg)
    now = _clock(cfg)
    return _view(group, week, _date_for(day, now.date()), now)


TOOLS = [next_class, timetable_today]
PROMPT = ("For the user's classes (next class, room, time left, a day's timetable) use mcp__info__next_class or "
          "mcp__info__timetable_today.")
