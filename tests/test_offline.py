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
    tm.command = lambda prompt: [sys.executable, str(fake)]
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


# -- M1: config round-trip, hotkey parsing, listening state, Premiere guard ----

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
    assert C.needs_restart(["stt.model", "app.hotkey", "agent.model", "agent.filler"]) == ["stt.model", "agent.model"]


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


def test_premiere_guard_switches_whisper():
    a = _assistant({"stt": {"device": "cuda"}})
    made = []

    class FakeStt:
        def __init__(self, gpu):
            self.device = "cuda" if gpu else "cpu"

    a._make_stt = lambda gpu: made.append(gpu) or FakeStt(gpu)
    running = {"v": True}
    a._premiere_running = lambda: running["v"]
    a.stt = FakeStt(True)
    assert a.guard_check() == "cpu" and a.stt.device == "cpu"
    assert a.guard_check() is None                  # still running: stay on CPU
    running["v"] = False
    assert a.guard_check() == "gpu" and a.stt.device == "cuda"
    a.cfg["app"]["premiere_guard"] = False
    running["v"] = True
    assert a.guard_check() is None and a.stt.device == "cuda"
    assert made == [False, True]
    cpu = _assistant()                              # default cpu config: guard never acts
    cpu.stt, cpu._premiere_running = FakeStt(False), (lambda: True)
    assert cpu.guard_check() is None


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
