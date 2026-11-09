"""Offline tests for M5, the Qt Quick island: the state registry, mode routing,
the response-widget contract and stream filter, service icons, plan usage,
and the QML island itself (software renderer, offscreen): every state, every
M9 effect, every widget from a simulated reply, approvals, the mic / stop /
not-me buttons, file drop, the talk-or-type field, a short state storm and the
rest invariant.

    .venv\\Scripts\\python tests\\test_island.py
"""
import json
import os
import random
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
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
    assert any(l["text"] == "what's on tomorrow?" and not l["out"] for l in snap["labels"])
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
    os._exit(0)
