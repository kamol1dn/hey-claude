"""Cloud speech (M4c): Groq / OpenAI transcription, OpenAI / ElevenLabs voices.

Keys come only from environment variables (GROQ_API_KEY, OPENAI_API_KEY,
ELEVENLABS_API_KEY) or the Windows Credential Manager (generic credential
"hey-claude/<provider>", written by the settings window via set_key). Never
from config.yaml or files, and never logged: error reasons name the env var,
not the key.

One httpx client per backend instance, so the TLS connection is reused across
requests (a fresh handshake costs 100-300 ms per call).

Cost: PRICES (estimates) and monthly_cost()/cost_line(), which read
state/latency.csv to say what the cloud would cost at this user's usage.
"""
from __future__ import annotations

import csv
import os
import time
from datetime import datetime, timedelta
from typing import Callable, Mapping

import numpy as np

from .base import SpeechError, SttBackend, TtsBackend, wav_bytes, RATE

PROVIDERS = ("groq", "openai", "elevenlabs")
ENV_VARS = {"groq": "GROQ_API_KEY", "openai": "OPENAI_API_KEY", "elevenlabs": "ELEVENLABS_API_KEY"}
CRED_PREFIX = "hey-claude/"

STT_URLS = {
    "groq": "https://api.groq.com/openai/v1/audio/transcriptions",
    "openai": "https://api.openai.com/v1/audio/transcriptions",
}
STT_DEFAULT_MODELS = {"groq": "whisper-large-v3-turbo", "openai": "gpt-4o-mini-transcribe"}
OPENAI_TTS_URL = "https://api.openai.com/v1/audio/speech"
ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format=pcm_24000"
ELEVENLABS_DEFAULT_VOICE = "21m00Tcm4TlvDq8ikWAM"     # "Rachel", a public premade voice
TTS_RATE = 24000                                       # both providers: 24 kHz 16-bit LE mono PCM

_SHORT = {                                             # model -> island label
    "whisper-large-v3-turbo": "turbo", "whisper-large-v3": "large-v3",
    "gpt-4o-mini-transcribe": "mini", "gpt-4o-transcribe": "4o", "whisper-1": "whisper-1",
    "gpt-4o-mini-tts": "mini-tts", "tts-1": "tts-1", "tts-1-hd": "tts-1-hd",
    "eleven_flash_v2_5": "flash", "eleven_turbo_v2_5": "turbo",
    "eleven_multilingual_v2": "multilingual", "eleven_v3": "v3",
}


def _httpx():
    """httpx, imported lazily. Newer anthropic SDKs ship it as `httpx2`."""
    try:
        import httpx
    except ImportError:
        import httpx2 as httpx
    return httpx


# ---------------------------------------------------------------- keys

def _target(provider: str) -> str:
    return CRED_PREFIX + provider


def _decode_blob(blob) -> str | None:
    """CredentialBlob -> text. Written by CredWrite / cmdkey it's UTF-16-LE;
    other tools store UTF-8."""
    if blob is None:
        return None
    if isinstance(blob, str):
        s = blob
    else:
        b = bytes(blob)
        if len(b) % 2 == 0 and b"\x00" in b:
            s = b.decode("utf-16-le", errors="ignore")
        else:
            s = b.decode("utf-8", errors="ignore")
    s = s.strip("\x00").strip()
    return s or None


def cred_read(target: str) -> str | None:
    """The secret of a generic Windows credential, or None (none, or no pywin32)."""
    try:
        import win32cred
        c = win32cred.CredRead(target, win32cred.CRED_TYPE_GENERIC, 0)
    except Exception:                    # pywintypes.error 1168 (not found), ImportError
        return None
    return _decode_blob(c.get("CredentialBlob"))


def cred_write(target: str, secret: str, user: str = "hey-claude"):
    import win32cred
    win32cred.CredWrite({
        "Type": win32cred.CRED_TYPE_GENERIC,
        "TargetName": target,
        "UserName": user,
        "CredentialBlob": secret,          # pywin32 stores a str as UTF-16-LE
        "Comment": "hey-claude speech key",
        "Persist": win32cred.CRED_PERSIST_LOCAL_MACHINE,
    }, 0)


def cred_delete(target: str) -> bool:
    """True if something was deleted."""
    try:
        import win32cred
        win32cred.CredDelete(target, win32cred.CRED_TYPE_GENERIC, 0)
        return True
    except Exception:
        return False


def _check(provider: str):
    if provider not in PROVIDERS:
        raise ValueError(f"unknown provider {provider!r} (one of {', '.join(PROVIDERS)})")


def key_source(provider: str, env: Mapping[str, str] | None = None,
               cred: Callable[[str], str | None] | None = None) -> str | None:
    """Where the key would come from: "env", "credential store" or None.
    `env` / `cred` are injectable for tests (default os.environ / cred_read)."""
    _check(provider)
    env = os.environ if env is None else env
    if (env.get(ENV_VARS[provider]) or "").strip():
        return "env"
    if (cred or cred_read)(_target(provider)):
        return "credential store"
    return None


def get_key(provider: str, env: Mapping[str, str] | None = None,
            cred: Callable[[str], str | None] | None = None) -> str | None:
    """The API key for groq / openai / elevenlabs: the env var first, else the
    Windows Credential Manager entry "hey-claude/<provider>"."""
    _check(provider)
    env = os.environ if env is None else env
    k = (env.get(ENV_VARS[provider]) or "").strip()
    if k:
        return k
    k = (cred or cred_read)(_target(provider))
    return k.strip() if k else None


def set_key(provider: str, key: str, write: Callable[[str, str], None] | None = None):
    """Store the key in the Windows Credential Manager (settings window).
    An env var, when set, still wins (key_source says which is used)."""
    _check(provider)
    key = (key or "").strip()
    if not key:
        raise ValueError("empty key")
    (write or cred_write)(_target(provider), key)


def delete_key(provider: str, delete: Callable[[str], bool] | None = None) -> bool:
    _check(provider)
    return (delete or cred_delete)(_target(provider))


# ---------------------------------------------------------------- HTTP

def _post(client, provider: str, url: str, timeout: float, **kw):
    """POST and map every failure to a short SpeechError reason."""
    httpx = _httpx()
    try:
        r = client.post(url, timeout=timeout, **kw)
    except httpx.TimeoutException:
        raise SpeechError("timed out") from None
    except httpx.NetworkError:                     # ConnectError (DNS, refused), ReadError, ...
        raise SpeechError("no network") from None
    except httpx.HTTPError:
        raise SpeechError(f"{provider} error") from None
    code = r.status_code
    if code < 400:
        return r
    if code == 401:
        raise SpeechError("key rejected")
    if code == 429:
        try:
            err = (r.json() or {}).get("error") or {}
            if isinstance(err, dict) and err.get("code") == "insufficient_quota":
                raise SpeechError(f"{provider} account out of credit")
        except SpeechError:
            raise
        except Exception:
            pass
        raise SpeechError("rate limited")
    raise SpeechError(f"{provider} error {code}")


class _Http:
    """Owns one lazily created httpx.Client (or uses an injected one)."""

    def __init__(self, client, timeout: float):
        self._client = client
        self._own = client is None
        self.timeout = timeout

    @property
    def client(self):
        if self._client is None:
            httpx = _httpx()
            self._client = httpx.Client(timeout=httpx.Timeout(self.timeout, connect=min(5.0, self.timeout)))
        return self._client

    def close(self):
        if self._own and self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None


def _lang(code: str) -> str:
    """"en-US" -> "en" (the transcription APIs take ISO-639-1)."""
    return code.split("-")[0].split("_")[0].strip().lower()


# ---------------------------------------------------------------- STT

class CloudStt(SttBackend):
    """Whisper-style transcription over HTTPS (Groq or OpenAI)."""

    name = "cloud"
    probe_ok = False                    # costs money and has rate limits

    def __init__(self, cfg: dict | None = None, client=None, key_lookup=None):
        cfg = cfg or {}
        self.provider = str(cfg.get("cloud_stt_provider") or "groq").strip().lower()
        if self.provider == "openai":
            self.model = cfg.get("openai_stt_model") or STT_DEFAULT_MODELS["openai"]
        else:
            self.model = cfg.get("groq_model") or STT_DEFAULT_MODELS["groq"]
        self.timeout = float(cfg.get("cloud_timeout_s") or 15)
        self._http = _Http(client, self.timeout)
        self._key_lookup = key_lookup or get_key
        self.audio_s_total = 0.0        # seconds of audio successfully transcribed
        self.requests = 0
        self.last_s: float | None = None  # wall time of the last request

    @property
    def label(self) -> str:
        return f"{self.provider} {_SHORT.get(self.model, self.model)}"

    def available(self) -> tuple[bool, str]:
        if self.provider not in STT_URLS:
            return False, f"unknown cloud_stt_provider {self.provider}"
        if not self._key_lookup(self.provider):
            return False, f"no {ENV_VARS[self.provider]}"
        return True, ""

    def transcribe(self, audio: np.ndarray, prompt: str | None = None,
                   languages: list[str] | None = None) -> str:
        ok, why = self.available()
        if not ok:
            raise SpeechError(why)
        audio = np.asarray(audio)
        if audio.size == 0:
            return ""
        key = self._key_lookup(self.provider)
        data = {"model": self.model, "response_format": "json"}
        if prompt:
            data["prompt"] = prompt
        langs = [_lang(c) for c in (languages or []) if c and str(c).strip()]
        if len(set(langs)) == 1:
            data["language"] = langs[0]
        t0 = time.perf_counter()
        r = _post(self._http.client, self.provider, STT_URLS[self.provider], self.timeout,
                  headers={"Authorization": f"Bearer {key}"},
                  data=data, files={"file": ("speech.wav", wav_bytes(audio, RATE), "audio/wav")})
        self.last_s = time.perf_counter() - t0
        try:
            text = r.json()["text"]
        except Exception:
            raise SpeechError(f"{self.provider} error bad reply") from None
        self.requests += 1
        self.audio_s_total += len(audio) / RATE
        return (text or "").strip()

    def close(self):
        self._http.close()


# ---------------------------------------------------------------- TTS

def _pcm16_to_float(raw: bytes) -> np.ndarray:
    if len(raw) % 2:
        raw = raw[:-1]
    return np.frombuffer(raw, "<i2").astype(np.float32) / 32768.0


class CloudTts(TtsBackend):
    """OpenAI or ElevenLabs voices, fetched as raw 24 kHz PCM."""

    name = "cloud"
    pcm = True
    rate = TTS_RATE

    def __init__(self, cfg: dict | None = None, client=None, key_lookup=None):
        cfg = cfg or {}
        self.provider = str(cfg.get("cloud_tts_provider") or "openai").strip().lower()
        if self.provider == "elevenlabs":
            self.model = cfg.get("elevenlabs_model") or "eleven_flash_v2_5"
            self.voice = cfg.get("elevenlabs_voice") or ELEVENLABS_DEFAULT_VOICE
        else:
            self.model = cfg.get("openai_tts_model") or "gpt-4o-mini-tts"
            self.voice = cfg.get("openai_voice") or "alloy"
        self.instructions = cfg.get("openai_tts_instructions") or None   # gpt-4o-mini-tts only
        self.timeout = float(cfg.get("cloud_timeout_s") or 15)
        self._http = _Http(client, self.timeout)
        self._key_lookup = key_lookup or get_key
        self.chars_total = 0
        self.audio_s_total = 0.0
        self.requests = 0
        self.last_s: float | None = None

    @property
    def label(self) -> str:
        m = _SHORT.get(self.model, self.model)
        if self.provider == "openai":
            return f"openai {m} {self.voice}"
        return f"{self.provider} {m}"

    def available(self) -> tuple[bool, str]:
        if self.provider not in ("openai", "elevenlabs"):
            return False, f"unknown cloud_tts_provider {self.provider}"
        if not self._key_lookup(self.provider):
            return False, f"no {ENV_VARS[self.provider]}"
        return True, ""

    def synth(self, text: str) -> np.ndarray:
        ok, why = self.available()
        if not ok:
            raise SpeechError(why)
        text = (text or "").strip()
        if not text:
            return np.zeros(0, np.float32)
        key = self._key_lookup(self.provider)
        t0 = time.perf_counter()
        if self.provider == "openai":
            body = {"model": self.model, "voice": self.voice, "input": text, "response_format": "pcm"}
            if self.instructions and "gpt-4o" in self.model:
                body["instructions"] = self.instructions
            r = _post(self._http.client, "openai", OPENAI_TTS_URL, self.timeout,
                      headers={"Authorization": f"Bearer {key}"}, json=body)
        else:
            r = _post(self._http.client, "elevenlabs", ELEVENLABS_TTS_URL.format(voice_id=self.voice),
                      self.timeout, headers={"xi-api-key": key},
                      json={"text": text, "model_id": self.model})
        self.last_s = time.perf_counter() - t0
        ctype = r.headers.get("content-type", "")
        if "json" in ctype or "text/" in ctype:
            raise SpeechError(f"{self.provider} error bad reply")
        x = _pcm16_to_float(r.content)
        self.requests += 1
        self.chars_total += len(text)
        self.audio_s_total += len(x) / self.rate
        return x

    def close(self):
        self._http.close()


# ---------------------------------------------------------------- cost
# Estimates as of 2026-11, USD, from the providers' public price pages; check
# them before relying on the numbers. Keys: (provider, model).
#   per_min      - per minute of audio in (STT)
#   min_s        - minimum billed seconds per request (Groq bills >= 10 s)
#   per_min_out  - per minute of audio out (TTS, token-priced models)
#   per_1k_chars - per 1000 characters of text in (TTS)
PRICES: dict[tuple[str, str], dict] = {
    ("groq", "whisper-large-v3-turbo"): {"per_min": 0.04 / 60, "min_s": 10},
    ("groq", "whisper-large-v3"): {"per_min": 0.111 / 60, "min_s": 10},
    ("openai", "gpt-4o-mini-transcribe"): {"per_min": 0.003},
    ("openai", "gpt-4o-transcribe"): {"per_min": 0.006},
    ("openai", "whisper-1"): {"per_min": 0.006},
    ("openai", "gpt-4o-mini-tts"): {"per_min_out": 0.015},
    ("openai", "tts-1"): {"per_1k_chars": 0.015},
    ("openai", "tts-1-hd"): {"per_1k_chars": 0.030},
    ("elevenlabs", "eleven_flash_v2_5"): {"per_1k_chars": 0.05},
    ("elevenlabs", "eleven_turbo_v2_5"): {"per_1k_chars": 0.05},
    ("elevenlabs", "eleven_multilingual_v2"): {"per_1k_chars": 0.10},
    ("elevenlabs", "eleven_v3"): {"per_1k_chars": 0.10},
}
CHARS_PER_MIN_SPOKEN = 900          # ~150 words/min, to turn reply text into minutes of voice


def _tts_usd(provider: str, model: str, chars: float) -> float | None:
    p = PRICES.get((provider, model))
    if not p:
        return None
    if "per_1k_chars" in p:
        return chars / 1000 * p["per_1k_chars"]
    if "per_min_out" in p:
        return chars / CHARS_PER_MIN_SPOKEN * p["per_min_out"]
    return None


def _read_rows(paths):
    for path in paths:
        try:
            fh = open(path, encoding="utf-8", errors="replace", newline="")
        except OSError:
            continue
        with fh:
            try:
                for row in csv.DictReader(fh):
                    yield row
            except csv.Error:
                continue


def monthly_cost(csv_path, provider: str, model: str, days: int = 30, now: datetime | None = None,
                 tts_provider: str | None = None, tts_model: str | None = None) -> dict:
    """What `provider`/`model` speech-to-text (and optionally a cloud voice)
    would cost per month at the usage logged in state/latency.csv.

    `csv_path`: one path or a list (latency.csv is rotated to latency-*.csv
    when its columns change). Uses rows from the last `days` days; STT counts
    spoken requests only (not source "typed", not audio_s 0). If the log spans
    less than 30 days (first to last row, at least 1 day) it is scaled up.
    TTS is estimated from the `reply` column, which assistant.py truncates to
    120 chars, so it's a lower bound.

    Returns dict(minutes, usd, requests, stt_usd, tts_usd, tts_chars,
    span_days, logged_minutes, logged_requests, priced) with minutes /
    requests / usd per month; priced=False if no price is known for a model.
    """
    paths = [csv_path] if isinstance(csv_path, (str, os.PathLike)) else list(csv_path)
    now = now or datetime.now()
    since = now - timedelta(days=days)
    p = PRICES.get((provider, model))
    min_s = (p or {}).get("min_s", 0)
    stamps, audio_s, billed_s, reqs, chars = [], 0.0, 0.0, 0, 0
    for row in _read_rows(paths):
        try:
            when = datetime.strptime((row.get("when") or "").strip(), "%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError, AttributeError):
            continue
        if when < since or when > now + timedelta(minutes=5):
            continue
        stamps.append(when)
        reply = row.get("reply")
        if isinstance(reply, str):
            chars += len(reply.strip())
        if (row.get("source") or "").strip() == "typed":
            continue
        try:
            a = float(row.get("audio_s") or 0)
        except (TypeError, ValueError):
            continue
        if not (a > 0) or a > 3600:
            continue
        audio_s += a
        billed_s += max(a, min_s)
        reqs += 1
    span = max(1.0, (max(stamps) - min(stamps)).total_seconds() / 86400) if stamps else 1.0
    scale = 30.0 / span
    stt_usd = billed_s / 60 * p["per_min"] * scale if p and "per_min" in p else None
    tts_usd = None
    if tts_provider:
        tts_usd = _tts_usd(tts_provider, tts_model or "", chars * scale)
    priced = stt_usd is not None and (tts_usd is not None or not tts_provider)
    usd = (stt_usd or 0.0) + (tts_usd or 0.0)
    return dict(minutes=audio_s / 60 * scale, usd=usd, requests=round(reqs * scale),
                stt_usd=stt_usd, tts_usd=tts_usd, tts_chars=round(chars * scale),
                span_days=span, logged_minutes=audio_s / 60, logged_requests=reqs, priced=priced)


def _usd(x: float) -> str:
    if x < 0.01:
        return "under $0.01"
    if x < 10:
        return f"about ${x:.2f}"
    return f"about ${x:.0f}"


def cost_line(csv_path, provider: str, model: str, days: int = 30, now: datetime | None = None,
              tts_provider: str | None = None, tts_model: str | None = None) -> str:
    """e.g. "about $0.12/month at your usage (41 min of speech)"."""
    c = monthly_cost(csv_path, provider, model, days=days, now=now,
                     tts_provider=tts_provider, tts_model=tts_model)
    if c["stt_usd"] is None:
        return f"no price known for {provider} {model}"
    if not c["logged_requests"] and not (tts_provider and c["tts_chars"]):
        return "no spoken requests logged yet"
    m = c["minutes"]
    s = f"{_usd(c['usd'])}/month at your usage ({'under 1' if m < 1 else f'{m:.0f}'} min of speech"
    if tts_provider:
        s += ", voice included" if c["tts_usd"] is not None else f", no price known for {tts_provider} {tts_model}"
    return s + ")"
