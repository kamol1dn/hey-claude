"""Settings -> Wake word: the personal tune-up / custom phrase wizard (M2b).

    1 Phrase  ->  2 Record (~25 clips)  ->  3 Room sound (optional, 1 min)
              ->  4 Train (progress + time estimate)  ->  5 Test and apply (with undo)

Everything it writes stays in state/ (clips in state/wake_samples/, the room
recording in state/wake_room/, models in state/wake_models/). Training runs
`python -m heyclaude.wake_train personal --json` in a subprocess (no console
window), so the app stays responsive and Cancel just stops it.

`WizardState` is the logic without Qt (tested offline); `WakeWizard` is the
dialog on top of it.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from .config import ROOT, STATE_DIR
from .wake_samples import CUES, phrase_folder

PAGES = ("phrase", "record", "room", "train", "test")
TITLES = {"phrase": "Phrase", "record": "Record", "room": "Room sound", "train": "Train", "test": "Test and apply"}
TARGET_CLIPS = 25
MIN_CLIPS = 16
ROOM_DIR = STATE_DIR / "wake_room"
MODELS_DIR = STATE_DIR / "wake_models"
HISTORY = MODELS_DIR / "history.json"
FALSE_WAKES = STATE_DIR / "wake_negatives"
DEFAULT_PHRASE = "hey claude"
COMMON = {"hey", "hi", "hello", "okay", "ok", "yes", "no", "yeah", "thanks", "thank you", "stop", "go", "please",
          "hey there", "hi there", "excuse me", "sorry", "right", "so", "well", "alright", "all right", "listen",
          "computer", "hey you", "come on", "what", "wait"}


def syllables(phrase: str) -> int:
    """Rough syllable count (vowel groups per word, at least one per word)."""
    n = 0
    for w in re.findall(r"[a-z']+", phrase.lower()):
        g = re.findall(r"[aeiouy]+", w)
        k = len(g) - (1 if w.endswith("e") and len(g) > 1 and not w.endswith(("le", "ee")) else 0)
        n += max(1, k)
    return n


def phrase_problems(phrase: str) -> tuple[list[str], list[str]]:
    """(errors, warnings) for a wake phrase."""
    p = " ".join(phrase.strip().split())
    if not p:
        return ["Type a phrase."], []
    if not re.search(r"[a-zA-Z]", p):
        return ["Use words with English letters (the synthetic voices read English)."], []
    warns = []
    low = re.sub(r"[^a-z' ]+", "", p.lower()).strip()
    if low in COMMON or (len(low.split()) == 1 and low in COMMON):
        warns.append("That's said all the time in normal talk, so it would wake by mistake. Add a name: "
                     "\"hey claude\", \"okay jasper\".")
    if syllables(p) < 2 or (len(p.split()) == 1 and syllables(p) < 3):
        warns.append("Very short: one short word wakes by mistake far more often. Two words, like "
                     "\"hey claude\", is about the minimum.")
    if len(p.split()) > 4:
        warns.append("Long phrases are hard to say the same way every time; 2-3 words work best.")
    return [], warns


class WizardState:
    """The wizard's logic: which page, what's recorded, training status, apply/undo."""

    def __init__(self, phrase: str = DEFAULT_PHRASE, target: int = TARGET_CLIPS, minimum: int = MIN_CLIPS):
        self.page = "phrase"
        self.phrase = phrase
        self.target, self.minimum = target, minimum
        self.clips: list[Path] = []                  # this session's clips, in order
        self.problems: dict[str, list[str]] = {}     # clip path -> problems (empty = fine)
        self.existing: list[Path] = []               # clips recorded earlier for this phrase
        self.room: Path | None = None
        self.room_skipped = False
        self.train_status = "idle"                   # idle | running | done | failed | cancelled
        self.train_msg = ""
        self.progress: dict = {}
        self.model: Path | None = None
        self.report: dict = {}
        self.applied = False

    # phrase --------------------------------------------------------------------
    def set_phrase(self, phrase: str) -> tuple[list[str], list[str]]:
        self.phrase = " ".join(phrase.strip().split())
        return phrase_problems(self.phrase)

    def folder(self) -> tuple[Path, str]:
        return phrase_folder(self.phrase)

    # record --------------------------------------------------------------------
    def good_clips(self) -> list[Path]:
        return [p for p in self.all_clips() if not self.problems.get(str(p))]

    def all_clips(self) -> list[Path]:
        seen = {str(p) for p in self.clips}
        return [p for p in self.existing if str(p) not in seen] + list(self.clips)

    def cue(self) -> tuple[str, str]:
        """(tag, instruction) for the next clip."""
        return CUES[len(self.all_clips()) % len(CUES)]

    def add_clip(self, path: Path, problems: list[str]):
        self.clips.append(Path(path))
        self.problems[str(path)] = list(problems)

    def redo_last(self) -> Path | None:
        """Forget (and delete) the last clip of this session."""
        if not self.clips:
            return None
        p = self.clips.pop()
        self.problems.pop(str(p), None)
        try:
            Path(p).unlink()
        except OSError:
            pass
        return p

    # room ----------------------------------------------------------------------
    def set_room(self, path: Path | None):
        self.room, self.room_skipped = (Path(path) if path else None), path is None

    # train ---------------------------------------------------------------------
    def train_started(self):
        self.train_status, self.train_msg, self.progress = "running", "", {}
        self.model, self.report, self.applied = None, {}, False

    def train_progress(self, d: dict):
        self.progress = d

    def train_finished(self, model: Path, report: dict):
        self.train_status, self.model, self.report = "done", Path(model), report

    def train_failed(self, msg: str):
        self.train_status, self.train_msg = ("cancelled" if msg == "cancelled" else "failed"), msg

    # navigation ------------------------------------------------------------------
    def can_next(self) -> tuple[bool, str]:
        if self.page == "phrase":
            errors, _ = phrase_problems(self.phrase)
            return (not errors, errors[0] if errors else "")
        if self.page == "record":
            n = len(self.good_clips())
            return (n >= self.minimum,
                    "" if n >= self.minimum else f"{n} good clips so far; at least {self.minimum} are needed "
                                                 f"({self.target} is better).")
        if self.page == "room":
            return True, ""
        if self.page == "train":
            return (self.train_status == "done", "" if self.train_status == "done" else "Train first.")
        return False, ""

    def can_back(self) -> bool:
        return self.page != "phrase" and self.train_status != "running"

    def next(self) -> str:
        ok, why = self.can_next()
        if not ok:
            raise ValueError(why or "can't go on yet")
        self.page = PAGES[PAGES.index(self.page) + 1]
        return self.page

    def back(self) -> str:
        if not self.can_back():
            raise ValueError("can't go back now")
        self.page = PAGES[PAGES.index(self.page) - 1]
        return self.page

    def train_args(self, false_wakes_dir: Path = FALSE_WAKES) -> list[str]:
        """Arguments for `python -m heyclaude.wake_train`."""
        a = ["personal", "--phrase", self.phrase, "--json", "--clips", *[str(p) for p in self.all_clips()
                                                                       if not self.problems.get(str(p))]]
        if self.room:
            a += ["--room", str(self.room)]
        if self.phrase.lower() == DEFAULT_PHRASE and Path(false_wakes_dir).is_dir() \
                and any(Path(false_wakes_dir).glob("*.wav")):
            a += ["--false-wakes", str(false_wakes_dir)]
        return a


# -- applying a model, with undo ---------------------------------------------------------
def _rel(path: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(ROOT.resolve())).replace("\\", "/")
    except ValueError:
        return str(path)


def load_history(path: Path = HISTORY) -> list[dict]:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def apply_model(cfg: dict, model: Path | str, threshold: float = 0.5, history: Path = HISTORY) -> dict:
    """Point `wake.model` at `model` (threshold 0.5: wizard models are
    calibrated to it), remembering the previous setting for undo. Returns the
    new config (a copy); the caller saves it."""
    import copy
    new = copy.deepcopy(cfg)
    h = load_history(history)
    h.append(dict(model=cfg["wake"]["model"], threshold=cfg["wake"]["threshold"], when=time.strftime("%Y-%m-%d %H:%M")))
    Path(history).parent.mkdir(parents=True, exist_ok=True)
    Path(history).write_text(json.dumps(h[-20:], indent=2), encoding="utf-8")
    m = str(model)
    new["wake"]["model"] = _rel(Path(m)) if m.lower().endswith(".onnx") else m
    new["wake"]["threshold"] = float(threshold)
    return new


def undo_model(cfg: dict, history: Path = HISTORY) -> dict | None:
    """The config with the previous wake model back (None if there's nothing to undo)."""
    import copy
    h = load_history(history)
    if not h:
        return None
    prev = h.pop()
    Path(history).write_text(json.dumps(h, indent=2), encoding="utf-8")
    new = copy.deepcopy(cfg)
    new["wake"]["model"], new["wake"]["threshold"] = prev["model"], float(prev["threshold"])
    return new


def describe_model(model: str) -> str:
    """"shared default" / "personal tune-up (2026-11-09)" / "stock openWakeWord" / "your own file"."""
    from .listen import resolve_wake_model
    from .wake_models import is_default_path
    path, why = resolve_wake_model(model)
    if why:
        return f"not found, using hey jarvis ({why})"
    if is_default_path(model):
        return "the shared default model"
    if not path.lower().endswith(".onnx"):
        return "a stock openWakeWord model"
    try:
        if Path(path).resolve().is_relative_to(MODELS_DIR.resolve()):
            return f"your personal tune-up ({Path(path).parent.name})"
    except (OSError, ValueError):
        pass
    return "your own model file"


# -- training subprocess ----------------------------------------------------------------
class TrainRunner:
    """Runs the trainer in a subprocess and reports its JSON lines.

    on_progress(dict), on_log(str), on_done(model, report), on_fail(message)
    are called from a reader thread."""

    def __init__(self, args: list[str], on_progress=None, on_log=None, on_done=None, on_fail=None,
                 python: str | None = None, cwd: Path = ROOT, popen=subprocess.Popen):
        self.args, self.cwd = args, cwd
        self.on_progress, self.on_log, self.on_done, self.on_fail = on_progress, on_log, on_done, on_fail
        self.python = python or _console_python()
        self.popen = popen
        self.proc = None
        self.cancelled = False

    def start(self):
        cmd = [self.python, "-u", "-m", "heyclaude.wake_train", *self.args]
        env = dict(os.environ, PYTHONUTF8="1", HF_HUB_DISABLE_XET="1")
        self.proc = self.popen(cmd, cwd=str(self.cwd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), text=True,
                               encoding="utf-8", errors="replace")
        threading.Thread(target=self._read, daemon=True, name="wake-train").start()

    def cancel(self):
        self.cancelled = True
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()

    def _read(self):
        done = False
        last_err = ""
        for line in self.proc.stdout:
            line = line.strip()
            if not line.startswith("{"):
                if line and "W:onnxruntime" not in line:
                    last_err = line
                    if self.on_log:
                        self.on_log(line)
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if "progress" in d and self.on_progress:
                self.on_progress(d["progress"])
            elif "log" in d and self.on_log:
                self.on_log(d["log"])
            elif d.get("done"):
                done = True
                if self.on_done:
                    self.on_done(Path(d["model"]), d.get("report", {}))
            elif "error" in d:
                done = True
                if self.on_fail:
                    self.on_fail(d["error"])
        code = self.proc.wait()
        if not done and self.on_fail:
            self.on_fail("cancelled" if self.cancelled else f"the trainer stopped (exit {code}): {last_err}"[:300])


def _console_python() -> str:
    """python.exe next to pythonw.exe (the trainer prints to a pipe)."""
    exe = sys.executable
    if os.path.basename(exe).lower() == "pythonw.exe":
        cand = os.path.join(os.path.dirname(exe), "python.exe")
        if os.path.isfile(cand):
            return cand
    return exe


def missing_requirements(tts_cfg: dict | None = None, find_spec=None, isfile=os.path.isfile) -> list[str]:
    """What the trainer needs that isn't installed (empty = ready)."""
    import importlib.util
    find = find_spec or importlib.util.find_spec
    out = []
    pkgs = [m for m, pip in (("kokoro_onnx", "kokoro-onnx"), ("onnx", "onnx")) if find(m) is None]
    if pkgs:
        out.append("Python packages: .venv\\Scripts\\python -m pip install " +
                   " ".join({"kokoro_onnx": "kokoro-onnx", "onnx": "onnx"}[m] for m in pkgs))
    from .tts import kokoro_paths
    model, voices = kokoro_paths(tts_cfg or {})
    if not (isfile(model) and isfile(voices)):
        out.append(f"the Kokoro voice files ({model}, {voices}); see README \"Kokoro voice\"")
    return out


def fmt_eta(s: float) -> str:
    s = max(0, int(s))
    return f"{s // 60} min {s % 60:02d} s" if s >= 60 else f"{s} s"


# -- live test ---------------------------------------------------------------------------
class LiveTester:
    """Runs a model on the microphone and reports scores (the wizard's Test step).
    on_score(score) per 80 ms block, on_hit(count) per wake (0.5, 2 s apart)."""

    def __init__(self, model: Path, open_source, on_score=None, on_hit=None, threshold: float = 0.5):
        self.model, self.open_source = Path(model), open_source
        self.on_score, self.on_hit, self.threshold = on_score, on_hit, threshold
        self._stop = threading.Event()
        self.hits = 0

    def start(self):
        self._stop.clear()
        threading.Thread(target=self._run, daemon=True, name="wake-test").start()

    def stop(self):
        self._stop.set()

    def _run(self):
        from openwakeword.model import Model
        m = Model(wakeword_models=[str(self.model)], inference_framework="onnx")
        name = next(iter(m.models))
        src = self.open_source()
        src.start()
        quiet_until = 0
        n = 0
        try:
            for block in src:
                if self._stop.is_set():
                    break
                s = float(m.predict(block)[name])
                n += 1
                if self.on_score:
                    self.on_score(s)
                if s >= self.threshold and n >= quiet_until:
                    self.hits += 1
                    quiet_until = n + 25
                    if self.on_hit:
                        self.on_hit(self.hits)
        finally:
            src.close()


# -- the dialog ------------------------------------------------------------------------------
def _qt():
    from PySide6 import QtCore, QtGui, QtWidgets
    return QtCore, QtGui, QtWidgets


def make_wizard(cfg: dict, hooks=None, parent=None, on_apply=None, on_undo=None):
    """Build the wizard dialog. on_apply(model_path) switches hey-claude to a
    model and on_undo() goes back to the previous one (SettingsWindow saves the
    config); both return a status line. hooks: pause()/resume() listening,
    gpu_util()."""
    QtCore, QtGui, QtWidgets = _qt()
    from .listen import MicSource
    from .wake_samples import ClipRecorder, clip_path, list_clips, next_index, record_seconds, write_wav

    class Bridge(QtCore.QObject):
        level = QtCore.Signal(float)
        clip = QtCore.Signal(str, list)
        fail = QtCore.Signal(str)
        room = QtCore.Signal(str)
        prog = QtCore.Signal(dict)
        log = QtCore.Signal(str)
        done = QtCore.Signal(str, dict)
        tfail = QtCore.Signal(str)
        score = QtCore.Signal(float)
        hit = QtCore.Signal(int)

    class Meter(QtWidgets.QWidget):
        """A level bar: green, amber when very quiet, red near clipping."""

        def __init__(self):
            super().__init__()
            self.v = 0.0
            self.mark = None
            self.setMinimumHeight(14)

        def set(self, v):
            self.v = max(v, self.v * 0.8)
            self.update()

        def paintEvent(self, _):
            p = QtGui.QPainter(self)
            p.setRenderHint(QtGui.QPainter.Antialiasing)
            r = self.rect().adjusted(0, 2, 0, -2)
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(QtGui.QColor("#262626"))
            p.drawRoundedRect(r, 5, 5)
            c = "#E5646A" if self.v > 0.95 else "#6CCB7A" if self.v > 0.08 else "#E5A04A"
            p.setBrush(QtGui.QColor(c))
            p.drawRoundedRect(QtCore.QRectF(r.x(), r.y(), r.width() * min(1.0, self.v), r.height()), 5, 5)
            if self.mark is not None:
                x = r.x() + r.width() * self.mark
                p.setPen(QtGui.QPen(QtGui.QColor("#EDEDED"), 1.5))
                p.drawLine(QtCore.QPointF(x, r.top()), QtCore.QPointF(x, r.bottom()))

    device = cfg.get("listen", {}).get("device")
    if isinstance(device, str) and device.isdigit():
        device = int(device)

    def open_mic():
        return MicSource(device)

    class WakeWizard(QtWidgets.QDialog):
        def __init__(self):
            super().__init__(parent)
            self.setWindowTitle("Wake word: personal tune-up")
            self.resize(640, 520)
            if parent is not None:
                self.setStyleSheet(parent.styleSheet())
            self.st = WizardState()
            self.b = Bridge()
            self.recorder = ClipRecorder(open_mic, on_level=self.b.level.emit,
                                         on_done=lambda p, pr: self.b.clip.emit(str(p), pr),
                                         on_fail=self.b.fail.emit)
            self.runner: TrainRunner | None = None
            self.tester: LiveTester | None = None
            self._room_stop = None
            self._paused = False
            self.b.level.connect(lambda v: self.meter.set(v))
            self.b.clip.connect(self._clip_done)
            self.b.fail.connect(self._clip_fail)
            self.b.room.connect(self._room_done)
            self.b.prog.connect(self._train_prog)
            self.b.log.connect(lambda m: self.train_log.appendPlainText(m))
            self.b.done.connect(self._train_done)
            self.b.tfail.connect(self._train_fail)
            self.b.score.connect(lambda s: self.test_meter.set(s))
            self.b.hit.connect(lambda n: self.test_hits.setText(f"{n} wake{'s' if n != 1 else ''} heard"))

            lay = QtWidgets.QVBoxLayout(self)
            self.steps = QtWidgets.QLabel()
            self.steps.setTextFormat(QtCore.Qt.RichText)
            lay.addWidget(self.steps)
            self.stack = QtWidgets.QStackedWidget()
            lay.addWidget(self.stack, 1)
            for build in (self._p_phrase, self._p_record, self._p_room, self._p_train, self._p_test):
                self.stack.addWidget(build())
            self.status = QtWidgets.QLabel("")
            self.status.setObjectName("hint")
            self.status.setWordWrap(True)
            lay.addWidget(self.status)
            row = QtWidgets.QHBoxLayout()
            self.btn_back = QtWidgets.QPushButton("Back", clicked=self._back)
            self.btn_next = QtWidgets.QPushButton("Next", clicked=self._next)
            self.btn_next.setObjectName("primary")
            self.btn_close = QtWidgets.QPushButton("Close", clicked=self.close)
            row.addWidget(self.btn_back)
            row.addStretch(1)
            row.addWidget(self.btn_next)
            row.addWidget(self.btn_close)
            lay.addLayout(row)
            self._refresh()

        # -- pages ---------------------------------------------------------------
        @staticmethod
        def _help(text):
            lab = QtWidgets.QLabel(text)
            lab.setObjectName("help")
            lab.setWordWrap(True)
            lab.setTextFormat(QtCore.Qt.RichText)
            return lab

        def _p_phrase(self):
            w = QtWidgets.QWidget()
            v = QtWidgets.QVBoxLayout(w)
            v.addWidget(self._help(
                "Optional. Everyone gets the shared \"hey claude\" model, trained on many synthetic voices. "
                "This tunes a model to <b>your</b> voice and accent, or to a <b>different phrase</b>. "
                "About 15 minutes on this PC: 5 minutes of recording, the rest is the computer working. "
                "Everything stays on this PC (state\\wake_models), never in the repository."))
            self.ed_phrase = QtWidgets.QLineEdit(self.st.phrase)
            self.ed_phrase.textChanged.connect(self._phrase_changed)
            f = QtWidgets.QFormLayout()
            f.addRow("Wake phrase", self.ed_phrase)
            v.addLayout(f)
            self.lb_phrase = QtWidgets.QLabel("")
            self.lb_phrase.setObjectName("warn")
            self.lb_phrase.setWordWrap(True)
            v.addWidget(self.lb_phrase)
            v.addStretch(1)
            return w

        def _p_record(self):
            w = QtWidgets.QWidget()
            v = QtWidgets.QVBoxLayout(w)
            self.lb_cue = QtWidgets.QLabel("")
            self.lb_cue.setWordWrap(True)
            f = self.lb_cue.font()
            f.setPointSize(f.pointSize() + 3)
            self.lb_cue.setFont(f)
            v.addWidget(self.lb_cue)
            self.meter = Meter()
            v.addWidget(self.meter)
            row = QtWidgets.QHBoxLayout()
            self.btn_rec = QtWidgets.QPushButton("Record", clicked=self._record)
            self.btn_rec.setObjectName("primary")
            self.btn_redo = QtWidgets.QPushButton("Redo the last one", clicked=self._redo)
            row.addWidget(self.btn_rec)
            row.addWidget(self.btn_redo)
            row.addStretch(1)
            v.addLayout(row)
            self.lb_count = QtWidgets.QLabel("")
            v.addWidget(self.lb_count)
            self.lst = QtWidgets.QListWidget()
            v.addWidget(self.lst, 1)
            v.addWidget(self._help("Press Record, then say the phrase the way the line above says. It stops "
                                   "when you stop talking. Mix it up: near and far, quiet and loud, fast and slow. "
                                   "Listening is paused while this window records."))
            return w

        def _p_room(self):
            w = QtWidgets.QWidget()
            v = QtWidgets.QVBoxLayout(w)
            v.addWidget(self._help(
                "Optional: one minute of this room as it usually sounds, so the model learns what <b>isn't</b> "
                "the wake word here. Talk normally (about anything but the phrase), or leave the TV, fan or "
                "music running. You can skip this."))
            self.room_meter = Meter()
            v.addWidget(self.room_meter)
            row = QtWidgets.QHBoxLayout()
            self.btn_room = QtWidgets.QPushButton("Record 1 minute", clicked=self._room)
            self.btn_room.setObjectName("primary")
            self.btn_room_skip = QtWidgets.QPushButton("Skip", clicked=lambda: (self.st.set_room(None), self._next()))
            row.addWidget(self.btn_room)
            row.addWidget(self.btn_room_skip)
            row.addStretch(1)
            v.addLayout(row)
            self.lb_room = QtWidgets.QLabel("")
            v.addWidget(self.lb_room)
            v.addStretch(1)
            self.b.level.connect(lambda x: self.room_meter.set(x) if self.st.page == "room" else None)
            return w

        def _p_train(self):
            w = QtWidgets.QWidget()
            v = QtWidgets.QVBoxLayout(w)
            self.lb_plan = self._help("")
            v.addWidget(self.lb_plan)
            row = QtWidgets.QHBoxLayout()
            self.btn_train = QtWidgets.QPushButton("Train", clicked=self._train)
            self.btn_train.setObjectName("primary")
            self.btn_cancel = QtWidgets.QPushButton("Cancel training", clicked=self._cancel_train)
            row.addWidget(self.btn_train)
            row.addWidget(self.btn_cancel)
            row.addStretch(1)
            v.addLayout(row)
            self.bar = QtWidgets.QProgressBar()
            self.bar.setRange(0, 1000)
            self.bar.setTextVisible(False)
            v.addWidget(self.bar)
            self.lb_train = QtWidgets.QLabel("")
            self.lb_train.setWordWrap(True)
            v.addWidget(self.lb_train)
            self.train_log = QtWidgets.QPlainTextEdit()
            self.train_log.setReadOnly(True)
            self.train_log.setMaximumBlockCount(400)
            v.addWidget(self.train_log, 1)
            return w

        def _p_test(self):
            w = QtWidgets.QWidget()
            v = QtWidgets.QVBoxLayout(w)
            self.lb_result = self._help("")
            v.addWidget(self.lb_result)
            self.test_meter = Meter()
            self.test_meter.mark = 0.5
            v.addWidget(self.test_meter)
            row = QtWidgets.QHBoxLayout()
            self.btn_test = QtWidgets.QPushButton("Test: say it a few times", clicked=self._test)
            self.test_hits = QtWidgets.QLabel("")
            row.addWidget(self.btn_test)
            row.addWidget(self.test_hits)
            row.addStretch(1)
            v.addLayout(row)
            row = QtWidgets.QHBoxLayout()
            self.btn_apply = QtWidgets.QPushButton("Use this model", clicked=self._apply)
            self.btn_apply.setObjectName("primary")
            self.btn_undo = QtWidgets.QPushButton("Undo (back to the previous model)", clicked=self._undo)
            row.addWidget(self.btn_apply)
            row.addWidget(self.btn_undo)
            row.addStretch(1)
            v.addLayout(row)
            v.addStretch(1)
            return w

        # -- navigation ----------------------------------------------------------
        def _refresh(self):
            names = []
            for i, pg in enumerate(PAGES):
                t = f"{i + 1} {TITLES[pg]}"
                names.append(f"<b style='color:#D97757'>{t}</b>" if pg == self.st.page else
                             f"<span style='color:#9A9A9A'>{t}</span>")
            self.steps.setText(" &nbsp;·&nbsp; ".join(names))
            self.stack.setCurrentIndex(PAGES.index(self.st.page))
            ok, why = self.st.can_next()
            self.btn_next.setVisible(self.st.page != "test")
            self.btn_next.setEnabled(ok)
            self.btn_back.setEnabled(self.st.can_back() and not self.recorder.busy)
            self.status.setText("" if ok else why)
            pg = self.st.page
            if pg == "phrase":
                self._phrase_changed(self.ed_phrase.text())
            elif pg == "record":
                tag, cue = self.st.cue()
                self.lb_cue.setText(f"Say \"{self.st.phrase}\" {cue}.")
                n, good = len(self.st.all_clips()), len(self.st.good_clips())
                self.lb_count.setText(f"{good} good clip{'s' if good != 1 else ''} of {self.st.target} "
                                      f"({n} recorded)")
                self.btn_rec.setEnabled(not self.recorder.busy)
                self.btn_redo.setEnabled(bool(self.st.clips) and not self.recorder.busy)
            elif pg == "train":
                running = self.st.train_status == "running"
                self.btn_train.setEnabled(not running)
                self._plan_text()
                self.btn_train.setText("Train again" if self.st.train_status in ("done", "failed", "cancelled")
                                       else "Train")
                self.btn_cancel.setEnabled(running)
            elif pg == "test":
                self._result_text()
            self.btn_close.setEnabled(self.st.train_status != "running")

        def _next(self):
            try:
                pg = self.st.next()
            except ValueError as e:
                self.status.setText(str(e))
                return
            if pg == "record":
                folder, prefix = self.st.folder()
                self.st.existing = list_clips(folder, prefix)
                for p in self.st.existing:
                    self.lst.addItem(f"{p.name}  (recorded earlier)")
            self._refresh()

        def _back(self):
            try:
                self.st.back()
            except ValueError:
                return
            self._stop_test()
            self._refresh()

        def _phrase_changed(self, text):
            errors, warns = self.st.set_phrase(text)
            folder, prefix = self.st.folder()
            have = len(list_clips(folder, prefix))
            msg = errors + warns + ([f"{have} clips of this phrase are already recorded; new ones are added."]
                                    if have else [])
            self.lb_phrase.setText("\n".join(msg))
            ok, _ = self.st.can_next()
            self.btn_next.setEnabled(ok)

        # -- recording ---------------------------------------------------------
        def _pause(self):
            if not self._paused and hooks is not None:
                hooks.pause()
                self._paused = True

        def _resume(self):
            if self._paused and hooks is not None:
                hooks.resume()
            self._paused = False

        def _record(self):
            folder, prefix = self.st.folder()
            idx = next_index(folder, prefix)          # clips are on disk as soon as they're saved
            tag, _ = self.st.cue()
            self._pause()
            self.status.setText("Listening… say it now.")
            self.recorder.record(clip_path(folder, idx, tag, prefix))
            self._refresh()
            self.status.setText("Listening… say it now.")

        def _clip_done(self, path, problems):
            self.st.add_clip(Path(path), problems)
            self.lst.addItem(f"{Path(path).name}  " + ("ok" if not problems else "⚠ " + "; ".join(problems)))
            self.lst.scrollToBottom()
            self.status.setText("Saved." if not problems else "Saved, but: " + "; ".join(problems) +
                                " (Redo to record it again).")
            self._refresh()

        def _clip_fail(self, msg):
            self.status.setText(msg.capitalize() + ". Press Record to try again.")
            self._refresh()

        def _redo(self):
            p = self.st.redo_last()
            if p is not None:
                for i in range(self.lst.count() - 1, -1, -1):
                    if self.lst.item(i).text().startswith(p.name):
                        self.lst.takeItem(i)
                        break
            self._refresh()

        def _room(self):
            self._pause()
            self.btn_room.setEnabled(False)
            self.btn_room_skip.setEnabled(False)
            self._room_stop = threading.Event()
            self._room_t0 = time.time()
            self.lb_room.setText("Recording… 60 s left")
            timer = QtCore.QTimer(self, interval=500)
            timer.timeout.connect(lambda: self.lb_room.setText(
                f"Recording… {max(0, 60 - int(time.time() - self._room_t0))} s left")
                if self._room_stop is not None else timer.stop())
            timer.start()

            def run():
                try:
                    pcm = record_seconds(open_mic, 60, on_level=self.b.level.emit, stop=self._room_stop)
                    path = ROOM_DIR / time.strftime("room_%Y%m%d-%H%M%S.wav")
                    write_wav(path, pcm)
                    self.b.room.emit(str(path))
                except Exception as e:
                    self.b.room.emit("error:" + str(e))
            threading.Thread(target=run, daemon=True).start()

        def _room_done(self, path):
            self._room_stop = None
            self.btn_room.setEnabled(True)
            self.btn_room_skip.setEnabled(True)
            if path.startswith("error:"):
                self.lb_room.setText("Couldn't record: " + path[6:])
                return
            self.st.set_room(Path(path))
            self.lb_room.setText(f"Saved {Path(path).name}. Next, or record it again.")
            self._refresh()

        # -- training --------------------------------------------------------------
        def _plan_text(self):
            from .wake_train import download as D
            from .wake_train.pipeline import SIZES
            n = SIZES["personal"]["acav"]
            need = D.plan_bytes(n) - D.have_bytes(STATE_DIR / "wake_train" / "features", n)
            good = len(self.st.good_clips())
            fw = len(list(FALSE_WAKES.glob("*.wav"))) if FALSE_WAKES.is_dir() else 0
            dl = (f"First it downloads openWakeWord's negative features: <b>{D.fmt_mb(need)}</b> "
                  f"(once; it resumes if interrupted; ~{need / 2 ** 20 / 0.7 / 60:.0f} min at 0.7 MB/s). "
                  if need > 0 else "The negative features are already downloaded. ")
            missing = missing_requirements(cfg.get("tts", {}))
            self.btn_train.setEnabled(not missing and self.st.train_status != "running")
            if missing:
                self.lb_plan.setText("<b>Can't train yet.</b> Missing: " + "; ".join(missing))
                return
            gu = hooks.gpu_util() if hooks is not None else None
            gpu = ("The GPU is busy, so it runs on the CPU (slower). " if gu is not None and gu >= 70 else
                   "It uses the GPU if it's free (under 70% busy), otherwise the CPU. ")
            self.lb_plan.setText(
                f"Trains \"{self.st.phrase}\" from your {good} clips"
                + (", your room recording" if self.st.room else "")
                + (f", {fw} \"that wasn't me\" clips" if fw and self.st.phrase.lower() == DEFAULT_PHRASE else "")
                + ", ~2,300 synthetic voices and look-alike phrases. " + dl + gpu
                + "Usually 10-15 minutes after the download. You can keep using the PC.")

        def _train(self):
            self.st.train_started()
            self.train_log.clear()
            args = self.st.train_args()
            gu = hooks.gpu_util() if hooks is not None else None
            if gu is not None:
                args += ["--gpu-util", f"{gu:.0f}"]
            self._resume()                     # recording is over: listen again while it trains
            self.runner = TrainRunner(args, on_progress=self.b.prog.emit, on_log=self.b.log.emit,
                                      on_done=lambda m, r: self.b.done.emit(str(m), r), on_fail=self.b.tfail.emit)
            try:
                self.runner.start()
            except Exception as e:
                self._train_fail(f"couldn't start the trainer: {e}")
                return
            self.bar.setValue(0)
            self.lb_train.setText("Starting…")
            self._refresh()

        def _train_prog(self, d):
            self.st.train_progress(d)
            self.bar.setValue(int(d.get("overall", 0) * 1000))
            self.lb_train.setText(f"{d.get('msg', '')}\nAbout {fmt_eta(d.get('eta_s', 0))} left.")

        def _train_done(self, model, report):
            self.st.train_finished(Path(model), report)
            self.bar.setValue(1000)
            self.lb_train.setText(f"Done in {fmt_eta(report.get('seconds', 0))}.")
            self._refresh()
            self._next()

        def _train_fail(self, msg):
            self.st.train_failed(msg)
            self.lb_train.setText("Cancelled." if msg == "cancelled" else f"Training failed: {msg}")
            self._refresh()

        def _cancel_train(self):
            if self.runner is not None:
                self.runner.cancel()

        # -- test and apply ------------------------------------------------------------
        def _result_text(self):
            r = self.st.report
            a = r.get("at_0_5", {})
            hit = a.get("hit", {}).get("yours", {})
            lines = [f"Model: {self.st.model}"]
            if hit:
                lines.append(f"Hit rate on your {hit['n']} held-out clips (never trained on): "
                             f"<b>{hit['hit_at_0_5'] * 100:.0f}%</b> (target ≥ 95%).")
            if "fph" in a:
                lines.append(f"False wakes on {r.get('validation_hours', 0):.1f} h of other people talking and "
                             f"music: <b>{a['false_wakes']}</b> ({a['fph']:.2f} per hour; target ≤ 0.5).")
            lines.append("Test it below, then <b>Use this model</b>. The previous one is kept, so you can undo.")
            self.lb_result.setText("<br>".join(lines))
            self.btn_apply.setEnabled(self.st.model is not None and not self.st.applied)
            self.btn_undo.setEnabled(bool(load_history()))

        def _test(self):
            if self.tester is not None:
                self._stop_test()
                return
            self._pause()
            self.tester = LiveTester(self.st.model, open_mic, on_score=self.b.score.emit, on_hit=self.b.hit.emit)
            self.tester.start()
            self.test_hits.setText("listening…")
            self.btn_test.setText("Stop testing")

        def _stop_test(self):
            if self.tester is not None:
                self.tester.stop()
                self.tester = None
                self.btn_test.setText("Test: say it a few times")
            self._resume()

        def _apply(self):
            self._stop_test()
            if on_apply is None or self.st.model is None:
                return
            msg = on_apply(self.st.model)
            self.st.applied = True
            self._refresh()
            self.status.setText(msg or "Applied: hey-claude listens with the new model now.")

        def _undo(self):
            if on_undo is not None:
                msg = on_undo()
                self.st.applied = False
                self._refresh()
                self.status.setText(msg or "")
                return
            self._refresh()

        def closeEvent(self, e):
            if self.st.train_status == "running":
                e.ignore()
                self.status.setText("Training is running: cancel it first.")
                return
            self.recorder.cancel()
            if self._room_stop is not None:
                self._room_stop.set()
            self._stop_test()
            self._resume()
            e.accept()

    return WakeWizard()
