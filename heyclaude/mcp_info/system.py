"""system_status(): battery, CPU and GPU load, VRAM and RAM, read here and now (ROADMAP M5b).

Each part degrades on its own: no battery (a desktop) -> "battery": null; no NVIDIA
driver or nvidia-ml-py -> "gpu": null; no psutil -> "cpu"/"ram" from what Windows
says directly (or null). What was missing is listed under "missing", so the agent
can say so instead of guessing. The machine's name is never read.

The island's system card comes from this result (widgets/system); its adapter adds
hey-claude's own devices (speech-to-text and voice: GPU or CPU), which only the
app itself knows.
"""
from __future__ import annotations

import ctypes
import platform
import sys
import time

PROMPT = ("system_status gives battery, CPU/GPU load, VRAM and RAM (\"how's my battery\", \"is the GPU busy\", "
          "\"how much RAM is free\").")


# ---------------------------------------------------------------- battery
class _PowerStatus(ctypes.Structure):
    _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
                ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
                ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong)]


def _power_status():
    """GetSystemPowerStatus, or None off Windows / when it fails."""
    if sys.platform != "win32":
        return None
    st = _PowerStatus()
    try:
        if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(st)):
            return None
    except (AttributeError, OSError):
        return None
    return st


def battery(power=None, psutil_mod=None) -> dict | None:
    """{percent, plugged, charging, time_left_min, saver} or None when there is no battery."""
    st = power if power is not None else _power_status()
    if st is not None:
        flag = int(st.BatteryFlag)
        if flag == 128 or (flag == 255 and int(st.BatteryLifePercent) == 255):   # no system battery / unknown
            return None
        pct = int(st.BatteryLifePercent)
        if pct > 100:
            return None
        plugged = int(st.ACLineStatus) == 1
        life = int(st.BatteryLifeTime)
        # charging only when Windows says so: plugged in at 96 % is often a charge limit, not charging
        return {"percent": pct, "plugged": plugged, "charging": bool(flag & 8),
                "time_left_min": None if plugged or life in (0xFFFFFFFF, -1) or life <= 0 else round(life / 60),
                "saver": int(st.SystemStatusFlag) == 1}
    ps = psutil_mod if psutil_mod is not None else _psutil()
    if ps is None:
        return None
    try:
        b = ps.sensors_battery()
    except Exception:
        b = None
    if b is None:
        return None
    secs = b.secsleft if isinstance(b.secsleft, (int, float)) and b.secsleft > 0 else None
    plugged = bool(b.power_plugged)
    return {"percent": int(round(b.percent)), "plugged": plugged, "charging": plugged and b.percent < 100,
            "time_left_min": None if plugged or secs is None else round(secs / 60), "saver": False}


# ---------------------------------------------------------------- CPU and RAM
def _psutil():
    try:
        import psutil
        return psutil
    except Exception:
        return None


def _cpu_name() -> str:
    """"AMD Ryzen 7 8845HS w/ Radeon 780M Graphics" (the registry), else platform.processor()."""
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
                return " ".join(str(winreg.QueryValueEx(k, "ProcessorNameString")[0]).split())
        except OSError:
            pass
    return " ".join((platform.processor() or "").split())


class _MemStatus(ctypes.Structure):
    _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


def ram(psutil_mod=None) -> dict | None:
    """{used_gb, total_gb, percent}."""
    ps = psutil_mod if psutil_mod is not None else _psutil()
    total = avail = None
    if ps is not None:
        try:
            vm = ps.virtual_memory()
            total, avail = float(vm.total), float(vm.available)
        except Exception:
            pass
    if total is None and sys.platform == "win32":
        ms = _MemStatus()
        ms.dwLength = ctypes.sizeof(_MemStatus)
        try:
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms)):
                total, avail = float(ms.ullTotalPhys), float(ms.ullAvailPhys)
        except (AttributeError, OSError):
            pass
    if not total:
        return None
    used = total - avail
    return {"used_gb": round(used / 2 ** 30, 1), "total_gb": round(total / 2 ** 30, 1),
            "percent": round(100 * used / total, 1)}


# ---------------------------------------------------------------- GPU (NVML)
def _nvml():
    try:
        import pynvml
        pynvml.nvmlInit()
        return pynvml
    except Exception:
        return None


def _gpu_name(raw) -> str:
    s = raw.decode() if isinstance(raw, bytes) else str(raw)
    return " ".join(s.split())


# ---------------------------------------------------------------- the tool
def _sample(window_s: float, psutil_mod, nv, handle) -> tuple[float | None, float | None]:
    """CPU % over the window and the GPU's mean utilisation over the same window."""
    cpu = None
    if psutil_mod is not None:
        try:
            psutil_mod.cpu_percent(None)                  # primes the counter
        except Exception:
            psutil_mod = None
    gpu = []
    n = max(1, int(window_s / 0.1))
    for _ in range(n):
        if nv is not None and handle is not None:
            try:
                gpu.append(float(nv.nvmlDeviceGetUtilizationRates(handle).gpu))
            except Exception:
                pass
        if window_s > 0:
            time.sleep(window_s / n)
    if psutil_mod is not None:
        try:
            cpu = float(psutil_mod.cpu_percent(None))
        except Exception:
            cpu = None
    return cpu, (sum(gpu) / len(gpu) if gpu else None)


def system_status() -> dict:
    """This PC's state right now: battery (percent, charging, time left; null on a desktop),
    CPU load, GPU load, VRAM used/total and temperature (NVIDIA), and RAM used/total.
    Anything this PC can't report is null and named in "missing"."""
    return read()


def read(psutil_mod=None, nvml=None, power=None, window_s: float = 0.3, cpu_name=None) -> dict:
    """system_status() with every OS source injectable (the tests pass fakes)."""
    missing = []
    ps = psutil_mod if psutil_mod is not None else _psutil()
    if ps is None:
        missing.append("psutil (CPU load)")
    nv = nvml if nvml is not None else _nvml()
    handle = None
    if nv is not None:
        try:
            handle = nv.nvmlDeviceGetHandleByIndex(0)
        except Exception:
            handle = None
    cpu_pct, gpu_pct = _sample(window_s, ps, nv, handle)

    out: dict = {"battery": battery(power, ps)}
    cpu = {"percent": None if cpu_pct is None else round(cpu_pct, 1),
           "name": cpu_name if cpu_name is not None else _cpu_name()}
    if ps is not None:
        try:
            cpu["cores"] = int(ps.cpu_count(logical=False) or 0) or None
            cpu["threads"] = int(ps.cpu_count() or 0) or None
        except Exception:
            pass
    out["cpu"] = cpu
    gpu = None
    if handle is not None:
        gpu = {"name": "", "percent": None if gpu_pct is None else round(gpu_pct)}
        try:
            gpu["name"] = _gpu_name(nv.nvmlDeviceGetName(handle))
        except Exception:
            pass
        try:
            mem = nv.nvmlDeviceGetMemoryInfo(handle)
            gpu["vram_used_mb"] = round(mem.used / 2 ** 20)
            gpu["vram_total_mb"] = round(mem.total / 2 ** 20)
        except Exception:
            missing.append("VRAM")
        try:
            gpu["temp_c"] = int(nv.nvmlDeviceGetTemperature(handle, 0))      # NVML_TEMPERATURE_GPU
        except Exception:
            pass
    else:
        missing.append("GPU (no NVIDIA driver or nvidia-ml-py)")
    out["gpu"] = gpu
    out["ram"] = ram(ps)
    if out["ram"] is None:
        missing.append("RAM")
    if out["battery"] is None:
        missing.append("battery (none on this PC)")
    out["missing"] = missing
    return out


TOOLS = [system_status]
