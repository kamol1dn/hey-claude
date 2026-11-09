"""Offline tests for M10 (instant commands without Claude): the slot parsers,
the phrasing corpus and the near-miss set (accuracy printed), follow-ups,
the routing order in Brain.handle, the executors over M9's real tool code
with the Windows APIs mocked, the effect + undo path in the assistant,
timers, learning from misses, and the in-process latency. No Windows state
is touched; no model, GPU or network.

    .venv\\Scripts\\python tests\\test_intents.py
"""
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import yaml  # noqa: E402

from heyclaude import brain as B  # noqa: E402
from heyclaude.intents import Executor, IntentEngine, Timers, learn  # noqa: E402
from heyclaude.intents import slots as S  # noqa: E402
from heyclaude.intents.normalize import normalize  # noqa: E402
from heyclaude.mcp_windows import audio, display, media, registry  # noqa: E402
from heyclaude.undo import UndoStack  # noqa: E402

HERE = Path(__file__).resolve().parent
CORPUS = yaml.safe_load((HERE / "fixtures" / "intents_corpus.yaml").read_text(encoding="utf-8"))
APPS = ["Spotify", "Google Chrome", "Calculator", "Notepad", "Visual Studio Code", "File Explorer", "Discord", "Steam",
        "Word", "Excel", "Telegram Desktop", "Settings", "Microsoft Edge", "Paint", "Windows PowerShell",
        "Registry Editor", "Command Prompt", "Mail", "Clock"]
P = B.Projects({"hey claude": "D:/coding/hey-claude", "inha app": "D:/coding/inha"})


def engine(cfg=None, **kw) -> IntentEngine:
    e = IntentEngine(cfg or {}, state_dir=kw.pop("state_dir", None), **kw)
    e.set_apps(APPS)
    return e


def same(slots: dict, want: dict) -> bool:
    return all(slots.get(k) == v for k, v in (want or {}).items())


# == slot parsers ===================================================================================
def test_spoken_numbers_and_levels():
    table = {"30": 30, "thirty": 30, "thirty five": 35, "thirty-five": 35, "a hundred": 100, "one hundred": 100,
             "three zero": 30, "five oh": 50, "3 0": 30, "seventy": 70, "eleven": 11, "zero": 0, "nineteen": 19,
             "one hundred and five": 105, "five three": None, "the": None, "": None}
    for s, want in table.items():
        assert S.parse_int(s.replace("-", " ")) == want, (s, S.parse_int(s), want)
    lv = {"30": 30, "thirty percent": 30, "30 percent": 30, "half": 50, "a third": 33, "a quarter": 25,
          "three quarters": 75, "max": 100, "maximum": 100, "the max": 100, "full": 100, "minimum": 0,
          "about 40": 40, "a hundred": 100, "101": None, "two hundred": None, "loud": None, "up": None}
    for s, want in lv.items():
        assert S.parse_level(s) == want, (s, S.parse_level(s), want)


def test_relative_amounts():
    table = {"": 10, "a bit": 5, "a little": 5, "a little bit": 5, "slightly": 5, "a tad": 5, "a lot": 25, "way": 25,
             "much": 25, "by 10": 10, "by twenty": 20, "10 percent": 10, "15": 15, "two notches": 4, "a notch": 10,
             "a bit more": 5, "a lot more": 25, "even more": 10, "by banana": None, "by 500": None}
    for s, want in table.items():
        assert S.parse_amount(s, 10) == want, (s, S.parse_amount(s, 10), want)
    assert S.parse_amount("a bit", 20) == 10 and S.parse_amount("a lot", 4) == 10


def test_durations():
    table = {"5 minutes": 300, "five minutes": 300, "five minute": 300, "a five minute": 300, "5 min": 300,
             "10 mins": 600, "30 seconds": 30, "90 seconds": 90, "an hour": 3600, "1 hour": 3600,
             "1 and a half hours": 5400, "one and a half hours": 5400, "an hour and a half": 5400,
             "half an hour": 1800, "a quarter of an hour": 900, "two hours and 15 minutes": 8100,
             "1 hour 30 minutes": 5400, "a minute and a half": 90, "1.5 hours": 5400, "2 hrs": 7200,
             "a couple of minutes": 120, "five": None, "minutes": None, "in python": None, "25 hours": None,
             "five minutes from this essay": None, "0 minutes": None}
    for s, want in table.items():
        assert S.parse_duration(s) == want, (s, S.parse_duration(s), want)
    assert S.say_duration(90) == "1 minute 30 seconds" and S.say_duration(3600) == "1 hour"
    assert S.say_duration(5400) == "1 hour 30 minutes" and S.say_duration(1) == "1 second"


def test_normalize_fixes_whisper_slips_and_filler():
    table = {"Dark mod.": "dark mode", "Volume tree zero.": "volume three zero", "Nest song!": "next song",
             "Hey Claude, could you turn it up a bit, please?": "turn it up a bit", "Un-mute.": "unmute",
             "Turn off Wi-Fi.": "turn off wifi", "Volume 30%.": "volume 30 percent", "What's playing?": "what is playing",
             "Set a timer for 1.5 hours.": "set a timer for 1.5 hours", "Okay, um, so mute please": "mute",
             "Blue tooth on": "bluetooth on", "volum 30": "volume 30"}
    for s, want in table.items():
        assert normalize(s) == want, (s, normalize(s), want)


# == the corpus ======================================================================================
def _run_corpus(e):
    ok, bad = 0, []
    by_source = {}
    for text, intent, want in CORPUS["corpus"]:
        m = e.match(text)
        if m is not None and m.intent == intent and same(m.slots, want):
            ok += 1
            by_source[m.source] = by_source.get(m.source, 0) + 1
        else:
            bad.append((text, intent, want, m.describe() if m else None))
    return ok, bad, by_source


def test_corpus_accuracy():
    e = engine()
    ok, bad, by_source = _run_corpus(e)
    n = len(CORPUS["corpus"])
    acc = ok / n
    print(f"[intents] corpus: {ok}/{n} routed correctly = {acc:.1%} (by layer: {by_source})")
    for b in bad:
        print(f"[intents]   miss: {b[0]!r} want {b[1]} {b[2]} got {b[3]}")
    assert n >= 300, n
    assert acc >= 0.95, f"{acc:.1%}"


def test_near_misses_never_trigger():
    e = engine()
    hits = [(t, e.match(t).describe()) for t in CORPUS["near_miss"] if e.match(t) is not None]
    print(f"[intents] near-misses: {len(CORPUS['near_miss'])} checked, {len(hits)} false trigger(s)")
    for h in hits:
        print(f"[intents]   FALSE TRIGGER: {h}")
    assert len(CORPUS["near_miss"]) >= 80
    assert not hits, hits


def test_near_misses_stay_quiet_with_context_and_a_lower_threshold():
    """Even right after an instant command, and with the classifier looser, near-misses that
    aren't bare follow-ups ("more", "again") stay with Claude."""
    e = engine({"threshold": 0.5})
    e.remember(e.match("volume 30"))
    bare = {"more.", "again.", "the other way.", "up.", "forty."}
    hits = [t for t in CORPUS["near_miss"] if t.lower() not in bare and e.match(t) is not None]
    assert not hits, hits


def test_classifier_alone_is_timid():
    """The second layer on its own: paraphrases above the threshold, questions/negations/
    unanchored text never, and nothing at all with a threshold of 1."""
    e = engine()
    clf = e.clf
    assert clf.predict(normalize("my ears hurt, it's too loud"))[0] == "volume.down"
    for t in ("why is it so loud", "it is not too loud", "write about my ears", "what is the volume of a cone",
              "the song is too long", "how dim is the moon"):
        assert clf.predict(normalize(t)) is None, t
    strict = engine({"threshold": 1.01})
    assert strict.match("My ears hurt, it's too loud.") is None
    assert strict.match("Volume 30") is not None                 # the grammar doesn't use the threshold
    off = engine({"classifier": False})
    assert off.match("I'm done with this song.") is None and off.match("next song") is not None


def test_disabled_engine_matches_nothing():
    e = engine({"enabled": False})
    assert e.match("volume 30") is None and e.match("mute") is None


def test_apps_only_known_names():
    e = engine()
    assert e.match("open spotify").slots == {"app": "Spotify"}
    assert e.match("open chrome").slots == {"app": "Google Chrome"}
    for t in ("open powershell", "open the registry editor", "open command prompt", "open my email", "open the door",
              "launch the rocket", "open youtube"):
        assert e.match(t) is None, t
    bare = IntentEngine({})                                        # no installed list yet: common names only
    assert bare.match("open notepad").slots == {"app": "Notepad"}
    assert bare.match("open frobnicator") is None


def test_sleep_restart_shutdown_are_never_instant():
    e = engine()
    for t in ("shut down", "shut down the computer", "restart the pc", "reboot", "put the computer to sleep",
              "sleep", "go to sleep", "power off", "turn off the computer", "log off", "sign out", "hibernate"):
        assert e.match(t) is None, t
    ex = Executor(runner=lambda n, a: {"ok": True})
    for tool in ("request_power_action", "perform_power_action", "close_window"):
        try:
            ex.call(tool, {})
        except PermissionError:
            continue
        raise AssertionError(f"{tool} ran")


# == follow-ups ======================================================================================
def test_context_followups():
    e = engine()
    for convo in CORPUS["context"]:
        e.forget()
        for text, intent, want in convo:
            m = e.match(text)
            assert m is not None and m.intent == intent and same(m.slots, want), (text, intent, want,
                                                                               m.describe() if m else None)
            res = {"new": want.get("mode")} if intent == "theme.set" else None
            e.remember(m, res)


def test_context_needs_a_recent_command():
    now = [1000.0]
    e = engine({"context_s": 60}, clock=lambda: now[0])
    for t in ("a bit more", "again", "the other way", "make it 40", "less", "next", "previous", "forty"):
        assert e.match(t) is None, t                               # nothing to lean on
    e.remember(e.match("turn it up"))
    assert e.match("a bit more").slots == {"delta": 5}
    now[0] += 61
    assert e.match("a bit more") is None                           # too long ago
    e.remember(e.match("dark mode"))
    assert e.match("a bit more") is None                           # "more" of a theme means nothing
    assert e.match("again") is None
    e.remember(e.match("open spotify"))
    assert e.match("the other way") is None


# == routing order ====================================================================================
class FakeChat:
    def __init__(self, chunks):
        self.chunks = chunks

    def stream(self, text):
        yield from self.chunks


def brain(intents=True, agent=True, cfg=None):
    b = B.Brain.__new__(B.Brain)
    b.projects, b.backend, b.chat, b.timing = P, "fake", FakeChat(["Eight."]), {}
    b.agent = object() if agent else None
    b.intents = engine(cfg) if intents else None
    return b


def test_routing_order():
    b = brain()
    # local exact routes stay first
    assert list(b.handle("Undo that.")) == [("undo",)]
    assert list(b.handle("put it back")) == [("undo",)]
    assert list(b.handle("never mind")) == [("noop",)]
    assert list(b.handle("stop the task"))[0][0] == "task_stop"
    assert list(b.handle("how are my tasks going")) == [("status",)]
    assert list(b.handle("start a task in hey claude: add a readme"))[0][0] == "task"
    assert list(b.handle("start fresh")) == [("fresh",)]
    # instant before control/lookup and the quick tier
    ev = list(b.handle("Set the volume to thirty."))
    assert ev[0][0] == "instant" and ev[0][1].intent == "volume.set" and b.timing["route"] == "local-instant"
    assert b.timing["match_ms"] < 50
    assert list(b.handle("next song"))[0][0] == "instant"
    assert list(b.handle("what time is it"))[0][0] == "instant"
    # weather questions fall through untouched (the weather route / the agent)
    for q in ("What's the weather?", "Is it going to rain tomorrow?", "Do I need an umbrella?",
              "What's the weather in London this week?", "When is sunset?", "What time is sunset today?",
              "What's the UV index?", "How's the air quality?", "Will it snow this weekend?"):
        assert b.instant(q) is None, q
    assert list(b.handle("What's the weather?")) == [("lookup", "What's the weather?")]
    # near-misses that look like PC control go to the agent, not instant
    assert list(b.handle("what's the volume of a sphere")) == [("control", "what's the volume of a sphere")]
    # anything else: the quick tier
    assert list(b.handle("how many legs does a spider have")) == [("say", "Eight.")]
    # instant works without the tool agent; disabled -> today's routes
    assert list(brain(agent=False).handle("mute"))[0][0] == "instant"
    assert list(brain(cfg={"enabled": False}).handle("volume 30")) == [("control", "volume 30")]
    assert list(brain(intents=False).handle("volume 30")) == [("control", "volume 30")]


def test_routing_order_with_the_weather_route():
    """The agreed order with both local routes on: exact commands -> instant (M10) ->
    local weather (weather-mcp) -> quick -> agent. Neither claims the other's requests."""
    from heyclaude.weather_mcp.query import parse

    class Wx:
        def query(self, text, prev_turn_was_weather=False):
            return parse(text)

    b = brain()
    b.wx = Wx()
    assert list(b.handle("Undo that.")) == [("undo",)]
    for q in ("Set the volume to thirty.", "mute", "dark mode", "next song", "what time is it",
              "set a timer for five minutes"):
        ev = list(b.handle(q))
        assert ev[0][0] == "instant" and b.timing["route"] == "local-instant", (q, ev)
    for q in ("What's the weather?", "Is it going to rain tomorrow?", "Do I need an umbrella?",
              "What's the weather in London this week?", "What's the UV index?", "When is sunset?"):
        ev = list(b.handle(q))
        assert ev[0][0] == "weather" and b.timing["route"] == "local-weather", (q, ev)
    for q in ("I'm feeling under the weather", "whether or not it works", "what's the volume of a sphere"):
        assert list(b.handle(q))[0][0] not in ("instant", "weather"), q
    assert list(b.handle("how many legs does a spider have")) == [("say", "Eight.")]


# == executors over M9's tool code, Windows mocked =====================================================
class FakeEndpoint:
    def __init__(self, level=0.45, muted=False):
        self.level, self.muted = level, muted

    def GetMasterVolumeLevelScalar(self):
        return self.level

    def SetMasterVolumeLevelScalar(self, v, ctx):
        self.level = v

    def GetMute(self):
        return 1 if self.muted else 0

    def SetMute(self, m, ctx):
        self.muted = bool(m)


class FakeWin:
    """The thin OS layers of the M9 modules, in memory."""

    def __init__(self):
        self.ep = FakeEndpoint()
        self.reg = {"AppsUseLightTheme": 0, "SystemUsesLightTheme": 0}
        self.track = 0
        self.tracks = [{"title": "So What", "artist": "Miles Davis"}, {"title": "Naima", "artist": "John Coltrane"}]
        self.playing = True
        self.launched, self.locked, self.pages = [], 0, []

    def patches(self):
        from heyclaude.mcp_windows import apps, power, screen

        def now(art=False):
            return dict(self.tracks[self.track % 2], status="playing" if self.playing else "paused", album="", app="x")

        def control(action):
            if action == "next":
                self.track += 1
            elif action == "previous":
                self.track -= 1
            else:
                self.playing = action == "play"
            return True
        return [mock.patch.object(audio, "_os_endpoint", lambda: self.ep),
                mock.patch.object(display, "_os_reg_get", lambda n: self.reg.get(n)),
                mock.patch.object(display, "_os_reg_set", lambda n, v: self.reg.__setitem__(n, v)),
                mock.patch.object(display, "_os_broadcast_theme", lambda: None),
                mock.patch.object(media, "_os_now_playing", now), mock.patch.object(media, "_os_control", control),
                mock.patch.object(media.time, "sleep", lambda s: None),
                mock.patch.object(apps, "_os_start_apps", lambda: [{"name": n, "id": n} for n in APPS]),
                mock.patch.object(apps, "_os_launch", self.launched.append),
                mock.patch.object(power, "_os_lock", lambda: setattr(self, "locked", self.locked + 1)),
                mock.patch.object(screen, "_os_open", self.pages.append)]


def _with(patches, fn):
    for p in patches:
        p.start()
    try:
        return fn()
    finally:
        for p in patches:
            p.stop()


def test_executors_use_the_m9_tools():
    w = FakeWin()
    e = engine()
    ex = Executor(e, runner=registry.call, timers=Timers())

    def go():
        out = ex.run(e.match("volume 30"))
        assert out.tool == "mcp__windows__set_volume" and out.result["old"] == 45 and out.result["new"] == 30
        assert out.say == "Volume 30." and round(w.ep.level * 100) == 30
        assert registry.inverse(out.tool, out.args, out.result) == [("set_volume", {"level": 45})]
        assert ex.run(e.match("turn it up a bit")).result["new"] == 35
        assert ex.run(e.match("mute")).say == "Muted." and w.ep.muted
        assert ex.run(e.match("mute")).say == "Already muted."
        assert ex.run(e.match("unmute")).say == "Sound on." and not w.ep.muted
        out = ex.run(e.match("light mode"))
        assert out.result["old"] == "dark" and out.result["new"] == "light" and w.reg["AppsUseLightTheme"] == 1
        assert out.say == "Light mode."
        assert ex.run(e.match("light mode")).say == "It's already in light mode."
        out = ex.run(e.match("next song"))
        assert out.result["old"]["title"] == "So What" and out.result["new"]["title"] == "Naima" and out.say == "Skipped."
        assert ex.run(e.match("what's playing")).say == "Naima by John Coltrane."
        assert ex.run(e.match("open spotify")).say == "Opening Spotify." and w.launched == ["Spotify"]
        assert ex.run(e.match("lock the screen")).say == "Locked." and w.locked == 1
        assert ex.run(e.match("open wifi settings")).say == "Opening Wi-Fi settings."
        assert w.pages == ["ms-settings:network-wifi"]
    _with(w.patches(), go)


def test_executor_errors_are_spoken_not_raised():
    from heyclaude.mcp_windows import apps
    e = engine()
    ex = Executor(e, runner=registry.call)
    with mock.patch.object(media, "_os_now_playing", lambda art=False: {"status": "none"}):
        out = ex.run(e.match("next song"))
    assert out.error and out.always_say and out.say == "Nothing is playing media right now."
    with mock.patch.object(apps, "_os_start_apps", lambda: []):
        out = ex.run(e.match("open spotify"))
    assert out.error and "no installed app" in out.say.lower()


def test_clock_and_date_are_local():
    from datetime import datetime
    e = engine()
    ex = Executor(e, runner=lambda n, a: (_ for _ in ()).throw(AssertionError("no tool for the clock")),
                  now=lambda: datetime(2026, 11, 10, 15, 7))
    assert ex.run(e.match("what time is it")).say == "It's 3:07 PM."
    assert ex.run(e.match("what's the date")).say == "It's Tuesday, November 10."
    ex.now = lambda: datetime(2026, 11, 10, 0, 30)
    assert ex.run(e.match("what's the time")).say == "It's 12:30 AM."


# == timers ===========================================================================================
def test_timers_fire_cancel_and_status():
    done = []
    tm = Timers(on_done=done.append)
    e = engine()
    e.has_timers = lambda: bool(tm.running())
    ex = Executor(e, timers=tm)
    out = ex.run(e.match("set a 10 minute pasta timer"))
    assert out.say == "Pasta timer set for 10 minutes." and out.widget == {"widget": "timer",
                                                                            "data": {"seconds": 600, "label": "pasta"}}
    from heyclaude.widgets import normalise
    assert normalise(out.widget)["widget"] == "timer"
    assert ex.run(e.match("how much time is left")).say == "10 minutes left."
    assert ex.run(e.match("cancel the tea timer")).say == "There's no tea timer."
    out = ex.run(e.match("cancel the pasta timer"))
    assert out.say == "Timer cancelled." and out.clear_timer_card and not tm.running()
    assert tm.owns("pasta")                                       # its card ending later is ours: not announced again
    assert e.match("how much time is left") is None               # no timer: not ours any more
    assert ex.run(e.match("cancel the timer")).say == "There's no timer running."
    t = tm.start(0.15, "")
    time.sleep(0.4)
    assert done == [t] and t.state == "done" and tm.owns("")
    assert not Timers().owns("pasta")                             # a card the model made: announce it


# == the assistant: effect + undo + ack, the same path as the agent's tools ==============================
class Sig:
    def __init__(self):
        self.got = []

    def emit(self, *a):
        self.got.append(a)


class UI:
    def __init__(self):
        self.effect, self.effect_art, self.state, self.widget, self.widget_clear = Sig(), Sig(), Sig(), Sig(), Sig()


class Spk:
    def __init__(self):
        self.fed = []

    def feed(self, t):
        self.fed.append(t)


def assistant(tmp_dir=None, ack="speak"):
    from heyclaude import assistant as A
    asst = A.Assistant.__new__(A.Assistant)
    asst.ui, asst.cfg = UI(), {"instant": {"ack": ack}}
    asst._pending_power, asst._reply, asst._turn_writes = {}, [], []
    asst.speaker = Spk()
    asst.brain = type("Br", (), {})()
    asst.brain.intents = engine(state_dir=tmp_dir)
    asst.brain.agent = None
    asst.brain.convo = None
    asst.undo_stack = UndoStack(runner=registry.call)
    asst.timers = None
    asst._setup_instant(runner=registry.call)
    return asst


def test_assistant_instant_effect_undo_and_ack():
    w = FakeWin()

    def go():
        asst = assistant()
        timing = {"speech_end": time.perf_counter()}
        m = asst.brain.instant("set the volume to thirty") if hasattr(asst.brain, "instant") else \
            asst.brain.intents.match("set the volume to thirty")
        assert asst._instant(m, "set the volume to thirty", 0, timing) == "instant"
        ev = asst.ui.effect.got[-1][0]
        assert ev["effect"] == "volume" and ev["from"] == 45 and ev["to"] == 30 and ev["undo"] is True
        assert asst.speaker.fed == ["Volume 30. "] and timing["intent"] == "volume.set/grammar"
        assert 0 <= timing["effect"] - timing["speech_end"] < 0.5
        # "undo that" is the existing local route, and the follow-up context is dropped
        assert asst.undo() == "Volume back to 45%." and round(w.ep.level * 100) == 45
        assert asst.brain.intents.match("a bit more") is None
        # a follow-up on the last instant command
        asst._instant(asst.brain.intents.match("turn it down"), "turn it down", 0, {})
        asst._instant(asst.brain.intents.match("a bit more"), "a bit more", 0, {})
        assert round(w.ep.level * 100) == 30                           # 45 - 10 - 5
        # dark mode: the theme effect, undoable
        asst._instant(asst.brain.intents.match("light mode"), "light mode", 0, {})
        assert asst.ui.effect.got[-1][0]["effect"] == "theme" and w.reg["AppsUseLightTheme"] == 1
        asst.undo()
        assert w.reg["AppsUseLightTheme"] == 0
        # silent acks: the effect only, but answers are still spoken
        q = assistant(ack="silent")
        q.speaker.fed.clear()
        q._instant(q.brain.intents.match("mute"), "mute", 0, {})
        assert q.speaker.fed == [] and q.ui.effect.got[-1][0]["effect"] == "mute"
        q._instant(q.brain.intents.match("what's playing"), "what's playing", 0, {})
        assert q.speaker.fed == ["So What by Miles Davis. "]
        # a timer: the card goes up; cancelling takes it down
        asst._instant(asst.brain.intents.match("set a timer for 5 minutes"), "", 0, {})
        assert asst.ui.widget.got[-1][0]["widget"] == "timer" and asst.timers.running()
        asst._instant(asst.brain.intents.match("cancel the timer"), "", 0, {})
        assert asst.ui.widget_clear.got and not asst.timers.running()
    _with(w.patches(), go)


def test_assistant_falls_back_when_an_executor_breaks():
    asst = assistant()
    asst.instant_exec.runner = lambda n, a: (_ for _ in ()).throw(OSError("COM exploded"))
    asked = []
    asst.brain.agent = object()
    asst.brain.lookup = lambda text: asked.append(text) or iter(["Done, volume is 30."])
    asst.gen = 0
    assert asst._instant(asst.brain.intents.match("volume 30"), "volume 30", 0, {}) == "agent"
    assert asked == ["volume 30"]
    asst.brain.agent = None
    asst.speaker.fed.clear()
    assert asst._instant(asst.brain.intents.match("volume 30"), "volume 30", 0, {}) == "instant"
    assert asst.speaker.fed == ["Sorry, that didn't work. "]


# == learning from misses ================================================================================
def test_missed_phrasing_is_logged_and_taught():
    with tempfile.TemporaryDirectory() as d:
        asst = assistant(tmp_dir=d)
        say = "give the boombox some juice"                                  # no grammar rule, no anchor word
        assert asst.brain.intents.match(say) is None
        # the agent handled it with one settings change -> a candidate phrasing
        asst._turn_writes = [("mcp__windows__get_volume", {}, {"level": 45}),
                             ("mcp__windows__set_volume", {"delta": 20}, {"old": 45, "new": 65})]
        with mock.patch.object(learn, "log_miss", wraps=learn.log_miss) as lm, \
                mock.patch("heyclaude.assistant.STATE_DIR", Path(d)):
            asst._learn_miss(say)
        assert lm.called and learn.misses(d)[-1]["intent"] == "volume.step"
        # two changes in one turn are not a phrasing of one command
        with mock.patch("heyclaude.assistant.STATE_DIR", Path(d)):
            asst._turn_writes = [("mcp__windows__set_volume", {"level": 1}, {}), ("mcp__windows__set_theme", {}, {})]
            asst._learn_miss("set things up for the night")
        assert len(learn.misses(d)) == 1
        # "teach it" adds it; from then on it is instant
        asst.instant_exec.state_dir = d
        out = asst.instant_exec.run(asst.brain.intents.match("teach it"))
        assert out.say == 'Got it. "give the boombox some juice" now means volume up.'
        m = asst.brain.intents.match("Give the boombox some juice!")
        assert m is not None and m.intent == "volume.step" and m.source == "taught" and m.slots["delta"] == 20
        assert asst.instant_exec.run(asst.brain.intents.match("teach it")).say.startswith("There's nothing new")
        assert yaml.safe_load((Path(d) / learn.TAUGHT).read_text(encoding="utf-8"))[0]["text"] == say
        # a fresh engine loads what was taught
        assert IntentEngine({}, state_dir=d).match("give the boombox some juice").source == "taught"


def test_tool_to_intent_mapping():
    assert learn.tool_to_intent("mcp__windows__set_volume", {"level": 30}) == ("volume.set", {"level": 30})
    assert learn.tool_to_intent("mcp__windows__set_mute", {"muted": False}) == ("volume.unmute", {})
    assert learn.tool_to_intent("mcp__windows__media_control", {"action": "next"})[0] == "media.next"
    assert learn.tool_to_intent("mcp__windows__set_theme", {"mode": "dark"}) == ("theme.set", {"mode": "dark"})
    assert learn.tool_to_intent("mcp__windows__set_wallpaper", {"path": "x.jpg"}) is None
    assert learn.tool_to_intent("mcp__windows__request_power_action", {"action": "sleep"}) is None


# == latency ==================================================================================================
def test_in_process_latency():
    """text -> match -> executor return, with M9's tool code over the mocked Windows layer."""
    w = FakeWin()
    e = engine()
    ex = Executor(e, runner=registry.call, timers=Timers())
    texts = ["Volume 30.", "Turn it up a bit.", "Mute.", "Unmute.", "Dark mode.", "Light mode.", "Next song.",
             "Pause.", "Play.", "What time is it?", "Brightness down."]

    def go():
        match_ms, total_ms = [], []
        with mock.patch.object(display, "_screens", lambda: [{"id": "panel", "kind": "panel", "level": 50}]), \
                mock.patch.object(display, "_os_panel_set", lambda v: None):
            for _ in range(30):
                for t in texts:
                    t0 = time.perf_counter()
                    m = e.match(t)
                    t1 = time.perf_counter()
                    ex.run(m)
                    t2 = time.perf_counter()
                    match_ms.append((t1 - t0) * 1000)
                    total_ms.append((t2 - t0) * 1000)
        return match_ms, total_ms
    match_ms, total_ms = _with(w.patches(), go)

    def p95(xs):
        return sorted(xs)[int(len(xs) * 0.95) - 1]
    print(f"[intents] latency, text -> match: median {statistics.median(match_ms):.2f} ms, p95 {p95(match_ms):.2f} ms")
    print(f"[intents] latency, text -> match -> executor (M9 mocked): median {statistics.median(total_ms):.2f} ms, "
          f"p95 {p95(total_ms):.2f} ms  (n={len(total_ms)})")
    print(f"[intents] engine load (grammar + classifier fit): {e.load_ms:.0f} ms")
    assert statistics.median(match_ms) < 20 and p95(total_ms) < 100


if __name__ == "__main__":
    n = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            n += 1
            print("ok", name)
    print(n, "passed")
