"""Offline tests for M2a/M2b: local wake-word training, the default-model
download, the Settings wizard's logic and "that wasn't me".

    .venv\\Scripts\\python tests\\test_wake_train.py

No network, no GPU, no microphone: TTS, downloads and the mic are faked. The
feature/ONNX tests use openWakeWord's own models (they ship in the package).
"""
from __future__ import annotations

import hashlib
import io
import json
import random
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from heyclaude import wake_metrics as WM  # noqa: E402
from heyclaude import wake_models as M  # noqa: E402
from heyclaude import wake_wizard as W  # noqa: E402
from heyclaude.wake_train import augment as A  # noqa: E402
from heyclaude.wake_train import download as D  # noqa: E402
from heyclaude.wake_train import head as H  # noqa: E402
from heyclaude.wake_train import pipeline as P  # noqa: E402
from heyclaude.wake_train import synth as S  # noqa: E402
from heyclaude.wake_train.features import ensure_feature_models  # noqa: E402

ensure_feature_models()          # openWakeWord's feature models (a fresh CI runner doesn't have them)


# -- synthesis plumbing (TTS mocked) -----------------------------------------------------
class FakeKokoro:
    """Records calls; returns a 0.5 s tone at 24 kHz whose pitch encodes the speed."""

    def __init__(self):
        self.calls = []
        self.voices = {v: np.full((510, 1, 256), i, np.float32) for i, v in enumerate(S.KOKORO_ALL)}

    def create(self, text, voice, speed=1.0, lang="en-us", is_phonemes=False):
        self.calls.append(dict(text=text, voice=voice, speed=speed, lang=lang, is_phonemes=is_phonemes))
        if text == "boom":
            raise ValueError("bad phonemes")
        t = np.arange(12000) / 24000
        return (0.3 * np.sin(2 * np.pi * 220 * speed * t)).astype(np.float32), 24000


def test_plan_is_deterministic_and_uses_the_accent_spellings():
    v = S.phrase_variants("hey claude")
    a = S.plan(random.Random(5), 300, v, list(S.KOKORO_ALL))
    b = S.plan(random.Random(5), 300, v, list(S.KOKORO_ALL))
    assert [u.key() for u in a] == [u.key() for u in b]
    labels = {u.label for u in a}
    assert {"hey claude", "hey klod", "hey clod", "hey klawd", "hey klode"} <= labels     # the M2 spellings
    assert any(u.is_phonemes and "ɔt" in u.text for u in a)                              # devoiced Russian-style "t"
    assert any("+" in u.voice for u in a) and any("+" not in u.voice for u in a)         # blends and plain voices
    assert all(0.75 <= u.speed <= 1.35 and -3 <= u.pitch <= 3 for u in a)
    assert {u.lang for u in a if not u.is_phonemes} == {"en-us", "en-gb"}
    assert all(u.lang == "en-us" for u in a if u.is_phonemes)


def test_held_out_voices_never_train_the_default_model():
    held = set(S.HELD_OUT_VOICES)
    assert held < set(S.KOKORO_ALL) and len(held) == 8
    train = [v for v in S.KOKORO_ALL if v not in held]
    u = S.plan(random.Random(1), 500, S.phrase_variants("hey claude"), train)
    used = {x for t in u for x in t.voice.split("@")[0].split("+")}
    assert not used & held


def test_negative_phrases():
    neg = S.negative_phrases("hey claude")
    assert {"hey cloud", "hey clyde", "hey claudia", "hey jarvis", "okay google"} <= set(neg)
    assert "hey claude" not in [n.lower() for n in neg]
    other = S.negative_phrases("okay jasper")
    assert "jasper" in other and "okay" in other and "okay jasper" not in other
    assert any(n.startswith("okay j") and n != "okay jasper" for n in other)       # vowel swaps of the last word


def test_synth_renders_16k_int16_and_blends_voices():
    eng = FakeKokoro()
    u = [S.Utterance("x", "hˈeɪ klˈɔːd", True, "af_heart", 1.0, 0.0),
         S.Utterance("x", "hey claude", False, "af_heart+jm_kumo@0.25", 1.2, 2.0, "en-gb"),
         S.Utterance("x", "boom", False, "af_heart", 1.0, 0.0)]
    got = []
    out = S.Synth(eng).render(u, progress=lambda d, t: got.append((d, t)))
    assert [c.dtype for c in out] == [np.int16] * 3
    assert len(out[0]) == 8000                                        # 0.5 s at 16 kHz
    assert 0 < len(out[1]) < 8000                                     # +2 semitones: shorter
    assert len(out[2]) == 0                                           # a failing clip is skipped, not fatal
    assert np.abs(out[0]).max() > 20000                               # normalised
    blend = eng.calls[1]["voice"]
    i, j = S.KOKORO_ALL.index("af_heart"), S.KOKORO_ALL.index("jm_kumo")
    assert np.allclose(blend, 0.75 * i + 0.25 * j)
    assert eng.calls[0]["is_phonemes"] and eng.calls[1]["lang"] == "en-gb"
    assert got[-1] == (3, 3)


def test_render_parallel_keeps_order_and_reports_progress():
    eng = [FakeKokoro(), FakeKokoro()]
    utts = [S.Utterance(str(i), "hey", False, "af_heart", 0.8 + i * 0.02, 0.0) for i in range(25)]
    seen = []
    out = P.render_parallel(utts, eng, progress=lambda d, t: seen.append(d))
    assert len(out) == 25 and seen[-1] == 25
    single = S.Synth(FakeKokoro()).render(utts)
    assert all(np.array_equal(a, b) for a, b in zip(out, single))
    assert sum(len(e.calls) for e in eng) == 25


# -- augmentation ---------------------------------------------------------------------------
def test_place_end_puts_the_phrase_at_the_end_of_the_window():
    rng = np.random.default_rng(0)
    clip = np.ones(8000, np.float32)
    w = A.place_end(clip, rng, end_jitter=(0.1, 0.1))
    nz = np.flatnonzero(w)
    assert w.size == A.WINDOW == 32000
    assert nz[-1] == A.WINDOW - 1600 - 1 and nz.size == 8000
    long = A.place_end(np.arange(40000, dtype=np.float32), rng, end_jitter=(0, 0))
    assert long[-1] == 39999                                          # long clips keep their end


def test_augment_window_and_effects():
    rng = np.random.default_rng(1)
    clip = (np.sin(np.arange(12000) / 5) * 9000).astype(np.int16)
    for _ in range(20):
        w = A.augment_window(clip, rng)
        assert w.dtype == np.int16 and w.shape == (A.WINDOW,)
        assert np.abs(w.astype(np.int32)).max() <= 32767
    x = A.as_float(clip)
    noise = A.coloured_noise(rng, x.size, "pink")
    y = A.mix_at_snr(x, noise, 10.0)
    snr = 20 * np.log10(A.rms(x) / A.rms(y - x))
    assert abs(snr - 10.0) < 0.1
    assert A.reverb(x, rng).shape == x.shape
    assert abs(A.change_speed(x, 1.25).size - x.size / 1.25) < x.size * 0.02     # 2% steps
    assert abs(A.rms(A.gain_db(x, -6)) / A.rms(x) - 0.501) < 0.01
    beds = [np.full(500, 0.2, np.float32)]
    assert A.background(np.random.default_rng(3), 1000, beds).shape == (1000,)


def test_slice_windows():
    pcm = np.arange(80000, dtype=np.int16)
    ws = A.slice_windows(pcm, hop=16000)
    assert len(ws) == 4 and all(w.shape == (A.WINDOW,) for w in ws) and ws[1][0] == 16000
    short = A.slice_windows(np.ones(1000, np.int16))
    assert len(short) == 1 and short[0][-1] == 1 and short[0][0] == 0
    assert A.slice_windows(np.zeros(0, np.int16)) == []


# -- the feature-shape contract -----------------------------------------------------------------
def test_feature_shape_contract_matches_openwakeword_streaming():
    """2 s windows -> (16, 96): the same 16 embedding frames openwakeword.Model
    hands a wake-word model on the 80 ms block where the window ends."""
    from openwakeword.utils import AudioFeatures

    from heyclaude.wake_train.features import EMB_DIM, N_FRAMES, Embedder, stream_to_windows
    rng = np.random.default_rng(0)
    t = np.arange(A.WINDOW) / 16000
    x = (np.sin(2 * np.pi * 300 * t) * np.sin(2 * np.pi * 3 * t) * 8000 + rng.standard_normal(A.WINDOW) * 500
         ).astype(np.int16)
    e = Embedder("cpu", ncpu=2).embed_windows(np.stack([x, x[::-1].copy(), np.zeros(A.WINDOW, np.int16)]))
    assert e.shape == (3, N_FRAMES, EMB_DIM) == (3, 16, 96) and e.dtype == np.float32
    af = AudioFeatures(inference_framework="onnx")
    pad = np.concatenate([np.zeros(32000, np.int16), x])
    for i in range(0, pad.size, 1280):
        af(pad[i:i + 1280])
    live = af.get_features(16)[0]
    assert live.shape == (16, 96)
    for k in range(16):
        assert np.corrcoef(e[0, k], live[k])[0, 1] > 0.95
    try:
        Embedder("cpu").embed_windows(np.zeros((2, 16000), np.int16))
        raise AssertionError("wrong window length accepted")
    except ValueError:
        pass
    fr = np.arange(20 * 96, dtype=np.float32).reshape(20, 96)
    sw = stream_to_windows(fr)
    assert sw.shape == (5, 16, 96) and np.array_equal(sw[2], fr[2:18])


# -- head + ONNX export ----------------------------------------------------------------------------
def _toy(rng, n):
    pos = rng.standard_normal((n, 16, 96)).astype(np.float32) * 0.5 + 1.0
    neg = rng.standard_normal((4 * n, 16, 96)).astype(np.float32) * 0.5 - 1.0
    return pos, neg


def test_head_learns_and_calibrates():
    rng = np.random.default_rng(0)
    pos, neg = _toy(rng, 300)
    p = H.train([H.Pool("pos", pos, 1, 1.0), H.Pool("neg", neg, 0, 1.0)], steps=300, batch=128, hidden=16,
                neg_weight=(1.0, 1.0), log=None)
    assert H.predict(p, pos).mean() > 0.9 and H.predict(p, neg).mean() < 0.1
    raw = H.logits(p, pos[:5]).copy()
    H.calibrate(p, 0.8)
    assert np.allclose(H.logits(p, pos[:5]), raw - np.log(0.8 / 0.2), atol=1e-4)
    # a raw score of exactly 0.8 now reads 0.5
    x = np.log(0.8 / 0.2)
    assert abs(1 / (1 + np.exp(-(x - np.log(0.8 / 0.2)))) - 0.5) < 1e-9


def test_onnx_export_loads_in_openwakeword_and_listen(tmp_path):
    from openwakeword.model import Model

    from heyclaude.listen import WakeWord
    from heyclaude.wake_train.export import export, read_meta
    rng = np.random.default_rng(2)
    p = H.HeadParams.init(rng, 1536, 32)
    f = export(p, tmp_path / "hey_claude.onnx", {"phrase": "hey claude"})
    m = Model(wakeword_models=[str(f)], inference_framework="onnx")
    assert list(m.models) == ["hey_claude"] and m.model_inputs["hey_claude"] == 16
    assert m.model_outputs["hey_claude"] == 1
    x = rng.standard_normal((4, 16, 96)).astype(np.float32)
    out = np.array([m.model_prediction_function["hey_claude"](x[i:i + 1])[0][0][0] for i in range(4)])
    assert np.allclose(out, H.predict(p, x), atol=1e-5)
    assert 0.0 <= float(m.predict(np.zeros(1280, np.int16))["hey_claude"]) <= 1.0
    assert read_meta(f)["phrase"] == "hey claude"
    w = WakeWord(str(f), 0.5)                                          # what the app does, unchanged
    assert w.fallback is None and w.label == "hey claude" and w.name == "hey_claude"
    assert isinstance(w.feed(np.zeros(1280, np.int16)), float)
    H.save(p, tmp_path / "h.npz")
    q = H.load(tmp_path / "h.npz")
    assert np.allclose(H.predict(q, x), H.predict(p, x))


def test_ensemble_export_averages_logits(tmp_path):
    from openwakeword.model import Model

    from heyclaude.wake_train.export import export, read_meta
    rng = np.random.default_rng(7)
    ens = [H.HeadParams.init(rng, 1536, 16) for _ in range(3)]
    x = rng.standard_normal((5, 16, 96)).astype(np.float32)
    assert np.allclose(H.ens_logits(ens, x), np.mean([H.logits(q, x) for q in ens], 0))
    before = H.ens_logits(ens, x).copy()
    H.calibrate(ens, 0.9)
    assert np.allclose(H.ens_logits(ens, x), before - np.log(9), atol=1e-4)   # each member shifted
    f = export(ens, tmp_path / "hey_claude.onnx")
    m = Model(wakeword_models=[str(f)], inference_framework="onnx")
    assert m.model_inputs["hey_claude"] == 16 and m.model_outputs["hey_claude"] == 1
    out = np.array([m.model_prediction_function["hey_claude"](x[i:i + 1])[0][0][0] for i in range(5)])
    assert np.allclose(out, H.predict(ens, x), atol=1e-5)
    assert read_meta(f)["heads"] == "3"
    pools = [H.Pool("pos", rng.standard_normal((50, 16, 96)) + 1, 1, 1.0),
             H.Pool("neg", rng.standard_normal((200, 16, 96)) - 1, 0, 1.0)]
    seen = []
    e2 = H.train_ensemble(pools, n=2, steps=20, batch=32, hidden=8, log=None,
                          progress=lambda s, n, l: seen.append((s, n)))
    assert len(e2) == 2 and seen[-1] == (40, 40)
    assert not np.allclose(e2[0].W1, e2[1].W1)                        # different seeds


# -- threshold choice -------------------------------------------------------------------------------
def test_threshold_choice_balances_hits_and_false_wakes():
    rng = np.random.default_rng(4)
    pos = np.clip(rng.normal(0.85, 0.05, 200), 0, 1)                 # held-out positives score high
    val = np.clip(rng.normal(0.05, 0.05, 50000), 0, 1)                # validation stream mostly low...
    val[[1000, 9000, 20000]] = [0.62, 0.55, 0.71]                     # ...with three loud spikes
    hours = val.size * 0.08 / 3600                                    # ~1.1 h
    grid = [round(t, 3) for t in np.arange(0.02, 0.991, 0.01)]
    rows, rec = P.choose(pos, val, hours, WM.TARGET_HIT, WM.TARGET_FPH, grid=grid)
    assert rec["meets"] and 0.71 < rec["threshold"] < 0.85            # above every spike, below the clips
    # the default grid is in logits and reaches far past a score of 0.99
    lg = lambda x: np.log(x / (1 - x))                                # noqa: E731
    _, rec_l = P.choose(lg(np.clip(pos, 1e-6, 1 - 1e-6)), lg(np.clip(val, 1e-6, 1 - 1e-6)), hours,
                        WM.TARGET_HIT, WM.TARGET_FPH)
    assert rec_l["meets"] and lg(0.71) < rec_l["threshold"] < lg(0.85)
    assert max(P.LOGIT_GRID) > 10 and abs(P.sigmoid(0.0) - 0.5) < 1e-12
    assert rec["fph"] == 0 and rec["hit"] >= 0.95
    # nothing quiet enough: the safest threshold, flagged as not meeting the target
    _, bad = P.choose(np.full(10, 0.3), np.full(5000, 0.99), 0.1, 0.95, 0.33, grid=grid)
    assert not bad["meets"]
    # wake_eval re-exports the same math
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
    import wake_eval as E
    assert E.recommend is WM.recommend and E.count_triggers is WM.count_triggers


def test_validation_scores_slide_16_frame_windows():
    p = H.HeadParams.init(np.random.default_rng(0), 1536, 8)
    frames = np.random.default_rng(1).standard_normal((100, 96)).astype(np.float32)
    s = P.validation_scores(p, frames, chunk=30)
    assert s.shape == (85,)
    from heyclaude.wake_train.features import stream_to_windows
    assert np.allclose(s, H.predict(p, stream_to_windows(frames)), atol=1e-6)


# -- progress, device, downloads -----------------------------------------------------------------
def test_progress_overall_and_eta():
    now = [0.0]
    seen = []
    pr = P.Progress(seen.append, {k: 10.0 for k, _ in P.STAGES}, clock=lambda: now[0])
    pr.stage("download")
    assert seen[-1]["overall"] == 0 and abs(seen[-1]["eta_s"] - 60) < 1e-6
    now[0] = 5
    pr.update(0.5)
    assert abs(seen[-1]["overall"] - 5 / 60) < 1e-3 and abs(seen[-1]["eta_s"] - 55) < 1e-6
    now[0] = 40                                                       # the download took 4x the estimate
    pr.stage("voices")
    assert seen[-1]["eta_s"] > 60                                     # later stages are scaled up (bounded)
    pr.finish()
    assert seen[-1]["overall"] == 1.0 and seen[-1]["eta_s"] == 0


def test_pick_device():
    yes = lambda: (True, "")                                          # noqa: E731
    no = lambda: (False, "no cuda in onnxruntime")                    # noqa: E731

    class Mon:
        def __init__(self, utils, free=6000):
            self.u, self.free = list(utils), free

        def gpu_util(self):
            return self.u.pop(0) if self.u else 50

        def gpu_free_mb(self):
            return self.free

        def close(self):
            pass
    assert P.pick_device("cpu", cuda_check=yes) == ("cpu", "set to CPU")
    assert P.pick_device("auto", cuda_check=no)[0] == "cpu"
    assert P.pick_device("auto", gpu_util=30, cuda_check=yes, monitor_factory=lambda: Mon([]), sample_s=0)[0] == "cuda"
    assert P.pick_device("auto", gpu_util=85, cuda_check=yes, monitor_factory=lambda: Mon([]), sample_s=0)[0] == "cpu"
    # sampled: hey-claude's own burst (95%) doesn't count, the quiet minimum does
    dev, why = P.pick_device("auto", cuda_check=yes, monitor_factory=lambda: Mon([95, 40, 90]), sample_s=1.6)
    assert dev == "cuda" and "40%" in why
    assert P.pick_device("auto", gpu_util=10, cuda_check=yes, monitor_factory=lambda: Mon([], free=900),
                         sample_s=0)[0] == "cpu"


class _Resp(io.BytesIO):
    status = 206

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def test_ranged_download_resumes(tmp_path):
    blob = bytes(range(256)) * 40                                     # 10240 bytes "on the server"
    calls = []

    def opener(req, timeout):
        a, b = req.get_header("Range")[6:].split("-")
        calls.append((int(a), int(b)))
        data = blob[int(a):int(b) + 1]
        if len(calls) == 1:                                           # first attempt dies half way
            data = data[:1000]
            r = _Resp(data)
            orig = r.read

            def read(n=-1):
                d = orig(n)
                if not d:
                    raise OSError("connection reset")
                return d
            r.read = read
            return r
        return _Resp(data)
    D.time.sleep = lambda s: None
    got = []
    out = D.fetch_range("http://x", tmp_path / "f.bin", 100, 5000, progress=lambda d, t: got.append(d),
                        opener=opener, chunk=512)
    assert out.read_bytes() == blob[100:5100]
    assert calls[0] == (100, 5099) and calls[1] == (1100, 5099)        # resumed where it stopped
    assert got[-1] == 5000 and not (tmp_path / "f.bin.part").exists()
    assert D.fetch_range("http://x", out, 100, 5000, opener=None) == out   # already complete: no request


def test_acav_slices_are_spread_and_stable():
    rows = D.acav_slice_rows(8)
    assert rows == D.acav_slice_rows(8) and rows[:4] == D.acav_slice_rows(4)
    assert rows[0] == 0 and len(set(rows)) == 8
    assert all(r % D.SLICE_ROWS == 0 and r + D.SLICE_ROWS <= D.ACAV_ROWS for r in rows)
    assert max(rows) > D.ACAV_ROWS * 0.8                               # spread over the whole file
    assert D.plan_bytes(2, False) == 2 * D.SLICE_ROWS * 3072
    assert D.url(D.ACAV_FILE).startswith("https://huggingface.co/datasets/davidscripka/openwakeword_features/"
                                         "resolve/" + D.REVISION)


# -- the shared default model: first-run download + SHA-256 ------------------------------------------
def test_default_model_download_checks_sha256(tmp_path):
    data = b"onnx bytes" * 100
    spec = dict(M.DEFAULT_MODEL, sha256=hashlib.sha256(data).hexdigest(), size=len(data))
    dest = tmp_path / "models" / "hey_claude.onnx"
    ok, msg = M.download_default(dest, spec, opener=lambda req, timeout: _Resp(data))
    assert ok and dest.read_bytes() == data and "SHA-256 ok" in msg
    dest.unlink()
    ok, msg = M.download_default(dest, spec, opener=lambda req, timeout: _Resp(b"tampered"))
    assert not ok and "doesn't match" in msg and not dest.exists()
    assert not list(dest.parent.glob("*.download"))

    def down(req, timeout):
        raise OSError("offline")
    ok, msg = M.download_default(dest, spec, opener=down)
    assert not ok and "offline" in msg
    assert M.download_default(dest, dict(spec, sha256=""))[0] is False


def test_ensure_default_model_only_touches_the_default_path(tmp_path):
    data = b"x" * 10
    spec = dict(M.DEFAULT_MODEL, sha256=hashlib.sha256(data).hexdigest())
    op = lambda req, timeout: _Resp(data)                             # noqa: E731
    assert M.is_default_path("models/hey_claude.onnx", tmp_path)
    assert M.is_default_path(str(tmp_path / "models" / "hey_claude.onnx"), tmp_path)
    assert not M.is_default_path("hey_jarvis", tmp_path)
    assert not M.is_default_path("state/wake_models/a/hey_claude.onnx", tmp_path)
    assert M.ensure_default_model("hey_jarvis", tmp_path, spec=spec, opener=op) is None
    msg = M.ensure_default_model("models/hey_claude.onnx", tmp_path, spec=spec, opener=op)
    assert "SHA-256 ok" in msg and (tmp_path / "models" / "hey_claude.onnx").read_bytes() == data
    (tmp_path / "models" / "hey_claude.onnx").write_bytes(b"my own colab model")
    assert M.ensure_default_model("models/hey_claude.onnx", tmp_path, spec=spec, opener=op) is None
    assert (tmp_path / "models" / "hey_claude.onnx").read_bytes() == b"my own colab model"   # left alone


def test_published_default_model_matches_the_staged_asset():
    """When the code names a SHA-256, the staged/downloaded file must match it."""
    from heyclaude.config import ROOT
    f = ROOT / M.DEFAULT_REL
    if M.DEFAULT_MODEL["sha256"] and f.is_file() and f.stat().st_size == M.DEFAULT_MODEL["size"]:
        assert M.sha256(f) == M.DEFAULT_MODEL["sha256"]


# -- the wizard's state machine ----------------------------------------------------------------------
def test_phrase_checks():
    assert W.phrase_problems("")[0] and W.phrase_problems("   ")[0]
    assert W.phrase_problems("привет")[0]                                # needs English letters
    assert W.phrase_problems("hey claude") == ([], [])
    assert any("common" in w or "said all the time" in w for w in W.phrase_problems("hello")[1])
    assert any("short" in w.lower() for w in W.phrase_problems("yo")[1])
    assert any("Long" in w for w in W.phrase_problems("hey there my good old friend")[1])
    assert W.syllables("hey claude") == 2 and W.syllables("okay jasper") == 4


def test_wizard_state_machine(tmp_path):
    st = W.WizardState(target=5, minimum=3)
    assert st.page == "phrase" and not st.can_back()
    st.set_phrase("")
    assert not st.can_next()[0]
    try:
        st.next()
        raise AssertionError("empty phrase accepted")
    except ValueError:
        pass
    st.set_phrase("  hey   claude ")
    assert st.phrase == "hey claude" and st.next() == "record"
    assert st.folder() == (W.phrase_folder("hey claude")[0], "hey_claude_")
    ok, why = st.can_next()
    assert not ok and "0 good clips" in why
    clips = []
    for i, prob in enumerate([[], ["very quiet"], [], []]):
        f = tmp_path / f"hey_claude_{i:03d}_x.wav"
        f.write_bytes(b"RIFF")
        clips.append(f)
        st.add_clip(f, prob)
    assert len(st.good_clips()) == 3 and st.can_next()[0]
    assert st.redo_last() == clips[-1] and not clips[-1].exists()      # redo deletes the file
    assert not st.can_next()[0]
    st.add_clip(clips[-1], [])
    st.cue()
    assert st.next() == "room"
    st.set_room(None)
    assert st.room_skipped and st.can_next()[0]
    assert st.next() == "train"
    assert not st.can_next()[0] and st.can_back()
    st.train_started()
    assert st.train_status == "running" and not st.can_back()
    st.train_failed("cancelled")
    assert st.train_status == "cancelled" and st.can_back()
    st.train_started()
    st.train_progress({"overall": 0.5})
    st.train_finished(tmp_path / "m.onnx", {"at_0_5": {}})
    assert st.can_next()[0] and st.next() == "test"
    assert not st.can_next()[0] and st.back() == "train"


def test_wizard_train_args(tmp_path):
    st = W.WizardState()
    st.set_phrase("hey claude")
    good, bad = tmp_path / "hey_claude_000_a.wav", tmp_path / "hey_claude_001_b.wav"
    st.add_clip(good, [])
    st.add_clip(bad, ["clipped"])
    fw = tmp_path / "neg"
    fw.mkdir()
    (fw / "false_1.wav").write_bytes(b"")
    st.set_room(tmp_path / "room.wav")
    a = st.train_args(false_wakes_dir=fw)
    assert a[:4] == ["personal", "--phrase", "hey claude", "--json"]
    assert str(good) in a and str(bad) not in a                        # clips with problems stay out
    assert a[a.index("--room") + 1] == str(tmp_path / "room.wav")
    assert a[a.index("--false-wakes") + 1] == str(fw)
    st.set_phrase("okay jasper")
    assert "--false-wakes" not in st.train_args(false_wakes_dir=fw)    # those were "hey claude" false wakes


def test_missing_requirements():
    assert W.missing_requirements({}, find_spec=lambda m: object(), isfile=lambda p: True) == []
    miss = W.missing_requirements({}, find_spec=lambda m: None if m == "onnx" else object(), isfile=lambda p: False)
    assert len(miss) == 2 and miss[0].endswith("pip install onnx") and "Kokoro" in miss[1]


def test_apply_and_undo_keep_a_history(tmp_path):
    from heyclaude.config import ROOT
    hist = tmp_path / "history.json"
    cfg = {"wake": {"model": "models/hey_claude.onnx", "threshold": 0.55}}
    model = ROOT / "state" / "wake_models" / "hey_claude-1" / "hey_claude.onnx"
    new = W.apply_model(cfg, model, history=hist)
    assert new["wake"] == {"model": "state/wake_models/hey_claude-1/hey_claude.onnx", "threshold": 0.5}
    assert cfg["wake"]["threshold"] == 0.55                            # the input isn't modified
    new2 = W.apply_model(new, "hey_jarvis", history=hist)
    assert new2["wake"]["model"] == "hey_jarvis"
    back = W.undo_model(new2, history=hist)
    assert back["wake"] == new["wake"]
    back2 = W.undo_model(back, history=hist)
    assert back2["wake"] == cfg["wake"]
    assert W.undo_model(back2, history=hist) is None
    assert json.loads(hist.read_text()) == []


def test_train_runner_parses_progress_and_result():
    lines = [json.dumps({"progress": {"overall": 0.1, "eta_s": 300, "msg": "a"}}), "some warning",
             json.dumps({"log": "[head] step 1"}),
             json.dumps({"done": True, "model": "C:/m.onnx", "report": {"seconds": 9}})]

    class Proc:
        def __init__(self, out, code=0):
            self.stdout, self.code = iter([x + "\n" for x in out]), code

        def wait(self):
            return self.code

        def poll(self):
            return self.code
    got = {"p": [], "l": [], "d": None, "f": None}
    seen_cmd = []

    def popen(cmd, **kw):
        seen_cmd.append((cmd, kw))
        return Proc(lines)
    r = W.TrainRunner(["personal", "--json"], on_progress=got["p"].append, on_log=got["l"].append,
                      on_done=lambda m, rep: got.__setitem__("d", (m, rep)), on_fail=lambda m: got.__setitem__("f", m),
                      python="py", popen=popen)
    r.start()
    import time
    for _ in range(100):
        if got["d"]:
            break
        time.sleep(0.01)
    assert seen_cmd[0][0] == ["py", "-u", "-m", "heyclaude.wake_train", "personal", "--json"]
    assert seen_cmd[0][1]["env"]["HF_HUB_DISABLE_XET"] == "1"
    assert got["p"][0]["overall"] == 0.1 and got["l"] == ["some warning", "[head] step 1"]
    assert got["d"] == (Path("C:/m.onnx"), {"seconds": 9}) and got["f"] is None
    fails = []
    r2 = W.TrainRunner([], on_fail=fails.append, python="py", popen=lambda c, **k: Proc(["Traceback: boom"], 1))
    r2.start()
    for _ in range(100):
        if fails:
            break
        time.sleep(0.01)
    assert fails and "exit 1" in fails[0] and "boom" in fails[0]


# -- recorder + "that wasn't me" -------------------------------------------------------------------
class FakeMic:
    def __init__(self, blocks):
        self.blocks, self.closed = blocks, False

    def start(self):
        pass

    def drain(self):
        pass

    def close(self):
        self.closed = True

    def __iter__(self):
        return iter(self.blocks)


def test_clip_recorder_saves_a_checked_clip(tmp_path):
    import threading
    tone = (np.sin(np.arange(1280) / 3) * 8000).astype(np.int16)
    blocks = [np.zeros(1280, np.int16)] * 5 + [tone] * 12 + [np.zeros(1280, np.int16)] * 30
    is_speech = lambda frame: np.abs(np.frombuffer(frame, np.int16)).max() > 1000   # noqa: E731
    from heyclaude.wake_samples import ClipRecorder, read_wav
    done = threading.Event()
    res = {}
    mic = FakeMic(blocks)
    rec = ClipRecorder(lambda: mic, on_level=lambda v: res.setdefault("lv", []).append(v),
                       on_done=lambda p, pr: (res.update(p=p, pr=pr), done.set()),
                       on_fail=lambda m: (res.update(f=m), done.set()), is_speech=is_speech)
    rec.record(tmp_path / "hey_claude_000_normal.wav")
    assert done.wait(5) and "f" not in res, res.get("f")
    pcm = read_wav(res["p"])
    assert 0.9 < len(pcm) / 16000 < 1.6 and mic.closed and not rec.busy
    assert max(res["lv"]) > 0.5
    done.clear()
    res.clear()
    rec2 = ClipRecorder(lambda: FakeMic([np.zeros(1280, np.int16)] * 3), on_done=lambda *a: done.set(),
                        on_fail=lambda m: (res.update(f=m), done.set()), is_speech=is_speech)
    rec2.record(tmp_path / "x.wav")
    assert done.wait(5) and "didn't hear" in res["f"]


def test_room_recording_and_live_tester(tmp_path):
    import threading

    from heyclaude.wake_samples import record_seconds
    from heyclaude.wake_train.export import export
    blocks = [np.full(1280, 3000, np.int16)] * 100
    lv = []
    pcm = record_seconds(lambda: FakeMic(blocks), 2.0, on_level=lv.append)
    assert len(pcm) == 25 * 1280 and len(lv) == 25                    # stops after 2 s
    p = H.HeadParams.init(np.random.default_rng(0), 1536, 8)
    H.calibrate(p, 1e-5)                                               # scores ~1: every block "hits"
    f = export(p, tmp_path / "hey_claude.onnx")
    scores, hits, done = [], [], threading.Event()
    mic = FakeMic([np.zeros(1280, np.int16)] * 60)
    t = W.LiveTester(f, lambda: mic, on_score=scores.append, on_hit=hits.append)
    orig = mic.close
    mic.close = lambda: (orig(), done.set())
    t.start()
    assert done.wait(20)
    assert len(scores) == 60 and hits[:3] == [1, 2, 3]                  # one wake per 2 s (25 blocks)
    assert t.hits == 3


def test_reject_wake_saves_the_false_wake(tmp_path):
    import time

    from test_offline import _assistant
    a = _assistant()
    a.false_wake_dir = tmp_path
    assert a.reject_wake() is None                                     # nothing to reject yet
    audio = (np.arange(40960) % 200).astype(np.int16)
    a.last_wake = (time.time(), audio)
    a.mode = "listening"
    p = a.reject_wake()
    from heyclaude.wake_samples import read_wav
    assert p.parent == tmp_path and np.array_equal(read_wav(p), audio)
    assert a._reject_rec.is_set() and a.last_wake is None
    a.last_wake = (time.time() - 1000, audio)                          # too old
    assert a.reject_wake() is None


if __name__ == "__main__":
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
