"""Uzbek phrasings for the core instant commands.

Whisper writes Uzbek in Latin ("ovozni o'ttizga qo'y") or in Cyrillic ("овозни
ўттизга қўй"), often with slips: q/k mixed up ("ko'shik"), apostrophes as ʻ ' ’
` or missing ("ottiz"), Turkish letters when it thinks it heard Turkish. So
the text is folded first: Cyrillic transliterated to Latin, apostrophes
dropped (o'ttiz -> ottiz, g'  -> g), common slips put right, and the case
suffixes the slots need split off ("o'ttizga" -> "ottiz ga", "daqiqalik" ->
"daqiqa lik", "rejimni" -> "rejim ni").

Same rules as grammar.py: the WHOLE utterance must fit ("ovoz balandligi
nima" never acts). Acks stay English (Kokoro has no Uzbek).
"""
from __future__ import annotations

import re

from . import grammar as G

RULES: list = []


def rule(intent, *patterns):
    return G.rule(intent, *patterns, into=RULES)


# -- folding ----------------------------------------------------------------------------------
_CYR = {"а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "ё": "yo", "ж": "j", "з": "z", "и": "i", "й": "y", "к": "k",
        "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f",
        "х": "x", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sh", "ъ": "", "ы": "i", "ь": "", "э": "e", "ю": "yu",
        "я": "ya", "ў": "o", "қ": "q", "ғ": "g", "ҳ": "h"}
_VOWELS = set("аеёиоуэюяў")
HAS_CYR = re.compile(r"[а-яёўқғҳ]")


def translit(t: str) -> str:
    """Uzbek Cyrillic -> Latin (apostrophes already dropped: ў -> o, ғ -> g)."""
    out, prev = [], " "
    for ch in t:
        if ch == "е":
            out.append("ye" if prev == " " or prev in _VOWELS else "e")
        else:
            out.append(_CYR.get(ch, ch))
        prev = ch
    return "".join(out)


_SLIPS = [
    (re.compile(r"\b(?:k|q)(?:o|u)shi(?:q|k)"), "qoshiq"),        # qo'shiq (song) and its slips
    (re.compile(r"\bkoy(ing|gin|chi|ib)?\b"), r"qoy\1"),
    (re.compile(r"\bkil(ing|gin|chi|ib)?\b"), r"qil\1"),
    (re.compile(r"\bda(?:q|k)i(?:q|k)a"), "daqiqa"),
    (re.compile(r"\b(?:minut|minud)"), "daqiqa"),
    (re.compile(r"\b(?:sekund|sekunt|soniya|sonya)"), "soniya"),
    (re.compile(r"\b(?:taymer|taimer|tayimer|timer)"), "taymer"),
    (re.compile(r"\b(?:muzika|musika|muzyka)"), "musiqa"),
    (re.compile(r"\b(?:foyiz|fois|protsent|prosent|protsen|percent)"), "foiz"),
    (re.compile(r"\b(?:avoz|ovos)(?=\w*\b)"), "ovoz"),
    (re.compile(r"\b(?:yarqin|yorkin)li"), "yorqinli"),
    (re.compile(r"\bkompyuter|\bkompiyuter|\bkomputer"), "kompyuter"),
    (re.compile(r"\b(?:qorongu|qorangi|qorongi|korongi)"), "qorongi"),
]

# -- numbers ------------------------------------------------------------------------------------
_UNITS = {"nol": 0, "bir": 1, "ikki": 2, "iki": 2, "uch": 3, "tort": 4, "turt": 4, "besh": 5, "bes": 5,
          "olti": 6, "yetti": 7, "etti": 7, "sakkiz": 8, "sakiz": 8, "toqqiz": 9, "tokkiz": 9, "toqiz": 9}
_TENS = {"on": 10, "un": 10, "yigirma": 20, "yigrima": 20, "ottiz": 30, "otiz": 30, "uttiz": 30, "ottis": 30,
         "qirq": 40, "kirk": 40, "qirk": 40, "kirq": 40, "ellik": 50, "elik": 50, "ellig": 50, "oltmish": 60,
         "yetmish": 70, "etmish": 70, "sakson": 80, "saksan": 80, "toqson": 90, "toqsan": 90, "tokson": 90}
_NUMWORDS = set(_UNITS) | set(_TENS) | {"yuz"}
# Words the suffixes are split off (so the slots and rules see "ottiz ga", "daqiqa lik").
_LEX = _NUMWORDS | {"foiz", "daqiqa", "soniya", "soat", "rejim", "mavzu", "tema", "maksimum", "maksimal", "minimum",
                    "minimal", "yarim", "taymer", "trek", "qoshiq", "musiqa", "video", "pauza", "oxiri"}
_SUFFIXES = ("gacha", "lik", "ning", "ga", "ka", "qa", "ta", "ni", "da", "dan", "si")


def _split(t: str) -> str:
    out = []
    for w in t.split():
        if w in _LEX or w.isdigit():
            out.append(w)
            continue
        for suf in _SUFFIXES:
            base = w[:-len(suf)]
            if w.endswith(suf) and base in _LEX:
                out += [base, suf]
                break
            if w.endswith(suf) and base.endswith(("k", "q")) and base[:-1] in _LEX and suf in ("ka", "qa"):
                out += [base[:-1], suf]                  # qirqqa
                break
        else:
            out.append(w)
    return " ".join(out)


def parse_int(s: str) -> int | None:
    """'30', 'ottiz', 'ottiz besh', 'yuz', 'on besh', 'bir yuz'."""
    words = " ".join((s or "").split()).split()
    if not words:
        return None
    if len(words) == 1 and words[0].isdigit():
        return int(words[0])
    total, last = 0, None
    for w in words:
        if w == "yuz" and last in (None, "u") and total <= 9:
            total, last = (total or 1) * 100, "h"
        elif w in _TENS and last in (None, "h"):
            total, last = total + _TENS[w], "t"
        elif w in _UNITS and last in (None, "h", "t"):
            total, last = total + _UNITS[w], "u"
        elif w.isdigit() and last is None:
            total, last = int(w), "u"
        else:
            return None
    return total


_LEVEL_WORDS = {"maksimum": 100, "maksimal": 100, "toliq": 100, "eng baland": 100, "oxiri": 100, "oxirigacha": 100,
                "eng yuqori": 100, "minimum": 0, "minimal": 0, "eng past": 0, "nol": 0, "yarim": 50, "yarmi": 50,
                "yarmiga": 50, "orta": 50, "ortacha": 50}


def parse_level(s: str) -> int | None:
    s = " ".join((s or "").split())
    s = re.sub(r"\s+(?:foiz|percent)$", "", s)
    if s in _LEVEL_WORDS:
        return _LEVEL_WORDS[s]
    n = parse_int(s)
    return n if n is not None and 0 <= n <= 100 else None


_SMALL = {"biroz", "sal", "ozgina", "ozroq", "bir oz", "bir ozgina", "salgina", "picha", "bir picha", "sekin",
          "yana biroz", "yana ozgina", "yana sal", "kichik", "salpal"}
_LARGE = {"ancha", "juda", "kop", "ancha kop", "juda kop", "yaxshilab", "rosa", "qattiq"}


def parse_amount(s: str, step: int = 10) -> int | None:
    """'' -> step, 'biroz' -> half a step, 'ancha' -> 2.5 steps, '10 ga' / '10 foiz ga' -> 10."""
    s = " ".join((s or "").split())
    if not s or s == "yana":
        return step
    if s in _SMALL:
        return max(1, round(step * 0.5))
    if s in _LARGE:
        return round(step * 2.5)
    s = re.sub(r"\s+ga$", "", s)
    s = re.sub(r"\s+(?:foiz|percent|pog?ona)$", "", s)
    n = parse_int(s)
    return n if n is not None and 1 <= n <= 100 else None


_DUR_UNITS = {"soniya": 1, "sek": 1, "daqiqa": 60, "min": 60, "soat": 3600}


def parse_duration(s: str) -> int | None:
    """Seconds: 'besh daqiqa', '10 daqiqa', 'yarim soat', 'bir yarim soat', '1 soat 30 daqiqa', '30 soniya'."""
    toks = " ".join((s or "").split()).split()
    total, buf, units = 0.0, [], 0
    for t in toks:
        if t in _DUR_UNITS:
            while buf and buf[0] in ("va", "u"):
                buf = buf[1:]
            half = bool(buf) and buf[-1] == "yarim"
            if half:
                buf = buf[:-1]
            if not buf:
                q = 0.5 if half else None
            else:
                n = parse_int(" ".join(buf))
                q = None if n is None else n + (0.5 if half else 0)
            if q is None or q <= 0:
                return None
            total += q * _DUR_UNITS[t]
            buf, units = [], units + 1
            continue
        buf.append(t)
    if buf or not units:
        return None
    secs = int(round(total))
    return secs if 0 < secs <= 86400 else None


# -- filler ------------------------------------------------------------------------------------
_LEAD = re.compile(r"^(?:(?:hey|xey|klod|klaud|claude|iltimos|qani|marhamat|ok|okey|xop|endi|ha|aka|uka|"
                   r"bro|ey|hoy)\s+)+")
_TAIL = re.compile(r"(?:\s+(?:iltimos|rahmat|qani|tezroq|endi|ok|okey|klod|claude|aka|bro|tez))+$")


def prep(t: str) -> str:
    """Normalized text (normalize.basic) -> what the Uzbek rules see."""
    if HAS_CYR.search(t):
        t = translit(t)
    for rx, rep in _SLIPS:
        t = rx.sub(rep, t)
    t = _split(t)
    prev = None
    while prev != t:
        prev = t
        t = _LEAD.sub("", t).strip()
        t = _TAIL.sub("", t).strip()
    return t


# Words that mark a sentence as Uzbek (the English classifier then stays out of it).
MARKERS = re.compile(r"\b(?:ovoz\w*|tovush\w*|qoy\w*|qil\w*|och|ochir\w*|yoq\w*|keyingi|oldingi|qoshiq\w*|musiqa\w*|"
                     r"taymer\w*|daqiqa\w*|soniya\w*|soat|necha\w*|yorqin\w*|yorug\w*|ekran\w*|rejim\w*|iltimos|"
                     r"kamaytir\w*|pasaytir\w*|pastroq|oshir\w*|kotar\w*|baland\w*|toxtat\w*|davom|qulfla\w*|"
                     r"kompyuter\w*|ishga|tushir\w*|nima|qanday|qancha|uchun|haqida|bilan|emas|qorongi\w*|kim|"
                     r"qachon|qayer\w*|qaysi|qanaqa|menga|meni|senga|shu|kerak|edi|"
                     r"\w{2,}(?:dimi|yapti|yapman|moqda|ganmi|sizmi|di))\b")


def looks_uzbek(t: str) -> bool:
    """Uzbek command words in the text as said (not after the slip fixes: "timer" stays English)."""
    return bool(MARKERS.search(translit(t) if HAS_CYR.search(t) else t))


# -- building blocks ----------------------------------------------------------------------------
_END = r"(?:ing|ng|gin|chi|ingchi|ginchi|sangiz|ib ber|ib bering|ib bergin|ib qoy|ib qoying|ib yubor|ib yuboring)?"
NUM = r"(?P<n>[a-z0-9 ]+?)"
AMT = r"(?P<a>[a-z0-9 ]+?)"
VOL = r"(?:ovoz|tovush|zvuk)(?:i|ni|ini|ning)?(?:\s+(?:balandligi|darajasi|kuchi)(?:ni)?)?"
SETV = r"(?:qoy|qil|ornat)" + _END
UPV = r"(?P<up>(?:oshir|kotar|kuchaytir|kopaytir|balandlat|baland qil|balandroq qil)" + _END + r")"
DOWNV = r"(?P<down>(?:kamaytir|pasaytir|pasayt|tushir|ozaytir|past qil|pastroq qil|sekinlat|sekinroq qil)" + _END + r")"
BR = r"(?:ekran(?:ning|ni)?\s+)?(?:yorqin|yorug|ravshan)li(?:k|gi|gini|kni|kka|ki)"
SCREEN = r"(?:ekran|ekranni|monitor|monitorni|displey|displeyni)"
PCT = r"(?:\s+foiz)?"


def _dirstep(m, ctx):
    up = bool(G._g(m, "up")) or G._g(m, "d") in ("balandroq", "kuchliroq", "yorqinroq", "yorugroq", "ravshanroq")
    down = bool(G._g(m, "down")) or G._g(m, "d") in ("pastroq", "xiraroq", "sekinroq", "qorongiroq")
    if G._g(m, "too"):
        up, down = G._g(m, "too") == "past", G._g(m, "too") == "baland"
    if up == down:
        return None
    amt = parse_amount(G._g(m, "a"), ctx.step)
    if amt is None:
        return None
    return {"delta": amt if up else -amt}


def _level(m, ctx):
    lv = parse_level(G._g(m, "n"))
    return None if lv is None else {"level": lv}


# == volume ===================================================================================
@rule("volume.mute",
      VOL + r"\s+(?:ochir|yop)" + _END,
      r"ovozsiz\s+(?:rejim(?:\s+ga)?\s+)?(?:qil|qoy|ot)" + _END,
      r"ovozsiz\s+rejim")
def _mute(m, ctx):
    return {}


@rule("volume.unmute",
      VOL + r"\s+(?:qayta\s+)?(?:yoq|qaytar)" + _END,
      r"ovozsiz\s+rejim(?:\s+ni)?\s+ochir" + _END)
def _unmute(m, ctx):
    return {}


@rule("volume.set",
      VOL + r"\s+" + NUM + PCT + r"(?:\s+ga)?(?:\s+" + SETV + r")?",
      VOL + r"\s+" + NUM + PCT + r"\s+gacha\s+(?:" + UPV + "|" + DOWNV + "|" + SETV + r")",
      VOL + r"\s+(?P<n>eng baland|eng past|eng yuqori|maksimum|maksimal|minimum|minimal|toliq|oxiri|yarim)"
      r"(?:\s+(?:ga|gacha))?(?:\s+(?:" + SETV + "|" + UPV + "|" + DOWNV + r"))?")
def _vol_set(m, ctx):
    return _level(m, ctx)


@rule("volume.step",
      VOL + r"\s+(?:" + AMT + r"\s+)?(?:" + UPV + "|" + DOWNV + r")",
      VOL + r"\s+(?:" + AMT + r"\s+)?(?P<d>balandroq|pastroq|sekinroq|kuchliroq)(?:\s+(?:qil|qoy)" + _END + r")?",
      VOL + r"\s+(?:juda|haddan tashqari|ancha)\s+(?P<too>baland|past)",
      r"(?:" + AMT + r"\s+)?(?P<d>balandroq|pastroq)(?:\s+(?:qil|qoy)" + _END + r")?")
def _vol_step(m, ctx):
    if not m.group(0).startswith(("ovoz", "tovush", "zvuk")) and ctx.domain == "brightness":
        return None                                     # "balandroq" right after a brightness change
    return _dirstep(m, ctx)


# == brightness ====================================================================================
@rule("brightness.set",
      BR + r"\s+" + NUM + PCT + r"(?:\s+ga)?(?:\s+" + SETV + r")?",
      BR + r"\s+" + NUM + PCT + r"\s+gacha\s+(?:" + UPV + "|" + DOWNV + "|" + SETV + r")",
      BR + r"\s+(?P<n>eng baland|eng past|maksimum|maksimal|minimum|minimal|toliq|yarim)"
      r"(?:\s+(?:ga|gacha))?(?:\s+(?:" + SETV + "|" + UPV + "|" + DOWNV + r"))?")
def _br_set(m, ctx):
    return _level(m, ctx)


@rule("brightness.step",
      BR + r"\s+(?:" + AMT + r"\s+)?(?:" + UPV + "|" + DOWNV + r")",
      SCREEN + r"\s+(?:" + AMT + r"\s+)?(?P<d>yorqinroq|yorugroq|ravshanroq|xiraroq|qorongiroq)"
      r"(?:\s+(?:qil|qoy)" + _END + r")?",
      r"(?:" + AMT + r"\s+)?(?P<d>yorqinroq|xiraroq)(?:\s+(?:qil|qoy)" + _END + r")?")
def _br_step(m, ctx):
    return _dirstep(m, ctx)


# == dark / light mode ===============================================================================
_DARK = r"(?:qorongi|qora|tungi|dark|tim qora)"
_LIGHT = r"(?:yorug|yorqin|oq|kunduzgi|light)"
_THEME = r"(?:rejim|rejimi|mavzu|mavzusi|tema|temasi)(?:\s+(?:ga|ni))?"
_ONV = r"(?:ot|otkaz|yoq|qoy|qil|ornat|almashtir|aylantir)" + _END


@rule("theme.set",
      r"(?:(?P<dark>" + _DARK + r")|(?P<light>" + _LIGHT + r"))\s+" + _THEME + r"(?:\s+" + _ONV + r")?",
      r"(?:(?P<dark2>" + _DARK + r")|(?P<light2>" + _LIGHT + r"))\s+" + _THEME + r"\s+(?P<off>ochir" + _END + r")")
def _theme(m, ctx):
    mode = "dark" if (G._g(m, "dark") or G._g(m, "dark2")) else "light"
    if G._g(m, "off"):
        mode = {"dark": "light", "light": "dark"}[mode]
    return {"mode": mode}


# == media ===========================================================================================
_SONG = r"(?:qoshiq|trek|musiqa|video|klip)(?:\s+(?:ni|ga))?"
_PLAYV = r"(?:qoy|ot|yoq|ijro et|qayta yoq|eshittir)" + _END


@rule("media.next",
      r"(?:keyingi|navbatdagi|boshqa)\s+" + _SONG + r"(?:\s+" + _PLAYV + r")?",
      r"(?:bu\s+)?" + _SONG + r"\s+(?:otkaz|almashtir|ozgartir)" + _END,
      r"keyingisi(?:\s*ga)?(?:\s+ot" + _END + r")?|keyingi")
def _next(m, ctx):
    if re.fullmatch(r"keyingisi(?:\s*ga)?(?:\s+ot\w*)?|keyingi", m.group(0)) and ctx.domain != "media":
        return None
    return {"action": "next"}


@rule("media.previous",
      r"(?:oldingi|avvalgi|otgan)\s+" + _SONG + r"(?:\s+(?:" + _PLAYV + r"|qaytar" + _END + r"))?",
      r"oldingisi(?:\s*ga)?(?:\s+qayt\w*)?|oldingi|orqaga")
def _prev(m, ctx):
    if re.fullmatch(r"oldingisi(?:\s*ga)?(?:\s+qayt\w*)?|oldingi|orqaga", m.group(0)) and ctx.domain != "media":
        return None
    return {"action": "previous"}


@rule("media.pause",
      r"pauza(?:\s+(?:qil|qoy)" + _END + r")?",
      r"(?:bu\s+)?" + _SONG + r"\s+(?:toxtat" + _END + r"|pauza\s+(?:ga\s+)?(?:qil|qoy)" + _END + r")")
def _pause(m, ctx):
    return {"action": "pause"}


@rule("media.play",
      _SONG + r"\s+(?:davom ettir|davom et|qayta yoq|yoq|qoy|ijro et)" + _END,
      r"(?:pley|play)\s+(?:qil|bos)" + _END,
      r"pauza\s+dan\s+(?:chiqar|ol)" + _END)
def _play(m, ctx):
    return {"action": "play"}


# == timers ==========================================================================================
_DUR = r"(?P<d>[a-z0-9 ]+?)"
_TSET = r"(?:qoy|ornat|yoq|qil|boshla)" + _END


@rule("timer.set",
      _DUR + r"\s+(?:ga\s+|lik\s+)?taymer(?:\s+(?:ni|ga))?(?:\s+" + _TSET + r")?",
      r"taymer(?:\s+(?:ni|ga))?\s+" + _DUR + r"(?:\s+(?:ga|lik))?(?:\s+" + _TSET + r")?")
def _timer_set(m, ctx):
    secs = parse_duration(G._g(m, "d"))
    return None if secs is None else {"seconds": secs}


@rule("timer.cancel",
      r"(?:hamma\s+)?taymer(?:lar)?(?:\s+ni)?\s+(?:ochir|bekor qil|toxtat|olib tashla|ochirib tashla)" + _END)
def _timer_cancel(m, ctx):
    return {}


# == the clock =========================================================================================
@rule("clock.time",
      r"(?:hozir\s+)?soat\s+(?:necha|nechchi|nechi)(?:\s+(?:boldi|bolyapti))?(?:\s+hozir)?",
      r"(?:hozir\s+)?vaqt\s+(?:necha|nechchi)(?:\s+boldi)?",
      r"(?:vaqt|soat)\s*ni\s+(?:ayt|aytib ber)" + _END)
def _time(m, ctx):
    return {}


@rule("clock.date",
      r"bugun\s+(?:necha|nechanchi|nechinchi)\s+(?:sana|kun|chislo)",
      r"bugun\s+(?:qaysi|qanaqa)\s+kun",
      r"bugun\s+sana\s+(?:necha|nechchi|qanaqa)")
def _date(m, ctx):
    return {}


# == lock ===============================================================================================
@rule("screen.lock",
      r"(?:kompyuter|komp|noutbuk|ekran|tizim)(?:\s*ni)?\s+(?:qulfla|blokla|bloklab qoy|qulflab qoy)" + _END)
def _lock(m, ctx):
    return {}


# == apps ===============================================================================================
APPS = {"kalkulyator": "calculator", "kalkulator": "calculator", "bloknot": "notepad", "xrom": "chrome",
        "gugl xrom": "chrome", "sozlamalar": "settings", "parametrlar": "settings", "provodnik": "file explorer",
        "fayllar": "file explorer", "kamera": "camera", "vord": "word", "eksel": "excel", "diskord": "discord",
        "stim": "steam", "telegramm": "telegram", "spotifay": "spotify", "rasmlar": "photos", "suratlar": "photos",
        "paint": "paint", "peint": "paint", "teams": "teams", "vatsap": "whatsapp", "vatsapp": "whatsapp"}


@rule("app.open",
      r"(?P<app>[a-z0-9 .]+?)(?:\s*ni)?\s+(?:och|ishga tushir)" + _END)
def _app(m, ctx):
    want = G._g(m, "app")
    want = re.sub(r"^(?:dastur|ilova)\s+", "", want)
    return G.open_app(APPS.get(want, want), ctx)


# == teach ==============================================================================================
@rule("learn.teach", r"buni\s+(?:eslab qol|organ|yodlab ol|yodla|organib ol)" + _END)
def _teach(m, ctx):
    return {}


# == follow-ups ===========================================================================================
@rule("context.more",
      r"yana(?:\s+" + AMT + r")?",
      r"(?:" + AMT + r"\s+)?(?P<w>koproq|balandroq|yuqoriroq)")
def _more(m, ctx):
    a = G._g(m, "a")
    if a in ("bir", "bir marta", "bir bor"):
        return None                                     # "yana bir marta" is "again"
    amt = parse_amount(a, ctx.step)
    if amt is None:
        return None
    out = {"direction": "up", "amount": amt}
    if G._g(m, "w") in ("", "koproq"):
        out["keep"] = True
    return out


@rule("context.less", r"(?:" + AMT + r"\s+)?(?:kamroq|pastroq|ozroq)")
def _less(m, ctx):
    amt = parse_amount(G._g(m, "a"), ctx.step)
    return None if amt is None else {"direction": "down", "amount": amt}


@rule("context.again", r"yana\s+bir\s+(?:marta|bor)|yana\s+bir|takrorla" + _END)
def _again(m, ctx):
    return {}


@rule("context.reverse", r"(?:yoq\s+)?(?:teskari(?:si)?|aksincha|boshqa tomonga)")
def _reverse(m, ctx):
    return {}


@rule("context.set",
      NUM + PCT + r"(?:\s+ga)?\s+(?:qil|qoy)" + _END,
      NUM + r"\s+foiz(?:\s+ga)?")
def _ctx_set(m, ctx):
    lv = parse_level(G._g(m, "n"))
    return None if lv is None else {"level": lv}


def parse(t: str, ctx: G.Ctx):
    return G.parse(prep(t), ctx, RULES)


def parse_context(t: str, ctx: G.Ctx):
    return G.parse_context(prep(t), ctx, RULES)
