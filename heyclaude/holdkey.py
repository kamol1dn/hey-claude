"""Hold a key to keep listening through pauses.

While the island is listening, holding the hold key (Ctrl by default) stops
end-of-speech detection from cutting you off however long you pause. When you
let go, normal detection resumes from that moment. Pressing any other key
during the hold (Ctrl+C, Ctrl+V...) makes it a shortcut, not a hold.

A low-level keyboard hook (WH_KEYBOARD_LL, no admin) is installed only while
armed, i.e. while recording, and it never blocks or eats keys: every event is
passed straight on with CallNextHookEx.
"""
from __future__ import annotations

import ctypes
import logging
import threading
import time
from ctypes import wintypes
from typing import Callable

log = logging.getLogger(__name__)

# Low-level hooks see left/right-specific virtual keys.
_NAMED = {
    "ctrl": {0xA2, 0xA3}, "control": {0xA2, 0xA3},
    "left ctrl": {0xA2}, "lctrl": {0xA2}, "right ctrl": {0xA3}, "rctrl": {0xA3},
    "shift": {0xA0, 0xA1}, "left shift": {0xA0}, "right shift": {0xA1},
    "alt": {0xA4, 0xA5}, "left alt": {0xA4}, "right alt": {0xA5}, "altgr": {0xA5},
    "win": {0x5B, 0x5C}, "left win": {0x5B}, "right win": {0x5C},
    "caps lock": {0x14}, "capslock": {0x14}, "caps": {0x14},
    "menu": {0x5D}, "apps": {0x5D},
}


def parse(spec: str) -> frozenset[int]:
    """'ctrl' / 'right ctrl' / 'caps lock' / 'f13' -> virtual-key codes. ValueError if unknown."""
    s = " ".join(str(spec or "").lower().replace("_", " ").replace("-", " ").split())
    if not s:
        raise ValueError("empty hold key")
    if s in _NAMED:
        return frozenset(_NAMED[s])
    from .hotkey import _KEYS
    k = s.replace(" ", "")
    if k in _KEYS:
        return frozenset({_KEYS[k]})
    raise ValueError(f"unknown hold key {spec!r} (try ctrl, right ctrl, caps lock, f13)")


class HoldState:
    """The hold logic, without Windows: feed key events, read `held`."""

    def __init__(self, vks: frozenset[int], clock=time.perf_counter):
        self.vks, self.clock = vks, clock
        self.down = False          # hold key physically down
        self.cancelled = False     # another key went down during this press: it's a shortcut
        self.held = False          # down, and not a shortcut
        self.released_at: float | None = None   # when the last real hold ended
        self.used = False          # a real hold happened since reset()
        self._used_before = False

    def reset(self, key_down_now: bool = False):
        # A key already down when listening starts (e.g. Ctrl of the push-to-talk
        # hotkey) doesn't count: a hold must be a fresh press.
        self.down, self.cancelled, self.held = key_down_now, key_down_now, False
        self.released_at, self.used = None, False

    def key(self, vk: int, is_down: bool) -> bool:
        """One key event. Returns True if `held` changed."""
        before = self.held
        if vk in self.vks:
            if is_down:
                if not self.down:              # ignore auto-repeat
                    self.down, self.cancelled = True, False
                    self.held = True
                    self._used_before, self.used = self.used, True
            else:
                if self.held:
                    self.released_at = self.clock()
                self.down = self.held = self.cancelled = False
        elif is_down and self.down and not self.cancelled:
            self.cancelled = True              # Ctrl+C etc.: a shortcut, not a hold
            if self.held:
                self.held = False
                self.used = self._used_before
                self.released_at = self.clock()
        return self.held != before


# --- Windows hook ------------------------------------------------------------------
WH_KEYBOARD_LL = 13
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x100, 0x101, 0x104, 0x105
WM_QUIT, WM_APP = 0x12, 0x8000
_ARM, _DISARM = WM_APP + 1, WM_APP + 2
PM_NOREMOVE = 0


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


LRESULT = ctypes.c_ssize_t
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
_user32.SetWindowsHookExW.restype = wintypes.HHOOK
_user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
_user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
_user32.CallNextHookEx.restype = LRESULT
_user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
_user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT,
                                 wintypes.UINT]
_user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
_user32.GetAsyncKeyState.restype = ctypes.c_short
_kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
_kernel32.GetModuleHandleW.restype = wintypes.HMODULE


class HoldKey:
    """Watches the hold key while armed. on_change(held) runs on the hook thread."""

    def __init__(self, spec: str = "ctrl", on_change: Callable[[bool], None] | None = None):
        self.state = HoldState(parse(spec))
        self.on_change = on_change
        self._hook = None
        self._proc = HOOKPROC(self._hook_proc)     # keep a reference: the hook calls it
        self._ready = threading.Event()
        self._tid = 0
        self._thread = threading.Thread(target=self._run, name="holdkey", daemon=True)
        self._thread.start()
        if not self._ready.wait(3):
            raise RuntimeError("hold-key thread did not start")

    # -- public (any thread) ------------------------------------------------------
    def set_key(self, spec: str):
        self.state.vks = parse(spec)

    @property
    def held(self) -> bool:
        return self.state.held

    def arm(self):
        """Start watching (installs the hook). Resets the hold state."""
        down = any(_user32.GetAsyncKeyState(vk) & 0x8000 for vk in self.state.vks)
        self.state.reset(key_down_now=bool(down))
        _user32.PostThreadMessageW(self._tid, _ARM, 0, 0)

    def disarm(self):
        _user32.PostThreadMessageW(self._tid, _DISARM, 0, 0)
        if self.state.held:
            self.state.held = False
            self._notify()

    def close(self):
        if self._thread.is_alive():
            _user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
            self._thread.join(3)

    # -- hook thread ----------------------------------------------------------------
    def _notify(self):
        if self.on_change:
            try:
                self.on_change(self.state.held)
            except Exception:
                log.exception("hold-key callback failed")

    def _hook_proc(self, n_code, w_param, l_param):
        try:
            if n_code == 0 and w_param in (WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP):
                kb = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                if self.state.key(int(kb.vkCode), w_param in (WM_KEYDOWN, WM_SYSKEYDOWN)):
                    self._notify()
        except Exception:
            pass
        return _user32.CallNextHookEx(None, n_code, w_param, l_param)   # never swallow

    def _install(self, on: bool):
        if on and not self._hook:
            self._hook = _user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, _kernel32.GetModuleHandleW(None), 0)
            if not self._hook:
                log.warning("hold key: SetWindowsHookEx failed (%s)", ctypes.get_last_error())
        elif not on and self._hook:
            _user32.UnhookWindowsHookEx(self._hook)
            self._hook = None

    def _run(self):
        msg = wintypes.MSG()
        self._tid = _kernel32.GetCurrentThreadId()
        _user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_NOREMOVE)
        self._ready.set()
        try:
            while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == _ARM:
                    self._install(True)
                elif msg.message == _DISARM:
                    self._install(False)
        finally:
            self._install(False)
