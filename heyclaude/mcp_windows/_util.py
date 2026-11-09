"""Shared helpers for the Windows settings tools: COM per thread, WinRT
coroutines from sync code, clamping and small result shapes.

Every Windows API is imported lazily inside the function that needs it, so
importing a tool module costs nothing and the offline tests can replace the
thin `_os_*` layers with fakes.
"""
from __future__ import annotations

import asyncio
import threading

_local = threading.local()


class ToolError(Exception):
    """A clear, user-facing failure (bad argument, nothing to act on).

    The MCP server turns it into an error result; the agent reads the
    message and says it out loud."""


def com_init() -> None:
    """CoInitialize once per thread (pycaw, IDesktopWallpaper, Shell COM)."""
    if getattr(_local, "com", False):
        return
    try:
        import comtypes
        comtypes.CoInitialize()
    except OSError:
        pass                    # already initialised in another mode: fine for these calls
    except ImportError:
        pass
    _local.com = True


def run_async(coro, timeout: float = 6.0):
    """Run a WinRT coroutine to completion from sync code.

    Always on a fresh thread: WinRT completions are delivered to the calling
    apartment, and a thread that pycaw/comtypes made single-threaded (STA)
    never pumps messages inside asyncio.run, so an await there can hang for
    good. The new thread starts in the multithreaded apartment. A call that
    doesn't finish within `timeout` raises ToolError instead of hanging."""
    box: dict = {}

    def runner():
        try:
            box["v"] = asyncio.run(coro)
        except BaseException as e:      # noqa: BLE001 - re-raised below
            box["e"] = e
    t = threading.Thread(target=runner, daemon=True, name="winrt")
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise ToolError("Windows didn't answer in time")
    if "e" in box:
        raise box["e"]
    return box.get("v")


def clamp(v: float, lo: float = 0, hi: float = 100) -> int:
    return int(round(max(lo, min(hi, v))))


def target_level(old: int, level: int | None, delta: int | None, lo: int = 0, hi: int = 100) -> int:
    """`level` wins; else old + delta. One of them is required."""
    if level is None and delta is None:
        raise ToolError("give a level or a delta")
    return clamp(level if level is not None else old + delta, lo, hi)


def change(old, new, **extra) -> dict:
    """The shape every write tool returns: {"old": ..., "new": ..., ...}."""
    out = {"old": old, "new": new}
    out.update(extra)
    return out
