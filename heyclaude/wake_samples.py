"""Record your own "hey claude" clips for training and for tools/wake_eval.py.

    python -m heyclaude --record-wake-samples          # 40 clips, Enter before each
    python -m heyclaude --record-wake-samples 30 --auto  # no keypresses, a short gap between clips
    python -m heyclaude.wake_samples --pack            # rebuild the Colab zip only

Clips go to state/wake_samples/ (git-ignored) as 16 kHz mono 16-bit wavs,
trimmed to the speech plus a little padding:

    hey_claude_007_quiet.wav     <- index, then the cue it was recorded with

Every 4th clip (index 3, 7, 11...) is held out for evaluation and never goes
into the training zip, so wake_eval can report a hit rate on clips the model
never saw. The rest are zipped into state/wake_samples/hey_claude_voice.zip
for the Colab notebook (docs/train-hey-claude.md).
"""
from __future__ import annotations

import argparse
import re
import time
import wave
import zipfile
from pathlib import Path

import numpy as np

from .config import STATE_DIR
from .listen import RATE, record_utterance

SAMPLES_DIR = STATE_DIR / "wake_samples"
ZIP_NAME = "hey_claude_voice.zip"
PREFIX = "hey_claude_"
HOLD_OUT_EVERY = 4                # index % 4 == 3 -> held out for evaluation

# (file tag, what to tell the user). Cycled in order, so any 20-50 clips get a mix.
CUES = [
    ("normal", "normal voice, at the desk"),
    ("quiet", "quietly, like someone is asleep nearby"),
    ("loud", "louder, a bit impatient"),
    ("fast", "fast, run the words together"),
    ("slow", "slowly, stretch it out"),
    ("far", "from 2-3 metres away (turn away from the mic or stand up)"),
    ("normal", "normal voice, looking at the screen"),
    ("tired", "tired / mumbled, end of the day"),
    ("question", "as if you're asking something: \"hey claude?\""),
    ("far", "from across the room"),
    ("sentence", "as the start of a request: \"hey claude, what time is it\" (just say it, it gets trimmed)"),
    ("side", "with your head turned to the side"),
]

# Recorder settings: shorter tail than the assistant (one short phrase), and a
# generous start timeout so you have time to walk around between clips.
REC_CFG = {"vad_aggressiveness": 2, "end_silence_ms": 450, "start_timeout": 10.0, "max_seconds": 5}


# -- files ----------------------------------------------------------------------
def clip_index(path: Path) -> int | None:
    m = re.match(re.escape(PREFIX) + r"(\d+)", Path(path).name)
    return int(m.group(1)) if m else None


def list_clips(folder: Path = SAMPLES_DIR) -> list[Path]:
    """Recorded clips in index order (other files in the folder are ignored)."""
    folder = Path(folder)
    if not folder.is_dir():
        return []
    clips = [p for p in folder.glob(PREFIX + "*.wav") if clip_index(p) is not None]
    return sorted(clips, key=clip_index)


def next_index(folder: Path = SAMPLES_DIR) -> int:
    idx = [clip_index(p) for p in list_clips(folder)]
    return max(idx) + 1 if idx else 0


def clip_path(folder: Path, index: int, tag: str) -> Path:
    return Path(folder) / f"{PREFIX}{index:03d}_{re.sub(r'[^a-z0-9]+', '', tag.lower()) or 'x'}.wav"


def is_held_out(path: Path) -> bool:
    i = clip_index(path)
    return i is not None and i % HOLD_OUT_EVERY == HOLD_OUT_EVERY - 1


def split(clips: list[Path]) -> tuple[list[Path], list[Path]]:
    """(train, held_out). Decided by each clip's index, so it never changes
    when you record more or delete a bad one."""
    train = [p for p in clips if not is_held_out(p)]
    held = [p for p in clips if is_held_out(p)]
    return train, held


def write_wav(path: Path, pcm: np.ndarray, rate: int = RATE):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(np.asarray(pcm, dtype=np.int16).tobytes())


def read_wav(path: Path) -> np.ndarray:
    """16 kHz mono 16-bit wav -> int16 array (anything else raises ValueError)."""
    with wave.open(str(path), "rb") as w:
        if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (RATE, 1, 2):
            raise ValueError(f"{path}: need 16 kHz mono 16-bit, got {w.getframerate()} Hz, "
                             f"{w.getnchannels()} ch, {8 * w.getsampwidth()}-bit")
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


def pack(folder: Path = SAMPLES_DIR, out: Path | None = None) -> tuple[Path, int]:
    """Zip the training clips (not the held-out ones) for the Colab notebook."""
    folder = Path(folder)
    out = Path(out) if out else folder / ZIP_NAME
    train, _ = split(list_clips(folder))
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for p in train:
            z.write(p, arcname=p.name)
    tmp.replace(out)
    return out, len(train)


# -- audio ----------------------------------------------------------------------
def trim_clip(pcm: np.ndarray, pad_s: float = 0.25, rel_db: float = -30.0, floor: float = 300.0) -> np.ndarray:
    """Cut leading/trailing silence, keeping `pad_s` around the speech.

    Speech = 20 ms frames whose RMS is within `rel_db` of the loudest frame
    (and above `floor`, so a silent clip stays empty).
    """
    pcm = np.asarray(pcm, dtype=np.int16)
    f = 320
    n = len(pcm) // f
    if n == 0:
        return pcm[:0]
    rms = np.sqrt(np.mean(pcm[: n * f].astype(np.float32).reshape(n, f) ** 2, axis=1))
    loud = np.flatnonzero(rms >= max(floor, rms.max() * 10 ** (rel_db / 20)))
    if loud.size == 0:
        return pcm[:0]
    pad = int(pad_s * RATE)
    a = max(0, loud[0] * f - pad)
    b = min(len(pcm), (loud[-1] + 1) * f + pad)
    return pcm[a:b]


def check_clip(pcm: np.ndarray) -> list[str]:
    """Problems worth re-recording for (empty list = fine)."""
    pcm = np.asarray(pcm, dtype=np.int16)
    secs = len(pcm) / RATE
    out = []
    if secs < 0.5:
        out.append(f"very short ({secs:.1f} s)")
    if secs > 3.0:
        out.append(f"long ({secs:.1f} s): say just \"hey claude\" for most clips")
    peak = int(np.abs(pcm.astype(np.int32)).max()) if len(pcm) else 0
    if peak < 1200:
        out.append("very quiet: move closer or raise the mic level")
    if len(pcm) and np.mean(np.abs(pcm.astype(np.int32)) >= 32000) > 0.001:
        out.append("clipped (too loud): back off a little")
    return out


# -- the session ----------------------------------------------------------------
def record_session(count: int, open_source, folder: Path = SAMPLES_DIR, auto: bool = False,
                   ask=input, say=print, is_speech=None, gap_s: float = 1.2) -> list[Path]:
    """Prompt for `count` clips and save them. `open_source()` returns an audio
    source like listen.MicSource (start/drain/close + iterating int16 blocks).

    Interactive: Enter records the next clip, r re-records the last one,
    q stops. With auto, it just keeps going with a short gap.
    """
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    first = next_index(folder)
    saved: list[Path] = []
    src = open_source()
    src.start()
    blocks = iter(src)
    try:
        k = 0
        while k < count:
            idx = first + k
            tag, cue = CUES[idx % len(CUES)]
            say(f"\n[{k + 1}/{count}] Say \"hey claude\" {cue}.")
            if not auto:
                cmd = (ask("  Enter = record, r = redo the last one, q = stop > ") or "").strip().lower()
                if cmd == "q":
                    break
                if cmd == "r" and saved:
                    last = saved.pop()
                    last.unlink(missing_ok=True)
                    k -= 1
                    say(f"  deleted {last.name}; recording it again")
                    continue
            else:
                time.sleep(gap_s)
            src.drain()                              # drop what the mic heard while we waited
            say("  listening…")
            audio, _ = record_utterance(blocks, REC_CFG, is_speech=is_speech)
            if audio is None:
                say("  didn't hear anything; let's try that one again")
                if auto:
                    break                            # nobody there: don't loop forever
                continue
            clip = trim_clip(audio)
            problems = check_clip(clip)
            path = clip_path(folder, idx, tag)
            write_wav(path, clip)
            saved.append(path)
            note = ("  ! " + "; ".join(problems) + " (r to redo)") if problems else ""
            say(f"  saved {path.name} ({len(clip) / RATE:.1f} s){' [held out for testing]' if is_held_out(path) else ''}"
                + (("\n" + note) if note else ""))
            k += 1
    finally:
        src.close()
    return saved


def main(argv=None, cfg: dict | None = None):
    ap = argparse.ArgumentParser(prog="heyclaude.wake_samples", description=__doc__.split("\n\n")[0])
    ap.add_argument("count", nargs="?", type=int, default=40, help="how many clips to record (20-50; default 40)")
    ap.add_argument("--auto", action="store_true", help="no keypresses between clips")
    ap.add_argument("--pack", action="store_true", help="only rebuild the Colab zip from the clips already recorded")
    ap.add_argument("--dir", type=Path, default=SAMPLES_DIR, help="where the clips live (default state/wake_samples)")
    ap.add_argument("--device", default=None, help="input device index or name (default: listen.device)")
    ap.add_argument("--config", help="config file (default: config.yaml)")
    ap.add_argument("--pause-at-end", action="store_true", help="wait for Enter before exiting (tray window)")
    args = ap.parse_args(argv)
    try:
        return _run(args, cfg)
    finally:
        if args.pause_at_end:
            try:
                input("\nPress Enter to close this window.")
            except (EOFError, KeyboardInterrupt):
                pass


def _run(args, cfg: dict | None):
    if not args.pack:
        if cfg is None:
            from . import config
            cfg = config.load(args.config)
        device = args.device if args.device is not None else cfg.get("listen", {}).get("device")
        if isinstance(device, str) and device.isdigit():
            device = int(device)
        have = len(list_clips(args.dir))
        print(__doc__.split("\n\n")[0])
        print(f"\nClips go to {args.dir}{f' ({have} already there; new ones are added)' if have else ''}.")
        print("Mix it up: near and far, quiet and loud, fast and slow, like you'd really say it.\n"
              "If hey-claude is running, pause it from the tray first so it doesn't answer you.")

        def open_mic():
            from .listen import MicSource
            return MicSource(device)

        try:
            new = record_session(args.count, open_mic, folder=args.dir, auto=args.auto)
        except KeyboardInterrupt:
            new = []
            print("\nstopped")
        print(f"\nRecorded {len(new)} new clip(s).")

    clips = list_clips(args.dir)
    train, held = split(clips)
    out, n = pack(args.dir)
    print(f"{len(clips)} clips in {args.dir}: {len(train)} for training, {len(held)} held out for testing.")
    print(f"Colab upload: {out} ({n} clips)")
    if len(clips) < 20:
        print("Aim for at least 20 (30-40 is better).")
    print("Next: docs/train-hey-claude.md, step 2 (Colab), or measure a model with\n"
          "  .venv\\Scripts\\python tools\\wake_eval.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
