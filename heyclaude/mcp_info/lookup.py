"""Definitions and translations, English / Russian / Uzbek (ROADMAP M5b).

Served by the info MCP server as mcp__info__define and mcp__info__translate; the
widgets define and translate draw the cards from their results. All keyless:

- define: English from dictionaryapi.dev (Wiktionary data, with IPA); Russian, Uzbek
  (and English words dictionaryapi.dev doesn't know) from English Wiktionary's REST
  definitions (glosses in English) plus the pronunciation from the page itself.
- translate: MyMemory (api.mymemory.translated.net), which does en / ru <-> uz. Its
  own "autodetect" is weak, so en / ru / uz are told apart here by script and
  spelling first. Anonymous use is ~5,000 characters a day; `info.translate.email`
  (the `info:` config section) raises that to ~50,000.
Results are cached in memory for the life of the server.
"""
from __future__ import annotations

import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request

PROMPT = ("For what a word means use define (en, ru, uz), and for translating between English, Russian and "
          "Uzbek (or other languages) use translate.")

UA = "hey-claude/1.0 (+https://github.com/kamol1dn/hey-claude)"
LANGS = {"en": "English", "ru": "Russian", "uz": "Uzbek", "de": "German", "fr": "French", "es": "Spanish",
         "it": "Italian", "tr": "Turkish", "kk": "Kazakh", "ky": "Kyrgyz", "tg": "Tajik", "uk": "Ukrainian",
         "zh": "Chinese", "ja": "Japanese", "ko": "Korean", "ar": "Arabic", "fa": "Persian", "hi": "Hindi",
         "pt": "Portuguese", "pl": "Polish", "nl": "Dutch", "az": "Azerbaijani", "tk": "Turkmen"}
_LANG_ALIASES = {"english": "en", "eng": "en", "английский": "en", "ingliz": "en", "inglizcha": "en",
                 "russian": "ru", "rus": "ru", "русский": "ru", "rus tili": "ru", "ruscha": "ru",
                 "uzbek": "uz", "uzb": "uz", "o'zbek": "uz", "o‘zbek": "uz", "ozbek": "uz", "o'zbekcha": "uz",
                 "узбекский": "uz", "ўзбек": "uz"}
for _c, _n in LANGS.items():
    _LANG_ALIASES.setdefault(_n.lower(), _c)
_cache: dict = {}


def _fetch(url: str, timeout: float = 6.0):
    """GET JSON; None on a 404 (no such word)."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise OSError(f"{urllib.parse.urlsplit(url).netloc}: HTTP {e.code}") from None


def lang_code(lang: str, default: str = "en") -> str:
    s = str(lang or "").strip().lower()
    if not s:
        return default
    s = s.split("-")[0].split("_")[0] if re.fullmatch(r"[a-z]{2}[-_][a-z]{2}", s) else s
    if s in LANGS or re.fullmatch(r"[a-z]{2}", s):
        return s
    if s in _LANG_ALIASES:
        return _LANG_ALIASES[s]
    raise ValueError(f"unknown language {lang!r} (use en, ru, uz or another 2-letter code)")


def _text(h: str) -> str:
    """Wiktionary's HTML gloss -> plain text."""
    s = re.sub(r"<(sup|style)[^>]*>.*?</\1>", "", str(h or ""), flags=re.S)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


# ----------------------------------------------------------------- define
def _dictionaryapi(word: str) -> dict | None:
    v = None
    for w in dict.fromkeys([word, word.lower()]):            # "Glimmer" at the start of a sentence
        v = _fetch("https://api.dictionaryapi.dev/api/v2/entries/en/" + urllib.parse.quote(w))
        if isinstance(v, list) and v:
            break
    if not isinstance(v, list) or not v:
        return None
    e = v[0]
    phon = e.get("phonetic") or next((p.get("text") for p in e.get("phonetics", []) if p.get("text")), "")
    meanings = []
    for m in [m for x in v for m in x.get("meanings", [])]:
        senses = []
        for d in m.get("definitions", [])[:3]:
            s = {"definition": d.get("definition", "").strip()}
            if d.get("example"):
                s["example"] = d["example"].strip()
            if s["definition"]:
                senses.append(s)
        if senses and not any(x["pos"] == m.get("partOfSpeech") for x in meanings):
            meanings.append({"pos": m.get("partOfSpeech") or "", "senses": senses})
    if not meanings:
        return None
    return {"word": e.get("word") or word, "lang": "en", "phonetic": phon, "meanings": meanings[:3],
            "source": "Wiktionary via dictionaryapi.dev",
            "url": (e.get("sourceUrls") or [f"https://en.wiktionary.org/wiki/{urllib.parse.quote(word)}"])[0]}


def _ipa(word: str, lang: str) -> str:
    """The first IPA on the word's Wiktionary page under its language (best effort)."""
    name = LANGS.get(lang, "")
    try:
        sec = _fetch("https://en.wiktionary.org/w/api.php?action=parse&format=json&formatversion=2&prop=sections&page="
                     + urllib.parse.quote(word), timeout=4)
        sections = (sec or {}).get("parse", {}).get("sections", [])
        top = next((s for s in sections if s.get("toclevel") == 1 and s.get("line") == name), None)
        if top is None:
            return ""
        nums = [s for s in sections if s.get("number", "").startswith(top["number"] + ".") and
                s.get("line", "").startswith("Pronunciation")]
        idx = (nums[0] if nums else top)["index"]
        page = _fetch("https://en.wiktionary.org/w/api.php?action=parse&format=json&formatversion=2&prop=text"
                      f"&section={idx}&page=" + urllib.parse.quote(word), timeout=4)
        h = (page or {}).get("parse", {}).get("text", "")
        m = re.search(r'<span class="IPA[^"]*"[^>]*>([^<]+)</span>', h)
        return html.unescape(m.group(1)).strip() if m else ""
    except (OSError, ValueError, KeyError, StopIteration):
        return ""


def _wiktionary(word: str, lang: str) -> dict | None:
    v = None
    for w in dict.fromkeys([word, word.lower()]):
        v = _fetch("https://en.wiktionary.org/api/rest_v1/page/definition/" + urllib.parse.quote(w.replace(" ", "_")))
        if isinstance(v, dict) and v.get(lang):
            word = w
            break
        v = None
    if not v:
        return None
    meanings = []
    for e in v[lang]:
        senses = []
        for d in e.get("definitions", []):
            gloss = _text(d.get("definition", ""))
            if not gloss:
                continue
            s = {"definition": gloss}
            ex = (d.get("parsedExamples") or [{}])[0]
            if ex.get("example"):
                s["example"] = _text(ex["example"])
                if ex.get("translation"):
                    s["example_translation"] = _text(ex["translation"])
            elif d.get("examples"):
                s["example"] = _text(d["examples"][0])
            senses.append(s)
            if len(senses) == 3:
                break
        pos = str(e.get("partOfSpeech") or "").lower()
        if senses and not any(x["pos"] == pos for x in meanings):
            meanings.append({"pos": pos, "senses": senses})
    if not meanings:
        return None
    return {"word": word, "lang": lang, "phonetic": _ipa(word, lang), "meanings": meanings[:3],
            "source": "Wiktionary", "url": f"https://en.wiktionary.org/wiki/{urllib.parse.quote(word)}#{LANGS.get(lang, lang)}"}


def define(word: str, lang: str = "en") -> dict:
    """Look up a word's meaning: pronunciation (IPA), part of speech, senses with examples.

    lang: "en" (English), "ru" (Russian) or "uz" (Uzbek); other Wiktionary language
    codes work too. Russian and Uzbek words get English glosses (from English
    Wiktionary). Returns {word, lang, language, phonetic, meanings: [{pos, senses:
    [{definition, example}]}], source, url}.
    """
    w = re.sub(r"\s+", " ", str(word or "")).strip().strip(".,!?\"'«»“”")
    if not w:
        raise ValueError("no word given")
    if len(w) > 60:
        raise ValueError("that's too long to look up as a word")
    lc = lang_code(lang)
    if lc == "en" and re.search(r"[А-Яа-яЁё]", w):
        lc = "uz" if re.search(r"[ЎўҚқҒғҲҳ]", w) else "ru"
    key = ("define", w.lower(), lc)
    if key in _cache:
        return _cache[key]
    out = _dictionaryapi(w) if lc == "en" else None
    if out is None:
        out = _wiktionary(w, lc)
    if out is None:
        raise LookupError(f"no definition found for {w!r} in {LANGS.get(lc, lc)}")
    out["language"] = LANGS.get(lc, lc)
    _cache[key] = out
    return out


# ----------------------------------------------------------------- translate
_UZ_MARKS = re.compile(r"[oOgG][‘'ʻ`’]|\b\w+(?:lar|larni|larga|ning|ni|ga|dagi|da|dan|man|san|miz|siz|dir|ingiz|imiz)\b|"
                       r"\b(?:va|bilan|uchun|emas|yo'q|bor|qanday|qalay|nima|men|sen|biz|ular|bu|shu|ham|juda|yaxshi|"
                       r"rahmat|salom|kitob|kerak|qachon|qayer|necha)\b", re.I)
_EN_MARKS = re.compile(r"\b(?:the|a|an|is|are|was|were|be|to|of|and|in|on|for|with|you|i|it|this|that|what|how|"
                       r"do|does|have|has|will|can|my|your|me|please|thank|thanks|hello|good|morning)\b", re.I)


def detect(text: str) -> str:
    """en / ru / uz by script and spelling ("" when it's none of them)."""
    t = str(text or "")
    if re.search(r"[ЎўҚқҒғҲҳ]", t):
        return "uz"
    cyr, lat = len(re.findall(r"[А-Яа-яЁё]", t)), len(re.findall(r"[A-Za-z]", t))
    if cyr > lat:
        return "ru"
    if not lat:
        return ""
    uz = len(_UZ_MARKS.findall(t)) + 2 * len(re.findall(r"[qxQX]", t)) / max(1, len(t.split()))
    en = len(_EN_MARKS.findall(t))
    if re.search(r"[‘ʻ]|\b[oOgG]'", t):
        uz += 2
    return "uz" if uz > en else "en"


def _mymemory(q: str, src: str, dst: str) -> dict:
    from heyclaude import mcp_info
    params = {"q": q, "langpair": f"{src}|{dst}"}
    email = str((mcp_info.config().get("translate") or {}).get("email") or "").strip()
    if email:
        params["de"] = email
    v = _fetch("https://api.mymemory.translated.net/get?" + urllib.parse.urlencode(params), timeout=8)
    if not isinstance(v, dict):
        raise OSError("MyMemory: no answer")
    status = int(v.get("responseStatus") or 0) if str(v.get("responseStatus", "")).isdigit() else 0
    out = html.unescape(str((v.get("responseData") or {}).get("translatedText") or "")).strip()
    if v.get("quotaFinished") or status == 429 or out.upper().startswith("MYMEMORY WARNING"):
        raise OSError("MyMemory's free daily quota is used up (set info.translate.email for more)")
    if status != 200 or not out:
        raise ValueError(f"MyMemory: {v.get('responseDetails') or 'no translation'}"[:160])
    alts = []
    same = re.sub(r"[\s.,!?…]+", " ", q).strip().lower()
    for m in v.get("matches") or []:                         # other renderings of the same text (memory hits)
        t = html.unescape(str(m.get("translation") or "")).strip()
        seg = re.sub(r"[\s.,!?…]+", " ", html.unescape(str(m.get("segment") or ""))).strip().lower()
        if t and seg == same and t.lower() != out.lower() and t.lower() not in (a.lower() for a in alts) and len(t) <= 80:
            alts.append(t)
    return {"text": out, "match": float((v.get("responseData") or {}).get("match") or 0), "alternatives": alts[:2]}


def _chunks(text: str, n: int = 450) -> list[str]:
    """MyMemory takes up to 500 bytes a request: split at sentence ends."""
    if len(text.encode("utf-8")) <= 500:
        return [text]
    parts, cur = [], ""
    for s in re.split(r"(?<=[.!?…])\s+", text):
        if cur and len((cur + " " + s).encode("utf-8")) > n * 1.1:
            parts.append(cur)
            cur = s
        else:
            cur = (cur + " " + s).strip()
    if cur:
        parts.append(cur)
    out = []
    for p in parts:                                         # a sentence that's still too long: by bytes
        while len(p.encode("utf-8")) > 500:
            cut = p[:200].rfind(" ")
            cut = cut if cut > 50 else 200
            out.append(p[:cut])
            p = p[cut:].strip()
        out.append(p)
    return out


def translate(text: str, to: str, from_: str = "auto") -> dict:
    """Translate text between languages, e.g. English, Russian and Uzbek (to="uz").

    to / from_: language codes ("en", "ru", "uz", or others such as "de", "tr") or
    names ("Uzbek"). from_="auto" tells en / ru / uz apart by script and spelling.
    Up to ~1,500 characters. Returns {text, translation, from, to, from_name,
    to_name, detected, alternatives, source}.
    """
    t = re.sub(r"[ \t]+", " ", str(text or "")).strip()
    if not t:
        raise ValueError("no text given")
    if len(t) > 1500:
        raise ValueError("that's too long to translate here (1,500 characters at most)")
    dst = lang_code(to)
    detected = str(from_ or "auto").strip().lower() in ("auto", "", "autodetect", "detect")
    src = (detect(t) or "en") if detected else lang_code(from_)
    if src == dst:
        cyr = bool(re.search(r"[А-Яа-яЁё]", t))
        if not detected or (dst == "ru" and cyr):
            raise ValueError(f"the text is already in {LANGS.get(dst, dst)}")
        # a guess that matches the target is a bad guess: the other Latin-script one of en / uz
        src = "uz" if dst == "en" and not cyr else "en" if not cyr else "ru"
    key = ("translate", t, src, dst)
    if key in _cache:
        return _cache[key]
    pieces = [_mymemory(c, src, dst) for c in _chunks(t)[:4]]
    out = {"text": t, "translation": " ".join(p["text"] for p in pieces), "from": src, "to": dst,
           "from_name": LANGS.get(src, src), "to_name": LANGS.get(dst, dst), "detected": detected,
           "match": round(min(p["match"] for p in pieces), 2), "source": "MyMemory"}
    if len(pieces) == 1 and pieces[0]["alternatives"]:
        out["alternatives"] = pieces[0]["alternatives"]
    _cache[key] = out
    return out


TOOLS = [define, translate]
