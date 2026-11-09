"""Power: battery status, power mode (the Settings slider: best power
efficiency / balanced / best performance, via PowerSetActiveOverlayScheme),
lock. Sleep, restart and shutdown are never done by a tool: the tool only
*asks*, and the island shows a button the user must click."""
from __future__ import annotations

import uuid

from ._util import ToolError, change

MODES = {
    "best_efficiency": "961cc777-2547-4f9d-8174-7d86181b8a7a",
    "balanced": "00000000-0000-0000-0000-000000000000",
    "better_performance": "3af9b8d9-7c97-431d-ad78-34a8bfea439f",
    "best_performance": "ded574b5-45a0-4f42-8737-46345c09c238",
}
ALIASES = {"efficiency": "best_efficiency", "power_saver": "best_efficiency", "saver": "best_efficiency",
           "battery_saver": "best_efficiency", "recommended": "balanced", "performance": "best_performance",
           "better": "better_performance", "best": "best_performance"}
POWER_ACTIONS = ("sleep", "restart", "shutdown")


# -- thin OS layer -------------------------------------------------------------------
def _guid_struct():
    import ctypes

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort), ("Data3", ctypes.c_ushort),
                    ("Data4", ctypes.c_ubyte * 8)]
    return GUID


def _os_battery() -> dict:
    import ctypes

    class SPS(ctypes.Structure):
        _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
                    ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
                    ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong)]
    s = SPS()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(s)):
        raise ToolError("couldn't read the power status")
    has = not (s.BatteryFlag & 128) and s.BatteryFlag != 255
    return {"has_battery": has, "percent": s.BatteryLifePercent if has and s.BatteryLifePercent <= 100 else None,
            "plugged_in": s.ACLineStatus == 1, "charging": bool(s.BatteryFlag & 8),
            "minutes_left": s.BatteryLifeTime // 60 if has and s.BatteryLifeTime != 0xFFFFFFFF else None,
            "battery_saver": bool(s.SystemStatusFlag & 1)}


def _os_get_overlay() -> str:
    import ctypes
    G = _guid_struct()
    g = G()
    rc = ctypes.windll.powrprof.PowerGetEffectiveOverlayScheme(ctypes.byref(g))
    if rc:
        raise ToolError(f"Windows didn't report the power mode (error {rc})")
    return str(uuid.UUID(bytes_le=bytes(memoryview(g).cast("B"))))


def _os_set_overlay(guid: str) -> None:
    import ctypes
    G = _guid_struct()
    g = G.from_buffer_copy(uuid.UUID(guid).bytes_le)
    rc = ctypes.windll.powrprof.PowerSetActiveOverlayScheme(g)
    if rc:
        raise ToolError(f"Windows refused the power mode (error {rc}); it only applies on the Balanced plan")


def _os_lock() -> None:
    import ctypes
    if not ctypes.windll.user32.LockWorkStation():
        raise ToolError("couldn't lock")


def _os_power_action(action: str) -> None:
    """Only ever called from hey-claude after the user clicked Confirm."""
    import subprocess
    if action == "sleep":
        import ctypes
        ctypes.windll.powrprof.SetSuspendState(0, 0, 0)
    elif action == "restart":
        subprocess.Popen(["shutdown", "/r", "/t", "0"], creationflags=0x08000000)
    elif action == "shutdown":
        subprocess.Popen(["shutdown", "/s", "/t", "0"], creationflags=0x08000000)


# -- tools ---------------------------------------------------------------------------
def battery_status() -> dict:
    """Battery level, whether it's plugged in / charging, time left."""
    return _os_battery()


def _mode_name(guid: str) -> str:
    return next((k for k, v in MODES.items() if v == guid.lower()), guid)


def get_power_mode() -> dict:
    """The power mode: best_efficiency, balanced, better_performance or best_performance."""
    return {"mode": _mode_name(_os_get_overlay())}


def set_power_mode(mode: str) -> dict:
    """Set the power mode: best_efficiency, balanced, better_performance or best_performance."""
    key = mode.lower().strip().replace(" ", "_").replace("-", "_")
    key = ALIASES.get(key, key)
    if key not in MODES:
        raise ToolError(f"mode must be one of {', '.join(MODES)}")
    old = _mode_name(_os_get_overlay())
    _os_set_overlay(MODES[key])
    return change(old, key)


def lock_pc() -> dict:
    """Lock the PC (the sign-in screen; nothing is closed)."""
    _os_lock()
    return {"locked": True}


def request_power_action(action: str) -> dict:
    """Ask to put the PC to sleep, restart it or shut it down. This does NOT
    do it: hey-claude shows a Confirm button on the island and the user has
    to click it. Tell the user to click Confirm on the island."""
    action = action.lower().strip()
    if action not in POWER_ACTIONS:
        raise ToolError(f"action must be one of {', '.join(POWER_ACTIONS)}")
    return {"pending": action, "needs_click": True,
            "note": f"Nothing happens until the user clicks Confirm on the island ({action})."}


def perform_power_action(action: str) -> None:
    """Not a tool: hey-claude calls this after the click."""
    if action not in POWER_ACTIONS:
        raise ToolError(f"unknown power action {action!r}")
    _os_power_action(action)
