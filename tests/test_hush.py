"""Tests for stopping it mid-answer: Assistant.hush() (stop button, Esc) and the
island's mic/stop buttons. No mic, models or network; Qt runs offscreen.

    .venv\\Scripts\\python tests\\test_hush.py
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from test_input import _assistant  # noqa: E402


class _StopSpeaker:
    busy = False

    def __init__(self):
        self.stopped = 0
        self.first_audio_t = None

    def stop(self):
        self.stopped += 1


def test_hush_stops_speech_and_drops_the_answer():
    a = _assistant()
    a.speaker = _StopSpeaker()
    a.listening.set()
    a.set_mode("speaking", "Paris is the capital of France and …")
    gen = a.gen
    a.hush()
    assert a.speaker.stopped == 1
    assert a.gen == gen + 1, "in-flight answers must see a new generation and give up"
    assert a.mode == "idle"


def test_hush_while_thinking_cancels_too():
    a = _assistant()
    a.speaker = _StopSpeaker()
    a.listening.set()
    a.set_mode("thinking", "…")
    a.hush()
    assert a.mode == "idle"


def test_hush_when_idle_changes_nothing_visible():
    a = _assistant()
    a.speaker = _StopSpeaker()
    a.listening.set()
    a.set_mode("listening")
    a.hush()
    assert a.mode == "listening", "hush must not cut off the user while they talk"


def _island(mode):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from heyclaude.island import Bridge, Island
    isl = Island(Bridge(), {"screen": "primary", "top_margin": "auto"}, wake_label="hey jarvis")
    isl.mode, isl.text = mode, "Paris is the capital of France."
    w, h = isl._target_size()
    isl.resize(w, h)
    return isl


class _Ev:
    def __init__(self, x, y):
        from PySide6.QtCore import QPointF, Qt
        self._p, self._b = QPointF(x, y), Qt.LeftButton

    def position(self):
        return self._p

    def button(self):
        return self._b


def test_buttons_fit_inside_the_pill_and_route_clicks():
    isl = _island("speaking")
    mic, stop = isl._ctrl_rects()
    assert 0 < mic.left() < stop.left() and stop.right() <= isl.width() - 8
    got = []
    isl.interrupt_talk.connect(lambda: got.append("mic"))
    isl.stop_speaking.connect(lambda: got.append("stop"))
    isl.mousePressEvent(_Ev(mic.center().x(), mic.center().y()))
    isl.mousePressEvent(_Ev(stop.center().x(), stop.center().y()))
    assert got == ["mic", "stop"]


def test_thinking_pill_is_wider_for_the_buttons():
    from heyclaude.island import CTRL_W
    isl = _island("thinking")
    w_think = isl._target_size()[0]
    isl.mode = "error"
    assert w_think - isl._target_size()[0] == CTRL_W


if __name__ == "__main__":
    n = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            n += 1
            print("ok", name)
    print(n, "passed")
