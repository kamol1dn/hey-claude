"""Offline tests for M7's hook plumbing: the relay Claude Code runs
(heyclaude/hookrelay/heyclaude_hook.py), the app's named-pipe server
(heyclaude/hookrelay/pipe.py) and the settings.json writer
(heyclaude/hookrelay/install.py). No Claude login, no real settings: every pipe
has a unique test name (the relay takes it from HEYCLAUDE_PIPE) and every
settings file lives in a temp folder.

    .venv\\Scripts\\python tests\\test_hooks.py
"""
import base64
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from heyclaude.hookrelay import heyclaude_hook as R  # noqa: E402
from heyclaude.hookrelay import install as I  # noqa: E402
from heyclaude.hookrelay import pipe as P  # noqa: E402

RELAY = ROOT / "heyclaude" / "hookrelay" / "heyclaude_hook.py"
WIN = sys.platform == "win32"
PYEXE = getattr(sys, "_base_executable", None) or sys.executable   # as installed: not the venv shim
ALLOW = b'{"hookSpecificOutput":{"hookEventName":"PermissionRequest","decision":{"behavior":"allow"}}}'


def _name():
    return R.PIPE_PREFIX + "test-" + uuid.uuid4().hex


@contextlib.contextmanager
def _tmp():
    d = Path(tempfile.mkdtemp(prefix="hc-hooks-"))
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


@contextlib.contextmanager
def _server(handler, name=None):
    name = name or _name()
    s = P.PipeServer(handler, name)
    assert s.start(), "pipe server did not start"
    try:
        yield s, name
    finally:
        s.stop()


def _env(name, **extra):
    env = {k: v for k, v in os.environ.items() if k not in ("HEYCLAUDE_INTERNAL", "HEYCLAUDE_PIPE")}
    env["HEYCLAUDE_PIPE"] = name
    env.update(extra)
    return env


def _relay_proc(event, payload, name, **env):
    """The relay as Claude Code runs it: python -I -S <file> <Event>, JSON on stdin."""
    data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    t = time.perf_counter()
    p = subprocess.run([PYEXE, "-I", "-S", str(RELAY), event], input=data,
                       capture_output=True, env=_env(name, **env), timeout=30)
    return p, time.perf_counter() - t


def _relay_inproc(event, payload, name, **env):
    out = io.BytesIO()
    data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    with mock.patch.dict(os.environ, _env(name, **env), clear=True):
        t = time.perf_counter()
        code = R.main([event], data, out)
        return code, out.getvalue(), time.perf_counter() - t


PERM = {"session_id": "s1", "transcript_path": "t.jsonl", "cwd": "C:/x", "permission_mode": "default",
        "hook_event_name": "PermissionRequest", "tool_name": "Bash", "tool_input": {"command": "rm -rf build"},
        "permission_suggestions": [{"type": "addRules", "rules": [{"toolName": "Bash", "ruleContent": "rm:*"}],
                                    "behavior": "allow", "destination": "localSettings"}]}
ASK = {"hook_event_name": "PermissionRequest", "tool_name": "AskUserQuestion",
       "tool_input": {"questions": [
           {"question": "Which database?", "header": "DB", "multiSelect": False,
            "options": [{"label": "Postgres", "description": ""}, {"label": "SQLite", "description": ""}]},
           {"question": "Which features?", "header": "Feat", "multiSelect": True,
            "options": [{"label": "Auth", "description": ""}, {"label": "Search", "description": ""},
                        {"label": "Billing", "description": ""}]}]}}


# =========================================================================== relay: pure parts

def test_sanitize_caps_strings_and_drops_tool_response():
    p = {"session_id": "s", "tool_name": "Bash", "tool_input": {"command": "x" * 5000, "n": 3, "ok": True},
         "tool_response": {"stdout": "y" * 10}, "list": ["z" * 3000, None, 1.5], "deep": {"a": {"b": "c"}}}
    out, cut = R.sanitize("PostToolUse", p)
    assert cut is True
    assert "tool_response" not in out
    assert out["tool_input"]["command"] == "x" * 2048 and out["tool_input"]["n"] == 3
    assert out["list"] == ["z" * 2048, None, 1.5] and out["deep"] == {"a": {"b": "c"}}
    out, cut = R.sanitize("PreToolUse", {"session_id": "s", "tool_input": {"command": "ls"}})
    assert cut is False and out == {"session_id": "s", "tool_input": {"command": "ls"}}
    assert R.sanitize("Stop", ["not", "a", "dict"]) == ({}, True)
    nested = cur = {}
    for _ in range(50):
        cur["k"] = {}
        cur = cur["k"]
    out, cut = R.sanitize("Stop", nested)
    assert cut is True


def test_sanitize_keeps_edit_text_for_diffs():
    big = "a" * (100 * 1024)
    out, cut = R.sanitize("PostToolUse", {"tool_name": "Edit", "tool_input": {
        "file_path": "f.py", "old_string": big, "new_string": big + "b", "replace_all": False}})
    assert cut is False and out["tool_input"]["old_string"] == big and out["tool_input"]["new_string"] == big + "b"
    # the same Edit before it ran (PreToolUse) gets the plain 2 KB cap
    out, cut = R.sanitize("PreToolUse", {"tool_name": "Edit", "tool_input": {"old_string": big}})
    assert cut is True and len(out["tool_input"]["old_string"]) == 2048
    # 256 KB per field
    huge = "q" * (300 * 1024)
    out, cut = R.sanitize("PostToolUse", {"tool_name": "Write", "tool_input": {"file_path": "x", "content": huge}})
    assert cut is True and len(out["tool_input"]["content"]) == 256 * 1024
    # 512 KB in total over MultiEdit's edits; a multi-byte character is never split
    field = "é" * (200 * 1024)                       # 400 KB of UTF-8
    out, cut = R.sanitize("PostToolUse", {"tool_name": "MultiEdit", "tool_input": {"file_path": "x", "edits": [
        {"old_string": field, "new_string": field, "replace_all": False}, {"old_string": "x", "new_string": "y"}]}})
    total = sum(len(e[k].encode()) for e in out["tool_input"]["edits"] for k in ("old_string", "new_string"))
    assert cut is True and total <= 512 * 1024
    e0 = out["tool_input"]["edits"][0]
    assert len(e0["old_string"].encode()) == 128 * 1024 * 2 and set(e0["new_string"]) <= {"é"}
    assert out["tool_input"]["edits"][0]["replace_all"] is False


def test_message_line_shape_and_size_cap():
    with mock.patch.dict(os.environ, {"CLAUDE_CODE_ENTRYPOINT": "claude-desktop", "WT_SESSION": "w1",
                                      "TERM_PROGRAM": "vscode", "VSCODE_PID": "42", "HEYCLAUDE_TASK": "t7"}):
        line = R.build_message("PermissionRequest", PERM)
    assert line.endswith(b"\n") and line.count(b"\n") == 1
    msg = json.loads(line)
    assert msg["v"] == 1 and msg["event"] == "PermissionRequest" and msg["wants_reply"] is True
    assert msg["pid"] == os.getpid() and msg["ppid"] == os.getppid() and msg["truncated"] is False
    assert msg["env"] == {"entrypoint": "claude-desktop", "wt_session": "w1", "term_program": "vscode",
                          "vscode_pid": "42", "heyclaude_task": "t7"}
    assert msg["payload"] == PERM
    # thousands of long strings still fit under the server's 1.5 MB line cap
    p = {"session_id": "s", "tool_name": "Edit", "cwd": "c",
         "tool_input": {"old_string": "o" * 300000, "new_string": "n" * 300000},
         "junk": ["j" * 2048] * 2000}
    line = R.build_message("PostToolUse", p)
    msg = json.loads(line)
    assert len(line) < P.LINE_CAP and msg["truncated"] is True and msg["payload"]["session_id"] == "s"
    assert json.loads(R.build_message("Stop", {"session_id": "s"}))["wants_reply"] is False


def test_build_output_allow_deny_always():
    assert R.build_output(PERM, {"reply": "allow"}) == ALLOW
    deny = json.loads(R.build_output(PERM, {"reply": "deny", "message": "Denied from hey-claude"}))
    assert deny == {"hookSpecificOutput": {"hookEventName": "PermissionRequest",
                                           "decision": {"behavior": "deny", "message": "Denied from hey-claude"}}}
    assert json.loads(R.build_output(PERM, {"reply": "deny"}))["hookSpecificOutput"]["decision"]["message"] \
        == "Denied from hey-claude"
    always = json.loads(R.build_output(PERM, {"reply": "always"}))["hookSpecificOutput"]["decision"]
    assert always == {"behavior": "allow", "updatedPermissions": PERM["permission_suggestions"]}
    no_sugg = dict(PERM, permission_suggestions=[])
    assert R.build_output(no_sugg, {"reply": "always"}) == ALLOW
    assert R.build_output({k: v for k, v in PERM.items() if k != "permission_suggestions"}, {"reply": "always"}) == ALLOW
    # pass / unknown / garbage: nothing
    for reply in ({"reply": "pass"}, {"reply": "yes"}, {}, ["allow"], "allow", None, {"reply": None}):
        assert R.build_output(PERM, reply) is None, reply
    assert R.build_output("nope", {"reply": "allow"}) is None
    assert R.build_output(dict(PERM, hook_event_name="PreToolUse"), {"reply": "allow"}) is None


def test_build_output_exit_plan_mode_keeps_input():
    plan = {"hook_event_name": "PermissionRequest", "tool_name": "ExitPlanMode",
            "tool_input": {"plan": "1. do it\n2. ship it"}}
    d = json.loads(R.build_output(plan, {"reply": "allow"}))["hookSpecificOutput"]["decision"]
    assert d == {"behavior": "allow", "updatedInput": {"plan": "1. do it\n2. ship it"}}
    d = json.loads(R.build_output(plan, {"reply": "always"}))["hookSpecificOutput"]["decision"]
    assert d["updatedInput"] == plan["tool_input"]


def test_answers_valid_and_invalid():
    good = {"Which database?": "SQLite", "Which features?": ["Auth", "Billing"]}
    d = json.loads(R.build_output(ASK, {"reply": "answers", "answers": good}))["hookSpecificOutput"]["decision"]
    assert d["behavior"] == "allow"
    assert d["updatedInput"]["questions"] == ASK["tool_input"]["questions"]
    assert d["updatedInput"]["answers"] == {"Which database?": "SQLite", "Which features?": "Auth, Billing"}
    bad = [
        {"Which database?": "MySQL", "Which features?": ["Auth"]},           # wrong label
        {"Which database?": "SQLite"},                                     # a question unanswered
        {"Which database?": "SQLite", "Which features?": "Auth"},           # multiSelect needs a list
        {"Which database?": ["SQLite"], "Which features?": ["Auth"]},       # single select needs a string
        {"Which database?": "SQLite", "Which features?": []},               # empty multiSelect
        {"Which database?": "SQLite", "Which features?": ["Auth", "Nope"]},  # one bad label
        {"Which database?": "SQLite", "Which features?": ["Auth", "Auth"]},  # duplicates
        {"Which database?": "SQLite", "Which features?": ["Auth"], "Extra?": "x"},  # not asked
        {"which database?": "SQLite", "Which features?": ["Auth"]},         # key must be exact
        ["SQLite", ["Auth"]],                                              # not a mapping
        None,
    ]
    for answers in bad:
        assert R.validate_answers(ASK["tool_input"], answers) is None, answers
        assert R.build_output(ASK, {"reply": "answers", "answers": answers}) is None, answers
    # answers for a tool that isn't AskUserQuestion, or questions that aren't well formed
    assert R.build_output(PERM, {"reply": "answers", "answers": good}) is None
    one_opt = {"questions": [{"question": "Q?", "options": [{"label": "A"}]}]}
    assert R.validate_answers(one_opt, {"Q?": "A"}) is None
    assert R.validate_answers({"questions": "nope"}, {}) is None
    assert R.validate_answers({"questions": []}, {}) is None


# =========================================================================== relay: main()

class _FakeClient:
    def __init__(self, reply=b""):
        self.sent = []
        self.reply = reply
        self.abort = threading.Event()
        self.closed = False

    def send(self, data, deadline):
        self.sent.append(data)

    def read_line(self, deadline):
        return self.reply

    def wait_eof(self, deadline):
        pass

    def close(self):
        self.closed = True


def test_main_strips_bom_and_survives_garbage():
    fake = _FakeClient()
    calls = []

    def connect(name, timeout):
        calls.append(name)
        return fake

    out = io.BytesIO()
    with mock.patch.dict(os.environ, _env(_name())):
        assert R.main(["Stop"], b"\xef\xbb\xbf" + json.dumps({"session_id": "é"}).encode(), out, connect) == 0
        assert json.loads(fake.sent[0])["payload"] == {"session_id": "é"} and fake.closed
        assert out.getvalue() == b""
        n = len(calls)
        for garbage in (b"", b"not json", b"\xff\xfe\x00", b"[1, 2]", b'"str"', b"{" * 100000):
            assert R.main(["Stop"], garbage, out, connect) == 0
        assert len(calls) == n and out.getvalue() == b""                 # never even connected
        for argv in ([], [""], ["Stop; rm"], ["x" * 100], [None]):
            assert R.main(argv, b"{}", out, connect) == 0
        assert len(calls) == n

        def boom(name, timeout):
            raise RuntimeError("boom")
        assert R.main(["PermissionRequest"], json.dumps(PERM).encode(), out, boom) == 0
        # a reply that isn't JSON, or a pass, prints nothing; an allow prints the exact JSON
        for reply, want in ((b"garbage", b""), (b'{"reply":"pass"}', b""), (b"", b""),
                            (b'{"reply":"allow"}', ALLOW)):
            out = io.BytesIO()
            assert R.main(["PermissionRequest"], json.dumps(PERM).encode(), out,
                          lambda n, t, r=reply: _FakeClient(r)) == 0
            assert out.getvalue() == want, reply


def test_internal_runs_never_connect():
    calls = []
    with mock.patch.dict(os.environ, {"HEYCLAUDE_INTERNAL": "1"}):
        out = io.BytesIO()
        assert R.main(["PermissionRequest"], json.dumps(PERM).encode(), out,
                      lambda n, t: calls.append(n) or _FakeClient(b'{"reply":"allow"}')) == 0
    assert calls == [] and out.getvalue() == b""


def test_pipe_override_must_be_ours():
    with mock.patch.dict(os.environ, {"HEYCLAUDE_PIPE": "\\\\.\\pipe\\someone-else"}):
        assert R.pipe_name() is None
    n = _name()
    with mock.patch.dict(os.environ, {"HEYCLAUDE_PIPE": n}):
        assert R.pipe_name() == n


def test_relay_without_server_is_silent_and_fast():
    if not WIN:
        return
    code, out, dt = _relay_inproc("PermissionRequest", PERM, _name())
    assert code == 0 and out == b"" and dt < 1.0, dt
    p, dt = _relay_proc("PermissionRequest", PERM, _name())
    assert p.returncode == 0 and p.stdout == b"" and p.stderr == b"" and dt < 1.0, (p, dt)
    p, dt = _relay_proc("Stop", b"\xef\xbb\xbfgarbage", _name())
    assert p.returncode == 0 and p.stdout == b"" and p.stderr == b""


# =========================================================================== relay <-> PipeServer

def test_round_trip_event_delivered_with_env_and_pid():
    if not WIN:
        return
    got = []

    def handler(msg, conn):
        time.sleep(0.3)                     # the app looks at the relay's parents meanwhile
        got.append((msg, conn.client_pid, conn.closed()))

    with _server(handler) as (_, name):
        p, dt = _relay_proc("PreToolUse", {"session_id": "s1", "tool_name": "Bash",
                                           "tool_input": {"command": "pytest -q"}, "tool_response": "x"},
                            name, CLAUDE_CODE_ENTRYPOINT="cli", WT_SESSION="abc", HEYCLAUDE_TASK="t1")
    assert p.returncode == 0 and p.stdout == b"" and p.stderr == b""
    assert len(got) == 1
    msg, pid, closed = got[0]
    assert dt >= 0.3, dt                    # the relay stayed until the handler returned
    assert closed is False                  # ... so it was still connected inside the handler
    assert msg["event"] == "PreToolUse" and msg["wants_reply"] is False and msg["v"] == 1
    assert msg["pid"] == pid and msg["ppid"] == os.getpid()
    assert msg["env"]["entrypoint"] == "cli" and msg["env"]["wt_session"] == "abc"
    assert msg["env"]["heyclaude_task"] == "t1"
    assert msg["payload"] == {"session_id": "s1", "tool_name": "Bash", "tool_input": {"command": "pytest -q"}}


def test_round_trip_permission_replies():
    if not WIN:
        return
    replies = []

    def handler(msg, conn):
        r = replies.pop(0)
        if r == "garbage":
            conn._lock.acquire()
            try:
                P._write_all(conn._h, b"\x00not json\n", 2.0)
            finally:
                conn._lock.release()
        elif r is not None:
            assert conn.reply(r)

    with _server(handler) as (_, name):
        replies.append({"reply": "allow"})
        p, _ = _relay_proc("PermissionRequest", PERM, name)
        assert p.returncode == 0 and p.stdout == ALLOW, p
        replies.append({"reply": "deny", "message": "Denied from hey-claude"})
        p, _ = _relay_proc("PermissionRequest", PERM, name)
        assert json.loads(p.stdout)["hookSpecificOutput"]["decision"]["behavior"] == "deny"
        replies.append({"reply": "answers", "answers": {"Which database?": "Postgres",
                                                        "Which features?": ["Search"]}})
        p, _ = _relay_proc("PermissionRequest", ASK, name)
        assert json.loads(p.stdout)["hookSpecificOutput"]["decision"]["updatedInput"]["answers"] \
            == {"Which database?": "Postgres", "Which features?": "Search"}
        for r in ("garbage", None, {"reply": "pass"}, {"reply": "answers", "answers": {"x": "y"}}):
            replies.append(r)
            p, dt = _relay_proc("PermissionRequest", ASK, name)
            assert p.returncode == 0 and p.stdout == b"" and p.stderr == b"", (r, p)
            assert dt < 5, dt
        # in-process too
        replies.append({"reply": "allow"})
        code, out, _ = _relay_inproc("PermissionRequest", PERM, name)
        assert code == 0 and out == ALLOW


def test_internal_env_never_reaches_the_server():
    if not WIN:
        return
    got = []
    with _server(lambda m, c: got.append(m)) as (_, name):
        p, _ = _relay_proc("PermissionRequest", PERM, name, HEYCLAUDE_INTERNAL="1")
        assert p.returncode == 0 and p.stdout == b""
        time.sleep(0.2)
    assert got == []


def test_fire_and_forget_deadline():
    if not WIN:
        return
    release = threading.Event()

    def handler(msg, conn):
        release.wait(6)

    with _server(handler) as (_, name):
        code, out, dt = _relay_inproc("Stop", {"session_id": "s"}, name)
        release.set()
    assert code == 0 and out == b"" and 1.9 <= dt < 2.6, dt


def test_closed_after_client_dies():
    if not WIN:
        return
    seen = {}
    inside = threading.Event()
    done = threading.Event()

    def handler(msg, conn):
        seen["before"] = conn.closed()
        inside.set()
        end = time.monotonic() + 10
        while not conn.closed() and time.monotonic() < end:
            time.sleep(0.05)
        seen["after"] = conn.closed()
        seen["reply"] = conn.reply({"reply": "allow"})
        done.set()

    with _server(handler) as (_, name):
        proc = subprocess.Popen([PYEXE, "-I", "-S", str(RELAY), "PermissionRequest"],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=_env(name))
        proc.stdin.write(json.dumps(PERM).encode())
        proc.stdin.close()
        assert inside.wait(10)
        proc.kill()
        proc.wait(10)
        assert done.wait(5)
        assert proc.stdout.read() == b""
        proc.stdout.close()
    assert seen == {"before": False, "after": True, "reply": False}


def test_relay_exits_when_its_parent_dies():
    """Claude Code kills the shell above the relay when the user answers in the
    terminal: the relay must exit at once so the app sees EOF."""
    if not WIN:
        return
    inside = threading.Event()
    gone = {}

    def handler(msg, conn):
        inside.set()
        t = time.monotonic()
        while not conn.closed() and time.monotonic() - t < 10:
            time.sleep(0.02)
        gone["after"] = time.monotonic()

    with _tmp() as d, _server(handler) as (_, name):
        payload = d / "perm.json"
        payload.write_text(json.dumps(PERM), encoding="utf-8")
        shell = ("import subprocess,sys; f=open(sys.argv[1],'rb'); "
                 "p=subprocess.Popen([sys.executable,'-I','-S',sys.argv[2],'PermissionRequest'],stdin=f); p.wait()")
        parent = subprocess.Popen([PYEXE, "-c", shell, str(payload), str(RELAY)], env=_env(name))
        assert inside.wait(10)
        killed = time.monotonic()
        parent.kill()
        parent.wait(10)
        end = time.monotonic() + 5
        while "after" not in gone and time.monotonic() < end:
            time.sleep(0.05)
    assert "after" in gone and gone["after"] - killed < 1.5, gone


# =========================================================================== PipeServer

def test_second_server_and_stop():
    if not WIN:
        return
    with _server(lambda m, c: None) as (_, name):
        assert P.PipeServer(lambda m, c: None, name).start() is False
    # stop() with a connect pending returns fast, and the name is free again
    s = P.PipeServer(lambda m, c: None, name)
    assert s.start()
    t = time.perf_counter()
    s.stop()
    assert time.perf_counter() - t < 0.5
    s2 = P.PipeServer(lambda m, c: None, name)
    assert s2.start()
    s2.stop()
    assert P.pipe_name() == "\\\\.\\pipe\\hey-claude-" + P.current_user_sid()
    assert P.current_user_sid().startswith("S-1-")


def test_server_drops_malformed_lines_and_keeps_serving():
    if not WIN:
        return
    got = []
    with _server(lambda m, c: got.append(m)) as (_, name):
        for raw in (b"not json\n", b"[1,2]\n", b'"x"\n', b"\xff\xfe\n", b"no newline"):
            c = R.connect(name, 1.0)
            assert c is not None
            c.send(raw, time.monotonic() + 2)
            c.close()
        assert P.send({"ok": 1}, name) is True
        r = P.send({"ok": 2}, name, wait_reply=True, timeout=2)        # handler doesn't reply: EOF
        assert r is None
        end = time.monotonic() + 3
        while len(got) < 2 and time.monotonic() < end:
            time.sleep(0.02)
    assert got == [{"ok": 1}, {"ok": 2}], got


def test_server_many_clients_at_once():
    if not WIN:
        return
    got = []
    lock = threading.Lock()

    def handler(msg, conn):
        with lock:
            got.append(msg["i"])
        conn.reply({"reply": "allow", "i": msg["i"]})

    results = {}
    with _server(handler) as (_, name):
        def one(i):
            results[i] = P.send({"i": i}, name, wait_reply=True, timeout=5)
        ts = [threading.Thread(target=one, args=(i,)) for i in range(12)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(10)
    assert sorted(got) == list(range(12))
    assert all(results[i] == {"reply": "allow", "i": i} for i in range(12)), results


# =========================================================================== settings writer

PY = "C:/Python312/python.exe"


def _relay(d):
    return (d / "relay" / "heyclaude-hook.py").as_posix()


def _ours_count(obj, relay):
    return sum(1 for groups in obj.get("hooks", {}).values() for g in groups for h in g["hooks"]
               if relay in h.get("command", ""))


USER = {
    "model": "opus",
    "permissions": {"allow": ["Bash(ls:*)"]},
    "hooks": {
        "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo user-pre"}]}],
        "Stop": [{"hooks": [{"type": "command", "command": "notify.exe done", "timeout": 5}]}],
        "PostCompact": [{"hooks": [{"type": "command", "command": "echo compacted"}]}],
    },
    "statusLine": {"type": "command", "command": "x"},
}


def test_hook_command_and_python():
    assert I.hook_command("C:\\P y\\python.exe", "C:\\a b\\heyclaude-hook.py", "PermissionRequest") \
        == '"C:/P y/python.exe" -I -S "C:/a b/heyclaude-hook.py" PermissionRequest'
    py = I.hook_python()
    assert "\\" not in py and Path(py).exists() and Path(py).name.lower() == "python.exe" or not WIN
    assert I.EVENTS["PermissionRequest"] == 120 and len(I.EVENTS) == 12
    assert all(t == 10 for e, t in I.EVENTS.items() if e != "PermissionRequest")
    with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": "D:/cfg"}):
        assert I.settings_path() == Path("D:/cfg/settings.json")
    with mock.patch.dict(os.environ, {"LOCALAPPDATA": "D:/lad"}):
        assert I.relay_dir() == Path("D:/lad/hey-claude/hook")


def test_install_merges_without_clobbering():
    with _tmp() as d:
        s = d / "settings.json"
        relay = _relay(d)
        user = json.loads(json.dumps(USER))
        # a user's hook sharing a group with an old entry of ours: the user's stays
        user["hooks"]["Notification"] = [{"matcher": "", "hooks": [
            {"type": "command", "command": "echo user-note"},
            {"type": "command", "command": f'"{PY}" -I -S "{relay}" Notification', "timeout": 99}]}]
        s.write_text(json.dumps(user, indent=2), encoding="utf-8")
        plan = I.plan_install(s, PY, relay)
        assert plan.action == "install" and plan.changed and plan.diff.startswith("--- ")
        assert '+        "matcher": "*"' in plan.diff
        backup = I.apply(plan, record_dir=d)
        obj = json.loads(s.read_text(encoding="utf-8"))
        assert list(obj) == ["model", "permissions", "hooks", "statusLine"]          # key order kept
        h = obj["hooks"]
        assert h["PreToolUse"][0] == user["hooks"]["PreToolUse"][0]
        assert h["PreToolUse"][1] == {"matcher": "*", "hooks": [{"type": "command",
                                      "command": f'"{PY}" -I -S "{relay}" PreToolUse', "timeout": 10}]}
        assert h["Stop"][0] == user["hooks"]["Stop"][0] and h["Stop"][1] == {"hooks": [
            {"type": "command", "command": f'"{PY}" -I -S "{relay}" Stop', "timeout": 10}]}
        assert h["PostCompact"] == user["hooks"]["PostCompact"]
        assert h["Notification"][0] == {"matcher": "", "hooks": [{"type": "command", "command": "echo user-note"}]}
        assert h["PermissionRequest"][-1]["hooks"][0]["timeout"] == 120
        assert h["PermissionRequest"][-1]["matcher"] == "*" and "matcher" not in h["SessionStart"][-1]
        assert _ours_count(obj, relay) == len(I.EVENTS)
        assert backup is not None and backup.read_bytes() == plan.before
        assert backup.name.startswith("settings.json.heyclaude-") and backup.name.endswith(".bak")
        st = I.status(s, relay)
        assert st["installed"] is True and set(st["events"]) == set(I.EVENTS) and st["relay"] == relay
        # idempotent: a second install changes nothing
        again = I.plan_install(s, PY, relay)
        assert not again.changed and again.diff == "" and I.apply(again, record_dir=d) is None
        # an entry of a different relay path is not ours
        other = json.loads(s.read_text(encoding="utf-8"))
        other["hooks"]["Stop"].append({"hooks": [{"type": "command",
                                                  "command": '"C:/else/heyclaude-hook.py" Stop'}]})
        s.write_text(json.dumps(other, indent=2), encoding="utf-8")
        I.apply(I.plan_uninstall(s, relay, record_dir=d), record_dir=d)
        after = json.loads(s.read_text(encoding="utf-8"))
        assert _ours_count(after, relay) == 0 and after["hooks"]["Stop"][-1]["hooks"][0]["command"].startswith('"C:/else')


def test_install_moves_stale_entries():
    with _tmp() as d:
        s = d / "settings.json"
        relay = _relay(d)
        I.apply(I.plan_install(s, PY, relay), record_dir=d)
        obj = json.loads(s.read_text(encoding="utf-8"))
        obj["hooks"]["Stop"][0]["hooks"][0]["timeout"] = 1                     # stale entry of ours
        obj["hooks"]["Stop"].append({"hooks": [{"type": "command", "command": "user"}]})
        s.write_text(json.dumps(obj, indent=2), encoding="utf-8")
        I.apply(I.plan_install(s, "C:/Other/python.exe", relay), record_dir=d)
        obj = json.loads(s.read_text(encoding="utf-8"))
        assert _ours_count(obj, relay) == len(I.EVENTS)
        assert obj["hooks"]["Stop"][0] == {"hooks": [{"type": "command", "command": "user"}]}
        assert obj["hooks"]["Stop"][1]["hooks"][0]["command"].startswith('"C:/Other/python.exe"')


def test_uninstall_restores_exact_bytes():
    variants = [
        b'{\n    "model":"opus",  "x": "\\u00e9t\\u00e9"\n}\n',                 # odd format, escapes
        b"\xef\xbb\xbf{\r\n  \"hooks\": {}\r\n}\r\n",                          # BOM + CRLF + empty hooks
        json.dumps(USER).encode(),                                            # one line, no newline
    ]
    for original in variants:
        with _tmp() as d:
            s = d / "settings.json"
            relay = _relay(d)
            s.write_bytes(original)
            I.apply(I.plan_install(s, PY, relay), record_dir=d)
            written = s.read_bytes()
            assert written != original and written.startswith(b"\xef\xbb\xbf") == original.startswith(b"\xef\xbb\xbf")
            assert (b"\r\n" in written) == (b"\r\n" in original)
            assert written.endswith(b"\n") == original.endswith(b"\n")
            assert (d / I.RECORD_NAME).exists()
            # re-install over our own install keeps the pre-hey-claude original
            I.apply(I.plan_install(s, "C:/New/python.exe", relay), record_dir=d)
            plan = I.plan_uninstall(s, relay, record_dir=d)
            assert plan.after == original and plan.changed and plan.diff
            backup = I.apply(plan, record_dir=d)
            assert s.read_bytes() == original, original
            assert backup is not None and not (d / I.RECORD_NAME).exists()
            assert I.status(s, relay)["installed"] is False


def test_uninstall_of_a_new_file_deletes_it():
    with _tmp() as d:
        s = d / "sub" / "settings.json"
        relay = _relay(d)
        plan = I.plan_install(s, PY, relay)
        assert plan.before is None and plan.fingerprint == "absent" and plan.diff.startswith("--- /dev/null")
        assert I.apply(plan, record_dir=d) is None                     # nothing to back up
        data = s.read_bytes()
        assert data.startswith(b'{\n  "hooks": {') and not data.endswith(b"\n")    # Claude Code's style
        plan = I.plan_uninstall(s, relay, record_dir=d)
        assert plan.delete and plan.changed
        I.apply(plan, record_dir=d)
        assert not s.exists()
        # uninstall with nothing there: a no-op
        plan = I.plan_uninstall(s, relay, record_dir=d)
        assert not plan.changed and I.apply(plan, record_dir=d) is None and not s.exists()


def test_uninstall_after_user_edit_removes_only_ours():
    with _tmp() as d:
        s = d / "settings.json"
        relay = _relay(d)
        s.write_text(json.dumps({"model": "opus"}, indent=2) + "\n", encoding="utf-8")
        I.apply(I.plan_install(s, PY, relay), record_dir=d)
        obj = json.loads(s.read_text(encoding="utf-8"))
        obj["theme"] = "dark"
        obj["hooks"]["Stop"][0]["hooks"].append({"type": "command", "command": "user-stop"})   # shares our group
        s.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")
        plan = I.plan_uninstall(s, relay, record_dir=d)
        assert not plan.delete and plan.after != plan.before
        I.apply(plan, record_dir=d)
        out = json.loads(s.read_text(encoding="utf-8"))
        assert out == {"model": "opus", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "user-stop"}]}]},
                       "theme": "dark"}
        assert s.read_text(encoding="utf-8").endswith("}\n")
        # when the user's hooks go too and the file had no hooks before, the key goes
        obj = json.loads(s.read_text(encoding="utf-8"))
        del obj["hooks"]
        s.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")
        I.apply(I.plan_install(s, PY, relay), record_dir=d)
        obj = json.loads(s.read_text(encoding="utf-8"))
        obj["theme"] = "light"
        s.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")
        I.apply(I.plan_uninstall(s, relay, record_dir=d), record_dir=d)
        assert json.loads(s.read_text(encoding="utf-8")) == {"model": "opus", "theme": "light"}


def test_refuses_changed_since_plan_bad_shape_and_bad_json():
    with _tmp() as d:
        s = d / "settings.json"
        relay = _relay(d)
        s.write_text('{"model": "opus"}', encoding="utf-8")
        plan = I.plan_install(s, PY, relay)
        s.write_text('{"model": "sonnet"}', encoding="utf-8")
        try:
            I.apply(plan, record_dir=d)
            raise AssertionError("applied a stale plan")
        except I.SettingsError:
            pass
        assert s.read_text(encoding="utf-8") == '{"model": "sonnet"}'
        assert not list(d.glob("*.bak"))
        # created meanwhile, when the plan saw no file
        s2 = d / "new.json"
        plan = I.plan_install(s2, PY, relay)
        s2.write_text("{}", encoding="utf-8")
        try:
            I.apply(plan, record_dir=d)
            raise AssertionError("applied a stale plan")
        except I.SettingsError:
            pass
        bad = [
            '{"hooks": []}', '{"hooks": {"Stop": {}}}', '{"hooks": {"Stop": ["x"]}}',
            '{"hooks": {"Stop": [{"matcher": 1, "hooks": []}]}}', '{"hooks": {"Stop": [{"command": "x"}]}}',
            '{"hooks": {"Stop": [{"hooks": [{"command": "x"}]}]}}',
            '{"hooks": {"Stop": [{"hooks": [{"type": "command"}]}]}}',
            '{"hooks": {"Stop": [{"hooks": ["echo"]}]}}',
            "not json", "", "[1]", '{"a": NaN}', '{"a": 1, "a": 2}', '{"a": 1,}', "// c\n{}",
        ]
        for text in bad:
            s.write_text(text, encoding="utf-8")
            for fn in (lambda: I.plan_install(s, PY, relay), lambda: I.plan_uninstall(s, relay, record_dir=d)):
                try:
                    fn()
                    raise AssertionError(f"accepted {text!r}")
                except I.SettingsError:
                    pass
            assert s.read_text(encoding="utf-8") == text
        s.write_bytes(b'{"a": "\xff"}')
        try:
            I.plan_install(s, PY, relay)
            raise AssertionError("accepted non-UTF-8")
        except I.SettingsError:
            pass
        assert "error" in I.status(s, relay) and I.status(s, relay)["installed"] is False


def test_diff_is_never_empty_for_a_change():
    # the Settings tab treats an empty diff as "nothing to change"
    for before, after in ((b"\xef\xbb\xbf{}", b"{}"), (b"{}\r\n", b"{}\n"), (b"{}", b"{}\n")):
        assert I._diff("s.json", before, after), (before, after)
    assert I._diff("s.json", b"{}", b"{}") == ""


def test_backup_names_never_collide():
    with _tmp() as d:
        s = d / "settings.json"
        s.write_text("{}", encoding="utf-8")
        with mock.patch.object(I.time, "strftime", return_value="20261110-120000"):
            b1 = I._backup(s, b"one")
            b2 = I._backup(s, b"two")
        assert b1.name == "settings.json.heyclaude-20261110-120000.bak"
        assert b2.name == "settings.json.heyclaude-20261110-120000-1.bak"
        assert b1.read_bytes() == b"one" and b2.read_bytes() == b"two"


def test_symlinked_settings_are_followed():
    with _tmp() as d:
        real = d / "dotfiles" / "settings.json"
        real.parent.mkdir()
        real.write_text('{"model": "opus"}\n', encoding="utf-8")
        link = d / "settings.json"
        try:
            os.symlink(real, link)
        except (OSError, NotImplementedError):
            print("  (skipped: can't create symlinks here)")
            return
        relay = _relay(d)
        backup = I.apply(I.plan_install(link, PY, relay), record_dir=d)
        assert link.is_symlink()
        assert _ours_count(json.loads(real.read_text(encoding="utf-8")), relay) == len(I.EVENTS)
        assert backup.parent.samefile(real.parent)  # CI temp paths can be 8.3 short names; realpath expands them
        I.apply(I.plan_uninstall(link, relay, record_dir=d), record_dir=d)
        assert link.is_symlink() and real.read_text(encoding="utf-8") == '{"model": "opus"}\n'


def test_deploy_relay_by_hash():
    with _tmp() as d:
        dest = I.deploy_relay(d)
        assert dest == d / "heyclaude-hook.py" and dest.read_bytes() == RELAY.read_bytes()
        assert I.relay_current(dest) and I.status(d / "none.json", dest.as_posix())["relay_current"]
        os.utime(dest, (1, 1))
        I.deploy_relay(d)
        assert dest.stat().st_mtime == 1                                  # same hash: untouched
        data = bytearray(dest.read_bytes())
        data[-2:-1] = b"#" if data[-2:-1] != b"#" else b"!"                 # same size, other bytes
        dest.write_bytes(bytes(data))
        os.utime(dest, (1, 1))
        assert not I.relay_current(dest)
        I.deploy_relay(d)
        assert dest.read_bytes() == RELAY.read_bytes() and I.relay_current(dest)
        assert not list(d.glob("*.tmp"))


def test_record_is_base64_of_before():
    with _tmp() as d:
        s = d / "settings.json"
        s.write_bytes(b'{"a": 1}')
        I.apply(I.plan_install(s, PY, _relay(d)), record_dir=d)
        rec = json.loads((d / I.RECORD_NAME).read_text(encoding="utf-8"))
        (entry,) = rec["entries"].values()
        assert base64.b64decode(entry["before"]) == b'{"a": 1}'
        assert entry["after_sha256"] == I._sha(s.read_bytes()) and entry["had_hooks"] is False


if __name__ == "__main__":
    n = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            n += 1
            print("ok", name)
    print(n, "passed")
