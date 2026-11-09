"""weather-mcp's answers (markdown text, no structured content) -> plain facts.

Each tool answers in markdown: `# Title`, `## Section`, and `**Key:** value`
lines (see tests/fixtures/weather_mcp/ for one real answer per tool). These
parsers read what the card and the spoken summary need and ignore the rest;
anything they can't read is left out, never guessed.

Facts (all keys optional):
    now:     {"temp", "feels", "condition", "icon", "wind", "gust", "humidity", "time": "19:30", "date": "2026-11-09"}
    days:    [{"date", "name": "Monday", "high", "low", "condition", "icon", "pop", "precip_mm", "sunrise",
               "sunset", "uv", "moon": {"phase", "illum"}}]
    hours:   [{"date", "time": "20:00", "temp", "feels", "condition", "icon", "pop"}]
    alerts:  {"covered": bool, "region": "United Kingdom", "country_level": bool, "count": n, "source": "UK Met Office",
              "items": [{"title", "level", "severity", "headline", "area", "onset", "expires", "sender"}]}
    air:     {"aqi", "scale": "EU"|"US", "label", "color", "uv", "uv_label", "pollen": {"level", "top", "grains"}}
    nowcast: {"covered": bool, "times": ["20:30", ...], "steps": [0..4], "model"}
    next_full_moon: "Nov 24"
"""
from __future__ import annotations

import re
from datetime import datetime

_KV = re.compile(r"\*\*(?P<k>[^*:]+):\*\*\s*(?P<v>[^|\n]*?)(?=\s*\|\s*\*\*|\s*$)", re.M)
_NUM = re.compile(r"-?\d+(?:\.\d+)?")
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
          "november", "december"]
BANDS = {"none": 0, "trace": 1, "light": 2, "moderate": 3, "heavy": 4}
SEVERITY = {"minor": 1, "moderate": 2, "severe": 3, "extreme": 4}


def text_of(result) -> str:
    """An MCP CallToolResult (or its dict / content list / plain text) -> its text."""
    if result is None:
        return ""
    if isinstance(result, str):
        return result
    content = getattr(result, "content", None)
    if content is None and isinstance(result, dict):
        content = result.get("content")
    if content is None and isinstance(result, list):
        content = result
    out = []
    for c in content or []:
        t = getattr(c, "text", None)
        if t is None and isinstance(c, dict):
            t = c.get("text")
        if t:
            out.append(str(t))
    return "\n".join(out)


def _num(s) -> float | None:
    m = _NUM.search(str(s or ""))
    return float(m.group(0)) if m else None


def _kv(block: str) -> dict:
    out = {}
    for m in _KV.finditer(block):
        k = m.group("k").strip().lower()
        if k not in out:
            out[k] = m.group("v").strip()
    return out


def sections(md: str, level: int = 2) -> list[tuple[str, str]]:
    """[(heading, body)] for the `#`*level headings (the body runs to the next one)."""
    mark = "#" * level + " "
    out, head, buf = [], None, []
    for line in md.splitlines():
        if line.startswith(mark):
            if head is not None:
                out.append((head, "\n".join(buf)))
            head, buf = line[len(mark):].strip(), []
        elif head is not None:
            buf.append(line)
    if head is not None:
        out.append((head, "\n".join(buf)))
    return out


def icon_for(condition: str, is_day: bool = True) -> str:
    """weather-mcp's condition text -> the card's icon set (sun partly cloud rain storm snow fog wind night)."""
    c = (condition or "").lower()
    if re.search(r"thunder|storm|hail", c):
        return "storm"
    if re.search(r"snow|sleet|flurr|ice pellet|blizzard", c):
        return "snow"
    if re.search(r"rain|drizzle|shower", c):
        return "rain"
    if re.search(r"fog|mist|haze", c):
        return "fog"
    if re.search(r"overcast|cloudy|clouds", c) and not re.search(r"partly|mostly sunny|mainly clear", c):
        return "cloud"
    if re.search(r"partly|scattered|mostly sunny|few clouds", c):
        return "partly" if is_day else "night"
    if re.search(r"wind|breez", c):
        return "wind"
    if re.search(r"clear|sunny|fair|mainly clear", c):
        return "sun" if is_day else "night"
    return "cloud"


def _hhmm(s: str) -> str:
    """"07:08", "7:08 AM", "19:30" -> "07:08" / "19:30"; "" when unreadable."""
    m = re.search(r"(\d{1,2}):(\d{2})(?:\s*([AaPp])\.?[Mm]\.?)?", s or "")
    if not m:
        return ""
    h, mi = int(m.group(1)), int(m.group(2))
    if m.group(3):
        pm = m.group(3).lower() == "p"
        h = (h % 12) + (12 if pm else 0)
    return f"{h:02d}:{mi:02d}"


def _date_of(head: str, year_hint: int | None = None) -> str:
    """"Monday, November 9" / "11/9/2026, 00:00" / "Nov 9, 2026, 19:30" -> "2026-11-09" ("" if unknown)."""
    h = head.lower()
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", h)
    if m:
        return f"{int(m.group(3)):04d}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    m = re.search(r"\b(" + "|".join(x[:3] for x in MONTHS) + r")[a-z]*\.? (\d{1,2})(?:, (\d{4}))?", h)
    if m:
        mo = [x[:3] for x in MONTHS].index(m.group(1)) + 1
        y = int(m.group(3)) if m.group(3) else (year_hint or datetime.now().year)
        return f"{y:04d}-{mo:02d}-{int(m.group(2)):02d}"
    return ""


def _range(v: str) -> tuple[float | None, float | None]:
    """"High 10°C / Low 6°C" -> (10, 6)"""
    hi = re.search(r"high\s*(-?\d+(?:\.\d+)?)", v or "", re.I)
    lo = re.search(r"low\s*(-?\d+(?:\.\d+)?)", v or "", re.I)
    return (float(hi.group(1)) if hi else None), (float(lo.group(1)) if lo else None)


# ------------------------------------------------------------------ the tools
def current(md: str) -> dict:
    """get_current_conditions (or the summary's "Current Weather Conditions" part)."""
    kv = _kv(md)
    if "temperature" not in kv:
        return {}
    out = {"temp": _num(kv["temperature"]), "condition": kv.get("conditions", "")}
    if kv.get("feels like"):
        out["feels"] = _num(kv["feels like"])
    if kv.get("humidity"):
        out["humidity"] = _num(kv["humidity"])
    if kv.get("wind"):
        out["wind"] = _num(kv["wind"])
        g = re.search(r"gust\w*\s+(?:to\s+)?(\d+(?:\.\d+)?)", kv["wind"], re.I)
        if g:
            out["gust"] = float(g.group(1))
    if kv.get("today's range"):
        out["high"], out["low"] = _range(kv["today's range"])
    if kv.get("time"):
        out["time"] = _hhmm(kv["time"])
        out["date"] = _date_of(kv["time"])
    return {k: v for k, v in out.items() if v not in (None, "")}


def _moon(v: str) -> dict:
    """"Waxing Crescent (5% illuminated) · Rise 10:02 · Set 16:51" -> {"phase", "illum", "rise", "set"}"""
    m = re.match(r"\s*([A-Za-z ]+?)\s*\((\d+(?:\.\d+)?)%", v or "")
    if not m:
        return {}
    out = {"phase": m.group(1).strip(), "illum": float(m.group(2))}
    r = re.search(r"rise\s+(\d{1,2}:\d{2})", v, re.I)
    s = re.search(r"set\s+(\d{1,2}:\d{2})", v, re.I)
    if r:
        out["rise"] = _hhmm(r.group(1))
    if s:
        out["set"] = _hhmm(s.group(1))
    return out


def daily(md: str, year_hint: int | None = None) -> list[dict]:
    """get_forecast (daily; with include_astronomy, moon and twilight too)."""
    days = []
    for head, body in sections(md, 2):
        kv = _kv(body)
        if "temperature" not in kv or "/" in head and ":" in head:
            continue
        hi, lo = _range(kv["temperature"])
        if hi is None and lo is None:
            continue
        d = {"date": _date_of(head, year_hint), "name": head.split(",")[0].strip(), "high": hi, "low": lo,
             "condition": kv.get("conditions", "")}
        d["icon"] = icon_for(d["condition"], True)
        if kv.get("precipitation chance"):
            d["pop"] = _num(kv["precipitation chance"])
        if kv.get("precipitation"):
            d["precip_mm"] = _num(kv["precipitation"])
        for k in ("sunrise", "sunset"):
            if kv.get(k):
                d[k] = _hhmm(kv[k])
        if kv.get("uv index"):
            d["uv"] = _num(kv["uv index"])
        if kv.get("wind"):
            d["wind"] = _num(kv["wind"])
        if kv.get("moon"):
            d["moon"] = _moon(kv["moon"])
        days.append({k: v for k, v in d.items() if v not in (None, "", {})})
    return days


def next_full_moon(md: str) -> str:
    m = re.search(r"\*\*Next full moon:\*\*\s*([A-Za-z]{3,9}\.? \d{1,2})", md or "")
    return m.group(1) if m else ""


def hourly(md: str) -> list[dict]:
    """get_forecast granularity=hourly."""
    hours = []
    for head, body in sections(md, 2):
        if not re.search(r"\d{1,2}:\d{2}", head):
            continue
        kv = _kv(body)
        if "temperature" not in kv:
            continue
        h = {"date": _date_of(head), "time": _hhmm(head), "temp": _num(kv["temperature"]),
             "condition": kv.get("conditions", "")}
        f = re.search(r"feels like\s*(-?\d+(?:\.\d+)?)", kv["temperature"], re.I)
        if f:
            h["feels"] = float(f.group(1))
        if kv.get("precipitation chance"):
            h["pop"] = _num(kv["precipitation chance"])
        hours.append({k: v for k, v in h.items() if v not in (None, "")})
    return hours


def nowcast(md: str) -> dict:
    """get_forecast granularity=minutely: the 15-minute nowcast table, or {"covered": False}."""
    rows = re.findall(r"^\|\s*(\d{1,2}:\d{2})\s*\|\s*([a-z]+)\s*\|", md or "", re.M)
    if not rows:
        return {"covered": False} if re.search(r"no 15-minute|not available|doesn't cover|does not cover", md or "", re.I) \
            else {}
    out = {"covered": True, "times": [_hhmm(t) for t, _ in rows], "steps": [BANDS.get(b.lower(), 0) for _, b in rows]}
    m = re.search(r"\*\*Model:\*\*\s*([^\n—]+?)\s*(?:—|\bvia\b|$)", md, re.M)
    if m:
        out["model"] = m.group(1).strip()
    return out


def alerts(md: str) -> dict:
    """get_alerts (standard detail), or the summary's alerts part (counts only)."""
    md = md or ""
    if re.search(r"not (?:yet )?available for|no alert coverage|outside (?:the )?(?:alert )?coverage", md, re.I):
        return {"covered": False}
    out: dict = {"covered": True, "items": []}
    m = re.search(r"^#+\s*Weather Alerts\s*[—-]\s*(.+)$", md, re.M)
    if m:
        out["region"] = m.group(1).strip()
    out["country_level"] = bool(re.search(r"matched at country level", md, re.I))
    m = re.search(r"\*\*(\d+) active (?:warning|alert)s?\b", md)
    out["count"] = int(m.group(1)) if m else 0
    m = re.search(r"national warnings:\s*([^)\n]+)\)", md) or re.search(r"Data source:\*?\s*([^.*\n]+)", md)
    if m:
        out["source"] = m.group(1).strip(" *")
    # one block per alert: `<emoji> **Title** — Colour (Severity)` then `---` then key lines
    blocks = re.split(r"\n(?=\S*\s*\*\*[^*\n]+\*\*\s*[—-]\s*\w+\s*\([A-Za-z]+\))", "\n" + md)
    for b in blocks:
        h = re.match(r"\s*\S*\s*\*\*(?P<t>[^*\n]+)\*\*\s*[—-]\s*(?P<lvl>\w+)\s*\((?P<sev>[A-Za-z]+)\)", b)
        if not h:
            continue
        kv = _kv(b)
        area = re.search(r"\*\*Area:\*\*\s*([^\n]+)", b)          # "A | B | C": the whole line
        a = {"title": h.group("t").strip(), "level": h.group("lvl").lower(), "severity": h.group("sev").lower(),
             "headline": kv.get("headline", ""), "area": area.group(1).strip() if area else "",
             "onset": kv.get("onset", ""),
             "expires": kv.get("expires", ""), "sender": kv.get("sender", "")}
        out["items"].append({k: v for k, v in a.items() if v})
    if not out["count"]:
        out["count"] = len(out["items"])
    if not out["items"] and out["count"]:
        # summary detail: counts only ("**By severity:** Moderate: 1", "**By colour:** Yellow: 1")
        kv = _kv(md)
        sev = re.match(r"\s*([A-Za-z]+)", kv.get("by severity", ""))
        col = re.match(r"\s*([A-Za-z]+)", kv.get("by colour", "") or kv.get("by color", ""))
        out["items"].append({k: v for k, v in {"title": "", "severity": sev.group(1).lower() if sev else "",
                                                 "level": col.group(1).lower() if col else ""}.items() if v})
    out["items"].sort(key=lambda a: -SEVERITY.get(a.get("severity", ""), 0))
    return out


def _pollen(md: str) -> dict:
    body = ""
    for head, b in sections(md, 2):
        if "pollen" in head.lower():
            body = b
    if not body:
        return {}
    grains = {k.strip(): _num(v) for k, v in re.findall(r"\*\*([A-Za-z ]+):\*\*\s*([\d.]+)\s*grains", body)}
    if not grains:
        return {}
    top, n = max(grains.items(), key=lambda kv: kv[1] or 0)
    n = n or 0
    level = "none" if n <= 0 else "low" if n < 10 else "moderate" if n < 50 else "high" if n < 200 else "very high"
    return {"level": level, "top": top if n > 0 else "", "grains": n}


def air(md: str) -> dict:
    """get_air_quality: AQI (European in Europe, else US), UV, pollen (Europe only)."""
    md = md or ""
    out = {}
    m = re.search(r"^##\s*\S*\s*(European|US|United States)\s+Air Quality Index:\s*(\d+)", md, re.M)
    if m:
        out["scale"] = "EU" if m.group(1).startswith("Euro") else "US"
        out["aqi"] = float(m.group(2))
        cat = re.search(r"\*\*Category:\*\*\s*([^(\n]+?)\s*(?:\((\w+)\))?\s*$", md[m.end():], re.M)
        if cat:
            out["label"] = cat.group(1).strip()
            if cat.group(2):
                out["color"] = cat.group(2).lower()
    us = re.search(r"US AQI:\s*(\d+)\s*\(([^)]+)\)", md)
    if us and "aqi" not in out:
        out.update(scale="US", aqi=float(us.group(1)), label=us.group(2).strip())
    m = re.search(r"^##\s*\S*\s*UV Index:\s*(\d+(?:\.\d+)?)", md, re.M)
    if m:
        out["uv"] = float(m.group(1))
        lv = re.search(r"\*\*Level:\*\*\s*([^\n]+)", md[m.end():])
        if lv:
            out["uv_label"] = lv.group(1).strip()
    p = _pollen(md)
    if p:
        out["pollen"] = p
    return out


def summary(md: str) -> dict:
    """get_weather_summary: its parts by their `# ` titles."""
    facts: dict = {}
    for head, body in sections(md, 1):
        h = head.lower()
        if h.startswith("current weather"):
            facts["now"] = current(body)
        elif h.startswith("weather forecast") and "hourly" in h:
            facts["hours"] = hourly(body)
        elif h.startswith("weather forecast"):
            facts["days"] = daily(body)
        elif h.startswith("weather alerts"):
            facts["alerts"] = alerts("# " + head + "\n" + body)
        elif h.startswith("air quality"):
            facts["air"] = air(body)
    return {k: v for k, v in facts.items() if v}


def location_of(md: str) -> tuple[float, float] | None:
    """search_location's first hit -> (lat, lon)."""
    m = re.search(r"Latitude:\s*(-?\d+(?:\.\d+)?),\s*Longitude:\s*(-?\d+(?:\.\d+)?)", md or "") or \
        re.search(r"\*\*Coordinates:\*\*\s*(-?\d+(?:\.\d+)?)°?,\s*(-?\d+(?:\.\d+)?)", md or "")
    return (float(m.group(1)), float(m.group(2))) if m else None


def place_name_of(md: str) -> str:
    """search_location's first hit -> "Greater London, England, United Kingdom"."""
    m = re.search(r"\*\*Full Name:\*\*\s*([^\n]+)", md or "")
    return m.group(1).strip() if m else ""
