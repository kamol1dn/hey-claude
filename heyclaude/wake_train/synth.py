"""Synthetic speech for wake-word training: the phrase (positives) and
look-alikes / ordinary speech (negatives), spoken by many local Kokoro voices.

Variety comes from:
- all 54 Kokoro voices, including the non-English ones (Japanese, Chinese,
  Hindi, French, Italian, Spanish, Portuguese) reading English phonemes, which
  gives accented English, plus random blends of two voices (new speakers);
- several pronunciations of the phrase, given as IPA phonemes so the sound is
  exactly what we mean (the M2 spellings klod / clod / klawd / klode, and
  Russian/Uzbek-style ones: open short "o", a devoiced final "t");
- speed 0.75-1.35 and pitch -3..+3 semitones (resampling).

`Utterance` is one planned clip; `plan()` draws them; `Synth.render()` turns
them into 16 kHz int16 arrays. The TTS engine is injected (`engine.create(text,
voice, speed, lang, is_phonemes) -> (float32 audio, rate)`), so tests use a fake.
"""
from __future__ import annotations

import random
import re
import time
from dataclasses import asdict, dataclass

import numpy as np

RATE = 16000

# Kokoro v1.0 voices (voices-v1.0.bin). First letter = language of the voice:
# a/b American/British English, e Spanish, f French, h Hindi, i Italian,
# j Japanese, p Portuguese, z Chinese.
KOKORO_ALL = (
    "af_alloy", "af_aoede", "af_bella", "af_heart", "af_jessica", "af_kore", "af_nicole", "af_nova", "af_river",
    "af_sarah", "af_sky", "am_adam", "am_echo", "am_eric", "am_fenrir", "am_liam", "am_michael", "am_onyx",
    "am_puck", "am_santa", "bf_alice", "bf_emma", "bf_isabella", "bf_lily", "bm_daniel", "bm_fable", "bm_george",
    "bm_lewis", "ef_dora", "em_alex", "em_santa", "ff_siwis", "hf_alpha", "hf_beta", "hm_omega", "hm_psi",
    "if_sara", "im_nicola", "jf_alpha", "jf_gongitsune", "jf_nezumi", "jf_tebukuro", "jm_kumo", "pf_dora",
    "pm_alex", "pm_santa", "zf_xiaobei", "zf_xiaoni", "zf_xiaoxiao", "zf_xiaoyi", "zm_yunjian", "zm_yunxi",
    "zm_yunxia", "zm_yunyang",
)
# Held out of training for the default model, to measure the hit rate on voices it never heard:
# two English, and one each of the accent families.
HELD_OUT_VOICES = ("af_kore", "bm_lewis", "am_echo", "jf_nezumi", "zm_yunxia", "hm_omega", "pm_santa", "if_sara")

# "hey claude" pronunciations. ("text", None) = phonemized by espeak (en-us / en-gb);
# ("ipa", phonemes) = exact. Weights: how often each is drawn.
HEY_CLAUDE = [
    # (label, text or None, phonemes or None, weight)
    ("hey claude", "hey claude", None, 6),            # American "klawd" (en-us) / British (en-gb)
    ("hey klod", None, "hˈeɪ klˈɑːd", 2),              # M2 spelling: short "o", Russian "Клод" (US reading)
    ("hey clod", None, "hˈeɪ klˈɒd", 2),               # the same, British rounded "o"
    ("hey klawd", None, "hˈeɪ klˈɔːd", 2),             # long open "aw"
    ("hey klode", None, "hˈeɪ klˈoʊd", 1),             # "klohd", a common non-native variant
    ("hey klode (gb)", None, "hˈeɪ klˈəʊd", 1),
    ("hej klod (ru)", None, "hˈɛj klˈɔd", 2),          # Russian/Uzbek: "хэй клод", pure vowels
    ("hej klot (ru)", None, "hˈɛj klˈɔt", 2),          # ...with the final "d" devoiced
    ("hey klot", None, "hˈeɪ klˈɔːt", 1),
    ("hej klood", None, "hˈej klˈoːd", 1),             # pure long "o" (German/Uzbek-style)
    ("hey, claude", None, "hˈeɪ, klˈɔːd", 1),          # with a little pause
]

# Look-alikes: things that must NOT wake it (M2's list plus a few more).
HEY_CLAUDE_NEGATIVES = [
    "hey cloud", "hey clyde", "hey claudia", "hey clara", "hey lord", "hey call", "they claw", "hey jarvis",
    "okay google", "hey clouds", "hey claw", "hey clot", "hey cloth", "hey glad", "hey god", "hey clock",
    "hey cod", "hey load", "hey code", "hey crowd", "hey proud", "a cloud", "the cloud", "say cloud",
    "hey, closed", "hey colleague", "hey Chloe", "hey clan", "hey Klaus", "hey Clark", "hey Claudio",
    "hey guys", "hey there", "hey you", "hey Siri", "Alexa", "claude", "Claude Code", "I asked Claude",
    "ask Claude", "Claude said", "applaud", "fraud", "it's loud", "play cloud", "pay cloud", "hay load",
]

# Ordinary sentences: lecture / office / home talk, with near-miss words sprinkled in.
SENTENCES = [
    "Let's move on to the next slide.", "The cloud provider bills by the hour.", "Can you hear me now?",
    "We'll take a short break and come back at three.", "Please close the door behind you.",
    "The clause in the contract was unclear.", "I think the answer is forty two.", "Hey, how was your weekend?",
    "The data is stored in the cloud.", "Open the window, it's hot in here.", "Did you see the game last night?",
    "That's a really good question.", "The lecture starts at nine tomorrow.", "Can you send me the file?",
    "I'll call you back in five minutes.", "The model was trained on a large dataset.", "Hey, are you coming?",
    "This function returns a list of strings.", "Turn left at the next corner.", "The meeting has been moved.",
    "Claudia will join us later today.", "He clawed his way to the top.", "The crowd was very loud.",
    "Clyde, could you pass the salt?", "We need to deploy it to the cloud by Friday.", "Say that again, please.",
    "The recording is a bit too quiet.", "I bought some bread and milk.", "Let me share my screen.",
    "According to the paper, the results are significant.", "What time does the train leave?",
    "The coffee machine is broken again.", "Hey, look at this.", "Please mute your microphone.",
    "We should applaud their effort.", "Close the laptop and listen.", "Let's go over the homework.",
    "The cloud service is down again.", "That clause doesn't apply here.", "Hey team, quick update.",
    "The lord of the manor lived here.", "I'm calling about the invoice.", "The clock is five minutes fast.",
    "Do you want tea or coffee?", "The code compiles without warnings.", "My cat sleeps all day.",
    "Hey, can you help me with this?", "Good morning everyone.", "The exam will cover chapters one to five.",
    "Write the equation on the board.", "The capital of France is Paris.", "Let's start with a quick recap.",
    "I asked Claude to summarize the report.", "Claude Code is running in the terminal.",
    "The weather forecast says rain.", "Hey, nice to meet you.", "Are there any questions?",
    "We're out of time for today.", "Remember to submit your assignment.", "He's a bit of a clod, honestly.",
    "Okay, so where were we?", "The clown made everyone laugh.", "Loud music is not allowed after ten.",
]

# Short random-word phrases, for more non-wake variety.
WORDS = ("hey hi okay so well the a cloud claw clown clear clean close cloth clock clot load road lord laud "
         "call cold code cod god glad clad plaid played made say they day may hey stay play played sorry "
         "listen look come go stop wait right left yes no maybe today tonight morning evening slide class "
         "student teacher question answer problem system data model voice audio music video phone screen").split()


@dataclass(frozen=True)
class Utterance:
    """One clip to synthesise."""
    label: str                     # what it says, for reports
    text: str                      # text, or IPA phonemes when is_phonemes
    is_phonemes: bool
    voice: str                     # "af_heart" or "af_heart+jm_kumo@0.35" (blend: 35% of the second)
    speed: float
    pitch: float                   # semitones
    lang: str = "en-us"

    def key(self) -> str:
        return "|".join(str(v) for v in asdict(self).values())


def phrase_variants(phrase: str) -> list[tuple]:
    """(label, text, phonemes, weight) pronunciations for a phrase."""
    if " ".join(phrase.lower().split()) in ("hey claude", "hey, claude"):
        return list(HEY_CLAUDE)
    p = " ".join(phrase.split())
    return [(p, p, None, 3), (p + ",", p + ",", None, 1), (p.lower() + "?", p.lower() + "?", None, 1)]


def negative_phrases(phrase: str) -> list[str]:
    """Look-alikes for a phrase: the built-in list for "hey claude", otherwise
    simple edits (each word alone, a changed first/last word, near-rhymes)."""
    if " ".join(phrase.lower().split()) in ("hey claude", "hey, claude"):
        return list(HEY_CLAUDE_NEGATIVES)
    words = phrase.lower().replace(",", " ").split()
    out = set()
    if len(words) > 1:
        out.update(words)                                    # each word on its own
        out.add(" ".join(words[1:]))
        out.update(f"{w} {' '.join(words[1:])}" for w in ("hi", "they", "say", "play", "okay", "a", "the"))
        out.update(f"{' '.join(words[:-1])} {w}" for w in ("there", "you", "guys", "cloud", "man"))
    last = words[-1] if words else phrase
    vowels = "aeiouy"
    for v in vowels:                                         # swap the first vowel of the last word
        m = re.search(f"[{vowels}]", last)
        if m and last[m.start()] != v:
            out.add(" ".join(words[:-1] + [last[:m.start()] + v + last[m.end():]]))
    out.discard(phrase.lower())
    out.update(["hey jarvis", "okay google", "hey siri", "alexa", "hey there"])
    return sorted(out)


def plan(rng: random.Random, n: int, variants: list[tuple], voices: list[str], blend_p: float = 0.35,
         speed=(0.75, 1.35), pitch=(-3.0, 3.0)) -> list[Utterance]:
    """Draw n utterances: a pronunciation by weight, a voice (or a blend of two),
    a speed, a pitch, and en-us / en-gb for text that espeak reads."""
    weights = [v[3] for v in variants]
    out = []
    for _ in range(n):
        label, text, ph, _w = rng.choices(variants, weights)[0]
        v = rng.choice(voices)
        if len(voices) > 1 and rng.random() < blend_p:
            w = rng.choice([x for x in voices if x != v])
            v = f"{v}+{w}@{rng.uniform(0.2, 0.6):.2f}"
        lang = "en-us" if (ph is not None or rng.random() < 0.6) else "en-gb"
        out.append(Utterance(label, ph if ph is not None else text, ph is not None, v,
                             round(rng.uniform(*speed), 2), round(rng.uniform(*pitch), 1), lang))
    return out


def plan_text(rng: random.Random, n: int, texts: list[str], voices: list[str], **kw) -> list[Utterance]:
    """Utterances of plain text (negatives)."""
    return plan(rng, n, [(t, t, None, 1) for t in texts], voices, **kw)


def random_word_phrases(rng: random.Random, n: int, k=(2, 5)) -> list[str]:
    return [" ".join(rng.choice(WORDS) for _ in range(rng.randint(*k))) for _ in range(n)]


# -- audio -------------------------------------------------------------------------
def resample(x: np.ndarray, src: int, dst: int) -> np.ndarray:
    """float32 resample with a polyphase filter (scipy)."""
    if src == dst:
        return np.asarray(x, np.float32)
    from math import gcd
    from scipy.signal import resample_poly
    g = gcd(int(src), int(dst))
    return resample_poly(np.asarray(x, np.float32), int(dst) // g, int(src) // g).astype(np.float32)


def to_16k(audio: np.ndarray, rate: int, pitch: float = 0.0) -> np.ndarray:
    """TTS output -> 16 kHz int16, pitch-shifted by `pitch` semitones (by
    resampling, which also changes the tempo a little, like a tape)."""
    f = 2.0 ** (pitch / 12.0)
    # Pretend it was recorded at a different rate, rounded to 400 Hz (~0.3 semitone)
    # so the resampling ratio stays small (a 16000/25427 ratio designs a huge filter).
    src = int(round(rate * f / 400.0)) * 400 if pitch else int(rate)
    y = resample(audio, src, RATE)
    peak = float(np.max(np.abs(y))) if y.size else 0.0
    if peak > 0:
        y = y / peak * 0.7                         # normalise; augment.gain varies the level later
    return np.clip(y * 32767, -32768, 32767).astype(np.int16)


class Synth:
    """Renders Utterances with a Kokoro-like engine.

    engine.create(text, voice=..., speed=..., lang=..., is_phonemes=...) -> (audio float32, rate)
    engine.voices: dict name -> style array (needed only for blends)
    """

    def __init__(self, engine):
        self.engine = engine

    def _voice(self, spec: str):
        if "+" not in spec:
            return spec
        a, rest = spec.split("+", 1)
        b, w = rest.split("@")
        w = float(w)
        vs = self.engine.voices
        return (1 - w) * np.asarray(vs[a]) + w * np.asarray(vs[b])

    def render_one(self, u: Utterance) -> np.ndarray:
        audio, rate = self.engine.create(u.text, voice=self._voice(u.voice), speed=u.speed, lang=u.lang,
                                         is_phonemes=u.is_phonemes)
        return to_16k(np.asarray(audio, np.float32), int(rate), u.pitch)

    def render(self, utts: list[Utterance], progress=None, cancel=None) -> list[np.ndarray]:
        """All clips (an empty array for any that failed). progress(done, total)."""
        out = []
        for i, u in enumerate(utts):
            if cancel and cancel():
                raise InterruptedError("cancelled")
            try:
                out.append(self.render_one(u))
            except InterruptedError:
                raise
            except Exception as e:                 # one odd phoneme string mustn't stop a run
                print(f"[synth] skipped {u.label!r} ({u.voice}): {e}")
                out.append(np.zeros(0, np.int16))
            if progress:
                progress(i + 1, len(utts))
        return out


def load_kokoro(device: str = "cuda", threads: int = 4, model: str | None = None, voices: str | None = None):
    """A kokoro_onnx.Kokoro on "cuda" or "cpu" (raises if CUDA isn't usable)."""
    import onnxruntime as ort
    from kokoro_onnx import Kokoro

    from ..tts import kokoro_paths, ort_cuda_available
    m, v = kokoro_paths({"kokoro_model": model, "kokoro_voices": voices})
    so = ort.SessionOptions()
    so.log_severity_level = 3
    if device == "cuda":
        ok, why = ort_cuda_available()
        if not ok:
            raise RuntimeError(why)
        providers = [("CUDAExecutionProvider", {"cudnn_conv_algo_search": "HEURISTIC",
                                                "arena_extend_strategy": "kSameAsRequested"}), "CPUExecutionProvider"]
    else:
        so.intra_op_num_threads = int(threads)
        providers = ["CPUExecutionProvider"]
    t = time.perf_counter()
    sess = ort.InferenceSession(m, sess_options=so, providers=providers)
    if device == "cuda" and sess.get_providers()[0] != "CUDAExecutionProvider":
        raise RuntimeError("onnxruntime fell back to the CPU")
    k = Kokoro.from_session(sess, v)
    k.create("hey", voice="af_heart")
    print(f"[synth] kokoro on {device} in {time.perf_counter() - t:.1f}s")
    return k
