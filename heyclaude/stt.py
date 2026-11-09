"""Speech-to-text with faster-whisper: GPU first, CPU as the fallback.

`stt.device: gpu` loads `stt.gpu_model` (large-v3-turbo) once into VRAM and
keeps it there. Whisper runs on the CPU (`stt.cpu_model`, loaded lazily the
first time it's needed) when:

- CUDA isn't available, or the GPU model fails to load (missing cuBLAS/cuDNN,
  out of memory...): CPU for the rest of the session;
- a transcription raises a CUDA error: that utterance is retried on the CPU,
  and the GPU isn't used again until restart;
- `stt.yield_to_premiere` is on and Adobe Premiere Pro is running: the GPU
  model is unloaded (VRAM back to Premiere) and reloaded in the background
  when Premiere closes. Never starts GPU work while Premiere runs.

`label` says what's active, e.g. "gpu (turbo)" or "cpu (fallback: premiere)".
"""
from __future__ import annotations

import threading
import time

import numpy as np

PREMIERE = "Adobe Premiere Pro.exe"


def normalize_device(v) -> str:
    v = str(v or "cpu").lower()
    return "gpu" if v in ("gpu", "cuda") or v.startswith("cuda") else "cpu"


def short_model(name: str) -> str:
    n = str(name)
    if "turbo" in n:
        return "turbo"
    return n.rsplit("/", 1)[-1].replace("faster-whisper-", "")


def cuda_available() -> tuple[bool, str]:
    """(ok, reason). Imports ctranslate2 only; doesn't load a model."""
    try:
        import ctranslate2
        n = ctranslate2.get_cuda_device_count()
    except Exception as e:                       # no CUDA driver / runtime
        return False, f"no CUDA ({e})"[:120]
    return (True, "") if n > 0 else (False, "no CUDA device")


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
    """Picks the device per utterance. Use `transcribe()`, read `label`/`device`."""

    PREMIERE_CHECK_S = 10.0

    def __init__(self, cfg: dict, device: str | None = None, hint_words: list[str] | None = None,
                 premiere_running=None, loader=None, cuda_check=None, on_change=None):
        self.cfg = cfg
        self.want = normalize_device(device or cfg.get("device", "gpu"))
        self.cpu_model = cfg.get("cpu_model") or "base.en"     # config.load migrates v1's `model`
        self.cpu_compute = cfg.get("cpu_compute_type") or "int8"
        self.gpu_model = cfg.get("gpu_model") or "large-v3-turbo"
        self.gpu_compute = cfg.get("gpu_compute_type") or "float16"
        self.prompt = ("Hey Claude. Start a task in " + ", ".join(hint_words) + ".") if hint_words else None
        self._loader = loader or self._load
        self._cuda_check = cuda_check or cuda_available
        self._premiere = premiere_running or _premiere_running
        self.on_change = on_change              # callback(label) when the active device changes
        self.gpu: Transcriber | None = None
        self.cpu: Transcriber | None = None
        self.gpu_failed: str | None = None      # reason the GPU is off for this session
        self.reason = ""                        # why we're on the CPU right now
        self._premiere_seen = False
        self._premiere_t = 0.0
        self._lock = threading.RLock()
        self._reloading = False
        self._start()

    # -- loading ---------------------------------------------------------------
    def _load(self, device: str) -> Transcriber:
        c = self.cfg
        if device == "cuda":
            return Transcriber(self.gpu_model, "cuda", self.gpu_compute, language=c.get("language"),
                               prompt=self.prompt)
        return Transcriber(self.cpu_model, "cpu", self.cpu_compute, int(c.get("cpu_threads", 4)),
                           language=c.get("language"), prompt=self.prompt)

    def _yield(self) -> bool:
        return bool(self.cfg.get("yield_to_premiere", True))

    def _check_premiere(self, force=False) -> bool:
        now = time.monotonic()
        if force or now - self._premiere_t >= self.PREMIERE_CHECK_S:
            self._premiere_t = now
            try:
                self._premiere_seen = bool(self._premiere())
            except Exception:
                self._premiere_seen = False
        return self._premiere_seen

    def _fail_gpu(self, why: str):
        self.gpu_failed, self.gpu = why, None
        print(f"[stt] GPU off for this session: {why}")

    def _try_gpu(self) -> bool:
        ok, why = self._cuda_check()
        if not ok:
            self._fail_gpu(why)
            return False
        try:
            t = time.perf_counter()
            self.gpu = self._loader("cuda")
            print(f"[stt] {self.gpu_model} on GPU ({self.gpu_compute}) in {time.perf_counter() - t:.1f}s")
            return True
        except Exception as e:
            self._fail_gpu(("out of memory" if "memory" in str(e).lower() else "GPU load failed") + f": {e}"[:160])
            return False

    def _ensure_cpu(self) -> Transcriber:
        if self.cpu is None:
            t = time.perf_counter()
            self.cpu = self._loader("cpu")
            print(f"[stt] {self.cpu_model} on CPU in {time.perf_counter() - t:.1f}s")
        return self.cpu

    def _start(self):
        if self.want == "gpu":
            if self._yield() and self._check_premiere(force=True):
                self.reason = "premiere"         # never start GPU work while Premiere runs
            elif self._try_gpu():
                self.reason = ""
                return
            else:
                self.reason = self._short_reason()
        else:
            self.reason = ""
        self._ensure_cpu()

    def _short_reason(self) -> str:
        r = (self.gpu_failed or "").lower()
        if "memory" in r:
            return "out of memory"
        if "cuda error" in r:
            return "cuda error"
        if "no cuda" in r:
            return "no cuda"
        return "gpu load failed" if r else ""

    # -- state -------------------------------------------------------------------
    @property
    def device(self) -> str:
        return "cuda" if self.gpu is not None and not self.reason else "cpu"

    @property
    def label(self) -> str:
        if self.device == "cuda":
            return f"gpu ({short_model(self.gpu_model)})"
        if self.want == "gpu" and self.reason:
            return f"cpu (fallback: {self.reason})"
        return f"cpu ({short_model(self.cpu_model)})"

    def _changed(self, before: str):
        if self.label != before:
            print(f"[stt] now {self.label}")
            if self.on_change:
                self.on_change(self.label)

    # -- Premiere ----------------------------------------------------------------
    def poll(self, force=False, background=True):
        """Check Premiere (cheap, rate-limited) and switch devices if needed.
        Called before each utterance and by a 10 s timer."""
        if self.want != "gpu" or self.gpu_failed:
            return
        with self._lock:
            before = self.label
            busy = self._yield() and self._check_premiere(force)
            if busy and self.reason != "premiere":
                self.reason = "premiere"
                self.gpu = None                  # free the VRAM for Premiere
                self._ensure_cpu()
                print("[stt] Premiere is running: whisper on the CPU, GPU model unloaded")
            elif not busy and self.reason == "premiere":
                if self.gpu is not None:
                    self.reason = ""
                elif not self._reloading:       # _reload_gpu reports the change itself
                    self._reloading = True
                    if background:
                        threading.Thread(target=self._reload_gpu, daemon=True, name="stt-gpu-load").start()
                    else:
                        self._reload_gpu()
                    return
            self._changed(before)

    def _reload_gpu(self):
        """Premiere closed: load the GPU model again; the CPU serves meanwhile."""
        try:
            if self._yield() and self._check_premiere(force=True):
                return
            ok = self._try_gpu()
            with self._lock:
                before = self.label
                self.reason = "" if ok else self._short_reason()
                self._changed(before)
        finally:
            self._reloading = False

    # -- transcription -----------------------------------------------------------
    def transcribe(self, audio: np.ndarray) -> str:
        self.poll(background=True)
        with self._lock:
            model = self.gpu if self.device == "cuda" else self._ensure_cpu()
        if model is self.gpu and model is not None:
            try:
                return model.transcribe(audio)
            except Exception as e:
                if not is_cuda_error(e):
                    raise
                with self._lock:
                    before = self.label
                    self._fail_gpu(f"cuda error: {e}"[:160])
                    self.reason = "cuda error"
                    self._changed(before)
                print("[stt] retrying that utterance on the CPU")
                return self._ensure_cpu().transcribe(audio)
        return model.transcribe(audio)


def _premiere_running() -> bool:
    from .winsys import process_running
    return process_running(PREMIERE)
