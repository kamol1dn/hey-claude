"""Train a wake-word model locally.

    python -m heyclaude.wake_train default                 # M2a: the shared "hey claude" model, synthetic only
    python -m heyclaude.wake_train personal --clips state\\wake_samples --room state\\wake_room\\room.wav
    python -m heyclaude.wake_train personal --phrase "hey computer" --out state\\wake_models\\x\\hey_computer.onnx

--json prints one JSON object per line ({"progress": ...}, then {"done": ...}
or {"error": ...}); the Settings wizard runs it that way in a subprocess.
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m heyclaude.wake_train", description=__doc__.split("\n\n")[0])
    ap.add_argument("mode", choices=["default", "personal"])
    ap.add_argument("--phrase", default="hey claude")
    ap.add_argument("--out", help="model path (default: models/hey_claude.onnx for default, "
                                  "state/wake_models/<phrase>-<time>/<phrase>.onnx for personal)")
    ap.add_argument("--clips", nargs="*", default=[], help="personal: wavs or folders of your clips")
    ap.add_argument("--room", nargs="*", default=[], help="personal: room recordings (negatives)")
    ap.add_argument("--false-wakes", nargs="*", default=[], help="personal: \"that wasn't me\" clips or folders")
    ap.add_argument("--device", choices=["auto", "cuda", "cpu"], default="auto")
    ap.add_argument("--gpu-util", type=float, help="GPU load as hey-claude measured it (its own work excluded)")
    ap.add_argument("--size", action="append", default=[], metavar="KEY=N",
                    help="override a size (n_pos, n_look, acav, steps...)")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--work", help="cache/download folder (default state/wake_train)")
    ap.add_argument("--json", action="store_true", help="machine-readable progress on stdout")
    ap.add_argument("--config", help="config file (for the Kokoro paths)")
    args = ap.parse_args(argv)

    from .. import config
    from .pipeline import Job, run

    def files(items):
        out = []
        for p in items:
            p = Path(p)
            out += sorted(str(q) for q in p.glob("*.wav")) if p.is_dir() else [str(p)]
        return out

    cfg = config.load(args.config)
    slug = "_".join(args.phrase.lower().replace(",", " ").split()) or "wake"
    out = args.out
    if not out:
        if args.mode == "default":
            out = "models/hey_claude.onnx"
        else:
            import time
            out = str(config.STATE_DIR / "wake_models" / f"{slug}-{time.strftime('%Y%m%d-%H%M%S')}" / f"{slug}.onnx")
    sizes = {}
    for kv in args.size:
        k, v = kv.split("=", 1)
        sizes[k.strip()] = int(v)
    clips = files(args.clips)
    if args.mode == "personal" and not clips:
        from ..wake_samples import SAMPLES_DIR, list_clips
        clips = [str(p) for p in list_clips(SAMPLES_DIR)]
    job = Job(mode=args.mode, phrase=args.phrase, out=out, work=args.work or "", device=args.device,
              gpu_util=args.gpu_util, seed=args.seed, user_clips=clips if args.mode == "personal" else [],
              room=files(args.room), false_wakes=files(args.false_wakes), sizes=sizes, tts=cfg.get("tts", {}))

    def emit(obj):
        print(json.dumps(obj), flush=True)

    last = [0.0]

    def on_progress(d):
        import time
        if args.json:
            emit({"progress": d})
        elif time.monotonic() - last[0] > 2:
            last[0] = time.monotonic()
            print(f"  {d['overall'] * 100:5.1f}%  eta {d['eta_s'] / 60:4.1f} min  {d['msg']}", flush=True)

    log = (lambda m: emit({"log": m})) if args.json else print
    try:
        report = run(job, on_progress, log=log)
    except InterruptedError:
        emit({"error": "cancelled"}) if args.json else print("cancelled")
        return 130
    except Exception as e:
        if args.json:
            emit({"error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()[-2000:]})
        else:
            traceback.print_exc()
        return 1
    if args.json:
        emit({"done": True, "model": report["model"], "report": report})
    else:
        a = report["at_0_5"]
        print(f"\nmodel: {report['model']}\nhit at 0.5: {a['hit']}\nfalse wakes: {a['false_wakes']} in "
              f"{report['validation_hours']} h ({a['fph']}/h)\ntook {report['seconds'] / 60:.1f} min")
    return 0


if __name__ == "__main__":
    sys.exit(main())
