"""Measure a wake-word model: hit rate on your own clips, false wakes per hour
on long audio that never says the wake word, and the threshold that balances
the two.

    .venv\\Scripts\\python tools\\wake_eval.py --negatives "D:\\lectures"
    .venv\\Scripts\\python tools\\wake_eval.py --negatives talk.mp4 --model models\\hey_claude.onnx --apply

Defaults: the model is `wake.model` from config.yaml (no silent fallback to
hey_jarvis here), positives are the clips in state/wake_samples/ (record them
with `python -m heyclaude --record-wake-samples`). Negatives can be any audio or
video file, or a folder of them (searched recursively); anything that isn't a
16 kHz mono wav is decoded with ffmpeg. Runs on the CPU (onnxruntime), like
the app, and never touches the GPU.

Targets (ROADMAP M2): hit rate >= 95% and fewer than 1 false wake per 3 hours.
A report is written to state/wake_eval/. --apply writes the recommended
threshold to config.yaml when both targets are met.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from heyclaude.listen import BLOCK, RATE, resolve_wake_model, wake_label  # noqa: E402

FRAME_S = BLOCK / RATE                        # one score per 80 ms
THRESHOLDS = [round(t, 2) for t in np.arange(0.05, 0.96, 0.05)]
TARGET_HIT = 0.95
TARGET_FPH = 1 / 3                            # "fewer than 1 false wake per 3 hours"
AUDIO_EXT = {".wav", ".mp3", ".m4a", ".mp4", ".mkv", ".mov", ".webm", ".flac", ".ogg", ".opus",
             ".aac", ".wma", ".avi", ".ts", ".m4v", ".mka", ".3gp"}


# -- the math (pure, tested offline) ----------------------------------------------
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


# -- audio ----------------------------------------------------------------------
def find_audio(paths: list[str]) -> list[Path]:
    out = []
    for p in paths:
        p = Path(p)
        if p.is_dir():
            out += sorted(q for q in p.rglob("*") if q.is_file() and q.suffix.lower() in AUDIO_EXT)
        elif p.is_file():
            out.append(p)
        else:
            raise SystemExit(f"not found: {p}")
    return out


def _is_plain_wav(path: Path) -> bool:
    try:
        with wave.open(str(path), "rb") as w:
            return (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (RATE, 1, 2)
    except Exception:
        return False


def probe_seconds(path: Path) -> float | None:
    """Duration in seconds (None if unknown)."""
    if _is_plain_wav(path):
        with wave.open(str(path), "rb") as w:
            return w.getnframes() / RATE
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    r = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        return float(r.stdout.strip())
    except ValueError:
        return None


def segments(total_s: float | None, seg_s: float = 600.0) -> list[tuple[float, float | None]]:
    """(start, length) pieces of a file, scored in parallel. Unknown length = one piece."""
    if not total_s:
        return [(0.0, None)]
    out, t = [], 0.0
    while t < total_s - 0.5:
        out.append((t, min(seg_s, total_s - t)))
        t += seg_s
    return out or [(0.0, None)]


def stream_audio(path: Path, start: float = 0.0, dur: float | None = None, chunk_frames: int = 750):
    """Yield int16 chunks (16 kHz mono) of any audio/video file, ~60 s at a time,
    optionally only `dur` seconds from `start`."""
    path = Path(path)
    n = BLOCK * chunk_frames
    if _is_plain_wav(path):
        with wave.open(str(path), "rb") as w:
            w.setpos(min(w.getnframes(), int(start * RATE)))
            left = int(dur * RATE) if dur is not None else None
            while left is None or left > 0:
                b = w.readframes(n if left is None else min(n, left))
                if not b:
                    return
                if left is not None:
                    left -= len(b) // 2
                yield np.frombuffer(b, dtype=np.int16)
        return
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise SystemExit(f"{path.name}: needs ffmpeg on PATH to decode (or convert it to a 16 kHz mono wav)")
    cut = (["-ss", f"{start:.3f}"] if start else []) + ["-i", str(path)] + (["-t", f"{dur:.3f}"] if dur else [])
    p = subprocess.Popen([ffmpeg, "-nostdin", "-v", "error", *cut, "-vn", "-ac", "1",
                          "-ar", str(RATE), "-f", "s16le", "-"], stdout=subprocess.PIPE,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        while True:
            b = p.stdout.read(n * 2)
            if not b:
                break
            yield np.frombuffer(b[: len(b) // 2 * 2], dtype=np.int16)
    finally:
        p.stdout.close()
        p.wait()


# -- scoring --------------------------------------------------------------------
def load_model(spec: str):
    from openwakeword.model import Model
    path, why = resolve_wake_model(spec)
    if why:
        raise SystemExit(f"wake model: {why}. Put the trained model there or pass --model "
                         f"(docs/train-hey-claude.md). wake_eval doesn't fall back to hey_jarvis.")
    if not path.endswith(".onnx"):
        import openwakeword
        try:
            openwakeword.utils.download_models([path])
        except Exception:
            pass
    m = Model(wakeword_models=[path], inference_framework="onnx")
    return m, next(iter(m.models)), path


def score_stream(model, name: str, chunks, limit_frames: int | None = None):
    """Frame scores for a stream of int16 chunks, fed 80 ms at a time like the app."""
    model.reset()
    scores, carry = [], np.zeros(0, np.int16)
    for c in chunks:
        buf = np.concatenate([carry, c]) if carry.size else c
        k = len(buf) // BLOCK
        for i in range(k):
            scores.append(model.predict(buf[i * BLOCK:(i + 1) * BLOCK])[name])
        carry = buf[k * BLOCK:]
        if limit_frames and len(scores) >= limit_frames:
            break
    return np.asarray(scores[:limit_frames] if limit_frames else scores, dtype=np.float32)


def clip_peak(model, name: str, pcm: np.ndarray, lead_s: float = 1.0, tail_s: float = 1.5) -> float:
    """Highest score over one clip, padded with silence (the model needs ~1 s of context)."""
    pad = lambda s: np.zeros(int(s * RATE), np.int16)  # noqa: E731
    return float(score_stream(model, name, [np.concatenate([pad(lead_s), pcm, pad(tail_s)])]).max())


_WORKER: dict = {}


def _init_worker(spec: str):
    _WORKER["m"], _WORKER["n"], _ = load_model(spec)


def _score_job(job):
    """(file, start, length) -> frame scores, or an error string. Runs in a worker process."""
    f, start, dur = job
    try:
        return score_stream(_WORKER["m"], _WORKER["n"], stream_audio(Path(f), start, dur))
    except SystemExit as e:
        return str(e)


def score_negatives(spec: str, files: list[Path], jobs: int, max_hours: float | None = None, say=print):
    """Score every file, split into 10-minute pieces, `jobs` processes at once.
    Returns (files that had audio, their frame scores)."""
    work = []
    budget = max_hours * 3600 if max_hours else None
    for i, f in enumerate(files):
        for start, dur in segments(probe_seconds(f)):
            if budget is not None:
                if budget <= 0:
                    break
                dur = min(dur, budget) if dur else budget
                budget -= dur
            work.append((i, str(f), start, dur))
    parts: dict[int, list] = {}
    t0, done_s = time.time(), 0.0

    def add(w, s, k):
        nonlocal done_s
        parts.setdefault(w[0], []).append(s)
        if not isinstance(s, str):
            done_s += len(s) * FRAME_S
        say(f"  {k}/{len(work)} pieces, {fmt_t(done_s)} of audio, "
            f"{done_s / max(0.01, time.time() - t0):.0f}x real time")

    if jobs > 1 and len(work) > 1:
        import multiprocessing as mp
        with mp.get_context("spawn").Pool(min(jobs, len(work)), initializer=_init_worker,
                                          initargs=(spec,)) as pool:
            for k, (w, s) in enumerate(zip(work, pool.imap(_score_job, [w[1:] for w in work])), 1):
                add(w, s, k)
    else:
        _init_worker(spec)
        for k, w in enumerate(work, 1):
            add(w, _score_job(w[1:]), k)
    out_files, out_scores = [], []
    for i, f in enumerate(files):
        ps = parts.get(i, [])
        errs = [p for p in ps if isinstance(p, str)]
        if errs:
            say(f"  skip {f.name}: {errs[0]}")
            continue
        s = np.concatenate(ps) if ps else np.zeros(0, np.float32)
        if not s.size:
            if ps:
                say(f"  skip {f.name}: no audio")
            continue
        out_files.append(f)
        out_scores.append(s)
        say(f"  {f.name}: {fmt_t(len(s) * FRAME_S)}, peak score {s.max():.2f}")
    return out_files, out_scores


def fmt_t(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


def pct(x):
    return "   -  " if x is None else f"{x * 100:5.1f}%"


def main(argv=None):
    from heyclaude import config
    from heyclaude.wake_samples import SAMPLES_DIR, is_held_out, list_clips, read_wav

    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--model", help="wake model (.onnx path or stock name); default: wake.model in config.yaml")
    ap.add_argument("--positives", nargs="*", help="clips/folders of you saying it (default: state/wake_samples)")
    ap.add_argument("--negatives", nargs="*", default=[], help="audio/video files or folders without the wake word")
    ap.add_argument("--threshold", type=float, help="also report this threshold (default: wake.threshold)")
    ap.add_argument("--refractory", type=float, default=2.0, help="seconds after a false wake before another counts")
    ap.add_argument("--max-hours", type=float, help="stop reading negatives after this many hours")
    ap.add_argument("--jobs", type=int, default=max(1, min(6, (os.cpu_count() or 2) // 3)),
                    help="CPU processes for the negatives (default: a third of the cores, up to 6)")
    ap.add_argument("--min-hit", type=float, default=TARGET_HIT)
    ap.add_argument("--max-fph", type=float, default=TARGET_FPH, help="false wakes per hour must be below this")
    ap.add_argument("--apply", action="store_true", help="write the recommended threshold to config.yaml if both targets are met")
    ap.add_argument("--config", help="config file (default: config.yaml)")
    args = ap.parse_args(argv)

    cfg = config.load(args.config)
    spec = args.model or cfg["wake"]["model"]
    model, name, path = load_model(spec)
    shown_thr = args.threshold if args.threshold is not None else float(cfg["wake"]["threshold"])
    print(f"model: {wake_label(name)}  ({path})")

    # positives
    if args.positives is None:
        pos_files = list_clips(SAMPLES_DIR)
    else:
        pos_files = []
        for p in args.positives:
            p = Path(p)
            pos_files += sorted(p.glob("*.wav")) if p.is_dir() else [p]
    peaks, held = [], []
    clips = []
    for f in pos_files:
        try:
            pcm = read_wav(f)
        except Exception as e:
            print(f"  skip {f.name}: {e}")
            continue
        pk = clip_peak(model, name, pcm)
        peaks.append(pk)
        h = is_held_out(f) if args.positives is None else False
        if h:
            held.append(pk)
        clips.append(dict(file=str(f), peak=round(pk, 4), held_out=h))
    if pos_files:
        print(f"positives: {len(peaks)} clips" + (f" ({len(held)} held out from training)" if held else ""))
    else:
        print("positives: none (record some: python -m heyclaude --record-wake-samples)")

    # negatives
    files = find_audio(args.negatives) if args.negatives else []
    t0 = time.time()
    if files:
        print(f"negatives: {len(files)} file(s), scoring with {args.jobs} process(es)...")
    neg_files, neg_scores = score_negatives(spec, files, args.jobs, args.max_hours) if files else ([], [])
    hours = sum(len(s) for s in neg_scores) * FRAME_S / 3600
    if files:
        print(f"negatives: {len(neg_files)} file(s), {hours:.2f} h, scored in {fmt_t(time.time() - t0)}")
        if hours < 3:
            print(f"  note: {hours:.1f} h is short for a '< 1 per 3 hours' target; 3+ hours gives a real number")

    refr = max(1, round(args.refractory / FRAME_S))
    thresholds = sorted(set(THRESHOLDS) | {round(shown_thr, 2)})
    rows = sweep(peaks, neg_scores, hours, refr, thresholds, held_peaks=held if held else None)
    rec = recommend(rows, args.min_hit, args.max_fph)

    print()
    print("threshold   hit (all)   hit (held out)   false wakes   per hour")
    for r in rows:
        mark = " <- recommended" if r["threshold"] == rec.get("threshold") else ""
        mark += " (current)" if r["threshold"] == round(shown_thr, 2) else ""
        f = "-" if r["false"] is None else str(r["false"])
        ph = "-" if r["fph"] is None else f"{r['fph']:.2f}"
        print(f"  {r['threshold']:.2f}       {pct(r['hit'])}      {pct(r['hit_held_out'])}         "
              f"{f:>6}      {ph:>6}{mark}")

    print()
    if rec["threshold"] is None:
        print("Nothing measured. Give --negatives and/or record samples.")
    else:
        print(f"Recommended threshold: {rec['threshold']:.2f}  ({rec['why']})")
        if rec.get("range") and rec["range"][0] != rec["range"][1]:
            print(f"  anything from {rec['range'][0]:.2f} to {rec['range'][1]:.2f} scores the same")
        if peaks:
            misses = [c for c in clips if c["peak"] < rec["threshold"]]
            for c in misses[:10]:
                print(f"  missed: {Path(c['file']).name} (peak {c['peak']:.2f})")
        if neg_scores:
            n = 0
            for f, s in zip(neg_files, neg_scores):
                for i in count_triggers(s, rec["threshold"], refr):
                    if n == 0:
                        print("  false wakes at that threshold (worth a listen):")
                    if n < 15:
                        print(f"    {f.name} @ {fmt_t(i * FRAME_S)} (score {s[i]:.2f})")
                    n += 1

    # report
    out_dir = config.STATE_DIR / "wake_eval"
    out_dir.mkdir(parents=True, exist_ok=True)
    report = out_dir / time.strftime("report-%Y%m%d-%H%M%S.json")
    false_at = {}
    if rec.get("threshold") is not None:
        for f, s in zip(neg_files, neg_scores):
            hits = [round(i * FRAME_S, 2) for i in count_triggers(s, rec["threshold"], refr)]
            if hits:
                false_at[str(f)] = hits
    report.write_text(json.dumps(dict(
        when=time.strftime("%Y-%m-%d %H:%M:%S"), model=path, negatives_hours=round(hours, 3),
        negative_files=[str(f) for f in neg_files], refractory_s=args.refractory,
        targets=dict(min_hit=args.min_hit, max_fph=args.max_fph),
        recommendation=rec, sweep=rows, clips=clips, false_wakes_s=false_at,
    ), indent=2), encoding="utf-8")
    print(f"\nreport: {report}")

    if args.apply:
        if not (rec.get("meets") and peaks and neg_scores):
            print("--apply: not applied (needs your clips, negative audio, and both targets met)")
            return 1
        user = config.ensure_user_config(args.config)
        new = config.load(args.config)
        new["wake"]["threshold"] = float(rec["threshold"])
        if args.model:
            new["wake"]["model"] = args.model
        config.save(new, user)
        print(f"--apply: wake.threshold = {rec['threshold']:.2f} written to {user}; restart hey-claude to use it")
    return 0 if rec.get("meets") else 2


if __name__ == "__main__":
    raise SystemExit(main())
