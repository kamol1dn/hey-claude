"""Dictate anywhere (M4d): hold a key, speak, let go, and the words are typed
where the cursor is, in any app.

- `dictate.key` (right alt by default) is its own hold key, separate from the
  hold-to-keep-listening Ctrl. Hold it while you talk; let go to paste.
- Double tap it for hands-free: it keeps listening until the next tap.
- Esc cancels. A different key pressed while you hold it (AltGr+e, Alt+Tab)
  makes it a shortcut: dictation is dropped and the shortcut goes through.
- The key is never swallowed outside dictation. Holding Alt alone would open
  a window's menu bar on release, so a masking key is sent while it's down
  (what AutoHotkey does); caps lock as the key is swallowed so it doesn't
  toggle; Esc is swallowed only when it cancels a dictation.
- The text goes through the clipboard: the user's clipboard is put back
  afterwards (paste.py); nothing is pasted into a password field.
- The personal dictionary (dictionary.py) is Whisper's initial prompt, then
  its replacements and snippets. Optional clean-up by Claude ("as said",
  "natural", "polished"), per app; off by default (it adds a second or two).
- The island shows "dictating" with the live level. Local stats only
  (state/dictation.json: dictations, words, seconds spoken, time saved).
"""
from __future__ import annotations

import json
import queue
import threading
import time

import numpy as np

from . import dictionary

RATE, BLOCK = 16000, 1280
VK_ESC = 0x1B
VK_MASK = 0xE8                      # unassigned: hides a lone Alt/Win press from menus
ALT_WIN = {0xA4, 0xA5, 0x12, 0x5B, 0x5C}
CAPS = 0x14
TYPING_WPM = 40                     # for "time saved"
CLEANUP_STYLES = ("off", "as_said", "natural", "polished")


class DictateKeys:
    """The key logic, without Windows: feed key events, get actions back.

    Actions: "start" (open the mic), "handsfree" (keep going after release),
    "stop" (the mic can close: hands-free ended by a tap; paste on "finish"),
    "finish" (key released: transcribe and paste), "cancel" (drop it).
    """

    def __init__(self, vks: frozenset[int], tap_s: float = 0.3, double_s: float = 0.4, clock=time.perf_counter):
        self.vks, self.tap_s, self.double_s, self.clock = frozenset(vks), tap_s, double_s, clock
        self.state = "idle"             # idle | hold | tap_wait | handsfree_down | handsfree | ending
        self.t_down = self.t_up = 0.0
        self.released_at: float | None = None

    @property
    def active(self) -> bool:
        return self.state != "idle"

    def key(self, vk: int, down: bool) -> tuple[list[str], bool]:
        """One key event -> (actions, swallow)."""
        now = self.clock()
        s = self.state
        if vk in self.vks:
            swallow = vk == CAPS
            if down:
                if s == "idle":
                    self.state, self.t_down = "hold", now
                    return ["start"], swallow
                if s == "tap_wait":
                    if now - self.t_up <= self.double_s:
                        self.state = "handsfree_down"
                        return ["handsfree"], swallow
                    self.state, self.t_down = "hold", now        # too late for a double tap: a new hold
                    return ["cancel", "start"], swallow
                if s == "handsfree":
                    self.state = "ending"
                    return ["stop"], swallow
                return [], swallow                                # auto-repeat
            if s == "hold":
                if now - self.t_down < self.tap_s:
                    self.state, self.t_up = "tap_wait", now
                    return [], swallow
                self.state, self.released_at = "idle", now
                return ["finish"], swallow
            if s == "handsfree_down":
                self.state = "handsfree"
                return [], swallow
            if s == "ending":
                self.state, self.released_at = "idle", now
                return ["finish"], swallow
            return [], swallow
        if s == "idle" or vk == VK_MASK:
            return [], False
        if vk == VK_ESC and down:
            self.state = "idle"
            return ["cancel"], True
        if down and s in ("hold", "tap_wait"):
            self.state = "idle"                                  # AltGr+e, Alt+Tab: a shortcut
            return ["cancel"], False
        return [], False

    def tick(self) -> list[str]:
        """Timeouts: a single short tap that wasn't followed by a second one."""
        if self.state == "tap_wait" and self.clock() - self.t_up > self.double_s:
            self.state = "idle"
            return ["cancel"]
        return []


# -- clean-up by Claude ---------------------------------------------------------------------
_STYLE_PROMPTS = {
    "as_said": "Fix only punctuation and capitalisation. Keep every word as said.",
    "natural": "Remove filler words (um, uh, like), false starts and repeated words; fix punctuation. "
               "Keep the speaker's wording otherwise.",
    "polished": "Rewrite it as clear, well-written text with the same meaning and tone. No additions.",
}


def cleanup_style(cfg: dict, app: str) -> str:
    """dictate.cleanup, overridden per app by dictate.cleanup_apps {exe: style}."""
    apps = {str(k).lower(): str(v) for k, v in (cfg.get("cleanup_apps") or {}).items()}
    style = apps.get(str(app or "").lower(), cfg.get("cleanup") or "off")
    style = str(style).lower().replace(" ", "_")
    return style if style in CLEANUP_STYLES else "off"


def claude_cleanup(text: str, style: str, model: str = "haiku", timeout: float = 15.0) -> str:
    """One `claude -p` call on the user's subscription. Returns the cleaned text
    (the input if anything goes wrong)."""
    import subprocess
    from .claude_cli import NO_WINDOW, child_env, find_claude
    system = ("You clean up dictated text. " + _STYLE_PROMPTS[style] +
              " Reply with the text only: no quotes, no preamble, nothing else.")
    cmd = [find_claude(), "-p", "--model", model, "--tools", "", "--strict-mcp-config", "--setting-sources", "",
           "--disable-slash-commands", "--no-session-persistence", "--system-prompt", system]
    r = subprocess.run(cmd, input=text, capture_output=True, text=True, encoding="utf-8", timeout=timeout,
                       env=child_env(), creationflags=NO_WINDOW)
    out = (r.stdout or "").strip()
    return out if r.returncode == 0 and out else text


# -- stats ----------------------------------------------------------------------------------------
def add_stats(path, words: int, spoken_s: float) -> dict:
    try:
        st = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        st = {}
    st["dictations"] = int(st.get("dictations", 0)) + 1
    st["words"] = int(st.get("words", 0)) + words
    st["spoken_s"] = round(float(st.get("spoken_s", 0.0)) + spoken_s, 1)
    st["saved_s"] = round(max(0.0, st["words"] / TYPING_WPM * 60 - st["spoken_s"]), 1)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(st, indent=1), encoding="utf-8")
    except Exception as e:
        print(f"[dictate] stats: {e}")
    return st


# -- the whole thing ---------------------------------------------------------------------------------
class Dictation:
    """Key events (from a LowLevelHook, or inject() in tests) -> mic -> text -> paste.

    speech: speech.manager.Speech (transcribe / wake / begin / cancel).
    ui(mode, text): the island; level(v): its meter; note(text): a flash.
    mic_factory(): a MicSource-like object (start / close / iteration).
    on_active(bool): the assistant pauses its wake word meanwhile.
    """

    def __init__(self, cfg: dict, speech, ui=None, level=None, note=None, mic_factory=None, paster=None,
                 cleaner=None, vad_factory=None, on_active=None, stats_path=None, foreground=None,
                 clock=time.perf_counter, rest=None):
        self.cfg = cfg                                 # the whole live config
        self.speech = speech
        self.ui = ui or (lambda mode, text="": None)
        self.level = level or (lambda v: None)
        self.note = note or (lambda t: None)
        self.rest = rest or (lambda: self.ui("idle", ""))
        self.mic_factory = mic_factory
        self.paster = paster
        self.cleaner = cleaner or claude_cleanup
        self.vad_factory = vad_factory
        self.on_active = on_active or (lambda on: None)
        self.stats_path = stats_path
        self.foreground = foreground
        self.clock = clock
        self.keys = DictateKeys(self.parse_key(), clock=clock)
        self.last_text = ""
        self.last_ms: float | None = None              # key release -> text pasted
        self.timings: list[dict] = []
        self._q: queue.Queue = queue.Queue()
        self._mic = None
        self._blocks: list = []
        self._voiced_ms = 0.0
        self._rec: threading.Thread | None = None
        self._lock = threading.Lock()
        self.hook = None
        threading.Thread(target=self._worker, daemon=True, name="dictate").start()

    # -- config ------------------------------------------------------------------------------
    @property
    def dcfg(self) -> dict:
        return self.cfg.setdefault("dictate", {})

    def parse_key(self) -> frozenset[int]:
        from .holdkey import parse
        return parse(self.dcfg.get("key") or "right alt")

    def set_key(self):
        self.keys.vks = self.parse_key()

    # -- key events (hook thread: be quick) ------------------------------------------------------
    def on_key(self, vk: int, down: bool, injected: bool = False) -> bool:
        if not self.dcfg.get("enabled", True):
            return False
        if vk == VK_MASK:
            return False
        actions, swallow = self.keys.key(vk, down)
        if down and vk in self.keys.vks and vk in ALT_WIN and self.keys.active:
            _mask_key()                                # a lone Alt/Win must not open a menu
        for a in actions:
            self._q.put((a, self.clock()))
        return swallow

    inject = on_key                                     # tests and tools/dictate_check.py

    def install(self):
        """Watch the key system-wide (a hook that passes everything else on)."""
        from .holdkey import LowLevelHook
        if self.hook is None:
            self.hook = LowLevelHook(self.on_key)

    def close(self):
        if self.hook is not None:
            self.hook.close()
            self.hook = None
        self._q.put(("quit", 0.0))

    # -- worker ---------------------------------------------------------------------------------------
    def _worker(self):
        while True:
            try:
                a, t = self._q.get(timeout=0.1)
            except queue.Empty:
                for a in self.keys.tick():
                    self._do(a, self.clock())
                continue
            if a == "quit":
                return
            self._do(a, t)

    def _do(self, a: str, t: float):
        try:
            if a == "start":
                self._start()
            elif a == "handsfree":
                self.ui("dictating", "hands-free: tap to finish")
            elif a == "stop":
                self._stop_mic()
            elif a == "finish":
                self._finish(t)
            elif a == "cancel":
                self._cancel()
        except Exception as e:
            print(f"[dictate] {a}: {e}")
            self._stop_mic()
            self.on_active(False)
            self.rest()

    def _start(self):
        self._blocks, self._voiced_ms = [], 0.0
        self.on_active(True)
        self.ui("dictating", "dictating…")
        try:
            self.speech.wake()                          # lean: reload while the user talks
            self.speech.begin(live=bool(self.dcfg.get("live_backend", True))
                              and self.cfg.get("listen", {}).get("device") is None)
        except Exception as e:
            print(f"[dictate] speech: {e}")
        self._mic = mic = self.mic_factory()
        mic.start()
        self._rec = threading.Thread(target=self._record, args=(mic,), daemon=True, name="dictate-mic")
        self._rec.start()

    def _record(self, mic):
        from .listen import level
        vad = None
        if self.vad_factory is not None:
            try:
                vad = self.vad_factory()
                vad.reset()
            except Exception:
                vad = None
        cap = int(float(self.dcfg.get("max_seconds", 300)) * RATE / BLOCK)
        for block in mic:
            self._blocks.append(block)
            self.level(level(block))
            if vad is not None:
                try:
                    self._voiced_ms += sum(vad.frames(block)) * float(vad.frame_ms)
                except Exception:
                    vad = None
            if len(self._blocks) >= cap:
                break

    def _stop_mic(self):
        mic, self._mic = self._mic, None
        if mic is not None:
            try:
                mic.close()
            except Exception:
                pass
        if self._rec is not None:
            self._rec.join(1.0)
            self._rec = None

    def _cancel(self):
        self._stop_mic()
        try:
            self.speech.cancel()
        except Exception:
            pass
        self._blocks = []
        self.on_active(False)
        self.rest()

    def _finish(self, t_release: float):
        self._stop_mic()
        blocks, self._blocks = self._blocks, []
        self.on_active(False)
        audio = np.concatenate(blocks) if blocks else np.zeros(0, np.int16)
        spoken_s = len(audio) / RATE
        heard = self.vad_factory is None or self._voiced_ms >= float(self.dcfg.get("min_speech_ms", 150))
        if spoken_s < 0.3 or not heard:
            try:
                self.speech.cancel()
            except Exception:
                pass
            self.note("didn't hear anything")
            self.rest()
            return
        self.ui("dictating", "…")
        t0 = self.clock()
        text = self.speech.transcribe(audio).strip()
        t_stt = self.clock()
        text = dictionary.apply(text, self.cfg.get("dictionary"))
        app = self.foreground() if self.foreground is not None else ""
        style = cleanup_style(self.dcfg, app)
        if text and style != "off":
            try:
                text = self.cleaner(text, style).strip() or text
            except Exception as e:
                print(f"[dictate] clean-up failed ({e}); pasting it as said")
        if not text:
            self.note("didn't catch that")
            self.rest()
            return
        if self.dcfg.get("trailing_space", True) and text[-1:] in ".!?,;:":
            text += " "                                 # the next dictation joins it naturally
        self.last_text = text
        r = self.paster(text) if self.paster is not None else None
        t_done = self.clock()
        ok = bool(getattr(r, "ok", True)) if r is not None else True
        self.last_ms = (t_done - t_release) * 1000
        row = dict(audio_s=round(spoken_s, 2), stt_ms=round((t_stt - t0) * 1000), release_to_text_ms=round(self.last_ms),
                   app=getattr(r, "app", app) or app, ok=ok, reason=getattr(r, "reason", ""), style=style,
                   words=len(text.split()))
        self.timings.append(row)
        print("[dictate] " + "  ".join(f"{k}={v}" for k, v in row.items()))
        if ok:
            if self.stats_path is not None:
                add_stats(self.stats_path, len(text.split()), spoken_s)
            self.note(f"typed {len(text.split())} words")
        else:
            why = getattr(r, "reason", "") or "paste failed"
            self.note(f"not typed: {why} (paste again: {self.dcfg.get('paste_again_hotkey') or 'off'})")
        self.rest()

    def paste_again(self):
        """The "paste last dictation again" hotkey."""
        if self.last_text and self.paster is not None:
            threading.Thread(target=self.paster, args=(self.last_text,), daemon=True, name="dictate-again").start()


def _mask_key():
    try:
        import ctypes
        u = ctypes.windll.user32
        u.keybd_event(VK_MASK, 0, 0, 0)
        u.keybd_event(VK_MASK, 0, 2, 0)                 # KEYEVENTF_KEYUP
    except Exception:
        pass


def attach(asst, bridge, cfg: dict, mic_factory=None, install: bool = True) -> Dictation:
    """Build the app's Dictation (after asst.load(): it uses asst.speech) and,
    with `install`, hook the key system-wide."""
    from . import paste
    from .config import STATE_DIR
    from .listen import MicSource
    from .vad import make_vad

    vad_box: dict = {}

    def vad():
        if "v" not in vad_box:
            vad_box["v"] = make_vad(cfg["listen"])
        return vad_box["v"]

    def ui(mode, text=""):
        if mode == "dictating" or asst.mode == "dictating":
            asst.set_mode(mode, text)

    def rest():
        if asst.mode == "dictating":                  # don't knock an answer in progress off the island
            asst.rest()

    def on_active(on: bool):
        asst.dictating = on

    def note(text: str):
        sig = getattr(bridge, "note", None)
        if sig is not None:
            sig.emit(text)

    d = Dictation(cfg, asst.speech, ui=ui, level=bridge.level.emit, note=note,
                  mic_factory=mic_factory or (lambda: MicSource(cfg["listen"].get("device"))),
                  paster=lambda text: paste.paste_text(text, restore_delay_s=float(
                      cfg.get("dictate", {}).get("restore_delay_s", 0.3))),
                  vad_factory=vad, on_active=on_active, stats_path=STATE_DIR / "dictation.json",
                  foreground=paste.foreground_app, rest=rest)
    if install:
        d.install()
    return d
