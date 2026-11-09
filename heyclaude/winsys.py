"""Small Windows helpers: start-with-Windows shortcut, single instance, process check.

- Autostart: a ``hey-claude.lnk`` in the user's Startup folder that runs
  ``pythonw.exe -m heyclaude`` from the repo folder. ``folder=`` overrides the
  target folder so tests never touch the real Startup folder.
- SingleInstance: a named Win32 mutex decides who is first; the first instance
  also runs a QLocalServer (same key) so later launches can "poke" it
  (e.g. ``"show"``). The QLocalServer / QLocalSocket parts need a
  QCoreApplication (or QApplication) to exist, and the ``poked`` signal is only
  delivered while its event loop runs.
- process_running: Toolhelp snapshot of process image names, no psutil, no
  subprocess. Costs a few ms per call.
"""
from __future__ import annotations

import ctypes
import hashlib
import os
import sys
from ctypes import wintypes
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from .config import ROOT

SHORTCUT_NAME = "hey-claude.lnk"
SHORTCUT_DESCRIPTION = "hey-claude voice assistant"

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_shell32 = ctypes.WinDLL("shell32")
_ole32 = ctypes.WinDLL("ole32")

ERROR_ALREADY_EXISTS = 183
TH32CS_SNAPPROCESS = 0x00000002
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


# ---------------------------------------------------------------- autostart

class _GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    @classmethod
    def from_str(cls, s: str) -> "_GUID":
        h = s.strip("{}").replace("-", "")
        g = cls()
        g.Data1 = int(h[0:8], 16)
        g.Data2 = int(h[8:12], 16)
        g.Data3 = int(h[12:16], 16)
        for i in range(8):
            g.Data4[i] = int(h[16 + 2 * i:18 + 2 * i], 16)
        return g


_FOLDERID_Startup = _GUID.from_str("{B97D20BB-F46A-4C97-BA10-5E3608430854}")

_shell32.SHGetKnownFolderPath.argtypes = [ctypes.POINTER(_GUID), wintypes.DWORD,
                                          wintypes.HANDLE, ctypes.POINTER(ctypes.c_wchar_p)]
_shell32.SHGetKnownFolderPath.restype = ctypes.c_long
_ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
_ole32.CoTaskMemFree.restype = None


def startup_dir() -> Path:
    """The current user's Startup folder (FOLDERID_Startup), APPDATA fallback."""
    p = ctypes.c_wchar_p()
    try:
        hr = _shell32.SHGetKnownFolderPath(ctypes.byref(_FOLDERID_Startup), 0, None, ctypes.byref(p))
        if hr == 0 and p.value:
            return Path(p.value)
    except OSError:
        pass
    finally:
        if p:
            _ole32.CoTaskMemFree(p)
    appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def shortcut_path(folder: str | os.PathLike | None = None) -> Path:
    return Path(folder if folder is not None else startup_dir()) / SHORTCUT_NAME


def launch_command() -> tuple[str, list[str], str]:
    """(interpreter, args, workdir) used by the shortcut: windowless pythonw -m heyclaude."""
    exe = Path(sys.executable)
    if exe.name.lower() != "pythonw.exe":
        w = exe.with_name("pythonw.exe")
        if w.exists():
            exe = w
    return str(exe), ["-m", "heyclaude"], str(ROOT)


def _ensure_com() -> None:
    """CoInitialize the calling thread (any thread). Deliberately never
    CoUninitialize: tearing COM down under pywin32/Qt objects that still hold
    interfaces breaks them; leaving a thread initialised is harmless."""
    import pythoncom
    try:
        pythoncom.CoInitialize()
    except pythoncom.com_error:  # already initialised in another apartment mode: usable as-is
        pass


def autostart_enabled(folder: str | os.PathLike | None = None) -> bool:
    return shortcut_path(folder).is_file()


def set_autostart(on: bool, folder: str | os.PathLike | None = None) -> Path:
    """Create/overwrite (on) or delete (off) the Startup shortcut. Idempotent. Returns its path."""
    path = shortcut_path(folder)
    if not on:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return path
    import win32com.client
    exe, args, workdir = launch_command()
    path.parent.mkdir(parents=True, exist_ok=True)
    _ensure_com()
    shell = win32com.client.Dispatch("WScript.Shell")
    sc = shell.CreateShortcut(str(path))
    sc.TargetPath = exe
    sc.Arguments = " ".join(args)
    sc.WorkingDirectory = workdir
    sc.Description = SHORTCUT_DESCRIPTION
    sc.WindowStyle = 7  # minimised (pythonw has no window anyway)
    sc.IconLocation = f"{exe},0"
    sc.Save()
    del sc, shell
    return path


# ---------------------------------------------------------- single instance

_kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
_kernel32.CreateMutexW.restype = wintypes.HANDLE
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.CloseHandle.restype = wintypes.BOOL


def default_instance_key() -> str:
    """Per-checkout key so separate clones don't block each other."""
    return "hey-claude-" + hashlib.sha1(str(ROOT).lower().encode("utf-8")).hexdigest()[:12]


class SingleInstance(QObject):
    """Named-mutex single-instance guard with a QLocalServer "poke" channel.

    First launch: ``acquire()`` -> True and starts listening; each newline-terminated
    message a later launch sends is emitted as ``poked(msg)``.
    Later launch: ``acquire()`` -> False, then ``poke("show")`` and exit.
    Needs a QCoreApplication/QApplication; ``poked`` fires from its event loop.
    """

    poked = Signal(str)

    def __init__(self, key: str | None = None, parent: QObject | None = None):
        super().__init__(parent)
        self.key = key or default_instance_key()
        self._mutex = None
        self._server: QLocalServer | None = None
        self._bufs: dict[QLocalSocket, bytes] = {}

    def acquire(self) -> bool:
        if self._mutex:
            return True
        h = _kernel32.CreateMutexW(None, False, "hey-claude-mutex-" + self.key)
        err = ctypes.get_last_error()
        if not h:
            raise ctypes.WinError(err)
        if err == ERROR_ALREADY_EXISTS:
            _kernel32.CloseHandle(h)
            return False
        self._mutex = h
        QLocalServer.removeServer(self.key)
        server = QLocalServer(self)
        server.newConnection.connect(self._on_new_connection)
        if not server.listen(self.key):
            # We still own the mutex, so we are the only instance; just no poke channel.
            server.deleteLater()
        else:
            self._server = server
        return True

    def poke(self, msg: str = "show", timeout_ms: int = 1500) -> bool:
        sock = QLocalSocket()
        sock.connectToServer(self.key)
        if not sock.waitForConnected(timeout_ms):
            return False
        try:
            sock.write((msg.replace("\n", " ") + "\n").encode("utf-8"))
            sock.flush()
            ok = sock.bytesToWrite() == 0 or sock.waitForBytesWritten(timeout_ms)
            return bool(ok)
        finally:
            sock.disconnectFromServer()
            if sock.state() != QLocalSocket.LocalSocketState.UnconnectedState:
                sock.waitForDisconnected(200)

    def release(self) -> None:
        if self._server is not None:
            self._server.close()
            self._server.deleteLater()
            self._server = None
        for s in list(self._bufs):
            s.abort()
        self._bufs.clear()
        if self._mutex:
            _kernel32.CloseHandle(self._mutex)
            self._mutex = None

    # -- server side
    def _on_new_connection(self) -> None:
        while self._server is not None and self._server.hasPendingConnections():
            s = self._server.nextPendingConnection()
            self._bufs[s] = b""
            s.readyRead.connect(lambda s=s: self._on_ready_read(s))
            s.disconnected.connect(lambda s=s: self._on_disconnected(s))
            if s.bytesAvailable():
                self._on_ready_read(s)

    def _on_ready_read(self, s: QLocalSocket) -> None:
        buf = self._bufs.get(s, b"") + bytes(s.readAll().data())
        *lines, rest = buf.split(b"\n")
        self._bufs[s] = rest
        for line in lines:
            self._emit(line)

    def _on_disconnected(self, s: QLocalSocket) -> None:
        if s.bytesAvailable():
            self._on_ready_read(s)
        rest = self._bufs.pop(s, b"")
        if rest.strip():
            self._emit(rest)
        s.deleteLater()

    def _emit(self, raw: bytes) -> None:
        msg = raw.decode("utf-8", "replace").strip()
        if msg:
            self.poked.emit(msg)


# ------------------------------------------------------------ process check

class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


_kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
_kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
_kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
_kernel32.Process32FirstW.restype = wintypes.BOOL
_kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
_kernel32.Process32NextW.restype = wintypes.BOOL


def process_running(exe_name: str) -> bool:
    """True if any process's image name equals exe_name (case-insensitive)."""
    target = exe_name.casefold()
    snap = _kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == INVALID_HANDLE_VALUE:
        return False
    try:
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
        ok = _kernel32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            if entry.szExeFile.casefold() == target:
                return True
            ok = _kernel32.Process32NextW(snap, ctypes.byref(entry))
        return False
    finally:
        _kernel32.CloseHandle(snap)


# --- foreground window (for the hotkey's input field) ---------------------------
_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.GetForegroundWindow.restype = wintypes.HWND
_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_user32.GetWindowThreadProcessId.restype = wintypes.DWORD
_user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
_user32.AttachThreadInput.restype = wintypes.BOOL
_user32.SetForegroundWindow.argtypes = [wintypes.HWND]
_user32.SetForegroundWindow.restype = wintypes.BOOL
_user32.BringWindowToTop.argtypes = [wintypes.HWND]
_user32.SetFocus.argtypes = [wintypes.HWND]
_user32.SetActiveWindow.argtypes = [wintypes.HWND]
_user32.IsWindow.argtypes = [wintypes.HWND]
_user32.IsWindow.restype = wintypes.BOOL
_kernel32.GetCurrentThreadId.restype = wintypes.DWORD


def foreground_window() -> int:
    return int(_user32.GetForegroundWindow() or 0)


def force_foreground(hwnd: int) -> bool:
    """Bring our window to the front with keyboard focus, even though another
    app is in front. Windows' foreground lock refuses a plain
    SetForegroundWindow from a background process; attaching our input queue
    to the foreground window's thread for the call is the usual way round it
    (we were just sent a hotkey, so the user asked for this)."""
    if not hwnd:
        return False
    fg = _user32.GetForegroundWindow()
    if fg and int(fg) == int(hwnd):
        return True
    me = _kernel32.GetCurrentThreadId()
    fg_tid = _user32.GetWindowThreadProcessId(fg, None) if fg else 0
    attached = bool(fg_tid and fg_tid != me and _user32.AttachThreadInput(fg_tid, me, True))
    try:
        _user32.BringWindowToTop(hwnd)
        ok = bool(_user32.SetForegroundWindow(hwnd))
        _user32.SetActiveWindow(hwnd)
        _user32.SetFocus(hwnd)
    finally:
        if attached:
            _user32.AttachThreadInput(fg_tid, me, False)
    return ok or foreground_window() == int(hwnd)


def restore_foreground(hwnd: int) -> bool:
    """Give focus back to the window that had it before the input field opened."""
    if not hwnd or not _user32.IsWindow(hwnd):
        return False
    return force_foreground(hwnd)


def foreground_is_ours() -> bool:
    """Is the foreground window one of this process's windows?"""
    fg = _user32.GetForegroundWindow()
    if not fg:
        return False
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(fg, ctypes.byref(pid))
    return pid.value == os.getpid()


def window_title(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    ctypes.windll.user32.GetWindowTextW(wintypes.HWND(hwnd), buf, 256)
    return buf.value
