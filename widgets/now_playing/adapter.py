"""now_playing: M9's `now_playing` result -> the card's data.

The tool answers {title, artist, album, app, status, position_s?, duration_s?}, or
{"status": "none"} when no player has a media session (no card then: the answer
says so). `app` is the player's AppUserModelID ("Spotify.exe", "Chrome",
"Microsoft.ZuneMusic_8wekyb3d8bbwe!Microsoft.ZuneMusic"), shown by its plain name.
The cover isn't in the result (no image goes to the model): the island reads it
itself for the card (heyclaude/island_qml.py, `_now_playing_art`).
"""
from __future__ import annotations

import json
import re

STATUSES = ("playing", "paused", "stopped", "changing", "opened", "closed", "unknown")
# a player's id (lower case, any part of it) -> its name
APPS = (("spotify", "Spotify"), ("zunemusic", "Media Player"), ("zunevideo", "Films & TV"),
        ("applemusic", "Apple Music"), ("itunes", "iTunes"), ("msedge", "Edge"), ("microsoftedge", "Edge"),
        ("chrome", "Chrome"), ("firefox", "Firefox"), ("brave", "Brave"), ("opera", "Opera"),
        ("vivaldi", "Vivaldi"), ("yandex", "Yandex"), ("vlc", "VLC"), ("foobar2000", "foobar2000"),
        ("musicbee", "MusicBee"), ("aimp", "AIMP"), ("tidal", "TIDAL"), ("deezer", "Deezer"),
        ("amazonmusic", "Amazon Music"), ("telegram", "Telegram"), ("discord", "Discord"),
        ("winamp", "Winamp"), ("potplayer", "PotPlayer"), ("mpc-hc", "MPC-HC"), ("youtube", "YouTube Music"))


def app_name(aumid: str) -> str:
    a = str(aumid or "").strip()
    low = a.lower().replace(" ", "")
    for key, name in APPS:
        if key in low:
            return name
    a = a.split("!")[-1]                              # Package_hash!App -> App
    a = re.sub(r"\.exe$", "", a, flags=re.I)
    a = a.split("\\")[-1].split("/")[-1]
    a = a.split(".")[-1] if "." in a and " " not in a else a
    a = re.sub(r"_[a-z0-9]{13}$", "", a)              # a package family hash
    return (a[:1].upper() + a[1:])[:40] if a else ""


def _clip(s, n: int) -> str:
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def adapt(args: dict, result, raw: str) -> dict | None:
    r = result
    if not isinstance(r, dict):
        try:
            r = json.loads(raw) if raw.lstrip().startswith("{") else None
        except ValueError:
            r = None
    if not isinstance(r, dict) or not str(r.get("title") or "").strip():
        return None                                   # nothing playing, or a session without a track
    status = str(r.get("status") or "unknown").lower()
    out = {"title": _clip(r["title"], 120), "status": status if status in STATUSES else "unknown"}
    for k, n in (("artist", 80), ("album", 80)):
        v = _clip(r.get(k), n)
        if v:
            out[k] = v
    app = app_name(r.get("app", ""))
    if app:
        out["app"] = app
    try:
        dur = float(r.get("duration_s") or 0)
        pos = float(r.get("position_s") or 0)
    except (TypeError, ValueError):
        dur = pos = 0.0
    if 0 < dur <= 360000:
        out["duration_s"] = round(dur, 1)
        out["position_s"] = round(max(0.0, min(dur, pos)), 1)
    return out
