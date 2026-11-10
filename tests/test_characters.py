"""Offline tests for M6, characters as plug-ins: the state registry's fallbacks, pack
loading and validation, the antic scheduler and the sleep progression, the driver
(modes, one-shots, mood), the CI checks on every built-in pack (tools/character_lab.py),
a core-only pack running every app state through fallbacks, the island hosting a
character, and Settings > Character.

    .venv\\Scripts\\python tests\\test_characters.py
    .venv\\Scripts\\python -m pytest tests\\test_characters.py
"""
import copy
import faulthandler
import json
import os
import random
import shutil
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
faulthandler.enable()
sys.stdout.reconfigure(line_buffering=True)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from heyclaude import characters as CH  # noqa: E402
from heyclaude import states as S  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CORE_ONLY = ROOT / "tests" / "fixtures" / "characters" / "coreonly"
BUILTINS = ("echo", "cinder", "wren", "tycho", "hum")


# ------------------------------------------------------------------ registry
def test_registry_1_1_adds_budgets_and_the_sleep_states_with_fallbacks():
    reg = S.load()
    assert S.validate(reg) == []
    assert reg["version"].startswith("1.") and reg["version"] != "1.0"
    b = reg["budgets"]
    assert b["idle_fps"] <= 8 and b["antic_fps"] <= 30 and b["interrupt_ms"] <= 150 and b["antic_max_s"] <= 20
    core = {"idle", "off", "listening", "thinking", "talking"}
    assert S.chain("asleep") == ["asleep", "drowsy", "idle"]
    assert S.resolve("waking", core) == "idle"
    for st in reg["states"]:
        assert S.chain(st["id"])[-1] in core


def test_an_older_theme_or_pack_stays_compatible_across_a_minor_bump():
    assert S.compatible("1.0") and S.compatible("1.1") and not S.compatible("2.0")
    S.load_theme("ember-thread")                      # built for 1.0, still loads


def test_a_new_registry_state_needs_no_change_to_any_pack():
    """Add a state (with a fallback) and bump the minor: every pack still validates and resolves it."""
    reg = copy.deepcopy(S.load())
    reg["version"] = "1.99"
    reg["states"].append({"id": "brand_new", "description": "x", "inputs": [], "kind": "loop",
                          "fallback": "sending", "aliases": []})
    assert S.validate(reg) == []
    for man in list(CH.registry(refresh=True).values()) + [CH.load_manifest(CORE_ONLY)]:
        assert CH.validate(man, Path(man["dir"]), reg=reg) == [], man["id"]
        got = S.resolve("brand_new", man["implements"], reg)
        assert got in man["implements"], (man["id"], got)


# ------------------------------------------------------------------ packs
def test_all_five_builtin_packs_load():
    reg = CH.registry(refresh=True)
    for pid in BUILTINS:
        assert pid in reg, f"{pid} didn't load"
        man = reg[pid]
        assert man["builtin"] and Path(man["qml"]).exists()
        assert set(S.load()["core"]) <= set(man["implements"])
        assert 10 <= len(man["antics"]) <= 15, (pid, len(man["antics"]))
        assert man["preview"], f"{pid} has no preview.png"


def test_bad_manifests_are_refused_with_reasons():
    good = json.loads((CORE_ONLY / "character.json").read_text(encoding="utf-8"))

    def problems(**changes):
        m = copy.deepcopy(good)
        for k, v in changes.items():
            if v is None:
                m.pop(k, None)
            else:
                m[k] = v
        return " | ".join(CH.validate(m))

    assert CH.validate(good) == []
    assert "core states missing" in problems(implements=["idle", "off"])
    assert "unknown state" in problems(implements=good["implements"] + ["dancing"])
    assert "isn't compatible" in problems(registry="2.0")
    assert "missing 'licence'" in problems(licence=None)
    assert "reserved" in problems(id="ember")
    ant = copy.deepcopy(good["antics"][0])
    assert "no interrupt exit" in problems(antics=[dict(ant, exit="")])
    assert "duration" in problems(antics=[dict(ant, duration=45)])
    assert "leaves the character's bounds" in problems(antics=[dict(ant, props=[{"id": "rod", "box": [40, 0, 40, 10]}])])
    assert "fps above" in problems(antics=[dict(ant, fps=60)])
    assert "unknown condition" in problems(antics=[dict(ant, when={"moon": "full"})])
    assert "weather is one of" in problems(antics=[dict(ant, when={"weather": ["hail"]})])
    assert "duplicate" in problems(antics=[ant, ant])


def test_user_packs_load_from_state_but_cannot_take_a_builtin_id():
    tmp = Path(tempfile.mkdtemp())
    try:
        user = tmp / "user"
        shutil.copytree(CORE_ONLY, user / "coreonly")
        shutil.copytree(CORE_ONLY, user / "echo")          # tries to replace a built-in
        m = json.loads((user / "echo" / "character.json").read_text(encoding="utf-8"))
        m["id"] = "echo"
        (user / "echo" / "character.json").write_text(json.dumps(m), encoding="utf-8")
        broken = user / "broken"
        broken.mkdir()
        (broken / "character.json").write_text("{not json", encoding="utf-8")
        reg = CH.registry(dirs=(CH.BUILTIN_DIR, user), refresh=True)
        assert "coreonly" in reg and not reg["coreonly"]["builtin"]
        assert reg["echo"]["builtin"], "the built-in Echo must win"
        assert "broken" not in reg
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        CH.registry(refresh=True)


def test_missing_states_resolve_through_fallbacks_and_are_logged_once():
    man = CH.load_manifest(CORE_ONLY)
    logged = []
    assert CH.resolve(man, "sending", log=logged.append) == "thinking"
    assert CH.resolve(man, "sending", log=logged.append) == "thinking"
    assert CH.resolve(man, "speaking", log=logged.append) == "talking"      # an alias, implemented
    assert CH.resolve(man, "done", log=logged.append) == "idle"
    assert len(logged) == 2 and "sending -> working -> thinking" in logged[0]


def test_app_modes_map_onto_registry_states():
    assert CH.state_for("speaking") == "talking"
    assert CH.state_for("ready") == "idle"
    assert CH.state_for("idle", tasks=2) == "working"
    assert CH.state_for("idle", sleep="drowsy") == "drowsy"
    assert CH.state_for("off", paused=True) == "paused"
    assert CH.state_for("approval") == "approval_needed"
    assert CH.state_for("dictating") == "dictating"


# ------------------------------------------------------------------ antic scheduler
def _antics():
    return [{"id": "a", "duration": 5, "weight": 6, "cooldown": 0, "min_idle": 0},
            {"id": "b", "duration": 5, "weight": 3, "cooldown": 0, "min_idle": 0},
            {"id": "c", "duration": 5, "weight": 1, "cooldown": 0, "min_idle": 0},
            {"id": "late", "duration": 5, "weight": 50, "cooldown": 0, "min_idle": 600},
            {"id": "night", "duration": 5, "weight": 50, "cooldown": 0, "min_idle": 0, "when": {"hours": [22, 5]}},
            {"id": "rainy", "duration": 5, "weight": 50, "cooldown": 0, "min_idle": 0, "when": {"weather": ["rain"]}},
            {"id": "deskonly", "duration": 5, "weight": 50, "cooldown": 0, "min_idle": 0, "where": ["desktop"]}]


def _run(sched, n, **ctx):
    picks, now = [], 1000.0
    for _ in range(n):
        c = CH.Context(now=now, idle_s=300, hour=12, **ctx)
        sched.next_at = 1.0                     # due
        a = sched.tick(c)
        if a:
            picks.append(a["id"])
            sched.playing = ""                  # it finished
        now += 10
    return picks


def test_scheduler_picks_by_weight_never_twice_in_a_row_and_honours_conditions():
    s = CH.AnticScheduler(_antics(), rng=random.Random(7))
    picks = _run(s, 3000)
    assert set(picks) == {"a", "b", "c"}         # not late (min_idle), night, rainy, desktop-only
    assert all(x != y for x, y in zip(picks, picks[1:]))
    assert picks.count("a") > picks.count("b") > picks.count("c")
    s2 = CH.AnticScheduler(_antics(), rng=random.Random(1))
    picks2 = _run(s2, 300, weather="rain", where="desktop")
    assert "rainy" in picks2 and "deskonly" in picks2
    night = CH.Context(now=0, idle_s=900, hour=23)
    assert CH.allowed(_antics()[4], night) and not CH.allowed(_antics()[4], CH.Context(hour=12))
    assert CH.allowed(_antics()[3], night) and not CH.allowed(_antics()[3], CH.Context(idle_s=60))


def test_scheduler_cooldowns_frequency_and_disabled_antics():
    ants = [{"id": "x", "duration": 5, "weight": 1, "cooldown": 600}, {"id": "y", "duration": 5, "weight": 1, "cooldown": 600}]
    s = CH.AnticScheduler(ants, rng=random.Random(3))
    picks = _run(s, 120)                          # 120 ticks, 10 s apart = 20 min
    assert len(picks) == 4, picks                 # each at most once per 10 min
    assert CH.AnticScheduler(ants, frequency="off").tick(CH.Context(now=5000, idle_s=999)) is None
    s3 = CH.AnticScheduler(ants, enabled={"y"}, rng=random.Random(2))
    assert set(_run(s3, 200)) == {"y"}
    # the real gap: the first antic only after a full gap, never more often than the frequency allows
    s4 = CH.AnticScheduler([{"id": "x", "duration": 5, "weight": 1}, {"id": "y", "duration": 5, "weight": 1}],
                           frequency="lively", rng=random.Random(5))
    starts, t = [], 0.0
    while t < 3600:
        a = s4.tick(CH.Context(now=t, idle_s=t))
        if a:
            starts.append(t)
        t += 1
    gaps = [b - a for a, b in zip(starts, starts[1:])]
    assert starts and starts[0] >= CH.FREQUENCY_S["lively"] * 0.6
    assert min(gaps) >= CH.FREQUENCY_S["lively"] * 0.6 + 5 - 1
    assert 3600 / CH.FREQUENCY_S["lively"] * 0.6 < len(starts) < 3600 / CH.FREQUENCY_S["lively"] * 1.6


def test_no_antics_while_typing_fullscreen_calm_battery_saver_hidden_or_busy():
    for gate in ("typing", "fullscreen", "reduced_motion", "battery_saver", "hidden"):
        s = CH.AnticScheduler(_antics()[:3], rng=random.Random(1))
        assert _run(s, 50, **{gate: True}) == [], gate
    s = CH.AnticScheduler(_antics()[:3], rng=random.Random(1))
    assert _run(s, 50, resting=False) == []
    # a gate arriving mid-antic ends it
    s = CH.AnticScheduler(_antics()[:3], rng=random.Random(1))
    s.next_at = 1
    assert s.tick(CH.Context(now=10, idle_s=300))
    assert s.playing
    s.tick(CH.Context(now=11, idle_s=300, typing=True))
    assert s.playing == "" and s.next_at > 11


def test_sleep_progresses_and_wakes_with_a_stretch():
    sl = CH.Sleep()
    assert sl.update(60) == ""
    assert sl.update(901) == "drowsy"
    assert sl.update(1500) == "drowsy"
    assert sl.update(1801) == "asleep"
    assert sl.update(0.4) == "waking"
    assert sl.update(1.0) == ""


def test_input_watch_tells_typing_from_pointer_moves():
    seq = iter([(0.2, (10, 10)), (0.2, (10, 10)), (0.3, (50, 60)), (30.0, (50, 60))])
    w = CH.InputWatch(probe=lambda: next(seq))
    assert w.poll(0.0) == (0.2, False)            # first look: no history
    assert w.poll(1.0) == (0.2, True)             # input, the pointer didn't move: keys
    assert w.poll(2.0)[1] is True                 # held for a few seconds
    assert w.poll(9.0) == (30.0, False)


# ------------------------------------------------------------------ driver
class _Clock:
    def __init__(self):
        self.t = 1000.0
        self.q = []

    def __call__(self):
        return self.t

    def later(self, ms, fn):
        self.q.append((self.t + ms / 1000, fn))

    def advance(self, s):
        self.t += s
        due = [x for x in self.q if x[0] <= self.t]
        self.q = [x for x in self.q if x[0] > self.t]
        for _, fn in due:
            fn()


def _driver(man=None):
    clk = _Clock()
    sent = []
    d = CH.Driver(send=lambda fn, a: sent.append((fn, list(a))), later=clk.later, clock=clk, log=None)
    d.configure(man or CH.load_manifest(CORE_ONLY), frequency="lively")
    return d, clk, sent


def _states(sent):
    return [a[0] for fn, a in sent if fn == "state"]


def test_driver_turns_modes_and_events_into_resolved_states():
    d, clk, sent = _driver()
    d.set_mode("idle")
    d.set_mode("listening")
    d.set_mode("speaking")
    d.set_mode("idle", tasks=1)                     # working -> thinking for a core-only pack
    assert _states(sent)[-3:] == ["listening", "talking", "thinking"]
    d.set_mode("idle", tasks=0)
    d.event("done")                                 # done -> happy -> idle: nothing new to show
    assert _states(sent)[-1] == "idle"
    echo = CH.registry()["echo"]
    d, clk, sent = _driver(echo)
    d.set_mode("idle")
    d.event("done")
    assert _states(sent)[-1] == "happy"             # done -> happy for Echo
    clk.advance(5)
    assert _states(sent)[-1] == "idle"              # the one-shot expired
    d.event("done")
    d.set_mode("listening")                         # a real state cuts it short
    assert _states(sent)[-1] == "listening"
    assert ("interrupt", []) in sent


def test_driver_mood_plays_as_the_answer_ends():
    echo = CH.registry()["echo"]
    d, clk, sent = _driver(echo)
    d.set_mode("speaking")
    d.set_mood("excited")
    d.set_mode("idle")
    assert _states(sent)[-1] == "happy"
    d.set_mode("speaking")
    d.set_mood("sorry")
    d.set_mode("followup")
    assert _states(sent)[-1] == "confused"
    d.set_mood("bogus")
    assert d.mood == "neutral"


def test_driver_plays_antics_only_at_rest_and_sleeps():
    d, clk, sent = _driver()
    d.set_mode("idle")
    d.sched.rng = random.Random(4)
    played = []
    for _ in range(600):
        clk.advance(1)
        d.tick(CH.Context(now=clk.t, user_idle_s=20, hour=12))
    played = [a for fn, a in sent if fn == "play"]
    assert played and all(a[0] == "spin" for a in played)
    n = len(played)
    d.set_mode("thinking")
    for _ in range(600):
        clk.advance(1)
        d.tick(CH.Context(now=clk.t, user_idle_s=20, hour=12))
    assert len([a for fn, a in sent if fn == "play"]) == n, "an antic played while busy"
    echo = CH.registry()["echo"]
    d, clk, sent = _driver(echo)
    d.set_mode("idle")
    d.tick(CH.Context(now=clk.t, user_idle_s=1000))
    assert _states(sent)[-1] == "drowsy"
    d.tick(CH.Context(now=clk.t, user_idle_s=2000))
    assert _states(sent)[-1] == "asleep"
    d.tick(CH.Context(now=clk.t, user_idle_s=0.5))
    assert _states(sent)[-1] == "waking"
    clk.advance(3)
    assert _states(sent)[-1] == "idle"


def test_the_mood_line_is_taken_out_of_the_answer():
    from heyclaude.widgets import WidgetFilter
    f = WidgetFilter()
    out = f.feed("Done, the timer is set.\nMOOD happy\n") + f.finish()
    assert out.strip() == "Done, the timer is set." and f.mood == "happy"
    f = WidgetFilter()
    out = f.feed("Moody weather today.\n") + f.feed("MOOD: nonsense") + f.finish()
    assert "Moody weather" in out and "MOOD" not in out and f.mood is None


# ------------------------------------------------------------------ the QML side
_LAB = None


def _lab():
    global _LAB
    if _LAB is None:
        from character_lab import Lab
        _LAB = Lab()
    return _LAB


def test_every_builtin_pack_passes_the_ci_checks():
    from character_lab import check_pack
    lab = _lab()
    reg = CH.registry(refresh=True)
    bad = {}
    for pid in BUILTINS:
        probs = check_pack(lab, reg[pid], builtin=True)
        if probs:
            bad[pid] = probs
    assert not bad, json.dumps(bad, indent=1)


def test_a_core_only_pack_runs_every_app_state_through_fallbacks():
    from character_lab import check_pack
    lab = _lab()
    man = CH.load_manifest(CORE_ONLY)
    assert check_pack(lab, man, builtin=False) == []
    lab.load(man, small=True)
    lab.warnings.clear()
    for st in [s["id"] for s in S.load()["states"]] + list(CH.MODE_STATE) + ["nonsense"]:
        lab.state(st)
        lab.run(0.2, at_fps=60)
        assert lab.call("info")["state"] in man["implements"], st
    assert lab.warnings == []


def test_an_antic_gives_way_to_a_real_state_at_once():
    lab = _lab()
    man = CH.registry()["echo"]
    lab.load(man, small=True)
    lab.state("idle")
    lab.run(1.5)
    aid = man["antics"][0]["id"]
    assert lab.call("playAntic", aid, 10.0)
    lab.run(2.0, at_fps=30)
    assert lab.host.property("antic") == aid
    lab.state("listening")                           # a real state: no antic left, synchronously
    assert lab.host.property("antic") == ""
    assert lab.call("info")["state"] == "listening"


def test_reduced_motion_rests_at_zero_fps():
    lab = _lab()
    for pid in BUILTINS:
        man = CH.registry()[pid]
        lab.load(man, small=True)
        lab.host.setProperty("reducedMotion", True)
        lab.state("idle")
        lab.run(2.0)
        n, _ = lab.run(10.0)
        assert n == 0, f"{pid} still draws {n} frames in calm idle"
        lab.host.setProperty("reducedMotion", False)


# ------------------------------------------------------------------ app side
def test_the_island_hosts_a_character_and_switches_packs_live():
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from heyclaude.island import Bridge
    from heyclaude.island_qml import QmlIsland

    def spin(ms):
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    def info():
        r = isl.call("characterInfo")
        return r.toVariant() if hasattr(r, "toVariant") else r

    b = Bridge()
    cfg = {"screen": "primary", "top_margin": 8, "motion": "full", "character": {"pack": "echo", "antics": "lively"}}
    isl = QmlIsland(b, cfg, wake_label="hey claude", software=True)
    try:
        spin(600)
        assert isl.model.characterUrl.endswith("characters/echo/Character.qml")
        isl.set_state("idle", "")
        spin(200)
        assert info()["ready"] and info()["state"] == "idle"
        isl.set_state("listening", "")
        spin(150)
        assert info()["state"] == "listening"
        isl.set_state("idle", "")
        spin(150)
        assert isl.play_antic(CH.registry()["echo"]["antics"][0]["id"])
        spin(150)
        assert info()["antic"]
        isl.model.hover(True)                          # the pointer arrives: caught
        spin(100)
        assert info()["antic"] == ""
        isl.model.hover(False)
        isl.preview_character("hum")                   # Settings' gallery: live switch
        spin(300)
        assert isl.model.characterUrl.endswith("characters/hum/Character.qml") and info()["ready"]
        isl.preview_character("ember")
        spin(100)
        assert isl.model.characterUrl == ""
        isl.preview_character(None)                    # back to the saved one
        spin(200)
        assert isl.model.characterUrl.endswith("characters/echo/Character.qml")
    finally:
        isl.close()


def test_settings_character_tab_round_trips():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from heyclaude.character_settings import CharacterTab
    tab = CharacterTab()
    seen = []
    tab.preview_pack.connect(seen.append)
    cfg = {"island": {"character": {"pack": "wren", "where": "both", "desktop_size": 140, "antics": "rare",
                                    "antics_off": ["fishing", "not-a-wren-antic"]}}}
    tab.fill(cfg)
    assert tab.current() == "wren" and seen == []
    new = {}
    tab.collect(new)
    cc = new["island"]["character"]
    assert cc["pack"] == "wren" and cc["where"] == "both" and cc["desktop_size"] == 140 and cc["antics"] == "rare"
    assert "not-a-wren-antic" in cc["antics_off"]
    for i in range(tab.gallery.count()):
        if tab.gallery.item(i).data(0x0100) == "tycho":
            tab.gallery.setCurrentRow(i)
    assert seen == ["tycho"]
    first = next(iter(tab._antic_boxes))
    tab._antic_boxes[first].setChecked(False)
    new = {}
    tab.collect(new)
    assert first in new["island"]["character"]["antics_off"]


if __name__ == "__main__":
    n = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            n += 1
            print("ok", name)
    print(n, "passed")
    sys.stdout.flush()
    sys.stderr.flush()
    faulthandler.disable()                  # the hard exit below isn't a crash
    if sys.platform == "win32":
        import ctypes
        k32 = ctypes.windll.kernel32
        k32.TerminateProcess(k32.GetCurrentProcess(), 0)
    os._exit(0)
