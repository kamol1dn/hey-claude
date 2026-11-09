"""Debug: warm the tool agent, ask one question, print every event of the turn.

    .venv\\Scripts\\python tools\\trace_agent.py "What's on my calendar tomorrow?"
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from heyclaude import config  # noqa: E402
from heyclaude.brain import Agent  # noqa: E402
from heyclaude.claude_cli import find_claude  # noqa: E402

cfg = config.load()
a = Agent(cfg, find_claude(cfg["claude"].get("exe")))
print("warm:", "".join(a.ask("Reply with just: ok. Do not use tools.")))
q = a.chat
q.proc.stdin.write(json.dumps({"type": "user", "message": {"role": "user", "content": sys.argv[1]}}) + "\n")
q.proc.stdin.flush()
while True:
    line = q.lines.get(timeout=170)
    if line is None:
        print("EOF, exit code", q.proc.poll())
        break
    if not line.startswith("{"):
        print("RAW", line.rstrip()[:200])
        continue
    ev = json.loads(line)
    t = ev.get("type")
    if t == "assistant":
        for b in ev["message"]["content"]:
            print("A", b.get("type"), (b.get("name") or b.get("text") or "")[:150], json.dumps(b.get("input"))[:150] if b.get("input") else "")
    elif t == "user" and isinstance(ev["message"].get("content"), list):
        for b in ev["message"]["content"]:
            print("U", b.get("type"), "ERR" if b.get("is_error") else "", str(b.get("content"))[:200])
    elif t == "result":
        print("RESULT", ev.get("subtype"), ev.get("is_error"), str(ev.get("result"))[:400], ev.get("permission_denials"))
        break
    elif t != "stream_event":
        print("EV", t, ev.get("subtype"))
a.close()
