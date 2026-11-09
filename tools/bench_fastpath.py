"""Measure time-to-first-text for the claude CLI fast path.

Compares a fresh `claude -p` per question against one long-lived
`claude -p --input-format stream-json` process that is fed questions.

    .venv\\Scripts\\python tools\\bench_fastpath.py
"""
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from heyclaude.claude_cli import find_claude, fast_args  # noqa: E402

QUESTIONS = [
    "What is the capital of France?",
    "How many minutes are in three hours?",
    "Spell the word necessary.",
]


def one_shot(exe, q):
    t0 = time.perf_counter()
    p = subprocess.Popen(
        [exe, "-p", q, *fast_args("haiku", "You are a terse voice assistant.")],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    first = None
    for line in p.stdout:
        if first is None and '"text_delta"' in line:
            first = time.perf_counter() - t0
        if line.startswith("{") and json.loads(line).get("type") == "result":
            break
    p.wait()
    return first, time.perf_counter() - t0


def persistent(exe):
    p = subprocess.Popen(
        [exe, "-p", "--input-format", "stream-json",
         *fast_args("haiku", "You are a terse voice assistant.")],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, encoding="utf-8", bufsize=1,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    time.sleep(3)  # let it boot, as the app would at startup
    out = []
    for q in QUESTIONS:
        t0 = time.perf_counter()
        msg = {"type": "user", "message": {"role": "user", "content": q}}
        p.stdin.write(json.dumps(msg) + "\n")
        p.stdin.flush()
        first = None
        for line in p.stdout:
            if first is None and '"text_delta"' in line:
                first = time.perf_counter() - t0
            if line.startswith("{") and json.loads(line).get("type") == "result":
                break
        out.append((first, time.perf_counter() - t0))
    p.stdin.close()
    p.wait(timeout=20)
    return out


if __name__ == "__main__":
    exe = find_claude()
    print("claude:", exe)
    for q in QUESTIONS:
        f, t = one_shot(exe, q)
        print(f"one-shot   first-text {f:5.2f}s  total {t:5.2f}s  {q}")
    for (f, t), q in zip(persistent(exe), QUESTIONS):
        print(f"persistent first-text {f:5.2f}s  total {t:5.2f}s  {q}")
