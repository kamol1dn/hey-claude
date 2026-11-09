"""Offline tests: routing, TASK-line detection in a streamed reply, sentence
splitting, and task stream parsing. No mic, models or network needed.

    .venv\\Scripts\\python -m pytest tests  (or just run this file)
"""
import json
import os
import subprocess
import sys
import textwrap
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from heyclaude import brain as B  # noqa: E402
from heyclaude import tasks as T  # noqa: E402

P = B.Projects({"hey claude": "D:/coding/hey-claude", "inha app": "D:/coding/inha_app"})


class FakeChat:
    def __init__(self, chunks):
        self.chunks = chunks

    def stream(self, text):
        yield from self.chunks

    def close(self):
        pass


def brain_with(chunks):
    b = B.Brain.__new__(B.Brain)
    b.projects, b.backend, b.chat, b.timing = P, "fake", FakeChat(chunks), {}
    b.agent = object()
    return b


def test_local_routes():
    assert B.local_route("Start a task in hey claude, add a readme.", P) == ("task", "hey claude", "add a readme.")
    assert B.local_route("start a new task in the inha app project. Fix login", P) == ("task", "inha app", "Fix login")
    assert B.local_route("launch a task in in ha app bump the version", P)[1] == "inha app"
    assert B.local_route("what's the weather", P)[0] == "lookup"
    assert B.local_route("how are my tasks going", P) == ("status",)
    assert B.local_route("Who messaged me on Telegram today?", P)[0] == "lookup"
    assert B.local_route("What's on my calendar tomorrow?", P)[0] == "lookup"
    assert B.local_route("How many legs does a spider have?", P) is None


def test_stream_lookup_line():
    assert list(brain_with(["LOO", "KUP"]).handle("did Sam reply?")) == [("lookup", "did Sam reply?")]


def test_word_starting_with_l_is_not_lookup():
    out = list(brain_with(["Lo", "ndon is big."]).handle("x"))
    assert "".join(e[1] for e in out) == "London is big."


def test_stream_answer_is_spoken_incrementally():
    out = list(brain_with(["Pa", "ris is ", "the capital. ", "It is big."]).handle("capital of france?"))
    assert out[0] == ("say", "Pa")          # first chunk goes out immediately
    assert "".join(e[1] for e in out) == "Paris is the capital. It is big."


def test_stream_task_line_detected_across_chunks():
    out = list(brain_with(["TA", "SK inha ", "app :: fix the ", "login screen\n"]).handle("x"))
    assert out == [("task", "inha app", "fix the login screen")]


def test_stream_unknown_project():
    assert list(brain_with(["TASK ? :: write a blog post"]).handle("x")) == [("task", None, "write a blog post")]


def test_word_starting_with_t_is_not_a_task():
    out = list(brain_with(["Ta", "ke the bus."]).handle("x"))
    assert "".join(e[1] for e in out) == "Take the bus."


def test_sentence_splitting():
    from heyclaude.tts import Speaker
    s = Speaker.__new__(Speaker)
    import queue
    s.q, s._buf, s._gen = queue.Queue(), "", 0
    for c in ["It is 3.", "5 km away. Then", " turn left! Done"]:
        s.feed(c)
    s.flush()
    got = []
    while not s.q.empty():
        got.append(s.q.get()[1])
    assert got == ["It is 3.5 km away.", "Then turn left!", "Done"]


def test_summary_extraction():
    assert T.extract_summary("Did stuff.\n\nSUMMARY: Counted 9 Python files.") == "Counted 9 Python files."
    assert T.extract_summary("**SUMMARY:** Added a readme.") == "Added a readme."
    assert T.extract_summary("Found 3 bugs. Details follow.") == "Found 3 bugs."


FAKE = textwrap.dedent("""
    import json, sys, time
    def out(o): print(json.dumps(o), flush=True)
    out({"type": "system", "subtype": "init", "session_id": "abc-123"})
    out({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Glob", "input": {"pattern": "**/*.py"}}]}})
    time.sleep(0.2)
    out({"type": "user", "message": {"content": [{"type": "tool_result", "content": "a.py\\nb.py"}]}})
    out({"type": "assistant", "message": {"content": [{"type": "text", "text": "There are 2 files.\\n\\nSUMMARY: There are 2 Python files."}]}})
    out({"type": "result", "subtype": "success", "is_error": False, "result": "There are 2 files.\\n\\nSUMMARY: There are 2 Python files.", "total_cost_usd": 0.01})
""")


def test_task_pump(tmp_path):
    # keep the fake task out of the real state/tasks.json and logs
    T.STATE_FILE, T.LOG_DIR = tmp_path / "tasks.json", tmp_path
    fake = tmp_path / "fake_claude.py"
    fake.write_text(FAKE)
    tm = T.TaskManager.__new__(T.TaskManager)
    tm.cfg = {"max_parallel": 3, "permission_mode": "acceptEdits", "allowed_tools": []}
    tm.exe, tm.tasks, tm.procs = sys.executable, {}, {}
    import threading
    tm.lock = threading.Lock()
    finished = []
    tm.on_change, tm.on_finish = (lambda t: None), finished.append
    tm.command = lambda prompt, name=None: [sys.executable, str(fake)]
    t = tm.spawn("hey claude", str(tmp_path), "count py files")
    for _ in range(100):
        if finished:
            break
        time.sleep(0.05)
    assert finished and t.status == "done"
    assert t.session_id == "abc-123"
    assert t.summary == "There are 2 Python files."
    log = open(t.log, encoding="utf-8").read()
    assert "Glob: **/*.py" in log and "done after" in log


def test_task_command_flags():
    tm = T.TaskManager.__new__(T.TaskManager)
    tm.exe = "claude"
    tm.cfg = {"permission_mode": "acceptEdits", "allowed_tools": ["Read", "Edit"], "model": None, "extra_args": []}
    c = tm.command("do x")
    assert c[:3] == ["claude", "-p", "do x"]
    assert c[c.index("--permission-mode") + 1] == "acceptEdits"
    assert c[c.index("--allowedTools") + 1] == "Read,Edit"
    assert "--permission-prompts" in c


# -- M1: config round-trip, hotkey parsing, listening state ----

def test_config_round_trip(tmp_path):
    from heyclaude import config as C
    cfg = C.load(tmp_path / "missing.yaml")          # no user file: pure defaults
    assert cfg["app"]["hotkey"] == "ctrl+alt+space" and cfg["app"]["listening"] is True
    new = json.loads(json.dumps(cfg))
    new["wake"]["threshold"] = 0.6
    new["projects"]["My  New App"] = "C:/x"
    new["tasks"]["allowed_tools"] = ["Read"]
    new["tts"]["voice"] = None
    path = C.save(new, tmp_path / "config.yaml")
    back = C.load(path)
    assert back["wake"]["threshold"] == 0.6
    assert back["projects"]["my new app"] == "C:/x"
    assert back["tasks"]["allowed_tools"] == ["Read"]
    assert back["tts"]["voice"] is None
    assert back["listen"] == cfg["listen"] and back["claude"] == cfg["claude"]
    changed = C.diff(cfg, back)
    assert set(changed) == {"wake.threshold", "projects", "tasks.allowed_tools"}
    assert C.needs_restart(changed) == []
    assert C.needs_restart(["stt.gpu_model", "app.hotkey", "agent.model", "agent.filler"]) == ["stt.gpu_model", "agent.model"]


def test_config_apply_in_place_keeps_sections(tmp_path):
    from heyclaude import config as C
    cfg = C.load(tmp_path / "missing.yaml")
    tasks_ref, listen_ref = cfg["tasks"], cfg["listen"]
    new = json.loads(json.dumps(cfg))
    new["tasks"]["permission_mode"] = "plan"
    new["listen"]["end_silence_ms"] = 500
    changed = C.apply_in_place(cfg, new)
    assert cfg["tasks"] is tasks_ref and tasks_ref["permission_mode"] == "plan"
    assert listen_ref["end_silence_ms"] == 500
    assert set(changed) == {"tasks.permission_mode", "listen.end_silence_ms"}


def test_ensure_user_config_copies_example(tmp_path):
    from heyclaude import config as C
    p = C.ensure_user_config(tmp_path / "config.yaml")
    assert p.read_text(encoding="utf-8") == C.EXAMPLE.read_text(encoding="utf-8")


def test_hotkey_parsing():
    from heyclaude import hotkey as H
    assert H.parse("ctrl+alt+space") == (H.MOD_CONTROL | H.MOD_ALT, 0x20)
    assert H.parse("Win + Shift + H") == (H.MOD_WIN | H.MOD_SHIFT, ord("H"))
    assert H.parse("f9") == (0, 0x78)
    assert H.parse("ctrl+num5") == (H.MOD_CONTROL, 0x65)
    assert H.normalize("Alt + CTRL+Space") == "ctrl+alt+space"
    assert H.normalize("control+escape") == "ctrl+esc"
    for bad in ("", "ctrl+alt", "ctrl+banana", "a+b", "ctrl++"):
        try:
            H.parse(bad)
        except ValueError:
            continue
        raise AssertionError(f"{bad!r} should not parse")


class _Sig:
    def __init__(self):
        self.calls = []

    def emit(self, *a):
        self.calls.append(a)


class _Bridge:
    def __init__(self):
        self.state, self.level, self.tasks, self.quit = _Sig(), _Sig(), _Sig(), _Sig()


def _assistant(cfg_over=None, **kw):
    from heyclaude import config as C
    from heyclaude.assistant import Assistant
    cfg = C.load(os.devnull + ".missing")
    for k, v in (cfg_over or {}).items():
        cfg[k].update(v)
    return Assistant(cfg, _Bridge(), **kw)


def test_listening_toggle_and_pause():
    a = _assistant({"app": {"listening": False}})
    assert not a.listening.is_set()
    a.ready.set()
    a.mode = "idle"
    seen = []
    a.on_listening = lambda on, until: seen.append((on, until))
    a.set_listening(False, pause_seconds=3600)
    assert a.paused_until and a._pause_timer is not None
    assert a.ui.state.calls[-1][0] == "off" and "paused until" in a.ui.state.calls[-1][1]
    a.set_listening(True)                       # cancels the pause timer
    assert a.listening.is_set() and a.paused_until is None and a._pause_timer is None
    assert a.ui.state.calls[-1] == ("idle", "")
    assert seen[0][0] is False and seen[1] == (True, None)
    a.set_listening(False)
    assert a.ui.state.calls[-1] == ("off", "listening off") and a._abort_rec.is_set()


# -- speech-to-text device choice (NVML/psutil/CUDA mocked: no GPU touched) --

class _FakeModel:
    def __init__(self, device, fail=None):
        self.device, self.fail = device, fail

    def transcribe(self, audio):
        if self.fail:
            raise self.fail
        return f"text from {self.device}"


class _FakeLoad:
    def __init__(self, gpu=5.0, free=5000.0, cpu=20.0):
        self.gpu, self.free, self.cpu = gpu, free, cpu

    def gpu_util(self):
        return self.gpu

    def gpu_free_mb(self):
        return self.free

    def cpu_pct(self):
        return self.cpu


def _stt(cfg=None, device=None, cuda=(True, ""), premiere=False, gpu_load_error=None, gpu_fail=None,
         load=None, cached=True, gate=None):
    from heyclaude.stt import SpeechToText
    c = {"device": "gpu", "gpu_model": "large-v3-turbo", "gpu_compute_type": "float16",
         "cpu_model": "base.en", "gpu_busy_pct": 70,
         "gpu_resume_pct": 60, "cpu_busy_pct": 85, "yield_to_premiere": False}
    c.update(cfg or {})
    state = {"premiere": premiere, "loads": [], "cuda_checks": 0, "load": load or _FakeLoad()}

    def loader(dev):
        if dev == "cuda" and gate is not None:
            gate.wait(5)                            # a slow download
        state["loads"].append(dev)
        if dev == "cuda" and gpu_load_error:
            raise gpu_load_error
        return _FakeModel(dev, gpu_fail if dev == "cuda" else None)

    def cuda_check():
        state["cuda_checks"] += 1
        return cuda

    s = SpeechToText(c, device=device, loader=loader, cuda_check=cuda_check, monitor=state["load"],
                     premiere_running=lambda: state["premiere"], cached=lambda m: cached)
    return s, state


def test_stt_start_never_waits_for_a_download():
    import threading
    import numpy as np
    gate = threading.Event()
    s, st = _stt(cached=False, gate=gate)           # turbo not downloaded: start-up must not block
    assert s.label == "cpu (downloading turbo)" and st["loads"] == ["cpu"]
    assert s.transcribe(np.zeros(1600, np.int16)) == "text from cpu"
    gate.set()                                      # download finishes
    for _ in range(100):
        if s.label == "gpu (turbo)":
            break
        threading.Event().wait(0.02)
    assert s.label == "gpu (turbo)" and s.transcribe(np.zeros(1600, np.int16)) == "text from cuda"


def test_choose_rule():
    from heyclaude.stt import choose
    assert choose(True, 5, 5000, 0, 20) == ("gpu", "")
    assert choose(True, 83, 5000, 0, 20) == ("cpu", "gpu busy 83%")
    assert choose(True, 83, 5000, 0, 90) == ("gpu", "cpu busy")          # both busy: stay on GPU
    assert choose(True, 5, 900, 2250, 20)[0] == "cpu"                    # not enough VRAM to load
    assert choose(True, 5, 900, 0, 20) == ("gpu", "")                    # already loaded: VRAM is ours
    assert choose(True, 65, 5000, 0, 20) == ("gpu", "")                  # 65% < 70: stays on GPU
    assert choose(False, 65, 5000, 0, 20)[0] == "cpu"                    # on CPU: needs < 60 to go back
    assert choose(False, 55, 5000, 0, 20) == ("gpu", "")
    assert choose(True, None, None, 2250, None) == ("gpu", "")           # no readings: default GPU


def test_stt_gpu_loaded_once_and_kept():
    import numpy as np
    s, st = _stt()
    assert s.label == "gpu (turbo)" and s.device == "cuda" and st["loads"] == ["cuda"]
    for _ in range(3):
        assert s.transcribe(np.zeros(1600, np.int16)) == "text from cuda"
    assert st["loads"] == ["cuda"]                  # no per-request load, CPU model never loaded


def test_stt_switches_with_load_and_hysteresis():
    import numpy as np
    z = np.zeros(1600, np.int16)
    s, st = _stt()
    load = st["load"]
    load.gpu = 83                                   # e.g. a Remotion render on the GPU
    assert s.transcribe(z) == "text from cpu" and s.label == "cpu (gpu busy 83%)"
    load.gpu = 65                                   # between resume (60) and busy (70): stay on CPU
    assert s.transcribe(z) == "text from cpu"
    load.gpu = 50
    assert s.transcribe(z) == "text from cuda" and s.label == "gpu (turbo)"
    load.gpu = 65                                   # on GPU now: 65 < 70 keeps it there
    assert s.transcribe(z) == "text from cuda"
    load.gpu, load.cpu = 90, 95                     # both busy: GPU
    assert s.transcribe(z) == "text from cuda" and s.label == "gpu (cpu busy)"
    assert st["loads"] == ["cuda", "cpu"]           # both stay loaded: switching never reloads


def test_stt_busy_gpu_at_start_loads_it_later():
    import numpy as np
    s, st = _stt(load=_FakeLoad(gpu=90))
    assert s.label == "cpu (gpu busy 90%)" and st["loads"] == ["cpu"]
    st["load"].gpu = 10
    s.poll(background=False)                        # GPU free again: loads it, CPU serves meanwhile
    assert s.label == "gpu (turbo)" and st["loads"] == ["cpu", "cuda"]
    assert s.transcribe(np.zeros(1600, np.int16)) == "text from cuda"


def test_stt_low_vram_uses_cpu():
    s, st = _stt(load=_FakeLoad(free=800))
    assert s.label.startswith("cpu (low vram") and st["loads"] == ["cpu"]


def test_stt_falls_back_without_cuda():
    s, st = _stt(cuda=(False, "no CUDA device"))
    assert s.label == "cpu (fallback: no cuda)" and st["loads"] == ["cpu"]


def test_stt_falls_back_when_gpu_load_fails():
    s, st = _stt(gpu_load_error=RuntimeError("Library cublas64_12.dll is not found"))
    assert s.label == "cpu (fallback: gpu load failed)" and st["loads"] == ["cuda", "cpu"]
    s, st = _stt(gpu_load_error=RuntimeError("CUDA failed with error out of memory"))
    assert s.label == "cpu (gpu out of memory)" and not s.gpu_failed   # retried later, not given up


def test_stt_cuda_error_retries_the_request_on_cpu():
    import numpy as np
    s, st = _stt(gpu_fail=RuntimeError("CUDA error: an illegal memory access"))
    assert s.transcribe(np.zeros(1600, np.int16)) == "text from cpu"
    assert s.label == "cpu (fallback: cuda error)" and s.gpu is None
    assert s.transcribe(np.zeros(1600, np.int16)) == "text from cpu"
    assert st["loads"] == ["cuda", "cpu"]


def test_stt_other_errors_are_not_swallowed():
    import numpy as np
    s, _ = _stt(gpu_fail=ValueError("bad audio"))
    try:
        s.transcribe(np.zeros(1600, np.int16))
    except ValueError:
        return
    raise AssertionError("non-CUDA error should propagate")


def test_stt_idle_premiere_is_fine_unless_opted_in():
    s, st = _stt(premiere=True)                     # default: Premiere open but idle -> GPU
    assert s.label == "gpu (turbo)"
    s, st = _stt(cfg={"yield_to_premiere": True}, premiere=True)
    assert s.label == "cpu (fallback: premiere)"
    assert st["loads"] == ["cpu"] and st["cuda_checks"] == 0   # opt-in: not even a CUDA probe


def test_stt_cpu_device_never_touches_cuda():
    s, st = _stt(device="cpu")
    assert s.label == "cpu (base.en)" and st["loads"] == ["cpu"] and st["cuda_checks"] == 0


def test_config_migrates_v1_stt_keys(tmp_path):
    from heyclaude import config as C
    p = tmp_path / "old.yaml"
    p.write_text("stt:\n  model: small.en\n  device: cuda\n  compute_type: int8\n"
                 "app:\n  premiere_guard: false\n")
    cfg = C.load(p)
    assert cfg["stt"]["cpu_model"] == "small.en" and cfg["stt"]["device"] == "gpu"
    assert "model" not in cfg["stt"] and "premiere_guard" not in cfg["app"]


def test_assistant_thread_restarts_once_after_a_crash():
    import heyclaude.assistant as A
    sleep, A.time.sleep = A.time.sleep, lambda s: None
    try:
        a = _assistant()
        a.ready.set()
        calls = []

        def crash_once():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("boom")
        a._run = crash_once
        a.run()
        assert len(calls) == 2 and a.ui.state.calls[-1][0] == "loading"

        b = _assistant()
        b.ready.set()
        b._run = lambda: (_ for _ in ()).throw(RuntimeError("boom again"))
        b.run()                                     # second crash: gives up, shows the error
        assert b.ui.state.calls[-1][0] == "error" and "boom again" in b.ui.state.calls[-1][1]
    finally:
        A.time.sleep = sleep


# -- hold key: a key-down/up timeline plus VAD frames -------------------------

CTRL, C_KEY = 0xA2, 0x43
TAIL = 9          # end_silence_ms 700 = 35 VAD frames = 9 blocks of 80 ms


def _timeline(n, speech, keys=None, hold=True, **cfg_over):
    """Feed n blocks; speech(i) says if block i is voiced; keys = {i: [(vk, down)]}.
    Returns (audio, blocks consumed, hold state)."""
    import numpy as np
    from heyclaude.holdkey import HoldState
    from heyclaude.listen import BLOCK, record_utterance
    now = {"t": 0.0}
    clock = lambda: now["t"]  # noqa: E731
    hs = HoldState(frozenset({0xA2, 0xA3}), clock=clock)
    hs.reset()
    used = {"n": 0}

    def gen():
        for i in range(n):
            now["t"] = i * 0.08
            for vk, down in (keys or {}).get(i, []):
                hs.key(vk, down)
            used["n"] = i + 1
            yield np.full(BLOCK, 1 if speech(i) else 0, np.int16)
    cfg = {"vad_aggressiveness": 2, "end_silence_ms": 700, "start_timeout": 5.0, "max_seconds": 20,
           "max_utterance_s": 180}
    cfg.update(cfg_over)
    audio, _ = record_utterance(gen(), cfg, hold=hs if hold else None,
                                is_speech=lambda f: f[0] != 0, clock=clock)
    return audio, used["n"], hs


def test_no_hold_ends_on_silence():
    audio, n, _ = _timeline(400, lambda i: i < 25)
    assert n == 25 + TAIL                           # ~0.7 s after the last speech


def test_hold_keeps_listening_through_a_long_pause():
    # talk 2 s, Ctrl down at 1.6 s, 30 s think-pause, talk again, release mid-sentence
    speech = lambda i: i < 25 or 400 <= i < 425    # noqa: E731
    audio, n, hs = _timeline(1000, speech, {20: [(CTRL, True)], 410: [(CTRL, False)]})
    assert n == 425 + TAIL                          # ends ~0.7 s after the second sentence
    assert hs.used and not hs.held
    from heyclaude.listen import BLOCK
    assert len(audio) // BLOCK < 100                # the 30 s pause was trimmed out


def test_release_during_pause_starts_the_silence_window_then():
    _, n, _ = _timeline(400, lambda i: i < 25, {10: [(CTRL, True)], 100: [(CTRL, False)]})
    assert n == 100 + TAIL                          # counted from the release, not from block 25


def test_hold_pressed_before_speech_starts():
    speech = lambda i: 100 <= i < 125              # noqa: E731  (8 s of thinking first)
    assert _timeline(400, speech, hold=False)[0] is None   # without a hold: start timeout
    audio, n, _ = _timeline(400, speech, {0: [(CTRL, True)], 110: [(CTRL, False)]})
    assert audio is not None and n == 125 + TAIL


def test_ctrl_shortcut_during_hold_is_not_a_hold():
    keys = {20: [(CTRL, True)], 22: [(C_KEY, True)], 23: [(C_KEY, False)], 300: [(CTRL, False)]}
    _, n, hs = _timeline(400, lambda i: i < 25, keys)
    assert n == 25 + TAIL                           # Ctrl+C: normal end-pointing despite Ctrl still down
    assert not hs.held and not hs.used              # no long cap, no trimming


def test_stuck_hold_key_hits_the_safety_cap():
    _, n, _ = _timeline(2000, lambda i: i < 25, {5: [(CTRL, True)]}, max_utterance_s=10)
    assert n == 125                                 # 10 s of audio (2-block pre-roll + 123 more)


def test_hold_key_ignores_autorepeat_and_parses_names():
    from heyclaude import holdkey as K
    hs = K.HoldState(K.parse("ctrl"))
    hs.reset()
    assert hs.key(0xA3, True) and hs.held           # right ctrl counts for "ctrl"
    assert not hs.key(0xA3, True)                   # auto-repeat: no change
    assert hs.key(0xA3, False) and not hs.held
    hs.reset(key_down_now=True)                     # already down when listening starts
    assert not hs.held and not hs.key(0xA2, True)   # (e.g. Ctrl of the hotkey) isn't a hold
    assert K.parse("right ctrl") == {0xA3} and K.parse("Caps Lock") == {0x14} and K.parse("f13") == {0x7C}
    for bad in ("", "hyper", "ctrl+x"):
        try:
            K.parse(bad)
        except ValueError:
            continue
        raise AssertionError(bad)


def test_trim_silences_keeps_short_gaps():
    from heyclaude.listen import trim_silences
    blocks = list(range(20))
    voiced = [True] * 3 + [False] * 10 + [True] * 2 + [False] * 3 + [True] * 2
    assert trim_silences(blocks, voiced, keep=4) == [0, 1, 2, 3, 4, 5, 6, 13, 14, 15, 16, 17, 18, 19]


def test_record_utterance_ends_when_source_closes():
    import numpy as np
    from heyclaude.listen import record_utterance
    blocks = iter([np.zeros(1280, np.int16)] * 3)   # silence, then the source ends
    cfg = {"vad_aggressiveness": 2, "end_silence_ms": 700, "start_timeout": 5, "max_seconds": 20}
    assert record_utterance(blocks, cfg) == (None, None)


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
