"""Screen: a screenshot for "what's on my screen?" and Settings pages
(ms-settings: URIs, the fallback for anything without its own tool)."""
from __future__ import annotations

import os
import re

from ._util import ToolError

# Friendly names -> ms-settings pages. Any other ms-settings:<page> works too.
PAGES = {
    "wifi": "network-wifi", "wi-fi": "network-wifi", "network": "network-status", "ethernet": "network-ethernet",
    "vpn": "network-vpn", "airplane mode": "network-airplanemode", "mobile hotspot": "network-mobilehotspot",
    "bluetooth": "bluetooth", "devices": "devices", "printers": "printers", "mouse": "mousetouchpad",
    "touchpad": "devices-touchpad", "display": "display", "night light": "nightlight", "sound": "sound",
    "volume mixer": "apps-volume", "notifications": "notifications", "focus": "focus", "power": "powersleep",
    "battery": "batterysaver", "storage": "storagesense", "multitasking": "multitasking",
    "clipboard": "clipboard", "about": "about", "personalization": "personalization",
    "background": "personalization-background", "wallpaper": "personalization-background",
    "colors": "colors", "dark mode": "colors", "themes": "themes", "lock screen": "lockscreen",
    "start": "personalization-start", "taskbar": "taskbar", "fonts": "fonts", "apps": "appsfeatures",
    "default apps": "defaultapps", "startup apps": "startupapps", "time": "dateandtime", "date": "dateandtime",
    "language": "regionlanguage", "region": "regionlanguage", "keyboard": "keyboard", "speech": "speech",
    "gaming": "gaming", "game mode": "gaming-gamemode", "accessibility": "easeofaccess",
    "text size": "easeofaccess-display", "magnifier": "easeofaccess-magnifier", "narrator": "easeofaccess-narrator",
    "privacy": "privacy", "microphone": "privacy-microphone", "camera": "privacy-webcam",
    "location": "privacy-location", "windows update": "windowsupdate", "update": "windowsupdate",
}
# Pages of the "never exposed" areas: security, accounts, sign-in, recovery.
BLOCKED_PAGES = re.compile(r"defender|security|firewall|account|yourinfo|signin|family|otherusers|"
                           r"workplace|recovery|developers|backup|sync|emailandaccounts|uac", re.I)
_PAGE = re.compile(r"^[a-z0-9-]+$")


def _os_open(uri: str) -> None:
    os.startfile(uri)


def open_settings_page(page: str) -> dict:
    """Open a Windows Settings page by name ("wifi", "bluetooth", "display",
    "sound", "night light", "taskbar"...) or by its ms-settings: id. Use this
    for anything that has no tool of its own."""
    p = page.strip().lower()
    p = p.removeprefix("ms-settings:")
    p = PAGES.get(p, PAGES.get(p.replace("_", " "), p))
    if not _PAGE.match(p):
        raise ToolError(f"not a Settings page: {page!r}")
    if BLOCKED_PAGES.search(p):
        raise ToolError("security and account settings aren't opened by voice")
    _os_open(f"ms-settings:{p}")
    return {"opened": f"ms-settings:{p}"}


# == screenshot ==========================================================================
def _os_capture(monitor: int | None) -> tuple[bytes, int, int]:
    """BGRA pixels of one monitor (or the whole desktop) at native resolution."""
    import ctypes

    import win32api
    import win32con
    import win32gui
    import win32ui
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        pass
    if monitor is None:
        x, y = win32api.GetSystemMetrics(76), win32api.GetSystemMetrics(77)
        w, h = win32api.GetSystemMetrics(78), win32api.GetSystemMetrics(79)
    else:
        mons = win32api.EnumDisplayMonitors()
        if not 0 <= monitor < len(mons):
            raise ToolError(f"no monitor {monitor}; have {len(mons)}")
        x, y, x1, y1 = win32api.GetMonitorInfo(mons[monitor][0])["Monitor"]
        w, h = x1 - x, y1 - y
    hdesk = win32gui.GetDesktopWindow()
    src = win32ui.CreateDCFromHandle(win32gui.GetWindowDC(hdesk))
    mem = src.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(src, w, h)
    mem.SelectObject(bmp)
    try:
        mem.BitBlt((0, 0), (w, h), src, (x, y), win32con.SRCCOPY)
        data = bmp.GetBitmapBits(True)
    finally:
        mem.DeleteDC()
        src.DeleteDC()
        win32gui.DeleteObject(bmp.GetHandle())
    return data, w, h


def encode(bgra: bytes, w: int, h: int, max_width: int = 1280, fmt: str = "jpeg") -> bytes:
    """Scale down (keeping the aspect) and encode with Qt's QImage (no app needed)."""
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
    from PySide6.QtGui import QImage
    img = QImage(bgra, w, h, w * 4, QImage.Format_RGB32)
    if w > max_width:
        img = img.scaledToWidth(max_width, Qt.SmoothTransformation)
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "JPG" if fmt == "jpeg" else "PNG", 80)
    return bytes(ba.data())


def screenshot_bytes(monitor: int | None = 0, max_width: int = 1280) -> tuple[bytes, int, int]:
    data, w, h = _os_capture(monitor)
    return encode(data, w, h, max_width), w, h
