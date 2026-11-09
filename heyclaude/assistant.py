"""Glue: mic loop -> wake -> record -> STT -> brain -> TTS / tasks, plus UI updates."""
from __future__ import annotations

import collections
import csv
import threading
import time
import traceback

from . import config as config_mod
from .brain import Brain, Projects
from .config import STATE_DIR
from .conversation import FollowUp, is_end_phrase, strip_wake
from .listen import MicSource, SpeakingGate, WakeWord, WavSource, record_utterance
from .stt import SpeechToText, normalize_device
from .tasks import TaskManager
from .tts import Speaker, clean_for_speech
from .vad import TurnListener, make_vad


class _Either:
    """Duck-typed threading.Event for record_utterance's stop_event: set when
    any of the given checks is true."""

    def __init__(self, *checks):
        self.checks = [c for c in checks if c is not None]

    def is_set(self) -> bool:
        return any(c() for c in self.checks)


class _AnyEvent:
    """is_set() if any of the events is set (record_utterance takes one stop_event)."""

    def __init__(self, events):
        self.events = events

    def is_set(self) -> bool:
        return any(e.is_set() for e in self.events)


def _elapsed(t) -> str:
    s = int((t.ended or time.time()) - t.started)
    return f"{s // 60}:{s % 60:02d}"


class Assistant:
    def __init__(self, cfg: dict, bridge, stt_device: str | None = None, simulate: str | None = None,
                 text_mode=False, exit_when_done=False, mic_factory=None):
        self.cfg, self.ui = cfg, bridge
        # A stand-in for the microphone (tools/ux_stress.py: WavSource.live over a SimRoom);
        # everything else runs exactly as with the real mic.
        self.mic_factory = mic_factory
        self.stt_override = normalize_device(stt_device) if stt_device else None   # --gpu / --cpu
        self.simulate, self.text_mode = simulate, text_mode
        self.exit_when_done = exit_when_done
        self.mode = "loading"
        self.manual = threading.Event()
        self.gen = 0
        self.conv_lock = threading.Lock()
        self.timings: list[dict] = []
        self.ready = threading.Event()
        self.pending_tasks = 0
        self.tasks: TaskManager | None = None
        # Listening on/off. Off = the mic stream is closed, not just ignored.
        self.listening = threading.Event()
        if cfg.get("app", {}).get("listening", True) or simulate:
            self.listening.set()
        self.paused_until: float | None = None
        self._pause_timer: threading.Timer | None = None
        self._wakeup = threading.Event()        # pokes the mic loop out of its "off" wait
        self._abort_rec = threading.Event()     # cuts an utterance short when listening goes off
        self._reject_rec = threading.Event()    # "that wasn't me" while it records: drop the recording
        self.mic_open = False
        self.stopping = False
        self.on_listening = None                # callback(on: bool, paused_until: float | None)
        self.stt: SpeechToText | None = None
        self.hold = None                        # holdkey.HoldKey, set by __main__ (mic runs only)
        self.wake_gate = SpeakingGate(cfg["wake"])   # no wake triggers while it talks (+ a short tail)
        # Hotkey "talk or type" session (inputbox.InputSession drives these).
        self.input_open = False
        self._cancel_utt = threading.Event()    # typing / Esc cuts the session's recording short
        # After an answer: listen again without the wake word, once its voice has finished.
        self.followup = FollowUp(cfg.setdefault("conversation", {}), self.wake_gate, busy=self._busy)
        self._user_stopped: set[str] = set()   # tasks stopped by voice: no "was stopped" announcement
        self._reply: list[str] = []            # what the current request has said so far
        # M4: echo cancellation + barge-in, Silero VAD, transcripts while you talk.
        self.far = None                        # aec.FarEnd: what the speaker is playing
        self.aec = None                        # aec.EchoCanceller (None = off/unavailable)
        self.barge = None                      # aec.BargeIn
        self.vad = None                        # for recording
        self.barge_vad = None                  # for barge-in (its own state)
        self.monitor = None                    # sysload.LoadMonitor shared by STT and TTS
        self.turn: TurnListener | None = None  # the utterance being recorded
        # M2b: "that wasn't me". The ~2.5 s before the last wake-word trigger, kept
        # so a false wake can be saved as a negative for the next tune-up.
        self.last_wake: tuple[float, object] | None = None     # (time.time(), int16 audio)
        self.false_wake_dir = STATE_DIR / "wake_negatives"
        # M9: Windows settings changes -> island effects, undo, click-to-confirm power actions.
        self.undo_stack = None                 # undo.UndoStack, made in load()
        self._pending_power: dict[str, tuple[str, float]] = {}   # effect id -> (action, expires)

    # -- UI helpers -----------------------------------------------------------
    def _wake_label(self, label: str):
        sig = getattr(self.ui, "wake", None)
        if sig is not None:
            sig.emit(label)

    def announce(self, text: str):
        """Say something that isn't an answer (a timer ended), from any thread."""
        self._speaking_text = text
        self.speaker.announce(text)

    def _ui_emit(self, name: str, *args):
        """Emit an optional Bridge signal (newer islands have more of them)."""
        sig = getattr(self.ui, name, None)
        if sig is not None:
            try:
                sig.emit(*args)
            except Exception as e:
                print(f"[ui] {name}: {e}")

    def set_mode(self, mode, text=""):
        self.mode = mode
        self.ui.state.emit(mode, text)

    def rest(self):
        """Back to the resting state: idle pill, or the muted one while listening is off."""
        if self.listening.is_set() or self.mic_open:
            self.set_mode("idle")
        elif self.paused_until:
            self.set_mode("off", time.strftime("paused until %H:%M", time.localtime(self.paused_until)))
        else:
            self.set_mode("off", "listening off")

    # -- listening control (any thread) ----------------------------------------
    def set_listening(self, on: bool, pause_seconds: float | None = None):
        """Turn the wake word on/off. Off closes the microphone. With
        pause_seconds, it turns itself back on after that long."""
        if self._pause_timer:
            self._pause_timer.cancel()
            self._pause_timer = None
        self.paused_until = None
        if on:
            self.listening.set()
        else:
            self.listening.clear()
            self._abort_rec.set()
            self.followup.cancel()
            if pause_seconds:
                self.paused_until = time.time() + pause_seconds
                self._pause_timer = threading.Timer(pause_seconds, self.set_listening, args=(True,))
                self._pause_timer.daemon = True
                self._pause_timer.start()
        self._wakeup.set()
        until = time.strftime(" until %H:%M", time.localtime(self.paused_until)) if self.paused_until else ""
        print(f"[listen] {'on' if on else 'off'}{until}")
        if self.on_listening:
            self.on_listening(on, self.paused_until)
        if self.ready.is_set() and self.mode in ("idle", "off"):
            self.rest()

    def on_hold(self, held: bool):
        """Hold key went down/up (hook thread). Only shown while listening."""
        if self.mode in ("listening", "holding", "followup"):
            muted = bool(held and self.hold is not None and self.hold.muted)
            self.set_mode("holding" if held else "listening", "muted" if muted else "")

    # -- hotkey input session (talk or type) -----------------------------------
    def begin_input(self):
        """The input field opened: listen for one request, no wake word needed."""
        self.input_open = True
        self._cancel_utt.clear()
        self.talk_now()

    def cancel_input_mic(self):
        """The user started typing (or cancelled): stop the session's recording."""
        self._cancel_utt.set()
        self.manual.clear()

    def end_input(self):
        self.input_open = False
        self.cancel_input_mic()

    def submit_text(self, text: str):
        """A typed request: same router and tiers as a spoken one."""
        text = " ".join(text.split()) if "\n" not in text else text.strip()
        if not text:
            return
        print(f"[typed] {text!r}")
        self.speaker.stop()
        self.followup.cancel()
        self.request(text, source="typed")     # joins the shared conversation

    def _heard(self, text: str):
        sig = getattr(self.ui, "heard", None)
        if sig is not None:
            sig.emit(text)

    def talk_now(self):
        """Same as clicking the island: listen for one request without the wake
        word (opens the mic just for that if listening is off)."""
        self.manual.set()
        self._wakeup.set()

    def hush(self):
        """Stop talking and drop the answer in progress (stop button, Esc). Listening carries on."""
        self.gen += 1                       # in-flight answers check gen and give up
        self.speaker.stop()
        self.followup.cancel()
        if getattr(self, "mode", None) in ("speaking", "thinking"):
            print("[hush] stopped by the user")
            self.rest()

    def stop(self):
        self.stopping = True
        self._abort_rec.set()
        self._wakeup.set()

    # -- live settings ----------------------------------------------------------
    def apply_config(self, new: dict) -> list[str]:
        """Copy saved settings into the running config. Returns changed keys."""
        changed = config_mod.apply_in_place(self.cfg, new)
        brain = getattr(self, "brain", None)
        if "projects" in changed and brain is not None:
            brain.projects = Projects(self.cfg["projects"])
        wake = getattr(self, "wake", None)
        if "wake.model" in changed and wake is not None:
            self.reload_wake()
        elif "wake.threshold" in changed and wake is not None:
            wake.threshold = float(self.cfg["wake"]["threshold"])
        return changed

    def reload_wake(self):
        """Load `wake.model` again (the wizard's Apply / Undo) and swap it in.
        The mic loop reads self.wake per block, so the swap is atomic for it."""
        try:
            new = WakeWord(self.cfg["wake"]["model"], float(self.cfg["wake"]["threshold"]), gate=self.wake_gate)
        except Exception as e:
            print(f"[wake] couldn't load {self.cfg['wake']['model']}: {e}")
            return
        self.wake = new
        self._wake_label(new.label)

    def reject_wake(self, max_age_s: float = 180.0):
        """"That wasn't me": save the audio that triggered the last wake as a
        negative (state/wake_negatives/), and drop the request it started.
        Returns the saved path, or None if there was no recent wake."""
        lw = self.last_wake
        if lw is None or time.time() - lw[0] > max_age_s:
            return None
        self.last_wake = None
        from .wake_samples import write_wav
        path = self.false_wake_dir / time.strftime("false_%Y%m%d-%H%M%S.wav", time.localtime(lw[0]))
        write_wav(path, lw[1])
        print(f"[wake] saved a false wake as a negative: {path.name}")
        if self.mode == "listening":
            self._reject_rec.set()              # it's still recording what came after: stop
        elif self.mode in ("thinking", "speaking"):
            self.hush()
        return path

    # -- speech-to-text ---------------------------------------------------------
    def stt_device(self) -> str:
        """gpu | cpu: --gpu/--cpu flags win over stt.device."""
        return self.stt_override or normalize_device(self.cfg["stt"].get("device", "gpu"))

    def _stt_changed(self, label: str):
        sig = getattr(self.ui, "stt", None)
        if sig is not None:
            sig.emit(label)

    def _voice_changed(self, label: str):
        sig = getattr(self.ui, "voice", None)
        if sig is not None:
            sig.emit(label)

    def _partial(self, text: str):
        sig = getattr(self.ui, "partial", None)
        if sig is not None:
            sig.emit(text)

    def _partials_on(self) -> bool:
        """listen.partials: auto = only while whisper runs on the GPU."""
        v = str(self.cfg["listen"].get("partials", "auto")).lower()
        if v in ("false", "off", "no", "0"):
            return False
        return v in ("true", "on", "yes", "1") or getattr(self.stt, "device", "cpu") == "cuda"

    def _stt_poll_loop(self):
        """Re-check GPU/CPU load every 10 s so the island stays current (each
        request also re-checks right before transcribing)."""
        while not self.stopping:
            time.sleep(10)
            try:
                self.stt.poll()
            except Exception:
                traceback.print_exc()

    def push_tasks(self, *_):
        if not self.tasks:
            return
        rows = []
        for t in list(self.tasks.tasks.values())[-8:]:
            rows.append(dict(id=t.id, project=t.project, prompt=t.prompt, status=t.status,
                             activity=t.activity, summary=t.summary, session_id=t.session_id,
                             path=t.path, log=t.log, elapsed=_elapsed(t)))
        self.ui.tasks.emit(rows)

    # -- startup --------------------------------------------------------------
    def load(self):
        c = self.cfg
        gpu_wanted = self.stt_device() == "gpu" or str(c["tts"].get("device", "gpu")).lower() in ("gpu", "cuda")
        if gpu_wanted and self.monitor is None:
            try:
                from .sysload import LoadMonitor
                self.monitor = LoadMonitor()
            except Exception as e:
                print(f"[load] no load monitor: {e}")
        if not self.text_mode:
            from .aec import BargeIn, FarEnd, make_canceller
            self.far = FarEnd()
            self.aec = make_canceller(c["listen"], self.far)
            self.barge = BargeIn(c["listen"])
        self.set_mode("loading", "loading voice…")
        self.speaker = Speaker(c["tts"], on_start=self._tts_started, on_idle=self._tts_idle, stt_cfg=c["stt"],
                               monitor=self.monitor, far_tap=self.far.push if self.aec else None,
                               on_label=self._voice_changed)
        self._voice_changed(self.speaker.label)
        player = getattr(self.speaker, "player", None)
        if player is not None and hasattr(player, "on_level"):
            player.on_level = lambda v: self._ui_emit("tts_level", v)     # M5: the island's outward wave
        if hasattr(self.speaker, "on_sentence"):
            self.speaker.on_sentence = self._sentence_started              # M5: text follows the voice
        self.tasks = TaskManager(c, on_change=self.push_tasks, on_finish=self._task_finished)
        self.push_tasks()
        self.set_mode("loading", "loading brain…")
        self.brain = Brain(c)
        try:
            from .undo import UndoStack
            self.undo_stack = UndoStack()
        except Exception as e:                  # the settings tools' modules failed to import
            print(f"[undo] off: {e}")
        if self.brain.agent is not None:
            self.brain.agent.on_tool_result = self._on_tool_result
            self.brain.agent.on_tool_use = lambda ev: self._ui_emit("tool", ev)       # M5: service icons
        for chat in (self.brain.chat, getattr(self.brain.agent, "chat", None)):
            if chat is not None and hasattr(chat, "on_usage"):
                chat.on_usage = lambda info: self._ui_emit("usage", info)            # M5: plan-usage pill
        if not self.text_mode:
            self.set_mode("loading", "loading wake word…")
            try:
                from .wake_models import ensure_default_model
                msg = ensure_default_model(c["wake"]["model"])     # first run: fetch the shared model
                if msg:
                    print(msg)
            except Exception as e:
                print(f"[wake] default model check failed: {e}")
            self.wake = WakeWord(c["wake"]["model"], float(c["wake"]["threshold"]), gate=self.wake_gate)
            self._wake_label(self.wake.label)
            self.set_mode("loading", f"loading whisper ({self.stt_device()})…")
            self.stt = SpeechToText(c["stt"], device=self.stt_device(), hint_words=list(c["projects"]),
                                    on_change=self._stt_changed, monitor=self.monitor)
            self.vad = make_vad(c["listen"])
            self.barge_vad = make_vad(c["listen"]) if self.aec else None
            self._stt_changed(self.stt.label)
            if self.stt.want == "gpu" and not self.stt.gpu_failed:
                threading.Thread(target=self._stt_poll_loop, daemon=True, name="stt-load").start()
        self.ready.set()
        self.rest()
        load = f" [{self.stt.readings()}]" if self.stt and self.stt.readings() else ""
        print(f"[ready] fast path: {self.brain.backend}; stt: {self.stt.label if self.stt else '-'}; "
              f"voice: {self.speaker.label}; vad: {type(self.vad).__name__ if self.vad else '-'}; "
              f"aec: {'on' if self.aec else 'off'}{load}")

    # -- TTS callbacks --------------------------------------------------------
    def _tts_started(self):
        self.wake_gate.set_speaking(True)
        if self.mode in ("listening",):
            return
        if self._filler and not self._speaking_text.strip():
            # The filler ("Let me check.") is a status line of the turn: shown once,
            # never as the answer, and the answer's text never includes it.
            self.set_mode("thinking", self._filler)
            return
        self.set_mode("speaking", self._speaking_text)

    def _tts_idle(self):
        self.wake_gate.set_speaking(False)
        if self.mode in ("speaking",):
            if self.conv_lock.locked() and not self._converse_done:
                # The voice caught up with the answer while more of it is still coming
                # (a slow model, a long tool call): stay on the answer, don't drop to idle
                # and start it over (converse() settles it when the answer is complete).
                return
            if self.followup.state == "armed":
                # The follow-up window opens in a moment (after the speaking tail):
                # the island goes straight from the answer to "go on", not via idle.
                threading.Timer(2.5, self._rest_if_still_speaking).start()
            else:
                self.rest()
        self._maybe_exit()

    def _rest_if_still_speaking(self):
        """Safety net for a follow-up window that never opened (listening switched off...)."""
        sp = getattr(self, "speaker", None)
        if self.mode == "speaking" and not (sp and sp.busy) and self.followup.state != "open":
            self.rest()

    def _sentence_started(self, text: str, secs: float):
        """A sentence starts playing: the island reveals its words in step with the voice."""
        if self._filler and text.strip() == clean_for_speech(self._filler).strip():
            return                                  # the filler: a status line, not answer text
        self._ui_emit("spoken", text, float(secs))

    _speaking_text = ""
    _filler = ""

    def _maybe_exit(self):
        if (self.exit_when_done and self._sim_done and self.pending_tasks == 0
                and not self.speaker.busy and not self.conv_lock.locked()):
            threading.Timer(0.5, self.ui.quit.emit).start()

    _sim_done = False
    _sim_idle_end = False                       # --simulate: last utterance asked for nothing

    # -- M9: effects, undo, confirmations ----------------------------------------
    def _emit_effect(self, ev: dict):
        sig = getattr(self.ui, "effect", None)
        if sig is not None:
            sig.emit(ev)

    def _on_tool_result(self, name: str, args: dict, result, is_error: bool):
        """The agent ran a tool (its process's thread): show the effect, remember the undo."""
        if is_error:
            return
        try:                                    # M5: a widget straight from the tool result (weather)
            from .widgets import from_tool
            spec = from_tool(name, args, result)
            if spec is not None:
                print(f"[widget] {spec['widget']} from {name}")
                self._ui_emit("widget", spec)
        except Exception as e:
            print(f"[widget] {name}: {e}")
        from . import effects
        undoable = self.undo_stack is not None and self.undo_stack.can_undo(name, args, result)
        try:
            ev = effects.build(name, args, result, undoable=undoable)
        except Exception as e:
            print(f"[effects] {name}: {e}")
            return
        if ev is None:
            return
        if undoable:
            self.undo_stack.record(name, args, result, effect=ev)
        if ev["effect"] == "confirm" and ev["extra"].get("action"):
            self._pending_power[ev["id"]] = (ev["extra"]["action"], time.time() + ev["ttl_s"])
        print(f"[effect] {ev['effect']} {ev['label']}: {ev['text_from']} -> {ev['text_to']}"
              + (" (undo)" if undoable else ""))
        self._emit_effect(ev)
        if ev["effect"] == "media":
            self._fetch_art(ev["id"])

    def _fetch_art(self, effect_id: str):
        """Album art for the media card, read here (it never goes to the model)."""
        def go():
            try:
                from .mcp_windows import media
                from .mcp_windows._util import com_init
                com_init()
                art = media.album_art()
            except Exception as e:
                print(f"[effects] no album art: {e}")
                return
            sig = getattr(self.ui, "effect_art", None)
            if art and sig is not None:
                sig.emit(effect_id, art)
        threading.Thread(target=go, daemon=True, name="album-art").start()

    def undo(self, effect_id: str | None = None, note: bool = False) -> str:
        """Undo a change (the newest, or the one whose Undo button was clicked).
        Returns what to say. Any thread. `note`: add it to the conversation
        (a click; a spoken "undo that" is recorded as its own turn)."""
        if self.undo_stack is None:
            return "Undo isn't available."
        try:
            done = self.undo_stack.undo(effect_id)
        except Exception as e:
            traceback.print_exc()
            return f"I couldn't undo that: {str(e)[:80]}."
        if done is None:
            return "There's nothing to undo." if effect_id is None else "That can't be undone any more."
        ch, res = done
        from . import effects
        from .undo import describe
        print(f"[undo] {ch.tool} {ch.args} -> {ch.calls}")
        if ch.effect:
            ev = effects.reversed_event(ch.effect, res)
            self._emit_effect(ev)
            if ev["effect"] == "media":
                self._fetch_art(ev["id"])
        if note and self.brain.convo is not None:
            self.brain.convo.add("", f"(Undid a change from the island: {describe(ch)})", "local")
        return describe(ch)

    def undo_clicked(self, effect_id: str):
        """The island's Undo button (UI thread): undo off the UI thread, no speech."""
        threading.Thread(target=self.undo, args=(effect_id, True), daemon=True, name="undo").start()

    def confirm_clicked(self, effect_id: str):
        """The island's Confirm button: the only way a sleep/restart/shutdown runs."""
        action, until = self._pending_power.pop(effect_id, (None, 0))
        if not action or time.time() > until + 1:
            print("[power] confirmation expired")
            return
        print(f"[power] confirmed by click: {action}")
        from .mcp_windows import power
        threading.Thread(target=power.perform_power_action, args=(action,), daemon=True).start()

    def cancel_clicked(self, effect_id: str):
        if self._pending_power.pop(effect_id, None):
            print("[power] cancelled")

    # -- task callbacks -------------------------------------------------------
    def _task_finished(self, t):
        self.pending_tasks = max(0, self.pending_tasks - 1)
        print(f"[task {t.id}] {t.status}: {t.summary}  (resume: {t.resume_cmd})")
        self.push_tasks()
        if t.id in self._user_stopped:          # "stop the task": already answered
            self._user_stopped.discard(t.id)
            return
        verb = {"done": "is done", "failed": "failed", "stopped": "was stopped"}.get(t.status, "ended")
        msg = f"Your {t.project} task {verb}. {t.summary}"
        self._speaking_text = msg
        self.speaker.announce(msg)
        if self.brain.convo is not None:
            self.brain.convo.add("", msg, "local", task_id=t.id)
        self._arm_followup()     # "what did it change?" works without the wake word

    def _busy(self) -> bool:
        """Still answering: a request running, or speech queued or playing."""
        sp = getattr(self, "speaker", None)
        return self.conv_lock.locked() or bool(sp and (sp.busy or not sp.q.empty()))

    def _arm_followup(self):
        if (self.listening.is_set() or self.simulate) and not self.text_mode:
            self.followup.arm()

    def _sim_ready(self) -> bool:
        """--simulate with several clips: play the next one into the follow-up
        window, or once everything is idle (and tasks have finished)."""
        if self.pending_tasks:
            return False
        if self.followup.is_open and self.followup.opens != self._sim_window:
            self._sim_window = self.followup.opens        # one clip per window, not two into the same one
            return True
        return self.followup.state == "idle" and not self._busy() and self.mode == "idle"

    _sim_window = 0

    # -- main loop ------------------------------------------------------------
    def run(self):
        """Assistant thread. A crash restarts it once; a second crash shows an error."""
        restarts = 0
        while True:
            try:
                if not self.ready.is_set():
                    self.load()
                return self._run()
            except Exception as e:
                traceback.print_exc()
                if self.stopping:
                    return
                if restarts >= 1:
                    self.set_mode("error", f"stopped after a crash: {e}"[:200])
                    return
                restarts += 1
                print(f"[crash] assistant thread crashed ({e}); restarting it once")
                self.set_mode("loading", "restarting after an error…")
                if not self.ready.is_set() and getattr(self, "brain", None) is not None:
                    try:                     # half-loaded: drop the processes it started
                        self.brain.close()
                    except Exception:
                        pass
                time.sleep(2)

    def _run(self):
        if self.text_mode:
            return self._text_loop()
        if self.simulate:
            self.brain.warm.wait(120)   # measure a warmed-up app, like real use after start-up
            src = WavSource(self.simulate, ready=self._sim_ready)
            try:
                self._pump(src, one_shot=False)
            finally:
                src.close()
            return
        while not self.stopping:
            if not self.listening.is_set() and not self.manual.is_set():
                self.rest()
                while not (self.listening.is_set() or self.manual.is_set() or self.stopping):
                    self._wakeup.wait(1.0)
                    self._wakeup.clear()
                continue
            one_shot = not self.listening.is_set()      # "talk now" while off: one request, then close
            self._abort_rec.clear()
            src = (self.mic_factory or MicSource)(self.cfg["listen"]["device"])
            src.start()
            self.mic_open = True
            print("[mic] open" + (" for one request" if one_shot else ""))
            try:
                self._pump(src, one_shot)
            finally:
                src.close()
                self.mic_open = False
                self.wake.reset()
                print("[mic] closed")

    def _pump(self, src, one_shot: bool):
        """Wake -> record -> transcribe over one audio source. Returns when the
        source ends, listening is switched off, or (one_shot) after one request."""
        blocks = self._cleaned(iter(src))
        cooldown_until = 0.0
        first = True
        recent: collections.deque = collections.deque(maxlen=8)   # ~0.6 s of cleaned audio, for barge-in
        before: collections.deque = collections.deque(maxlen=32)  # ~2.6 s, saved by "that wasn't me"
        for block in blocks:
            if self.stopping:
                return
            recent.append(block)
            before.append(block)
            if self._sim_idle_end and getattr(src, "finished", False):
                # --simulate: the last clip ended without a request (an end phrase,
                # silence) before its trailing silence had been fed; exit now.
                self._sim_idle_end = False
                self._sim_done = True
                self._maybe_exit()
            barged = self._barge_check(block)
            if one_shot and first and not self.manual.is_set():
                return                          # the request was cancelled before the mic opened
            first = False
            if not (one_shot or self.simulate or self.listening.is_set() or self.manual.is_set()):
                return                          # listening switched off: release the mic
            # No wake word while the input field is open (you might be typing).
            hit = False if (one_shot or self.input_open) else self.wake.hit(block)
            by_wake = hit                       # the wake word fired (not a click, hotkey or follow-up)
            from_input = False
            if self.manual.is_set():
                self.manual.clear()
                hit = True
                by_wake = False
                from_input = self.input_open
            followup = False
            if barged and not hit:
                hit = followup = True           # talked over the answer: it stops, and we listen
            if not hit and not one_shot and not self.input_open and self.followup.poll():
                hit = followup = True           # answered and done speaking: listen without the wake word
            if not hit or (not followup and time.perf_counter() < cooldown_until):
                continue
            t_wake = time.perf_counter()
            if by_wake and not (followup or barged):
                import numpy as _np
                self.last_wake = (time.time(), _np.concatenate(list(before)))
                wake_sig = getattr(self.ui, "wake_hit", None)
                if wake_sig is not None:
                    wake_sig.emit(time.time())
            before.clear()
            if barged:
                print("[barge-in] you talked over it: stopped speaking, listening")
                self.followup.cancel()
                self.speaker.stop()
                if self.far is not None:
                    self.far.clear()
            elif not followup:
                self.followup.cancel()
                self.speaker.stop()             # barge-in: wake word cuts off speech
            self.gen += 1
            lcfg = self.cfg["listen"]
            if followup and not barged:
                print(f"[followup] listening {self.followup.remaining():.1f}s without the wake word")
                self.set_mode("followup", f"{self.followup.length:.0f}")
                lcfg = dict(lcfg, start_timeout=self.followup.remaining())
            else:
                self.set_mode("listening")
            hold = self.hold if (self.hold is not None and not self.simulate) else None
            if hold:
                hold.arm()
            try:
                stops = [] if self.simulate else ([] if one_shot else [self._abort_rec])
                self._reject_rec.clear()
                stops.append(self._reject_rec)
                if from_input:
                    stops.append(self._cancel_utt)
                stop = _AnyEvent(stops) if stops else None
                if followup and not barged:
                    # An announcement starting meanwhile closes the window (it re-opens after).
                    stop = _Either(stop.is_set if stop else None, self.wake_gate.busy)
                if self.vad is not None:
                    self.vad.reset()
                self.turn = TurnListener(self.stt.transcribe, self.cfg["listen"], on_partial=self._partial,
                                         partials=self._partials_on()) if self.stt is not None else None
                audio, t_end = record_utterance(blocks, lcfg, on_level=self.ui.level.emit,
                                                stop_event=stop, hold=hold.state if hold else None,
                                                vad=self.vad, listener=self.turn,
                                                preroll=list(recent)[-6:] if barged else None)
            finally:
                if hold:
                    hold.disarm()
                self.followup.close()
            self.wake.reset()
            if audio is None:
                if followup:
                    print("[followup] closed: nothing said")
                if from_input:
                    self._heard("")
                self.rest()
                if self.simulate:
                    self._sim_done = getattr(src, "finished", True)
                    self._maybe_exit()
                if one_shot or (self.simulate and not followup and getattr(src, "finished", True)):
                    return
                continue
            t_rec = time.perf_counter()
            self.set_mode("thinking", self.turn.partial_text if self.turn and self.turn.partial_text else "…")
            text = self.turn.final() if self.turn is not None else None
            reused = text is not None
            if text is None:
                text = self.stt.transcribe(audio)
            t_stt = time.perf_counter()
            print(f"[heard] {text!r}" + (" (follow-up)" if followup and not barged else "")
                  + (" (barge-in)" if barged else "") + (" [transcribed at the pause]" if reused else ""))
            if from_input:
                self._heard(text)
            cooldown_until = time.perf_counter() + float(self.cfg["wake"]["cooldown"])
            if followup:
                text = strip_wake(text)
            if self.simulate:
                self._sim_done = getattr(src, "finished", True)
            if not text.strip() or (followup and is_end_phrase(text)):
                if text.strip():
                    print(f"[followup] ended by {text!r}")
                self.rest()
                self._sim_idle_end = bool(self.simulate)
                self._maybe_exit()
                if one_shot:
                    return
                continue
            timing = dict(wake=t_wake, speech_end=t_end or t_rec, vad_done=t_rec, stt_done=t_stt,
                          audio_s=len(audio) / 16000, text=text, stt_reused=reused,
                          stt_device=getattr(self.stt, "label", ""))
            self.request(text, timing, source="barge-in" if barged else "followup" if followup else "voice")
            if one_shot:
                return

    def _cleaned(self, blocks):
        """Mic blocks with the assistant's own voice cancelled (when AEC is on)."""
        for b in blocks:
            if self.aec is not None:
                try:
                    b = self.aec.process(b)
                except Exception as e:
                    print(f"[aec] off after an error: {e}")
                    self.aec = None
            yield b

    def _barge_check(self, block) -> bool:
        """While it's talking (PCM voice, echo-cancelled mic): did the user talk over it?"""
        if self.barge is None or self.aec is None or self.barge_vad is None:
            return False
        speaking = self.wake_gate.speaking and getattr(self.speaker.voice.current, "pcm", False)
        if not speaking:
            if self.barge._since is not None:
                self.barge.speaking(False)
                self.barge_vad.reset()
            return False
        self.barge.speaking(True)
        from .aec import rms
        return self.barge.feed(self.barge_vad.frames(block), self.barge_vad.frame_ms, rms(block))

    def _text_loop(self):
        print("text mode: type a request, empty line to quit")
        while True:
            try:
                line = input("> ").strip()
            except EOFError:
                break
            if not line:
                break
            self.request(line, source="text", wait=True)
            self.speaker.wait()
            while self.pending_tasks and self.exit_when_done:
                time.sleep(0.5)
            self.speaker.wait()
        self.ui.quit.emit()

    def request(self, text: str, timing: dict | None = None, source: str = "voice", wait: bool = False):
        """The one entry point for a request, spoken or typed. It joins the
        shared conversation and is answered by whichever tier fits. Runs on a
        new thread (returned) unless `wait`. `source`: voice | followup | typed | text."""
        text = text.strip()
        if not text:
            return None
        self.gen += 1
        now = time.perf_counter()
        timing = timing or dict(wake=now, speech_end=now, vad_done=now, stt_done=now, audio_s=0, text=text)
        timing["source"] = source
        if wait:
            self.converse(text, self.gen, timing)
            return None
        th = threading.Thread(target=self.converse, args=(text, self.gen, timing), daemon=True)
        th.start()
        return th

    _converse_done = False

    def converse(self, text: str, gen: int, timing: dict):
        with self.conv_lock:
            self._converse_done = False
            try:
                self._converse(text, gen, timing)
            finally:
                self._converse_done = True
        sp = getattr(self, "speaker", None)
        if self.mode == "speaking" and sp is not None and not sp.busy and sp.q.empty() and gen == self.gen:
            self._tts_idle()                    # the voice finished before the answer did
        self._maybe_exit()

    def _converse(self, text, gen, timing):
        self._filler = ""
        self._speaking_text = ""
        self._ui_emit("turn", {"question": text})          # M5: the island keeps the question up all turn
        self.set_mode("thinking", text)
        self.speaker.begin()
        self.brain.timing = {}
        said = self._reply = []
        from .widgets import WidgetFilter
        wf = self._wf = WidgetFilter()          # M5: a `WIDGET {...}` line is shown, never spoken
        tier, task_id, answered = "", None, False
        t_llm0 = time.perf_counter()
        try:
            for ev in self.brain.handle(text):
                if gen != self.gen:
                    break
                kind = ev[0]
                if kind != "noop":
                    answered = True
                if kind == "say":
                    tier = tier or "fast"
                    ev = ("say", wf.feed(ev[1]))
                    if not ev[1]:
                        continue
                    said.append(ev[1])
                    self._speaking_text = "".join(said).strip()
                    if self.mode == "speaking":
                        self.ui.state.emit("speaking", self._speaking_text)
                    self.speaker.feed(ev[1])
                elif kind == "lookup":
                    filler = self.cfg["agent"].get("filler")
                    # Only longer requests get the filler: a short one is answered quickly anyway.
                    if len(text.split()) < int(self.cfg["agent"].get("filler_min_words", 8) or 0):
                        filler = ""
                    self.set_mode("thinking", "checking with your tools… " + text)
                    if filler:
                        self._filler = filler           # spoken, shown as the status line; not the answer
                        self.speaker.feed(filler + " ")
                    tier = "agent"
                    self._stream(self.brain.lookup(ev[1]), gen, timing)
                elif kind == "control":
                    # "volume 30": straight to the agent and its Windows tools, no filler.
                    tier = "agent"
                    self._stream(self.brain.lookup(ev[1]), gen, timing)
                elif kind == "undo":
                    tier = "local"
                    self._say(self.undo())
                elif kind == "task":
                    tier = "task"
                    task_id = self._start_task(ev[1], ev[2])
                elif kind == "status":
                    tier = "local"
                    self._say_status()
                elif kind == "fresh":
                    tier = "fresh"             # not recorded: the new conversation starts after it
                    self.brain.forget()
                    self._say("Okay, starting fresh.")
                elif kind == "task_ask":
                    tier = "task-ask"
                    task_id = self._ask_task(ev[1], ev[2], gen, timing)
                elif kind == "task_stop":
                    tier = "local"
                    task_id = self._stop_task(ev[1])
                elif kind == "task_resume":
                    tier = "local"
                    task_id = self._resume_task(ev[1])
                elif kind == "noop":
                    pass
        except Exception as e:
            traceback.print_exc()
            msg = str(e)
            if "authenticate" in msg.lower() or "login" in msg.lower():
                self._say("The Claude command line isn't signed in. Run claude auth login once in a terminal.")
            else:
                self._say("Sorry, that didn't work. " + msg[:80])
        rest = wf.finish()
        if rest.strip() and gen == self.gen:
            said.append(rest)
            self.speaker.feed(rest)
        if wf.spec is not None and gen == self.gen:
            print(f"[widget] {wf.spec.get('widget')}")
            self._ui_emit("widget", wf.spec)
        self._wf = None
        self.speaker.flush()
        if answered and gen == self.gen:
            if tier != "fresh" and self.brain.convo is not None:
                self.brain.convo.add(text, "".join(said).strip(), tier or "fast", task_id=task_id)
            if timing.get("source") != "typed":
                self._arm_followup()
        # wait for first audio to record latency
        t_wait = time.perf_counter()
        while self.speaker.first_audio_t is None and time.perf_counter() - t_wait < 20 and gen == self.gen:
            time.sleep(0.01)
        timing.update(llm_start=t_llm0, route=self.brain.timing.get("route"), voice=getattr(self.speaker, "label", ""),
                      first_token=self.brain.timing.get("first_token"),
                      first_audio=self.speaker.first_audio_t, reply="".join(said).strip())
        self._log_timing(timing)
        if not self.speaker.busy and self.mode == "thinking":
            self.rest()
        self._maybe_exit()

    def _say(self, text):
        self._speaking_text = text
        self._reply.append(text + " ")
        self.speaker.feed(text + " ")

    def _stream(self, chunks, gen, timing):
        """Speak a streamed answer (tool agent, task question)."""
        for chunk in chunks:
            if gen != self.gen:
                break
            timing.setdefault("answer", time.perf_counter())
            wf = getattr(self, "_wf", None)
            if wf is not None:
                chunk = wf.feed(chunk)
                if not chunk:
                    continue
            self._reply.append(chunk)
            self._speaking_text = "".join(self._reply).strip()
            self.speaker.feed(chunk)

    def _start_task(self, project, task):
        if not project:
            names = ", ".join(self.cfg["projects"]) or "none yet"
            self._say(f"Which project should that run in? I know {names}.")
            return None
        path = self.cfg["projects"][project]
        try:
            ctx = self.brain.convo.task_context() if self.brain.convo is not None else ""
            t = self.tasks.spawn(project, path, task, context=ctx)
        except Exception as e:
            self._say(f"I couldn't start it: {e}.")
            return None
        self.pending_tasks += 1
        print(f"[task {t.id}] started in {path}: {task}")
        self._say(f"Okay, starting that in {project}. I'll tell you when it's done.")
        self.push_tasks()
        return t.id

    # -- talking about tasks ----------------------------------------------------
    def _find_task(self, ref: str, running: bool | None = None):
        if ref and ref.startswith("#"):
            return self.tasks.tasks.get(ref[1:])
        return self.tasks.find(ref, self.brain.projects, running)

    def _ask_task(self, ref, question, gen, timing):
        """'What did the inha app task change?' -> resume its session headlessly."""
        t = (self._find_task(ref, running=False) or self._find_task(ref)) if self.tasks.tasks else None
        if t is None:
            self._say("I couldn't find that task." if self.tasks.tasks else "There are no tasks yet.")
            return None
        if t.status in ("starting", "running"):
            on = f", on {t.activity}" if t.activity else ""
            self._say(f"The {t.project} task is still running{on}. I'll tell you when it's done.")
            return t.id
        if not t.session_id:
            self._say(f"The {t.project} task never got started, so there's nothing to ask it.")
            return t.id
        print(f"[task {t.id}] asking its session {t.session_id}: {question}")
        self._say(self.cfg["agent"].get("filler") or "Let me check.")
        self.set_mode("thinking", f"asking the {t.project} task… {question}")
        self._stream(self.brain.ask_task(t, question), gen, timing)
        return t.id

    def _stop_task(self, ref):
        run = self.tasks.running()
        if not run:
            self._say("Nothing is running.")
            return None
        if ref == "all":
            for t in run:
                self._user_stopped.add(t.id)
                self.tasks.stop(t.id)
            self._say(f"Stopped {len(run)} task{'s' if len(run) != 1 else ''}.")
            return None
        if not ref and len(run) > 1:
            names = " or ".join(f"the {t.project} one" for t in run)
            self._say(f"{len(run)} are running. Which one, {names}?")
            return None
        t = self._find_task(ref, running=True) if ref else run[0]
        if t is None:
            self._say("I couldn't find a running task like that.")
            return None
        self._user_stopped.add(t.id)
        self.tasks.stop(t.id)
        print(f"[task {t.id}] stopped by voice")
        self._say(f"Stopped the {t.project} task.")
        self.push_tasks()
        return t.id

    def _resume_task(self, ref):
        """'Resume the last task in a terminal': a console running claude --resume in its folder."""
        t = self._find_task(ref) if self.tasks.tasks else None
        if t is None:
            self._say("There's no task like that to resume." if self.tasks.tasks else "There are no tasks yet.")
            return None
        if t.status in ("starting", "running"):
            self._say(f"The {t.project} task is still running. Stop it first, or wait until it's done.")
            return t.id
        try:
            self.tasks.open_terminal(t)
        except Exception as e:
            self._say(f"I couldn't open it: {e}.")
            return t.id
        print(f"[task {t.id}] resumed in a terminal: {t.resume_cmd}")
        self._say(f"Opening the {t.project} task in a terminal.")
        return t.id

    def _say_status(self):
        run = self.tasks.running()
        if not run:
            done = [t for t in self.tasks.tasks.values() if t.status != "running"][-1:]
            self._say("Nothing is running." + (f" The last one, in {done[0].project}, {done[0].status}." if done else ""))
            return
        parts = [f"{t.project} is {('on ' + t.activity) if t.activity else 'starting'}" for t in run]
        self._say(f"{len(run)} running. " + "; ".join(parts) + ".")

    def _log_timing(self, tm):
        def d(a, b):
            return round(tm[b] - tm[a], 3) if tm.get(a) and tm.get(b) else None

        row = dict(
            when=time.strftime("%Y-%m-%d %H:%M:%S"), route=tm.get("route"), audio_s=round(tm["audio_s"], 2),
            wake_to_first_audio=d("wake", "first_audio"),
            speech_end_to_first_audio=d("speech_end", "first_audio"),
            speech_end_to_lookup_answer=d("speech_end", "answer"),
            vad_tail=d("speech_end", "vad_done"), stt=d("vad_done", "stt_done"),
            first_token=round(tm["first_token"], 3) if tm.get("first_token") else None,
            tts_start=round(tm["first_audio"] - tm["llm_start"] - (tm.get("first_token") or 0), 3)
            if tm.get("first_audio") else None,
            stt_reused=tm.get("stt_reused"), stt_device=tm.get("stt_device"),
            voice=tm.get("voice"), source=tm.get("source"),
            text=tm["text"], reply=tm.get("reply", "")[:120],
        )
        self.timings.append(row)
        print("[latency] " + "  ".join(f"{k}={v}" for k, v in row.items() if k not in ("text", "reply", "when")))
        f = STATE_DIR / "latency.csv"
        if f.exists():                          # columns changed (M4 added some): start a new file
            with open(f, encoding="utf-8") as fh:
                head = fh.readline().strip()
            if head != ",".join(row):
                f.rename(f.with_name(f"latency-{time.strftime('%Y%m%d-%H%M%S')}.csv"))
        new = not f.exists()
        with open(f, "a", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(row))
            if new:
                w.writeheader()
            w.writerow(row)
