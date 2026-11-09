"""Speech-to-text with faster-whisper: GPU by default, CPU when the GPU is busy.

`stt.device: gpu` picks the device per request from actual load:

- GPU (`stt.gpu_model`, large-v3-turbo) unless it's busy: utilisation
  >= `stt.gpu_busy_pct` (70) averaged over ~0.5 s, or too little free VRAM to
  load the model. Once on the CPU it goes back only below `gpu_resume_pct`
  (60), so it doesn't flip back and forth.
- A busy GPU sends the request to the CPU (`stt.cpu_model`) only if the CPU
  isn't busier: CPU >= `stt.cpu_busy_pct` (85) keeps it on the GPU.
- Both models stay loaded once used, so switching is instant. The GPU model
  loads at start-up; the CPU one the first time it's needed. If the GPU is
  chosen but not loaded yet, that request uses the CPU while it loads.

Hard fallbacks to the CPU: CUDA missing or the GPU model failing to load
(for the session; an out-of-memory load is retried after a minute), and a
CUDA error mid-transcription (that request is retried on the CPU).
`stt.yield_to_premiere` (off by default) still forces the CPU while Adobe
Premiere Pro runs, for anyone who wants that.

`label` says what's active, e.g. "gpu (turbo)", "cpu (gpu busy 83%)",
"gpu (cpu busy)".
"""
from __future__ import annotations

import threading
import time

import numpy as np

PREMIERE = "Adobe Premiere Pro.exe"

# Rough VRAM (MB) per model in float16, plus headroom for activations.
_VRAM_MB = {"large-v3-turbo": 1650, "turbo": 1650, "large-v3": 3200, "large-v2": 3200, "large-v1": 3200,
            "large": 3200, "distil-large-v3": 1600, "distil-large-v3.5": 1600, "distil-large-v2": 1600,
            "medium": 1600, "medium.en": 1600, "small": 520, "small.en": 520, "base": 160, "base.en": 160,
            "tiny": 90, "tiny.en": 90}
HEADROOM_MB = 600


def normalize_device(v) -> str:
    v = str(v or "cpu").lower()
    return "gpu" if v in ("gpu", "cuda") or v.startswith("cuda") else "cpu"


def short_model(name: str) -> str:
    n = str(name)
    if "turbo" in n:
        return "turbo"
    return n.rsplit("/", 1)[-1].replace("faster-whisper-", "")


def vram_needed_mb(model: str, compute_type: str) -> float:
    mb = _VRAM_MB.get(short_model(model) if "turbo" not in str(model) else "turbo", 1700)
    if "int8" in str(compute_type):
        mb *= 0.55
    return mb + HEADROOM_MB


def choose(on_gpu: bool, gpu_util, gpu_free_mb, need_mb, cpu_pct,
           busy=70.0, resume=60.0, cpu_busy=85.0) -> tuple[str, str]:
    """The per-request rule. Returns ("gpu" | "cpu", reason).

    on_gpu: whether the last choice was the GPU (hysteresis). need_mb: VRAM
    still needed (0 when the GPU model is already loaded). None readings
    count as "not busy"."""
    gpu_hot = gpu_util is not None and gpu_util >= (busy if on_gpu else resume)
    vram_low = gpu_free_mb is not None and need_mb > 0 and gpu_free_mb < need_mb
    cpu_hot = cpu_pct is not None and cpu_pct >= cpu_busy
    if gpu_hot or vram_low:
        if cpu_hot:
            return "gpu", "cpu busy"
        return "cpu", f"gpu busy {gpu_util:.0f}%" if gpu_hot else f"low vram {gpu_free_mb / 1024:.1f} GB"
    return "gpu", ""


def cuda_available() -> tuple[bool, str]:
    """(ok, reason). Imports ctranslate2 only; doesn't load a model."""
    try:
        import ctranslate2
        n = ctranslate2.get_cuda_device_count()
    except Exception as e:                       # no CUDA driver / runtime
        return False, f"no CUDA ({e})"[:120]
    return (True, "") if n > 0 else (False, "no CUDA device")


def is_cached(model: str) -> bool:
    """Is the whisper model already downloaded? (No network.)"""
    import os
    if os.path.isdir(model):
        return True
    try:
        from faster_whisper.utils import download_model
        download_model(model, local_files_only=True)
        return True
    except Exception:
        return False


def is_cuda_error(e: BaseException) -> bool:
    s = f"{type(e).__name__} {e}".lower()
    return any(k in s for k in ("cuda", "cublas", "cudnn", "out of memory", "gpu"))


class Transcriber:
    """One faster-whisper model on one device, warmed up at load."""

    def __init__(self, model: str, device: str, compute_type: str, cpu_threads: int = 4,
                 language: str | None = "en", prompt: str | None = None):
        from faster_whisper import WhisperModel

        self.device, self.model_name = device, model
        self.model = WhisperModel(model, device=device, compute_type=compute_type, cpu_threads=cpu_threads)
        self.language, self.prompt = language or None, prompt
        self.transcribe(np.zeros(16000, np.int16))   # first real request doesn't pay init

    def transcribe(self, audio: np.ndarray) -> str:
        f = audio.astype(np.float32) / 32768.0
        segments, _ = self.model.transcribe(
            f, language=self.language, beam_size=1, vad_filter=False,
            condition_on_previous_text=False, without_timestamps=True,
            initial_prompt=self.prompt,
        )
        return " ".join(s.text.strip() for s in segments).strip()


class SpeechToText:
    """Picks the device per request. Use `transcribe()`, read `label`/`device`."""

    PREMIERE_CHECK_S = 10.0
    OOM_RETRY_S = 60.0

    def __init__(self, cfg: dict, device: str | None = None, hint_words: list[str] | None = None,
                 monitor=None, premiere_running=None, loader=None, cuda_check=None, on_change=None,
                 cached=None):
        self.cfg = cfg                           # read live: thresholds can change without a restart
        self.want = normalize_device(device or cfg.get("device", "gpu"))
        self.cpu_model = cfg.get("cpu_model") or "base.en"     # config.load migrates v1's `model`
        self.cpu_compute = cfg.get("cpu_compute_type") or "int8"
        self.gpu_model = cfg.get("gpu_model") or "large-v3-turbo"
        self.gpu_compute = cfg.get("gpu_compute_type") or "float16"
        self.prompt = ("Hey Claude. Start a task in " + ", ".join(hint_words) + ".") if hint_words else None
        self._loader = loader or self._load
        self._cuda_check = cuda_check or cuda_available
        self._cached = cached or is_cached
        self.downloading = False                 # GPU model being fetched in the background
        self._premiere = premiere_running or _premiere_running
        self.on_change = on_change               # callback(label) when the active device changes
        self.gpu: Transcriber | None = None
        self.cpu: Transcriber | None = None
        self.gpu_failed: str | None = None       # the GPU is off for this session, and why
        self._gpu_retry_at = 0.0                 # after an out-of-memory load
        self.choice, self.reason = "gpu", ""     # last decision and why
        self._premiere_seen, self._premiere_t = False, 0.0
        self._lock = threading.RLock()
        self._loading = False
        self.monitor = monitor
        if self.monitor is None and self.want == "gpu":
            from .sysload import LoadMonitor
            self.monitor = LoadMonitor()
        self._start()

    # -- loading -----------------------------------------------------------------
    def _load(self, device: str) -> Transcriber:
        c = self.cfg
        if device == "cuda":
            return Transcriber(self.gpu_model, "cuda", self.gpu_compute, language=c.get("language"),
                               prompt=self.prompt)
        return Transcriber(self.cpu_model, "cpu", self.cpu_compute, int(c.get("cpu_threads", 4)),
                           language=c.get("language"), prompt=self.prompt)

    def _try_gpu(self) -> bool:
        ok, why = self._cuda_check()
        if not ok:
            self._fail_gpu(why)
            return False
        try:
            t = time.perf_counter()
            model = self._loader("cuda")
            with self._lock:
                self.gpu = model
            print(f"[stt] {self.gpu_model} on GPU ({self.gpu_compute}) in {time.perf_counter() - t:.1f}s")
            return True
        except Exception as e:
            if "memory" in str(e).lower():       # transient: try again later
                self._gpu_retry_at = time.monotonic() + self.OOM_RETRY_S
                print(f"[stt] GPU out of memory loading {self.gpu_model}; CPU for now, retry in a minute")
            else:
                self._fail_gpu(f"GPU load failed: {e}"[:160])
            return False

    def _fail_gpu(self, why: str):
        self.gpu_failed, self.gpu = why, None
        print(f"[stt] GPU off for this session: {why}")

    def _ensure_cpu(self) -> Transcriber:
        with self._lock:
            if self.cpu is None:
                t = time.perf_counter()
                self.cpu = self._loader("cpu")
                print(f"[stt] {self.cpu_model} on CPU in {time.perf_counter() - t:.1f}s")
            return self.cpu

    def _start(self):
        if self.want == "gpu":
            self._decide()
            if self.choice == "gpu" and self.gpu is None and not self._loading:
                if self._cached(self.gpu_model):
                    self._try_gpu()              # downloaded already: load it now (a few seconds)
                else:
                    # Never block start-up on a ~1.6 GB download: the CPU model
                    # serves while the GPU one downloads and loads in the background.
                    print(f"[stt] {self.gpu_model} isn't downloaded yet: fetching it in the background")
                    self._loading = self.downloading = True
                    threading.Thread(target=self._load_gpu_bg, daemon=True, name="stt-gpu-load").start()
            self._decide()
        if self.device == "cpu":
            self._ensure_cpu()

    # -- the decision --------------------------------------------------------------
    def _gpu_hard_reason(self) -> str:
        r = (self.gpu_failed or "").lower()
        if "cuda error" in r:
            return "fallback: cuda error"
        if "no cuda" in r:
            return "fallback: no cuda"
        return "fallback: gpu load failed"

    def _check_premiere(self, force=False) -> bool:
        now = time.monotonic()
        if force or now - self._premiere_t >= self.PREMIERE_CHECK_S:
            self._premiere_t = now
            try:
                self._premiere_seen = bool(self._premiere())
            except Exception:
                self._premiere_seen = False
        return self._premiere_seen

    def _decide(self):
        """Set self.choice / self.reason from the current load."""
        c = self.cfg
        if self.want != "gpu":
            self.choice, self.reason = "cpu", ""
        elif self.gpu_failed:
            self.choice, self.reason = "cpu", self._gpu_hard_reason()
        elif c.get("yield_to_premiere") and self._check_premiere():
            self.choice, self.reason = "cpu", "fallback: premiere"
            self.gpu = None                      # opt-in: give Premiere the VRAM back
        else:
            m = self.monitor
            need = 0.0 if self.gpu is not None else vram_needed_mb(self.gpu_model, self.gpu_compute)
            choice, reason = choose(
                self.choice == "gpu",
                m.gpu_util() if m else None, m.gpu_free_mb() if m else None, need, m.cpu_pct() if m else None,
                busy=float(c.get("gpu_busy_pct", 70)), resume=float(c.get("gpu_resume_pct", 60)),
                cpu_busy=float(c.get("cpu_busy_pct", 85)))
            if choice == "gpu" and self.gpu is None and time.monotonic() < self._gpu_retry_at:
                choice, reason = "cpu", "gpu out of memory"
            self.choice, self.reason = choice, reason

    @property
    def device(self) -> str:
        return "cuda" if self.choice == "gpu" and self.gpu is not None else "cpu"

    @property
    def label(self) -> str:
        if self.device == "cuda":
            return f"gpu ({self.reason or short_model(self.gpu_model)})"
        if self.want == "gpu":
            if self.choice == "gpu":             # chosen, but the model is still loading
                return f"cpu (downloading {short_model(self.gpu_model)})" if self.downloading else "cpu (gpu loading)"
            if self.reason:
                return f"cpu ({self.reason})"
        return f"cpu ({short_model(self.cpu_model)})"

    def _changed(self, before: str):
        if self.label != before:
            print(f"[stt] now {self.label}")
            if self.on_change:
                self.on_change(self.label)

    def poll(self, background=True):
        """Re-decide from the current load; load whatever the choice needs.
        Called before each request and by a 10 s timer (keeps the island current)."""
        if self.want != "gpu":
            return
        with self._lock:
            before = self.label
            self._decide()
            need_gpu_load = (self.choice == "gpu" and self.gpu is None and not self.gpu_failed
                             and not self._loading)
            if need_gpu_load:
                self._loading = True
            self._changed(before)
        if need_gpu_load:                        # the CPU serves while it loads
            if background:
                threading.Thread(target=self._load_gpu_bg, daemon=True, name="stt-gpu-load").start()
            else:
                self._load_gpu_bg()

    def _load_gpu_bg(self):
        try:
            self._try_gpu()
        finally:
            self._loading = self.downloading = False
            with self._lock:
                before = self.label
                self._decide()
                self._changed(before)

    # -- transcription -------------------------------------------------------------
    def transcribe(self, audio: np.ndarray) -> str:
        self.poll(background=True)
        model = self.gpu if self.device == "cuda" else None
        if model is not None:
            try:
                return model.transcribe(audio)
            except Exception as e:
                if not is_cuda_error(e):
                    raise
                with self._lock:
                    before = self.label
                    self._fail_gpu(f"cuda error: {e}"[:160])
                    self._decide()
                    self._changed(before)
                print("[stt] retrying that request on the CPU")
        return self._ensure_cpu().transcribe(audio)

    def readings(self) -> str:
        m = self.monitor
        if not m:
            return ""
        g, f, c = m.gpu_util(), m.gpu_free_mb(), m.cpu_pct()
        return ", ".join(x for x in (f"gpu {g:.0f}%" if g is not None else "", f"vram free {f / 1024:.1f} GB"
                                     if f is not None else "", f"cpu {c:.0f}%" if c is not None else "") if x)


def _premiere_running() -> bool:
    from .winsys import process_running
    return process_running(PREMIERE)
