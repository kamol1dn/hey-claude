"""What every speech backend implements (M4c).

Backends: local (faster-whisper + Kokoro, speech/local.py), windows (WinRT
speech recognition and synthesis, SAPI fallbacks, speech/windows.py) and cloud
(Groq / OpenAI transcription, OpenAI / ElevenLabs voices, speech/cloud.py).
speech/manager.py picks one per request (`speech.backend`, `auto`), falls back
along `speech.fallback_order` when one fails, and unloads local models in lean
mode.

Audio in is always 16 kHz mono int16 (listen.RATE). Audio out is float32 mono
in -1..1 at the backend's `rate`.

A backend that can't serve a request raises SpeechError with a short reason a
person can read on the island ("no network", "no GROQ_API_KEY", "online speech
recognition is off"); the manager then moves on to the next backend.
"""
from __future__ import annotations

import numpy as np

RATE = 16000


class SpeechError(RuntimeError):
    """This backend can't do it right now. str(e) is a short, human reason."""


class SttBackend:
    """Speech-to-text. Subclasses override transcribe(); the rest are defaults."""

    name = "?"                 # local | windows | cloud
    # Cheap enough to run at every pause while the user talks (live partials
    # and the smart end-of-turn probe). False for anything that costs money,
    # has rate limits or can't transcribe a buffer quickly.
    probe_ok = False

    @property
    def label(self) -> str:
        """What the island shows, e.g. "groq turbo", "winrt online"."""
        return self.name

    def available(self) -> tuple[bool, str]:
        """A cheap check without network or model loads: (usable, why not)."""
        return True, ""

    @property
    def loaded(self) -> bool:
        """Models held in memory (only local ever is)."""
        return False

    def begin(self, live: bool = False):
        """An utterance is about to be recorded. live=True: from the real
        default microphone (not a simulation), so a backend that listens to
        the mic itself (WinRT) may start now."""

    def cancel(self):
        """The utterance was dropped (nothing said, Esc): stop whatever begin() started."""

    def transcribe(self, audio: np.ndarray, prompt: str | None = None,
                   languages: list[str] | None = None) -> str:
        """The final text of one utterance. `prompt`: Whisper's initial prompt
        (vocabulary). `languages`: limit language detection to these codes; a
        single one fixes the language. Raises SpeechError."""
        raise NotImplementedError

    def unload(self):
        """Free memory now (lean mode, "Free memory now")."""

    def reload(self):
        """Load again after unload() (blocking; called off the UI thread)."""

    def close(self):
        pass


class TtsBackend:
    """Text-to-speech that returns PCM, played by tts.Player (so echo
    cancellation and barge-in keep working)."""

    name = "?"
    pcm = True
    rate = 24000

    @property
    def label(self) -> str:
        return self.name

    def available(self) -> tuple[bool, str]:
        return True, ""

    def synth(self, text: str) -> np.ndarray:
        """float32 mono samples at self.rate. Raises SpeechError."""
        raise NotImplementedError

    def close(self):
        pass


def wav_bytes(audio: np.ndarray, rate: int = RATE) -> bytes:
    """16-bit mono WAV file bytes (for upload APIs)."""
    import io
    import wave
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(np.asarray(audio, np.int16).tobytes())
    return buf.getvalue()


def pcm_from_wav(data: bytes) -> tuple[np.ndarray, int]:
    """WAV file bytes (16-bit PCM, any rate, mono or stereo) -> (float32 mono, rate)."""
    import io
    import wave
    with wave.open(io.BytesIO(data), "rb") as w:
        rate, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if width != 2:
        raise SpeechError(f"unsupported wav sample width {width}")
    x = np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    return x, rate
