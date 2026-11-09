"""Radios: Wi-Fi and Bluetooth on/off (WinRT Windows.Devices.Radios) and
airplane mode status (read only)."""
from __future__ import annotations

from ._util import ToolError, change, run_async

KINDS = {"wifi": "WI_FI", "bluetooth": "BLUETOOTH"}


# -- thin OS layer -------------------------------------------------------------------
async def _radios():
    from winrt.windows.devices.radios import Radio
    try:
        await Radio.request_access_async()
    except Exception:
        pass
    return list(await Radio.get_radios_async())


def _kind_name(r) -> str:
    k = str(getattr(r.kind, "name", r.kind)).upper()
    return {"WI_FI": "wifi", "WIFI": "wifi", "BLUETOOTH": "bluetooth", "MOBILE_BROADBAND": "cellular"}.get(k, k.lower())


def _state_name(r) -> str:
    return str(getattr(r.state, "name", r.state)).lower()      # on / off / disabled / unknown


def _os_list() -> list[dict]:
    async def go():
        return [{"kind": _kind_name(r), "name": r.name, "state": _state_name(r)} for r in await _radios()]
    return run_async(go())


def _os_set(kind: str, on: bool) -> list[str]:
    """Switch every radio of `kind`; returns the access status per radio."""
    from winrt.windows.devices.radios import RadioState

    async def go():
        out = []
        for r in await _radios():
            if _kind_name(r) == kind:
                st = await r.set_state_async(RadioState.ON if on else RadioState.OFF)
                out.append(str(getattr(st, "name", st)).lower())
        return out
    return run_async(go())


def _os_airplane() -> bool | None:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SYSTEM\CurrentControlSet\Control\RadioManagement\SystemRadioState") as k:
            return bool(winreg.QueryValueEx(k, "")[0])
    except OSError:
        return None


# -- tools ---------------------------------------------------------------------------
def get_radios() -> dict:
    """Wi-Fi / Bluetooth radios and whether each is on, plus airplane mode."""
    return {"radios": _os_list(), "airplane_mode": _os_airplane()}


def _set(kind: str, on: bool | None) -> dict:
    radios = [r for r in _os_list() if r["kind"] == kind]
    if not radios:
        raise ToolError(f"this PC has no {'Wi-Fi' if kind == 'wifi' else 'Bluetooth'} radio")
    old = radios[0]["state"] == "on"
    new = (not old) if on is None else bool(on)
    res = _os_set(kind, new)
    if res and all(s not in ("allowed", "0") for s in res):
        raise ToolError(f"Windows refused ({', '.join(res)})")
    return change(old, new, radio=kind)


def set_wifi(on: bool | None = None) -> dict:
    """Turn Wi-Fi on (true), off (false) or toggle it (omit)."""
    return _set("wifi", on)


def set_bluetooth(on: bool | None = None) -> dict:
    """Turn Bluetooth on (true), off (false) or toggle it (omit)."""
    return _set("bluetooth", on)
