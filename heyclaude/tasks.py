"""Headless Claude Code runs ("tasks").

Each task is `claude -p "<task>" --output-format stream-json` started in the
project folder. Its raw stream goes to state/logs/<id>.jsonl and a readable
log to state/logs/<id>.log; the session id is stored so it can be resumed
with `claude --resume <id>` from that folder.
"""
from __future__ import annotations

import json
import re
import subprocess
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field

from .claude_cli import NO_WINDOW, child_env, find_claude
from .config import LOG_DIR, STATE_DIR

SUMMARY_INSTRUCTION = (
    "You were started by a voice assistant; nobody is watching this run live. Work on your own, "
    "don't ask questions. When you are done, end your final message with one line of the form "
    "'SUMMARY: <one plain sentence, under 25 words, saying what you did or found>'. "
    "It will be read aloud, so no markdown, paths or code in that line."
)
STATE_FILE = STATE_DIR / "tasks.json"


@dataclass
class Task:
    id: str
    project: str
    path: str
    prompt: str
    status: str = "starting"        # starting | running | done | failed | stopped
    session_id: str | None = None
    started: float = field(default_factory=time.time)
    ended: float | None = None
    activity: str = ""              # last thing it did, for the island
    summary: str = ""
    cost_usd: float | None = None
    log: str = ""

    @property
    def resume_cmd(self) -> str:
        return f'cd /d "{self.path}" && claude --resume {self.session_id}' if self.session_id else ""


def _short(v, n=60) -> str:
    s = str(v).replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"


def describe_tool(name: str, inp: dict) -> str:
    for k in ("file_path", "command", "pattern", "url", "query", "description", "path"):
        if k in inp:
            val = inp[k]
            if k in ("file_path", "path"):
                val = re.split(r"[\\/]", str(val))[-1] or val
            return f"{name}: {_short(val, 50)}"
    return name


def extract_summary(result: str) -> str:
    m = re.findall(r"^\s*\**SUMMARY:?\**:?\s*(.+)$", result or "", re.M | re.I)
    if m:
        return m[-1].strip().strip("*").strip()
    text = re.sub(r"[`*#>_\[\]]", "", result or "").strip()
    first = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0] if text else ""
    return _short(first, 200) or "It finished without saying anything."


_RECENT = {"", "last", "latest", "previous", "recent", "most recent", "that", "this", "the", "my", "it",
           "current", "running", "one", "last one", "that one", "this one", "same"}
_STOP = {"the", "a", "an", "my", "task", "job", "session", "that", "this", "which", "in", "on", "for",
         "to", "of", "and", "with", "about", "one", "it"}


def find_task(tasks: list[Task], ref: str | None, projects=None, running: bool | None = None) -> Task | None:
    """The task a spoken reference means: "last" / nothing = the most recent;
    a project name ("inha app", misheard is fine) = its most recent task;
    otherwise the task whose prompt shares the most words with `ref`.
    running=True/False limits it to running / finished tasks."""
    from .brain import norm

    pool = [t for t in tasks if running is None or (t.status in ("starting", "running")) == running]
    pool.sort(key=lambda t: t.started)
    if not pool:
        return None
    r = norm(ref or "")
    r = " ".join(w for w in r.split() if w not in ("task", "job", "session"))
    if r in _RECENT:
        return pool[-1]
    if projects is not None:
        key = projects.resolve(r)
        if not key:
            words = r.split()
            for n in range(len(words), 0, -1):          # "inha app login" -> "inha app"
                key = projects.resolve(" ".join(words[:n]))
                if key:
                    break
        if key:
            hit = [t for t in pool if t.project == key]
            if hit:
                return hit[-1]
    want = {w for w in r.split() if w not in _STOP and len(w) > 2}
    best, score = None, 0
    for t in pool:
        have = set(norm(t.prompt + " " + t.project).split())
        s = len(want & have)
        if s and s >= score:
            best, score = t, s
    return best


class TaskManager:
    def __init__(self, cfg: dict, on_change=None, on_finish=None):
        self.cfg = cfg["tasks"]
        self.exe = find_claude(cfg["claude"].get("exe"))
        self.on_change = on_change or (lambda t: None)
        self.on_finish = on_finish or (lambda t: None)
        self.tasks: dict[str, Task] = {}
        self.procs: dict[str, subprocess.Popen] = {}
        self.lock = threading.Lock()
        self._load()

    # -- persistence -------------------------------------------------------
    def _load(self):
        if STATE_FILE.exists():
            try:
                for d in json.loads(STATE_FILE.read_text(encoding="utf-8"))[-20:]:
                    t = Task(**d)
                    if t.status in ("starting", "running"):
                        t.status = "stopped"   # the app was closed while it ran
                    self.tasks[t.id] = t
            except Exception:
                pass

    def _save(self):
        with self.lock:
            data = [asdict(t) for t in list(self.tasks.values())[-50:]]
        STATE_FILE.write_text(json.dumps(data, indent=1), encoding="utf-8")

    # -- running -----------------------------------------------------------
    def running(self) -> list[Task]:
        return [t for t in self.tasks.values() if t.status in ("starting", "running")]

    def command(self, prompt: str, name: str | None = None) -> list[str]:
        c = self.cfg
        args = [self.exe, "-p", prompt, "--output-format", "stream-json", "--verbose",
                "--permission-mode", c["permission_mode"],
                "--permission-prompts", "none",
                "--append-system-prompt", SUMMARY_INSTRUCTION,
                "--name", "voice: " + _short(name or prompt, 40)]
        if c.get("allowed_tools"):
            args += ["--allowedTools", ",".join(c["allowed_tools"])]
        if c.get("model"):
            args += ["--model", c["model"]]
        args += [str(a) for a in c.get("extra_args") or []]
        return args

    def spawn(self, project: str, path: str, prompt: str, context: str = "") -> Task:
        """Start a run. `context` (the conversation that led to it) is appended
        to what Claude Code is told; `prompt` alone is what the island shows."""
        if len(self.running()) >= int(self.cfg.get("max_parallel", 3)):
            raise RuntimeError("too many tasks are already running")
        tid = time.strftime("%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
        t = Task(id=tid, project=project, path=path, prompt=prompt, log=str(LOG_DIR / f"{tid}.log"))
        with self.lock:
            self.tasks[tid] = t
        proc = subprocess.Popen(
            self.command(prompt + ("\n\n" + context if context else ""), name=prompt), cwd=path,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
            errors="replace", bufsize=1, env=child_env(), creationflags=NO_WINDOW,
        )
        self.procs[tid] = proc
        threading.Thread(target=self._pump, args=(t, proc), daemon=True).start()
        self._save()
        self.on_change(t)
        return t

    def stop(self, tid: str) -> bool:
        p = self.procs.get(tid)
        if p and p.poll() is None:
            self.tasks[tid].status = "stopped"
            p.terminate()
            return True
        return False

    def find(self, ref: str | None, projects=None, running: bool | None = None) -> Task | None:
        return find_task(list(self.tasks.values()), ref, projects, running)

    def open_terminal(self, t: Task, popen=subprocess.Popen):
        """A new console window running the interactive session in the task's folder."""
        if not t.session_id:
            raise RuntimeError("that task has no session to resume")
        return popen(["cmd", "/k", self.exe, "--resume", t.session_id], cwd=t.path,
                     creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))

    def _pump(self, t: Task, proc: subprocess.Popen):
        raw = open(LOG_DIR / f"{t.id}.jsonl", "w", encoding="utf-8")
        log = open(t.log, "w", encoding="utf-8")

        def w(s):
            log.write(s + "\n")
            log.flush()

        w(f"# {t.project} — {t.prompt}\n# folder: {t.path}\n# started {time.ctime(t.started)}\n")
        result_text = ""
        is_error = False
        for line in proc.stdout:
            raw.write(line)
            raw.flush()
            if not line.startswith("{"):
                w(line.rstrip())
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            typ = ev.get("type")
            if typ == "system" and ev.get("subtype") == "init":
                t.session_id = ev.get("session_id")
                t.status = "running"
                w(f"session {t.session_id}  (resume: {t.resume_cmd})\n")
                self._save()
            elif typ == "assistant":
                for b in ev.get("message", {}).get("content", []):
                    if b.get("type") == "text" and b.get("text", "").strip():
                        w(b["text"].strip() + "\n")
                        t.activity = _short(b["text"].strip().splitlines()[0], 60)
                    elif b.get("type") == "tool_use":
                        d = describe_tool(b.get("name", "?"), b.get("input") or {})
                        w(f"  → {d}")
                        t.activity = d
            elif typ == "user":
                for b in ev.get("message", {}).get("content", []) if isinstance(ev.get("message", {}).get("content"), list) else []:
                    if b.get("type") == "tool_result" and b.get("is_error"):
                        w(f"    ✗ {_short(b.get('content'), 200)}")
            elif typ == "result":
                result_text = ev.get("result") or ""
                is_error = bool(ev.get("is_error")) or ev.get("subtype") != "success"
                t.cost_usd = ev.get("total_cost_usd")
                denials = ev.get("permission_denials") or []
                if denials:
                    w("permission denied for: " + ", ".join(sorted({d.get('tool_name', '?') for d in denials})))
            self.on_change(t)
        proc.wait()
        t.ended = time.time()
        if t.status != "stopped":
            t.status = "failed" if (is_error or proc.returncode not in (0, None)) else "done"
        t.summary = extract_summary(result_text) if result_text else (
            "It stopped without a result." if t.status != "done" else "It finished.")
        w(f"\n# {t.status} after {t.ended - t.started:.0f}s"
          + (f", ${t.cost_usd:.3f}" if t.cost_usd else "") + f"\n# summary: {t.summary}")
        raw.close()
        log.close()
        self.procs.pop(t.id, None)
        self._save()
        self.on_change(t)
        self.on_finish(t)
