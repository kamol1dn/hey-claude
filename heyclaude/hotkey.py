"""Global hotkey (push-to-talk) via the Win32 RegisterHotKey API. No admin needed.

Hotkeys are written like "ctrl+alt+space", "win+shift+h" or "f9".
`parse()` turns that into (modifiers, virtual-key) for RegisterHotKey and
raises ValueError for anything it doesn't understand. `GlobalHotkey`
registers one on a background thread and calls you back when it's pressed.
"""
from __future__ import annotations

import ctypes
import logging
import threading
from ctypes import wintypes
from typing import Callable

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000

_MODS = {"ctrl": MOD_CONTROL, "control": MOD_CONTROL, "alt": MOD_ALT, "shift": MOD_SHIFT,
         "win": MOD_WIN, "windows": MOD_WIN, "super": MOD_WIN, "meta": MOD_WIN}
_MOD_ORDER = [(MOD_CONTROL, "ctrl"), (MOD_ALT, "alt"), (MOD_SHIFT, "shift"), (MOD_WIN, "win")]

_KEYS = {
    "space": 0x20, "enter": 0x0D, "return": 0x0D, "tab": 0x09, "esc": 0x1B, "escape": 0x1B,
    "backspace": 0x08, "delete": 0x2E, "del": 0x2E, "insert": 0x2D, "ins": 0x2D,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pgup": 0x21, "pagedown": 0x22, "pgdn": 0x22,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28, "pause": 0x13,
    "printscreen": 0x2C, "prtsc": 0x2C, "scrolllock": 0x91, "capslock": 0x14,
    "`": 0xC0, "-": 0xBD, "=": 0xBB, "[": 0xDB, "]": 0xDD, "\\": 0xDC, ";": 0xBA, "'": 0xDE,
    ",": 0xBC, ".": 0xBE, "/": 0xBF,
}
_KEYS.update({chr(c): c for c in range(ord("0"), ord("9") + 1)})
_KEYS.update({chr(c).lower(): c for c in range(ord("A"), ord("Z") + 1)})
_KEYS.update({f"f{i}": 0x70 + i - 1 for i in range(1, 25)})
_KEYS.update({f"num{i}": 0x60 + i for i in range(10)})
_KEYS.update({f"numpad{i}": 0x60 + i for i in range(10)})
# Canonical spelling of each key for normalize().
_ALIASES = {"return": "enter", "escape": "esc", "del": "delete", "ins": "insert", "pgup": "pageup",
            "pgdn": "pagedown", "prtsc": "printscreen"}
_ALIASES.update({f"numpad{i}": f"num{i}" for i in range(10)})


def parse(spec: str) -> tuple[int, int]:
    """'ctrl+alt+space' -> (MOD_CONTROL | MOD_ALT, VK_SPACE). ValueError if invalid."""
    if not spec or not str(spec).strip():
        raise ValueError("empty hotkey")
    parts = [p.strip().lower() for p in str(spec).replace(" ", "").split("+")]
    # "ctrl++" means ctrl and the plus key; we don't support "+" itself.
    if any(not p for p in parts):
        raise ValueError(f"bad hotkey {spec!r}")
    mods, key = 0, None
    for p in parts:
        if p in _MODS:
            mods |= _MODS[p]
        elif p in _KEYS:
            if key is not None:
                raise ValueError(f"hotkey {spec!r} has two keys")
            key = _KEYS[p]
        else:
            raise ValueError(f"unknown key {p!r} in hotkey {spec!r}")
    if key is None:
        raise ValueError(f"hotkey {spec!r} has no key, only modifiers")
    return mods, key


def normalize(spec: str) -> str:
    """Canonical text: 'Alt + CTRL+Space' -> 'ctrl+alt+space'. ValueError if invalid."""
    mods, _ = parse(spec)
    key = next(p for p in (x.strip().lower() for x in str(spec).replace(" ", "").split("+")) if p not in _MODS)
    key = _ALIASES.get(key, key)
    return "+".join([name for bit, name in _MOD_ORDER if mods & bit] + [key])


# --- registration -------------------------------------------------------------
log = logging.getLogger(__name__)

WM_QUIT, WM_HOTKEY, WM_APP = 0x0012, 0x0312, 0x8000
PM_NOREMOVE = 0x0000
ERROR_HOTKEY_ALREADY_REGISTERED = 1409

# Private DLL handles so our argtypes never clash with other modules' ctypes use.
_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_LPMSG = ctypes.POINTER(wintypes.MSG)

_user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
_user32.RegisterHotKey.restype = wintypes.BOOL
_user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.UnregisterHotKey.restype = wintypes.BOOL
_user32.GetMessageW.argtypes = [_LPMSG, wintypes.HWND, wintypes.UINT, wintypes.UINT]
_user32.GetMessageW.restype = wintypes.BOOL
_user32.PeekMessageW.argtypes = [_LPMSG, wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
_user32.PeekMessageW.restype = wintypes.BOOL
_user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_user32.PostThreadMessageW.restype = wintypes.BOOL
_kernel32.GetCurrentThreadId.argtypes = []
_kernel32.GetCurrentThreadId.restype = wintypes.DWORD


class GlobalHotkey:
    """Registers one system-wide hotkey on its own thread (RegisterHotKey(NULL, id, mods|MOD_NOREPEAT, vk)
    + a GetMessageW loop). Calls on_press() from that thread on each WM_HOTKEY.

    on_press runs on the hotkey thread, not the Qt main thread: emit a signal from it
    (queued connection) rather than touching widgets directly.
    """

    _TIMEOUT = 3.0

    def __init__(self, on_press: Callable[[], None]):
        self.on_press = on_press
        self.spec: str | None = None        # normalized spec currently registered, or None
        self._lock = threading.Lock()       # serializes set()/close() callers
        self._ready = threading.Event()
        self._done = threading.Event()
        self._request: tuple[int, int] | None = None   # (mods, vk) or None = unregister
        self._result: str | None = None
        self._tid = 0
        self._ids = (0xB001, 0xB002)        # alternate ids so a failed re-register keeps the old key
        self._cur_id: int | None = None
        self._cur_key: tuple[int, int] | None = None
        self._closed = False
        self._thread = threading.Thread(target=self._run, name="hotkey", daemon=True)
        self._thread.start()
        if not self._ready.wait(self._TIMEOUT):
            raise RuntimeError("hotkey thread did not start")

    # -- public ----------------------------------------------------------------
    def set(self, spec: str | None) -> str | None:
        """(Re)register. Empty/None = unregister. Returns None on success, else an error string.
        If registering a new key fails, the previous one stays registered."""
        if spec is None or not str(spec).strip():
            req, norm = None, None
        else:
            try:
                req, norm = parse(spec), normalize(spec)
            except ValueError as e:
                return str(e)
        with self._lock:
            if self._closed:
                return "hotkey listener is closed"
            self._request = req
            self._result = None
            self._done.clear()
            if not _user32.PostThreadMessageW(self._tid, WM_APP, 0, 0):
                return f"could not reach hotkey thread (error {ctypes.get_last_error()})"
            if not self._done.wait(self._TIMEOUT):
                return "hotkey thread did not respond"
            err = self._result
            if err is None:
                self.spec = norm
            elif norm:
                err = f"{norm}: {err}"
            return err

    def close(self):
        """Unregister, post WM_QUIT, join. Safe to call twice."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._thread.is_alive():
                _user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
                self._thread.join(self._TIMEOUT)
            self.spec = None

    # -- hotkey thread -----------------------------------------------------------
    def _run(self):
        msg = wintypes.MSG()
        self._tid = _kernel32.GetCurrentThreadId()
        _user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_NOREMOVE)   # creates the message queue
        self._ready.set()
        try:
            while True:
                r = _user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if r == 0 or r == -1:       # WM_QUIT or error
                    if r == -1:
                        log.error("hotkey GetMessageW failed: %s", ctypes.get_last_error())
                    break
                if msg.message == WM_HOTKEY:
                    if self._cur_id is not None and msg.wParam == self._cur_id:
                        try:
                            self.on_press()
                        except Exception:
                            log.exception("hotkey callback failed")
                elif msg.message == WM_APP:
                    self._result = self._apply(self._request)
                    self._done.set()
        finally:
            self._apply(None)

    def _apply(self, req: tuple[int, int] | None) -> str | None:
        if req is None:
            if self._cur_id is not None:
                _user32.UnregisterHotKey(None, self._cur_id)
            self._cur_id = self._cur_key = None
            return None
        if req == self._cur_key:
            return None
        mods, vk = req
        new_id = self._ids[1] if self._cur_id == self._ids[0] else self._ids[0]
        if not _user32.RegisterHotKey(None, new_id, mods | MOD_NOREPEAT, vk):
            code = ctypes.get_last_error()
            if code == ERROR_HOTKEY_ALREADY_REGISTERED:
                return "already in use by another app"
            return f"RegisterHotKey failed (error {code})"
        if self._cur_id is not None:
            _user32.UnregisterHotKey(None, self._cur_id)
        self._cur_id, self._cur_key = new_id, req
        return None
