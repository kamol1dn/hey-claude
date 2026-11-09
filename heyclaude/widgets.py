"""Response widgets (ROADMAP M5): the answer can carry a small card.

The contract is ui/widgets.schema.json, the only place widgets are defined.
An answer (either tier) may end with one line

    WIDGET {"widget": "calendar_list", "data": {...}}

which is never read aloud. `WidgetFilter` takes it out of the streamed text,
`normalise()` checks it against the schema (unknown or invalid widgets fall
back to their `text`, or nothing), and the island draws it (ui/island/widgets).
"""
from __future__ import annotations

import json
import os
import re
import uuid
from functools import lru_cache
from pathlib import Path

from .config import ROOT, STATE_DIR

SCHEMA_FILE = ROOT / "ui" / "widgets.schema.json"
PREFIX = "WIDGET"
DEFAULT_TTL = 12.0
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg"}
# Never launched from a card; shown in Explorer instead.
RUNNABLE_EXT = {".exe", ".bat", ".cmd", ".com", ".ps1", ".psm1", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh",
                ".msi", ".msp", ".lnk", ".scr", ".pif", ".reg", ".hta", ".cpl", ".jar", ".py", ".pyw", ".url"}


@lru_cache(maxsize=2)
def load(path: str | None = None) -> dict:
    with open(path or SCHEMA_FILE, encoding="utf-8") as f:
        return json.load(f)


def names() -> list[str]:
    return list(load()["x-widgets"])


# ----------------------------------------------------------------- validation
_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool}


def validate(value, schema: dict, path: str = "data") -> list[str]:
    """The subset of JSON Schema the widget contract uses."""
    errs: list[str] = []
    t = schema.get("type")
    if t == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return [f"{path}: expected a number"]
    elif t == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            return [f"{path}: expected an integer"]
    elif t in _TYPES and not isinstance(value, _TYPES[t]):
        return [f"{path}: expected {t}"]
    if "enum" in schema and value not in schema["enum"]:
        errs.append(f"{path}: not one of {schema['enum']}")
    if isinstance(value, str):
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errs.append(f"{path}: longer than {schema['maxLength']}")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errs.append(f"{path}: doesn't match {schema['pattern']}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errs.append(f"{path}: below {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errs.append(f"{path}: above {schema['maximum']}")
    if isinstance(value, dict):
        for k in schema.get("required", []):
            if k not in value:
                errs.append(f"{path}: missing {k}")
        for k, sub in schema.get("properties", {}).items():
            if k in value:
                errs += validate(value[k], sub, f"{path}.{k}")
    if isinstance(value, list):
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errs.append(f"{path}: more than {schema['maxItems']} items")
        if "items" in schema:
            for i, v in enumerate(value):
                errs += validate(v, schema["items"], f"{path}[{i}]")
    return errs


def _strip(value, schema: dict):
    """Keep only declared properties (the island never sees anything else)."""
    if isinstance(value, dict) and "properties" in schema:
        return {k: _strip(v, schema["properties"][k]) for k, v in value.items() if k in schema["properties"]}
    if isinstance(value, list) and "items" in schema:
        return [_strip(v, schema["items"]) for v in value]
    return value


def normalise(spec) -> dict | None:
    """A valid widget spec, the text fallback, or None."""
    if isinstance(spec, str):
        try:
            spec = json.loads(spec)
        except (json.JSONDecodeError, ValueError):
            return None
    if not isinstance(spec, dict):
        return None
    reg = load()["x-widgets"]
    name, data = spec.get("widget"), spec.get("data")
    fallback = spec.get("text") if isinstance(spec.get("text"), str) else ""
    out = {"widget": name, "data": data, "text": fallback[:200]}
    try:
        ttl = float(spec.get("ttl_s", DEFAULT_TTL))
    except (TypeError, ValueError):
        ttl = DEFAULT_TTL
    out["ttl_s"] = max(3.0, min(3600.0, ttl))
    if name not in reg or not isinstance(data, dict):
        return _fallback(out, f"unknown widget {name!r}" if name not in reg else "no data")
    errs = validate(data, reg[name]["schema"])
    if errs:
        return _fallback(out, "; ".join(errs[:3]))
    out["data"] = _strip(data, reg[name]["schema"])
    if name == "timer":
        out["ttl_s"] = max(out["ttl_s"], float(data["seconds"]) + 8)
    return out


def _fallback(out: dict, why: str) -> dict | None:
    print(f"[widget] {out.get('widget')!r} shown as text ({why})" if out["text"] else f"[widget] dropped: {why}")
    if not out["text"]:
        return None
    return {"widget": "text", "data": {"text": out["text"]}, "text": out["text"], "ttl_s": out["ttl_s"]}


def _local_image(p: str) -> str:
    try:
        path = Path(os.path.expandvars(os.path.expanduser(p)))
        if path.suffix.lower() in IMAGE_EXT and path.is_file():
            return path.resolve().as_uri()
    except (OSError, ValueError):
        pass
    return ""


def for_qml(spec) -> dict | None:
    """normalise() plus what the QML needs: an id, file URLs for local images."""
    w = normalise(spec)
    if w is None:
        return None
    w = json.loads(json.dumps(w))           # a plain copy
    w["id"] = uuid.uuid4().hex[:10]
    d = w["data"]
    if w["widget"] == "image":
        d["src"] = d.get("url") or _local_image(d.get("path", ""))
        if not d["src"]:
            return _fallback({"widget": "image", "text": d.get("caption", ""), "ttl_s": w["ttl_s"]}, "no image")
    if w["widget"] == "telegram_chats":
        for c in d.get("chats", []):
            c["avatar_src"] = _avatar(c.get("avatar", ""))
    if w["widget"] == "link" and d.get("path"):
        d["path_ok"] = os.path.exists(os.path.expandvars(os.path.expanduser(d["path"])))
    return w


def _avatar(p: str) -> str:
    """Telegram avatars only from the git-ignored cache state/icons/telegram/."""
    if not p:
        return ""
    try:
        path = Path(p).resolve()
        cache = (STATE_DIR / "icons" / "telegram").resolve()
        if cache in path.parents and path.is_file():
            return path.as_uri()
    except (OSError, ValueError):
        pass
    return ""


# ----------------------------------------------------------------- streaming
class WidgetFilter:
    """Feed it the answer as it streams; it returns the speakable part and keeps
    any `WIDGET {...}` line (at most one widget per answer; the last one wins)."""

    def __init__(self):
        self.spec: dict | None = None
        self.raw: str | None = None
        self._line = ""             # the current line, while it might be a widget line
        self._at_start = True       # at the start of a line
        self._holding = False

    def feed(self, chunk: str) -> str:
        out = []
        for ch in chunk:
            if self._holding:
                if ch == "\n":
                    self._take(self._line)
                    self._line, self._holding, self._at_start = "", False, True
                else:
                    self._line += ch
                continue
            if self._at_start:
                self._line += ch
                head = self._line.lstrip(" \t`*")
                if not head:
                    if ch == "\n":
                        out.append(self._line)
                        self._line = ""
                    continue
                if len(head) <= len(PREFIX) and PREFIX.startswith(head):
                    continue                        # could still become "WIDGET "
                if head.startswith(PREFIX) and head[len(PREFIX)] in " :{":
                    self._holding = True
                    continue
                out.append(self._line)
                self._line, self._at_start = "", ch == "\n"
                continue
            out.append(ch)
            if ch == "\n":
                self._at_start = True
        return "".join(out)

    def finish(self) -> str:
        """End of the answer: the rest of a held line, or a widget taken from it."""
        rest = ""
        if self._holding:
            self._take(self._line)
        elif self._line:
            rest = self._line
        self._line, self._holding, self._at_start = "", False, True
        return rest

    def _take(self, line: str):
        s = line.strip().strip("`*").strip()
        s = s[len(PREFIX):].lstrip(" :")
        s = s.strip("`").strip()
        if s.startswith("json"):
            s = s[4:].strip()
        try:
            v = json.loads(s)
        except (json.JSONDecodeError, ValueError):
            print(f"[widget] not JSON: {s[:80]!r}")
            return
        if isinstance(v, dict):
            self.spec, self.raw = v, s


def strip_widget_lines(text: str) -> tuple[str, dict | None]:
    """The whole answer at once: (speakable text, widget spec or None)."""
    f = WidgetFilter()
    out = f.feed(text) + f.finish()
    return out, f.spec


# ----------------------------------------------------------------- prompting
def prompt_text() -> str:
    """The widget contract for the answering models, built from the schema."""
    lines = ["After your spoken answer you may add ONE final line that the island shows as a small card "
             "(it is never read aloud): WIDGET {\"widget\": NAME, \"data\": {...}, \"text\": \"one-line fallback\"}. "
             "Use it only when a card clearly helps (a forecast, an agenda, a countdown, a link, a short code "
             "snippet, an image, Telegram chats or a draft); keep the spoken part complete without it. "
             "Valid JSON on a single line. NAME and data (keys marked * are required):"]
    for name, w in load()["x-widgets"].items():
        sch = w["schema"]
        req = set(sch.get("required", []))
        keys = ", ".join(k + ("*" if k in req else "") for k in sch.get("properties", {}))
        lines.append(f"- {name}: {w['description']}. data: {{{keys}}}. e.g. {json.dumps(w['example'], ensure_ascii=False)}")
    lines.append("Timer: the island counts down and tells the user when it ends; you don't need any tool for it. "
                 "telegram_draft never sends anything: sending needs the user's approval, which isn't available yet.")
    return "\n".join(lines)


# ----------------------------------------------------------------- clicks
def safe_open(target: str) -> bool:
    """Open a card's link or file. http(s) and tg:// links open normally; a file
    opens with its app unless it could run code, which is shown in Explorer instead."""
    import subprocess
    t = (target or "").strip()
    if re.match(r"^(https?|tg)://", t, re.I):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        return QDesktopServices.openUrl(QUrl(t))
    if t.startswith("file:"):
        from PySide6.QtCore import QUrl
        t = QUrl(t).toLocalFile()
    p = Path(os.path.expandvars(os.path.expanduser(t)))
    if not p.exists():
        print(f"[widget] not found: {p}")
        return False
    if p.is_file() and p.suffix.lower() in RUNNABLE_EXT:
        subprocess.Popen(["explorer", "/select,", str(p)])
        return True
    os.startfile(str(p))
    return True


def attachment_prompt(paths: list[str], text: str) -> str:
    """A request about dropped files, for the tool agent (it gets the paths, not the bytes)."""
    names = "\n".join(f"- {p}" for p in paths)
    q = text.strip() or ("What is in this file?" if len(paths) == 1 else "What are these files?")
    return f"{q}\n\n(The user dropped {'this file' if len(paths) == 1 else 'these files'} on the island; " \
           f"read {'it' if len(paths) == 1 else 'them'} to answer:\n{names})"
