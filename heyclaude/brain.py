"""Router and the two talking tiers.

Every utterance goes through `Brain.handle(text)`, which yields events:

    ("say", chunk)              text to speak, streamed as it arrives
    ("lookup", question)        needs the user's tools: hand to the warm agent
    ("task", project, task)     launch a headless Claude Code run
    ("status",)                 user asked how the tasks are going
    ("noop",)                   nothing to do ("never mind")
    ("fresh",)                  "start fresh": forget the conversation
    ("task_ask", ref, question) "what did the inha app task change?"
    ("task_stop", ref)          "stop the task"
    ("task_resume", ref)        "resume the last task in a terminal"
    ("control", text)           "volume 30", "dark mode": the agent with the Windows tools, no filler
    ("undo",)                   "undo that": revert the last Windows settings change locally
    ("instant", match)          M10: "volume 30", "next song", "timer 5 minutes": run locally, no model
                                (heyclaude/intents; match is an intents.Match)
    ("weather", query)          a plain weather question: answered locally from weather-mcp (weather_mcp/)

`ref` is how the user named a task ("inha app", "last", "readme", "#<id>");
tasks.find_task turns it into a task.

All tiers share one conversation (conversation.Transcript): each request is
sent with the turns that tier hasn't seen yet, so context survives a switch
between the quick model, the tool agent and tasks.

Three tiers:
  (a) quick answers, no tools: the Anthropic API directly (haiku, the
      low-latency option) when hey-claude has an API key (ANTHROPIC_API_KEY,
      or the file `fast.api_key_file`), else one warm tool-less `claude`
      process on the subscription login. No `claude` process ever gets the
      key (claude_cli.child_env strips it).
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
from .conversation import Transcript, session_name

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
# Controlling this PC (M9) -> the agent with the Windows settings tools, no model hop first.
_PC_RE = re.compile(
    r"\b(volume|mute|unmute|louder|quieter|brightness|brighter|dimmer|dark (?:mode|theme)|light (?:mode|theme)|"
    r"wallpaper|desktop background|wi-?fi|bluetooth|airplane mode|night light|power mode|battery|"
    r"(?:next|previous|last|skip(?: this| the)?) (?:song|track)|skip (?:this|it)|"
    r"(?:pause|resume|stop|play) (?:the |my )?(?:music|song|video|track|playback|spotify)|"
    r"what(?:'?s| is) (?:playing|this song)|what song|now playing|"
    r"lock (?:the |my )?(?:pc|computer|laptop|screen)|screenshot|on my screen|"
    r"(?:open|show) (?:the )?[\w -]{0,30}settings|snap (?:this|the|it|[\w ]{1,30}) (?:window )?(?:to the )?(?:left|right)|"
    r"minimi[sz]e|clipboard|(?:put|send) (?:the |my )?(?:pc|computer|laptop) to sleep|"
    r"(?:restart|shut ?down|reboot) (?:the |my )?(?:pc|computer|laptop))\b", re.I)
_UNDO_RE = re.compile(
    r"^(?:(?:and|so|ok(?:ay)?|please|now|then|can you|could you|would you|hey|no|oops|wait)[,\s]+)*"
    r"(?:undo|revert|reverse)(?: (?:that|it|this|the last (?:change|thing|one)|what you (?:just )?did))?"
    r"(?:,? please)?[.!]?$|"
    r"^(?:(?:and|so|ok(?:ay)?|please|no|oops)[,\s]+)*(?:put|change|set|switch|turn) (?:it|that) back(?:,? please)?[.!]?$",
    re.I)


_POLITE = r"^(?:(?:and|so|ok(?:ay)?|please|now|then|can you|could you|would you|hey)[,\s]+)*"
_FRESH_RE = re.compile(
    _POLITE + r"(?:let'?s\s+)?(?:start (?:fresh|over|again|a new conversation)|new conversation|"
    r"forget (?:this|that|our|the|everything)(?: conversation| chat)?|clear (?:the |our )?(?:conversation|context|chat))"
    r"\b[^?]*[.!]?$", re.I)
_TASK_WORD = r"(?:tasks?|jobs?|runs?|sessions?)"
_TSTOP_RE = re.compile(
    _POLITE + r"(?:stop|cancel|kill|abort|end|halt|terminate)\s+(?:the\s+|my\s+|that\s+|this\s+)?"
    r"(?P<ref>[^.?!]*?)\s*\b" + _TASK_WORD + r"\b(?:\s+(?:in|for|on)\s+(?:the\s+)?(?P<ref2>[^.?!]+?))?"
    r"\s*(?:now|please)?[.!?]*$", re.I)
_TRESUME_RE = re.compile(
    _POLITE + r"(?P<verb>resume|continue|reopen|re-open|open|pick up|bring up)\s+(?:the\s+|my\s+|that\s+)?"
    r"(?P<ref>[^.?!]*?)\s*\b(?:task|job|session)\b(?P<tail>[^?]*)$", re.I)
_TERMINAL = re.compile(r"\b(terminal|console|command line|window|claude code)\b", re.I)
# Words that make "... task ..." a question about what a task did. Status
# questions ("how's the task going") are caught before this.
_TASK_VERB = (r"\b(?:did|does|has|have|was|were|change[sd]?|do|done|find|found|fix(?:ed)?|add(?:ed)?|make|made|"
              r"write|wrote|results?|summary|summari[sz]e|happen(?:ed)?|go|went|say|said|touch(?:ed)?|"
              r"modif(?:y|ied)|edit(?:ed)?|break|broke|explain|tell)\b")
_TASK_ASK_RE = re.compile(_POLITE + r"(?:what|which|how|why|did|does|has|was|were|tell me|summari[sz]e|explain|"
                          r"give me|can you tell)\b.*\b(?:task|job)s?\b", re.I)
_REF_RE = re.compile(r"\b(?:the|that|my|this|last)\s+(?P<ref>[\w' -]*?)\s*\b(?:task|job)\b", re.I)
# "what did it change?" right after a task was announced or talked about.
_IT_RE = re.compile(_POLITE + r"(?:what|how|did|does|has|was|why|tell me|which)\b.*\b(?:it|that)\b.*" + _TASK_VERB,
                    re.I)


def _task_ref(text: str, projects: Projects) -> str:
    """'what did the inha app task change' -> 'inha app'; 'the last task' -> 'last'."""
    m = _REF_RE.search(text)
    ref = m.group("ref").strip() if m else ""
    if m and not ref and norm(m.group(0)).startswith("last"):
        ref = "last"
    if not ref or ref.lower() in ("last", "latest"):
        # "the task in inha app", "what did inha app's task do"
        s = f" {norm(text)} "
        for key in sorted(projects.map, key=len, reverse=True):
            if f" {norm(key)} " in s:
                return key
    return ref


def local_route(text: str, projects: Projects):
    t = text.strip()
    if _NOOP_RE.match(t):
        return ("noop",)
    if _UNDO_RE.match(t):
        return ("undo",)
    if _FRESH_RE.match(t):
        return ("fresh",)
    m = _TSTOP_RE.match(t)
    if m:
        ref = (m.group("ref2") or m.group("ref") or "").strip()
        if norm(ref) in ("all", "all the", "all of the", "all my", "every", "all running", "all of my"):
            ref = "all"
        return ("task_stop", ref)
    m = _TRESUME_RE.match(t)
    if m and (m.group("verb").lower() != "open" or _TERMINAL.search(m.group("tail") or "")):
        ref = m.group("ref").strip()
        if not ref:
            tail = re.sub(r"\b(in|on|inside)\s+(a|the|my)?\s*(new\s+)?(terminal|console|window|command line)\b.*",
                          "", m.group("tail") or "", flags=re.I).strip()
            ref = _task_ref(f"the task {tail}", projects) if tail else ""
        return ("task_resume", ref)
    if _STATUS_RE.search(t):
        return ("status",)
    if _TASK_ASK_RE.match(t) and re.search(_TASK_VERB, t, re.I) and not _TASK_RE.match(t):
        return ("task_ask", _task_ref(t, projects), t)
    m = _TASK_RE.match(t)
    if not m:
        if _PC_RE.search(t):
            return ("control", t)
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


def widget_prompt(cfg: dict | None) -> str:
    """The response-widget contract (ui/widgets.schema.json) for either tier;
    off with island.widgets: false."""
    if not ((cfg or {}).get("island") or {}).get("widgets", True):
        return ""
    from .widgets import prompt_text
    return ("\n\nIsland cards. " + prompt_text() +
            "\nThe WIDGET line is the only exception to the plain-spoken-sentences rule, and it must come last."
            "\nYou may also end with one line `MOOD happy`, `MOOD excited`, `MOOD sorry` or `MOOD confused` when "
            "the answer clearly carries that feeling (something worked, good news, an apology, you didn't "
            "understand); the island's character shows it. Most answers have no MOOD line.")


def system_prompt(projects: Projects, has_agent: bool = True) -> str:
    names = ", ".join(projects.map) or "(none configured)"
    lookup = (
        "If answering needs the user's own data or live information (their messages, Telegram, "
        "email, calendar, files, Drive, or a web search, news, weather, prices), or controlling this "
        "PC (volume, brightness, dark mode, wallpaper, Wi-Fi, Bluetooth, music, apps, windows, Settings), "
        "do not guess; reply with exactly: LOOKUP\n" if has_agent else "")
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
        "A message may start with a note in parentheses holding earlier turns of this conversation "
        "that another part of the assistant answered (lookups with the user's tools, tasks); treat "
        "them as part of the conversation, e.g. to know what 'there' or 'then' refers to.\n"
        "Otherwise just answer."
    )


AGENT_PROMPT = (
    "You are the tool-using half of a voice assistant on the user's Windows PC. Each user message "
    "was spoken aloud and your final reply will be read aloud by TTS. Use your tools and MCP "
    "servers to look things up. Do not narrate what you are doing. Your final reply must be one "
    "to three short plain sentences: no markdown, lists, links, file paths or code. Don't change "
    "files or send anything on the user's behalf unless they explicitly asked for exactly that. "
    "A message may start with a note in parentheses holding earlier turns of the same voice "
    "conversation that another part of the assistant answered; use it to resolve 'there', "
    "'then', 'that' and the like."
)
# Added when the Windows settings server is on (M9).
AGENT_WINDOWS_PROMPT = (
    " To control this PC (volume, mute, brightness, dark or light mode, wallpaper, night light, Wi-Fi, "
    "Bluetooth, power mode, media playback, opening apps, windows, clipboard, Settings pages) use the "
    "mcp__windows tools: they are exact and undoable. Act right away without asking; the island shows "
    "the change with an Undo button, so just confirm in a few words, e.g. 'Volume 30.' or 'Dark mode on.' "
    "Short commands: 'volume 30' means set it to 30; 'brightness down' or 'quieter' means about 20 less; "
    "'next song' means media_control next. For sleep, restart or shutdown call request_power_action and "
    "tell the user to click Confirm on the island; never try another way. Never click or type through "
    "the UI to change a setting; Windows-MCP tools, when present, are only for looking at the screen."
)

# Windows-MCP (CursorTouch) tools that act on the UI. They stay off the allow-list
# until M7's island approvals exist; PowerShell and Registry are never allowed.
WINDOWS_MCP_SERVER = "windows-mcp"
WINDOWS_MCP_READ_ONLY = ("Snapshot", "Screenshot")
NEVER_ALLOWED = re.compile(r"(?:^|__)(PowerShell|Registry)$|powershell|regedit", re.I)


TASK_QA_PROMPT = (
    "The user is asking about this session's work by voice, after it finished. Answer from what "
    "was done in this session; check git diff or git log if that helps. Do not change any "
    "files. Your reply is read aloud: one to three short plain sentences, no markdown, lists, "
    "file paths or code."
)


class _CliChat:
    """A `claude` CLI process fed user messages over stream-json.

    persistent=True keeps one process warm between questions (start-up paid
    once, conversation remembered); it is recycled after max_turns.
    final_only=True yields only the final answer of each turn (for the agent,
    whose intermediate messages are tool-calling chatter).
    """
    on_usage = None                         # M5: callback(dict) for Claude Code's rate_limit_event

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
        self.epoch = 0      # bumped on every new process: the Transcript re-sends context then
        self.on_tool = None  # callback(kind, block): "tool_use" / "tool_result" content blocks (M9 effects)
        self.on_usage = None  # callback(dict): Claude Code's rate_limit_event (M5 plan-usage pill)
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
        self.epoch += 1
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

    def recycle(self, warm: bool = True):
        """Forget the conversation: a new process (and, warm, one throwaway turn)."""
        with self.lock:
            self.close()
            if not self.persistent:
                return
            self._spawn()
        if warm:
            for _ in self.stream("Reply with just: ok"):
                pass

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
            if t in ("assistant", "user") and self.on_tool is not None:
                self._tool_blocks(t, ev)
            if t == "rate_limit_event" and self.on_usage is not None:
                try:
                    self.on_usage(usage_info(ev))
                except Exception as e:
                    print(f"[usage] {e}")
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

    def _tool_blocks(self, kind: str, ev: dict):
        """Hand tool_use (assistant) and tool_result (user) blocks to on_tool."""
        content = (ev.get("message") or {}).get("content")
        if not isinstance(content, list):
            return
        want = "tool_use" if kind == "assistant" else "tool_result"
        for block in content:
            if isinstance(block, dict) and block.get("type") == want:
                try:
                    self.on_tool(want, block)
                except Exception as e:          # an effect must never break an answer
                    print(f"[effects] {want} handler failed: {e}")


def usage_info(ev: dict) -> dict:
    """Claude Code's stream-json rate_limit_event -> {"five_hour": 0.06, "seven_day": 0.16,
    "resets": {...}, "status": "allowed"}. Utilisation is 0..1, as Claude Code reports it."""
    info = ev.get("rate_limit_info") or {}
    out: dict = {"status": info.get("status", ""), "resets": {}}
    for name, w in (info.get("unifiedWindows") or {}).items():
        if isinstance(w, dict) and isinstance(w.get("utilization"), (int, float)):
            out[name] = float(w["utilization"])
            if w.get("resetsAt"):
                out["resets"][name] = w["resetsAt"]
    return out


def api_key(fast_cfg: dict | None = None) -> str | None:
    """The key for the direct API path: ANTHROPIC_API_KEY, else the first
    line of `fast.api_key_file` (default state/anthropic-api-key.txt, git-ignored)."""
    k = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if k:
        return k
    from .config import ROOT, STATE_DIR
    f = (fast_cfg or {}).get("api_key_file") or str(STATE_DIR / "anthropic-api-key.txt")
    f = os.path.expandvars(os.path.expanduser(str(f)))
    if not os.path.isabs(f):
        f = str(ROOT / f)
    try:
        with open(f, encoding="utf-8") as fh:
            return (fh.readline().strip() or None)
    except OSError:
        return None


class _SdkChat:
    """Tier (a) straight to the Anthropic API (streaming), used when there's a key."""

    def __init__(self, model: str, sys_prompt: str, max_tokens: int, key: str | None = None):
        import anthropic

        self.client = anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()
        self.model, self.sys_prompt, self.max_tokens = model, sys_prompt, max_tokens
        self.history: list[dict] = []
        self.epoch = 1

    def recycle(self, warm: bool = False):
        self.history = []
        self.epoch += 1

    def warm(self):
        """Open the HTTPS connection (token counting is free), so the first
        question doesn't pay the TLS handshake."""
        self.client.messages.count_tokens(model=self.model, messages=[{"role": "user", "content": "ok"}])

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


def _console_python() -> str:
    """This interpreter, as python.exe (an MCP server needs stdio; pythonw has none)."""
    import sys
    exe = sys.executable
    if os.path.basename(exe).lower() == "pythonw.exe":
        cand = os.path.join(os.path.dirname(exe), "python.exe")
        if os.path.isfile(cand):
            return cand
    return exe


def find_windows_mcp(command: str | None = None) -> str | None:
    """The `windows-mcp` executable from `uv tool install windows-mcp` (its own
    isolated venv, never hey-claude's), or `command` if given."""
    import shutil
    if command:
        return command if os.path.isfile(command) or shutil.which(command) else None
    hit = shutil.which("windows-mcp")
    if hit:
        return hit
    home = os.path.expanduser("~")
    for p in (os.path.join(home, ".local", "bin", "windows-mcp.exe"),
              os.path.join(os.environ.get("APPDATA", ""), "uv", "tools", "windows-mcp", "Scripts", "windows-mcp.exe")):
        if os.path.isfile(p):
            return p
    return None


def windows_mcp_servers(agent_cfg: dict) -> dict:
    """mcpServers entries for M9: our settings server ("windows"), plus
    Windows-MCP ("windows-mcp") when it's switched on and installed."""
    from .config import ROOT
    servers = {}
    if agent_cfg.get("windows_settings", True):
        servers["windows"] = {"command": _console_python(), "args": ["-m", "heyclaude.mcp_windows"],
                              "env": {"PYTHONPATH": str(ROOT), "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}}
    wm = agent_cfg.get("windows_mcp") or {}
    if wm.get("enabled"):
        exe = find_windows_mcp(wm.get("command"))
        if exe:
            exclude = list(dict.fromkeys(["PowerShell", "Registry", *[str(x) for x in wm.get("exclude_tools") or []]]))
            servers[WINDOWS_MCP_SERVER] = {
                "command": exe, "args": ["serve", "--transport", "stdio", "--exclude-tools", ",".join(exclude)],
                "env": {"ANONYMIZED_TELEMETRY": "false"}}
        else:
            print("[agent] windows_mcp is on but `windows-mcp` isn't installed (uv tool install windows-mcp)")
    return servers


def weather_mcp_servers(cfg: dict) -> dict:
    """mcpServers entries: the built-in weather tool (Open-Meteo, keyless; M5), plus
    weather-mcp ("weather-mcp", M5b) when weather.mcp.agent is on and npx is there."""
    from .config import ROOT
    from .weather_mcp import agent_servers
    w = cfg.get("weather") or {}
    if not w.get("enabled", True):
        return {}
    return {"weather": {"command": _console_python(), "args": ["-m", "heyclaude.mcp_weather"],
                        "env": {"PYTHONPATH": str(ROOT), "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1",
                                "HEYCLAUDE_WEATHER_LOCATION": str(w.get("location") or ""),
                                "HEYCLAUDE_WEATHER_UNIT": str(w.get("unit") or "C")}},
            **agent_servers(cfg)}


WEATHER_TOOL = "mcp__weather__get_weather"
AGENT_WEATHER_PROMPT = (
    " For any weather question call mcp__weather__get_weather (empty location = the user's home place); "
    "the island shows the forecast card from its result by itself, so don't add a WIDGET line for "
    "weather; just say the gist, e.g. 'Fifteen degrees and clear, rain after midnight.'"
)
AGENT_WEATHER_MCP_PROMPT = (
    " For official weather warnings, air quality, UV, pollen, a rain nowcast, the moon or a forecast beyond "
    "three days, use the mcp__weather-mcp tools (get_weather_summary first; city_name for a place)."
)


def gate_allowed_tools(tools: list[str]) -> tuple[list[str], list[str]]:
    """(kept, dropped). PowerShell / Registry never pass, and of Windows-MCP
    only the read-only tools do: its acting tools (Click, Type, Shortcut, Drag,
    App...) wait for M7's island approvals."""
    kept, dropped = [], []
    wm = f"mcp__{WINDOWS_MCP_SERVER}"
    for t in tools:
        s = str(t).strip()
        bad = bool(NEVER_ALLOWED.search(s))
        if s == wm or s.startswith(wm + "__"):
            bad = bad or s[len(wm) + 2:] not in WINDOWS_MCP_READ_ONLY
        (dropped if bad else kept).append(s)
    return kept, dropped


class Agent:
    """Tier (b): one warm Claude Code process with tools, reused for every lookup."""
    on_tool_use = None                      # M5: callback({"id", "name", "phase", "input"}) for the island's icons

    def __init__(self, cfg: dict, exe: str):
        from .config import ROOT

        self.cfg = cfg
        a = cfg["agent"]
        self.chat = _CliChat(exe, self._args(), cwd=a.get("cwd") or str(ROOT), persistent=True,
                             max_turns=int(a.get("max_turns", 30)), final_only=True, name="agent",
                             timeout=float(a.get("timeout", 180)))
        self.reported = False
        # M9: tool calls are watched so changes get an island effect and an undo.
        self.on_tool_result = None          # callback(name, input, result, is_error, raw=text)
        self.on_tool_use = None             # M5: callback({"id", "name", "phase": "start"|"end", "input"}) for the icons
        self._pending_tools: dict[str, tuple[str, dict]] = {}
        self.chat.on_tool = self._tool_event

    def _tool_event(self, kind: str, block: dict):
        if kind == "tool_use":
            self._pending_tools[block.get("id", "")] = (block.get("name", ""), block.get("input") or {})
            if self.on_tool_use is not None:
                self.on_tool_use({"id": block.get("id", ""), "name": block.get("name", ""), "phase": "start",
                                  "input": block.get("input") or {}})
            return
        if self.on_tool_use is not None:
            self.on_tool_use({"id": block.get("tool_use_id", ""), "phase": "end"})
        name, args = self._pending_tools.pop(block.get("tool_use_id", ""), ("", {}))
        if not name or self.on_tool_result is None:
            return
        from .effects import parse_result, result_text
        content = block.get("content")
        self.on_tool_result(name, args, parse_result(content), bool(block.get("is_error")),
                            raw=result_text(content))

    def _args(self) -> list[str]:
        """With conversation.save on, the session is saved and named per day
        ("hey-claude 2026-11-09"); with it off, nothing is written to disk."""
        from .config import STATE_DIR

        a = self.cfg["agent"]
        day = self.day = session_name(self.cfg.get("conversation"))
        win = windows_mcp_servers(a)
        win.update(weather_mcp_servers(self.cfg))
        prompt = (AGENT_PROMPT + (AGENT_WINDOWS_PROMPT if "windows" in win else "")
                  + (AGENT_WEATHER_PROMPT if "weather" in win else "")
                  + (AGENT_WEATHER_MCP_PROMPT if "weather-mcp" in win else "") + widget_prompt(self.cfg))
        args = ["--model", a["model"], "--output-format", "stream-json", "--verbose",
                "--permission-mode", a["permission_mode"], "--permission-prompts", "none",
                "--append-system-prompt", prompt, "--name", day or "hey-claude assistant"]
        if not day:
            args.append("--no-session-persistence")
        if a.get("allowed_tools"):
            allowed = list(a["allowed_tools"])
            if "weather" in win and WEATHER_TOOL not in allowed:
                allowed.append(WEATHER_TOOL)              # read-only and keyless: always allowed when on
            if "weather-mcp" in win:                      # M5b: exactly weather.mcp.tools (read-only, keyless)
                from .weather_mcp import agent_tools
                allowed += [t for t in agent_tools(self.cfg) if t not in allowed]
            kept, dropped = gate_allowed_tools(allowed)
            if dropped:
                print(f"[agent] not allowed without approvals (M7): {', '.join(dropped)}")
            if kept:
                args += ["--allowedTools", ",".join(kept)]
        # Tools it must not even be offered: Claude Code's own PowerShell (Windows) would be
        # refused anyway (not allowed), but the model asking for it costs a turn.
        dis = ["PowerShell"] + [str(t) for t in a.get("disallowed_tools") or [] if str(t) != "PowerShell"]
        args += ["--disallowedTools", ",".join(dis)]
        mcp = list(a.get("mcp_configs") or [])
        if a.get("import_desktop_mcp"):
            f = _desktop_mcp_file(list(a["import_desktop_mcp"]), STATE_DIR / "agent-mcp.json")
            if f:
                mcp.append(f)
        if win:
            f = STATE_DIR / "agent-windows-mcp.json"       # git-ignored, regenerated at every start
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            with open(f, "w", encoding="utf-8") as fh:
                json.dump({"mcpServers": win}, fh, indent=1)
            mcp.append(str(f))
        if mcp:
            args += ["--mcp-config", *mcp]
        args += [str(x) for x in a.get("extra_args") or []]
        return args

    def ask(self, text: str):
        if session_name(self.cfg.get("conversation")) != self.day:   # a new day, or "save" changed
            with self.chat.lock:
                self.chat.args = self._args()
                self.chat.close()          # the next stream() starts a new process
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
    convo: Transcript | None = None

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.convo = Transcript(cfg.setdefault("conversation", {}))
        self.projects = Projects(cfg["projects"])
        fast = cfg["fast"]
        backend = fast["backend"]
        key = api_key(fast) if backend in ("auto", "sdk") else None
        if backend == "auto":
            backend = "sdk" if key else "cli"
        self.backend = backend
        exe = find_claude(cfg["claude"].get("exe")) if backend == "cli" or cfg["agent"]["enabled"] else None
        self.exe = exe
        self.agent = Agent(cfg, exe) if cfg["agent"]["enabled"] else None
        sp = system_prompt(self.projects, has_agent=self.agent is not None) + widget_prompt(cfg)
        if backend == "sdk":
            self.chat = _SdkChat(fast["sdk_model"], sp, int(fast["max_tokens"]), key)
        else:
            self.chat = _CliChat(exe, fast_args(fast["cli_model"], sp), cwd=os.path.expanduser("~"),
                                 persistent=bool(fast["persistent"]), max_turns=int(fast["max_turns"]))
        self.timing: dict = {}
        self.intents = None                     # M10: the local intent engine (heyclaude/intents)
        try:
            from .config import STATE_DIR
            from .intents import IntentEngine
            self.intents = IntentEngine(cfg.setdefault("instant", {}), state_dir=STATE_DIR)
            print(f"[instant] intent engine ready in {self.intents.load_ms:.0f} ms "
                  f"({len(self.intents.clf.texts)} example phrasings)")
        except Exception as e:
            print(f"[instant] intent engine off: {e}")
        self.wx = None                      # M5b: the local weather route (weather_mcp.WeatherService)
        if (cfg.get("weather") or {}).get("enabled", True):
            from .weather_mcp import WeatherService
            self.wx = WeatherService(cfg)   # weather-mcp starts with the warm-up, in its own thread
        self.warm = threading.Event()
        threading.Thread(target=self._warm_up, daemon=True).start()

    def _warm_up(self):
        """One throwaway turn per warm process, so the user's first question
        doesn't pay the first-turn cost (~1.5 s measured)."""
        if getattr(self, "wx", None) is not None:
            try:
                self.wx.start()             # non-blocking: weather-mcp connects in the background
            except Exception as e:
                print(f"[warm] weather-mcp: {e}")
        jobs = []
        if isinstance(self.chat, _CliChat) and self.chat.persistent:
            jobs.append(("fast", lambda: self.chat.stream("Reply with just: ok")))
        elif isinstance(self.chat, _SdkChat):
            jobs.append(("fast (api)", lambda: iter([self.chat.warm()])))
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
        if getattr(self, "wx", None) is not None:
            self.wx.close()

    def weather_query(self, text: str):
        """M5b: a plain weather question -> its query for the local weather route, else None."""
        wx = getattr(self, "wx", None)
        if wx is None:
            return None
        turns = self.convo.turns if self.convo is not None else []
        return wx.query(text, prev_turn_was_weather=bool(turns and turns[-1].tier == "weather"))

    # -- shared conversation -----------------------------------------------------
    def with_context(self, consumer: str, chat, text: str) -> str:
        """`text` plus the conversation turns `consumer` hasn't seen."""
        if self.convo is None:
            return text
        out = self.convo.prompt_for(consumer, getattr(chat, "epoch", 0), text)
        if out != text:
            print(f"[context] {consumer} gets {out.count('User: ')} earlier turn(s)")
        return out

    def lookup(self, text: str):
        """Tier (b), with the shared conversation."""
        yield from self.agent.ask(self.with_context("agent", self.agent.chat, text))

    def forget(self):
        """'Start fresh': a new conversation, and new warm processes (in the
        background) so nothing of the old one is remembered."""
        if self.convo is not None:
            self.convo.reset("start fresh")

        def recycle():
            for name, chat in (("fast", self.chat), ("agent", self.agent.chat if self.agent else None)):
                if chat is None:
                    continue
                try:
                    chat.recycle(warm=True)
                    print(f"[conversation] {name} restarted")
                except Exception as e:
                    print(f"[conversation] {name} restart failed: {e}")
        threading.Thread(target=recycle, daemon=True, name="forget").start()

    def task_question_cmd(self, task, question: str) -> tuple[list[str], str]:
        """`claude -p <question> --resume <session> --fork-session ...` and its folder."""
        c = self.cfg.get("conversation") or {}
        args = ["--resume", task.session_id, "--fork-session", "--no-session-persistence",
                "--output-format", "stream-json", "--verbose",
                "--permission-mode", "default", "--permission-prompts", "none",
                "--allowedTools", "Read,Glob,Grep,Bash(git diff:*),Bash(git log:*),Bash(git status:*),Bash(git show:*)",
                "--append-system-prompt", TASK_QA_PROMPT]
        if c.get("task_question_model"):
            args += ["--model", str(c["task_question_model"])]
        return args, task.path

    def ask_task(self, task, question: str):
        """Answer a question about a finished task by resuming its session
        headlessly (forked and not saved, so the task's own session is left as
        it was; read-only tools). Yields the answer."""
        args, cwd = self.task_question_cmd(task, question)
        exe = self.exe or find_claude(self.cfg["claude"].get("exe"))
        timeout = float((self.cfg.get("conversation") or {}).get("task_question_timeout") or 120)
        chat = _CliChat(exe, args, cwd=cwd, persistent=False, final_only=True, name="task-question", timeout=timeout)
        # Said again in the message: after a long session the system prompt alone gets markdown back.
        yield from chat.stream(question + "\n\n(This answer is read aloud: one to three short plain "
                               "sentences, no markdown, bullet points, file paths or code.)")

    def instant(self, text: str):
        """M10: an intents.Match when `text` is an instant command, else None."""
        eng = getattr(self, "intents", None)
        if eng is None:
            return None
        try:
            m = eng.match(text)
        except Exception as e:
            print(f"[instant] match failed: {e}")
            return None
        if m is not None:
            self.timing["match_ms"] = round(m.match_ms, 2)
        return m

    def handle(self, text: str):
        if self.convo is not None:
            self.convo.expire_if_idle()
        routed = local_route(text, self.projects)
        if routed is None and self.convo is not None and _IT_RE.match(text.strip()):
            tid = self.convo.last_task_id()
            if tid:
                routed = ("task_ask", "#" + tid, text.strip())
        if routed is None or routed[0] in ("control", "lookup"):      # M10
            inst = self.instant(text)
            if inst is not None:
                routed = ("instant", inst)
        if routed is None or routed[0] in ("control", "lookup"):      # weather-mcp
            wq = self.weather_query(text)
            if wq is not None:
                routed = ("weather", wq)
        if routed and not (routed[0] in ("lookup", "control") and not self.agent):
            self.timing["route"] = "local-" + routed[0]
            yield routed
            return
        self.timing["route"] = self.backend
        t0 = time.perf_counter()
        buf = ""
        decided = None   # None until we know if it's a TASK/LOOKUP line or an answer
        for chunk in self.chat.stream(self.with_context("fast", self.chat, text)):
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
