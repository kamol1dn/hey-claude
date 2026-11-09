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
