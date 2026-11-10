"""Plugins (heyclaude/plugins.py) and live activities (heyclaude/activities.py):
manifest validation, discovery with a broken plugin isolated, enable/disable saved in
config.yaml, a switched-off plugin's card / tools / intents / routes / character really
gone, a user plugin snapping in, and live-activity arbitration and frame budget.

    .venv\\Scripts\\python tests\\test_plugins.py
"""
import json
import os
import shutil
import sys
import tempfile
import time
import types
from pathlib import Path
from unittest import mock

sys.stdout.reconfigure(line_buffering=True)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from heyclaude import activities as A  # noqa: E402
from heyclaude import plugins as P  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
GOOD = {"id": "lyrics", "api": "1.0", "name": "Lyrics", "description": "Synced lyrics on the island.",
        "permissions": ["network", "background"],
        "contributes": {"service": "service.py",
                        "live_activities": [{"id": "line", "template": "media", "priority": "active"}]}}


def _tmp() -> Path:
    return Path(tempfile.mkdtemp(prefix="hc-plugins-"))


def _write(folder: Path, man: dict, files: dict | None = None) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "plugin.json").write_text(json.dumps(man) if isinstance(man, dict) else man, encoding="utf-8")
    for name, text in (files or {}).items():
        p = folder / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return folder


def _wait(cond, timeout=3.0):
    t = time.time()
    while time.time() - t < timeout:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


def _with(reg):
    """Run the app with `reg` as its registry (widgets, intents... read plugins.get())."""
    P.reset(reg)
    return reg


# ------------------------------------------------------------------ built-ins
def test_builtin_plugins_are_valid_and_cover_every_feature():
    reg = P.PluginRegistry(None)
    assert not reg.broken, reg.broken
    assert len(reg.plugins) >= 20
    assert all(p.enabled for p in reg.list())                         # everything on by default
    core = [p for p in reg.list() if p.core]
    assert [p.id for p in core] == ["core"] and not core[0].can_disable
    # every card, hey-claude tool, intent and character pack belongs to a plugin
    from heyclaude import characters as CH
    from heyclaude import mcp_info
    from heyclaude import widgets as W
    from heyclaude.intents import grammar  # noqa: F401
    from heyclaude.mcp_windows import registry as R

    def claimed(kind, item, match=lambda a, b: a == b):
        return any(match(item, p) for m in reg.plugins.values() for p in m["contributes"].get(kind) or [])
    assert [w for w in W.registry() if not claimed("widgets", w)] == []
    assert [t.full_name for t in R.TOOLS.values() if not claimed("tools", t.full_name, P.tool_matches)] == []
    assert [t for t in mcp_info.tool_names([]) if not claimed("tools", t, P.tool_matches)] == []
    intents = {"volume.set", "brightness.step", "theme.set", "media.next", "timer.set", "app.open", "settings.open",
               "screen.lock", "clock.time", "radio.set", "learn.teach", "media.now_playing", "timer.list"}
    assert [i for i in intents if not claimed("intents", i, P.intent_matches)] == []
    assert [c for c in CH.registry() if not claimed("characters", c)] == []
    # the settings a built-in shows point at the config keys that exist
    from heyclaude import config
    d = config.defaults()
    for p in reg.list():
        for s in p.settings:
            node = d
            for part in s["config_key"].split("."):
                assert isinstance(node, dict) and part in node, (p.id, s["config_key"])
                node = node[part]


def test_settings_page_surface():
    reg = P.PluginRegistry(None)
    w = reg.info("weather")
    assert w.name == "Weather" and w.description and w.can_disable and w.enabled and w.status == "ok"
    assert set(w.kinds) >= {"widgets", "tools", "routes", "settings"}
    assert [s["config_key"] for s in w.settings] == ["weather.location", "weather.unit"]
    assert w.settings[1]["type"] == "enum" and w.settings[1]["options"] == ["C", "F"]
    assert {"id": "network", "text": P.PERMISSIONS["network"]} in w.permissions
    tg = reg.info("telegram")
    assert tg.icon_url.endswith("telegram.svg") and tg.icon_url.startswith("file:")
    d = w.as_dict()
    json.dumps(d)                                                     # plain data for any UI
    assert {"id", "name", "description", "icon_url", "glyph", "enabled", "can_disable", "core", "kinds",
            "contributes", "permissions", "settings", "status", "problems", "builtin"} <= set(d)
    cfg = {"weather": {"location": "Tashkent"}}
    reg2 = P.PluginRegistry(cfg, persist=False)
    assert reg2.setting("weather", "location") == "Tashkent" and reg2.setting("weather", "unit") == "C"
    reg2.set_setting("weather", "unit", "F")
    assert cfg["weather"]["unit"] == "F"


# ------------------------------------------------------------------ validation
def test_manifest_validation():
    def probs(man, folder=None, builtin=False):
        return P.validate(man, folder, builtin)[0]
    d = _tmp()
    f = _write(d / "lyrics", GOOD, {"service.py": "def start(ctx): pass\n"})
    assert probs(GOOD, f) == []
    assert any("folder" in p for p in probs(dict(GOOD, id="other"), f))
    assert any("id must" in p for p in probs(dict(GOOD, id="Bad Id")))
    assert any("api" in p for p in probs(dict(GOOD, api="one")))
    assert any("isn't compatible" in p for p in probs(dict(GOOD, api="2.0")))
    assert probs(dict(GOOD, api="1.7"), f) == []                       # a newer minor still loads
    probs_, notes = P.validate(dict(GOOD, contributes=dict(GOOD["contributes"], hologram={"x": 1})), f)
    assert probs_ == [] and any("hologram" in n for n in notes)       # unknown kinds: ignored, logged
    assert any("background" in p for p in probs(dict(GOOD, permissions=["network"]), f))
    assert any("only built-in" in p for p in probs(dict(GOOD, core=True), f))
    assert probs(dict(GOOD, core=True), f, builtin=True) == []
    assert any("no service.py" in p for p in probs(GOOD, d))          # the file must be there
    srv = dict(GOOD, contributes={"mcp_servers": {"weather": {"command": "x"}}, "tools": ["mcp__weather"]})
    assert any("hey-claude's own" in p for p in probs(srv))
    srv = dict(GOOD, contributes={"mcp_servers": {"lyr": {"command": "x"}}})
    assert any("list its tools" in p for p in probs(srv))
    assert any("unknown route" in p for p in probs(dict(GOOD, contributes={"routes": ["teleport"]})))
    ro = dict(GOOD, contributes={"tools": ["mcp__lyr"], "read_only_tools": ["mcp__other__x"]})
    assert any("isn't one of its tools" in p for p in probs(ro))
    sets = dict(GOOD, contributes={"settings": [{"key": "k", "type": "enum", "label": "K"}]})
    assert any("options" in p for p in probs(sets))
    sets = dict(GOOD, contributes={"settings": [{"key": "k", "label": "K", "config_key": "weather.unit"}]})
    assert any("only built-in" in p for p in probs(sets))
    # live activities: the frame budget rule
    act = lambda **kw: dict(GOOD, contributes={"service": "service.py",  # noqa: E731
                                               "live_activities": [dict({"id": "a"}, **kw)]})
    assert probs(act(template="glance"), f) == []
    assert any("fps_reason" in p for p in probs(act(fps=8, priority="active"), f))
    assert probs(act(fps=8, priority="active", fps_reason="waveform"), f) == []
    assert any("above the budget" in p for p in probs(act(fps=24, priority="info", fps_reason="x"), f))
    assert any("above the budget" in p for p in probs(act(fps=60, priority="alert", fps_reason="x"), f))
    assert any("template" in p for p in probs(act(template="hologram"), f))
    assert any("priority" in p for p in probs(act(priority="urgent!"), f))
    assert any("no Activity.qml" in p for p in probs(act(qml="Activity.qml"), f))
    assert any("label" in p for p in probs(act(actions=[{"id": "x"}]), f))


# ------------------------------------------------------------------ discovery
def test_discovery_isolates_broken_plugins():
    d = _tmp()
    builtin, user = d / "builtin", d / "user"
    _write(builtin / "weather", {"id": "weather", "api": "1.0", "name": "W", "description": "w",
                                 "contributes": {"widgets": ["weather"]}})
    _write(user / "notjson", "{ this isn't json")
    _write(user / "wrongid", dict(GOOD, id="another"))
    _write(user / "weather", {"id": "weather", "api": "1.0", "name": "Mine", "description": "takes a built-in id"})
    _write(user / "future", dict(GOOD, id="future", api="2.0"))
    _write(user / "lyrics", GOOD, {"service.py": "def start(ctx): pass\n"})
    _write(user / "crashy", {"id": "crashy", "api": "1.0", "name": "Crashy", "description": "raises",
                             "contributes": {"instant": "instant.py"}},
           {"instant.py": "def match(text):\n    raise RuntimeError('boom')\n"})
    _write(user / "hello", {"id": "hello", "api": "1.0", "name": "Hello", "description": "says hi",
                            "contributes": {"instant": "instant.py"}},
           {"instant.py": "def match(text):\n    return {'say': 'Hi!'} if text == 'hello plugin' else None\n"})
    off = P.PluginRegistry({}, dirs=[builtin, user], persist=False)
    assert off.is_enabled("weather") and not any(off.is_enabled(p) for p in ("crashy", "hello", "lyrics"))
    assert off.instant("hello plugin") is None                       # yours start off: they run code
    reg = P.PluginRegistry({"plugins": {"enabled": ["crashy", "hello", "lyrics", "badsvc"]}},
                           dirs=[builtin, user], persist=False)
    assert sorted(reg.plugins) == ["crashy", "hello", "lyrics", "weather"]
    assert reg.plugins["weather"]["name"] == "W" and reg.plugins["weather"]["builtin"]
    assert sorted(reg.broken) == ["future", "notjson", "weather", "wrongid"]
    by = {p.dir: p for p in reg.list()}
    assert by[str(user / "future")].status == "incompatible"
    assert by[str(user / "notjson")].status == "broken" and not by[str(user / "notjson")].enabled
    # a plugin whose code raises is switched off for the session; the next one still answers
    seen = []
    reg.subscribe(lambda pid, on: seen.append((pid, on)))
    assert reg.instant("hello plugin") == ("hello", {"say": "Hi!"})
    assert not reg.is_enabled("crashy") and ("crashy", False) in seen
    assert reg.info("crashy").status == "failed" and "boom" in reg.info("crashy").problems[0]
    assert reg.instant("hello plugin") == ("hello", {"say": "Hi!"})
    reg.set_enabled("crashy", True)                                   # switching it on again is a retry
    assert reg.is_enabled("crashy")
    # a service that raises: off, its activities gone, nothing else touched
    _write(user / "badsvc", {"id": "badsvc", "api": "1.0", "name": "Bad", "description": "service raises",
                             "permissions": ["background"],
                             "contributes": {"service": "service.py", "live_activities": [{"id": "x"}]}},
           {"service.py": "def start(ctx):\n    ctx.activities.start('x', {'text': 'hi'})\n    raise OSError('no device')\n"})
    reg.rescan()
    hub = A.ActivityHub()
    reg.start_services(hub)
    assert _wait(lambda: not reg.is_enabled("badsvc"))
    assert _wait(lambda: not hub.running())
    assert reg.is_enabled("lyrics") and reg.is_enabled("hello")
    reg.stop_services()


def test_enable_disable_persists_and_notifies():
    from heyclaude import config
    d = _tmp()
    path = d / "config.yaml"
    shutil.copyfile(config.EXAMPLE, path)
    cfg = config.load(path)
    assert cfg["plugins"]["disabled"] == []
    reg = P.PluginRegistry(cfg, path)
    seen = []
    reg.subscribe(lambda pid, on: seen.append((pid, on)))
    assert reg.set_enabled("telegram", False) is True
    assert reg.set_enabled("telegram", False) is False               # no change, no event
    assert seen == [("telegram", False)] and not reg.is_enabled("telegram")
    assert cfg["plugins"]["disabled"] == ["telegram"]                 # the app's own config dict, in place
    again = config.load(path)
    assert again["plugins"]["disabled"] == ["telegram"]
    assert not P.PluginRegistry(again, path).is_enabled("telegram")
    try:
        reg.set_enabled("core", False)
        raise AssertionError("core switched off")
    except ValueError:
        pass
    try:
        reg.set_enabled("nope", False)
        raise AssertionError("unknown plugin")
    except ValueError:
        pass
    # Settings saves the whole config instead: sync() tells the listeners what changed
    cfg["plugins"]["disabled"] = ["news"]
    assert reg.sync() == ["news", "telegram"]
    assert ("telegram", True) in seen and ("news", False) in seen
    reg.set_enabled("news", True)
    assert config.load(path)["plugins"]["disabled"] == []
    assert config.is_live("plugins.disabled")


# ------------------------------------------------------------------ really gone
def test_switched_off_plugins_are_really_gone():
    from heyclaude import brain as B
    from heyclaude import characters as CH
    from heyclaude import config
    from heyclaude import mcp_info
    from heyclaude import widgets as W
    from heyclaude.intents import IntentEngine
    cfg = config.defaults()
    reg = _with(P.PluginRegistry(cfg, persist=False))
    try:
        eng = IntentEngine({"classifier": False}, state_dir=None)
        wx_card = W.registry()["weather"]["example"]
        on_args = _agent_args(cfg)
        assert W.normalise({"widget": "weather", "data": wx_card})["widget"] == "weather"
        assert eng.match("next song").intent == "media.next"
        assert "echo" in CH.registry()
        for pid in ("weather", "convert", "media", "telegram", "character-echo", "timers", "news"):
            reg.set_enabled(pid, False)
        # cards: the text fallback (or nothing), never offered to the models, no tool card
        assert W.normalise({"widget": "weather", "data": wx_card, "text": "Sunny, 20."})["widget"] == "text"
        assert W.normalise({"widget": "weather", "data": wx_card}) is None
        assert W.normalise({"widget": "calendar_list", "data": W.registry()["calendar_list"]["example"]})["widget"] \
            == "calendar_list"                                         # others untouched
        assert W.from_tool("mcp__info__calculate", {"expression": "2+2"},
                           {"expression": "2+2", "result": "4", "kind": "math"}) is None
        txt = W.prompt_text()
        assert "- weather:" not in txt and "- convert:" not in txt and "- telegram_draft:" not in txt
        assert "- timer:" not in txt and "Timer: the island" not in txt and "- calendar_list:" in txt
        # tools: not started, not offered, not allowed
        skip = reg.disabled_tools()
        assert "mcp__weather" in skip and "mcp__info__calculate" in skip and "mcp__telegram" in skip
        assert "mcp__info__calculate" not in mcp_info.tool_names(skip) and "mcp__info__define" in mcp_info.tool_names(skip)
        assert "calculate" not in mcp_info.prompt(skip) and "define" in mcp_info.prompt(skip)
        servers = B.plugin_filter_servers({"weather": {"command": "py"}, "weather-mcp": {"command": "npx"},
                                           "info": {"command": "py", "env": {"A": "1"}}, "windows": {"command": "py"}},
                                          skip)
        assert sorted(servers) == ["info", "windows"]
        assert json.loads(servers["info"]["env"][P.SKIP_ENV]) == skip and servers["info"]["env"]["A"] == "1"
        with mock.patch.dict(os.environ, {P.SKIP_ENV: json.dumps(skip)}):
            assert "calculate" not in mcp_info.tools()                 # what the info server serves
        args = _agent_args(cfg)
        allowed = args[args.index("--allowedTools") + 1].split(",")
        dis = args[args.index("--disallowedTools") + 1].split(",")
        assert "mcp__weather__get_weather" not in allowed and "mcp__info__calculate" not in allowed
        assert "mcp__windows__play_spotify" not in allowed and "mcp__claude_ai_Spotify__search" not in allowed
        assert "mcp__windows__set_volume" in on_args[on_args.index("--allowedTools") + 1].split(",")
        assert {"mcp__weather", "mcp__info__calculate", "mcp__telegram", "mcp__windows__media_control"} <= set(dis)
        prompt = args[args.index("--append-system-prompt") + 1]
        assert "mcp__weather__get_weather" not in prompt and "headlines card" not in prompt
        # intents: not claimed (the request goes on to Claude); other plugins' still are
        assert eng.match("next song") is None and eng.match("timer for 5 minutes") is None
        assert eng.match("volume 30").intent == "volume.set"
        # the local weather route
        fake = types.SimpleNamespace(wx=types.SimpleNamespace(query=lambda t, prev_turn_was_weather: "Q"), convo=None)
        assert B.Brain.weather_query(fake, "weather tomorrow") is None
        # the character pack
        assert "echo" not in CH.registry() and "wren" in CH.registry()
        # back on: everything returns
        for pid in ("weather", "convert", "media", "telegram", "character-echo", "timers", "news"):
            reg.set_enabled(pid, True)
        assert reg.disabled_tools() == []
        assert B.Brain.weather_query(fake, "weather tomorrow") == "Q"
        assert eng.match("next song").intent == "media.next" and "echo" in CH.registry()
        assert _agent_args(cfg) == on_args                             # default config: exactly as before
    finally:
        P.reset(None)


def test_pc_control_and_media_off_release_the_control_shortcut():
    """With pc_control or media switched off, the no-model "control" shortcut stops claiming their
    phrases, the quick model stops offering them, and the agent's prompt drops their lines."""
    from heyclaude import brain as B
    from heyclaude import config
    cfg = config.defaults()
    reg = _with(P.PluginRegistry(cfg, persist=False))
    proj = B.Projects({"hey claude": "x"})
    try:
        assert B.local_route("volume 30", proj) == ("control", "volume 30")
        assert B.local_route("next song", proj) == ("control", "next song")
        assert B.AGENT_WINDOWS_PROMPT == B.windows_prompt(True, True)
        assert "Spotify" in B.windows_prompt(True, True) and "media playback" in B.windows_prompt(True, True)
        full_sys = B.system_prompt(proj)
        assert "volume, brightness" in full_sys and "music" in full_sys
        reg.set_enabled("media", False)
        assert B.local_route("next song", proj) is None                     # no tool for it: not claimed
        assert B.local_route("play the song bohemian rhapsody", proj) is None
        assert B.local_route("volume 30", proj) == ("control", "volume 30")   # pc_control still on
        sp = B.system_prompt(proj)
        assert "music" not in sp and "volume, brightness" in sp
        no_media = B.windows_prompt(True, False)
        assert "Spotify" not in no_media and "play_spotify" not in no_media and "media playback" not in no_media
        assert "next song" not in no_media and "Volume 30." in no_media
        args = _agent_args(cfg)
        prompt = args[args.index("--append-system-prompt") + 1]
        assert "Spotify" not in prompt and "media_control next" not in prompt and "mcp__windows tools" in prompt
        reg.set_enabled("pc_control", False)
        assert B.local_route("volume 30", proj) is None and B.local_route("dark mode on", proj) is None
        assert "controlling this PC" not in B.system_prompt(proj)
        assert B.windows_prompt(False, False) == ""
        reg.set_enabled("media", True)
        assert B.local_route("next song", proj) == ("control", "next song") and B.local_route("mute", proj) is None
        assert "controlling this PC (music)" in B.system_prompt(proj)
        reg.set_enabled("pc_control", True)
        assert B.system_prompt(proj) == full_sys
    finally:
        P.reset(None)


_AGENT_TMP = None


def _agent_args(cfg):
    from heyclaude import brain as B
    from heyclaude import config
    a = B.Agent.__new__(B.Agent)
    a.cfg = dict(cfg, agent=dict(cfg["agent"], allowed_tools=["Read", "mcp__windows__set_volume"]),
                 hooks={"enabled": False})
    global _AGENT_TMP
    _AGENT_TMP = _AGENT_TMP or _tmp()               # one folder: its path is in the args
    tmp = _AGENT_TMP
    with mock.patch.object(config, "STATE_DIR", tmp), mock.patch.dict(os.environ, {"LOCALAPPDATA": str(tmp)}):
        return a._args()


# ------------------------------------------------------------------ a user plugin snaps in
def test_a_user_plugin_snaps_in_and_out():
    from heyclaude import service_icons as SI
    from heyclaude import widgets as W
    d = _tmp()
    text_widget = ROOT / "widgets" / "text"
    man = {"id": "lyrics", "api": "1.0", "name": "Lyrics", "description": "Synced lyrics.", "version": "0.1.0",
           "permissions": ["network", "background"], "icon": "icon.svg",
           "contributes": {
               "widgets": ["lyrics_card"], "service": "service.py", "instant": "instant.py",
               "tools": ["mcp__lyrics"], "read_only_tools": ["mcp__lyrics__get_lyrics"],
               "mcp_servers": {"lyrics": {"command": "${PYTHON}", "args": ["${PLUGIN_DIR}/server.py"]}},
               "icons": {"lyrics": {"name": "Lyrics", "icon": "icon.svg"}},
               "prompt": "For song lyrics use mcp__lyrics__get_lyrics.",
               "live_activities": [{"id": "line", "template": "media", "priority": "active",
                                    "actions": [{"id": "hide", "label": "Hide", "voice": ["hide the lyrics"]}]}]}}
    folder = _write(d / "user" / "lyrics", man, {
        "icon.svg": "<svg xmlns='http://www.w3.org/2000/svg'/>",
        "server.py": "",
        "instant.py": "def match(text):\n    return {'say': 'Lyrics on.'} if text == 'show lyrics' else None\n",
        "service.py": ("import threading\nSTOP = threading.Event()\n"
                       "def start(ctx):\n"
                       "    ctx.activities.start('line', {'title': 'Song', 'line': 'la la'},\n"
                       "                         on_action=lambda a: 'Hidden.')\n"
                       "def stop():\n    STOP.set()\n")})
    shutil.copytree(text_widget, folder / "widgets" / "lyrics_card")
    wj = folder / "widgets" / "lyrics_card" / "widget.json"
    wj.write_text(wj.read_text(encoding="utf-8").replace('"id": "text"', '"id": "lyrics_card"'), encoding="utf-8")
    reg = _with(P.PluginRegistry({}, dirs=[ROOT / "plugins", d / "user"], persist=False))
    try:
        assert "lyrics" in reg.plugins and not reg.broken, reg.broken
        # yours start switched off (they run arbitrary code): nothing of it is offered yet
        assert not reg.is_enabled("lyrics") and "lyrics_card" not in W.active() and reg.mcp_servers() == {}
        assert reg.set_enabled("lyrics", True) and reg.cfg["plugins"]["enabled"] == ["lyrics"]
        assert reg.cfg["plugins"].get("disabled", []) == []
        assert "lyrics_card" in W.registry() and "lyrics_card" in W.active()
        assert W.normalise({"widget": "lyrics_card", "data": {"text": "la la"}})["widget"] == "lyrics_card"
        assert reg.instant("show lyrics") == ("lyrics", {"say": "Lyrics on."})
        srv = reg.mcp_servers("PY")
        assert srv == {"lyrics": {"command": "PY", "args": [f"{folder}/server.py"], "env": {}}}
        assert reg.read_only_tools() == ["mcp__lyrics__get_lyrics"] and "mcp__lyrics" in reg.prompts()
        assert SI.describe("mcp__lyrics__get_lyrics")["icon_url"].endswith("icon.svg")
        assert reg.info("lyrics").icon_url.endswith("icon.svg")
        hub = A.ActivityHub()
        reg.start_services(hub)
        assert _wait(lambda: hub.layout()["primary"] is not None)
        p = hub.layout()["primary"]
        assert p["key"] == "lyrics:line" and p["template"] == "media" and p["fallback"] == "progress"
        assert hub.match_voice("hide the lyrics") == ("lyrics:line", "hide")
        assert hub.act("lyrics:line", "hide") == "Hidden."
        # out: card, instant, server, prompt, icon and its activity are gone, without a restart
        reg.set_enabled("lyrics", False)
        assert "lyrics_card" not in W.active() and "lyrics_card" in W.registry()
        assert W.normalise({"widget": "lyrics_card", "data": {"text": "x"}, "text": "x"})["widget"] == "text"
        assert reg.instant("show lyrics") is None and reg.mcp_servers() == {} and reg.prompts() == ""
        assert reg.read_only_tools() == [] and "mcp__lyrics" in reg.disabled_tools()
        assert not SI.describe("mcp__lyrics__get_lyrics")["icon_url"]
        assert not hub.running()
        assert reg._modules["lyrics:service.py"].STOP.is_set()
        reg.set_enabled("lyrics", True)                                # and back: its service starts again
        assert _wait(lambda: hub.running())
        reg.stop_services()
    finally:
        P.reset(None)


# ------------------------------------------------------------------ live activities
class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


BUDGETS = {"idle_fps": 8, "antic_fps": 30, "live_fps": 60}


def test_live_activity_arbitration():
    c = Clock()
    hub = A.ActivityHub(clock=c, budgets=BUDGETS)
    seen = []
    hub.subscribe(seen.append)
    assert hub.layout()["primary"] is None and hub.layout()["fps"] == 0
    hub.start("cal", "next", {"text": "Physics in 20 min"}, priority="ambient")
    c.t += 1
    hub.start("music", "track", {"title": "Song"}, template="media", priority="active")
    lay = hub.layout()
    assert lay["primary"]["key"] == "music:track" and lay["primary"]["presentation"] == "compact"
    assert lay["secondary"]["key"] == "cal:next" and lay["secondary"]["presentation"] == "minimal"
    # equal priority: the one that started first keeps the pill (no flapping)
    c.t += 1
    hub.start("timer", "t1", {"ends_at": 5}, template="countdown", priority="active")
    lay = hub.layout()
    assert lay["primary"]["key"] == "music:track" and lay["secondary"]["key"] == "timer:t1"
    assert lay["waiting"] == ["cal:next"]
    # a conversation owns the island; an incoming call still breaks in, expanded
    hub.set_busy(True)
    lay = hub.layout()
    assert lay["primary"] is None and sorted(lay["hidden"]) == ["cal:next", "music:track", "timer:t1"]
    c.t += 1
    hub.start("phone", "call", {"caller": "Mum"}, template="call", priority="alert", alert=True,
              actions=[{"id": "answer", "label": "Answer", "voice": ["answer", "pick up"]},
                       {"id": "decline", "label": "Decline", "voice": ["decline"]}],
              on_action=lambda a: "Answered." if a == "answer" else None)
    lay = hub.layout()
    assert lay["primary"]["key"] == "phone:call" and lay["primary"]["presentation"] == "expanded"
    assert lay["secondary"] is None                                    # expanded takes the whole island
    assert hub.match_voice("Answer.") == ("phone:call", "answer")
    assert hub.match_voice("okay pick up") == ("phone:call", "answer")
    assert hub.match_voice("answer the email from Bob") is None
    assert hub.act("phone:call", "answer") == "Answered." and hub.act("phone:call", "nope") is None
    c.t += 100
    assert hub.layout()["primary"]["presentation"] == "expanded"     # a call rings until it's answered or ends
    hub.end("phone:call")
    hub.set_busy(False)
    assert hub.layout()["primary"]["key"] == "music:track"
    assert hub.match_voice("answer") is None                          # nothing to answer now
    # an alert that isn't a call collapses to compact after ALERT_MAX_S
    hub.update("timer:t1", {"done": True}, priority="alert", alert=True)
    lay = hub.layout()
    assert lay["primary"]["key"] == "timer:t1" and lay["primary"]["presentation"] == "expanded"
    c.t += A.ALERT_MAX_S + 1
    assert hub.layout()["primary"]["presentation"] == "compact"
    # the user opens one; ending it closes it
    hub.update("timer:t1", priority="active")
    hub.expand("music:track")
    assert hub.layout()["primary"]["presentation"] == "expanded"
    hub.end("music:track")
    assert hub.layout()["primary"]["presentation"] == "compact"
    # ttl: gone at the next tick
    hub.start("cal", "next", {"text": "x"}, priority="info", ttl_s=5)
    c.t += 6
    assert hub.tick() and "cal:next" not in json.dumps(hub.layout())
    # a handler that raises ends its activity
    hub.start("bad", "a", {}, actions=[{"id": "go", "label": "Go"}], on_action=lambda a: 1 / 0)
    assert hub.act("bad:a", "go") is None and "bad:a" not in json.dumps(hub.layout())
    # a plugin can't flood the island
    for i in range(A.MAX_ACTIVITIES_PER_PLUGIN):
        hub.start("spam", f"a{i}", {})
    try:
        hub.start("spam", "one-more", {})
        raise AssertionError("no per-plugin limit")
    except RuntimeError:
        pass
    hub.end_plugin("spam")
    assert not any(k.startswith("spam:") for k in json.dumps(hub.layout()).split('"'))
    assert seen                                                         # listeners heard all this


def test_live_activity_frame_budget_and_coalescing():
    c = Clock()
    hub = A.ActivityHub(clock=c, budgets=BUDGETS)
    seen = []
    hub.subscribe(seen.append)
    hub.start("music", "track", {"line": "one"}, template="media", priority="active")
    assert hub.layout()["fps"] == 60                                   # the entry animates...
    c.t += A.TRANSITION_S + 0.01
    assert hub.layout()["fps"] == 0                                    # ...then 0 fps at rest
    # discrete updates: a brief transition each, coalesced when closer than MIN_UPDATE_S
    n = len(seen)
    c.t += 1
    hub.update("music:track", {"line": "two"})
    assert len(seen) == n + 1 and hub.layout()["fps"] == 60
    c.t += 0.05
    hub.update("music:track", {"line": "three"})
    c.t += 0.05
    hub.update("music:track", {"line": "four"})
    assert len(seen) == n + 1                                          # held back
    c.t += A.MIN_UPDATE_S
    assert hub.tick() and len(seen) == n + 2 and seen[-1]["primary"]["data"]["line"] == "four"
    c.t += 1
    assert hub.layout()["fps"] == 0 and not hub.tick()
    # declared continuous animation: capped by priority, and off with reduced motion
    hub.start("viz", "wave", {}, priority="active", fps=24)
    c.t += 1
    hub.end("music:track")
    c.t += 1
    assert hub.layout()["fps"] == 24
    hub.start("amb", "glow", {}, priority="ambient", fps=24)          # ambient: idle budget at most
    assert hub._acts["amb:glow"].fps == 8
    hub.set_reduced_motion(True)
    c.t += 1
    assert hub.layout()["fps"] == 0
    hub.set_reduced_motion(False)
    c.t += 1
    assert hub.layout()["fps"] == 24
    # priority can drop the cap later
    hub.update("viz:wave", priority="info")
    c.t += 1
    assert hub.layout()["fps"] == 8


def test_scoped_activities_stay_within_the_manifest():
    c = Clock()
    hub = A.ActivityHub(clock=c, budgets=BUDGETS)
    specs = {"line": {"id": "line", "template": "media", "priority": "active", "fps": 0, "dir": str(ROOT)},
             "call": {"id": "call", "template": "call", "priority": "info"}}
    s = hub.scoped("lyrics", specs)
    k = s.start("line", {"line": "la"}, priority="critical")           # asks for more than it declared
    assert k == "lyrics:line" and hub._acts[k].priority == A.PRIORITY["active"]
    s.start("call", {}, alert=True)
    assert hub._acts["lyrics:call"].priority == A.PRIORITY["info"]
    s.update("line", priority="critical")
    assert hub._acts[k].priority == A.PRIORITY["active"]
    try:
        s.start("undeclared", {})
        raise AssertionError("an undeclared activity started")
    except KeyError:
        pass
    assert s.end("line") and not s.end("line")
    assert A.validate_spec({"id": "x", "fps": 8, "priority": "active"}) == ["continuous animation (fps > 0) needs fps_reason"]


def test_island_seam_carries_the_layout():
    """island_qml: model.liveActivity gets the hub's layout (on the UI thread), the busy
    modes hide activities, and a click reaches the plugin."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from heyclaude import activities
    from heyclaude.island import Bridge
    from heyclaude.island_qml import QmlIsland

    def spin(ms):
        loop = QEventLoop()
        QTimer.singleShot(int(ms), loop.quit)
        loop.exec()
    b = Bridge()
    isl = QmlIsland(b, {"screen": "primary", "top_margin": 8, "motion": "full"}, software=True, show=False)
    try:
        hub = activities.get()
        clicked = []
        hub.start("phone", "call", {"caller": "Mum"}, template="call", priority="active",
                  actions=[{"id": "answer", "label": "Answer"}], on_action=lambda a: clicked.append(a) or "")
        spin(50)
        la = isl.model.liveActivity
        assert la["primary"]["key"] == "phone:call" and la["primary"]["data"]["caller"] == "Mum"
        assert isl._act_timer.isActive()
        b.state.emit("thinking", "…")
        spin(50)
        assert isl.model.liveActivity["primary"] is None                # the conversation owns the island
        b.state.emit("idle", "")
        spin(50)
        assert isl.model.liveActivity["primary"]["key"] == "phone:call"
        isl.model.activityAction("phone:call", "answer")
        assert clicked == ["answer"]
        hub.end("phone:call")
        spin(50)
        assert isl.model.liveActivity["primary"] is None
        spin(300)
        assert not isl._act_timer.isActive()                              # nothing running: no timer
    finally:
        isl.close()


if __name__ == "__main__":
    n = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            n += 1
            print("ok", name)
    print(n, "passed")
    sys.stdout.flush()
    if sys.platform == "win32":                    # Qt objects at exit (the island test)
        import ctypes
        ctypes.windll.kernel32.TerminateProcess(ctypes.windll.kernel32.GetCurrentProcess(), 0)
    os._exit(0)
