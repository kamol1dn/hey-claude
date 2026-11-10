"""Presented-frame pacing of the island, in a real (on-screen) window (docs/island-motion.md).

    .venv\\Scripts\\python tools\\island_pacing.py                       # speaking in a turn, 10 s
    .venv\\Scripts\\python tools\\island_pacing.py --state listening --seconds 20
    .venv\\Scripts\\python tools\\island_pacing.py --fps native         # island.fps for this run
    .venv\\Scripts\\python tools\\island_pacing.py --state idle --motion calm   # expect no frames

Timestamps every QQuickWindow.frameSwapped on the render thread (a direct connection, right
after the swap returns) while the island holds one state with fake audio, and prints the
interval histogram in vsyncs of this window's screen: on cadence, a live state at
island.fps auto on 144 Hz is all "2". Not meaningful on the offscreen platform (no vsync).
Writes the full result to state/island-pacing.json.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtCore import Qt, QTimer  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import island_lab as IL  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def interval_stats(ts_ns: list[int], hz: float) -> dict:
    d = [(b - a) / 1e6 for a, b in zip(ts_ns, ts_ns[1:])]
    if not d:
        return {"n": 0}
    per = 1000.0 / hz
    hist: dict[int, int] = {}
    for x in d:
        hist[round(x / per)] = hist.get(round(x / per), 0) + 1
    mode = max(hist, key=hist.get)
    q = sorted(d)
    return {"n": len(d), "fps": round(len(d) / ((ts_ns[-1] - ts_ns[0]) / 1e9), 2),
            "mean_ms": round(statistics.mean(d), 3), "sd_ms": round(statistics.pstdev(d), 3),
            "p1": round(q[len(q) // 100], 2), "p50": round(q[len(q) // 2], 2),
            "p99": round(q[min(len(q) - 1, len(q) * 99 // 100)], 2), "max": round(q[-1], 2),
            "vsync_hist": {str(k): v for k, v in sorted(hist.items())},
            "on_cadence": round(hist[mode] / len(d), 4),
            "off": [(i, round(x / per, 2)) for i, x in enumerate(d) if round(x / per) != mode][:200]}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default="speaking", help="speaking, listening, thinking, holding, idle")
    ap.add_argument("--seconds", type=float, default=10)
    ap.add_argument("--fps", default=None, help="island.fps for this run (auto, 30, 60, native)")
    ap.add_argument("--motion", default="full")
    ap.add_argument("--no-turn", action="store_true", help="outside a turn (no question panel)")
    a = ap.parse_args(argv)
    app = QApplication(sys.argv[:1])
    lab = IL.Lab(argparse.Namespace(software=False, top=False, window_y=300, motion=a.motion, hide_idle=False,
                                    scale="1.0", shots=None, measure=False, quick=False))
    if a.fps is not None:
        lab.isl.cfg["fps"] = a.fps
        lab.isl.apply_config()
    v = lab.isl.view
    swaps: list[int] = []
    v.frameSwapped.connect(lambda: swaps.append(time.perf_counter_ns()), Qt.DirectConnection)

    def start():
        lab.state("idle")
        if a.state != "idle":
            def go():
                if not a.no_turn:
                    lab.b.turn.emit({"question": IL.QUESTIONS[0]})
                lab.state(a.state, " ".join(IL.ANSWERS) if a.state == "speaking" else "")
            QTimer.singleShot(1500, go)
        QTimer.singleShot(3500, swaps.clear)
        QTimer.singleShot(int(3500 + a.seconds * 1000), finish)

    def finish():
        hz = v.screen().refreshRate()
        res = {"hz": hz, "platform": QGuiApplication.platformName(), "state": a.state, "fps_setting": lab.isl.cfg.get("fps", "auto"),
               "live_fps": getattr(lab.isl, "live_fps", None), "swaps": interval_stats(list(swaps), hz)}
        out = ROOT / "state" / "island-pacing.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(res, indent=1))
        s = res["swaps"]
        print(f"{res['platform']} {hz:.2f} Hz  state {a.state}  island.fps {res['fps_setting']} -> {res['live_fps']}")
        print({k: s.get(k) for k in ("n", "fps", "mean_ms", "sd_ms", "p1", "p50", "p99", "max", "vsync_hist", "on_cadence")})
        if s.get("off"):
            print("off cadence (index, vsyncs):", s["off"][:40])
        app.quit()

    QTimer.singleShot(1500, start)
    QTimer.singleShot(int(a.seconds * 1000) + 15000, app.quit)
    app.exec()
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
