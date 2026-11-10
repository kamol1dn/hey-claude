"""M4d: does the personal dictionary fix the words Whisper gets wrong?

Speaks sentences with the user's own words (Kokoro, three voices), then
transcribes each with large-v3-turbo twice: plain, and with the dictionary
(the words as Whisper's initial prompt, then the replacements). Counts how
many of the dictionary words come out spelled right.

    .venv\\Scripts\\python tools\\dictionary_eval.py
    .venv\\Scripts\\python tools\\dictionary_eval.py --config my.yaml   # your config's dictionary

Without a `dictionary:` in the config it uses a sample one with the words the
roadmap names (Phoenix, eClass, Inha, Tashkent...). Needs the GPU under ~70%.
"""
from __future__ import annotations

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

SAMPLE = {
    "words": ["eClass", "Phoenix", "Inha", "Tashkent", "Dilnoza", "Claude Code", "hey-claude"],
    "replacements": {"e class": "eClass", "e-class": "eClass", "hey cloud": "hey Claude",
                     "cloud code": "Claude Code", "tashkint": "Tashkent"},
}
SENTENCES = [
    "The eClass assignment for the Phoenix course is due on Friday.",
    "Dilnoza shared her notes from Inha University in Tashkent.",
    "Hey Claude, open Claude Code in the hey-claude folder.",
    "Upload the lab report to eClass before the Phoenix lecture.",
    "Ask Dilnoza whether the Inha bus leaves Tashkent at eight.",
]
VOICES = ["am_michael", "bf_emma", "af_heart"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config")
    a = ap.parse_args()
    import numpy as np
    from heyclaude import config, dictionary
    from heyclaude.stt import Transcriber, add_cuda_dll_dirs
    from heyclaude.tts import KokoroVoice, add_ort_cuda_dirs, kokoro_paths, to_far
    cfg = config.load(a.config)
    d = cfg.get("dictionary") or {}
    if not dictionary.words(d):
        d = SAMPLE
    words = dictionary.words(d)
    add_cuda_dll_dirs()
    add_ort_cuda_dirs()
    kv = KokoroVoice(*kokoro_paths(cfg["tts"]), "cpu")
    model = cfg["stt"].get("gpu_model") or "large-v3-turbo"
    stt = Transcriber(model, "cuda", "int8_float16", language="en")
    prompt = "Vocabulary: " + ", ".join(["Claude"] + words) + "."

    def hits(text: str, sentence: str) -> tuple[int, int]:
        want = [w for w in words if re.search(re.escape(w), sentence, re.I)]
        got = [w for w in want if re.search(r"(?<!\w)" + re.escape(w) + r"(?!\w)", text)]   # exact spelling
        return len(got), len(want)

    tot = {"plain": [0, 0], "dictionary": [0, 0]}
    for voice in VOICES:
        kv.voice = voice
        for s in SENTENCES:
            pcm = to_far(kv.synth(s), 24000)
            plain = stt.transcribe(pcm)
            fixed = dictionary.apply(stt.transcribe(pcm, prompt=prompt), d, snippets=False)
            for name, text in (("plain", plain), ("dictionary", fixed)):
                g, w = hits(text, s)
                tot[name][0] += g
                tot[name][1] += w
            print(f"[{voice}] {s}\n   plain:      {plain}\n   dictionary: {fixed}")
    for name, (g, w) in tot.items():
        print(f"{name:11} {g}/{w} dictionary words spelled right ({100 * g / max(1, w):.0f}%)")
    ok = tot["dictionary"][0] > tot["plain"][0] and tot["dictionary"][0] >= 0.9 * tot["dictionary"][1]
    print("PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
