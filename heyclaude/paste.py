"""Dictate anywhere (M4d): type text into whatever window has focus.

The text goes on the clipboard, Ctrl+V goes to the focused window, and the
user's previous clipboard comes back once the target has had time to read it.
Our temporary text and the restore are both marked so Windows clipboard history
(Win+V) and the cloud clipboard skip them.

    from heyclaude.paste import paste_text
    r = paste_text("hello")          # PasteResult(ok, reason, app, ms, ...)

Everything heavy (pywin32, comtypes) is imported lazily so the module imports
anywhere; all Windows calls are injectable so the logic is testable offline.
"""
from __future__ import annotations

import ctypes
import os
import struct
import sys
import threading
import time
from dataclasses import dataclass, field

__all__ = [
    "Clipboard", "ClipboardBusy", "PasteResult", "paste_text", "send_paste",
    "plan_paste_keys", "focused_is_password", "foreground_app",
]

# --------------------------------------------------------------------------- clipboard

CF_TEXT, CF_BITMAP, CF_METAFILEPICT, CF_PALETTE = 1, 2, 3, 9
CF_UNICODETEXT, CF_ENHMETAFILE = 13, 14
CF_OWNERDISPLAY, CF_DSPBITMAP, CF_DSPMETAFILEPICT, CF_DSPENHMETAFILE = 0x80, 0x82, 0x83, 0x8E
# Formats whose data is a GDI/other handle, not an HGLOBAL we can copy as bytes.
# CF_DIB / CF_DIBV5 carry the same picture as CF_BITMAP, so nothing is lost.
_HANDLE_FORMATS = frozenset({CF_BITMAP, CF_METAFILEPICT, CF_PALETTE, CF_ENHMETAFILE,
                             CF_OWNERDISPLAY, CF_DSPBITMAP, CF_DSPMETAFILEPICT,
                             CF_DSPENHMETAFILE})


def _is_handle_format(fmt: int) -> bool:
    # 0x200-0x2FF private and 0x300-0x3FF GDI-object formats aren't HGLOBALs either.
    return fmt in _HANDLE_FORMATS or 0x200 <= fmt <= 0x3FF


EXCLUDE_FORMAT = "ExcludeClipboardContentFromMonitorProcessing"
HISTORY_FORMAT = "CanIncludeInClipboardHistory"
CLOUD_FORMAT = "CanUploadToCloudClipboard"
_DWORD0 = struct.pack("<I", 0)

GMEM_MOVEABLE = 0x0002


class ClipboardBusy(OSError):
    """Another process kept the clipboard open through all our retries."""


class Clipboard:
    """Snapshot / restore / set the system clipboard (win32clipboard + a bit of ctypes).

    Every write we make (our temporary text and the restore) carries the
    "don't record me" formats, so clipboard history and cloud sync ignore them.
    """

    def __init__(self, retries: int = 10, retry_delay_s: float = 0.015,
                 max_bytes: int = 64 * 1024 * 1024):
        self.retries = retries
        self.retry_delay_s = retry_delay_s
        self.max_bytes = max_bytes          # skip giant formats (a 4K screenshot DIB is ~33 MB)
        self._marker_ids: dict[int, bytes] | None = None

    # ---- low level
    @staticmethod
    def _wc():
        import win32clipboard
        return win32clipboard

    def _open(self):
        wc = self._wc()
        last = None
        for i in range(max(1, self.retries)):
            try:
                wc.OpenClipboard(None)
                return wc
            except Exception as e:          # pywintypes.error: access denied, someone holds it
                last = e
                time.sleep(self.retry_delay_s * (1 + i // 3))
        raise ClipboardBusy(f"clipboard is held by another app: {last}")

    def markers(self) -> dict[int, bytes]:
        """{registered format id: data} for the no-history / no-cloud markers."""
        if self._marker_ids is None:
            wc = self._wc()
            self._marker_ids = {
                wc.RegisterClipboardFormat(EXCLUDE_FORMAT): b"\0\0\0\0",
                wc.RegisterClipboardFormat(HISTORY_FORMAT): _DWORD0,
                wc.RegisterClipboardFormat(CLOUD_FORMAT): _DWORD0,
            }
        return self._marker_ids

    @staticmethod
    def _put(wc, fmt: int, data: bytes) -> None:
        """SetClipboardData with an exact-size HGLOBAL (no extra terminator added)."""
        k32 = _kernel32()
        h = k32.GlobalAlloc(GMEM_MOVEABLE, max(1, len(data)))
        if not h:
            raise MemoryError("GlobalAlloc failed")
        p = k32.GlobalLock(h)
        if not p:
            k32.GlobalFree(h)
            raise MemoryError("GlobalLock failed")
        try:
            ctypes.memmove(p, data, len(data))
        finally:
            k32.GlobalUnlock(h)
        try:
            wc.SetClipboardData(fmt, h)     # the system owns h from here on
        except Exception:
            k32.GlobalFree(h)
            raise

    # ---- public
    def sequence_number(self) -> int:
        return int(_user32().GetClipboardSequenceNumber())

    def snapshot(self) -> list[tuple[int, bytes]]:
        """Every format we can carry as bytes, in the clipboard's own order."""
        wc = self._open()
        out: list[tuple[int, bytes]] = []
        try:
            fmt = 0
            while True:
                fmt = wc.EnumClipboardFormats(fmt)
                if not fmt:
                    break
                if _is_handle_format(fmt):
                    continue
                try:
                    data = self._read(wc, fmt)
                except Exception:
                    continue                # a format that won't render: skip it
                if data is not None:
                    out.append((fmt, data))
        finally:
            wc.CloseClipboard()
        return out

    def _read(self, wc, fmt: int) -> bytes | None:
        """The raw HGLOBAL bytes of one format (clipboard open), None if absent or too big."""
        h = wc.GetClipboardDataHandle(fmt)
        if not h:
            return None
        size = _kernel32().GlobalSize(h)
        if not size or size > self.max_bytes:
            return None
        data = wc.GetGlobalMemory(h)
        if isinstance(data, str):
            data = data.encode("utf-16-le")
        return bytes(data)

    def restore(self, snapshot: list[tuple[int, bytes]]) -> None:
        wc = self._open()
        try:
            wc.EmptyClipboard()
            if not snapshot:
                return                      # it was empty; an empty clipboard has no history entry
            markers = self.markers()
            for fmt, data in snapshot:
                if fmt in markers:
                    continue                # ours win
                try:
                    self._put(wc, fmt, data)
                except Exception:
                    continue
            for fmt, data in markers.items():
                self._put(wc, fmt, data)
        finally:
            wc.CloseClipboard()

    def set_text(self, text: str) -> int:
        """Put `text` (CF_UNICODETEXT) on the clipboard; returns the sequence number after."""
        wc = self._open()
        try:
            wc.EmptyClipboard()
            for fmt, data in self.markers().items():
                self._put(wc, fmt, data)     # markers first: monitors see them with the text
            self._put(wc, CF_UNICODETEXT, (text + "\0").encode("utf-16-le"))
        finally:
            wc.CloseClipboard()
        return self.sequence_number()

    def get_text(self) -> str | None:
        wc = self._open()
        try:
            if not wc.IsClipboardFormatAvailable(CF_UNICODETEXT):
                return None
            return wc.GetClipboardData(CF_UNICODETEXT)
        finally:
            wc.CloseClipboard()


# --------------------------------------------------------------------------- ctypes plumbing

_dlls: dict[str, object] = {}


def _user32():
    u = _dlls.get("user32")
    if u is None:
        from ctypes import wintypes as w
        u = ctypes.WinDLL("user32", use_last_error=True)
        u.GetClipboardSequenceNumber.restype = w.DWORD
        u.GetAsyncKeyState.argtypes = [ctypes.c_int]
        u.GetAsyncKeyState.restype = ctypes.c_short
        u.MapVirtualKeyW.argtypes = [ctypes.c_uint, ctypes.c_uint]
        u.MapVirtualKeyW.restype = ctypes.c_uint
        u.SendInput.argtypes = [ctypes.c_uint, ctypes.c_void_p, ctypes.c_int]
        u.SendInput.restype = ctypes.c_uint
        u.GetForegroundWindow.restype = w.HWND
        u.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
        u.GetWindowThreadProcessId.restype = w.DWORD
        u.EnumChildWindows.argtypes = [w.HWND, ctypes.c_void_p, w.LPARAM]
        u.EnumChildWindows.restype = w.BOOL
        _dlls["user32"] = u
    return u


def _kernel32():
    k = _dlls.get("kernel32")
    if k is None:
        from ctypes import wintypes as w
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
        k.GlobalAlloc.restype = ctypes.c_void_p
        k.GlobalLock.argtypes = [ctypes.c_void_p]
        k.GlobalLock.restype = ctypes.c_void_p
        k.GlobalUnlock.argtypes = [ctypes.c_void_p]
        k.GlobalUnlock.restype = w.BOOL
        k.GlobalFree.argtypes = [ctypes.c_void_p]
        k.GlobalFree.restype = ctypes.c_void_p
        k.GlobalSize.argtypes = [ctypes.c_void_p]
        k.GlobalSize.restype = ctypes.c_size_t
        k.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        k.OpenProcess.restype = w.HANDLE
        k.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD, w.LPWSTR,
                                                 ctypes.POINTER(w.DWORD)]
        k.QueryFullProcessImageNameW.restype = w.BOOL
        k.CloseHandle.argtypes = [w.HANDLE]
        k.CloseHandle.restype = w.BOOL
        _dlls["kernel32"] = k
    return k


# --------------------------------------------------------------------------- keystrokes

VK_SHIFT, VK_CONTROL, VK_MENU = 0x10, 0x11, 0x12
VK_LSHIFT, VK_RSHIFT, VK_LCONTROL, VK_RCONTROL = 0xA0, 0xA1, 0xA2, 0xA3
VK_LMENU, VK_RMENU, VK_LWIN, VK_RWIN = 0xA4, 0xA5, 0x5B, 0x5C
VK_V = 0x56
VK_MASK = 0xE8          # unassigned VK: tapped before releasing Alt/Win so the lone
                        # key-up doesn't open the menu bar / Start menu (AutoHotkey's trick)

KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP = 0x0001, 0x0002

# (vk, name, extended) in the order we release them: Alt and Win first.
_MODIFIERS = (
    (VK_LMENU, "lalt", False), (VK_RMENU, "ralt", True),
    (VK_LWIN, "lwin", True), (VK_RWIN, "rwin", True),
    (VK_LSHIFT, "lshift", False), (VK_RSHIFT, "rshift", False),
    (VK_LCONTROL, "lctrl", False), (VK_RCONTROL, "rctrl", True),
)
_EXTENDED = {vk for vk, _n, ext in _MODIFIERS if ext}


def plan_paste_keys(get_async_key_state) -> tuple[list[tuple[int, bool]], list[str]]:
    """The key events for one Ctrl+V, given a GetAsyncKeyState-like function.

    Returns ([(vk, key_up), ...], [names of modifiers we release first]).
    Modifiers still down (the user's hold key, a stuck Alt) are released before
    the paste so the target sees plain Ctrl+V, not Alt+Ctrl+V or Ctrl+Shift+V.
    """
    down = [(vk, name) for vk, name, _e in _MODIFIERS
            if get_async_key_state(vk) & 0x8000]
    events: list[tuple[int, bool]] = []
    released: list[str] = []
    if any(vk in (VK_LMENU, VK_RMENU, VK_LWIN, VK_RWIN) for vk, _n in down):
        events += [(VK_MASK, False), (VK_MASK, True)]
    for vk, name in down:
        events.append((vk, True))
        released.append(name)
    events += [(VK_CONTROL, False), (VK_V, False), (VK_V, True), (VK_CONTROL, True)]
    return events, released


def _send_input(events: list[tuple[int, bool]]) -> int:
    """SendInput the events as keyboard input (virtual key + scan code); returns how many went in."""
    from ctypes import wintypes as w
    ULONG_PTR = ctypes.c_size_t

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", w.WORD), ("wScan", w.WORD), ("dwFlags", w.DWORD),
                    ("time", w.DWORD), ("dwExtraInfo", ULONG_PTR)]

    class MOUSEINPUT(ctypes.Structure):        # only here so the union has the right size
        _fields_ = [("dx", w.LONG), ("dy", w.LONG), ("mouseData", w.DWORD),
                    ("dwFlags", w.DWORD), ("time", w.DWORD), ("dwExtraInfo", ULONG_PTR)]

    class _U(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", w.DWORD), ("u", _U)]

    u = _user32()
    arr = (INPUT * len(events))()
    for i, (vk, up) in enumerate(events):
        scan = u.MapVirtualKeyW(vk, 0) & 0xFF     # MAPVK_VK_TO_VSC
        flags = (KEYEVENTF_KEYUP if up else 0) | (KEYEVENTF_EXTENDEDKEY if vk in _EXTENDED else 0)
        arr[i].type = 1                            # INPUT_KEYBOARD
        arr[i].u.ki = KEYBDINPUT(vk, scan, flags, 0, 0)
    return int(u.SendInput(len(events), arr, ctypes.sizeof(INPUT)))


def send_paste(get_async_key_state=None, send_input=None) -> list[str]:
    """Send Ctrl+V to the focused window; returns the modifiers released first.

    Raises OSError if SendInput inserted fewer events than asked (input blocked).
    Note: SendInput into an elevated window from a non-elevated process is
    silently dropped by UIPI, with no error.
    """
    gaks = get_async_key_state or _user32().GetAsyncKeyState
    events, released = plan_paste_keys(gaks)
    n = (send_input or _send_input)(events)
    if n != len(events):
        raise OSError(f"SendInput inserted {n} of {len(events)} key events")
    return released


# --------------------------------------------------------------------------- focus info

_tls = threading.local()


def _automation():
    """The per-thread IUIAutomation object (COM initialised on this thread)."""
    uia = getattr(_tls, "uia", None)
    if uia is not None:
        return uia
    import comtypes
    import comtypes.client
    if not getattr(_tls, "com", False):
        try:
            comtypes.CoInitializeEx(getattr(sys, "coinit_flags", comtypes.COINIT_APARTMENTTHREADED))
        except OSError:
            pass                            # already initialised in another mode: fine
        _tls.com = True
    mod = comtypes.client.GetModule("UIAutomationCore.dll")
    uia = None
    try:                                    # IUIAutomation2 lets us cap a hung target's stall
        uia = comtypes.client.CreateObject(mod.CUIAutomation8, interface=mod.IUIAutomation2)
        uia.ConnectionTimeout = 500
        uia.TransactionTimeout = 500
    except Exception:
        uia = comtypes.client.CreateObject(mod.CUIAutomation, interface=mod.IUIAutomation)
    _tls.uia = uia
    return uia


def focused_is_password() -> bool | None:
    """True if the keyboard focus is a password box; None if UIA can't tell.

    COM is initialised on the calling thread on first use and the automation
    object is cached per thread. The first call per process also generates the
    comtypes wrapper (slow once); afterwards it's a few ms.
    """
    try:
        el = _automation().GetFocusedElement()
        if not el:
            return None
        return bool(el.CurrentIsPassword)
    except Exception:
        return None


PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def _exe_of_pid(pid: int) -> str:
    from ctypes import wintypes as w
    k = _kernel32()
    h = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        n = w.DWORD(len(buf))
        if not k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
            return ""
        return os.path.basename(buf.value).lower()
    finally:
        k.CloseHandle(h)


def _pid_of(hwnd) -> int:
    from ctypes import wintypes as w
    pid = w.DWORD(0)
    _user32().GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def foreground_app() -> str:
    """The foreground window's exe name, lowercased ("notepad.exe"); "" if unknown.

    UWP windows are hosted by ApplicationFrameHost.exe; for those we report the
    app inside (the child window owned by another process).
    """
    try:
        from ctypes import wintypes as w
        u = _user32()
        hwnd = u.GetForegroundWindow()
        if not hwnd:
            return ""
        pid = _pid_of(hwnd)
        exe = _exe_of_pid(pid)
        if exe == "applicationframehost.exe":
            found: list[int] = []
            proto = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)

            def cb(child, _lp):
                cpid = _pid_of(child)
                if cpid and cpid != pid:
                    found.append(cpid)
                    return False
                return True

            u.EnumChildWindows(hwnd, proto(cb), 0)
            if found:
                exe = _exe_of_pid(found[0]) or exe
        return exe
    except Exception:
        return ""


# --------------------------------------------------------------------------- paste

_PASTE_LOCK = threading.Lock()


@dataclass
class PasteResult:
    ok: bool
    reason: str
    app: str
    ms: float
    released: list[str] = field(default_factory=list)   # modifiers let go before Ctrl+V
    restored: bool = False                               # previous clipboard put back
    sent_at: float | None = None                         # perf_counter when Ctrl+V went out (the text appears)


def paste_text(text: str, restore_delay_s: float = 0.25, clipboard=None, send=None,
               is_password=None, sleep=time.sleep, foreground=None,
               settle_s: float = 0.02) -> PasteResult:
    """Type `text` into the focused window via clipboard + Ctrl+V, then restore the clipboard.

    - Refuses (ok=False, reason "password field") when the focus is a password box.
    - The previous clipboard comes back after `restore_delay_s` (the target reads
      the clipboard asynchronously; terminals are the slow ones), but only if the
      clipboard still holds our text: if anything replaced it meanwhile, we leave it.
    - The restore runs even if sending the keystroke failed.
    - Pastes are serialised by a module-wide lock.

    `clipboard` needs snapshot()/restore(snap)/set_text(text)->seq/sequence_number();
    `send` is a callable returning the released modifier names (send_paste).
    """
    clipboard = clipboard if clipboard is not None else Clipboard()
    send = send or send_paste
    is_password = is_password or focused_is_password
    foreground = foreground or foreground_app
    t0 = time.perf_counter()

    def done(ok, reason, **kw):
        return PasteResult(ok, reason, app, round((time.perf_counter() - t0) * 1000, 1), **kw)

    with _PASTE_LOCK:
        try:
            app = foreground() or ""
        except Exception:
            app = ""
        if not text:
            return done(False, "nothing to paste")
        try:
            pw = is_password()
        except Exception:
            pw = None
        if pw is True:
            return done(False, "password field")

        try:
            snap = clipboard.snapshot()
            seq_before = clipboard.sequence_number()
        except Exception as e:
            return done(False, f"clipboard unavailable: {e}")

        ok, reason, released, our_seq, sent_at = False, "", [], None, None
        restored = False
        try:
            our_seq = clipboard.set_text(text)
            sleep(settle_s)
            released = list(send() or [])
            sent_at = time.perf_counter()
            sleep(restore_delay_s)
            ok, reason = True, "pasted"
        except Exception as e:
            reason = f"{'paste' if our_seq is not None else 'set clipboard'} failed: {e}"
        finally:
            try:
                now = clipboard.sequence_number()
                if our_seq is not None:
                    mine = now == our_seq
                else:                        # set_text failed part-way: did it touch anything?
                    mine = now != seq_before
                if mine:
                    clipboard.restore(snap)
                    restored = True
                elif our_seq is not None:
                    reason += "; clipboard changed meanwhile, left as is"
            except Exception as e:
                reason += f"; restore failed: {e}"
        return done(ok, reason, released=released, restored=restored, sent_at=sent_at)
