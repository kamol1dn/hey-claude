"""Echo cancellation and barge-in: hearing "stop" over its own voice.

The TTS player hands every sample it plays to `FarEnd` (16 kHz). The mic
loop runs each 80 ms block through `EchoCanceller`, WebRTC's AEC3 (the
AudioProcessingModule in the `livekit` package's prebuilt binary), with the
far-end audio as the reference, so what's left is mostly the user. Blocks
are processed as 10 ms frames, each paired with the next 10 ms of far-end
audio; AEC3 finds the speaker->mic delay itself.

`BargeIn` watches the cleaned audio while the assistant is speaking. Once it
hears `barge_in_ms` of speech (Silero, after a short grace period while the
filter converges at the start of a reply), it fires: the assistant stops
talking and records what you're saying, without the wake word.

Barge-in needs PCM playback (Kokoro or Piper). With SAPI there's no
reference signal, so it stays off.
"""
from __future__ import annotations

import collections
import threading
import time

import numpy as np

RATE = 16000
FRAME = 160          # 10 ms


class FarEnd:
    """Thread-safe FIFO of 16 kHz int16 playback samples."""

    def __init__(self, max_s: float = 3.0):
        self._buf: collections.deque = collections.deque()
        self._n = 0
        self._max = int(max_s * RATE)
        self._lock = threading.Lock()
        self.last_push = 0.0

    def push(self, samples: np.ndarray):
        s = np.asarray(samples, dtype=np.int16)
        if not len(s):
            return
        with self._lock:
            self._buf.append(s)
            self._n += len(s)
            while self._n > self._max and self._buf:     # consumer stalled: drop the oldest
                self._n -= len(self._buf.popleft())
            self.last_push = time.monotonic()

    def pull(self, n: int) -> np.ndarray:
        out = np.zeros(n, np.int16)
        got = 0
        with self._lock:
            while got < n and self._buf:
                head = self._buf[0]
                k = min(n - got, len(head))
                out[got:got + k] = head[:k]
                got += k
                if k == len(head):
                    self._buf.popleft()
                else:
                    self._buf[0] = head[k:]
            self._n -= got
        return out

    def clear(self):
        with self._lock:
            self._buf.clear()
            self._n = 0

    @property
    def pending(self) -> int:
        return self._n


def _livekit_apm(noise_suppression: bool):
    from livekit import rtc
    return rtc.AudioProcessingModule(echo_cancellation=True, noise_suppression=noise_suppression,
                                     high_pass_filter=True, auto_gain_control=False)


class EchoCanceller:
    """process(block) -> block with the assistant's own voice removed.

    `apm` is anything with process_reverse_stream(frame) / process_stream(frame)
    taking livekit AudioFrames; `frame_factory(bytes) -> frame` and
    `frame_data(frame) -> np.int16` let tests swap both out.
    """

    def __init__(self, far: FarEnd, apm=None, noise_suppression: bool = False, frame_factory=None,
                 frame_data=None):
        self.far = far
        self.apm = apm if apm is not None else _livekit_apm(noise_suppression)
        if frame_factory is None:
            from livekit import rtc
            frame_factory = lambda b: rtc.AudioFrame(b, RATE, 1, FRAME)  # noqa: E731
            frame_data = lambda f: np.frombuffer(bytes(f.data), dtype=np.int16).copy()  # noqa: E731
        self._frame, self._data = frame_factory, frame_data
        self.ms = 0.0                            # time spent, for the log

    def process(self, block: np.ndarray) -> np.ndarray:
        t = time.perf_counter()
        out = np.empty_like(block)
        for i in range(0, len(block) - FRAME + 1, FRAME):
            ref = self._frame(self.far.pull(FRAME).tobytes())
            self.apm.process_reverse_stream(ref)
            near = self._frame(np.ascontiguousarray(block[i:i + FRAME]).tobytes())
            self.apm.process_stream(near)
            out[i:i + FRAME] = self._data(near)
        self.ms = (time.perf_counter() - t) * 1000
        return out


def make_canceller(cfg: dict, far: FarEnd):
    """listen.aec on: an EchoCanceller, or None (with the reason printed)."""
    if not cfg.get("aec", True):
        return None
    try:
        return EchoCanceller(far, noise_suppression=bool(cfg.get("aec_noise_suppression", False)))
    except Exception as e:
        print(f"[aec] echo cancellation unavailable ({e}); barge-in is off")
        return None


class BargeIn:
    """Fires when the user talks over the assistant.

    feed(voiced_frames, frame_ms, rms) once per mic block while speaking;
    returns True once there has been `barge_in_ms` of speech (gaps up to
    `gap_ms` don't reset it) after `barge_in_grace_ms` from the start of the
    reply, and the block is louder than `barge_in_min_rms`.
    """

    def __init__(self, cfg: dict, clock=time.monotonic):
        self.cfg = cfg                           # live listen section
        self.clock = clock
        self.reset()

    def reset(self):
        self._since: float | None = None         # when the assistant started speaking
        self._speech_ms = 0.0
        self._gap_ms = 0.0
        self.fired = False

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.get("barge_in", True))

    def speaking(self, on: bool):
        if on and self._since is None:
            self._since = self.clock()
            self._speech_ms = self._gap_ms = 0.0
            self.fired = False
        elif not on:
            self.reset()

    def feed(self, voiced: list[bool], frame_ms: float, rms: float = 1e9) -> bool:
        if not self.enabled or self._since is None or self.fired:
            return False
        grace = float(self.cfg.get("barge_in_grace_ms", 400)) / 1000
        if self.clock() - self._since < grace:
            return False
        loud = rms >= float(self.cfg.get("barge_in_min_rms", 300))
        gap_max = float(self.cfg.get("barge_in_gap_ms", 100))
        for v in voiced:
            if v and loud:
                self._speech_ms += frame_ms
                self._gap_ms = 0.0
            else:
                self._gap_ms += frame_ms
                if self._gap_ms > gap_max:
                    self._speech_ms = 0.0
        if self._speech_ms >= float(self.cfg.get("barge_in_ms", 300)):
            self.fired = True
            return True
        return False


def rms(block: np.ndarray) -> float:
    return float(np.sqrt(np.mean(block.astype(np.float32) ** 2)))
