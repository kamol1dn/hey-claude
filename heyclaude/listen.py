"""Microphone, wake word and record-until-silence.

Audio is 16 kHz mono int16 everywhere. The mic delivers 80 ms blocks
(1280 samples), which is what openWakeWord wants; webrtcvad looks at 20 ms
frames inside each block.
"""
from __future__ import annotations

import collections
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
        if self._closed is not None:
            self._closed.set()

    def drain(self):
        pass

    room: "SimRoom | None" = None
    _closed: "threading.Event | None" = None

    def __iter__(self):
        if self.room is not None:
            return self._live_iter()
        return self._files_iter()

    def _files_iter(self):
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

    # -- live mode: a simulated room (tools/ux_stress.py) -------------------------------
    @classmethod
    def live(cls, room: "SimRoom") -> "WavSource":
        """A microphone stand-in that listens to `room` in real time: whatever is
        said there (room.say), its background noise and the echo of the
        assistant's own voice. Like a MicSource it can be closed and a new one
        opened later; what was said while it was closed is not heard."""
        src = cls.__new__(cls)
        src.clips, src.names, src.ready = [], [], (lambda: True)
        src.lead, src.tail, src.gap = 0.0, 0.0, 0.0
        src.audio_end_t, src.played, src.audio = None, 0, np.zeros(0, np.int16)
        src.room, src._closed = room, threading.Event()
        return src

    def _live_iter(self):
        room = self.room
        n = room.block_index()                   # start "now", like opening a real mic
        while not self._closed.is_set():
            delay = room.block_time(n + 1) - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            elif delay < -0.5:                   # consumer stalled (a GIL hog): drop, like a full mic queue
                n = room.block_index()
                continue
            yield room.block(n)
            n += 1



class SimRoom:
    """The world a simulated microphone hears (tools/ux_stress.py, --simulate's
    live mode): a shared clock, scheduled speech, background noise, and the echo
    of the assistant's own voice coming back from the speakers.

    Everything is in 80 ms blocks on one timeline (block n starts at
    t0 + n * 0.08 s), so a mic that is closed and opened again joins the room
    "now". `say()` and `echo_push()` are thread-safe.
    """

    def __init__(self, noise_dbfs: float | None = -62.0, echo_gain: float = 0.0, echo_delay_s: float = 0.06,
                 seed: int = 1, clock=time.perf_counter):
        self.clock = clock
        self.t0 = clock()
        self.noise_dbfs = noise_dbfs
        self.echo_gain, self.echo_delay = float(echo_gain), float(echo_delay_s)
        self._rng = np.random.default_rng(seed)
        self._lock = threading.Lock()
        self._speech: list[list] = []            # [start_sample, int16 pcm, label]
        self._echo: collections.deque = collections.deque()   # (start_sample, float32) of the speaker's output
        self._echo_pos = 0                       # next free sample on the echo timeline
        self.log: list[dict] = []                # what was said: {"label", "t0", "t1"} (perf_counter)
        self.extra_noise: tuple[float, float, float] | None = None   # (dbfs, t_from, t_to): a noisy stretch

    # -- the timeline ----------------------------------------------------------------
    def block_index(self) -> int:
        return int((self.clock() - self.t0) * RATE / BLOCK)

    def block_time(self, n: int) -> float:
        return self.t0 + n * BLOCK / RATE

    def sample_now(self) -> int:
        return int((self.clock() - self.t0) * RATE)

    # -- inputs ----------------------------------------------------------------------
    def say(self, pcm: np.ndarray, label: str = "", lead_s: float = 0.0) -> tuple[float, float]:
        """Schedule speech to start `lead_s` from now (never on top of earlier
        scheduled speech by the same call). Returns (t_start, t_end) in clock()
        time."""
        pcm = np.asarray(pcm, np.int16)
        with self._lock:
            start = (self.block_index() + 1) * BLOCK + int(lead_s * RATE)
            self._speech.append([start, pcm, label])
        t_start = self.t0 + start / RATE
        t_end = t_start + len(pcm) / RATE
        self.log.append({"label": label, "t0": t_start, "t1": t_end})
        return t_start, t_end

    def busy_until(self) -> float:
        """clock() time when the last scheduled speech ends."""
        with self._lock:
            ends = [s[0] + len(s[1]) for s in self._speech]
        return self.t0 + max(ends) / RATE if ends else self.clock()

    def echo_push(self, samples: np.ndarray):
        """What the speakers play (16 kHz int16, as aec.FarEnd gets it), as it plays."""
        if self.echo_gain <= 0:
            return
        s = np.asarray(samples, np.int16).astype(np.float32) * self.echo_gain
        with self._lock:
            now = self.sample_now() + int(self.echo_delay * RATE)
            start = max(now, self._echo_pos)
            self._echo.append((start, s))
            self._echo_pos = start + len(s)

    # -- output ------------------------------------------------------------------------
    def block(self, n: int) -> np.ndarray:
        a, b = n * BLOCK, (n + 1) * BLOCK
        out = np.zeros(BLOCK, np.float32)
        t = self.t0 + a / RATE
        db = self.noise_dbfs
        if self.extra_noise and self.extra_noise[1] <= t < self.extra_noise[2]:
            db = self.extra_noise[0] if db is None else max(db, self.extra_noise[0])
        if db is not None:
            amp = 32768.0 * 10 ** (db / 20)
            white = self._rng.standard_normal(BLOCK + 16).astype(np.float32)
            out += amp * np.convolve(white, np.ones(16, np.float32) / 4.0, "valid")[:BLOCK]   # soft, room-like
        with self._lock:
            keep = []
            for s in self._speech:
                start, pcm = s[0], s[1]
                end = start + len(pcm)
                if end <= a:
                    continue                     # over (or the mic missed it)
                keep.append(s)
                lo, hi = max(a, start), min(b, end)
                if lo < hi:
                    out[lo - a:hi - a] += pcm[lo - start:hi - start]
            self._speech = keep
            while self._echo and self._echo[0][0] + len(self._echo[0][1]) <= a:
                self._echo.popleft()
            for start, s in self._echo:
                if start >= b:
                    break
                lo, hi = max(a, start), min(b, start + len(s))
                if lo < hi:
                    out[lo - a:hi - a] += s[lo - start:hi - start]
        return np.clip(out, -32768, 32767).astype(np.int16)


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

    @property
    def speaking(self) -> bool:
        """Audio is playing right now (no tail)."""
        return self._speaking

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
                     hold=None, is_speech=None, clock=time.perf_counter, vad=None, listener=None,
                     preroll: list | None = None):
    """Pull blocks until speech has started and then enough silence.

    `vad` has `frames(block) -> [bool]` and `frame_ms` (vad.SileroVad or
    WebrtcVad); without one, `is_speech(20 ms frame bytes)` or webrtcvad.
    How much silence ends it: `listener.silence_needed_ms()` if a listener
    (vad.TurnListener) is given, which also hears `on_block` / `on_pause`;
    otherwise `end_silence_ms`.

    `hold` (a holdkey.HoldState, optional): while `hold.held`, silence never
    ends the utterance and the start timeout waits; after release the silence
    window counts from the later of release and the last speech. Once a hold
    was used the length cap is `max_utterance_s` instead of `max_seconds`,
    and long silences are trimmed out before returning. While `hold.muted`
    (Ctrl+Shift) the mic's audio is replaced with silence, so it's trimmed too.

    `preroll`: blocks heard just before (barge-in), counted as speech already.

    Returns (int16 audio, speech_end_wall_time) or (None, None) on timeout.
    """
    if vad is None:
        from .vad import WebrtcVad
        vad = WebrtcVad(int(cfg.get("vad_aggressiveness", 2)), is_speech=is_speech)
    frame_ms = float(vad.frame_ms)
    end_ms = float(cfg["end_silence_ms"])
    probe_ms = float(cfg.get("pause_probe_ms", 160))
    start_ms = float(cfg.get("start_speech_ms", 120))
    start_timeout = float(cfg["start_timeout"])
    start_deadline = clock() + start_timeout
    max_blocks = int(cfg["max_seconds"] * RATE / BLOCK)
    cap_blocks = int(float(cfg.get("max_utterance_s", 180)) * RATE / BLOCK)

    pre_roll: list[np.ndarray] = []   # keep ~0.3 s before speech so the first word isn't clipped
    out: list[np.ndarray] = []
    flags: list[bool] = []            # per block in `out`: any speech in it?
    started = False
    silent_ms = 0.0
    probed = False
    voiced_ms = 0.0
    last_voice_t = None
    if preroll:
        started = True
        out.extend(preroll)
        flags.extend([True] * len(preroll))
        last_voice_t = clock()
    for block in blocks:
        if stop_event is not None and stop_event.is_set():
            return None, None
        if hold is not None and getattr(hold, "muted", False):
            block = np.zeros_like(block)   # Ctrl+Shift: the mic is muted, the hold goes on
        if on_level:
            on_level(level(block))
        voiced = vad.frames(block)
        held = bool(hold is not None and hold.held)
        if not started:
            pre_roll.append(block)
            pre_roll = pre_roll[-4:]
            if held:                       # thinking first, then talking: keep waiting
                start_deadline = clock() + start_timeout
            voiced_ms = voiced_ms + sum(voiced) * frame_ms if any(voiced) else 0.0
            if voiced_ms >= start_ms:      # ~120 ms of speech: it's real
                started = True
                out.extend(pre_roll)
                flags.extend([True] * len(pre_roll))
                last_voice_t = clock()
                if listener is not None:
                    listener.on_block(out, True)
            elif clock() > start_deadline:
                return None, None
            continue
        out.append(block)
        flags.append(any(voiced))
        for v in voiced:
            if v:
                silent_ms = 0.0
                probed = False
                last_voice_t = clock()
            else:
                silent_ms += frame_ms
        if held:
            silent_ms = 0.0                # the silence window restarts at release
            probed = False
        if listener is not None:
            listener.on_block(out, any(voiced))
            if not probed and silent_ms >= probe_ms:
                probed = True
                listener.on_pause(out)
            need = listener.silence_needed_ms()
        else:
            need = end_ms
        limit = cap_blocks if (hold is not None and hold.used) else max_blocks
        if (silent_ms >= need and not held) or len(out) >= limit:
            break
    if not started or not out:      # source closed before speech
        return None, None
    if hold is not None and hold.used:
        n = len(out)
        out = trim_silences(out, flags)
        if len(out) < n:
            print(f"[listen] hold: trimmed {(n - len(out)) * BLOCK / RATE:.1f}s of silence")
    return np.concatenate(out), last_voice_t
