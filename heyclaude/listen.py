"""Microphone, wake word and record-until-silence.

Audio is 16 kHz mono int16 everywhere. The mic delivers 80 ms blocks
(1280 samples), which is what openWakeWord wants; webrtcvad looks at 20 ms
frames inside each block.
"""
from __future__ import annotations

import os
import queue
import re
import threading
import time
import wave
from pathlib import Path

import numpy as np

RATE = 16000
BLOCK = 1280          # 80 ms
VAD_FRAME = 320       # 20 ms


class MicSource:
    """Live microphone via sounddevice. Iterating yields int16 blocks.

    close() releases the device (Windows' mic-in-use indicator goes off) and
    ends any iteration in progress; make a new MicSource to listen again.
    """

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

    def close(self):
        try:
            self._stream.stop()
            self._stream.close()
        except Exception:
            pass
        self.drain()
        self._q.put(None)          # wake and end a blocked iterator

    def drain(self):
        while not self._q.empty():
            try:
                self._q.get_nowait()
            except queue.Empty:
                break

    def __iter__(self):
        while True:
            b = self._q.get()
            if b is None:
                return
            yield b


def _read_wav(path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        assert w.getframerate() == RATE and w.getnchannels() == 1 and w.getsampwidth() == 2,             "simulate wav must be 16 kHz mono 16-bit"
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


class WavSource:
    """Plays 16 kHz mono wavs into the pipeline in real time, then silence.

    Used by --simulate to test wake -> answer without speaking into the mic.
    With several files (a simulated conversation), each one after the first
    waits until `ready()` says the assistant can take it (for example its
    follow-up window is open); silence is fed meanwhile, like a quiet room.
    """

    def __init__(self, path, lead_silence: float = 1.0, tail_silence: float = 30.0, ready=None,
                 gap: float = 0.4):
        paths = [path] if isinstance(path, (str, Path)) else list(path)
        self.clips = [_read_wav(p) for p in paths]
        self.names = [Path(p).name for p in paths]
        self.ready = ready or (lambda: True)
        self.lead, self.tail, self.gap = lead_silence, tail_silence, gap
        self.audio_end_t: float | None = None   # wall time the last real sample was "heard"
        self.played = 0                          # clips fully played so far
        self.audio = self.clips[0]               # (kept for callers that read it)

    @property
    def finished(self) -> bool:
        return self.played >= len(self.clips)

    def start(self):
        pass

    def stop(self):
        pass

    def close(self):
        pass

    def drain(self):
        pass

    def __iter__(self):
        t0 = time.perf_counter()
        n = 0                                    # blocks yielded, for real-time pacing
        silence = np.zeros(BLOCK, np.int16)

        def tick(block):
            nonlocal n
            delay = t0 + (n + 1) * BLOCK / RATE - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            n += 1
            return block

        for k, pcm in enumerate(self.clips):
            if k == 0:
                for _ in range(int(self.lead * RATE / BLOCK)):
                    yield tick(silence)
            else:
                while not self.ready():          # wait for the assistant, feeding silence
                    yield tick(silence)
                for _ in range(int(self.gap * RATE / BLOCK)):
                    yield tick(silence)
            if len(self.clips) > 1:
                print(f"[simulate] playing {self.names[k]} ({k + 1}/{len(self.clips)})")
            pad = np.concatenate([pcm, np.zeros((-len(pcm)) % BLOCK, np.int16)])
            for i in range(0, len(pad), BLOCK):
                yield tick(pad[i:i + BLOCK])
            self.audio_end_t = time.perf_counter()
            self.played = k + 1
        while True:   # after the files: endless silence, so tasks can finish
            yield tick(silence)


STOCK_FALLBACK = "hey_jarvis"     # used when the configured model is missing or broken
FALLBACK_THRESHOLD = 0.5          # the stock models are trained for 0.5


def resolve_wake_model(model, root: Path | None = None) -> tuple[str, str | None]:
    """`wake.model` -> (what to load, why it fell back to hey_jarvis or None).

    A stock name (hey_jarvis, alexa...) is returned as is. Anything that looks
    like a file (.onnx, or a path) is resolved against the repo folder when
    relative; a missing file falls back to the stock hey_jarvis model.
    """
    m = str(model or "").strip()
    if not m:
        return STOCK_FALLBACK, "no wake.model set"
    if not (m.lower().endswith((".onnx", ".tflite")) or "/" in m or "\\" in m):
        return m, None
    if root is None:
        from .config import ROOT as root
    p = Path(os.path.expandvars(os.path.expanduser(m)))
    if not p.is_absolute():
        p = root / p
    if p.suffix.lower() == ".tflite":          # we run onnxruntime; use the .onnx next to it
        p = p.with_suffix(".onnx")
    if not p.is_file():
        return STOCK_FALLBACK, f"{m} not found"
    return str(p), None


def wake_label(name_or_path: str) -> str:
    """'models/hey_claude.onnx' or 'hey_jarvis_v0.1' -> 'hey claude' / 'hey jarvis'."""
    base = os.path.splitext(os.path.basename(str(name_or_path)))[0]
    return re.sub(r"_v?[\d.]+$", "", base).replace("_", " ")


def _load_oww(model: str):
    import openwakeword
    from openwakeword.model import Model

    if not model.endswith(".onnx"):
        # Stock models live inside the package; fetch them once.
        try:
            openwakeword.utils.download_models([model])
        except Exception:
            pass
    return Model(wakeword_models=[model], inference_framework="onnx")


class SpeakingGate:
    """Says whether wake triggers should be ignored because the assistant itself
    is talking (TTS playing), or stopped less than `wake.speaking_tail_s` ago
    (room echo, audio still in the mic queue).

    `cfg` is the live `wake` config section; both keys are read on every call,
    so the settings window changes them without a restart.
    """

    def __init__(self, cfg: dict | None = None, clock=time.monotonic):
        self.cfg = cfg if cfg is not None else {}
        self.clock = clock
        self._speaking = False
        self._until = 0.0

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.get("suppress_while_speaking", True))

    def set_speaking(self, on: bool):
        if on:
            self._speaking = True
        elif self._speaking:
            self._speaking = False
            self._until = self.clock() + float(self.cfg.get("speaking_tail_s", 0.7))

    def busy(self) -> bool:
        """Talking, or within the tail after it, whatever suppress_while_speaking
        says. The follow-up window waits for this to clear."""
        return self._speaking or self.clock() < self._until

    def suppressed(self) -> bool:
        return self.enabled and self.busy()


class WakeWord:
    """openWakeWord on the CPU (onnxruntime).

    A missing or unloadable `wake.model` file falls back to the stock
    hey_jarvis model at threshold 0.5; `fallback` then says why.
    With a `gate` (SpeakingGate), triggers are ignored while the assistant is
    speaking. The model keeps hearing the audio meanwhile, and is reset when
    the gate opens, so its own voice still in the model's window can't fire it.
    """

    def __init__(self, model: str, threshold: float, gate: SpeakingGate | None = None, loader=None):
        load = loader or _load_oww
        path, why = resolve_wake_model(model)
        try:
            self.model = load(path)
        except Exception as e:
            if path == STOCK_FALLBACK:
                raise
            why, path = f"{model} failed to load ({e})", STOCK_FALLBACK
            self.model = load(path)
        self.fallback = why
        self.source = path
        self._threshold = FALLBACK_THRESHOLD if why else float(threshold)
        self.name = next(iter(self.model.models))
        self.label = wake_label(self.name)
        self.gate = gate
        self._gated = False               # the gate was closed on the last block
        self._gated_hit = False           # ...and the model fired behind it
        self.ignored = 0                  # triggers swallowed while speaking (for the log)
        if why:
            print(f"[wake] {why}; using the stock hey_jarvis model at threshold {FALLBACK_THRESHOLD} "
                  f"(say \"hey jarvis\"). See docs/train-hey-claude.md")
        else:
            print(f"[wake] {self.label} ({path}), threshold {self._threshold}")

    @property
    def threshold(self) -> float:
        return self._threshold

    @threshold.setter
    def threshold(self, value: float):
        # On the hey_jarvis fallback the configured threshold belongs to the
        # missing model; keep the stock one.
        if not self.fallback:
            self._threshold = float(value)

    def feed(self, block: np.ndarray) -> float:
        return float(self.model.predict(block)[self.name])

    def hit(self, block: np.ndarray) -> bool:
        score = self.feed(block)
        if self.gate is not None:
            if self.gate.suppressed():
                if score >= self._threshold and not self._gated_hit:
                    self._gated_hit = True
                    self.ignored += 1
                    print(f"[wake] ignored a trigger while speaking (score {score:.2f})")
                self._gated = True
                return False
            if self._gated:               # just stopped speaking: forget what it heard
                self._gated = self._gated_hit = False
                self.model.reset()
                return False
        return score >= self._threshold

    def reset(self):
        self.model.reset()


def level(block: np.ndarray) -> float:
    """0..1 loudness for the island's meter."""
    rms = float(np.sqrt(np.mean(block.astype(np.float32) ** 2)))
    return min(1.0, rms / 6000.0)


def trim_silences(blocks: list, voiced: list[bool], keep: int = 6) -> list:
    """Collapse every run of unvoiced blocks longer than `keep` (~0.5 s) to
    `keep` blocks, so a long think-pause doesn't cost whisper time."""
    out, run = [], []
    for b, v in zip(blocks, voiced):
        if v:
            out.extend(run[:keep] if len(run) > keep else run)
            run = []
            out.append(b)
        else:
            run.append(b)
    out.extend(run[:keep])
    return out


def record_utterance(blocks, cfg: dict, on_level=None, stop_event: threading.Event | None = None,
                     hold=None, is_speech=None, clock=time.perf_counter):
    """Pull blocks until speech has started and then `end_silence_ms` of silence.

    `hold` (a holdkey.HoldState, optional): while `hold.held`, silence never
    ends the utterance and the start timeout waits; after release the silence
    window counts from the later of release and the last speech. Once a hold
    was used the length cap is `max_utterance_s` instead of `max_seconds`,
    and long silences are trimmed out before returning.

    Returns (int16 audio, speech_end_wall_time) or (None, None) on timeout.
    """
    if is_speech is None:
        import webrtcvad
        vad = webrtcvad.Vad(int(cfg["vad_aggressiveness"]))
        is_speech = lambda frame: vad.is_speech(frame, RATE)  # noqa: E731
    end_frames = max(1, int(cfg["end_silence_ms"] / 20))
    start_timeout = float(cfg["start_timeout"])
    start_deadline = clock() + start_timeout
    max_blocks = int(cfg["max_seconds"] * RATE / BLOCK)
    cap_blocks = int(float(cfg.get("max_utterance_s", 180)) * RATE / BLOCK)

    pre_roll: list[np.ndarray] = []   # keep ~0.3 s before speech so the first word isn't clipped
    out: list[np.ndarray] = []
    flags: list[bool] = []            # per block in `out`: any speech in it?
    started = False
    silent = 0
    voiced_run = 0
    last_voice_t = None
    for block in blocks:
        if stop_event is not None and stop_event.is_set():
            return None, None
        if on_level:
            on_level(level(block))
        voiced = [is_speech(block[i:i + VAD_FRAME].tobytes()) for i in range(0, BLOCK, VAD_FRAME)]
        held = bool(hold is not None and hold.held)
        if not started:
            pre_roll.append(block)
            pre_roll = pre_roll[-4:]
            if held:                       # thinking first, then talking: keep waiting
                start_deadline = clock() + start_timeout
            voiced_run = voiced_run + sum(voiced) if any(voiced) else 0
            if voiced_run >= 6:            # ~120 ms of speech: it's real
                started = True
                out.extend(pre_roll)
                flags.extend([True] * len(pre_roll))
                last_voice_t = clock()
            elif clock() > start_deadline:
                return None, None
            continue
        out.append(block)
        flags.append(any(voiced))
        for v in voiced:
            if v:
                silent = 0
                last_voice_t = clock()
            else:
                silent += 1
        if held:
            silent = 0                     # the silence window restarts at release
        limit = cap_blocks if (hold is not None and hold.used) else max_blocks
        if (silent >= end_frames and not held) or len(out) >= limit:
            break
    if not started or not out:      # source closed before speech
        return None, None
    if hold is not None and hold.used:
        n = len(out)
        out = trim_silences(out, flags)
        if len(out) < n:
            print(f"[listen] hold: trimmed {(n - len(out)) * BLOCK / RATE:.1f}s of silence")
    return np.concatenate(out), last_voice_t
