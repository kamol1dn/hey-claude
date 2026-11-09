"""openWakeWord's frozen feature models (melspectrogram + Google's speech
embedding), used offline in batches.

The contract the classifier head relies on (tested in tests/test_wake_train.py):
- one training example = 2.0 s of 16 kHz int16 audio (augment.WINDOW samples)
- `embed_windows` turns (N, 32000) into (N, 16, 96) float32: 16 embedding
  frames of 96 values, one per 80 ms, the newest last. That is exactly what
  openwakeword.Model feeds a wake-word model on every 80 ms block
  (`preprocessor.get_features(16)` -> shape (1, 16, 96)).
- long audio (room sound, false wakes) is cut into overlapping 2 s windows
  first (augment.slice_windows); openWakeWord's validation set comes as
  (frames, 96) rows, and `stream_to_windows` slides 16-frame windows over it.

These two ONNX models ship inside the openwakeword package, so nothing is
downloaded. On "cuda" they run through onnxruntime-gpu.
"""
from __future__ import annotations

import numpy as np

from .augment import WINDOW

N_FRAMES = 16
EMB_DIM = 96


def ensure_feature_models():
    """The melspectrogram and embedding models aren't in openwakeword's wheel;
    fetch them (~2 MB, from openWakeWord's GitHub release) if they're missing."""
    import os

    import openwakeword
    from openwakeword.utils import download_models
    d = os.path.join(os.path.dirname(openwakeword.__file__), "resources", "models")
    if not all(os.path.isfile(os.path.join(d, f)) for f in ("melspectrogram.onnx", "embedding_model.onnx")):
        download_models(["no-wake-word-model"])     # feature (+ VAD) models only


class Embedder:
    def __init__(self, device: str = "cpu", ncpu: int = 4, batch: int = 256):
        from openwakeword.utils import AudioFeatures
        ensure_feature_models()
        if device == "cuda":
            from ..tts import ort_cuda_available
            ok, why = ort_cuda_available()
            if not ok:
                raise RuntimeError(why)
        self.af = AudioFeatures(inference_framework="onnx", device="gpu" if device == "cuda" else "cpu", ncpu=ncpu)
        self.device = "cuda" if "CUDA" in self.af.onnx_execution_provider else "cpu"
        if device == "cuda" and self.device != "cuda":
            raise RuntimeError("onnxruntime fell back to the CPU for the feature models")
        self.ncpu, self.batch = ncpu, batch

    def embed_windows(self, x: np.ndarray, progress=None, cancel=None) -> np.ndarray:
        """(N, WINDOW) int16 -> (N, 16, 96) float32."""
        x = np.asarray(x, np.int16)
        if x.ndim != 2 or x.shape[1] != WINDOW:
            raise ValueError(f"need (N, {WINDOW}) int16 windows, got {x.shape}")
        out = np.empty((x.shape[0], N_FRAMES, EMB_DIM), np.float32)
        step = 1024
        for i in range(0, x.shape[0], step):
            if cancel and cancel():
                raise InterruptedError("cancelled")
            e = self.af.embed_clips(x[i:i + step], batch_size=self.batch, ncpu=self.ncpu)
            out[i:i + step] = e[:, -N_FRAMES:, :]
            if progress:
                progress(min(x.shape[0], i + step), x.shape[0])
        return out


def stream_to_windows(frames: np.ndarray, hop: int = 1) -> np.ndarray:
    """(frames, 96) -> (k, 16, 96) sliding windows (a view-like copy)."""
    frames = np.asarray(frames)
    if frames.shape[0] < N_FRAMES:
        return np.zeros((0, N_FRAMES, EMB_DIM), frames.dtype)
    idx = np.arange(0, frames.shape[0] - N_FRAMES + 1, hop)
    return np.stack([frames[i:i + N_FRAMES] for i in idx])
