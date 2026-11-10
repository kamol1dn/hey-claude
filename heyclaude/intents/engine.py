"""IntentEngine: text -> Match (or None), in about a millisecond.

Order inside match():
  1. the grammar (grammar.py) over the normalized text, then the Russian and
     Uzbek grammars (grammar_ru.py, grammar_uz.py);
  2. follow-ups on the last instant command ("a bit more", "again", "the other
     way", "make it 40"), only while it is recent (instant.context_s);
  3. phrasings you taught it ("teach it"), exactly;
  4. the classifier (classifier.py), for English paraphrases; timid by design.
None means "not an instant command": the request goes on to Claude.

The engine only decides. executors.py acts, and the assistant calls
remember() after a command ran, which is what step 2 reads.
"""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import grammar as G
from . import grammar_ru as RU
from . import grammar_uz as UZ
from . import learn
from .classifier import Classifier
from .normalize import basic, fix_slips, normalize

EXAMPLES = Path(__file__).with_name("examples.yaml")
DEFAULTS = {"enabled": True, "threshold": 0.62, "ack": "speak", "step": 10, "context_s": 120,
            "radios": True, "learn": True, "classifier": True}

DOMAIN = {"volume": "volume", "brightness": "brightness", "theme": "theme", "media": "media", "timer": "timer",
          "app": "app", "settings": "app", "screen": "lock", "clock": "clock", "radio": "radio", "learn": "learn"}
# What the classifier may decide (never radios, lock, apps, the clock or timers it can't size).
CLASSIFIER_LABELS = {"volume.up", "volume.down", "volume.mute", "volume.unmute", "brightness.up", "brightness.down",
                     "theme.dark", "theme.light", "media.next", "media.previous", "media.pause", "media.play",
                     "media.now_playing", "timer.cancel"}


@dataclass
class Match:
    intent: str                     # e.g. "volume.set"
    slots: dict
    text: str                       # what was said
    norm: str                       # what the grammar saw
    source: str                     # grammar | context | taught | classifier
    score: float = 1.0
    match_ms: float = 0.0
    note: str = ""                  # the classifier's nearest example, the context it used
    extra: dict = field(default_factory=dict)

    @property
    def domain(self) -> str:
        return DOMAIN.get(self.intent.split(".")[0], self.intent.split(".")[0])

    def describe(self) -> str:
        s = ", ".join(f"{k}={v}" for k, v in self.slots.items())
        return f"{self.intent}({s}) via {self.source}" + (f" {self.score:.2f}" if self.source == "classifier" else "")


def language(norm: str) -> str:
    """'ru', 'uz' or 'en' for normalized text: Cyrillic is Russian unless it has
    Uzbek letters (ў қ ғ ҳ); Latin is Uzbek when it has Uzbek command words."""
    if UZ.HAS_CYR.search(norm):
        return "uz" if re.search(r"[ўқғҳ]", norm) else "ru"
    return "uz" if UZ.looks_uzbek(norm) else "en"


def _label_to_intent(label: str, step: int) -> tuple[str, dict]:
    return {
        "volume.up": ("volume.step", {"delta": step}), "volume.down": ("volume.step", {"delta": -step}),
        "volume.mute": ("volume.mute", {}), "volume.unmute": ("volume.unmute", {}),
        "brightness.up": ("brightness.step", {"delta": step}), "brightness.down": ("brightness.step", {"delta": -step}),
        "theme.dark": ("theme.set", {"mode": "dark"}), "theme.light": ("theme.set", {"mode": "light"}),
        "media.next": ("media.next", {"action": "next"}), "media.previous": ("media.previous", {"action": "previous"}),
        "media.pause": ("media.pause", {"action": "pause"}), "media.play": ("media.play", {"action": "play"}),
        "media.now_playing": ("media.now_playing", {}), "timer.cancel": ("timer.cancel", {}),
    }.get(label, (label, {}))


class IntentEngine:
    def __init__(self, cfg: dict | None = None, state_dir: str | Path | None = None, examples: Path | None = None,
                 clock=time.monotonic):
        self.cfg = cfg if cfg is not None else {}
        self.state_dir = Path(state_dir) if state_dir else None
        self.examples_path = examples or EXAMPLES
        self.clock = clock
        self.apps: dict[str, str] = {}
        self.has_timers = lambda: False
        self._last: tuple[Match, dict | None, float] | None = None
        self._lock = threading.Lock()
        self.taught: dict[str, tuple[str, dict]] = {}
        self.clf: Classifier | None = None
        t = time.perf_counter()
        try:                                    # the app / Settings rules read these: import them now, not mid-request
            from ..mcp_windows import apps, screen  # noqa: F401
        except Exception as e:
            print(f"[instant] {e}")
        self.reload()
        self.load_ms = (time.perf_counter() - t) * 1000

    # -- config ---------------------------------------------------------------------------
    def opt(self, key: str):
        v = self.cfg.get(key) if isinstance(self.cfg, dict) else None
        return DEFAULTS[key] if v is None else v

    @property
    def enabled(self) -> bool:
        return bool(self.opt("enabled"))

    def reload(self):
        """(Re)train the classifier from examples.yaml + the taught phrasings."""
        import yaml
        rows = []
        try:
            data = yaml.safe_load(self.examples_path.read_text(encoding="utf-8")) or {}
            for label, phrases in data.items():
                rows += [(str(p), str(label)) for p in phrases or []]
        except Exception as e:
            print(f"[instant] examples: {e}")
        self.taught = {}
        self._taught_mtime = learn.taught_mtime(self.state_dir)
        for ex in learn.load_taught(self.state_dir):
            n = normalize(ex["text"])
            if n:
                self.taught[n] = (ex["intent"], dict(ex.get("slots") or {}))
                lab = learn.intent_label(ex["intent"], ex.get("slots") or {})
                if lab:
                    rows.append((ex["text"], lab))
        self.clf = Classifier(rows, threshold=float(self.opt("threshold")))

    def _check_taught(self):
        """Reload when state/intents_taught.yaml changed on disk (edited by hand, or a
        phrasing removed in Settings). One stat per request."""
        if self.state_dir is not None and learn.taught_mtime(self.state_dir) != self._taught_mtime:
            self.reload()

    def set_apps(self, names):
        """The installed Start-menu apps (names), for "open <app>"."""
        self.apps = {G._norm_app(n): n for n in names if n}

    # -- context ----------------------------------------------------------------------------
    def remember(self, match: Match, result: dict | None = None):
        with self._lock:
            self._last = (match, result, self.clock())

    def last(self) -> tuple[Match, dict | None] | None:
        with self._lock:
            if self._last is None:
                return None
            m, r, t = self._last
            if self.clock() - t > float(self.opt("context_s")):
                return None
            return m, r

    def forget(self):
        with self._lock:
            self._last = None

    def _ctx(self) -> G.Ctx:
        last = self.last()
        return G.Ctx(step=int(self.opt("step")), domain=last[0].domain if last else None,
                     last_intent=last[0].intent if last else None, apps=self.apps,
                     has_timers=bool(self.has_timers()), radios=bool(self.opt("radios")))

    # -- matching ---------------------------------------------------------------------------
    def match(self, text: str) -> Match | None:
        if not self.enabled or not text or not text.strip():
            return None
        t0 = time.perf_counter()
        m = self._match(text)
        if m is not None and not self._allowed(m.intent):
            print(f"[instant] {m.intent} belongs to a switched-off plugin: not claimed")
            return None
        if m is not None:
            m.match_ms = (time.perf_counter() - t0) * 1000
        return m

    def _allowed(self, intent: str) -> bool:
        """A switched-off plugin's intents (heyclaude/plugins.py) don't claim requests:
        they go on to Claude as if there were no instant command for them."""
        try:
            from .. import plugins
            return plugins.get().intent_enabled(intent)
        except Exception as e:
            print(f"[instant] plugin check failed: {e}")
            return True

    def _other_langs(self, raw: str):
        """(module, lang) for the Russian / Uzbek grammars worth trying on this text."""
        if UZ.HAS_CYR.search(raw):
            return ((RU, "ru"), (UZ, "uz"))
        return ((UZ, "uz"),)

    def _match(self, text: str) -> Match | None:
        t = normalize(text)
        if not t:
            return None
        self._check_taught()
        ctx = self._ctx()
        raw = fix_slips(basic(text))
        for cand in dict.fromkeys((t, raw)):
            hit = G.parse(cand, ctx)
            if hit:
                return Match(hit[0], hit[1], text, cand, "grammar")
        for mod, lang in self._other_langs(raw):        # Russian, Uzbek (Latin or Cyrillic)
            hit = mod.parse(raw, ctx)
            if hit:
                return Match(hit[0], hit[1], text, mod.prep(raw), "grammar", extra={"lang": lang})
        if ctx.domain:
            hit = G.parse_context(t, ctx)
            for mod, lang in self._other_langs(raw):
                hit = hit or mod.parse_context(raw, ctx)
            if hit:
                m = self._resolve_context(hit[0], hit[1], text, t)
                if m is not None:
                    return m
        if t in self.taught:
            intent, sl = self.taught[t]
            return Match(intent, dict(sl), text, t, "taught")
        # The classifier only knows English: it never decides a Russian or Uzbek sentence.
        if self.clf is not None and self.opt("classifier") and language(t) == "en":
            self.clf.threshold = float(self.opt("threshold"))
            got = self.clf.predict(t, allowed=CLASSIFIER_LABELS)
            if got:
                label, score, ex = got
                intent, sl = _label_to_intent(label, int(self.opt("step")))
                return Match(intent, sl, text, t, "classifier", score=score, note=ex)
        return None

    def _resolve_context(self, kind: str, sl: dict, text: str, t: str) -> Match | None:
        """'a bit more' after 'volume up' -> volume.step(+5), etc. None when it doesn't apply."""
        last = self.last()
        if last is None:
            return None
        prev, res = last
        dom = prev.domain
        note = f"after {prev.intent}"

        def mk(intent, slots):
            return Match(intent, slots, text, t, "context", note=note)
        if kind in ("context.more", "context.less"):
            if dom not in ("volume", "brightness"):
                return None
            up = sl["direction"] == "up"
            if prev.intent == f"{dom}.step" and kind == "context.more" and prev.slots.get("delta", 0) < 0 \
                    and (sl.get("keep") or t.split()[-1] in ("more", "further", "going")):
                up = False                                 # "quieter" ... "a bit more" = quieter still
            amt = int(sl["amount"])
            return mk(f"{dom}.step", {"delta": amt if up else -amt})
        if kind == "context.set":
            if dom not in ("volume", "brightness"):
                return None
            return mk(f"{dom}.set", {"level": sl["level"]})
        if kind == "context.again":
            if prev.intent in ("volume.step", "brightness.step", "media.next", "media.previous"):
                return mk(prev.intent, dict(prev.slots))
            return None
        if kind == "context.reverse":
            if prev.intent in ("volume.step", "brightness.step"):
                return mk(prev.intent, {"delta": -2 * int(prev.slots.get("delta", 0))})
            if prev.intent == "media.next":
                return mk("media.previous", {"action": "previous"})
            if prev.intent == "media.previous":
                return mk("media.next", {"action": "next"})
            if prev.intent == "theme.set":
                cur = (res or {}).get("new") or prev.slots.get("mode")
                return mk("theme.set", {"mode": "light" if cur == "dark" else "dark"})
            if prev.intent in ("volume.mute", "volume.unmute"):
                return mk("volume.unmute" if prev.intent == "volume.mute" else "volume.mute", {})
            if prev.intent == "radio.set" and prev.slots.get("on") is not None:
                return mk("radio.set", {"radio": prev.slots["radio"], "on": not prev.slots["on"]})
            return None
        return None
