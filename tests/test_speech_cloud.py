"""Cloud speech (speech/cloud.py): Groq / OpenAI transcription, OpenAI /
ElevenLabs voices, key lookup and the monthly cost estimate. Offline: every
HTTP call goes to an httpx.MockTransport, keys are obviously fake.

    .venv\\Scripts\\python tests\\test_speech_cloud.py
    .venv\\Scripts\\python tests\\test_speech_cloud.py --live   # real round trip (needs keys; costs < $0.01)
"""
import json
import os
import re
import sys
import tempfile
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import numpy as np  # noqa: E402

from heyclaude.speech import cloud  # noqa: E402
from heyclaude.speech.base import SpeechError, pcm_from_wav  # noqa: E402

httpx = cloud._httpx()
FAKE = "test-not-a-real-key-0000"
AUDIO = (np.sin(np.arange(16000) / 16000 * 2 * np.pi * 220) * 8000).astype(np.int16)   # 1 s


def _keys(provider):
    return FAKE


def _no_keys(provider):
    return None


def _fields(req) -> dict:
    """The plain (non-file) fields of a multipart body."""
    body = req.content
    return {m.group(1).decode(): m.group(2).decode()
            for m in re.finditer(rb'name="(\w+)"\r\n\r\n(.*?)\r\n--', body, re.S)}


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def _stt(handler, key_lookup=_keys, **cfg):
    return cloud.CloudStt(cfg, client=_client(handler), key_lookup=key_lookup)


def _tts(handler, key_lookup=_keys, **cfg):
    return cloud.CloudTts(cfg, client=_client(handler), key_lookup=key_lookup)


def _raises(fn, reason):
    try:
        fn()
    except SpeechError as e:
        assert str(e) == reason, f"{str(e)!r} != {reason!r}"
        assert FAKE not in str(e)
        return
    raise AssertionError(f"no SpeechError ({reason})")


# ------------------------------------------------------------------ STT

def test_groq_request():
    seen = []

    def h(req):
        seen.append(req)
        return httpx.Response(200, json={"text": " hello there "})

    stt = _stt(h)
    assert stt.label == "groq turbo" and stt.name == "cloud" and stt.probe_ok is False
    assert stt.available() == (True, "")
    assert stt.transcribe(AUDIO, prompt="Claude, Kokoro", languages=["en"]) == "hello there"
    req = seen[0]
    assert req.method == "POST" and str(req.url) == "https://api.groq.com/openai/v1/audio/transcriptions"
    assert req.headers["authorization"] == f"Bearer {FAKE}"
    f = _fields(req)
    assert f == {"model": "whisper-large-v3-turbo", "response_format": "json",
                 "prompt": "Claude, Kokoro", "language": "en"}, f
    assert b'filename="speech.wav"' in req.content and b"RIFF" in req.content
    assert abs(stt.audio_s_total - 1.0) < 1e-6 and stt.requests == 1


def test_openai_request_and_language_rules():
    seen = []

    def h(req):
        seen.append(_fields(req))
        assert str(req.url) == "https://api.openai.com/v1/audio/transcriptions"
        return httpx.Response(200, json={"text": "ok"})

    stt = _stt(h, cloud_stt_provider="openai")
    assert stt.label == "openai mini"
    stt.transcribe(AUDIO, languages=["en", "de"])          # several: no language, no failure
    stt.transcribe(AUDIO, languages=["en-US"])             # one, normalised
    stt.transcribe(AUDIO)
    assert seen[0] == {"model": "gpt-4o-mini-transcribe", "response_format": "json"}, seen[0]
    assert seen[1]["language"] == "en"
    assert "language" not in seen[2] and "prompt" not in seen[2]
    stt2 = _stt(h, cloud_stt_provider="openai", openai_stt_model="gpt-4o-transcribe")
    stt2.transcribe(AUDIO)
    assert seen[-1]["model"] == "gpt-4o-transcribe" and stt2.label == "openai 4o"


def test_empty_audio_makes_no_request():
    def h(req):
        raise AssertionError("no request expected")
    assert _stt(h).transcribe(np.zeros(0, np.int16)) == ""


def test_stt_error_mapping():
    def status(code, body=None):
        return lambda req: httpx.Response(code, json=body or {"error": {"message": "x"}})

    def raise_(exc):
        def h(req):
            raise exc("boom", request=req)
        return h

    _raises(lambda: _stt(status(200), key_lookup=_no_keys).transcribe(AUDIO), "no GROQ_API_KEY")
    assert _stt(status(200), key_lookup=_no_keys).available() == (False, "no GROQ_API_KEY")
    assert _stt(status(200), key_lookup=_no_keys, cloud_stt_provider="openai").available() == (False, "no OPENAI_API_KEY")
    _raises(lambda: _stt(raise_(httpx.ConnectError)).transcribe(AUDIO), "no network")
    _raises(lambda: _stt(raise_(httpx.ReadTimeout)).transcribe(AUDIO), "timed out")
    _raises(lambda: _stt(raise_(httpx.ConnectTimeout)).transcribe(AUDIO), "timed out")
    _raises(lambda: _stt(status(401)).transcribe(AUDIO), "key rejected")
    _raises(lambda: _stt(status(429)).transcribe(AUDIO), "rate limited")
    _raises(lambda: _stt(status(500)).transcribe(AUDIO), "groq error 500")
    _raises(lambda: _stt(status(400), cloud_stt_provider="openai").transcribe(AUDIO), "openai error 400")
    _raises(lambda: _stt(status(429, {"error": {"code": "insufficient_quota"}}), cloud_stt_provider="openai")
            .transcribe(AUDIO), "openai account out of credit")
    _raises(lambda: _stt(lambda req: httpx.Response(200, text="not json")).transcribe(AUDIO), "groq error bad reply")
    stt = _stt(status(500))
    try:
        stt.transcribe(AUDIO)
    except SpeechError:
        pass
    assert stt.audio_s_total == 0 and stt.requests == 0        # failures cost nothing


def test_unknown_provider():
    stt = cloud.CloudStt({"cloud_stt_provider": "nope"}, key_lookup=_keys)
    ok, why = stt.available()
    assert not ok and "nope" in why
    _raises(lambda: stt.transcribe(AUDIO), why)


def test_one_client_reused():
    clients = set()

    def h(req):
        return httpx.Response(200, json={"text": "x"})

    stt = _stt(h)
    for _ in range(3):
        stt.transcribe(AUDIO)
        clients.add(id(stt._http.client))
    assert len(clients) == 1
    own = cloud.CloudStt({}, key_lookup=_keys)               # lazily creates its own
    c = own._http.client
    assert c is own._http.client
    own.close()
    assert own._http._client is None


# ------------------------------------------------------------------ TTS

PCM = np.array([0, 16384, -16384, 32767, -32768], "<i2").tobytes()
PCM_F = np.array([0, 0.5, -0.5, 32767 / 32768, -1.0], np.float32)


def test_openai_tts():
    seen = []

    def h(req):
        seen.append(req)
        return httpx.Response(200, content=PCM, headers={"content-type": "audio/pcm"})

    tts = _tts(h)
    assert tts.name == "cloud" and tts.rate == 24000 and tts.pcm and tts.label == "openai mini-tts alloy"
    x = tts.synth("Hello.")
    assert x.dtype == np.float32 and np.allclose(x, PCM_F)
    req = seen[0]
    assert str(req.url) == "https://api.openai.com/v1/audio/speech"
    assert req.headers["authorization"] == f"Bearer {FAKE}"
    assert json.loads(req.content) == {"model": "gpt-4o-mini-tts", "voice": "alloy",
                                       "input": "Hello.", "response_format": "pcm"}
    assert tts.chars_total == 6 and tts.requests == 1
    tts2 = _tts(h, openai_voice="nova", openai_tts_model="tts-1", openai_tts_instructions="calm")
    tts2.synth("Hi")
    b = json.loads(seen[-1].content)
    assert b["voice"] == "nova" and b["model"] == "tts-1" and "instructions" not in b
    tts3 = _tts(h, openai_tts_instructions="calm")
    tts3.synth("Hi")
    assert json.loads(seen[-1].content)["instructions"] == "calm"


def test_elevenlabs_tts():
    seen = []

    def h(req):
        seen.append(req)
        return httpx.Response(200, content=PCM + b"\x01")      # odd trailing byte is dropped

    tts = _tts(h, cloud_tts_provider="elevenlabs")
    assert tts.label == "elevenlabs flash"
    x = tts.synth("Hi there")
    assert np.allclose(x, PCM_F)
    req = seen[0]
    assert str(req.url) == ("https://api.elevenlabs.io/v1/text-to-speech/21m00Tcm4TlvDq8ikWAM"
                            "?output_format=pcm_24000"), str(req.url)
    assert req.headers["xi-api-key"] == FAKE and "authorization" not in req.headers
    assert json.loads(req.content) == {"text": "Hi there", "model_id": "eleven_flash_v2_5"}
    _tts(h, cloud_tts_provider="elevenlabs", elevenlabs_voice="abc", elevenlabs_model="eleven_v3").synth("x")
    assert "/text-to-speech/abc?" in str(seen[-1].url) and json.loads(seen[-1].content)["model_id"] == "eleven_v3"


def test_tts_errors():
    def h(req):
        raise AssertionError("no request expected")

    _raises(lambda: _tts(h, key_lookup=_no_keys).synth("hi"), "no OPENAI_API_KEY")
    _raises(lambda: _tts(h, key_lookup=_no_keys, cloud_tts_provider="elevenlabs").synth("hi"), "no ELEVENLABS_API_KEY")
    assert _tts(h).synth("   ").size == 0
    _raises(lambda: _tts(lambda r: httpx.Response(401, json={})).synth("hi"), "key rejected")
    _raises(lambda: _tts(lambda r: httpx.Response(429, json={}), cloud_tts_provider="elevenlabs").synth("hi"),
            "rate limited")
    _raises(lambda: _tts(lambda r: httpx.Response(503, text="x"), cloud_tts_provider="elevenlabs").synth("hi"),
            "elevenlabs error 503")
    _raises(lambda: _tts(lambda r: httpx.Response(200, json={"oops": 1})).synth("hi"), "openai error bad reply")

    def boom(req):
        raise httpx.ConnectError("offline", request=req)
    _raises(lambda: _tts(boom).synth("hi"), "no network")


# ------------------------------------------------------------------ keys

def test_key_lookup_order():
    store = {"hey-claude/groq": "from-store", "hey-claude/openai": "store-openai"}
    cred = store.get
    env = {"GROQ_API_KEY": " from-env "}
    assert cloud.get_key("groq", env=env, cred=cred) == "from-env"           # env wins
    assert cloud.key_source("groq", env=env, cred=cred) == "env"
    assert cloud.get_key("openai", env=env, cred=cred) == "store-openai"
    assert cloud.key_source("openai", env=env, cred=cred) == "credential store"
    assert cloud.get_key("elevenlabs", env=env, cred=cred) is None
    assert cloud.key_source("elevenlabs", env=env, cred=cred) is None
    assert cloud.get_key("groq", env={"GROQ_API_KEY": "  "}, cred=cred) == "from-store"   # blank env ignored
    try:
        cloud.get_key("anthropic", env={}, cred=cred)
        raise AssertionError("unknown provider accepted")
    except ValueError:
        pass


def test_blob_decoding():
    assert cloud._decode_blob("abc".encode("utf-16-le")) == "abc"
    assert cloud._decode_blob("abc\x00".encode("utf-16-le")) == "abc"
    assert cloud._decode_blob(b"gsk_abc123") == "gsk_abc123"
    assert cloud._decode_blob(b"") is None and cloud._decode_blob(None) is None


def test_set_delete_injected():
    store = {}
    cloud.set_key("elevenlabs", "  k1  ", write=lambda t, s: store.__setitem__(t, s))
    assert store == {"hey-claude/elevenlabs": "k1"}
    assert cloud.delete_key("elevenlabs", delete=lambda t: store.pop(t, None) is not None) is True
    try:
        cloud.set_key("groq", "  ", write=lambda t, s: None)
        raise AssertionError("empty key accepted")
    except ValueError:
        pass


def test_credential_manager_roundtrip():
    """The real Windows Credential Manager, under a throwaway target."""
    try:
        import win32cred  # noqa: F401
    except ImportError:
        print("  (skipped: no pywin32)")
        return
    target = f"hey-claude/selftest-{os.getpid()}"
    try:
        cloud.cred_write(target, FAKE)
        assert cloud.cred_read(target) == FAKE                  # stored as UTF-16-LE, read back
    finally:
        assert cloud.cred_delete(target) is True
    assert cloud.cred_read(target) is None
    assert cloud.cred_delete(target) is False


# ------------------------------------------------------------------ cost

def _csv(rows, header):
    fd, path = tempfile.mkstemp(suffix=".csv")
    with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
        f.write(header + "\n")
        for r in rows:
            f.write(r + "\n")
    return path


def test_monthly_cost():
    header = "when,route,audio_s,stt,source,text,reply"
    rows = [
        '2026-11-01 10:00:00,fast,30,0.2,,what time is it,"It\'s ten, about."',   # 30 s spoken
        "2026-11-03 10:00:00,fast,30,0.2,wake,hi,Hello",                          # 30 s spoken
        "2026-11-05 10:00:00,fast,4,0.2,hold,x,Yo",                               # 4 s (groq bills 10)
        "2026-11-05 11:00:00,fast,0,0.0,typed,typed one,Typed reply",             # typed: no STT
        "2026-11-05 12:00:00,fast,0,0.0,,nothing,",                               # audio_s 0: skipped
        "2026-11-05 13:00:00,fast,abc,0.0,,bad audio,",                           # bad number
        "not a date,fast,60,0.2,,bad row,",                                       # bad when
        "2026-11-06 10:00:00,fast",                                               # short row
        "2026-09-01 10:00:00,fast,600,0.2,,too old,",                             # outside 30 days
    ]
    path = _csv(rows, header)
    now = datetime(2026, 11, 11, 10, 0, 0)
    try:
        c = cloud.monthly_cost(path, "openai", "gpt-4o-mini-transcribe", now=now)
        # spoken: 64 s over a 5-day log (11-01 10:00 .. 11-06 10:00) -> x6
        assert c["logged_requests"] == 3 and abs(c["logged_minutes"] - 64 / 60) < 1e-9, c
        assert abs(c["span_days"] - 5) < 1e-9
        assert abs(c["minutes"] - 64 / 60 * 6) < 1e-9 and c["requests"] == 18
        assert abs(c["usd"] - 64 / 60 * 6 * 0.003) < 1e-9 and c["priced"]
        g = cloud.monthly_cost(path, "groq", "whisper-large-v3-turbo", now=now)
        assert abs(g["usd"] - 70 / 60 * 6 * 0.04 / 60) < 1e-9              # 30 + 30 + 10 billed
        t = cloud.monthly_cost(path, "groq", "whisper-large-v3-turbo", now=now,
                               tts_provider="elevenlabs", tts_model="eleven_flash_v2_5")
        chars = len("It's ten, about.") + len("Hello") + len("Yo") + len("Typed reply")
        assert t["tts_chars"] == chars * 6
        assert abs(t["tts_usd"] - chars * 6 / 1000 * 0.05) < 1e-9
        assert abs(t["usd"] - (g["usd"] + t["tts_usd"])) < 1e-12
        o = cloud.monthly_cost(path, "groq", "whisper-large-v3-turbo", now=now,
                               tts_provider="openai", tts_model="gpt-4o-mini-tts")
        assert abs(o["tts_usd"] - chars * 6 / 900 * 0.015) < 1e-9
        w = cloud.monthly_cost(path, "openai", "gpt-4o-mini-transcribe", now=now, days=7)   # 11-04 on
        assert w["logged_requests"] == 1
        line = cloud.cost_line(path, "openai", "gpt-4o-mini-transcribe", now=now)
        assert line == "about $0.02/month at your usage (6 min of speech)", line
        assert cloud.cost_line(path, "groq", "whisper-large-v3-turbo", now=now) == \
            "under $0.01/month at your usage (6 min of speech)"
        assert cloud.cost_line(path, "groq", "nope", now=now) == "no price known for groq nope"
        assert "voice included" in cloud.cost_line(path, "groq", "whisper-large-v3-turbo", now=now,
                                                   tts_provider="openai", tts_model="gpt-4o-mini-tts")
    finally:
        os.remove(path)


def test_monthly_cost_edge_files():
    now = datetime(2026, 11, 11, 10, 0, 0)
    missing = os.path.join(tempfile.gettempdir(), "hey-claude-no-such-latency.csv")
    c = cloud.monthly_cost(missing, "groq", "whisper-large-v3-turbo", now=now)
    assert c["usd"] == 0 and c["requests"] == 0
    assert cloud.cost_line(missing, "groq", "whisper-large-v3-turbo", now=now) == "no spoken requests logged yet"
    # an older file without source/reply columns, one row in one day: x30
    old = _csv(["2026-11-10 09:00:00,12", "2026-11-10 09:30:00,48"], "when,audio_s")
    try:
        c = cloud.monthly_cost([old, missing], "openai", "whisper-1", now=now)
        assert c["span_days"] == 1 and abs(c["minutes"] - 30) < 1e-9 and c["requests"] == 60
        assert abs(c["usd"] - 30 * 0.006) < 1e-9
    finally:
        os.remove(old)


# ------------------------------------------------------------------ live (opt-in)

def _sapi_wav() -> np.ndarray:
    """"What's the weather like in Paris tomorrow" via SAPI, as 16 kHz int16."""
    import win32com.client
    path = os.path.join(tempfile.gettempdir(), f"hey-claude-cloud-live-{os.getpid()}.wav")
    fmt = win32com.client.Dispatch("SAPI.SpAudioFormat")
    fmt.Type = 18                         # SAFT16kHz16BitMono
    stream = win32com.client.Dispatch("SAPI.SpFileStream")
    stream.Format = fmt
    stream.Open(path, 3)                  # SSFMCreateForWrite
    voice = win32com.client.Dispatch("SAPI.SpVoice")
    voice.AudioOutputStream = stream
    voice.Speak("What's the weather like in Paris tomorrow?")
    stream.Close()
    with open(path, "rb") as f:
        x, rate = pcm_from_wav(f.read())
    os.remove(path)
    assert rate == 16000
    return (x * 32767).astype(np.int16)


def live():
    have = {p: cloud.key_source(p) for p in cloud.PROVIDERS}
    print("keys:", {p: (s or "none") for p, s in have.items()})
    if not any(have.values()):
        print("live: no cloud keys in env or credential store; nothing to test")
        return
    audio = _sapi_wav()
    for prov in ("groq", "openai"):
        if have[prov]:
            stt = cloud.CloudStt({"cloud_stt_provider": prov})
            text = stt.transcribe(audio, prompt="Paris", languages=["en"])
            print(f"live stt {stt.label}: {text!r} in {stt.last_s:.2f}s")
            assert "paris" in text.lower()
            stt.transcribe(audio)
            print(f"live stt {stt.label} (warm connection): {stt.last_s:.2f}s")
            stt.close()
    for prov in ("openai", "elevenlabs"):
        if have[prov]:
            tts = cloud.CloudTts({"cloud_tts_provider": prov})
            x = tts.synth("Tomorrow in Paris: sunny, eighteen degrees.")
            print(f"live tts {tts.label}: {len(x) / tts.rate:.2f}s of audio in {tts.last_s:.2f}s, peak {abs(x).max():.2f}")
            assert len(x) > tts.rate and 0.01 < abs(x).max() <= 1.0
            tts.close()


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"{len(tests)} passed")
    if "--live" in sys.argv:
        live()
