"""Windows speech backends (speech/windows.py) with the WinRT and SAPI layers faked.
No mic, network or speech engines needed.

    .venv\\Scripts\\python tests\\test_speech_windows.py
    .venv\\Scripts\\python tests\\test_speech_windows.py --live    # real WinRT / SAPI on this PC
"""
import os
import struct
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import numpy as np  # noqa: E402

from heyclaude.speech import windows as W  # noqa: E402
from heyclaude.speech.base import SpeechError, wav_bytes  # noqa: E402

AUDIO = np.zeros(16000, np.int16)


# -- fakes -----------------------------------------------------------------------------
class FakeLive:
    """A WinrtLive stand-in. `during`: results emitted right after start; `pending`:
    results StopAsync delivers; then on_done(`status`)."""

    def __init__(self, during=(), pending=(), hyp=None, status="success", start_error=None,
                 start_delay=0.0):
        self.calls = []
        self.during, self.pending, self.hyp, self.status = list(during), list(pending), hyp, status
        self.start_error, self.start_delay = start_error, start_delay
        self.sink = None

    def start(self, on_result, on_hypothesis, on_done):
        self.calls.append("start")
        if self.start_delay:
            time.sleep(self.start_delay)
        if self.start_error is not None:
            raise self.start_error
        self.sink = (on_result, on_hypothesis, on_done)
        for t in self.during:
            on_result(t)
        if self.hyp:
            on_hypothesis(self.hyp)

    def stop(self):
        self.calls.append("stop")
        r, _h, d = self.sink
        for t in self.pending:
            r(t)
        threading.Timer(0.02, d, args=(self.status,)).start()   # Completed comes a bit later

    def cancel(self):
        self.calls.append("cancel")
        self.sink = None


class FakeBuffer:
    def __init__(self, text="from the buffer", error=None):
        self.text, self.error, self.calls = text, error, 0

    def recognize(self, audio):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.text


def _stt(live=None, buffer=None, privacy=True, sapi=True, **kw):
    live = live or FakeLive()
    buffer = buffer or FakeBuffer()
    made = {"live": 0}

    def lf(lang):
        made["live"] += 1
        return live
    stt = W.WindowsStt({"windows_language": "en-US"}, live_factory=lf, buffer_factory=lambda lang: buffer,
                       privacy=lambda: privacy, sapi_check=lambda lang: sapi, **kw)
    return stt, live, buffer, made


def _started(stt, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        s = stt._session
        if s is None or s.started.is_set():
            return
        time.sleep(0.005)


# -- speech-to-text --------------------------------------------------------------------
def test_live_session_joins_results_in_order():
    stt, live, buf, _ = _stt(FakeLive(during=["Hello"], pending=["world."]))
    stt.begin(live=True)
    _started(stt)
    assert stt.transcribe(AUDIO) == "Hello world."
    assert live.calls == ["start", "stop"]
    assert buf.calls == 0
    assert stt.label == "winrt online" and stt.note == ""
    assert "stop_s" in stt.timings and "start_s" in stt.timings


def test_recognizer_kept_between_utterances():
    stt, live, _, made = _stt(FakeLive(pending=["one"]))
    for _ in range(3):
        stt.begin(live=True)
        _started(stt)
        assert stt.transcribe(AUDIO) == "one"
    assert made["live"] == 1
    assert live.calls == ["start", "stop"] * 3


def test_begin_returns_before_a_slow_start():
    stt, live, _, _ = _stt(FakeLive(pending=["ok"], start_delay=0.3))
    t = time.perf_counter()
    stt.begin(live=True)
    assert time.perf_counter() - t < 0.1
    assert stt.transcribe(AUDIO) == "ok"          # transcribe waits for the start


def test_hypothesis_when_no_final_result():
    stt, _, _, _ = _stt(FakeLive(hyp="half a sentence"))
    stt.begin(live=True)
    _started(stt)
    assert stt.transcribe(AUDIO) == "half a sentence"


def test_silence_gives_empty_text_not_buffer():
    stt, _, buf, _ = _stt(FakeLive())
    stt.begin(live=True)
    _started(stt)
    assert stt.transcribe(AUDIO) == ""
    assert buf.calls == 0 and stt.label == "winrt online"


def test_cancel_discards_and_next_transcribe_uses_buffer():
    stt, live, buf, _ = _stt(FakeLive(during=["drop me"]))
    stt.begin(live=True)
    _started(stt)
    stt.cancel()
    assert live.calls == ["start", "cancel"]
    assert stt.transcribe(AUDIO) == "from the buffer"
    assert buf.calls == 1 and stt.label == "sapi"


def test_cancel_during_start_cancels_once_started():
    stt, live, _, _ = _stt(FakeLive(start_delay=0.15))
    stt.begin(live=True)
    stt.cancel()
    time.sleep(0.4)
    assert live.calls == ["start", "cancel"]


def test_begin_twice_cancels_the_first():
    stt, live, _, _ = _stt(FakeLive(pending=["second"]))
    stt.begin(live=True)
    _started(stt)
    stt.begin(live=True)
    _started(stt)
    assert stt.transcribe(AUDIO) == "second"
    assert live.calls == ["start", "cancel", "start", "stop"]


def test_not_live_uses_buffer():
    stt, live, buf, made = _stt()
    stt.begin(live=False)
    assert stt.transcribe(AUDIO) == "from the buffer"
    assert made["live"] == 0 and live.calls == [] and buf.calls == 1
    assert stt.label == "sapi"


def test_no_begin_uses_buffer():
    stt, _, buf, _ = _stt()
    assert stt.transcribe(AUDIO) == "from the buffer"
    assert buf.calls == 1


def test_privacy_off_notes_and_uses_buffer():
    stt, live, buf, made = _stt(privacy=False)
    stt.begin(live=True)                          # must not crash or start anything
    assert made["live"] == 0
    assert stt.note == W.PRIVACY_OFF
    assert stt.transcribe(AUDIO) == "from the buffer"
    assert stt.label == "sapi"


def test_available_with_privacy_off_needs_sapi():
    assert _stt(privacy=False, sapi=True)[0].available() == (True, W.PRIVACY_OFF)
    ok, why = _stt(privacy=False, sapi=False)[0].available()
    assert not ok and why == W.PRIVACY_OFF
    assert _stt(privacy=True, sapi=True)[0].available() == (True, "")


def test_privacy_hresult_from_start_becomes_note():
    err = OSError(None, "The speech privacy policy was not accepted", None, -2147199735)
    stt, _, buf, _ = _stt(FakeLive(start_error=err), privacy=None)
    stt.begin(live=True)
    _started(stt)
    assert stt.transcribe(AUDIO) == "from the buffer"
    assert stt.note == W.PRIVACY_OFF and buf.calls == 1


def test_missing_recognizer_is_not_retried():
    stt, live, _, _ = _stt(FakeLive(start_error=SpeechError("no en-US speech recognizer")))
    stt.begin(live=True)
    _started(stt)
    stt.transcribe(AUDIO)
    stt.begin(live=True)
    assert stt._session is None
    assert live.calls == ["start"]
    assert stt.note == "no en-US speech recognizer"


def test_session_failing_midway_falls_back_to_buffer():
    stt, _, buf, _ = _stt(FakeLive(status="network_failure"))
    stt.begin(live=True)
    _started(stt)
    assert stt.transcribe(AUDIO) == "from the buffer"
    assert stt.note == "no network for online speech recognition" and buf.calls == 1


def test_failure_with_some_text_keeps_the_text():
    stt, _, buf, _ = _stt(FakeLive(during=["got this"], status="network_failure"))
    stt.begin(live=True)
    _started(stt)
    assert stt.transcribe(AUDIO) == "got this" and buf.calls == 0


def test_slow_start_is_given_up_and_cancelled_later():
    stt, live, buf, _ = _stt(FakeLive(start_delay=0.3), start_wait=0.05)
    stt.begin(live=True)
    assert stt.transcribe(AUDIO) == "from the buffer"
    assert "too long" in stt.note
    time.sleep(0.45)
    assert live.calls == ["start", "cancel"]


def test_buffer_failure_raises_speecherror():
    stt, _, _, _ = _stt(buffer=FakeBuffer(error=RuntimeError("COM exploded")))
    try:
        stt.transcribe(AUDIO)
    except SpeechError as e:
        assert "COM exploded" in str(e)
    else:
        raise AssertionError("expected SpeechError")


def test_buffer_speecherror_passes_through():
    stt, _, _, _ = _stt(buffer=FakeBuffer(error=SpeechError("no en-US speech recognizer")))
    try:
        stt.transcribe(AUDIO)
    except SpeechError as e:
        assert str(e) == "no en-US speech recognizer"
    else:
        raise AssertionError("expected SpeechError")


def test_empty_buffer_needs_no_com():
    assert W.SapiBuffer().recognize(np.zeros(0, np.int16)) == ""


def test_privacy_reader_returns_bool_or_none():
    assert W.online_speech_enabled() in (True, False, None)


def test_import_is_lazy():
    code = ("import sys; import heyclaude.speech.windows; "
            "bad = [m for m in sys.modules if m.startswith(('winrt', 'win32com', 'pythoncom'))]; "
            "assert not bad, bad")
    root = os.path.join(os.path.dirname(__file__), "..")
    subprocess.run([sys.executable, "-c", code], cwd=root, check=True)


# -- WAV parsing -----------------------------------------------------------------------
def _riff(fmt: bytes, data: bytes) -> bytes:
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(data)) + data
    return b"RIFF" + struct.pack("<I", len(body)) + body


_GUID_TAIL = bytes.fromhex("000000001000800000aa00389b71")      # KSDATAFORMAT_SUBTYPE_* after the tag


def _extensible(tag: int, ch: int, rate: int, bits: int) -> bytes:
    align = ch * bits // 8
    return struct.pack("<HHIIHHHHI", 0xFFFE, ch, rate, rate * align, align, bits, 22, bits, 0x4) \
        + struct.pack("<H", tag) + _GUID_TAIL


def test_parse_plain_wav():
    x = (np.sin(np.arange(1600) / 5) * 12000).astype(np.int16)
    y, rate = W.parse_wav(wav_bytes(x, 22050))
    assert rate == 22050 and y.dtype == np.float32 and len(y) == 1600
    assert np.allclose(y, x / 32768.0, atol=1e-6)


def test_parse_winrt_style_wav_with_cbsize():
    """WinRT's own WAVs carry an 18-byte fmt chunk (WAVEFORMATEX with cbSize 0)."""
    x = np.array([0, 16384, -16384, 32767], np.int16)
    fmt = struct.pack("<HHIIHHH", 1, 1, 16000, 32000, 2, 16, 0)
    y, rate = W.parse_wav(_riff(fmt, x.tobytes()))
    assert rate == 16000 and np.allclose(y, x / 32768.0, atol=1e-6)


def test_parse_extensible_int16():
    x = np.array([0, 8192, -8192, 32767, -32768], np.int16)
    data = _riff(_extensible(1, 1, 24000, 16), x.tobytes())
    for parse in (W.parse_wav, W._parse_riff):
        y, rate = parse(data)
        assert rate == 24000 and len(y) == 5
        assert np.allclose(y, x / 32768.0, atol=1e-6), y


def test_parse_extensible_float_stereo():
    left = np.array([0.5, -0.5, 0.25], np.float32)
    right = np.array([0.5, 0.5, -0.25], np.float32)
    inter = np.stack([left, right], axis=1).reshape(-1).astype("<f4")
    data = _riff(_extensible(3, 2, 48000, 32), inter.tobytes())
    y, rate = W.parse_wav(data)
    assert rate == 48000 and np.allclose(y, (left + right) / 2)


def test_parse_skips_unknown_chunks():
    x = np.array([100, -100], np.int16)
    fmt = struct.pack("<HHIIHH", 1, 1, 16000, 32000, 2, 16)
    body = (b"WAVE" + b"LIST" + struct.pack("<I", 3) + b"abc\x00" + b"fmt " + struct.pack("<I", 16) + fmt
            + b"data" + struct.pack("<I", 4) + x.tobytes())
    y, rate = W._parse_riff(b"RIFF" + struct.pack("<I", len(body)) + body)
    assert rate == 16000 and np.allclose(y, x / 32768.0)


def test_parse_garbage_raises_speecherror():
    for bad in (b"", b"not a wav at all", b"RIFF\x00\x00\x00\x00WAVE"):
        try:
            W.parse_wav(bad)
        except SpeechError:
            continue
        raise AssertionError(f"expected SpeechError for {bad!r}")


# -- text-to-speech --------------------------------------------------------------------
class FakeSynth:
    def __init__(self, names=("Microsoft David", "Microsoft Zira", "Microsoft Mark"), rate=16000, error=None):
        self.names, self.rate, self.error = list(names), rate, error
        self.chosen = None
        self.texts = []

    def voices(self):
        return list(self.names)

    def default_voice(self):
        return self.names[0]

    def set_voice(self, name):
        self.chosen = name

    def synth_wav(self, text):
        if self.error is not None:
            raise self.error
        self.texts.append(text)
        x = (np.sin(np.arange(800) / 3) * 9000).astype(np.int16)
        return wav_bytes(x, self.rate)


def test_tts_voice_by_substring():
    fake = FakeSynth()
    tts = W.WindowsTts({"windows_voice": "zira"}, synth_factory=lambda: fake)
    tts.synth("hi")
    assert fake.chosen == "Microsoft Zira" and tts.voice == "Microsoft Zira"
    assert tts.label == "windows zira"
    assert tts.voices() == fake.names


def test_tts_default_voice_when_no_match():
    fake = FakeSynth()
    tts = W.WindowsTts({"windows_voice": "hedda"}, synth_factory=lambda: fake)
    tts.synth("hi")
    assert fake.chosen is None and tts.voice == "Microsoft David"
    assert "hedda" in tts.note


def test_tts_rate_from_wav_and_float_output():
    tts = W.WindowsTts({}, synth_factory=lambda: FakeSynth(rate=22050))
    x = tts.synth("Hello there.")
    assert tts.rate == 22050 and x.dtype == np.float32 and len(x) == 800
    assert float(np.abs(x).max()) <= 1.0
    assert tts.pcm is True and tts.name == "windows"


def test_tts_failure_raises_speecherror():
    tts = W.WindowsTts({}, synth_factory=lambda: FakeSynth(error=OSError("boom")))
    try:
        tts.synth("hi")
    except SpeechError as e:
        assert "boom" in str(e)
    else:
        raise AssertionError("expected SpeechError")


def test_tts_factory_failure_raises_speecherror():
    def broken():
        raise ImportError("no winrt")
    try:
        W.WindowsTts({}, synth_factory=broken).synth("hi")
    except SpeechError:
        pass
    else:
        raise AssertionError("expected SpeechError")


def test_tts_empty_text_is_silent():
    fake = FakeSynth()
    tts = W.WindowsTts({}, synth_factory=lambda: fake)
    assert len(tts.synth("  ")) == 0 and fake.texts == []


def test_tts_one_synth_from_many_threads():
    made = []

    def factory():
        made.append(FakeSynth())
        return made[-1]
    tts = W.WindowsTts({}, synth_factory=factory)
    errs = []

    def run(i):
        try:
            for _ in range(5):
                assert len(tts.synth(f"sentence {i}")) == 800
        except BaseException as e:     # noqa: BLE001
            errs.append(e)
    ts = [threading.Thread(target=run, args=(i,)) for i in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errs, errs
    assert len(made) == 1 and len(made[0].texts) == 20


# -- live, on this PC only (not CI) ------------------------------------------------------
def live():
    import wave
    print("online speech recognition switch:", W.online_speech_enabled())
    print("SAPI en-US recognizer installed:", W.sapi_recognizer_present("en-US"))

    tts = W.WindowsTts({"windows_voice": "zira"})
    print("voices:", tts.voices(), "->", tts.voice)
    sentence = "Please open the weather forecast for tomorrow in London."
    for i in range(3):
        box = {}
        th = threading.Thread(target=lambda: box.update(x=tts.synth(sentence)))   # a non-UI thread
        th.start()
        th.join()
        x = box["x"]
        print(f"tts #{i + 1}: {tts.timings['synth_s'] * 1000:.0f} ms for {len(x) / tts.rate:.2f} s of audio "
              f"at {tts.rate} Hz, peak {np.abs(x).max():.2f}")
    assert len(x) > tts.rate and np.abs(x).max() > 0.05

    if tts.rate != 16000:
        from scipy.signal import resample_poly
        x = resample_poly(x, 16000, tts.rate)
    pcm = (np.clip(x, -1, 1) * 32767).astype(np.int16)

    stt = W.WindowsStt({"windows_language": "en-US"})
    print("stt available:", stt.available())
    t = time.perf_counter()
    stt.prepare(live=False)
    print(f"SAPI dictation load: {time.perf_counter() - t:.2f} s")
    for i in range(2):
        text = stt.transcribe(pcm)
        print(f"sapi #{i + 1}: {stt.timings['buffer_s'] * 1000:.0f} ms ({stt.label}): {text!r}")
    low = text.lower()
    assert sum(w in low for w in ("weather", "forecast", "tomorrow", "london")) >= 3, text

    samples = os.path.join(os.path.dirname(__file__), "..", "samples")
    for f in sorted(os.listdir(samples)) if os.path.isdir(samples) else []:
        if f.endswith(".wav"):
            with wave.open(os.path.join(samples, f)) as w:
                a = np.frombuffer(w.readframes(w.getnframes()), np.int16)
            print(f"sapi samples/{f}: {stt.transcribe(a)!r} ({stt.timings['buffer_s'] * 1000:.0f} ms)")

    # begin(live=True) with the real privacy switch: off here -> a note and the buffer path
    stt.begin(live=True)
    time.sleep(0.5)
    text = stt.transcribe(pcm)
    print(f"begin(live) with the real switch: label {stt.label!r}, note {stt.note!r}, text {text!r}")

    # Pretend we can't read the switch: WinRT start must fail cleanly (privacy HRESULT) -> buffer
    probe = W.WindowsStt({}, privacy=lambda: None)
    probe.begin(live=True)
    text = probe.transcribe(pcm)
    print(f"forced WinRT dictation start: label {probe.label!r}, note {probe.note!r}")

    # The session mechanics (start / stop / Completed / reuse) with an offline list
    # grammar, which needs no privacy consent: proves start + stop work and silence -> "".
    def list_constraint():
        from winrt.windows.media.speechrecognition import SpeechRecognitionListConstraint
        return SpeechRecognitionListConstraint(["hey claude test phrase"], "list")
    mech = W.WindowsStt({}, live_factory=lambda lang: W.WinrtLive(lang, constraint=list_constraint),
                        privacy=lambda: True)
    t = time.perf_counter()
    mech.prepare(live=True)
    print(f"WinRT recognizer create + compile: {time.perf_counter() - t:.2f} s")
    for i in range(3):
        mech.begin(live=True)
        time.sleep(1.0)
        text = mech.transcribe(np.zeros(16000, np.int16))
        print(f"winrt session #{i + 1}: start {mech.timings['start_s'] * 1000:.0f} ms, "
              f"stop->final {mech.timings['stop_s'] * 1000:.0f} ms, label {mech.label!r}, text {text!r}")
        assert mech.label == "winrt online" and text == "", (mech.label, mech.note, text)
    mech.begin(live=True)
    time.sleep(0.5)
    mech.cancel()
    time.sleep(0.3)
    mech.begin(live=True)
    time.sleep(0.5)
    assert mech.transcribe(np.zeros(16000, np.int16)) == "" and mech.label == "winrt online"
    print("winrt cancel then a new session: ok")
    for o in (mech, probe, stt, tts):
        o.close()
    print("live ok")


if __name__ == "__main__":
    n = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            n += 1
            print("ok", name)
    print(n, "passed")
    if "--live" in sys.argv:
        live()
