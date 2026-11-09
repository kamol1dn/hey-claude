"""M10 latency bench: how long an instant command takes inside the process.

    .venv\\Scripts\\python tools\\bench_instant.py            # mocked writes + live READ-ONLY reads
    .venv\\Scripts\\python tools\\bench_instant.py --no-live  # mocked only (CI-safe)

1. text -> match -> executor return, every write mocked (the M9 tool code with
   its Windows layer replaced, as in tests/test_intents.py).
2. Live, read-only: the executor path with the real Windows APIs, for reads
   only (get_volume, get_theme, get_brightness, now_playing, the clock). A
   guard refuses any other tool, so nothing on this PC changes.

Speech end -> effect in the running app adds the VAD tail and speech-to-text
before this; the app logs it per turn (state/latency.csv, speech_end_to_effect)
and tools/ux_stress.py reports it per scenario.
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

READS = {"get_volume", "get_theme", "get_brightness", "now_playing"}


def stats(xs):
    xs = sorted(xs)
    return f"median {statistics.median(xs):.2f} ms, p95 {xs[max(0, int(len(xs) * 0.95) - 1)]:.2f} ms (n={len(xs)})"


def mocked(n: int = 30):
    import test_intents as T
    from unittest import mock
    from heyclaude.intents import Executor, Timers
    from heyclaude.mcp_windows import display, registry
    w = T.FakeWin()
    e = T.engine()
    ex = Executor(e, runner=registry.call, timers=Timers())
    texts = ["Volume 30.", "Turn it up a bit.", "Mute.", "Unmute.", "Dark mode.", "Light mode.", "Next song.",
             "Pause.", "Play.", "What time is it?", "Brightness down.", "My ears hurt, it's too loud."]
    m_ms, t_ms = [], []

    def go():
        with mock.patch.object(display, "_screens", lambda: [{"id": "panel", "kind": "panel", "level": 50}]), \
                mock.patch.object(display, "_os_panel_set", lambda v: None):
            for _ in range(n):
                for t in texts:
                    t0 = time.perf_counter()
                    m = e.match(t)
                    t1 = time.perf_counter()
                    ex.run(m)
                    t2 = time.perf_counter()
                    m_ms.append((t1 - t0) * 1000)
                    t_ms.append((t2 - t0) * 1000)
    T._with(w.patches(), go)
    print(f"[bench] engine load: {e.load_ms:.0f} ms")
    print(f"[bench] text -> match:                          {stats(m_ms)}")
    print(f"[bench] text -> match -> executor (M9 mocked):  {stats(t_ms)}")


def live(n: int = 20):
    from heyclaude.intents import Executor, IntentEngine, Match
    from heyclaude.intents.executors import _default_runner

    def guard(name, args):
        if name not in READS:
            raise PermissionError(f"bench_instant is read-only: refused {name}")
        return _default_runner(name, args)
    e = IntentEngine({})
    ex = Executor(e, runner=guard)
    rows = {}
    for name in ("get_volume", "get_theme", "get_brightness"):
        xs = []
        for _ in range(n):
            t0 = time.perf_counter()
            try:
                ex.call(name, {})
            except Exception as err:
                print(f"[bench] {name}: {err}")
                break
            xs.append((time.perf_counter() - t0) * 1000)
        if xs:
            rows[f"executor.call({name})"] = xs
    for text in ("What's playing?", "What time is it?"):
        xs = []
        for _ in range(n):
            t0 = time.perf_counter()
            m = e.match(text)
            out = ex.run(m)
            xs.append((time.perf_counter() - t0) * 1000)
        rows[f"{text!r} -> {m.intent} -> {out.say[:30]!r}"] = xs
    assert isinstance(m, Match)
    for k, xs in rows.items():
        print(f"[bench] live read-only {k}: first {xs[0]:.1f} ms; {stats(xs[1:] or xs)}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--no-live", action="store_true", help="skip the live read-only timings")
    ap.add_argument("-n", type=int, default=30)
    a = ap.parse_args()
    mocked(a.n)
    if not a.no_live:
        live(max(5, a.n // 2))


if __name__ == "__main__":
    main()
