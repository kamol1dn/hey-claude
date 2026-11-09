"""Acceptance measurements for the M5 island (docs/island-motion.md section 11),
run by `tools/island_lab.py --measure [--quick]` on the real window.

What it measures, and how:
- frames per second per state: QQuickWindow.frameSwapped over a window;
- CPU: this process (all threads) and the UI thread alone (psutil);
- GPU: the "GPU Engine ... engtype_3D" utilisation of this process, the
  counter Task Manager shows (PDH via pywin32); n/a if PDH isn't there;
- transitions: frame-to-frame times while cycling the section 6 transitions
  (p50 / p99, and the share of frames within 16.7 ms);
- wake latency: wake_hit to the next swapped frame; mic level to ember radius;
- a state storm (random states every 50-300 ms): no NaN, labels settled, the
  pill edge never jumps more than 40 px in a frame;
- the rest invariant (no FrameAnimation running, rest timer only) 1 s after
  entering ready / off / paused, full and calm;
- focus: the foreground window is unchanged in every state except input, and
  is given back when input closes.
"""
from __future__ import annotations

import json
import os
import statistics
import threading
import time

from PySide6.QtCore import QElapsedTimer, QEventLoop, QTimer


def wait(ms):
    loop = QEventLoop()
    QTimer.singleShot(int(ms), loop.quit)
    loop.exec()


class Probe:
    def __init__(self, lab):
        import psutil
        self.lab = lab
        self.proc = psutil.Process()
        self.main_tid = threading.main_thread().native_id
        self.clock = QElapsedTimer()
        self.clock.start()
        self.stamps: list[int] = []
        lab.isl.view.frameSwapped.connect(lambda: self.stamps.append(self.clock.nsecsElapsed()))
        self.gpu = _GpuCounter(os.getpid())

    def ui_cpu(self):
        for t in self.proc.threads():
            if t.id == self.main_tid:
                return t.user_time + t.system_time
        return 0.0

    def window(self, secs):
        """(fps, process CPU %, UI-thread CPU %, GPU 3D %) over `secs`."""
        self.stamps.clear()
        c0, u0, w0 = self.proc.cpu_times(), self.ui_cpu(), time.perf_counter()
        self.gpu.sample()
        wait(secs * 1000)
        dt = time.perf_counter() - w0
        c1, u1 = self.proc.cpu_times(), self.ui_cpu()
        gpu = self.gpu.sample()
        cpu = ((c1.user - c0.user) + (c1.system - c0.system)) / dt * 100
        return len(self.stamps) / dt, cpu, (u1 - u0) / dt * 100, gpu

    def frame_times(self):
        s = self.stamps
        return [(b - a) / 1e6 for a, b in zip(s, s[1:])]


class _GpuCounter:
    """Sum of this process's 3D engine utilisation (what Task Manager shows)."""

    def __init__(self, pid):
        self.pid, self.ok, self.counters, self.err = pid, False, [], ""
        try:
            import win32pdh
            self.pdh = win32pdh
            self.ok = True
        except Exception as e:
            self.err = str(e)

    def _expand(self):
        """The process gets GPU engine instances only once it has drawn: expand lazily."""
        p = self.pdh
        try:
            paths = p.ExpandCounterPath(f"\\GPU Engine(pid_{self.pid}_*engtype_3D)\\Utilization Percentage")
        except Exception as e:
            self.err = str(e)
            return
        if paths and len(paths) != len(self.counters):
            self.q = p.OpenQuery()
            self.counters = [p.AddCounter(self.q, x) for x in paths]
            p.CollectQueryData(self.q)

    def sample(self):
        if not self.ok:
            return None
        self._expand()
        if not self.counters:
            return None
        try:
            self.pdh.CollectQueryData(self.q)
            tot = 0.0
            for c in self.counters:
                try:
                    tot += self.pdh.GetFormattedCounterValue(c, self.pdh.PDH_FMT_DOUBLE)[1]
                except Exception:
                    pass
            return tot
        except Exception:
            return None


def run(lab, quick=False):
    from heyclaude.winsys import foreground_window, window_title
    isl, b = lab.isl, lab.b
    P = Probe(lab)
    W = 4 if quick else 10
    res: dict = {"renderer": "software" if isl.model._software else "gpu", "rows": []}
    try:
        from PySide6.QtGui import QGuiApplication
        res["refresh_hz"] = QGuiApplication.primaryScreen().refreshRate()
    except Exception:
        pass
    wait(1800)
    fg0 = foreground_window()
    focus_bad = []

    def measure(name, setup, settle=1500, secs=W):
        setup()
        wait(settle)
        fps, cpu, ui, gpu = P.window(secs)
        fg = foreground_window()
        if fg != fg0:
            focus_bad.append((name, window_title(fg)))
        row = {"state": name, "fps": round(fps, 1), "cpu": round(cpu, 2), "ui_cpu": round(ui, 2),
               "gpu3d": None if gpu is None else round(gpu, 2), "engine_gap": isl._last_gap}
        res["rows"].append(row)
        print(f"[measure] {name:22s} {row['fps']:6.1f} fps  cpu {row['cpu']:5.2f}%  ui {row['ui_cpu']:5.2f}%  "
              f"gpu3d {row['gpu3d']}  gap {row['engine_gap']}")

    lab.mic_mode = "speech"
    measure("ready", lambda: lab.state("idle"))
    measure("off", lambda: (b.listening.emit(True, 0.0), lab.state("off", "listening off")))
    measure("paused", lambda: (b.listening.emit(False, time.time() + 3000), lab.state("off", "paused")))
    measure("ready + tasks", lambda: (b.listening.emit(True, 0.0), lab.state("idle"), lab.add_task()), settle=8000)
    lab.tasks.clear(); b.tasks.emit([])
    measure("loading", lambda: lab.state("loading", "loading models…"))
    measure("listening", lambda: lab.wake())
    measure("holding", lambda: lab.state("holding"))
    measure("followup", lambda: lab.state("followup", "30"))
    measure("thinking", lambda: lab.state("thinking", "what's on my calendar tomorrow?"))
    measure("speaking", lambda: lab.state("speaking", " ".join(["word"] * 60)))
    measure("error hold", lambda: lab.state("error", "couldn't reach the model"), settle=1200, secs=min(W, 2.5))
    measure("ready (back)", lambda: lab.state("idle"), settle=6000)
    isl._send("setMotion", ["calm"])
    measure("calm ready", lambda: lab.state("idle"))
    measure("calm off", lambda: (b.listening.emit(True, 0.0), lab.state("off", "listening off")))
    measure("calm paused", lambda: (b.listening.emit(False, time.time() + 3000), lab.state("off", "paused")))
    isl._send("setMotion", ["full"])
    lab.state("idle")
    wait(1500)

    # -- rest invariant, 1 s after entering each rest state ------------------------------
    inv = []
    for motion in ("full", "calm"):
        isl._send("setMotion", [motion])
        for st, setup in (("ready", lambda: lab.state("idle")),
                          ("off", lambda: (b.listening.emit(True, 0.0), lab.state("off", "listening off"))),
                          ("paused", lambda: (b.listening.emit(False, time.time() + 3000), lab.state("off", "paused")))):
            lab.state("speaking", "hello there")
            wait(500)
            setup()
            wait(1150)          # the paused pilot's own entry ends at 1000 ms (400 delay + 600)
            rc = isl.call("restCheck")
            rc = rc.toVariant() if hasattr(rc, "toVariant") else rc
            ok = rc and not rc.get("frameAnimation") and not rc.get("layer")
            if st == "off" or (motion == "calm" and st == "ready"):
                ok = ok and not rc.get("timer")             # 0 frames between events
            elif motion == "calm" and st == "paused":
                ok = ok and (not rc.get("timer") or rc.get("interval", 0) >= 1000)   # the arc's 30 s step
            rc = dict(rc or {})
            rc["engine_state"] = rc.pop("state", None)
            inv.append({"motion": motion, "state": st, "ok": bool(ok), **rc})
            b.listening.emit(True, 0.0)
    isl._send("setMotion", ["full"])
    res["rest_invariant"] = inv
    print("[measure] rest invariant:", "ok" if all(x["ok"] for x in inv) else inv)

    # -- transitions: frame times while cycling section 6 ---------------------------------
    lab.state("idle")
    wait(1200)
    P.stamps.clear()
    seq = [lab.wake, lambda: lab.state("holding"), lambda: lab.state("listening"), lambda: lab.state("thinking"),
           lambda: lab.state("speaking"), lambda: lab.state("followup", "2"), lambda: lab.state("idle"),
           lambda: lab.state("off", "listening off"), lambda: lab.state("idle"), lambda: lab.state("error", "oops"),
           lambda: lab.state("idle"), lab.add_task, lab.task_done, lambda: b.mismatch.emit(), lab.swap_badge,
           lab.swap_badge, lambda: lab.effect(0), lambda: lab.widget("calendar_list")]
    for fn in seq:
        fn()
        wait(650)
    ft = [t for t in P.frame_times() if t < 200]       # gaps between rest ticks aren't transition frames
    if ft:
        ft.sort()
        p99 = ft[int(0.99 * (len(ft) - 1))]
        res["transitions"] = {"frames": len(ft), "p50_ms": round(statistics.median(ft), 2), "p99_ms": round(p99, 2),
                              "within_16_7": round(sum(1 for t in ft if t <= 16.7) / len(ft), 4)}
        print("[measure] transitions:", res["transitions"])
    lab.tasks.clear(); b.tasks.emit([]); isl.clear_widget()

    # -- wake latency and mic -> ember ----------------------------------------------------
    lat = []
    for _ in range(8):
        lab.state("idle")
        wait(1500)
        P.stamps.clear()
        t0 = P.clock.nsecsElapsed()
        b.wake_hit.emit(time.time())
        lab.mode = "listening"
        wait(80)
        after = [s for s in P.stamps if s > t0]
        if after:
            lat.append((after[0] - t0) / 1e6)
    res["wake_first_frame_ms"] = {"median": round(statistics.median(lat), 1), "max": round(max(lat), 1)} if lat else None
    print("[measure] wake -> first frame:", res["wake_first_frame_ms"])
    lab.state("listening")
    lab.mic_mode = "silent"
    wait(1200)
    base = _er(isl)
    t0 = time.perf_counter()
    lab.mic_mode = "manual"
    lab.mic_val = 0.9
    b.level.emit(0.9)
    got = None
    while time.perf_counter() - t0 < 0.3:
        wait(2)
        if _er(isl) > base + 0.5:
            got = (time.perf_counter() - t0) * 1000
            break
    res["mic_to_ember_ms"] = None if got is None else round(got, 1)
    print("[measure] mic level -> ember radius:", res["mic_to_ember_ms"], "ms")
    lab.mic_mode = "speech"

    # -- state storm ------------------------------------------------------------------------
    from island_lab import storm
    secs = 20 if quick else 120
    st0 = _stats(isl)
    storm(lab, secs)
    wait(secs * 1000 + 2500)
    if isl.input_open:
        isl.session.cancel()
    lab.state("idle")
    wait(3000)
    st1 = _stats(isl)
    snap = isl.call("snapshot")
    snap = snap.toVariant() if hasattr(snap, "toVariant") else snap
    partial = [l for l in (snap or {}).get("labels", []) if 0.01 < l["a"] < 0.99]
    res["storm"] = {"seconds": secs, "nan": st1["nan"] - st0["nan"], "max_edge_px_per_frame": round(st1["maxEdge"], 2),
                    "labels_partial_after": len(partial), "final_state": snap.get("state") if snap else None,
                    "tweens_left": snap.get("tweens") if snap else None}
    print("[measure] storm:", res["storm"])

    # -- focus ------------------------------------------------------------------------------
    res["focus"] = {"start": window_title(fg0), "changed_in": focus_bad}
    isl.toggle_input()
    wait(500)
    took = foreground_window() == int(isl.view.winId())
    isl.session.cancel()
    wait(500)
    res["focus"]["input_takes_focus"] = took
    res["focus"]["input_gives_back"] = foreground_window() == fg0
    print("[measure] focus:", res["focus"])

    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "state", "island-measure.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(res, f, indent=1)
    print("[measure] written", out)
    return res


def _er(isl):
    v = isl.call("probe")
    v = v.toVariant() if hasattr(v, "toVariant") else v
    return float(v["er"]) if v else 0.0


def _stats(isl):
    v = isl.call("engineStats")
    v = v.toVariant() if hasattr(v, "toVariant") else v
    return v
