"""The session hub: what every watched Claude Code session is doing, and the
approvals they wait on (M7).

The pipe server (pipe.py) calls `SessionHub.handle(msg, conn)` on a worker
thread for every hook event the relay forwards. The hub keeps one entry per
`session_id` (never one per agent kind), turns events into the island's
ticker / done moment, keeps the edit diffs, and runs the approval wait for
PermissionRequest:

  1. the card goes to the island; the island acknowledges it within ACK_S,
     or the request is passed back at once (the terminal asks);
  2. then it waits up to `hooks.approval_timeout_s` (≤ 108 s, under the relay's
     110 s and the hook's 120 s) for a click or a spoken answer;
  3. it is dropped early when it is settled elsewhere: the relay went away (the
     user answered in the terminal), or the same session moved on (Stop,
     StopFailure, UserPromptSubmit, SessionEnd, or a PostToolUse of the same
     tool and target).

Replies to the relay are only `allow` / `deny` / `always` / `answers` / `pass`;
the relay builds Claude Code's JSON. Voice is a convenience: anything flagged
dangerous needs a click.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field

from .diff import diff_entry

ACK_S = 0.8
MAX_PENDING = 5
APP_TIMEOUT_MAX = 108.0          # app 108 s < relay 110 s < hook 120 s
STEPS = 20
MAX_DIFFS = 50
DIFF_TTL_S = 3600.0
DONE_TO_IDLE_S = 5.0
ENDED_KEEP_S = 600.0             # an ended session stays on the list (diffs, jump) this long
STALE_S = 6 * 3600.0             # a session silent this long is dropped
EDIT_TOOLS = ("Edit", "MultiEdit", "Write", "NotebookEdit")
DENY_MESSAGE = "Denied from hey-claude"


# ----------------------------------------------------------------- ticker text
def _short(s, n: int) -> str:
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _base(p) -> str:
    p = str(p or "").replace("\\", "/").rstrip("/")
    return p.rsplit("/", 1)[-1] or p


def target_of(tool: str, inp: dict) -> str:
    """What a tool call is about: the command, file or query (for matching too)."""
    inp = inp or {}
    for k in ("command", "file_path", "notebook_path", "path", "pattern", "query", "url", "description", "prompt"):
        if inp.get(k):
            return str(inp[k])
    return ""


def ticker(tool: str, inp: dict) -> str:
    """`<Runs|Reads|Writes|Edits|Searches> · <command[:40] | basename(file) | query[:40]>`."""
    inp = inp or {}
    if tool in ("Bash", "PowerShell", "BashOutput"):
        return f"Runs · {_short(inp.get('command') or inp.get('description') or tool, 40)}"
    if tool in ("Read", "NotebookRead"):
        return f"Reads · {_base(inp.get('file_path') or inp.get('notebook_path'))}"
    if tool == "Write":
        return f"Writes · {_base(inp.get('file_path'))}"
    if tool in ("Edit", "MultiEdit", "NotebookEdit"):
        return f"Edits · {_base(inp.get('file_path') or inp.get('notebook_path'))}"
    if tool in ("Grep", "Glob"):
        return f"Searches · {_short(inp.get('pattern'), 40)}"
    if tool == "WebSearch":
        return f"Searches · {_short(inp.get('query'), 40)}"
    if tool == "WebFetch":
        m = re.match(r"https?://([^/]+)", str(inp.get("url") or ""))
        return f"Reads · {m.group(1) if m else _short(inp.get('url'), 40)}"
    if tool in ("Task", "Agent"):
        return f"Delegates · {_short(inp.get('description') or inp.get('subagent_type'), 40)}"
    if tool == "TodoWrite":
        return "Plans"
    if tool == "AskUserQuestion":
        return "Asks you"
    if tool.startswith("mcp__"):
        parts = tool.split("__")
        return f"Uses · {parts[1] if len(parts) > 1 else tool} {parts[-1] if len(parts) > 2 else ''}".rstrip()
    t = target_of(tool, inp)
    return f"{tool} · {_short(t, 40)}" if t else tool


# ----------------------------------------------------------------- danger
_DANGER_CMD = re.compile(r"""
    \brm\s+(-\w*\s+)*-\w*[rf] | \brm\s+-[-\w]*(recursive|force) | \brmdir\s+/s | \brd\s+/s
  | \bdel\s | \berase\s | Remove-Item | \bformat(\.com)?\s+[a-z]: | \bmkfs | \bdd\s+if=
  | \bgit\s+push | \bgit\s+reset\s+--hard | \bgit\s+clean\s+-\w*f | \bgit\s+checkout\s+--\s | \bgit\s+branch\s+-D
  | \bgit\s+filter-(branch|repo) | \bgit\s+rebase | \bgh\s+(pr\s+merge|release|repo\s+(delete|edit)|secret|api\s+-X)
  | \b(npm|pnpm|yarn)\s+publish | \btwine\s+upload | \bcargo\s+publish
  | (curl|wget|iwr|irm|Invoke-WebRequest|Invoke-RestMethod)\b[^|]*\|\s*(ba|z)?sh\b | \|\s*(iex|Invoke-Expression)\b
  | Invoke-Expression | \bshutdown\b | Stop-Computer | Restart-Computer | \breg\s+(add|delete|import)
  | Set-ItemProperty\s+[^|]*HK(LM|CU) | Set-ExecutionPolicy | \bnetsh\b | \bsc(\.exe)?\s+(delete|config|stop)
  | \btakeown\b | \bicacls\b | \bchmod\s+(-R\s+)?777 | \bsudo\b | \bkill(all)?\s+-9 | Stop-Process | \btaskkill\b
  | \bdocker\s+(rm|rmi|system\s+prune|volume\s+rm) | \bkubectl\s+delete | \bterraform\s+(apply|destroy)
  | \bdrop\s+(table|database)\b | \btruncate\s+table\b | >\s*/dev/sd | \bcipher\s+/w | \bvssadmin\b | \bbcdedit\b
""", re.I | re.X)
_DANGER_MCP = re.compile(r"send|delete|remove|post|publish|transfer|pay|purchase|forward|reply|trash|merge|deploy"
                         r"|create_(event|draft|issue|pr)|update|write|edit|move|share|invite|archive", re.I)
_SENSITIVE_PATH = re.compile(r"(^|[\\/])(\.ssh|\.gnupg|\.aws|\.azure|\.kube|\.git[\\/]config|\.env(\.|$)|\.npmrc|\.pypirc"
                             r"|\.claude[\\/]settings(\.local)?\.json|\.claude\.json|AppData[\\/]Roaming[\\/]Microsoft"
                             r"|[\\/]Windows[\\/]|Program Files)", re.I)


def is_dangerous(tool: str, inp: dict, cwd: str = "") -> bool:
    """Needs a click: destructive or outward-facing commands, writes outside the
    session's folder or to credentials/settings, and acting MCP tools."""
    inp = inp or {}
    if tool in ("Bash", "PowerShell"):
        return bool(_DANGER_CMD.search(str(inp.get("command") or "")))
    if tool in EDIT_TOOLS:
        p = str(inp.get("file_path") or inp.get("notebook_path") or "")
        if _SENSITIVE_PATH.search(p):
            return True
        if cwd and p and os.path.isabs(p):
            try:
                if os.path.commonpath([os.path.normcase(os.path.abspath(p)), os.path.normcase(os.path.abspath(cwd))]) \
                        != os.path.normcase(os.path.abspath(cwd)):
                    return True
            except ValueError:            # another drive
                return True
        return False
    if tool.startswith("mcp__"):
        return bool(_DANGER_MCP.search(tool.split("__")[-1]))
    return False


# ----------------------------------------------------------------- voice
def _norm(text: str) -> str:
    t = re.sub(r"[^\w\s']", " ", (text or "").lower())
    return " ".join(t.split())


_FILL = r"(?:\s+(?:it|that|this|this one|that one|the command|the edit|them|please|thanks|thank you|hey claude|claude|now|then))*"
_ALWAYS_RE = re.compile(r"^(?:yes\s+|yeah\s+|ok\s+|okay\s+)?(?:always(?:\s+allow)?|allow\s+(?:it\s+|that\s+)?always"
                        r"|(?:allow\s+(?:it\s+|that\s+)?and\s+)?don't\s+ask\s+(?:me\s+)?again)" + _FILL + "$")
_ALLOW_RE = re.compile(r"^(?:(?:yes|yeah|yep|yup|sure|ok|okay|fine|alright)\s*)?(?:please\s+)?"
                       r"(?:allow|approve|accept|go\s+ahead|do\s+it|run\s+it|let\s+it(?:\s+run)?|yes|yeah|yep|yup|sure|ok|okay)?"
                       + _FILL + "$")
_DENY_RE = re.compile(r"^(?:no|nope|nah)?\s*(?:,?\s*)?(?:deny|reject|block|refuse|don't|do\s+not|don't\s+allow|do\s+not\s+allow"
                      r"|don't\s+do\s+it|cancel|stop|no)?" + _FILL + "$")
_PASS_RE = re.compile(r"\b(in|from|on)\s+the\s+(terminal|console)\b|\bi'll\s+(answer|do\s+it|decide)\b")


def voice_decision(text: str) -> str | None:
    """A spoken answer to a pending approval: allow | deny | always | pass, or None."""
    t = _norm(text)
    if not t:
        return None
    if _PASS_RE.search(t):
        return "pass"
    if _ALWAYS_RE.match(t):
        return "always"
    if t not in ("please",) and _ALLOW_RE.match(t) and re.search(r"\b(allow|approve|accept|ahead|do|run|let|yes|yeah|yep|yup|sure|ok|okay|fine|alright)\b", t):
        return "allow"
    if _DENY_RE.match(t) and re.search(r"\b(no|nope|nah|deny|reject|block|refuse|don't|not|cancel|stop)\b", t):
        return "deny"
    return None


def voice_option(text: str, questions: list[dict]) -> dict | None:
    """A spoken choice for a single-question, single-select AskUserQuestion:
    the option whose label is said ("the first one" works too)."""
    if len(questions) != 1 or questions[0].get("multiSelect"):
        return None
    q = questions[0]
    labels = [str(o.get("label") or "") for o in q.get("options") or []]
    t = _norm(text)
    ords = {"first": 0, "1st": 0, "one": 0, "second": 1, "2nd": 1, "two": 1, "third": 2, "3rd": 2, "three": 2,
            "fourth": 3, "4th": 3, "four": 3, "last": len(labels) - 1}
    m = re.search(r"\b(?:the\s+)?(first|1st|second|2nd|third|3rd|fourth|4th|last)\b(?:\s+(?:one|option))?|\boption\s+(one|two|three|four)\b", t)
    if m:
        i = ords.get(m.group(1) or m.group(2), -1)
        if 0 <= i < len(labels):
            return {q["question"]: labels[i]}
    hits = [lab for lab in labels if _norm(lab) and re.search(r"\b" + re.escape(_norm(lab)) + r"\b", t)]
    if len(hits) == 1:
        return {q["question"]: hits[0]}
    return None


# ----------------------------------------------------------------- state
@dataclass
class Session:
    id: str
    cwd: str = ""
    title: str = ""
    first_prompt: str = ""
    status: str = "idle"          # working | waiting | done | error | idle
    activity: str = ""
    steps: deque = field(default_factory=lambda: deque(maxlen=STEPS))
    plus: int = 0
    minus: int = 0
    diffs: list = field(default_factory=list)
    desktop: bool = False
    task: str = ""
    started: float = 0.0
    updated: float = 0.0
    done_at: float = 0.0
    ended_at: float = 0.0
    last: str = ""
    transcript: str = ""
    hwnd: int | None = None
    window_checked: bool = False
    handoff_after_turn: bool = False

    @property
    def project(self) -> str:
        return _base(self.cwd)

    def row(self) -> dict:
        return {"id": self.id, "title": self.title or _short(self.first_prompt, 50), "project": self.project,
                "cwd": self.cwd, "status": self.status, "activity": self.activity, "plus": self.plus,
                "minus": self.minus, "diffs": len(self.diffs), "desktop": self.desktop, "task": self.task,
                "updated": self.updated, "last": self.last}

    def label(self) -> str:
        t = self.title or _short(self.first_prompt, 40)
        return f"{self.project} · {t}" if t and self.project else (t or self.project or self.id[:8])


@dataclass
class Pending:
    id: str
    session: str
    tool: str
    target: str
    req: dict
    dangerous: bool
    acked: threading.Event = field(default_factory=threading.Event)
    done: threading.Event = field(default_factory=threading.Event)
    result: dict | None = None


def read_title(transcript: str, tail_bytes: int = 256 * 1024) -> str:
    """The session's last custom title, else its AI title, from the transcript tail."""
    try:
        with open(transcript, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - tail_bytes))
            data = f.read().decode("utf-8", "replace")
    except OSError:
        return ""
    custom = ai = ""
    for ln in data.splitlines():
        if '"custom-title"' in ln or '"ai-title"' in ln:
            try:
                d = json.loads(ln)
            except ValueError:
                continue
            if d.get("type") == "custom-title" and d.get("customTitle"):
                custom = str(d["customTitle"])
            elif d.get("type") == "ai-title" and d.get("aiTitle"):
                ai = str(d["aiTitle"])
    return custom or ai


class SessionHub:
    def __init__(self, cfg: dict | None = None, *, on_sessions=None, on_event=None, on_approval=None,
                 on_withdraw=None, window_finder=None, clock=time.time):
        self.cfg = cfg if cfg is not None else {}      # the live `hooks:` section (Settings edits it in place)
        self.on_sessions = on_sessions or (lambda rows: None)
        self.on_event = on_event or (lambda ev: None)
        self.on_approval = on_approval or (lambda req: None)
        self.on_withdraw = on_withdraw or (lambda ap_id: None)
        self.on_handoff = None                # callback(session) when a queued handoff can run
        self.window_finder = window_finder    # (pid, cwd) -> hwnd | None
        self.clock = clock
        self.sessions: dict[str, Session] = {}
        self.pending: dict[str, Pending] = {}
        self.lock = threading.RLock()
        self._stop = threading.Event()
        self._ticker = None

    # -- config ---------------------------------------------------------------------
    @property
    def approvals_on(self) -> bool:
        return bool(self.cfg.get("approvals", True))

    @property
    def timeout_s(self) -> float:
        try:
            v = float(self.cfg.get("approval_timeout_s", 100))
        except (TypeError, ValueError):
            v = 100.0
        return max(5.0, min(APP_TIMEOUT_MAX, v))

    # -- lifecycle ------------------------------------------------------------------
    def start(self):
        if self._ticker is None:
            self._ticker = threading.Thread(target=self._tick_loop, name="session-hub", daemon=True)
            self._ticker.start()

    def stop(self):
        self._stop.set()
        with self.lock:
            for ap in list(self.pending.values()):
                self._settle(ap, {"reply": "pass"})

    def _tick_loop(self):
        while not self._stop.wait(1.0):
            try:
                self.tick()
            except Exception as e:                       # never let the ticker die
                print(f"[sessions] tick: {e}")

    def tick(self, now: float | None = None):
        now = self.clock() if now is None else now
        changed = False
        with self.lock:
            for s in list(self.sessions.values()):
                if s.status == "done" and s.done_at and now - s.done_at >= DONE_TO_IDLE_S:
                    s.status = "idle"
                    changed = True
                old = len(s.diffs)
                s.diffs = [d for d in s.diffs if now - d["t"] < DIFF_TTL_S]
                changed |= len(s.diffs) != old
                gone = (s.ended_at and now - s.ended_at > ENDED_KEEP_S) or (now - s.updated > STALE_S)
                if gone and not any(p.session == s.id for p in self.pending.values()):
                    del self.sessions[s.id]
                    changed = True
        if changed:
            self._push()

    # -- queries --------------------------------------------------------------------
    def snapshot(self) -> list[dict]:
        with self.lock:
            rows = [s.row() for s in self.sessions.values()]
        rows.sort(key=lambda r: r["updated"], reverse=True)
        return rows

    def session(self, sid: str) -> Session | None:
        return self.sessions.get(sid)

    def diffs(self, sid: str) -> list[dict]:
        s = self.sessions.get(sid)
        return list(s.diffs) if s else []

    def pending_requests(self) -> list[dict]:
        with self.lock:
            return [p.req for p in self.pending.values()]

    def find(self, ref: str = "") -> Session | None:
        """The session a spoken reference means: nothing / "it" = the most recently
        active one; else the one whose title or folder shares the most words."""
        with self.lock:
            pool = sorted(self.sessions.values(), key=lambda s: s.updated)
        if not pool:
            return None
        words = {w for w in _norm(ref).split() if len(w) > 2 and w not in
                 ("the", "that", "this", "session", "one", "claude", "code", "last", "latest", "current", "task", "show",
                  "open", "diff", "diffs", "changes", "change", "what", "did", "see", "let", "jump", "switch", "terminal",
                  "window", "move", "desktop", "app", "hand", "over", "into", "from", "with")}
        if not words:
            return pool[-1]
        best, score = None, 0
        for s in pool:
            have = set(_norm(f"{s.title} {s.first_prompt} {s.project}").split())
            n = len(words & have)
            if n and n >= score:
                best, score = s, n
        return best

    def dismiss(self, sid: str):
        with self.lock:
            if any(p.session == sid for p in self.pending.values()):
                return
            self.sessions.pop(sid, None)
        self._push()

    # -- events from the relay ---------------------------------------------------------
    def handle(self, msg: dict, conn=None):
        """pipe.PipeServer handler (a worker thread per connection)."""
        try:
            self._handle(msg, conn)
        except Exception as e:
            print(f"[sessions] {msg.get('event')}: {type(e).__name__}: {e}")
            if msg.get("wants_reply") and conn is not None:
                conn.reply({"reply": "pass"})

    def _session(self, payload: dict, msg: dict, conn) -> Session | None:
        sid = str(payload.get("session_id") or "")
        if not sid:
            return None
        now = self.clock()
        env = msg.get("env") or {}
        with self.lock:
            s = self.sessions.get(sid)
            new = s is None
            if new:
                s = self.sessions[sid] = Session(id=sid, started=now)
            if payload.get("cwd"):
                s.cwd = str(payload["cwd"])
            if payload.get("transcript_path"):
                s.transcript = str(payload["transcript_path"])
            if env.get("entrypoint") == "claude-desktop":
                s.desktop = True
            elif env.get("entrypoint"):
                s.desktop = False
            if env.get("heyclaude_task"):
                s.task = str(env["heyclaude_task"])
            s.updated = now
            s.ended_at = 0.0
        if not s.window_checked and self.window_finder is not None and not s.desktop:
            s.window_checked = True
            pid = int(msg.get("pid") or getattr(conn, "client_pid", 0) or 0)
            if pid:
                try:
                    s.hwnd = self.window_finder(pid, s.cwd)
                except Exception:
                    s.hwnd = None
        if new:
            self.on_event({"session": sid, "kind": "started", "title": s.label()})
        return s

    def _handle(self, msg: dict, conn):
        event = str(msg.get("event") or "")
        payload = msg.get("payload") or {}
        if not isinstance(payload, dict):
            return
        s = self._session(payload, msg, conn)
        if s is None:
            if msg.get("wants_reply") and conn is not None:
                conn.reply({"reply": "pass"})
            return
        tool = str(payload.get("tool_name") or "")
        inp = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
        now = self.clock()

        if event == "PermissionRequest":
            self._permission(s, tool, inp, payload, msg, conn)
            return

        if event == "SessionStart":
            s.status = "idle"
            self._title(s)
        elif event == "UserPromptSubmit":
            self._settle_session(s.id, "prompt")
            s.status, s.done_at, s.last = "working", 0.0, ""
            if not s.first_prompt:
                s.first_prompt = _short(payload.get("prompt"), 80)
            s.activity = "Thinking"
            self._title(s)
            self.on_event({"session": s.id, "kind": "tool_done"})
        elif event == "PreToolUse":
            s.status, s.done_at = "working", 0.0
            s.activity = ticker(tool, inp)
            s.steps.append({"t": now, "tool": tool, "text": s.activity})
            self.on_event({"session": s.id, "kind": "tool", "tool": tool, "input": inp, "text": s.activity})
        elif event in ("PostToolUse", "PostToolUseFailure"):
            self._settle_tool(s.id, tool, target_of(tool, inp))
            if event == "PostToolUse" and tool in ("Edit", "MultiEdit", "Write"):
                d = diff_entry(tool, inp, now, truncated=bool(msg.get("truncated")))
                if d:
                    s.diffs.append(d)
                    s.diffs = s.diffs[-MAX_DIFFS:]
                    s.plus += d["plus"]
                    s.minus += d["minus"]
                    s.activity = f"{ticker(tool, inp)} +{d['plus']} −{d['minus']}"
            if s.status == "waiting":
                s.status = "working"
            self.on_event({"session": s.id, "kind": "tool_done", "tool": tool})
        elif event == "Notification":
            text = str(payload.get("message") or "")
            if "rate limit" in text.lower():
                s.status, s.activity = "error", "rate limited"
            elif payload.get("notification_type") == "idle_prompt" or "waiting for your input" in text.lower():
                if s.status not in ("done", "idle"):
                    s.status, s.activity = "waiting", "waiting for you"
            elif text and s.status != "done":
                s.activity = _short(text, 60)
        elif event == "Stop":
            self._settle_session(s.id, "stop")
            s.status, s.done_at = "done", now
            last = str(payload.get("last_assistant_message") or "").strip()
            s.last = _short(last.splitlines()[-1] if last else "", 200)
            s.activity = "done"
            self._title(s)
            self.on_event({"session": s.id, "kind": "done", "title": s.label(), "text": f"done · {s.label()}"})
            if s.handoff_after_turn and self.on_handoff is not None:
                s.handoff_after_turn = False
                self.on_handoff(s)
        elif event == "StopFailure":
            self._settle_session(s.id, "stop")
            s.status = "error"
            err = payload.get("error") or payload.get("message") or "stopped with an error"
            s.activity = _short(err, 60)
            self.on_event({"session": s.id, "kind": "error", "title": s.label(), "text": f"{s.label()}: {s.activity}"})
        elif event == "SessionEnd":
            self._settle_session(s.id, "end")
            s.ended_at = now
            if s.status in ("working", "waiting"):
                s.status = "idle"
            self.on_event({"session": s.id, "kind": "idle"})
            if s.handoff_after_turn and self.on_handoff is not None:
                s.handoff_after_turn = False
                self.on_handoff(s)
        elif event in ("SubagentStart", "SubagentStop"):
            kind = str(payload.get("agent_type") or "")
            if not kind:                                  # Claude Code's own helper (Coucou #840)
                return
            if event == "SubagentStart":
                s.activity = f"Delegates · {_short(kind, 30)}"
        self._push()

    def _title(self, s: Session):
        if s.transcript:
            t = read_title(s.transcript)
            if t:
                s.title = t

    def _push(self):
        self.on_sessions(self.snapshot())

    # -- approvals ------------------------------------------------------------------
    def _permission(self, s: Session, tool: str, inp: dict, payload: dict, msg: dict, conn):
        def reply(obj):
            if conn is not None:
                conn.reply(obj)

        if not self.approvals_on:
            reply({"reply": "pass"})
            return
        question = tool == "AskUserQuestion"
        req = {"id": "ap-" + uuid.uuid4().hex[:10], "session": s.label(), "session_id": s.id, "tool": tool,
               "expires": self.clock() + self.timeout_s}
        if question:
            qs = inp.get("questions") if isinstance(inp.get("questions"), list) else []
            ok = bool(qs) and not msg.get("truncated") and all(
                isinstance(q, dict) and q.get("question") and isinstance(q.get("options"), list)
                and len(q["options"]) >= 2 and all(isinstance(o, dict) and o.get("label") for o in q["options"])
                for q in qs)
            if not ok:
                reply({"reply": "pass"})                 # the terminal asks
                return
            req.update(kind="question", dangerous=False, title="Claude is asking",
                       detail=_short(qs[0].get("question"), 160),
                       questions=[{"question": q["question"], "header": q.get("header") or "",
                                   "multiSelect": bool(q.get("multiSelect")),
                                   "options": [{"label": o["label"], "description": o.get("description") or ""}
                                               for o in q["options"]]} for q in qs],
                       actions=[])
        else:
            danger = is_dangerous(tool, inp, s.cwd)
            what = ticker(tool, inp)
            detail = str(inp.get("command") or inp.get("file_path") or inp.get("url") or target_of(tool, inp) or "")
            if tool == "ExitPlanMode":
                what, detail = "Start on the plan?", _short(inp.get("plan"), 300)
            actions = [{"id": "allow", "label": "Allow", "primary": not danger}, {"id": "deny", "label": "Deny"}]
            if payload.get("permission_suggestions"):
                actions.append({"id": "always", "label": "Always"})
            req.update(kind="permission", dangerous=danger, title=what, detail=_short(detail, 300), actions=actions)
        ap = Pending(id=req["id"], session=s.id, tool=tool, target=target_of(tool, inp), req=req,
                     dangerous=bool(req.get("dangerous")))
        with self.lock:
            if len(self.pending) >= MAX_PENDING:
                ap = None
            else:
                self.pending[req["id"]] = ap
                s.status, s.activity = "waiting", "needs you · " + (req["title"] or tool)
        if ap is None:
            reply({"reply": "pass"})
            return
        self._push()
        self.on_approval(req)
        if not ap.acked.wait(ACK_S):                       # the island never showed it
            self._settle(ap, {"reply": "pass"})
            reply({"reply": "pass"})
            return
        deadline = self.clock() + self.timeout_s
        while not ap.done.wait(0.1):
            if conn is not None and conn.closed():          # answered in the terminal: the relay is gone
                self._settle(ap, None)
                return
            if self.clock() >= deadline:
                self._settle(ap, {"reply": "pass"})
                break
            if self._stop.is_set():
                self._settle(ap, {"reply": "pass"})
                break
        if ap.result is not None:
            reply(ap.result)

    def _settle(self, ap: Pending, result: dict | None) -> bool:
        """Finish a pending approval (once). The card goes away either way."""
        with self.lock:
            if self.pending.pop(ap.id, None) is None:
                return False
            ap.result = result
            s = self.sessions.get(ap.session)
            if s is not None and s.status == "waiting" and not any(p.session == s.id for p in self.pending.values()):
                s.status = "working"
                s.activity = ap.req.get("title") or s.activity
        ap.done.set()
        self.on_withdraw(ap.id)
        self._push()
        return True

    def _settle_session(self, sid: str, why: str):
        for ap in [p for p in list(self.pending.values()) if p.session == sid]:
            self._settle(ap, {"reply": "pass"})

    def _settle_tool(self, sid: str, tool: str, target: str):
        for ap in [p for p in list(self.pending.values()) if p.session == sid and p.tool == tool and p.target == target]:
            self._settle(ap, {"reply": "pass"})

    def acknowledge(self, ap_id: str):
        ap = self.pending.get(ap_id)
        if ap is not None:
            ap.acked.set()

    def decide(self, ap_id: str, action: str, *, by_voice: bool = False) -> bool:
        """allow | deny | always | pass for a pending request. Voice can't
        approve anything dangerous (returns False)."""
        ap = self.pending.get(ap_id)
        if ap is None:
            return False
        if by_voice and ap.dangerous and action in ("allow", "always"):
            return False
        if ap.req.get("kind") == "question" and action in ("allow", "always"):
            return False                                    # a question needs its answers
        if action == "always" and not any(a.get("id") == "always" for a in ap.req.get("actions") or []):
            action = "allow"
        result = {"allow": {"reply": "allow"}, "always": {"reply": "always"},
                  "deny": {"reply": "deny", "message": DENY_MESSAGE}}.get(action, {"reply": "pass"})
        return self._settle(ap, result)

    def answer(self, ap_id: str, answers: dict) -> bool:
        ap = self.pending.get(ap_id)
        if ap is None or ap.req.get("kind") != "question" or not isinstance(answers, dict):
            return False
        return self._settle(ap, {"reply": "answers", "answers": answers})

    def voice_applies(self, text: str) -> bool:
        """Whether a spoken line answers the oldest pending card (no side effects)."""
        with self.lock:
            pend = sorted(self.pending.values(), key=lambda p: p.req.get("expires", 0))
        if not pend:
            return False
        ap = pend[0]
        if ap.req.get("kind") == "question":
            return bool(voice_option(text, ap.req.get("questions") or [])) or voice_decision(text) in ("pass", "deny")
        return voice_decision(text) is not None

    def voice(self, text: str) -> tuple[bool, str]:
        """A spoken reply while a card is up. (handled, what to say)."""
        with self.lock:
            pend = sorted(self.pending.values(), key=lambda p: p.req.get("expires", 0))
        if not pend:
            return False, ""
        ap = pend[0]
        if ap.req.get("kind") == "question":
            ans = voice_option(text, ap.req.get("questions") or [])
            if ans:
                self.answer(ap.id, ans)
                return True, f"Okay, {list(ans.values())[0]}."
            d = voice_decision(text)
            if d in ("pass", "deny"):
                self.decide(ap.id, "pass")
                return True, "Okay, answer it in the terminal."
            return False, ""
        d = voice_decision(text)
        if d is None:
            return False, ""
        if d in ("allow", "always") and ap.dangerous:
            return True, "That one needs a click on the island."
        self.decide(ap.id, d, by_voice=True)
        return True, {"allow": "Allowed.", "always": "Allowed, and it won't ask again.", "deny": "Denied.",
                      "pass": "Okay, answer it in the terminal."}[d]

    # -- handoff --------------------------------------------------------------------
    def request_handoff(self, sid: str) -> str:
        """"now" (between turns: hand it over), "after_turn" (queued until Stop), or "unknown"."""
        s = self.sessions.get(sid)
        if s is None:
            return "unknown"
        if s.desktop:
            return "already"
        if s.status in ("working", "waiting"):
            s.handoff_after_turn = True
            return "after_turn"
        return "now"


# ----------------------------------------------------------------- spoken session commands
_SESSION_WORDS = r"(?:session|claude\s+code|terminal|task|it|that|this|that\s+one|this\s+one)"
_DESKTOP_RE = re.compile(r"\b(?:move|hand|send|put|open|transfer|continue|take)\b(?P<ref>.*?)\b(?:to|in|into|over\s+to)\s+"
                         r"(?:the\s+)?(?:claude\s+)?desktop(?:\s+app)?\b", re.I)
_JUMP_RE = re.compile(r"^\s*(?:please\s+)?(?:jump|go|switch|take\s+me|bring\s+me|show\s+me|open)\s+(?:back\s+)?(?:to\s+)?"
                      r"(?P<ref>.*?)\b(?:session|claude\s+code(?:\s+session)?|terminal(?:\s+window)?)\s*(?:window)?\s*[.!?]?\s*$", re.I)
_DIFF_RE = re.compile(r"\b(?:show|open|let\s+me\s+see|see)\b(?P<ref>.*?)\b(?:diff|diffs|changes)\b"
                      r"|\bwhat\s+did\s+(?P<ref2>.*?)\s*change\b", re.I)


def session_command(text: str) -> tuple[str, str] | None:
    """("desktop" | "jump" | "diff", reference) for a spoken session command."""
    t = (text or "").strip()
    m = _DESKTOP_RE.search(t)
    if m and re.search(_SESSION_WORDS, m.group("ref") or "it", re.I):
        return ("desktop", (m.group("ref") or "").strip())
    m = _DIFF_RE.search(t)
    if m:
        return ("diff", t)
    m = _JUMP_RE.match(t)
    if m:
        return ("jump", (m.group("ref") or "").strip())
    return None
