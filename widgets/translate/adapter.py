"""mcp__info__translate -> the translate card: the source and the translation side by
side under their language tags, plus one alternative rendering when there is one."""
from __future__ import annotations

import re


def _clip(s, n: int) -> str:
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def adapt(args: dict, result, raw: str) -> dict | None:
    r = result if isinstance(result, dict) else None
    if r is None or not r.get("translation") or not r.get("text"):
        return None
    src, dst = str(r.get("from") or "").lower(), str(r.get("to") or "").lower()
    if not re.fullmatch(r"[a-z]{2,3}", src) or not re.fullmatch(r"[a-z]{2,3}", dst):
        return None
    data = {"from": src, "to": dst,
            "from_name": _clip(r.get("from_name") or src.upper(), 20), "to_name": _clip(r.get("to_name") or dst.upper(), 20),
            "text": _clip(r["text"], 300), "translation": _clip(r["translation"], 300)}
    alts = [a for a in (r.get("alternatives") or []) if isinstance(a, str) and a.strip()]
    if alts:
        data["alt"] = _clip(alts[0], 80)
    if r.get("detected"):
        data["detected"] = True
    if r.get("source"):
        data["source"] = _clip(r["source"], 30)
    return data
