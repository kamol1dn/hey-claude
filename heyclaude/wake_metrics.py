"""Wake-word metrics shared by tools/wake_eval.py and the trainer
(heyclaude.wake_train): count wakes in a score stream, hit rates, and the
threshold sweep / recommendation. Pure numpy, tested offline.
"""
from __future__ import annotations

import numpy as np

THRESHOLDS = [round(t, 2) for t in np.arange(0.05, 0.96, 0.05)]
TARGET_HIT = 0.95
TARGET_FPH = 1 / 3                            # "fewer than 1 false wake per 3 hours"


def count_triggers(scores, threshold: float, refractory_frames: int) -> list[int]:
    """Frame indices where the app would wake: the score reaches `threshold`,
    then nothing counts again for `refractory_frames` (in the app a wake is
    followed by recording and a cooldown, so one long "hit" is one wake)."""
    scores = np.asarray(scores)
    out, next_ok = [], -1
    for i in np.flatnonzero(scores >= threshold):
        if i >= next_ok:
            out.append(int(i))
            next_ok = i + max(1, refractory_frames)
    return out


def hit_rate(peaks, threshold: float) -> float | None:
    """Share of positive clips whose peak score reaches the threshold."""
    peaks = np.asarray(peaks, dtype=float)
    return float(np.mean(peaks >= threshold)) if peaks.size else None


def per_hour(n: int, hours: float) -> float | None:
    return n / hours if hours > 0 else None


def sweep(peaks, neg_scores: list, neg_hours: float, refractory_frames: int,
          thresholds=THRESHOLDS, held_peaks=None) -> list[dict]:
    """One row per threshold: hit rates and false wakes (total and per hour)."""
    rows = []
    for t in thresholds:
        n = sum(len(count_triggers(s, t, refractory_frames)) for s in neg_scores) if neg_scores else None
        rows.append(dict(threshold=t, hit=hit_rate(peaks, t),
                         hit_held_out=hit_rate(held_peaks, t) if held_peaks is not None else None,
                         false=n, fph=per_hour(n, neg_hours) if n is not None else None))
    return rows


def recommend(rows: list[dict], min_hit: float = TARGET_HIT, max_fph: float = TARGET_FPH) -> dict:
    """Pick a threshold from a sweep.

    With both measurements: among thresholds with fewer than `max_fph` false
    wakes per hour, take those with the best hit rate and choose the middle
    one, for margin both ways. With only one kind of data the pick is
    tentative and leans one step toward safety.
    """
    has_pos = any(r["hit"] is not None for r in rows)
    has_neg = any(r["fph"] is not None for r in rows)
    if not rows or not (has_pos or has_neg):
        return dict(threshold=None, meets=False, why="nothing to measure")
    if has_pos and has_neg:
        ok = [r for r in rows if r["fph"] < max_fph]
        if not ok:
            r = min(rows, key=lambda r: (r["fph"], -r["threshold"]))
            return dict(threshold=r["threshold"], meets=False, hit=r["hit"], fph=r["fph"],
                        why=f"no threshold gets under {max_fph:.2f} false wakes/hour; the model needs "
                            f"more training (raise false_activation_penalty)")
        best = max(r["hit"] for r in ok)
        tied = [r for r in ok if r["hit"] == best]
        r = tied[(len(tied) - 1) // 2]
        meets = best >= min_hit
        why = ("both targets met" if meets else
               f"best hit rate without too many false wakes is {best:.0%} (< {min_hit:.0%}): "
               f"record more samples / retrain with more of your voice")
        return dict(threshold=r["threshold"], meets=meets, hit=r["hit"], fph=r["fph"], why=why,
                    range=[tied[0]["threshold"], tied[-1]["threshold"]])
    if has_pos:
        ok = [r for r in rows if r["hit"] >= min_hit]
        if not ok:
            r = max(rows, key=lambda r: (r["hit"], -r["threshold"]))
            return dict(threshold=r["threshold"], meets=False, hit=r["hit"], fph=None,
                        why=f"no threshold reaches a {min_hit:.0%} hit rate")
        r = ok[-2] if len(ok) > 1 else ok[-1]          # one step below the highest that still hears you
        return dict(threshold=r["threshold"], meets=False, hit=r["hit"], fph=None,
                    why="tentative: no negative audio given, so false wakes weren't measured")
    ok = [r for r in rows if r["fph"] < max_fph]
    if not ok:
        return dict(threshold=rows[-1]["threshold"], meets=False, hit=None, fph=rows[-1]["fph"],
                    why=f"no threshold gets under {max_fph:.2f} false wakes/hour")
    i = rows.index(ok[0])
    r = rows[min(i + 1, len(rows) - 1)]                 # one step above the lowest quiet-enough one
    return dict(threshold=r["threshold"], meets=False, hit=None, fph=r["fph"],
                why="tentative: no clips of you given, so the hit rate wasn't measured")
