"""Compare wake-word models on the same held-out data: the held-out synthetic
positives of a default training run (by source: Kokoro voices, Piper
speakers) and openWakeWord's ~10.7 h validation set (false wakes per hour).

    .venv\\Scripts\\python tools\\wake_compare.py dist\\wake-model-v1\\hey_claude.onnx models\\hey_claude.onnx
    .venv\\Scripts\\python tools\\wake_compare.py a.onnx b.onnx --features state\\wake_train\\cache\\features-xxxx.npz

`--features` is the features cache a default run leaves in
state/wake_train/cache (the newest one by default); its "ev" windows were
never trained on by that run. A model trained on a *different* plan may have
seen some of those voices, so compare models on the held-out set of the newer
run (whose held-out voices are a superset). Scores are taken like the
trainer's check stage: a clip's score is the best over four end offsets, a
hit is >= the threshold (0.5, the calibrated default), and false wakes use a
2 s refractory. Runs on the CPU.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from heyclaude import wake_metrics as WM  # noqa: E402
from heyclaude.wake_train.features import stream_to_windows  # noqa: E402

REFRACTORY_FRAMES = 25
FRAME_S = 0.08


class OnnxModel:
    def __init__(self, path: Path):
        import onnxruntime as ort
        so = ort.SessionOptions()
        so.log_severity_level = 3
        self.sess = ort.InferenceSession(str(path), sess_options=so, providers=["CPUExecutionProvider"])
        self.inp = self.sess.get_inputs()[0].name

    def __call__(self, x: np.ndarray, batch: int = 4096) -> np.ndarray:
        x = np.asarray(x, np.float32)
        out = [self.sess.run(None, {self.inp: x[i:i + batch]})[0].reshape(-1) for i in range(0, len(x), batch)]
        return np.concatenate(out) if out else np.zeros(0, np.float32)


def held_out_hits(model, ev: np.ndarray, src: list[str], threshold: float) -> dict:
    """{source: {n, hit}} over (k, 4, 16, 96) eval windows."""
    s = model(ev.reshape(-1, 16, 96)).reshape(-1, 4).max(1) if len(ev) else np.zeros(0)
    out = {}
    for name in sorted(set(src)):
        sel = np.array([x == name for x in src])
        out[name] = dict(n=int(sel.sum()), hit=round(float(np.mean(s[sel] >= threshold)), 4))
    out["all"] = dict(n=len(s), hit=round(float(np.mean(s >= threshold)), 4) if len(s) else None)
    return out


def false_wakes(model, frames: np.ndarray, threshold: float, chunk: int = 40000) -> dict:
    n = frames.shape[0]
    scores = [model(stream_to_windows(np.asarray(frames[i:i + chunk + 15], np.float32)))
              for i in range(0, n - 15, chunk)]
    s = np.concatenate(scores)
    hours = n * FRAME_S / 3600
    k = len(WM.count_triggers(s, threshold, REFRACTORY_FRAMES))
    return dict(hours=round(hours, 2), false_wakes=k, fph=round(k / hours, 3))


def newest_features(work: Path) -> Path:
    c = sorted((work / "cache").glob("features-*.npz"), key=lambda p: p.stat().st_mtime)
    if not c:
        raise SystemExit(f"no features cache in {work / 'cache'}: run python -m heyclaude.wake_train default first")
    return c[-1]


def main(argv=None) -> int:
    from heyclaude.config import STATE_DIR
    from heyclaude.wake_train import download as D
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("models", nargs="+", type=Path)
    ap.add_argument("--features", type=Path, help="a default run's features cache (default: the newest)")
    ap.add_argument("--work", type=Path, default=STATE_DIR / "wake_train", help="trainer folder (default state/wake_train)")
    ap.add_argument("--threshold", type=float, default=0.5)
    ap.add_argument("--json", type=Path, help="also write the results here")
    args = ap.parse_args(argv)

    feats = args.features or newest_features(args.work)
    z = np.load(feats, allow_pickle=False)
    ev, src = z["ev"], [str(x) for x in z["eval_src"]]
    frames = D.load_validation(args.work / "features" / D.VAL_FILE)
    print(f"held-out windows: {feats.name} ({len(src)} clips: "
          + ", ".join(f"{s} {src.count(s)}" for s in sorted(set(src))) + ")")
    results = {}
    for m in args.models:
        model = OnnxModel(m)
        r = dict(held_out=held_out_hits(model, ev, src, args.threshold), validation=false_wakes(model, frames, args.threshold))
        results[str(m)] = r
        hits = "  ".join(f"{k} {v['hit'] * 100:.1f}% (n={v['n']})" for k, v in r["held_out"].items())
        v = r["validation"]
        print(f"{m}\n  hit at {args.threshold}: {hits}\n  false wakes: {v['false_wakes']} in {v['hours']} h "
              f"({v['fph']:.2f}/h)")
    if args.json:
        args.json.write_text(json.dumps(dict(features=str(feats), threshold=args.threshold, results=results),
                                        indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
