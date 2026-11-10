"""mcp__info__calculate / convert_units / convert_currency -> the convert card
(heyclaude/mcp_info/convert.py makes the numbers and the working; this only picks
what the card shows)."""
from __future__ import annotations

import re

_CATEGORY = {"length": "Length", "mass": "Mass", "temperature": "Temperature", "volume": "Volume",
             "speed": "Speed", "area": "Area", "data": "Data", "time": "Time"}


def _clip(s, n: int) -> str:
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def adapt(args: dict, result, raw: str) -> dict | None:
    r = result if isinstance(result, dict) else None
    if r is None or r.get("kind") not in ("calc", "units", "currency"):
        return None
    text = _clip(r.get("result_text"), 32)
    steps = [_clip(s, 80) for s in (r.get("steps") or []) if str(s).strip()][:5]
    if not text or not steps:
        return None
    kind = r["kind"]
    out = {"kind": kind, "result": text, "steps": steps}
    if kind == "calc":
        out["heading"] = "Maths"
    elif kind == "units":
        cat = _CATEGORY.get(str(r.get("category")), "Units")
        out["heading"] = _clip(f"{cat} · {r.get('from', '')} → {r.get('to', '')}", 60)
        out["unit"] = _clip(r.get("to"), 12)
    else:
        out["heading"] = _clip(f"Currency · {r.get('from', '')} → {r.get('to', '')}", 60)
        out["unit"] = _clip(r.get("to"), 12)
        src = str(r.get("source") or "")
        m = re.search(r"\(([^)]+)\)", src)                  # "ExchangeRate-API (open.er-api.com)" -> the site
        out["source"] = _clip(m.group(1) if m else src, 40)
        if r.get("date"):
            out["date"] = _clip(r["date"], 12)
        if r.get("stale"):
            out["stale"] = True
    if r.get("note"):
        out["note"] = _clip(r["note"], 60)
    return {k: v for k, v in out.items() if v not in ("", None)}
