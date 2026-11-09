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


def run_async(coro):
    """Run a WinRT coroutine to completion from sync code (any thread)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    box: dict = {}

    def runner():
        try:
            box["v"] = asyncio.run(coro)
        except BaseException as e:      # noqa: BLE001 - re-raised below
            box["e"] = e
    t = threading.Thread(target=runner, daemon=True)
    t.start()
    t.join()
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
