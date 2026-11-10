"""The personal dictionary (M4d), used by dictation and by the assistant's own
speech-to-text.

    dictionary:
      words: [Phoenix, eClass]          # Whisper's initial prompt: spell these right
      replacements: {hey cloud: hey Claude}
      snippets: {my email: me@example.com}

- words go into Whisper's initial prompt as a spelling hint (speech.manager
  builds it: "Vocabulary: Claude, Phoenix, eClass.").
- replacements fix what it still gets wrong, as whole words, any case:
  "Hey cloud, open it" -> "hey Claude, open it". A replacement whose key is
  all lower case keeps the case of a capital first letter
  ("Hey cloud" -> "Hey Claude").
- snippets: a short spoken phrase becomes saved text. Said on its own
  ("My email.") the whole dictation is the snippet; inside a sentence
  ("send it to my email please") just the phrase is replaced.
"""
from __future__ import annotations

import re

_PUNCT = " \t\r\n.,!?;:\"'"


def _phrase_re(phrase: str) -> re.Pattern | None:
    words = [re.escape(w) for w in re.split(r"[\s,]+", str(phrase).strip()) if w]
    if not words:
        return None
    # words may be separated by spaces, a comma or a hyphen ("e-class", "e class")
    return re.compile(r"(?<![\w])" + r"[\s,\-]*".join(words) + r"(?![\w])", re.I)


def _match_case(found: str, repl: str, key: str) -> str:
    if key == key.lower() and found[:1].isupper() and repl[:1].islower():
        return repl[:1].upper() + repl[1:]
    return repl


def norm(text: str) -> str:
    return " ".join(str(text).lower().strip(_PUNCT).replace(",", " ").split())


def words(cfg: dict | None) -> list[str]:
    return [str(w).strip() for w in ((cfg or {}).get("words") or []) if str(w).strip()]


def apply(text: str, cfg: dict | None, snippets: bool = True) -> str:
    """Replacements, then snippets (dictation only). Returns the fixed text."""
    if not text or not cfg:
        return text
    out = text
    for key, value in (cfg.get("replacements") or {}).items():
        rx = _phrase_re(key)
        if rx is not None and value is not None:
            out = rx.sub(lambda m, v=str(value), k=str(key): _match_case(m.group(0), v, k), out)
    snips = {norm(k): str(v) for k, v in (cfg.get("snippets") or {}).items() if norm(k) and v is not None}
    if not snips or not snippets:
        return out
    whole = snips.get(norm(out))
    if whole is not None:
        return whole
    for key, value in sorted(snips.items(), key=lambda kv: -len(kv[0])):   # longest first
        rx = _phrase_re(key)
        if rx is not None:
            out = rx.sub(lambda _m, v=value: v, out)
    return out
