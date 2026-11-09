"""Ctrl+Shift mute during a hold: the key logic and the recording.
No mic or Windows hook needed.

    .venv\\Scripts\\python tests\\test_holdmute.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from heyclaude.holdkey import HoldState, parse  # noqa: E402

CTRL, SHIFT, T_KEY = 0xA2, 0xA0, 0x54


def _hs():
    hs = HoldState(parse("ctrl"), mute_vks=parse("shift"))
    hs.reset()
    return hs


def test_shift_during_hold_mutes_and_unmutes():
    hs = _hs()
    hs.key(CTRL, True)
    assert hs.held and not hs.muted
    assert hs.key(SHIFT, True) is True               # a change: muted
    assert hs.held and hs.muted
    hs.key(SHIFT, True)                              # auto-repeat changes nothing
    assert hs.muted
    assert hs.key(SHIFT, False) is True
    assert hs.held and not hs.muted                  # still holding, live again


def test_releasing_ctrl_while_muted_ends_hold_and_mute():
    hs = _hs()
    hs.key(CTRL, True)
    hs.key(SHIFT, True)
    hs.key(CTRL, False)
    assert not hs.held and not hs.muted


def test_ctrl_shift_another_key_is_still_a_shortcut():
    hs = _hs()
    hs.key(CTRL, True)
    hs.key(SHIFT, True)
    hs.key(T_KEY, True)                              # Ctrl+Shift+T
    assert not hs.held and not hs.muted and not hs.used


def test_shift_alone_means_nothing():
    hs = _hs()
    hs.key(SHIFT, True)
    assert not hs.held and not hs.muted
    hs.key(SHIFT, False)


def test_shift_first_then_ctrl_is_a_muted_hold():
    hs = _hs()
    hs.key(SHIFT, True)
    hs.key(CTRL, True)
    assert hs.held and hs.muted


def test_mute_off_keeps_the_old_shortcut_behaviour():
    hs = HoldState(parse("ctrl"))                   # hold_mute_key: "" -> no mute keys
    hs.reset()
    hs.key(CTRL, True)
    hs.key(SHIFT, True)
    assert not hs.held                               # Shift counts as a shortcut key again


def test_mute_key_same_as_hold_key_is_ignored():
    from heyclaude.holdkey import HoldKey
    assert HoldKey._mute_vks("ctrl", "ctrl") == frozenset()
    assert HoldKey._mute_vks("ctrl", "") == frozenset()
    assert HoldKey._mute_vks("ctrl", "shift") == parse("shift")


def test_muted_audio_is_silenced_and_trimmed():
    """Talk, hold Ctrl, mute with Shift while 'coughing' (loud audio), unmute, talk, release."""
    import numpy as np
    from heyclaude.listen import BLOCK, record_utterance
    now = {"t": 0.0}
    clock = lambda: now["t"]  # noqa: E731
    hs = HoldState(parse("ctrl"), clock=clock, mute_vks=parse("shift"))
    hs.reset()
    keys = {20: [(CTRL, True)], 30: [(SHIFT, True)], 80: [(SHIFT, False)], 110: [(CTRL, False)]}
    speech = lambda i: i < 25 or 30 <= i < 80 or 90 <= i < 115   # noqa: E731  (30..80 is the cough)
    consumed = {"n": 0}
    levels = []

    def gen():
        for i in range(400):
            now["t"] = i * 0.08
            for vk, down in keys.get(i, []):
                hs.key(vk, down)
            consumed["n"] = i + 1
            yield np.full(BLOCK, 7 if speech(i) else 0, np.int16)

    cfg = {"vad_aggressiveness": 2, "end_silence_ms": 700, "start_timeout": 5.0, "max_seconds": 20,
           "max_utterance_s": 180}
    audio, _ = record_utterance(gen(), cfg, hold=hs, is_speech=lambda f: f[0] != 0, clock=clock,
                                on_level=levels.append)
    assert audio is not None
    assert consumed["n"] == 115 + 9                  # ends ~0.7 s after the last real speech
    # 25 + 25 voiced blocks plus a little kept context, but none of the 50 muted blocks' sound
    blocks = len(audio) // BLOCK
    assert blocks < 25 + 25 + 30, blocks
    assert all(v == 0 for v in levels[30:80]), "the level meter must show silence while muted"


if __name__ == "__main__":
    n = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            n += 1
            print("ok", name)
    print(n, "passed")
