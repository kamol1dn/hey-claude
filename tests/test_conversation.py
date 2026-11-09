"""Offline tests for M3 (conversation, follow-ups, memory): the shared
transcript and what each tier is told, the follow-up window and the speaking
gate, task lookup by name / project / recency, and voice task control.
No mic, models or network needed.

    .venv\\Scripts\\python tests\\test_conversation.py   (or python -m pytest tests)
"""
import os
import sys
import threading
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from heyclaude import brain as B  # noqa: E402
from heyclaude import conversation as C  # noqa: E402
from heyclaude import tasks as T  # noqa: E402
from heyclaude.listen import SpeakingGate  # noqa: E402

P = B.Projects({"hey claude": "D:/coding/hey-claude", "inha app": "D:/coding/inha_app", "sandbox": "D:/tmp/sb"})


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def _tr(tmp_path=None, **cfg):
    return C.Transcript(cfg, clock=Clock(), save_dir=tmp_path or Path(os.devnull + ".nope"))


# -- transcript: what each tier is told ------------------------------------------

def test_first_request_has_no_context():
    tr = _tr()
    assert tr.prompt_for("fast", 1, "what's on my calendar?") == "what's on my calendar?"


def test_other_tiers_turns_are_passed_but_own_turns_are_not():
    tr = _tr()
    tr.prompt_for("fast", 1, "what's on my calendar tomorrow?")           # fast said LOOKUP...
    tr.prompt_for("agent", 1, "what's on my calendar tomorrow?")          # ...the agent answered
    tr.add("what's on my calendar tomorrow?", "A dentist appointment at 3 pm in Seoul.", "agent")
    out = tr.prompt_for("fast", 1, "and the weather then?")
    assert "dentist appointment at 3 pm" in out and out.endswith("and the weather then?")
    tr.add("and the weather then?", "Rain is likely around 3 pm.", "fast")
    # The agent remembers its own answer; it gets only the quick model's turn.
    out = tr.prompt_for("agent", 1, "should I take an umbrella there?")
    assert "Rain is likely" in out and "dentist" not in out
    tr.add("should I take an umbrella there?", "Yes.", "agent")
    # Asked again right away: nothing new from other tiers.
    assert tr.prompt_for("agent", 1, "thanks") == "thanks"


def test_restarted_process_gets_the_whole_window():
    tr = _tr()
    tr.prompt_for("fast", 1, "capital of Australia?")
    tr.add("capital of Australia?", "Canberra.", "fast")
    assert tr.prompt_for("fast", 1, "and its population?") == "and its population?"   # remembers
    tr.add("and its population?", "About 470,000.", "fast")
    out = tr.prompt_for("fast", 2, "how far is it from Sydney?")                    # new process
    assert "Canberra." in out and "470,000" in out


def test_reset_tells_a_warm_process_to_forget():
    tr = _tr()
    tr.prompt_for("fast", 1, "a")
    tr.add("a", "b", "fast")
    tr.reset()
    out = tr.prompt_for("fast", 1, "hello")
    assert out.startswith("(New conversation") and out.endswith("hello")
    assert tr.prompt_for("agent", 1, "x") == "x"         # never spoke before: nothing to forget


def test_window_capped_by_turns_and_tokens():
    tr = _tr(max_turns=3)
    for i in range(6):
        tr.add(f"question {i}", f"answer {i}", "fast")
    assert [t.user for t in tr.window()] == ["question 3", "question 4", "question 5"]
    tr = _tr(max_turns=50, max_tokens=60)                 # ~240 characters
    for i in range(10):
        tr.add(f"question {i} " + "x" * 40, f"answer {i} " + "y" * 40, "fast")
    w = tr.window()
    assert 1 <= len(w) < 10 and w[-1].user.startswith("question 9")


def test_idle_reset():
    tr = _tr(idle_reset_s=600)
    tr.add("a", "b", "fast")
    tr.clock.t += 599
    assert not tr.expire_if_idle() and tr.turns
    tr.clock.t += 2
    assert tr.expire_if_idle() and not tr.turns


def test_task_context_and_last_task():
    tr = _tr()
    tr.add("what's the weather in Canberra?", "Sunny, 24 degrees.", "agent")
    tr.add("start a task in sandbox: write a packing list for there", "Okay, starting that in sandbox.", "task",
           task_id="t1")
    ctx = tr.task_context()
    assert "Canberra" in ctx and "Sunny, 24 degrees." in ctx
    assert tr.last_task_id() == "t1"
    tr.add("x", "y", "fast")
    tr.add("x", "y", "fast")
    assert tr.last_task_id() is None                      # too long ago to be "it"


def test_save_and_restore(tmp_path):
    tr = _tr(tmp_path, save=True)
    tr.add("capital of Peru?", "Lima.", "fast")
    files = list(tmp_path.glob("*.jsonl"))
    assert len(files) == 1 and "Lima." in files[0].read_text(encoding="utf-8")
    again = C.Transcript({"save": True}, clock=Clock(tr.clock.t + 60), save_dir=tmp_path)
    assert [t.user for t in again.turns] == ["capital of Peru?"]
    stale = C.Transcript({"save": True}, clock=Clock(tr.clock.t + 3600), save_dir=tmp_path)
    assert stale.turns == []                              # older than idle_reset_s: start over
    off = _tr(tmp_path / "off", save=False)
    off.add("a", "b", "fast")
    assert not (tmp_path / "off").exists()                # nothing written when save is off


def test_agent_session_named_per_day_only_when_saving():
    a = B.Agent.__new__(B.Agent)
    a.cfg = {"agent": {"model": "haiku", "permission_mode": "default"}, "conversation": {"save": False}}
    args = a._args()
    assert "--no-session-persistence" in args and args[args.index("--name") + 1] == "hey-claude assistant"
    a.cfg["conversation"]["save"] = True
    args = a._args()
    assert "--no-session-persistence" not in args
    assert args[args.index("--name") + 1] == "hey-claude " + time.strftime("%Y-%m-%d")


# -- brain: context reaches every tier ------------------------------------------------

class RecChat:
    """A warm chat that records what it was sent and replies from a script."""

    def __init__(self, replies):
        self.replies, self.sent, self.epoch = list(replies), [], 1

    def stream(self, text):
        self.sent.append(text)
        yield from self.replies.pop(0)

    def recycle(self, warm=False):
        self.epoch += 1

    def close(self):
        pass


class RecAgent:
    def __init__(self, replies):
        self.chat = RecChat(replies)

    def ask(self, text):
        yield from self.chat.stream(text)


def _brain(fast, agent, tmp_path=None):
    b = B.Brain.__new__(B.Brain)
    b.projects, b.backend, b.timing = P, "fake", {}
    b.chat, b.agent = RecChat(fast), RecAgent(agent)
    b.convo = _tr(tmp_path)
    b.cfg = {"conversation": {}, "claude": {}}
    b.exe = "claude"
    return b


def test_brain_passes_context_between_quick_model_and_agent():
    b = _brain(fast=[["LOOKUP"], ["Yes, take one."]], agent=[["You have a dentist appointment at 3 pm."]])
    assert list(b.handle("when am I free tomorrow?")) == [("lookup", "when am I free tomorrow?")]
    ans = "".join(b.lookup("when am I free tomorrow?"))
    b.convo.add("when am I free tomorrow?", ans, "agent")
    list(b.handle("should I bring an umbrella then?"))
    assert "dentist appointment at 3 pm" in b.chat.sent[-1]
    assert b.chat.sent[-1].endswith("should I bring an umbrella then?")


def test_it_question_after_a_task_goes_to_that_task():
    b = _brain(fast=[], agent=[])
    b.convo.add("", "Your sandbox task is done. It wrote packing.txt.", "local", task_id="0101-x")
    assert list(b.handle("What did it change?")) == [("task_ask", "#0101-x", "What did it change?")]
    b.convo.add("a", "b", "fast")
    b.convo.add("a", "b", "fast")
    b.chat.replies.append(["It changed nothing."])
    assert list(b.handle("What did it change?"))[0][0] == "say"     # no task in sight: the quick model


def test_task_question_resumes_the_session_read_only():
    b = _brain(fast=[], agent=[])
    t = T.Task(id="x", project="sandbox", path="D:/tmp/sb", prompt="p", status="done", session_id="sess-1")
    args, cwd = b.task_question_cmd(t, "what did it change?")
    assert cwd == "D:/tmp/sb"
    assert args[args.index("--resume") + 1] == "sess-1"
    assert "--fork-session" in args and "--no-session-persistence" in args
    tools = args[args.index("--allowedTools") + 1]
    assert "Read" in tools and "Edit" not in tools and "Write" not in tools


# -- routes and task lookup -------------------------------------------------------------

def test_voice_routes():
    r = lambda t: B.local_route(t, P)  # noqa: E731
    assert r("Start fresh.") == ("fresh",)
    assert r("let's start over") == ("fresh",)
    assert r("Stop the task.") == ("task_stop", "")
    assert r("stop the inha app task") == ("task_stop", "inha app")
    assert r("cancel the task in inha app") == ("task_stop", "inha app")
    assert r("stop all tasks") == ("task_stop", "all")
    assert r("stop") == ("noop",)
    assert r("Resume the last task in a terminal.") == ("task_resume", "last")
    assert r("open the inha app task in a terminal") == ("task_resume", "inha app")
    assert r("open a task in hey claude: add a readme")[0] == "task"        # still starts a task
    assert r("What did the inha app task change?") == ("task_ask", "inha app", "What did the inha app task change?")
    assert r("how did the last task go?")[:2] == ("task_ask", "last")
    assert r("tell me what the task in inha app did")[:2] == ("task_ask", "inha app")
    assert r("how are my tasks going") == ("status",)
    assert r("start a task in sandbox: write a file")[0] == "task"


def _tasks():
    mk = lambda i, proj, prompt, status, t: T.Task(id=i, project=proj, path="x", prompt=prompt,  # noqa: E731
                                                     status=status, started=t, session_id="s" + i)
    return [mk("1", "inha app", "fix the login screen", "done", 1),
            mk("2", "hey claude", "add a readme", "done", 2),
            mk("3", "inha app", "bump the version", "failed", 3),
            mk("4", "sandbox", "write a packing list", "running", 4)]


def test_find_task_by_name_project_and_recency():
    ts = _tasks()
    assert T.find_task(ts, "inha app", P).id == "3"              # project: its latest task
    assert T.find_task(ts, "in ha app", P).id == "3"             # misheard project name
    assert T.find_task(ts, "readme", P).id == "2"                # words of the prompt
    assert T.find_task(ts, "login screen", P).id == "1"
    assert T.find_task(ts, "last", P).id == "4"                  # recency
    assert T.find_task(ts, "", P, running=False).id == "3"       # latest finished
    assert T.find_task(ts, "", P, running=True).id == "4"
    assert T.find_task(ts, "spaceship", P) is None
    assert T.find_task([], "last", P) is None


def test_end_phrases_and_wake_stripping():
    for s in ("Thanks.", "thank you", "Thanks, that's all.", "no thanks", "That's all", "ok cool", "Nope.",
              "OK, thank you very much.", "bye"):
        assert C.is_end_phrase(s), s
    for s in ("thanks, and what about tomorrow?", "what's the weather", "no, I meant Sydney",
              "Is it going to rain?", ""):
        assert not C.is_end_phrase(s), s
    assert C.strip_wake("Hey Jarvis, what time is it?") == "What time is it?"
    assert C.strip_wake("hey claude what's new") == "What's new"
    assert C.strip_wake("what time is it?") == "what time is it?"


# -- follow-up window and the speaking gate --------------------------------------------

def test_followup_opens_only_after_speech_and_tail():
    clock = Clock(0.0)
    gate = SpeakingGate({"suppress_while_speaking": True, "speaking_tail_s": 0.7}, clock=clock)
    busy = [False]
    fu = C.FollowUp({"followup_s": 5}, gate, busy=lambda: busy[0], clock=clock)
    assert not fu.poll()                         # nothing armed
    gate.set_speaking(True)                      # the answer is being read out
    fu.arm()
    assert not fu.poll()
    clock.t = 3.0
    gate.set_speaking(False)                     # done speaking at t=3: tail until 3.7
    clock.t = 3.5
    assert not fu.poll() and not fu.is_open
    clock.t = 3.8
    busy[0] = True                               # e.g. a reply still being generated
    assert not fu.poll()
    busy[0] = False
    assert fu.poll() and fu.is_open              # opens once ...
    assert not fu.poll()                         # ... and reports it once
    clock.t = 5.8
    assert abs(fu.remaining() - 3.0) < 1e-9
    clock.t = 8.9
    assert not fu.is_open and fu.remaining() == 0


def test_followup_waits_for_the_voice_even_if_wake_suppression_is_off():
    clock = Clock(0.0)
    gate = SpeakingGate({"suppress_while_speaking": False, "speaking_tail_s": 0.7}, clock=clock)
    fu = C.FollowUp({"followup_s": 5}, gate, clock=clock)
    gate.set_speaking(True)
    fu.arm()
    assert not gate.suppressed() and gate.busy()
    assert not fu.poll()                         # its own voice never opens the window
    gate.set_speaking(False)
    clock.t = 1.0
    assert fu.poll()


def test_followup_off_and_stuck_speech():
    clock = Clock(0.0)
    gate = SpeakingGate({}, clock=clock)
    off = C.FollowUp({"followup_s": 0}, gate, clock=clock)
    off.arm()
    assert off.state == "idle" and not off.poll()
    fu = C.FollowUp({"followup_s": 5}, gate, clock=clock)
    gate.set_speaking(True)
    fu.arm()
    clock.t = 200                                # speech that never ends: give up
    assert not fu.poll() and fu.state == "idle"


# -- the assistant: one entry point, follow-up in the mic loop, task control ---------------

class FakeSpeaker:
    def __init__(self, a):
        self.a, self.spoken, self.announced = a, [], []
        self.busy, self.first_audio_t = False, None

        class Q:
            @staticmethod
            def empty():
                return True
        self.q = Q()

    def begin(self):
        self.first_audio_t = None

    def feed(self, chunk):
        self.spoken.append(chunk)
        self.first_audio_t = self.first_audio_t or time.perf_counter()

    def flush(self):
        pass

    def announce(self, text):
        self.announced.append(text)

    def stop(self):
        pass

    def wait(self, timeout=0):
        pass


class FakeProc:
    def __init__(self):
        self.terminated = False

    def poll(self):
        return 0 if self.terminated else None

    def terminate(self):
        self.terminated = True


def _assistant(tmp_path, fast=(), agent=()):
    from test_offline import _assistant as make
    a = make()
    a._log_timing = lambda tm: None
    a.cfg["projects"] = dict(P.map)
    a.speaker = FakeSpeaker(a)
    a.brain = _brain(fast=list(fast), agent=list(agent), tmp_path=tmp_path)
    a.brain.cfg = a.cfg
    a.ready.set()
    T.STATE_FILE, T.LOG_DIR = tmp_path / "tasks.json", tmp_path
    tm = T.TaskManager.__new__(T.TaskManager)
    tm.cfg = {"max_parallel": 3, "permission_mode": "acceptEdits", "allowed_tools": []}
    tm.exe, tm.tasks, tm.procs, tm.lock = "claude", {}, {}, threading.Lock()
    tm.on_change, tm.on_finish = (lambda t: None), a._task_finished
    a.tasks = tm
    return a


def test_five_turn_mixed_conversation_keeps_context(tmp_path):
    """quick -> lookup -> quick -> task -> question about the task, all
    through Assistant.request (the entry point typed input uses too)."""
    a = _assistant(tmp_path,
                   fast=[["Canberra is the capital of Australia."],
                         ["Yes, take a light jacket for the evening."]],
                   agent=[["It's 18 degrees and sunny in Canberra right now."]])
    sent_to_task = []

    def fake_spawn(project, path, prompt, context=""):
        sent_to_task.append((project, prompt, context))
        t = T.Task(id="t1", project=project, path=path, prompt=prompt, status="running", session_id="sess-9")
        a.tasks.tasks[t.id] = t
        return t
    a.tasks.spawn = fake_spawn
    asked = []
    a.brain.ask_task = lambda t, q: (asked.append((t.id, t.session_id, q)) or iter(["It added packing.txt."]))

    a.request("What is the capital of Australia?", wait=True)
    a.request("What's the weather there right now?", source="followup", wait=True)
    assert "Canberra is the capital" in a.brain.agent.chat.sent[-1]           # quick -> agent
    a.request("Should I take a jacket?", source="followup", wait=True)
    assert "18 degrees" in a.brain.chat.sent[-1]                               # agent -> quick
    a.request("Start a task in sandbox: write packing.txt with a packing list for that city", wait=True)
    proj, prompt, ctx = sent_to_task[0]
    assert proj == "sandbox" and "Canberra" in ctx and "18 degrees" in ctx      # whole window -> task
    t = a.tasks.tasks["t1"]
    t.status, t.summary = "done", "It wrote packing.txt."
    a._task_finished(t)
    assert a.speaker.announced and "sandbox task is done" in a.speaker.announced[-1]
    a.request("What did the sandbox task change?", source="followup", wait=True)
    assert asked == [("t1", "sess-9", "What did the sandbox task change?")]
    assert "It added packing.txt." in "".join(a.speaker.spoken)
    tiers = [x.tier for x in a.brain.convo.turns]
    assert tiers == ["fast", "agent", "fast", "task", "local", "task-ask"]
    assert a.followup.state == "armed"                                         # listening for a follow-up


def test_typed_request_joins_the_conversation_without_a_followup(tmp_path):
    a = _assistant(tmp_path, fast=[["Lima."]])
    a.request("capital of Peru?", source="typed", wait=True)
    assert a.brain.convo.turns[-1].user == "capital of Peru?" and a.followup.state == "idle"
    a.brain.chat.replies.append(["About 10 million."])
    a.followup.arm()                                   # a typed request also closes a pending window
    a.submit_text("and its population?")               # the hotkey input field's path
    for _ in range(100):
        if len(a.brain.convo.turns) == 2 and not a.conv_lock.locked():
            break
        time.sleep(0.02)
    assert a.brain.convo.turns[-1].user == "and its population?" and a.followup.state == "idle"
    assert "Lima." not in a.brain.chat.sent[-1]        # the quick model remembers its own turn


def test_start_fresh_forgets(tmp_path):
    a = _assistant(tmp_path, fast=[["Lima."]])
    a.request("capital of Peru?", wait=True)
    epoch = a.brain.chat.epoch
    a.request("start fresh", wait=True)
    for _ in range(50):
        if a.brain.chat.epoch != epoch:
            break
        time.sleep(0.02)
    assert a.brain.convo.turns == [] and a.brain.chat.epoch == epoch + 1
    assert "starting fresh" in "".join(a.speaker.spoken)


def test_stop_the_task(tmp_path):
    a = _assistant(tmp_path)
    run = T.Task(id="r1", project="inha app", path="x", prompt="fix login", status="running", started=1)
    run2 = T.Task(id="r2", project="sandbox", path="x", prompt="write a file", status="running", started=2)
    a.tasks.tasks.update(r1=run, r2=run2)
    a.tasks.procs.update(r1=FakeProc(), r2=FakeProc())
    a.request("stop the task", wait=True)                           # two running: which one?
    assert "Which one" in "".join(a.speaker.spoken) and not a.tasks.procs["r1"].terminated
    a.request("stop the inha app task", wait=True)
    assert a.tasks.procs["r1"].terminated and run.status == "stopped" and not a.tasks.procs["r2"].terminated
    a._task_finished(run)                                           # no "was stopped" announcement
    assert a.speaker.announced == []
    a.request("stop all tasks", wait=True)
    assert a.tasks.procs["r2"].terminated


def test_resume_the_last_task_in_a_terminal(tmp_path):
    a = _assistant(tmp_path)
    opened = []
    a.tasks.tasks.update(
        old=T.Task(id="old", project="inha app", path="D:/a", prompt="x", status="done", started=1, session_id="s1"),
        new=T.Task(id="new", project="sandbox", path="D:/b", prompt="y", status="done", started=2, session_id="s2"))
    a.tasks.open_terminal = opened.append
    a.request("resume the last task in a terminal", wait=True)
    assert [t.id for t in opened] == ["new"]
    a.request("open the inha app task in a terminal", wait=True)
    assert [t.id for t in opened] == ["new", "old"]
    calls = []
    t = a.tasks.tasks["old"]
    T.TaskManager.open_terminal(a.tasks, t, popen=lambda cmd, **kw: calls.append((cmd, kw)))
    assert calls[0][0][-2:] == ["--resume", "s1"] and calls[0][1]["cwd"] == "D:/a"


def test_mic_loop_followup_window(tmp_path):
    """After an answer the mic loop records without the wake word, but only
    once the speaking gate (TTS + tail) is clear; 'thanks' ends it; silence
    closes it."""
    import heyclaude.assistant as A
    a = _assistant(tmp_path)
    a.cfg["conversation"]["followup_s"] = 5
    a.wake_gate.cfg["speaking_tail_s"] = 0.0

    class NoWake:
        def hit(self, block):
            return False

        def reset(self):
            pass
    a.wake = NoWake()
    heard = iter(["Hey Jarvis, and tomorrow?", "Thanks, that's all."])
    a.stt = type("S", (), {"transcribe": lambda self, audio: next(heard)})()
    requests, recs = [], []
    a.request = lambda text, timing=None, source="voice", wait=False: requests.append((text, source))
    script = iter([True, True, False])    # speech, speech, then silence

    def fake_record(blocks, cfg, on_level=None, stop_event=None, hold=None):
        recs.append(cfg["start_timeout"])
        next(blocks)
        return (np.zeros(1600, np.int16), time.perf_counter()) if next(script) else (None, None)
    orig = A.record_utterance
    A.record_utterance = fake_record
    try:
        state = {"n": 0}

        def blocks():
            while state["n"] < 400:
                n = state["n"]
                state["n"] += 1
                if n == 0:
                    a.wake_gate.set_speaking(True)      # the answer is being spoken...
                    a.followup.arm()
                if n == 50:
                    assert not recs                     # ...nothing recorded while it talks
                    a.wake_gate.set_speaking(False)
                if n in (120, 240):
                    a.followup.arm()                    # the next answers
                yield np.zeros(1280, np.int16)

        class Src:
            finished = True

            def __iter__(self):
                return blocks()
        a._pump(Src(), one_shot=False)
    finally:
        A.record_utterance = orig
    assert requests == [("And tomorrow?", "followup")]             # wake word stripped, no wake needed
    assert len(recs) == 3 and all(4.0 < r <= 5.0 for r in recs)   # the window's remaining time
    assert a.followup.state == "idle"


def test_simulator_plays_one_clip_per_followup_window(tmp_path):
    a = _assistant(tmp_path)
    a.mode = "thinking"
    a.wake_gate.cfg["speaking_tail_s"] = 0.0
    assert not a._sim_ready()
    a.followup.arm()
    assert a.followup.poll() and a._sim_ready()        # window open: play the next clip...
    assert not a._sim_ready()                          # ...but only one into this window
    a.followup.close()
    a.pending_tasks = 1
    a.followup.arm()
    a.followup.poll()
    assert not a._sim_ready()                          # a task is running: wait for it
    a.pending_tasks = 0
    assert a._sim_ready()


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
