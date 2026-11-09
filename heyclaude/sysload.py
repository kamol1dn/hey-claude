"""GPU and CPU load, sampled in the background so a reading costs nothing.

GPU utilisation and free VRAM come from NVML (nvidia-ml-py, which talks to
the driver's nvml.dll; no nvidia-smi process). GPU utilisation is averaged
over the last ~0.5 s. CPU comes from psutil.cpu_percent, sampled every 0.5 s.
Any reading that isn't available (no NVIDIA driver, psutil missing) is None.
"""
from __future__ import annotations

import collections
import threading
import time


class LoadMonitor:
    def __init__(self, gpu_index: int = 0, interval: float = 0.1, window: float = 0.5):
        self.interval, self.window = interval, window
        self._gpu = collections.deque(maxlen=max(1, round(window / interval)))
        self._free_mb: float | None = None
        self._cpu: float | None = None
        self._h = None
        self._nvml = None
        try:
            import pynvml
            pynvml.nvmlInit()
            self._h = pynvml.nvmlDeviceGetHandleByIndex(gpu_index)
            self._nvml = pynvml
        except Exception:
            self._h = None
        try:
            import psutil
            psutil.cpu_percent(None)                # first call primes the counter
            self._psutil = psutil
        except Exception:
            self._psutil = None
        self._stop = threading.Event()
        self._sample()
        self._thread = threading.Thread(target=self._run, daemon=True, name="load-monitor")
        self._thread.start()

    def _sample(self, cpu=False):
        if self._h is not None:
            try:
                self._gpu.append(float(self._nvml.nvmlDeviceGetUtilizationRates(self._h).gpu))
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
        g = list(self._gpu)
        return sum(g) / len(g) if g else None

    def gpu_free_mb(self) -> float | None:
        return self._free_mb

    def cpu_pct(self) -> float | None:
        return self._cpu

    def close(self):
        self._stop.set()
