"""Text-to-speech on its own thread.

Text arrives in streamed pieces; it's cut into sentences and each sentence is
spoken as soon as it is complete, so the first words play while the model is
still writing the rest.

Backends: sapi (pyttsx3, Windows voices, default), piper, kokoro.
"""
from __future__ import annotations

import queue
import re
import subprocess
import threading
import time

_SENT_END = re.compile(r"(.+?[.!?;:])(\s+|$)", re.S)


def clean_for_speech(s: str) -> str:
    s = re.sub(r"`{1,3}[^`]*`{1,3}", "", s)
    s = re.sub(r"[*_#>\[\]]", "", s)
    s = re.sub(r"https?://\S+", "a link", s)
    return " ".join(s.split())


class _Sapi:
    """Windows SAPI 5 (the engine pyttsx3 wraps), driven directly through COM.

    pyttsx3 2.99's runAndWait() hangs after a save_to_file and can return
    without speaking on repeated calls, so we talk to SAPI.SpVoice ourselves:
    async Speak + poll, which also lets stop() cut a sentence off mid-word.
    """

    SPEAK_ASYNC, PURGE = 1, 2
    RUNNING = 2

    def __init__(self, cfg):
        import pythoncom
        import win32com.client

        pythoncom.CoInitialize()
        self.v = win32com.client.Dispatch("SAPI.SpVoice")
        # pyttsx3-style words-per-minute -> SAPI rate (-10..10, 0 ~ 180 wpm)
        self.v.Rate = max(-10, min(10, round((int(cfg.get("rate", 190)) - 180) / 15)))
        if cfg.get("voice"):
            voices = self.v.GetVoices()
            for i in range(voices.Count):
                if cfg["voice"].lower() in voices.Item(i).GetDescription().lower():
                    self.v.Voice = voices.Item(i)
                    break
        self.on_start = None
        self._stop = False

    def speak(self, text: str):
        self._stop = False
        self.v.Speak(text, self.SPEAK_ASYNC)
        started = False
        while True:
            if self._stop:
                self.v.Speak("", self.SPEAK_ASYNC | self.PURGE)
                return
            if not started and self.v.Status.RunningState == self.RUNNING:
                started = True
                if self.on_start:
                    self.on_start()
            if self.v.WaitUntilDone(15):
                if not started and self.on_start:
                    self.on_start()
                return

    def stop(self):
        self._stop = True


class _Pcm:
    """Shared player for backends that synthesise raw audio."""

    def __init__(self):
        self.on_start = None
        self._stop = False

    def play(self, samples, rate):
        import sounddevice as sd

        self._stop = False
        if self.on_start:
            self.on_start()
        sd.play(samples, rate)
        while sd.get_stream().active:
            if self._stop:
                sd.stop()
                break
            time.sleep(0.02)

    def stop(self):
        self._stop = True


class _Piper(_Pcm):
    def __init__(self, cfg):
        super().__init__()
        if not cfg.get("piper_model"):
            raise ValueError("tts.piper_model must point at a piper .onnx voice")
        self.exe, self.model = cfg.get("piper_exe", "piper"), cfg["piper_model"]
        import json, os
        meta = self.model + ".json"
        self.rate = json.load(open(meta))["audio"]["sample_rate"] if os.path.exists(meta) else 22050

    def speak(self, text):
        import numpy as np

        out = subprocess.run([self.exe, "--model", self.model, "--output_raw"], input=text.encode("utf-8"),
                             capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        self.play(np.frombuffer(out, dtype=np.int16), self.rate)


class _Kokoro(_Pcm):
    def __init__(self, cfg):
        super().__init__()
        from kokoro_onnx import Kokoro  # pip install kokoro-onnx (CPU onnxruntime)

        self.k = Kokoro(cfg["kokoro_model"], cfg["kokoro_voices"])
        self.voice = cfg.get("kokoro_voice", "af_heart")

    def speak(self, text):
        samples, rate = self.k.create(text, voice=self.voice, speed=1.1, lang="en-us")
        self.play(samples, rate)


class Speaker:
    def __init__(self, cfg: dict, on_start=None, on_idle=None):
        self.cfg = cfg
        self.q: queue.Queue = queue.Queue()
        self.on_start = on_start or (lambda: None)
        self.on_idle = on_idle or (lambda: None)
        self._buf = ""
        self._gen = 0                     # bumped by stop(); stale sentences are skipped
        self.first_audio_t: float | None = None
        self.busy = False
        self._ready = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()
        self._ready.wait(15)

    def _run(self):
        backend = self.cfg.get("backend", "sapi")
        try:
            self.engine = {"sapi": _Sapi, "piper": _Piper, "kokoro": _Kokoro}[backend](self.cfg)
        except Exception as e:  # fall back to SAPI rather than going mute
            print(f"[tts] {backend} failed ({e}); using SAPI")
            self.engine = _Sapi(self.cfg)
        self.engine.on_start = self._started
        self._ready.set()
        while True:
            gen, text = self.q.get()
            if gen != self._gen or not text:   # cancelled by stop()
                self._maybe_idle()
                continue
            self.busy = True
            try:
                self.engine.speak(text)
            except Exception as e:
                print(f"[tts] {e}")
            self._maybe_idle()

    def _started(self):
        if self.first_audio_t is None:
            self.first_audio_t = time.perf_counter()
        self.on_start()

    def _maybe_idle(self):
        if self.q.empty():
            self.busy = False
            self.on_idle()

    # -- streaming API --------------------------------------------------------
    def begin(self):
        """Start a new reply (resets the first-audio timer)."""
        self._buf = ""
        self.first_audio_t = None

    def feed(self, chunk: str):
        self._buf += chunk
        while True:
            m = _SENT_END.match(self._buf)
            # Don't cut "3.5" or "e.g." — need whitespace after the stop.
            if not m or (m.group(2) == "" ):
                break
            sent = clean_for_speech(m.group(1))
            self._buf = self._buf[m.end():]
            if sent:
                self.q.put((self._gen, sent))

    def flush(self):
        rest = clean_for_speech(self._buf)
        self._buf = ""
        if rest:
            self.q.put((self._gen, rest))

    def announce(self, text: str):
        """Queue a standalone message (task finished) without disturbing a reply being streamed."""
        for sent in re.split(r"(?<=[.!?])\s+", clean_for_speech(text)):
            if sent:
                self.q.put((self._gen, sent))

    def say(self, text: str):
        self.begin()
        self.feed(text)
        self.flush()

    def stop(self):
        self._gen += 1
        self._buf = ""
        try:
            self.engine.stop()
        except Exception:
            pass

    def wait(self, timeout=60):
        """Block until everything queued has been spoken."""
        end = time.time() + timeout
        time.sleep(0.05)
        while (self.busy or not self.q.empty()) and time.time() < end:
            time.sleep(0.05)
