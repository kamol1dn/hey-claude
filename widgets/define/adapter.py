"""mcp__info__define -> the define card: the word, its IPA, and up to three senses
(each with its part of speech and an example), taken across the parts of speech so
a noun-and-verb word shows both."""
from __future__ import annotations

import re


def _clip(s, n: int) -> str:
    # Wiktionary's stress marks (кни́гу) are combining accents most fonts misplace
    s = re.sub(r"[̀́]", "", str(s or ""))
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def adapt(args: dict, result, raw: str) -> dict | None:
    r = result if isinstance(result, dict) else None
    if r is None or not r.get("word") or not isinstance(r.get("meanings"), list):
        return None
    meanings = [m for m in r["meanings"] if isinstance(m, dict) and isinstance(m.get("senses"), list) and m["senses"]]
    if not meanings:
        return None
    # the first part of speech gets two senses when there's room, every other one its first
    picks: list[tuple[int, int]] = []
    for mi, m in enumerate(meanings[:3]):
        picks.append((mi, 0))
    mi = 0
    while len(picks) < 3 and mi < len(meanings):
        for si in range(1, len(meanings[mi]["senses"])):
            if len(picks) < 3:
                picks.append((mi, si))
        mi += 1
    senses = []
    for mi, si in sorted(picks[:3]):
        s = meanings[mi]["senses"][si]
        if not isinstance(s, dict) or not s.get("definition"):
            continue
        out = {"pos": _clip(meanings[mi].get("pos"), 20), "text": _clip(s["definition"], 220)}
        if s.get("example"):
            out["example"] = _clip(s["example"], 120)
        if s.get("example_translation"):
            out["example_tr"] = _clip(s["example_translation"].split(" / ")[0], 120)
        senses.append({k: v for k, v in out.items() if v})
    if not senses:
        return None
    data = {"word": _clip(r["word"], 40), "language": _clip(r.get("language") or r.get("lang") or "English", 20),
            "senses": senses}
    if r.get("phonetic"):
        data["ipa"] = _clip(r["phonetic"], 40)
    if r.get("source"):
        data["source"] = _clip(r["source"], 40)
    return data
