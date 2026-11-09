"""Display: brightness (laptop panel through WMI, external monitors through
DDC/CI), dark/light mode, wallpaper per monitor (IDesktopWallpaper), and
night light (registry blob, experimental)."""
from __future__ import annotations

import os

from ._util import ToolError, change, clamp, com_init, target_level

PERSONALIZE = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
NIGHT_LIGHT = (r"Software\Microsoft\Windows\CurrentVersion\CloudStore\Store\DefaultAccount\Current"
               r"\default$windows.data.bluelightreduction.bluelightreductionstate"
               r"\windows.data.bluelightreduction.bluelightreductionstate")
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tif", ".tiff", ".jfif", ".webp", ".heic")


# == brightness ======================================================================
def _os_panel_get() -> int | None:
    """Built-in panel brightness via WMI, None when there is no such panel."""
    try:
        com_init()
        import win32com.client
        wmi = win32com.client.GetObject(r"winmgmts:\\.\root\WMI")
        for m in wmi.InstancesOf("WmiMonitorBrightness"):
            return int(m.CurrentBrightness)
    except Exception:
        return None
    return None


def _os_panel_set(level: int) -> None:
    com_init()
    import win32com.client
    wmi = win32com.client.GetObject(r"winmgmts:\\.\root\WMI")
    for m in wmi.InstancesOf("WmiMonitorBrightnessMethods"):
        # A dynamic m.WmiSetBrightness(...) call is rejected ("Invalid parameter"):
        # the in-parameters must be typed, so fill an instance of them.
        params = m.Methods_("WmiSetBrightness").InParameters.SpawnInstance_()
        params.Properties_.Item("Timeout").Value = 1
        params.Properties_.Item("Brightness").Value = int(level)
        m.ExecMethod_("WmiSetBrightness", params)


def _os_ddc_monitors() -> list:
    """External monitors that answer DDC/CI (monitorcontrol objects)."""
    try:
        from monitorcontrol import get_monitors
        return list(get_monitors())
    except Exception:
        return []


def _os_ddc_get(mon) -> int | None:
    try:
        with mon:
            return int(mon.get_luminance())
    except Exception:
        return None


def _os_ddc_set(mon, level: int) -> None:
    with mon:
        mon.set_luminance(int(level))


def _screens() -> list[dict]:
    """[{id, kind, level, handle}] for every monitor whose brightness we can read."""
    out = []
    p = _os_panel_get()
    if p is not None:
        out.append({"id": "panel", "kind": "panel", "level": p, "handle": None})
    for i, m in enumerate(_os_ddc_monitors()):
        v = _os_ddc_get(m)
        if v is not None:
            out.append({"id": f"ddc{i}", "kind": "ddc", "level": v, "handle": m})
    return out


def _pick(screens: list[dict], monitor: str | None) -> list[dict]:
    if monitor in (None, "", "all"):
        return screens
    hit = [s for s in screens if s["id"] == str(monitor)]
    if not hit and str(monitor).isdigit() and int(monitor) < len(screens):
        hit = [screens[int(monitor)]]
    if not hit:
        raise ToolError(f"no monitor {monitor!r}; have: {', '.join(s['id'] for s in screens)}")
    return hit


def get_brightness() -> dict:
    """Brightness (0-100) of each monitor that supports it: "panel" is a
    laptop's built-in screen, "ddc0".. are external monitors (DDC/CI)."""
    s = _screens()
    if not s:
        return {"level": None, "monitors": [], "note": "no monitor reports brightness (DDC/CI off?)"}
    return {"level": round(sum(x["level"] for x in s) / len(s)),
            "monitors": [{"id": x["id"], "level": x["level"]} for x in s]}


def set_brightness(level: int | None = None, delta: int | None = None, monitor: str | None = None) -> dict:
    """Set brightness to `level` (0-100) or change it by `delta` (e.g. -20),
    on every monitor or just `monitor` (an id from get_brightness)."""
    screens = _pick(_screens(), monitor)
    if not screens:
        raise ToolError("no monitor here lets me change its brightness (DDC/CI may be off in the monitor's menu)")
    per = []
    for s in screens:
        new = target_level(s["level"], level, delta)
        if s["kind"] == "panel":
            _os_panel_set(new)
        else:
            _os_ddc_set(s["handle"], new)
        per.append({"id": s["id"], "old": s["level"], "new": new})
    old_avg = round(sum(p["old"] for p in per) / len(per))
    new_avg = round(sum(p["new"] for p in per) / len(per))
    return change(old_avg, new_avg, monitors=per)


def restore_brightness(monitors: list[dict]) -> dict:
    """Undo helper: put each monitor back to its own old level."""
    screens = {s["id"]: s for s in _screens()}
    per = []
    for m in monitors:
        s = screens.get(m["id"])
        if s is None:
            continue
        lvl = clamp(m["old"])
        if s["kind"] == "panel":
            _os_panel_set(lvl)
        else:
            _os_ddc_set(s["handle"], lvl)
        per.append({"id": m["id"], "old": s["level"], "new": lvl})
    if not per:
        raise ToolError("those monitors are gone")
    return change(round(sum(p["old"] for p in per) / len(per)), round(sum(p["new"] for p in per) / len(per)),
                  monitors=per)


# == dark / light mode ===================================================================
def _os_reg_get(name: str) -> int | None:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, PERSONALIZE) as k:
            return int(winreg.QueryValueEx(k, name)[0])
    except OSError:
        return None


def _os_reg_set(name: str, value: int) -> None:
    import winreg
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, PERSONALIZE, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, name, 0, winreg.REG_DWORD, int(value))


def _os_broadcast_theme() -> None:
    """Tell running apps (Explorer, the taskbar, UWP) the colour set changed.

    The broadcast waits on every top-level window (up to 200 ms each for a
    slow one) and took ~2 s here, so it runs on its own thread: the tool
    answers at once and the windows repaint as the message reaches them.
    (The async SendNotifyMessage can't carry the string to other processes.)"""
    import ctypes
    import threading
    from ctypes import wintypes

    def send():
        res = wintypes.DWORD()
        ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "ImmersiveColorSet", 0x0002, 200,
                                                 ctypes.byref(res))
    threading.Thread(target=send, daemon=False, name="theme-broadcast").start()


def _mode(v: int | None) -> str:
    return "light" if v == 1 else "dark" if v == 0 else "unknown"


def get_theme() -> dict:
    """Current app and system (taskbar) colour mode: dark or light."""
    apps, system = _os_reg_get("AppsUseLightTheme"), _os_reg_get("SystemUsesLightTheme")
    return {"apps": _mode(apps), "system": _mode(system),
            "mode": _mode(apps) if apps == system else "mixed"}


def set_theme(mode: str = "dark", scope: str = "both") -> dict:
    """Switch to dark or light mode (`mode`: dark, light or toggle). `scope`:
    both (default), apps or system (taskbar and Start)."""
    mode, scope = mode.lower().strip(), scope.lower().strip()
    if mode not in ("dark", "light", "toggle"):
        raise ToolError("mode must be dark, light or toggle")
    if scope not in ("both", "apps", "system"):
        raise ToolError("scope must be both, apps or system")
    before = get_theme()
    old_apps, old_system = _os_reg_get("AppsUseLightTheme"), _os_reg_get("SystemUsesLightTheme")
    if mode == "toggle":
        cur = old_apps if scope != "system" else old_system
        mode = "dark" if cur != 0 else "light"
    light = 1 if mode == "light" else 0
    if scope in ("both", "apps"):
        _os_reg_set("AppsUseLightTheme", light)
    if scope in ("both", "system"):
        _os_reg_set("SystemUsesLightTheme", light)
    _os_broadcast_theme()
    return change(before["mode"], get_theme()["mode"], old_apps=old_apps, old_system=old_system)


def restore_theme(apps: int | None, system: int | None) -> dict:
    """Undo helper: the exact registry values from before."""
    before = get_theme()["mode"]
    if apps is not None:
        _os_reg_set("AppsUseLightTheme", apps)
    if system is not None:
        _os_reg_set("SystemUsesLightTheme", system)
    _os_broadcast_theme()
    return change(before, get_theme()["mode"])


# == wallpaper ===========================================================================
def _wallpaper_api():
    com_init()
    import comtypes
    from ctypes import POINTER
    from ctypes.wintypes import LPCWSTR, LPWSTR, UINT

    from comtypes import COMMETHOD, GUID, HRESULT, IUnknown

    class IDesktopWallpaper(IUnknown):
        _iid_ = GUID("{B92B56A9-8B55-4E14-9A89-0199BBB6F93B}")
        _methods_ = [
            COMMETHOD([], HRESULT, "SetWallpaper", (["in"], LPCWSTR, "monitorID"), (["in"], LPCWSTR, "wallpaper")),
            COMMETHOD([], HRESULT, "GetWallpaper", (["in"], LPCWSTR, "monitorID"),
                      (["out"], POINTER(LPWSTR), "wallpaper")),
            COMMETHOD([], HRESULT, "GetMonitorDevicePathAt", (["in"], UINT, "monitorIndex"),
                      (["out"], POINTER(LPWSTR), "monitorID")),
            COMMETHOD([], HRESULT, "GetMonitorDevicePathCount", (["out"], POINTER(UINT), "count")),
        ]
    return comtypes.CoCreateInstance(GUID("{C2CF3110-460E-4fc1-B9D0-8A1C0C9CC4BD}"), IDesktopWallpaper,
                                     comtypes.CLSCTX_ALL)


def _os_wallpapers() -> list[dict]:
    """[{monitor: index, id: device path, path}] for every monitor."""
    api = _wallpaper_api()
    out = []
    for i in range(api.GetMonitorDevicePathCount()):
        mid = api.GetMonitorDevicePathAt(i)
        try:
            path = api.GetWallpaper(mid) or ""
        except Exception:
            path = ""
        out.append({"monitor": i, "id": mid, "path": path})
    return out


def _os_set_wallpaper(monitor_id: str | None, path: str) -> None:
    _wallpaper_api().SetWallpaper(monitor_id, path)


def get_wallpaper() -> dict:
    """The wallpaper image of each monitor (an empty path = a solid colour,
    Spotlight or a slideshow)."""
    w = _os_wallpapers()
    return {"monitors": [{"monitor": x["monitor"], "path": x["path"]} for x in w],
            "path": w[0]["path"] if w else ""}


def set_wallpaper(path: str, monitor: int | None = None) -> dict:
    """Set the wallpaper to an image file (full path), on every monitor or
    only `monitor` (0, 1, ...)."""
    path = os.path.abspath(os.path.expandvars(os.path.expanduser(path.strip().strip('"'))))
    if not os.path.isfile(path):
        raise ToolError(f"no such file: {path}")
    if not path.lower().endswith(IMAGE_EXT):
        raise ToolError("that isn't an image file")
    walls = _os_wallpapers()
    if monitor is not None and not any(w["monitor"] == monitor for w in walls):
        raise ToolError(f"no monitor {monitor}; have {len(walls)}")
    targets = [w for w in walls if monitor is None or w["monitor"] == monitor]
    per = []
    for w in targets:
        _os_set_wallpaper(w["id"], path)
        per.append({"monitor": w["monitor"], "old": w["path"], "new": path})
    if not per:                                   # no per-monitor API answer: all monitors at once
        _os_set_wallpaper(None, path)
        per.append({"monitor": None, "old": "", "new": path})
    return change(per[0]["old"], path, monitors=per)


def restore_wallpaper(monitors: list[dict]) -> dict:
    """Undo helper: each monitor's old image (skipped where there was none)."""
    walls = {w["monitor"]: w for w in _os_wallpapers()}
    per = []
    for m in monitors:
        old = m.get("old") or ""
        w = walls.get(m.get("monitor"))
        if not old or not os.path.isfile(old):
            continue
        _os_set_wallpaper(w["id"] if w else None, old)
        per.append({"monitor": m.get("monitor"), "old": w["path"] if w else "", "new": old})
    if not per:
        raise ToolError("the old wallpaper wasn't an image file I can put back")
    return change(per[0]["old"], per[0]["new"], monitors=per)


# == night light (experimental) ============================================================
def _os_night_blob() -> bytes | None:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, NIGHT_LIGHT) as k:
            return bytes(winreg.QueryValueEx(k, "Data")[0])
    except OSError:
        return None


def _os_set_night_blob(data: bytes) -> None:
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, NIGHT_LIGHT, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, "Data", 0, winreg.REG_BINARY, bytes(data))


def night_light_blob(data: bytes, on: bool) -> bytes:
    """The community-documented edit of the state blob: byte 18 is 0x15 when
    on (0x13 off), "10 00" sits at offset 23 when on, and the change stamp at
    10..14 must move forward or Windows ignores the write."""
    b = bytearray(data)
    if len(b) < 25:
        raise ToolError("night light state looks unfamiliar; not touching it")
    is_on = b[18] == 0x15
    if is_on == on:
        return bytes(b)
    for i in range(10, 15):
        if b[i] != 0xFF:
            b[i] += 1
            break
    if on:
        b[18] = 0x15
        b[23:23] = b"\x10\x00"
    else:
        b[18] = 0x13
        if b[23:25] == b"\x10\x00":
            del b[23:25]
    return bytes(b)


def get_night_light() -> dict:
    """Whether night light is on (read from its registry state)."""
    b = _os_night_blob()
    if b is None or len(b) < 19:
        return {"on": None, "note": "night light has never been set on this account"}
    return {"on": b[18] == 0x15}


def set_night_light(on: bool) -> dict:
    """EXPERIMENTAL (undocumented registry format): turn night light on or off."""
    b = _os_night_blob()
    if b is None:
        raise ToolError("night light has never been used here; turn it on once in Settings first")
    old = b[18] == 0x15
    _os_set_night_blob(night_light_blob(b, bool(on)))
    return change(old, bool(on))
