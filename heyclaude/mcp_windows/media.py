"""Media: what's playing (title, artist, album art) and play / pause / next /
previous through WinRT GlobalSystemMediaTransportControlsSessionManager (the
same session the Windows volume flyout shows)."""
from __future__ import annotations

import time

from ._util import ToolError, change, run_async

ACTIONS = ("play", "pause", "play_pause", "next", "previous")
STATUS = {0: "closed", 1: "opened", 2: "changing", 3: "stopped", 4: "playing", 5: "paused"}


# -- thin OS layer -------------------------------------------------------------------
async def _session():
    from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as Manager
    mgr = await Manager.request_async()
    return mgr.get_current_session()


async def _read_art(props, max_bytes: int = 2_000_000) -> bytes | None:
    ref = getattr(props, "thumbnail", None)
    if ref is None:
        return None
    from winrt.windows.storage.streams import Buffer, InputStreamOptions
    stream = await ref.open_read_async()
    size = min(int(stream.size), max_bytes)
    if size <= 0:
        return None
    buf = Buffer(size)
    got = await stream.read_async(buf, size, InputStreamOptions.READ_AHEAD)
    return bytes(memoryview(got))[: got.length]


def _status_name(info) -> str:
    st = getattr(info, "playback_status", None)
    name = getattr(st, "name", None)
    if name:
        return name.lower()
    try:
        return STATUS.get(int(st), "unknown")
    except (TypeError, ValueError):
        return "unknown"


def _os_now_playing(art: bool = False) -> dict:
    async def go():
        s = await _session()
        if s is None:
            return {"status": "none"}
        props = await s.try_get_media_properties_async()
        info = s.get_playback_info()
        out = {"title": props.title or "", "artist": props.artist or "", "album": props.album_title or "",
               "app": s.source_app_user_model_id or "", "status": _status_name(info)}
        try:
            tl = s.get_timeline_properties()
            end = tl.end_time.total_seconds() - tl.start_time.total_seconds()
            if end > 0:
                out["position_s"] = round(tl.position.total_seconds(), 1)
                out["duration_s"] = round(end, 1)
        except Exception:
            pass
        if art:
            try:
                out["art"] = await _read_art(props)
            except Exception:
                out["art"] = None
        return out
    return run_async(go())


def _os_control(action: str) -> bool:
    async def go():
        s = await _session()
        if s is None:
            raise ToolError("nothing is playing media right now")
        c = s.get_playback_info().controls
        able = {"play": c.is_play_enabled, "pause": c.is_pause_enabled or c.is_play_pause_toggle_enabled,
                "play_pause": c.is_play_pause_toggle_enabled, "next": c.is_next_enabled,
                "previous": c.is_previous_enabled}
        if not able[action]:
            what = {"next": "skip to the next track", "previous": "go back a track"}.get(action, action.replace("_", "/"))
            raise ToolError(f"{s.source_app_user_model_id or 'the player'} can't {what} right now")
        call = {"play": s.try_play_async, "pause": s.try_pause_async, "play_pause": s.try_toggle_play_pause_async,
                "next": s.try_skip_next_async, "previous": s.try_skip_previous_async}[action]
        return bool(await call())
    return run_async(go())


# -- tools ---------------------------------------------------------------------------
def _track(d: dict) -> dict:
    return {k: d.get(k) for k in ("title", "artist", "album", "status", "app") if d.get(k) is not None}


def now_playing() -> dict:
    """What's playing: title, artist, album, app, playing/paused, position."""
    d = _os_now_playing(art=False)
    d.pop("art", None)
    return d


def media_control(action: str) -> dict:
    """Control the current media player: play, pause, play_pause, next or previous."""
    action = action.lower().strip().replace("-", "_").replace(" ", "_")
    action = {"skip": "next", "prev": "previous", "back": "previous", "toggle": "play_pause",
              "resume": "play", "stop": "pause"}.get(action, action)
    if action not in ACTIONS:
        raise ToolError(f"action must be one of {', '.join(ACTIONS)}")
    before = _os_now_playing()
    if before.get("status") == "none":
        raise ToolError("nothing is playing media right now")
    if not _os_control(action):
        raise ToolError(f"the player ignored {action}")
    after = before
    deadline = time.monotonic() + (1.5 if action in ("next", "previous") else 0.8)
    while time.monotonic() < deadline:          # the player updates its state a moment later
        time.sleep(0.12)
        after = _os_now_playing()
        if action in ("next", "previous") and after.get("title") != before.get("title"):
            break
        if action not in ("next", "previous") and after.get("status") != before.get("status"):
            break
    return change(_track(before), _track(after), action=action)


def album_art(title: str | None = None) -> bytes | None:
    """Not a tool (the island fetches the art itself, so no image goes to the model).
    With `title`: only while that track is the current one (M5b's now-playing card
    never shows another track's cover)."""
    d = _os_now_playing(art=True)
    if title is not None and (d.get("title") or "") != title:
        return None
    return d.get("art")
