"""Slot parsers: spoken numbers and levels, relative amounts, durations, on/off.

Each parser takes the (normalized) words of one slot and returns a value, or
None when the words aren't that kind of slot at all. None makes the rule that
asked fail, so an utterance whose slot doesn't parse never acts.
"""
from __future__ import annotations

import re

UNITS = {"zero": 0, "oh": 0, "o": 0, "nil": 0, "one": 1, "won": 1, "two": 2, "to": None, "too": None, "three": 3,
         "four": 4, "for": None, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
         "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
         "eighteen": 18, "nineteen": 19}
TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fourty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
        "eighty": 80, "ninety": 90}
# Levels said as words, 0-100.
LEVEL_WORDS = {
    "half": 50, "a half": 50, "halfway": 50, "half way": 50, "middle": 50, "the middle": 50, "mid": 50,
    "a third": 33, "one third": 33, "a quarter": 25, "one quarter": 25, "quarter": 25, "two thirds": 67,
    "three quarters": 75, "three quarter": 75,
    "max": 100, "maximum": 100, "the max": 100, "the maximum": 100, "full": 100, "full blast": 100, "top": 100,
    "the top": 100, "all the way up": 100, "a hundred": 100, "one hundred": 100, "hundred": 100, "highest": 100,
    "the highest": 100, "full power": 100,
    "min": 0, "minimum": 0, "the minimum": 0, "the min": 0, "lowest": 0, "the lowest": 0, "all the way down": 0,
    "nothing": 0, "none": 0, "zero": 0, "off": None,
}
SMALL, LARGE = 0.5, 2.5            # "a bit" = half a step, "a lot" = two and a half


def parse_int(s: str) -> int | None:
    """'30', 'thirty', 'thirty five', 'a hundred', 'three zero' (Whisper's digit by digit), 'one hundred and ten'."""
    s = " ".join((s or "").split())
    if not s:
        return None
    if re.fullmatch(r"\d+", s):
        return int(s)
    if re.fullmatch(r"\d+(?: \d+)+", s):                  # "3 0"
        return int(s.replace(" ", ""))
    words = s.replace(" and ", " ").split()
    if words and words[0] == "a" and len(words) > 1 and words[1] in ("hundred", "thousand"):
        words[0] = "one"
    # digit by digit ("three zero", "five oh") when every word is a single digit
    if len(words) >= 2 and all(w in UNITS and UNITS[w] is not None and UNITS[w] < 10 for w in words) \
            and words[-1] in ("zero", "oh", "o"):
        return int("".join(str(UNITS[w]) for w in words))
    total, cur, seen = 0, 0, False
    for w in words:
        if w in UNITS and UNITS[w] is not None:
            v, low = UNITS[w], cur % 100
            if not seen or (cur >= 100 and low == 0):
                cur += v                               # "five", "one hundred five"
            elif low >= 20 and low % 10 == 0 and v < 10:
                cur += v                               # "thirty five"
            else:
                return None                            # "five three"
            seen = True
        elif w in TENS:
            if cur % 100:
                return None
            cur += TENS[w]
            seen = True
        elif w == "hundred":
            cur = (cur or 1) * 100
            seen = True
        elif w == "thousand":
            total += (cur or 1) * 1000
            cur = 0
            seen = True
        elif re.fullmatch(r"\d+", w) and not cur:
            cur = int(w)
            seen = True
        else:
            return None
    return total + cur if seen else None


def parse_float(s: str) -> float | None:
    s = " ".join((s or "").split())
    if re.fullmatch(r"\d+\.\d+", s):
        return float(s)
    if s.endswith(" and a half") or s.endswith(" and half"):
        base = parse_float(s.rsplit(" and ", 1)[0])
        return None if base is None else base + 0.5
    if s in ("a", "an", "one", "a single"):
        return 1.0
    if s in ("half a", "half an", "a half", "half", "a half a", "a half an"):
        return 0.5
    if s in ("a quarter of a", "a quarter of an", "quarter of a", "quarter of an", "a quarter"):
        return 0.25
    if s in ("three quarters of a", "three quarters of an", "three quarters"):
        return 0.75
    if s in ("a couple", "a couple of", "couple of", "a couple a"):
        return 2.0
    if s in ("a few", "few"):
        return 3.0
    n = parse_int(s)
    return float(n) if n is not None else None


def parse_level(s: str) -> int | None:
    """A level 0-100: '30', 'thirty percent', 'half', 'a third', 'max', 'volume tree zero' -> 30."""
    s = " ".join((s or "").split())
    s = re.sub(r"\s*(?:percent|per cent|pc|%)$", "", s).strip()
    s = re.sub(r"^(?:about|around|roughly|like|maybe|approximately|exactly|just|only)\s+", "", s)
    if s in LEVEL_WORDS:
        return LEVEL_WORDS[s]
    n = parse_int(s)
    if n is None or not 0 <= n <= 100:
        return None
    return n


def parse_amount(s: str, step: int = 10) -> int | None:
    """A relative amount (always positive): '' -> step, 'a bit' -> half a step,
    'a lot' -> 2.5 steps, 'by 10' / '10 percent' -> 10, 'two notches' -> 4 (Windows' key step is 2)."""
    s = " ".join((s or "").split())
    s = re.sub(r"^(?:just|only|even|maybe|still)\b\s*", "", s)
    s = re.sub(r"(?:^|\s+)(?:more|less|further|again|higher|lower|louder|quieter|up|down)$", "", s).strip()
    if not s or s in ("some", "a step", "one step", "a notch", "one notch", "a little more", "more"):
        return step
    if s in ("a bit", "a little", "a little bit", "a tiny bit", "a tad", "a touch", "slightly", "a smidge",
             "a hair", "little", "bit", "a little tiny bit", "just a bit", "a wee bit", "a teeny bit", "tiny bit",
             "little bit", "a lil", "a lil bit", "lil bit", "somewhat"):
        return max(1, round(step * SMALL))
    if s in ("a lot", "lots", "way", "much", "a lot more", "loads", "a ton", "tons", "significantly",
             "heaps", "a bunch", "way more", "quite a bit", "a good bit", "a fair bit", "plenty", "a great deal",
             "massively", "really", "a whole lot"):
        return round(step * LARGE)
    m = re.fullmatch(r"(?:by )?(.+?)(?: percent| points?| percentage points?| notch(?:es)?| steps?| clicks?| levels?)?",
                     s)
    if not m:
        return None
    body = m.group(1)
    n = parse_int(body)
    if n is None:
        f = parse_float(body)
        n = int(f) if f is not None and f == int(f) else None
    if n is None or not 1 <= n <= 100:
        return None
    if re.search(r"notch|step|click", s):
        n = min(100, n * 2)
    return n


_DUR_UNITS = {"second": 1, "seconds": 1, "sec": 1, "secs": 1, "minute": 60, "minutes": 60, "min": 60,
              "mins": 60, "hour": 3600, "hours": 3600, "hr": 3600, "hrs": 3600}
MAX_TIMER_S = 86400


def parse_duration(s: str) -> int | None:
    """Seconds: 'five minutes', '5 min', '1 and a half hours', 'an hour and a half',
    'half an hour', 'two hours and 15 minutes', '90 seconds', 'a five minute' (as in a five minute timer)."""
    toks = " ".join((s or "").split()).split()
    if toks and toks[0] in ("a", "an") and len(toks) > 2 and toks[1] not in ("half", "quarter", "couple", "few") \
            and toks[1] not in _DUR_UNITS:
        toks = toks[1:]                                     # "a five minute (timer)"
    total, buf, units, i = 0.0, [], 0, 0
    while i < len(toks):
        t = toks[i]
        if t in _DUR_UNITS:
            while buf and buf[0] in ("and", "plus"):
                buf = buf[1:]
            q = parse_float(" ".join(buf))
            if q is None or q <= 0:
                return None
            u = _DUR_UNITS[t]
            total += q * u
            buf = []
            units += 1
            if toks[i + 1:i + 4] == ["and", "a", "half"]:
                total += 0.5 * u
                i += 3
            i += 1
            continue
        buf.append(t)
        i += 1
    if buf or not units:
        return None
    secs = int(round(total))
    return secs if 0 < secs <= MAX_TIMER_S else None


def say_duration(secs: int) -> str:
    """90 -> '1 minute 30 seconds', 3600 -> '1 hour', 5400 -> '1 hour 30 minutes'."""
    h, rem = divmod(int(secs), 3600)
    m, s = divmod(rem, 60)
    parts = []
    for n, w in ((h, "hour"), (m, "minute"), (s, "second")):
        if n:
            parts.append(f"{n} {w}{'' if n == 1 else 's'}")
    return " ".join(parts) or "0 seconds"


ON_WORDS = {"on": True, "back on": True, "enable": True, "enabled": True, "activate": True, "start": True,
            "off": False, "disable": False, "disabled": False, "deactivate": False, "kill": False, "stop": False}


def parse_onoff(s: str) -> bool | None:
    return ON_WORDS.get(" ".join((s or "").split()))
