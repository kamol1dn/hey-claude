"""GPU and CPU load, sampled in the background so a reading costs nothing.

GPU utilisation and free VRAM come from NVML (nvidia-ml-py, which talks to
the driver's nvml.dll; no nvidia-smi process). GPU utilisation is averaged
over the last ~0.5 s. CPU comes from psutil.cpu_percent, sampled every 0.5 s.
Any reading that isn't available (no NVIDIA driver, psutil missing) is None.

hey-claude's own GPU work (whisper, Kokoro) mustn't count as "the GPU is
busy", or a transcription would push the next one (and the voice) onto the
CPU. Wrap it in `with monitor.own():`. NVML reports utilisation over its
last sample period (up to ~1 s), so samples taken during our own work and
for `own_tail_s` after it are left out, and the average falls back to the
latest samples taken while we weren't using the GPU (what other programs do).
"""
from __future__ import annotations

import collections
import contextlib
import threading
import time


class LoadMonitor:
    def __init__(self, gpu_index: int = 0, interval: float = 0.1, window: float = 0.5, own_tail_s: float = 1.0,
                 nvml=None, handle=None, psutil_mod=None, clock=time.monotonic, start: bool = True):
        self.interval, self.window, self.own_tail_s = interval, window, own_tail_s
        self.clock = clock
        n = max(1, round(window / interval))
        self._gpu = collections.deque(maxlen=n)          # clean samples only (not during our own work)
        self._free_mb: float | None = None
        self._cpu: float | None = None
        self._own = 0                                    # our GPU jobs running now
        self._own_until = 0.0                            # ...and the tail after the last one
        self._own_lock = threading.Lock()
        self.skipped = 0
        self._h = handle
        self._nvml = nvml
        if nvml is None:
            try:
                import pynvml
                pynvml.nvmlInit()
                self._h = pynvml.nvmlDeviceGetHandleByIndex(gpu_index)
                self._nvml = pynvml
            except Exception:
                self._h = None
        if psutil_mod is None:
            try:
                import psutil as psutil_mod
                psutil_mod.cpu_percent(None)              # first call primes the counter
            except Exception:
                psutil_mod = None
        self._psutil = psutil_mod
        self._stop = threading.Event()
        self._sample()
        if start:
            self._thread = threading.Thread(target=self._run, daemon=True, name="load-monitor")
            self._thread.start()

    # -- our own GPU work ------------------------------------------------------------
    @contextlib.contextmanager
    def own(self):
        with self._own_lock:
            self._own += 1
        try:
            yield
        finally:
            with self._own_lock:
                self._own -= 1
                self._own_until = self.clock() + self.own_tail_s

    def _ours(self) -> bool:
        with self._own_lock:
            return self._own > 0 or self.clock() < self._own_until

    def _sample(self, cpu=False):
        if self._h is not None:
            try:
                util = float(self._nvml.nvmlDeviceGetUtilizationRates(self._h).gpu)
                if self._ours():
                    self.skipped += 1
                else:
                    self._gpu.append(util)
                self._free_mb = self._nvml.nvmlDeviceGetMemoryInfo(self._h).free / 2 ** 20
            except Exception:
                pass
        if cpu and self._psutil is not None:
            self._cpu = float(self._psutil.cpu_percent(None))

    def _run(self):
        n = 0
        every = max(1, round(0.5 / self.interval))
        while not self._stop.wait(self.interval):
            n += 1
            self._sample(cpu=(n % every == 0))

    # -- readings ---------------------------------------------------------------
    def gpu_util(self) -> float | None:
        """Average of the last ~0.5 s of samples taken while hey-claude itself
        wasn't using the GPU."""
        g = list(self._gpu)
        return sum(g) / len(g) if g else None

    def gpu_free_mb(self) -> float | None:
        return self._free_mb

    def cpu_pct(self) -> float | None:
        return self._cpu

    def close(self):
        self._stop.set()
