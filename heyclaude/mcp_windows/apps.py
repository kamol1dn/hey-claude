"""Apps and windows: launch an installed app by name (only what the Start
menu lists), list / focus / minimize / close windows, snap left or right.

Apps that belong to the "never exposed" list (shells, the registry editor,
security and account tools) can't be launched from here."""
from __future__ import annotations

import difflib
import os
import re

from ._util import ToolError, change, com_init

# Never launched by voice: shells, registry, security, accounts, drivers/services.
BLOCKED_APPS = re.compile(
    r"power ?shell|\bcmd\b|command prompt|terminal|\bwsl\b|bash|regedit|registry|"
    r"defender|windows security|firewall|user account|\buac\b|netplwiz|lusrmgr|computer management|"
    r"device manager|services|task scheduler|group policy|gpedit|secpol|mmc|"
    r"administrative tools|windows tools|system configuration|msconfig|"
    r"credential|local security|recovery|disk management|format", re.I)

SW_MINIMIZE, SW_RESTORE, SW_SHOWMAXIMIZED = 6, 9, 3


# -- thin OS layer -------------------------------------------------------------------
def _os_start_apps() -> list[dict]:
    """[{name, id}] from shell:AppsFolder (what the Start menu shows)."""
    com_init()
    import win32com.client
    shell = win32com.client.Dispatch("Shell.Application")
    folder = shell.NameSpace("shell:AppsFolder")
    return [{"name": it.Name, "id": it.Path} for it in folder.Items()]


def _os_launch(app_id: str) -> None:
    os.startfile("shell:AppsFolder\\" + app_id)


def _os_windows() -> list[dict]:
    import win32gui
    import win32process
    out = []
    fg = win32gui.GetForegroundWindow()

    def visit(h, _):
        if not win32gui.IsWindowVisible(h) or win32gui.GetWindow(h, 4):     # GW_OWNER: skip owned popups
            return True
        title = win32gui.GetWindowText(h)
        if not title.strip() or title in ("Program Manager",):
            return True
        if win32gui.GetWindowLong(h, -20) & 0x80:                         # WS_EX_TOOLWINDOW (islands, tips)
            return True
        _, pid = win32process.GetWindowThreadProcessId(h)
        out.append({"hwnd": h, "title": title, "app": _proc_name(pid), "pid": pid,
                    "minimized": bool(win32gui.IsIconic(h)), "foreground": h == fg})
        return True
    win32gui.EnumWindows(visit, None)
    return out


def _proc_name(pid: int) -> str:
    try:
        import psutil
        return os.path.splitext(psutil.Process(pid).name())[0]
    except Exception:
        return ""


def _os_placement(hwnd: int) -> list:
    import win32gui
    flags, show, pmin, pmax, rect = win32gui.GetWindowPlacement(hwnd)
    return [flags, show, list(pmin), list(pmax), list(rect)]


def _os_set_placement(hwnd: int, pl: list) -> None:
    import win32gui
    win32gui.SetWindowPlacement(hwnd, (pl[0], pl[1], tuple(pl[2]), tuple(pl[3]), tuple(pl[4])))


def _os_show(hwnd: int, cmd: int) -> None:
    import win32gui
    win32gui.ShowWindow(hwnd, cmd)


def _os_focus(hwnd: int) -> bool:
    """SetForegroundWindow from a background process: attach to the current
    foreground thread first, or Windows only flashes the taskbar button."""
    import ctypes
    u = ctypes.windll.user32
    if u.IsIconic(hwnd):
        u.ShowWindow(hwnd, SW_RESTORE)
    fg = u.GetForegroundWindow()
    cur = ctypes.windll.kernel32.GetCurrentThreadId()
    fg_thread = u.GetWindowThreadProcessId(fg, None)
    if fg_thread and fg_thread != cur:
        u.AttachThreadInput(cur, fg_thread, True)
    try:
        u.BringWindowToTop(hwnd)
        ok = bool(u.SetForegroundWindow(hwnd))
    finally:
        if fg_thread and fg_thread != cur:
            u.AttachThreadInput(cur, fg_thread, False)
    return ok


def _os_close(hwnd: int) -> None:
    import win32con
    import win32gui
    win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)


def _os_work_area(hwnd: int) -> tuple[int, int, int, int]:
    import win32api
    mon = win32api.MonitorFromWindow(hwnd, 2)          # MONITOR_DEFAULTTONEAREST
    return tuple(win32api.GetMonitorInfo(mon)["Work"])


def _os_move(hwnd: int, x: int, y: int, w: int, h: int) -> None:
    import win32gui
    win32gui.ShowWindow(hwnd, SW_RESTORE)
    win32gui.MoveWindow(hwnd, x, y, w, h, True)


# -- helpers -------------------------------------------------------------------------
def _norm(s: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", s.lower()).split())


def _match_app(name: str, apps: list[dict]) -> dict | None:
    want = _norm(name)
    if not want:
        return None
    by = {_norm(a["name"]): a for a in apps}
    if want in by:
        return by[want]
    starts = sorted((a for n, a in by.items() if n.startswith(want)), key=lambda a: len(a["name"]))
    if starts:
        return starts[0]
    words = sorted((a for n, a in by.items() if f" {want} " in f" {n} "), key=lambda a: len(a["name"]))
    if words:
        return words[0]
    close = difflib.get_close_matches(want, list(by), n=1, cutoff=0.75)
    return by[close[0]] if close else None


def _window(title: str | None = None, hwnd: int | None = None) -> dict:
    wins = _os_windows()
    if hwnd:
        hit = next((w for w in wins if w["hwnd"] == int(hwnd)), None)
    elif title:
        t = title.lower().strip()
        hit = (next((w for w in wins if w["app"].lower() == t), None)
               or next((w for w in wins if t in w["title"].lower()), None)
               or next((w for w in wins if t in w["app"].lower()), None))
    else:
        hit = next((w for w in wins if w["foreground"]), None)
    if hit is None:
        raise ToolError(f"no window like {title!r}" if title or hwnd else "no window is in front")
    return hit


# -- tools ---------------------------------------------------------------------------
def list_apps(query: str = "") -> dict:
    """Installed apps from the Start menu, filtered by `query` (max 40)."""
    q = _norm(query)
    names = sorted({a["name"] for a in _os_start_apps() if not q or q in _norm(a["name"])})
    return {"apps": names[:40], "more": max(0, len(names) - 40)}


def launch_app(name: str) -> dict:
    """Open an installed app by its Start-menu name, e.g. "spotify", "notepad",
    "calculator". Shells, the registry editor and security/account tools are blocked."""
    if BLOCKED_APPS.search(name):
        raise ToolError("I don't open that one by voice")
    hit = _match_app(name, _os_start_apps())
    if hit is None:
        raise ToolError(f"no installed app called {name!r}")
    if BLOCKED_APPS.search(hit["name"]) or BLOCKED_APPS.search(hit["id"]):
        raise ToolError("I don't open that one by voice")
    _os_launch(hit["id"])
    return {"launched": hit["name"]}


def list_windows() -> dict:
    """Open windows: title, app, minimized, which one is in front."""
    return {"windows": [{k: w[k] for k in ("title", "app", "minimized", "foreground", "hwnd")}
                        for w in _os_windows()]}


def focus_window(title: str) -> dict:
    """Bring a window to the front, by part of its title or its app name."""
    w = _window(title)
    old = next((x["title"] for x in _os_windows() if x["foreground"]), "")
    ok = _os_focus(w["hwnd"])
    return change(old, w["title"], hwnd=w["hwnd"], focused=ok)


def minimize_window(title: str = "") -> dict:
    """Minimize a window (by title or app name; empty = the one in front)."""
    w = _window(title or None)
    pl = _os_placement(w["hwnd"])
    _os_show(w["hwnd"], SW_MINIMIZE)
    return change("normal" if not w["minimized"] else "minimized", "minimized",
                  hwnd=w["hwnd"], title=w["title"], placement=pl)


def restore_window(title: str = "", hwnd: int | None = None) -> dict:
    """Restore a minimized window."""
    w = _window(title or None, hwnd)
    _os_show(w["hwnd"], SW_RESTORE)
    return change("minimized" if w["minimized"] else "normal", "normal", hwnd=w["hwnd"], title=w["title"])


def snap_window(side: str, title: str = "") -> dict:
    """Snap a window to the left or right half of its screen (empty title =
    the one in front)."""
    side = side.lower().strip()
    if side not in ("left", "right"):
        raise ToolError("side must be left or right")
    w = _window(title or None)
    pl = _os_placement(w["hwnd"])
    x0, y0, x1, y1 = _os_work_area(w["hwnd"])
    half = (x1 - x0) // 2
    _os_move(w["hwnd"], x0 if side == "left" else x0 + half, y0, half, y1 - y0)
    return change("unsnapped", side, hwnd=w["hwnd"], title=w["title"], placement=pl)


def close_window(title: str) -> dict:
    """Close a window (the app may ask to save first). Not on the default allow-list."""
    if not title.strip():
        raise ToolError("say which window to close")
    w = _window(title)
    _os_close(w["hwnd"])
    return {"closed": w["title"]}


def restore_placement(hwnd: int, placement: list) -> dict:
    """Undo helper: put a window back exactly where it was."""
    _os_set_placement(int(hwnd), placement)
    return {"restored": int(hwnd)}
