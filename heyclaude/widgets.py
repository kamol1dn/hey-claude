"""Response widgets (ROADMAP M5): the answer can carry a small card.

Widgets are plug-ins: widgets/<id>/widget.json (its data schema, example,
size, title, tool triggers) + Widget.qml. ui/widgets.schema.json is the
envelope and the manifest contract. An answer (either tier) may end with one
line

    WIDGET {"widget": "calendar_list", "data": {...}}

which is never read aloud, or a tool result makes one by itself (a manifest's
`triggers`: the weather card comes straight from the weather tool's result,
so the model never makes the numbers up). `WidgetFilter` takes the line out
of the streamed text, `normalise()` checks the data against the widget's
schema (unknown or invalid widgets fall back to their `text`, or nothing),
and the island draws it.
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
WIDGETS_DIR = ROOT / "widgets"
API_MAJOR = "1"
PREFIX = "WIDGET"
MOOD_PREFIX = "MOOD"           # M6: `MOOD happy` (characters.MOODS), the character's mood for this answer
MOODS = ("neutral", "happy", "confused", "sorry", "excited")
DEFAULT_TTL = 12.0
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg"}
# Never launched from a card; shown in Explorer instead.
RUNNABLE_EXT = {".exe", ".bat", ".cmd", ".com", ".ps1", ".psm1", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh",
                ".msi", ".msp", ".lnk", ".scr", ".pif", ".reg", ".hta", ".cpl", ".jar", ".py", ".pyw", ".url"}


@lru_cache(maxsize=2)
def registry(folder: str | None = None) -> dict[str, dict]:
    """id -> manifest (widget.json, plus "dir" and "qml"), for every widget folder that loads."""
    out: dict[str, dict] = {}
    base = Path(folder) if folder else WIDGETS_DIR
    for man_file in sorted(base.glob("*/widget.json")):
        try:
            with open(man_file, encoding="utf-8") as f:
                man = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            print(f"[widget] {man_file.parent.name}: bad widget.json ({e})")
            continue
        wid = man.get("id")
        problems = []
        if wid != man_file.parent.name:
            problems.append("id must match its folder")
        if str(man.get("api", "")).split(".")[0] != API_MAJOR:
            problems.append(f"widget API {man.get('api')!r} isn't {API_MAJOR}.x")
        for k in ("name", "description", "schema", "example"):
            if k not in man:
                problems.append(f"no {k}")
        qml = man_file.parent / "Widget.qml"
        if not qml.exists():
            problems.append("no Widget.qml")
        if problems:
            print(f"[widget] {man_file.parent.name} skipped: {'; '.join(problems)}")
            continue
        man = dict(man, dir=str(man_file.parent), qml=qml.resolve().as_uri())
        out[wid] = man
    return out


@lru_cache(maxsize=2)
def load(path: str | None = None) -> dict:
    """The envelope schema, with the registry's widgets under "x-widgets"."""
    with open(path or SCHEMA_FILE, encoding="utf-8") as f:
        env = json.load(f)
    env["x-widgets"] = registry()
    return env


def names() -> list[str]:
    return list(registry())


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
    if "ttl_s" not in spec and reg[name].get("ttl_s"):
        out["ttl_s"] = float(reg[name]["ttl_s"])
    if name == "timer":
        out["ttl_s"] = max(out["ttl_s"], float(data["seconds"]) + 8)
    return out


def _tool_matches(tool: str, names) -> bool:
    """A trigger names a tool in full (mcp__weather__get_weather) or by its own
    name (telegram_list_dialogs), whatever the MCP server is called locally."""
    return any(tool == n or tool.endswith("__" + n) for n in names or [])


def from_tool(tool: str, args: dict | None, result, raw: str | None = None) -> dict | None:
    """A widget straight from a tool result (a manifest's triggers), or None.

    `result` is the tool's JSON (or None); `raw` its text, for tools that answer
    in markdown (the Telegram server does unless asked for JSON). A manifest's
    `triggers.data` is "result" (the JSON is the card's data) or the name of an
    adapter in ADAPTERS that turns the result into the card's data."""
    for wid, man in registry().items():
        trig = man.get("triggers") or {}
        if not _tool_matches(tool, trig.get("tools")):
            continue
        how = trig.get("data", "result")
        if how == "result":
            data = result if isinstance(result, dict) else None
        elif how in ADAPTERS:
            try:
                data = ADAPTERS[how](args or {}, result, raw or "")
            except Exception as e:                     # a tool's odd output never breaks the turn
                print(f"[widget] {how}: {e}")
                data = None
        else:
            print(f"[widget] {wid}: no adapter {how!r}")
            data = None
        if not data:
            return None
        return normalise({"widget": wid, "data": data, "text": ""})
    return None


# ------------------------------------------------------- tool-result adapters
def _clip(s, n: int) -> str:
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _local_hhmm(iso: str) -> str:
    """"2026-11-09T14:02:11.000Z" (UTC) -> "19:02" in local time; "" when unreadable."""
    from datetime import datetime
    s = str(iso or "").strip()
    if not s:
        return ""
    try:
        if re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d", s):      # the markdown form, UTC
            s += "Z"
        d = datetime.fromisoformat(s.replace("Z", "+00:00").replace(" ", "T"))
        if d.tzinfo is None:
            return d.strftime("%H:%M")
        return d.astimezone().strftime("%H:%M")
    except ValueError:
        return ""


def _tg_link(username: str) -> str:
    u = str(username or "").lstrip("@")
    return f"tg://resolve?domain={u}" if re.fullmatch(r"[A-Za-z0-9_]{3,64}", u) else ""


def _json_of(result, raw: str):
    if isinstance(result, dict):
        return result
    try:
        v = json.loads(raw) if raw.lstrip().startswith("{") else None
    except ValueError:
        v = None
    return v if isinstance(v, dict) else None


_DIALOG_LINE = re.compile(r'^- \*\*(?P<name>.+?)\*\*(?: @(?P<user>\w+))? \[[^\]]*\]'
                          r'(?: \((?P<unread>\d+) unread\))?(?: — "(?P<last>.*)")?\s*$')


def _telegram_dialogs(args: dict, result, raw: str) -> dict | None:
    """telegram_list_dialogs -> telegram_chats (at most 6, as listed: pinned and newest first)."""
    v = _json_of(result, raw)
    chats = []
    if v is not None and isinstance(v.get("dialogs"), list):
        for d in v["dialogs"]:
            if not isinstance(d, dict) or not d.get("name"):
                continue
            last = d.get("lastMessage") if isinstance(d.get("lastMessage"), dict) else {}
            text = last.get("text") or ""
            if last.get("outgoing") and text:
                text = "You: " + text
            c = {"name": _clip(d["name"], 50), "unread": max(0, int(d.get("unreadCount") or 0)),
                 "last": _clip(text, 120), "time": _local_hhmm(last.get("date", ""))}
            link = _tg_link(d.get("username", ""))
            if link:
                c["link"] = link
            chats.append(c)
    else:
        for line in raw.splitlines():
            m = _DIALOG_LINE.match(line.strip())
            if not m:
                continue
            c = {"name": _clip(m["name"], 50), "unread": int(m["unread"] or 0), "last": _clip(m["last"] or "", 120)}
            link = _tg_link(m["user"] or "")
            if link:
                c["link"] = link
            chats.append(c)
    chats = [{k: v for k, v in c.items() if v != ""} for c in chats[:6]]
    return {"chats": chats} if chats else None


_MESSAGE_LINE = re.compile(r"^- \[(?P<id>\d+)\] (?P<when>\d{4}-\d\d-\d\d \d\d:\d\d) \*\*(?P<who>.+?)\*\*"
                           r"(?: \(reply to \d+\))?: (?P<text>.*)$")


def _telegram_messages(args: dict, result, raw: str) -> dict | None:
    """telegram_get_messages / _search_messages -> telegram_message: the newest
    five, oldest first, so it reads like the chat; yours are indented."""
    v = _json_of(result, raw)
    msgs = []
    if v is not None and isinstance(v.get("messages"), list):
        for m in v["messages"]:
            if not isinstance(m, dict):
                continue
            text = m.get("text") or (f"[{m['media'].get('type', 'media')}]" if isinstance(m.get("media"), dict) else "")
            if not text:
                continue
            mine = bool(m.get("outgoing"))
            msgs.append({"sender": "You" if mine else _clip(m.get("senderName") or "", 50),
                         "text": _clip(text, 600), "time": _local_hhmm(m.get("date", "")), "reply": mine})
    else:
        for line in raw.splitlines():
            m = _MESSAGE_LINE.match(line.strip())
            if not m or m["text"] == "[no text]":
                continue
            mine = m["who"] == "me"
            msgs.append({"sender": "You" if mine else _clip(m["who"], 50), "text": _clip(m["text"], 600),
                         "time": _local_hhmm(m["when"]), "reply": mine})
    if not msgs:
        return None
    msgs = list(reversed(msgs[:5]))                    # the tool lists newest first
    msgs = [{k: v for k, v in m.items() if v not in ("", None)} for m in msgs]
    data = {"messages": msgs}
    chat = str(args.get("chat") or "").strip()
    if chat and not re.fullmatch(r"-?\d+", chat):      # a name or @handle, not a bare id
        data["chat"] = _clip(chat.lstrip("@"), 50)
        link = _tg_link(chat) if chat.startswith("@") else ""
        if link:
            data["link"] = link
    elif args.get("query"):
        data["chat"] = _clip(f"“{args['query']}”", 50)
    else:
        senders = {m.get("sender") for m in msgs if not m.get("reply") and m.get("sender")}
        if len(senders) == 1:
            data["chat"] = senders.pop()
    return data


def _weather(args: dict, result, raw: str) -> dict | None:
    """The built-in weather tool's JSON (already the card's data), or a weather-mcp
    answer (markdown: summary, current conditions, forecast) read into the same card."""
    from .weather_mcp import widget_from_tool
    return widget_from_tool(args, result, raw)


ADAPTERS = {"telegram_dialogs": _telegram_dialogs, "telegram_messages": _telegram_messages, "weather": _weather}


def title_for(man: dict, data: dict) -> str:
    """The card header from the manifest's template ("Weather · {location}").
    Empty when the template has nothing to show (a text card without a title):
    the card then has no header, never the widget's type name."""
    def sub(m):
        v = data.get(m.group(1))
        return "" if v is None else str(v)
    t = re.sub(r"\{(\w+)\}", sub, man.get("title") or "")
    return re.sub(r"\s*·\s*$", "", t).strip(" ·")


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
    man = registry().get(w["widget"]) or registry().get("text") or {}
    w["qml"] = man.get("qml", "")
    w["title"] = title_for(man, d)
    w["glyph"] = man.get("glyph", "")
    w["icon_url"] = (ROOT / "ui" / "icons" / "services" / f"{man['icon']}.svg").resolve().as_uri() if man.get("icon") else ""
    w["private"] = bool(man.get("private"))
    size = man.get("size") or {}
    w["width"] = float(size.get("width", 400))
    w["max_height"] = float(size.get("max_height", 236))
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
        self.mood: str | None = None    # M6: from a `MOOD happy` line
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
                if any(len(head) <= len(p) and p.startswith(head) for p in (PREFIX, MOOD_PREFIX)):
                    continue                        # could still become "WIDGET " or "MOOD "
                if head.startswith(PREFIX) and head[len(PREFIX)] in " :{":
                    self._holding = True
                    continue
                if head.startswith(MOOD_PREFIX) and head[len(MOOD_PREFIX)] in " :":
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
        if s.startswith(MOOD_PREFIX):
            m = s[len(MOOD_PREFIX):].lstrip(" :").strip().strip("`*.").lower()
            self.mood = m if m in MOODS else None
            return
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
        trig = (w.get("triggers") or {}).get("tools") or []
        if trig:
            lines.append(f"- {name}: {w['description']}. Shown automatically from {', '.join(trig)}: never write it yourself.")
            continue
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
