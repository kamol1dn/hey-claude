"""Which app or service a tool call is using, and its icon (ROADMAP M5).

`describe("mcp__claude_ai_Google_Calendar__list_events", {})` ->
    {"service": "Google Calendar", "label": "Google Calendar · list events",
     "icon_url": "file:///.../ui/icons/services/googlecalendar.svg",
     "glyph": "", "initials": "GC", "server": "google_calendar", "tool": "list_events"}

Icon sources, in order:
1. `island.service_icons` in the config: {server: image path} (a per-server override);
2. an icon the MCP server declares itself (the MCP spec's `icons`), saved as
   state/icons/declared/<server>.(svg|png) by whoever learns it (M7's hook
   payloads, or `save_declared()`);
3. a bundled Simple Icons SVG (ui/icons/services, CC0);
4. the service's favicon, fetched once into state/icons/favicon/ (only with
   `island.fetch_favicons: true`; off by default, so nothing leaves the PC);
5. nothing: the island draws a plug glyph with the initials.
Claude Code's built-in tools (Bash, Read, WebSearch...) get small glyphs.
"""
from __future__ import annotations

import json
import re
import threading
import urllib.request
from functools import lru_cache
from pathlib import Path

from .config import ROOT, STATE_DIR

SERVICES_FILE = ROOT / "ui" / "services.json"
BUNDLED = ROOT / "ui" / "icons" / "services"
DECLARED = STATE_DIR / "icons" / "declared"
FAVICONS = STATE_DIR / "icons" / "favicon"

_fetching: set[str] = set()


@lru_cache(maxsize=2)
def load(path: str | None = None) -> dict:
    with open(path or SERVICES_FILE, encoding="utf-8") as f:
        return json.load(f)


def server_key(server: str) -> str:
    s = server.strip()
    s = re.sub(r"^claude_ai_", "", s, flags=re.I)
    return re.sub(r"[\s\-]+", "_", s).lower()


def split_tool(name: str) -> tuple[str, str]:
    """("server", "tool") for mcp__server__tool, ("", name) for built-in tools."""
    if name.startswith("mcp__"):
        rest = name[5:]
        server, _, tool = rest.partition("__")
        return server, tool
    return "", name


def pretty_server(server: str) -> str:
    s = re.sub(r"^claude_ai_", "", server, flags=re.I).replace("_", " ").replace("-", " ").strip()
    return " ".join(w if w.isupper() else w[:1].upper() + w[1:] for w in s.split()) or server


def tool_words(tool: str) -> str:
    """"list_events" -> "list events"; "getCurrentWeather" -> "get current weather"."""
    t = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", tool)
    return re.sub(r"[_\-]+", " ", t).strip().lower()


def initials(name: str) -> str:
    words = [w for w in re.split(r"[\s._\-]+", name) if w]
    if not words:
        return "?"
    if len(words) == 1:
        return words[0][:2].upper()
    return (words[0][0] + words[1][0]).upper()


def _url(p: Path) -> str:
    return p.resolve().as_uri()


def icon_for(key: str, spec: dict, overrides: dict | None = None, fetch: bool = False) -> str:
    """The best icon file URL for a server, or "" (initials)."""
    ov = (overrides or {}).get(key)
    if ov and Path(str(ov)).expanduser().exists():
        return _url(Path(str(ov)).expanduser())
    for ext in ("svg", "png", "ico"):
        p = DECLARED / f"{key}.{ext}"
        if p.exists():
            return _url(p)
    own = spec.get("icon_path")                     # a plugin's own icon (heyclaude/plugins.py)
    if own and Path(own).is_file():
        return _url(Path(own))
    slug = spec.get("icon")
    if slug and (BUNDLED / f"{slug}.svg").exists():
        return _url(BUNDLED / f"{slug}.svg")
    dom = spec.get("domain")
    if dom:
        p = FAVICONS / f"{dom}.png"
        if p.exists():
            return _url(p)
        if fetch:
            fetch_favicon(dom)
    return ""


def describe(name: str, args: dict | None = None, overrides: dict | None = None, fetch: bool = False) -> dict | None:
    if not name:
        return None
    data = load()
    server, tool = split_tool(name)
    if not server:
        b = data.get("builtin", {}).get(tool)
        if b is None:
            return {"service": tool, "label": tool_words(tool), "icon_url": "", "glyph": "gear",
                    "initials": initials(tool), "server": "", "tool": tool}
        words = _builtin_detail(tool, args or {})
        return {"service": b["name"], "label": f"{b['name']} · {words}" if words else b["name"],
                "icon_url": "", "glyph": b.get("glyph", "gear"), "initials": initials(b["name"]), "server": "", "tool": tool}
    key = server_key(server)
    if tool.lower().startswith(key + "_"):        # telegram_list_dialogs on the telegram server
        tool = tool[len(key) + 1:]
    spec = data.get("servers", {}).get(key) or _plugin_spec(key)
    svc = spec.get("name") or pretty_server(server)
    return {"service": svc, "label": f"{svc} · {tool_words(tool)}" if tool else svc,
            "icon_url": icon_for(key, spec, overrides, fetch), "glyph": spec.get("glyph", ""),
            "initials": initials(svc), "server": key, "tool": tool}


def _plugin_spec(key: str) -> dict:
    """The name and icon an enabled plugin declares for its MCP server ({} if none)."""
    try:
        from . import plugins
        return plugins.get().service_icon(key) or {}
    except Exception:
        return {}


def _builtin_detail(tool: str, args: dict) -> str:
    """A few words about what a built-in tool is doing. Never a path or command
    line (those can hold private data); only the file's name or the search."""
    if tool in ("Read", "Write", "Edit", "MultiEdit") and args.get("file_path"):
        return ("reading " if tool == "Read" else "editing ") + Path(str(args["file_path"])).name
    if tool == "WebSearch" and args.get("query"):
        q = str(args["query"])
        return q if len(q) <= 40 else q[:39] + "…"
    if tool == "WebFetch" and args.get("url"):
        m = re.match(r"https?://([^/]+)", str(args["url"]))
        return m.group(1) if m else ""
    if tool in ("Glob", "Grep"):
        return "searching"
    if tool in ("Bash", "PowerShell"):
        return "running a command"
    return ""


def save_declared(server: str, data: bytes, ext: str = "png") -> Path:
    """Store an icon an MCP server declared (MCP `icons`), for describe() to use."""
    DECLARED.mkdir(parents=True, exist_ok=True)
    p = DECLARED / f"{server_key(server)}.{ext.lstrip('.')}"
    p.write_bytes(data)
    return p


def fetch_favicon(domain: str, timeout: float = 4.0) -> None:
    """Fetch https://<domain>/favicon.ico once, in the background, into the cache.
    Only for domains listed in ui/services.json (never one a tool result names)."""
    if domain in _fetching or not re.fullmatch(r"[a-z0-9.\-]+", domain):
        return
    _fetching.add(domain)

    def go():
        try:
            req = urllib.request.Request(f"https://{domain}/favicon.ico", headers={"User-Agent": "hey-claude"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                blob = r.read(256 * 1024)
            from PySide6.QtGui import QImage
            img = QImage()
            if img.loadFromData(blob):
                FAVICONS.mkdir(parents=True, exist_ok=True)
                img.save(str(FAVICONS / f"{domain}.png"))
        except Exception as e:
            print(f"[icons] no favicon for {domain}: {e}")
    threading.Thread(target=go, daemon=True, name="favicon").start()
