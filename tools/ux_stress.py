"""UX stress test: drive the real hey-claude through scripted conversations and find the jank.

    .venv\\Scripts\\python tools\\ux_stress.py run                      # every scenario, the real app
    .venv\\Scripts\\python tools\\ux_stress.py run weather quick -n 3   # some scenarios, 3 times each
    .venv\\Scripts\\python tools\\ux_stress.py run --offline            # no GPU/models/login (CI)
    .venv\\Scripts\\python tools\\ux_stress.py wakecheck                # does the wake model fire on the clips?
    .venv\\Scripts\\python tools\\ux_stress.py list

The app runs in this process (heyclaude.__main__.main with a harness): the
real Assistant, claude CLI tiers, MCP tools, Kokoro, whisper, the QML island.
Only the microphone is simulated: a SimRoom (heyclaude/listen.py) that "hears"
the wake word (your clips in state/wake_samples, or Kokoro saying it), the
questions (Kokoro voices), pauses, background noise and the echo of the
assistant's own voice, all in real time. The hotkey, the hold key (Ctrl),
Ctrl+Shift mute, Esc and the island's buttons are driven where their OS hooks
would call into the app (no real key presses reach other windows).

Everything is recorded (heyclaude/ux_probe.py): Bridge signals, engine
commands, the island sampled ~30x a second, audio as it plays, frame pacing,
focus, and window grabs -> state/ux/runs/<stamp>/<scenario>-<n>/ with a
timeline, findings, a contact sheet and a GIF. A summary.md ranks the jank.

Safety with real connections: the agent's tools are cut down to read-only ones
plus the volume (state/ux/config.harness.yaml), every tool call is audited (the
run stops on anything else), the volume is put back after each scenario, tasks
only run in state/ux/scratch, and no message, mail or event is ever sent or
changed. The user's own hey-claude must not be running (one GPU).
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import wave
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

from heyclaude import ux_probe as UX  # noqa: E402
from heyclaude.listen import BLOCK, RATE, SimRoom, WavSource  # noqa: E402

STATE = ROOT / "state"
UXDIR = STATE / "ux"
SCEN_DIR = HERE / "ux_scenarios"
VOICES = ["am_michael", "bf_emma", "am_adam", "af_sarah", "bm_george"]
GPU_MAX = 70


def main_checkout() -> Path:
    """The checkout with the user's config.yaml and models (this one, or the main one of a worktree)."""
    if (ROOT / "config.yaml").exists():
        return ROOT
    p = ROOT
    while p.parent != p:
        if p.name == "worktrees" and p.parent.name == ".claude":
            return p.parent.parent
        p = p.parent
    return ROOT


# =============================================================================== scenarios
def load_scenarios(names: list[str] | None = None) -> list[dict]:
    import yaml
    out = []
    for f in sorted(SCEN_DIR.glob("*.yaml")):
        s = yaml.safe_load(f.read_text(encoding="utf-8"))
        s.setdefault("name", f.stem)
        s["file"] = f.name
        out.append(s)
    if names:
        want = set(names)
        out = [s for s in out if s["name"] in want or s["file"].split(".")[0] in want]
        missing = want - {s["name"] for s in out} - {s["file"].split(".")[0] for s in out}
        if missing:
            raise SystemExit(f"unknown scenario(s): {', '.join(sorted(missing))}")
    return out


def texts_of(steps) -> list[tuple[str, str]]:
    """(text, voice) of everything the scenarios say."""
    out = []
    for st in steps:
        for k in ("say", "ask", "barge"):
            if k in st:
                v = st[k]
                if isinstance(v, dict):
                    parts = v.get("parts") or [v.get("text", "")]
                    voice = v.get("voice")
                else:
                    parts, voice = [v], None
                for p in parts:
                    if isinstance(p, str) and p:
                        out.append((p, voice))
    return out


# =============================================================================== voices
class Voices:
    """Kokoro (on the CPU, so it never competes with the app's GPU) for the user's
    lines, cached as 16 kHz wavs; the user's own wake clips for "hey claude"."""

    def __init__(self, main: Path, offline: bool, wake: str = "mine", seed: int = 7):
        self.main, self.offline, self.wake_mode = main, offline, wake
        self.cache = UXDIR / "voices"
        self.rng = random.Random(seed)
        self._k = None
        self.wake_clips = []
        if not offline and wake == "mine":
            d = main / "state" / "wake_samples"
            self.wake_clips = [read16(p) for p in sorted(d.glob("*.wav"))] if d.exists() else []
            self.wake_clips = self._firing(self.wake_clips)
            if not self.wake_clips:
                print("[ux] no clips in state/wake_samples: using Kokoro's \"hey claude\"")
                self.wake_mode = "synth"

    def _firing(self, clips):
        """Only the clips the configured wake model fires on (a miss isn't jank to measure)."""
        try:
            import yaml
            from heyclaude.listen import WakeWord, resolve_wake_model
            cfg = yaml.safe_load((self.main / "config.yaml").read_text(encoding="utf-8")) or {}
            w = cfg.get("wake") or {}
            path, why = resolve_wake_model(w.get("model", ""), root=self.main)
            if why:
                return clips
            ww = WakeWord(path, float(w.get("threshold", 0.5)))
        except Exception as e:
            print(f"[ux] wake clips not checked: {e}")
            return clips
        keep = []
        for c in clips:
            ww.reset()
            x = np.concatenate([np.zeros(RATE, np.int16), trim(c), np.zeros(RATE // 2, np.int16)])
            if max(float(ww.feed(x[i:i + BLOCK])) for i in range(0, len(x) - BLOCK, BLOCK)) >= ww.threshold:
                keep.append(c)
        print(f"[ux] wake: {len(keep)}/{len(clips)} of your clips fire; using those")
        return keep

    def _kokoro(self):
        if self._k is None:
            from heyclaude.tts import KokoroVoice, kokoro_paths
            m, v = kokoro_paths({})
            if not os.path.exists(m):
                m = str(self.main / "state" / "models" / "kokoro" / "kokoro-v1.0.onnx")
                v = str(self.main / "state" / "models" / "kokoro" / "voices-v1.0.bin")
            self._k = KokoroVoice(m, v, "cpu", threads=4)
        return self._k

    def line(self, text: str, voice: str | None = None) -> np.ndarray:
        if self.offline:
            from ux_offline import speech_like
            return speech_like(text, seed=len(text))
        voice = voice or VOICES[0]
        key = hashlib.sha1(f"{voice}|{text}".encode()).hexdigest()[:16]
        f = self.cache / f"{voice}-{key}.wav"
        if f.exists():
            return read16(f)
        k = self._kokoro()
        k.voice = voice
        x = k.synth(text)
        from scipy.signal import resample_poly
        y = resample_poly(x, 2, 3)                           # 24 kHz -> 16 kHz
        pcm = np.clip(y / max(1e-6, float(np.max(np.abs(y)))) * 0.5 * 32767, -32767, 32767).astype(np.int16)
        write16(f, pcm)
        return pcm

    def wake(self) -> np.ndarray:
        if self.offline:
            from ux_offline import speech_like
            return speech_like("hey claude", seed=1)
        if self.wake_mode == "mine" and self.wake_clips:
            return self.rng.choice(self.wake_clips)
        return self.line("Hey Claude", self.rng.choice(VOICES[:3]))


def read16(p) -> np.ndarray:
    with wave.open(str(p), "rb") as w:
        assert w.getframerate() == RATE and w.getnchannels() == 1 and w.getsampwidth() == 2, p
        return np.frombuffer(w.readframes(w.getnframes()), np.int16).copy()


def write16(p, pcm):
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(np.asarray(pcm, np.int16).tobytes())


def trim(pcm: np.ndarray, thr: int = 400) -> np.ndarray:
    """Cut leading/trailing silence (the wake clips have some)."""
    a = np.abs(pcm.astype(np.int32))
    idx = np.where(a > thr)[0]
    if not len(idx):
        return pcm
    lo, hi = max(0, idx[0] - 800), min(len(pcm), idx[-1] + 1600)
    return pcm[lo:hi]


# =============================================================================== safety
SAFE_TOOL = re.compile(
    r"^(Read|Glob|Grep|WebSearch|WebFetch|Edit|Write|TodoWrite|Task|ToolSearch|"
    r"mcp__claude_ai_Google_Calendar__(list_calendars|list_events|search_events|get_event)|"
    r"mcp__claude_ai_Gmail__(search_threads|get_thread|get_message|list_labels)|"
    r"mcp__claude_ai_Google_Drive__(search_files|list_recent_files|get_file_metadata)|"
    r"mcp__telegram__telegram_(get_me|list_dialogs|get_messages|get_context|get_chat|search_messages|search_contacts)|"
    r"mcp__weather__get_weather|"
    r"mcp__windows__(get_volume|set_volume|list_audio_devices|get_brightness|get_theme|battery_status|get_power_mode|"
    r"now_playing|list_apps|list_windows|get_radios|get_night_light)|"
    r"mcp__windows-mcp__Snapshot)$")


def harness_config(user_cfg: dict, scratch: Path) -> dict:
    """The user's settings with the agent cut down to safe tools and tasks in a scratch folder."""
    c = copy.deepcopy(user_cfg)
    c.setdefault("agent", {})
    c["agent"]["allowed_tools"] = [t for t in c["agent"].get("allowed_tools", []) if SAFE_TOOL.match(t)]
    c["agent"]["windows_mcp"] = dict(c["agent"].get("windows_mcp") or {}, exclude_tools=[
        "PowerShell", "Registry", "Click", "Type", "Shortcut", "Drag", "App", "Move", "Scroll", "Wait", "Launch",
        "Switch", "Resize", "Clipboard", "Key", "Notification", "Process"])
    # tools the agent must not even see in a test run (it then never tries them; PowerShell always)
    c["agent"]["disallowed_tools"] = ["Bash", "KillShell", "NotebookEdit"]
    c["projects"] = {"scratch": str(scratch)}
    c.setdefault("tasks", {})
    c["tasks"]["permission_mode"] = "acceptEdits"
    c["tasks"]["allowed_tools"] = ["Read", "Glob", "Grep", "Edit", "Write"]
    c.setdefault("conversation", {})["save"] = False
    c.setdefault("app", {})["start_with_windows"] = user_cfg.get("app", {}).get("start_with_windows", False)
    return c


class Audit:
    """Every tool the agent calls is checked against SAFE_TOOL. An attempt at anything
    else is logged; if it then ran (its result isn't a refusal) the run stops. Claude Code
    refuses tools outside --allowedTools (--permission-prompts none), so an attempt alone
    is the model asking, not something happening."""

    def __init__(self, on_violation):
        self.calls: list[str] = []
        self.attempts: list[str] = []
        self.violations: list[str] = []
        self.on_violation = on_violation

    def tool(self, ev: dict):
        name = str(ev.get("name") or "")
        if ev.get("phase") != "start" or not name:
            return
        self.calls.append(name)
        if not SAFE_TOOL.match(name):
            self.attempts.append(name)
            print(f"[ux] SAFETY: the agent asked for {name}; watching its result")

    def result(self, name: str, is_error: bool, content) -> None:
        if SAFE_TOOL.match(name or ""):
            return
        text = str(content)[:200]
        if is_error:
            print(f"[ux] SAFETY: {name} was refused ({text[:100]!r})")
            return
        self.violations.append(name)
        print(f"[ux] SAFETY: {name} RAN ({text[:100]!r}); stopping the run")
        self.on_violation(name)


def volume_now():
    try:
        from heyclaude.mcp_windows import audio
        from heyclaude.mcp_windows._util import com_init
        com_init()
        return audio.get_volume()
    except Exception as e:
        print(f"[ux] can't read the volume: {e}")
        return None


def volume_restore(v):
    if not v:
        return None
    try:
        from heyclaude.mcp_windows import audio
        from heyclaude.mcp_windows._util import com_init
        com_init()
        now = audio.get_volume()
        if now != v:
            audio.set_volume(level=int(v["level"]))
            audio.set_mute(bool(v["muted"]))
            print(f"[ux] volume restored: {now} -> {audio.get_volume()}")
            return now
    except Exception as e:
        print(f"[ux] couldn't restore the volume: {e}")
    return None


def gpu_util() -> int | None:
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=5).stdout
        return int(out.strip().splitlines()[0])
    except Exception:
        return None


def wait_gpu(limit=GPU_MAX, patience_s=600):
    """GPU work only under ~70% measured load: wait (sampling for 3 s) until it's there."""
    end = time.time() + patience_s
    while True:
        xs = [gpu_util() for _ in range(3) if not time.sleep(1)]
        xs = [x for x in xs if x is not None]
        if not xs or max(xs) < limit:
            return max(xs) if xs else None
        if time.time() > end:
            raise RuntimeError(f"GPU stayed above {limit}% ({xs}) for {patience_s}s")
        print(f"[ux] GPU at {max(xs)}%: waiting")
        time.sleep(10)


# =============================================================================== state dir
def link_state(main: Path):
    """In a worktree: state/models and state/wake_models point at the main checkout's (read only)."""
    STATE.mkdir(exist_ok=True)
    if main == ROOT:
        return
    for sub in ("models", "wake_models"):
        dst, src = STATE / sub, main / "state" / sub
        if dst.exists() or not src.exists():
            continue
        subprocess.run(["cmd", "/c", "mklink", "/J", str(dst), str(src)], capture_output=True)


# =============================================================================== the driver
class Gui:
    """Run a callable on the Qt GUI thread and wait for it."""

    def __init__(self):
        from PySide6.QtCore import QObject, Signal, Slot

        class _Invoker(QObject):
            sig = Signal(object)

            @Slot(object)
            def run(self, job):
                fn, box, ev = job
                try:
                    box.append(fn())
                except Exception as e:
                    box.append(e)
                ev.set()
        self.inv = _Invoker()
        self.inv.sig.connect(self.inv.run)

    def __call__(self, fn, timeout=10.0):
        box, ev = [], threading.Event()
        self.inv.sig.emit((fn, box, ev))
        if not ev.wait(timeout):
            raise TimeoutError("GUI call timed out")
        if box and isinstance(box[0], Exception):
            raise box[0]
        return box[0] if box else None


class FakeHold:
    """holdkey.HoldKey for the harness: the same HoldState, fed by the scenario
    instead of a keyboard hook (so no real key presses are needed)."""

    def __init__(self, on_change, spec="ctrl", mute="shift"):
        from heyclaude.holdkey import HoldKey, HoldState, parse
        self.state = HoldState(parse(spec), mute_vks=HoldKey._mute_vks(spec, mute))
        self.on_change = on_change
        self.vk = sorted(parse(spec))[0]
        self.mute_vk = sorted(parse(mute))[0] if mute else None
        self.armed = False

    held = property(lambda self: self.state.held)
    muted = property(lambda self: self.state.muted)

    def arm(self):
        self.state.reset(key_down_now=self.state.down, mute_down_now=self.state.mute_down)
        self.armed = True

    def disarm(self):
        self.armed = False
        if self.state.held:
            self.state.held = False
            self.on_change(False)

    def key(self, vk, down):
        if self.state.key(vk, down) and self.armed:
            self.on_change(self.state.held)

    def set_key(self, *a):
        pass

    def close(self):
        pass


class Harness:
    def __init__(self, args, scenarios: list[dict], out: Path):
        self.args, self.scenarios, self.out = args, scenarios, out
        self.offline = args.offline
        self.main = main_checkout()
        self.room = SimRoom(noise_dbfs=args.noise, echo_gain=0.0 if self.offline else args.echo, seed=11)
        self.voices = Voices(self.main, self.offline, wake=args.wake)
        self.results: list[dict] = []
        self.stop_all = threading.Event()
        self.audit = Audit(self._violation)
        self.volume0 = None if self.offline else volume_now()
        self.ctx = None

    # -- main() hooks ------------------------------------------------------------------
    def configure(self, cfg):
        if self.offline:
            cfg["agent"]["enabled"] = False
            cfg["island"]["motion"] = "full"
            cfg["wake"]["model"] = "hey_jarvis"
            cfg["island"]["renderer"] = "software"
        cfg["island"]["hide_when_idle"] = False
        cfg.setdefault("listen", {})["hold_key"] = "ctrl"
        cfg["listen"]["hold_mute_key"] = "shift"

    def mic_factory(self, device=None):
        return WavSource.live(self.room)

    def started(self, ctx):
        self.ctx = ctx
        asst, bridge, island = ctx["asst"], ctx["bridge"], ctx["island"]
        if self.offline:
            import ux_offline
            ux_offline.install(asst, self.room, ctx["cfg"])
        self.gui = Gui()
        self.rec = UX.Recorder(bridge, island, sample_hz=30, grab_fps=self.args.grab_fps,
                               grab_dir=self.out / "_frames")
        bridge.tool.connect(self.audit.tool)
        asst.hold = FakeHold(asst.on_hold)
        asst.false_wake_dir = UXDIR / "wake_negatives"     # never the user's training negatives
        threading.Thread(target=self._driver, daemon=True, name="ux-driver").start()

    def _violation(self, name):
        self.stop_all.set()
        try:
            self.ctx["asst"].hush()
            self.ctx["asst"].set_listening(False)
        except Exception:
            pass

    # -- the run -----------------------------------------------------------------------
    def _driver(self):
        asst = self.ctx["asst"]
        try:
            asst.ready.wait(600)
            if not self.offline:
                print("[ux] waiting for the brain to warm up")
                asst.brain.warm.wait(180)
                # the far-end echo of the assistant's voice goes back into the room's "air"
                if asst.far is not None:
                    orig = asst.far.push

                    def push(s, orig=orig):
                        self.room.echo_push(s)
                        orig(s)
                    asst.far.push = push
            self.rec.hook_assistant(asst)
            ag = getattr(getattr(asst, "brain", None), "agent", None)
            if ag is not None and ag.on_tool_result is not None:
                orig_res = ag.on_tool_result

                def on_res(name, args, result, is_error, orig_res=orig_res):
                    self.audit.result(name, is_error, result)
                    return orig_res(name, args, result, is_error)
                ag.on_tool_result = on_res
            time.sleep(1.5)
            for rep in range(self.args.repeat):
                order = list(self.scenarios)
                if rep and self.args.shuffle:
                    random.Random(rep).shuffle(order)
                for sc in order:
                    if self.stop_all.is_set():
                        break
                    for k in range(int(sc.get("repeat", 1)) if self.args.repeat == 1 else 1):
                        self.run_one(sc, rep * 10 + k)
        except Exception:
            traceback.print_exc()
        finally:
            self.finish()

    def run_one(self, sc: dict, n: int):
        asst = self.ctx["asst"]
        name = f"{sc['name']}-{n + 1}"
        d = self.out / name
        print(f"\n[ux] === {name}: {sc.get('desc', '')}")
        if not self.offline:
            g = wait_gpu()
            print(f"[ux] GPU {g}%")
        self._settle(25)
        vol0 = None if self.offline else volume_now()
        self.rec.grab_dir = d / "frames"
        self.rec.grabbing = True
        t0 = time.perf_counter()
        self.rec.mark("scenario_start", name=name)
        err = None
        try:
            for st in sc["steps"]:
                if self.stop_all.is_set():
                    break
                self.step(st, sc)
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            traceback.print_exc()
        # a field the scenario left open would keep the wake word off for the next one
        isl = self.ctx["island"]
        if isl is not None and getattr(isl, "session", None) is not None and isl.session.is_open:
            self.gui(isl.session.cancel)
            self.rec.mark("closed_field")
        # let the turn finish: the answer, the follow-up window, the panel's linger
        self._settle(float(sc.get("settle_s", 40)), quiet_s=float(sc.get("quiet_s", 6)))
        self.rec.mark("scenario_end", name=name)
        t1 = time.perf_counter()
        self.rec.grabbing = False
        self.rec.flush_grabs()
        ev = self.rec.since(t0, t1)
        # put the volume back only if the agent changed it in this scenario (a volume effect):
        # a change by hand (you turning it down while it talks) is yours and stays
        touched = any(e["k"] == "effect" and e.get("effect") == "volume" for e in ev) or             any(t.endswith(("set_volume", "set_mute")) for t in self.audit.calls)
        restored = volume_restore(vol0) if (touched and not self.offline) else None
        ev += [{"t": c["t0"], "k": "user", "label": c["label"], "t0": c["t0"], "t1": c["t1"]}
               for c in self.room.log if t0 <= c["t0"] <= t1]
        ev.sort(key=lambda e: e["t"])
        UX.save_timeline(ev, d / "timeline.jsonl")
        res = UX.detect(ev, scenario=sc["name"])
        res.update(name=name, scenario=sc["name"], t0=t0, error=err, volume_restored=restored,
                   tools=sorted(set(self.audit.calls)), expect=self._expect(sc, ev))
        grabs = [e for e in ev if e["k"] == "grab"]
        try:
            res["contact"] = self.gui(lambda: UX.contact_sheet(d / "frames", grabs, d / "contact.png", t0), timeout=60)
            if self.args.gif:
                res["gif"] = UX.gif(d / "frames", grabs, d / "anim.gif", t0)
        except Exception as e:
            print(f"[ux] frames: {e}")
        (d / "findings.json").write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
        self.results.append(res)
        kinds = sorted({f["kind"] for f in res["findings"]})
        print(f"[ux] {name}: {len(res['findings'])} finding(s) {kinds}  latency {res['latency']}")
        self.audit.calls.clear()

    def _expect(self, sc, ev) -> dict:
        exp = sc.get("expect") or {}
        got = {}
        if "widget" in exp:
            got["widget"] = any(e["k"] == "w_shown" and e.get("widget") == exp["widget"] for e in ev)
        if "effect" in exp:
            got["effect"] = any(e["k"] == "effect" and e.get("effect") == exp["effect"] for e in ev)
        if "turns" in exp:
            got["turns"] = sum(1 for e in ev if e["k"] == "turn") >= int(exp["turns"])
        if "answer" in exp:
            got["answer"] = any(e["k"] == "audio" for e in ev)
        return got

    def _settle(self, timeout: float, quiet_s: float = 3.0):
        """Wait until the assistant is idle, its voice quiet, the follow-up closed and
        the island's panel collapsed, for `quiet_s` in a row."""
        asst = self.ctx["asst"]
        end = time.time() + timeout
        quiet_since = None
        while time.time() < end and not self.stop_all.is_set():
            p = self.rec.last_probe or {}
            sp = getattr(asst, "speaker", None)
            busy = (asst.mode not in ("idle", "off") or (sp is not None and (sp.busy or not sp.q.empty()))
                    or asst.conv_lock.locked() or asst.followup.state != "idle"
                    or (p.get("extrasH") or 0) > 0 or p.get("state") not in UX.REST)
            if busy:
                quiet_since = None
            elif quiet_since is None:
                quiet_since = time.time()
            elif time.time() - quiet_since >= quiet_s:
                return True
            time.sleep(0.1)
        return False

    # -- steps -------------------------------------------------------------------------
    def step(self, st: dict, sc: dict):
        (k, v), = [(k, v) for k, v in st.items() if k != "note"][:1] or [("noop", None)]
        asst, bridge, isl = self.ctx["asst"], self.ctx["bridge"], self.ctx["island"]
        if k == "wake":
            self._say(trim(self.voices.wake()), "wake")
            self._wait_said()
            time.sleep(float((v or {}).get("gap", 0.25)) if isinstance(v, dict) else 0.25)
        elif k in ("say", "barge"):
            self._utter(v)
            if k == "barge":
                self.rec.mark("barge_in")
        elif k == "ask":                                  # wake word, a natural gap, the question
            self._say(trim(self.voices.wake()), "wake")
            gap = float(v.get("gap", 0.35)) if isinstance(v, dict) else 0.35
            self._wait_said(extra=gap)
            self._utter(v)
        elif k == "pause" or k == "wait":
            time.sleep(float(v))
        elif k == "until":
            self._until(v)
        elif k == "noise":
            dbfs, secs = float(v.get("dbfs", -40)), float(v.get("secs", 3))
            now = self.room.clock()
            self.room.extra_noise = (dbfs, now, now + secs)
            self.rec.mark("noise", dbfs=dbfs, secs=secs)
        elif k == "hotkey":
            self.rec.mark("hotkey")
            bridge.talk.emit()
        elif k == "type":
            self._type(str(v))
        elif k == "esc":
            self.rec.mark("esc")
            bridge.hush.emit()                            # what the Esc stop key's hotkey callback does
        elif k == "hold":
            self.rec.mark(f"hold_{v}")
            asst.hold.key(asst.hold.vk, v == "down")
        elif k == "mute":
            self.rec.mark(f"mute_{v}")
            asst.hold.key(asst.hold.mute_vk, v == "down")
        elif k == "click":
            self._click(str(v))
        elif k == "check_volume":
            self.rec.mark("volume", **(volume_now() or {}))
        elif k == "noop":
            pass
        else:
            raise ValueError(f"unknown step {k!r}")

    def _say(self, pcm, label):
        t0, t1 = self.room.say(pcm, label=label)
        self.rec.mark("say", text=label[:80], secs=round(t1 - t0, 2))
        return t0, t1

    def _wait_said(self, extra: float = 0.0):
        d = self.room.busy_until() - self.room.clock() + extra
        if d > 0:
            time.sleep(d)

    def _utter(self, v):
        """say: "text" | {text|parts: [...], voice, pause_s, lead_s, wait: false}"""
        if isinstance(v, str):
            v = {"text": v}
        voice = v.get("voice") or self.args.voice
        parts = v.get("parts") or [v.get("text", "")]
        lead = float(v.get("lead_s", 0.0))
        if lead:
            time.sleep(lead)
        for p in parts:
            if isinstance(p, (int, float)):
                time.sleep(float(p))                     # a pause inside the utterance
                continue
            if isinstance(p, dict) and "pause" in p:
                time.sleep(float(p["pause"]))
                continue
            self._say(self.voices.line(p, voice), p)
            if v.get("wait", True):
                self._wait_said()

    def _until(self, v):
        """until: idle | followup | speaking | thinking | listening | widget | answer | done
        (or {what: ..., timeout: s})."""
        what, timeout = (v, 60.0) if isinstance(v, str) else (v.get("what"), float(v.get("timeout", 60)))
        asst = self.ctx["asst"]
        end = time.time() + timeout
        start_n = len(self.rec.events)
        was = asst.mode
        while time.time() < end and not self.stop_all.is_set():
            new = self.rec.events[start_n:]
            # a state counts once it is entered after this step began (still being in it
            # from the turn before is not "the next one")
            if what in ("speaking", "thinking", "listening", "followup") and (
                    any(e["k"] == "state" and e.get("mode") == what for e in new) or (asst.mode == what and was != what)):
                return True
            if what == "idle" and asst.mode in ("idle", "off") and not asst.conv_lock.locked():
                return True
            if what == "done" and self._settle(0.2, quiet_s=0.0):
                return True
            if what == "widget" and any(e["k"] == "w_shown" for e in new):
                return True
            if what == "answer" and any(e["k"] == "audio" and UX._norm(e.get("text", "")) not in
                                        [UX._norm(f) for f in UX.FILLERS] for e in new):
                return True
            time.sleep(0.05)
        print(f"[ux] until {what}: timed out after {timeout:.0f}s")
        self.rec.mark("timeout", waited_for=what)
        return False

    def _type(self, text):
        """The hotkey's field: type `text` and press Enter (Qt key events to the island's window)."""
        isl = self.ctx["island"]
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest

        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QGuiApplication, QKeyEvent

        def key(ch):
            # a real key press/release with its text, delivered to the island's window
            # (QTest.keyClicks only takes widgets in PySide)
            k = Qt.Key_Space if ch == " " else (Qt.Key(ord(ch.upper())) if ch.isalnum() else Qt.Key_unknown)
            for typ in (QEvent.KeyPress, QEvent.KeyRelease):
                QGuiApplication.sendEvent(isl.view, QKeyEvent(typ, k, Qt.NoModifier, ch))

        for ch in text:
            self.gui(lambda c=ch: key(c))
            time.sleep(0.03)
        time.sleep(0.3)
        self.rec.mark("type_submit", text=text)

        def enter():
            sess = isl.session
            if sess is not None and sess.is_open and sess.state != "closed":
                for typ in (QEvent.KeyPress, QEvent.KeyRelease):
                    QGuiApplication.sendEvent(isl.view, QKeyEvent(typ, Qt.Key_Return, Qt.NoModifier, chr(13)))
                if sess.is_open:                          # the key didn't reach the field: same path as Enter
                    isl.model.inputEdited(text)
                    isl.model.inputEnter(text, False)
        self.gui(enter)

    def _click(self, what):
        """Click the island's mic / stop / not-me button with a synthetic mouse event."""
        isl = self.ctx["island"]
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtTest import QTest

        def go():
            p = self.rec.probe()
            s = float(isl.scale)
            w, cy = float(p.get("ow", 300)), float(p.get("ctlCy") or 20)
            x0, y0 = float(p.get("pillX", 0)), float(p.get("pillY", 0))
            if what == "stop":
                x, y = w - 26, cy
            elif what == "mic":
                x, y = w - 56, cy
            elif what in ("notme", "not_me"):
                x, y = w - 14 - 31, min(float(p.get("oh", 40)), 40) / 2
            else:
                raise ValueError(what)
            hit = isl.call("hit", x, y)
            pt = QPoint(round((x0 + x) * s), round((y0 + y) * s))
            QTest.mouseClick(isl.view, Qt.LeftButton, Qt.NoModifier, pt)
            return hit
        hit = self.gui(go)
        self.rec.mark(f"click_{what.replace('notme', 'not_me')}" if what != "notme" else "not_me", hit=str(hit))
        if what in ("notme", "not_me"):
            self.rec.mark("not_me")

    # -- end ---------------------------------------------------------------------------
    def finish(self):
        try:
            write_summary(self.out, self.results, self.audit, self.args)
        except Exception:
            traceback.print_exc()
        finally:
            print(f"\n[ux] results: {self.out}")
            self.ctx["bridge"].quit.emit()


# =============================================================================== summary
def write_summary(out: Path, results: list[dict], audit: Audit, args):
    rows = {}
    for r in results:
        for f in r["findings"]:
            rows.setdefault(f["kind"], []).append((r["name"], f))
    lines = [f"# UX stress run {out.name}", "",
             f"Mode: {'offline (fake brain/voice)' if args.offline else 'live (real app)'}; "
             f"scenarios run: {len(results)}; audited tool calls: {len(audit.calls)}; "
             f"refused attempts: {', '.join(audit.attempts) or 'none'}; "
             f"safety violations (a tool outside the safe list that ran): {', '.join(audit.violations) or 'none'}", "",
             "## Findings by how janky they feel", ""]
    order = sorted(rows, key=lambda k: -max(f["severity"] for _, f in rows[k]))
    if not order:
        lines.append("None: every detector was quiet.")
    for k in order:
        runs = sorted({n for n, _ in rows[k]})
        lines.append(f"### {k} (severity {max(f['severity'] for _, f in rows[k])}, {len(rows[k])}x in {len(runs)} run(s))")
        lines.append(f"_{UX.FEEL.get(k, '')}_")
        for n, f in rows[k][:6]:
            lines.append(f"- `{n}` @{f['t'] - next(r['t0'] for r in results if r['name'] == n):.2f}s: {f['msg']}")
        lines.append("")
    lines += ["## Stage latency (seconds after the end of speech)", "",
              "| run | first visual | first audio | widget | answer | answer end |", "|---|---|---|---|---|---|"]
    for r in results:
        for L in r["latency"]:
            lines.append(f"| {r['name']} | {L['first_visual']} | {L['first_audio']} | {L['widget']} | {L['answer']} | {L['answer_end']} |")
    lines += ["", "## Frames", "", "| run | live ticks | late ticks | in transitions | worst ms | swap p50/p95/p99 ms |",
              "|---|---|---|---|---|---|"]
    for r in results:
        fr = r["frames"]
        lines.append(f"| {r['name']} | {fr['live_ticks']} | {fr['hitches']} | {fr['hitches_in_transitions']} | "
                     f"{fr['worst_hitch_ms']} | {fr['swap_p50_ms']}/{fr['swap_p95_ms']}/{fr['swap_p99_ms']} |")
    lines += ["", "## Expectations", ""]
    for r in results:
        if r.get("expect") or r.get("error"):
            lines.append(f"- `{r['name']}`: {r.get('expect')}" + (f" ERROR {r['error']}" if r.get("error") else ""))
    (out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out / "summary.json").write_text(json.dumps(results, indent=1, default=str), encoding="utf-8")


# =============================================================================== re-score a run
def rescore(run_dir: Path, exclude: list[str] | None = None) -> list[dict]:
    """Run the current detectors over a finished run's timelines (so runs made with an
    older harness are scored the same way) and rewrite its summary.md/.json."""
    results = []
    for d in sorted(p for p in run_dir.iterdir() if (p / "timeline.jsonl").exists()):
        if any(re.fullmatch(x, d.name) for x in exclude or []):
            continue
        ev = UX.load_timeline(d / "timeline.jsonl")
        old = json.loads((d / "findings.json").read_text(encoding="utf-8")) if (d / "findings.json").exists() else {}
        name = old.get("name", d.name)
        res = UX.detect(ev, scenario=old.get("scenario", name.rsplit("-", 1)[0]))
        res.update(name=name, scenario=old.get("scenario", name.rsplit("-", 1)[0]), t0=ev[0]["t"] if ev else 0,
                   error=old.get("error"), expect=old.get("expect"), tools=old.get("tools"))
        results.append(res)
    write_summary(run_dir, results, Audit(lambda n: None), argparse.Namespace(offline="offline" in run_dir.name))
    return results


# =============================================================================== wake check
def wakecheck(args):
    """Does the user's wake model fire on their clips, and on Kokoro saying "hey claude"?"""
    main = main_checkout()
    link_state(main)
    import yaml
    cfg = yaml.safe_load((main / "config.yaml").read_text(encoding="utf-8")) if (main / "config.yaml").exists() else {}
    model = (cfg.get("wake") or {}).get("model") or "state/wake_models/hey_claude-20261109-mine/hey_claude.onnx"
    thr = float((cfg.get("wake") or {}).get("threshold", 0.5))
    from heyclaude.listen import WakeWord, resolve_wake_model
    path, why = resolve_wake_model(model, root=main)
    print(f"[wake] model {path} (threshold {thr}){' FALLBACK: ' + why if why else ''}")
    ww = WakeWord(path, thr)
    v = Voices(main, offline=False, wake="mine")
    rng = np.random.default_rng(5)

    def score(pcm):
        ww.reset()
        noise = (rng.standard_normal(RATE * 3) * 32768 * 10 ** (args.noise / 20)).astype(np.int16)
        x = noise.copy()
        x[RATE: RATE + len(pcm)] += pcm[: len(x) - RATE]
        best = 0.0
        for i in range(0, len(x) - BLOCK, BLOCK):
            best = max(best, float(ww.feed(x[i:i + BLOCK])))
        return best >= ww.threshold, best
    mine = [score(trim(c)) for c in v.wake_clips]
    print(f"[wake] your clips: {sum(h for h, _ in mine)}/{len(mine)} fire "
          f"(scores {', '.join(f'{s:.2f}' for _, s in mine)})")
    for voice in VOICES + ["af_heart"]:
        for phrase in ("Hey Claude", "Hey, Claude.", "hey claude"):
            h, s = score(trim(v.line(phrase, voice)))
            print(f"[wake] kokoro {voice:11s} {phrase!r:16s} {'FIRES' if h else 'no   '} {s:.2f}")


# =============================================================================== CLI
def main(argv=None):
    ap = argparse.ArgumentParser(prog="ux_stress", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run scenarios against the app")
    r.add_argument("scenarios", nargs="*", help="scenario names (default: all)")
    r.add_argument("-n", "--repeat", type=int, default=1, help="run the whole set this many times")
    r.add_argument("--offline", action="store_true", help="fake brain/voice/STT/wake (no GPU, models or login)")
    r.add_argument("--grab-fps", type=float, default=12.0, help="island frames per second during turns (0 = none)")
    r.add_argument("--gif", action="store_true", default=True)
    r.add_argument("--no-gif", dest="gif", action="store_false")
    r.add_argument("--noise", type=float, default=-58.0, help="room noise, dBFS")
    r.add_argument("--echo", type=float, default=0.15, help="the assistant's voice coming back into the mic (gain)")
    r.add_argument("--voice", default=None, help="Kokoro voice for the user's lines (default: per scenario/am_michael)")
    r.add_argument("--wake", choices=["mine", "synth"], default="mine", help="wake word: your clips or Kokoro")
    r.add_argument("--shuffle", action="store_true", help="shuffle the order on repeats")
    r.add_argument("--out", default=None, help="output folder (default state/ux/runs/<stamp>)")
    r.add_argument("--show", action="store_true", help="--offline: show the island on the desktop (default offscreen)")
    r.add_argument("--allow-running", action="store_true", help="don't refuse when another hey-claude is running")
    w = sub.add_parser("wakecheck", help="does the wake model fire on your clips and on Kokoro?")
    w.add_argument("--noise", type=float, default=-58.0)
    sub.add_parser("list", help="list the scenarios")
    rp = sub.add_parser("report", help="re-score a finished run with the current detectors")
    rp.add_argument("run_dir")
    rp.add_argument("--exclude", nargs="*", default=[], help="regexes of run names to leave out (harness artifacts)")
    args = ap.parse_args(argv)

    if args.cmd == "list":
        for s in load_scenarios():
            print(f"{s['name']:22s} {s.get('desc', '')}")
        return 0
    if args.cmd == "report":
        res = rescore(Path(args.run_dir), args.exclude)
        print(f"[ux] {len(res)} runs re-scored: {Path(args.run_dir) / 'summary.md'}")
        return 0
    if args.cmd == "wakecheck":
        wakecheck(args)
        return 0

    if args.offline and not args.show:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"     # nothing on the desktop
    scenarios = load_scenarios(args.scenarios or None)
    main = main_checkout()
    if not args.offline and not args.allow_running and other_instance_running():
        print("[ux] hey-claude is already running (it holds the GPU models and the mic). Quit it first, "
              "or pass --allow-running.")
        return 2
    link_state(main)
    UXDIR.mkdir(parents=True, exist_ok=True)
    scratch = UXDIR / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    out = Path(args.out) if args.out else UXDIR / "runs" / (time.strftime("%Y%m%d-%H%M%S") + ("-offline" if args.offline else ""))
    out.mkdir(parents=True, exist_ok=True)
    # the user's settings, made safe
    import yaml
    from heyclaude import config as C
    user = C.load(str(main / "config.yaml")) if (main / "config.yaml").exists() and not args.offline else C.load(os.devnull + ".none")
    hc = harness_config(user, scratch)
    cfg_path = UXDIR / "config.harness.yaml"
    cfg_path.write_text(yaml.safe_dump(hc, sort_keys=False, allow_unicode=True), encoding="utf-8")
    h = Harness(args, scenarios, out)
    if not args.offline:                       # make the voices before the app takes the CPU/GPU
        for sc in scenarios:
            for text, voice in texts_of(sc["steps"]):
                h.voices.line(text, voice or args.voice)
        h.voices.line("Hey Claude", VOICES[0])
    from heyclaude.__main__ import main as app_main
    argv = ["--config", str(cfg_path), "--no-tray"]
    try:
        app_main(argv, harness=h)
    except SystemExit:
        pass
    return 1 if h.audit.violations else 0


def other_instance_running() -> bool:
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-c",
                              "Get-CimInstance Win32_Process | ? { $_.CommandLine -match '-m heyclaude($|\\s)' } | "
                              "% { $_.ProcessId }"], capture_output=True, text=True, timeout=20).stdout
        pids = [int(x) for x in out.split() if x.strip().isdigit() and int(x) != os.getpid()]
        return bool(pids)
    except Exception:
        return False


if __name__ == "__main__":
    sys.exit(main())
