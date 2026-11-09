"""Voice activity, end of turn, and transcripts while you talk.

- `SileroVad`: Silero VAD (the v6 model faster-whisper ships, on the CPU
  through onnxruntime) over 32 ms frames, with hysteresis. `WebrtcVad` is
  the old 20 ms webrtcvad, used if Silero can't load (`listen.vad: webrtc`).
- `turn_complete(text)` / `EndOfTurn`: how much silence ends the turn. A
  pause after a finished sentence ("what's the capital of Australia?") ends
  it after `short_silence_ms`; one after "and", "the", "um"... waits
  `long_silence_ms`; otherwise `end_silence_ms`.
- `TurnListener`: what record_utterance reports to while you talk. At each
  pause it transcribes the audio so far in the background; that text decides
  the silence window, and if you don't speak again it *is* the transcript, so
  speech-to-text costs nothing after you stop. While you talk it also sends
  partial transcripts to the island (GPU only by default).
"""
from __future__ import annotations

import os
import re
import threading
import time

import numpy as np

RATE = 16000


class SileroVad:
    """Streaming Silero VAD: `frames(block)` -> one bool per completed 32 ms frame."""

    FRAME = 512
    CONTEXT = 64
    frame_ms = 32.0

    def __init__(self, threshold: float = 0.5, path: str | None = None, session=None):
        if session is None:
            import onnxruntime as ort
            if path is None:
                import faster_whisper
                path = os.path.join(os.path.dirname(faster_whisper.__file__), "assets", "silero_vad_v6.onnx")
            so = ort.SessionOptions()
            so.inter_op_num_threads = so.intra_op_num_threads = 1
            so.log_severity_level = 4
            session = ort.InferenceSession(path, sess_options=so, providers=["CPUExecutionProvider"])
        self.sess = session
        self.threshold = float(threshold)
        self.reset()

    def reset(self):
        self._h = np.zeros((1, 1, 128), np.float32)
        self._c = np.zeros((1, 1, 128), np.float32)
        self._ctx = np.zeros(self.CONTEXT, np.float32)
        self._rest = np.zeros(0, np.float32)
        self._on = False
        self.last_prob = 0.0

    def probs(self, block: np.ndarray) -> list[float]:
        x = np.concatenate([self._rest, block.astype(np.float32) / 32768.0])
        out = []
        n = len(x) // self.FRAME
        for i in range(n):
            f = x[i * self.FRAME:(i + 1) * self.FRAME]
            inp = np.concatenate([self._ctx, f])[None, :]
            p, self._h, self._c = self.sess.run(None, {"input": inp, "h": self._h, "c": self._c})
            self._ctx = f[-self.CONTEXT:]
            out.append(float(np.ravel(p)[0]))
        self._rest = x[n * self.FRAME:]
        if out:
            self.last_prob = out[-1]
        return out

    def frames(self, block: np.ndarray) -> list[bool]:
        """Speech yes/no per frame; once speaking, stays on until the
        probability drops below threshold - 0.15 (Silero's usual hysteresis)."""
        flags = []
        for p in self.probs(block):
            if self._on:
                self._on = p >= self.threshold - 0.15
            else:
                self._on = p >= self.threshold
            flags.append(self._on)
        return flags


class WebrtcVad:
    frame_ms = 20.0

    def __init__(self, aggressiveness: int = 2, is_speech=None):
        if is_speech is None:
            import webrtcvad
            vad = webrtcvad.Vad(int(aggressiveness))
            is_speech = lambda frame: vad.is_speech(frame, RATE)  # noqa: E731
        self.is_speech = is_speech

    def reset(self):
        pass

    def frames(self, block: np.ndarray) -> list[bool]:
        return [self.is_speech(block[i:i + 320].tobytes()) for i in range(0, len(block) - 319, 320)]


def make_vad(cfg: dict):
    """listen.vad: silero (default) | webrtc. Silero failing to load -> webrtc."""
    if str(cfg.get("vad", "silero")).lower() == "silero":
        try:
            return SileroVad(float(cfg.get("silero_threshold", 0.5)))
        except Exception as e:
            print(f"[listen] silero VAD failed ({e}); using webrtcvad")
    return WebrtcVad(int(cfg.get("vad_aggressiveness", 2)))


# -- end of turn -----------------------------------------------------------------------
# Words a sentence doesn't end on: the speaker is mid-thought.
_CONTINUE = set("""
and or but so because cause if then than that which who whom whose where when while whereas although though
the a an my your his her its our their this these those some any every each no
to of in on at for with from by about into onto over under after before between through like as
is are was were be been am will would can could should shall may might must do does did have has had
um uh er erm hmm uhm ah like i'm it's what's that's there's let's
also plus versus vs
""".split())
_FILLER_END = re.compile(r"(\.\.\.|…|,|-|–|—)$")


def turn_complete(text: str | None) -> str:
    """'complete' | 'incomplete' | 'unknown' for a transcript of what was said so far."""
    t = (text or "").strip()
    if not t:
        return "unknown"
    if _FILLER_END.search(t):
        return "incomplete"
    words = re.findall(r"[a-z']+", t.lower())
    if not words:
        return "unknown"
    if words[-1] in _CONTINUE:
        return "incomplete"
    if t[-1] in ".?!":
        return "complete"
    return "unknown"


class EndOfTurn:
    """Silence (ms) that ends the turn, given the transcript at the current pause."""

    def __init__(self, cfg: dict):
        self.cfg = cfg                           # live listen section

    def needed_ms(self, text: str | None) -> float:
        c = self.cfg
        base = float(c.get("end_silence_ms", 700))
        if not c.get("smart_end", True):
            return base
        kind = turn_complete(text)
        if kind == "complete":
            return min(base, float(c.get("short_silence_ms", 300)))
        if kind == "incomplete":
            return max(base, float(c.get("long_silence_ms", 1200)))
        return base


class TurnListener:
    """Hooks record_utterance calls while recording one utterance.

    transcribe(audio) -> text is the speech-to-text call (serialised by the
    caller). `partials` sends live text to `on_partial` every
    `partial_interval_s` while speech continues.
    """

    def __init__(self, transcribe, cfg: dict, on_partial=None, partials: bool = False, clock=time.perf_counter):
        self.transcribe = transcribe
        self.cfg = cfg
        self.eot = EndOfTurn(cfg)
        self.on_partial = on_partial
        self.partials = partials and on_partial is not None
        self.clock = clock
        self._lock = threading.Condition()
        self._busy = False                       # a transcription is running
        self._pending = None                     # (kind, seq, audio) waiting for the worker
        self._seq = 0                            # pause number
        self._pause = None                       # seq of the pause we're in, None while talking
        self._texts: dict[int, str] = {}         # pause seq -> transcript of the audio up to it
        self._last_partial_t = clock()
        self.partial_text = ""
        self.reused = False

    # -- called by record_utterance ------------------------------------------------------
    def on_block(self, blocks: list, voiced: bool):
        if voiced:
            with self._lock:
                self._pause = None
            if self.partials and self.clock() - self._last_partial_t >= float(self.cfg.get("partial_interval_s", 0.8)):
                self._last_partial_t = self.clock()
                self._submit("partial", 0, blocks)

    def on_pause(self, blocks: list):
        """Silence started (probe_ms in): transcribe what was said so far."""
        with self._lock:
            self._seq += 1
            self._pause = self._seq
            seq = self._seq
        self._submit("pause", seq, blocks)

    def silence_needed_ms(self) -> float:
        with self._lock:
            text = self._texts.get(self._pause) if self._pause is not None else None
        return self.eot.needed_ms(text)

    # -- worker --------------------------------------------------------------------------
    def _submit(self, kind, seq, blocks):
        audio = np.concatenate(blocks) if blocks else np.zeros(0, np.int16)
        with self._lock:
            # A pause job replaces a waiting partial; a partial never replaces a pause.
            if self._pending is not None and self._pending[0] == "pause" and kind == "partial":
                return
            self._pending = (kind, seq, audio)
            if self._busy:
                return
            self._busy = True
        threading.Thread(target=self._work, daemon=True, name="stt-turn").start()

    def _work(self):
        while True:
            with self._lock:
                job, self._pending = self._pending, None
                if job is None:
                    self._busy = False
                    self._lock.notify_all()
                    return
            kind, seq, audio = job
            try:
                text = self.transcribe(audio) if len(audio) else ""
            except Exception as e:
                print(f"[stt] {kind} transcript failed: {e}")
                text = None
            with self._lock:
                if kind == "pause" and text is not None:
                    self._texts[seq] = text
                self._lock.notify_all()
            if text and self.on_partial:
                self.partial_text = text
                try:
                    self.on_partial(text)
                except Exception:
                    pass

    def final(self, timeout: float = 5.0) -> str | None:
        """The transcript of the last pause if nothing was said after it
        (waits for it if it's still running), else None."""
        with self._lock:
            seq = self._pause
            if seq is None:
                return None
            end = time.monotonic() + timeout
            while seq not in self._texts and (self._busy or self._pending is not None):
                left = end - time.monotonic()
                if left <= 0:
                    return None
                self._lock.wait(left)
            text = self._texts.get(seq)
        self.reused = text is not None
        return text
