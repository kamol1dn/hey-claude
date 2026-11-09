"""Undo for the Windows settings tools: "undo that", or the island's Undo button.

Each change the tool agent makes through the settings server is recorded
with its {old, new} result. Undoing runs the registry's inverse calls right
here in hey-claude's process: no model round trip, so it works in ~0.1 s and
even when the agent is busy (or Wi-Fi was just switched off).
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

MAX_AGE_S = 600          # "undo that" reaches back this far
MAX_DEPTH = 12


@dataclass
class Change:
    id: str                     # the effect id shown on the island
    tool: str                   # full tool name
    args: dict
    result: dict
    calls: list                 # [(tool, args)] that undo it
    effect: dict | None = None
    at: float = field(default_factory=time.time)


def describe(ch: Change) -> str:
    """Spoken confirmation after an undo, e.g. "Volume back to 45%."."""
    ev = ch.effect or {}
    label = (ev.get("label") or ch.tool.split("__")[-1].replace("_", " ")).strip()
    old = ev.get("text_from") or ""
    if ev.get("effect") == "media":
        old = ev.get("from") if isinstance(ev.get("from"), dict) else {}
        return f"Back to {old['title']}." if old.get("title") else "Okay, undone."
    if old:
        return f"{label} back to {old}."
    return f"Undone: {label.lower()}."


class UndoStack:
    def __init__(self, runner=None):
        from .mcp_windows import registry
        self._registry = registry
        self.runner = runner or registry.call      # (name, args) -> result
        self.items: list[Change] = []
        self.lock = threading.Lock()

    def record(self, tool: str, args: dict, result, effect: dict | None = None) -> Change | None:
        """Remember a change if it can be undone; returns it (None otherwise)."""
        if not isinstance(result, dict):
            return None
        calls = self._registry.inverse(tool, args or {}, result)
        if not calls:
            return None
        ch = Change(id=(effect or {}).get("id") or f"u{time.time_ns()}", tool=tool, args=args or {},
                    result=result, calls=calls, effect=effect)
        with self.lock:
            self.items.append(ch)
            del self.items[:-MAX_DEPTH]
        return ch

    def can_undo(self, tool: str, args: dict, result) -> bool:
        return isinstance(result, dict) and bool(self._registry.inverse(tool, args or {}, result))

    def last(self, max_age_s: float = MAX_AGE_S) -> Change | None:
        with self.lock:
            for ch in reversed(self.items):
                if time.time() - ch.at <= max_age_s:
                    return ch
        return None

    def pop(self, change_id: str | None = None, max_age_s: float = MAX_AGE_S) -> Change | None:
        with self.lock:
            for i in range(len(self.items) - 1, -1, -1):
                ch = self.items[i]
                if change_id is None and time.time() - ch.at > max_age_s:
                    return None
                if change_id is None or ch.id == change_id:
                    return self.items.pop(i)
        return None

    def undo(self, change_id: str | None = None) -> tuple[Change, object] | None:
        """Undo the newest change (or the one with `change_id`). Returns
        (change, result of the last inverse call); None if there's nothing.
        Raises if Windows refused (the change is put back on the stack)."""
        ch = self.pop(change_id)
        if ch is None:
            return None
        from .mcp_windows._util import com_init
        com_init()
        res = None
        try:
            for name, a in ch.calls:
                res = self.runner(name, a)
        except Exception:
            with self.lock:
                self.items.append(ch)
            raise
        return ch, res
