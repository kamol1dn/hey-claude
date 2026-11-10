"""Local timers for "set a timer for 5 minutes" (no model involved).

The island's timer card (widgets/timer) counts down on screen; this
scheduler is what actually keeps time, so a timer still ends (and is said
aloud) when another card has replaced it on the island, or the island was
restarted. When the card also reports "timer_done", owns() lets the app skip
the second announcement.
"""
from __future__ import annotations

import itertools
import threading
import time
from dataclasses import dataclass, field


@dataclass
class LocalTimer:
    id: int
    seconds: int
    label: str
    ends: float
    state: str = "running"                  # running | done | cancelled
    handle: threading.Timer | None = field(default=None, repr=False)

    def left(self, now: float) -> float:
        return max(0.0, self.ends - now)


class Timers:
    def __init__(self, on_done=None, clock=time.time):
        self.on_done = on_done or (lambda t: None)
        self.clock = clock
        self.items: list[LocalTimer] = []
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    def start(self, seconds: int, label: str = "") -> LocalTimer:
        t = LocalTimer(next(self._ids), int(seconds), (label or "").strip(), self.clock() + int(seconds))
        h = threading.Timer(int(seconds), self._fire, args=(t,))
        h.daemon = True
        t.handle = h
        with self._lock:
            self.items.append(t)
            self.items = [x for x in self.items if x.state == "running" or self.clock() - x.ends < 600]
        h.start()
        return t

    def _fire(self, t: LocalTimer):
        with self._lock:
            if t.state != "running":
                return
            t.state = "done"
        try:
            self.on_done(t)
        except Exception as e:
            print(f"[timer] {e}")

    def running(self) -> list[LocalTimer]:
        with self._lock:
            return [t for t in self.items if t.state == "running"]

    def snapshot(self) -> dict:
        """The running timers, soonest first, as a plain result (M5b): what "what timers do I
        have" answers from and the reminders card is made of (widgets/reminders)."""
        now = self.clock()
        run = sorted(self.running(), key=lambda t: t.ends)
        return {"now": round(now, 3),
                "timers": [{"id": t.id, "label": t.label, "kind": "timer", "seconds": t.seconds,
                            "left_s": round(t.left(now), 1), "ends_at": round(t.ends, 3)} for t in run]}

    def cancel(self, label: str | None = None) -> list[LocalTimer]:
        """Cancel the timers with this label (all running ones when no label)."""
        want = (label or "").strip().lower()
        out = []
        with self._lock:
            for t in self.items:
                if t.state == "running" and (not want or t.label.lower() == want):
                    t.state = "cancelled"
                    if t.handle is not None:
                        t.handle.cancel()
                    out.append(t)
        return out

    def owns(self, label: str) -> bool:
        """The island's card for this label ended (or was cancelled here): is it one of ours?"""
        want = (label or "").strip().lower()
        now = self.clock()
        with self._lock:
            return any(t.label.lower() == want and t.ends - 15 <= now <= t.ends + 60 or
                       (t.label.lower() == want and t.state == "cancelled" and now <= t.ends + 60)
                       for t in self.items)
