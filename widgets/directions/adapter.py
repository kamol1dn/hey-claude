"""directions / place (heyclaude/mcp_info/places.py) -> the directions card.

The map is a PNG the tool rendered under state/maps/; the card gets it as a file: URL
only when it really is one of those (anything else is dropped and the card draws its
placeholder grid under the route). The arrival time is worked out here, at show time.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path

MODE_WORD = {"driving": "by car", "walking": "on foot", "cycling": "by bike"}


def _clip(s, n: int) -> str:
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _json(result, raw: str):
    if isinstance(result, dict):
        return result
    try:
        v = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return v if isinstance(v, dict) else None


def map_url(p) -> str:
    """A file: URL for a map PNG under state/maps/ (absolute, or relative to the repo), else ""."""
    from heyclaude.config import ROOT, STATE_DIR
    if not isinstance(p, str) or not p.strip():
        return ""
    try:
        path = Path(p)
        if not path.is_absolute():
            path = ROOT / path
        path = path.resolve()
        maps = (STATE_DIR / "maps").resolve()
        if maps in path.parents and re.fullmatch(r"map-[0-9a-f]+\.png", path.name) and path.is_file():
            return path.as_uri()
    except (OSError, ValueError):
        pass
    return ""


def _xy(v) -> list | None:
    if isinstance(v, (list, tuple)) and len(v) == 2 and all(isinstance(n, (int, float)) and not isinstance(n, bool) for n in v):
        return [round(min(1.5, max(-0.5, float(n))), 4) for n in v]
    return None


def short_address(address: str, name: str) -> str:
    """Nominatim's long display name -> "1 Pariser Platz, Mitte, Berlin, Germany"."""
    parts = [p.strip() for p in str(address or "").split(",") if p.strip()]
    if parts and name and parts[0].lower() == str(name).strip().lower():
        parts = parts[1:]
    parts = [p for p in parts if not re.fullmatch(r"[\d\s-]{4,10}", p)]        # postcodes
    if len(parts) >= 2 and re.fullmatch(r"\d+[a-zA-Z]?", parts[0]):
        parts = [parts[0] + " " + parts[1]] + parts[2:]
    parts = [p for p in parts if p not in ("Metropolitan France",)]
    if len(parts) > 4:
        parts = parts[:2] + parts[-2:]
    return _clip(", ".join(parts), 110)


def adapt(args: dict, result, raw: str, now: datetime | None = None) -> dict | None:
    v = _json(result, raw)
    if not v:
        return None
    pins = [p for p in (_xy(x) for x in (v.get("pins") or [])[:2]) if p]
    out: dict = {"attribution": "© OpenStreetMap"}
    url = map_url(v.get("map"))
    if url:
        out["map"] = url
    if v.get("to") and v.get("duration_s") is not None:                       # directions()
        try:
            secs = float(v["duration_s"])
        except (TypeError, ValueError):
            return None
        route = [p for p in (_xy(x) for x in (v.get("route") or [])) if p][:200]
        arrive = ((now or datetime.now()) + timedelta(seconds=secs)).strftime("%H:%M")
        out.update(kind="route", title=_clip("Directions · " + str(v["to"]), 60),
                   mode=v.get("mode") if v.get("mode") in MODE_WORD else "driving",
                   origin=_clip(v.get("from"), 60), dest=_clip(v["to"], 60),
                   duration=_clip(v.get("duration") or f"{max(1, round(secs / 60))} min", 20),
                   distance=_clip(v.get("distance") or "", 20), arrive=arrive)
        out["how"] = MODE_WORD[out["mode"]]
        if route:
            out["route"] = route
        if pins:
            out["pins"] = pins
        return out
    if v.get("name") and ("lat" in v or v.get("address")):                     # place()
        out.update(kind="place", title=_clip("Place · " + str(v["name"]), 60), name=_clip(v["name"], 60),
                   address=short_address(v.get("address", ""), v["name"]))
        kind = _clip(v.get("kind") or "", 24)
        if kind and kind.lower() not in ("yes", "point"):
            out["place_kind"] = kind
        out["pins"] = pins[:1] or [[0.5, 0.5]]
        return out
    return None
