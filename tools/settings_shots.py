"""Render every Settings page to PNG (docs/settings-redesign/).

    python tools/settings_shots.py OUT_DIR [--scale 1.5] [--width 980] [--full]

Uses the real Windows platform with the window kept off screen (so fonts and
DPI handling match the app); QT_SCALE_FACTOR gives the 125 / 150 / 200 % views.
--full makes each shot as tall as its page, so nothing is hidden below the fold.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--width", type=int, default=0)
    ap.add_argument("--height", type=int, default=0)
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--prefix", default="")
    a = ap.parse_args()
    if a.scale != 1.0:
        os.environ["QT_SCALE_FACTOR"] = str(a.scale)
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QScrollArea
    app = QApplication.instance() or QApplication([])
    from heyclaude import config
    from heyclaude.settings_ui import SettingsWindow
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    cfg = config.defaults()
    cfg["projects"] = {"hey claude": str(ROOT), "website": "D:/sites/portfolio"}
    with tempfile.TemporaryDirectory() as d:
        w = SettingsWindow(cfg, config_path=Path(d) / "config.yaml", state_dir=d)
        w.setAttribute(Qt.WA_DontShowOnScreen, True)
        try:                                         # no page cross-fade caught mid-way in a shot
            from heyclaude import settings_theme
            settings_theme._MOTION["on"] = False
        except ImportError:
            pass
        base_w = a.width or w.width()
        base_h = a.height or w.height()
        w.resize(base_w, base_h)
        w.show()
        app.processEvents()
        tabs = w.tabs
        for i in range(tabs.count()):
            tabs.setCurrentIndex(i)
            w.resize(base_w, base_h)
            for _ in range(5):
                app.processEvents()
            h = base_h
            if a.full:
                page = tabs.widget(i)
                sa = page if isinstance(page, QScrollArea) else page.findChild(QScrollArea)
                if sa is not None and sa.widget() is not None:
                    inner = sa.widget()
                    extra = inner.sizeHint().height() - sa.viewport().height()
                    if extra > 0:
                        h = base_h + extra + 4
                w.resize(base_w, h)
                for _ in range(5):
                    app.processEvents()
            name = re.sub(r"[^a-z0-9]+", "-", tabs.tabText(i).lower()).strip("-")
            path = out / f"{a.prefix}{i + 1:02d}-{name}.png"
            w.grab().save(str(path))
            print(path)
            ct = getattr(w, "char_tab", None)
            if name == "character" and ct is not None:          # the same page with a character picked
                for j in range(ct.gallery.count()):
                    if ct.gallery.item(j).data(Qt.UserRole) == "wren":
                        ct.gallery.setCurrentRow(j)
                for _ in range(10):
                    app.processEvents()
                if a.full:
                    sa = tabs.widget(i)
                    extra = sa.widget().sizeHint().height() - sa.viewport().height()
                    if extra > 0:
                        w.resize(base_w, w.height() + extra + 4)
                    for _ in range(5):
                        app.processEvents()
                path = out / f"{a.prefix}{i + 1:02d}-{name}-picked.png"
                w.grab().save(str(path))
                print(path)
                ct.gallery.setCurrentRow(0)
        try:                                                     # the wake-word tune-up dialog, first step
            from heyclaude.wake_wizard import make_wizard
            wz = make_wizard(cfg, None, w)
            wz.setAttribute(Qt.WA_DontShowOnScreen, True)
            wz.show()
            for _ in range(5):
                app.processEvents()
            path = out / f"{a.prefix}{tabs.count() + 1:02d}-wake-word-tune-up.png"
            wz.grab().save(str(path))
            print(path)
            wz.hide()
        except Exception as e:
            print("wizard:", e)
        w._pending_restart = []
        w.has_unsaved_changes = lambda: False
        w.close()


if __name__ == "__main__":
    main()
