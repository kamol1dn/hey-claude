"""mcp__info__system_status -> the system card's data (widgets/system).

The tool's JSON (heyclaude/mcp_info/system.py) has the raw readings; this shortens the
hardware names ("AMD Ryzen 9 7940HS w/ Radeon 780M Graphics" -> "Ryzen 9 7940HS"),
turns MB into GB, writes the battery's note, and adds hey-claude's own devices from
heyclaude.widgets.APP_STATE (speech-to-text and voice: GPU or CPU), which only the app
knows (the tool runs in the info server's process).
"""
from __future__ import annotations

import json
import re


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def short_cpu(name: str) -> str:
    s = " ".join(str(name or "").split())
    s = re.sub(r"\((?:R|TM)\)", "", s)
    s = re.sub(r"\s+(?:w/|with)\s+.*$", "", s)                 # "w/ Radeon 780M Graphics"
    s = re.sub(r"\s+@\s+[\d.]+\s*GHz.*$", "", s, flags=re.I)
    s = re.sub(r"\b(?:CPU|Processor|\d+(?:-|\s)Core|\d+th Gen|\d+nd Gen|\d+rd Gen|\d+st Gen)\b", "", s, flags=re.I)
    s = re.sub(r"^(?:AMD|Intel|Qualcomm(?:\s+Technologies,?\s+Inc)?)\s+", "", s.strip(), flags=re.I)
    s = " ".join(s.split())
    return s[:28]


def short_gpu(name: str) -> str:
    s = " ".join(str(name or "").split())
    s = re.sub(r"^(?:NVIDIA\s+)?(?:GeForce\s+)?", "", s, flags=re.I)
    s = re.sub(r"\s+GPU$", "", s, flags=re.I)
    return s.strip()[:28]


def _note(b: dict) -> str:
    if b.get("charging"):
        return "charging"
    if b.get("plugged"):
        return "plugged in"
    m = _num(b.get("time_left_min"))
    left = ""
    if m is not None and m > 0:
        h, mm = divmod(int(m), 60)
        left = (f"{h} h {mm} min" if h and mm else f"{h} h" if h else f"{mm} min") + " left"
    if b.get("saver"):
        return (left + " · saver") if left else "battery saver"
    return left or "on battery"


def _devices(state: dict) -> list[dict]:
    out = []
    for key, name in (("stt", "stt"), ("voice", "voice")):
        v = " ".join(str(state.get(key) or "").split())
        if v:
            out.append({"name": name, "value": v[:32], "gpu": bool(re.search(r"\bgpu\b", v.split("(")[0], re.I))})
    return out


def adapt(args: dict, result, raw: str, state: dict | None = None) -> dict | None:
    r = result if isinstance(result, dict) else None
    if r is None:
        try:
            r = json.loads(raw) if raw.lstrip().startswith("{") else None
        except ValueError:
            r = None
    if not isinstance(r, dict) or not any(k in r for k in ("cpu", "gpu", "ram", "battery")):
        return None
    data: dict = {}
    b = r.get("battery")
    if isinstance(b, dict) and _num(b.get("percent")) is not None:
        data["battery"] = {"percent": max(0, min(100, int(round(b["percent"])))), "charging": bool(b.get("charging")),
                           "plugged": bool(b.get("plugged")), "note": _note(b)}
    c = r.get("cpu")
    if isinstance(c, dict) and _num(c.get("percent")) is not None:
        cpu = {"percent": max(0.0, min(100.0, float(c["percent"]))), "label": short_cpu(c.get("name", ""))}
        if _num(c.get("cores")):
            cpu["sub"] = f"{int(c['cores'])} cores"
        data["cpu"] = cpu
    g = r.get("gpu")
    if isinstance(g, dict):
        if _num(g.get("percent")) is not None:
            gpu = {"percent": max(0.0, min(100.0, float(g["percent"]))), "label": short_gpu(g.get("name", ""))}
            if _num(g.get("temp_c")) is not None:
                gpu["sub"] = f"{int(g['temp_c'])} °C"
            data["gpu"] = gpu
        if _num(g.get("vram_total_mb")) and _num(g.get("vram_used_mb")) is not None:
            data["vram"] = {"used": round(g["vram_used_mb"] / 1024, 1), "total": round(g["vram_total_mb"] / 1024, 1)}
    m = r.get("ram")
    if isinstance(m, dict) and _num(m.get("total_gb")) and _num(m.get("used_gb")) is not None:
        data["ram"] = {"used": round(float(m["used_gb"]), 1), "total": round(float(m["total_gb"]), 1)}
    if not data:
        return None
    if state is None:
        from heyclaude.widgets import APP_STATE
        state = APP_STATE
    dev = _devices(state)
    if dev:
        data["devices"] = dev
    return data
