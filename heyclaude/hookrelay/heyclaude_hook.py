"""hey-claude's Claude Code hook relay (M7).

Claude Code runs this as the hook command for every event hey-claude listens to:

    "<base python.exe>" -I -S "<%LOCALAPPDATA%/hey-claude/hook/heyclaude-hook.py>" <EventName>

with the hook JSON on stdin. It forwards one sanitized JSON line to hey-claude's
named pipe (``\\\\.\\pipe\\hey-claude-<user SID>``) and, for PermissionRequest only,
waits for the app's one-line reply and turns it into Claude Code's
``hookSpecificOutput`` JSON on stdout. This file is the only place that builds that
JSON.

Rules (docs/m7-hooks-notes.md "Relay rules"):
- STDLIB ONLY and self-contained: it is copied out of the package and run with
  ``-I -S``; it must never import heyclaude.
- Exit 0 and print nothing on ANY failure (app not running, 300 ms connect
  timeout, garbage stdin, a pipe server owned by another user, a bad reply, any
  exception). Nothing is ever printed that allows a tool unless the app replied.
- Every blocking call runs on a worker thread under a deadline: 2 s in total for
  fire-and-forget events, 110 s for PermissionRequest (app 108 s < relay 110 s <
  hook 120 s). The relay keeps the pipe open until the app closes it (EOF), so
  the app can look at the relay's parent chain while it handles the event.
- The parent process (the shell Claude Code started) is watched every 100 ms;
  when it dies (the user answered in the terminal) the relay exits at once, so
  the app's pipe read sees EOF.
- ``HEYCLAUDE_INTERNAL`` set: exit 0 at once (hey-claude's own claude runs).
- ``HEYCLAUDE_PIPE``: overrides the pipe name, for tests. Only names starting
  with ``\\\\.\\pipe\\hey-claude-`` are accepted.

Design taken from Coucou's hook (github.com/Louis-CFM/coucou, MIT); no code copied.
"""
import json
import os
import sys
import threading
import time

PIPE_PREFIX = "\\\\.\\pipe\\hey-claude-"
STR_CAP = 2048                  # characters, for every string in the payload
EDIT_FIELD_CAP = 256 * 1024     # UTF-8 bytes per edit-text field (PostToolUse Edit/MultiEdit/Write)
EDIT_TOTAL_CAP = 512 * 1024     # UTF-8 bytes of edit text in total
LINE_CAP = 1_400_000            # stay under the server's ~1.5 MB line cap
REPLY_CAP = 256 * 1024
MAX_DEPTH = 32
CONNECT_TIMEOUT = 0.3
EVENT_DEADLINE = 2.0
DECISION_DEADLINE = 110.0
EDIT_TOOLS = ("Edit", "MultiEdit", "Write")
EDIT_KEYS = ("old_string", "new_string", "content")
DENY_MESSAGE = "Denied from hey-claude"
_ENV = (("entrypoint", "CLAUDE_CODE_ENTRYPOINT"), ("wt_session", "WT_SESSION"),
        ("term_program", "TERM_PROGRAM"), ("vscode_pid", "VSCODE_PID"),
        ("heyclaude_task", "HEYCLAUDE_TASK"))
_KEEP_ON_SHRINK = ("session_id", "transcript_path", "cwd", "permission_mode", "hook_event_name",
                   "tool_name", "tool_use_id", "agent_type", "agent_id", "source", "reason",
                   "notification_type", "title", "message", "prompt", "last_assistant_message")


# --------------------------------------------------------------------------- sanitizing

def _cap_bytes(s, cap):
    """Cut s to at most cap UTF-8 bytes on a character boundary."""
    b = s.encode("utf-8", "surrogatepass")
    if len(b) <= cap:
        return s, len(b), False
    return b[:cap].decode("utf-8", "ignore"), cap, True


class _San:
    def __init__(self, edit_budget):
        self.truncated = False
        self.budget = edit_budget

    def edit_text(self, s):
        if not isinstance(s, str):
            return self.value(s, 1)
        cap = min(EDIT_FIELD_CAP, self.budget)
        out, used, cut = _cap_bytes(s, cap)
        self.budget -= used
        self.truncated |= cut
        return out

    def string(self, s):
        if len(s) > STR_CAP:
            self.truncated = True
            return s[:STR_CAP]
        return s

    def value(self, v, depth):
        if isinstance(v, str):
            return self.string(v)
        if v is None or isinstance(v, (bool, int, float)):
            return v
        if depth > MAX_DEPTH:
            self.truncated = True
            return None
        if isinstance(v, dict):
            return {self.string(str(k)): self.value(x, depth + 1) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            return [self.value(x, depth + 1) for x in v]
        self.truncated = True
        return None

    def edit_input(self, ti):
        """tool_input of a PostToolUse Edit/MultiEdit/Write: edit text kept long."""
        out = {}
        for k, v in ti.items():
            if k in EDIT_KEYS:
                out[self.string(str(k))] = self.edit_text(v)
            elif k == "edits" and isinstance(v, list):
                edits = []
                for e in v:
                    if isinstance(e, dict):
                        edits.append({self.string(str(ek)): (self.edit_text(ev) if ek in EDIT_KEYS
                                                             else self.value(ev, 3))
                                      for ek, ev in e.items()})
                    else:
                        edits.append(self.value(e, 3))
                out["edits"] = edits
            else:
                out[self.string(str(k))] = self.value(v, 2)
        return out


def sanitize(event, payload, keep_edits=True):
    """The payload as sent to the app, and whether anything was cut.

    Strings are capped at 2048 characters and ``tool_response`` is dropped. For
    PostToolUse of Edit/MultiEdit/Write the edit text (old_string, new_string,
    content, edits[*].old_string/new_string) is kept up to 256 KB per field and
    512 KB in total, so the app can show diffs."""
    if not isinstance(payload, dict):
        return {}, True
    san = _San(EDIT_TOTAL_CAP)
    edit = (keep_edits and event == "PostToolUse" and payload.get("tool_name") in EDIT_TOOLS
            and isinstance(payload.get("tool_input"), dict))
    out = {}
    for k, v in payload.items():
        if k == "tool_response":
            continue
        if edit and k == "tool_input":
            out["tool_input"] = san.edit_input(v)
        else:
            out[san.string(str(k))] = san.value(v, 1)
    return out, san.truncated


def env_context(environ=None):
    environ = os.environ if environ is None else environ
    out = {}
    for key, var in _ENV:
        v = environ.get(var)
        out[key] = v[:256] if isinstance(v, str) else None
    return out


def build_message(event, payload, environ=None):
    """The one line (bytes, '\\n'-terminated) the relay sends to the app."""
    def line(p, t):
        msg = {"v": 1, "event": event, "pid": os.getpid(), "ppid": os.getppid(),
               "env": env_context(environ), "payload": p, "truncated": t,
               "wants_reply": event == "PermissionRequest"}
        return json.dumps(msg, ensure_ascii=False, separators=(",", ":")).encode("utf-8", "replace") + b"\n"

    p, t = sanitize(event, payload)
    data = line(p, t)
    if len(data) > LINE_CAP:                       # many long strings: drop the edit text
        p, _ = sanitize(event, payload, keep_edits=False)
        data = line(p, True)
    if len(data) > LINE_CAP:                       # still huge: only the identifying fields
        small = {k: payload[k] for k in _KEEP_ON_SHRINK if k in payload}
        p, _ = sanitize(event, small, keep_edits=False)
        data = line(p, True)
    return data


# --------------------------------------------------------------------------- replies

def validate_answers(tool_input, answers):
    """AskUserQuestion answers from the app, checked against the original tool_input.

    Exactly one answer per question, keyed by the exact question text; a label of
    that question's options (a string), or for multiSelect a non-empty list of
    distinct valid labels. Returns Claude Code's ``answers`` mapping (question ->
    label; multiSelect labels joined with ", "), or None when anything is off."""
    if not isinstance(tool_input, dict) or not isinstance(answers, dict):
        return None
    questions = tool_input.get("questions")
    if not isinstance(questions, list) or not questions:
        return None
    out = {}
    for q in questions:
        if not isinstance(q, dict):
            return None
        text = q.get("question")
        options = q.get("options")
        if not isinstance(text, str) or not text or text in out:
            return None
        if not isinstance(options, list) or len(options) < 2:
            return None
        labels = []
        for o in options:
            if not isinstance(o, dict) or not isinstance(o.get("label"), str):
                return None
            labels.append(o["label"])
        if text not in answers:
            return None
        a = answers[text]
        if q.get("multiSelect") is True:
            if (not isinstance(a, list) or not a or len(set(map(repr, a))) != len(a)
                    or not all(isinstance(x, str) and x in labels for x in a)):
                return None
            out[text] = ", ".join(a)
        else:
            if not isinstance(a, str) or a not in labels:
                return None
            out[text] = a
    if set(answers) != set(out):                   # an answer to a question that wasn't asked
        return None
    return out


def build_output(payload, reply):
    """Claude Code's hookSpecificOutput for a PermissionRequest (UTF-8 bytes), or None
    (= print nothing; the terminal asks). ``payload`` is the ORIGINAL hook payload,
    ``reply`` the app's parsed reply line."""
    if not isinstance(payload, dict) or not isinstance(reply, dict):
        return None
    if payload.get("hook_event_name", "PermissionRequest") != "PermissionRequest":
        return None
    kind = reply.get("reply")
    tool = payload.get("tool_name")
    tool_input = payload.get("tool_input")
    if kind in ("allow", "always"):
        decision = {"behavior": "allow"}
        if kind == "always":
            sugg = payload.get("permission_suggestions")
            if isinstance(sugg, list) and sugg:
                decision["updatedPermissions"] = sugg
        if tool == "ExitPlanMode" and isinstance(tool_input, dict):
            decision["updatedInput"] = tool_input
    elif kind == "deny":
        msg = reply.get("message")
        msg = msg[:1000] if isinstance(msg, str) and msg.strip() else DENY_MESSAGE
        decision = {"behavior": "deny", "message": msg}
    elif kind == "answers":
        if tool != "AskUserQuestion":
            return None
        valid = validate_answers(tool_input, reply.get("answers"))
        if valid is None:
            return None
        decision = {"behavior": "allow",
                    "updatedInput": {"questions": tool_input["questions"], "answers": valid}}
    else:                                           # "pass" and anything unknown
        return None
    out = {"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": decision}}
    return json.dumps(out, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


# --------------------------------------------------------------------------- Win32 (lazy)

_W = None


def _win():
    """ctypes setup, done on first use (keeps startup fast)."""
    global _W
    if _W is not None:
        return _W
    import ctypes
    from ctypes import wintypes as wt

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    adv = ctypes.WinDLL("advapi32", use_last_error=True)

    class OVERLAPPED(ctypes.Structure):
        _fields_ = [("Internal", ctypes.c_void_p), ("InternalHigh", ctypes.c_void_p),
                    ("Offset", wt.DWORD), ("OffsetHigh", wt.DWORD), ("hEvent", wt.HANDLE)]

    def fn(dll, name, res, *args):
        f = getattr(dll, name)
        f.restype = res
        f.argtypes = list(args)
        return f

    H, B, D, P = wt.HANDLE, wt.BOOL, wt.DWORD, ctypes.c_void_p
    LPD = ctypes.POINTER(D)
    POV = ctypes.POINTER(OVERLAPPED)
    ns = type("W", (), {})()
    ns.ct = ctypes
    ns.OVERLAPPED = OVERLAPPED
    ns.CreateFileW = fn(k32, "CreateFileW", H, wt.LPCWSTR, D, D, P, D, D, H)
    ns.WaitNamedPipeW = fn(k32, "WaitNamedPipeW", B, wt.LPCWSTR, D)
    ns.CloseHandle = fn(k32, "CloseHandle", B, H)
    ns.ReadFile = fn(k32, "ReadFile", B, H, P, D, LPD, POV)
    ns.WriteFile = fn(k32, "WriteFile", B, H, P, D, LPD, POV)
    ns.GetOverlappedResult = fn(k32, "GetOverlappedResult", B, H, POV, LPD, B)
    ns.CancelIoEx = fn(k32, "CancelIoEx", B, H, POV)
    ns.CreateEventW = fn(k32, "CreateEventW", H, P, B, B, wt.LPCWSTR)
    ns.WaitForSingleObject = fn(k32, "WaitForSingleObject", D, H, D)
    ns.OpenProcess = fn(k32, "OpenProcess", H, D, B, D)
    ns.GetCurrentProcess = fn(k32, "GetCurrentProcess", H)
    ns.GetNamedPipeServerProcessId = fn(k32, "GetNamedPipeServerProcessId", B, H, ctypes.POINTER(wt.ULONG))
    ns.LocalFree = fn(k32, "LocalFree", P, P)
    ns.OpenProcessToken = fn(adv, "OpenProcessToken", B, H, D, ctypes.POINTER(H))
    ns.GetTokenInformation = fn(adv, "GetTokenInformation", B, H, ctypes.c_int, P, D, LPD)
    ns.ConvertSidToStringSidW = fn(adv, "ConvertSidToStringSidW", B, P, ctypes.POINTER(wt.LPWSTR))
    ns.INVALID = ctypes.c_void_p(-1).value
    _W = ns
    return ns


def _process_sid(hproc):
    """String SID of the user owning a process handle, or None."""
    w = _win()
    ct = w.ct
    tok = ct.c_void_p()
    if not w.OpenProcessToken(hproc, 0x0008, ct.byref(tok)):        # TOKEN_QUERY
        return None
    try:
        need = ct.c_ulong(0)
        w.GetTokenInformation(tok, 1, None, 0, ct.byref(need))       # TokenUser
        if not need.value:
            return None
        buf = ct.create_string_buffer(need.value)
        if not w.GetTokenInformation(tok, 1, buf, need.value, ct.byref(need)):
            return None
        psid = ct.c_void_p.from_buffer(buf).value                  # TOKEN_USER.User.Sid
        s = ct.c_wchar_p()
        if not w.ConvertSidToStringSidW(psid, ct.byref(s)):
            return None
        try:
            return s.value
        finally:
            w.LocalFree(s)
    finally:
        w.CloseHandle(tok)


_OWN_SID = []


def own_sid():
    if not _OWN_SID:
        _OWN_SID.append(_process_sid(_win().GetCurrentProcess()))
    return _OWN_SID[0]


def pid_sid(pid):
    w = _win()
    h = w.OpenProcess(0x1000, False, pid)                           # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return None
    try:
        return _process_sid(h)
    finally:
        w.CloseHandle(h)


def pipe_name():
    override = os.environ.get("HEYCLAUDE_PIPE")
    if override:
        return override if override.startswith(PIPE_PREFIX) else None
    sid = own_sid()
    return PIPE_PREFIX + sid if sid else None


class _Aborted(Exception):
    pass


class PipeClient:
    """An overlapped client end of the app's pipe; every wait is bounded and abortable."""

    def __init__(self, handle):
        self.h = handle
        self.abort = threading.Event()
        self._lock = threading.Lock()

    def _io(self, write, buf, n, deadline):
        w = _win()
        ct = w.ct
        ev = w.CreateEventW(None, True, False, None)
        if not ev:
            raise OSError("CreateEvent")
        ov = w.OVERLAPPED()
        ov.hEvent = ev
        got = ct.c_ulong(0)
        try:
            op = w.WriteFile if write else w.ReadFile
            ok = op(self.h, buf, n, None, ct.byref(ov))
            if not ok:
                err = ct.get_last_error()
                if err == 109:                                      # ERROR_BROKEN_PIPE
                    return 0
                if err != 997:                                      # ERROR_IO_PENDING
                    raise OSError(err)
            while True:
                if w.WaitForSingleObject(ev, 50) == 0:
                    break
                if self.abort.is_set() or time.monotonic() > deadline:
                    w.CancelIoEx(self.h, ct.byref(ov))
                    w.GetOverlappedResult(self.h, ct.byref(ov), ct.byref(got), True)
                    raise _Aborted()
            if not w.GetOverlappedResult(self.h, ct.byref(ov), ct.byref(got), False):
                err = ct.get_last_error()
                if err in (109, 233):                               # broken / not connected
                    return 0
                raise OSError(err)
            return got.value
        finally:
            w.CloseHandle(ev)

    def send(self, data, deadline):
        w = _win()
        buf = w.ct.create_string_buffer(data, len(data))
        off = 0
        while off < len(data):
            n = self._io(True, w.ct.byref(buf, off), len(data) - off, deadline)
            if n <= 0:
                raise OSError("pipe closed")
            off += n

    def read_line(self, deadline, cap=REPLY_CAP):
        """The app's reply line (without '\\n'); what arrived before EOF if it had
        no newline; b'' when it is over `cap`."""
        w = _win()
        buf = w.ct.create_string_buffer(65536)
        data = bytearray()
        while True:
            n = self._io(False, buf, 65536, deadline)
            if n <= 0:
                return bytes(data)
            data += buf.raw[:n]
            i = data.find(b"\n")
            if i >= 0:
                return bytes(data[:i])
            if len(data) > cap:
                return b""

    def wait_eof(self, deadline):
        """Read and discard until the app closes the pipe (or the deadline)."""
        w = _win()
        buf = w.ct.create_string_buffer(4096)
        while self._io(False, buf, 4096, deadline) > 0:
            pass

    def close(self):
        with self._lock:
            if self.h:
                _win().CloseHandle(self.h)
                self.h = None


def connect(name, timeout=CONNECT_TIMEOUT):
    """Open the app's pipe (waiting up to `timeout` while every instance is busy) and
    check that its server runs as our user. None when the app isn't there."""
    if not name:
        return None
    w = _win()
    ct = w.ct
    deadline = time.monotonic() + timeout
    while True:
        h = w.CreateFileW(name, 0xC0000000, 0, None, 3, 0x40000000, None)  # R|W, OPEN_EXISTING, OVERLAPPED
        if h and h != w.INVALID:
            break
        if ct.get_last_error() != 231:                               # ERROR_PIPE_BUSY
            return None
        left = deadline - time.monotonic()
        if left <= 0 or not w.WaitNamedPipeW(name, max(1, int(left * 1000))):
            return None
    client = PipeClient(h)
    try:
        spid = ct.c_ulong(0)
        mine = own_sid()
        if not (w.GetNamedPipeServerProcessId(h, ct.byref(spid)) and mine and pid_sid(spid.value) == mine):
            client.close()
            return None
    except Exception:
        client.close()
        return None
    return client


# --------------------------------------------------------------------------- main

def _parent_watch(ppid):
    """A function returning True once our parent process is gone (None if unknown)."""
    try:
        w = _win()
        h = w.OpenProcess(0x00100000, False, ppid)                  # SYNCHRONIZE
        if not h:
            return None
        return lambda: w.WaitForSingleObject(h, 0) == 0
    except Exception:
        return None


def _work(event, raw, connect_fn, deadline, state):
    """Worker thread: parse, send, (maybe) receive; leaves output bytes in state."""
    if raw[:3] == b"\xef\xbb\xbf":
        raw = raw[3:]
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        return
    data = build_message(event, payload)
    if state.get("stop"):
        return
    client = connect_fn(pipe_name(), CONNECT_TIMEOUT)
    if client is None:
        return
    state["client"] = client
    try:
        if state.get("stop"):
            return
        client.send(data, deadline)
        if event != "PermissionRequest":
            client.wait_eof(deadline)
            return
        line = client.read_line(deadline)
        try:
            reply = json.loads(line.decode("utf-8"))
        except Exception:
            return
        state["out"] = build_output(payload, reply)
    finally:
        client.close()


def main(argv=None, stdin_bytes=None, stdout=None, connect=None):
    """argv: the arguments after the script (argv[0] = the event name). Returns the exit
    code, always 0. stdin_bytes / stdout (a binary stream) / connect(name, timeout) can be
    injected for tests."""
    try:
        if os.environ.get("HEYCLAUDE_INTERNAL"):
            return 0
        argv = sys.argv[1:] if argv is None else argv
        event = argv[0] if argv else ""
        if not (isinstance(event, str) and 0 < len(event) <= 64 and event.isalnum()):
            return 0
        if sys.platform != "win32" and connect is None:
            return 0
        connect_fn = connect or globals()["connect"]
        limit = DECISION_DEADLINE if event == "PermissionRequest" else EVENT_DEADLINE
        deadline = time.monotonic() + limit
        state = {}

        def run():
            try:
                raw = stdin_bytes if stdin_bytes is not None else sys.stdin.buffer.read()
                _work(event, raw, connect_fn, deadline, state)
            except BaseException:
                state.pop("out", None)

        t = threading.Thread(target=run, name="heyclaude-hook", daemon=True)
        t.start()
        parent_gone = None
        checked = False
        while t.is_alive():
            t.join(0.1)
            if not t.is_alive():
                break
            if not checked:
                checked = True
                parent_gone = _parent_watch(os.getppid())
            if (parent_gone is not None and parent_gone()) or time.monotonic() > deadline:
                state["stop"] = True                     # give up: no output, close the pipe
                client = state.get("client")
                if client is not None:
                    client.abort.set()
                    t.join(0.3)
                    client.close()
                return 0
        out = state.get("out")
        if out:
            stream = stdout if stdout is not None else sys.stdout.buffer
            stream.write(out)
            stream.flush()
        return 0
    except BaseException:
        return 0


if __name__ == "__main__":
    code = 0
    try:
        code = main()
        sys.stdout.flush()
    except BaseException:
        pass
    os._exit(code)
