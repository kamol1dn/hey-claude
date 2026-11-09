"""Speech-to-text with faster-whisper. CPU by default; the GPU is opt-in."""
from __future__ import annotations

import numpy as np


class Transcriber:
    def __init__(self, cfg: dict, use_gpu: bool = False, hint_words: list[str] | None = None):
        from faster_whisper import WhisperModel

        device = "cuda" if use_gpu else cfg.get("device", "cpu")
        compute = "float16" if device == "cuda" else cfg.get("compute_type", "int8")
        self.model = WhisperModel(
            cfg["model"], device=device, compute_type=compute,
            cpu_threads=int(cfg.get("cpu_threads", 4)),
        )
        self.language = cfg.get("language") or None
        self.device = device
        # Biasing prompt: spelling of project names helps whisper hear them.
        self.prompt = ("Hey Claude. Start a task in " + ", ".join(hint_words) + ".") if hint_words else None
        # Warm up so the first real request doesn't pay model init.
        self.transcribe(np.zeros(16000, np.int16))

    def transcribe(self, audio: np.ndarray) -> str:
        f = audio.astype(np.float32) / 32768.0
        segments, _ = self.model.transcribe(
            f, language=self.language, beam_size=1, vad_filter=False,
            condition_on_previous_text=False, without_timestamps=True,
            initial_prompt=self.prompt,
        )
        return " ".join(s.text.strip() for s in segments).strip()
