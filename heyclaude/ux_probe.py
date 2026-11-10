"""UX probe: record what the island shows and what the assistant says, then find jank.

Used by tools/ux_stress.py (the real app, a simulated room) and by
tests/test_ux_stress.py (offline: fake brain/voice, the real Assistant and
QML island). Three parts:

- `Recorder` (Qt, GUI thread): every Bridge signal, every engine command and
  island model signal, the island sampled ~30x a second (state, size, label,
  words revealed, the turn's question/answer/card), audio as it plays, frame
  pacing (engine ticks + presented frames), focus, and window grabs.
- `detect(timeline)`: pure functions over the recorded events -> findings.
- `contact_sheet()` / `gif()`: frames of a scenario for the report.

A timeline is a list of events `{"t": seconds, "k": kind, ...}`, t on one clock
(time.perf_counter). Kinds: state, turn, spoken, widget, tool, effect, wake_hit,
heard, partial, cmd, q_shown, a_shown, w_shown, w_cleared, audio (a sentence
starts playing: text, secs), audio_stop, user (simulated speech: label, t0, t1),
action (a harness step), sample (the island, see Island.qml probeUx), hitch
(an engine tick late while it wanted every frame: ms), swap (a presented
frame), focus (island: bool), grab (path).
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path

REST = ("ready", "off", "paused")
FILLERS = ("let me check.",)


# =============================================================================== recorder
class Recorder:
    """Hooks a running app (Bridge + QmlIsland + Assistant). Start/stop segments
    with mark(); read `events`. All hooks append to one list (GIL-safe)."""

    def __init__(self, bridge, island=None, asst=None, sample_hz: float = 30.0, grab_fps: float = 0.0,
                 grab_dir: str | os.PathLike | None = None, clock=time.perf_counter):
        from PySide6.QtCore import QTimer
        self.clock = clock
        self.events: list[dict] = []
        self.bridge, self.island, self.asst = bridge, island, asst
        self.grab_dir = Path(grab_dir) if grab_dir else None
        self.grab_fps = float(grab_fps or 0)
        self.grabbing = False
        self._grab_n = 0
        self._grab_q: list = []
        self._grab_lock = threading.Lock()
        self._grab_busy = 0.0               # seconds spent in grabWindow (they stall the GUI thread)
        self.grab_spans: list[tuple[float, float]] = []
        self.paused = False
        self.last_probe: dict = {}
        self._hook_bridge(bridge)
        if island is not None and hasattr(island, "model"):
            self._hook_island(island)
            self._t_sample = QTimer(interval=max(5, int(1000 / sample_hz)))
            self._t_sample.timeout.connect(self._sample)
            self._t_sample.start()
            self._t_focus = QTimer(interval=50)
            self._t_focus.timeout.connect(self._focus)
            self._t_focus.start()
            self._t_grab = QTimer(interval=max(20, int(1000 / self.grab_fps)) if self.grab_fps else 1000)
            self._t_grab.timeout.connect(self._grab)
            if self.grab_fps and self.grab_dir:
                self._t_grab.start()
                threading.Thread(target=self._grab_writer, daemon=True, name="ux-grabs").start()
            self._t_hitch = QTimer(interval=500)
            self._t_hitch.timeout.connect(self._drain_hitches)
            self._t_hitch.start()
        self._focus_last = None
        if asst is not None:
            self.hook_assistant(asst)

    # -- recording ---------------------------------------------------------------------
    def add(self, k: str, **kw):
        if self.paused and k not in ("action",):
            return
        kw["t"] = self.clock()
        kw["k"] = k
        self.events.append(kw)

    def mark(self, label: str, **kw):
        self.add("action", what=label, **kw)

    def since(self, t0: float, t1: float | None = None) -> list[dict]:
        return [e for e in self.events if e["t"] >= t0 and (t1 is None or e["t"] <= t1)]

    def _hook_bridge(self, b):
        from PySide6.QtCore import Qt

        def on(name, fn, direct=False):
            sig = getattr(b, name, None)
            if sig is not None:
                if direct:
                    sig.connect(fn, Qt.DirectConnection)
                else:
                    sig.connect(fn)
        on("state", lambda m, t: self.add("state", mode=m, text=str(t)[:300]))
        # A turn is stamped when it's emitted, not when the GUI thread gets to it: an announcement
        # (a timer ended) emits it from its own thread right before its audio starts, and on a slow
        # machine (CI's software-rendered grabs) the queued copy landed after that audio, so the
        # announcement was counted into the previous turn (dead air + idle mid-turn on main's CI).
        on("turn", lambda d: self.add("turn", question=str((d or {}).get("question", ""))[:300],
                                      **({"announcement": True} if (d or {}).get("announcement") else {})),
           direct=True)
        on("spoken", lambda t, s: self.add("spoken", text=str(t)[:300], secs=float(s)))
        on("widget", lambda d: self.add("widget", widget=str((d or {}).get("widget", ""))))
        on("tool", lambda d: self.add("tool", name=str((d or {}).get("name", "")), phase=str((d or {}).get("phase", ""))))
        on("effect", lambda d: self.add("effect", effect=str((d or {}).get("effect", "")), label=str((d or {}).get("label", "")),
                                          text_from=str((d or {}).get("text_from", "")), text_to=str((d or {}).get("text_to", ""))))
        on("wake_hit", lambda t: self.add("wake_hit"))
        on("heard", lambda t: self.add("heard", text=str(t)[:300]))
        on("partial", lambda t: self.add("partial", text=str(t)[:120]))
        on("listening", lambda on_, until: self.add("listening", on=bool(on_)))

    def _hook_island(self, isl):
        m = isl.model
        m.cmd.connect(lambda fn, args: self.add("cmd", fn=str(fn), args=_short(args)) if fn not in ("pushLevel", "pushTts") else None)
        m.questionShown.connect(lambda q: self.add("q_shown", text=str(q)[:300]))
        m.answerShown.connect(lambda a: self.add("a_shown", text=str(a)[:300]))
        m.widgetShown.connect(lambda w: self.add("w_shown", widget=str((w or {}).get("widget", ""))))
        m.widgetCleared.connect(lambda: self.add("w_cleared"))
        # Frames the GUI thread prepares (afterAnimating is emitted on the GUI thread).
        # Never hook frameSwapped/afterRendering from Python: they fire on the render
        # thread, and a Python callback there deadlocks against a GUI thread holding
        # the GIL (grabWindow, setSource...), freezing the window.
        isl.view.afterAnimating.connect(lambda: self.add("swap"))

    def hook_assistant(self, asst):
        """Audio as it actually plays (the player's callback), and stops."""
        sp = getattr(asst, "speaker", None)
        if sp is None:
            return
        player = getattr(sp, "player", None)
        prev = getattr(player, "on_chunk", None)
        if player is not None and prev is not None and not getattr(prev, "_ux", False):
            def on_chunk(tag, secs, prev=prev):
                self.add("audio", text=str(tag)[:300], secs=float(secs))
                prev(tag, secs)
            on_chunk._ux = True
            player.on_chunk = on_chunk
        if player is not None and not getattr(player.stop, "_ux", False):
            orig = player.stop

            def stop(orig=orig):
                if player.active:
                    self.add("audio_stop")
                return orig()
            stop._ux = True
            player.stop = stop

    # -- sampling (GUI thread) ------------------------------------------------------------
    def probe(self) -> dict:
        isl = self.island
        if isl is None or getattr(isl, "root", None) is None or not getattr(isl, "_ready", True):
            return {}
        try:
            v = isl.call("probeUx")
            return v.toVariant() if hasattr(v, "toVariant") else (v or {})
        except Exception:
            return {}

    def _sample(self):
        p = self.probe()
        if not p:
            return
        self.last_probe = p
        keep = {k: (round(v, 1) if isinstance(v, float) else v) for k, v in p.items()}
        self.add("sample", **keep)
        self._active_until = max(getattr(self, "_active_until", 0.0),
                                 self.clock() + 1.5 if (p.get("state") not in REST or p.get("extrasH", 0) > 0) else 0)

    def _drain_hitches(self):
        isl = self.island
        try:
            v = isl.call("takeHitches")
            v = v.toVariant() if hasattr(v, "toVariant") else v
        except Exception:
            return
        if not v:
            return
        off = time.time() - self.clock()               # QML's Date.now() is epoch ms
        for t_ms, dms in v.get("hitches", []):
            t = float(t_ms) / 1000 - off
            if not self.paused:
                self.events.append({"t": t, "k": "hitch", "ms": float(dms),
                                    "grab": any(a - 0.005 <= t <= b + 0.06 for a, b in self.grab_spans[-60:])})
        if v.get("live") and not self.paused:
            self.events.append({"t": self.clock(), "k": "live_ticks", "n": int(v["live"])})

    def _focus(self):
        try:
            from .winsys import foreground_window
            fg = int(foreground_window() or 0)
            ours = fg == int(self.island.view.winId())
            active = bool(self.island.view.isActive())
        except Exception:
            return
        st = (ours, active)
        if st != self._focus_last:
            self._focus_last = st
            self.add("focus", island=ours, active=active)

    # -- frames ---------------------------------------------------------------------------
    def _grab(self):
        if not self.grabbing or self.paused or self.clock() > getattr(self, "_active_until", 0.0) + 0.5:
            return
        t0 = self.clock()
        img = self.island.grab()
        t1 = self.clock()
        self.grab_spans.append((t0, t1))
        self._grab_busy += t1 - t0
        p = self.last_probe or {}
        n = self._grab_n
        self._grab_n += 1
        name = f"f{n:05d}.png"
        with self._grab_lock:
            self._grab_q.append((img, name, p, t0))
        self.events.append({"t": t0, "k": "grab", "file": name, "state": p.get("state", ""),
                            "box": _box(p, self.island)})

    def _grab_writer(self):
        while True:
            time.sleep(0.05)
            with self._grab_lock:
                q, self._grab_q = self._grab_q, []
            for img, name, p, _t in q:
                try:
                    self.grab_dir.mkdir(parents=True, exist_ok=True)
                    img.save(str(self.grab_dir / name))
                except Exception as e:
                    print(f"[ux] grab save: {e}")

    def flush_grabs(self, timeout: float = 10.0):
        end = time.time() + timeout
        while time.time() < end:
            with self._grab_lock:
                if not self._grab_q:
                    break
            time.sleep(0.05)
        time.sleep(0.1)

    def stop(self):
        for t in ("_t_sample", "_t_focus", "_t_grab", "_t_hitch"):
            tm = getattr(self, t, None)
            if tm is not None:
                tm.stop()


def _short(args):
    try:
        s = json.dumps(args, default=str)
    except Exception:
        s = str(args)
    return s[:200]


def _box(p: dict, island) -> list:
    """The pill's rectangle in window pixels (for cropping frames)."""
    try:
        s = float(island.scale)
    except Exception:
        s = 1.0
    w, h = float(p.get("ow", 300) or 300), float(p.get("oh", 40) or 40)
    x, y = float(p.get("pillX", 0) or 0), float(p.get("pillY", 0) or 0)
    return [round(x * s), round(y * s), round(w * s), round(h * s)]


def save_timeline(events: list[dict], path: str | os.PathLike):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e, default=str) + "\n")


def load_timeline(path: str | os.PathLike) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


# =============================================================================== detectors
# Thresholds (seconds unless noted). Kept here so the report and the tests agree.
DEAD_AIR_S = 1.5
CARD_READ_S = 4.0              # a response card that just appeared is something to look at
SYNC_START_S = 0.35            # first word of a sentence vs its audio
SYNC_END_EARLY_S = 1.0         # last word may come this much before its audio ends (reading ahead is fine)
SYNC_END_LATE_S = 0.4          # ...but not this much after it
OVERLAP_S = 0.6                # the assistant keeps talking over the user this long
HITCH_MS = 34                  # an engine tick this late while it wanted every frame = a dropped frame
STAGE_JUMPS = 1                # panel size changes allowed per stage
JUMP_PX = 3.0
MERGE_S = 0.3                  # same-direction size changes this close together read as one motion


def _words(s: str) -> list[str]:
    return [w for w in re.split(r"\s+", s or "") if w]


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", (s or "").lower()).strip()


def turns(events: list[dict]) -> list[dict]:
    """Split a timeline into turns: a `turn` event to the next one (or the end).
    Each turn: {t0, t1, question, audio: [{t0, t1, text}], speech_end, end}."""
    ev = sorted(events, key=lambda e: e["t"])
    starts = [e for e in ev if e["k"] == "turn"]
    # the end of the last thing in the timeline (a sentence ends after it starts)
    t_end = max([e["t"] + float(e.get("secs", 0) or 0) for e in ev] + [e.get("t1", 0) for e in ev] + [0.0]) + 0.5
    out = []
    stops = [e["t"] for e in ev if e["k"] == "audio_stop"]
    for i, s in enumerate(starts):
        t0 = s["t"]
        t1 = starts[i + 1]["t"] if i + 1 < len(starts) else t_end
        audio = []
        for e in ev:
            if e["k"] == "audio" and t0 <= e["t"] < t1:
                end = e["t"] + float(e.get("secs", 0))
                cut = [x for x in stops if e["t"] < x < end]
                audio.append({"t0": e["t"], "t1": min([end] + cut), "text": e.get("text", ""),
                              "filler": _norm(e.get("text", "")) in [_norm(f) for f in FILLERS], "cut": bool(cut)})
        users = [e for e in ev if e["k"] == "user" and e.get("t1", e["t"]) <= t0 + 0.05]
        acts = [e for e in ev if e["k"] == "action" and e["t"] <= t0 and e.get("what") in ("type_submit", "click_submit")]
        speech_end = max([u.get("t1", u["t"]) for u in users] + [a["t"] for a in acts] + [t0 - 30])
        if speech_end < t0 - 20 or s.get("announcement"):
            speech_end = t0                     # an announcement (a timer ended) answers no one's speech
        stopped = [e["t"] for e in ev if e["k"] == "action" and t0 <= e["t"] < t1
                   and e.get("what") in ("click_stop", "esc", "hotkey", "barge_in", "click_mic", "not_me", "wake")]
        answer_end = max([a["t1"] for a in audio] + [t0])
        out.append({"i": i, "t0": t0, "t1": t1, "question": s.get("question", ""), "audio": audio,
                    "speech_end": speech_end, "answer_end": answer_end, "interrupted": bool(stopped),
                    "interrupt_t": min(stopped) if stopped else None})
    return out


def _samples(events, t0, t1):
    return [e for e in events if e["k"] == "sample" and t0 <= e["t"] < t1]


def _finding(kind, sev, t, msg, **kw):
    d = {"kind": kind, "severity": sev, "t": round(t, 3), "msg": msg}
    d.update(kw)
    return d


def detect(events: list[dict], scenario: str = "") -> dict:
    """All detectors over one scenario's timeline -> {"findings": [...], "latency": [...], "frames": {...}}."""
    ev = sorted(events, key=lambda e: e["t"])
    T = turns(ev)
    f: list[dict] = []
    lat = []
    for tr in T:
        t0, t1 = tr["t0"], tr["t1"]
        end = tr["interrupt_t"] if tr["interrupted"] else max(tr["answer_end"], t0)
        ss = _samples(ev, t0, t1)
        f += _expand_collapse(ev, tr, ss)
        f += _idle_mid_turn(ev, tr, ss, end)
        f += _revealed_twice(ev, tr, ss)
        f += _widget_early(ev, tr, ss, end)
        f += _sync(ev, tr, ss)
        f += _dead_air(ev, tr, ss, end)
        f += _layout_jumps(ev, tr, ss)
        f += _pixel_jitter(ev, tr, ss)
        lat.append(_latency(ev, tr, ss))
    f += _overlaps(ev)
    f += _focus(ev)
    frames = _frames(ev)
    f += frames.pop("findings")
    for x in f:
        x.setdefault("scenario", scenario)
    return {"findings": sorted(f, key=lambda x: x["t"]), "latency": lat, "frames": frames, "turns": len(T)}


def _panel_open(s: dict) -> bool:
    return float(s.get("extrasH", 0) or 0) > 1


def _expand_collapse(ev, tr, ss):
    """The panel under the head (question, card, answer) should open once and close once per turn."""
    opens, closes, prev = [], [], None
    for s in ss:
        o = _panel_open(s)
        if prev is not None and o != prev:
            (opens if o else closes).append(s["t"])
        prev = o
    out = []
    if len(opens) > 1 or len(closes) > 1:
        out.append(_finding("expand_collapse", 3, tr["t0"], f"panel opened {len(opens)}x / closed {len(closes)}x in one turn",
                            opens=[round(t - tr["t0"], 2) for t in opens], closes=[round(t - tr["t0"], 2) for t in closes]))
    return out


def _idle_mid_turn(ev, tr, ss, end):
    """The pill at rest ("ready") between the request and the end of its answer."""
    out = []
    lo = tr["t0"] + 0.05
    idle = [e for e in ev if e["k"] == "state" and e.get("mode") in ("idle", "off") and lo < e["t"] < end - 0.05]
    rest = [s for s in ss if s.get("state") in REST and lo < s["t"] < end - 0.05]
    if idle or rest:
        t = min([e["t"] for e in idle] + [s["t"] for s in rest])
        out.append(_finding("idle_mid_turn", 5, t, f"rest/idle shown {t - tr['t0']:.2f}s into the turn, "
                            f"answer ends at {end - tr['t0']:.2f}s", bridge_idle=len(idle), rest_samples=len(rest)))
    return out


def _revealed_twice(ev, tr, ss):
    """Words of the answer revealed, wiped, and revealed again; a sentence spoken twice;
    the speaking line re-laid out with different text."""
    out = []
    prev_n, peak, peak_text, resets = 0, 0, "", []
    for s in ss:
        if s.get("state") != "speaking":
            continue
        n = len(_words(s.get("said", "")))
        if n < prev_n and prev_n >= 2:
            resets.append((s["t"], prev_n, peak_text))
        if n > peak:
            peak, peak_text = n, s.get("said", "")
        prev_n = n
    # a reset only counts if the same words come back (not a new sentence window)
    for t, n, txt in resets:
        later = [s for s in ss if s["t"] > t and s.get("state") == "speaking"]
        again = any(_norm(s.get("said", ""))[:30] and _norm(txt).startswith(_norm(s.get("said", ""))[:30]) and
                    len(_words(s.get("said", ""))) >= 2 for s in later)
        if again:
            out.append(_finding("text_revealed_twice", 4, t, f"answer words revealed again after a reset ({n} words shown before)"))
            break
    seen = {}
    for e in ev:
        if e["k"] == "spoken" and tr["t0"] <= e["t"] < tr["t1"]:
            key = _norm(e.get("text", ""))
            if key and key in seen and e["t"] - seen[key] < 30:
                out.append(_finding("text_revealed_twice", 4, e["t"], f"sentence revealed twice: {e.get('text', '')[:60]!r}"))
            seen[key] = e["t"]
    # entering "speaking" more than once in a turn = the head starts the answer over
    enters = [e for e in ev if e["k"] == "cmd" and e.get("fn") == "go" and '"speaking"' in e.get("args", "")
              and tr["t0"] <= e["t"] < tr["t1"]]
    if len(enters) > 1:
        out.append(_finding("text_revealed_twice", 4, enters[1]["t"],
                            f"speaking entered {len(enters)}x in one turn (the answer line restarts)",
                            at=[round(e["t"] - tr["t0"], 2) for e in enters]))
    return out


def _widget_early(ev, tr, ss, end):
    """A response card that goes before its answer has finished (or flashes and is replaced)."""
    out = []
    shown = [e for e in ev if e["k"] == "w_shown" and tr["t0"] <= e["t"] < tr["t1"]]
    if not shown:
        return out
    t_show = shown[0]["t"]
    cleared = [e for e in ev if e["k"] == "w_cleared" and t_show < e["t"] < end]
    gone = [s for s in ss if s["t"] > t_show + 0.3 and s["t"] < end and not s.get("widget")]
    if cleared or gone:
        t = min([e["t"] for e in cleared] + [s["t"] for s in gone])
        out.append(_finding("widget_removed_early", 5, t, f"card gone {t - t_show:.2f}s after it appeared, "
                            f"{end - t:.2f}s before the answer ended"))
    if len(shown) > 1:
        out.append(_finding("widget_removed_early", 3, shown[1]["t"], f"card shown {len(shown)}x in one turn"))
    return out


def _sync(ev, tr, ss):
    """Each sentence's words should appear while its audio plays."""
    out = []
    spk = [s for s in ss if s.get("state") == "speaking"]
    if not spk:
        return out
    worst = None
    base = 0
    for a in tr["audio"]:
        if a["filler"]:
            continue
        n = len(_words(a["text"]))
        if not n:
            continue
        want_first, want_last = base + 1, base + n
        first = next((s["t"] for s in spk if s["t"] >= a["t0"] - 2 and len(_words(s.get("said", ""))) >= want_first), None)
        last = next((s["t"] for s in spk if s["t"] >= a["t0"] - 2 and len(_words(s.get("said", ""))) >= want_last), None)
        base += n
        if first is None:
            continue
        lag0 = first - a["t0"]
        lag1 = (last - a["t1"]) if last is not None else None
        bad = abs(lag0) > SYNC_START_S or (lag1 is not None and not a["cut"] and not -SYNC_END_EARLY_S <= lag1 <= SYNC_END_LATE_S)
        if bad and (worst is None or abs(lag0) > abs(worst[1])):
            worst = (a, lag0, lag1)
    if worst:
        a, lag0, lag1 = worst
        out.append(_finding("text_audio_sync", 3, a["t0"], f"words {'lead' if lag0 < 0 else 'lag'} the voice by "
                            f"{abs(lag0):.2f}s at the sentence start" + (f", {lag1:+.2f}s at its end" if lag1 is not None else ""),
                            sentence=a["text"][:60]))
    return out


def _progress_times(ev, t0, t1):
    """Times when something visibly moved forward."""
    ts = []
    prev = None
    for e in ev:
        if not (t0 <= e["t"] <= t1):
            continue
        k = e["k"]
        if k in ("w_shown", "q_shown", "a_shown", "effect", "audio", "spoken", "tool"):
            ts.append(e["t"])
        elif k == "sample":
            key = (e.get("state"), e.get("label"), e.get("tool"), len(_words(e.get("said", ""))), e.get("widget"),
                   e.get("pending"), round(float(e.get("extrasH", 0)) / 8))
            if key != prev:
                ts.append(e["t"])
            prev = key
    return sorted(ts)


def _dead_air(ev, tr, ss, end):
    """No sound and nothing new on screen for more than DEAD_AIR_S, between the end of
    the request and the end of its answer."""
    out = []
    a, b = tr["speech_end"], end
    if b - a < DEAD_AIR_S:
        return out
    ts = [a] + _progress_times(ev, a, b) + [b]
    # a card that just appeared is something to read: ~4 s of it is not dead air
    cards = [e["t"] for e in ev if e["k"] == "w_shown" and a <= e["t"] <= b]
    playing = [(x["t0"], x["t1"]) for x in tr["audio"]]
    gaps = []
    for x, y in zip(ts, ts[1:]):
        # time in [x, y] with no audio playing
        silent_from = x
        for p0, p1 in playing:
            if p0 <= silent_from < p1:
                silent_from = p1
        for c in cards:
            if c <= silent_from < c + CARD_READ_S:
                silent_from = min(y, c + CARD_READ_S)
        if y - silent_from > DEAD_AIR_S:
            gaps.append((silent_from, y))
    if gaps:
        g = max(gaps, key=lambda g: g[1] - g[0])
        st = next((s.get("state") for s in reversed(ss) if s["t"] <= g[0] + 0.05), "?")
        lbl = next((s.get("label") for s in reversed(ss) if s["t"] <= g[0] + 0.05), "")
        out.append(_finding("dead_air", 2 if g[1] - g[0] < 3 else 3, g[0],
                            f"{g[1] - g[0]:.1f}s with no sound and nothing new on screen ({st}: {lbl!r})",
                            gaps=[[round(x - tr["t0"], 2), round(y - x, 2)] for x, y in gaps]))
    return out


def _layout_jumps(ev, tr, ss):
    """The pill's target size changing more than once within one stage (engine state)."""
    out = []
    stages: list[list[dict]] = []
    for s in ss:
        if not stages or stages[-1][0].get("state") != s.get("state"):
            stages.append([s])
        else:
            stages[-1].append(s)
    worst = None
    for st in stages:
        name = st[0].get("state")
        if name in ("woke", "listening", "holding", "followup", "input"):
            continue                     # these grow with the live transcript by design
        changes, prev = [], (st[0].get("wt"), st[0].get("ht"))
        for s in st[1:]:
            cur = (s.get("wt"), s.get("ht"))
            if prev[0] is not None and cur[0] is not None and (abs(cur[0] - prev[0]) > JUMP_PX or abs(cur[1] - prev[1]) > JUMP_PX):
                last = changes[-1] if changes else None
                same_dir = last is not None and (cur[0] - prev[0]) * (last[2][0] - last[1][0]) >= 0 and                     (cur[1] - prev[1]) * (last[2][1] - last[1][1]) >= 0
                if same_dir and s["t"] - last[3] < MERGE_S:
                    changes[-1] = (last[0], last[1], cur, s["t"])     # one motion, retargeted on the way
                else:
                    changes.append((s["t"], prev, cur, s["t"]))
            prev = cur
        if len(changes) > STAGE_JUMPS and (worst is None or len(changes) > len(worst[1])):
            worst = (name, changes)
    if worst:
        name, ch = worst
        out.append(_finding("layout_jump", 2 if len(ch) <= 3 else 3, ch[0][0],
                            f"{len(ch)} size changes while {name}",
                            changes=[[round(t - tr["t0"], 2), [round(a[0] or 0), round(a[1] or 0)], [round(b[0] or 0), round(b[1] or 0)]]
                                     for t, a, b, _ in ch[:8]]))
    return out


def _pixel_jitter(ev, tr, ss):
    """The pill (and everything laid out on it: the answer line, the card) shifting
    left-right by a pixel or two: the width spring overshooting its target and coming
    back, or the centred position flipping between two rounded values."""
    out = []
    xs = [(s["t"], float(s.get("pillX") or 0), float(s.get("ow") or 0), float(s.get("wt") or 0)) for s in ss
          if s.get("pillX") is not None]
    flips, over = [], []
    for (t0, a, _, _), (t1, b, _, _), (t2, c, _, _) in zip(xs, xs[1:], xs[2:]):
        if a != b and b != c and (b - a) * (c - b) < 0 and abs(b - a) <= 3 and abs(c - b) <= 3:
            flips.append(t1)
    # overshoot: the width passes its target and comes back by more than half a pixel
    for i in range(1, len(xs)):
        t, _, w, wt = xs[i]
        _, _, w0, wt0 = xs[i - 1]
        if wt == wt0 and wt and w0 and (w0 - wt) * (w - wt) < 0:
            peak = max(abs(x[2] - wt) for x in xs[i:i + 15] if x[3] == wt) if xs[i:i + 15] else 0
            if peak > 1.0:
                over.append((t, peak))
    if flips or over:
        t = min([*flips, *[o[0] for o in over]])
        msg = []
        if over:
            msg.append(f"width overshoots its target by up to {max(o[1] for o in over):.1f}px ({len(over)}x)")
        if flips:
            msg.append(f"pill x flips back and forth {len(flips)}x")
        st = next((s.get("state") for s in ss if s["t"] >= t), "?")
        out.append(_finding("pixel_jitter", 3, t, "; ".join(msg) + f" (while {st})",
                            at=[round(x - tr["t0"], 2) for x in sorted(flips + [o[0] for o in over])[:10]]))
    return out


def _latency(ev, tr, ss):
    se = tr["speech_end"]

    def first(pred):
        return next((e["t"] - se for e in ev if e["t"] >= se - 0.01 and pred(e)), None)
    vis = first(lambda e: e["k"] == "sample" and e.get("state") == "thinking")
    aud = next((a["t0"] - se for a in tr["audio"]), None)
    ans = next((a["t0"] - se for a in tr["audio"] if not a["filler"]), None)
    wid = first(lambda e: e["k"] == "w_shown" and e["t"] < tr["t1"])
    eff = first(lambda e: e["k"] == "effect" and e["t"] < tr["t1"])         # M10: the change landed
    r = lambda v: round(v, 3) if v is not None else None   # noqa: E731
    return {"question": tr["question"][:80], "first_visual": r(vis), "effect": r(eff), "first_audio": r(aud),
            "widget": r(wid), "answer": r(ans), "answer_end": r(tr["answer_end"] - se)}


def _overlaps(ev):
    """Two voices at once: the assistant's sentences overlapping each other, or it
    talking over the user for more than OVERLAP_S."""
    out = []
    audio = [e for e in ev if e["k"] == "audio"]
    stops = [e["t"] for e in ev if e["k"] == "audio_stop"]
    spans = []
    for e in audio:
        end = e["t"] + float(e.get("secs", 0))
        cut = [x for x in stops if e["t"] < x < end]
        spans.append((e["t"], min([end] + cut), e.get("text", "")))
    for (a0, a1, ta), (b0, b1, tb) in zip(spans, spans[1:]):
        if b0 < a1 - 0.08:
            out.append(_finding("overlapping_voices", 4, b0, f"two sentences at once ({a1 - b0:.2f}s): {tb[:40]!r}"))
    users = [e for e in ev if e["k"] == "user"]
    for u in users:
        u0, u1 = u.get("t0", u["t"]), u.get("t1", u["t"])
        talk = sum(max(0.0, min(u1, s1) - max(u0, s0)) for s0, s1, _ in spans)
        if talk > OVERLAP_S:
            out.append(_finding("overlapping_voices", 3, u0, f"assistant talked over the user for {talk:.2f}s "
                                f"({u.get('label', '')[:40]!r})"))
    return out


def _focus(ev):
    """The island must never take focus, except while the talk-or-type field is open."""
    out = []
    typing = []
    for e in ev:
        if e["k"] == "action" and e.get("what") == "hotkey":
            typing.append(e["t"])
    for e in ev:
        if e["k"] == "focus" and (e.get("island") or e.get("active")):
            if not any(0 <= e["t"] - t < 60 for t in typing):
                out.append(_finding("took_focus", 5, e["t"], "the island took the keyboard focus without the talk-or-type field"))
                break
    return out


def _frames(ev):
    hit = [e for e in ev if e["k"] == "hitch"]
    clean = [e for e in hit if not e.get("grab")]
    live = sum(e.get("n", 0) for e in ev if e["k"] == "live_ticks")
    swaps = [e["t"] for e in ev if e["k"] == "swap"]
    iv = sorted(b - a for a, b in zip(swaps, swaps[1:]) if b - a < 0.5)
    pct = lambda p: round(iv[min(len(iv) - 1, int(p * len(iv)))] * 1000, 1) if iv else None   # noqa: E731
    changes = [e["t"] for e in ev if e["k"] == "cmd" and e.get("fn") == "go"]
    near = [e for e in clean if any(-0.05 <= e["t"] - c <= 0.6 for c in changes)]
    out = {"live_ticks": live, "hitches": len(clean), "hitches_in_transitions": len(near),
           "hitches_while_grabbing": len(hit) - len(clean),
           "worst_hitch_ms": round(max([e["ms"] for e in clean], default=0), 1),
           "swap_p50_ms": pct(0.5), "swap_p95_ms": pct(0.95), "swap_p99_ms": pct(0.99), "swaps": len(swaps)}
    fs = []
    bad = [e for e in near if e["ms"] > 50]
    if bad:
        w = max(bad, key=lambda e: e["ms"])
        fs.append(_finding("dropped_frames", 3 if w["ms"] > 100 else 2, w["t"],
                           f"{len(near)} late frames during transitions (worst {w['ms']:.0f} ms)"))
    out["findings"] = fs
    return out


# Ranking for the report: how janky it feels (5 = worst).
FEEL = {"idle_mid_turn": "the island drops to rest in the middle of an answer",
        "widget_removed_early": "the card disappears while its answer is still going",
        "took_focus": "the island steals keyboard focus",
        "text_revealed_twice": "the answer text restarts / is shown twice",
        "overlapping_voices": "two voices at once",
        "expand_collapse": "the panel opens and closes more than once",
        "text_audio_sync": "words and voice out of step",
        "dead_air": "silence with nothing moving on screen",
        "layout_jump": "the pill changes size several times in one stage",
        "pixel_jitter": "the pill and its content shift left-right by a pixel or two",
        "dropped_frames": "stutter during transitions"}


# =============================================================================== frames
def contact_sheet(frames_dir: str | os.PathLike, grabs: list[dict], out: str | os.PathLike, t0: float,
                  cols: int = 4, max_frames: int = 24, crop_h: int | None = None, bg="#1b1b1f") -> str | None:
    """Pick up to `max_frames` frames (every state change, then evenly) and lay them
    out with their time and state. QPainter only (works without Pillow)."""
    from PySide6.QtCore import QRect, Qt
    from PySide6.QtGui import QColor, QFont, QImage, QPainter
    frames_dir = Path(frames_dir)
    gs = [g for g in grabs if (frames_dir / g["file"]).exists()]
    if not gs:
        return None
    pick = [gs[0]]
    for g in gs[1:]:
        if g["state"] != pick[-1]["state"]:
            pick.append(g)
    if len(pick) < max_frames:
        step = max(1, len(gs) // (max_frames - len(pick) + 1))
        pick = sorted({id(g): g for g in pick + gs[::step]}.values(), key=lambda g: g["t"])
    pick = pick[:max_frames]
    imgs = [QImage(str(frames_dir / g["file"])) for g in pick]
    cw = max(i.width() for i in imgs)
    ch = crop_h or max(min(i.height(), max(g["box"][1] + g["box"][3] + 12 for g in pick)) for i in imgs)
    rows = (len(imgs) + cols - 1) // cols
    lab = 22
    sheet = QImage(cols * cw, rows * (ch + lab), QImage.Format_ARGB32)
    sheet.fill(QColor(bg))
    p = QPainter(sheet)
    p.setRenderHint(QPainter.Antialiasing)
    f = QFont("Segoe UI", 9)
    p.setFont(f)
    for i, (g, img) in enumerate(zip(pick, imgs)):
        x, y = (i % cols) * cw, (i // cols) * (ch + lab)
        p.drawImage(QRect(x, y + lab, cw, ch), img, QRect(0, 0, cw, ch))
        p.setPen(QColor("#bdbdbd"))
        p.drawText(QRect(x + 6, y, cw - 12, lab), Qt.AlignVCenter | Qt.AlignLeft, f"{g['t'] - t0:6.2f}s  {g['state']}")
    p.end()
    sheet.save(str(out))
    return str(out)


def gif(frames_dir: str | os.PathLike, grabs: list[dict], out: str | os.PathLike, t0: float, fps: float = 10,
        max_s: float = 40.0, crop_h: int | None = None, bg=(27, 27, 31)) -> str | None:
    """An animated GIF of the scenario (needs Pillow). Frames are timed by when they were grabbed."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        print("[ux] Pillow isn't installed: no GIF (pip install pillow)")
        return None
    frames_dir = Path(frames_dir)
    gs = [g for g in grabs if (frames_dir / g["file"]).exists() and g["t"] - t0 <= max_s]
    if len(gs) < 2:
        return None
    ims, durs = [], []
    hmax = crop_h or max(g["box"][1] + g["box"][3] + 12 for g in gs)
    for g, nxt in zip(gs, gs[1:] + [None]):
        im = Image.open(frames_dir / g["file"]).convert("RGBA")
        im = im.crop((0, 0, im.width, min(im.height, hmax)))
        base = Image.new("RGBA", im.size, bg + (255,))
        base.alpha_composite(im)
        d = ImageDraw.Draw(base)
        d.text((8, base.height - 14), f"{g['t'] - t0:5.2f}s {g['state']}", fill=(150, 150, 150, 255))
        ims.append(base.convert("P", palette=Image.ADAPTIVE, colors=128))
        durs.append(int(max(40, min(1000, ((nxt["t"] if nxt else g["t"] + 0.1) - g["t"]) * 1000))))
    ims[0].save(str(out), save_all=True, append_images=ims[1:], duration=durs, loop=0, disposal=2, optimize=False)
    return str(out)
