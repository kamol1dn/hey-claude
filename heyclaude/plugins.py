"""Plugins: one folder, one manifest, and a feature snaps in (docs/plugin-api.md).

A plugin is `plugins/<id>/plugin.json` (built in, by PR) or `state/plugins/<id>/plugin.json`
(yours, git-ignored). The manifest says what the plugin contributes:

    widgets          island cards (widgets/<id>/widget.json + Widget.qml)
    tools            MCP tool names / prefixes it owns (mcp__info__calculate, mcp__telegram)
    mcp_servers      MCP servers it brings (user plugins: started for the tool agent)
    intents          instant-command intents it owns ("media.*")
    instant          a module with match(text) -> {"say", "widget"} for its own instant commands
    routes           hey-claude's local routes it owns ("weather")
    characters       character packs (characters/<id>/)
    icons            service icons for its MCP servers
    live_activities  things it may show on the island unasked (activities.py)
    service          a module with start(ctx) / stop(): its background part (pushes live activities)
    settings         its settings, for the Settings page
    prompt           a sentence for the tool agent's system prompt

The existing loaders ask this registry before they offer anything: a disabled plugin's card
falls back to the text card, its tools are neither started nor offered to the agent, its
instant intents and routes stop claiming requests, and its character pack disappears.
Something no plugin claims is always on, so a missing or broken manifest never removes a
feature. Built-in plugins are on by default (`plugins.disabled: []`); your own plugins in
state/plugins/ start off, since they run arbitrary code, until you switch them on
(`plugins.enabled`).

Versioning follows ui/states.json: `api` is major.minor; a plugin works here when the major
matches. A newer minor may add contribution kinds; this host ignores the ones it doesn't know
(logged once), so an old host loads a new plugin with what it understands, and an old plugin
loads on a new host unchanged.
"""
from __future__ import annotations

import fnmatch
import importlib.util
import json
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .config import ROOT, STATE_DIR

API_VERSION = "1.0"
BUILTIN_DIR = ROOT / "plugins"
USER_DIR = STATE_DIR / "plugins"
MANIFEST = "plugin.json"
CONFIG_KEY = "plugins.disabled"
ENABLED_KEY = "plugins.enabled"          # your own plugins you switched on (they start off)
SETTINGS_KEY = "plugins.settings"

# Contribution kinds this host understands, and the API version that added each.
KINDS = {"widgets": "1.0", "tools": "1.0", "mcp_servers": "1.0", "intents": "1.0", "instant": "1.0",
         "routes": "1.0", "characters": "1.0", "icons": "1.0", "live_activities": "1.0", "service": "1.0",
         "settings": "1.0", "prompt": "1.0", "read_only_tools": "1.0"}
ROUTES = ("weather",)                 # hey-claude's local routes a plugin may own
PERMISSIONS = {
    "network": "talks to the internet",
    "personal_data": "reads your own data (messages, mail, files, calendar)",
    "acts": "has tools that change things or send on your behalf (each asks on the island)",
    "background": "runs code in the background (a service)",
    "screen": "sees or controls the screen",
}
SETTING_TYPES = ("string", "number", "integer", "boolean", "enum", "secret")
# hey-claude's own MCP server names: a user plugin's server can't take one
RESERVED_SERVERS = {"windows", "weather", "weather-mcp", "info", "windows-mcp"}
_ID = re.compile(r"^[a-z][a-z0-9_-]{1,40}$")


def _major_minor(v) -> tuple[int, int] | None:
    m = re.fullmatch(r"(\d+)\.(\d+)", str(v or "").strip())
    return (int(m.group(1)), int(m.group(2))) if m else None


def compatible(api: str, host: str = API_VERSION) -> bool:
    a, h = _major_minor(api), _major_minor(host)
    return a is not None and h is not None and a[0] == h[0]


# ------------------------------------------------------------------ patterns
def tool_matches(name: str, pattern: str) -> bool:
    """A tool pattern: a full name (mcp__info__calculate), a server (mcp__telegram: all of
    its tools), a glob (mcp__info__convert_*), or a built-in tool's name (WebSearch)."""
    n, p = str(name), str(pattern)
    if n == p:
        return True
    if any(c in p for c in "*?["):
        return fnmatch.fnmatchcase(n, p)
    return p.startswith("mcp__") and p.count("__") == 1 and n.startswith(p + "__")


def intent_matches(intent: str, pattern: str) -> bool:
    """"media.*" claims media.next, media.pause...; "timer.set" just that one."""
    return fnmatch.fnmatchcase(str(intent), str(pattern))


def disabled_from_env(env_value: str | None) -> list[str]:
    """The tool patterns hey-claude passes its own MCP servers (HEYCLAUDE_SKIP_TOOLS, JSON)."""
    try:
        v = json.loads(env_value or "[]")
    except ValueError:
        return []
    return [str(x) for x in v] if isinstance(v, list) else []


SKIP_ENV = "HEYCLAUDE_SKIP_TOOLS"


def skipped(full_name: str, patterns) -> bool:
    return any(tool_matches(full_name, p) for p in patterns or [])


# ------------------------------------------------------------------ manifests
@dataclass
class PluginInfo:
    """One plugin, as the Settings page shows it."""
    id: str
    name: str
    description: str
    icon_url: str                      # file URL, or "" (draw `glyph` or the initials)
    glyph: str
    version: str
    author: str
    builtin: bool                      # shipped in plugins/ (else state/plugins/)
    core: bool                         # part of hey-claude itself: can't be switched off
    can_disable: bool
    enabled: bool
    status: str                        # ok | broken | failed (its service crashed) | incompatible
    problems: list = field(default_factory=list)
    kinds: list = field(default_factory=list)         # contribution kinds, e.g. ["widgets", "tools"]
    contributes: dict = field(default_factory=dict)   # the manifest's contributes, as declared
    permissions: list = field(default_factory=list)   # [{"id", "text"}]
    settings: list = field(default_factory=list)      # the settings schema, see settings_schema()
    dir: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def _str_list(v) -> bool:
    return isinstance(v, list) and all(isinstance(x, str) and x for x in v)


def validate(man: dict, folder: Path | None = None, builtin: bool = False) -> tuple[list[str], list[str]]:
    """(problems, notes): a problem keeps the plugin from loading, a note is only logged."""
    from . import activities as A
    problems: list[str] = []
    notes: list[str] = []
    if not isinstance(man, dict):
        return ["the manifest isn't a JSON object"], notes
    pid = man.get("id")
    if not isinstance(pid, str) or not _ID.match(pid):
        problems.append("id must be 2-41 lower-case letters, digits, - or _, starting with a letter")
    elif folder is not None and folder.name != pid:
        problems.append(f"id {pid!r} doesn't match its folder {folder.name!r}")
    for k in ("name", "description"):
        if not isinstance(man.get(k), str) or not man.get(k).strip():
            problems.append(f"no {k}")
    api = man.get("api")
    if _major_minor(api) is None:
        problems.append("api must be major.minor, e.g. \"1.0\"")
    elif not compatible(api):
        problems.append(f"plugin API {api} isn't compatible with this hey-claude ({API_VERSION})")
    if man.get("core") and not builtin:
        problems.append("only built-in plugins can be core")
    perms = man.get("permissions", [])
    if not _str_list(perms) and perms != []:
        problems.append("permissions must be a list of names")
        perms = []
    for p in perms:
        if p not in PERMISSIONS:
            notes.append(f"unknown permission {p!r} (ignored)")
    c = man.get("contributes", {})
    if not isinstance(c, dict):
        return problems + ["contributes must be an object"], notes
    for kind in c:
        if kind not in KINDS:
            notes.append(f"contribution {kind!r} isn't known to this hey-claude ({API_VERSION}); ignored")
    for kind in ("widgets", "tools", "intents", "characters", "read_only_tools"):
        if kind in c and not _str_list(c[kind]):
            problems.append(f"{kind} must be a list of names")
    for r in c.get("routes", []) if isinstance(c.get("routes", []), list) else ["?"]:
        if r not in ROUTES:
            problems.append(f"unknown route {r!r} (one of {', '.join(ROUTES)})")
    for t in c.get("read_only_tools", []) if _str_list(c.get("read_only_tools", [])) else []:
        if not any(tool_matches(t, p) for p in c.get("tools", []) or []):
            problems.append(f"read_only_tools: {t!r} isn't one of its tools")
    for kind in ("service", "instant"):
        if kind in c:
            f = c[kind]
            if not isinstance(f, str) or not f.endswith(".py"):
                problems.append(f"{kind} must be a .py file in the plugin's folder")
            elif folder is not None and not (folder / f).is_file():
                problems.append(f"{kind}: no {f}")
    if "service" in c and "background" not in perms:
        problems.append("a service needs the 'background' permission")
    servers = c.get("mcp_servers", {})
    if not isinstance(servers, dict):
        problems.append("mcp_servers must map a server name to {command, args, env}")
        servers = {}
    for name, spec in servers.items():
        if not builtin and name in RESERVED_SERVERS:
            problems.append(f"mcp server name {name!r} is hey-claude's own")
        if not isinstance(spec, dict) or not isinstance(spec.get("command"), str):
            problems.append(f"mcp server {name!r} needs a command")
        if not any(tool_matches(f"mcp__{name}__x", p) for p in c.get("tools", []) or []):
            problems.append(f"mcp server {name!r}: list its tools (e.g. \"mcp__{name}\") under tools")
    acts = c.get("live_activities", [])
    if not isinstance(acts, list):
        problems.append("live_activities must be a list")
        acts = []
    seen = set()
    for a in acts:
        problems += [f"live activity {a.get('id') if isinstance(a, dict) else a!r}: {e}"
                     for e in A.validate_spec(a, folder)]
        if isinstance(a, dict):
            if a.get("id") in seen:
                problems.append(f"live activity {a.get('id')!r} declared twice")
            seen.add(a.get("id"))
    if acts and "service" not in c:
        notes.append("live activities without a service can only be started by hey-claude itself")
    sets = c.get("settings", [])
    if not isinstance(sets, list):
        problems.append("settings must be a list")
        sets = []
    keys = set()
    for s in sets:
        if not isinstance(s, dict) or not isinstance(s.get("key"), str) or not s.get("key"):
            problems.append("a setting needs a key")
            continue
        tag = f"setting {s['key']!r}"
        if s["key"] in keys:
            problems.append(f"{tag} declared twice")
        keys.add(s["key"])
        if s.get("type", "string") not in SETTING_TYPES:
            problems.append(f"{tag}: type is one of {', '.join(SETTING_TYPES)}")
        if s.get("type") == "enum" and not _str_list(s.get("options")):
            problems.append(f"{tag}: an enum needs options")
        if "config_key" in s and not builtin:
            problems.append(f"{tag}: only built-in plugins point at hey-claude's own config keys")
        if not s.get("label"):
            problems.append(f"{tag}: no label")
    if "prompt" in c and not isinstance(c["prompt"], str):
        problems.append("prompt must be a string")
    icons = c.get("icons", {})
    if not isinstance(icons, dict):
        problems.append("icons must map an MCP server to {name, icon}")
    return problems, notes


# ------------------------------------------------------------------ the registry
class PluginRegistry:
    """Discovers plugins, validates them, and says what is on.

    `cfg` is the app's whole config (the registry reads and writes cfg["plugins"] in place,
    so Settings and the running app agree); `config_path` is where set_enabled() saves
    (None: the default config.yaml; persist=False: nowhere, for tests and tools)."""

    def __init__(self, cfg: dict | None = None, config_path=None, dirs=None, persist: bool | None = None):
        self.cfg = cfg if cfg is not None else {}
        self.config_path = config_path
        self.persist = (cfg is not None) if persist is None else persist
        self.dirs = [Path(d) for d in (dirs or (BUILTIN_DIR, USER_DIR))]
        self._lock = threading.RLock()
        self._listeners: list = []
        self._failed: dict[str, str] = {}             # id -> why (a service or instant module crashed)
        self._modules: dict[str, object] = {}
        self._services: dict[str, object] = {}
        self._host = None                             # (activities hub) once start_services() ran
        self.plugins: dict[str, dict] = {}
        self.broken: dict[str, dict] = {}             # folder name -> {"dir", "problems", "builtin", "manifest"}
        self.rescan()
        self._snapshot = self._off()

    # -- discovery -------------------------------------------------------------------------
    def rescan(self):
        plugins, broken = {}, {}
        builtin_dir = self.dirs[0].resolve() if self.dirs else None
        for base in self.dirs:
            if not base.is_dir():
                continue
            builtin = builtin_dir is not None and base.resolve() == builtin_dir
            for man_file in sorted(base.glob(f"*/{MANIFEST}")):
                folder = man_file.parent
                try:
                    with open(man_file, encoding="utf-8") as f:
                        man = json.load(f)
                except (OSError, ValueError) as e:
                    broken[folder.name] = {"dir": str(folder), "problems": [f"bad {MANIFEST}: {e}"],
                                           "builtin": builtin, "manifest": {}}
                    print(f"[plugins] {folder.name}: bad {MANIFEST} ({e})")
                    continue
                try:
                    problems, notes = validate(man, folder, builtin)
                except Exception as e:                  # a validator bug never takes the others down
                    problems, notes = [f"couldn't check the manifest: {e}"], []
                for n in notes:
                    print(f"[plugins] {folder.name}: {n}")
                if not problems and man["id"] in plugins:
                    problems = [f"{man['id']!r} is already installed ({plugins[man['id']]['dir']})"]
                if problems:
                    broken[folder.name] = {"dir": str(folder), "problems": problems, "builtin": builtin,
                                           "manifest": man if isinstance(man, dict) else {}}
                    print(f"[plugins] {folder.name} skipped: {'; '.join(problems)}")
                    continue
                man = dict(man, dir=str(folder), builtin=builtin)
                man.setdefault("contributes", {})
                plugins[man["id"]] = man
        with self._lock:
            self.plugins, self.broken = plugins, broken
        _clear_loader_caches()

    # -- enabled -------------------------------------------------------------------------
    def _section(self) -> dict:
        p = self.cfg.get("plugins")
        if not isinstance(p, dict):
            p = self.cfg["plugins"] = {}
        return p

    def _list(self, key: str) -> list[str]:
        sec = self.cfg.get("plugins")
        v = sec.get(key) if isinstance(sec, dict) else None
        return [str(x) for x in v] if isinstance(v, list) else []

    def _disabled(self) -> list[str]:
        return self._list("disabled")

    def _opted_in(self) -> list[str]:
        """Your own plugins (state/plugins/) that you switched on: they start off."""
        return self._list("enabled")

    def can_disable(self, pid: str) -> bool:
        man = self.plugins.get(pid)
        return man is not None and not man.get("core")

    def is_enabled(self, pid: str) -> bool:
        """A built-in is on unless it's in plugins.disabled (core ones are always on); one of
        yours (state/plugins/) only once it's in plugins.enabled, since it runs arbitrary code.
        Never while its service crashed this session."""
        man = self.plugins.get(pid)
        if man is None:
            return False
        if man.get("core"):
            return True
        if pid in self._failed or pid in self._disabled():
            return False
        return bool(man.get("builtin")) or pid in self._opted_in()

    def feature_on(self, pid: str) -> bool:
        """For hey-claude's own code asking about a built-in feature: on when that plugin is on,
        or when there is no such plugin (nothing removes a feature no plugin claims)."""
        return pid not in self.plugins or self.is_enabled(pid)

    def set_enabled(self, pid: str, on: bool) -> bool:
        """Switch a plugin on or off, save it (plugins.disabled, and plugins.enabled for your own
        plugins, in config.yaml) and tell the listeners. Returns whether anything changed.
        ValueError for an unknown or core plugin."""
        if pid not in self.plugins:
            raise ValueError(f"no plugin {pid!r}")
        if not on and not self.can_disable(pid):
            raise ValueError(f"{pid!r} is part of hey-claude and can't be switched off")
        user = not self.plugins[pid].get("builtin")
        with self._lock:
            was = self.is_enabled(pid)
            dis, opt = self._disabled(), self._opted_in()
            if on:
                self._failed.pop(pid, None)            # switching it on again is a retry
                new_dis = [x for x in dis if x != pid]
                new_opt = opt if (not user or pid in opt) else opt + [pid]
            else:
                new_dis = dis if (user or pid in dis) else dis + [pid]
                new_opt = [x for x in opt if x != pid]
                if user:
                    new_dis = [x for x in dis if x != pid]
            if new_dis == dis and new_opt == opt:
                if was == on:
                    return False
                self.sync()
                return True
            # in place: whoever holds cfg["plugins"] (Settings, the app) sees the same lists
            sec = self._section()
            sec["disabled"] = sorted(new_dis)
            keys = {CONFIG_KEY: sorted(new_dis)}
            if new_opt != opt or "enabled" in sec:
                sec["enabled"] = sorted(new_opt)
                keys[ENABLED_KEY] = sorted(new_opt)
            if self.persist:
                from . import config
                config.save_keys(keys, self.config_path)
        self.sync()
        return True

    def _off(self) -> set[str]:
        return {pid for pid in self.plugins if not self.is_enabled(pid)}

    def sync(self) -> list[str]:
        """Tell the listeners about plugins switched on or off since last time (set_enabled
        calls it; the app calls it after Settings saved the whole config). Returns their ids."""
        with self._lock:
            now = self._off()
            changed = sorted(now ^ self._snapshot)
            self._snapshot = now
        for pid in changed:
            on = self.is_enabled(pid)
            self._apply_service(pid, on)
            for fn in list(self._listeners):
                try:
                    fn(pid, on)
                except Exception as e:
                    print(f"[plugins] a listener failed on {pid}: {e}")
        return changed

    def subscribe(self, fn) -> callable:
        """fn(plugin_id, enabled) after a plugin was switched on or off (any thread). Returns
        a function that unsubscribes."""
        self._listeners.append(fn)
        return lambda: self._listeners.remove(fn) if fn in self._listeners else None

    def mark_failed(self, pid: str, why: str):
        """A plugin's own code crashed: it is off for the rest of the session (not saved)."""
        if pid in self.plugins and not self.plugins[pid].get("core") and pid not in self._failed:
            print(f"[plugins] {pid} switched off for this session: {why}")
            self._failed[pid] = why
            self.sync()

    # -- what the Settings page shows ----------------------------------------------------------
    def list(self) -> list[PluginInfo]:
        out = []
        for pid, man in sorted(self.plugins.items(), key=lambda kv: (not kv[1].get("core"), kv[1]["name"].lower())):
            c = man.get("contributes") or {}
            out.append(PluginInfo(
                id=pid, name=man["name"], description=man["description"], icon_url=self._icon_url(man),
                glyph=str(man.get("glyph", "")), version=str(man.get("version", "")),
                author=str(man.get("author", "")), builtin=bool(man["builtin"]), core=bool(man.get("core")),
                can_disable=self.can_disable(pid), enabled=self.is_enabled(pid),
                status="failed" if pid in self._failed else "ok",
                problems=[self._failed[pid]] if pid in self._failed else [],
                kinds=[k for k in c if k in KINDS], contributes=json.loads(json.dumps(c)),
                permissions=[{"id": p, "text": PERMISSIONS.get(p, p)} for p in man.get("permissions", [])],
                settings=self.settings_schema(pid), dir=man["dir"]))
        for folder, b in sorted(self.broken.items()):
            m = b.get("manifest") or {}
            incompatible = any("isn't compatible" in p for p in b["problems"])
            out.append(PluginInfo(
                id=str(m.get("id") or folder), name=str(m.get("name") or folder),
                description=str(m.get("description") or ""), icon_url="", glyph="", version=str(m.get("version", "")),
                author=str(m.get("author", "")), builtin=bool(b["builtin"]), core=False, can_disable=False,
                enabled=False, status="incompatible" if incompatible else "broken", problems=list(b["problems"]),
                dir=b["dir"]))
        return out

    def info(self, pid: str) -> PluginInfo | None:
        return next((p for p in self.list() if p.id == pid), None)

    def _icon_url(self, man: dict) -> str:
        icon = str(man.get("icon") or "")
        if not icon:
            return ""
        own = Path(man["dir"]) / icon
        if own.suffix and own.is_file():
            return own.resolve().as_uri()
        bundled = ROOT / "ui" / "icons" / "services" / f"{icon}.svg"
        return bundled.resolve().as_uri() if bundled.is_file() else ""

    def settings_schema(self, pid: str) -> list[dict]:
        """[{key, type, label, help, default, options, config_key}]: config_key is the dotted
        key the value lives at (a built-in points at its existing section, e.g. weather.location;
        a user plugin's live under plugins.settings.<id>.<key>)."""
        man = self.plugins.get(pid) or {}
        out = []
        for s in (man.get("contributes") or {}).get("settings", []):
            d = {"key": s["key"], "type": s.get("type", "string"), "label": s["label"],
                 "help": s.get("help", ""), "default": s.get("default"), "options": list(s.get("options") or []),
                 "config_key": s.get("config_key") or f"{SETTINGS_KEY}.{pid}.{s['key']}"}
            out.append(d)
        return out

    def setting(self, pid: str, key: str):
        """A plugin setting's value (its default when unset)."""
        for s in self.settings_schema(pid):
            if s["key"] == key:
                d = self.cfg
                for part in s["config_key"].split("."):
                    if not isinstance(d, dict) or part not in d:
                        return s["default"]
                    d = d[part]
                return d
        raise KeyError(f"{pid} has no setting {key!r}")

    def set_setting(self, pid: str, key: str, value):
        s = next((s for s in self.settings_schema(pid) if s["key"] == key), None)
        if s is None:
            raise KeyError(f"{pid} has no setting {key!r}")
        *parents, leaf = s["config_key"].split(".")
        d = self.cfg
        for p in parents:
            if not isinstance(d.get(p), dict):
                d[p] = {}
            d = d[p]
        d[leaf] = value
        if self.persist:
            from . import config
            config.save_keys({s["config_key"]: value}, self.config_path)

    # -- what the loaders ask ----------------------------------------------------------------
    def _owners(self, kind: str, item: str, match) -> tuple[bool, bool]:
        """(claimed by an enabled plugin, claimed by a disabled one)."""
        on = off = False
        for pid, man in self.plugins.items():
            pats = (man.get("contributes") or {}).get(kind) or []
            if any(match(item, p) for p in pats):
                if self.is_enabled(pid):
                    on = True
                else:
                    off = True
        return on, off

    def _active(self, kind: str, item: str, match=lambda a, b: a == b) -> bool:
        """Unclaimed things stay on; claimed ones are on while any plugin that claims them is."""
        on, off = self._owners(kind, item, match)
        return on or not off

    def widget_enabled(self, wid: str) -> bool:
        return self._active("widgets", wid)

    def character_enabled(self, cid: str) -> bool:
        return self._active("characters", cid)

    def intent_enabled(self, intent: str) -> bool:
        return self._active("intents", intent, intent_matches)

    def route_enabled(self, route: str) -> bool:
        return self._active("routes", route)

    def tool_enabled(self, name: str) -> bool:
        return self._active("tools", name, tool_matches)

    def disabled_tools(self) -> list[str]:
        """Tool patterns of switched-off plugins that no switched-on plugin also claims: the
        agent gets them as --disallowedTools, and hey-claude's own servers leave them out."""
        on = [p for pid, m in self.plugins.items() if self.is_enabled(pid)
              for p in (m.get("contributes") or {}).get("tools") or []]
        out = []
        for pid, man in self.plugins.items():
            if self.is_enabled(pid):
                continue
            for p in (man.get("contributes") or {}).get("tools") or []:
                if p not in on and p not in out:
                    out.append(p)
        return out

    def _enabled_user(self):
        for pid, man in self.plugins.items():
            if self.is_enabled(pid):
                yield pid, man

    def widget_dirs(self) -> list[Path]:
        """<plugin>/widgets/ of every plugin that has one (their cards join widgets/)."""
        return [Path(m["dir"]) / "widgets" for m in self.plugins.values() if (Path(m["dir"]) / "widgets").is_dir()]

    def character_dirs(self) -> list[Path]:
        return [Path(m["dir"]) / "characters" for m in self.plugins.values()
                if (Path(m["dir"]) / "characters").is_dir()]

    def mcp_servers(self, python: str | None = None) -> dict:
        """mcpServers entries the enabled plugins bring. In command, args and env values
        ${PLUGIN_DIR} is the plugin's folder and ${PYTHON} hey-claude's interpreter."""
        import sys
        py = python or sys.executable
        out = {}
        for pid, man in self._enabled_user():
            for name, spec in ((man.get("contributes") or {}).get("mcp_servers") or {}).items():
                def sub(v, d=man["dir"]):
                    return str(v).replace("${PLUGIN_DIR}", d).replace("${PYTHON}", py)
                out[name] = {"command": sub(spec["command"]), "args": [sub(a) for a in spec.get("args") or []],
                             "env": {k: sub(v) for k, v in (spec.get("env") or {}).items()}}
        return out

    def read_only_tools(self) -> list[str]:
        """Tools the enabled plugins declare read-only: pre-allowed for the agent (everything
        else a plugin's server offers asks on the island first)."""
        out = []
        for _, man in self._enabled_user():
            out += [t for t in (man.get("contributes") or {}).get("read_only_tools") or [] if t not in out]
        return out

    def prompts(self) -> str:
        parts = [str((m.get("contributes") or {}).get("prompt") or "").strip() for _, m in self._enabled_user()]
        return "".join(" " + p for p in parts if p)

    def service_icon(self, server_key: str) -> dict | None:
        """{"name", "icon_path", "glyph"} for an MCP server an enabled plugin brings."""
        for _, man in self._enabled_user():
            icons = (man.get("contributes") or {}).get("icons") or {}
            spec = next((v for k, v in icons.items() if re.sub(r"[\s\-]+", "_", k).lower() == server_key), None)
            if isinstance(spec, dict):
                out = {"name": spec.get("name", ""), "glyph": spec.get("glyph", "")}
                if spec.get("icon"):
                    out["icon_path"] = str(Path(man["dir"]) / spec["icon"])
                return out
        return None

    def activity_specs(self, pid: str) -> dict[str, dict]:
        man = self.plugins.get(pid) or {}
        return {a["id"]: dict(a, plugin=pid, dir=man.get("dir", ""))
                for a in (man.get("contributes") or {}).get("live_activities") or []}

    # -- plugins' own Python (instant commands, services) ------------------------------------------
    def _module(self, pid: str, file: str):
        key = f"{pid}:{file}"
        if key not in self._modules:
            path = Path(self.plugins[pid]["dir"]) / file
            spec = importlib.util.spec_from_file_location(f"heyclaude_plugin_{pid}_{path.stem}", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            self._modules[key] = mod
        return self._modules[key]

    def instant(self, text: str, budget_ms: float = 30.0) -> tuple[str, dict] | None:
        """(plugin id, {"say": str, "widget": spec?}) from the first enabled plugin's
        instant.py whose match(text) claims it. A plugin that raises is switched off for the
        session; one slower than the budget is logged (it runs in the request's path)."""
        for pid, man in list(self._enabled_user()):
            f = (man.get("contributes") or {}).get("instant")
            if not f:
                continue
            t = time.perf_counter()
            try:
                got = self._module(pid, f).match(text)
            except Exception as e:
                self.mark_failed(pid, f"instant: {e}")
                continue
            ms = (time.perf_counter() - t) * 1000
            if ms > budget_ms:
                print(f"[plugins] {pid}: instant match took {ms:.0f} ms (budget {budget_ms:.0f})")
            if isinstance(got, dict) and isinstance(got.get("say", ""), str) and (got.get("say") or got.get("widget")):
                return pid, got
        return None

    def start_services(self, hub) -> None:
        """Start every enabled plugin's service (its own thread). `hub` is the live-activity hub."""
        self._host = hub
        for pid, _ in list(self._enabled_user()):
            self._apply_service(pid, True)

    def stop_services(self):
        for pid in list(self._services):
            self._apply_service(pid, False)
        self._host = None

    def _apply_service(self, pid: str, on: bool):
        man = self.plugins.get(pid)
        if man is None:
            return
        f = (man.get("contributes") or {}).get("service")
        hub = self._host
        if not on:
            if hub is not None:
                hub.end_plugin(pid, "plugin switched off")
            mod = self._services.pop(pid, None)
            if mod is not None and hasattr(mod, "stop"):
                try:
                    mod.stop()
                except Exception as e:
                    print(f"[plugins] {pid}: stop failed: {e}")
            return
        if not f or hub is None or pid in self._services:
            return
        ctx = PluginContext(self, pid, hub)

        def run():
            try:
                mod = self._module(pid, f)
                self._services[pid] = mod
                mod.start(ctx)
            except Exception as e:
                self._services.pop(pid, None)
                self.mark_failed(pid, f"service: {e}")
        threading.Thread(target=run, daemon=True, name=f"plugin-{pid}").start()


class PluginContext:
    """What a plugin's service gets: its activities, its settings, a log, a folder for its data."""

    def __init__(self, reg: PluginRegistry, pid: str, hub):
        self.id = pid
        self.dir = Path(reg.plugins[pid]["dir"])
        self.data_dir = STATE_DIR / "plugin-data" / pid
        self.activities = hub.scoped(pid, reg.activity_specs(pid))
        self._reg = reg

    def setting(self, key: str):
        return self._reg.setting(self.id, key)

    @property
    def enabled(self) -> bool:
        """A long-running loop checks this and returns once the plugin is switched off."""
        return self._reg.is_enabled(self.id)

    def log(self, msg: str):
        print(f"[plugin:{self.id}] {msg}")


# ------------------------------------------------------------------ the app's registry
_REGISTRY: PluginRegistry | None = None


def init(cfg: dict, config_path=None, dirs=None) -> PluginRegistry:
    """The app's registry, over its config (call once at start)."""
    global _REGISTRY
    old = _REGISTRY
    _REGISTRY = PluginRegistry(cfg, config_path, dirs=dirs)
    if old is not None:                          # whoever subscribed early (the island) keeps listening
        _REGISTRY._listeners = old._listeners
    on = [p for p in _REGISTRY.plugins if _REGISTRY.is_enabled(p)]
    print(f"[plugins] {len(on)} of {len(_REGISTRY.plugins)} on"
          + (f"; broken: {', '.join(_REGISTRY.broken)}" if _REGISTRY.broken else ""))
    return _REGISTRY


def get() -> PluginRegistry:
    """The app's registry; before init() (tests, tools) one with everything on, saved nowhere."""
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = PluginRegistry(None)
    return _REGISTRY


def reset(reg: PluginRegistry | None = None):
    """Tests: swap the app's registry (None: a fresh default one on next get())."""
    global _REGISTRY
    _REGISTRY = reg
    _clear_loader_caches()


def _clear_loader_caches():
    """Widgets and characters cache what they load; a rescan may add folders."""
    import sys
    w = sys.modules.get("heyclaude.widgets")
    if w is not None:
        for fn in ("registry", "load"):
            c = getattr(w, fn, None)
            if c is not None and hasattr(c, "cache_clear"):
                c.cache_clear()
