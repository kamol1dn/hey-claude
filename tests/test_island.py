"""Offline tests for M5, the Qt Quick island: the state registry, mode routing,
the response-widget contract and stream filter, service icons, plan usage,
and the QML island itself (software renderer, offscreen): every state, every
M9 effect, every widget from a simulated reply, approvals, the mic / stop /
not-me buttons, file drop, the talk-or-type field, a short state storm and the
rest invariant.

    .venv\\Scripts\\python tests\\test_island.py
"""
import faulthandler
import json
import os
import random
import re
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
faulthandler.enable()                       # a native crash prints where it happened (CI)
sys.stdout.reconfigure(line_buffering=True)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from heyclaude import states as S  # noqa: E402
from heyclaude import widgets as W  # noqa: E402
from heyclaude.island_qml import badge_dir, route, stt_short, task_rows  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


# ------------------------------------------------------------------ state registry
def test_registry_is_valid_and_every_chain_ends_in_a_core_state():
    reg = S.load()
    assert S.validate(reg) == []
    core = set(reg["core"])
    assert core == {"idle", "off", "listening", "thinking", "talking"}
    for st in reg["states"]:
        assert S.chain(st["id"])[-1] in core, st["id"]


def test_registry_fallbacks_match_the_roadmap():
    want = {"woke": "listening", "holding": "listening", "holding_muted": "off", "followup": "listening",
            "loading": "thinking", "paused": "off", "working": "thinking", "done": "happy", "happy": "idle",
            "error": "confused", "confused": "idle", "voice_mismatch": "confused", "sending": "working",
            "dictating": "listening", "approval_needed": "confused"}
    by = {s["id"]: s for s in S.load()["states"]}
    for k, v in want.items():
        assert by[k]["fallback"] == v, k


def test_resolve_walks_fallbacks_for_an_old_pack():
    core_only = {"idle", "off", "listening", "thinking", "talking"}
    assert S.resolve("dictating", core_only) == "listening"
    assert S.resolve("sending", core_only) == "thinking"            # sending -> working -> thinking
    assert S.resolve("done", core_only) == "idle"                   # done -> happy -> idle
    assert S.resolve("speaking", core_only) == "talking"            # alias
    assert S.resolve("brand_new_state", core_only) == "idle"


def test_validate_catches_cycles_and_missing_fallbacks():
    reg = json.loads(json.dumps(S.load()))
    by = {s["id"]: s for s in reg["states"]}
    by["happy"]["fallback"] = "done"                                # done -> happy -> done
    by["sending"]["fallback"] = "nope"
    probs = " ".join(S.validate(reg))
    assert "cycle" in probs and "nope" in probs


def test_the_ember_theme_implements_the_core_and_maps_newer_modes():
    t = S.load_theme("ember-thread")
    assert S.compatible(t["registry"])
    assert not S.compatible("2.0")
    assert S.mode_for("dictating", t["implements"]) == "listening"
    assert S.mode_for("working", t["implements"]) == "thinking"
    assert S.mode_for("speaking", t["implements"]) == "speaking"


# ------------------------------------------------------------------ routing
def test_route_maps_assistant_modes_to_engine_commands():
    ctx = {"engine": "ready", "now": 1000.0}
    assert route("idle", "", ctx) == [("go", ["ready", {}])]
    assert route("off", "listening off", ctx) == [("go", ["off", {}])]
    paused = route("off", "paused until 12:00", dict(ctx, paused_until=1600.0, paused_since=1000.0))
    assert paused[0][1][0] == "paused" and paused[0][1][1]["until"] == 1600000.0
    assert route("listening", "", ctx) == [("go", ["woke", {}])]               # from rest: the catch
    assert route("listening", "", dict(ctx, engine="followup")) == [("go", ["listening", {}])]
    assert route("listening", "", dict(ctx, engine="listening")) == []
    assert route("listening", "", dict(ctx, input=True)) == []
    assert route("holding", "muted", ctx) == [("go", ["holding", {"muted": True}])]
    assert route("followup", "7", ctx) == [("go", ["followup", {"seconds": 7.0}])]
    assert route("thinking", "hi", ctx) == [("go", ["thinking", {"text": "hi"}])]
    assert route("thinking", "more", dict(ctx, engine="thinking")) == [("setThinkText", ["more"])]
    assert route("speaking", "a b", dict(ctx, engine="speaking")) == [("setSpeakText", ["a b"])]
    assert route("error", "boom", ctx)[0][1][0] == "error"


def test_badge_and_task_rows():
    assert stt_short("gpu (turbo)") == "gpu"
    assert stt_short("cpu (gpu busy 83%)") == "cpu · gpu busy 83%"
    assert badge_dir("gpu (turbo)", "cpu (gpu busy)") == -1 and badge_dir("cpu (x)", "gpu (turbo)") == 1
    rows = task_rows([{"id": 1, "project": "p", "prompt": "do it", "status": "starting", "activity": "reading"}])
    assert rows == [{"id": "1", "name": "p: do it", "status": "running", "right": "reading", "elapsed": ""}]


# ------------------------------------------------------------------ widgets
def test_every_widget_example_validates_and_the_prompt_lists_it():
    p = W.prompt_text()
    for name, spec in W.load()["x-widgets"].items():
        assert W.validate(spec["example"], spec["schema"]) == [], name
        assert W.normalise({"widget": name, "data": spec["example"]})["widget"] == name
        assert f"- {name}:" in p
    assert set(W.names()) >= {"weather", "calendar_list", "timer", "task_card", "link", "code", "image",
                              "telegram_chats", "telegram_message", "telegram_draft"}


def test_a_busy_agenda_keeps_its_card():
    """Nine events today: the card shows the first six (and long titles are cut), not an empty text card."""
    ev = [{"start": f"{8 + i:02d}:00", "title": f"Class {i}" + ("x" * 100 if i == 0 else "")} for i in range(9)]
    w = W.normalise({"widget": "calendar_list", "data": {"title": "Today's agenda", "events": ev}, "text": "Today's agenda"})
    assert w["widget"] == "calendar_list", w
    assert len(w["data"]["events"]) == 6 and w["data"]["events"][5]["title"] == "Class 5"
    assert len(w["data"]["events"][0]["title"]) == 80 and w["data"]["events"][0]["title"].endswith("…")
    # what isn't a size problem still falls back
    assert W.normalise({"widget": "calendar_list", "data": {"events": [{"title": "no start"}]}, "text": "x"})["widget"] == "text"


def test_widget_filter_takes_the_line_out_wherever_the_stream_splits():
    spec = {"widget": "weather", "data": {"location": "Tashkent", "temp": 21}}
    text = "It's 21 degrees and sunny.\nWIDGET " + json.dumps(spec) + "\n"
    rnd = random.Random(3)
    for _ in range(200):
        cuts = sorted(rnd.sample(range(1, len(text)), rnd.randint(1, 12)))
        parts = [text[a:b] for a, b in zip([0] + cuts, cuts + [len(text)])]
        f = W.WidgetFilter()
        said = "".join(f.feed(x) for x in parts) + f.finish()
        assert said == "It's 21 degrees and sunny.\n" and f.spec == spec


def test_widget_filter_leaves_normal_text_alone():
    for t in ("Wide roads.", "WIDE awake.\nOk", "We did it", "  When?", "Widgets are cool"):
        f = W.WidgetFilter()
        assert f.feed(t) + f.finish() == t and f.spec is None
    out, spec = W.strip_widget_lines("Done.\n`WIDGET {\"widget\":\"timer\",\"data\":{\"seconds\":60}}`")
    assert out == "Done.\n" and spec["widget"] == "timer"
    out, spec = W.strip_widget_lines("Hi.\nWIDGET {not json")
    assert out == "Hi.\n" and spec is None


def test_unknown_or_invalid_widgets_fall_back_to_text():
    assert W.normalise({"widget": "weather", "data": {"temp": "hot"}, "text": "warm"})["widget"] == "text"
    assert W.normalise({"widget": "hologram", "data": {}, "text": "see it"}) == \
        {"widget": "text", "data": {"text": "see it"}, "text": "see it", "ttl_s": 12.0}
    assert W.normalise({"widget": "hologram", "data": {}}) is None
    w = W.normalise({"widget": "calendar_list", "data": {"events": [{"start": "9", "title": "x", "evil": 1}], "x": 2}})
    assert w["data"] == {"events": [{"start": "9", "title": "x"}]}         # undeclared keys dropped
    assert W.normalise({"widget": "link", "data": {"title": "x", "url": "javascript:alert(1)"}}) is None
    assert W.normalise({"widget": "timer", "data": {"seconds": 300}})["ttl_s"] >= 308


def test_cards_never_launch_programs():
    with tempfile.TemporaryDirectory() as d:
        exe = Path(d) / "evil.bat"
        exe.write_text("echo hi")
        doc = Path(d) / "notes.txt"
        doc.write_text("hi")
        with mock.patch("subprocess.Popen") as popen, mock.patch("os.startfile", create=True) as start:
            assert W.safe_open(str(exe))
            assert popen.call_args[0][0][:2] == ["explorer", "/select,"] and not start.called
            assert W.safe_open(str(doc))
            start.assert_called_once()
        assert not W.safe_open(str(Path(d) / "missing.txt"))


def test_dropped_files_become_a_request_with_paths():
    p = W.attachment_prompt([r"C:\x\report.pdf"], "")
    assert p.startswith("What is in this file?") and r"C:\x\report.pdf" in p
    assert W.attachment_prompt(["a", "b"], "compare them").startswith("compare them")


def test_telegram_avatars_only_from_the_cache():
    assert W._avatar(str(ROOT / "README.md")) == ""
    assert W._avatar("") == ""


# ------------------------------------------------------------------ widget plug-ins
FIRST_PACKS = {"weather", "calendar_list", "timer", "task_card", "link", "code", "image",
               "telegram_chats", "telegram_message", "telegram_draft", "text"}


def test_every_first_pack_widget_is_a_valid_plugin():
    reg = W.registry()
    assert set(reg) >= FIRST_PACKS, set(reg) ^ FIRST_PACKS
    for wid, man in reg.items():
        folder = ROOT / "widgets" / wid
        assert man["id"] == wid and (folder / "Widget.qml").exists()
        assert W.validate(man["example"], man["schema"]) == [], wid
        assert str(man["api"]).startswith("1.")
        assert man["size"]["width"] <= 460 and man["size"]["max_height"] <= 280, wid
        w = W.for_qml({"widget": wid, "data": man["example"]})
        assert w["qml"].endswith(f"widgets/{wid}/Widget.qml"), wid
        assert w["title"] or wid == "text", wid             # a text card without a title has no header
        assert w["title"] not in ("Text", wid), wid


def test_a_broken_plugin_is_skipped_not_fatal():
    with tempfile.TemporaryDirectory() as d:
        bad = Path(d) / "broken"
        bad.mkdir()
        (bad / "widget.json").write_text('{"id": "other", "api": "9.0"}', encoding="utf-8")
        good = Path(d) / "mini"
        good.mkdir()
        (good / "widget.json").write_text(json.dumps({"id": "mini", "name": "Mini", "api": "1.0", "description": "x",
                                                     "schema": {"type": "object"}, "example": {}}), encoding="utf-8")
        (good / "Widget.qml").write_text("import QtQuick\nItem {}\n", encoding="utf-8")
        reg = W.registry.__wrapped__(d)
        assert set(reg) == {"mini"}


def test_widgets_and_effects_draw_on_the_gpu():
    """No CPU-painted Canvas in widget plug-ins, effects or glyphs (Shapes / items / ShaderEffect only)."""
    files = list((ROOT / "widgets").glob("*/Widget.qml")) + [ROOT / "ui" / "island" / n for n in
                                                            ("EffectRow.qml", "Glyph.qml", "WidgetCard.qml", "Sky.qml", "Extras.qml")]
    for f in files:
        assert "Canvas" not in f.read_text(encoding="utf-8"), f


def test_titles_from_the_manifest_template():
    man = W.registry()["weather"]
    assert W.title_for(man, {"location": "Tashkent, Uzbekistan"}) == "Weather · Tashkent, Uzbekistan"
    assert W.title_for(man, {}) == "Weather"


# ------------------------------------------------------------------ weather (Open-Meteo)
_GEO = {"results": [{"name": "Tashkent", "latitude": 41.26, "longitude": 69.21, "country": "Uzbekistan"}]}
_FC = {"current": {"time": "2026-11-09T21:45", "temperature_2m": 15.2, "apparent_temperature": 13.9, "weather_code": 1,
                   "is_day": 0, "wind_speed_10m": 11.6},
       "hourly": {"time": [f"2026-11-09T{h:02d}:00" for h in range(24)] + [f"2026-11-10T{h:02d}:00" for h in range(24)],
                  "temperature_2m": [10 + h % 7 for h in range(48)], "weather_code": [61 if h % 5 == 0 else 2 for h in range(48)],
                  "is_day": [1 if 7 <= h % 24 <= 17 else 0 for h in range(48)]},
       "daily": {"time": ["2026-11-09", "2026-11-10", "2026-11-11"], "weather_code": [61, 0, 3],
                 "temperature_2m_max": [20.1, 16.4, 18.0], "temperature_2m_min": [13.6, 9.2, 8.8]}}


def _fetch_counter():
    calls = []

    def fetch(url, params, timeout=8.0):
        calls.append(url)
        return _GEO if "geocoding" in url else _FC
    return fetch, calls


def test_weather_maps_open_meteo_onto_the_widget():
    from heyclaude import weather as WX
    WX._cache.clear()
    WX._places.clear()
    fetch, calls = _fetch_counter()
    d = WX.forecast("Tashkent", fetch=fetch, now=1000.0)
    assert d["location"] == "Tashkent, Uzbekistan" and d["temp"] == 15 and d["icon"] == "night"
    assert d["condition"] == "Mainly clear" and d["feels"] == 14 and d["high"] == 20 and d["low"] == 14
    assert [h["time"] for h in d["hourly"]] == ["22:00", "23:00", "00:00", "01:00", "02:00", "03:00"]
    assert [x["day"] for x in d["days"]] == ["Today", "Tue", "Wed"] and d["days"][1]["icon"] == "sun"
    w = W.normalise({"widget": "weather", "data": d})
    assert w is not None and w["widget"] == "weather"                     # the tool result IS the widget data
    WX.forecast("tashkent", fetch=fetch, now=1300.0)                      # cached: same place, 5 min later
    assert len(calls) == 2
    WX.forecast("Tashkent", fetch=fetch, now=1000.0 + 700)                # 11+ min later: the forecast again
    assert len(calls) == 3


def test_weather_errors_say_what_to_do():
    from heyclaude import weather as WX
    WX._places.clear()
    try:
        WX.forecast("", fetch=lambda *a, **k: {})
        raise AssertionError("expected an error")
    except WX.WeatherError as e:
        assert "weather.location" in str(e)
    try:
        WX.forecast("Nowhereville", fetch=lambda *a, **k: {"results": []})
        raise AssertionError("expected an error")
    except WX.WeatherError as e:
        assert "Nowhereville" in str(e)
    assert WX.describe(95) == ("Thunderstorm", "storm") and WX.describe(0, False)[1] == "night"


def test_the_weather_tool_result_becomes_the_card():
    from heyclaude import weather as WX
    d = WX.to_widget(_FC, "Tashkent, Uzbekistan")
    spec = W.from_tool("mcp__weather__get_weather", {}, d)
    assert spec["widget"] == "weather" and spec["data"]["temp"] == 15
    assert W.from_tool("mcp__other__tool", {}, d) is None
    assert "never write it yourself" in W.prompt_text()


def test_the_agent_gets_the_weather_server_and_tool():
    from heyclaude import brain as B
    srv = B.weather_mcp_servers({"weather": {"location": "Tashkent", "unit": "C"}})
    assert srv["weather"]["args"] == ["-m", "heyclaude.mcp_weather"]
    assert srv["weather"]["env"]["HEYCLAUDE_WEATHER_LOCATION"] == "Tashkent"
    assert B.weather_mcp_servers({"weather": {"enabled": False}}) == {}
    from heyclaude.mcp_weather.__main__ import build
    assert build() is not None


# ------------------------------------------------------------------ service icons
def test_service_icons_resolve_in_order():
    from heyclaude import service_icons as I
    d = I.describe("mcp__claude_ai_Google_Calendar__list_events")
    assert d["service"] == "Google Calendar" and d["label"] == "Google Calendar · list events"
    assert d["icon_url"].endswith("googlecalendar.svg")
    assert I.describe("mcp__telegram__telegram_list_dialogs")["label"] == "Telegram · list dialogs"
    w = I.describe("WebSearch", {"query": "weather in Tashkent"})
    assert w["glyph"] == "globe" and "weather in Tashkent" in w["label"]
    r = I.describe("Read", {"file_path": r"C:\Users\me\secret\plan.md"})
    assert r["label"] == "Files · reading plan.md"                      # the name only, never the path
    u = I.describe("mcp__weird-server__doThing")
    assert u["icon_url"] == "" and u["initials"] == "WS" and u["label"] == "Weird Server · do thing"
    with tempfile.TemporaryDirectory() as d:
        png = Path(d) / "x.png"
        png.write_bytes(b"x")
        assert I.describe("mcp__claude_ai_Gmail__search", {}, {"gmail": str(png)})["icon_url"] == png.resolve().as_uri()
        with mock.patch.object(I, "DECLARED", Path(d)):
            (Path(d) / "gmail.svg").write_text("<svg/>")
            assert I.describe("mcp__claude_ai_Gmail__search")["icon_url"].endswith("/gmail.svg")


def test_bundled_icons_are_plain_single_paths_and_never_the_claude_mark():
    import re
    files = list((ROOT / "ui" / "icons" / "services").glob("*.svg"))
    assert len(files) > 30
    names = {f.stem for f in files}
    assert not ({"anthropic", "claude"} & names)
    for f in files:
        s = f.read_text(encoding="utf-8")
        assert re.fullmatch(r'<svg fill="#F2F2F2" role="img" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">'
                            r'<title>[^<]*</title><path d="[^"<>]*"/></svg>\s*', s), f.name
    data = json.loads((ROOT / "ui" / "services.json").read_text(encoding="utf-8"))
    for key, spec in data["servers"].items():
        if spec.get("icon"):
            assert spec["icon"] in names, key


def test_favicons_are_off_by_default_and_never_for_unknown_domains():
    from heyclaude import service_icons as I
    with mock.patch.object(I, "fetch_favicon") as f:
        I.describe("mcp__monday__list")
        assert not f.called
        I.describe("mcp__monday__list", fetch=True)
        f.assert_called_once_with("monday.com")


# ------------------------------------------------------------------ brain: usage, tool events
def test_usage_info_from_claude_codes_rate_limit_event():
    from heyclaude.brain import usage_info
    ev = {"type": "rate_limit_event", "rate_limit_info": {"status": "allowed", "unifiedWindows": {
        "five_hour": {"utilization": 0.05, "resetsAt": 1794222600, "status": "allowed"},
        "seven_day": {"utilization": 0.16, "resetsAt": 1794531600, "status": "allowed"}}}}
    u = usage_info(ev)
    assert u["five_hour"] == 0.05 and u["seven_day"] == 0.16 and u["status"] == "allowed"


def test_agent_reports_tool_starts_and_ends():
    from heyclaude import brain as B
    ag = B.Agent.__new__(B.Agent)
    ag._pending_tools, ag.on_tool_result, got = {}, None, []
    ag.on_tool_use = got.append
    ag._tool_event("tool_use", {"id": "t1", "name": "mcp__x__y", "input": {"a": 1}})
    ag._tool_event("tool_result", {"tool_use_id": "t1", "content": "{}"})
    assert got == [{"id": "t1", "name": "mcp__x__y", "phase": "start", "input": {"a": 1}}, {"id": "t1", "phase": "end"}]


def test_widget_prompt_goes_to_both_tiers_unless_turned_off():
    from heyclaude.brain import widget_prompt
    assert "WIDGET" in widget_prompt({}) and "telegram_draft" in widget_prompt({"island": {}})
    assert widget_prompt({"island": {"widgets": False}}) == ""


def test_an_answer_with_a_widget_line_shows_it_and_never_says_it():
    from test_input import _Sig, _assistant, _Chat, _wait
    a = _assistant()
    a.ui.widget = _Sig()
    a.brain.chat = _Chat(["It will be sunny, 24 degrees.\nWID", "GET {\"widget\":\"weather\",\"data\":",
                          "{\"location\":\"Tashkent\",\"temp\":24}}"])
    a.submit_text("tell me something nice")
    assert _wait(lambda: not a.conv_lock.locked() and a.ui.widget.calls)
    said = "".join(a.speaker.fed)
    assert "WIDGET" not in said and "sunny" in said
    assert a.ui.widget.calls[-1][0]["widget"] == "weather"


# ------------------------------------------------------------------ the QML island
_APP = None
_ISL = None


def _island():
    global _APP, _ISL
    if _ISL is not None:
        return _ISL
    from PySide6.QtWidgets import QApplication
    _APP = QApplication.instance() or QApplication([])
    from heyclaude.island import Bridge
    from heyclaude.island_qml import QmlIsland
    b = Bridge()
    isl = QmlIsland(b, {"screen": "primary", "top_margin": "auto", "motion": "full"}, wake_label="hey claude", software=True)
    isl.bridge_ = b
    _ISL = isl
    _spin(600)
    return isl


def _spin(ms):
    from PySide6.QtCore import QEventLoop, QTimer
    loop = QEventLoop()
    QTimer.singleShot(int(ms), loop.quit)
    loop.exec()


def _v(x):
    return x.toVariant() if hasattr(x, "toVariant") else x


def _state(isl):
    return _v(isl.call("probe"))["state"]


def test_qml_island_loads_on_the_software_path_and_reaches_every_state():
    isl = _island()
    b = isl.bridge_
    assert isl.root is not None and isl.model._software
    b.stt.emit("gpu (turbo)")
    seq = [("idle", "", "ready"), ("off", "listening off", "off"), ("loading", "loading…", "loading"),
           ("idle", "", "ready"), ("listening", "", "listening"), ("holding", "", "holding"),
           ("holding", "muted", "holding"), ("listening", "", "listening"), ("followup", "5", "followup"),
           ("thinking", "what's up", "thinking"), ("speaking", "All good. " * 12, "speaking"),
           ("error", "oops", "error"), ("idle", "", "ready")]
    for mode, text, want in seq:
        b.state.emit(mode, text)
        _spin(700 if mode != "listening" else 900)
        assert _state(isl) == want, (mode, _state(isl))
    b.listening.emit(False, time.time() + 600)
    b.state.emit("off", "paused")
    _spin(500)
    assert _state(isl) == "paused"
    b.listening.emit(True, 0.0)
    b.state.emit("idle", "")
    _spin(500)
    st = _v(isl.call("engineStats"))
    assert st["nan"] == 0


def test_controls_hit_test_and_route_clicks():
    isl = _island()
    b = isl.bridge_
    b.state.emit("speaking", "Paris is the capital of France.")
    _spin(800)
    p = _v(isl.call("probe"))
    cy = min(p["h"], 60) / 2
    assert isl.call("hit", p["w"] - 26, cy) == "stop" and isl.call("hit", p["w"] - 56, cy) == "mic"
    assert isl.call("hit", 100, cy) is None
    got = []
    isl.stop_speaking.connect(lambda: got.append("stop"))
    isl.interrupt_talk.connect(lambda: got.append("mic"))
    isl.model.stopSpeaking()
    isl.model.interruptTalk()
    assert got == ["stop", "mic"]
    # the text never runs under the buttons: the pill is wide enough for both
    assert p["w"] >= 300 + 66


def test_not_me_after_a_wake_only():
    isl = _island()
    b = isl.bridge_
    b.state.emit("idle", "")
    _spin(400)
    b.wake_hit.emit(time.time())
    b.state.emit("listening", "")
    _spin(900)
    p = _v(isl.call("probe"))
    assert isl.call("hit", p["w"] - 45, 20) == "notme"
    got = []
    isl.not_me.connect(lambda: got.append(1))
    isl.model.notMe()
    assert got == [1]
    b.partial.emit("so can you")
    b.state.emit("thinking", "so can you")
    _spin(600)
    assert isl.call("hit", p["w"] - 45, 20) != "notme"


def test_every_m9_effect_shows_and_its_buttons_route():
    from heyclaude import effects as E
    isl = _island()
    b = isl.bridge_
    b.state.emit("idle", "")
    samples = [("mcp__windows__set_volume", {"old": 45, "new": 30}), ("mcp__windows__set_brightness", {"old": 40, "new": 80}),
               ("mcp__windows__set_mute", {"old": False, "new": True}), ("mcp__windows__set_theme", {"old": "light", "new": "dark"}),
               ("mcp__windows__media_control", {"old": {"title": "a"}, "new": {"title": "b", "duration_s": 10, "position_s": 2},
                                                "action": "next"}),
               ("mcp__windows__request_power_action", {"pending": "sleep"}), ("mcp__windows__screenshot", {}),
               ("mcp__windows__launch_app", {"launched": "Notepad"})]
    kinds = set()
    for tool, res in samples:
        ev = E.build(tool, {}, res, undoable=tool != "mcp__windows__request_power_action")
        b.effect.emit(ev)
        _spin(700)
        info = _v(isl.call("extrasInfo"))
        assert info["effect"] == ev["effect"] and info["height"] >= 44, (tool, info)
        assert info["pillH"] >= 28 + 44 - 1
        kinds.add(ev["effect"])
    assert kinds == set(E.KINDS) - {"generic"} | {"generic"}
    clicks = []
    isl.effect_undo.connect(lambda i: clicks.append(("undo", i)))
    isl.effect_confirm.connect(lambda i: clicks.append(("confirm", i)))
    isl.model.effectClicked("undo", "e1")
    isl.model.effectClicked("confirm", "e2")
    assert clicks == [("undo", "e1"), ("confirm", "e2")]
    b.effect_art.emit(ev["id"], b"not an image")                    # bad bytes are ignored


def test_every_widget_renders_from_a_simulated_reply():
    isl = _island()
    b = isl.bridge_
    b.state.emit("speaking", "Here you go.")
    for name, spec in W.load()["x-widgets"].items():
        data = spec["example"]
        if name == "image":
            img = Path(tempfile.gettempdir()) / "hc-island-test.png"
            from PySide6.QtGui import QColor, QImage
            q = QImage(64, 40, QImage.Format_RGB32)
            q.fill(QColor("#335577"))
            q.save(str(img))
            data = {"path": str(img), "caption": "test"}
        reply = "Here you go.\nWIDGET " + json.dumps({"widget": name, "data": data, "text": name})
        said, w = W.strip_widget_lines(reply)
        assert said == "Here you go.\n"
        b.widget.emit(w)
        _spin(900)
        info = _v(isl.call("extrasInfo"))
        assert info["widget"] == name and info["widgetReady"], (name, info)
        assert info["height"] > 20 and info["pillH"] > 40 + 20, (name, info)
        assert info["pillW"] >= info["minWidth"] - 1, (name, info)
    isl.clear_widget()
    _spin(400)
    assert _v(isl.call("extrasInfo"))["widget"] == ""


def test_widget_actions_timer_done_and_draft_send_stays_off():
    isl = _island()
    got = []
    isl.widget_action.connect(lambda wid, a, p: got.append((a, p)))
    isl.show_widget({"widget": "telegram_draft", "data": {"chat": "Aziz", "text": "yes!"}})
    _spin(300)
    isl.model.widgetAction(isl.widget["id"], "send", "yes!")
    assert got[-1][0] == "send"                     # reported; nothing sends without M7's approval
    with mock.patch("heyclaude.widgets.safe_open") as so:
        isl.model.openUrl("https://example.com")
        so.assert_called_once_with("https://example.com")
    isl.clear_widget()


def test_approval_cards_answer_only_by_click():
    isl = _island()
    b = isl.bridge_
    b.state.emit("idle", "")
    got = []
    isl.approval_decided.connect(lambda i, a: got.append((i, a)))
    b.approval.emit({"id": "ap1", "title": "Run git push?", "tool": "Bash", "session": "m5"})
    _spin(500)
    assert _v(isl.call("extrasInfo"))["approvals"] == 1
    isl.model.approvalDecided("ap1", "allow")
    isl.model.approvalDecided("ap1", "allow")                       # a second click does nothing
    assert got == [("ap1", "allow")]
    _spin(300)
    assert _v(isl.call("extrasInfo"))["approvals"] == 0               # and the card is gone
    b.approval.emit({"id": "ap2", "title": "x"})
    b.approval_withdrawn.emit("ap2")
    _spin(400)
    assert "ap2" not in isl.approvals


def test_tools_in_use_show_their_service():
    isl = _island()
    b = isl.bridge_
    b.state.emit("thinking", "what's on tomorrow?")
    b.tool.emit({"id": "t1", "name": "mcp__claude_ai_Google_Calendar__list_events", "phase": "start"})
    b.tool.emit({"id": "t2", "name": "mcp__telegram__telegram_list_dialogs", "phase": "start"})
    _spin(400)
    snap = _v(isl.call("snapshot"))
    assert any("Telegram · list dialogs" in l["text"] and "+1" in l["text"] for l in snap["labels"])
    b.tool.emit({"id": "t1", "phase": "end"})
    b.tool.emit({"id": "t2", "phase": "end"})
    _spin(400)
    snap = _v(isl.call("snapshot"))
    # the last tool's label stays until the answer starts (no flip back to the first status line)
    assert any(l["text"].startswith("Telegram · list dialogs") and not l["out"] for l in snap["labels"]), snap["labels"]
    assert not any(l["text"] == "what's on tomorrow?" and not l["out"] for l in snap["labels"])
    b.session_event.emit({"session": "m5", "kind": "tool", "tool": "Bash"})
    assert "session:m5" in isl.tools
    b.session_event.emit({"session": "m5", "kind": "done"})
    assert not isl.tools


def test_usage_row_and_talk_or_type_with_a_dropped_file():
    isl = _island()
    b = isl.bridge_
    b.state.emit("idle", "")
    b.usage.emit({"five_hour": 0.06, "seven_day": 0.16})
    assert isl.model.usageOn and isl.model.usage["seven_day"] == 0.16
    sent = []
    isl.attach_input(lambda: None, lambda: None, sent.append)
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write("hello")
    from PySide6.QtCore import QUrl
    isl.model.filesDropped([QUrl.fromLocalFile(f.name).toString(), "https://not-a-file"])
    _spin(500)
    assert isl.input_open and _state(isl) == "input"
    assert isl.model.inputEnter("summarise it", False)
    _spin(500)
    assert sent and sent[0].startswith("summarise it") and os.path.normpath(f.name) in sent[0]
    assert not isl.input_open
    isl.toggle_input()
    _spin(300)
    assert isl.input_open
    isl.model.inputEscape()
    _spin(300)
    assert not isl.input_open
    os.unlink(f.name)


def test_state_storm_stays_finite_and_settles():
    isl = _island()
    b = isl.bridge_
    rnd = random.Random(11)
    modes = [("idle", ""), ("listening", ""), ("holding", ""), ("thinking", "x"), ("speaking", "hello there"),
             ("followup", "2"), ("off", "listening off"), ("error", "e"), ("loading", "l")]
    end = time.time() + 4
    while time.time() < end:
        r = rnd.random()
        if r < 0.1:
            b.wake_hit.emit(time.time())
        elif r < 0.15:
            b.mismatch.emit()
        elif r < 0.2:
            b.tasks.emit([{"id": "a", "project": "p", "prompt": "t", "status": rnd.choice(["running", "done"])}])
        else:
            b.state.emit(*rnd.choice(modes))
        _spin(rnd.randint(50, 300))
    b.state.emit("idle", "")
    b.tasks.emit([])
    _spin(5000)
    st = _v(isl.call("engineStats"))
    snap = _v(isl.call("snapshot"))
    assert st["nan"] == 0
    assert snap["state"] == "ready"
    assert all(l["a"] > 0.99 or l["out"] for l in snap["labels"]), snap["labels"]
    assert st["maxEdge"] < 40, st


def test_rest_invariant_in_ready_and_off():
    isl = _island()
    b = isl.bridge_
    b.listening.emit(True, 0.0)                                     # no pause left over from another test
    for mode, text, timer_ok in (("idle", "", True), ("off", "listening off", False)):
        b.state.emit("speaking", "hello")
        _spin(500)
        b.state.emit(mode, text)
        _spin(1600)
        rc = _v(isl.call("restCheck"))
        assert not rc["frameAnimation"], rc
        if not timer_ok:
            assert not rc["timer"], rc
        else:
            assert rc["interval"] >= 100, rc
    isl._send("setMotion", ["calm"])
    b.state.emit("idle", "")
    _spin(1500)
    rc = _v(isl.call("restCheck"))
    assert not rc["frameAnimation"] and not rc["timer"], rc
    isl._send("setMotion", ["full"])


def test_scale_setting_resolves_and_picks_crisp_text():
    from heyclaude.island_qml import resolve_scale, text_render_type
    from PySide6.QtQuick import QQuickWindow
    assert resolve_scale("auto") == 1.0 and resolve_scale(None) == 1.0
    assert resolve_scale(1.5) == 1.5 and resolve_scale("125%") == 1.25 and resolve_scale("1.33") == 1.35
    assert resolve_scale(150) == 1.5 and resolve_scale(9.0) == 0.8              # over 4 = a percentage
    assert resolve_scale(0.1) == 0.8 and resolve_scale(3.0) == 2.0               # clamped to 0.8-2.0
    assert text_render_type(1.0) == QQuickWindow.NativeTextRendering             # hinted: sharpest at 1:1
    assert text_render_type(1.5) == QQuickWindow.QtTextRendering                 # distance field: sharp scaled


def test_scale_is_proportional_unclipped_and_keeps_the_idle_budget():
    isl = _island()
    b = isl.bridge_
    b.listening.emit(True, 0.0)
    base = None
    for sc in (1.0, 1.5, 2.0):
        isl.preview_scale(sc)
        _spin(700)
        assert isl.view.width() == round(600 * sc) and isl.view.height() == round(400 * sc)
        b.state.emit("speaking", "You have two meetings tomorrow.")
        isl.show_widget({"widget": "calendar_list", "data": W.registry()["calendar_list"]["example"]})
        _spin(3000)                                                                 # settled: the mask is exact
        m, p = isl._mask, _v(isl.call("probe"))
        assert m[0] >= 0 and m[1] >= 0 and m[0] + m[2] <= isl.view.width() + 1 and m[1] + m[3] <= isl.view.height() + 1, (sc, m)
        if base is None:
            base = (p["w"], p["h"])
        else:   # the same island in island units, drawn sc times bigger
            assert abs(p["w"] - base[0]) <= 1 and abs(p["h"] - base[1]) <= 1, (sc, p, base)
        assert abs(m[2] - (p["w"] + 4) * sc) <= 4 and abs(m[3] - (p["h"] + 4) * sc) <= 4, (sc, m, p)
        isl.clear_widget()
        b.state.emit("idle", "")
        _spin(1800)
        rc = _v(isl.call("restCheck"))
        assert not rc["frameAnimation"] and rc["interval"] >= 100, (sc, rc)          # the 8 fps rest tick, any scale
    isl.preview_scale(1.0)
    _spin(500)
    assert isl.view.width() == 600 and _state(isl) == "ready"                      # back to 1:1 (a QML reload)


def test_settings_slider_previews_the_size_live():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from heyclaude import config
    from heyclaude.settings_ui import SettingsWindow
    w = SettingsWindow(config.defaults())
    got = []
    w.island_preview.connect(got.append)
    w.cb_scale_auto.setChecked(False)
    w.sl_scale.setValue(30)
    new, errors, _ = w._collect()
    assert not errors and new["island"]["scale"] == 1.5 and got[-1] == 1.5
    w.cb_scale_auto.setChecked(True)
    assert w._collect()[0]["island"]["scale"] == "auto" and got[-1] == "auto"
    assert config.needs_restart(["island.scale"]) == []                           # applied live
    w.hide()
    w.deleteLater()


class _VoiceSpeaker:
    """A speaker that plays what it's fed on a thread and fires the callbacks the
    real one does: on_start per sentence, a sentence event, on_idle when done."""

    def __init__(self, a, secs=0.5):
        import queue
        import threading
        self.a, self.secs = a, secs
        self.q = queue.Queue()
        self.busy, self.first_audio_t, self.fed = False, None, []
        self._t = None
        self._threading = threading

    def begin(self):
        self.first_audio_t = None

    def feed(self, text):
        if text.strip():
            self.fed.append(text)
            self.q.put(text.strip())
            if self._t is None or not self._t.is_alive():
                self._t = self._threading.Thread(target=self._play, daemon=True)
                self._t.start()

    def _play(self):
        while not self.q.empty():
            sent = self.q.get()
            self.busy = True
            self.first_audio_t = self.first_audio_t or time.perf_counter()
            self.a._tts_started()
            self.a._sentence_started(sent, self.secs)
            time.sleep(self.secs)
        self.busy = False
        self.a._tts_idle()

    def flush(self):
        pass

    def stop(self):
        while not self.q.empty():
            self.q.get()

    def announce(self, text):
        self.feed(text)


def test_one_request_is_one_open_panel():
    """heard -> filler speaking -> weather widget -> answer speaking -> followup -> close.
    One expand and one collapse, the card on screen while the answer plays, and the
    filler shown once (never re-animated, never part of the answer)."""
    from test_input import _assistant
    isl = _island()
    b = isl.bridge_
    b.listening.emit(True, 0.0)
    isl.clear_widget()
    b.state.emit("idle", "")
    _spin(1500)
    a = _assistant()
    a.ui = b                                      # the real Bridge: the assistant drives the real island
    a.listening.set()
    a.cfg["agent"]["filler"] = "Let me check."
    a.cfg["agent"]["filler_min_words"] = 0              # this test wants the filler on a short question
    a.speaker = _VoiceSpeaker(a)
    from heyclaude import weather as WX
    card = WX.to_widget(_FC, "Tashkent, Uzbekistan")

    def lookup(text):
        time.sleep(0.9)                              # the tool runs while the filler plays
        a._on_tool_result("mcp__weather__get_weather", {}, card, False)
        time.sleep(1.2)
        yield "It's fifteen degrees and clear. "
        yield "Rain is likely after midnight."
    a.brain.lookup = lookup

    sent = []
    orig = isl._send
    isl._send = lambda fn, args: (sent.append((fn, list(args))), orig(fn, args))[1]
    seen = {"card_at_answer": None, "speaking_texts": []}
    try:
        a.set_mode("listening")
        _spin(500)
        a.request("what's the weather today?", source="voice")
        t_end = time.time() + 15
        while time.time() < t_end:
            _spin(100)
            if isl.mode == "speaking":
                info = _v(isl.call("extrasInfo"))
                if seen["card_at_answer"] is None and info["widget"]:
                    seen["card_at_answer"] = info
                seen["speaking_texts"].append(isl.text)
            if isl.mode == "speaking" and not a.speaker.busy and a.speaker.q.empty() and not a.conv_lock.locked():
                break
        assert a.followup.state == "armed" and isl.mode == "speaking"   # no drop to idle before "go on"
        a.followup.state = "open"                                        # what the listen loop does next
        a.set_mode("followup", "1")
        _spin(1200)
        a.followup.close()
        a.rest()
        _spin(1500)
        mid = _v(isl.call("extrasInfo"))
        assert mid["widget"] == "weather" and mid["question"] == "what's the weather today?" and mid["answer"], mid
        _spin(13000)                                                     # the card's linger, then one collapse
        end = _v(isl.call("extrasInfo"))
    finally:
        isl._send = orig
    rest = {"ready", "off", "paused"}
    gos = [args[0] for fn, args in sent if fn == "go"]
    expands = sum(1 for p, q in zip(["ready"] + gos, gos) if p in rest and q not in rest)
    collapses = sum(1 for p, q in zip(["ready"] + gos, gos) if p not in rest and q in rest)
    assert expands == 1 and collapses == 1, gos
    assert seen["card_at_answer"] and seen["card_at_answer"]["widget"] == "weather" and not seen["card_at_answer"]["pending"]
    filler_shows = [args for fn, args in sent if (fn == "go" and args[0] == "thinking" and args[1].get("text") == "Let me check.")
                    or (fn == "setThinkText" and args[0] == "Let me check.")]
    assert len(filler_shows) == 1, filler_shows
    assert all("Let me check" not in t for t in seen["speaking_texts"]), seen["speaking_texts"][:3]
    assert any(fn == "spoken" and "fifteen" in args[0] for fn, args in sent)       # text follows the voice
    assert not any(fn == "spoken" and "Let me check" in args[0] for fn, args in sent)
    assert end["widget"] == "" and end["question"] == "" and end["height"] == 0, end


def test_the_filler_is_only_for_longer_requests():
    from test_input import _assistant, _wait
    for question, want in (("what's the weather today?", False),
                           ("can you look up what time the pharmacy near the office closes on saturdays", True)):
        a = _assistant()
        a.cfg["agent"]["filler"] = "Let me check."
        a.cfg["agent"]["filler_min_words"] = 8
        a.brain.lookup = lambda text: iter(["It closes at six."])
        a.brain.handle = lambda text, q=question: iter([("lookup", text)])
        a.submit_text(question)
        assert _wait(lambda: not a.conv_lock.locked() and a.speaker.fed)
        said = "".join(a.speaker.fed)
        assert ("Let me check" in said) == want, (question, said)
        assert "Let me check" not in "".join(a._reply)                     # never part of the answer


# ------------------------------------------------------------------ real tool results, rendered live
FIXTURES = ROOT / "tests" / "fixtures" / "tool_results"
# what must never be on a card: a type name or a debug value
_BAD_TEXT = {"Text", "text", "undefined", "null", "NaN", "[object Object]"}


def _items(item):
    """Every QQuickItem under `item` (visual children, Loader items and delegates too)."""
    out = [item]
    for c in item.childItems():
        out += _items(c)
    return out


def _shown(item, root) -> bool:
    """Visible and not faded out, up the chain to `root`."""
    it = item
    while it is not None:
        if not it.isVisible() or it.opacity() < 0.05:
            return False
        if it is root:
            return True
        it = it.parentItem()
    return True


def _named(root, name):
    return [i for i in _items(root) if i.objectName() == name and _shown(i, root)]


def _texts(root):
    return [str(i.property("text")) for i in _items(root)
            if i.metaObject().className().startswith("QQuickText") and _shown(i, root) and i.property("text")]


def _fixture_spec(fx):
    """A fixture through the same path a live turn takes (the agent's tool_result, or the answer's WIDGET line)."""
    from heyclaude.effects import parse_result, result_text
    if "answer" in fx:
        return W.strip_widget_lines(fx["answer"])[1]
    content = fx["content"]
    return W.from_tool(fx["tool"], fx["args"], parse_result(content), result_text(content))


def _preview_layout():
    """The reference: docs/widgets-preview.html's Telegram cards (title word, parts, rows, badges)."""
    import re
    html = (ROOT / "docs" / "widgets-preview.html").read_text(encoding="utf-8")
    chats = html[html.index("tgchats(){"): html.index("tgmsg(){")]
    msg = html[html.index("tgmsg(){"): html.index("tgdraft(){")]
    unread = [int(n) for n in re.findall(r"\['[^']*','[^']*','[^']*',(\d+),'#", chats)]

    def classes(block):
        return {c for attr in re.findall(r'class="([^"]+)"', block) for c in attr.split()}
    return {
        "telegram_chats": {"title": re.search(r"title:'(\w+)", chats).group(1), "rows": len(unread),
                           "badges": sum(1 for n in unread if n), "parts": {"chat", "av", "n", "m", "r", "ub"} & classes(chats)},
        "telegram_message": {"title": re.search(r"title:'(\w+)", msg).group(1), "rows": msg.count('class="msg'),
                             "parts": {"msg", "me", "reply"} & classes(msg) | ({"meta"} if "<small>" in msg else set())},
    }


# the live QML part for each part of the preview's markup
_LIVE_PART = {"chat": "chatRow", "av": "chatAvatar", "r": "chatTime", "ub": "unreadBadge",
              "msg": "messageRow", "meta": "messageMeta"}


def _card_check(isl, spec, expect, label):
    """Render `spec` on the live island (offscreen) and compare it with what the preview draws."""
    from PySide6.QtCore import QRectF
    from PySide6.QtQuick import QQuickItem
    isl.clear_widget()
    _spin(300)
    isl.bridge_.widget.emit(spec)
    _spin(1400)                                          # rows rise in 45 ms apart, badges pop after them
    info = _v(isl.call("extrasInfo"))
    assert info["widget"] == expect["widget"] and info["widgetReady"], (label, info)
    card = isl.root.findChild(QQuickItem, "widgetCard")
    assert card is not None and card.isVisible(), label
    heads = _named(card, "cardHeader")
    titles = [str(t.property("text")) for t in _named(card, "cardTitle")]
    if expect["title"]:
        assert heads and titles == [expect["title"]], (label, titles)
    else:
        assert not heads and not titles, (label, "a card without a title has no header", titles)
    ids = set(W.registry())
    for t in _texts(card):
        assert t.strip() not in _BAD_TEXT and t.strip() not in ids, (label, "debug or type label on the card", t)
        assert "{" not in t and "undefined" not in t, (label, t)
    assert bool(_named(card, "cardIcon")) == expect.get("icon", False), (label, "service icon")
    if "rows" in expect:
        row = "chatRow" if expect["widget"] == "telegram_chats" else "messageRow"
        assert len(_named(card, row)) == expect["rows"], (label, row, len(_named(card, row)))
    if "badges" in expect:
        assert len(_named(card, "unreadBadge")) == expect["badges"], (label, "badges")
    # the panel fits the window and the grab shows a drawn card (not an empty or cut panel)
    assert info["pillH"] <= 400 - 8, (label, info["pillH"])
    img = isl.view.grabWindow()
    assert not img.isNull(), label
    r = card.mapRectToScene(QRectF(0, 0, card.width(), card.height()))
    sx, sy = img.width() / isl.view.width(), img.height() / isl.view.height()
    colours = set()
    for gx in range(12):
        for gy in range(8):
            x = int((r.x() + r.width() * (gx + 0.5) / 12) * sx)
            y = int((r.y() + r.height() * (gy + 0.5) / 8) * sy)
            if 0 <= x < img.width() and 0 <= y < img.height():
                colours.add(img.pixel(x, y))
    assert len(colours) >= 3, (label, "the grab of the card is blank")
    if os.environ.get("HC_CARD_SHOTS"):                   # tools: look at what CI rendered
        img.save(str(Path(os.environ["HC_CARD_SHOTS"]) / f"{label}.png"))
    return card


def test_tool_results_map_to_their_widgets_in_both_formats():
    """Recorded-shape tool results (JSON and the server's markdown) -> the right widget and data."""
    for f in sorted(FIXTURES.glob("*.json")):
        fx = json.loads(f.read_text(encoding="utf-8"))
        spec = _fixture_spec(fx)
        w = W.for_qml(spec)
        assert w is not None and w["widget"] == fx["expect"]["widget"], (f.name, spec)
        assert w["title"] == fx["expect"]["title"], (f.name, w["title"])
        d = w["data"]
        if w["widget"] == "telegram_chats":
            assert len(d["chats"]) == fx["expect"]["rows"] <= 6
            assert all(c["name"] and "**" not in c["name"] for c in d["chats"])
            assert d["chats"][0]["unread"] == 2 and d["chats"][0]["link"] == "tg://resolve?domain=nova_test"
            assert "\n" not in d["chats"][1]["last"]
        if w["widget"] == "telegram_message":
            assert len(d["messages"]) == fx["expect"]["rows"] <= 5
            assert all("**" not in m["text"] and m["text"] for m in d["messages"])
    # never a card from a tool that writes (send, edit, delete...), whatever it returns
    for tool in ("telegram_send_message", "telegram_edit_message", "telegram_delete_messages", "telegram_forward_messages"):
        assert W.from_tool("mcp__telegram__" + tool, {}, {"messages": [{"text": "x"}]}, "") is None, tool
    # an odd result is no card, not an error
    assert W.from_tool("mcp__telegram__telegram_list_dialogs", {}, None, "# Conversations (0)\n") is None
    assert W.from_tool("mcp__telegram__telegram_get_messages", {}, {"messages": "?"}, "") is None


def test_live_cards_from_real_tool_results_match_the_preview():
    """Every recorded tool result renders on the live QML island (offscreen) with the
    preview's structure: header title and icon, one row per chat or message, an unread
    badge per unread chat, and no "Text" or debug labels anywhere on the card."""
    isl = _island()
    b = isl.bridge_
    b.state.emit("speaking", "Here you go.")
    _spin(300)
    for f in sorted(FIXTURES.glob("*.json")):
        fx = json.loads(f.read_text(encoding="utf-8"))
        _card_check(isl, _fixture_spec(fx), fx["expect"], f.stem)
    # the manifests' examples against the preview's own markup
    ref = _preview_layout()
    for wid, want in ref.items():
        assert want["parts"] >= ({"chat", "av", "n", "m", "r", "ub"} if wid == "telegram_chats" else {"msg", "meta", "me"}), (wid, want)
        man = W.registry()[wid]
        spec = {"widget": wid, "data": man["example"], "text": ""}
        expect = {"widget": wid, "title": W.title_for(man, man["example"]), "icon": True}
        assert expect["title"].split(" ")[0] == want["title"], (wid, expect["title"], want["title"])
        if wid == "telegram_chats":
            expect.update(rows=want["rows"], badges=want["badges"])
        card = _card_check(isl, spec, expect, wid + "_example")
        for part in want["parts"]:
            if part in _LIVE_PART and not (part == "ub" and not want["badges"]):
                assert _named(card, _LIVE_PART[part]), (wid, "the preview has", part, "the live card doesn't")
    isl.clear_widget()
    _spin(300)


def test_a_long_answer_is_never_cut_and_the_followup_is_quiet_over_a_card():
    """With a card open: the whole answer is on the panel (it scrolls, no ellipsis), and
    the followup cue is the ring around the ember, not the thread and "go on"."""
    from PySide6.QtQuick import QQuickItem
    isl = _island()
    b = isl.bridge_
    fx = json.loads((FIXTURES / "telegram_list_dialogs_markdown.json").read_text(encoding="utf-8"))
    long_answer = " ".join(f"Sentence number {i} of a long answer that goes on for a while." for i in range(1, 15))
    isl.start_turn({"question": "show me the recent messages"})
    b.state.emit("thinking", "")
    _spin(300)
    b.widget.emit(_fixture_spec(fx))
    b.state.emit("speaking", long_answer)
    _spin(800)
    b.state.emit("followup", "4")
    _spin(900)
    p = _v(isl.call("probe"))
    assert p["state"] == "followup", p
    assert p["thrA"] < 0.05 and p["washA"] == 0 and p["dotsA"] == 0 and "go on" not in p["labels"], p
    assert p["drainA"] > 0.5 and 0 < p["drainU"] < 1, p
    drain = isl.root.findChild(QQuickItem, "followupDrain")
    assert drain is not None and drain.isVisible()                    # the ring around the ember
    u0 = p["drainU"]
    _spin(1200)
    assert _v(isl.call("probe"))["drainU"] < u0 - 0.15                    # it drains with the window
    b.state.emit("idle", "")
    _spin(1200)
    info = _v(isl.call("extrasInfo"))
    assert info["widget"] == "telegram_chats" and info["answer"] == long_answer, info
    # one place for the answer: the row under the question holds every word of it, in order
    lines = [i for i in _items(isl.root) if i.objectName() == "answerLine"]
    said = " ".join(re.sub(r"<[^>]+>", "", str(t.property("text"))) for t in lines).split()
    assert said == long_answer.split(), "the answer row lost or cut words"
    view = isl.root.findChild(QQuickItem, "answerView")
    assert view.height() < view.property("contentHeight"), "this answer is longer than the room left: it scrolls"
    assert view.property("interactive")
    assert info["pillH"] <= 400 - 8, info
    # without a panel the followup is the usual one ("go on", the wave, the same ring)
    isl.clear_widget()
    isl.model.questionShown.emit("")                                       # a new turn: the panel empties
    _spin(600)
    assert _v(isl.call("extrasInfo"))["height"] == 0
    b.state.emit("followup", "3")
    _spin(700)
    p = _v(isl.call("probe"))
    assert "go on" in p["labels"] and p["washA"] == 0 and p["drainA"] > 0.5, p   # the ring, no sweep
    b.state.emit("idle", "")
    _spin(600)


def test_the_speaking_wave_fills_the_head_up_to_the_controls():
    """Regression: in a turn (every voice request) the answer goes to the row under the head, and
    the speaking wave stopped at x 208 of a 440+ px pill: half the head looked empty. It runs to
    the controls now (14 px short of the mic, like the thinking label) at every width the pill
    takes, and follows it when a card widens it; outside a turn the words still get the room."""
    isl = _island()
    b = isl.bridge_
    b.listening.emit(True, 0.0)
    b.state.emit("idle", "")
    _spin(600)

    def drawn():
        s, p = _v(isl.call("snapshot")), _v(isl.call("probe"))
        return s, p

    b.state.emit("speaking", "Done. Standup now starts at ten.")            # no turn: words in the head
    _spin(1200)
    s, p = drawn()
    assert s["state"] == "speaking" and not s["turn"]
    assert s["threadMax"] <= 80, s                                          # words start at x 80
    b.state.emit("idle", "")
    _spin(1500)
    b.turn.emit({"question": "what's on my calendar tomorrow?"})
    b.state.emit("thinking", "thinking…")
    _spin(800)
    b.state.emit("speaking", "You have two meetings tomorrow: design review at ten.")
    _spin(1500)
    s, p = drawn()
    assert s["turn"] and p["w"] >= 440, (s, p)
    mic_left = p["w"] - 56 - 12
    assert mic_left - 16 <= s["threadMax"] <= mic_left - 10, (s["threadMax"], p["w"])
    for calm in (True, False):                                              # calm: the target is the same
        isl._send("setMotion", ["calm" if calm else "full"])
        _spin(400)
        s = _v(isl.call("snapshot"))
        assert abs(s["tx1t"] - (p["w"] - 80)) <= 1 and s["threadMax"] <= mic_left - 10, s
    isl.show_approval({"id": "apw", "title": "Run git push?", "detail": "", "tool": "Bash", "session": "x"})
    _spin(1500)                                                             # a 460 px card: the pill widens
    s2, p2 = drawn()
    assert p2["w"] >= 460, p2
    assert abs(s2["threadMax"] - (p2["w"] - 80)) <= 2, (s2["threadMax"], p2["w"])
    isl.withdraw_approval("apw")
    b.state.emit("idle", "")
    _spin(1500)


def test_qsb_is_built_from_the_current_shader():
    import subprocess
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "build_shaders.py"), "--check"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


if __name__ == "__main__":
    n = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            n += 1
            print("ok", name)
    print(n, "passed")
    if _ISL is not None:
        _ISL.close()
        _spin(300)
    sys.stdout.flush()
    sys.stderr.flush()
    # End without DLL teardown: Qt's loader / render threads are still alive here,
    # and a hard exit through DLL_PROCESS_DETACH sometimes faults (exit 0xC0000005
    # in CI after every test had passed).
    if sys.platform == "win32":
        import ctypes
        k32 = ctypes.windll.kernel32
        k32.TerminateProcess(k32.GetCurrentProcess(), 0)
    os._exit(0)
