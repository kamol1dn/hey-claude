"""Facts -> the weather card's data and a short spoken summary, both local (no model).

`facts` come from weather-mcp (parse.py) or from the built-in Open-Meteo
tool (`from_builtin`), so the card and the summary look the same whichever
answered. The summary is one or two plain sentences for TTS, e.g.
"8 degrees and overcast, feels like 5. Rain likely from around 10 pm."
Asked how sure that is (`q.sure`), the card adds the model-agreement chip
(`facts["agreement"]`, heyclaude/weather.agreement) and the summary is the
models' verdict: "The models mostly agree about tomorrow: 9 to 11 degrees, rain likely."
"""
from __future__ import annotations

import math
from dataclasses import replace
from datetime import date, datetime, timedelta

from .parse import SEVERITY, icon_for
from .query import WeatherQuery

SYNODIC = 29.530588853
_NEW_MOON = datetime(2000, 1, 6, 18, 14)        # a known new moon (UTC)
_SHORT = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_LONG = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


# ------------------------------------------------------------------ small helpers
def _r(x):
    return None if x is None else int(round(float(x)))


def _deg(x) -> str:
    v = _r(x)
    return f"minus {-v} degrees" if v < 0 else f"{v} degree{'s' if v != 1 else ''}"


def say_time(hhmm: str) -> str:
    """"22:00" -> "10 pm", "07:08" -> "7:08 am", "00:00" -> "midnight", "12:00" -> "noon"."""
    try:
        h, m = (int(x) for x in hhmm.split(":")[:2])
    except (ValueError, AttributeError):
        return hhmm or ""
    if m == 0 and h == 0:
        return "midnight"
    if m == 0 and h == 12:
        return "noon"
    ap = "am" if h < 12 else "pm"
    h12 = h % 12 or 12
    return f"{h12} {ap}" if m == 0 else f"{h12}:{m:02d} {ap}"


def _minutes(hhmm: str) -> int | None:
    try:
        h, m = (int(x) for x in hhmm.split(":")[:2])
        return h * 60 + m
    except (ValueError, AttributeError):
        return None


def _date(s: str) -> date | None:
    try:
        return date.fromisoformat(s[:10])
    except (TypeError, ValueError):
        return None


def moon_phase(day: date | None = None) -> dict:
    """The moon on a day (local maths, good to about a day): {"phase", "illum", "waxing"}."""
    d = datetime.combine(day or date.today(), datetime.min.time()) + timedelta(hours=12)
    age = ((d - _NEW_MOON).total_seconds() / 86400.0) % SYNODIC
    illum = round((1 - math.cos(2 * math.pi * age / SYNODIC)) / 2 * 100)
    names = [(1.85, "New Moon"), (5.54, "Waxing Crescent"), (9.23, "First Quarter"), (12.92, "Waxing Gibbous"),
             (16.61, "Full Moon"), (20.30, "Waning Gibbous"), (23.99, "Last Quarter"), (27.68, "Waning Crescent"),
             (SYNODIC + 1, "New Moon")]
    phase = next(n for limit, n in names if age < limit)
    return {"phase": phase, "illum": float(illum), "waxing": age < SYNODIC / 2}


def next_full_moon(day: date | None = None) -> date:
    d = datetime.combine(day or date.today(), datetime.min.time())
    age = ((d - _NEW_MOON).total_seconds() / 86400.0) % SYNODIC
    ahead = (SYNODIC / 2 - age) % SYNODIC
    return (d + timedelta(days=ahead)).date()


def _is_day(hhmm: str, day: dict | None) -> bool:
    t = _minutes(hhmm)
    if t is None:
        return True
    rise = _minutes((day or {}).get("sunrise", "")) or 6 * 60
    sset = _minutes((day or {}).get("sunset", "")) or 18 * 60
    return rise <= t < sset


# ------------------------------------------------------------------ built-in Open-Meteo -> facts
def from_builtin(d: dict) -> dict:
    """heyclaude.weather.forecast(..., detail=True) -> facts."""
    now = {"temp": d.get("temp"), "condition": d.get("condition", ""), "icon": d.get("icon")}
    for k in ("feels", "wind", "high", "low"):
        if d.get(k) is not None:
            now[k] = d[k]
    for k in ("time", "date"):
        if d.get("_" + k):
            now[k] = d["_" + k]
    days = [dict(x) for x in d.get("_days") or []] or [
        {"name": x.get("day"), "high": x.get("high"), "low": x.get("low"), "icon": x.get("icon")} for x in d.get("days") or []]
    hours = [dict(x) for x in d.get("_hours") or []] or [dict(x) for x in d.get("hourly") or []]
    facts = {"location": d.get("location", ""), "unit": d.get("unit", "C"), "source": "Open-Meteo",
             "now": {k: v for k, v in now.items() if v not in (None, "")}, "days": days, "hours": hours}
    return facts


# ------------------------------------------------------------------ choosing what the answer is about
def _target_index(facts: dict, q: WeatherQuery) -> int:
    if q.when in ("tomorrow", "day"):
        return q.day if q.when == "day" else 1
    if q.when == "weekend":
        return q.day
    return 0


def _day(facts: dict, i: int) -> dict | None:
    days = facts.get("days") or []
    return days[i] if 0 <= i < len(days) else None


def _day_name(facts: dict, i: int) -> str:
    if i == 0:
        return "today"
    if i == 1:
        return "tomorrow"
    d = _day(facts, i) or {}
    dd = _date(d.get("date", ""))
    if dd:
        return "on " + _LONG[dd.weekday()]
    n = d.get("name") or ""
    return ("on " + n) if n else f"in {i} days"


def _upcoming_hours(facts: dict, n: int = 24, this_hour: bool = False) -> list[dict]:
    """The next hours after now (place time; with this_hour, the current one too), at most n."""
    hours = facts.get("hours") or []
    now = facts.get("now") or {}
    nd, nt = now.get("date", ""), _minutes(now.get("time", ""))
    if not nd or nt is None:
        return hours[:n]
    out = []
    for h in hours:
        hd, ht = h.get("date", ""), _minutes(h.get("time", ""))
        if not hd or ht is None:
            continue
        if (hd, ht) > (nd, nt - 60 if this_hour else nt):
            out.append(h)
    return out[:n]


def _wet(h: dict) -> bool:
    c = (h.get("condition") or "").lower()
    return (h.get("pop") or 0) >= 50 or any(w in c for w in ("rain", "drizzle", "shower", "thunder", "snow", "sleet")) \
        or h.get("icon") in ("rain", "storm", "snow")


def _local_alerts(facts: dict) -> list[dict]:
    """Alerts that are about this place: point/polygon-matched ones, or a country-level
    list whose area names the place."""
    a = facts.get("alerts") or {}
    items = [x for x in a.get("items") or [] if x.get("title") or x.get("severity")]
    if not a.get("country_level"):
        return items
    words = [w.lower() for w in str(facts.get("location", "")).replace(",", " ").split() if len(w) >= 4][:2]
    return [x for x in items if any(w in (x.get("area") or "").lower() for w in words)]


# ------------------------------------------------------------------ the card
_AQI_COLOR = {"good": "green", "fair": "yellow", "moderate": "yellow", "poor": "orange",
              "unhealthy for sensitive groups": "orange", "very poor": "red", "unhealthy": "red",
              "very unhealthy": "purple", "extremely poor": "purple", "hazardous": "purple"}


def _clip(s, n):
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def card(facts: dict, q: WeatherQuery | None = None, label: str = "") -> dict:
    """The `weather` widget's data (widgets/weather/widget.json)."""
    now = facts.get("now") or {}
    days = facts.get("days") or []
    today = days[0] if days else {}
    icon = now.get("icon") or icon_for(now.get("condition", ""), _is_day(now.get("time", ""), today))
    d: dict = {"location": _clip(label or facts.get("location") or "", 60), "temp": _r(now.get("temp")),
               "unit": facts.get("unit", "C"), "condition": _clip(now.get("condition", ""), 40), "icon": icon,
               "source": _clip(facts.get("source", ""), 30)}
    for k in ("feels", "wind"):
        if now.get(k) is not None:
            d[k] = _r(now[k])
    hi = today.get("high", now.get("high"))
    lo = today.get("low", now.get("low"))
    if hi is not None and lo is not None:
        d["high"], d["low"] = _r(hi), _r(lo)
    hrs = []
    for h in _upcoming_hours(facts, 6):
        hd = next((x for x in days if x.get("date") and x.get("date") == h.get("date")), today)
        ic = h.get("icon") or icon_for(h.get("condition", ""), _is_day(h.get("time", ""), hd))
        if h.get("temp") is not None and h.get("time"):
            hrs.append({"time": h["time"][:5], "temp": _r(h["temp"]), "icon": ic})
    if hrs:
        d["hourly"] = hrs
    start = 0
    t = _target_index(facts, q) if q else 0
    if t > 2:
        start = t                                   # "on Saturday": the window starts there
    rows = []
    for i, x in enumerate(days[start:start + 3], start=start):
        if x.get("high") is None or x.get("low") is None:
            continue
        dd = _date(x.get("date", ""))
        name = "Today" if i == 0 else (_SHORT[dd.weekday()] if dd else str(x.get("name") or "")[:3])
        rows.append({"day": name[:10], "low": _r(x["low"]), "high": _r(x["high"]),
                     "icon": x.get("icon") or icon_for(x.get("condition", ""), True)})
    if rows:
        d["days"] = rows
    # --- the new optional states (each hidden when its data isn't there)
    focus = q.focus if q else "general"
    local = _local_alerts(facts)
    al = facts.get("alerts") or {}
    pick = local[0] if local else ((al.get("items") or [None])[0] if focus == "alerts" else None)
    if pick:
        title = pick.get("title") or f"{al.get('count', 1)} warning{'s' if al.get('count', 1) != 1 else ''}"
        if not local and al.get("region"):
            title = f"{title} · {al['region']}"
        a = {"title": _clip(title, 60), "severity": pick.get("severity") or "unknown",
             "source": _clip(pick.get("sender") or al.get("source") or "", 40)}
        if pick.get("level") in ("yellow", "orange", "red", "green"):
            a["level"] = pick["level"]
        exp = pick.get("expires", "")
        if len(exp) >= 16:
            ed = _date(exp)
            a["until"] = (_SHORT[ed.weekday()] + " " if ed and ed != _date(now.get("date", "")) else "") + exp[11:16]
        more = (len(local) if local else int(al.get("count") or 0)) - 1
        if more > 0:
            a["more"] = more
        d["alert"] = a
    air = facts.get("air") or {}
    strip = {}
    if air.get("aqi") is not None:
        strip["aqi"] = _r(air["aqi"])
        strip["scale"] = air.get("scale", "")
        lab = air.get("label", "")
        strip["label"] = _clip(lab, 24)
        strip["color"] = air.get("color") or _AQI_COLOR.get(lab.lower(), "")
    uv = today.get("uv") if today.get("uv") is not None else air.get("uv")
    if uv is not None:
        strip["uv"] = _r(uv)
    pol = air.get("pollen") or {}
    if pol.get("level") and (pol["level"] != "none" or focus == "pollen"):
        strip["pollen"] = _clip(pol["level"] + (f" · {pol['top'].lower()}" if pol.get("top") else ""), 24)
    if strip:
        d["air"] = {k: v for k, v in strip.items() if v not in ("", None)}
    if today.get("sunrise") and today.get("sunset"):
        d["sun"] = {"rise": today["sunrise"][:5], "set": today["sunset"][:5]}
    m = today.get("moon") or {}
    if m.get("phase"):
        ph = m["phase"]
        waxing = "waxing" in ph.lower() or "first" in ph.lower() or (ph.lower() == "new moon" and
                                                                      _waxing_from_next(days))
        d["moon"] = {"phase": _clip(ph, 24), "illum": _r(m.get("illum", 0)), "waxing": bool(waxing)}
    elif facts.get("moon"):
        mm = facts["moon"]
        d["moon"] = {"phase": mm["phase"], "illum": _r(mm["illum"]), "waxing": bool(mm.get("waxing"))}
    ag = facts.get("agreement") or {}
    if ag.get("models") and ag.get("level"):            # "how sure is that?": the forecast models compared
        d["agreement"] = {k: v for k, v in ag.items() if k != "summary"}
    nc = facts.get("nowcast") or {}
    if nc.get("covered") and nc.get("steps") and (any(nc["steps"]) or focus in ("rain", "umbrella")):
        d["nowcast"] = {"text": _clip(nowcast_text(nc), 40), "steps": [int(x) for x in nc["steps"][:8]]}
        if nc.get("times"):
            d["nowcast"]["start"] = nc["times"][0]
    return {k: v for k, v in d.items() if v not in (None, "", [], {}) or k == "location"}


def _waxing_from_next(days: list[dict]) -> bool:
    if len(days) > 1:
        a, b = (days[0].get("moon") or {}).get("illum"), (days[1].get("moon") or {}).get("illum")
        if a is not None and b is not None:
            return b >= a
    return True


def nowcast_text(nc: dict) -> str:
    steps = nc.get("steps") or []
    if not steps:
        return ""
    if steps[0] >= 2:
        dry = next((i for i, s in enumerate(steps) if s <= 1), None)
        return "Rain now" + (f", easing in {dry * 15} min" if dry else "")
    first = next((i for i, s in enumerate(steps) if s >= 2), None)
    if first is None:
        return f"No rain for {len(steps) * 15 // 60} h" if len(steps) >= 4 else "No rain soon"
    return f"Rain in {first * 15} min"


# ------------------------------------------------------------------ the spoken summary
def _cond(s: str) -> str:
    c = (s or "").strip().lower()
    return {"clear sky": "clear", "mainly clear": "mostly clear", "slight rain": "light rain",
            "foggy": "foggy", "overcast": "overcast"}.get(c, c)


def _rain_outlook(facts: dict, hours: int = 12) -> str:
    up = _upcoming_hours(facts, hours, this_hour=True)
    if not up:
        return ""
    first = next((h for h in up if _wet(h)), None)
    if first is None:
        return ""
    c = (first.get("condition") or "").lower()
    what = "Snow" if "snow" in c else "Rain"
    if first is up[0]:
        return f"{what} around now."
    return f"{what} likely from around {say_time(first.get('time', ''))}."


def _day_sentence(facts: dict, i: int, wind_unit: str) -> str:
    d = _day(facts, i)
    if not d:
        return ""
    when = _day_name(facts, i)
    parts = [_cond(d.get("condition")) or ""]
    if d.get("low") is not None and d.get("high") is not None:
        parts.append(f"{_r(d['low'])} to {_deg(d['high'])}")
    if (d.get("pop") or 0) >= 30:
        parts.append(f"{_r(d['pop'])}% chance of rain")
    body = ", ".join(p for p in parts if p)
    return (when + ": " + body + ".") if body else ""


def speak(facts: dict, q: WeatherQuery, label: str = "", wind_unit: str = "km/h") -> str:
    now = facts.get("now") or {}
    place = "" if q.home else (label or q.place).split(",")[0]
    pre = f"In {place}, " if place else ""
    if q.sure:                                           # "how sure is that?": what the models say
        s = (facts.get("agreement") or {}).get("summary") or ""
        if s:
            return pre + s[0].lower() + s[1:] if pre else s
        return (speak(facts, replace(q, sure=False), label, wind_unit) +
                " I couldn't compare the forecast models just now.").strip()
    i = _target_index(facts, q)
    day = _day(facts, i) or {}
    f = q.focus
    if f in ("rain", "umbrella", "snow"):
        word = "snow" if f == "snow" else "rain"
        if i == 0 and q.when in ("now", "today", "tonight"):
            nc = facts.get("nowcast") or {}
            if nc.get("covered") and any(s >= 2 for s in nc.get("steps") or []):
                t = nowcast_text(nc)
                tail = " Take an umbrella." if f == "umbrella" else ""
                return _cap(f"{pre}{t[0].lower() + t[1:]}".replace(" min", " minutes") + "." + tail)
            out = _rain_outlook(facts, 18 if q.when != "now" else 6)
            if out and (f != "snow" or out.startswith("Snow")):
                return ("Yes, take an umbrella. " if f == "umbrella" else "") + _cap(pre + out[0].lower() + out[1:])
            return _cap(pre + (f"no {word} expected today" if f != "umbrella"
                               else "no rain expected today; you won't need an umbrella") + ".")
        if q.when in ("week", "weekend"):
            span = (facts.get("days") or [])[i:i + (2 if q.when == "weekend" else 7)]
            wet = [x for x in span if _wet(x) and (f != "snow" or "snow" in (x.get("condition") or "").lower())]
            names = [_LONG[_date(x["date"]).weekday()] if _date(x.get("date", "")) else x.get("name", "") for x in wet]
            if not names:
                return _cap(f"{pre}no {word} expected {'this weekend' if q.when == 'weekend' else 'this week'}.")
            return _cap(f"{pre}{word} likely on {_and(names)}.")
        pop = day.get("pop")
        when = _day_name(facts, i)
        if _wet(day) or (pop or 0) >= 40:
            s = f"{pre}{word} likely {when}" + (f", {_r(pop)}% chance." if pop else ".")
            return ("Yes, take an umbrella. " if f == "umbrella" else "") + _cap(s)
        return _cap(f"{pre}no {word} expected {when}") + (f", {_r(pop)}% chance." if pop else ".")
    if f == "alerts":
        al = facts.get("alerts") or {}
        if al.get("covered") is False or not al:
            return f"I don't have official weather warnings for {place or 'your area'}."
        local = _local_alerts(facts)
        if local:
            a = local[0]
            who = a.get("sender") or al.get("source") or ""
            title = (a.get("title") or "a weather warning").lower()
            until = ""
            if len(a.get("expires", "")) >= 16:
                tmrw = _date(a["expires"]) and _date(now.get("date", "")) and \
                    _date(a["expires"]) > _date(now.get("date", ""))
                until = f", until {say_time(a['expires'][11:16])}" + (" tomorrow" if tmrw else "")
            more = f" And {len(local) - 1} more." if len(local) > 1 else ""
            return f"{who + ' has ' if who else 'There is '}{_an(title)} {title}{until}.{more}".strip()
        n = int(al.get("count") or 0)
        if n and al.get("country_level"):
            return (f"Nothing for {place or 'your area'} itself, but {n} warning{'s' if n != 1 else ''} "
                    f"for {al.get('region', 'the country')}.")
        return f"No weather warnings for {place or 'your area'} right now."
    if f in ("air", "uv", "pollen"):
        air = facts.get("air") or {}
        uv = day.get("uv") if day.get("uv") is not None else air.get("uv")
        if f == "pollen":
            p = air.get("pollen") or {}
            if not p:
                return "I don't have pollen data there; it's only available for Europe."
            if p.get("level") == "none":
                return _cap(f"{pre}no pollen to speak of right now.")
            top = f", mostly {p['top'].lower()}" if p.get("top") else ""
            return _cap(f"{pre}pollen is {p['level']}{top}.")
        if f == "uv":
            if uv is None:
                return "I couldn't get the UV index."
            lvl = _uv_label(uv)
            return _cap(f"{pre}UV peaks at {_r(uv)} {_day_name(facts, i)}, {lvl}.") if i or q.when != "now" else \
                _cap(f"{pre}the UV index is {_r(uv)}, {lvl}.")
        if air.get("aqi") is None:
            return "I couldn't get the air quality there."
        scale = "European index" if air.get("scale") == "EU" else "AQI"
        s = f"{pre}air quality is {(air.get('label') or '').lower() or 'unknown'}, {scale} {_r(air['aqi'])}."
        return _cap(s)
    if f == "sun":
        which = "sunrise" if q.sub == "rise" else "sunset"
        if not day.get(which):
            return f"I couldn't get the {which} time."
        t = day[which]
        if i == 0 and which == "sunset" and _minutes(now.get("time", "")) is not None \
                and _minutes(now["time"]) > (_minutes(t) or 0):
            nxt = (_day(facts, 1) or {}).get("sunset")
            return f"Sunset was at {say_time(t)}" + (f"; tomorrow it's at {say_time(nxt)}." if nxt else ".")
        lead = "" if i == 0 else _day_name(facts, i) + ", "
        return _cap(f"{lead}{which} is at {say_time(t)}.")
    if f == "moon":
        m = day.get("moon") or facts.get("moon") or {}
        if not m:
            return "I couldn't get the moon phase."
        ph = m["phase"].lower()
        s = f"It's {_an(ph)} {ph}, {_r(m.get('illum', 0))}% lit."
        nf = facts.get("next_full_moon")
        if nf and "full" not in ph:
            s += f" The next full moon is {nf}."
        return s
    if f == "wind":
        if i == 0 and q.when == "now" and now.get("wind") is not None:
            g = f", gusting to {_r(now['gust'])}" if now.get("gust") else ""
            return _cap(f"{pre}wind is {_r(now['wind'])} {wind_unit}{g}.")
        if day.get("wind") is not None:
            return _cap(f"{pre}{_day_name(facts, i)}: wind around {_r(day['wind'])} {wind_unit}.")
    # general and temperature
    if q.when == "week":
        span = (facts.get("days") or [])[:7]
        his = [x["high"] for x in span if x.get("high") is not None]
        wet = [_LONG[_date(x["date"]).weekday()] for x in span if _wet(x) and _date(x.get("date", ""))]
        if not his:
            return "I couldn't get this week's forecast."
        s = f"{pre}highs of {_r(min(his))} to {_deg(max(his))} this week"
        s = s[0].upper() + s[1:]
        return s + (f", with rain likely on {_and(wet[:3])}." if wet else ", and mostly dry.")
    if q.when == "weekend":
        out = [_day_sentence(facts, j, wind_unit) for j in (i, i + 1)]
        out = [x for x in out if x]
        return _cap(pre + " ".join(_cap(x) if k else x for k, x in enumerate(out))) if out else \
            "I couldn't get the weekend forecast."
    if q.when in ("tomorrow", "day"):
        s = _day_sentence(facts, i, wind_unit)
        return _cap(pre + s) if s else "I don't have a forecast that far ahead."
    if q.when == "tonight" or q.part in ("evening", "night"):
        night = [h for h in _upcoming_hours(facts, 24) if (_minutes(h.get("time", "")) or 0) >= 18 * 60
                 or (_minutes(h.get("time", "")) or 0) < 6 * 60][:12]
        if night:
            low = min(h["temp"] for h in night if h.get("temp") is not None)
            wet = any(_wet(h) for h in night)
            c = _cond(night[len(night) // 2].get("condition", ""))
            s = f"{pre}tonight, {c + ', ' if c else ''}down to {_deg(low)}" + (", with rain likely." if wet else ", and dry.")
            return s[0].upper() + s[1:]
    if q.part in ("morning", "afternoon") and q.when == "today":
        lo_h, hi_h = (6, 12) if q.part == "morning" else (12, 18)
        span = [h for h in _upcoming_hours(facts, 24)
                if lo_h * 60 <= (_minutes(h.get("time", "")) or -1) < hi_h * 60 and h.get("date") == now.get("date")]
        if span:
            temps = [h["temp"] for h in span if h.get("temp") is not None]
            c = _cond(span[len(span) // 2].get("condition", ""))
            wet = any(_wet(h) for h in span)
            s = (f"{pre}this {q.part}, {c + ', ' if c else ''}around {_deg(sum(temps) / len(temps))}"
                 + (", with some rain." if wet else ", and dry."))
            return s[0].upper() + s[1:]
    if now.get("temp") is None:
        s = _day_sentence(facts, 0, wind_unit)
        return _cap(pre + s) if s else "I couldn't get the weather."
    c = _cond(now.get("condition", ""))
    first = f"{pre}{_deg(now['temp'])}" + (f" and {c}" if c else "")
    if now.get("feels") is not None and abs(_r(now["feels"]) - _r(now["temp"])) >= 2:
        first += f", feels like {_r(now['feels'])}"
    first = first[0].upper() + first[1:] + "."
    if f == "temp":
        return first
    second = ""
    sev = [a for a in _local_alerts(facts) if SEVERITY.get(a.get("severity", ""), 0) >= 3]
    if sev:
        t = (sev[0].get("title") or "a severe weather warning").lower()
        second = f"There's {_an(t)} {t} in effect."
    elif _wet({"condition": now.get("condition", "")}):
        # raining now: when it stops, not that it rains
        dry = next((h for h in _upcoming_hours(facts, 12) if not _wet(h)), None)
        second = f"Dry from around {say_time(dry.get('time', ''))}." if dry else ""
    else:
        second = _rain_outlook(facts, 12)
        if not second and q.when == "today" and day.get("high") is not None:
            second = f"A high of {_r(day['high'])} today."
    return (first + " " + second).strip()


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def _uv_label(uv) -> str:
    u = float(uv)
    return "low" if u < 3 else "moderate" if u < 6 else "high" if u < 8 else "very high" if u < 11 else "extreme"


def _an(word: str) -> str:
    return "an" if word[:1].lower() in "aeiou" else "a"


def _and(xs: list[str]) -> str:
    xs = [x for x in xs if x]
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1] if xs else ""
