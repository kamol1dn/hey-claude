"""Tests for the hotkey's talk-or-type input: the session state machine, the
assistant's typed path through the router, and typing cancelling the mic.
No mic, models, network or Qt needed.

    .venv\\Scripts\\python tests\\test_input.py
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from heyclaude.inputbox import CLOSED, IDLE_OPEN, LISTENING, TYPING, InputSession  # noqa: E402


def _session():
    log = []
    s = InputSession(open_mic=lambda: log.append("open_mic"), cancel_mic=lambda: log.append("cancel_mic"),
                     submit=lambda t: log.append(("submit", t)), show=lambda: log.append("show"),
                     close=lambda sent: log.append(("close", sent)))
    return s, log


def test_hotkey_opens_field_and_mic():
    s, log = _session()
    s.hotkey()
    assert s.state == LISTENING and s.mic_on and log == ["show", "open_mic"]


def test_typing_cancels_the_mic_once():
    s, log = _session()
    s.hotkey()
    s.edited("w")
    s.edited("wh")
    assert s.state == TYPING and not s.mic_on
    assert log.count("cancel_mic") == 1


def test_enter_sends_shift_enter_is_a_new_line():
    s, log = _session()
    s.hotkey()
    s.edited("what's 2+2")
    assert s.key_enter("what's 2+2", shift=True) is False      # the field inserts a new line
    assert s.is_open
    assert s.key_enter("what's 2+2\nand 3+3", shift=False) is True
    assert s.state == CLOSED
    assert log[-2:] == [("close", True), ("submit", "what's 2+2\nand 3+3")]


def test_enter_on_empty_field_does_nothing():
    s, log = _session()
    s.hotkey()
    assert s.key_enter("   ", shift=False) is True and s.state == LISTENING
    assert not any(isinstance(x, tuple) and x[0] == "submit" for x in log)


def test_enter_while_mic_still_open_cancels_it():
    s, log = _session()
    s.hotkey()
    s.key_enter("pasted text", shift=False)                   # text arrived without an edit event
    assert "cancel_mic" in log and ("submit", "pasted text") in log


def test_escape_cancels_and_collapses():
    s, log = _session()
    s.hotkey()
    s.key_escape()
    assert s.state == CLOSED and log[-2:] == ["cancel_mic", ("close", False)]
    s.key_escape()                                             # closed: nothing happens
    assert log[-1] == ("close", False) and log.count(("close", False)) == 1


def test_hotkey_again_toggles_closed():
    s, log = _session()
    s.hotkey()
    s.hotkey()
    assert s.state == CLOSED and log == ["show", "open_mic", "cancel_mic", ("close", False)]
    s.hotkey()                                                 # and opens again
    assert s.state == LISTENING


def test_escape_while_typing_does_not_touch_the_mic_again():
    s, log = _session()
    s.hotkey()
    s.edited("x")
    s.key_escape()
    assert log.count("cancel_mic") == 1 and log[-1] == ("close", False)


def test_speech_closes_the_field_nothing_heard_keeps_it():
    s, log = _session()
    s.hotkey()
    s.heard("what's the weather")
    assert s.state == CLOSED and log[-1] == ("close", True)
    assert not any(isinstance(x, tuple) and x[0] == "submit" for x in log)   # the assistant sends speech itself
    s, log = _session()
    s.hotkey()
    s.heard("")
    assert s.state == IDLE_OPEN and not s.mic_on and s.is_open             # mic timed out: type instead
    s.edited("hi")
    assert s.state == TYPING and "cancel_mic" not in log
    s.key_enter("hi", shift=False)
    assert ("submit", "hi") in log


# -- the assistant side ---------------------------------------------------------------

class _Sig:
    def __init__(self):
        self.calls = []

    def emit(self, *a):
        self.calls.append(a)


class _Bridge:
    def __init__(self):
        self.state, self.level, self.tasks, self.quit, self.heard = _Sig(), _Sig(), _Sig(), _Sig(), _Sig()


class _Speaker:
    busy = False

    def __init__(self):
        self.fed, self.first_audio_t = [], None

    def begin(self):
        self.first_audio_t = None

    def feed(self, t):
        self.fed.append(t)
        self.first_audio_t = time.perf_counter()

    def flush(self):
        pass

    def stop(self):
        pass


class _Chat:
    def __init__(self, chunks):
        self.chunks, self.asked = chunks, []

    def stream(self, text):
        self.asked.append(text)
        yield from self.chunks


class _Tasks:
    def __init__(self):
        self.spawned, self.tasks = [], {}

    def spawn(self, project, path, task, context=""):
        self.spawned.append((project, path, task))
        return type("T", (), {"id": "t1"})()


def _assistant():
    from heyclaude import brain as B
    from heyclaude import config as C
    from heyclaude.assistant import Assistant
    a = Assistant(C.load(os.devnull + ".missing"), _Bridge())
    b = B.Brain.__new__(B.Brain)
    b.projects, b.backend, b.chat, b.timing = B.Projects({"hey claude": "D:/coding/hey-claude"}), "fake", \
        _Chat(["Paris is the capital."]), {}
    b.agent = object()
    a.brain, a.speaker, a.tasks = b, _Speaker(), _Tasks()
    a.cfg["projects"] = {"hey claude": "D:/coding/hey-claude"}
    return a


def _wait(cond, secs=5):
    end = time.time() + secs
    while time.time() < end and not cond():
        time.sleep(0.02)
    return cond()


def test_typed_request_goes_through_the_router():
    a = _assistant()
    a.submit_text("start a task in hey claude: add a license file")       # local route -> task tier
    assert _wait(lambda: a.tasks.spawned)
    assert a.tasks.spawned == [("hey claude", "D:/coding/hey-claude", "add a license file")]
    a.submit_text("what is the capital of france?")                       # quick-answer tier
    assert _wait(lambda: "Paris is the capital." in "".join(a.speaker.fed))
    assert a.brain.chat.asked == ["what is the capital of france?"]
    assert _wait(lambda: not a.conv_lock.locked())


def test_typing_stops_the_sessions_recording():
    import numpy as np
    a = _assistant()

    class Wake:
        def hit(self, b):
            raise AssertionError("no wake word while the field is open")

        def reset(self):
            pass

    class Src:                                                # endless "speech" from the mic
        def __iter__(self):
            while True:
                time.sleep(0.005)
                yield np.full(1280, 3000, np.int16)

    a.wake, a.stt = Wake(), None
    a.begin_input()                                           # hotkey: field open, manual listen
    done = threading.Event()
    threading.Thread(target=lambda: (a._pump(Src(), one_shot=True), done.set()), daemon=True).start()
    time.sleep(0.3)                                           # recording is under way
    assert not done.is_set()
    a.cancel_input_mic()                                      # first keystroke in the field
    assert done.wait(2), "the recording should stop as soon as typing starts"
    assert a.ui.heard.calls == [("",)]                        # tells the island: nothing spoken
    a.end_input()
    assert not a.input_open


if __name__ == "__main__":
    n = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            n += 1
            print("ok", name)
    print(n, "passed")
