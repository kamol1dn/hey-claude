"""clipboard: M9's `get_clipboard` and `screenshot` results -> the card's data.

get_clipboard answers {"text": str | None, "chars": n, "holds"?: "image" | "files" |
"empty" | "other"}; the card shows the text (clamped; monospace when it looks like
code; a link's host), with its size. screenshot answers an image for the model plus
a text block {"width", "height", "monitor", "preview": path}: a small copy the
MCP server left in state/screen/ (hey-claude only sees a tool's text). It is read
into the card here (a data: URL) and deleted, so the screenshot doesn't stay on disk.
"""
from __future__ import annotations

import base64
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
MAX_LINES = 12
MAX_CHARS = 900
_URL = re.compile(r"^(https?|ftp)://[^\s/$.?#][^\s]*$", re.I)
_CODE_WORDS = re.compile(r"^\s*(def |class |import |from \S+ import|return\b|function\b|const |let |var |"
                         r"public |private |#include|using |package |fn |func |SELECT |INSERT |UPDATE |"
                         r"if \(|for \(|while \(|elif |else:|try:|except\b|async |await |@\w+)", re.M)


def looks_like_code(t: str) -> bool:
    lines = [ln for ln in t.splitlines() if ln.strip()]
    if not lines:
        return False
    score = len(_CODE_WORDS.findall(t)) * 2
    score += sum(1 for ln in lines if re.match(r"^( {2,}|\t)\S", ln))                 # indented lines
    score += sum(1 for ln in lines if re.search(r"[;{}]\s*$|\)\s*:\s*$|=>|->|::|==|!=|\+=", ln))
    sym = sum(t.count(c) for c in "{}[]();=<>_$#") / max(1, len(t))
    if sym > 0.06:
        score += 2
    return score >= max(3, len(lines) // 2) if len(lines) > 1 else score >= 3


def _json_of(result, raw: str):
    if isinstance(result, dict):
        return result
    try:
        v = json.loads(raw) if raw.lstrip().startswith("{") else None
    except ValueError:
        v = None
    return v if isinstance(v, dict) else None


def _clipboard(r: dict) -> dict:
    t = r.get("text")
    if not isinstance(t, str) or not t.strip():
        holds = "empty" if isinstance(t, str) else str(r.get("holds") or "other")     # "" or only spaces: empty
        kind = holds if holds in ("image", "files", "empty", "other") else "other"
        return {"kind": kind, "heading": "Clipboard", "chars": len(t or "")}
    t = t.replace("\r\n", "\n").replace("\r", "\n")
    lines = t.split("\n")
    n_lines = len(lines) - (1 if lines[-1] == "" and len(lines) > 1 else 0)
    one = t.strip()
    if "\n" not in one and _URL.match(one):
        kind = "link"
    elif looks_like_code(t):
        kind = "code"
    else:
        kind = "text"
    shown = "\n".join(ln.rstrip() for ln in t.strip("\n").split("\n")[:MAX_LINES])
    if kind != "code":
        shown = re.sub(r"\n{3,}", "\n\n", shown)
    else:
        shown = shown.expandtabs(4)
    if len(shown) > MAX_CHARS:
        shown = shown[: MAX_CHARS - 1].rstrip() + "…"
    out = {"kind": kind, "heading": "Clipboard", "text": shown, "chars": len(t), "lines": max(1, n_lines)}
    rest = len(t.strip("\n")) - len(shown.rstrip("…"))
    if rest > 0:
        out["more"] = rest
    if kind == "link":
        m = re.match(r"^[a-z]+://([^/?#]+)", one, re.I)
        if m:
            out["host"] = re.sub(r"^www\.", "", m.group(1))[:120]
    return out


def _allowed(p: Path) -> tuple[bool, bool]:
    """(may read it, may delete it): only the server's preview folder, or a repo fixture."""
    from heyclaude.mcp_windows.screen import preview_dir
    try:
        p = p.resolve()
        live = preview_dir().resolve()
        fx = (ROOT / "tests" / "fixtures" / "tool_results").resolve()
    except (OSError, ValueError):
        return False, False
    if live in p.parents:
        return True, True
    return fx in p.parents, False


def _preview_uri(path: str) -> str:
    if not path:
        return ""
    p = Path(path)
    if not p.is_absolute():
        p = ROOT / p
    ok, delete = _allowed(p)
    if not ok or p.suffix.lower() not in (".jpg", ".jpeg") or not p.is_file():
        return ""
    try:
        data = p.read_bytes()
        if delete:
            p.unlink(missing_ok=True)                  # the copy goes once the card has it
    except OSError:
        return ""
    if data[:2] != b"\xff\xd8" or len(data) > 420_000:
        return ""
    return "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")


def _screenshot(r: dict) -> dict:
    w, h = int(r.get("width") or 0), int(r.get("height") or 0)
    mon = r.get("monitor")
    out = {"kind": "screenshot", "heading": "Screenshot"}
    if w > 0 and h > 0:
        out.update(width=w, height=h)
    if isinstance(mon, int) and mon >= 0:
        out["monitor"] = mon
    src = _preview_uri(str(r.get("preview") or ""))
    if src:
        out["src"] = src
    return out


def adapt(args: dict, result, raw: str) -> dict | None:
    r = _json_of(result, raw)
    if r is None:
        return None
    if "width" in r and "height" in r:
        return _screenshot(r)
    if "text" in r or "holds" in r:
        return _clipboard(r)
    return None
