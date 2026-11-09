"""The whole local training run, in stages with progress and a time estimate.

    download -> voices (synthesis) -> features (augmentation + embeddings)
             -> train -> check (threshold + measurements) -> export

Two modes share it:
- "default" (M2a): synthetic voices only, for the shared model everyone
  downloads. Half of every synthetic set is spoken by Kokoro, half by Piper's
  multi-speaker LibriTTS-R voice (904 audiobook readers; size key `piper`).
  It refuses user clips, and holds 8 Kokoro voices and 45 Piper speakers out
  of training to measure the hit rate on voices it never heard.
- "personal" (M2b, the Settings wizard): adds the user's clips (positives,
  every 4th held out), their room recording and "that wasn't me" clips
  (negatives). Results stay in state/.

Where it runs: synthesis and the two feature models (the heavy parts) go to
the GPU when onnxruntime has CUDA, there is VRAM to spare and the GPU is under
`gpu_busy_pct` (70%) busy, excluding hey-claude's own load; otherwise the CPU.
The classifier head is small and always trains on the CPU (numpy).

Measurements, all in the report:
- hit rate on held-out positives (held-out Kokoro voices and Piper speakers,
  reported separately; the user's held-out clips in personal mode), as the best score over a few end offsets,
  like the streaming app sees it;
- false wakes per hour on openWakeWord's ~10.7 h validation set (dinner
  parties, conversations, music; never trained on), with a 2 s refractory;
- the threshold: wake_metrics.recommend() over those two, then the output is
  calibrated so that threshold becomes 0.5 (the app's default).
"""
from __future__ import annotations

import hashlib
import json
import os
import queue
import random
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from .. import wake_metrics as WM
from . import augment as A
from . import download as D
from . import head as H
from . import piper_voice as PV
from . import synth as S

SIZES = {
    # synthetic clips: positives, held-out positives (Kokoro voices / Piper speakers), look-alikes,
    # sentences, random words; windows per positive clip; ACAV slices (6.3 MB each); training steps;
    # piper: percent of every synthetic set spoken by Piper's LibriTTS-R speakers (0 = Kokoro only)
    "default": dict(n_pos=12000, n_held=480, n_held_piper=480, n_look=3000, n_sent=1500, n_words=1500, aug_pos=3,
                    acav=256, steps=8000, user_copies=0, heads=8, piper=50),
    "personal": dict(n_pos=1000, n_held=0, n_held_piper=0, n_look=700, n_sent=300, n_words=300, aug_pos=3,
                     acav=256, steps=5000, user_copies=25, heads=3, piper=0),
}
FRAME_S = 0.08
REFRACTORY_FRAMES = 25          # 2 s, like tools/wake_eval.py's default


@dataclass
class Job:
    mode: str = "default"                       # "default" | "personal"
    phrase: str = "hey claude"
    out: str = "models/hey_claude.onnx"
    work: str = ""                              # cache + downloads; default state/wake_train
    device: str = "auto"                        # auto | cuda | cpu
    gpu_busy_pct: float = 70.0
    gpu_util: float | None = None               # the app's own reading (its own load excluded), if known
    seed: int = 1
    user_clips: list[str] = field(default_factory=list)      # personal: the user's wavs (16 kHz mono)
    room: list[str] = field(default_factory=list)            # personal: room recordings (negatives)
    false_wakes: list[str] = field(default_factory=list)     # personal: "that wasn't me" clips (negatives)
    sizes: dict = field(default_factory=dict)                # overrides of SIZES[mode]
    tts: dict = field(default_factory=dict)                  # tts config (kokoro_model / kokoro_voices paths)

    def size(self, k):
        return self.sizes.get(k, SIZES[self.mode][k])


# -- progress -----------------------------------------------------------------------
STAGES = [("download", "Downloading negative features"), ("voices", "Synthesising voices"),
          ("features", "Mixing in noise and computing features"),
          ("train", "Training"), ("check", "Checking and choosing the threshold"), ("export", "Saving")]


class Progress:
    """Turns per-stage progress into overall progress plus an honest ETA.

    Each stage starts with an estimate (seconds); once a stage is under way
    its own measured rate replaces the estimate, and finished stages scale the
    estimates of the later ones by how far off they were (bounded)."""

    def __init__(self, cb=None, estimates: dict | None = None, clock=time.monotonic):
        self.cb, self.clock = cb, clock
        self.est = {k: 1.0 for k, _ in STAGES}
        self.est.update(estimates or {})
        self.done: dict[str, float] = {}
        self.cur, self.t_stage, self.frac = None, clock(), 0.0
        self.msg = ""

    def stage(self, name: str, msg: str = ""):
        if self.cur is not None:
            self.done[self.cur] = self.clock() - self.t_stage
        self.cur, self.t_stage, self.frac = name, self.clock(), 0.0
        self.msg = msg or dict(STAGES)[name]
        self._emit()

    def update(self, frac: float, msg: str | None = None):
        self.frac = min(1.0, max(0.0, frac))
        if msg is not None:
            self.msg = msg
        self._emit()

    def finish(self):
        if self.cur is not None:
            self.done[self.cur] = self.clock() - self.t_stage
            self.cur = None
        self._emit()

    def _scale(self) -> float:
        est = sum(self.est[k] for k in self.done)
        act = sum(self.done.values())
        return min(3.0, max(0.5, act / est)) if est > 1 and act > 1 else 1.0

    def eta(self) -> float:
        names = [k for k, _ in STAGES]
        left = 0.0
        if self.cur is not None:
            el = self.clock() - self.t_stage
            if self.frac >= 0.03 and el > 2:
                left += el * (1 - self.frac) / self.frac
            else:
                left += self.est[self.cur] * self._scale() * (1 - self.frac)
            later = names[names.index(self.cur) + 1:]
        else:
            later = []
        return left + sum(self.est[k] for k in later) * self._scale()

    def overall(self) -> float:
        total = sum(self.est.values())
        done = sum(self.est[k] for k in self.done)
        cur = self.est[self.cur] * self.frac if self.cur else 0.0
        return 1.0 if self.cur is None and self.done else (done + cur) / total

    def _emit(self):
        if self.cb:
            self.cb(dict(stage=self.cur, label=dict(STAGES).get(self.cur, "Done"), stage_frac=round(self.frac, 4),
                         overall=round(self.overall(), 4), eta_s=round(self.eta(), 1), msg=self.msg))


# -- device -----------------------------------------------------------------------------
def pick_device(want: str = "auto", busy_pct: float = 70.0, gpu_util: float | None = None,
                cuda_check=None, monitor_factory=None, sample_s: float = 2.0) -> tuple[str, str]:
    """("cuda" | "cpu", why). The GPU is used when CUDA works in onnxruntime,
    >= 1.5 GB VRAM is free and it's under `busy_pct` busy. `gpu_util` is the
    app's own reading (hey-claude's load already excluded); without it this
    samples for `sample_s` and takes the lowest 0.5 s average, so a burst of
    hey-claude's own whisper/voice work doesn't count."""
    if want == "cpu":
        return "cpu", "set to CPU"
    if cuda_check is None:
        from ..tts import ort_cuda_available as cuda_check
    ok, why = cuda_check()
    if not ok:
        return "cpu", why
    free = None
    if gpu_util is None or monitor_factory is not None:
        try:
            if monitor_factory is None:
                from ..sysload import LoadMonitor as monitor_factory
            mon = monitor_factory()
            lows = []
            t_end = time.monotonic() + sample_s
            while time.monotonic() < t_end:
                time.sleep(0.5)
                u = mon.gpu_util()
                if u is not None:
                    lows.append(u)
            free = mon.gpu_free_mb()
            mon.close()
            if gpu_util is None and lows:
                gpu_util = min(lows)
        except Exception:
            pass
    if want == "cuda":
        return "cuda", "set to GPU"
    if gpu_util is not None and gpu_util >= busy_pct:
        return "cpu", f"GPU busy ({gpu_util:.0f}%)"
    if free is not None and free < 1500:
        return "cpu", f"only {free:.0f} MB of VRAM free"
    return "cuda", f"GPU free ({gpu_util:.0f}% busy)" if gpu_util is not None else "GPU (load unknown)"


# -- cache ------------------------------------------------------------------------------
def _key(*parts) -> str:
    return hashlib.sha1(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:12]


def _save_clips(path: Path, clips: list[np.ndarray]):
    lens = np.array([len(c) for c in clips], np.int64)
    flat = np.concatenate(clips) if clips else np.zeros(0, np.int16)
    tmp = path.with_name(path.stem + ".tmp.npz")
    np.savez(tmp, flat=flat.astype(np.int16), lens=lens)
    tmp.replace(path)


def _load_clips(path: Path) -> list[np.ndarray]:
    z = np.load(path)
    return np.split(z["flat"], np.cumsum(z["lens"])[:-1]) if len(z["lens"]) else []


def read_wav16(path) -> np.ndarray:
    from ..wake_samples import read_wav
    return read_wav(Path(path))


# -- synthesis with several engine sessions ------------------------------------------------
def render_parallel(utts: list[S.Utterance], engines: list, progress=None, cancel=None) -> list[np.ndarray]:
    """Render with one thread per engine (onnxruntime releases the GIL)."""
    out: list[np.ndarray | None] = [None] * len(utts)
    q: queue.Queue = queue.Queue()
    for i in range(len(utts)):
        q.put(i)
    done = [0]
    lock = threading.Lock()
    errors: list[BaseException] = []

    def work(engine):
        sy = S.Synth(engine)
        while not errors:
            try:
                i = q.get_nowait()
            except queue.Empty:
                return
            if cancel and cancel():
                errors.append(InterruptedError("cancelled"))
                return
            try:
                out[i] = sy.render([utts[i]])[0]
            except BaseException as e:      # noqa: BLE001 - surfaced below
                errors.append(e)
                return
            with lock:
                done[0] += 1
                if progress and (done[0] % 10 == 0 or done[0] == len(utts)):
                    progress(done[0], len(utts))

    ts = [threading.Thread(target=work, args=(e,), daemon=True) for e in engines]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    if errors:
        raise errors[0]
    return [c if c is not None else np.zeros(0, np.int16) for c in out]


def make_engines(device: str, tts_cfg: dict, free_mb: float | None = None, piper_model: Path | None = None,
                 kokoro: bool = True) -> list:
    """One engine per synthesis thread: Kokoro, plus Piper when `piper_model` is
    given (behind a piper_voice.MixedEngine that routes "piper:N" voices)."""
    cores = os.cpu_count() or 4
    if device == "cuda":
        n = 3 if (free_mb is None or free_mb > 3500) else 2 if free_mb > 2200 else 1
    else:
        n = max(1, min(4, (cores - 2) // 4))

    def kok():
        if not kokoro:
            return None
        if device == "cuda":
            return S.load_kokoro("cuda", model=tts_cfg.get("kokoro_model"), voices=tts_cfg.get("kokoro_voices"))
        return S.load_kokoro("cpu", threads=4, model=tts_cfg.get("kokoro_model"), voices=tts_cfg.get("kokoro_voices"))
    if piper_model is None:
        return [kok() for _ in range(n)]
    lock = threading.Lock()                       # Piper's espeak-ng phonemizer is shared and not thread-safe
    return [PV.MixedEngine(kok(), PV.load_piper(piper_model, device, threads=4, phonemize_lock=lock))
            for _ in range(n)]


# -- evaluation helpers -------------------------------------------------------------------
def eval_windows(clip: np.ndarray, rng: np.random.Generator, beds, noisy: bool) -> list[np.ndarray]:
    """A positive clip at 4 end offsets (0-0.24 s), like successive 80 ms
    blocks of the app's stream; its score is the best of the four."""
    x = A.as_float(clip)
    out = []
    for off in (0.0, 0.08, 0.16, 0.24):
        w = A.place_end(x, rng, end_jitter=(off, off))
        if noisy:
            w = A.mix_at_snr(w, A.background(rng, A.WINDOW, beds), rng.uniform(10, 30))
        out.append(A.as_int16(w))
    return out


def validation_scores(p, frames: np.ndarray, chunk: int = 40000, logit: bool = False) -> np.ndarray:
    """Score every 80 ms frame of the validation set (16-frame windows).
    logit=True returns the (mean) logit instead of the 0..1 score."""
    from .features import stream_to_windows
    n = frames.shape[0]
    out = []
    for s in range(0, n - 15, chunk):
        w = stream_to_windows(np.asarray(frames[s:s + chunk + 15], np.float32))
        out.append(H.ens_logits(p, w) if logit else H.predict(p, w))
    return np.concatenate(out) if out else np.zeros(0, np.float32)


LOGIT_GRID = [round(float(t), 2) for t in np.arange(-4.0, 16.01, 0.1)]


def choose(pos_scores: np.ndarray, val_scores: np.ndarray, hours: float, min_hit: float, max_fph: float,
           grid=None):
    """Sweep + recommendation (wake_metrics). Works on scores or on logits
    (pass logits and the default logit grid, which reaches far past 0.99)."""
    th = grid if grid is not None else LOGIT_GRID
    rows = WM.sweep(pos_scores, [val_scores], hours, REFRACTORY_FRAMES, th)
    return rows, WM.recommend(rows, min_hit, max_fph)


def sigmoid(x):
    return 1.0 / (1.0 + np.exp(-np.clip(np.asarray(x, np.float64), -40, 40)))


def _windows_and_features(job, clips, user_train, user_held, room, false_w, device, nrng, prog, cancel, log):
    """Augmented 2 s windows -> features, chunk by chunk. Returns ({group: (n, 16, 96) float16},
    eval features (k, 4, 16, 96), eval source per clip)."""
    from .features import Embedder
    try:
        emb = Embedder(device)
    except Exception as e:
        log(f"[wake_train] feature models on {device} failed ({e}); using the CPU")
        emb = Embedder("cpu")
    beds = [A.as_float(r) for r in room if len(r) > 16000]
    babble = [c for c in clips["sent"] if len(c)]
    if babble:                                     # "TV / people talking in the background"
        k = min(len(babble), 200)
        beds.append(A.as_float(np.concatenate([babble[i] for i in nrng.choice(len(babble), k, replace=False)])) * 0.5)
    work_items = []                                # (group, clip, kind) kind: placed | full | room | noise | eval
    for c in clips["pos"]:
        if len(c):
            work_items += [("pos", c, "placed")] * job.size("aug_pos")
    for c in user_train:
        work_items += [("user", c, "placed")] * job.size("user_copies")
    for c in clips["look"]:
        if len(c):
            work_items += [("look", c, "placed")] * 2
    for c in clips["sent"] + clips["words"]:
        if len(c):
            work_items.append(("speech", c, "placed"))
            work_items += [("speech", w, "full") for w in A.slice_windows(c, hop=12000)]
    for r in room:
        work_items += [("room", w, "room") for w in A.slice_windows(r, hop=4000)]
    for c in false_w:
        work_items += [("fw", c, "placed")] * 12
    work_items += [("noise", None, "noise")] * 3000
    eval_src = []
    for name, src in (("held", "kokoro"), ("held_piper", "piper")):    # voices / speakers never trained on
        for c in clips.get(name, []):
            if len(c):
                eval_src.append(src)
                work_items.append(("eval", c, "eval-noisy"))
    for c in user_held:
        eval_src.append("yours")
        work_items.append(("eval", c, "eval"))
    n_items = len(work_items)

    def make(g, c, kind):
        if kind == "room":           # real room audio: keep it real, only vary the level
            return [A.as_int16(A.gain_db(A.as_float(c), nrng.uniform(-10, 6)))]
        if kind == "noise":          # pure background, from near-silence to loud
            return [A.as_int16(A.background(nrng, A.WINDOW, beds) * 10 ** (nrng.uniform(-60, -12) / 20))]
        if kind.startswith("eval"):
            return eval_windows(c, nrng, beds, noisy=kind == "eval-noisy")
        return [A.augment_window(c, nrng, beds, placed=kind == "placed")]

    parts: dict[str, list[np.ndarray]] = {}
    chunk_g: list[str] = []
    chunk_w: list[np.ndarray] = []

    def flush():
        if not chunk_w:
            return
        f = emb.embed_windows(np.stack(chunk_w)).astype(np.float16)
        o = 0
        for g in chunk_g:                       # chunk_g holds one entry per window
            parts.setdefault(g, []).append(f[o:o + 1])
            o += 1
        chunk_g.clear()
        chunk_w.clear()

    for i, (g, c, kind) in enumerate(work_items):
        if cancel and cancel():
            raise InterruptedError("cancelled")
        ws = make(g, c, kind)
        chunk_w.extend(ws)
        chunk_g.extend([g] * len(ws))
        if len(chunk_w) >= 1024:
            flush()
            prog.update(i / n_items, f"Mixing in noise and computing features: {i} of {n_items} windows")
    flush()
    fx = {g: np.concatenate(v) for g, v in parts.items()}
    del parts
    ev = fx.pop("eval").reshape(-1, 4, 16, 96) if "eval" in fx else np.zeros((0, 4, 16, 96), np.float16)
    return fx, ev, eval_src


# -- the run ------------------------------------------------------------------------------
def plan_sets(job: Job, rng: random.Random, variants, held: set, train_voices: list[str],
              use_piper: bool) -> dict[str, list[S.Utterance]]:
    """Every synthetic set of a run. With Piper, `piper` percent of pos / look /
    sent / words is spoken by Piper speakers; in default mode every 20th Piper
    speaker is held out of all of them and speaks the "held_piper" set instead."""
    share = job.size("piper") / 100.0 if use_piper else 0.0
    p_held = [PV.voice_name(s) for s in PV.held_out_speakers()] if (share and job.mode == "default") else []
    p_train = [PV.voice_name(s) for s in (PV.train_speakers() if p_held else range(PV.N_SPEAKERS))] if share else []
    sets = {
        "pos": S.plan_mixed(rng, job.size("n_pos"), variants, train_voices, p_train, share),
        "held": S.plan(rng, job.size("n_held"), variants, sorted(held), blend_p=0.0) if held else [],
        "look": S.plan_text_mixed(rng, job.size("n_look"), S.negative_phrases(job.phrase), list(S.KOKORO_ALL),
                                  p_train, share),
        "sent": S.plan_text_mixed(rng, job.size("n_sent"), S.SENTENCES, list(S.KOKORO_ALL), p_train, share,
                                  speed=(0.85, 1.25)),
        "words": S.plan_text_mixed(rng, job.size("n_words"), S.random_word_phrases(rng, 400), list(S.KOKORO_ALL),
                                   p_train, share),
    }
    if p_held and job.size("n_held_piper"):
        sets["held_piper"] = S.plan(rng, job.size("n_held_piper"), variants, p_held, blend_p=0.0)
    return sets


def estimates(job: Job, device: str, have_bytes: int, rate_mb_s: float = 0.7) -> dict:
    n_clips = sum(job.size(k) for k in ("n_pos", "n_held", "n_held_piper", "n_look", "n_sent", "n_words"))
    synth_rate = 9.0 if device == "cuda" else 3.5
    n_win = job.size("n_pos") * job.size("aug_pos") + job.size("n_look") * 2 + job.size("n_sent") * 3 \
        + job.size("n_words") + 3000 + len(job.user_clips) * job.size("user_copies")
    need = D.plan_bytes(job.size("acav")) + (PV.plan_bytes() if job.size("piper") else 0) - have_bytes
    return dict(download=max(1.0, need / (rate_mb_s * 2 ** 20)), voices=n_clips / synth_rate,
                features=n_win * 0.004 + n_win / (700 if device == "cuda" else 220),
                train=job.size("steps") * job.size("heads") * 0.008, check=25.0, export=2.0)


def run(job: Job, progress_cb=None, cancel=None, log=print) -> dict:
    """Train a model; returns the report (also written next to the model)."""
    from ..config import ROOT, STATE_DIR
    t_start = time.time()
    if job.mode == "default" and (job.user_clips or job.room or job.false_wakes):
        raise ValueError("the shared default model is trained on synthetic voices only (no user clips)")
    work = Path(job.work) if job.work else STATE_DIR / "wake_train"
    feat_dir, cache = work / "features", work / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    out = Path(job.out)
    if not out.is_absolute():
        out = ROOT / out
    rng = random.Random(job.seed)
    nrng = np.random.default_rng(job.seed)

    device, why = pick_device(job.device, job.gpu_busy_pct, job.gpu_util)
    log(f"[wake_train] {job.mode} model for {job.phrase!r} on {device} ({why})")
    piper_dir = work / "piper"
    use_piper = job.size("piper") > 0
    if use_piper:
        import importlib.util
        if importlib.util.find_spec("piper") is None:
            raise RuntimeError('this run mixes in Piper voices: .venv\\Scripts\\python -m pip install "piper-tts>=1.8" '
                               "(or --size piper=0 for Kokoro only)")
    have =D.have_bytes(feat_dir, job.size("acav")) + (PV.have_bytes(piper_dir) if use_piper else 0)
    prog = Progress(progress_cb, estimates(job, device, have))
    report: dict = dict(mode=job.mode, phrase=job.phrase, device=device, device_why=why,
                        when=time.strftime("%Y-%m-%d %H:%M:%S"), sizes={k: job.size(k) for k in SIZES[job.mode]})

    # 1. download -----------------------------------------------------------------------
    prog.stage("download")
    total = D.plan_bytes(job.size("acav")) + (PV.plan_bytes() if use_piper else 0)
    prog.update(0, f"Downloads: {D.fmt_mb(have)} of {D.fmt_mb(total)}")

    def dl(base, what="Negative features"):
        return lambda d, t: prog.update((base + d) / total, f"{what}: {D.fmt_mb(base + d)} of {D.fmt_mb(total)}")
    val_path = D.validation_set(feat_dir, dl(0), cancel)
    acav_paths = D.acav_slices(job.size("acav"), feat_dir, dl(D.VAL_BYTES), cancel)
    piper_model = None
    if use_piper:
        piper_model = PV.ensure_voice(piper_dir, dl(D.plan_bytes(job.size("acav")), "Piper voice"), cancel, log=log)

    # 2. voices -------------------------------------------------------------------------
    prog.stage("voices")
    held = set(S.HELD_OUT_VOICES) if job.mode == "default" else set()
    train_voices = [v for v in S.KOKORO_ALL if v not in held]
    variants = S.phrase_variants(job.phrase)
    sets = plan_sets(job, rng, variants, held, train_voices, use_piper)
    clips: dict[str, list[np.ndarray]] = {}
    todo = {}
    for name, utts in sets.items():
        f = cache / f"clips-{name}-{_key([u.key() for u in utts])}.npz"
        if f.is_file():
            clips[name] = _load_clips(f)
        else:
            todo[name] = (utts, f)
    n_todo = sum(len(u) for u, _ in todo.values())
    if todo:
        engines = []
        want = [u.voice for utts, _ in todo.values() for u in utts]
        need_kokoro = any(not v.startswith(S.PIPER_PREFIX) for v in want)
        pm = piper_model if any(v.startswith(S.PIPER_PREFIX) for v in want) else None
        try:
            engines = make_engines(device, job.tts, piper_model=pm, kokoro=need_kokoro)
        except Exception as e:
            if device != "cuda":
                raise
            log(f"[wake_train] synthesis on the GPU failed ({e}); using the CPU")
            device, report["device"], report["device_why"] = "cpu", "cpu", f"GPU failed: {e}"[:120]
            engines = make_engines("cpu", job.tts, piper_model=pm, kokoro=need_kokoro)
        done_before = 0
        for name, (utts, f) in todo.items():
            def p(d, t, base=done_before):
                prog.update((base + d) / n_todo, f"Synthesising voices: {base + d} of {n_todo} clips")
            clips[name] = render_parallel(utts, engines, p, cancel)
            _save_clips(f, clips[name])
            done_before += len(utts)
        del engines
    report["clips"] = {k: sum(1 for c in v if len(c)) for k, v in clips.items()}

    # user data
    user = [read_wav16(p) for p in job.user_clips]
    from ..wake_samples import is_held_out
    user_train = [c for c, p in zip(user, job.user_clips) if not is_held_out(Path(p))]
    user_held = [c for c, p in zip(user, job.user_clips) if is_held_out(Path(p))]
    if job.mode == "personal" and job.user_clips and not user_held:   # few clips, odd names: hold some out anyway
        user_held, user_train = user_train[3::4], [c for i, c in enumerate(user_train) if i % 4 != 3]
    room = [read_wav16(p) for p in job.room]
    false_w = [read_wav16(p) for p in job.false_wakes]
    report["user"] = dict(train=len(user_train), held_out=len(user_held), room_s=round(sum(map(len, room)) / 16000, 1),
                          false_wakes=len(false_w))

    # 3. windows + features (in chunks, so the audio never piles up in memory) ---------------
    prog.stage("features")
    fkey = _key([u.key() for v in sets.values() for u in v], job.seed, job.size("aug_pos"), job.size("user_copies"),
                [str(Path(x).resolve()) for x in job.user_clips + job.room + job.false_wakes], A.WINDOW, "v2")
    fcache = cache / f"features-{fkey}.npz"
    if job.mode == "default" and fcache.is_file():   # personal runs always redo it (the clips may have changed)
        z = np.load(fcache, allow_pickle=False)
        fx = {k[3:]: z[k] for k in z.files if k.startswith("fx_")}
        ev, eval_pos = z["ev"], [str(x) for x in z["eval_src"]]
        report["windows"] = {k: int(v.shape[0]) for k, v in fx.items()}
        log(f"[wake_train] features from cache {fcache.name}")
    else:
        fx, ev, eval_pos = _windows_and_features(job, clips, user_train, user_held, room, false_w, device, nrng,
                                                 prog, cancel, log)
        report["windows"] = {k: int(v.shape[0]) for k, v in fx.items()}
        if job.mode == "default":
            np.savez(fcache, ev=ev, eval_src=np.array(eval_pos), **{"fx_" + k: v for k, v in fx.items()})
    acav = D.load_acav(acav_paths)

    # 5. train ------------------------------------------------------------------------------------
    prog.stage("train")
    pools = [H.Pool("pos", fx.get("pos", np.zeros((0, 16, 96))), 1, 1.0)]
    if "user" in fx:
        pools.append(H.Pool("user", fx["user"], 1, 1.0, weight=1.0))
        pools[0].share = 0.5                        # half synthetic, half the user's own voice
        pools[1].share = 0.5
    neg_spec = [("acav", acav, 0.50, 1.0), ("look", fx.get("look"), 0.20, 1.0), ("speech", fx.get("speech"), 0.15, 1.0),
                ("noise", fx.get("noise"), 0.05, 1.0), ("room", fx.get("room"), 0.07, 1.5),
                ("fw", fx.get("fw"), 0.03, 3.0)]
    for name, x, share, wgt in neg_spec:
        if x is not None and len(x):
            pools.append(H.Pool(name, x, 0, share, wgt))
    steps = job.size("steps")
    params = H.train_ensemble(pools, n=job.size("heads"), steps=steps, seed=job.seed, cancel=cancel, log=log,
                              progress=lambda s, n, l: prog.update(s / n, f"Training: step {s} of {n} (loss {l:.3f})"))

    # 6. check ------------------------------------------------------------------------------------
    prog.stage("check")
    frames = D.load_validation(val_path)
    hours = frames.shape[0] * FRAME_S / 3600
    vl = validation_scores(params, frames, logit=True)
    prog.update(0.7)
    src = list(eval_pos)
    pos_l = H.ens_logits(params, ev.reshape(-1, 16, 96)).reshape(-1, 4).max(1) if len(ev) else np.zeros(0)
    if job.mode == "personal" and any(s == "yours" for s in src):
        sel = np.array([s == "yours" for s in src])
        rows, rec = choose(pos_l[sel], vl, hours, WM.TARGET_HIT, 0.5)   # personal target: <= 1 per 2 h
    else:
        rows, rec = choose(pos_l, vl, hours, WM.TARGET_HIT, WM.TARGET_FPH)
    t_logit = rec.get("threshold")
    t_logit = 0.0 if t_logit is None else float(t_logit)
    thr = float(sigmoid(t_logit))
    H.calibrate(params, thr)
    vs_c = validation_scores(params, frames)
    pos_c = H.predict(params, ev.reshape(-1, 16, 96)).reshape(-1, 4).max(1) if len(ev) else np.zeros(0)
    by_src = {}
    for s in sorted(set(src)):
        sel = np.array([x == s for x in src])
        by_src[s] = dict(n=int(sel.sum()), hit_at_0_5=round(float(np.mean(pos_c[sel] >= 0.5)), 4))
    n_false = len(WM.count_triggers(vs_c, 0.5, REFRACTORY_FRAMES))
    # the sweep, re-expressed as thresholds of the calibrated model (0.5 = the chosen one)
    sweep = [dict(r, threshold=round(float(sigmoid(r["threshold"] - t_logit)), 3)) for r in rows
             if abs(round((r["threshold"] - t_logit) * 2) / 2 - (r["threshold"] - t_logit)) < 1e-6
             and abs(r["threshold"] - t_logit) <= 4]
    report.update(validation_hours=round(hours, 2), chosen_raw_threshold=thr, chosen_logit=round(t_logit, 2),
                  recommendation=rec, at_0_5=dict(hit=by_src, false_wakes=n_false, fph=round(n_false / hours, 3)),
                  sweep=sweep, heads=len(params) if isinstance(params, list) else 1)
    log(f"[wake_train] threshold logit {t_logit:.1f} -> 0.5; hit {by_src}; false wakes {n_false} in {hours:.1f} h "
        f"({n_false / hours:.2f}/h)")

    # 7. export ---------------------------------------------------------------------------------
    prog.stage("export")
    from .export import export
    meta = dict(phrase=job.phrase, mode=job.mode, trained=time.strftime("%Y-%m-%d"),
                trainer="hey-claude heyclaude.wake_train", threshold="0.5 (calibrated)",
                license="CC BY-NC-SA 4.0 (trained with openWakeWord's ACAV100M negative features; see models/LICENSE)")
    export(params, out, meta)
    report.update(model=str(out), seconds=round(time.time() - t_start, 1))
    out.with_suffix(".report.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    prog.finish()
    log(f"[wake_train] wrote {out} in {report['seconds'] / 60:.1f} min")
    return report


def job_from_dict(d: dict) -> Job:
    return Job(**{k: v for k, v in d.items() if k in Job.__dataclass_fields__})


def job_to_dict(j: Job) -> dict:
    return asdict(j)
