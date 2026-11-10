"""M4c/M4d live check: the real app (tray, island, speech backends, the
dictation key hook, clipboard paste), with only the dictation microphone
replaced by spoken sentences (Kokoro), for machines where nobody can talk
into the mic (a headset can't hear the speakers).

    .venv\\Scripts\\python tools\\dictate_check.py [--config f.yaml] [--cmd-file f.txt]

Then, while it runs, focus a text field and write a line to the command file:

    dictate <n>        # hold the dictation key (injected), "speak" clip n, let go
    handsfree <n>      # double tap, clip n, tap
    status             # print the speech labels and NVML used memory
    quit

Results (one JSON line per command) go to <cmd-file>.out. The tray's Speech
menu switches the backend for real meanwhile.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np  # noqa: E402

CLIPS = [
    "The eClass assignment for the Phoenix course is due on Friday, so I will start tonight.",
    "This sentence was dictated into the browser text box by hey Claude.",
    "echo dictated into the terminal without touching the keyboard",
    "Hands free dictation keeps listening until the next tap of the key.",
]
VK_RMENU, KEYUP, EXT = 0xA5, 0x2, 0x1


def key(vk: int, down: bool):
    u = ctypes.windll.user32
    u.keybd_event(vk, 0, (0 if down else KEYUP) | EXT, 0)


class ClipMic:
    """A MicSource stand-in: the clip in real time, then silence until closed."""

    def __init__(self, pcm: np.ndarray, lead_s: float = 0.15):
        self.pcm = np.concatenate([np.zeros(int(lead_s * 16000), np.int16), pcm])
        self.closed = threading.Event()

    def start(self):
        pass

    def close(self):
        self.closed.set()

    def __iter__(self):
        t0 = time.perf_counter()
        n = 0
        pad = np.concatenate([self.pcm, np.zeros((-len(self.pcm)) % 1280, np.int16)])
        while not self.closed.is_set():
            delay = t0 + (n + 1) * 0.08 - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            i = n * 1280
            yield pad[i:i + 1280] if i < len(pad) else np.zeros(1280, np.int16)
            n += 1


def make_clips(cfg) -> list[np.ndarray]:
    from heyclaude.tts import KokoroVoice, kokoro_paths, to_far
    kv = KokoroVoice(*kokoro_paths(cfg["tts"]), "cpu", voice="am_michael")
    out = [to_far(kv.synth(t), 24000) for t in CLIPS]
    del kv
    return out


def used_mb():
    try:
        import pynvml
        pynvml.nvmlInit()
        return round(pynvml.nvmlDeviceGetMemoryInfo(pynvml.nvmlDeviceGetHandleByIndex(0)).used / 2 ** 20)
    except Exception:
        return None


class Harness:
    def __init__(self, cmd_file: Path):
        self.cmd_file, self.out = cmd_file, cmd_file.with_suffix(cmd_file.suffix + ".out")
        self.clip = None
        self.clips = []

    def configure(self, cfg):
        self.cfg = cfg

    def started(self, ctx):
        threading.Thread(target=self.run, args=(ctx,), daemon=True, name="dictate-check").start()

    def write(self, **row):
        row["t"] = time.strftime("%H:%M:%S")
        with open(self.out, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        print("[check] " + json.dumps(row))

    def run(self, ctx):
        asst, bridge, cfg, app = ctx["asst"], ctx["bridge"], ctx["cfg"], ctx["app"]
        asst.ready.wait()
        self.clips = make_clips(cfg)
        from heyclaude.dictate import attach
        d = attach(asst, bridge, cfg, mic_factory=lambda: ClipMic(self.clip))
        self.write(event="ready", speech=asst.speech.label, voice=asst.speaker.label, vram_mb=used_mb(),
                   clips=[round(len(c) / 16000, 1) for c in self.clips])
        self.cmd_file.write_text("", encoding="utf-8")
        while True:
            time.sleep(0.2)
            try:
                lines = self.cmd_file.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            if not lines:
                continue
            self.cmd_file.write_text("", encoding="utf-8")
            for line in lines:
                parts = line.split()
                if not parts:
                    continue
                cmd = parts[0]
                if cmd == "quit":
                    self.write(event="quit")
                    app.quit()
                    return
                if cmd == "status":
                    self.write(event="status", speech=asst.speech.label, voice=asst.speaker.label,
                               vram_mb=used_mb(), backend=cfg["speech"].get("backend"))
                    continue
                n = int(parts[1]) if len(parts) > 1 else 0
                self.clip = self.clips[n]
                secs = len(self.clip) / 16000 + 0.4
                before = len(d.timings)
                fg = _foreground_title()
                if cmd == "dictate":
                    key(VK_RMENU, True)
                    time.sleep(secs)
                    key(VK_RMENU, False)
                elif cmd == "handsfree":
                    key(VK_RMENU, True), time.sleep(0.08), key(VK_RMENU, False)
                    time.sleep(0.15)
                    key(VK_RMENU, True), time.sleep(0.08), key(VK_RMENU, False)
                    time.sleep(secs)
                    key(VK_RMENU, True), time.sleep(0.08), key(VK_RMENU, False)
                end = time.time() + 30
                while len(d.timings) == before and time.time() < end:
                    time.sleep(0.05)
                t = d.timings[-1] if len(d.timings) > before else None
                self.write(event=cmd, clip=n, window=fg, text=d.last_text if t else None, timing=t,
                           speech=asst.speech.label)


def _foreground_title() -> str:
    u = ctypes.windll.user32
    h = u.GetForegroundWindow()
    buf = ctypes.create_unicode_buffer(256)
    u.GetWindowTextW(h, buf, 256)
    return buf.value


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config")
    ap.add_argument("--cmd-file", default=str(Path(os.environ.get("TEMP", ".")) / "hc_dictate_cmd.txt"))
    a = ap.parse_args()
    h = Harness(Path(a.cmd_file))
    Path(h.out).unlink(missing_ok=True)
    from heyclaude.__main__ import main as app_main
    argv = (["--config", a.config] if a.config else [])
    app_main(argv, harness=h)


if __name__ == "__main__":
    main()
