"""Offline tests for the UX stress harness (tools/ux_stress.py, heyclaude/ux_probe.py):
the jank detectors on hand-made timelines, the simulated room (live WavSource),
and an end-to-end offline run: the real Assistant, Speaker, Bridge and QML
island (offscreen) with a scripted brain and a clocked player, through the
same scenarios as the live harness. The detectors must stay quiet.

    .venv\\Scripts\\python tests\\test_ux_stress.py
"""
import faulthandler
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
faulthandler.enable()
sys.stdout.reconfigure(line_buffering=True)
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from heyclaude import ux_probe as UX  # noqa: E402
from heyclaude.listen import BLOCK, RATE, SimRoom, WavSource  # noqa: E402


# ------------------------------------------------------------------ timelines
def _tl(*evs):
    return [dict(e) for e in evs]


def S(t, state="thinking", **kw):
    d = {"t": t, "k": "sample", "state": state, "wt": 300, "ht": 40, "extrasH": 30, "said": "", "label": "x"}
    d.update(kw)
    return d


def _turn(t=0.0, q="what's up"):
    return [{"t": t - 1.0, "k": "user", "label": q, "t0": t - 2.0, "t1": t - 0.5}, {"t": t, "k": "turn", "question": q}]


def kinds(r):
    return sorted({f["kind"] for f in r["findings"]})


def test_a_clean_turn_has_no_findings():
    ev = _turn() + [S(0.1 * i, "thinking", label="thinking…") for i in range(1, 10)]
    ev += [{"t": 1.0, "k": "audio", "text": "It is sunny today.", "secs": 1.6},
           {"t": 1.0, "k": "cmd", "fn": "go", "args": '["speaking", {}]'}]
    words = "It is sunny today.".split()
    ev += [S(1.0 + 0.1 * i, "speaking", said=" ".join(words[:min(4, 1 + i // 4)]), wt=400) for i in range(17)]
    ev += [S(2.8 + 0.1 * i, "followup", extrasH=50) for i in range(10)]
    ev += [S(4.0 + 0.1 * i, "ready", extrasH=0) for i in range(5)]
    r = UX.detect(ev)
    assert r["findings"] == [], r["findings"]
    lat = r["latency"][0]
    assert lat["first_visual"] == 0.6 and lat["first_audio"] == 1.5


def test_idle_in_the_middle_of_a_turn():
    ev = _turn() + [{"t": 1.0, "k": "audio", "text": "One.", "secs": 0.5}, {"t": 2.5, "k": "audio", "text": "Two.", "secs": 0.5}]
    ev += [S(1.6, "ready"), {"t": 1.55, "k": "state", "mode": "idle", "text": ""}]
    assert "idle_mid_turn" in kinds(UX.detect(ev))


def test_idle_after_a_stop_is_fine():
    ev = _turn() + [{"t": 1.0, "k": "audio", "text": "One two three four five.", "secs": 3.0},
                    {"t": 1.5, "k": "action", "what": "click_stop"}, {"t": 1.52, "k": "audio_stop"}, S(1.7, "ready", extrasH=0)]
    assert "idle_mid_turn" not in kinds(UX.detect(ev))


def test_text_revealed_twice_and_speaking_entered_twice():
    ev = _turn() + [{"t": 1.0, "k": "audio", "text": "Hello there friend.", "secs": 1.0}]
    ev += [S(1.1, "speaking", said="Hello there"), S(1.2, "speaking", said="Hello there friend."),
           S(1.3, "speaking", said=""), S(1.4, "speaking", said="Hello there")]
    ev += [{"t": 1.0, "k": "cmd", "fn": "go", "args": '["speaking", {"text": ""}]'},
           {"t": 1.35, "k": "cmd", "fn": "go", "args": '["speaking", {"text": ""}]'}]
    assert "text_revealed_twice" in kinds(UX.detect(ev))


def test_widget_removed_before_the_answer_ends():
    ev = _turn() + [{"t": 1.0, "k": "w_shown", "widget": "weather"}, {"t": 2.0, "k": "w_cleared"},
                    {"t": 1.5, "k": "audio", "text": "Sunny and warm all day today.", "secs": 3.0}]
    assert "widget_removed_early" in kinds(UX.detect(ev))


def test_expand_collapse_twice():
    ev = _turn() + [S(0.1, extrasH=30), S(0.5, extrasH=0), S(0.9, extrasH=30), S(1.3, extrasH=0),
                    {"t": 0.2, "k": "audio", "text": "Ok.", "secs": 1.5}]
    assert "expand_collapse" in kinds(UX.detect(ev))


def test_words_out_of_step_with_the_voice():
    ev = _turn() + [{"t": 1.0, "k": "audio", "text": "Alpha beta gamma delta.", "secs": 2.0}]
    ev += [S(1.0 + 0.1 * i, "speaking", said="" if i < 15 else "Alpha beta gamma delta.") for i in range(30)]
    r = UX.detect(ev)
    assert "text_audio_sync" in kinds(r), r["findings"]


def test_talking_over_the_user_and_two_voices():
    ev = _turn() + [{"t": 1.0, "k": "audio", "text": "A long sentence here.", "secs": 3.0},
                    {"t": 2.0, "k": "audio", "text": "Another one.", "secs": 1.0},
                    {"t": 1.5, "k": "user", "label": "stop", "t0": 1.5, "t1": 2.5}]
    assert "overlapping_voices" in kinds(UX.detect(ev))


def test_dead_air_and_layout_jumps():
    ev = _turn() + [S(0.1, "thinking", wt=300), S(0.4, "thinking", wt=400), S(0.7, "thinking", wt=300),
                    S(4.0, "thinking", wt=300, label="still"),
                    {"t": 4.2, "k": "audio", "text": "Done.", "secs": 0.5}]
    k = kinds(UX.detect(ev))
    assert "dead_air" in k and "layout_jump" in k, k


def test_focus_taken_without_the_field():
    ev = _turn() + [{"t": 0.5, "k": "focus", "island": True, "active": True}]
    assert "took_focus" in kinds(UX.detect(ev))
    ev2 = _turn() + [{"t": 0.2, "k": "action", "what": "hotkey"}, {"t": 0.5, "k": "focus", "island": True, "active": True}]
    assert "took_focus" not in kinds(UX.detect(ev2))


def test_dropped_frames_near_a_transition():
    ev = _turn() + [{"t": 0.5, "k": "cmd", "fn": "go", "args": '["thinking", {}]'},
                    {"t": 0.7, "k": "hitch", "ms": 140.0, "grab": False}, {"t": 0.8, "k": "live_ticks", "n": 50}]
    r = UX.detect(ev)
    assert "dropped_frames" in kinds(r) and r["frames"]["worst_hitch_ms"] == 140.0


def test_timeline_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        ev = _turn() + [S(0.3)]
        UX.save_timeline(ev, Path(d) / "t.jsonl")
        assert UX.load_timeline(Path(d) / "t.jsonl") == ev


# ------------------------------------------------------------------ the simulated room
def test_room_mixes_speech_noise_and_echo_in_real_time():
    clock = [100.0]
    room = SimRoom(noise_dbfs=None, echo_gain=0.5, echo_delay_s=0.0, clock=lambda: clock[0])
    pcm = np.full(BLOCK * 2, 1000, np.int16)
    t0, t1 = room.say(pcm, label="hello")
    assert abs((t1 - t0) - 2 * BLOCK / RATE) < 1e-9
    n0 = round((t0 - room.t0) * RATE / BLOCK)
    assert room.block(n0 - 1).max() == 0 and room.block(n0).max() == 1000 and room.block(n0 + 2).max() == 0
    clock[0] = room.block_time(n0 + 5)
    room.echo_push(np.full(BLOCK, 2000, np.int16))
    assert room.block(n0 + 5).max() == 1000            # echo at half gain
    room2 = SimRoom(noise_dbfs=-40, clock=lambda: 0.0)
    rms = float(np.sqrt(np.mean(room2.block(0).astype(np.float32) ** 2)))
    assert 50 < rms < 800, rms


def test_live_wavsource_reads_the_room_and_closes():
    room = SimRoom(noise_dbfs=None)
    src = WavSource.live(room)
    room.say(np.full(BLOCK * 3, 500, np.int16), label="x")
    got = []
    for b in src:
        got.append(int(b.max()))
        if len(got) >= 6:
            src.close()
    assert 500 in got and len(got) == 6
    assert src.finished                               # no clips: a live source is never "waiting"


# ------------------------------------------------------------------ end to end, offline
def test_offline_run_is_quiet():
    """The whole harness, offline: real Assistant + Speaker + island, scripted brain."""
    with tempfile.TemporaryDirectory() as d:
        cmd = [sys.executable, str(ROOT / "tools" / "ux_stress.py"), "run", "--offline", "--grab-fps", "4", "--no-gif",
               "--out", d, "quick", "weather", "volume_undo", "followup_chain"]
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONIOENCODING="utf-8")
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=600, env=env, cwd=str(ROOT))
        res = json.loads((Path(d) / "summary.json").read_text(encoding="utf-8")) if (Path(d) / "summary.json").exists() else None
        assert res is not None, p.stdout[-3000:] + p.stderr[-3000:]
        names = [r["name"] for r in res]
        assert names == ["quick-1", "weather-1", "volume_undo-1", "followup_chain-1"], names
        bad = [(r["name"], f["kind"], f["msg"]) for r in res for f in r["findings"]]
        assert not bad, json.dumps(bad, indent=1)
        exp = {r["name"]: r["expect"] for r in res}
        assert exp["weather-1"].get("widget") and exp["volume_undo-1"].get("effect") and exp["followup_chain-1"].get("turns"), exp
        assert all(not r.get("error") for r in res), [r.get("error") for r in res]


if __name__ == "__main__":
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            t = time.time()
            try:
                fn()
                print(f"ok   {name} ({time.time() - t:.1f}s)")
            except Exception:
                fails += 1
                import traceback
                print(f"FAIL {name}")
                traceback.print_exc()
    print(f"{fails} failure(s)")
    sys.exit(1 if fails else 0)
