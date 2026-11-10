"""M4d: dictate anywhere. The key logic, the dictionary, and the whole
key -> mic -> text -> paste flow with a fake mic, speech and paster.
No Windows hook, mic, model or clipboard needed.

    .venv\\Scripts\\python tests\\test_dictate.py
"""
import os
import sys
import threading
import time
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np  # noqa: E402

from heyclaude import dictionary  # noqa: E402
from heyclaude.dictate import DictateKeys, Dictation, add_stats, cleanup_style  # noqa: E402
from heyclaude.holdkey import parse  # noqa: E402

RALT, ESC, E_KEY, CAPS, TAB = 0xA5, 0x1B, 0x45, 0x14, 0x09


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def _keys():
    c = Clock()
    return DictateKeys(parse("right alt"), clock=c), c


# -- the key logic ---------------------------------------------------------------------
def test_hold_and_release_dictates():
    k, c = _keys()
    assert k.key(RALT, True) == (["start"], False)
    assert k.key(RALT, True) == ([], False)                 # auto-repeat
    c.t += 2.0
    assert k.key(RALT, False) == (["finish"], False)
    assert not k.active and k.released_at == c.t


def test_double_tap_is_hands_free_until_the_next_tap():
    k, c = _keys()
    assert k.key(RALT, True)[0] == ["start"]
    c.t += 0.1
    assert k.key(RALT, False)[0] == []                      # short: a tap, wait for a second one
    c.t += 0.2
    assert k.key(RALT, True)[0] == ["handsfree"]
    c.t += 0.1
    assert k.key(RALT, False)[0] == []
    c.t += 20                                               # talks for a while, hands free
    assert k.tick() == []
    assert k.key(E_KEY, True)[0] == []                      # typing meanwhile doesn't cancel it
    assert k.key(RALT, True)[0] == ["stop"]
    assert k.key(RALT, False)[0] == ["finish"]              # pasted once the key is up


def test_a_single_tap_does_nothing():
    k, c = _keys()
    k.key(RALT, True)
    c.t += 0.1
    k.key(RALT, False)
    c.t += 0.5
    assert k.tick() == ["cancel"] and not k.active


def test_esc_cancels_and_is_swallowed_only_then():
    k, c = _keys()
    assert k.key(ESC, True) == ([], False)                  # not dictating: Esc goes to the app
    k.key(RALT, True)
    assert k.key(ESC, True) == (["cancel"], True)
    assert not k.active
    assert k.key(RALT, False) == ([], False)                # the release after a cancel is nothing


def test_another_key_while_holding_is_a_shortcut():
    k, c = _keys()
    k.key(RALT, True)
    assert k.key(E_KEY, True) == (["cancel"], False)        # AltGr+e: the key goes through
    k.key(E_KEY, False)
    assert k.key(RALT, False) == ([], False)


def test_never_swallows_outside_dictation_except_caps_as_its_own_key():
    k, c = _keys()
    for vk in (RALT, E_KEY, TAB):
        assert k.key(vk, True)[1] is False if vk != RALT else True
    k2 = DictateKeys(parse("caps lock"), clock=Clock())
    assert k2.key(CAPS, True) == (["start"], True)          # caps lock as the key: no toggle


# -- the dictionary -----------------------------------------------------------------------
D = {"words": ["Phoenix", "eClass"],
     "replacements": {"hey cloud": "hey Claude", "e class": "eClass", "fenix": "Phoenix", "inha app": "Inha"},
     "snippets": {"my email": "me@example.com", "sign off": "Best,\nK."}}


def test_replacements_fix_the_misheard_words():
    assert dictionary.apply("Hey cloud, open e-class.", D) == "Hey Claude, open eClass."
    assert dictionary.apply("I study at Fenix and the inha app.", D) == "I study at Phoenix and the Inha."
    assert dictionary.apply("cloudy day", D) == "cloudy day"                 # whole words only


def test_snippets_whole_and_inline():
    assert dictionary.apply("My email.", D) == "me@example.com"
    assert dictionary.apply("Send it to my email please.", D) == "Send it to me@example.com please."
    assert dictionary.apply("Sign off", D) == "Best,\nK."
    assert dictionary.apply("My email.", D, snippets=False) == "My email."   # the assistant's own STT


def test_words_become_the_whisper_prompt():
    from heyclaude.speech.manager import Speech
    sp = Speech({"dictionary": D, "speech": {}, "stt": {}}, hint_words=["hey claude"], start_thread=False)
    assert sp.prompt() == "Vocabulary: Claude, hey claude, Phoenix, eClass."
    assert sp.fix("hey cloud what's up") == "hey Claude what's up"


def test_cleanup_style_per_app():
    cfg = {"cleanup": "off", "cleanup_apps": {"OUTLOOK.EXE": "polished", "windowsterminal.exe": "as said"}}
    assert cleanup_style(cfg, "outlook.exe") == "polished"
    assert cleanup_style(cfg, "WindowsTerminal.exe") == "as_said"
    assert cleanup_style(cfg, "notepad.exe") == "off"
    assert cleanup_style({"cleanup": "weird"}, "x") == "off"


def test_stats_accumulate(tmp=None):
    import tempfile
    from pathlib import Path
    p = Path(tempfile.mkdtemp()) / "dictation.json"
    add_stats(p, 40, 12.0)
    st = add_stats(p, 20, 6.0)
    assert st["dictations"] == 2 and st["words"] == 60 and st["spoken_s"] == 18.0
    assert st["saved_s"] == 60 / 40 * 60 - 18.0


# -- the whole flow ---------------------------------------------------------------------------
class FakeMic:
    """Yields `seconds` of 'speech' as fast as asked, then blocks like a mic until closed."""

    def __init__(self, seconds=5.0, loud=True):
        self.n = int(seconds * 16000 / 1280)
        self.loud = loud
        self.closed = threading.Event()
        self.started = False

    def start(self):
        self.started = True

    def close(self):
        self.closed.set()

    def __iter__(self):
        for _ in range(self.n):
            if self.closed.is_set():
                return
            yield np.full(1280, 3000 if self.loud else 0, np.int16)
        self.closed.wait(10)


class FakeVad:
    frame_ms = 20.0

    def reset(self):
        pass

    def frames(self, block):
        return [bool(block[0])] * 4


class FakeSpeech:
    def __init__(self, text="hey cloud, the e class starts at nine.", delay=0.0):
        self.text, self.delay, self.calls, self.events = text, delay, [], []

    def wake(self):
        self.events.append("wake")

    def begin(self, live=False):
        self.events.append(f"begin live={live}")

    def cancel(self):
        self.events.append("cancel")

    def transcribe(self, audio):
        self.calls.append(len(audio))
        time.sleep(self.delay)
        return self.text


def _dictation(mic=None, speech=None, paster=None, **cfg):
    modes, notes, active, pasted = [], [], [], []
    mic = mic or FakeMic()

    def default_paster(text):
        pasted.append(text)
        return SimpleNamespace(ok=True, reason="", app="notepad.exe", ms=12.0)
    conf = {"dictate": dict({"key": "right alt"}, **cfg), "dictionary": D, "listen": {"device": None}}
    d = Dictation(conf, speech or FakeSpeech(), ui=lambda m, t="": modes.append((m, t)), note=notes.append,
                  mic_factory=lambda: mic, paster=paster or default_paster, vad_factory=FakeVad,
                  on_active=active.append, foreground=lambda: "notepad.exe", cleaner=lambda t, s: f"[{s}] {t}",
                  rest=lambda: modes.append(("idle", "")))
    return d, SimpleNamespace(modes=modes, notes=notes, active=active, pasted=pasted, mic=mic)


def _wait(cond, timeout=5.0):
    end = time.time() + timeout
    while not cond() and time.time() < end:
        time.sleep(0.01)
    assert cond(), "timed out"


def test_hold_speak_release_pastes_the_fixed_text():
    sp = FakeSpeech()
    d, r = _dictation(speech=sp)
    d.inject(RALT, True)
    _wait(lambda: len(d._blocks) >= 62)                     # ~5 s heard
    time.sleep(0.4)                                         # (longer than a tap)
    d.inject(RALT, False)
    _wait(lambda: r.pasted)
    assert r.pasted == ["hey Claude, the eClass starts at nine. "], r.pasted
    assert sp.events[:2] == ["wake", "begin live=True"]
    assert r.active == [True, False] and r.mic.closed.is_set()
    assert ("dictating", "dictating…") in r.modes and r.modes[-1] == ("idle", "")
    assert r.notes == ["typed 7 words"]
    assert d.last_text.startswith("hey Claude")
    assert d.timings[-1]["ok"] and d.timings[-1]["app"] == "notepad.exe"
    d.close()


def test_release_to_text_well_under_a_second_for_5s_on_turbo_speed():
    # turbo on the GPU transcribes 5 s in ~0.25-0.3 s (README "Voice"); the rest is ours
    d, r = _dictation(speech=FakeSpeech(delay=0.30))
    d.inject(RALT, True)
    _wait(lambda: len(d._blocks) >= 62)
    time.sleep(0.4)
    d.inject(RALT, False)
    _wait(lambda: r.pasted)
    assert d.last_ms is not None and d.last_ms < 1000, d.last_ms
    assert d.last_ms < 500, f"our own overhead is too high: {d.last_ms:.0f} ms"
    d.close()


def test_esc_cancels_without_pasting():
    sp = FakeSpeech()
    d, r = _dictation(speech=sp)
    d.inject(RALT, True)
    _wait(lambda: len(d._blocks) >= 5)
    assert d.inject(ESC, True) is True
    _wait(lambda: r.active == [True, False])
    d.inject(RALT, False)
    time.sleep(0.2)
    assert not r.pasted and not sp.calls and "cancel" in sp.events
    d.close()


def test_silence_pastes_nothing():
    d, r = _dictation(mic=FakeMic(3.0, loud=False))
    d.inject(RALT, True)
    _wait(lambda: len(d._blocks) >= 30)
    time.sleep(0.35)
    d.inject(RALT, False)
    _wait(lambda: r.notes)
    assert r.notes == ["didn't hear anything"] and not r.pasted
    d.close()


def test_password_field_is_not_typed_and_paste_again_works():
    calls = []

    def paster(text):
        calls.append(text)
        if len(calls) == 1:
            return SimpleNamespace(ok=False, reason="password field", app="chrome.exe", ms=1.0)
        return SimpleNamespace(ok=True, reason="", app="notepad.exe", ms=1.0)
    d, r = _dictation(paster=paster, paste_again_hotkey="win+alt+v")
    d.inject(RALT, True)
    _wait(lambda: len(d._blocks) >= 20)
    time.sleep(0.35)
    d.inject(RALT, False)
    _wait(lambda: r.notes)
    assert r.notes[-1].startswith("not typed: password field (paste again: win+alt+v)")
    d.paste_again()
    _wait(lambda: len(calls) == 2)
    assert calls[1] == calls[0]
    d.close()


def test_cleanup_runs_only_when_on():
    d, r = _dictation(cleanup="natural")
    d.inject(RALT, True)
    _wait(lambda: len(d._blocks) >= 20)
    time.sleep(0.35)
    d.inject(RALT, False)
    _wait(lambda: r.pasted)
    assert r.pasted[0].startswith("[natural] hey Claude")
    assert d.timings[-1]["style"] == "natural"
    d.close()


def test_disabled_dictation_ignores_the_key():
    d, r = _dictation(enabled=False)
    assert d.inject(RALT, True) is False
    time.sleep(0.2)
    assert not r.active and not r.mic.started
    d.close()


def test_dictating_state_falls_back_to_listening():
    from heyclaude import states
    assert states.resolve("dictating", {"idle", "off", "listening", "thinking", "talking"}) == "listening"
    assert states.resolve("loading", {"idle", "off", "listening", "thinking", "talking"}) == "thinking"


def test_qml_route_for_dictating():
    from heyclaude.island_qml import route
    cmds = route("dictating", "", {"engine": "ready"})
    assert cmds == [("go", ["listening", {}]), ("setPartial", ["dictating…"])], cmds
    assert route("dictating", "…", {"engine": "listening"}) == [("setPartial", ["…"])]


if __name__ == "__main__":
    n = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            n += 1
            print("ok", name)
    print(n, "passed")
