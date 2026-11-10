"""M4c: speech backend selection, fallback, auto, lean mode and the tray switch.
Every backend is mocked: no models, network, WinRT or GPU needed.

    .venv\\Scripts\\python tests\\test_speech.py
"""
import os
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np  # noqa: E402

from heyclaude.speech import manager as M  # noqa: E402
from heyclaude.speech.base import SpeechError, SttBackend, TtsBackend, pcm_from_wav, wav_bytes  # noqa: E402

AUDIO = np.zeros(16000, np.int16)


class FakeStt(SttBackend):
    def __init__(self, name, text="hello", fail=None, avail=(True, ""), probe=False):
        self.name, self.text, self.fail, self.avail, self.probe_ok = name, text, fail, avail, probe
        self.calls, self.began, self.cancelled = [], 0, 0
        self._loaded = name == "local"
        self.unloads = self.reloads = 0

    @property
    def label(self):
        return f"fake {self.name}"

    def available(self):
        return self.avail

    @property
    def loaded(self):
        return self._loaded

    def begin(self, live=False):
        self.began += 1

    def cancel(self):
        self.cancelled += 1

    def transcribe(self, audio, prompt=None, languages=None):
        self.calls.append((prompt, languages))
        if self.fail:
            raise self.fail
        return self.text

    def unload(self):
        self._loaded = False
        self.unloads += 1

    def reload(self):
        self._loaded = True
        self.reloads += 1

    # LocalStt extras
    device = "cuda"

    def ensure(self):
        self._loaded = True

    def poll(self):
        pass

    def readings(self):
        return ""


def _cfg(**speech):
    s = dict(backend="local", stt_backend=None, tts_backend=None, fallback_order=["local", "windows", "cloud"],
             lean=False, unload_after_min=10)
    s.update(speech)
    return {"speech": s, "stt": {"device": "gpu"}, "tts": {}, "dictionary": {"words": ["Phoenix", "eClass"]}}


def _speech(cfg=None, **fakes):
    fakes = {n: fakes.get(n) or FakeStt(n, text=f"from {n}") for n in ("local", "windows", "cloud")}
    notes, loading = [], []
    sp = M.Speech(cfg or _cfg(), factories={n: (lambda b=b: b) for n, b in fakes.items()},
                  on_note=notes.append, on_loading=lambda on, t="": loading.append((on, t)),
                  cuda_check=lambda: (True, ""), ram_free=lambda: 8000, start_thread=False)
    return sp, fakes, notes, loading


# -- selection ---------------------------------------------------------------------------
def test_order_puts_the_choice_first_then_the_fallback_order():
    assert M.order("cloud", ["local", "windows", "cloud"]) == ["cloud", "local", "windows"]
    assert M.order("windows", ["cloud"]) == ["windows", "cloud", "local"]
    assert M.order("local", ["bogus", None]) == ["local", "windows", "cloud"]


def test_each_backend_answers_when_chosen():
    for name in ("local", "windows", "cloud"):
        sp, fakes, notes, _ = _speech(_cfg(backend=name))
        assert sp.transcribe(AUDIO) == f"from {name}"
        assert sp.label.startswith(f"{name}: "), sp.label
        assert not notes
        others = [n for n in fakes if n != name]
        assert all(not fakes[n].calls for n in others)


def test_stt_backend_overrides_backend():
    sp, fakes, _, _ = _speech(_cfg(backend="local", stt_backend="cloud"))
    assert sp.transcribe(AUDIO) == "from cloud"
    assert sp.chosen("tts") == "local"


def test_prompt_and_languages_reach_the_backend():
    cfg = _cfg(backend="cloud")
    cfg["stt"]["languages"] = ["en", "ru"]
    sp, fakes, _, _ = _speech(cfg)
    sp.hint_words = ["hey claude"]
    sp.transcribe(AUDIO)
    prompt, langs = fakes["cloud"].calls[-1]
    assert prompt == "Vocabulary: Claude, hey claude, Phoenix, eClass.", prompt
    assert langs == ["en", "ru"]


# -- fallback ------------------------------------------------------------------------------
def test_failure_falls_back_and_says_why():
    cloud = FakeStt("cloud", fail=SpeechError("no network"))
    sp, fakes, notes, _ = _speech(_cfg(backend="cloud"), cloud=cloud)
    assert sp.transcribe(AUDIO) == "from local"
    assert notes == ["cloud: no network; used local"], notes
    assert sp.last_used == "local" and sp.label.startswith("local:")


def test_unavailable_backend_is_skipped_without_a_call():
    cloud = FakeStt("cloud", avail=(False, "no GROQ_API_KEY"))
    sp, fakes, notes, _ = _speech(_cfg(backend="cloud", fallback_order=["windows", "local"]), cloud=cloud)
    assert sp.transcribe(AUDIO) == "from windows"
    assert not cloud.calls
    assert notes == ["cloud: no GROQ_API_KEY; used windows"]


def test_all_failing_returns_empty_and_never_raises():
    f = {n: FakeStt(n, fail=RuntimeError("boom")) for n in ("local", "windows", "cloud")}
    sp, _, notes, _ = _speech(_cfg(), **f)
    assert sp.transcribe(AUDIO) == ""
    assert notes and notes[-1].startswith("speech-to-text failed")


def test_begin_goes_to_the_chosen_backend_and_is_cancelled_on_fallback():
    win = FakeStt("windows", fail=SpeechError("winrt error"))
    sp, fakes, notes, _ = _speech(_cfg(backend="windows"), windows=win)
    sp.begin(live=True)
    assert win.began == 1
    assert sp.transcribe(AUDIO) == "from local"
    assert win.cancelled == 1             # the failed backend's session is cleaned up
    sp.begin(live=True)
    sp.cancel()
    assert win.cancelled == 2


def test_partials_only_on_cheap_backends():
    sp, fakes, _, _ = _speech(_cfg(backend="cloud"))
    assert sp.transcribe_partial(AUDIO) == "" and not fakes["cloud"].calls
    assert sp.probe_ok is False
    local = FakeStt("local", text="partial", probe=True)
    sp, fakes, _, _ = _speech(_cfg(backend="local"), local=local)
    assert sp.probe_ok is True
    assert sp.transcribe_partial(AUDIO) == "partial"


# -- auto -----------------------------------------------------------------------------------
def test_pick_auto_rules():
    P = M.pick_auto
    assert P(True, True, 100, 100, 2500, 1500, True, True)[0] == "local"          # loaded: keep it
    assert P(False, True, 6000, 8000, 2500, 1500, True, True)[0] == "local"       # room on the GPU
    name, why = P(False, True, 1200, 8000, 2500, 1500, True, True)
    assert name == "windows" and "low vram 1.2 GB" in why
    assert P(False, True, 1200, 8000, 2500, 1500, False, True)[0] == "cloud"      # online recognition off
    assert P(False, True, 1200, 8000, 2500, 1500, False, False)[0] == "windows"   # nothing better: sapi
    assert P(False, False, None, 8000, 2500, 1500, True, True)[0] == "local"      # no GPU: CPU has RAM
    assert P(False, False, None, 900, 2500, 1500, True, True)[0] == "windows"     # no GPU, low RAM


def test_auto_follows_free_vram():
    class Mon:
        free = 1000.0

        def gpu_free_mb(self):
            return self.free

        def gpu_util(self):
            return 0.0

        def cpu_pct(self):
            return 0.0
    mon = Mon()
    local = FakeStt("local", text="from local")
    local._loaded = False
    sp, fakes, _, _ = _speech(_cfg(backend="auto", auto_min_vram_mb=2500), local=local)
    sp.monitor = mon
    sp._windows_good = lambda: True
    assert sp.resolve("stt")[0] == "windows"
    mon.free = 6000.0
    assert sp.resolve("stt")[0] == "local"


# -- lean mode ------------------------------------------------------------------------------------
class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


def test_lean_unloads_after_idle_and_reloads_on_wake():
    clock = Clock()
    local = FakeStt("local", text="from local")
    sp, _, _, loading = _speech(_cfg(lean=True, unload_after_min=10), local=local)
    sp.clock = clock
    sp.start()
    sp.backend("local")                    # (lean start-up loads nothing; the first wake does)
    sp.touch()
    clock.t += 9 * 60
    assert sp.check_idle() is False and local.loaded
    clock.t += 2 * 60
    assert sp.check_idle() is True
    assert not local.loaded and local.unloads == 1
    assert "unloaded (lean)" in sp.label
    sp.wake()                              # the next wake reloads it in the background
    for t in threading.enumerate():
        if t.name == "speech-reload":
            t.join(5)
    assert local.loaded and local.reloads == 1


def test_lean_never_unloads_mid_turn():
    clock = Clock()
    local = FakeStt("local", text="from local")
    sp, _, _, _ = _speech(_cfg(lean=True, unload_after_min=1), local=local)
    sp.clock, sp.busy = clock, (lambda: True)
    sp.backend("local")
    sp.touch()
    clock.t += 600
    assert sp.check_idle() is False and local.loaded


def test_transcribe_while_unloaded_shows_loading():
    local = FakeStt("local", text="from local")
    local._loaded = False
    sp, _, _, loading = _speech(_cfg(lean=True), local=local)
    assert sp.transcribe(AUDIO) == "from local"
    assert loading == [(True, "loading whisper…"), (False, "")], loading


def test_free_memory_now_unloads_even_without_lean():
    local = FakeStt("local", text="from local")
    sp, _, notes, _ = _speech(_cfg(lean=False), local=local)
    sp.backend("local")
    sp.free_memory()
    assert not local.loaded and notes == ["speech models unloaded"]


# -- the tray switch ----------------------------------------------------------------------------
def test_switch_without_restart_unloads_local_and_routes_next_request():
    local = FakeStt("local", text="from local")
    sp, fakes, notes, _ = _speech(_cfg(backend="local"), local=local)
    assert sp.transcribe(AUDIO) == "from local"
    sp.set_backend("windows")
    assert sp.cfg["speech"]["backend"] == "windows"
    assert not local.loaded                                  # leaving local frees its memory
    assert sp.transcribe(AUDIO) == "from windows"
    assert notes[-1] == "speech: windows"
    sp.set_backend("local")
    for t in threading.enumerate():
        if t.name == "speech-switch":
            t.join(5)
    assert local.loaded and sp.transcribe(AUDIO) == "from local"


def test_switch_to_an_unusable_backend_keeps_its_fallback_loaded():
    local = FakeStt("local", text="from local")
    cloud = FakeStt("cloud", avail=(False, "no GROQ_API_KEY"))
    sp, _, notes, _ = _speech(_cfg(backend="local", fallback_order=["cloud", "local", "windows"]),
                              local=local, cloud=cloud)
    sp.backend("local")
    sp.set_backend("cloud")
    assert local.loaded and local.unloads == 0      # local is what will run: don't pay a reload
    assert sp.transcribe(AUDIO) == "from local"


def test_switch_one_side_only():
    sp, _, _, _ = _speech(_cfg(backend="local"))
    sp.set_backend("cloud", kind="stt")
    assert sp.chosen("stt") == "cloud" and sp.chosen("tts") == "local"


# -- the voice side ------------------------------------------------------------------------------
class FakeTts(TtsBackend):
    def __init__(self, name, fail=None, avail=(True, "")):
        self.name, self.fail, self.avail, self.rate = name, fail, avail, 22050
        self.said = []

    @property
    def label(self):
        return f"fake {self.name}"

    def available(self):
        return self.avail

    def synth(self, text):
        if self.fail:
            raise self.fail
        self.said.append(text)
        return np.zeros(10, np.float32)


class FakeVoiceEngine:
    """tts.VoiceEngine's surface."""
    pcm = True
    rate = 24000

    def __init__(self):
        self.gpu, self.cpu, self.unloaded, self.kokoro_failed = object(), None, False, None
        self.current, self.label = None, "kokoro gpu"

    @property
    def loaded(self):
        return not self.unloaded

    def start(self):
        pass

    def pick(self):
        self.current = self
        return self

    def synth(self, text):
        return np.ones(5, np.float32), 24000

    def unload(self):
        self.unloaded = True

    def reload(self):
        self.unloaded = False


class FakeSapi:
    pcm = False

    def __init__(self):
        self.on_start = None

    def stop(self):
        pass


def _router(cfg, **tts):
    sp, _, notes, _ = _speech(cfg)
    local = FakeVoiceEngine()
    backs = {n: tts.get(n) or FakeTts(n) for n in ("windows", "cloud")}
    vr = M.VoiceRouter(sp, cfg["tts"], local_factory=lambda: local,
                       factories={n: (lambda b=b: b) for n, b in backs.items()}, sapi=FakeSapi)
    return vr, sp, local, backs, notes


def test_voice_follows_the_backend():
    vr, sp, local, backs, _ = _router(_cfg(backend="windows"))
    assert vr.pick() is backs["windows"]
    pcm, rate = vr.synth("Hello.")
    assert rate == 22050 and backs["windows"].said == ["Hello."]
    assert vr.label == "windows: fake windows"
    sp.set_backend("local")
    assert vr.pick() is local and vr.synth("Hi.")[1] == 24000


def test_cloud_speaks_with_the_windows_voice_unless_a_cloud_voice_is_set():
    vr, sp, _, backs, _ = _router(_cfg(backend="cloud"))
    assert vr.pick() is backs["windows"]
    sp.cfg["speech"]["cloud_tts_provider"] = "openai"
    assert vr.pick() is backs["cloud"]


def test_voice_falls_back_mid_reply_then_to_sapi():
    from heyclaude.tts import _UseSapi
    vr, sp, local, backs, notes = _router(_cfg(backend="windows", fallback_order=["windows", "local"]),
                                          windows=FakeTts("windows", fail=SpeechError("winrt error")))
    vr.pick()
    pcm, rate = vr.synth("Hello.")                 # windows failed -> local kokoro
    assert rate == 24000 and vr.current_name == "local"
    assert notes and "voice windows: winrt error; using local" in notes[-1]
    vr2, *_ = _router(_cfg(backend="cloud", cloud_tts_provider="openai", fallback_order=["cloud"]),
                      cloud=FakeTts("cloud", avail=(False, "no OPENAI_API_KEY")),
                      windows=FakeTts("windows", avail=(False, "no voices")))
    vr2._get_local = lambda: (_ for _ in ()).throw(RuntimeError("kokoro missing"))
    eng = vr2.pick()
    assert isinstance(eng, FakeSapi) and vr2.label.startswith("sapi (")
    try:
        vr2.synth("x")
        raise AssertionError("expected _UseSapi")
    except _UseSapi:
        pass


def test_voice_lean_unload_and_reload():
    vr, sp, local, _, _ = _router(_cfg(backend="local", lean=True))
    vr.pick()
    sp.free_memory(quiet=True)
    assert local.unloaded and vr.current is None
    assert vr.needs_reload()
    vr.reload()
    assert not local.unloaded and vr.current is local


# -- the real local backend's unload/reload (faster-whisper mocked) ------------------------------
def test_speechtotext_unload_and_reload_with_fake_models():
    from heyclaude.stt import SpeechToText
    made = []

    class FakeTranscriber:
        def __init__(self, dev):
            self.dev = dev
            made.append(dev)

        def transcribe(self, audio, prompt=None, languages=None):
            return f"{self.dev}:{prompt}"

    class Mon:
        def gpu_util(self): return 5.0
        def gpu_free_mb(self): return 8000.0
        def cpu_pct(self): return 5.0

    s = SpeechToText({"gpu_model": "large-v3-turbo"}, device="gpu", loader=FakeTranscriber,
                     cuda_check=lambda: (True, ""), cached=lambda m: True, monitor=Mon())
    assert s.loaded and made == ["cuda"]
    assert s.transcribe(AUDIO, prompt="Vocabulary: X.") == "cuda:Vocabulary: X."
    s.unload()
    assert not s.loaded and s.label == "unloaded (lean)"
    s.poll()                                       # the 10 s poll must not bring it back
    assert not s.loaded and made == ["cuda"]
    assert s.transcribe(AUDIO).startswith("cuda:")  # a request reloads it
    assert made == ["cuda", "cuda"] and s.label.startswith("gpu")


def test_wav_helpers_round_trip():
    a = (np.sin(np.arange(1600) / 5) * 8000).astype(np.int16)
    x, rate = pcm_from_wav(wav_bytes(a))
    assert rate == 16000 and len(x) == 1600 and abs(x[10] - a[10] / 32768) < 1e-6


if __name__ == "__main__":
    n = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            n += 1
            print("ok", name)
    print(n, "passed")
