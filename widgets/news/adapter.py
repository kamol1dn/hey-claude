"""Claude Code's WebSearch result -> news / sports headlines, only for a news-like search.

What hey-claude receives (the tool_result block's content, a plain string), as recorded
on 2026-11-10:

    Web search results for query: "Premier League results this weekend"

    Links: [{"title":"...","url":"https://..."}, ...]

    <a summary the search wrote, with its own reading of the pages>

    REMINDER: You MUST include the sources above in your response to the user using markdown hyperlinks.

There may be more than one "Links:" block. The links carry no dates, so a headline's
time comes from its URL or title when it has one ("/2026/11/07/", "June 10, 2025").
Search results can be thin (site names, repeated pages, slug titles), so those are
dropped or ranked last. An ordinary search (a recipe, a how-to) returns None: no card.
"""
from __future__ import annotations

import json
import re
from datetime import date
from urllib.parse import urlparse

MAX = 5
SPORTS = re.compile(r"\b(fixtures?|standings|match(es)?|game|games|league|cup|playoffs?|"
                    r"nba|nfl|mlb|nhl|mls|ufc|f1|formula 1|grand prix|premier league|la liga|serie a|bundesliga|"
                    r"champions league|world cup|olympics?|tennis|cricket|football|soccer|basketball|"
                    r"baseball|hockey|rugby|boxing|golf|marathon|wimbledon|super bowl)\b", re.I)
SCORE = re.compile(r"\b(score|scores|scoreline|final score|who won)\b", re.I)    # sports by itself
NEWS = re.compile(r"\b(news|headlines?|breaking|latest|happening|update|updates|today'?s? stories|"
                  r"press|announce[sd]?|election|elections)\b", re.I)
# a sports word alone ("football boots", "how to play golf") isn't enough: it needs a news/time word too
SPORTS_TIME = re.compile(r"\b(today|tonight|yesterday|this (week|weekend)|last night|latest|live|score|scores|"
                         r"result|results|fixtures|standings|who won|did .* win|vs\.?|v)\b", re.I)
FILLER = re.compile(r"\b(the|latest|top|today'?s?|todays|tonight|yesterday|this (week|weekend)|last night|news|"
                    r"headlines?|breaking|results?|scores?|live|updates?|now|current|recent|what'?s|happening|in|on|"
                    r"about|for|from|of|reuters|ap|bbc)\b", re.I)
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
JUNK_TITLES = {"best counter", "latest news", "home", "news", "stats centre", "index"}


def _query(args: dict, raw: str) -> str:
    q = str((args or {}).get("query") or "").strip()
    if not q:
        m = re.match(r'\s*Web search results for query: "(.*)"', raw or "")
        q = m.group(1) if m else ""
    return q


def kind_of(query: str) -> str | None:
    """"sports", "news" or None (not a news-like search)."""
    q = query or ""
    if SCORE.search(q) or SPORTS.search(q) and (SPORTS_TIME.search(q) or NEWS.search(q)):
        return "sports"
    if NEWS.search(q):
        return "news"
    return None


def topic_of(query: str, kind: str) -> str:
    t = FILLER.sub(" ", query)
    t = re.sub(r"[^\w\s&'.-]", " ", t)
    t = re.sub(r"\s+", " ", t).strip(" .-")
    if not t:
        return "Sports" if kind == "sports" else "Headlines"
    return t[:1].upper() + t[1:] if not t[:1].isupper() else t


def links(raw: str, result=None) -> list[dict]:
    """Every {"title", "url"} in the result's "Links:" blocks, in order."""
    out: list[dict] = []
    if isinstance(result, dict) and isinstance(result.get("results"), list):     # the structured form, if ever
        for r in result["results"]:
            if isinstance(r, dict) and isinstance(r.get("content"), list):
                out += [x for x in r["content"] if isinstance(x, dict)]
    dec = json.JSONDecoder()
    for m in re.finditer(r"(?m)^Links: ", raw or ""):
        try:
            v, _ = dec.raw_decode(raw, m.end())
        except ValueError:
            continue
        if isinstance(v, list):
            out += [x for x in v if isinstance(x, dict)]
    return out


def fix_text(s: str) -> str:
    """Undo UTF-8 read as a Windows code page ("πŸ“± Technology" -> "📱 Technology"), then drop
    leading emoji and symbols."""
    def undo(m):
        run, b = m.group(0), bytearray()
        for ch in run:                       # each char back to its byte in whichever code page has it
            for enc in ("cp1252", "cp1253", "latin-1"):
                try:
                    b += ch.encode(enc)
                    break
                except UnicodeEncodeError:
                    continue
            else:
                return run
        try:
            return b.decode("utf-8")
        except UnicodeDecodeError:
            return run
    s = re.sub(r"[^\x00-\x7f]{2,}", undo, str(s or ""))
    s = re.sub(r"^[^\w\"'“‘(]+", "", s)
    s = re.sub(r"[\U0001F000-\U0001FFFF☀-➿️]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def _domain(url: str) -> str:
    try:
        h = (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""
    return h[4:] if h.startswith("www.") else h


def when(url: str, title: str, today: date | None = None) -> str:
    """"Nov 7" (or "Jun 10, 2025" in another year) from a date in the URL or title, else ""."""
    today = today or date.today()
    y = mo = d = None
    m = re.search(r"/(20\d\d)[/-]?([01]\d)[/-]?([0-3]\d)(?:[/_.-]|$)", url or "")
    if m:
        y, mo, d = int(m[1]), int(m[2]), int(m[3])
    else:
        m = re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.? (\d{1,2})(?:st|nd|rd|th)?,? (20\d\d)\b",
                      title or "", re.I)
        if m:
            y, mo, d = int(m[3]), MONTHS.index(m[1].lower()) + 1, int(m[2])
    try:
        dt = date(y, mo, d) if y else None
    except ValueError:
        dt = None
    if dt is None or dt > today:
        return ""
    s = f"{MONTHS[dt.month - 1].title()} {dt.day}"
    return s if dt.year == today.year else f"{s}, {dt.year}"


def headlines(items: list[dict], today: date | None = None) -> list[dict]:
    seen_t, seen_d, count = set(), set(), {}
    for x in items:
        k = fix_text(x.get("title", "")).lower()
        count[k] = count.get(k, 0) + 1
    good, slugs = [], []
    for x in items:
        url = str(x.get("url") or "")
        if not re.match(r"^https?://", url) or len(url) > 300:
            continue
        title = fix_text(x.get("title", ""))
        key, dom = title.lower(), _domain(url)
        if (not dom or key in seen_t or dom in seen_d or count.get(key, 0) >= 3 or key in JUNK_TITLES
                or len(title.split()) < 3 or key == dom or "." in title and " " not in title):
            continue
        seen_t.add(key)
        seen_d.add(dom)
        h = {"title": title[:1].upper() + title[1:] if len(title) <= 140 else title[:139].rstrip() + "…",
             "source": dom[:40], "link": url}
        t = when(url, title, today)
        if t:
            h["time"] = t
        (slugs if title == title.lower() else good).append(h)    # all-lowercase titles are URL slugs: last
    return (good + slugs)[:MAX]


def adapt(args: dict, result, raw: str, today: date | None = None) -> dict | None:
    q = _query(args, raw)
    kind = kind_of(q)
    if kind is None:
        return None
    rows = headlines(links(raw, result), today)
    if len(rows) < 2:
        return None
    topic = topic_of(q, kind)
    label = "Sports" if kind == "sports" else "News"
    return {"title": f"{label} · {topic}"[:60], "kind": kind, "query": q[:120], "headlines": rows}
