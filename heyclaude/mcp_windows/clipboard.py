"""Clipboard text: read and write."""
from __future__ import annotations

import time

from ._util import ToolError, change

MAX_UNDO = 10_000          # an old clipboard longer than this isn't sent back for undo


def _open():
    import win32clipboard
    for _ in range(10):            # another app may hold it for a moment
        try:
            win32clipboard.OpenClipboard()
            return win32clipboard
        except Exception:
            time.sleep(0.05)
    raise ToolError("the clipboard is busy")


def _os_get() -> str | None:
    cb = _open()
    try:
        if cb.IsClipboardFormatAvailable(13):          # CF_UNICODETEXT
            return cb.GetClipboardData(13)
        return None
    finally:
        cb.CloseClipboard()


def _os_set(text: str) -> None:
    cb = _open()
    try:
        cb.EmptyClipboard()
        cb.SetClipboardData(13, text)
    finally:
        cb.CloseClipboard()


def get_clipboard() -> dict:
    """The text on the clipboard (None if it holds something else, like an image)."""
    t = _os_get()
    return {"text": t, "chars": len(t) if t is not None else 0}


def set_clipboard(text: str) -> dict:
    """Put text on the clipboard."""
    old = _os_get()
    _os_set(text)
    if old is not None and len(old) > MAX_UNDO:
        return {"old": None, "new": text, "old_chars": len(old), "undoable": False}
    return change(old, text)
