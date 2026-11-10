"""M4c live check: lean mode frees the VRAM (NVML), and cold-start costs.

    .venv\\Scripts\\python tools\\speech_check.py              # lean: load, free, reload (NVML)
    .venv\\Scripts\\python tools\\speech_check.py --cold       # + cold start per model (README table)
    .venv\\Scripts\\python tools\\speech_check.py --config my.yaml

Uses the real models from config (stt.gpu_model, tts.kokoro_*). Run it with
hey-claude itself stopped or on the CPU, or its own VRAM blurs the numbers.
NVML's per-process numbers aren't available under WDDM, so it reads the
device's used memory before and after (other programs should be quiet).
"""
from __future__ import annotations

import argparse
import gc
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np  # noqa: E402


def used_mb() -> float | None:
    try:
        import pynvml
        pynvml.nvmlInit()
        h = pynvml.nvmlDeviceGetHandleByIndex(0)
        return pynvml.nvmlDeviceGetMemoryInfo(h).used / 2 ** 20
    except Exception:
        return None


def settle() -> float | None:
    gc.collect()
    time.sleep(1.5)
    return used_mb()


def lean_check(cfg) -> bool:
    from heyclaude.speech.manager import Speech, VoiceRouter
    from heyclaude.sysload import LoadMonitor
    cfg["speech"].update(backend="local", stt_backend=None, tts_backend=None, lean=True)
    mon = LoadMonitor()
    sp = Speech(cfg, monitor=mon, start_thread=False)
    vr = VoiceRouter(sp, cfg["tts"], cfg["stt"], monitor=mon)
    base = settle()
    print(f"NVML used before loading: {base:.0f} MB")
    t = time.perf_counter()
    sp._reload(stt=True, tts=True)
    load_s = time.perf_counter() - t
    audio = np.zeros(16000, np.int16)
    sp.transcribe(audio)
    vr.pick()
    vr.synth("Ready.")
    loaded = settle()
    print(f"loaded whisper + kokoro in {load_s:.1f}s: {sp.label} / {vr.label}; used {loaded:.0f} MB "
          f"(+{loaded - base:.0f})")
    sp.free_memory()
    freed = settle()
    print(f"after Free memory now: used {freed:.0f} MB (freed {loaded - freed:.0f} of the "
          f"{loaded - base:.0f} MB it took); label {sp.label}")
    t = time.perf_counter()
    sp.wake()
    for th in __import__("threading").enumerate():
        if th.name == "speech-reload":
            th.join(120)
    again_s = time.perf_counter() - t
    sp.transcribe(audio)
    vr.pick()
    again = settle()
    print(f"reloaded on wake in {again_s:.1f}s: used {again:.0f} MB; {sp.label} / {vr.label}")
    kept = freed - base
    ok = (loaded - freed) >= 0.7 * (loaded - base)
    print(f"{'PASS' if ok else 'FAIL'}: lean frees {100 * (loaded - freed) / max(1, loaded - base):.0f}% "
          f"of its VRAM ({kept:.0f} MB stay: the CUDA contexts of ctranslate2 and onnxruntime)")
    sp.free_memory(quiet=True)
    return ok


def cold(cfg):
    """Cold start of each model, in a fresh process each (so nothing is cached in-process)."""
    import subprocess
    code = r'''
import sys, time, os
sys.path.insert(0, os.getcwd())
import numpy as np
kind, model, dev = sys.argv[1:4]
t = time.perf_counter()
if kind == "whisper":
    from heyclaude.stt import Transcriber, add_cuda_dll_dirs
    add_cuda_dll_dirs()
    Transcriber(model, "cuda" if dev == "gpu" else "cpu", "int8_float16" if dev == "gpu" else "int8")
else:
    from heyclaude.tts import KokoroVoice, add_ort_cuda_dirs
    add_ort_cuda_dirs()
    m, v = model.split("|")
    KokoroVoice(m, v, "cuda" if dev == "gpu" else "cpu")
print(f"{time.perf_counter() - t:.2f}")
'''
    from heyclaude.tts import kokoro_paths
    from heyclaude.stt import is_cached
    rows = [("whisper", cfg["stt"].get("gpu_model") or "large-v3-turbo", "gpu"),
            ("whisper", "large-v3", "gpu"),
            ("whisper", cfg["stt"].get("cpu_model") or "base.en", "cpu"),
            ("kokoro", "|".join(kokoro_paths(cfg["tts"])), "gpu"),
            ("kokoro", "|".join(kokoro_paths(cfg["tts"])), "cpu")]
    root = os.path.join(os.path.dirname(__file__), "..")
    for kind, model, dev in rows:
        name = model if kind == "whisper" else "kokoro v1.0"
        if kind == "whisper" and not is_cached(model):
            print(f"{kind:8} {name:50} {dev}: not downloaded, skipped")
            continue
        r = subprocess.run([sys.executable, "-c", code, kind, model, dev], cwd=root, capture_output=True,
                           text=True, timeout=600)
        out = r.stdout.strip().splitlines()[-1] if r.returncode == 0 and r.stdout.strip() else r.stderr[-200:]
        print(f"{kind:8} {os.path.basename(name):50} {dev}: {out} s (load + warm-up)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config")
    ap.add_argument("--cold", action="store_true")
    a = ap.parse_args()
    from heyclaude import config
    cfg = config.load(a.config)
    ok = lean_check(cfg)
    if a.cold:
        cold(cfg)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
