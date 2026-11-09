"""Glue: mic loop -> wake -> record -> STT -> brain -> TTS / tasks, plus UI updates."""
from __future__ import annotations

import csv
import threading
import time
import traceback

from . import config as config_mod
from .brain import Brain, Projects
from .config import STATE_DIR
from .listen import MicSource, SpeakingGate, WakeWord, WavSource, record_utterance
from .stt import SpeechToText, normalize_device
from .tasks import TaskManager
from .tts import Speaker


def _elapsed(t) -> str:
    s = int((t.ended or time.time()) - t.started)
    return f"{s // 60}:{s % 60:02d}"


class Assistant:
    def __init__(self, cfg: dict, bridge, stt_device: str | None = None, simulate: str | None = None,
                 text_mode=False, exit_when_done=False):
        self.cfg, self.ui = cfg, bridge
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
        self.mic_open = False
        self.stopping = False
        self.on_listening = None                # callback(on: bool, paused_until: float | None)
        self.stt: SpeechToText | None = None
        self.hold = None                        # holdkey.HoldKey, set by __main__ (mic runs only)
        self.wake_gate = SpeakingGate(cfg["wake"])   # no wake triggers while it talks (+ a short tail)

    # -- UI helpers -----------------------------------------------------------
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
        if self.mode in ("listening", "holding"):
            self.set_mode("holding" if held else "listening")

    def talk_now(self):
        """Same as clicking the island: listen for one request without the wake
        word (opens the mic just for that if listening is off)."""
        self.manual.set()
        self._wakeup.set()

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
        if "wake.threshold" in changed and wake is not None:
            wake.threshold = float(self.cfg["wake"]["threshold"])
        return changed

    # -- speech-to-text ---------------------------------------------------------
    def stt_device(self) -> str:
        """gpu | cpu: --gpu/--cpu flags win over stt.device."""
        return self.stt_override or normalize_device(self.cfg["stt"].get("device", "gpu"))

    def _stt_changed(self, label: str):
        sig = getattr(self.ui, "stt", None)
        if sig is not None:
            sig.emit(label)

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
        self.set_mode("loading", "loading voice…")
        self.speaker = Speaker(c["tts"], on_start=self._tts_started, on_idle=self._tts_idle)
        self.tasks = TaskManager(c, on_change=self.push_tasks, on_finish=self._task_finished)
        self.push_tasks()
        self.set_mode("loading", "loading brain…")
        self.brain = Brain(c)
        if not self.text_mode:
            self.set_mode("loading", "loading wake word…")
            self.wake = WakeWord(c["wake"]["model"], float(c["wake"]["threshold"]), gate=self.wake_gate)
            self.set_mode("loading", f"loading whisper ({self.stt_device()})…")
            self.stt = SpeechToText(c["stt"], device=self.stt_device(), hint_words=list(c["projects"]),
                                    on_change=self._stt_changed)
            self._stt_changed(self.stt.label)
            if self.stt.want == "gpu" and not self.stt.gpu_failed:
                threading.Thread(target=self._stt_poll_loop, daemon=True, name="stt-load").start()
        self.ready.set()
        self.rest()
        load = f" [{self.stt.readings()}]" if self.stt and self.stt.readings() else ""
        print(f"[ready] fast path: {self.brain.backend}; stt: {self.stt.label if self.stt else '-'}{load}")

    # -- TTS callbacks --------------------------------------------------------
    def _tts_started(self):
        self.wake_gate.set_speaking(True)
        if self.mode not in ("listening",):
            self.set_mode("speaking", self._speaking_text)

    def _tts_idle(self):
        self.wake_gate.set_speaking(False)
        if self.mode in ("speaking",):
            self.rest()
        self._maybe_exit()

    _speaking_text = ""

    def _maybe_exit(self):
        if (self.exit_when_done and self._sim_done and self.pending_tasks == 0
                and not self.speaker.busy and not self.conv_lock.locked()):
            threading.Timer(0.5, self.ui.quit.emit).start()

    _sim_done = False

    # -- task callbacks -------------------------------------------------------
    def _task_finished(self, t):
        self.pending_tasks = max(0, self.pending_tasks - 1)
        verb = {"done": "is done", "failed": "failed", "stopped": "was stopped"}.get(t.status, "ended")
        msg = f"Your {t.project} task {verb}. {t.summary}"
        print(f"[task {t.id}] {t.status}: {t.summary}  (resume: {t.resume_cmd})")
        self._speaking_text = msg
        self.speaker.announce(msg)
        self.push_tasks()

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
            src = WavSource(self.simulate)
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
            src = MicSource(self.cfg["listen"]["device"])
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
        blocks = iter(src)
        cooldown_until = 0.0
        for block in blocks:
            if self.stopping:
                return
            if not (one_shot or self.simulate or self.listening.is_set() or self.manual.is_set()):
                return                          # listening switched off: release the mic
            hit = False if one_shot else self.wake.hit(block)
            if self.manual.is_set():
                self.manual.clear()
                hit = True
            if not hit or time.perf_counter() < cooldown_until:
                continue
            t_wake = time.perf_counter()
            self.speaker.stop()                 # barge-in: wake word cuts off speech
            self.gen += 1
            self.set_mode("listening")
            hold = self.hold if (self.hold is not None and not self.simulate) else None
            if hold:
                hold.arm()
            try:
                audio, t_end = record_utterance(blocks, self.cfg["listen"], on_level=self.ui.level.emit,
                                                stop_event=None if (self.simulate or one_shot) else self._abort_rec,
                                                hold=hold.state if hold else None)
            finally:
                if hold:
                    hold.disarm()
            self.wake.reset()
            if audio is None:
                self.rest()
                if self.simulate or one_shot:
                    return
                continue
            t_rec = time.perf_counter()
            self.set_mode("thinking", "…")
            text = self.stt.transcribe(audio)
            t_stt = time.perf_counter()
            print(f"[heard] {text!r}")
            cooldown_until = time.perf_counter() + float(self.cfg["wake"]["cooldown"])
            if not text.strip():
                self.rest()
                if one_shot:
                    return
                continue
            timing = dict(wake=t_wake, speech_end=t_end or t_rec, vad_done=t_rec, stt_done=t_stt,
                          audio_s=len(audio) / 16000, text=text)
            threading.Thread(target=self.converse, args=(text, self.gen, timing), daemon=True).start()
            if self.simulate:
                # one utterance per simulated file; keep pumping silence until done
                self._sim_done = True
            if one_shot:
                return

    def _text_loop(self):
        print("text mode: type a request, empty line to quit")
        while True:
            try:
                line = input("> ").strip()
            except EOFError:
                break
            if not line:
                break
            self.gen += 1
            self.converse(line, self.gen, dict(wake=time.perf_counter(), speech_end=time.perf_counter(),
                                               vad_done=time.perf_counter(), stt_done=time.perf_counter(),
                                               audio_s=0, text=line))
            self.speaker.wait()
            while self.pending_tasks and self.exit_when_done:
                time.sleep(0.5)
            self.speaker.wait()
        self.ui.quit.emit()

    def converse(self, text: str, gen: int, timing: dict):
        with self.conv_lock:
            self._converse(text, gen, timing)
        self._maybe_exit()

    def _converse(self, text, gen, timing):
        self.set_mode("thinking", text)
        self.speaker.begin()
        self.brain.timing = {}
        said = []
        t_llm0 = time.perf_counter()
        try:
            for ev in self.brain.handle(text):
                if gen != self.gen:
                    break
                kind = ev[0]
                if kind == "say":
                    said.append(ev[1])
                    self._speaking_text = "".join(said).strip()
                    if self.mode == "speaking":
                        self.ui.state.emit("speaking", self._speaking_text)
                    self.speaker.feed(ev[1])
                elif kind == "lookup":
                    filler = self.cfg["agent"].get("filler")
                    if filler:
                        self._say(filler)
                    self.set_mode("thinking", "checking with your tools… " + text)
                    for chunk in self.brain.agent.ask(ev[1]):
                        if gen != self.gen:
                            break
                        timing.setdefault("answer", time.perf_counter())
                        said.append(chunk)
                        self._speaking_text = "".join(said).strip()
                        self.speaker.feed(chunk)
                elif kind == "task":
                    self._start_task(ev[1], ev[2])
                elif kind == "status":
                    self._say_status()
                elif kind == "noop":
                    pass
        except Exception as e:
            traceback.print_exc()
            msg = str(e)
            if "authenticate" in msg.lower() or "login" in msg.lower():
                self._say("The Claude command line isn't signed in. Run claude auth login once in a terminal.")
            else:
                self._say("Sorry, that didn't work. " + msg[:80])
        self.speaker.flush()
        # wait for first audio to record latency
        t_wait = time.perf_counter()
        while self.speaker.first_audio_t is None and time.perf_counter() - t_wait < 20 and gen == self.gen:
            time.sleep(0.01)
        timing.update(llm_start=t_llm0, route=self.brain.timing.get("route"),
                      first_token=self.brain.timing.get("first_token"),
                      first_audio=self.speaker.first_audio_t, reply="".join(said).strip())
        self._log_timing(timing)
        if not self.speaker.busy and self.mode == "thinking":
            self.rest()
        self._maybe_exit()

    def _say(self, text):
        self._speaking_text = text
        self.speaker.feed(text + " ")

    def _start_task(self, project, task):
        if not project:
            names = ", ".join(self.cfg["projects"]) or "none yet"
            self._say(f"Which project should that run in? I know {names}.")
            return
        path = self.cfg["projects"][project]
        try:
            t = self.tasks.spawn(project, path, task)
        except Exception as e:
            self._say(f"I couldn't start it: {e}.")
            return
        self.pending_tasks += 1
        print(f"[task {t.id}] started in {path}: {task}")
        self._say(f"Okay, starting that in {project}. I'll tell you when it's done.")
        self.push_tasks()

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
            text=tm["text"], reply=tm.get("reply", "")[:120],
        )
        self.timings.append(row)
        print("[latency] " + "  ".join(f"{k}={v}" for k, v in row.items() if k not in ("text", "reply", "when")))
        f = STATE_DIR / "latency.csv"
        new = not f.exists()
        with open(f, "a", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(row))
            if new:
                w.writeheader()
            w.writerow(row)
