"""Compare whisper models on your own voice: speed and word error rate.

1. Record the prompts (about two minutes; clips stay in state/stt_eval/,
   which is git-ignored):

       .venv\\Scripts\\python tools\\stt_eval.py --record

2. Evaluate (GPU models only run while the GPU is under 70% busy):

       .venv\\Scripts\\python tools\\stt_eval.py
       .venv\\Scripts\\python tools\\stt_eval.py --models large-v3@cuda state/models/faster-whisper-large-v3-turbo@cuda base.en@cpu

Each model spec is NAME_OR_FOLDER@cuda|cpu. Clips are any 16 kHz mono wav
with a .txt of what was said next to it (--dir). With --tts, clips are made
with Kokoro voices instead (native accents: a speed check, not an accent check).
"""
from __future__ import annotations

import argparse
import re
import statistics
import sys
import time
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from heyclaude.config import ROOT, STATE_DIR  # noqa: E402

PROMPTS = [
    "Hey Claude, what's the capital of Australia?",
    "What's the weather in Tashkent tomorrow?",
    "Start a task in inha app: add a dark mode toggle to the settings page.",
    "What's on my calendar on Wednesday afternoon?",
    "How many minutes are in three and a half hours?",
    "Did anyone message me on Telegram this morning?",
    "Set a reminder to call my brother at seven thirty.",
    "What did the timetable bot task change?",
    "Explain the difference between a process and a thread in two sentences.",
    "Search my email for the receipt from the electronics store.",
    "Convert twenty five degrees Celsius to Fahrenheit.",
    "Resume the last task in a terminal.",
    "Translate 'good morning' into Russian and Uzbek.",
    "Stop the task in hey claude.",
    "What's fifteen percent of eighty, rounded to the nearest whole number?",
]
DEFAULT_MODELS = ["large-v3@cuda", "state/models/faster-whisper-large-v3-turbo@cuda", "base.en@cpu", "small@cpu"]
TTS_VOICES = ["am_michael", "bf_emma", "af_heart"]


def norm_words(s: str) -> list[str]:
    s = s.lower().replace("'", "")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return s.split()


def wer(ref: str, hyp: str) -> tuple[int, int]:
    """(edits, reference words) by word-level Levenshtein."""
    r, h = norm_words(ref), norm_words(hyp)
    d = list(range(len(h) + 1))
    for i, rw in enumerate(r, 1):
        prev, d[0] = d[0], i
        for j, hw in enumerate(h, 1):
            cur = min(d[j] + 1, d[j - 1] + 1, prev + (rw != hw))
            prev, d[j] = d[j], cur
    return d[len(h)], len(r)


def read_wav(p: Path) -> np.ndarray:
    with wave.open(str(p), "rb") as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1 and w.getsampwidth() == 2, f"{p}: need 16 kHz mono"
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


def write_wav(p: Path, pcm: np.ndarray):
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(pcm.astype(np.int16).tobytes())


def record(out: Path, n: int, device=None):
    from heyclaude import config
    from heyclaude.listen import MicSource, record_utterance
    from heyclaude.vad import make_vad
    out.mkdir(parents=True, exist_ok=True)
    cfg = dict(config.load()["listen"], start_timeout=10.0, end_silence_ms=1200, smart_end=False)
    vad = make_vad(cfg)
    print(f"Read each sentence aloud after it appears, at your normal pace. {n} sentences; Ctrl+C stops.\n")
    for i, text in enumerate(PROMPTS[:n], 1):
        input(f"[{i}/{n}] Press Enter, then say:\n    {text}\n")
        src = MicSource(device)
        src.start()
        vad.reset()
        try:
            audio, _ = record_utterance(iter(src), cfg, vad=vad)
        finally:
            src.close()
        if audio is None:
            print("  (didn't hear anything; skipped)")
            continue
        write_wav(out / f"{i:02d}.wav", audio)
        (out / f"{i:02d}.txt").write_text(text, encoding="utf-8")
        print(f"  saved {len(audio) / 16000:.1f}s")
    print(f"\nDone. Now run: .venv\\Scripts\\python tools\\stt_eval.py --dir {out}")


def tts_clips(out: Path) -> Path:
    """Kokoro-spoken prompts in a few voices (cached in state/stt_eval_tts)."""
    from heyclaude.tts import KokoroVoice, kokoro_paths, to_far
    out.mkdir(parents=True, exist_ok=True)
    if len(list(out.glob("*.wav"))) >= len(PROMPTS) * len(TTS_VOICES):
        return out
    model, voices = kokoro_paths({})
    k = KokoroVoice(model, voices, "cpu", threads=8)
    for v in TTS_VOICES:
        k.voice = v
        for i, text in enumerate(PROMPTS, 1):
            pcm = to_far(k.synth(text), 24000)
            pad = np.zeros(4000, np.int16)
            write_wav(out / f"{v}-{i:02d}.wav", np.concatenate([pad, pcm, pad]))
            (out / f"{v}-{i:02d}.txt").write_text(text, encoding="utf-8")
    return out


def gpu_busy() -> float | None:
    from heyclaude.sysload import LoadMonitor
    m = LoadMonitor()
    time.sleep(0.8)
    u = m.gpu_util()
    m.close()
    return u


def evaluate(clips: list[tuple[Path, str]], specs: list[str], prompt: str | None, max_gpu: float):
    from faster_whisper import WhisperModel

    from heyclaude.stt import add_cuda_dll_dirs
    add_cuda_dll_dirs()
    rows = []
    audio = [(p, read_wav(p), ref) for p, ref in clips]
    for spec in specs:
        name, _, dev = spec.rpartition("@")
        if not name:
            name, dev = spec, "cuda"
        path = str(ROOT / name) if (ROOT / name).is_dir() else name
        if dev == "cuda":
            u = gpu_busy()
            if u is not None and u >= max_gpu:
                print(f"skip {spec}: GPU {u:.0f}% busy (>= {max_gpu:.0f}%)")
                continue
        ct = "int8_float16" if dev == "cuda" else "int8"
        t = time.perf_counter()
        m = WhisperModel(path, device=dev, compute_type=ct, cpu_threads=8)
        load_s = time.perf_counter() - t
        m.transcribe(np.zeros(16000, np.float32), language="en", beam_size=1)
        edits = words = 0
        times, rtf, bad = [], [], []
        for p, pcm, ref in audio:
            f = pcm.astype(np.float32) / 32768.0
            t = time.perf_counter()
            segs, _ = m.transcribe(f, language="en", beam_size=1, vad_filter=False, condition_on_previous_text=False,
                                   without_timestamps=True, initial_prompt=prompt)
            hyp = " ".join(s.text.strip() for s in segs).strip()
            dt = time.perf_counter() - t
            e, n = wer(ref, hyp)
            edits, words = edits + e, words + n
            times.append(dt)
            rtf.append(dt / (len(pcm) / 16000))
            if e:
                bad.append(f"    {p.name}: {hyp!r}")
        rows.append(dict(model=f"{Path(name).name.replace('faster-whisper-', '')} ({dev}, {ct})",
                         wer=100 * edits / max(1, words), median_s=statistics.median(times),
                         p90_s=sorted(times)[int(0.9 * (len(times) - 1))], rtf=statistics.median(rtf), load_s=load_s))
        print(f"{rows[-1]['model']}: WER {rows[-1]['wer']:.1f}%  median {rows[-1]['median_s']:.2f}s  "
              f"p90 {rows[-1]['p90_s']:.2f}s  load {load_s:.1f}s")
        for b in bad[:8]:
            print(b)
        del m
    return rows


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--record", nargs="?", type=int, const=len(PROMPTS), metavar="N")
    ap.add_argument("--dir", default=str(STATE_DIR / "stt_eval"))
    ap.add_argument("--tts", action="store_true", help="evaluate on Kokoro-spoken prompts instead")
    ap.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    ap.add_argument("--max-gpu", type=float, default=70.0, help="skip GPU models while the GPU is this busy")
    ap.add_argument("--no-prompt", action="store_true", help="without the app's vocabulary hint")
    ap.add_argument("--device", default=None, help="mic for --record")
    a = ap.parse_args()
    d = Path(a.dir)
    if a.record:
        return record(d, a.record, a.device)
    if a.tts:
        d = tts_clips(STATE_DIR / "stt_eval_tts")
    clips = [(p, p.with_suffix(".txt").read_text(encoding="utf-8").strip())
             for p in sorted(d.glob("*.wav")) if p.with_suffix(".txt").exists()]
    if not clips:
        print(f"No clips with transcripts in {d}. Record some with --record, or use --tts.")
        return 1
    from heyclaude import config
    projects = list(config.load()["projects"])
    prompt = None if a.no_prompt else ("Vocabulary: Claude, " + ", ".join(projects) + ".")
    secs = sum(len(read_wav(p)) for p, _ in clips) / 16000
    print(f"{len(clips)} clips, {secs:.0f}s of audio, from {d}\n")
    rows = evaluate(clips, a.models, prompt, a.max_gpu)
    print("\n| model | WER | median per clip | p90 | real-time factor | load |\n|---|---|---|---|---|---|")
    for r in rows:
        print(f"| {r['model']} | {r['wer']:.1f}% | {r['median_s']:.2f} s | {r['p90_s']:.2f} s | {r['rtf']:.3f} | {r['load_s']:.1f} s |")


if __name__ == "__main__":
    sys.exit(main())
