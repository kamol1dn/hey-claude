"""Augmentation and window placement (16 kHz int16 in, int16 out).

Every training example is a 2 s window (WINDOW samples), which openWakeWord's
feature models turn into exactly 16 x 96 embedding frames. A positive clip is
placed so the phrase *ends* 0-0.25 s before the window ends (that's when the
app's streaming scores should fire); the rest of the window is background.

Augmentations (each with a probability, in this order): speed, room reverb
(a synthetic impulse response: exponentially decaying noise, RT60 0.1-0.9 s,
so nothing has to be downloaded), background noise at an SNR (coloured noise,
or a real background clip such as the user's room recording), and gain.
"""
from __future__ import annotations

import numpy as np

RATE = 16000
WINDOW = 32000                  # 2.0 s -> 16 embedding frames


def as_float(x: np.ndarray) -> np.ndarray:
    return np.asarray(x, np.float32) / 32768.0


def as_int16(x: np.ndarray) -> np.ndarray:
    return np.clip(np.round(np.asarray(x, np.float32) * 32768.0), -32768, 32767).astype(np.int16)


def rms(x: np.ndarray) -> float:
    x = np.asarray(x, np.float32)
    return float(np.sqrt(np.mean(x ** 2))) if x.size else 0.0


# -- single effects (float32 in -1..1) ----------------------------------------------
def change_speed(x: np.ndarray, factor: float) -> np.ndarray:
    """Faster (>1) or slower (<1), pitch moves with it."""
    if abs(factor - 1.0) < 1e-3 or x.size == 0:
        return x
    from math import gcd
    from scipy.signal import resample_poly
    up, down = 50, int(round(50 * factor))         # 2% steps keep the polyphase filter short
    g = gcd(up, down)
    return resample_poly(x, up // g, down // g).astype(np.float32)


def room_ir(rng: np.random.Generator, rt60: float, rate: int = RATE) -> np.ndarray:
    """Synthetic room impulse response: a direct path, then decaying noise."""
    n = max(16, int(rt60 * rate))
    t = np.arange(n) / rate
    decay = np.exp(-6.9 * t / rt60)                     # -60 dB at rt60
    ir = rng.standard_normal(n).astype(np.float32) * decay.astype(np.float32)
    pre = int(rng.uniform(0.002, 0.012) * rate)         # early reflections start after the direct sound
    ir[:pre] = 0
    ir *= rng.uniform(0.15, 0.6) / (np.sqrt(np.sum(ir ** 2)) + 1e-9)
    ir[0] = 1.0
    return ir


def reverb(x: np.ndarray, rng: np.random.Generator, rt60=(0.1, 0.9)) -> np.ndarray:
    if x.size == 0:
        return x
    from scipy.signal import fftconvolve
    y = fftconvolve(x, room_ir(rng, rng.uniform(*rt60)))[: x.size].astype(np.float32)
    p = np.max(np.abs(y))
    return y / p * np.max(np.abs(x)) if p > 0 else y


def coloured_noise(rng: np.random.Generator, n: int, colour: str = "white") -> np.ndarray:
    """white / pink (1/f) / brown (1/f^2) noise with unit RMS."""
    w = rng.standard_normal(n).astype(np.float32)
    if colour != "white" and n > 1:
        f = np.fft.rfft(w)
        k = np.arange(f.size, dtype=np.float32)
        k[0] = 1
        f /= k ** (0.5 if colour == "pink" else 1.0)
        w = np.fft.irfft(f, n).astype(np.float32)
    return w / (rms(w) + 1e-9)


def mix_at_snr(x: np.ndarray, noise: np.ndarray, snr_db: float) -> np.ndarray:
    """x + noise scaled so that rms(x) / rms(noise) = snr_db."""
    if noise.size < x.size:
        noise = np.resize(noise, x.size)
    rx, rn = rms(x), rms(noise[: x.size])
    if rx == 0 or rn == 0:
        return x
    return x + noise[: x.size] * (rx / rn) / (10 ** (snr_db / 20))


def gain_db(x: np.ndarray, db: float) -> np.ndarray:
    return x * (10 ** (db / 20))


# -- windows -----------------------------------------------------------------------
def place_end(clip: np.ndarray, rng: np.random.Generator, window: int = WINDOW,
              end_jitter=(0.0, 0.25)) -> np.ndarray:
    """Put `clip` into a silent window so it ends `end_jitter` seconds before
    the window's end (longer clips keep their last part)."""
    out = np.zeros(window, np.float32)
    clip = np.asarray(clip, np.float32)
    gap = int(rng.uniform(*end_jitter) * RATE)
    end = window - gap
    take = clip[-end:] if clip.size > end else clip
    out[end - take.size:end] = take
    return out


def background(rng: np.random.Generator, n: int, beds: list[np.ndarray] | None = None) -> np.ndarray:
    """n samples of background noise (unit-ish RMS): a slice of a real recording
    from `beds` (float32) when given, otherwise coloured noise."""
    if beds and rng.random() < 0.6:
        bed = beds[rng.integers(len(beds))]
        if bed.size >= n:
            s = rng.integers(0, bed.size - n + 1)
            seg = bed[s:s + n]
        else:
            seg = np.resize(bed, n)
        r = rms(seg)
        if r > 1e-5:
            return seg / r
    return coloured_noise(rng, n, rng.choice(["white", "pink", "brown"]))


def augment_window(clip_i16: np.ndarray, rng: np.random.Generator, beds: list[np.ndarray] | None = None,
                   p_speed=0.3, p_reverb=0.5, p_noise=0.8, snr=(3.0, 30.0), gain=(-18.0, 3.0),
                   placed: bool = True) -> np.ndarray:
    """One augmented 2 s training window (int16) from a clip.

    placed=True: the clip is end-aligned in the window (positives, short
    negatives). placed=False: the clip is already a full window (slices of
    long audio)."""
    x = as_float(clip_i16)
    if placed and rng.random() < p_speed:
        x = change_speed(x, rng.uniform(0.85, 1.15))
    if rng.random() < p_reverb:
        x = reverb(x, rng)
    w = place_end(x, rng) if placed else np.resize(x, WINDOW).astype(np.float32)
    if rng.random() < p_noise:
        w = mix_at_snr(w, background(rng, WINDOW, beds), rng.uniform(*snr))
    elif rms(w) > 0:
        w = w + coloured_noise(rng, WINDOW) * rms(w) * 10 ** (-45 / 20)   # never digital silence
    w = gain_db(w, rng.uniform(*gain))
    peak = float(np.max(np.abs(w)))
    if peak > 0.99:
        w = w / peak * 0.99
    return as_int16(w)


def slice_windows(pcm_i16: np.ndarray, hop: int = 16000, window: int = WINDOW) -> list[np.ndarray]:
    """Overlapping 2 s windows from long audio (negatives such as room sound)."""
    pcm = np.asarray(pcm_i16, np.int16)
    if pcm.size < window:
        return [np.concatenate([np.zeros(window - pcm.size, np.int16), pcm])] if pcm.size else []
    return [pcm[s:s + window] for s in range(0, pcm.size - window + 1, hop)]
