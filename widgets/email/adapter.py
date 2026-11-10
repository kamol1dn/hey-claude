"""The claude.ai Gmail connector's search_threads -> an inbox digest: the unread count and
the top senders.

The tool agent sees the connector as `mcp__claude_ai_Gmail__search_threads`. Its result
(checked 2026-11-10) is JSON:

    {"threads": [{"id", "messageCount", "viewUrl",
                  "messages": [{"id", "threadId", "sender": "a@b.com", "toRecipients": [...],
                                "addressDisplayNames": [{"displayName", "emailAddress"}, ...],
                                "date": "2026-11-10T01:33:25Z", "labelIds": ["UNREAD", "INBOX", ...],
                                "subject", "snippet", "viewUrl", ...}]}],
     "nextPageToken": "...", "resultCountEstimate": "9"}

(subject and snippet are left out with view THREAD_VIEW_METADATA_ONLY; THREAD_VIEW_IDS_ONLY
has no messages at all, and then there is no card.) `{}` means nothing matched: a card
saying so. Snippets never reach the card, only who wrote and the subject line.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta

MAX_SENDERS = 4
INBOXY = re.compile(r"^(\s*(is:unread|in:inbox|is:important|is:starred|category:\w+|label:inbox|newer_than:\w+|"
                    r"after:\S+|-in:\S+|-category:\w+)\s*)*$", re.I)


def _clip(s, n: int) -> str:
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _json(result, raw: str):
    if isinstance(result, dict):
        return result
    try:
        v = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return v if isinstance(v, dict) else None


def _when(iso: str, now: datetime) -> tuple[str, float]:
    """("14:02" today / "Tue" this week / "Nov 3", a sort key) in local time."""
    try:
        d = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return "", 0.0
    if d.tzinfo is not None:
        d = d.astimezone().replace(tzinfo=None)
    key = d.timestamp()
    if d.date() == now.date():
        return d.strftime("%H:%M"), key
    if now - d < timedelta(days=6):
        return d.strftime("%a"), key
    return f"{d.strftime('%b')} {d.day}", key


def _name(msg: dict) -> str:
    sender = str(msg.get("sender") or "").strip()
    m = re.search(r"<([^>]+)>", sender)
    email = (m.group(1) if m else sender).lower()
    for a in msg.get("addressDisplayNames") or []:
        if isinstance(a, dict) and str(a.get("emailAddress") or "").lower() == email and a.get("displayName"):
            return _clip(a["displayName"], 40)
    if m and sender[: m.start()].strip(' "'):
        return _clip(sender[: m.start()].strip(' "'), 40)
    local = email.split("@")[0]
    return _clip(re.sub(r"[._-]+", " ", local).title() or email, 40)


def adapt(args: dict, result, raw: str, now: datetime | None = None) -> dict | None:
    v = _json(result, raw)
    if v is None:
        return None
    now = now or datetime.now()
    query = str((args or {}).get("query") or "").strip()
    if (args or {}).get("view") == "THREAD_VIEW_IDS_ONLY":
        return None
    threads = [t for t in (v.get("threads") or []) if isinstance(t, dict)]
    senders: dict[str, dict] = {}
    unread_threads = 0
    for t in threads:
        msgs = [m for m in (t.get("messages") or []) if isinstance(m, dict)]
        if not msgs:
            continue
        unread = any("UNREAD" in (m.get("labelIds") or []) for m in msgs)
        unread_threads += unread
        stamped = [(_when(m.get("date", ""), now), m) for m in msgs]
        (when, key), last = max(stamped, key=lambda x: x[0][1])
        if "SENT" in (last.get("labelIds") or []) and len(stamped) > 1:     # the newest is yours: who wrote before
            others = [s for s in stamped if "SENT" not in (s[1].get("labelIds") or [])]
            if others:
                (when, key), last = max(others, key=lambda x: x[0][1])
        name = _name(last)
        s = senders.setdefault(name.lower(), {"name": name, "count": 0, "unread": 0, "key": 0.0})
        s["count"] += 1
        s["unread"] += int(unread)
        if key >= s["key"]:
            s["key"], s["time"] = key, when
            s["subject"] = _clip(last.get("subject") or "", 90)
            link = str(t.get("viewUrl") or last.get("viewUrl") or "")
            s["link"] = link if re.match(r"^https://mail\.google\.com/", link) and len(link) <= 300 else ""
    if threads and not senders:
        return None
    unread, approx = unread_threads, False
    if re.search(r"\bis:unread\b", query, re.I):
        try:
            est = int(str(v.get("resultCountEstimate") or "").strip())
        except ValueError:
            est = 0
        if est > unread:
            unread, approx = est, bool(v.get("nextPageToken")) or est > len(threads)
    top = sorted(senders.values(), key=lambda s: (-s["unread"], -s["key"]))[:MAX_SENDERS]
    rows = []
    for s in top:
        r = {"name": s["name"], "count": s["count"], "unread": s["unread"]}
        for k in ("subject", "time", "link"):
            if s.get(k):
                r[k] = s[k]
        rows.append(r)
    title = "Inbox" if INBOXY.match(query) else _clip(f"“{query}”", 40)
    return {"title": title, "unread": min(unread, 9999), "approx": approx, "threads": len(threads), "senders": rows}
