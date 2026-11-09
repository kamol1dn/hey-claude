"""Router and the two talking tiers.

Every utterance goes through `Brain.handle(text)`, which yields events:

    ("say", chunk)              text to speak, streamed as it arrives
    ("lookup", question)        needs the user's tools: hand to the warm agent
    ("task", project, task)     launch a headless Claude Code run
    ("status",)                 user asked how the tasks are going
    ("noop",)                   nothing to do ("never mind")

Three tiers:
  (a) quick answers, no tools: Anthropic SDK (haiku) if ANTHROPIC_API_KEY is
      set, else one warm tool-less `claude` process.
  (b) questions that need the user's tools ("who messaged me on Telegram"):
      `Agent`, one warm Claude Code process with tools and MCP servers,
      started at launch and reused for every such question.
  (c) real work: a new headless Claude Code session per task (tasks.py).

Routing: a local pattern catches "start a task in <project>: <thing>" and
obvious tool questions with no model call; everything else goes to tier (a),
which either answers or replies with a TASK / LOOKUP line.
"""
from __future__ import annotations

import difflib
import json
import os
import queue
import re
import subprocess
import threading
import time
from datetime import datetime

from .claude_cli import NO_WINDOW, child_env, fast_args, find_claude

_WORD = re.compile(r"[^a-z0-9 ]+")


def norm(s: str) -> str:
    return " ".join(_WORD.sub(" ", s.lower()).split())


class Projects:
    def __init__(self, mapping: dict[str, str]):
        self.map = mapping

    def resolve(self, spoken: str) -> str | None:
        s = norm(spoken)
        if not s:
            return None
        for key in self.map:
            if norm(key) == s:
                return key
        # Whisper often mangles names; accept a close match.
        close = difflib.get_close_matches(s, [norm(k) for k in self.map], n=1, cutoff=0.72)
        if close:
            return next(k for k in self.map if norm(k) == close[0])
        return None

    def split_prefix(self, rest: str) -> tuple[str, str] | None:
        """'hey claude add a readme' -> ('hey claude', 'add a readme')."""
        words = rest.split()
        for n in range(min(6, len(words)), 0, -1):
            key = self.resolve(" ".join(words[:n]))
            if key and len(" ".join(words[n:])) > 2:
                return key, " ".join(words[n:])
        return None


_TASK_RE = re.compile(
    r"^(?:ok(?:ay)?\s+|please\s+|can you\s+|could you\s+)*"
    r"(?:start|create|launch|run|spawn|kick off|begin|open)\s+(?:a\s+|an\s+|the\s+)?(?:new\s+)?"
    r"(?:task|job|session)\s+(?:in|on|for|inside)\s+(?:the\s+|my\s+)?(?P<rest>.+)$",
    re.I | re.S,
)
_STATUS_RE = re.compile(r"\b(how are|status of|what are|how's|hows|check on)\b.*\b(tasks?|jobs?)\b", re.I)
_NOOP_RE = re.compile(r"^(never ?mind|cancel|stop|nothing|forget it)[.!]?$", re.I)
# Questions that obviously need the user's own data / live info -> tier (b) directly.
_LOOKUP_RE = re.compile(
    r"\b(telegram|gmail|e-?mails?|inbox|calendar|schedule|meetings?|appointments?|drive|"
    r"google docs?|messaged|messages?|dms?|unread|notifications?|my files?|weather|news|"
    r"search (the web|online|for)|look up|google)\b", re.I)


def local_route(text: str, projects: Projects):
    t = text.strip()
    if _NOOP_RE.match(t):
        return ("noop",)
    if _STATUS_RE.search(t):
        return ("status",)
    m = _TASK_RE.match(t)
    if not m:
        if _LOOKUP_RE.search(t):
            return ("lookup", t)
        return None
    rest = m.group("rest").strip()
    # With a separator: "<project>: <task>" / "<project>, <task>" / "<project> project. <task>"
    sep = re.match(r"^(?P<p>[^:,.;\-–—]{1,40}?)(?:\s+(?:project|folder|repo))?\s*[:,.;\-–—]\s*(?P<t>.+)$", rest, re.S)
    if sep:
        key = projects.resolve(sep.group("p"))
        if key:
            return ("task", key, sep.group("t").strip())
    # No separator: longest known project name at the front.
    split = projects.split_prefix(norm(rest))
    if split:
        return ("task", split[0], _strip_lead(rest, split[0]))
    # A task was clearly asked for but the project is unknown.
    return ("task", None, rest)


def _strip_lead(rest: str, key: str) -> str:
    n = len(key.split())
    words = rest.split()
    tail = " ".join(words[n:]).lstrip(":,.;-–— ")
    tail = re.sub(r"^(project|folder|repo)\b[:,.;\-–— ]*", "", tail, flags=re.I)
    return tail or rest


def system_prompt(projects: Projects, has_agent: bool = True) -> str:
    names = ", ".join(projects.map) or "(none configured)"
    lookup = (
        "If answering needs the user's own data or live information (their messages, Telegram, "
        "email, calendar, files, Drive, or a web search, news, weather, prices), do not guess; "
        "reply with exactly: LOOKUP\n" if has_agent else "")
    return (
        "You are a voice assistant on the user's Windows PC. Your reply is read aloud by TTS, so: "
        "plain sentences, no markdown, no lists, no code, no emoji. Answer in one or two short "
        "sentences unless asked for more. Today is " + datetime.now().strftime("%A %d %B %Y") + ".\n"
        "You have no tools. " + lookup +
        "If the user asks for real work on a project (writing or changing code or files, "
        "investigating a repo, anything that takes more than a minute), do not answer it; instead "
        "reply with exactly one line:\n"
        "TASK <project> :: <one-line task in imperative form>\n"
        "where <project> is one of: " + names + ". Use TASK ? :: <task> if none fits.\n"
        "Otherwise just answer."
    )


AGENT_PROMPT = (
    "You are the tool-using half of a voice assistant on the user's Windows PC. Each user message "
    "was spoken aloud and your final reply will be read aloud by TTS. Use your tools and MCP "
    "servers to look things up. Do not narrate what you are doing. Your final reply must be one "
    "to three short plain sentences: no markdown, lists, links, file paths or code. Don't change "
    "files or send anything on the user's behalf unless they explicitly asked for exactly that."
)


class _CliChat:
    """A `claude` CLI process fed user messages over stream-json.

    persistent=True keeps one process warm between questions (start-up paid
    once, conversation remembered); it is recycled after max_turns.
    final_only=True yields only the final answer of each turn (for the agent,
    whose intermediate messages are tool-calling chatter).
    """

    def __init__(self, exe: str, args: list[str], *, cwd: str, persistent: bool = True,
                 max_turns: int = 20, final_only: bool = False, name: str = "fast", timeout: float = 60):
        self.exe, self.args, self.cwd = exe, args, cwd
        self.persistent, self.max_turns, self.final_only = persistent, max_turns, final_only
        self.name, self.timeout = name, timeout
        self.proc = None
        self.lines: queue.Queue = queue.Queue()
        self.turns = 0
        self.lock = threading.Lock()
        self.init_info: dict | None = None
        if persistent:
            self._spawn()

    def _spawn(self, prompt: str | None = None):
        cmd = [self.exe, "-p"] + (["--input-format", "stream-json"] if prompt is None else [prompt]) + self.args
        self.lines = queue.Queue()
        from .config import LOG_DIR
        err = open(LOG_DIR / f"{self.name}.stderr.log", "a", encoding="utf-8")
        err.write(f"--- {time.ctime()} start\n")
        err.flush()
        self.proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE if prompt is None else subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=err, text=True, encoding="utf-8",
            errors="replace", bufsize=1, env=child_env(), creationflags=NO_WINDOW, cwd=self.cwd,
        )
        self.turns = 0
        q, p = self.lines, self.proc
        threading.Thread(target=lambda: [q.put(l) for l in p.stdout] + [q.put(None)], daemon=True).start()

    def close(self):
        if self.proc and self.proc.poll() is None:
            try:
                if self.proc.stdin:
                    self.proc.stdin.close()
                self.proc.wait(timeout=3)
            except Exception:
                self.proc.kill()

    def stream(self, text: str):
        with self.lock:
            if self.persistent:
                if self.proc is None or self.proc.poll() is not None or self.turns >= self.max_turns:
                    self.close()
                    self._spawn()
                msg = json.dumps({"type": "user", "message": {"role": "user", "content": text}}) + "\n"
                try:
                    self.proc.stdin.write(msg)
                    self.proc.stdin.flush()
                except OSError:
                    self._spawn()
                    self.proc.stdin.write(msg)
                    self.proc.stdin.flush()
                self.turns += 1
            else:
                self._spawn(text)
            yield from self._read_turn()

    def _read_turn(self):
        got_text = False
        recent: list[str] = []
        while True:
            try:
                line = self.lines.get(timeout=self.timeout)
            except queue.Empty:
                raise RuntimeError(f"claude CLI ({self.name}) timed out")
            if line is None:
                if not got_text:
                    raise RuntimeError(f"claude CLI ({self.name}) exited without answering "
                                       f"(exit code {self.proc.poll()}); last output: {' | '.join(recent)[-600:]}")
                return
            recent = (recent + [line.strip()[:200]])[-4:]
            if not line.startswith("{"):
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = ev.get("type")
            if t == "system" and ev.get("subtype") == "init":
                self.init_info = ev
            elif t == "stream_event" and not self.final_only:
                e = ev.get("event", {})
                d = e.get("delta", {})
                if e.get("type") == "content_block_delta" and d.get("type") == "text_delta":
                    got_text = True
                    yield d["text"]
                elif e.get("type") == "content_block_stop" and got_text:
                    # The CLI sends its `result` ~2 s after the text ends; a
                    # newline here lets the last sentence be spoken right away.
                    yield "\n"
            elif t == "result":
                if ev.get("is_error"):
                    raise RuntimeError(str(ev.get("result") or ev.get("subtype")))
                if not got_text and ev.get("result"):
                    yield ev["result"]
                return


class _SdkChat:
    """Anthropic SDK backend for tier (a), used when ANTHROPIC_API_KEY is set."""

    def __init__(self, model: str, sys_prompt: str, max_tokens: int):
        import anthropic

        self.client = anthropic.Anthropic()
        self.model, self.sys_prompt, self.max_tokens = model, sys_prompt, max_tokens
        self.history: list[dict] = []

    def stream(self, text: str):
        self.history.append({"role": "user", "content": text})
        self.history = self.history[-12:]
        if self.history[0]["role"] != "user":
            self.history = self.history[1:]
        out = []
        with self.client.messages.stream(
            model=self.model, max_tokens=self.max_tokens, system=self.sys_prompt,
            messages=self.history,
        ) as s:
            for chunk in s.text_stream:
                out.append(chunk)
                yield chunk
        self.history.append({"role": "assistant", "content": "".join(out) or "(no answer)"})

    def close(self):
        pass


def _desktop_mcp_file(names: list[str], out_path) -> str | None:
    """Copy the named servers out of the Claude desktop app's MCP config.

    The desktop app keeps its own MCP servers (e.g. telegram) in
    %LOCALAPPDATA%\\Claude-Data\\<id>\\claude_desktop_config.json, which the
    standalone CLI never reads. The copy (with any env/tokens it carries)
    goes to state/, which is git-ignored.
    """
    import glob

    local = os.environ.get("LOCALAPPDATA", "")
    files = glob.glob(os.path.join(local, "Claude-Data", "*", "claude_desktop_config.json"))
    files += glob.glob(os.path.join(os.environ.get("APPDATA", ""), "Claude", "claude_desktop_config.json"))
    servers = {}
    for f in files:
        try:
            servers.update(json.load(open(f, encoding="utf-8")).get("mcpServers") or {})
        except Exception:
            pass
    picked = {n: servers[n] for n in names if n in servers}
    missing = [n for n in names if n not in servers]
    if missing:
        print(f"[agent] desktop MCP servers not found: {', '.join(missing)}")
    if not picked:
        return None
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump({"mcpServers": picked}, fh, indent=1)
    return str(out_path)


class Agent:
    """Tier (b): one warm Claude Code process with tools, reused for every lookup."""

    def __init__(self, cfg: dict, exe: str):
        from .config import ROOT, STATE_DIR

        a = cfg["agent"]
        args = ["--model", a["model"], "--output-format", "stream-json", "--verbose",
                "--permission-mode", a["permission_mode"], "--permission-prompts", "none",
                "--append-system-prompt", AGENT_PROMPT, "--name", "hey-claude assistant"]
        if a.get("allowed_tools"):
            args += ["--allowedTools", ",".join(a["allowed_tools"])]
        mcp = list(a.get("mcp_configs") or [])
        if a.get("import_desktop_mcp"):
            f = _desktop_mcp_file(list(a["import_desktop_mcp"]), STATE_DIR / "agent-mcp.json")
            if f:
                mcp.append(f)
        if mcp:
            args += ["--mcp-config", *mcp]
        args += [str(x) for x in a.get("extra_args") or []]
        self.chat = _CliChat(exe, args, cwd=a.get("cwd") or str(ROOT), persistent=True,
                             max_turns=int(a.get("max_turns", 30)), final_only=True, name="agent",
                             timeout=float(a.get("timeout", 180)))
        self.reported = False

    def ask(self, text: str):
        for chunk in self.chat.stream(text):
            yield chunk
        if not self.reported and self.chat.init_info:
            self.reported = True
            i = self.chat.init_info
            mcps = ", ".join(f"{m.get('name')}({m.get('status')})" for m in i.get("mcp_servers", [])) or "none"
            print(f"[agent] model {i.get('model')}, {len(i.get('tools', []))} tools, MCP: {mcps}")

    def close(self):
        self.chat.close()


class Brain:
    def __init__(self, cfg: dict):
        self.projects = Projects(cfg["projects"])
        fast = cfg["fast"]
        backend = fast["backend"]
        if backend == "auto":
            backend = "sdk" if os.environ.get("ANTHROPIC_API_KEY") else "cli"
        self.backend = backend
        exe = find_claude(cfg["claude"].get("exe")) if backend == "cli" or cfg["agent"]["enabled"] else None
        self.agent = Agent(cfg, exe) if cfg["agent"]["enabled"] else None
        sp = system_prompt(self.projects, has_agent=self.agent is not None)
        if backend == "sdk":
            self.chat = _SdkChat(fast["sdk_model"], sp, int(fast["max_tokens"]))
        else:
            self.chat = _CliChat(exe, fast_args(fast["cli_model"], sp), cwd=os.path.expanduser("~"),
                                 persistent=bool(fast["persistent"]), max_turns=int(fast["max_turns"]))
        self.timing: dict = {}
        self.warm = threading.Event()
        threading.Thread(target=self._warm_up, daemon=True).start()

    def _warm_up(self):
        """One throwaway turn per warm process, so the user's first question
        doesn't pay the first-turn cost (~1.5 s measured)."""
        jobs = []
        if isinstance(self.chat, _CliChat) and self.chat.persistent:
            jobs.append(("fast", lambda: self.chat.stream("Reply with just: ok")))
        if self.agent:
            jobs.append(("agent", lambda: self.agent.ask("Reply with just: ok. Don't use any tools.")))
        for name, job in jobs:
            t = time.perf_counter()
            try:
                for _ in job():
                    pass
                print(f"[warm] {name} ready in {time.perf_counter() - t:.1f}s")
            except Exception as e:
                print(f"[warm] {name} failed: {e}")
        self.warm.set()

    def close(self):
        self.chat.close()
        if self.agent:
            self.agent.close()

    def handle(self, text: str):
        routed = local_route(text, self.projects)
        if routed and not (routed[0] == "lookup" and not self.agent):
            self.timing["route"] = "local-" + routed[0]
            yield routed
            return
        self.timing["route"] = self.backend
        t0 = time.perf_counter()
        buf = ""
        decided = None   # None until we know if it's a TASK/LOOKUP line or an answer
        for chunk in self.chat.stream(text):
            if "first_token" not in self.timing:
                self.timing["first_token"] = time.perf_counter() - t0
            if decided is None:
                buf += chunk
                head = buf.lstrip()
                if len(head) < 6 and ("TASK ".startswith(head[:5]) or "LOOKUP".startswith(head[:6])):
                    continue            # could still turn into a control line
                decided = "task" if head.startswith("TASK") else "lookup" if head.startswith("LOOKUP") else "say"
                if decided == "say":
                    yield ("say", buf)
                continue
            if decided == "say":
                yield ("say", chunk)
            else:
                buf += chunk
        if decided is None and buf.strip():
            head = buf.strip()
            decided = "task" if head.startswith("TASK") else "lookup" if head.startswith("LOOKUP") else "say"
            if decided == "say":
                yield ("say", buf)
        if decided == "lookup":
            if self.agent:
                self.timing["route"] += "+agent"
                yield ("lookup", text)
            else:
                yield ("say", "I'd need your tools for that, and the tool agent is turned off.")
        elif decided == "task":
            m = re.match(r"\s*TASK\s+(.+?)\s*::\s*(.+)", buf, re.S)
            if not m:
                yield ("say", "I wanted to start a task but lost track of it. Please say it again.")
                return
            proj, task = m.group(1).strip(), m.group(2).strip().splitlines()[0]
            yield ("task", None if proj == "?" else self.projects.resolve(proj), task)
