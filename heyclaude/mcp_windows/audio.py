"""Audio: master volume and mute, per-app volume, output devices (Core Audio
through pycaw). Switching the default output device goes through the
undocumented IPolicyConfig, so it is marked experimental and kept off the
agent's allow-list."""
from __future__ import annotations

import os

from ._util import ToolError, change, com_init, target_level


# -- thin OS layer (replaced by fakes in the tests) ------------------------------
def _os_speakers():
    """The default render device as a pycaw AudioDevice (None if there is none)."""
    com_init()
    from pycaw.pycaw import AudioUtilities
    return AudioUtilities.GetSpeakers()


def _os_endpoint():
    d = _os_speakers()
    if d is None:
        raise ToolError("no audio output device")
    return d.EndpointVolume


def _os_sessions() -> list:
    com_init()
    from pycaw.pycaw import AudioUtilities
    return AudioUtilities.GetAllSessions()


def _os_output_devices() -> list[dict]:
    com_init()
    from pycaw.constants import DEVICE_STATE, EDataFlow
    from pycaw.pycaw import AudioUtilities
    default = AudioUtilities.GetSpeakers()
    default_id = default.id if default is not None else None
    out = []
    for d in AudioUtilities.GetAllDevices(EDataFlow.eRender.value, DEVICE_STATE.ACTIVE.value):
        out.append({"id": d.id, "name": d.FriendlyName or d.id, "default": d.id == default_id})
    return out


def _os_set_default(device_id: str) -> None:
    com_init()
    from pycaw.constants import ERole
    from pycaw.pycaw import AudioUtilities
    AudioUtilities.SetDefaultDevice(device_id, [ERole.eConsole, ERole.eMultimedia, ERole.eCommunications])


# -- tools -------------------------------------------------------------------------
def get_volume() -> dict:
    """Master volume (0-100) and mute state of the default output device."""
    ev = _os_endpoint()
    return {"level": round(ev.GetMasterVolumeLevelScalar() * 100), "muted": bool(ev.GetMute())}


def set_volume(level: int | None = None, delta: int | None = None) -> dict:
    """Set the master volume to `level` (0-100), or change it by `delta`
    (e.g. -10). Setting a level above 0 also unmutes."""
    ev = _os_endpoint()
    old = round(ev.GetMasterVolumeLevelScalar() * 100)
    old_muted = bool(ev.GetMute())
    new = target_level(old, level, delta)
    ev.SetMasterVolumeLevelScalar(new / 100, None)
    muted = old_muted
    if old_muted and new > 0:
        ev.SetMute(0, None)
        muted = False
    return change(old, new, old_muted=old_muted, muted=muted)


def set_mute(muted: bool | None = None) -> dict:
    """Mute (true), unmute (false) or toggle (omit) the master volume."""
    ev = _os_endpoint()
    old = bool(ev.GetMute())
    new = (not old) if muted is None else bool(muted)
    ev.SetMute(1 if new else 0, None)
    return change(old, new, level=round(ev.GetMasterVolumeLevelScalar() * 100))


def _session_name(s) -> str:
    p = s.Process
    if p is not None:
        try:
            return os.path.splitext(p.name())[0]
        except Exception:
            pass
    return (s.DisplayName or "").strip() or "system sounds"


def list_app_volumes() -> dict:
    """Apps that currently have an audio session, with their own volume (0-100)."""
    apps = []
    for s in _os_sessions():
        v = s.SimpleAudioVolume
        apps.append({"app": _session_name(s), "level": round(v.GetMasterVolume() * 100), "muted": bool(v.GetMute())})
    return {"apps": apps}


def _find_session(app: str):
    want = app.lower().removesuffix(".exe").strip()
    if not want:
        raise ToolError("which app?")
    sessions = [(s, _session_name(s)) for s in _os_sessions()]
    hits = [s for s, n in sessions if n.lower() == want] or [s for s, n in sessions if want in n.lower()]
    if not hits:
        names = ", ".join(sorted({n for _, n in sessions})) or "none"
        raise ToolError(f"no audio session for {app!r}; apps with sound: {names}")
    return hits


def set_app_volume(app: str, level: int | None = None, delta: int | None = None) -> dict:
    """Set one app's own volume (0-100, or by `delta`), e.g. app="spotify"."""
    hits = _find_session(app)
    first = hits[0].SimpleAudioVolume
    old = round(first.GetMasterVolume() * 100)
    new = target_level(old, level, delta)
    for s in hits:
        s.SimpleAudioVolume.SetMasterVolume(new / 100, None)
    return change(old, new, app=_session_name(hits[0]))


def list_audio_devices() -> dict:
    """Active output devices; `default` marks the current one."""
    return {"devices": _os_output_devices()}


def set_default_output_device(device: str) -> dict:
    """EXPERIMENTAL (undocumented Windows API): make `device` (a name or id
    from list_audio_devices) the default output."""
    devs = _os_output_devices()
    want = device.lower().strip()
    hit = next((d for d in devs if d["id"] == device), None) \
        or next((d for d in devs if d["name"].lower() == want), None) \
        or next((d for d in devs if want and want in d["name"].lower()), None)
    if hit is None:
        raise ToolError(f"no output device like {device!r}; have: {', '.join(d['name'] for d in devs)}")
    old = next((d for d in devs if d["default"]), None)
    _os_set_default(hit["id"])
    return change(old["name"] if old else None, hit["name"], old_id=old["id"] if old else None, new_id=hit["id"])
