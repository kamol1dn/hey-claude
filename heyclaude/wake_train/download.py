"""Resumable, ranged downloads of openWakeWord's precomputed negative features.

The network here is slow (~0.5-1 MB/s) and Hugging Face's xet transfer stalls,
so this uses plain HTTPS GETs with `Range:` headers against the `resolve/`
URL (HF_HUB_DISABLE_XET=1 is set as well, for anything that imports
huggingface_hub). Every piece is written to a `.part` file and resumed from
its size, so an interrupted download continues where it stopped.

Two files from https://huggingface.co/datasets/davidscripka/openwakeword_features
(CC BY-NC-SA 4.0; see models/LICENSE):

- `validation_set_features.npy`: ~10.7 h of continuous 80 ms embedding frames
  (DiPCo dinner parties, Santa Barbara conversations, reverberated music),
  185 MB. Used only to *measure* false wakes per hour, never to train.
- `openwakeword_features_ACAV100M_2000_hrs_16bit.npy`: 5.6 M rows of
  (16, 96) float16 windows, 1.28 s each, 17 GB. We never fetch all of it:
  `acav_slices(n)` reads n slices of 2048 consecutive rows (6.3 MB, ~44 min of
  audio) spread evenly over the file, so any prefix of the list is a spread
  sample too. The pipeline picks n (see wake_train.pipeline).
"""
from __future__ import annotations

import os
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np

os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

REPO = "https://huggingface.co/datasets/davidscripka/openwakeword_features/resolve/"
REVISION = "985bf1b47e7f19c07741af82bfe32d5a9dc56096"     # pinned, so slices never move
ACAV_FILE = "openwakeword_features_ACAV100M_2000_hrs_16bit.npy"
ACAV_ROWS, ACAV_HEADER = 5_625_000, 128                    # shape (5625000, 16, 96) <f2, 128-byte npy header
ROW_BYTES = 16 * 96 * 2
SLICE_ROWS = 2048
VAL_FILE = "validation_set_features.npy"
VAL_BYTES, VAL_HEADER = 184_836_608, 128                   # shape (481345, 96) <f4
USER_AGENT = "hey-claude-wake-train/1"


def url(name: str) -> str:
    return f"{REPO}{REVISION}/{name}"


def fmt_mb(n: float) -> str:
    return f"{n / 2 ** 20:.1f} MB" if n < 2 ** 30 else f"{n / 2 ** 30:.2f} GB"


def fetch_range(src: str, dest: Path, start: int, length: int, progress=None, cancel=None,
                opener=None, retries: int = 8, timeout: float = 30.0, chunk: int = 1 << 16) -> Path:
    """Download bytes [start, start+length) of `src` into `dest`, resuming a
    `dest.part` left by an earlier attempt. `progress(done, total)` is called
    as bytes arrive; `cancel()` returning True stops (the .part is kept)."""
    dest = Path(dest)
    if dest.is_file() and dest.stat().st_size == length:
        if progress:
            progress(length, length)
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    open_ = opener or urllib.request.urlopen
    tries = 0
    while True:
        have = part.stat().st_size if part.exists() else 0
        if have > length:                                    # corrupt leftover
            part.unlink()
            have = 0
        if have == length:
            part.replace(dest)
            return dest
        req = urllib.request.Request(src, headers={
            "Range": f"bytes={start + have}-{start + length - 1}", "User-Agent": USER_AGENT})
        try:
            with open_(req, timeout=timeout) as r:
                status = getattr(r, "status", 206)
                if status == 200 and (start + have) != 0:
                    raise OSError("server ignored the Range header")
                with open(part, "ab") as f:
                    while True:
                        if cancel and cancel():
                            raise InterruptedError("cancelled")
                        b = r.read(min(chunk, length - have))
                        if not b:
                            break
                        f.write(b)
                        have += len(b)
                        if progress:
                            progress(have, length)
                        if have >= length:
                            break
            tries = 0
        except InterruptedError:
            raise
        except (OSError, urllib.error.URLError) as e:   # timeouts, resets, 5xx: back off and resume
            tries += 1
            if tries > retries:
                raise OSError(f"download failed after {retries} retries: {e}") from e
            time.sleep(min(30, 2 ** tries))


def acav_slice_rows(n: int, total_rows: int = ACAV_ROWS, rows: int = SLICE_ROWS) -> list[int]:
    """First row of each of n slices, spread over the file in van der Corput
    order (0, 1/2, 1/4, 3/4, ...), so the first k slices are spread too."""
    out = []
    for i in range(n):
        x, denom, k = 0.0, 1.0, i
        while k:
            denom *= 2
            x += (k & 1) / denom
            k >>= 1
        out.append(int(x * (total_rows - rows)) // rows * rows)
    return out


def acav_slices(n: int, folder: Path, progress=None, cancel=None, opener=None, workers: int = 4) -> list[Path]:
    """Download (or reuse) n ACAV100M slices, `workers` at a time (the CDN
    throttles each connection, so parallel ranges add up).
    progress(done_bytes, total_bytes)."""
    import threading
    from concurrent.futures import ThreadPoolExecutor
    folder = Path(folder)
    size = SLICE_ROWS * ROW_BYTES
    total = n * size
    rows = acav_slice_rows(n)
    done = {r: 0 for r in rows}
    lock = threading.Lock()

    def one(row):
        def cb(d, t):
            with lock:
                done[row] = d
                if progress:
                    progress(sum(done.values()), total)
        return fetch_range(url(ACAV_FILE), folder / f"acav_{row:07d}.f16", ACAV_HEADER + row * ROW_BYTES,
                           size, cb, cancel, opener)
    if workers <= 1:
        return [one(r) for r in rows]
    with ThreadPoolExecutor(workers) as ex:
        return list(ex.map(one, rows))


def load_acav(paths: list[Path]) -> np.ndarray:
    """Slices -> (rows, 16, 96) float16."""
    if not paths:
        return np.zeros((0, 16, 96), np.float16)
    return np.concatenate([np.fromfile(p, dtype="<f2").reshape(-1, 16, 96) for p in paths])


def validation_set(folder: Path, progress=None, cancel=None, opener=None) -> Path:
    return fetch_range(url(VAL_FILE), Path(folder) / VAL_FILE, 0, VAL_BYTES, progress, cancel, opener)


def load_validation(path: Path) -> np.ndarray:
    """(frames, 96) float32 continuous embedding frames, 80 ms each."""
    return np.load(path, mmap_mode="r")


def plan_bytes(n_slices: int, with_validation: bool = True) -> int:
    return n_slices * SLICE_ROWS * ROW_BYTES + (VAL_BYTES if with_validation else 0)


def have_bytes(folder: Path, n_slices: int, with_validation: bool = True) -> int:
    """Bytes already on disk (finished or partial) for that plan."""
    folder = Path(folder)
    names = [f"acav_{r:07d}.f16" for r in acav_slice_rows(n_slices)] + ([VAL_FILE] if with_validation else [])
    n = 0
    for name in names:
        for p in (folder / name, folder / (name + ".part")):
            if p.is_file():
                n += p.stat().st_size
    return n


if __name__ == "__main__":                       # python -m heyclaude.wake_train.download 64
    import sys
    from ..config import STATE_DIR
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 64
    folder = STATE_DIR / "wake_train" / "features"
    t0, last = time.time(), [0.0]

    def show(done, total, what):
        if time.time() - last[0] > 5 or done == total:
            last[0] = time.time()
            rate = done / max(1e-3, time.time() - t0)
            print(f"{what}: {fmt_mb(done)} / {fmt_mb(total)}  {rate / 2 ** 20:.2f} MB/s", flush=True)

    print(f"plan: {fmt_mb(plan_bytes(n))} ({n} ACAV slices + validation set); "
          f"on disk already: {fmt_mb(have_bytes(folder, n))}", flush=True)
    validation_set(folder, lambda d, t: show(d, t, "validation"))
    t0 = time.time()
    acav_slices(n, folder, lambda d, t: show(d, t, "acav"))
    print("done", flush=True)
