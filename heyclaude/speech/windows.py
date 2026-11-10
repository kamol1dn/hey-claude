"""Windows' own speech engines (M4c): WinRT recognition and synthesis, SAPI fallback.

Speech-to-text, WindowsStt
    Live path ("winrt online"): WinRT SpeechRecognizer with the online dictation
    topic constraint, run as a ContinuousRecognitionSession. WinRT only listens to
    the system default microphone itself (it can't be fed a buffer), so begin(live=True)
    starts the session alongside hey-claude's own capture of the same mic (shared
    mode) and transcribe() stops it and returns what it heard. Needs Settings >
    Privacy & security > Speech > "Online speech recognition" on, and a network.
    Buffer path ("sapi"): SAPI's in-process desktop recognizer (Microsoft Speech
    Recognizer 8.0) with a dictation grammar, fed the recorded buffer through an
    SpMemoryStream. Offline, works with the privacy switch off; less accurate.
    It runs whenever the live path wasn't used: simulated audio, begin(live=False),
    the switch off, the session failing to start or failing mid-way.

Text-to-speech, WindowsTts
    WinRT SpeechSynthesizer -> WAV stream -> float32 PCM (played by tts.Player, so
    echo cancellation and barge-in keep working). SAPI as a further fallback lives
    in tts._Sapi.

Threads: every WinRT coroutine runs on one persistent asyncio loop thread (WinRT
completions and events need no message pump there), every SAPI COM object lives on
one persistent CoInitialized thread that pumps messages while it recognises. So the
public methods may be called from any thread, repeatedly. All winrt / win32com
imports are lazy: importing this module costs nothing and works without them.
"""
from __future__ import annotations

import asyncio
import struct
import threading
import time

import numpy as np

from .base import RATE, SpeechError, SttBackend, TtsBackend, pcm_from_wav

PRIVACY_OFF = "online speech recognition is off (Settings > Privacy > Speech)"
_PRIVACY_KEY = r"Software\Microsoft\Speech_OneCore\Settings\OnlineSpeechPrivacy"
_HR_PRIVACY = 0x80045509          # SPERR_SPEECH_PRIVACY_POLICY_NOT_ACCEPTED
_SAPI_FORMAT_16K_MONO = 18        # SAFT16kHz16BitMono

# ContinuousRecognitionSession.Completed statuses -> a reason a person can read.
# success / user_canceled / timeout_exceeded (silence) are normal ends.
_DONE_REASONS = {
    "network_failure": "no network for online speech recognition",
    "audio_quality_failure": "Windows speech couldn't hear the microphone clearly",
    "microphone_unavailable": "the microphone isn't available to Windows speech",
    "topic_language_not_supported": "the dictation language isn't supported",
    "grammar_language_mismatch": "the dictation language isn't supported",
    "grammar_compilation_failure": "Windows speech couldn't set up dictation",
    "pause_limit_exceeded": "Windows speech stopped (pause limit)",
    "unknown": "Windows speech recognition failed",
}
_DONE_OK = {"success", "user_canceled", "timeout_exceeded"}


# -- the privacy switch --------------------------------------------------------------
def online_speech_enabled() -> bool | None:
    """Settings > Privacy & security > Speech > "Online speech recognition".

    HKCU\\Software\\Microsoft\\Speech_OneCore\\Settings\\OnlineSpeechPrivacy,
    HasAccepted (DWORD): 1 on, 0 off. None when it can't tell (not Windows, never set)."""
    try:
        import winreg
    except ImportError:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _PRIVACY_KEY) as k:
            v, _ = winreg.QueryValueEx(k, "HasAccepted")
        return bool(int(v))
    except (OSError, ValueError, TypeError):
        return None


def sapi_recognizer_present(language: str = "en-US") -> bool:
    """A cheap registry check: is a SAPI desktop recognizer for `language` installed?"""
    lcid = _lcid(language)
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Speech\Recognizers\Tokens") as k:
            i = 0
            while True:
                try:
                    name = winreg.EnumKey(k, i)
                except OSError:
                    return False
                i += 1
                try:
                    with winreg.OpenKey(k, name + r"\Attributes") as a:
                        langs, _ = winreg.QueryValueEx(a, "Language")
                except OSError:
                    continue
                codes = {c.strip().lower() for c in str(langs).split(";")}
                if lcid is None or f"{lcid:x}" in codes:
                    return True
    except (ImportError, OSError):
        return False


def _lcid(language: str) -> int | None:
    """"en-US" -> 0x409 (SAPI's recognizer attribute is the LCID in hex)."""
    try:
        import ctypes
        n = ctypes.windll.kernel32.LocaleNameToLCID(language, 0)
        return n or None
    except (AttributeError, OSError):
        return {"en-us": 0x409, "en-gb": 0x809}.get(language.lower())


def _hresult(e: BaseException) -> int | None:
    for attr in ("winerror", "hresult"):
        v = getattr(e, attr, None)
        if isinstance(v, int):
            return v & 0xFFFFFFFF
    args = getattr(e, "args", ())
    if args and isinstance(args[0], int):
        return args[0] & 0xFFFFFFFF
    return None


def _speech_error(e: BaseException, what: str) -> SpeechError:
    if isinstance(e, SpeechError):
        return e
    if _hresult(e) == _HR_PRIVACY:
        return SpeechError(PRIVACY_OFF)
    msg = str(e).strip().splitlines()[0][:120] if str(e).strip() else type(e).__name__
    return SpeechError(f"{what}: {msg}")


# -- threads that own the Windows objects ----------------------------------------------
class _Loop:
    """One asyncio loop on a daemon thread for every WinRT call of this process."""

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True, name="winrt-speech")
        self.thread.start()

    def submit(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def run(self, coro, timeout: float, what: str = "Windows speech"):
        if threading.current_thread() is self.thread:
            raise RuntimeError("_Loop.run called from its own thread")
        fut = self.submit(coro)
        try:
            return fut.result(timeout)
        except TimeoutError:
            fut.cancel()
            raise SpeechError(f"{what} didn't answer in time") from None


class _ComThread:
    """One persistent thread, CoInitialized (STA) once, that owns every SAPI object."""

    def __init__(self):
        from concurrent.futures import ThreadPoolExecutor

        def init():
            import pythoncom
            pythoncom.CoInitialize()
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sapi-speech", initializer=init)

    def call(self, fn, *args, timeout: float = 30.0):
        from concurrent.futures import TimeoutError as FutTimeout
        try:
            return self.pool.submit(fn, *args).result(timeout)
        except FutTimeout:
            raise SpeechError("Windows speech didn't answer in time") from None


_shared: dict = {}
_shared_lock = threading.Lock()


def _loop() -> _Loop:
    with _shared_lock:
        if "loop" not in _shared:
            _shared["loop"] = _Loop()
        return _shared["loop"]


def _com() -> _ComThread:
    with _shared_lock:
        if "com" not in _shared:
            _shared["com"] = _ComThread()
        return _shared["com"]


# -- WinRT live recognition (the real layer; tests inject a fake) ---------------------
class WinrtLive:
    """A WinRT SpeechRecognizer with the dictation topic compiled once and its
    ContinuousRecognitionSession reused for every utterance.

    start(on_result, on_hypothesis, on_done) blocks until the session runs (or
    raises SpeechError); results then arrive on WinRT threads: on_result(text) per
    finished phrase, on_hypothesis(text) for the running guess, on_done(status)
    once when the session ends ("success", "network_failure", ...).
    stop() blocks for StopAsync, which delivers the pending results, then on_done.
    cancel() drops the session without results and doesn't block."""

    def __init__(self, language: str = "en-US", max_seconds: float = 300.0, constraint=None):
        self.language = language
        self.max_seconds = max_seconds
        self._constraint = constraint      # for the --live test: an offline list grammar
        self._rec = None
        self._sess = None
        self._lock = None
        self._sink = None

    async def _ensure(self):
        if self._rec is not None:
            return
        from winrt.windows.globalization import Language
        from winrt.windows.media.speechrecognition import (
            SpeechRecognitionResultStatus, SpeechRecognitionScenario,
            SpeechRecognitionTopicConstraint, SpeechRecognizer)
        tags = [lang.language_tag.lower() for lang in SpeechRecognizer.supported_topic_languages]
        if self.language.lower() not in tags:
            raise SpeechError(f"no {self.language} speech recognizer")
        rec = SpeechRecognizer(Language(self.language))
        rec.constraints.append(self._constraint() if self._constraint else
                               SpeechRecognitionTopicConstraint(SpeechRecognitionScenario.DICTATION, "dictation"))
        res = await rec.compile_constraints_async()
        if res.status != SpeechRecognitionResultStatus.SUCCESS:
            raise SpeechError(_DONE_REASONS.get(_status_name(res.status), "Windows speech couldn't set up dictation"))
        sess = rec.continuous_recognition_session
        try:
            import datetime
            sess.auto_stop_silence_timeout = datetime.timedelta(seconds=self.max_seconds)
        except Exception:               # noqa: BLE001 - the default (a few seconds of silence) also works
            pass
        sess.add_result_generated(self._on_result)
        sess.add_completed(self._on_completed)
        rec.add_hypothesis_generated(self._on_hypothesis)
        self._rec, self._sess = rec, sess

    # WinRT event handlers (WinRT thread pool)
    def _on_result(self, _sess, args):
        sink = self._sink
        if sink is None:
            return
        r = args.result
        if _status_name(r.status) != "success" or _status_name(r.confidence) == "rejected":
            return
        if r.text:
            sink[0](r.text)

    def _on_hypothesis(self, _rec, args):
        sink = self._sink
        if sink is not None and args.hypothesis.text:
            sink[1](args.hypothesis.text)

    def _on_completed(self, _sess, args):
        sink = self._sink
        if sink is not None:
            sink[2](_status_name(args.status))

    def _alock(self):
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    def prepare(self, timeout: float = 15.0):
        """Create the recognizer and compile dictation now (blocking, ~0.5-1 s once)."""
        async def go():
            async with self._alock():
                await self._ensure()
        try:
            _loop().run(go(), timeout)
        except Exception as e:          # noqa: BLE001
            raise _speech_error(e, "Windows speech recognition failed") from e

    def start(self, on_result, on_hypothesis, on_done, timeout: float = 15.0):
        async def go():
            async with self._alock():
                await self._ensure()
                self._sink = (on_result, on_hypothesis, on_done)
                try:
                    await self._sess.start_async()
                except BaseException:
                    self._sink = None
                    raise
        try:
            _loop().run(go(), timeout)
        except Exception as e:          # noqa: BLE001
            raise _speech_error(e, "Windows speech recognition failed") from e

    def stop(self, timeout: float = 5.0):
        async def go():
            async with self._alock():
                if self._sess is not None:
                    try:
                        await self._sess.stop_async()
                    except OSError:
                        pass            # already ended (silence timeout, error): on_done came already
        _loop().run(go(), timeout)

    def cancel(self):
        self._sink = None

        async def go():
            async with self._alock():
                if self._sess is not None:
                    try:
                        await self._sess.cancel_async()
                    except OSError:
                        pass
        _loop().submit(go())

    def close(self):
        rec, self._rec, self._sess, self._sink = self._rec, None, None, None
        if rec is not None:
            try:
                rec.close()
            except Exception:           # noqa: BLE001
                pass


def _status_name(st) -> str:
    name = getattr(st, "name", None)
    return name.lower() if name else str(st).lower()


# -- SAPI buffer recognition (the real layer; tests inject a fake) --------------------
class SapiBuffer:
    """SAPI.SpInprocRecognizer + dictation grammar, fed one buffer at a time
    through an SpMemoryStream. Every COM object lives on the shared COM thread."""

    def __init__(self, language: str = "en-US"):
        self.language = language
        self._st = None                 # (rec, ctx, handler, grammar), touched on the COM thread only

    def _setup(self):
        if self._st is not None:
            return self._st
        import win32com.client

        rec = win32com.client.Dispatch("SAPI.SpInprocRecognizer")
        lcid = _lcid(self.language)
        toks = rec.GetRecognizers(f"Language={lcid:X}" if lcid else "")
        if toks.Count == 0:
            raise SpeechError(f"no {self.language} speech recognizer")
        rec.Recognizer = toks.Item(0)
        ctx = rec.CreateRecoContext()
        h = win32com.client.WithEvents(ctx, _SapiEvents)
        h.reset()
        g = ctx.CreateGrammar(1)
        g.DictationLoad("", 0)
        self._st = (rec, ctx, h, g)
        return self._st

    def _recognize(self, pcm: bytes, timeout: float) -> str:
        import pythoncom
        import win32com.client

        rec, _ctx, h, g = self._setup()
        ms = win32com.client.Dispatch("SAPI.SpMemoryStream")
        fmt = win32com.client.Dispatch("SAPI.SpAudioFormat")
        fmt.Type = _SAPI_FORMAT_16K_MONO
        ms.Format = fmt
        ms.SetData(pcm)
        h.reset()
        rec.AudioInputStream = ms
        g.DictationSetState(1)
        try:
            end = time.monotonic() + timeout
            while not h.ended and time.monotonic() < end:
                pythoncom.PumpWaitingMessages()
                time.sleep(0.005)
        finally:
            g.DictationSetState(0)
        if not h.ended and not h.texts:
            raise SpeechError("Windows speech recognition timed out")
        return " ".join(t.strip() for t in h.texts if t and t.strip())

    def recognize(self, audio: np.ndarray, timeout: float | None = None) -> str:
        a = np.asarray(audio, np.int16).reshape(-1)
        if a.size == 0:
            return ""
        t = timeout if timeout is not None else 5.0 + a.size / RATE
        try:
            return _com().call(self._recognize, a.tobytes(), t, timeout=t + 10)
        except Exception as e:          # noqa: BLE001
            raise _speech_error(e, "Windows speech recognition failed") from e

    def prepare(self):
        try:
            _com().call(self._setup, timeout=20)
        except Exception as e:          # noqa: BLE001
            raise _speech_error(e, "Windows speech recognition failed") from e

    def close(self):
        if self._st is None:
            return

        def drop():
            self._st = None             # released here, on the thread that made them
        try:
            _com().call(drop, timeout=5)
        except SpeechError:
            pass


class _SapiEvents:
    """ISpeechRecoContext events (win32com.client.WithEvents)."""

    def reset(self):
        self.texts, self.ended, self.hypothesis = [], False, ""

    def OnRecognition(self, _stream, _pos, _rtype, result):
        import win32com.client
        try:
            self.texts.append(win32com.client.Dispatch(result).PhraseInfo.GetText())
        except Exception:               # noqa: BLE001
            pass

    def OnHypothesis(self, _stream, _pos, result):
        import win32com.client
        try:
            self.hypothesis = win32com.client.Dispatch(result).PhraseInfo.GetText()
        except Exception:               # noqa: BLE001
            pass

    def OnEndStream(self, _stream, _pos, _released):
        self.ended = True


# -- speech-to-text --------------------------------------------------------------------
class _Session:
    def __init__(self):
        self.lock = threading.Lock()
        self.results: list[str] = []
        self.hypothesis = ""
        self.started = threading.Event()   # start finished (ok or error)
        self.done = threading.Event()      # on_done arrived
        self.ok = False
        self.error: SpeechError | None = None
        self.status: str | None = None
        self.dropped = False               # cancel()/transcribe() gave up on it

    def on_result(self, text: str):
        with self.lock:
            self.results.append(text)

    def on_hypothesis(self, text: str):
        with self.lock:
            self.hypothesis = text

    def on_done(self, status: str):
        self.status = status
        self.done.set()

    def text(self) -> str:
        with self.lock:
            joined = " ".join(t.strip() for t in self.results if t and t.strip())
            return joined or self.hypothesis.strip()


class WindowsStt(SttBackend):
    """Windows speech recognition: WinRT online dictation from the live mic, SAPI on buffers.

    cfg: the `speech` config section (`windows_language`, default "en-US").
    Factories (tests): live_factory(language) -> WinrtLive-like, buffer_factory(language)
    -> SapiBuffer-like, privacy() -> bool | None, sapi_check(language) -> bool.
    `note`: the last reason the live path wasn't used ("" when it was)."""

    name = "windows"
    probe_ok = False

    def __init__(self, cfg: dict | None = None, live_factory=None, buffer_factory=None,
                 privacy=None, sapi_check=None, start_wait: float = 3.0, stop_wait: float = 2.0):
        cfg = cfg or {}
        self.language = str(cfg.get("windows_language") or "en-US")
        self._live_factory = live_factory or (lambda lang: WinrtLive(lang))
        self._buffer_factory = buffer_factory or (lambda lang: SapiBuffer(lang))
        self._privacy = privacy or online_speech_enabled
        self._sapi_check = sapi_check or sapi_recognizer_present
        self.start_wait, self.stop_wait = start_wait, stop_wait
        self._live = None
        self._live_dead: str | None = None     # a reason that won't change by retrying
        self._buffer = None
        self._session: _Session | None = None
        self._lock = threading.Lock()
        self._path = None                      # "live" | "buffer" after a transcription
        self.note = ""
        self.timings: dict[str, float] = {}    # start_s, stop_s (stop -> final text), buffer_s

    @property
    def label(self) -> str:
        return {"live": "winrt online", "buffer": "sapi"}.get(self._path, "windows")

    def available(self) -> tuple[bool, str]:
        try:
            import winreg  # noqa: F401
        except ImportError:
            return False, "not Windows"
        sapi = self._sapi_check(self.language)
        online = self._privacy()
        if online is False:
            return (True, PRIVACY_OFF) if sapi else (False, PRIVACY_OFF)
        if sapi:
            return True, ""
        try:
            import winrt.windows.media.speechrecognition  # noqa: F401
        except ImportError:
            return False, f"no {self.language} speech recognizer"
        return True, ""

    # -- live session --
    def begin(self, live: bool = False):
        if not live:
            return
        self.cancel()
        if self._privacy() is False:
            self.note = PRIVACY_OFF
            return
        if self._live_dead:
            self.note = self._live_dead
            return
        s = _Session()
        with self._lock:
            self._session = s
        threading.Thread(target=self._start, args=(s,), daemon=True, name="winrt-stt-start").start()

    def _start(self, s: _Session):
        t0 = time.perf_counter()
        try:
            if self._live is None:
                self._live = self._live_factory(self.language)
            self._live.start(s.on_result, s.on_hypothesis, s.on_done)
            s.ok = True
            self.timings["start_s"] = time.perf_counter() - t0
        except Exception as e:          # noqa: BLE001
            s.error = _speech_error(e, "Windows speech recognition failed")
            self.note = str(s.error)
            if self.note.startswith("no "):     # "no en-US speech recognizer": retrying won't help
                self._live_dead = self.note
        finally:
            s.started.set()
        if s.ok and s.dropped:          # cancelled (or given up on) while it was starting
            self._live.cancel()

    def cancel(self):
        with self._lock:
            s, self._session = self._session, None
        if s is None:
            return
        s.dropped = True
        if s.ok and self._live is not None:
            self._live.cancel()

    def transcribe(self, audio: np.ndarray, prompt: str | None = None,
                   languages: list[str] | None = None) -> str:
        with self._lock:
            s, self._session = self._session, None
        if s is not None:
            text = self._finish(s)
            if text is not None:
                self._path = "live"
                self.note = ""
                return text
        return self._transcribe_buffer(audio)

    def _finish(self, s: _Session) -> str | None:
        """Stop a live session and return its text; None if the live path failed."""
        if not s.started.wait(self.start_wait):
            s.dropped = True
            self.note = "Windows speech took too long to start"
            return None
        if not s.ok:
            return None
        t0 = time.perf_counter()
        if not s.done.is_set():
            try:
                self._live.stop()
            except Exception as e:      # noqa: BLE001
                self.note = str(_speech_error(e, "Windows speech recognition failed"))
                return s.text() or None
            s.done.wait(self.stop_wait)
        self.timings["stop_s"] = time.perf_counter() - t0
        text = s.text()
        if s.status and s.status not in _DONE_OK and not text:
            self.note = _DONE_REASONS.get(s.status, _DONE_REASONS["unknown"])
            return None
        return text

    def _transcribe_buffer(self, audio: np.ndarray) -> str:
        t0 = time.perf_counter()
        if self._buffer is None:
            self._buffer = self._buffer_factory(self.language)
        try:
            text = self._buffer.recognize(audio)
        except Exception as e:          # noqa: BLE001
            raise _speech_error(e, "Windows speech recognition failed") from e
        self._path = "buffer"
        self.timings["buffer_s"] = time.perf_counter() - t0
        return text

    def prepare(self, live: bool = True):
        """Warm up off the UI thread: compile dictation (live) and load SAPI dictation,
        so the first utterance doesn't pay ~1 s. Errors are noted, not raised."""
        if live and self._privacy() is not False and not self._live_dead:
            try:
                if self._live is None:
                    self._live = self._live_factory(self.language)
                if hasattr(self._live, "prepare"):
                    self._live.prepare()
            except Exception as e:      # noqa: BLE001
                self.note = str(_speech_error(e, "Windows speech recognition failed"))
        try:
            if self._buffer is None:
                self._buffer = self._buffer_factory(self.language)
            if hasattr(self._buffer, "prepare"):
                self._buffer.prepare()
        except Exception as e:          # noqa: BLE001
            self.note = str(_speech_error(e, "Windows speech recognition failed"))

    def close(self):
        self.cancel()
        for obj in (self._live, self._buffer):
            if obj is not None and hasattr(obj, "close"):
                try:
                    obj.close()
                except Exception:       # noqa: BLE001
                    pass
        self._live = self._buffer = None


# -- text-to-speech --------------------------------------------------------------------
def parse_wav(data: bytes) -> tuple[np.ndarray, int]:
    """WAV bytes -> (float32 mono -1..1, rate). base.pcm_from_wav first; our own RIFF
    reader for what `wave` rejects (WAVE_FORMAT_EXTENSIBLE, float, 24/32-bit)."""
    try:
        return pcm_from_wav(data)
    except Exception:                   # noqa: BLE001 - wave.Error, EOFError, SpeechError
        return _parse_riff(data)


def _parse_riff(data: bytes) -> tuple[np.ndarray, int]:
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise SpeechError("Windows voice returned no WAV audio")
    fmt = None
    raw = None
    i = 12
    while i + 8 <= len(data):
        cid, size = data[i:i + 4], struct.unpack_from("<I", data, i + 4)[0]
        body = data[i + 8:i + 8 + size]
        if cid == b"fmt ":
            fmt = body
        elif cid == b"data":
            raw = body
            if fmt is not None:
                break
        i += 8 + size + (size & 1)
    if fmt is None or raw is None or len(fmt) < 16:
        raise SpeechError("Windows voice returned a broken WAV")
    tag, ch, rate, _bps, _align, bits = struct.unpack_from("<HHIIHH", fmt, 0)
    if tag == 0xFFFE and len(fmt) >= 40:          # WAVE_FORMAT_EXTENSIBLE: sub-format GUID at 24
        tag = struct.unpack_from("<H", fmt, 24)[0]
    ch = max(1, ch)
    if tag == 1 and bits == 16:
        x = np.frombuffer(raw[: len(raw) // 2 * 2], "<i2").astype(np.float32) / 32768.0
    elif tag == 1 and bits == 32:
        x = (np.frombuffer(raw[: len(raw) // 4 * 4], "<i4").astype(np.float64) / 2147483648.0).astype(np.float32)
    elif tag == 1 and bits == 24:
        b = np.frombuffer(raw[: len(raw) // 3 * 3], np.uint8).reshape(-1, 3).astype(np.int32)
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        v = np.where(v >= 1 << 23, v - (1 << 24), v)
        x = (v / float(1 << 23)).astype(np.float32)
    elif tag == 1 and bits == 8:
        x = (np.frombuffer(raw, np.uint8).astype(np.float32) - 128.0) / 128.0
    elif tag == 3 and bits == 32:
        x = np.frombuffer(raw[: len(raw) // 4 * 4], "<f4").astype(np.float32)
    else:
        raise SpeechError(f"unsupported Windows voice format (tag {tag}, {bits} bit)")
    if ch > 1:
        x = x[: len(x) // ch * ch].reshape(-1, ch).mean(axis=1)
    return np.clip(x, -1.0, 1.0).astype(np.float32), int(rate)


class WinrtSynth:
    """The real WinRT layer: one SpeechSynthesizer, used from the shared loop thread."""

    def __init__(self):
        self._s = None
        self._voices = None

    async def _ensure(self):
        if self._s is None:
            from winrt.windows.media.speechsynthesis import SpeechSynthesizer
            self._s = SpeechSynthesizer()
        return self._s

    def voices(self) -> list[str]:
        if self._voices is None:
            async def go():
                from winrt.windows.media.speechsynthesis import SpeechSynthesizer
                return [v.display_name for v in SpeechSynthesizer.all_voices]
            self._voices = _loop().run(go(), 10)
        return list(self._voices)

    def default_voice(self) -> str:
        async def go():
            from winrt.windows.media.speechsynthesis import SpeechSynthesizer
            return SpeechSynthesizer.default_voice.display_name
        return _loop().run(go(), 10)

    def set_voice(self, display_name: str):
        async def go():
            from winrt.windows.media.speechsynthesis import SpeechSynthesizer
            s = await self._ensure()
            for v in SpeechSynthesizer.all_voices:
                if v.display_name == display_name:
                    s.voice = v
                    return
        _loop().run(go(), 10)

    def synth_wav(self, text: str, timeout: float = 30.0) -> bytes:
        async def go():
            from winrt.windows.storage.streams import Buffer, InputStreamOptions
            s = await self._ensure()
            stream = await s.synthesize_text_to_stream_async(text)
            try:
                size = int(stream.size)
                if size <= 0:
                    return b""
                buf = Buffer(size)
                got = await stream.read_async(buf, size, InputStreamOptions.NONE)
                return bytes(memoryview(got))[: got.length]
            finally:
                try:
                    stream.close()
                except Exception:   # noqa: BLE001
                    pass
        return _loop().run(go(), timeout, "Windows voice")

    def close(self):
        s, self._s = self._s, None
        if s is not None:
            try:
                s.close()
            except Exception:           # noqa: BLE001
                pass


class WindowsTts(TtsBackend):
    """WinRT SpeechSynthesizer as PCM. cfg: the `speech` (or `tts`) section;
    `windows_voice`: a substring of a voice's display name ("zira", "Microsoft Mark").
    synth_factory() -> WinrtSynth-like (tests). Thread-safe; one synthesizer."""

    name = "windows"
    pcm = True
    rate = 16000                        # what the OneCore voices produce; updated from each WAV

    def __init__(self, cfg: dict | None = None, synth_factory=None):
        cfg = cfg or {}
        self._factory = synth_factory or WinrtSynth
        self._want = str(cfg.get("windows_voice") or "").strip()
        self._s = None
        self._lock = threading.Lock()
        self.voice = ""
        self.note = ""
        self.timings: dict[str, float] = {}

    @property
    def label(self) -> str:
        return f"windows {self.voice.removeprefix('Microsoft ').lower()}".strip() if self.voice else "windows"

    def available(self) -> tuple[bool, str]:
        try:
            import winrt.windows.media.speechsynthesis  # noqa: F401
        except ImportError:
            return False, "WinRT speech synthesis isn't installed"
        return True, ""

    def _synth(self):
        if self._s is None:
            try:
                s = self._factory()
                self.voice = self._pick(s)
            except Exception as e:      # noqa: BLE001
                raise _speech_error(e, "Windows voice failed") from e
            self._s = s
        return self._s

    def _pick(self, s) -> str:
        if self._want:
            for name in s.voices():
                if self._want.lower() in name.lower():
                    s.set_voice(name)
                    return name
            self.note = f"no Windows voice matching {self._want!r}"
        try:
            return s.default_voice()
        except Exception:               # noqa: BLE001
            return ""

    def voices(self) -> list[str]:
        with self._lock:
            try:
                return list(self._synth().voices())
            except SpeechError:
                return []

    def synth(self, text: str) -> np.ndarray:
        if not text or not text.strip():
            return np.zeros(0, np.float32)
        with self._lock:
            s = self._synth()
            t0 = time.perf_counter()
            try:
                data = s.synth_wav(text)
            except Exception as e:      # noqa: BLE001
                raise _speech_error(e, "Windows voice failed") from e
            if not data:
                raise SpeechError("Windows voice returned no audio")
            x, rate = parse_wav(data)
            self.rate = rate
            self.timings["synth_s"] = time.perf_counter() - t0
            return x

    def close(self):
        with self._lock:
            s, self._s = self._s, None
        if s is not None and hasattr(s, "close"):
            s.close()


__all__ = ["PRIVACY_OFF", "SapiBuffer", "WindowsStt", "WindowsTts", "WinrtLive", "WinrtSynth",
           "online_speech_enabled", "parse_wav", "sapi_recognizer_present"]
