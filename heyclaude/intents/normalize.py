"""Text clean-up before the grammar: lower case, no punctuation, contractions
spelled out, polite filler and the wake phrase removed, and the Whisper slips
we have seen ("dark mod", "volume tree zero", "nest song") put right.

Everything here is cheap string work (well under a millisecond a call).
"""
from __future__ import annotations

import difflib
import re

_CONTRACTIONS = [
    (r"\bwhat's\b", "what is"), (r"\bwhats\b", "what is"), (r"\bit's\b", "it is"), (r"\bthat's\b", "that is"),
    (r"\bthere's\b", "there is"), (r"\bhow's\b", "how is"), (r"\bwho's\b", "who is"), (r"\blet's\b", "let us"),
    (r"\bi'm\b", "i am"), (r"\bi'd\b", "i would"), (r"\bi'll\b", "i will"), (r"\byou're\b", "you are"),
    (r"\bcan't\b", "cannot"), (r"\bwon't\b", "will not"), (r"\bdon't\b", "do not"), (r"\bdoesn't\b", "does not"),
    (r"\bisn't\b", "is not"), (r"\baren't\b", "are not"), (r"\bdidn't\b", "did not"), (r"\bshouldn't\b", "should not"),
    (r"\bwouldn't\b", "would not"), (r"\bcouldn't\b", "could not"), (r"\bi've\b", "i have"), (r"\bwhere's\b", "where is"),
]
_CONTRACTIONS = [(re.compile(a), b) for a, b in _CONTRACTIONS]

# Said before the request: the wake phrase, hesitations, politeness.
_LEAD = re.compile(
    r"^(?:(?:hey|hi|okay|ok|o k|yo|so|um+|uh+|er|erm|hmm|well|right|alright|and|then|also|now|please|kindly|"
    r"claude|clod|cloud|clawed|jarvis|"
    r"can you|could you|would you|will you|can u|would you mind|would you be so kind as to|"
    r"i want you to|i would like you to|i need you to|i would love it if you|i would love you to|"
    r"go ahead and|just|quickly|quick|real quick|maybe|perhaps|actually|oh|ah|alright then)\s+)+")
# Said after it.
_TAIL = re.compile(
    r"(?:\s+(?:please|pls|plz|for me|thanks|thank you|thank you very much|now|right now|real quick|quickly|"
    r"would you|could you|will you|if you can|if you could|if you would|okay|ok|claude|thx|cheers|mate|buddy|"
    r"already|yeah|then))+$")

# Whisper slips, fixed with the words around them (a bare word may be right: "tree").
_SLIPS = [
    (re.compile(r"\b(dark|light|darks)\s+(?:mod|mud|mood|mold|mode s|modes|more|mo)\b"), r"\1 mode"),
    (re.compile(r"\bdarkmode\b"), "dark mode"), (re.compile(r"\blightmode\b"), "light mode"),
    (re.compile(r"\b(?:nest|necks|nex|text|neck)\s+(song|track|tune|video)\b"), r"next \1"),
    (re.compile(r"\b(volume|brightness|to|at)\s+tree\b"), r"\1 three"),
    (re.compile(r"\btree\s+(zero|oh|five|0|5)\b"), r"three \1"),
    (re.compile(r"\b(?:volumes|valium|bolume|volum|volyume|volune)\b(?=\s+(?:to|up|down|at|\d|zero|one|two|three|"
                r"four|five|six|seven|eight|nine|ten|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|"
                r"max|half|off|on)\b|$)"), "volume"),
    (re.compile(r"\b(?:brightnes|britness|brightless|brightest)\b(?=\s+(?:to|up|down|at|\d)|$)"), "brightness"),
    (re.compile(r"\bun\s+mute\b"), "unmute"), (re.compile(r"\bun\s+pause\b"), "unpause"),
    (re.compile(r"\bwi\s+fi\b"), "wifi"), (re.compile(r"\bwhy\s+fi\b"), "wifi"), (re.compile(r"\bwifi s\b"), "wifi"),
    (re.compile(r"\bblue\s+tooth\b"), "bluetooth"),
    (re.compile(r"\bspot\s?ify\b"), "spotify"), (re.compile(r"\bspotty\s?fi\b"), "spotify"),
    (re.compile(r"\btimes\s+(?=for\s)"), "timer "),
    (re.compile(r"\bmutes\b(?=\s*$)"), "mute"),
]

# Keywords a slightly misheard word is pulled to (only words of 5+ letters, close spellings).
KEYWORDS = ("volume", "brightness", "unmute", "louder", "quieter", "dimmer", "brighter", "spotify", "timer",
            "minutes", "seconds", "bluetooth", "previous", "calculator", "notepad", "countdown")
_COMMON = {"timber", "valley", "values", "volumes", "louse", "quite", "quiet", "bright", "brighten", "minute",
           "second", "timers", "dimmers", "brightest", "column", "solemn", "letter", "lighter", "biter", "mutes",
           "minutes", "seconds", "hours", "louder", "brighter", "dimmer", "quieter", "previous", "muter",
           "times", "tighter", "tiger", "timed", "timely", "spotty", "linger", "longer", "bluetooth", "summer",
           "settings", "setter", "sitter", "litter"}


def _fuzzy_word(w: str) -> str:
    if len(w) < 5 or w in KEYWORDS or w in _COMMON or not w.isalpha():
        return w
    hit = difflib.get_close_matches(w, KEYWORDS, n=1, cutoff=0.86)
    return hit[0] if hit else w


def basic(text: str) -> str:
    """Lower case, '%' -> percent, contractions out, punctuation out, one space."""
    t = (text or "").lower().replace("’", "'").replace("‘", "'")
    t = re.sub(r"(\d)\s*%", r"\1 percent", t)
    t = t.replace("%", " percent").replace("&", " and ").replace("+", " plus ")
    t = re.sub(r"(\d)\.(\d)", r"\1point\2", t)          # keep 1.5 together through the punctuation pass
    t = re.sub(r"(\d):(\d\d)\b", r"\1colon\2", t)
    for rx, rep in _CONTRACTIONS:
        t = rx.sub(rep, t)
    t = re.sub(r"[-‐–—/_]", " ", t)
    t = re.sub(r"[^a-z0-9' ]+", " ", t)
    t = re.sub(r"'s\b", "s", t).replace("'", "")
    t = re.sub(r"(\d)point(\d)", r"\1.\2", t)
    t = re.sub(r"(\d)colon(\d\d)", r"\1:\2", t)
    t = re.sub(r"(\d)([a-z])", r"\1 \2", t)              # 5min -> 5 min, 30percent -> 30 percent
    return " ".join(t.split())


def fix_slips(t: str) -> str:
    for rx, rep in _SLIPS:
        t = rx.sub(rep, t)
    return " ".join(_fuzzy_word(w) for w in t.split())


def strip_filler(t: str) -> str:
    prev = None
    while prev != t:
        prev = t
        t = _LEAD.sub("", t).strip()
        t = _TAIL.sub("", t).strip()
    return t


def normalize(text: str) -> str:
    """The form the grammar and the classifier see."""
    return strip_filler(fix_slips(basic(text)))
