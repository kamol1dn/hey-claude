"""Line diffs for the edits a watched Claude Code session makes (PostToolUse of
Edit / MultiEdit / Write). The hook only carries the edited text, not the whole
file, so a diff is of the changed region: `old_string` -> `new_string`, or the
whole `content` for a Write (shown as added; the old file isn't in the hook)."""
from __future__ import annotations

import difflib
import os
from collections import Counter

HUGE_LINES = 4000          # above this (old + new) only the counts are kept


def edit_pairs(tool: str, inp: dict) -> list[tuple[str, str]]:
    """(old, new) text pairs of one edit tool call."""
    inp = inp or {}
    if tool == "Edit":
        return [(str(inp.get("old_string") or ""), str(inp.get("new_string") or ""))]
    if tool == "MultiEdit":
        return [(str(e.get("old_string") or ""), str(e.get("new_string") or ""))
                for e in inp.get("edits") or [] if isinstance(e, dict)]
    if tool == "Write":
        return [("", str(inp.get("content") or ""))]
    return []


def _lines(s: str) -> list[str]:
    return s.splitlines() if s else []


def line_diff(old: str, new: str, name: str = "file", context: int = 3) -> tuple[str, int, int, bool]:
    """(unified diff text, lines added, lines removed, huge)."""
    a, b = _lines(old), _lines(new)
    if len(a) + len(b) > HUGE_LINES:
        ca, cb = Counter(a), Counter(b)
        return "", sum((cb - ca).values()), sum((ca - cb).values()), True
    plus = minus = 0
    out = []
    for ln in difflib.unified_diff(a, b, f"a/{name}", f"b/{name}", n=context, lineterm=""):
        out.append(ln)
        if ln.startswith("+") and not ln.startswith("+++"):
            plus += 1
        elif ln.startswith("-") and not ln.startswith("---"):
            minus += 1
    return "\n".join(out), plus, minus, False


def diff_entry(tool: str, inp: dict, t: float, truncated: bool = False) -> dict | None:
    """The diff record of one edit (contract: {"file", "tool", "plus", "minus", "unified", "huge", "t"})."""
    pairs = edit_pairs(tool, inp)
    if not pairs:
        return None
    path = str((inp or {}).get("file_path") or (inp or {}).get("notebook_path") or "")
    name = os.path.basename(path.replace("\\", "/")) or "file"
    parts, plus, minus, huge = [], 0, 0, False
    for old, new in pairs:
        u, p, m, h = line_diff(old, new, name)
        plus, minus, huge = plus + p, minus + m, huge or h
        if u:
            parts.append(u if not parts else "\n".join(u.splitlines()[2:]))   # one ---/+++ header
    unified = "" if huge else "\n".join(parts)
    if truncated and unified:
        unified += "\n… (cut: the edit was too large to pass through the hook in full)"
    return {"file": path, "tool": tool, "plus": plus, "minus": minus, "unified": unified, "huge": huge, "t": t}
