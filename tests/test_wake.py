"""Offline tests for M2 (the "hey claude" wake word): eval math, the sample
recorder's file handling, speaking suppression and the model fallback.
No mic, models or network needed.

    .venv\\Scripts\\python tests\\test_wake.py   (or python -m pytest tests)
"""
import os
import sys
import wave
import zipfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import wake_eval as E  # noqa: E402
from heyclaude import listen as L  # noqa: E402
from heyclaude import wake_samples as S  # noqa: E402


# -- eval math --------------------------------------------------------------------

def test_count_triggers_uses_a_refractory_period():
    s = [0, 0.9, 0.95, 0.9, 0, 0, 0, 0.8, 0, 0, 0, 0, 0.7]
    assert E.count_triggers(s, 0.5, refractory_frames=3) == [1, 7, 12]   # 2 and 3 are the same wake
    assert E.count_triggers(s, 0.5, refractory_frames=10) == [1, 12]
    assert E.count_triggers(s, 0.85, refractory_frames=1) == [1, 2, 3]
    assert E.count_triggers(s, 0.99, refractory_frames=3) == []
    assert E.count_triggers([], 0.5, 3) == []


def test_hit_rate_and_per_hour():
    peaks = [0.2, 0.6, 0.9, 0.95]
    assert E.hit_rate(peaks, 0.5) == 0.75
    assert E.hit_rate(peaks, 0.95) == 0.25
    assert E.hit_rate([], 0.5) is None
    assert E.per_hour(1, 3.0) == 1 / 3
    assert E.per_hour(0, 0.0) is None


def test_sweep_rows():
    neg = [np.array([0.0, 0.7, 0.0, 0.0, 0.4]), np.array([0.6, 0.0])]
    rows = E.sweep([0.3, 0.8], neg, neg_hours=2.0, refractory_frames=2, thresholds=[0.3, 0.5, 0.65, 0.9],
                   held_peaks=[0.8])
    by = {r["threshold"]: r for r in rows}
    assert by[0.3]["false"] == 3 and by[0.3]["fph"] == 1.5 and by[0.3]["hit"] == 1.0
    assert by[0.5]["false"] == 2 and by[0.5]["hit"] == 0.5 and by[0.5]["hit_held_out"] == 1.0
    assert by[0.65]["false"] == 1
    assert by[0.9]["false"] == 0 and by[0.9]["fph"] == 0 and by[0.9]["hit"] == 0.0
    assert E.sweep([0.3], [], 0.0, 2, thresholds=[0.5])[0]["fph"] is None


def _rows(table):
    return [dict(threshold=t, hit=h, hit_held_out=None, false=None if f is None else round(f * 3), fph=f)
            for t, h, f in table]


def test_recommend_picks_the_middle_of_the_best_quiet_range():
    rows = _rows([(0.3, 1.0, 2.0), (0.4, 1.0, 0.5), (0.5, 1.0, 0.0), (0.6, 1.0, 0.0),
                  (0.7, 1.0, 0.0), (0.8, 0.9, 0.0), (0.9, 0.6, 0.0)])
    r = E.recommend(rows)
    assert r["meets"] and r["threshold"] == 0.6 and r["range"] == [0.5, 0.7]


def test_recommend_when_the_hit_rate_is_too_low():
    rows = _rows([(0.3, 0.97, 1.0), (0.5, 0.9, 0.0), (0.7, 0.8, 0.0)])
    r = E.recommend(rows)
    assert not r["meets"] and r["threshold"] == 0.5 and "90%" in r["why"]


def test_recommend_when_nothing_is_quiet_enough():
    rows = _rows([(0.3, 1.0, 5.0), (0.5, 1.0, 2.0), (0.7, 0.9, 1.0)])
    r = E.recommend(rows)
    assert not r["meets"] and r["threshold"] == 0.7 and "false_activation_penalty" in r["why"]


def test_recommend_with_one_kind_of_data_is_tentative():
    pos_only = _rows([(0.3, 1.0, None), (0.5, 1.0, None), (0.7, 0.96, None), (0.9, 0.5, None)])
    r = E.recommend(pos_only)
    assert not r["meets"] and r["threshold"] == 0.5 and "tentative" in r["why"]
    neg_only = _rows([(0.3, None, 3.0), (0.5, None, 0.2), (0.7, None, 0.0), (0.9, None, 0.0)])
    r = E.recommend(neg_only)
    assert not r["meets"] and r["threshold"] == 0.7 and "tentative" in r["why"]
    assert E.recommend([])["threshold"] is None


def test_segments_split_long_files():
    assert E.segments(None) == [(0.0, None)]
    assert E.segments(100.0, seg_s=600) == [(0.0, 100.0)]
    assert E.segments(1500.0, seg_s=600) == [(0.0, 600), (600, 600), (1200, 300.0)]


def test_stream_audio_reads_a_slice_of_a_wav(tmp_path):
    pcm = np.arange(16000 * 3, dtype=np.int16)
    f = tmp_path / "a.wav"
    S.write_wav(f, pcm)
    whole = np.concatenate(list(E.stream_audio(f)))
    assert np.array_equal(whole, pcm)
    part = np.concatenate(list(E.stream_audio(f, start=1.0, dur=0.5)))
    assert np.array_equal(part, pcm[16000:24000])
    assert E.probe_seconds(f) == 3.0


def test_score_stream_feeds_80ms_blocks():
    class M:
        def __init__(self):
            self.sizes, self.resets = [], 0

        def reset(self):
            self.resets += 1

        def predict(self, b):
            self.sizes.append(len(b))
            return {"w": float(b.max()) / 1000}
    m = M()
    chunks = [np.full(2000, 100, np.int16), np.full(1000, 500, np.int16)]   # 3000 samples = 2 blocks + 440
    s = E.score_stream(m, "w", chunks)
    assert m.sizes == [1280, 1280] and m.resets == 1
    assert np.allclose(s, [0.1, 0.5])


# -- sample recorder files ------------------------------------------------------------

def test_clip_names_indices_and_split(tmp_path):
    assert S.next_index(tmp_path) == 0
    for i, tag in [(0, "normal"), (1, "quiet"), (2, "Far away!"), (3, "loud"), (7, "x"), (8, "y")]:
        S.write_wav(S.clip_path(tmp_path, i, tag), np.zeros(1600, np.int16))
    (tmp_path / "notes.txt").write_text("x")
    (tmp_path / "other.wav").write_bytes(b"")
    names = [p.name for p in S.list_clips(tmp_path)]
    assert names[:3] == ["hey_claude_000_normal.wav", "hey_claude_001_quiet.wav", "hey_claude_002_faraway.wav"]
    assert len(names) == 6 and S.next_index(tmp_path) == 9
    train, held = S.split(S.list_clips(tmp_path))
    assert [S.clip_index(p) for p in held] == [3, 7]          # every 4th, by index
    assert [S.clip_index(p) for p in train] == [0, 1, 2, 8]
    S.clip_path(tmp_path, 2, "faraway").unlink()              # deleting one doesn't reshuffle the rest
    assert [S.clip_index(p) for p in S.split(S.list_clips(tmp_path))[1]] == [3, 7]


def test_pack_zips_only_training_clips(tmp_path):
    for i in range(8):
        S.write_wav(S.clip_path(tmp_path, i, "n"), np.zeros(800, np.int16))
    out, n = S.pack(tmp_path)
    assert out == tmp_path / S.ZIP_NAME and n == 6
    names = zipfile.ZipFile(out).namelist()
    assert len(names) == 6 and "hey_claude_003_n.wav" not in names and "hey_claude_007_n.wav" not in names
    assert not out.with_name(out.name + ".tmp").exists()


def test_wav_round_trip_and_format_check(tmp_path):
    pcm = (np.sin(np.arange(4000) / 5) * 9000).astype(np.int16)
    S.write_wav(tmp_path / "sub" / "a.wav", pcm)
    assert np.array_equal(S.read_wav(tmp_path / "sub" / "a.wav"), pcm)
    with wave.open(str(tmp_path / "b.wav"), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(b"\0" * 400)
    try:
        S.read_wav(tmp_path / "b.wav")
        assert False, "should reject 44.1 kHz stereo"
    except ValueError as e:
        assert "16 kHz" in str(e)


def test_trim_and_check_clip():
    rng = np.random.default_rng(1)
    speech = (rng.standard_normal(16000) * 5000).astype(np.int16)
    pcm = np.concatenate([np.zeros(16000, np.int16), speech, np.zeros(24000, np.int16)])
    t = S.trim_clip(pcm, pad_s=0.25)
    assert abs(len(t) - (16000 + 8000)) <= 640              # speech + 0.25 s each side
    assert S.check_clip(t) == []
    assert S.trim_clip(np.zeros(16000, np.int16)).size == 0
    assert any("short" in p for p in S.check_clip(speech[:4000]))
    assert any("quiet" in p for p in S.check_clip((speech // 50)))
    assert any("long" in p for p in S.check_clip(np.tile(speech, 4)))
    assert any("clipped" in p for p in S.check_clip(np.full(16000, 32767, np.int16)))


class _FakeMic:
    """Blocks of silence / loud noise standing in for a microphone."""

    def __init__(self, plan):
        rng = np.random.default_rng(0)
        self.blocks = []
        for kind, n in plan:
            for _ in range(n):
                self.blocks.append(np.zeros(L.BLOCK, np.int16) if kind == "s"
                                   else (rng.standard_normal(L.BLOCK) * 6000).astype(np.int16))
        self.started = self.closed = False

    def start(self):
        self.started = True

    def drain(self):
        pass

    def close(self):
        self.closed = True

    def __iter__(self):
        yield from self.blocks


def _loud(frame):
    return np.abs(np.frombuffer(frame, np.int16)).mean() > 1000


def _one_clip():
    return [("s", 3), ("v", 12), ("s", 8)]


def test_record_session_auto_saves_trimmed_clips(tmp_path):
    mic = _FakeMic(_one_clip() * 3)
    out = []
    saved = S.record_session(3, lambda: mic, folder=tmp_path, auto=True, say=out.append,
                             is_speech=_loud, gap_s=0)
    assert [p.name for p in saved] == ["hey_claude_000_normal.wav", "hey_claude_001_quiet.wav",
                                       "hey_claude_002_loud.wav"]
    assert mic.started and mic.closed
    a = S.read_wav(saved[0])
    assert 0.9 < len(a) / 16000 < 2.0                        # ~1 s of speech plus padding, tail trimmed
    assert any("hey claude" in line for line in out)


def test_record_session_redo_and_quit(tmp_path):
    S.write_wav(S.clip_path(tmp_path, 0, "old"), np.zeros(800, np.int16))   # earlier session
    mic = _FakeMic(_one_clip() * 3)
    answers = iter(["", "r", "", "q"])
    saved = S.record_session(5, lambda: mic, folder=tmp_path, ask=lambda _: next(answers),
                             say=lambda *_: None, is_speech=_loud)
    assert [p.name for p in saved] == ["hey_claude_001_quiet.wav"]          # recorded, redone, then quit
    assert [p.name for p in S.list_clips(tmp_path)] == ["hey_claude_000_old.wav", "hey_claude_001_quiet.wav"]
    assert mic.closed


def test_record_session_auto_stops_when_nobody_speaks(tmp_path):
    mic = _FakeMic([("s", 5)])                                # source ends without speech
    saved = S.record_session(3, lambda: mic, folder=tmp_path, auto=True, say=lambda *_: None,
                             is_speech=_loud, gap_s=0)
    assert saved == [] and mic.closed


# -- speaking suppression -------------------------------------------------------------

class _Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def test_speaking_gate_with_tail_and_live_config():
    clk, cfg = _Clock(), {"suppress_while_speaking": True, "speaking_tail_s": 0.7}
    g = L.SpeakingGate(cfg, clock=clk)
    assert not g.suppressed()
    g.set_speaking(True)
    clk.t += 5
    assert g.suppressed()
    g.set_speaking(False)
    clk.t += 0.5
    assert g.suppressed()                                     # inside the tail
    clk.t += 0.3
    assert not g.suppressed()
    g.set_speaking(False)                                     # idle again without speaking: no new tail
    assert not g.suppressed()
    cfg["suppress_while_speaking"] = False                    # settings window, applied live
    g.set_speaking(True)
    assert not g.suppressed()


class _FakeOww:
    def __init__(self, scores):
        self.models = {"hey_claude": object()}
        self.scores = list(scores)
        self.resets = 0

    def predict(self, block):
        return {"hey_claude": self.scores.pop(0)}

    def reset(self):
        self.resets += 1


def test_wake_word_ignores_its_own_voice_and_resets_after(tmp_path):
    model = tmp_path / "hey_claude.onnx"
    model.write_bytes(b"x")
    fake = _FakeOww([0.9, 0.95, 0.9, 0.1, 0.9, 0.9])
    clk = _Clock()
    gate = L.SpeakingGate({}, clock=clk)
    w = L.WakeWord(str(model), 0.5, gate=gate, loader=lambda p: fake)
    b = np.zeros(L.BLOCK, np.int16)
    gate.set_speaking(True)
    assert not w.hit(b) and not w.hit(b)                      # the assistant says "...Claude..."
    gate.set_speaking(False)
    assert not w.hit(b)                                       # still in the tail
    clk.t += 1.0
    assert not w.hit(b) and fake.resets == 1                  # gate opens: model state forgotten
    assert w.hit(b) and w.hit(b)                              # then the user can wake it
    assert w.ignored == 1                                     # one swallowed trigger, logged once


def test_wake_word_without_gate_is_unchanged(tmp_path):
    fake = _FakeOww([0.2, 0.6])
    w = L.WakeWord("hey_jarvis", 0.5, loader=lambda p: fake)
    b = np.zeros(L.BLOCK, np.int16)
    assert not w.hit(b) and w.hit(b) and w.fallback is None


def test_resolve_wake_model(tmp_path):
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "hey_claude.onnx").write_bytes(b"x")
    assert L.resolve_wake_model("hey_jarvis", root=tmp_path) == ("hey_jarvis", None)
    assert L.resolve_wake_model("models/hey_claude.onnx", root=tmp_path) == (
        str(tmp_path / "models" / "hey_claude.onnx"), None)
    assert L.resolve_wake_model("models/hey_claude.tflite", root=tmp_path)[1] is None   # uses the .onnx
    path, why = L.resolve_wake_model("models/missing.onnx", root=tmp_path)
    assert path == "hey_jarvis" and "not found" in why
    assert L.resolve_wake_model("", root=tmp_path)[0] == "hey_jarvis"
    assert L.wake_label("hey_jarvis_v0.1") == "hey jarvis"
    assert L.wake_label(str(tmp_path / "models" / "hey_claude.onnx")) == "hey claude"


def test_missing_model_falls_back_to_hey_jarvis_at_its_own_threshold(tmp_path):
    loaded = []

    def loader(p):
        loaded.append(p)
        return _FakeOww([]) if p != "hey_jarvis" else type("J", (), {"models": {"hey_jarvis_v0.1": 0}})()
    w = L.WakeWord(str(tmp_path / "nope.onnx"), 0.3, loader=loader)
    assert loaded == ["hey_jarvis"] and w.label == "hey jarvis" and "not found" in w.fallback
    assert w.threshold == L.FALLBACK_THRESHOLD
    w.threshold = 0.2                                         # live threshold change: kept for the real model
    assert w.threshold == L.FALLBACK_THRESHOLD


def test_broken_model_file_falls_back(tmp_path):
    bad = tmp_path / "hey_claude.onnx"
    bad.write_bytes(b"not a model")

    def loader(p):
        if p != "hey_jarvis":
            raise RuntimeError("invalid protobuf")
        return type("J", (), {"models": {"hey_jarvis": 0}})()
    w = L.WakeWord(str(bad), 0.5, loader=loader)
    assert w.source == "hey_jarvis" and "failed to load" in w.fallback


def test_assistant_tells_the_gate_when_it_speaks():
    sys.path.insert(0, str(ROOT / "tests"))
    from test_offline import _assistant
    a = _assistant()
    assert a.wake_gate.cfg is a.cfg["wake"]                   # live section, not a copy
    a._tts_started()
    assert a.wake_gate.suppressed()
    a._tts_idle()
    a.wake_gate._until = 0                                    # skip the tail
    assert not a.wake_gate.suppressed()


def test_example_config_points_at_the_trained_model():
    from heyclaude import config as C
    d = C.defaults()["wake"]
    assert d["model"] == "models/hey_claude.onnx" and d["suppress_while_speaking"] is True
    assert C.is_live("wake.suppress_while_speaking") and C.is_live("wake.speaking_tail_s")
    assert C.is_live("wake.model")                            # M2b: Apply/Undo swaps the model live


if __name__ == "__main__":
    import tempfile
    n = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            if "tmp_path" in fn.__code__.co_varnames[: fn.__code__.co_argcount]:
                fn(Path(tempfile.mkdtemp()))
            else:
                fn()
            n += 1
            print("ok", name)
    print(n, "passed")
