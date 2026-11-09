"""Offline stand-ins for tools/ux_stress.py --offline (and tests/test_ux_stress.py).

Everything that needs a GPU, a model, the network or a Claude login is
replaced; everything that decides what the user sees and hears is real:
the Assistant (mic loop, follow-up, converse, filler, widgets), the Speaker
(sentence splitting, idle/start callbacks), the Player's playback callback
(driven by a clock thread instead of a sound card), the Bridge and the QML
island.

- `SimPlayer`: tts.Player whose output stream is a thread calling the real
  `_cb` every 20 ms (no audio device).
- `ToneVoice`: a "voice" whose sentences last as long as Kokoro's would
  (~0.33 s a word) and have a level (so the island's wave moves).
- `ScriptedBrain`: brain.Brain's surface with canned, timed answers: a fast
  answer, a weather question on the local weather route (M5b: the card, then
  the summary, no agent), a tool lookup, a Windows volume change, a long answer.
- `RoomWake` / `RoomSTT` / `energy_vad`: the wake word fires when a "wake"
  clip ends in the SimRoom; the transcript is the text of the last clip said.
"""
from __future__ import annotations

import re
import threading
import time

import numpy as np

from heyclaude import tts as T
from heyclaude.listen import BLOCK, RATE
from heyclaude.vad import WebrtcVad

WORD_S = 0.33          # Kokoro's pace at speed 1.0, roughly


# ------------------------------------------------------------------ audio out
class SimPlayer(T.Player):
    """The real Player (its _cb queues, on_chunk/on_start/on_level, far tap),
    clocked by a thread instead of an output stream."""

    def __init__(self, far_tap=None):
        super().__init__(far_tap=far_tap, idle_close_s=4.0)
        self._clock = None

    def _open(self, rate):
        with self._lock:
            self._rate = rate
            self._idle_since = time.monotonic()
            if self._clock is not None:
                return
            self._stream = object()
        self._clock = threading.Thread(target=self._run, daemon=True, name="sim-player")
        self._clock.start()

    def _run(self):
        frames = 480                                # 20 ms at 24 kHz
        out = np.zeros((frames, 1), np.float32)
        t = time.perf_counter()
        while True:
            t += frames / float(self._rate or T.KOKORO_RATE)
            d = t - time.perf_counter()
            if d > 0:
                time.sleep(d)
            self._cb(out, frames, None, None)

    def close(self):
        pass


class ToneVoice:
    """Sentences as quiet noise, as long as Kokoro would take to say them."""
    pcm = True
    rate = T.KOKORO_RATE
    device = "cpu"

    def __init__(self, word_s: float = WORD_S, synth_s: float = 0.05):
        self.word_s, self.synth_s = word_s, synth_s
        self.rng = np.random.default_rng(3)

    def synth(self, text: str) -> np.ndarray:
        time.sleep(self.synth_s)
        n = max(1, len(text.split()))
        k = int((0.25 + n * self.word_s) * self.rate)
        env = 0.5 + 0.5 * np.abs(np.sin(np.linspace(0, n * np.pi, k)))
        return (self.rng.standard_normal(k).astype(np.float32) * 0.05 * env).astype(np.float32)


class ToneEngine:
    """tts.VoiceEngine's surface over ToneVoice."""

    def __init__(self, voice=None):
        self.current = voice or ToneVoice()
        self.sapi = None
        self.kokoro_failed = None

    def start(self):
        pass

    def pick(self):
        return self.current

    def synth(self, text):
        return self.current.synth(text), self.current.rate

    def _ensure_sapi(self):
        raise RuntimeError("no SAPI offline")

    @property
    def label(self):
        return "tone (offline)"


def make_speaker(asst, far_tap=None):
    sp = T.Speaker(asst.cfg["tts"], on_start=asst._tts_started, on_idle=asst._tts_idle, engine=ToneEngine(),
                   player=SimPlayer(far_tap=far_tap))
    sp.player.on_level = lambda v: asst._ui_emit("tts_level", v)
    sp.on_sentence = asst._sentence_started
    return sp


# ------------------------------------------------------------------ audio in
def speech_like(text: str, seed: int = 0, dbfs: float = -22.0) -> np.ndarray:
    """A burst of syllable-shaped noise as long as `text` would take to say."""
    rng = np.random.default_rng(seed)
    n = max(1, len(text.split()))
    k = int((0.15 + n * 0.30) * RATE)
    k += (-k) % BLOCK
    env = np.clip(np.sin(np.linspace(0, n * 2 * np.pi, k)) * 0.6 + 0.6, 0, 1)
    amp = 32768 * 10 ** (dbfs / 20)
    return np.clip(rng.standard_normal(k) * amp * env, -32767, 32767).astype(np.int16)


def energy_vad(threshold_rms: float = 400.0):
    def is_speech(frame: bytes) -> bool:
        x = np.frombuffer(frame, np.int16).astype(np.float32)
        return bool(len(x)) and float(np.sqrt(np.mean(x * x))) > threshold_rms
    return WebrtcVad(2, is_speech=is_speech)


class RoomWake:
    """The wake word fires on the block after a clip labelled "wake" ends."""
    label, fallback, threshold = "hey claude", None, 0.5

    def __init__(self, room, gate=None):
        self.room, self.gate = room, gate
        self._fired: set[int] = set()

    def hit(self, block) -> bool:
        now = self.room.clock()
        for i, c in enumerate(self.room.log):
            if c["label"] == "wake" and i not in self._fired and c["t1"] <= now:
                self._fired.add(i)
                if self.gate is not None and self.gate.suppressed():
                    return False
                return now - c["t1"] < 1.0
        return False

    def reset(self):
        pass


class RoomSTT:
    """Transcripts are the text of the last thing said in the room (not the wake)."""
    label = "offline"
    device = "cpu"
    want = "cpu"
    gpu_failed = False

    def __init__(self, room, delay_s: float = 0.15):
        self.room, self.delay = room, delay_s

    def transcribe(self, audio) -> str:
        time.sleep(self.delay)
        said = [c for c in self.room.log if c["label"] not in ("wake", "") and c["t0"] <= self.room.clock()]
        return said[-1]["label"] if said else ""

    def poll(self):
        pass

    def readings(self):
        return ""


# ------------------------------------------------------------------ the brain
def _weather():
    """The weather widget's own example data, with a neutral place name."""
    import json
    from heyclaude.config import ROOT
    d = json.loads((ROOT / "widgets" / "weather" / "widget.json").read_text(encoding="utf-8"))["example"]
    return dict(d, location="Springfield")


class _Weather:
    """weather_mcp.WeatherService's surface: the card from the widget's example data
    after a warm weather-mcp's ~0.2 s, and the summary built from the same data."""

    def __init__(self, delay_s: float = 0.2):
        self.delay_s, self.last = delay_s, None

    def query(self, text, prev_turn_was_weather=False):
        from heyclaude.weather_mcp.query import WeatherQuery, parse
        return parse(text, prev=self.last if prev_turn_was_weather else None) or WeatherQuery(text=text)

    def answer(self, q):
        from heyclaude.widgets import normalise
        time.sleep(self.delay_s)
        self.last = q
        d = _weather()
        say = "It's mainly clear and twenty one degrees in Springfield, with a small chance of rain later."
        return normalise({"widget": "weather", "data": d, "text": say}), say


class _Agent:
    on_tool_result = None
    on_tool_use = None
    chat = None


class _Convo:
    def __init__(self):
        self.turns = []

    def add(self, q, a, tier, task_id=None):
        self.turns.append((q, a, tier))

    def task_context(self):
        return ""


class ScriptedBrain:
    """brain.Brain's surface with canned answers chosen by keywords.

    Timings mimic the live app: the fast tier streams its first words after
    ~0.8 s; the tool agent thinks ~2.5 s, calls a tool, then streams."""
    backend = "scripted"

    def __init__(self, cfg, fast_first_s: float = 0.8, agent_think_s: float = 2.5, token_s: float = 0.04):
        self.cfg = cfg
        self.convo = _Convo()
        self.agent = _Agent()
        self.wx = _Weather()
        self.chat = None
        self.timing = {}
        self.warm = threading.Event()
        self.warm.set()
        self.projects = None
        self.fast_first_s, self.agent_think_s, self.token_s = fast_first_s, agent_think_s, token_s

    def _stream(self, text):
        for i, w in enumerate(re.findall(r"\S+\s*", text)):
            if i == 0:
                self.timing["first_token"] = self.fast_first_s
            time.sleep(self.token_s)
            yield w

    def handle(self, text):
        t = text.lower()
        self.timing = {"route": 0.01}
        if "weather" in t or "umbrella" in t:
            self.timing["route"] = "local-weather"
            yield ("weather", self.wx.query(text))
        elif "volume" in t:
            yield ("control", text)
        elif "undo" in t:
            yield ("undo",)
        else:
            time.sleep(self.fast_first_s)
            long = "story" in t or "explain" in t
            answer = ("Sure. Here's the short version. The tide comes in twice a day because the moon pulls the "
                      "oceans toward it, and the earth spins underneath that bulge. A second bulge sits on the far "
                      "side. So every spot on the coast passes through two highs and two lows. The sun adds a "
                      "smaller pull, which is why some tides are bigger than others.") if long else \
                "It's about three hundred and eighty four thousand kilometres away. That's roughly thirty earths."
            for chunk in self._stream(answer):
                yield ("say", chunk)

    def lookup(self, prompt):
        p = prompt.lower()
        time.sleep(self.agent_think_s * 0.4)
        if "weather" in p or "umbrella" in p:
            self._tool("t1", "mcp__weather__get_weather", {"location": "Springfield"}, _weather())
            time.sleep(self.agent_think_s * 0.6)
            yield from self._stream("It's mainly clear and twenty one degrees in Springfield. "
                                    "Later it gets up to twenty four, with only a small chance of rain.")
        elif "volume" in p:
            self._tool("t2", "mcp__windows__set_volume", {"level": 30},
                       {"ok": True, "old": 45, "new": 30, "muted": False})
            time.sleep(self.agent_think_s * 0.3)
            yield from self._stream("Volume is at thirty.")
        else:
            time.sleep(self.agent_think_s * 0.6)
            yield from self._stream("I checked, and there's nothing new.")

    def _tool(self, tid, name, args, result):
        ag = self.agent
        if ag.on_tool_use:
            ag.on_tool_use({"id": tid, "name": name, "phase": "start", "input": args})
        time.sleep(0.3)
        if ag.on_tool_use:
            ag.on_tool_use({"id": tid, "phase": "end"})
        if ag.on_tool_result:
            ag.on_tool_result(name, args, result, False)

    def forget(self):
        pass

    def close(self):
        pass

    def ask_task(self, t, q):
        yield "Nothing to report."


class FakeUndo:
    """undo.UndoStack's surface: remembers the volume change, "undoes" it."""

    def __init__(self):
        self.stack = []

    def can_undo(self, name, args, result):
        return name.endswith("set_volume")

    def record(self, name, args, result, effect=None):
        from types import SimpleNamespace
        self.stack.append(SimpleNamespace(tool=name, args=args, calls=[], effect=effect, result=result))

    def undo(self, effect_id=None):
        if not self.stack:
            return None
        ch = self.stack.pop()
        return ch, {"ok": True, "old": 30, "new": 45}


def install(asst, room, cfg):
    """Fill an Assistant with the offline parts (call before asst.run starts)."""
    from heyclaude.listen import SpeakingGate
    asst.monitor = None
    asst.far = asst.aec = asst.barge = asst.barge_vad = None
    asst.speaker = make_speaker(asst)
    asst.brain = ScriptedBrain(cfg)
    asst.brain.agent.on_tool_result = asst._on_tool_result
    asst.brain.agent.on_tool_use = lambda ev: asst._ui_emit("tool", ev)
    asst.undo_stack = FakeUndo()
    asst.wake_gate = asst.wake_gate or SpeakingGate(cfg["wake"])
    asst.wake = RoomWake(room, asst.wake_gate)
    asst.stt = RoomSTT(room)
    asst.vad = energy_vad()
    asst.tasks = None
    asst._ui_emit("voice", asst.speaker.label)
    asst._ui_emit("stt", "offline")
    asst.ready.set()
    asst.rest()
