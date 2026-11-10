"""Jump to a Claude Code session's window, or hand a session to the desktop app.

- ``session_window(pid, cwd)``: walk up the parents of ``pid`` (the hook
  relay) and return the nearest ancestor's top-level window. A process that
  owns no window of its own may still own a hidden, *owned* window whose root
  owner is the real one; that is how terminals show up:

  - Windows Terminal (also as the default terminal, where the shell's parent
    is explorer and WindowsTerminal.exe is not in the chain at all): the
    console client (cmd/pwsh) owns a ``PseudoConsoleWindow`` that Windows
    reports under the client's pid, owned by the WT window. Started from WT
    itself, the chain also simply goes through WindowsTerminal.exe.
  - Classic conhost: the console window is reported under the client's pid
    too; a ``conhost.exe`` / ``OpenConsole.exe`` child of a chain process is
    checked as well.
  - VS Code: the shell's parents are Code.exe processes; the main one owns
    the windows, and the one whose title has the folder name wins.

  The walk stops at system/shell processes (explorer, svchost, ...), at our
  own process, at a loop, and at a parent younger than its child (pid reuse).
- ``raise_window(hwnd)``: restore + AttachThreadInput + SetForegroundWindow,
  with the Alt-key trick as a fallback (the island never takes focus, so
  Windows' foreground lock otherwise refuses).
- ``jump(info)``: desktop session -> open its ``claude://`` link; window ->
  raise it; else ``code <cwd>``; else nothing.
- ``handoff_to_desktop(...)``: ``claude --desktop --resume <id>`` in its own
  minimized console (it refuses redirected output).

All Win32 goes through ctypes with explicit argtypes/restype; nothing here
raises: failures come back as None / False / "none".
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
from typing import NamedTuple

log = logging.getLogger(__name__)

MAX_DEPTH = 16
DESKTOP_LINK = "claude://claude.ai/epitaxy/local_{}"

# Never climb into (or past) these: their windows are not the session's.
STOP_EXES = frozenset(x.casefold() for x in (
    "explorer.exe", "svchost.exe", "services.exe", "wininit.exe", "winlogon.exe",
    "csrss.exe", "smss.exe", "System", "[System Process]", "Registry",
    "sihost.exe", "RuntimeBroker.exe", "userinit.exe", "dllhost.exe",
))
CONSOLE_HOSTS = frozenset(("conhost.exe", "openconsole.exe"))
TERMINAL_EXE = "windowsterminal.exe"

SW_RESTORE = 9
SW_SHOWMINNOACTIVE = 7
STARTF_USESHOWWINDOW = 0x00000001
CREATE_NEW_CONSOLE = getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010)
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


class Win(NamedTuple):
    hwnd: int
    pid: int
    title: str
    visible: bool = True
    root_owner: int = 0       # GetAncestor(GA_ROOTOWNER); 0 or == hwnd when unowned


# ------------------------------------------------------------------ Win32

_WIN = sys.platform == "win32"
if _WIN:
    try:
        import ctypes
        from ctypes import wintypes

        _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        _u32 = ctypes.WinDLL("user32", use_last_error=True)

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

        _TH32CS_SNAPPROCESS = 0x00000002
        _INVALID_HANDLE = ctypes.c_void_p(-1).value
        _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        _GW_OWNER = 4
        _GA_ROOTOWNER = 3
        _VK_MENU = 0x12
        _KEYEVENTF_EXTENDEDKEY = 0x0001
        _KEYEVENTF_KEYUP = 0x0002

        _k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        _k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        _k32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
        _k32.Process32FirstW.restype = wintypes.BOOL
        _k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
        _k32.Process32NextW.restype = wintypes.BOOL
        _k32.CloseHandle.argtypes = [wintypes.HANDLE]
        _k32.CloseHandle.restype = wintypes.BOOL
        _k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        _k32.OpenProcess.restype = wintypes.HANDLE
        _k32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        _k32.GetProcessTimes.restype = wintypes.BOOL
        _k32.GetCurrentThreadId.argtypes = []
        _k32.GetCurrentThreadId.restype = wintypes.DWORD

        _WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        _u32.EnumWindows.argtypes = [_WNDENUMPROC, wintypes.LPARAM]
        _u32.EnumWindows.restype = wintypes.BOOL
        _u32.IsWindow.argtypes = [wintypes.HWND]
        _u32.IsWindow.restype = wintypes.BOOL
        _u32.IsWindowVisible.argtypes = [wintypes.HWND]
        _u32.IsWindowVisible.restype = wintypes.BOOL
        _u32.IsIconic.argtypes = [wintypes.HWND]
        _u32.IsIconic.restype = wintypes.BOOL
        _u32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        _u32.GetWindowTextLengthW.restype = ctypes.c_int
        _u32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        _u32.GetWindowTextW.restype = ctypes.c_int
        _u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        _u32.GetWindowThreadProcessId.restype = wintypes.DWORD
        _u32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        _u32.GetAncestor.restype = wintypes.HWND
        _u32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
        _u32.GetWindow.restype = wintypes.HWND
        _u32.GetForegroundWindow.argtypes = []
        _u32.GetForegroundWindow.restype = wintypes.HWND
        _u32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        _u32.ShowWindow.restype = wintypes.BOOL
        _u32.BringWindowToTop.argtypes = [wintypes.HWND]
        _u32.BringWindowToTop.restype = wintypes.BOOL
        _u32.SetForegroundWindow.argtypes = [wintypes.HWND]
        _u32.SetForegroundWindow.restype = wintypes.BOOL
        _u32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
        _u32.AttachThreadInput.restype = wintypes.BOOL
        _u32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_size_t]
        _u32.keybd_event.restype = None
    except Exception:  # pragma: no cover - a broken ctypes setup
        log.exception("jump: Win32 setup failed")
        _WIN = False


def _process_table() -> dict[int, tuple[int, str]]:
    """pid -> (parent pid, exe name) for every process (ToolHelp32 snapshot)."""
    if not _WIN:
        return {}
    out: dict[int, tuple[int, str]] = {}
    try:
        snap = _k32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
        if not snap or snap == _INVALID_HANDLE:
            return {}
        try:
            entry = _PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
            ok = _k32.Process32FirstW(snap, ctypes.byref(entry))
            while ok:
                out[int(entry.th32ProcessID)] = (int(entry.th32ParentProcessID), entry.szExeFile)
                ok = _k32.Process32NextW(snap, ctypes.byref(entry))
        finally:
            _k32.CloseHandle(snap)
    except Exception:
        log.debug("jump: process snapshot failed", exc_info=True)
    return out


def _creation_time(pid: int) -> int | None:
    """Process creation time (FILETIME ticks), or None when it can't be read."""
    if not _WIN or pid <= 4:
        return None
    try:
        h = _k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            return None
        try:
            c, e, k, u = (wintypes.FILETIME() for _ in range(4))
            if not _k32.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(e),
                                        ctypes.byref(k), ctypes.byref(u)):
                return None
            return (c.dwHighDateTime << 32) | c.dwLowDateTime
        finally:
            _k32.CloseHandle(h)
    except Exception:
        return None


def _window_text(hwnd: int) -> str:
    n = _u32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    _u32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def _top_windows() -> list[Win]:
    """Every top-level window, in Z order (topmost first)."""
    if not _WIN:
        return []
    out: list[Win] = []

    def cb(hwnd, _lparam):
        try:
            h = int(hwnd or 0)
            if h:
                pid = wintypes.DWORD()
                _u32.GetWindowThreadProcessId(h, ctypes.byref(pid))
                root = int(_u32.GetAncestor(h, _GA_ROOTOWNER) or 0)
                out.append(Win(h, int(pid.value), _window_text(h),
                               bool(_u32.IsWindowVisible(h)), 0 if root == h else root))
        except Exception:
            pass
        return True

    try:
        _u32.EnumWindows(_WNDENUMPROC(cb), 0)
    except Exception:
        log.debug("jump: EnumWindows failed", exc_info=True)
    return out


# --------------------------------------------------------------- finding

def _ancestors(pid: int, procs: dict[int, tuple[int, str]]) -> list[int]:
    """pid and its parents, nearest first, up to (not including) a stop process."""
    chain: list[int] = []
    seen: set[int] = set()
    me = os.getpid()
    cur = int(pid)
    child_ct = _creation_time(cur)
    while len(chain) < MAX_DEPTH and cur not in seen and cur in procs:
        ppid, exe = procs[cur]
        if exe.casefold() in STOP_EXES or (cur == me and cur != pid):
            break
        chain.append(cur)
        seen.add(cur)
        if not ppid or ppid == cur:
            break
        parent_ct = _creation_time(ppid)
        if child_ct is not None and parent_ct is not None and parent_ct > child_ct:
            break                      # the real parent died and its pid was reused
        cur, child_ct = ppid, parent_ct
    return chain


def _pick(cands: list[Win], folder: str) -> int:
    """The window whose title has the folder name, else the topmost one."""
    if folder:
        for w in cands:
            if folder in w.title.casefold():
                return w.hwnd
    return cands[0].hwnd


def session_window(pid: int, cwd: str = "") -> int | None:
    """The top-level window of the nearest ancestor of ``pid`` that has one."""
    try:
        procs = _process_table()
        if not pid or pid not in procs:
            return None
        chain = _ancestors(pid, procs)
        if not chain:
            return None
        wins = _top_windows()
        by_hwnd = {w.hwnd: w for w in wins}
        mains: dict[int, list[Win]] = {}     # pid -> visible, titled, unowned windows
        owned: dict[int, list[Win]] = {}     # pid -> windows owned by another window
        for w in wins:
            if w.root_owner and w.root_owner != w.hwnd:
                owned.setdefault(w.pid, []).append(w)
            elif w.visible and w.title.strip():
                mains.setdefault(w.pid, []).append(w)
        hosts: dict[int, list[int]] = {}     # pid -> its conhost/OpenConsole children
        for p, (pp, exe) in procs.items():
            if exe.casefold() in CONSOLE_HOSTS:
                hosts.setdefault(pp, []).append(p)
        folder = os.path.basename(os.path.normpath(cwd)).casefold() if cwd else ""
        if folder in (".", os.sep):
            folder = ""

        def via_owner(p: int) -> list[Win]:
            # e.g. the PseudoConsoleWindow of a shell in Windows Terminal,
            # owned by the WT window (another process).
            out: list[Win] = []
            for w in owned.get(p, ()):
                root = by_hwnd.get(w.root_owner)
                if root and root.visible and root.title.strip() and root not in out:
                    out.append(root)
            return out

        for p in chain:
            exe = procs[p][1].casefold()
            if exe == "openconsole.exe":
                # A ConPTY host: its terminal is the parent (WindowsTerminal.exe).
                pp = procs[p][0]
                if procs.get(pp, (0, ""))[1].casefold() == TERMINAL_EXE and mains.get(pp):
                    return _pick(mains[pp], folder)
            cands = mains.get(p) or via_owner(p)
            if not cands:
                for h in hosts.get(p, ()):
                    cands = mains.get(h) or via_owner(h)
                    if cands:
                        break
            if cands:
                return _pick(cands, folder)
        return None
    except Exception:
        log.debug("jump: session_window failed", exc_info=True)
        return None


# --------------------------------------------------------------- raising

def _foreground() -> int:
    return int(_u32.GetForegroundWindow() or 0)


def raise_window(hwnd: int) -> bool:
    """Bring hwnd to the front (restoring it when minimized). True when it ended
    up as the foreground window."""
    if not _WIN or not hwnd:
        return False
    try:
        if not _u32.IsWindow(hwnd):
            return False
        if _u32.IsIconic(hwnd):
            _u32.ShowWindow(hwnd, SW_RESTORE)
        if _foreground() == int(hwnd):
            return True
        fg = _u32.GetForegroundWindow()
        me = _k32.GetCurrentThreadId()
        fg_tid = _u32.GetWindowThreadProcessId(fg, None) if fg else 0
        attached = bool(fg_tid and fg_tid != me and _u32.AttachThreadInput(me, fg_tid, True))
        try:
            _u32.BringWindowToTop(hwnd)
            ok = bool(_u32.SetForegroundWindow(hwnd))
        finally:
            if attached:
                _u32.AttachThreadInput(me, fg_tid, False)
        if not ok or _foreground() != int(hwnd):
            # The foreground lock gives way after a keyboard event of ours.
            _u32.keybd_event(_VK_MENU, 0, _KEYEVENTF_EXTENDEDKEY, 0)
            try:
                _u32.SetForegroundWindow(hwnd)
                _u32.BringWindowToTop(hwnd)
            finally:
                _u32.keybd_event(_VK_MENU, 0, _KEYEVENTF_EXTENDEDKEY | _KEYEVENTF_KEYUP, 0)
        return _foreground() == int(hwnd)
    except Exception:
        log.debug("jump: raise_window failed", exc_info=True)
        return False


# ----------------------------------------------------------- jump / hand-off

def _bare_id(session_id: str) -> str:
    sid = (session_id or "").strip()
    return sid[len("local_"):] if sid.startswith("local_") else sid


def desktop_link(session_id: str) -> str:
    """The Claude desktop app's link to a session: claude://claude.ai/epitaxy/local_<id>."""
    return DESKTOP_LINK.format(_bare_id(session_id))


_CMD_META = re.compile(r'[&|<>^%!"\r\n]')


def _code_command(cwd: str) -> list[str] | None:
    """argv to open cwd in VS Code, or None. Prefers Code.exe next to the
    ``bin/code.cmd`` shim, so the folder never goes through cmd.exe parsing."""
    code = shutil.which("code")
    if not code:
        return None
    if code.lower().endswith((".cmd", ".bat")):
        exe = os.path.join(os.path.dirname(os.path.dirname(code)), "Code.exe")
        if os.path.isfile(exe):
            return [exe, cwd]
        if _CMD_META.search(cwd):
            return None
    return [code, cwd]


def jump(info: dict, *, open_url=getattr(os, "startfile", None), popen=subprocess.Popen) -> str:
    """Show the session: "desktop" | "window" | "vscode" | "none"."""
    try:
        info = info or {}
        sid = str(info.get("session_id") or "")
        cwd = str(info.get("cwd") or "")
        if info.get("desktop") and _bare_id(sid) and open_url:
            try:
                open_url(desktop_link(sid))
                return "desktop"
            except Exception:
                log.debug("jump: opening the desktop link failed", exc_info=True)
        hwnd = info.get("hwnd")
        if hwnd and raise_window(int(hwnd)):
            return "window"
        if cwd and os.path.isdir(cwd):
            argv = _code_command(cwd)
            if argv:
                try:
                    popen(argv, creationflags=NO_WINDOW, stdin=subprocess.DEVNULL,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    return "vscode"
                except Exception:
                    log.debug("jump: starting VS Code failed", exc_info=True)
    except Exception:
        log.debug("jump failed", exc_info=True)
    return "none"


_SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")


def handoff_to_desktop(exe: str, session_id: str, cwd: str, *, popen=subprocess.Popen, env=None):
    """Start ``<exe> --desktop --resume <id>`` in cwd, in its own minimized
    console (it refuses redirected output, so nothing is redirected).
    Returns the Popen, or None when it could not start."""
    try:
        sid = _bare_id(session_id)
        if not exe or not _SESSION_ID.fullmatch(sid):
            log.warning("handoff: bad exe or session id %r", session_id)
            return None
        if env is None:
            from heyclaude.claude_cli import child_env
            env = child_env()
        si = subprocess.STARTUPINFO() if hasattr(subprocess, "STARTUPINFO") else None
        if si is not None:
            si.dwFlags |= STARTF_USESHOWWINDOW
            si.wShowWindow = SW_SHOWMINNOACTIVE
        return popen([exe, "--desktop", "--resume", sid], cwd=cwd or None, env=env,
                     creationflags=CREATE_NEW_CONSOLE, startupinfo=si)
    except Exception:
        log.warning("handoff to the desktop app failed", exc_info=True)
        return None
