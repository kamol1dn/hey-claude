"""Spotify desktop: play a track, album, playlist or artist by its spotify: URI
(or a search query) and make sure it actually starts.

No Web API and no keys. What the desktop app does with a URI (measured on
Spotify 1.2.x for Windows):
  - spotify:track:...  starts the song by itself (~1 s), but only while nothing
    is playing; while something plays it just opens the album with the song's
    row selected;
  - spotify:album: / playlist: / artist: / search:  only open the page.
So for those (and a track that didn't start) this presses Play through UI
Automation inside the page's main landmark: the selected row's own Play
button for a track, else the page's first "Play" button (an album's,
playlist's or artist's big one; a search's top result). UIA's Invoke needs no
focus and no mouse. The button names are Spotify's English UI strings.

Whether it worked comes from Spotify's own media session (SMTC), never from
the session that happens to be "current" (Chrome, a video...).
"""
from __future__ import annotations

import os
import re
import time
from urllib.parse import quote

from ._util import ToolError, run_async

AUTOPLAYS = ("track", "episode")        # the app starts these by itself
PLAY_NAMES = ("Play", "Pause")          # Spotify's English aria-labels
LAUNCH_WAIT_S = 10.0                    # the window after a cold start (1-9 s measured)
AUTOPLAY_WAIT_S = 4.5                   # a track URI starting by itself
PAGE_WAIT_S = 5.0                       # the opened page's Play button (+4 s after a cold start)
SAME_PAGE_S = 2.5                       # page didn't change: it was already open
PLAY_WAIT_S = 3.0                       # the session reporting the new item
TOTAL_S = 14.0                          # the whole call, launch included

_URL = re.compile(r"^https?://open\.spotify\.com/(?:intl-[a-z-]+/)?(?:embed/)?"
                  r"(track|album|playlist|artist|episode|show)/([A-Za-z0-9]+)", re.I)
_URI = re.compile(r"^spotify:(?:user:[^:]+:)?(track|album|playlist|artist|episode|show):([A-Za-z0-9]+)$", re.I)


# -- thin OS layer -------------------------------------------------------------------
def _os_session() -> dict | None:
    """Spotify's own media session: {title, artist, album, status}, or None."""
    from .media import _status_name

    async def go():
        from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as Manager
        mgr = await Manager.request_async()
        for s in mgr.get_sessions():
            if "spotify" in (s.source_app_user_model_id or "").lower():
                p = await s.try_get_media_properties_async()
                return {"title": p.title or "", "artist": p.artist or "", "album": p.album_title or "",
                        "status": _status_name(s.get_playback_info())}
        return None
    return run_async(go(), timeout=3.0)


def _os_window() -> int | None:
    """Spotify's main window (visible or not), or None if it isn't running."""
    import win32gui
    import win32process
    found: list[int] = []

    def visit(h, _):
        if win32gui.GetClassName(h) != "Chrome_WidgetWin_1" or not win32gui.GetWindowText(h).strip():
            return True
        _, pid = win32process.GetWindowThreadProcessId(h)
        try:
            import psutil
            if psutil.Process(pid).name().lower() == "spotify.exe":
                found.append(h)
        except Exception:
            pass
        return True
    win32gui.EnumWindows(visit, None)
    vis = [h for h in found if win32gui.IsWindowVisible(h)]
    return (vis or found or [None])[0]


def _os_open(uri: str) -> None:
    os.startfile(uri)                   # the spotify: protocol handler (starts the app if needed)


_uia = None


def _automation():
    global _uia
    if _uia is None:
        import comtypes.client
        from ._util import com_init
        com_init()
        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen import UIAutomationClient as U
        _uia = (comtypes.client.CreateObject(U.CUIAutomation._reg_clsid_, interface=U.IUIAutomation), U)
    return _uia


def _os_page(hwnd: int, selected: bool = False) -> tuple[str, list]:
    """(the page's title, [(button name, element)] in page order) from the page's
    main landmark; with `selected`, only the buttons of the selected track row (a
    track URI that didn't start opens its album with that row selected).
    Chromium builds its accessibility tree on the first UIA query."""
    auto, U = _automation()
    root = auto.ElementFromHandle(hwnd)
    main = root.FindFirst(U.TreeScope_Descendants,
                          auto.CreatePropertyCondition(30157, 80002))     # LandmarkType == Main
    if not main:
        return "", []
    scope = main
    if selected:
        scope = main.FindFirst(U.TreeScope_Descendants, auto.CreateAndCondition(
            auto.CreatePropertyCondition(U.UIA_ControlTypePropertyId, U.UIA_DataItemControlTypeId),
            auto.CreatePropertyCondition(30079, True)))                  # SelectionItem.IsSelected
        if not scope:
            return main.CurrentName or "", []
    els = scope.FindAll(U.TreeScope_Descendants,
                        auto.CreatePropertyCondition(U.UIA_ControlTypePropertyId, U.UIA_ButtonControlTypeId))
    out = []
    for i in range(els.Length):
        e = els.GetElement(i)
        out.append((e.CurrentName or "", e))
    return main.CurrentName or "", out


def _os_invoke(element) -> None:
    _, U = _automation()
    pat = element.GetCurrentPattern(U.UIA_InvokePatternId)
    pat.QueryInterface(U.IUIAutomationInvokePattern).Invoke()


# -- helpers -------------------------------------------------------------------------
def to_uri(s: str) -> str:
    """spotify:<kind>:<id> from a URI or an open.spotify.com link ("" if it's neither)."""
    s = (s or "").strip()
    m = _URL.match(s) or _URI.match(s.split("?")[0])
    return f"spotify:{m.group(1).lower()}:{m.group(2)}" if m else ""


def _kind(uri: str) -> str:
    return uri.split(":")[1] if uri.count(":") >= 2 else ""


def _page_name(title: str) -> str:
    """"Listen to Homework - Album by Daft Punk | Spotify" -> "Homework - Album by Daft Punk"."""
    t = re.sub(r"\s*[|–—-]\s*Spotify(?: Playlist)?\s*$", "", (title or "").strip())
    return re.sub(r"^Listen to\s+", "", t).strip()


def _play_button(buttons: list) -> tuple[str, object] | None:
    """The page's main Play (or Pause, when it's already playing) button: the first
    one named exactly that or "Play <something>" (a search's top result)."""
    for name, el in buttons:
        n = (name or "").strip()
        if n in PLAY_NAMES or any(n.startswith(p + " ") for p in PLAY_NAMES):
            return n, el
    return None


def _started(before: dict | None, now: dict | None) -> bool:
    """Did Spotify start something new (or start at all) since `before`?"""
    if not now or now.get("status") != "playing":
        return False
    if not before:
        return True
    return (now.get("title"), now.get("artist")) != (before.get("title"), before.get("artist")) \
        or before.get("status") != "playing"


def _wait(cond, seconds: float, step: float = 0.2):
    """Poll `cond()` until it returns something truthy or time runs out."""
    end = time.monotonic() + seconds
    while True:
        try:
            v = cond()
        except ToolError:
            v = None
        if v or time.monotonic() >= end:
            return v
        time.sleep(step)


def _signature(hwnd: int | None, selected: bool = False) -> tuple:
    """What the page shows now: its title and first buttons (with `selected`, the
    selected row's), to tell the opened page from the one before it."""
    if not hwnd:
        return ("", ())
    try:
        title, buttons = _os_page(hwnd, selected)
    except Exception:
        return ("", ())
    return (title, tuple(n for n, _ in buttons[:6]))


def _press_play(hwnd: int, old_sig: tuple, selected: bool, seconds: float) -> dict:
    """Wait for the opened page, then press its Play button (with `selected`: the
    selected track row's). {"page", "pressed": name} or {"page", "already": True}.

    The opened page is new content: a new page title, or (track rows) a newly
    selected row. While Spotify loads a page it can keep the old title for a
    couple of seconds with the old page's buttons half there, so once anything
    changed, only a new title counts. If nothing changes at all for SAME_PAGE_S,
    the page was already open (Spotify doesn't reload it): its button then."""
    t0 = time.monotonic()
    found: dict = {}
    changed = [False]

    def look():
        try:
            title, buttons = _os_page(hwnd, selected)
        except Exception:               # the page is being rebuilt
            return None
        names = tuple(n for n, _ in buttons[:6])
        if (title, names) != old_sig:
            changed[0] = True
        new = bool(title) and (title != old_sig[0] or (selected and names and names != old_sig[1]))
        same = (title, names) == old_sig and (not changed[0] or time.monotonic() - t0 >= seconds)
        if not new and not (same and time.monotonic() - t0 >= SAME_PAGE_S):
            return None
        b = _play_button(buttons)
        if b is None:
            return None
        found.update(page=title, button=b)
        return True

    if not _wait(look, seconds, 0.25) and not look():
        raise ToolError("Spotify opened it but I couldn't find its Play button")
    name, el = found["button"]
    if name.startswith("Pause"):
        return {"page": found["page"], "already": True}
    _os_invoke(el)
    return {"page": found["page"], "pressed": name}


def _press_again(hwnd: int, selected: bool) -> bool:
    """Press the page's Play button once more (the first press can land while the
    page is still wiring itself up). Never presses a Pause button."""
    try:
        b = _play_button(_os_page(hwnd, selected)[1])
    except Exception:
        return False
    if b is None or not b[0].startswith("Play"):
        return False
    _os_invoke(b[1])
    return True


def _result(now: dict | None, uri: str, page: str = "", how: str = "") -> dict:
    now = now or {}
    out = {"playing": {k: now.get(k, "") for k in ("title", "artist", "album")},
           "status": now.get("status", "unknown"), "uri": uri}
    if page:
        out["opened"] = _page_name(page)
    if how:
        out["how"] = how
    return out


# -- tool ----------------------------------------------------------------------------
def play_spotify(uri: str = "", query: str = "") -> dict:
    """Play something in the Spotify desktop app and make sure it starts.
    `uri`: a spotify:track: / album: / playlist: / artist: URI (or an open.spotify.com
    link), e.g. from the Spotify connector's search. `query` (no uri): Spotify's own
    search, then its top result ("daft punk one more time"). Returns what's playing now."""
    target = to_uri(uri) if uri else ""
    if uri and not target:
        raise ToolError("that isn't a Spotify track, album, playlist or artist link")
    if not target:
        q = " ".join((query or "").split())
        if not q:
            raise ToolError("say what to play")
        target = "spotify:search:" + quote(q)
    kind = _kind(target)
    deadline = time.monotonic() + TOTAL_S

    def left(cap: float) -> float:
        return max(0.5, min(cap, deadline - time.monotonic()))

    def started():
        s = _os_session()
        return s if _started(before, s) else None

    hwnd = _os_window()
    cold = not hwnd
    before = _os_session() if hwnd else None
    track = kind in AUTOPLAYS
    old_sig = _signature(hwnd, selected=track)
    _os_open(target)
    if cold:
        hwnd = _wait(_os_window, LAUNCH_WAIT_S, 0.3)
        if not hwnd:
            raise ToolError("Spotify didn't start")

    # A track/episode URI starts by itself, but only while Spotify isn't already
    # playing; otherwise it just opens the album with that row selected.
    if track and (before or {}).get("status") != "playing":
        now = _wait(started, left(AUTOPLAY_WAIT_S + (4.0 if cold else 0.0)))
        if now:
            return _result(now, target, how="opened")

    pressed = _press_play(hwnd, old_sig, selected=track, seconds=left(PAGE_WAIT_S + (4.0 if cold else 0.0)))
    if pressed.get("already"):
        return _result(_os_session(), target, pressed["page"], how="already playing")
    now = _wait(started, left(PLAY_WAIT_S))
    if not now and deadline - time.monotonic() > 1.5 and _press_again(hwnd, track):
        now = _wait(started, left(PLAY_WAIT_S))
    if not now:
        raise ToolError(f"I pressed Play on {_page_name(pressed['page']) or 'it'} but Spotify didn't start")
    return _result(now, target, pressed["page"], how="pressed play")
