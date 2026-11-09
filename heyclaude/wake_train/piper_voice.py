"""Piper (piper-tts >= 1.8) as a second synthetic-voice source for wake-word training.

Kokoro gives 54 clean, studio-like voices. Piper's multi-speaker LibriTTS-R
model (`en_US-libritts_r-medium`, 904 speakers) was trained on LibriVox
audiobook readers: ordinary people with ordinary microphones, so its voices are
much closer to the people who will actually say "hey claude". Speaker ids act
as voices: `"piper:123"` is speaker 123.

The model (75 MB + a 20 KB config, from https://huggingface.co/rhasspy/piper-voices,
pinned revision, MIT licence; the LibriTTS-R speakers are CC BY 4.0) is fetched
with the same resumable ranged download as the negative features
(`download.fetch_range`, `HF_HUB_DISABLE_XET=1`) and checked against its SHA-256.

`PiperEngine` fits `synth.Synth`'s engine interface:
    create(text, voice="piper:123", speed=1.0, lang="en-us", is_phonemes=False) -> (float32 audio, rate)
IPA phonemes (the accent spellings in synth.HEY_CLAUDE) go straight to the
model's phoneme ids; Piper's ids are espeak-ng IPA characters, the same
alphabet Kokoro's phonemes use. Piper reads English text with en-us rules
whatever `lang` says. Piper speakers can't be blended (the speaker embedding
lives inside the model), so `plan` never blends them.

`MixedEngine` routes "piper:..." voices to Piper and everything else to Kokoro,
so one `Synth` can render a mixed plan.
"""
from __future__ import annotations

import hashlib
import time
import unicodedata
import zlib
from pathlib import Path

import numpy as np

from . import download as D
from .synth import PIPER_PREFIX as PREFIX

REPO = "https://huggingface.co/rhasspy/piper-voices/resolve/"
REVISION = "e7229f7d33a10e145e109b886d661984201ac3c4"            # pinned, so the voice never changes
VOICE = "en_US-libritts_r-medium"
VOICE_DIR = "en/en_US/libritts_r/medium"
# (file, bytes, sha256 or None). The .json is small and not in LFS; its size is checked.
FILES = (
    (f"{VOICE}.onnx", 78_580_914, "10bb85e071d616fcf4071f369f1799d0491492ab3c5d552ec19fb548fac13195"),
    (f"{VOICE}.onnx.json", 20_123, None),
)
N_SPEAKERS = 904
# Held out of training for the default model (every 20th speaker, 45 of 904), to
# measure the hit rate on voices it never heard.
HELD_OUT_EVERY, HELD_OUT_OFFSET = 20, 7


def voice_name(speaker: int) -> str:
    return f"{PREFIX}{int(speaker)}"


def is_piper(voice) -> bool:
    return isinstance(voice, str) and voice.startswith(PREFIX)


def speaker_id(voice: str) -> int:
    return int(voice[len(PREFIX):])


def held_out_speakers(n: int = N_SPEAKERS) -> list[int]:
    return [s for s in range(n) if s % HELD_OUT_EVERY == HELD_OUT_OFFSET]


def train_speakers(n: int = N_SPEAKERS) -> list[int]:
    held = set(held_out_speakers(n))
    return [s for s in range(n) if s not in held]


# -- download -----------------------------------------------------------------------------
def plan_bytes() -> int:
    return sum(size for _, size, _ in FILES)


def have_bytes(folder: Path) -> int:
    n = 0
    for name, _, _ in FILES:
        for p in (Path(folder) / name, Path(folder) / (name + ".part")):
            if p.is_file():
                n += p.stat().st_size
    return n


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def ensure_voice(folder: Path, progress=None, cancel=None, opener=None, log=print) -> Path:
    """Download (or reuse) the LibriTTS-R voice into `folder`; returns the .onnx path.
    progress(done_bytes, total_bytes). Resumes `.part` files; a file whose SHA-256
    doesn't match is deleted and the error raised (the next run fetches it again)."""
    folder = Path(folder)
    total = plan_bytes()
    if have_bytes(folder) < total:
        log(f"[wake_train] Piper voice {VOICE}: {D.fmt_mb(total)} to download "
            f"({D.fmt_mb(have_bytes(folder))} already here)")
    base = 0
    for name, size, digest in FILES:
        dest = folder / name
        fresh = not dest.is_file()
        D.fetch_range(f"{REPO}{REVISION}/{VOICE_DIR}/{name}", dest, 0, size,
                      (lambda d, t, b=base: progress(b + d, total)) if progress else None, cancel, opener)
        if digest and fresh and _sha256(dest) != digest:
            dest.unlink()
            raise OSError(f"{name}: SHA-256 mismatch (deleted; it will be downloaded again)")
        base += size
    return folder / FILES[0][0]


# -- the engine ---------------------------------------------------------------------------
class PiperEngine:
    """A piper.PiperVoice behind the Kokoro-style `create()`.

    Besides the speed, every clip gets its own noise_scale (0.45-0.85) and
    noise_w (0.5-1.0), Piper's knobs for intonation and timing variety, drawn
    from a hash of the request so a re-run renders the same audio."""

    voices: dict = {}                       # no style vectors: Piper speakers aren't blended

    def __init__(self, voice, phonemize_lock=None, syn_config=None):
        self.voice = voice                  # piper.PiperVoice (or a fake in tests)
        self._syn_config = syn_config       # piper.SynthesisConfig (injectable for tests)
        cfg = voice.config
        self.rate = int(cfg.sample_rate)
        self.n_speakers = int(getattr(cfg, "num_speakers", 1) or 1)
        self._lock = phonemize_lock

    def phonemes(self, text: str, is_phonemes: bool) -> list[str]:
        if is_phonemes:                     # IPA string -> one id per character (NFD, like Piper's phonemizer)
            return list(unicodedata.normalize("NFD", text))
        if self._lock is not None:          # espeak-ng isn't thread-safe
            with self._lock:
                sents = self.voice.phonemize(text)
        else:
            sents = self.voice.phonemize(text)
        out: list[str] = []
        for i, s in enumerate(sents):
            if i:
                out.append(" ")
            out.extend(s)
        return out

    def create(self, text, voice="piper:0", speed=1.0, lang="en-us", is_phonemes=False):
        if self._syn_config is None:
            from piper import SynthesisConfig
            self._syn_config = SynthesisConfig
        sid = speaker_id(voice) if is_piper(voice) else int(voice)
        if not 0 <= sid < self.n_speakers:
            raise ValueError(f"speaker {sid} out of range (0..{self.n_speakers - 1})")
        h = zlib.crc32(f"{text}|{sid}|{speed}".encode())
        r = np.random.default_rng(h)
        cfg = self._syn_config(speaker_id=sid, length_scale=1.0 / float(speed),
                              noise_scale=float(r.uniform(0.45, 0.85)), noise_w_scale=float(r.uniform(0.5, 1.0)),
                              normalize_audio=False)
        ids = self.voice.phonemes_to_ids(self.phonemes(text, is_phonemes))
        audio = self.voice.phoneme_ids_to_audio(ids, cfg)
        if isinstance(audio, tuple):        # (audio, alignments) in some versions
            audio = audio[0]
        return np.asarray(audio, np.float32).reshape(-1), self.rate


class MixedEngine:
    """Routes "piper:N" voices to a PiperEngine and the rest (names, blends) to Kokoro."""

    def __init__(self, kokoro=None, piper=None):
        self.kokoro, self.piper = kokoro, piper

    @property
    def voices(self):
        return getattr(self.kokoro, "voices", {})

    def create(self, text, voice, speed=1.0, lang="en-us", is_phonemes=False):
        if is_piper(voice):
            if self.piper is None:
                raise RuntimeError("a Piper voice was planned but Piper isn't loaded")
            return self.piper.create(text, voice=voice, speed=speed, lang=lang, is_phonemes=is_phonemes)
        if self.kokoro is None:
            raise RuntimeError("a Kokoro voice was planned but Kokoro isn't loaded")
        return self.kokoro.create(text, voice=voice, speed=speed, lang=lang, is_phonemes=is_phonemes)


def load_piper(model: Path, device: str = "cpu", threads: int = 4, phonemize_lock=None) -> PiperEngine:
    """A PiperEngine for the downloaded voice, on "cuda" or "cpu" (raises if CUDA isn't usable)."""
    import json

    import onnxruntime as ort
    from piper import PiperVoice
    from piper.config import PiperConfig
    t = time.perf_counter()
    so = ort.SessionOptions()
    so.log_severity_level = 3
    if device == "cuda":
        providers = [("CUDAExecutionProvider", {"cudnn_conv_algo_search": "HEURISTIC",
                                                "arena_extend_strategy": "kSameAsRequested"}), "CPUExecutionProvider"]
    else:
        so.intra_op_num_threads = int(threads)
        providers = ["CPUExecutionProvider"]
    sess = ort.InferenceSession(str(model), sess_options=so, providers=providers)
    if device == "cuda" and sess.get_providers()[0] != "CUDAExecutionProvider":
        raise RuntimeError("onnxruntime fell back to the CPU")
    cfg = PiperConfig.from_dict(json.loads(Path(str(model) + ".json").read_text(encoding="utf-8")))
    eng = PiperEngine(PiperVoice(config=cfg, session=sess), phonemize_lock)
    eng.create("hey", voice=voice_name(0))
    print(f"[synth] piper ({eng.n_speakers} speakers) on {device} in {time.perf_counter() - t:.1f}s")
    return eng
