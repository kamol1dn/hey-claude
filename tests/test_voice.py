"""M4 offline tests: the TTS fallback chain (CUDA and Kokoro mocked), end of
turn, Silero framing, echo cancellation / barge-in state, partial transcripts,
and keeping the API key away from every `claude` process. No mic, speakers,
GPU, models or network.

    .venv\\Scripts\\python tests\\test_voice.py   (or pytest)
"""
import os
import sys
import threading
import time

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from heyclaude import aec as A  # noqa: E402
from heyclaude import tts as T  # noqa: E402
from heyclaude import vad as V  # noqa: E402


# -- fakes -------------------------------------------------------------------------------
class FakeVoice:
    pcm = True
    rate = 24000

    def __init__(self, device, fail=None):
        self.device, self.fail, self.said = device, fail, []

    def synth(self, text):
        if self.fail:
            raise self.fail
        self.said.append(text)
        return np.full(240, 0.1 if self.device == "cuda" else 0.2, np.float32)


class FakeSapi:
    pcm = False

    def __init__(self):
        self.said, self.on_start = [], None

    def speak(self, text):
        if self.on_start:
            self.on_start()
        self.said.append(text)

    def stop(self):
        pass


class FakeLoad:
    def __init__(self, gpu=5.0, free=5000.0, cpu=20.0):
        self.gpu, self.free, self.cpu = gpu, free, cpu

    def gpu_util(self):
        return self.gpu

    def gpu_free_mb(self):
        return self.free

    def cpu_pct(self):
        return self.cpu


def engine(cfg=None, stt_cfg=None, cuda=(True, ""), gpu_error=None, cpu_error=None, gpu_fail=None,
           cpu_fail=None, load=None, premiere=False):
    c = {"engine": "kokoro", "device": "gpu", "voice": "af_heart"}
    c.update(cfg or {})
    s = {"gpu_busy_pct": 70, "gpu_resume_pct": 60, "cpu_busy_pct": 85, "yield_to_premiere": False}
    s.update(stt_cfg or {})
    st = {"loads": [], "sapi": FakeSapi(), "labels": []}

    def loader(dev):
        st["loads"].append(dev)
        if dev == "cuda" and gpu_error:
            raise gpu_error
        if dev == "cpu" and cpu_error:
            raise cpu_error
        return FakeVoice(dev, gpu_fail if dev == "cuda" else cpu_fail)

    e = T.VoiceEngine(c, s, monitor=load or FakeLoad(), loader=loader, sapi=lambda: st["sapi"],
                      cuda_check=lambda: cuda, premiere_running=lambda: premiere,
                      on_change=st["labels"].append)
    return e, st


# -- TTS fallback chain ------------------------------------------------------------------
def test_kokoro_on_gpu_when_cuda_works():
    e, st = engine()
    e.start()
    assert e.current.device == "cuda" and e.label == "kokoro gpu" and st["loads"] == ["cuda"]
    audio, rate = e.synth("Hello.")
    assert rate == 24000 and len(audio) == 240


def test_no_cuda_provider_falls_back_to_kokoro_cpu():
    e, st = engine(cuda=(False, "no cuda in onnxruntime"))
    e.start()
    assert st["loads"] == ["cpu"] and e.current.device == "cpu"
    assert e.label == "kokoro cpu (fallback: no cuda)"


def test_gpu_session_failing_to_load_falls_back_to_cpu():
    e, st = engine(gpu_error=RuntimeError("cuDNN is unavailable: LoadLibrary failed for cudnn64_9.dll"))
    e.start()
    assert st["loads"] == ["cuda", "cpu"] and e.current.device == "cpu"
    assert e.label.startswith("kokoro cpu (fallback:") and e.gpu_failed


def test_kokoro_missing_falls_back_to_sapi():
    err = FileNotFoundError("kokoro file missing: state/models/kokoro/kokoro-v1.0.onnx")
    e, st = engine(gpu_error=err, cpu_error=err)
    e.start()
    assert e.current is st["sapi"] and e.label == "sapi (kokoro: model missing)"


def test_cuda_error_while_speaking_retries_the_sentence_on_cpu():
    e, st = engine(gpu_fail=RuntimeError("CUDA error: an illegal memory access was encountered"))
    e.start()
    audio, _ = e.synth("Still speaking.")
    assert e.current.device == "cpu" and e.cpu.said == ["Still speaking."]
    assert e.gpu is None and "cuda error" in e.gpu_failed
    assert e.pick() is e.cpu                       # off for the session


def test_cpu_error_falls_back_to_sapi():
    e, st = engine({"device": "cpu"}, cpu_fail=RuntimeError("bad input"))
    e.start()
    try:
        e.synth("Hi.")
        raise AssertionError("expected _UseSapi")
    except T._UseSapi:
        pass
    assert e.current is st["sapi"] and e.label.startswith("sapi (kokoro:")


def test_busy_gpu_moves_the_next_reply_to_cpu_with_hysteresis():
    load = FakeLoad(gpu=10)
    e, st = engine(load=load)
    e.start()
    assert e.pick() is e.gpu
    load.gpu = 85
    assert e.pick() is e.cpu and e.label == "kokoro cpu (gpu busy 85%)"
    load.gpu = 65                                  # under 70 but above the 60 resume line
    assert e.pick() is e.cpu
    load.gpu = 50
    assert e.pick() is e.gpu and e.label == "kokoro gpu"
    assert "kokoro cpu (gpu busy 85%)" in st["labels"]


def test_busy_gpu_and_busy_cpu_stays_on_gpu():
    e, _ = engine(load=FakeLoad(gpu=90, cpu=95))
    e.start()
    assert e.pick() is e.gpu


def test_premiere_moves_kokoro_to_cpu_only_when_opted_in():
    e, _ = engine(premiere=True)
    e.start()
    assert e.pick() is e.gpu                        # idle Premiere is fine by default
    e2, _ = engine(stt_cfg={"yield_to_premiere": True}, premiere=True)
    e2.start()
    assert e2.pick() is e2.cpu and e2.label == "kokoro cpu (fallback: premiere)"


def test_gpu_chosen_later_loads_in_the_background():
    load = FakeLoad(gpu=10)
    e, st = engine(gpu_error=RuntimeError("out of memory"))
    e.start()
    assert e.gpu is None and e.gpu_failed           # a failed load turns it off for the session
    e2, st2 = engine({"device": "cpu"}, load=load)
    e2.start()
    assert st2["loads"] == ["cpu"]                  # device cpu never touches CUDA


def test_sapi_engine_never_loads_kokoro():
    e, st = engine({"engine": "sapi"})
    e.start()
    assert st["loads"] == [] and e.label == "sapi" and e.current is st["sapi"]


class FakePlayer:
    def __init__(self):
        self.played, self.on_start = [], None
        self.prepared = 0

    def prepare(self, rate=24000):
        self.prepared += 1

    def play(self, samples, rate):
        self.played.append((len(samples), rate))
        if self.on_start:
            self.on_start()

    @property
    def active(self):
        return False

    def wait(self, gen_check=None, timeout=120, more=None):
        pass

    def stop(self):
        pass


def _speaker(e, **cfg):
    events = []
    sp = T.Speaker(dict({"first_chunk_words": 9}, **cfg), on_start=lambda: events.append("start"),
                   on_idle=lambda: events.append("idle"), engine=e, player=FakePlayer())
    return sp, events


def test_speaker_speaks_sentences_through_kokoro():
    e, st = engine()
    sp, events = _speaker(e)
    sp.say("Canberra is the capital. It's in the ACT.")
    sp.wait(5)
    assert e.gpu.said == ["Canberra is the capital.", "It's in the ACT."]
    assert len(sp.player.played) == 2 and sp.first_audio_t is not None
    assert events[0] == "start" and events[-1] == "idle" and not sp.busy


def test_speaker_falls_back_to_sapi_mid_reply():
    e, st = engine({"device": "cpu"}, cpu_fail=RuntimeError("onnx went away"))
    sp, _ = _speaker(e)
    sp.say("First. Second.")
    sp.wait(5)
    assert st["sapi"].said == ["First.", "Second."] and sp.label.startswith("sapi")


def test_first_chunk_is_cut_at_a_comma_when_long():
    import queue
    sp = T.Speaker.__new__(T.Speaker)               # no TTS thread: read the queue ourselves
    sp.q, sp._buf, sp._gen, sp.cfg, sp._first_chunk = queue.Queue(), "", 0, {"first_chunk_words": 9}, True
    sp.feed("Well, the weather in Tashkent tomorrow looks sunny, with a high of twenty two")
    first = sp.q.get_nowait()[1]
    assert first == "Well, the weather in Tashkent tomorrow looks sunny,"
    sp.feed(" degrees. Then rain.")
    assert sp.q.get_nowait()[1] == "with a high of twenty two degrees."
    sp._buf, sp._first_chunk = "", True
    sp.feed("Short, reply here. ")
    assert sp.q.get_nowait()[1] == "Short, reply here."   # short: whole sentence


def test_player_hands_played_audio_to_the_echo_canceller():
    far = []
    p = T.Player(far_tap=far.append, idle_close_s=999)
    p._open = lambda rate: None                     # no audio device
    p.play(np.ones(24000, np.float32) * 0.5, 24000)
    started = []
    p.on_start = lambda: started.append(1)
    out = np.zeros((2400, 1), np.float32)
    for _ in range(10):
        p._cb(out, 2400, None, None)
    got = np.concatenate(far)
    assert started == [1] and not p.active
    assert abs(len(got) - 16000) <= 2 and got.dtype == np.int16
    assert abs(int(np.median(got)) - 16383) < 200


def test_conv_stft_rewrite_matches_onnx_stft(tmp_path):
    try:
        import onnx
        import onnxruntime as ort
        from onnx import TensorProto, helper, numpy_helper
    except ImportError:
        print("  (skipped: no onnx package)")
        return
    win = (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(20) / 20)).astype(np.float32)   # Kokoro's STFT
    g = helper.make_graph(
        [helper.make_node("STFT", ["x", "step", "win", "len"], ["y"], onesided=1)], "g",
        [helper.make_tensor_value_info("x", TensorProto.FLOAT, ["B", "L"])],
        [helper.make_tensor_value_info("y", TensorProto.FLOAT, None)],
        [numpy_helper.from_array(np.array(5, np.int64), "step"), numpy_helper.from_array(win, "win"),
         numpy_helper.from_array(np.array(20, np.int64), "len")])
    m = helper.make_model(g, opset_imports=[helper.make_opsetid("", 20)])
    m.ir_version = 9
    src, dst = str(tmp_path / "stft.onnx"), str(tmp_path / "conv.onnx")
    onnx.save(m, src)
    T.conv_stft_model(src, dst)
    assert [n.op_type for n in onnx.load(dst).graph.node] == ["Reshape", "Conv", "Reshape", "Transpose"]
    x = np.random.default_rng(0).normal(size=(2, 4005)).astype(np.float32)
    so = ort.SessionOptions()
    so.log_severity_level = 3
    a = ort.InferenceSession(src, so, providers=["CPUExecutionProvider"]).run(None, {"x": x})[0]
    b = ort.InferenceSession(dst, so, providers=["CPUExecutionProvider"]).run(None, {"x": x})[0]
    assert a.shape == b.shape == (2, 798, 11, 2)
    assert np.abs(a - b).max() < 1e-4 * np.abs(a).max()


def test_config_migrates_pre_m4_tts_keys(tmp_path):
    from heyclaude import config as C
    p = tmp_path / "config.yaml"
    p.write_text("tts:\n  backend: sapi\n  rate: 200\n  voice: Zira\n  kokoro_voice: bf_emma\n", encoding="utf-8")
    c = C.load(p)
    assert c["tts"]["engine"] == "kokoro" and c["tts"]["sapi_voice"] == "Zira"
    assert c["tts"]["voice"] == "bf_emma" and c["tts"]["rate"] == 200 and "backend" not in c["tts"]
    p.write_text("tts:\n  backend: piper\n  piper_model: x.onnx\n", encoding="utf-8")
    assert C.load(p)["tts"]["engine"] == "piper"


# -- end of turn --------------------------------------------------------------------------
def test_turn_complete():
    assert V.turn_complete("What's the capital of Australia?") == "complete"
    assert V.turn_complete("Set a timer for ten minutes.") == "complete"
    assert V.turn_complete("What's the weather in Tashkent and") == "incomplete"
    assert V.turn_complete("Tell me about the.") == "incomplete"
    assert V.turn_complete("I want to, um") == "incomplete"
    assert V.turn_complete("Remind me to call Sam,") == "incomplete"
    assert V.turn_complete("open the inha app") == "unknown"
    assert V.turn_complete("") == "unknown" and V.turn_complete(None) == "unknown"


def test_end_of_turn_silence_window():
    cfg = {"end_silence_ms": 700, "short_silence_ms": 300, "long_silence_ms": 1200}
    e = V.EndOfTurn(cfg)
    assert e.needed_ms("What time is it?") == 300
    assert e.needed_ms("What time is it in") == 1200
    assert e.needed_ms("open the inha app") == 700 and e.needed_ms(None) == 700
    cfg["smart_end"] = False
    assert e.needed_ms("What time is it?") == 700


class ScriptVad:
    """frames(block) from a script of 'v' (voiced) / '.' per 80 ms block."""
    frame_ms = 20.0

    def __init__(self, script):
        self.script = list(script)

    def reset(self):
        pass

    def frames(self, block):
        c = self.script.pop(0) if self.script else "."
        return [c == "v"] * 4


def _record(script, transcribe, cfg_over=None):
    from heyclaude.listen import BLOCK, record_utterance
    cfg = {"end_silence_ms": 700, "short_silence_ms": 300, "long_silence_ms": 1200, "pause_probe_ms": 160,
           "start_timeout": 5, "max_seconds": 20, "partial_interval_s": 999}
    cfg.update(cfg_over or {})
    n = {"blocks": 0}

    def blocks():
        for i in range(len(script) + 40):
            n["blocks"] += 1
            time.sleep(0.01)                        # let the background transcription run
            yield np.full(BLOCK, i, np.int16)
    tl = V.TurnListener(transcribe, cfg)
    audio, t_end = record_utterance(blocks(), cfg, vad=ScriptVad(script), listener=tl)
    return audio, tl, n["blocks"]


def test_finished_sentence_ends_the_turn_sooner():
    _, tl, n_short = _record("vvvvvv", lambda a: "What's the capital of Australia?")
    _, _, n_base = _record("vvvvvv", lambda a: "open the inha app")
    _, _, n_long = _record("vvvvvv", lambda a: "what's the weather in Tashkent and")
    # 6 speech blocks, then silence: ~300 / 700 / 1200 ms of it at 80 ms per block
    assert n_short < n_base < n_long
    assert n_short <= 6 + 6 and n_long >= 6 + 15
    assert tl.final() == "What's the capital of Australia?" and tl.reused


def test_speech_after_the_pause_means_a_fresh_transcript():
    calls = []

    def tr(a):
        calls.append(len(a))
        return "what's the weather in Tashkent and"
    audio, tl, _ = _record("vvvv...vvvv", tr)
    assert len(calls) == 2                           # one per pause
    assert tl.final() == "what's the weather in Tashkent and"   # the last pause covers all speech
    assert calls[-1] <= len(audio)


def test_final_is_none_while_talking():
    tl = V.TurnListener(lambda a: "x", {})
    tl.on_pause([np.zeros(1280, np.int16)])
    tl.on_block([np.zeros(1280, np.int16)], True)    # spoke again
    assert tl.final(timeout=1) is None


def test_partials_go_to_the_island_while_talking():
    seen = []
    tl = V.TurnListener(lambda a: f"{len(a)} samples", {"partial_interval_s": 0.0}, on_partial=seen.append,
                        partials=True)
    blocks = []
    for _ in range(3):
        blocks.append(np.zeros(1280, np.int16))
        tl.on_block(blocks, True)
        time.sleep(0.05)
    time.sleep(0.1)
    assert seen and seen[-1].endswith("samples") and tl.partial_text == seen[-1]
    off = []
    tl2 = V.TurnListener(lambda a: "x", {"partial_interval_s": 0.0}, on_partial=off.append, partials=False)
    tl2.on_block(blocks, True)
    time.sleep(0.05)
    assert off == []


class FakeSilero:
    """Records what the streaming wrapper feeds the model."""

    def __init__(self, probs):
        self.probs, self.inputs = list(probs), []

    def run(self, _, feed):
        self.inputs.append((feed["input"].shape, float(feed["h"].sum())))
        p = self.probs.pop(0) if self.probs else 0.0
        return np.array([[p]], np.float32), feed["h"] + 1, feed["c"]


def test_silero_streams_32ms_frames_with_context_and_state():
    s = V.SileroVad(session=FakeSilero([0.9, 0.9, 0.4, 0.3, 0.1, 0.9, 0.6]))
    a = s.frames(np.zeros(1280, np.int16))           # 1280 = 2 frames + 256 left over
    b = s.frames(np.zeros(1280, np.int16))           # 256 + 1280 = 3 frames + 0
    c = s.frames(np.zeros(1280, np.int16))
    assert (len(a), len(b), len(c)) == (2, 3, 2)
    assert all(shape == (1, 576) for shape, _ in s.sess.inputs)
    assert [h for _, h in s.sess.inputs][:3] == [0.0, 128.0, 256.0]   # LSTM state carried
    # hysteresis: on at >= 0.5, stays on down to 0.35, off below
    assert a + b + c == [True, True, True, False, False, True, True]
    s.reset()
    assert s.sess.inputs and s._rest.size == 0


# -- echo cancellation and barge-in -------------------------------------------------------
def test_far_end_fifo():
    f = A.FarEnd(max_s=0.1)                          # 1600 samples
    f.push(np.arange(1, 101, dtype=np.int16))
    got = f.pull(160)
    assert list(got[:100]) == list(range(1, 101)) and not got[100:].any()
    for _ in range(3):
        f.push(np.ones(1000, np.int16))             # a stalled reader: oldest chunks are dropped
    assert f.pending <= 2000
    f.clear()
    assert f.pending == 0 and not f.pull(10).any()


class SubtractApm:
    """Fake AEC: the near frame minus the last far frame."""

    def __init__(self):
        self.ref, self.calls = None, []

    def process_reverse_stream(self, f):
        self.calls.append("far")
        self.ref = f

    def process_stream(self, f):
        self.calls.append("near")
        f[:] = f - self.ref


def test_echo_canceller_pairs_10ms_far_and_near_frames():
    far = A.FarEnd()
    apm = SubtractApm()
    ec = A.EchoCanceller(far, apm=apm, frame_factory=lambda b: np.frombuffer(b, np.int16).copy(),
                         frame_data=lambda f: f)
    echo = (np.sin(np.arange(1280) / 5) * 3000).astype(np.int16)
    far.push(echo)
    out = ec.process(echo.copy())
    assert apm.calls[:4] == ["far", "near", "far", "near"] and len(apm.calls) == 16
    assert not out.any() and far.pending == 0
    out2 = ec.process(echo.copy())                   # nothing playing: passes through
    assert (out2 == echo).all()


def test_barge_in_needs_sustained_speech_after_the_grace_period():
    t = [0.0]
    cfg = {"barge_in_ms": 200, "barge_in_grace_ms": 400, "barge_in_gap_ms": 100, "barge_in_min_rms": 300}
    b = A.BargeIn(cfg, clock=lambda: t[0])
    v = [True, True, True]                           # 3 x 32 ms
    assert not b.feed(v, 32, 2000)                   # not speaking yet
    b.speaking(True)
    assert not b.feed(v, 32, 2000) and not b.feed(v, 32, 2000)   # grace period
    t[0] = 0.5
    assert not b.feed(v, 32, 2000)                   # 96 ms
    assert not b.feed(v, 32, 2000)                   # 192 ms
    assert b.feed(v, 32, 2000)                       # 288 ms >= 200
    assert b.fired and not b.feed(v, 32, 2000)       # fires once per reply


def test_barge_in_ignores_quiet_or_choppy_audio_and_resets():
    t = [1.0]
    cfg = {"barge_in_ms": 200, "barge_in_grace_ms": 0, "barge_in_gap_ms": 100, "barge_in_min_rms": 300}
    b = A.BargeIn(cfg, clock=lambda: t[0])
    b.speaking(True)
    for _ in range(5):
        assert not b.feed([True, True, True], 32, 100)          # too quiet (residual echo)
    for _ in range(5):
        assert not b.feed([True, False, False, False, False], 32, 2000)   # gaps > 100 ms reset it
    b.speaking(False)
    assert b._since is None and not b.feed([True] * 10, 32, 2000)
    cfg["barge_in"] = False
    b.speaking(True)
    assert not b.feed([True] * 10, 32, 2000)


def _barge_assistant(pcm=True, speaking=True):
    from heyclaude.assistant import Assistant
    a = Assistant.__new__(Assistant)
    a.barge = A.BargeIn({"barge_in_ms": 64, "barge_in_grace_ms": 0, "barge_in_min_rms": 0})
    a.aec = object()

    class Gate:
        pass
    a.wake_gate = Gate()
    a.wake_gate.speaking = speaking

    class Sp:
        pass
    a.speaker = Sp()
    a.speaker.voice = Sp()
    a.speaker.voice.current = FakeVoice("cuda") if pcm else FakeSapi()

    class AlwaysSpeech:
        frame_ms = 32.0

        def frames(self, block):
            return [True, True]

        def reset(self):
            pass
    a.barge_vad = AlwaysSpeech()
    return a


def test_assistant_barge_in_only_while_a_pcm_voice_plays():
    blk = np.full(1280, 1000, np.int16)
    a = _barge_assistant()
    assert a._barge_check(blk) is True
    assert _barge_assistant(pcm=False)._barge_check(blk) is False      # SAPI: no echo reference
    assert _barge_assistant(speaking=False)._barge_check(blk) is False
    b = _barge_assistant()
    b.aec = None
    assert b._barge_check(blk) is False                                # no AEC, no barge-in


def test_record_utterance_counts_barge_in_preroll_as_speech():
    from heyclaude.listen import BLOCK, record_utterance
    pre = [np.full(BLOCK, 7, np.int16)] * 3
    cfg = {"end_silence_ms": 160, "start_timeout": 5, "max_seconds": 20}
    audio, t = record_utterance(iter([np.zeros(BLOCK, np.int16)] * 10), cfg, vad=ScriptVad(""), preroll=pre)
    assert audio is not None and (audio[:3 * BLOCK] == 7).all() and t is not None


# -- the API key never reaches a claude process ------------------------------------------
def test_child_env_strips_api_keys_and_parent_session():
    from heyclaude.claude_cli import child_env
    env = child_env({"ANTHROPIC_API_KEY": "sk-ant-x", "ANTHROPIC_AUTH_TOKEN": "t", "CLAUDECODE": "1",
                     "PATH": "C:\\bin", "ANTHROPIC_MODEL": "haiku"})
    assert "ANTHROPIC_API_KEY" not in env and "ANTHROPIC_AUTH_TOKEN" not in env and "CLAUDECODE" not in env
    assert env["PATH"] == "C:\\bin" and env["ANTHROPIC_MODEL"] == "haiku"


def test_every_claude_process_gets_the_stripped_env(tmp_path):
    import subprocess
    from heyclaude import brain as B
    from heyclaude import tasks as TK
    os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test-only"
    seen = []

    class FakeProc:
        def __init__(self, cmd, **kw):
            seen.append((cmd, kw.get("env")))
            self.stdout = iter([])
            self.stdin = None
            self.pid = 1

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0
    real = subprocess.Popen
    try:
        B.subprocess.Popen = FakeProc
        TK.subprocess.Popen = FakeProc
        # quick-answer / agent / task-question processes (brain._CliChat)
        B._CliChat("claude.exe", [], cwd=str(tmp_path), persistent=False)._spawn("hi")
        # headless tasks
        tm = TK.TaskManager.__new__(TK.TaskManager)
        tm.exe = "claude.exe"
        t = TK.Task.__new__(TK.Task)
        t.session_id, t.path = "abc", str(tmp_path)
        tm.open_terminal(t, popen=FakeProc)
    finally:
        B.subprocess.Popen = real
        TK.subprocess.Popen = real
        del os.environ["ANTHROPIC_API_KEY"]
    assert len(seen) == 2
    for cmd, env in seen:
        assert env is not None and "ANTHROPIC_API_KEY" not in env, cmd


def test_task_spawn_uses_the_stripped_env():
    import inspect
    from heyclaude import tasks as TK
    src = inspect.getsource(TK.TaskManager.spawn)
    assert "env=child_env()" in src


def test_api_key_from_env_or_file(tmp_path):
    from heyclaude import brain as B
    old = os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        f = tmp_path / "key.txt"
        assert B.api_key({"api_key_file": str(f)}) is None
        f.write_text("sk-ant-from-file\n", encoding="utf-8")
        assert B.api_key({"api_key_file": str(f)}) == "sk-ant-from-file"
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-env"
        assert B.api_key({"api_key_file": str(f)}) == "sk-ant-env"
    finally:
        os.environ.pop("ANTHROPIC_API_KEY", None)
        if old is not None:
            os.environ["ANTHROPIC_API_KEY"] = old


def test_auto_backend_uses_the_api_when_there_is_a_key(tmp_path):
    from heyclaude import brain as B
    from heyclaude import config as C
    cfg = C.load(os.devnull + ".missing")
    f = tmp_path / "key.txt"
    f.write_text("sk-ant-not-real\n", encoding="utf-8")
    cfg["fast"]["api_key_file"] = str(f)
    cfg["agent"]["enabled"] = False
    started = []
    real = B.Brain._warm_up
    B.Brain._warm_up = lambda self: started.append(1)
    try:
        b = B.Brain(cfg)
    finally:
        B.Brain._warm_up = real
    assert b.backend == "sdk" and isinstance(b.chat, B._SdkChat)
    assert b.chat.client.api_key == "sk-ant-not-real"


if __name__ == "__main__":
    import pathlib
    import tempfile
    n = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            if "tmp_path" in fn.__code__.co_varnames[: fn.__code__.co_argcount]:
                fn(pathlib.Path(tempfile.mkdtemp()))
            else:
                fn()
            n += 1
            print("ok", name)
    print(n, "passed")
