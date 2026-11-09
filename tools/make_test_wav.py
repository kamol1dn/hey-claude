"""Render a phrase with a Windows voice to a 16 kHz mono wav for --simulate.

    .venv\\Scripts\\python tools\\make_test_wav.py "Hey Jarvis" "what is the capital of France?" samples\\q.wav [Zira]

The wake phrase and the request are rendered separately with a short pause
between them, like a person would say it.
"""
import os
import sys
import tempfile
import wave

import numpy as np
import win32com.client

SAFT16kHz16BitMono = 18


def render(voice, text, rate):
    fd, path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    fmt = win32com.client.Dispatch("SAPI.SpAudioFormat")
    fmt.Type = SAFT16kHz16BitMono
    fs = win32com.client.Dispatch("SAPI.SpFileStream")
    fs.Format = fmt
    fs.Open(path, 3)
    voice.AudioOutputStream = fs
    voice.Rate = rate
    voice.Speak(text)
    fs.Close()
    with wave.open(path, "rb") as w:
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    os.remove(path)
    return pcm


def main(wake, request, out, voice_name=None):
    v = win32com.client.Dispatch("SAPI.SpVoice")
    if voice_name:
        vs = v.GetVoices()
        for i in range(vs.Count):
            if voice_name.lower() in vs.Item(i).GetDescription().lower():
                v.Voice = vs.Item(i)
    a = render(v, wake, -1)
    b = render(v, request, 0)
    gap = np.zeros(int(16000 * 0.35), np.int16)
    audio = np.concatenate([a, gap, b])
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(audio.tobytes())
    print(f"{out}: {len(audio) / 16000:.2f}s")


if __name__ == "__main__":
    main(*sys.argv[1:5])
