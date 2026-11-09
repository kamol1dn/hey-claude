"""The island motion lab (docs/island-motion.md section 12): the real QML island
driven by fake events, with an fps readout. It never touches a running
hey-claude: no single-instance guard, no mic, and the island window sits lower
on the screen unless --top.

    .venv\\Scripts\\python tools\\island_lab.py                 # the lab window
    .venv\\Scripts\\python tools\\island_lab.py --software      # the software (no GPU) path
    .venv\\Scripts\\python tools\\island_lab.py --shots DIR     # a png of every state and widget, then exit
    .venv\\Scripts\\python tools\\island_lab.py --measure       # the acceptance measurements (section 11)

--measure prints a table and writes state/island-measure.json: frames per
second in each rest and live state (QQuickWindow.frameSwapped), frame-time p99
in transitions, UI-thread CPU, the GPU 3D load of this process (PDH counters,
as Task Manager shows), wake latency, a 120 s state storm, and the focus check.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QElapsedTimer, QEventLoop, QObject, Qt, QTimer  # noqa: E402
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QGridLayout, QGroupBox, QHBoxLayout,  # noqa: E402
                               QLabel, QPushButton, QRadioButton, QSlider, QVBoxLayout, QWidget)

ROOT = Path(__file__).resolve().parent.parent

ANSWERS = ["You have two meetings tomorrow: design review at ten, and a one-on-one with Sam at three.",
           "Done. Standup now starts at ten, and I told the team.",
           "About four minutes. I'll ping you when it's green."]
QUESTIONS = ["what's on my calendar tomorrow?", "move standup to ten", "how long until the build finishes?"]
EFFECTS = [
    ("mcp__windows__set_volume", {}, {"old": 45, "new": 30}, True),
    ("mcp__windows__set_brightness", {}, {"old": 40, "new": 80}, True),
    ("mcp__windows__set_mute", {}, {"old": False, "new": True}, True),
    ("mcp__windows__set_theme", {}, {"old": "light", "new": "dark"}, True),
    ("mcp__windows__set_wifi", {}, {"old": False, "new": True}, True),
    ("mcp__windows__media_control", {}, {"old": {"title": "Old song", "artist": "A"}, "action": "next",
                                         "new": {"title": "Clair de Lune", "artist": "Debussy", "status": "playing",
                                                 "position_s": 40, "duration_s": 300}}, True),
    ("mcp__windows__request_power_action", {}, {"pending": "sleep"}, False),
    ("mcp__windows__screenshot", {}, {}, False),
    ("mcp__windows__launch_app", {}, {"launched": "Spotify"}, False),
]
TOOLS = ["mcp__claude_ai_Google_Calendar__list_events", "mcp__claude_ai_Gmail__search_threads",
         "mcp__telegram__telegram_list_dialogs", "WebSearch", "mcp__weird-server__doThing"]


def wait(ms: int):
    loop = QEventLoop()
    QTimer.singleShot(int(ms), loop.quit)
    loop.exec()


class Lab(QObject):
    def __init__(self, args):
        super().__init__()
        from heyclaude.island import Bridge
        from heyclaude.island_qml import QmlIsland
        self.args = args
        self.b = Bridge()
        cfg = {"screen": "primary", "top_margin": "auto" if args.top else 8, "motion": args.motion,
               "window_y": 0 if args.top else args.window_y, "hide_when_idle": args.hide_idle}
        self.isl = QmlIsland(self.b, cfg, wake_label="hey claude", software=args.software)
        self.isl.attach_input(lambda: None, lambda: None, lambda t: self.b.state.emit("thinking", t))
        self.isl.files_dropped.connect(lambda p: print("[lab] dropped", p))
        self.isl.widget_action.connect(lambda w, a, p: print("[lab] widget", w, a, p))
        self.isl.approval_decided.connect(lambda i, a: print("[lab] approval", i, a))
        self.isl.stop_speaking.connect(lambda: self.state("idle"))
        self.isl.interrupt_talk.connect(lambda: (self.b.wake_hit.emit(time.time()), self.state("listening")))
        self.isl.activate.connect(lambda: self.state("listening"))
        self.mic_mode, self.tts_mode = "speech", "speech"
        self.mic_val, self.tts_val = 0.45, 0.55
        self.t0 = time.monotonic()
        self.tasks: list[dict] = []
        self.mode = "idle"
        self.badge = "gpu (turbo)"
        self.swapped = 0
        self.isl.view.frameSwapped.connect(self._swapped)
        self.audio = QTimer(self, interval=33, timeout=self._audio)
        self.b.stt.emit(self.badge)
        self.b.state.emit("loading", "loading models…")
        QTimer.singleShot(1200, lambda: self.state("idle"))

    def _swapped(self):
        self.swapped += 1

    def state(self, mode, text=""):
        self.mode = mode
        live = mode in ("listening", "holding", "followup", "speaking")
        if live != self.audio.isActive():
            self.audio.start() if live else self.audio.stop()   # a fake mic / voice only while one is live
        if mode == "thinking" and not text:
            text = random.choice(QUESTIONS)
        if mode == "speaking" and not text:
            text = random.choice(ANSWERS)
        self.b.state.emit(mode, text)

    def wake(self):
        self.audio.start()
        self.b.wake_hit.emit(time.time())
        self.state("listening")

    def _audio(self):
        t = time.monotonic() - self.t0
        if self.mode in ("listening", "holding", "followup") or self.isl.input_open:
            if self.mic_mode == "manual":
                v = self.mic_val
            elif self.mic_mode == "speech":
                v = max(0.0, 0.5 * math.sin(t * 2.2) + 0.25 * math.sin(t * 9.1)) * (0.6 + 0.4 * random.random())
            else:
                v = random.random() * 0.02
            self.b.level.emit(v)
        if self.mode == "speaking":
            if self.tts_mode == "manual":
                a = self.tts_val
            else:
                a = max(0.0, math.sin(t * 7.0)) ** 0.8 * (0.5 + 0.5 * random.random()) * (0.3 if (t % 2.2) > 1.9 else 1)
            self.b.tts_level.emit(a)

    def add_task(self):
        n = len(self.tasks) + 1
        self.tasks.append({"id": f"t{n}", "project": "hey-claude", "prompt": ["refactor audio loop", "write wake tests",
                           "tidy the roadmap", "bump PySide6"][n % 4], "status": "running", "activity": "editing", "elapsed": "0:12"})
        self.b.tasks.emit([dict(t) for t in self.tasks])

    def task_done(self):
        for t in self.tasks:
            if t["status"] == "running":
                t["status"], t["summary"] = "done", "all green"
                break
        self.b.tasks.emit([dict(t) for t in self.tasks])

    def swap_badge(self):
        self.badge = "cpu (gpu busy 83%)" if self.badge.startswith("gpu") else "gpu (turbo)"
        self.b.stt.emit(self.badge)

    def effect(self, i):
        from heyclaude import effects as E
        tool, a, res, undo = EFFECTS[i % len(EFFECTS)]
        ev = E.build(tool, a, res, undoable=undo)
        if ev:
            self.b.effect.emit(ev)

    def widget(self, name):
        from heyclaude import widgets as W
        ex = W.load()["x-widgets"][name]["example"]
        if name == "image":
            ex = {"path": str(ROOT / "state" / "lab-sample.png"), "caption": "a local image"}
            _sample_image(ROOT / "state" / "lab-sample.png")
        if name == "telegram_draft":
            ex = dict(ex)
        self.b.widget.emit({"widget": name, "data": ex, "text": name})

    def approval(self):
        self.b.approval.emit({"id": f"ap{random.randint(1, 9999)}", "title": "Run git push in hey-claude?",
                              "detail": "The m5 session wants to push its branch to origin.", "tool": "Bash",
                              "session": "hc-m5 · claude/m5-island-qml"})

    def tools_seq(self):
        for k, name in enumerate(TOOLS):
            QTimer.singleShot(700 * k, lambda n=name, k=k: self.b.tool.emit({"id": f"x{k}", "name": n, "phase": "start"}))
            QTimer.singleShot(700 * k + 650, lambda k=k: self.b.tool.emit({"id": f"x{k}", "phase": "end"}))

    def usage(self):
        self.b.usage.emit({"five_hour": 0.06, "seven_day": 0.16, "status": "allowed"})


def _sample_image(path: Path):
    if path.exists():
        return
    from PySide6.QtGui import QColor, QImage, QPainter, QLinearGradient
    img = QImage(480, 270, QImage.Format_RGB32)
    p = QPainter(img)
    g = QLinearGradient(0, 0, 480, 270)
    g.setColorAt(0, QColor("#21364a"))
    g.setColorAt(1, QColor("#3a2a4a"))
    p.fillRect(img.rect(), g)
    p.setPen(QColor("#D97757"))
    p.drawEllipse(200, 95, 80, 80)
    p.end()
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(path))


# ------------------------------------------------------------------ the panel
class Panel(QWidget):
    def __init__(self, lab: Lab):
        super().__init__()
        self.lab = lab
        self.setWindowTitle("hey-claude island lab")
        lay = QVBoxLayout(self)

        def group(title, items):
            box = QGroupBox(title)
            g = QGridLayout(box)
            for i, (label, fn) in enumerate(items):
                b = QPushButton(label)
                b.clicked.connect(fn)
                g.addWidget(b, i // 5, i % 5)
            lay.addWidget(box)
        L = lab
        group("Rest", [("ready", lambda: L.state("idle")), ("off", lambda: L.state("off", "listening off")),
                       ("paused 40m", lambda: (L.b.listening.emit(False, time.time() + 2400), L.state("off", "paused"))),
                       ("paused 70s", lambda: (L.b.listening.emit(False, time.time() + 70), L.state("off", "paused"))),
                       ("loading", lambda: L.state("loading", "loading whisper (gpu)…"))])
        group("Voice", [("wake", L.wake), ("listening", lambda: L.state("listening")), ("holding", lambda: L.state("holding")),
                        ("holding muted", lambda: L.state("holding", "muted")), ("followup", lambda: L.state("followup", "5")),
                        ("input (hotkey)", L.isl.toggle_input), ("partial", lambda: L.b.partial.emit("so can you check what the weather"))])
        group("Claude", [("thinking", lambda: L.state("thinking")), ("speaking", lambda: L.state("speaking")),
                         ("long answer", lambda: L.state("speaking", " ".join(ANSWERS))),
                         ("tools", L.tools_seq), ("error", lambda: L.state("error", "couldn't reach the model · retrying"))])
        group("One-shots", [("mismatch", lambda: L.b.mismatch.emit()), ("+ task", L.add_task), ("task done", L.task_done),
                            ("badge", L.swap_badge), ("flash", lambda: L.isl.flash("saved: not the wake word"))])
        group("Effects", [(f"effect {i + 1}", lambda i=i: L.effect(i)) for i in range(len(EFFECTS))])
        from heyclaude import widgets as W
        group("Widgets", [(n, lambda n=n: L.widget(n)) for n in W.names()] + [("approval", L.approval), ("usage", L.usage)])
        group("Sequence", [("chaos 5 s", lambda: storm(L, 5)), ("demo", lambda: demo(L))])

        row = QHBoxLayout()
        row.addWidget(QLabel("Motion"))
        grp = QButtonGroup(self)
        for m in ("full", "calm", "off"):
            rb = QRadioButton(m)
            rb.setChecked(m == (L.isl.motion))
            rb.toggled.connect(lambda on, m=m: on and L.isl._send("setMotion", [m]))
            grp.addButton(rb)
            row.addWidget(rb)
        pin = QCheckBox("pin task list")
        pin.toggled.connect(lambda on: L.isl._send("pinList", [on]))
        row.addWidget(pin)
        hide = QCheckBox("hide when idle")
        hide.toggled.connect(lambda on: L.isl._send("setHideIdle", [on]))
        row.addWidget(hide)
        lay.addLayout(row)

        row = QHBoxLayout()
        for name, attr in (("Mic", "mic"), ("TTS", "tts")):
            row.addWidget(QLabel(name))
            for m in (("speech", "silent", "manual") if attr == "mic" else ("speech", "manual")):
                rb = QRadioButton(m)
                rb.setChecked(m == "speech")
                rb.toggled.connect(lambda on, m=m, a=attr: on and setattr(L, a + "_mode", m))
                row.addWidget(rb)
            s = QSlider(Qt.Horizontal)
            s.setRange(0, 100)
            s.setValue(45 if attr == "mic" else 55)
            s.valueChanged.connect(lambda v, a=attr: setattr(L, a + "_val", v / 100))
            row.addWidget(s)
        lay.addLayout(row)
        self.fps = QLabel("-- fps")
        lay.addWidget(self.fps)
        self.last = 0
        QTimer(self, interval=1000, timeout=self._fps).start()

    def _fps(self):
        n, self.last = self.lab.swapped - self.last, self.lab.swapped
        self.last = self.lab.swapped
        self.fps.setText(f"{n} fps   state: {self.lab.isl.engine_state}   gap: {self.lab.isl._last_gap}")


def storm(lab: Lab, seconds: float):
    """Random states every 50-300 ms, with one-shots and overlays."""
    end = time.monotonic() + seconds
    rnd = random.Random(7)
    # (no talk-or-type field here: opening it takes the keyboard focus from whatever you're doing)
    picks = ["idle", "listening", "holding", "thinking", "speaking", "followup", "off", "error", "loading", "wake",
             "mismatch", "paused", "task", "done", "effect", "widget"]

    def step():
        if time.monotonic() > end:
            if lab.isl.input_open:
                lab.isl.session.cancel()
            lab.state("idle")
            return
        p = rnd.choice(picks)
        if p == "wake":
            lab.wake()
        elif p == "mismatch":
            lab.b.mismatch.emit()
        elif p == "paused":
            lab.b.listening.emit(False, time.time() + 600)
            lab.state("off", "paused")
        elif p == "task":
            lab.add_task() if len(lab.tasks) < 3 else None
        elif p == "done":
            lab.task_done()
        elif p == "effect":
            lab.effect(rnd.randrange(len(EFFECTS)))
        elif p == "widget":
            from heyclaude import widgets as W
            lab.widget(rnd.choice(W.names()))
        elif p == "input":
            lab.isl.toggle_input()
        else:
            if lab.isl.input_open and lab.isl.session is not None:
                lab.isl.session.cancel()
            lab.state(p, "" if p not in ("followup", "error", "off") else {"followup": "3", "error": "oops", "off": "listening off"}[p])
        QTimer.singleShot(rnd.randint(50, 300), step)
    step()


def demo(lab: Lab):
    seq = [(0, lambda: lab.state("idle")), (2400, lab.wake), (3400, lambda: lab.state("thinking")),
           (1200, lab.tools_seq), (3600, lambda: lab.state("speaking")), (4500, lambda: lab.state("followup", "5")),
           (5200, lambda: lab.state("idle")), (1800, lab.add_task), (2800, lab.task_done), (4400, lambda: lab.state("off", "listening off")),
           (2800, lambda: lab.state("idle"))]
    t = 0
    for dt, fn in seq:
        t += dt
        QTimer.singleShot(t, fn)


# ------------------------------------------------------------------ shots
def shots(lab: Lab, out: Path):
    out.mkdir(parents=True, exist_ok=True)
    for f in out.glob("*.png"):
        f.unlink()
    n = [0]

    def shot(name, h=None):
        n[0] += 1
        img = lab.isl.grab()
        m = lab.isl._mask
        top = max(0, m[1] - 4)
        hh = h or max(60, min(400 - top, m[3] + 12))
        img.copy(0, top, 600, hh).save(str(out / f"{n[0]:02d}-{name}.png"))

    b = lab.b
    wait(1600)
    lab.state("idle"); wait(1200); shot("ready")
    lab.state("off", "listening off"); wait(900); shot("off")
    b.listening.emit(False, time.time() + 2400); lab.state("off", "paused"); wait(1400); shot("paused")
    lab.state("loading", "loading whisper (gpu)…"); wait(900); shot("loading")
    lab.state("idle"); wait(700)
    lab.wake(); wait(90); shot("woke-90ms"); wait(700); shot("listening")
    b.partial.emit("so can you check what the weather does"); wait(500); shot("listening-partial")
    lab.state("holding"); wait(700); shot("holding")
    lab.state("holding", "muted"); wait(500); shot("holding-muted")
    lab.state("followup", "5"); wait(1200); shot("followup")
    lab.state("thinking", QUESTIONS[0]); wait(800); shot("thinking")
    lab.b.tool.emit({"id": "c1", "name": TOOLS[0], "phase": "start"}); wait(500); shot("thinking-tool")
    lab.b.tool.emit({"id": "c2", "name": TOOLS[2], "phase": "start"}); wait(500); shot("thinking-tools-parallel")
    lab.b.tool.emit({"id": "c1", "phase": "end"}); lab.b.tool.emit({"id": "c2", "phase": "end"})
    lab.state("speaking", ANSWERS[0]); wait(2600); shot("speaking")
    lab.state("speaking", " ".join(ANSWERS)); wait(3500); shot("speaking-long")
    lab.state("error", "couldn't reach the model · retrying"); wait(1100); shot("error")
    lab.state("idle"); wait(600)
    b.mismatch.emit(); wait(500); shot("mismatch")
    wait(1800)
    lab.add_task(); lab.add_task(); wait(1400); shot("tasks-list")
    wait(6000); shot("tasks-satellites")
    lab.task_done(); wait(800); shot("task-done")
    wait(3500)
    lab.isl.flash("saved: not the wake word"); wait(400); shot("flash")
    lab.swap_badge(); wait(3000); shot("badge-cpu")
    lab.swap_badge(); wait(500)
    lab.tasks.clear(); b.tasks.emit([]); wait(800)
    lab.isl.toggle_input(); wait(700); shot("input")
    lab.isl._send("inputText", ["remind me to stretch at four"]); wait(400); shot("input-typed")
    lab.isl.session.cancel(); wait(700)
    for i in range(len(EFFECTS)):
        lab.effect(i); wait(900); shot(f"effect-{EFFECTS[i][0].split('__')[-1]}")
    lab.isl.model.effectShown.emit({"id": "x", "effect": "generic", "ttl_s": 0.1, "label": "", "icon": "gear"}); wait(600)
    from heyclaude import widgets as W
    lab.state("speaking", "Here you go."); wait(300)
    for name in W.names():
        lab.widget(name); wait(1000); shot(f"widget-{name}")
    lab.isl.clear_widget(); wait(600)
    lab.approval(); wait(800); shot("approval")
    for ap in list(lab.isl.approvals):
        lab.isl.withdraw_approval(ap)
    lab.state("idle"); lab.usage(); lab.isl._send("pinList", [True]); wait(900); shot("usage")
    lab.isl._send("pinList", [False]); wait(600)
    print(f"[lab] {n[0]} shots in {out}")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--software", action="store_true")
    ap.add_argument("--top", action="store_true", help="put the island where the real one goes")
    ap.add_argument("--window-y", type=int, default=300)
    ap.add_argument("--motion", default="full")
    ap.add_argument("--hide-idle", action="store_true")
    ap.add_argument("--shots", metavar="DIR")
    ap.add_argument("--measure", action="store_true")
    ap.add_argument("--quick", action="store_true", help="shorter measurement windows")
    a = ap.parse_args(argv)
    app = QApplication(sys.argv)
    lab = Lab(a)
    if a.shots:
        QTimer.singleShot(0, lambda: (shots(lab, Path(a.shots)), app.quit()))
        app.exec()
        return 0
    if a.measure:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from island_measure import run
        QTimer.singleShot(0, lambda: (run(lab, quick=a.quick), app.quit()))
        app.exec()
        return 0
    panel = Panel(lab)
    panel.show()
    panel.move(40, 420)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
