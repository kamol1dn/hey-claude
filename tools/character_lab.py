"""The character lab (ROADMAP M6): load a pack, cycle every state, fake the inputs,
play its antics, measure fps, and run the checks CI runs on every pack.

    .venv\\Scripts\\python tools\\character_lab.py                    # the lab window
    .venv\\Scripts\\python tools\\character_lab.py --pack echo        # ...starting on one pack
    .venv\\Scripts\\python tools\\character_lab.py --check            # the CI checks, every pack
    .venv\\Scripts\\python tools\\character_lab.py --check --pack D:/my-pack   # a pack folder anywhere
    .venv\\Scripts\\python tools\\character_lab.py --shots DIR        # a contact sheet per pack
    .venv\\Scripts\\python tools\\character_lab.py --previews         # characters/<id>/preview.png

The checks (tests/test_characters.py runs them too):
  - the manifest schema (heyclaude/characters.validate), the core states present;
  - every implemented state, and every registry state through its fallback, renders
    offscreen without QML errors;
  - idle budget: a resting character averages no more than the registry's idle fps
    (x1.5 for blinks), off / asleep no more than its rest fps;
  - every antic: an interrupt exit (`exit` in the pack's JS), it plays to the end inside
    the antic fps budget and its duration, and an interrupt ends it at once (< 150 ms);
  - props inside the character's bounds (manifest boxes, checked by validate).
Originality (no known mascots or logos) is reviewed by hand in the PR.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if "--check" in sys.argv or "--shots" in sys.argv or "--previews" in sys.argv:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.environ.get("QT_QPA_PLATFORM") == "offscreen" and sys.platform == "win32":
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))

from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, QPoint, QRect, Qt, QTimer, QUrl, Q_ARG, Q_RETURN_ARG  # noqa: E402
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter  # noqa: E402
from PySide6.QtQuick import QQuickView, QQuickWindow, QSGRendererInterface  # noqa: E402

from heyclaude import characters as CH  # noqa: E402
from heyclaude import states as S  # noqa: E402

STAGE = ROOT / "tools" / "character_lab.qml"
SHOW_STATES = ["idle", "listening", "thinking", "talking", "happy", "confused", "working", "sending", "off",
               "drowsy", "asleep", "waking"]


def _v(x):
    return x.toVariant() if hasattr(x, "toVariant") else x


def spin(ms: int):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


class Lab:
    """One offscreen (or on-screen) stage with a CharacterHost, driven by a manual clock."""

    def __init__(self, software: bool = True, clear: bool = False, size=(420, 340)):
        if QGuiApplication.instance() is None:
            if software:
                QQuickWindow.setGraphicsApi(QSGRendererInterface.Software)
            QQuickWindow.setDefaultAlphaBuffer(True)
            from PySide6.QtWidgets import QApplication
            self.app = QApplication(sys.argv[:1])      # widgets too (tests, Settings)
        self.warnings: list[str] = []
        self.view = QQuickView()
        self.view.setColor(QColor(0, 0, 0, 0) if clear else QColor("#080808"))
        eng = self.view.engine()
        eng.addImportPath(str(CH.KIT_IMPORT_DIR))
        eng.warnings.connect(lambda ws: self.warnings.extend(w.toString() for w in ws))
        self.view.setResizeMode(QQuickView.SizeRootObjectToView)
        self.view.setSource(QUrl.fromLocalFile(str(STAGE)))
        if self.view.status() == QQuickView.Error:
            raise RuntimeError("; ".join(e.toString() for e in self.view.errors()))
        self.root = self.view.rootObject()
        self.root.setProperty("clear", clear)
        from PySide6.QtQuick import QQuickItem
        self.host = self.root.findChild(QQuickItem, "host")
        self.view.resize(*size)
        self.view.show()
        self.man: dict = {}
        self.reg = S.load()
        self.budgets = self.reg.get("budgets", {})

    def call(self, fn, *args):
        ret = QMetaObject.invokeMethod(self.host, fn, Qt.DirectConnection, Q_RETURN_ARG("QVariant"),
                                       *[Q_ARG("QVariant", a) for a in args])
        return _v(ret)

    def load(self, man: dict, small: bool = False, size: float | None = None, manual: bool = True):
        self.man = man
        h = self.host
        h.setProperty("source", QUrl())
        h.setProperty("manifest", {k: v for k, v in man.items() if k in ("bounds", "desktop_bounds", "oneshot")})
        h.setProperty("small", small)
        h.setProperty("size", size or (man.get("size", {}).get("pill" if small else "desktop") or (24 if small else 120)))
        h.setProperty("budgets", self.budgets)
        h.setProperty("manualClock", manual)
        h.setProperty("stateId", "idle")
        h.setProperty("source", QUrl.fromLocalFile(man["qml"]))
        spin(30)
        return bool(h.property("ready"))

    def state(self, st: str):
        self.host.setProperty("stateId", CH.resolve(self.man, st, log=None) if self.man else st)

    def inputs(self, level=0.0, amplitude=0.0):
        self.host.setProperty("level", level)
        self.host.setProperty("amplitude", amplitude)

    def run(self, seconds: float, at_fps: float | None = None, fake_audio: bool = False) -> tuple[int, float]:
        """Advance the manual clock for `seconds`. Each step is as long as the frame rate the
        character asks for (or 1/at_fps); at 0 fps the clock jumps 0.5 s and nothing is drawn.
        Returns (frames drawn, seconds)."""
        t, frames = 0.0, 0
        fps = float(self.call("advance", 0.0) or 0)
        while t < seconds:
            f = at_fps or fps
            dt = 1.0 / f if f > 0 else 0.5
            if fake_audio:
                u = t * 5.0
                self.inputs(level=abs(math.sin(u * 2.3)) * 0.8, amplitude=abs(math.sin(u * 3.1 + 1)) * 0.9)
            fps = float(self.call("advance", dt) or 0)
            t += dt
            if f > 0:
                frames += 1
                if frames % 3 == 0:            # render now: the draw code really runs and its errors surface
                    self.view.grabWindow()
        return frames, t

    def grab(self, size_px: int | None = None) -> QImage:
        spin(20)
        img = self.view.grabWindow()
        info = self.call("info")
        if not info or not info.get("ready"):
            return img
        dpr = img.devicePixelRatio() or 1
        w, h = info["w"] * dpr, info["h"] * dpr
        b = info["bounds"]
        cx, cy = self.host.property("x") * dpr, self.host.property("y") * dpr
        sz = self.host.property("size") * dpr
        x0 = cx + b[0] / 100 * sz
        y0 = cy + b[1] / 100 * sz
        return img.copy(QRect(int(x0), int(y0), int(math.ceil(w)), int(math.ceil(h))))


# ------------------------------------------------------------------ checks
def check_pack(lab: Lab, man: dict, builtin: bool = True) -> list[str]:
    out = [f"manifest: {p}" for p in CH.validate(man, Path(man["dir"]))]
    lab.warnings.clear()
    if not lab.load(man, small=True, size=24):
        return out + ["Character.qml didn't load: " + "; ".join(lab.warnings[-3:])]
    if lab.warnings:
        out.append("QML warnings on load: " + "; ".join(lab.warnings[:3]))
    B = lab.budgets
    for small in (True, False):
        lab.load(man, small=small)
        lab.warnings.clear()
        for st in man["implements"]:
            lab.state(st)
            lab.inputs(0.5, 0.5)
            lab.run(0.6, at_fps=60)
        for st in (s["id"] for s in lab.reg["states"]):           # everything else through fallbacks
            lab.state(st)
            lab.run(0.1, at_fps=60)
        lab.inputs(0, 0)
        if lab.warnings:
            out.append(f"QML warnings while cycling states ({'pill' if small else 'desktop'}): " + "; ".join(sorted(set(lab.warnings))[:3]))
        if int(lab.call("info").get("frames", 0)) <= 0:
            out.append("nothing was drawn")
    # idle budget (pill)
    lab.load(man, small=True)
    caps = {"idle": B.get("idle_fps", 8) * 1.5, "drowsy": B.get("idle_fps", 8) * 1.5,
            "off": B.get("rest_fps", 4) * 1.5 + 1, "asleep": B.get("rest_fps", 4) * 1.5 + 1}
    for st in ("idle", "off", "asleep", "drowsy"):
        shown = CH.resolve(man, st, log=None)
        cap = caps.get(shown, caps["idle"])
        lab.state(st)
        lab.run(2.0)                                     # let the entry settle
        n, secs = lab.run(20.0)
        avg = n / secs
        if avg > cap:
            out.append(f"{st}: {avg:.1f} fps on average at rest, over the {cap:g} budget")
    # antics
    ids = [a["id"] for a in man.get("antics", [])]
    have = set(lab.call("anticIds") or [])
    if builtin and not 10 <= len(ids):
        out.append(f"{len(ids)} antics; a built-in pack has 10-15")
    for a in man.get("antics", []):
        aid = a["id"]
        if aid not in have:
            out.append(f"antic {aid}: in character.json but not in the pack's JS")
            continue
        if not lab.call("anticExit", aid):
            out.append(f"antic {aid}: no interrupt exit (exit function)")
        for small in ([True] if "pill" in a.get("where", CH.WHERE) else []) + ([False] if "desktop" in a.get("where", CH.WHERE) else []):
            lab.load(man, small=small)
            lab.warnings.clear()
            lab.state("idle")
            lab.run(1.5)
            if not lab.call("playAntic", aid, float(a["duration"])):
                out.append(f"antic {aid}: didn't start")
                continue
            n, secs = lab.run(float(a["duration"]) + 0.5)
            if lab.host.property("antic"):
                out.append(f"antic {aid}: still playing after its duration")
            fps = n / secs
            if fps > B.get("antic_fps", 30) + 0.5:
                out.append(f"antic {aid}: {fps:.0f} fps, over the antic budget")
            # interrupt half-way
            lab.call("playAntic", aid, float(a["duration"]))
            lab.run(float(a["duration"]) / 2)
            t0 = time.perf_counter()
            lab.call("interrupt")
            lab.call("advance", 1 / 60)
            ms = (time.perf_counter() - t0) * 1000
            if lab.host.property("antic"):
                out.append(f"antic {aid}: an interrupt didn't end it")
            if ms > B.get("interrupt_ms", 150):
                out.append(f"antic {aid}: interrupt took {ms:.0f} ms")
            lab.run(0.5, at_fps=60)
            if lab.warnings:
                out.append(f"antic {aid}: QML warnings: " + "; ".join(sorted(set(lab.warnings))[:2]))
    for extra in sorted(have - set(ids)):
        out.append(f"antic {extra}: in the JS but not declared in character.json")
    return out


def packs_from(arg: str | None) -> list[dict]:
    if arg and (os.sep in arg or "/" in arg) and Path(arg).is_dir():
        return [CH.load_manifest(Path(arg).resolve())]
    reg = CH.registry(refresh=True)
    if arg:
        if arg not in reg:
            # a pack that fails validation still deserves a report
            folder = CH.BUILTIN_DIR / arg
            if (folder / "character.json").exists():
                return [CH.load_manifest(folder)]
            raise SystemExit(f"no pack {arg!r} (installed: {', '.join(reg) or 'none'})")
        return [reg[arg]]
    return list(reg.values())


# ------------------------------------------------------------------ sheets
def contact_sheet(lab: Lab, man: dict, path: Path):
    """States at desktop size, then the same at pill size (true size and 3x), then antics."""
    tiles: list[tuple[str, QImage]] = []
    lab.load(man, small=False)
    for st in SHOW_STATES:
        lab.state(st)
        lab.inputs(0.6 if st == "listening" else 0, 0.7 if st == "talking" else 0)
        lab.run(0.25 if st in ("happy", "sending", "waking", "confused") else 0.9, at_fps=60, fake_audio=st in ("listening", "talking"))
        tiles.append((st + ("" if CH.resolve(man, st, log=None) == st else " *"), lab.grab()))
    lab.inputs(0, 0)
    pill: list[tuple[str, QImage]] = []
    lab.load(man, small=True, size=72)             # the island drawing (small) at 3x, rendered, not upscaled
    for st in SHOW_STATES:
        lab.state(st)
        lab.run(0.25 if st in ("happy", "sending", "waking", "confused") else 0.9, at_fps=60, fake_audio=st in ("listening", "talking"))
        pill.append((st, lab.grab()))
    antic_tiles: list[tuple[str, QImage]] = []
    for a in man.get("antics", []):
        small = "desktop" not in a.get("where", CH.WHERE)
        lab.load(man, small=small)
        lab.state("idle")
        lab.run(1.3)
        lab.call("playAntic", a["id"], float(a["duration"]))
        for frac in (0.25, 0.6):
            lab.run(float(a["duration"]) * (frac - (0.25 if frac == 0.6 else 0)), at_fps=30)
            antic_tiles.append((f"{a['id']} {int(frac * 100)}%", lab.grab()))
        lab.call("interrupt")
        lab.call("advance", 0.06)
        antic_tiles.append((f"{a['id']} caught", lab.grab()))
    cell, pad, lab_h = 170, 8, 16
    cols = 6
    rows_states = math.ceil(len(tiles) / cols)
    rows_pill = math.ceil(len(pill) / cols)
    rows_ant = math.ceil(len(antic_tiles) / cols)
    H = 40 + (rows_states + rows_pill + rows_ant) * (cell + lab_h + pad) + 3 * 24
    W = cols * (cell + pad) + pad
    sheet = QImage(W, H, QImage.Format_ARGB32)
    sheet.fill(QColor("#141210"))
    p = QPainter(sheet)
    p.setRenderHint(QPainter.SmoothPixmapTransform, False)
    p.setPen(QColor("#F2F2F2"))
    f = QFont("Segoe UI", 12)
    f.setBold(True)
    p.setFont(f)
    p.drawText(pad, 26, f"{man['name']} · {man.get('kind', '')}")
    p.setFont(QFont("Segoe UI", 8))
    y = 40

    def block(title, items, y):
        p.setPen(QColor("#9A9A9A"))
        p.drawText(pad, y + 14, title)
        y += 24
        for i, (name, img) in enumerate(items):
            cx = pad + (i % cols) * (cell + pad)
            cy = y + (i // cols) * (cell + lab_h + pad)
            p.fillRect(cx, cy, cell, cell, QColor("#080808"))
            sc = img.scaled(cell, cell, Qt.KeepAspectRatio, Qt.FastTransformation if img.width() < 60 else Qt.SmoothTransformation)
            p.drawImage(cx + (cell - sc.width()) // 2, cy + (cell - sc.height()) // 2, sc)
            p.setPen(QColor("#C8C2BC"))
            p.drawText(cx + 2, cy + cell + 12, name)
        return y + math.ceil(len(items) / cols) * (cell + lab_h + pad)

    y = block("desktop companion (120 px)   * = through a fallback", tiles, y)
    y = block("in the island (24 px, drawn at 3x)", pill, y)
    block(f"idle antics ({len(man.get('antics', []))})", antic_tiles, y)
    p.end()
    sheet.save(str(path))
    return path


def write_preview(lab: Lab, man: dict):
    lab.load(man, small=False, size=96)
    lab.state("idle")
    lab.run(1.4, at_fps=30)
    img = lab.grab()
    img.save(str(Path(man["dir"]) / "preview.png"))


# ------------------------------------------------------------------ the lab window
def lab_window(start: str | None):
    from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QGridLayout, QHBoxLayout, QLabel,
                                   QPushButton, QSlider, QVBoxLayout, QWidget)
    app = QApplication(sys.argv[:1])
    lab = Lab(software=False)
    reg = CH.registry(refresh=True)
    if not reg:
        raise SystemExit("no character packs installed")
    w = QWidget()
    w.setWindowTitle("hey-claude character lab")
    lay = QVBoxLayout(w)
    top = QHBoxLayout()
    pick = QComboBox()
    for pid, m in reg.items():
        pick.addItem(m["name"], pid)
    where = QComboBox()
    where.addItems(["desktop (120 px)", "in the island (24 px)"])
    calm = QCheckBox("reduced motion")
    fake = QCheckBox("fake mic / voice")
    fake.setChecked(True)
    top.addWidget(pick)
    top.addWidget(where)
    top.addWidget(calm)
    top.addWidget(fake)
    lay.addLayout(top)
    states_box = QGridLayout()
    lay.addLayout(states_box)
    antics_box = QGridLayout()
    lay.addWidget(QLabel("Antics"))
    lay.addLayout(antics_box)
    scale = QSlider(Qt.Horizontal)
    scale.setRange(10, 40)
    scale.setValue(10)
    lay.addWidget(QLabel("pixel scale (island scale)"))
    lay.addWidget(scale)
    fps_lbl = QLabel("")
    lay.addWidget(fps_lbl)
    cur = {"man": None, "t": 0.0, "last": 0, "lastT": time.time()}

    def clear(grid):
        while grid.count():
            it = grid.takeAt(0)
            if it.widget():
                it.widget().deleteLater()

    def load():
        man = reg[pick.currentData()]
        cur["man"] = man
        small = where.currentIndex() == 1
        lab.load(man, small=small, manual=False)
        lab.host.setProperty("reducedMotion", calm.isChecked())
        lab.host.setProperty("pixelScale", scale.value() / 10 * (4 if small else 1))
        clear(states_box)
        for i, s in enumerate(s["id"] for s in lab.reg["states"]):
            b = QPushButton(s + ("" if s in man["implements"] else " →" + CH.resolve(man, s, log=None)))
            b.clicked.connect(lambda _=False, s=s: lab.state(s))
            states_box.addWidget(b, i // 6, i % 6)
        clear(antics_box)
        for i, a in enumerate(man.get("antics", [])):
            b = QPushButton(a["name"])
            b.setToolTip(f"{a['id']} · {a['duration']} s · weight {a.get('weight', 1)}")
            b.clicked.connect(lambda _=False, a=a: lab.call("playAntic", a["id"], float(a["duration"])))
            antics_box.addWidget(b, i // 4, i % 4)
        stop = QPushButton("interrupt (caught!)")
        stop.clicked.connect(lambda: lab.call("interrupt"))
        antics_box.addWidget(stop, 99, 0)

    pick.currentIndexChanged.connect(load)
    where.currentIndexChanged.connect(load)
    calm.toggled.connect(lambda v: lab.host.setProperty("reducedMotion", v))
    scale.valueChanged.connect(lambda v: lab.host.setProperty("pixelScale", v / 10 * (4 if where.currentIndex() == 1 else 1)))
    if start:
        i = pick.findData(start)
        if i >= 0:
            pick.setCurrentIndex(i)
    load()

    def tick():
        cur["t"] += 0.05
        if fake.isChecked():
            u = cur["t"] * 5
            lab.inputs(abs(math.sin(u * 2.3)) * 0.8, abs(math.sin(u * 3.1 + 1)) * 0.9)
        info = lab.call("info") or {}
        now = time.time()
        if now - cur["lastT"] >= 1.0:
            fr = int(info.get("frames", 0))
            fps_lbl.setText(f"state {info.get('state')} · antic {info.get('antic') or '-'} · asks {info.get('fps', 0):.0f} fps · "
                            f"drew {(fr - cur['last']) / (now - cur['lastT']):.1f} fps")
            cur["last"], cur["lastT"] = fr, now
    t = QTimer(interval=50, timeout=tick)
    t.start()
    w.resize(760, 420)
    w.show()
    lab.view.setTitle("character")
    app.exec()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pack", help="a pack id, or a pack folder")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--shots", metavar="DIR")
    ap.add_argument("--previews", action="store_true")
    a = ap.parse_args()
    if a.check or a.shots or a.previews:
        lab = Lab(clear=a.previews)
        bad = 0
        for man in packs_from(a.pack):
            if a.check:
                probs = check_pack(lab, man, builtin=man.get("builtin", False))
                print(f"{man['id']}: {'ok' if not probs else str(len(probs)) + ' problem(s)'}")
                for pr in probs:
                    print("   -", pr)
                bad += bool(probs)
            if a.shots:
                Path(a.shots).mkdir(parents=True, exist_ok=True)
                print("wrote", contact_sheet(lab, man, Path(a.shots) / f"{man['id']}.png"))
            if a.previews:
                write_preview(lab, man)
                print("preview", man["id"])
        sys.stdout.flush()
        os._exit(1 if bad else 0)
    lab_window(a.pack)


if __name__ == "__main__":
    main()
