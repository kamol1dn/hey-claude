"""The classifier head: a small MLP on openWakeWord embeddings, in plain numpy.

    (16, 96) -> flatten 1536 -> Dense(H) -> LayerNorm -> ReLU
             -> Dense(H) -> LayerNorm -> ReLU -> Dense(1) -> sigmoid

The same shape as openWakeWord's own default model, so the export looks like
theirs. ~100k weights train in seconds to a few minutes on the CPU, so this
needs neither torch nor a GPU (the GPU, when it's free, goes to the heavy part:
synthesis and the feature models).

Training samples balanced batches from several pools (positives, ACAV100M
negatives, synthetic look-alikes, the user's room sound and "that wasn't me"
clips), each with its own share of the batch and loss weight. The negative
weight ramps up over the first half of training (openWakeWord's "false
activation penalty"), and from a third of the way in, part of each negative
batch is drawn from the negatives the model currently scores highest
(hard-negative mining), which is what pushes false wakes per hour down.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

import numpy as np

EPS = 1e-5


@dataclass
class Pool:
    name: str
    x: np.ndarray            # (n, 16, 96) float16/float32
    label: int               # 1 positive, 0 negative
    share: float             # share of its class's part of each batch
    weight: float = 1.0      # loss weight multiplier


@dataclass
class HeadParams:
    W1: np.ndarray
    b1: np.ndarray
    g1: np.ndarray
    be1: np.ndarray
    W2: np.ndarray
    b2: np.ndarray
    g2: np.ndarray
    be2: np.ndarray
    W3: np.ndarray
    b3: np.ndarray
    meta: dict = field(default_factory=dict)

    NAMES = ("W1", "b1", "g1", "be1", "W2", "b2", "g2", "be2", "W3", "b3")

    def arrays(self) -> dict:
        return {k: getattr(self, k) for k in self.NAMES}

    @classmethod
    def init(cls, rng: np.random.Generator, d_in: int = 1536, hidden: int = 64) -> "HeadParams":
        he = lambda a, b: (rng.standard_normal((a, b)) * math.sqrt(2.0 / a)).astype(np.float32)  # noqa: E731
        z = lambda n: np.zeros(n, np.float32)  # noqa: E731
        o = lambda n: np.ones(n, np.float32)  # noqa: E731
        return cls(he(d_in, hidden), z(hidden), o(hidden), z(hidden), he(hidden, hidden), z(hidden), o(hidden),
                   z(hidden), (rng.standard_normal((hidden, 1)) * 0.01).astype(np.float32), z(1))


def _ln(z, g, b):
    mu = z.mean(1, keepdims=True)
    var = z.var(1, keepdims=True)
    inv = 1.0 / np.sqrt(var + EPS)
    n = (z - mu) * inv
    return n * g + b, n, inv


def _ln_back(da, n, inv, g):
    dn = da * g
    dz = inv * (dn - dn.mean(1, keepdims=True) - n * (dn * n).mean(1, keepdims=True))
    return dz, (da * n).sum(0), da.sum(0)


def logits(p: HeadParams, x: np.ndarray, batch: int = 8192) -> np.ndarray:
    """(n, 16, 96) -> (n,) logits."""
    x = np.asarray(x)
    out = np.empty(x.shape[0], np.float32)
    for i in range(0, x.shape[0], batch):
        xb = np.asarray(x[i:i + batch], np.float32).reshape(-1, p.W1.shape[0])
        a1, _, _ = _ln(xb @ p.W1 + p.b1, p.g1, p.be1)
        a2, _, _ = _ln(np.maximum(a1, 0) @ p.W2 + p.b2, p.g2, p.be2)
        out[i:i + batch] = (np.maximum(a2, 0) @ p.W3 + p.b3)[:, 0]
    return out


def predict(p: HeadParams, x: np.ndarray) -> np.ndarray:
    """(n, 16, 96) -> (n,) scores 0..1, what the exported ONNX model returns."""
    return 1.0 / (1.0 + np.exp(-np.clip(logits(p, x), -40, 40)))


def _step(p: HeadParams, x, y, w):
    """Loss and gradients for one batch (weighted binary cross-entropy)."""
    z1 = x @ p.W1 + p.b1
    a1, n1, inv1 = _ln(z1, p.g1, p.be1)
    r1 = np.maximum(a1, 0)
    z2 = r1 @ p.W2 + p.b2
    a2, n2, inv2 = _ln(z2, p.g2, p.be2)
    r2 = np.maximum(a2, 0)
    lg = (r2 @ p.W3 + p.b3)[:, 0]
    sw = w.sum()
    loss = float(np.sum(w * (np.logaddexp(0, lg) - y * lg)) / sw)
    d = (w * (1 / (1 + np.exp(-lg)) - y) / sw).astype(np.float32)[:, None]
    g = {"W3": r2.T @ d, "b3": d.sum(0)}
    da2 = (d @ p.W3.T) * (a2 > 0)
    dz2, g["g2"], g["be2"] = _ln_back(da2, n2, inv2, p.g2)
    g["W2"], g["b2"] = r1.T @ dz2, dz2.sum(0)
    da1 = (dz2 @ p.W2.T) * (a1 > 0)
    dz1, g["g1"], g["be1"] = _ln_back(da1, n1, inv1, p.g1)
    g["W1"], g["b1"] = x.T @ dz1, dz1.sum(0)
    return loss, g


def train(pools: list[Pool], steps: int = 6000, batch: int = 512, pos_frac: float = 0.25, lr: float = 2e-3,
          neg_weight=(1.0, 8.0), hard_frac: float = 0.5, hidden: int = 64, seed: int = 0, weight_decay=1e-4,
          progress=None, cancel=None, log=print) -> HeadParams:
    """Train a head. progress(step, steps, loss)."""
    rng = np.random.default_rng(seed)
    pos = [q for q in pools if q.label == 1 and len(q.x)]
    neg = [q for q in pools if q.label == 0 and len(q.x)]
    if not pos or not neg:
        raise ValueError("need at least one positive and one negative pool")
    d_in = int(np.prod(pos[0].x.shape[1:]))
    p = HeadParams.init(rng, d_in, hidden)
    m = {k: np.zeros_like(v) for k, v in p.arrays().items()}
    v2 = {k: np.zeros_like(v) for k, v in p.arrays().items()}
    n_pos = max(1, int(batch * pos_frac))
    n_neg = batch - n_pos
    hard: dict[str, np.ndarray] = {}                   # pool name -> indices of its hardest negatives
    t0, avg = time.time(), None

    def draw(qs, n, use_hard):
        shares = np.array([q.share for q in qs], float)
        counts = rng.multinomial(n, shares / shares.sum())
        xs, ws = [], []
        for q, c in zip(qs, counts):
            if c == 0:
                continue
            h = hard.get(q.name) if use_hard else None
            if h is not None and len(h):
                kh = int(c * hard_frac)
                idx = np.concatenate([rng.choice(h, kh), rng.integers(0, len(q.x), c - kh)])
            else:
                idx = rng.integers(0, len(q.x), c)
            xs.append(np.asarray(q.x[np.sort(idx)], np.float32).reshape(c, -1))
            ws.append(np.full(c, q.weight, np.float32))
        return np.concatenate(xs), np.concatenate(ws)

    for step in range(1, steps + 1):
        if cancel and cancel():
            raise InterruptedError("cancelled")
        frac = step / steps
        if hard_frac > 0 and step in (steps // 3, steps // 2, (2 * steps) // 3, (5 * steps) // 6):
            for q in neg:                               # refresh the hardest negatives
                sub = rng.choice(len(q.x), min(len(q.x), 60000), replace=False)
                s = logits(p, q.x[np.sort(sub)])
                k = max(1, len(sub) // 50)              # top 2%
                hard[q.name] = np.sort(sub)[np.argpartition(-s, k - 1)[:k]]
        xp, wp = draw(pos, n_pos, False)
        xn, wn = draw(neg, n_neg, step >= steps // 3)
        nw = neg_weight[0] + (neg_weight[1] - neg_weight[0]) * min(1.0, frac * 2)
        x = np.concatenate([xp, xn])
        y = np.concatenate([np.ones(len(xp), np.float32), np.zeros(len(xn), np.float32)])
        w = np.concatenate([wp, wn * nw])
        loss, g = _step(p, x, y, w)
        avg = loss if avg is None else 0.98 * avg + 0.02 * loss
        a = lr * 0.5 * (1 + math.cos(math.pi * frac))   # cosine decay
        for k, val in p.arrays().items():
            gk = g[k] + (weight_decay * val if k.startswith("W") else 0)
            m[k] = 0.9 * m[k] + 0.1 * gk
            v2[k] = 0.999 * v2[k] + 0.001 * gk * gk
            mh = m[k] / (1 - 0.9 ** step)
            vh = v2[k] / (1 - 0.999 ** step)
            val -= (a * mh / (np.sqrt(vh) + 1e-8)).astype(np.float32)
        if progress and (step % 50 == 0 or step == steps):
            progress(step, steps, avg)
        if log and step % max(1, steps // 10) == 0:
            log(f"[head] step {step}/{steps} loss {avg:.4f} ({time.time() - t0:.0f}s)")
    p.meta.update(steps=steps, hidden=hidden, seed=seed)
    return p


def calibrate(p: HeadParams, threshold: float) -> HeadParams:
    """Shift the output bias so that a score of `threshold` becomes 0.5, so the
    app's default threshold (0.5) is the chosen one."""
    t = min(max(float(threshold), 1e-4), 1 - 1e-4)
    p.b3 = (p.b3 - math.log(t / (1 - t))).astype(np.float32)
    p.meta["calibrated_from"] = round(t, 4)
    return p


def save(p: HeadParams, path):
    import json
    np.savez(path, **p.arrays(), meta=json.dumps(p.meta))


def load(path) -> HeadParams:
    import json
    z = np.load(path)
    return HeadParams(**{k: z[k] for k in HeadParams.NAMES}, meta=json.loads(str(z["meta"])))
