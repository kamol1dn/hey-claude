"""The local backend: faster-whisper (stt.SpeechToText) and Kokoro (tts.VoiceEngine).

Whisper isn't loaded until local speech-to-text is first needed, so a
start-up on the windows or cloud backend never takes VRAM for it. Lean mode
(speech.lean) unloads both models after `speech.unload_after_min` idle and
reloads them on the next wake; manager.Speech drives that.
"""
from __future__ import annotations

import threading

import numpy as np

from .base import SttBackend


class LocalStt(SttBackend):
    name = "local"
    probe_ok = True                  # whisper on our own hardware: pause probes cost nothing

    def __init__(self, stt_cfg: dict, device: str = "gpu", hint_words=None, monitor=None, on_change=None,
                 factory=None):
        self.cfg, self.device_want = stt_cfg, device
        self.hint_words, self.monitor, self.on_change = list(hint_words or []), monitor, on_change
        self._factory = factory
        self.stt = None              # stt.SpeechToText, made on first use
        self._lock = threading.Lock()

    def ensure(self):
        """Make the SpeechToText (loads the model the load rule picks). Blocking."""
        with self._lock:
            if self.stt is None:
                if self._factory is not None:
                    self.stt = self._factory()
                else:
                    from ..stt import SpeechToText
                    self.stt = SpeechToText(self.cfg, device=self.device_want, hint_words=self.hint_words,
                                            on_change=self.on_change, monitor=self.monitor)
            return self.stt

    @property
    def label(self) -> str:
        return self.stt.label if self.stt is not None else "not loaded"

    @property
    def device(self) -> str:
        return getattr(self.stt, "device", "cpu") if self.stt is not None else "cpu"

    @property
    def loaded(self) -> bool:
        return self.stt is not None and bool(getattr(self.stt, "loaded", True))

    def transcribe(self, audio: np.ndarray, prompt: str | None = None, languages=None) -> str:
        return self.ensure().transcribe(audio, prompt=prompt, languages=languages)

    def unload(self):
        if self.stt is not None:
            self.stt.unload()

    def reload(self):
        s = self.ensure()
        if getattr(s, "unloaded", False):
            s.reload()

    def poll(self):
        if self.stt is not None and not getattr(self.stt, "unloaded", False):
            self.stt.poll()

    def readings(self) -> str:
        return self.stt.readings() if self.stt is not None else ""
