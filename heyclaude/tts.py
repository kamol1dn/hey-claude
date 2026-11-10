"""Text-to-speech on its own threads.

Text arrives in streamed pieces; it's cut into sentences and each sentence is
spoken as soon as it is complete, so the first words play while the model is
still writing the rest. For Kokoro, sentence N+1 is synthesised while sentence
N plays.

Engines (`tts.engine`): kokoro (default), sapi (Windows voices), piper.

Kokoro runs through kokoro-onnx on onnxruntime. With `tts.device: gpu` it
follows the same per-request rule as speech-to-text (stt.choose): the GPU
unless it is busy (utilisation >= stt.gpu_busy_pct) or short of VRAM, then the
CPU. The device is picked once per reply, not per sentence. Fallback chain,
with the reason in the log and on the island:

    Kokoro on the GPU -> Kokoro on the CPU -> Windows SAPI

- no CUDA provider in onnxruntime, or the GPU session failing to load: the
  GPU is off for the session; a CUDA error while speaking retries that
  sentence on the CPU and turns the GPU off for the session.
- Kokoro missing (model files, package) or failing on the CPU too: SAPI.
- `stt.yield_to_premiere` (opt-in) moves Kokoro to the CPU while Premiere runs.

Short sentences ("Volume 30.", "Dark mode.", the acks of M10's instant
commands) are kept in a small cache once synthesised, per voice and speed,
so saying them again costs nothing; `Speaker.prewarm` fills it ahead.

PCM engines (Kokoro, piper) play through our own output stream, which also
hands every played sample to `far_tap` (16 kHz) for echo cancellation.
"""
from __future__ import annotations

import os
import queue
import re
import subprocess
import threading
import time
from collections import OrderedDict

import numpy as np

_SENT_END = re.compile(r"(.+?[.!?;:])(\s+|$)", re.S)
KOKORO_RATE = 24000
FAR_RATE = 16000

# Kokoro voices that ship in voices-v1.0.bin (English ones; a = American, b = British).
KOKORO_VOICES = ("af_heart", "af_bella", "af_nicole", "af_sarah", "af_sky", "af_nova", "af_river",
                 "am_michael", "am_fenrir", "am_puck", "am_adam", "am_echo", "bf_emma", "bf_isabella",
                 "bf_alice", "bm_george", "bm_fable", "bm_lewis", "bm_daniel")
ACK_MAX_CHARS = 40          # sentences this short are cached once synthesised (instant-command acks)
ACK_CACHE_SIZE = 96
KOKORO_VRAM_MB = 700        # fp32 model + activations, measured ~450 MB on an RTX 4060


def clean_for_speech(s: str) -> str:
    s = re.sub(r"(?im)^\s*(summary|tl;?dr)\s*:\s*", "", s)      # a section label, not something to say
    s = re.sub(r"`{1,3}[^`]*`{1,3}", "", s)
    s = re.sub(r"[*_#>\[\]]", "", s)
    s = re.sub(r"https?://\S+", "a link", s)
    return " ".join(s.split())


# -- device plumbing -----------------------------------------------------------------
def kokoro_paths(cfg: dict) -> tuple[str, str]:
    """(model, voices) from tts.kokoro_model / kokoro_voices; null = state/models/kokoro/."""
    from .config import ROOT, STATE_DIR

    def res(v, default):
        p = os.path.expandvars(os.path.expanduser(str(v))) if v else str(STATE_DIR / "models" / "kokoro" / default)
        return p if os.path.isabs(p) else str(ROOT / p)
    return res(cfg.get("kokoro_model"), "kokoro-v1.0.onnx"), res(cfg.get("kokoro_voices"), "voices-v1.0.bin")


_dll_dirs_added = False


def add_ort_cuda_dirs():
    """onnxruntime-gpu needs the CUDA runtime, cuFFT and cuDNN DLLs. Python
    doesn't search PATH for them, so add the CUDA toolkit's bin folders and
    the nvidia-* pip packages, then let onnxruntime preload what it can."""
    global _dll_dirs_added
    import sys
    if sys.platform != "win32" or _dll_dirs_added:
        return
    _dll_dirs_added = True
    import importlib.util
    dirs = []
    cuda = os.environ.get("CUDA_PATH")
    if cuda:
        dirs += [os.path.join(cuda, "bin", "x64"), os.path.join(cuda, "bin")]
    for pkg in ("nvidia.cudnn", "nvidia.cufft", "nvidia.cuda_runtime", "nvidia.cublas"):
        try:
            spec = importlib.util.find_spec(pkg)
        except (ImportError, ValueError):
            spec = None
        for base in (spec.submodule_search_locations or []) if spec else []:
            dirs += [os.path.join(base, "bin"), os.path.join(base, "bin", "x86_64")]
    for d in dirs:
        if os.path.isdir(d):
            try:
                os.add_dll_directory(d)
            except OSError:
                pass
    try:
        import onnxruntime as ort
        if hasattr(ort, "preload_dlls"):
            ort.preload_dlls(directory="")
    except Exception:
        pass


def ort_cuda_available() -> tuple[bool, str]:
    try:
        import onnxruntime as ort
    except Exception as e:
        return False, f"no onnxruntime ({e})"[:120]
    if "CUDAExecutionProvider" not in ort.get_available_providers():
        import importlib.metadata as md
        try:
            md.distribution("onnxruntime-gpu")
            hint = " (onnxruntime-gpu is installed but the CPU package overwrote it: "                    "pip install --force-reinstall --no-deps onnxruntime-gpu)"
        except md.PackageNotFoundError:
            hint = " (pip install onnxruntime-gpu; README \"Kokoro voice\")"
        return False, "no cuda in onnxruntime" + hint
    add_ort_cuda_dirs()
    return True, ""


def is_cuda_error(e: BaseException) -> bool:
    s = f"{type(e).__name__} {e}".lower()
    return any(k in s for k in ("cuda", "cudnn", "cublas", "out of memory", "gpu"))


def to_far(samples: np.ndarray, rate: int) -> np.ndarray:
    """Playback audio -> 16 kHz int16, the echo canceller's reference."""
    x = np.asarray(samples, dtype=np.float32)
    if rate != FAR_RATE:
        from math import gcd
        from scipy.signal import resample_poly
        g = gcd(FAR_RATE, rate)
        x = resample_poly(x, FAR_RATE // g, rate // g).astype(np.float32)
    return np.clip(x * 32767.0, -32768, 32767).astype(np.int16)


# -- engines ---------------------------------------------------------------------------
class _Sapi:
    """Windows SAPI 5 (the engine pyttsx3 wraps), driven directly through COM.

    pyttsx3 2.99's runAndWait() hangs after a save_to_file and can return
    without speaking on repeated calls, so we talk to SAPI.SpVoice ourselves:
    async Speak + poll, which also lets stop() cut a sentence off mid-word.
    SAPI plays the audio itself, so there's no echo reference (no barge-in).
    """

    SPEAK_ASYNC, PURGE = 1, 2
    RUNNING = 2
    pcm = False

    def __init__(self, cfg):
        import pythoncom
        import win32com.client

        pythoncom.CoInitialize()
        self.v = win32com.client.Dispatch("SAPI.SpVoice")
        # pyttsx3-style words-per-minute -> SAPI rate (-10..10, 0 ~ 180 wpm)
        self.v.Rate = max(-10, min(10, round((int(cfg.get("rate", 190)) - 180) / 15)))
        want = cfg.get("sapi_voice")
        if want:
            voices = self.v.GetVoices()
            for i in range(voices.Count):
                if want.lower() in voices.Item(i).GetDescription().lower():
                    self.v.Voice = voices.Item(i)
                    break
        self.on_start = None
        self._stop = False

    def speak(self, text: str):
        self._stop = False
        self.v.Speak(text, self.SPEAK_ASYNC)
        started = False
        while True:
            if self._stop:
                self.v.Speak("", self.SPEAK_ASYNC | self.PURGE)
                return
            if not started and self.v.Status.RunningState == self.RUNNING:
                started = True
                if self.on_start:
                    self.on_start()
            if self.v.WaitUntilDone(15):
                if not started and self.on_start:
                    self.on_start()
                return

    def stop(self):
        self._stop = True


class _Piper:
    pcm = True

    def __init__(self, cfg):
        if not cfg.get("piper_model"):
            raise ValueError("tts.piper_model must point at a piper .onnx voice")
        self.exe, self.model = cfg.get("piper_exe", "piper"), cfg["piper_model"]
        import json
        meta = self.model + ".json"
        self.rate = json.load(open(meta))["audio"]["sample_rate"] if os.path.exists(meta) else 22050

    def synth(self, text) -> np.ndarray:
        out = subprocess.run([self.exe, "--model", self.model, "--output_raw"], input=text.encode("utf-8"),
                             capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        return np.frombuffer(out, dtype=np.int16).astype(np.float32) / 32768.0


class KokoroVoice:
    """kokoro-onnx on one device ("cuda" | "cpu"), warmed up at load."""

    pcm = True
    rate = KOKORO_RATE

    def __init__(self, model: str, voices: str, device: str, voice: str = "af_heart", speed: float = 1.0,
                 threads: int = 4):
        import onnxruntime as ort
        from kokoro_onnx import Kokoro

        for p in (model, voices):
            if not os.path.isfile(p):
                raise FileNotFoundError(f"kokoro file missing: {p}")
        so = ort.SessionOptions()
        so.log_severity_level = 3
        if device == "cuda":
            ok, why = ort_cuda_available()
            if not ok:
                raise RuntimeError(why)
            # HEURISTIC: no exhaustive cuDNN search for every new sentence length
            # (that alone costs seconds per new shape).
            providers = [("CUDAExecutionProvider", {"cudnn_conv_algo_search": "HEURISTIC",
                                                    "arena_extend_strategy": "kSameAsRequested"}),
                         "CPUExecutionProvider"]
        else:
            so.intra_op_num_threads = int(threads)
            providers = ["CPUExecutionProvider"]
        sess = ort.InferenceSession(model, sess_options=so, providers=providers)
        if device == "cuda" and sess.get_providers()[0] != "CUDAExecutionProvider":
            raise RuntimeError("onnxruntime fell back to the CPU (CUDA/cuDNN DLLs missing?)")
        self.device = device
        self.k = Kokoro.from_session(sess, voices)
        self.voice, self.speed = voice, float(speed)
        self.synth("Ready.")                     # first real sentence doesn't pay init

    def synth(self, text: str) -> np.ndarray:
        samples, _ = self.k.create(text, voice=self.voice, speed=self.speed, lang="en-us")
        return np.asarray(samples, dtype=np.float32)


class VoiceEngine:
    """Owns the Kokoro sessions and the fallback chain; picks the device per reply.

    `loader(device)` builds a KokoroVoice ("cuda" | "cpu"); `sapi()` builds the
    SAPI fallback; both injectable for tests. `label` says what's speaking:
    "kokoro gpu", "kokoro cpu (gpu busy 83%)", "sapi (kokoro: <why>)".
    """

    def __init__(self, cfg: dict, stt_cfg: dict | None = None, monitor=None, loader=None, sapi=None,
                 piper=None, cuda_check=None, premiere_running=None, on_change=None):
        self.cfg = cfg                           # live tts section
        self.stt_cfg = stt_cfg if stt_cfg is not None else {}   # shared thresholds
        self.engine = str(cfg.get("engine") or "kokoro").lower()
        self.want = "gpu" if str(cfg.get("device") or "gpu").lower() in ("gpu", "cuda") else "cpu"
        self.monitor = monitor
        self._loader = loader or self._load
        self._sapi_factory = sapi or (lambda: _Sapi(self.cfg))
        self._piper_factory = piper or (lambda: _Piper(self.cfg))
        self._cuda_check = cuda_check or ort_cuda_available
        self._premiere = premiere_running
        self.on_change = on_change
        self.gpu: KokoroVoice | None = None
        self.cpu: KokoroVoice | None = None
        self.sapi = None
        self.piper = None
        self.gpu_failed: str | None = None
        self.kokoro_failed: str | None = None
        self.choice, self.reason = "gpu", ""
        self.current = None                      # engine object used for the current reply
        self._lock = threading.RLock()
        self._premiere_t, self._premiere_seen = 0.0, False
        self._loading = False

    # -- loading ---------------------------------------------------------------------
    def _load(self, device: str) -> KokoroVoice:
        model, voices = kokoro_paths(self.cfg)
        return KokoroVoice(model, voices, device, voice=str(self.cfg.get("voice") or "af_heart"),
                           speed=float(self.cfg.get("speed") or 1.0), threads=int(self.cfg.get("cpu_threads", 4)))

    def start(self):
        """Load what the configured engine needs (on the TTS thread, at start-up)."""
        if self.engine == "sapi":
            self._ensure_sapi()
        elif self.engine == "piper":
            try:
                self.piper = self._piper_factory()
            except Exception as e:
                self.kokoro_failed = f"piper: {e}"[:160]
                print(f"[tts] piper failed ({e}); using SAPI")
                self._ensure_sapi()
        else:
            if self.want == "gpu":
                self._try_gpu()
            if self.gpu is None:
                self._ensure_cpu()
        self.pick()

    def _try_gpu(self) -> bool:
        ok, why = self._cuda_check()
        if not ok:
            self._fail_gpu(why)
            return False
        try:
            t = time.perf_counter()
            self.gpu = self._loader("cuda")
            print(f"[tts] kokoro on GPU in {time.perf_counter() - t:.1f}s")
            return True
        except Exception as e:
            self._fail_gpu(f"gpu load failed: {e}"[:160])
            return False

    def _load_gpu_bg(self):
        try:
            self._try_gpu()
        finally:
            self._loading = False

    def _fail_gpu(self, why: str):
        self.gpu_failed, self.gpu = why, None
        print(f"[tts] kokoro GPU off for this session: {why}")

    def _ensure_cpu(self):
        if self.cpu is not None or self.kokoro_failed:
            return self.cpu
        try:
            t = time.perf_counter()
            self.cpu = self._loader("cpu")
            print(f"[tts] kokoro on CPU in {time.perf_counter() - t:.1f}s")
        except Exception as e:
            self.kokoro_failed = str(e)[:160]
            print(f"[tts] kokoro failed ({e}); using SAPI")
            self._ensure_sapi()
        return self.cpu

    def _ensure_sapi(self):
        if self.sapi is None:
            self.sapi = self._sapi_factory()
        return self.sapi

    # -- lean mode (M4c) ---------------------------------------------------------------
    unloaded = False

    @property
    def loaded(self) -> bool:
        return self.gpu is not None or self.cpu is not None

    def unload(self):
        """Drop the Kokoro sessions (VRAM/RAM back). SAPI and piper stay: they hold no model."""
        with self._lock:
            if self.engine != "kokoro":
                return
            if self.current is self.gpu or self.current is self.cpu:
                self.current = None
            self.gpu = self.cpu = None
            self.unloaded = True
        import gc
        gc.collect()
        print("[tts] kokoro unloaded (lean)")

    def reload(self):
        """Load Kokoro again after unload() (blocking): the GPU unless it's off or busy."""
        with self._lock:
            if not self.unloaded:
                return
            self.unloaded = False
        if self.want == "gpu" and not self.gpu_failed:
            self._try_gpu()
        if self.gpu is None:
            self._ensure_cpu()

    # -- the per-reply choice ------------------------------------------------------------
    def _premiere_on(self) -> bool:
        if not self.stt_cfg.get("yield_to_premiere") or self._premiere is None:
            return False
        now = time.monotonic()
        if now - self._premiere_t >= 10:
            self._premiere_t = now
            try:
                self._premiere_seen = bool(self._premiere())
            except Exception:
                self._premiere_seen = False
        return self._premiere_seen

    def pick(self):
        """Choose the engine for the next reply from the current load. Returns it."""
        from .stt import choose
        if self.unloaded:
            self.reload()
        with self._lock:
            before = self.label
            if self.engine == "sapi" or (self.engine == "piper" and self.piper is None) or self.kokoro_failed:
                self.choice, self.reason, eng = "sapi", "", self._ensure_sapi()
            elif self.engine == "piper":
                self.choice, self.reason, eng = "piper", "", self.piper
            else:
                if self.want != "gpu":
                    self.choice, self.reason = "cpu", ""
                elif self.gpu_failed:
                    self.choice, self.reason = "cpu", "fallback: " + _short_reason(self.gpu_failed)
                elif self._premiere_on():
                    self.choice, self.reason = "cpu", "fallback: premiere"
                else:
                    m, c = self.monitor, self.stt_cfg
                    need = 0.0 if self.gpu is not None else KOKORO_VRAM_MB
                    self.choice, self.reason = choose(
                        self.choice == "gpu", m.gpu_util() if m else None, m.gpu_free_mb() if m else None,
                        need, m.cpu_pct() if m else None, busy=float(c.get("gpu_busy_pct", 70)),
                        resume=float(c.get("gpu_resume_pct", 60)), cpu_busy=float(c.get("cpu_busy_pct", 85)))
                    if self.choice == "gpu" and self.gpu is None and not self._loading:
                        # Load it in the background; the CPU speaks this reply.
                        self._loading = True
                        threading.Thread(target=self._load_gpu_bg, daemon=True, name="tts-gpu-load").start()
                if self.choice == "gpu" and self.gpu is not None:
                    eng = self.gpu
                else:
                    if self.choice == "gpu":     # chosen, but not loadable
                        self.choice = "cpu"
                    eng = self._ensure_cpu() or self._ensure_sapi()
                    if eng is self.sapi:
                        self.choice = "sapi"
            self.current = eng
            if self.label != before:
                print(f"[tts] voice: {self.label}")
                if self.on_change:
                    self.on_change(self.label)
            return eng

    def synth(self, text: str) -> tuple[np.ndarray, int]:
        """PCM for one sentence on the current engine, walking down the chain on errors."""
        eng = self.current
        if isinstance(eng, KokoroVoice):          # voice and speed are live settings
            eng.voice = str(self.cfg.get("voice") or "af_heart")
            eng.speed = float(self.cfg.get("speed") or 1.0)
        try:
            if eng is self.gpu and eng is not None:
                from .stt import own_gpu
                with own_gpu(self.monitor):     # our own work doesn't make the GPU "busy"
                    return eng.synth(text), eng.rate
            return eng.synth(text), eng.rate
        except Exception as e:
            if eng is self.gpu and eng is not None:
                with self._lock:
                    self._fail_gpu(f"cuda error: {e}"[:160] if is_cuda_error(e) else f"gpu error: {e}"[:160])
                print("[tts] retrying that sentence on the CPU")
                self.pick()
                if self.current is not None and self.current.pcm:
                    return self.synth(text)
                raise _UseSapi()
            if eng is self.cpu and eng is not None:
                with self._lock:
                    self.kokoro_failed, self.cpu = f"cpu error: {e}"[:160], None
                self.pick()
                raise _UseSapi()
            raise

    @property
    def label(self) -> str:
        if self.engine == "piper" and self.piper is not None and not self.kokoro_failed:
            return "piper"
        if self.choice == "sapi" or self.engine == "sapi" or self.kokoro_failed:
            if self.engine == "sapi":
                return "sapi"
            return f"sapi ({'kokoro' if self.engine == 'kokoro' else self.engine}: {_short_reason(self.kokoro_failed or '')})"
        if self.unloaded:
            return "kokoro unloaded (lean)"
        dev = "gpu" if self.choice == "gpu" and self.gpu is not None else "cpu"
        return f"kokoro {dev}" + (f" ({self.reason})" if self.reason and dev == "cpu" else "")


class _UseSapi(Exception):
    """The PCM chain is exhausted for this sentence: speak it with SAPI."""


def _short_reason(r: str) -> str:
    s = r.lower()
    if "no cuda" in s or "fell back to the cpu" in s:
        return "no cuda"
    if "cuda error" in s:
        return "cuda error"
    if "missing" in s or "no such file" in s:
        return "model missing"
    if "no module" in s or "no onnxruntime" in s:
        return "not installed"
    if "gpu load failed" in s:
        return "gpu load failed"
    return r[:40]


# -- playback --------------------------------------------------------------------------
class Player:
    """One output stream, opened for a reply and closed after `idle_close_s`
    (an always-open stream would keep Windows from sleeping).

    `play(samples, rate)` queues audio and returns at once; `on_start` fires
    when the first sample of a queued chunk goes out; `far_tap(int16_16k)`
    receives what's being played, as it plays.
    """

    def __init__(self, far_tap=None, idle_close_s: float = 4.0, device=None):
        self.far_tap = far_tap
        self.idle_close_s, self.device = idle_close_s, device
        self._chunks: list[list] = []            # [samples, pos, far, far_pos, rate]
        self._lock = threading.Lock()
        # Opening, replacing and closing the stream happen under this one lock. begin()
        # opens it on its own thread while the synth thread's play() may open it too: with
        # M10's cached acks both land within milliseconds, and two OutputStreams were
        # built for one player; the overwritten one kept PortAudio calling into a stream
        # Python then freed (the live re-check's native crashes right after an ack).
        self._open_lock = threading.RLock()
        self._stream = None
        self._rate = None
        self._idle_since = time.monotonic()
        self.on_start = None
        self.on_level = None                     # M5: loudness 0..1 of what's playing, ~30 Hz
        self.on_chunk = None                     # M5: callback(tag, seconds) when a tagged chunk starts
        self._level_t = 0.0
        self._started_chunk = False
        self._closer = threading.Thread(target=self._close_when_idle, daemon=True, name="tts-player-idle")
        self._closer.start()

    def prepare(self, rate: int = KOKORO_RATE):
        """Open the stream ahead of the first sentence (saves ~50-100 ms)."""
        try:
            self._open(rate)
        except Exception as e:
            print(f"[tts] output stream: {e}")

    def _open(self, rate):
        import sounddevice as sd
        with self._open_lock:
            with self._lock:
                if self._stream is not None and self._rate == rate:
                    self._idle_since = time.monotonic()
                    return
            self.close()
            s = sd.OutputStream(samplerate=rate, channels=1, dtype="float32", callback=self._cb,
                                latency="low", device=self.device)
            s.start()
            with self._lock:
                self._stream, self._rate = s, rate
                self._idle_since = time.monotonic()

    def play(self, samples: np.ndarray, rate: int, tag=None):
        """Queue audio. `tag` (the sentence) is handed to on_chunk when it starts playing."""
        self._open(rate)
        far = to_far(samples, rate) if self.far_tap else None
        with self._lock:
            self._chunks.append([np.asarray(samples, np.float32), 0, far, 0, rate, tag])

    @property
    def active(self) -> bool:
        with self._lock:
            return bool(self._chunks)

    def stop(self):
        with self._lock:
            self._chunks.clear()

    underflows = 0                               # output underruns (crackles), logged per reply

    def _cb(self, out, frames, t, status):
        if status and getattr(status, "output_underflow", False):
            self.underflows += 1
        out.fill(0)
        filled = 0
        fire = False
        started = []                             # (tag, seconds) of chunks that begin in this block
        with self._lock:
            while filled < frames and self._chunks:
                c = self._chunks[0]
                samples, pos = c[0], c[1]
                if pos == 0:
                    fire = True
                    if len(c) > 5 and c[5] is not None:
                        started.append((c[5], len(samples) / float(c[4])))
                n = min(frames - filled, len(samples) - pos)
                out[filled:filled + n, 0] = samples[pos:pos + n]
                if c[2] is not None and self.far_tap:
                    f0 = c[3]
                    f1 = min(len(c[2]), round((pos + n) * FAR_RATE / c[4]))
                    if f1 > f0:
                        self.far_tap(c[2][f0:f1])
                    c[3] = f1
                c[1] = pos + n
                filled += n
                if c[1] >= len(samples):
                    self._chunks.pop(0)
            if self._chunks or filled:
                self._idle_since = time.monotonic()
        if fire and self.on_start:
            self.on_start()
        if started and self.on_chunk is not None:
            for tag, secs in started:
                try:
                    self.on_chunk(tag, secs)
                except Exception:
                    pass
        if self.on_level is not None and filled:
            now = time.monotonic()
            if now - self._level_t >= 0.03:
                self._level_t = now
                rms = float(np.sqrt(np.mean(out[:filled, 0] ** 2)))
                try:
                    self.on_level(min(1.0, rms / 0.12))
                except Exception:
                    pass

    def wait(self, gen_check=None, timeout=120, more=None):
        end = time.monotonic() + timeout
        while self.active and time.monotonic() < end:
            if gen_check is not None and not gen_check():
                self.stop()
                return
            if more is not None and more():
                return
            time.sleep(0.01)
        # let the device drain what's already in its buffer
        time.sleep(0.03)

    def _close_when_idle(self):
        while True:
            time.sleep(0.5)
            if self._idle():
                with self._open_lock:
                    if self._idle():                # still idle: nothing was queued meanwhile
                        self.close()

    def _idle(self) -> bool:
        with self._lock:
            return self._stream is not None and not self._chunks and \
                time.monotonic() - self._idle_since > self.idle_close_s

    def close(self):
        with self._open_lock:
            with self._lock:
                s, self._stream, self._rate = self._stream, None, None
            if s is not None:
                try:
                    s.stop()
                    s.close()
                except Exception:
                    pass


class Speaker:
    """Sentences in, audio out. Two threads: the synth thread turns queued
    sentences into PCM, the player plays them back to back.

    `q`, `busy`, `first_audio_t`, `on_start` / `on_idle` are what the rest of
    the app reads (wake-word gate, follow-up window, latency log).
    """

    _first_chunk = False
    cfg: dict = {}

    def __init__(self, cfg: dict, on_start=None, on_idle=None, stt_cfg: dict | None = None, monitor=None,
                 far_tap=None, on_label=None, engine: VoiceEngine | None = None, player=None):
        self.cfg = cfg
        self.q: queue.Queue = queue.Queue()
        self.on_start = on_start or (lambda: None)
        self.on_idle = on_idle or (lambda: None)
        self._buf = ""
        self._gen = 0                     # bumped by stop(); stale sentences are skipped
        self._first_chunk = True          # first sentence of a reply: may be cut at a comma
        self.first_audio_t: float | None = None
        self.busy = False
        self.voice = engine or VoiceEngine(cfg, stt_cfg, monitor=monitor, on_change=on_label,
                                           premiere_running=_premiere_running)
        self.player = player if player is not None else Player(far_tap=far_tap,
                                                               idle_close_s=float(cfg.get("idle_close_s", 4.0)))
        self.player.on_start = self._started
        self.on_sentence = None           # M5: callback(text, seconds) as each sentence starts playing
        if hasattr(self.player, "on_chunk"):
            self.player.on_chunk = self._sentence
        self.synth_ms: list[float] = []   # per sentence, for the log
        self._ack_cache: OrderedDict = OrderedDict()   # (voice, speed, text) -> (pcm, rate)
        self._synth_lock = threading.Lock()            # the speaking thread and prewarm() share the engine
        self.ack_hits = 0
        self._ready = threading.Event()
        threading.Thread(target=self._run, daemon=True, name="tts").start()
        self._ready.wait(60)

    @property
    def label(self) -> str:
        return self.voice.label

    @property
    def engine(self):                     # (older callers)
        return self.voice.current

    def _run(self):
        try:
            self.voice.start()
        except Exception as e:
            print(f"[tts] {e}; using SAPI")
            self.voice.kokoro_failed = str(e)[:160]
            self.voice.pick()
        if self.voice.sapi is not None:
            self.voice.sapi.on_start = self._started
        print(f"[tts] voice: {self.voice.label}")
        self._ready.set()
        while True:
            gen, text = self.q.get()
            if gen != self._gen or not text:   # cancelled by stop()
                self._maybe_idle()
                continue
            self.busy = True
            try:
                self._speak(gen, text)
            except Exception as e:
                print(f"[tts] {e}")
            self._maybe_idle()

    def _speak(self, gen, text):
        eng = self.voice.current or self.voice.pick()
        if eng is not None and eng.pcm:
            t = time.perf_counter()
            try:
                samples, rate = self.synth_cached(text)
            except _UseSapi:
                eng = None
            else:
                self.synth_ms.append((time.perf_counter() - t) * 1000)
                if gen != self._gen:
                    return
                if hasattr(self.player, "on_chunk"):
                    self.player.play(samples, rate, tag=text)     # on_chunk reports when it starts
                else:
                    self.player.play(samples, rate)
                # Wait for playback, unless another sentence arrives to synthesise meanwhile.
                self.player.wait(lambda: gen == self._gen, more=lambda: not self.q.empty())
                return
        sapi = self.voice._ensure_sapi()

        def started(t=text):
            self._started()
            self._sentence(t, max(0.6, len(t) / 14.0))      # SAPI: no timing, ~14 chars a second
        sapi.on_start = started
        self.player.wait(lambda: gen == self._gen)   # finish any PCM still playing first
        if gen == self._gen:
            sapi.speak(text)

    def _ack_key(self, text: str):
        if len(text) > ACK_MAX_CHARS:
            return None
        return (self.voice.label, str(self.cfg.get("voice") or ""), float(self.cfg.get("speed") or 1.0), text)

    def synth_cached(self, text: str):
        """voice.synth, with short sentences served from (and added to) the ack cache."""
        key = self._ack_key(text)
        if key is not None:
            hit = self._ack_cache.get(key)
            if hit is not None:
                self._ack_cache.move_to_end(key)
                self.ack_hits += 1
                return hit
        with self._synth_lock:
            samples, rate = self.voice.synth(text)
        if key is not None:
            self._ack_cache[key] = (samples, rate)
            while len(self._ack_cache) > ACK_CACHE_SIZE:
                self._ack_cache.popitem(last=False)
        return samples, rate

    def prewarm(self, phrases, delay_s: float = 4.0):
        """Synthesise short phrases ahead (in the background, after start-up settles),
        so an instant command's ack plays at once. Kokoro/piper only."""
        def go():
            time.sleep(delay_s)
            done = 0
            for p in phrases:
                eng = self.voice.current
                if eng is None or not getattr(eng, "pcm", False) or self._ack_key(p) in self._ack_cache:
                    continue
                if self.busy:                   # never compete with a reply
                    time.sleep(1.0)
                try:
                    self.synth_cached(p)
                    done += 1
                except Exception as e:
                    print(f"[tts] prewarm: {e}")
                    return
            if done:
                print(f"[tts] {done} short acks cached ({self.voice.label})")
        threading.Thread(target=go, daemon=True, name="tts-prewarm").start()

    def _sentence(self, text, secs):
        cb = self.on_sentence
        if cb is not None:
            try:
                cb(text, float(secs))
            except Exception as e:
                print(f"[tts] on_sentence: {e}")

    def _started(self):
        if self.first_audio_t is None:
            self.first_audio_t = time.perf_counter()
        self.on_start()

    def _maybe_idle(self):
        if self.q.empty() and not self.player.active:
            n = getattr(self.player, "underflows", 0)
            if n:
                print(f"[tts] {n} audio underrun(s) in that reply (crackle); the output stream was starved")
                self.player.underflows = 0
            self.busy = False
            self.on_idle()

    # -- streaming API --------------------------------------------------------
    def begin(self):
        """Start a new reply: reset the first-audio timer, pick the device for
        it from the current load, and open the output stream meanwhile."""
        self._buf = ""
        self._first_chunk = True
        self.first_audio_t = None
        try:
            eng = self.voice.pick()
            if eng is not None and eng.pcm:
                threading.Thread(target=self.player.prepare, args=(eng.rate,), daemon=True).start()
        except Exception as e:
            print(f"[tts] {e}")

    def _first_cut(self) -> str | None:
        """The first sentence of a reply, cut early at a comma when it's long,
        so the first audio doesn't wait for a whole long sentence."""
        n = int(self.cfg.get("first_chunk_words", 9) or 0)
        if not n:
            return None
        if len(self._buf.split()) < n:
            return None
        for m in re.finditer(r",\s+", self._buf):
            head = self._buf[:m.start() + 1]
            if len(head.split()) >= min(n, 5):
                return head
        return None

    def feed(self, chunk: str):
        self._buf += chunk
        while True:
            m = _SENT_END.match(self._buf)
            # Don't cut "3.5" or "e.g." — need whitespace after the stop.
            if not m or (m.group(2) == ""):
                if self._first_chunk:
                    cut = self._first_cut()
                    if cut:
                        self._buf = self._buf[len(cut):].lstrip()
                        self._first_chunk = False
                        sent = clean_for_speech(cut)
                        if sent:
                            self.q.put((self._gen, sent))
                        continue
                break
            sent = clean_for_speech(m.group(1))
            self._buf = self._buf[m.end():]
            self._first_chunk = False
            if sent:
                self.q.put((self._gen, sent))

    def flush(self):
        rest = clean_for_speech(self._buf)
        self._buf = ""
        if rest:
            self.q.put((self._gen, rest))

    def announce(self, text: str):
        """Queue a standalone message (task finished) without disturbing a reply being streamed."""
        if not self.busy:
            self.voice.pick()
        for sent in re.split(r"(?<=[.!?])\s+", clean_for_speech(text)):
            if sent:
                self.q.put((self._gen, sent))

    def say(self, text: str):
        self.begin()
        self.feed(text)
        self.flush()

    def stop(self):
        self._gen += 1
        self._buf = ""
        self.player.stop()
        try:
            if self.voice.sapi is not None:
                self.voice.sapi.stop()
        except Exception:
            pass

    def wait(self, timeout=60):
        """Block until everything queued has been spoken."""
        end = time.time() + timeout
        time.sleep(0.05)
        while (self.busy or not self.q.empty()) and time.time() < end:
            time.sleep(0.05)


def _premiere_running() -> bool:
    from .winsys import process_running
    from .stt import PREMIERE
    return process_running(PREMIERE)
