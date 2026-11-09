"""Microphone, wake word and record-until-silence.

Audio is 16 kHz mono int16 everywhere. The mic delivers 80 ms blocks
(1280 samples), which is what openWakeWord wants; webrtcvad looks at 20 ms
frames inside each block.
"""
from __future__ import annotations

import queue
import threading
import time
import wave
from pathlib import Path

import numpy as np

RATE = 16000
BLOCK = 1280          # 80 ms
VAD_FRAME = 320       # 20 ms


class MicSource:
    """Live microphone via sounddevice. Iterating yields int16 blocks."""

    def __init__(self, device=None):
        import sounddevice as sd

        self._q: queue.Queue = queue.Queue(maxsize=200)
        self._stream = sd.InputStream(
            samplerate=RATE, channels=1, dtype="int16", blocksize=BLOCK,
            device=device, callback=self._cb,
        )

    def _cb(self, indata, frames, t, status):
        try:
            self._q.put_nowait(indata[:, 0].copy())
        except queue.Full:
            pass  # consumer stalled; dropping audio beats growing memory

    def start(self):
        self._stream.start()

    def stop(self):
        self._stream.stop()

    def drain(self):
        while not self._q.empty():
            self._q.get_nowait()

    def __iter__(self):
        while True:
            yield self._q.get()


class WavSource:
    """Plays a 16 kHz mono wav into the pipeline in real time, then silence.

    Used by --simulate to test wake -> answer without speaking into the mic.
    """

    def __init__(self, path: str | Path, lead_silence: float = 1.0, tail_silence: float = 30.0):
        with wave.open(str(path), "rb") as w:
            assert w.getframerate() == RATE and w.getnchannels() == 1 and w.getsampwidth() == 2, \
                "simulate wav must be 16 kHz mono 16-bit"
            pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        lead = np.zeros(int(RATE * lead_silence), np.int16)
        tail = np.zeros(int(RATE * tail_silence), np.int16)
        self.audio = np.concatenate([lead, pcm, tail])
        self.audio_end_t: float | None = None   # wall time the last real sample was "heard"
        self._lead = len(lead)
        self._speech_len = len(pcm)

    def start(self):
        pass

    def stop(self):
        pass

    def drain(self):
        pass

    def __iter__(self):
        t0 = time.perf_counter()
        i = 0
        silence = np.zeros(BLOCK, np.int16)
        while True:   # after the file: endless silence, so tasks can finish
            target = t0 + (i + BLOCK) / RATE
            delay = target - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            if self.audio_end_t is None and i + BLOCK >= self._lead + self._speech_len:
                self.audio_end_t = time.perf_counter()
            yield self.audio[i:i + BLOCK] if i + BLOCK <= len(self.audio) else silence
            i += BLOCK


class WakeWord:
    def __init__(self, model: str, threshold: float):
        import openwakeword
        from openwakeword.model import Model

        if not model.endswith(".onnx"):
            # Stock models live inside the package; fetch them once.
            try:
                openwakeword.utils.download_models([model])
            except Exception:
                pass
        self.model = Model(wakeword_models=[model], inference_framework="onnx")
        self.threshold = threshold
        self.name = next(iter(self.model.models))

    def feed(self, block: np.ndarray) -> float:
        return float(self.model.predict(block)[self.name])

    def hit(self, block: np.ndarray) -> bool:
        return self.feed(block) >= self.threshold

    def reset(self):
        self.model.reset()


def level(block: np.ndarray) -> float:
    """0..1 loudness for the island's meter."""
    rms = float(np.sqrt(np.mean(block.astype(np.float32) ** 2)))
    return min(1.0, rms / 6000.0)


def record_utterance(blocks, cfg: dict, on_level=None, stop_event: threading.Event | None = None):
    """Pull blocks until speech has started and then `end_silence_ms` of silence.

    Returns (int16 audio, speech_end_wall_time) or (None, None) on timeout.
    """
    import webrtcvad

    vad = webrtcvad.Vad(int(cfg["vad_aggressiveness"]))
    end_frames = max(1, int(cfg["end_silence_ms"] / 20))
    start_deadline = time.perf_counter() + float(cfg["start_timeout"])
    max_blocks = int(cfg["max_seconds"] * RATE / BLOCK)

    pre_roll: list[np.ndarray] = []   # keep ~0.3 s before speech so the first word isn't clipped
    out: list[np.ndarray] = []
    started = False
    silent = 0
    voiced_run = 0
    last_voice_t = None
    for n, block in enumerate(blocks):
        if stop_event is not None and stop_event.is_set():
            return None, None
        if on_level:
            on_level(level(block))
        voiced = [vad.is_speech(block[i:i + VAD_FRAME].tobytes(), RATE)
                  for i in range(0, BLOCK, VAD_FRAME)]
        if not started:
            pre_roll.append(block)
            pre_roll = pre_roll[-4:]
            voiced_run = voiced_run + sum(voiced) if any(voiced) else 0
            if voiced_run >= 6:            # ~120 ms of speech: it's real
                started = True
                out.extend(pre_roll)
                last_voice_t = time.perf_counter()
            elif time.perf_counter() > start_deadline:
                return None, None
            continue
        out.append(block)
        for v in voiced:
            if v:
                silent = 0
                last_voice_t = time.perf_counter()
            else:
                silent += 1
        if silent >= end_frames or len(out) >= max_blocks:
            break
    return np.concatenate(out), last_voice_t
