"""Every tool of the Windows settings server in one table: its group, whether
it reads or changes something, whether it belongs on the agent's default
allow-list, and how to undo it.

hey-claude imports this too: "undo that" and the island's Undo button run the
inverse call in-process (no model round trip), and the effects read `kind`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

from . import apps, audio, clipboard, display, media, power, radios, screen, spotify

SERVER = "windows"                     # the MCP server name: tools are mcp__windows__<name>
PREFIX = f"mcp__{SERVER}__"

# The "never exposed" areas (ROADMAP M9). No tool name may match this, and the
# launcher / Settings-page tools refuse these targets.
NEVER = re.compile(r"defender|firewall|\buac\b|user_?account|registry|regedit|powershell|"
                   r"\bshell\b|driver|service", re.I)


@dataclass
class Tool:
    name: str
    func: Callable
    group: str
    kind: str                          # read | write | ask (asks the user; does nothing itself)
    allow: bool                        # on the default allow-list
    experimental: bool = False
    undo: Callable[[dict, dict], list] | None = field(default=None, repr=False)

    @property
    def full_name(self) -> str:
        return PREFIX + self.name


def _vol_undo(a, r):
    out = [("set_volume", {"level": r["old"]})]
    if r.get("old_muted") and not r.get("muted"):
        out.append(("set_mute", {"muted": True}))
    return out


_MEDIA_INVERSE = {"next": "previous", "previous": "next", "play": "pause", "pause": "play",
                  "play_pause": "play_pause"}


def _placement_undo(a, r):
    return [("restore_placement", {"hwnd": r["hwnd"], "placement": r["placement"]})] if r.get("placement") else None


def _wall_undo(a, r):
    mons = [m for m in r.get("monitors") or [] if m.get("old")]
    return [("restore_wallpaper", {"monitors": mons})] if mons else None


def _power_mode_undo(a, r):
    return [("set_power_mode", {"mode": r["old"]})] if r.get("old") in power.MODES else None


def _clip_undo(a, r):
    if r.get("undoable") is False or r.get("old") is None:
        return None
    return [("set_clipboard", {"text": r["old"]})]


def _screenshot(monitor: int = 0):
    """A screenshot of one monitor (0 = the main one), scaled to at most 1280 px
    wide, for "what's on my screen?"."""
    data, w, h = screen.screenshot_bytes(monitor)
    return {"image_jpeg": data, "width": w, "height": h}


_T = [
    # audio
    Tool("get_volume", audio.get_volume, "audio", "read", True),
    Tool("set_volume", audio.set_volume, "audio", "write", True, undo=_vol_undo),
    Tool("set_mute", audio.set_mute, "audio", "write", True, undo=lambda a, r: [("set_mute", {"muted": r["old"]})]),
    Tool("list_app_volumes", audio.list_app_volumes, "audio", "read", True),
    Tool("set_app_volume", audio.set_app_volume, "audio", "write", True,
         undo=lambda a, r: [("set_app_volume", {"app": r["app"], "level": r["old"]})]),
    Tool("list_audio_devices", audio.list_audio_devices, "audio", "read", True),
    Tool("set_default_output_device", audio.set_default_output_device, "audio", "write", False, experimental=True,
         undo=lambda a, r: [("set_default_output_device", {"device": r["old_id"]})] if r.get("old_id") else None),
    # display
    Tool("get_brightness", display.get_brightness, "display", "read", True),
    Tool("set_brightness", display.set_brightness, "display", "write", True,
         undo=lambda a, r: [("restore_brightness", {"monitors": r["monitors"]})]),
    Tool("get_theme", display.get_theme, "display", "read", True),
    Tool("set_theme", display.set_theme, "display", "write", True,
         undo=lambda a, r: [("restore_theme", {"apps": r.get("old_apps"), "system": r.get("old_system")})]),
    Tool("get_wallpaper", display.get_wallpaper, "display", "read", True),
    Tool("set_wallpaper", display.set_wallpaper, "display", "write", True, undo=_wall_undo),
    Tool("get_night_light", display.get_night_light, "display", "read", True),
    Tool("set_night_light", display.set_night_light, "display", "write", False, experimental=True,
         undo=lambda a, r: [("set_night_light", {"on": r["old"]})]),
    # radios
    Tool("get_radios", radios.get_radios, "radios", "read", True),
    Tool("set_wifi", radios.set_wifi, "radios", "write", True, undo=lambda a, r: [("set_wifi", {"on": r["old"]})]),
    Tool("set_bluetooth", radios.set_bluetooth, "radios", "write", True,
         undo=lambda a, r: [("set_bluetooth", {"on": r["old"]})]),
    # power
    Tool("battery_status", power.battery_status, "power", "read", True),
    Tool("get_power_mode", power.get_power_mode, "power", "read", True),
    Tool("set_power_mode", power.set_power_mode, "power", "write", True, undo=_power_mode_undo),
    Tool("lock_pc", power.lock_pc, "power", "write", True),
    Tool("request_power_action", power.request_power_action, "power", "ask", True),
    # media
    Tool("now_playing", media.now_playing, "media", "read", True),
    Tool("media_control", media.media_control, "media", "write", True,
         undo=lambda a, r: [("media_control", {"action": _MEDIA_INVERSE[r["action"]]})]
         if r.get("action") in _MEDIA_INVERSE else None),
    Tool("play_spotify", spotify.play_spotify, "media", "write", True),     # no undo: nothing to go back to
    # apps and windows
    Tool("list_apps", apps.list_apps, "apps", "read", True),
    Tool("launch_app", apps.launch_app, "apps", "write", True),
    Tool("list_windows", apps.list_windows, "apps", "read", True),
    Tool("focus_window", apps.focus_window, "apps", "write", True),
    Tool("minimize_window", apps.minimize_window, "apps", "write", True, undo=_placement_undo),
    Tool("restore_window", apps.restore_window, "apps", "write", True),
    Tool("snap_window", apps.snap_window, "apps", "write", True, undo=_placement_undo),
    Tool("close_window", apps.close_window, "apps", "write", False),
    # clipboard
    Tool("get_clipboard", clipboard.get_clipboard, "clipboard", "read", True),
    Tool("set_clipboard", clipboard.set_clipboard, "clipboard", "write", True, undo=_clip_undo),
    # screen and Settings
    Tool("screenshot", _screenshot, "screen", "read", True),
    Tool("open_settings_page", screen.open_settings_page, "settings", "write", True),
]
TOOLS: dict[str, Tool] = {t.name: t for t in _T}

# Undo helpers: callable in-process, never exposed as MCP tools.
INTERNAL: dict[str, Callable] = {
    "restore_brightness": display.restore_brightness,
    "restore_theme": display.restore_theme,
    "restore_wallpaper": display.restore_wallpaper,
    "restore_placement": apps.restore_placement,
}


def short_name(name: str) -> str:
    """"mcp__windows__set_volume" -> "set_volume" (other servers' names unchanged)."""
    return name[len(PREFIX):] if name.startswith(PREFIX) else name


def get(name: str) -> Tool | None:
    return TOOLS.get(short_name(name)) if name.startswith(PREFIX) or name in TOOLS else None


def call(name: str, args: dict | None = None):
    """Run a tool or an undo helper in this process."""
    n = short_name(name)
    fn = TOOLS[n].func if n in TOOLS else INTERNAL.get(n)
    if fn is None:
        raise KeyError(f"no tool {name!r}")
    return fn(**(args or {}))


def inverse(name: str, args: dict, result: dict) -> list[tuple[str, dict]] | None:
    """The calls that undo `name(args) -> result`, or None if it can't be undone."""
    t = get(name)
    if t is None or t.undo is None or not isinstance(result, dict):
        return None
    try:
        return t.undo(args or {}, result) or None
    except (KeyError, TypeError):
        return None


def allow_list() -> list[str]:
    """Full tool names for the agent's default allow-list (read + safe writes)."""
    return [t.full_name for t in _T if t.allow]


def never_exposed_violations() -> list[str]:
    """Tool names that touch a never-exposed area (must stay empty)."""
    return [t.name for t in _T if NEVER.search(t.name)]
