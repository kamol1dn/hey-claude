"""The second layer: a tiny nearest-neighbour classifier for paraphrases the
grammar misses ("it's way too loud in here", "my ears hurt", "I'm done with
this song").

numpy only (CI has no scikit-learn): word unigrams + bigrams and character
3-grams, TF-IDF weighted, L2-normalised; cosine similarity against every
example phrasing (examples.yaml, plus the ones you taught it in
state/intents_taught.yaml). Trained at load time in a few milliseconds.

It is deliberately timid. A label is only returned when
- the best example is similar enough (the threshold) and clearly ahead of
  the best example of any other label (the margin),
- the best label is not "none" (the decoys: near-misses that look like commands),
- the utterance is short, not a question, has no negation, and contains a
  word of that intent's domain (an anchor: "loud", "song", "screen"...).
Anything else returns None and the request goes to Claude.
"""
from __future__ import annotations

import math
import re
from collections import Counter

import numpy as np

# The words an utterance needs for a label (no anchor -> no match, however similar).
ANCHORS = {
    "volume.up": r"volume|sound|audio|loud|louder|quiet|quieter|soft|softer|hear|speakers?|music|ears|tunes|crank|blast",
    "volume.down": r"volume|sound|audio|loud|louder|quiet|quieter|soft|softer|hear|speakers?|music|ears|deafening|"
                   r"blasting|tunes",
    "volume.mute": r"mute|silence|silent|sound|audio|volume|speakers?",
    "volume.unmute": r"unmute|sound|audio|volume|speakers?|mute",
    "brightness.up": r"bright|brighter|brightness|screen|display|monitor|backlight|dim|dark|see",
    "brightness.down": r"bright|brighter|brightness|screen|display|monitor|backlight|dim|dimmer|glare|eyes|blinding",
    "theme.dark": r"dark|night|black",
    "theme.light": r"light|white|bright",
    "media.next": r"song|track|tune|skip|next|music|playlist|this one",
    "media.previous": r"song|track|tune|previous|back|before|last|again",
    "media.pause": r"pause|music|song|playback|track|podcast|video",
    "media.play": r"play|resume|unpause|music|song|playback|track|podcast|video",
    "media.now_playing": r"song|track|playing|listening|music|tune|artist|band|singer",
    "timer.cancel": r"timer|countdown",
}
QUESTION = re.compile(r"^(?:what|why|how|who|whom|whose|when|where|which|is|are|was|were|does|do|did|should|"
                      r"shall|may|might|tell me about|explain|describe|define|can (?!you)|could (?!you))\b")
NEGATION = re.compile(r"\b(?:not|never|no|nothing|nobody|cannot|dont|without|neither|nor|stop asking)\b")
ALLOW_QUESTION = {"media.now_playing"}
MAX_WORDS = 12


def _feats(t: str) -> Counter:
    words = t.split()
    c = Counter()
    for w in words:
        c["w:" + w] += 1
        p = f"<{w}>"
        for i in range(len(p) - 2):
            c["c:" + p[i:i + 3]] += 0.5
    for a, b in zip(words, words[1:]):
        c[f"b:{a} {b}"] += 1
    return c


class Classifier:
    def __init__(self, examples: list[tuple[str, str]], threshold: float = 0.62, margin: float = 0.06):
        self.threshold, self.margin = float(threshold), float(margin)
        self.fit(examples)

    def fit(self, examples: list[tuple[str, str]]):
        from .normalize import normalize
        rows = []
        for text, label in examples:
            t = normalize(text)
            if t:
                rows.append((t, label))
        self.texts = [t for t, _ in rows]
        self.labels = [lb for _, lb in rows]
        feats = [_feats(t) for t in self.texts]
        df = Counter()
        for f in feats:
            df.update(set(f))
        self.vocab = {k: i for i, k in enumerate(sorted(df))}
        n = max(1, len(feats))
        self.idf = np.array([math.log((1 + n) / (1 + df[k])) + 1.0 for k in sorted(df)], np.float32)
        X = np.zeros((len(feats), len(self.vocab)), np.float32)
        for r, f in enumerate(feats):
            for k, v in f.items():
                X[r, self.vocab[k]] = (1 + math.log(v)) if v >= 1 else v
        X *= self.idf
        X /= np.maximum(np.linalg.norm(X, axis=1, keepdims=True), 1e-9)
        self.X = X
        self.label_set = sorted(set(self.labels))
        self._lab = np.array([self.label_set.index(lb) for lb in self.labels], np.int32)

    def vector(self, t: str) -> np.ndarray:
        v = np.zeros(len(self.vocab), np.float32)
        for k, c in _feats(t).items():
            i = self.vocab.get(k)
            if i is not None:
                v[i] = (1 + math.log(c)) if c >= 1 else c
        v *= self.idf
        nrm = float(np.linalg.norm(v))
        return v / nrm if nrm > 0 else v

    def scores(self, t: str) -> list[tuple[str, float, str]]:
        """[(label, best similarity, the example it came from)], best first."""
        if not len(self.texts):
            return []
        sims = self.X @ self.vector(t)
        best: dict[str, tuple[float, int]] = {}
        for i in np.argsort(-sims)[:40]:
            lb = self.labels[i]
            if lb not in best:
                best[lb] = (float(sims[i]), int(i))
        return sorted(((lb, s, self.texts[i]) for lb, (s, i) in best.items()), key=lambda x: -x[1])

    def predict(self, t: str, allowed: set | None = None) -> tuple[str, float, str] | None:
        """(label, score, nearest example) or None when unsure. `t` is normalized text."""
        words = t.split()
        if not words or len(words) > MAX_WORDS:
            return None
        sc = self.scores(t)
        if not sc:
            return None
        label, s, ex = sc[0]
        second = sc[1][1] if len(sc) > 1 else 0.0
        if label == "none" or s < self.threshold or s - second < self.margin:
            return None
        if allowed is not None and label not in allowed:
            return None
        if QUESTION.match(t) and label not in ALLOW_QUESTION:
            return None
        if NEGATION.search(t):
            return None
        anchor = ANCHORS.get(label)
        if anchor and not re.search(r"\b(?:" + anchor + r")\b", t):
            return None
        return label, s, ex
