"""Which speech backend runs, falling back when it fails, and lean mode (M4c).

`Speech` is the speech-to-text side. It replaces stt.SpeechToText in the
assistant (same transcribe / label / device / poll / readings) and routes
each request:

    speech.stt_backend (or speech.backend): local | windows | cloud | auto
      -> on failure, the next one in speech.fallback_order, with the reason
         on the island ("cloud: no network, used local")

`VoiceRouter` is the voice side, a drop-in for tts.VoiceEngine in
tts.Speaker: the reply goes to speech.tts_backend (or speech.backend), the
cloud backend speaking with the Windows voice unless a cloud voice is set
(speech.cloud_tts_provider), then down the same order, and SAPI last.

auto picks local while it is loaded or the GPU has room for it (or, without
a usable GPU, RAM does), else windows (online recognition on) or cloud (a
key), see pick_auto().

Lean mode (speech.lean) unloads Whisper and Kokoro after
speech.unload_after_min idle, and reloads them on the next wake (wake());
"Free memory now" (free_memory()) unloads at once. The wake word model is
never unloaded (~20 MB on the CPU). Switching to windows/cloud also unloads
the local models: that's what the switch is for.
"""
from __future__ import annotations

import threading
import time

import numpy as np

from .base import SpeechError

BACKENDS = ("local", "windows", "cloud")
CHOICES = BACKENDS + ("auto",)
KOKORO_VRAM_MB = 700
RAM_DEFAULT_MB = 1500


def norm(name, default="local") -> str:
    n = str(name or "").strip().lower()
    return n if n in CHOICES else default


def order(first: str, fallback_order) -> list[str]:
    """`first`, then the rest of fallback_order (then any backend it left out)."""
    seq = [first] + [norm(n, "") for n in (fallback_order or [])] + list(BACKENDS)
    out = []
    for n in seq:
        if n in BACKENDS and n not in out:
            out.append(n)
    return out


def pick_auto(local_loaded: bool, cuda_ok: bool, gpu_free_mb, ram_free_mb, need_vram_mb: float,
              need_ram_mb: float, windows_good: bool, cloud_ok: bool) -> tuple[str, str]:
    """speech.backend: auto. (backend, why)."""
    if local_loaded:
        return "local", "loaded"
    if cuda_ok and gpu_free_mb is not None:
        if gpu_free_mb >= need_vram_mb:
            return "local", f"vram free {gpu_free_mb / 1024:.1f} GB"
        why = f"low vram {gpu_free_mb / 1024:.1f} GB"
    elif ram_free_mb is None or ram_free_mb >= need_ram_mb:
        return "local", "cpu"
    else:
        why = f"low ram {ram_free_mb / 1024:.1f} GB"
    if windows_good:
        return "windows", why
    if cloud_ok:
        return "cloud", why
    return "windows", why


def _ram_free_mb():
    try:
        import psutil
        return psutil.virtual_memory().available / 2 ** 20
    except Exception:
        return None


class Speech:
    """Speech-to-text routing, fallback and lean mode. Thread-safe."""

    IDLE_CHECK_S = 15.0

    def __init__(self, cfg: dict, monitor=None, hint_words=None, stt_device: str | None = None,
                 on_label=None, on_note=None, on_loading=None, busy=None, factories: dict | None = None,
                 cuda_check=None, ram_free=None, clock=time.monotonic, start_thread: bool = True):
        self.cfg = cfg                                   # the whole live config (speech, stt, tts, dictionary)
        self.monitor = monitor
        self.hint_words = list(hint_words or [])
        self.stt_device = stt_device or str(cfg.get("stt", {}).get("device", "gpu"))
        self.on_label = on_label                         # (label) the island's stt line
        self.on_note = on_note                           # (text) a short flash: fallbacks, switches
        self.on_loading = on_loading                     # (on, text) the island's loading state
        self.busy = busy or (lambda: False)              # the assistant is mid-turn: don't unload
        self._factories = dict(factories or {})
        self._cuda_check = cuda_check
        self._ram_free = ram_free or _ram_free_mb
        self.clock = clock
        self._stt: dict = {}                             # name -> SttBackend
        self._lock = threading.RLock()
        self._load_lock = threading.Lock()               # one reload at a time
        self.last_used: str | None = None
        self.last_note = ""
        self._fallback_msg = ""
        self.last_active = clock()
        self.voice: VoiceRouter | None = None            # set by VoiceRouter(speech=self)
        self._begun = None
        self._cuda: tuple[bool, str] | None = None
        self._stop = threading.Event()
        if start_thread:
            threading.Thread(target=self._idle_loop, daemon=True, name="speech-lean").start()

    # -- config ------------------------------------------------------------------------
    @property
    def scfg(self) -> dict:
        return self.cfg.setdefault("speech", {})

    def chosen(self, kind: str = "stt") -> str:
        """The configured backend for stt/tts: <kind>_backend, else backend."""
        s = self.scfg
        return norm(s.get(f"{kind}_backend") or s.get("backend") or "local")

    def lean(self) -> bool:
        return bool(self.scfg.get("lean", False))

    def vocabulary(self) -> list[str]:
        d = self.cfg.get("dictionary") or {}
        words = ["Claude"] + list(self.hint_words) + [str(w) for w in (d.get("words") or []) if str(w).strip()]
        seen, out = set(), []
        for w in words:
            if w.lower() not in seen:
                seen.add(w.lower())
                out.append(w)
        return out

    def prompt(self) -> str | None:
        """Whisper's initial prompt: a spelling hint, not phrased like a request
        (large-v3 drops words that look already said)."""
        words = self.vocabulary()
        return ("Vocabulary: " + ", ".join(words) + ".") if words else None

    def languages(self) -> list[str] | None:
        langs = self.cfg.get("stt", {}).get("languages") or []
        return [str(x) for x in langs] or None

    # -- backends ----------------------------------------------------------------------
    def _make(self, name: str):
        f = self._factories.get(name)
        if f is not None:
            return f()
        if name == "local":
            from .local import LocalStt
            return LocalStt(self.cfg["stt"], device=self.stt_device, hint_words=self.hint_words,
                            monitor=self.monitor, on_change=lambda _l: self._label_changed())
        if name == "windows":
            from .windows import WindowsStt
            return WindowsStt(self.scfg)
        if name == "cloud":
            from .cloud import CloudStt
            return CloudStt(self.scfg)
        raise ValueError(name)

    def backend(self, name: str):
        with self._lock:
            b = self._stt.get(name)
            if b is None:
                b = self._stt[name] = self._make(name)
                prep = getattr(b, "prepare", None)
                if prep is not None:            # windows: compile WinRT/SAPI dictation off the hot path
                    threading.Thread(target=_quiet, args=(prep,), daemon=True, name=f"speech-prep-{name}").start()
            return b

    def _maybe(self, name: str):
        """The backend if it exists already (no construction)."""
        return self._stt.get(name)

    def cuda_ok(self) -> bool:
        if self._cuda is None:
            check = self._cuda_check
            if check is None:
                from ..stt import cuda_available as check
            try:
                self._cuda = check()
            except Exception as e:
                self._cuda = (False, str(e))
        return bool(self._cuda[0])

    def _windows_good(self) -> bool:
        try:
            from .windows import online_speech_enabled
            return online_speech_enabled() is True
        except Exception:
            return False

    def _cloud_ok(self) -> bool:
        try:
            return bool(self.backend("cloud").available()[0])
        except Exception:
            return False

    def resolve(self, kind: str = "stt") -> tuple[str, str]:
        """The backend to use now for stt/tts, and why (auto)."""
        name = self.chosen(kind)
        if name != "auto":
            return name, ""
        local = self._maybe("local")
        voice_local = self.voice.local if self.voice is not None else None
        loaded = bool(local is not None and local.loaded) or bool(voice_local is not None and voice_local.loaded)
        from ..stt import vram_needed_mb
        sc = self.cfg.get("stt", {})
        need = self.scfg.get("auto_min_vram_mb") or (
            vram_needed_mb(sc.get("gpu_model") or "large-v3-turbo", sc.get("gpu_compute_type") or "int8_float16")
            + KOKORO_VRAM_MB)
        m = self.monitor
        gpu_free = m.gpu_free_mb() if m is not None else None
        return pick_auto(loaded, self.cuda_ok() if gpu_free is not None else False, gpu_free, self._ram_free(),
                         float(need), float(self.scfg.get("auto_min_ram_mb") or RAM_DEFAULT_MB),
                         self._windows_good(), self._cloud_ok())

    # -- the island's labels -----------------------------------------------------------------
    @property
    def active(self) -> str:
        return self.last_used or self.resolve("stt")[0]

    @property
    def label(self) -> str:
        name = self.active
        b = self._maybe(name)
        inner = b.label if b is not None else "ready"
        if name == "local" and self.lean() and b is not None and not b.loaded:
            inner = "unloaded (lean)"
        return _join(name, inner)

    @property
    def device(self) -> str:
        """cuda | cpu for local (live partials only on the GPU), else the backend's name."""
        name = self.active
        if name == "local":
            b = self._maybe("local")
            return b.device if b is not None else "cpu"
        return name

    @property
    def probe_ok(self) -> bool:
        """Cheap enough for pause probes and partials (vad.TurnListener)."""
        try:
            return bool(self.backend(self.resolve("stt")[0]).probe_ok)
        except Exception:
            return False

    # (stt.SpeechToText compatibility)
    @property
    def want(self) -> str:
        return "gpu" if str(self.stt_device).lower() in ("gpu", "cuda") else "cpu"

    @property
    def gpu_failed(self):
        b = self._maybe("local")
        return getattr(getattr(b, "stt", None), "gpu_failed", None)

    def readings(self) -> str:
        b = self._maybe("local")
        return b.readings() if b is not None else ""

    def _label_changed(self):
        if self.on_label:
            try:
                self.on_label(self.label)
            except Exception as e:
                print(f"[speech] label: {e}")

    def note(self, text: str):
        self.last_note = text
        print(f"[speech] {text}")
        if self.on_note:
            try:
                self.on_note(text)
            except Exception as e:
                print(f"[speech] note: {e}")

    def _loading(self, on: bool, text: str = ""):
        if self.on_loading:
            try:
                self.on_loading(on, text)
            except Exception as e:
                print(f"[speech] loading: {e}")

    # -- start up ------------------------------------------------------------------------------
    def start(self):
        """At launch: load the local model only if local is what will run (blocking)."""
        name, why = self.resolve("stt")
        eff = name
        if name != "local":
            ok, reason = self.backend(name).available()
            if not ok:                       # say so now, and get the fallback ready
                for n in order(name, self.scfg.get("fallback_order"))[1:]:
                    if n == "local" or self.backend(n).available()[0]:
                        eff = n
                        break
                self.note(f"{name}: {reason}; using {eff}")
        if eff == "local" and not self.lean():
            self.backend("local").ensure()
        self.last_used = eff
        print(f"[speech] speech-to-text: {name}" + (f" (auto: {why})" if why else ""))
        self._label_changed()

    # -- per utterance --------------------------------------------------------------------------
    def touch(self):
        self.last_active = self.clock()

    def wake(self):
        """The wake word fired (or the hotkey, a click, dictation): start reloading
        lean-unloaded local models now, in the background, so they're ready by the
        time the user stops talking."""
        self.touch()
        need_stt = self.resolve("stt")[0] == "local" and self._needs_reload_stt()
        need_tts = self.voice is not None and self.voice.needs_reload()
        if need_stt or need_tts:
            threading.Thread(target=self._reload, args=(need_stt, need_tts), daemon=True,
                             name="speech-reload").start()

    def _needs_reload_stt(self) -> bool:
        b = self._maybe("local")
        return b is None or not b.loaded

    def _reload(self, stt=True, tts=True):
        with self._load_lock:
            t = time.perf_counter()
            voice = None
            if tts and self.voice is not None and self.voice.needs_reload():
                def load_voice():                # in parallel with whisper: ~5 s instead of ~8
                    try:
                        self.voice.reload()
                        print(f"[speech] kokoro reloaded in {time.perf_counter() - t:.1f}s")
                    except Exception as e:
                        print(f"[speech] kokoro reload: {e}")
                voice = threading.Thread(target=load_voice, daemon=True, name="speech-reload-voice")
                voice.start()
            if stt and self._needs_reload_stt():
                self.backend("local").reload()
                print(f"[speech] whisper reloaded in {time.perf_counter() - t:.1f}s")
            if voice is not None:
                voice.join(120)
        self._label_changed()

    def begin(self, live: bool = False):
        """An utterance is about to be recorded (after wake())."""
        name = self.resolve("stt")[0]
        try:
            b = self.backend(name)
            b.begin(live=live)
            self._begun = b
        except Exception as e:
            self._begun = None
            print(f"[speech] {name} begin: {e}")

    def cancel(self):
        b, self._begun = self._begun, None
        if b is not None:
            try:
                b.cancel()
            except Exception as e:
                print(f"[speech] cancel: {e}")

    def transcribe(self, audio: np.ndarray, prompt: str | None = None, languages=None) -> str:
        """The text of one utterance, from the chosen backend or the next one that
        works. Never raises: "" if none did (the reason is on the island)."""
        self.touch()
        prompt = self.prompt() if prompt is None else prompt
        languages = self.languages() if languages is None else languages
        first, _why = self.resolve("stt")
        failed: list[tuple[str, str]] = []
        begun, self._begun = self._begun, None
        for name in order(first, self.scfg.get("fallback_order")):
            try:
                b = self.backend(name)
            except Exception as e:
                failed.append((name, _short(e)))
                continue
            ok, why = b.available()
            if not ok:
                failed.append((name, why))
                continue
            if begun is not None and begun is not b:
                try:
                    begun.cancel()
                except Exception:
                    pass
                begun = None
            loading = name == "local" and not b.loaded
            if loading:
                self._loading(True, "loading whisper…")
            try:
                with self._load_lock if loading else _null():
                    text = b.transcribe(audio, prompt=prompt, languages=languages)
            except Exception as e:
                failed.append((name, _short(e)))
                continue
            finally:
                if loading:
                    self._loading(False)
            changed = name != self.last_used
            self.last_used = name
            msg = f"{failed[0][0]}: {failed[0][1]}; used {name}" if failed else ""
            if msg and msg != self._fallback_msg:   # say it once, not on every request
                self.note(msg)
            self._fallback_msg = msg
            if changed or failed:
                self._label_changed()
            self.touch()
            return text
        why = "; ".join(f"{n}: {w}" for n, w in failed)
        self.note(f"speech-to-text failed ({why})")
        return ""

    def transcribe_partial(self, audio: np.ndarray) -> str:
        """Pause probes and live partials: the active backend only, and only if cheap."""
        name = self.resolve("stt")[0]
        b = self.backend(name)
        if not b.probe_ok:
            return ""
        return b.transcribe(audio, prompt=self.prompt(), languages=self.languages())

    # -- switching and memory -----------------------------------------------------------------
    def set_backend(self, name: str, kind: str | None = None):
        """The tray switch: speech.backend = name (both sides; clears per-side
        overrides unless `kind` says stt/tts), no restart. Leaving local
        unloads its models; switching to local loads them in the background."""
        name = norm(name)
        s = self.scfg
        if kind in ("stt", "tts"):
            s[f"{kind}_backend"] = name
        else:
            s["backend"] = name
            s["stt_backend"] = s["tts_backend"] = None
        self.reconfigure()

    def reconfigure(self):
        """speech.backend / stt_backend / tts_backend changed (tray or Settings):
        apply it now."""
        name = self.chosen("stt")
        self.last_used = None
        stt_now, tts_now = self.resolve("stt")[0], self.resolve("tts")[0]
        if self.voice is not None:
            self.voice.switched()
        if stt_now != "local" and tts_now != "local":
            self.free_memory(quiet=True)
        elif stt_now != "local":
            self._unload_stt()
        elif tts_now != "local" and self.voice is not None:
            self.voice.unload()
        if stt_now == "local" or tts_now == "local":
            threading.Thread(target=self._switch_load, args=(stt_now == "local", tts_now == "local"),
                             daemon=True, name="speech-switch").start()
        self.last_used = stt_now
        tts_said = self.voice.tts_name(tts_now) if self.voice is not None else tts_now
        self.note(f"speech: {name}" + ("" if stt_now == tts_said else f" (voice: {tts_said})"))
        self._label_changed()

    def _switch_load(self, stt: bool, tts: bool):
        if self.lean():
            return                                   # lean: load on the next wake
        self._loading(True, "loading speech models…")
        try:
            self._reload(stt, tts)
        finally:
            self._loading(False)

    def _unload_stt(self):
        b = self._maybe("local")
        if b is not None and b.loaded:
            b.unload()

    def free_memory(self, quiet: bool = False):
        """Unload Whisper and Kokoro now. They load again when next needed."""
        with self._load_lock:
            had = bool((self._maybe("local") is not None and self._maybe("local").loaded)
                       or (self.voice is not None and self.voice.local is not None and self.voice.local.loaded))
            self._unload_stt()
            if self.voice is not None:
                self.voice.unload()
        import gc
        gc.collect()
        if not quiet:
            self.note("speech models unloaded" if had else "nothing loaded")
        self._label_changed()

    def _idle_loop(self):
        while not self._stop.wait(self.IDLE_CHECK_S):
            try:
                self.check_idle()
            except Exception as e:
                print(f"[speech] idle check: {e}")

    def check_idle(self) -> bool:
        """Lean mode: unload after unload_after_min of no speech. True if it did."""
        if not self.lean():
            return False
        idle_s = self.clock() - self.last_active
        if idle_s < float(self.scfg.get("unload_after_min", 10) or 10) * 60 or self.busy():
            return False
        b = self._maybe("local")
        loaded = (b is not None and b.loaded) or (self.voice is not None and self.voice.local is not None
                                                  and self.voice.local.loaded)
        if not loaded:
            return False
        print(f"[speech] lean: idle {idle_s / 60:.0f} min, unloading whisper and kokoro")
        self.free_memory(quiet=True)
        return True

    def poll(self):
        """Every ~10 s (assistant): keep the local device choice current."""
        b = self._maybe("local")
        if b is not None and self.active == "local":
            b.poll()

    def close(self):
        self._stop.set()
        for b in list(self._stt.values()):
            try:
                b.close()
            except Exception:
                pass


def _join(name: str, inner: str) -> str:
    """"windows" + "windows david" -> "windows david"; "local" + "gpu (turbo)" -> "local: gpu (turbo)"."""
    inner = str(inner or "")
    return inner if inner.split(" ", 1)[0] == name else f"{name}: {inner}"


def _quiet(fn):
    try:
        fn()
    except Exception as e:
        print(f"[speech] prepare: {e}")


class _null:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _short(e: BaseException) -> str:
    if isinstance(e, SpeechError):
        return str(e)[:80]
    return f"{type(e).__name__}: {e}"[:80]


class VoiceRouter:
    """tts.VoiceEngine's interface (start, pick, synth, label, current, sapi,
    _ensure_sapi, kokoro_failed) over the TTS backends. Built by the assistant
    and handed to tts.Speaker(engine=...)."""

    def __init__(self, speech: Speech, tts_cfg: dict, stt_cfg: dict | None = None, monitor=None,
                 on_change=None, local_factory=None, factories: dict | None = None, sapi=None,
                 premiere_running=None):
        self.speech = speech
        speech.voice = self
        self.cfg, self.stt_cfg, self.monitor = tts_cfg, stt_cfg or {}, monitor
        self.on_change = on_change
        self._local_factory = local_factory
        self._factories = dict(factories or {})
        self._sapi_factory = sapi
        self._premiere = premiere_running
        self.local = None                       # tts.VoiceEngine, made when local speaks
        self._backends: dict = {}
        self.sapi = None
        self.current = None
        self.current_name = None
        self._failed: dict[str, str] = {}      # backend -> why, for this reply
        self._lock = threading.RLock()
        self._label = ""

    # -- pieces ------------------------------------------------------------------------------------
    def _make_sapi(self):
        if self.sapi is None:
            if self._sapi_factory is not None:
                self.sapi = self._sapi_factory()
            else:
                from ..tts import _Sapi
                self.sapi = _Sapi(self.cfg)
        return self.sapi

    def _ensure_sapi(self):
        return self._make_sapi()

    def _get_local(self):
        if self.local is None:
            if self._local_factory is not None:
                self.local = self._local_factory()
            else:
                from ..tts import VoiceEngine
                self.local = VoiceEngine(self.cfg, self.stt_cfg, monitor=self.monitor,
                                         on_change=lambda _l: self._changed(), sapi=self._make_sapi,
                                         premiere_running=self._premiere)
            self.local.start()
        return self.local

    def _backend(self, name: str):
        b = self._backends.get(name)
        if b is None:
            f = self._factories.get(name)
            if f is not None:
                b = f()
            elif name == "windows":
                from .windows import WindowsTts
                b = WindowsTts(self.speech.scfg)
            elif name == "cloud":
                from .cloud import CloudTts
                b = CloudTts(self.speech.scfg)
            else:
                raise ValueError(name)
            self._backends[name] = b
        return b

    def tts_name(self, name: str) -> str:
        """cloud speaks with the Windows voice unless a cloud voice is configured."""
        if name == "cloud" and not self.speech.scfg.get("cloud_tts_provider"):
            return "windows"
        return name

    # -- VoiceEngine interface -------------------------------------------------------------------
    @property
    def kokoro_failed(self):
        return getattr(self.local, "kokoro_failed", None)

    @kokoro_failed.setter
    def kokoro_failed(self, v):
        if self.local is not None:
            self.local.kokoro_failed = v

    def start(self):
        first = self.tts_name(self.speech.resolve("tts")[0])
        if first == "local" and not self.speech.lean():
            self._get_local()
        self.pick()

    def pick(self):
        """The engine for the next reply. Returns an object with .pcm (and
        .synth/.rate when pcm), like VoiceEngine.pick()."""
        with self._lock:
            self._failed.clear()
            return self._pick_from(self.tts_name(self.speech.resolve("tts")[0]))

    def _pick_from(self, first: str, skip=()):
        for name in order(first, self.speech.scfg.get("fallback_order")):
            name = self.tts_name(name)
            if name in skip or name in self._failed:
                continue
            if name == "local":
                try:
                    eng = self._get_local().pick()
                except Exception as e:
                    self._failed[name] = _short(e)
                    continue
                self._set(eng, "local")
                return eng
            try:
                b = self._backend(name)
                ok, why = b.available()
            except Exception as e:
                ok, why = False, _short(e)
            if not ok:
                self._failed[name] = why
                continue
            self._set(b, name)
            return b
        eng = self._make_sapi()
        self._set(eng, "sapi")
        return eng

    def _set(self, eng, name):
        self.current, self.current_name = eng, name
        self._changed()

    def synth(self, text: str):
        """(pcm, rate) for one sentence; walks down the order on errors. Raises
        tts._UseSapi when only SAPI is left."""
        from ..tts import _UseSapi
        with self._lock:
            eng, name = self.current, self.current_name
        if name == "local":
            return self.local.synth(text)            # its own GPU -> CPU -> _UseSapi chain
        if eng is None or not getattr(eng, "pcm", False):
            raise _UseSapi()
        try:
            return eng.synth(text), eng.rate
        except Exception as e:
            why = _short(e)
            with self._lock:
                self._failed[name] = why
                nxt = self._pick_from(name, skip=(name,))
            self.speech.note(f"voice {name}: {why}; using {self.current_name}")
            if nxt is not None and getattr(nxt, "pcm", False):
                return self.synth(text)
            raise _UseSapi()

    @property
    def label(self) -> str:
        name = self.current_name
        if name == "local" and self.local is not None:
            return self.local.label
        if name == "sapi" or self.current is None:
            why = "; ".join(f"{n}: {w}" for n, w in self._failed.items())
            return "sapi" + (f" ({why})" if why else "")
        return _join(name, self.current.label)

    def _changed(self):
        lbl = self.label
        if lbl != self._label:
            self._label = lbl
            print(f"[tts] voice: {lbl}")
            if self.on_change:
                try:
                    self.on_change(lbl)
                except Exception:
                    pass

    # -- lean / switching ------------------------------------------------------------------------
    def needs_reload(self) -> bool:
        if self.tts_name(self.speech.resolve("tts")[0]) != "local":
            return False
        return self.local is None or bool(getattr(self.local, "unloaded", False))

    def reload(self):
        if self.local is None:
            self._get_local()
        else:
            self.local.reload()
        with self._lock:
            if self.current_name == "local" or self.current is None:
                self.pick()

    def unload(self):
        if self.local is not None:
            self.local.unload()
            with self._lock:
                if self.current_name == "local":
                    self.current = None

    def switched(self):
        """The backend changed: the next reply picks again."""
        with self._lock:
            self.current, self.current_name = None, None
