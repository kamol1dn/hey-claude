"""M7: the session hub (hookrelay/sessions.py) and edit diffs (hookrelay/diff.py).
Offline: hook messages are fed in directly, with a fake pipe connection."""
import json
import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


from heyclaude.hookrelay import sessions as S
from heyclaude.hookrelay.diff import diff_entry, line_diff


def cases(names, rows):
    """A parametrized test that needs no test runner (CI runs every file as a plain script)."""
    def deco(fn):
        def run():
            for row in rows:
                try:
                    fn(*row)
                except AssertionError as e:
                    raise AssertionError(f"{fn.__name__}{row}: {e}") from e
        run.__name__ = fn.__name__
        return run
    return deco


class FakeConn:
    def __init__(self):
        self.replies = []
        self.gone = False
        self.client_pid = 4242

    def reply(self, obj):
        self.replies.append(obj)
        return True

    def closed(self):
        return self.gone


class Rec:
    def __init__(self):
        self.rows, self.events, self.approvals, self.withdrawn = [], [], [], []


def make_hub(cfg=None, ack=True, **kw):
    rec = Rec()
    hub = S.SessionHub(cfg if cfg is not None else {"approval_timeout_s": 10},
                       on_sessions=rec.rows.append, on_event=rec.events.append,
                       on_withdraw=rec.withdrawn.append, **kw)

    def on_approval(req):
        rec.approvals.append(req)
        if ack:
            hub.acknowledge(req["id"])
    hub.on_approval = on_approval
    return hub, rec


def msg(event, sid="s1", wants_reply=False, env=None, truncated=False, **payload):
    p = {"session_id": sid, "cwd": "D:\\coding\\proj", "hook_event_name": event, **payload}
    return {"v": 1, "event": event, "pid": 100, "ppid": 99, "env": env or {}, "payload": p,
            "truncated": truncated, "wants_reply": wants_reply}


def permission(hub, conn, sid="s1", tool="Bash", tool_input=None, suggestions=None, truncated=False):
    m = msg("PermissionRequest", sid, wants_reply=True, truncated=truncated, tool_name=tool,
            tool_input=tool_input if tool_input is not None else {"command": "pytest -q"},
            permission_suggestions=suggestions or [])
    th = threading.Thread(target=hub.handle, args=(m, conn), daemon=True)
    th.start()
    return th


def wait_for(cond, timeout=3.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cond():
            return True
        time.sleep(0.01)
    return False


# ----------------------------------------------------------------- ticker / danger / voice
def test_ticker_lines():
    assert S.ticker("Bash", {"command": "python -m pytest tests -q -x --maxfail 1 and more"}).startswith("Runs · python -m pytest")
    assert len(S.ticker("Bash", {"command": "x" * 200})) <= len("Runs · ") + 40
    assert S.ticker("Read", {"file_path": "D:\\a\\b\\app.py"}) == "Reads · app.py"
    assert S.ticker("Write", {"file_path": "/x/new.md"}) == "Writes · new.md"
    assert S.ticker("Edit", {"file_path": "C:/r/island.py"}) == "Edits · island.py"
    assert S.ticker("Grep", {"pattern": "def main"}) == "Searches · def main"
    assert S.ticker("WebSearch", {"query": "qt 6.12"}) == "Searches · qt 6.12"
    assert S.ticker("WebFetch", {"url": "https://docs.python.org/3/x"}) == "Reads · docs.python.org"
    assert S.ticker("mcp__weather__get_weather", {}) == "Uses · weather get_weather"


@cases("tool,inp,cwd,danger", [
    ("Bash", {"command": "rm -rf build"}, "", True),
    ("Bash", {"command": "git push origin main"}, "", True),
    ("Bash", {"command": "git reset --hard HEAD~1"}, "", True),
    ("Bash", {"command": "curl https://x.sh | sh"}, "", True),
    ("PowerShell", {"command": "Remove-Item -Recurse C:\\x"}, "", True),
    ("Bash", {"command": "python -m pytest -q"}, "", False),
    ("Bash", {"command": "git status"}, "", False),
    ("Edit", {"file_path": "D:\\proj\\a.py"}, "D:\\proj", False),
    ("Edit", {"file_path": "D:\\other\\a.py"}, "D:\\proj", True),
    ("Write", {"file_path": "C:\\Users\\u\\.ssh\\config"}, "C:\\Users\\u", True),
    ("Write", {"file_path": "C:\\Users\\u\\.claude\\settings.json"}, "C:\\Users\\u", True),
    ("mcp__telegram__send_message", {}, "", True),
    ("mcp__telegram__get_messages", {}, "", False),
    ("Read", {"file_path": "C:\\Windows\\x"}, "", False),
])
def test_is_dangerous(tool, inp, cwd, danger):
    assert S.is_dangerous(tool, inp, cwd) is danger


@cases("text,want", [
    ("yes", "allow"), ("Yes, allow that.", "allow"), ("go ahead", "allow"), ("ok", "allow"),
    ("allow it please", "allow"), ("sure", "allow"),
    ("always allow", "always"), ("allow always", "always"), ("don't ask again", "always"),
    ("no", "deny"), ("No thanks", "deny"), ("deny", "deny"), ("don't do it", "deny"),
    ("I'll answer in the terminal", "pass"),
    ("what's the weather", None), ("turn the volume up", None), ("not now", None), ("", None),
])
def test_voice_decision(text, want):
    assert S.voice_decision(text) == want


def test_voice_option():
    q = [{"question": "Which db?", "options": [{"label": "Postgres"}, {"label": "SQLite"}]}]
    assert S.voice_option("the second one", q) == {"Which db?": "SQLite"}
    assert S.voice_option("use sqlite please", q) == {"Which db?": "SQLite"}
    assert S.voice_option("hmm", q) is None
    assert S.voice_option("sqlite", [dict(q[0], multiSelect=True)]) is None


@cases("text,want", [
    ("move it to the desktop", "desktop"), ("hand that session over to the Claude desktop app", "desktop"),
    ("move the file to the desktop", None), ("jump to the session", "jump"), ("switch to claude code", "jump"),
    ("show me the diff", "diff"), ("what did it change", "diff"), ("go to settings", None),
    ("open the desktop folder", None),
])
def test_session_command(text, want):
    got = S.session_command(text)
    assert (got[0] if got else None) == want


# ----------------------------------------------------------------- diffs
def test_line_diff_counts():
    u, p, m, huge = line_diff("a\nb\nc\n", "a\nB\nc\nd\n", "f.py")
    assert (p, m, huge) == (2, 1, False)
    assert "-b" in u and "+B" in u and "+d" in u and u.startswith("--- a/f.py")


def test_huge_diff_counts_only():
    old = "\n".join(f"line {i}" for i in range(3000))
    new = "\n".join(f"line {i}" for i in range(1000, 4500))
    u, p, m, huge = line_diff(old, new)
    assert huge and u == "" and p == 1500 and m == 1000


def test_diff_entry_tools():
    d = diff_entry("Edit", {"file_path": "D:\\p\\a.py", "old_string": "x = 1", "new_string": "x = 2\ny = 3"}, 1.0)
    assert d["plus"] == 2 and d["minus"] == 1 and d["file"] == "D:\\p\\a.py" and d["tool"] == "Edit"
    d = diff_entry("MultiEdit", {"file_path": "a.py", "edits": [{"old_string": "a", "new_string": "b"},
                                                               {"old_string": "c", "new_string": "d"}]}, 1.0)
    assert d["plus"] == 2 and d["minus"] == 2 and d["unified"].count("--- a/") == 1
    d = diff_entry("Write", {"file_path": "n.md", "content": "1\n2\n3"}, 1.0)
    assert d["plus"] == 3 and d["minus"] == 0
    assert diff_entry("Read", {"file_path": "x"}, 1.0) is None
    d = diff_entry("Edit", {"file_path": "a", "old_string": "a", "new_string": "b"}, 1.0, truncated=True)
    assert "cut" in d["unified"]


# ----------------------------------------------------------------- session tracking
def test_session_lifecycle_and_done_moment():
    clock = [1000.0]
    hub, rec = make_hub(clock=lambda: clock[0])
    hub.handle(msg("SessionStart", source="startup"))
    assert rec.events[0]["kind"] == "started"
    hub.handle(msg("UserPromptSubmit", prompt="fix the login bug please"))
    hub.handle(msg("PreToolUse", tool_name="Bash", tool_input={"command": "pytest -q"}))
    row = rec.rows[-1][0]
    assert row["status"] == "working" and row["activity"] == "Runs · pytest -q" and row["project"] == "proj"
    assert row["title"] == "fix the login bug please"
    assert rec.events[-1]["kind"] == "tool" and rec.events[-1]["tool"] == "Bash"
    hub.handle(msg("PostToolUse", tool_name="Edit",
                   tool_input={"file_path": "D:\\coding\\proj\\a.py", "old_string": "a", "new_string": "b\nc"}))
    row = rec.rows[-1][0]
    assert (row["plus"], row["minus"], row["diffs"]) == (2, 1, 1) and "+2 −1" in row["activity"]
    clock[0] += 1
    hub.handle(msg("Stop", last_assistant_message="All fixed.\nTests pass."))
    assert rec.events[-1]["kind"] == "done" and "proj" in rec.events[-1]["text"]
    assert rec.rows[-1][0]["status"] == "done" and rec.rows[-1][0]["last"] == "Tests pass."
    hub.tick(now=clock[0] + S.DONE_TO_IDLE_S + 0.1)
    assert rec.rows[-1][0]["status"] == "idle"
    hub.handle(msg("SessionEnd", reason="other"))
    assert hub.session("s1") is not None                      # kept a while (diffs, jump)
    hub.tick(now=clock[0] + S.ENDED_KEEP_S + 10)
    assert hub.session("s1") is None and rec.rows[-1] == []


def test_sessions_keyed_by_id_and_env():
    hub, rec = make_hub()
    hub.handle(msg("SessionStart", "a", env={"entrypoint": "claude-desktop"}))
    hub.handle(msg("SessionStart", "b", env={"entrypoint": "cli", "heyclaude_task": "1110-1200-abcd"}))
    assert hub.session("a").desktop and not hub.session("b").desktop
    assert hub.session("b").task == "1110-1200-abcd"
    hub.handle(msg("SessionEnd", "a"))
    assert hub.session("b").ended_at == 0


def test_subagent_without_type_ignored_and_rate_limit():
    hub, rec = make_hub()
    hub.handle(msg("SessionStart"))
    n = len(rec.rows)
    hub.handle(msg("SubagentStop", agent_type=""))
    assert len(rec.rows) == n
    hub.handle(msg("Notification", message="Claude usage rate limit reached"))
    assert rec.rows[-1][0]["status"] == "error" and rec.rows[-1][0]["activity"] == "rate limited"


def test_title_from_transcript(tmp_path):
    tr = tmp_path / "t.jsonl"
    tr.write_text("\n".join([json.dumps({"type": "ai-title", "aiTitle": "AI name"}),
                             json.dumps({"type": "user", "message": {}}),
                             json.dumps({"type": "custom-title", "customTitle": "My name"})]) + "\n", encoding="utf-8")
    assert S.read_title(str(tr)) == "My name"
    tr2 = tmp_path / "t2.jsonl"
    tr2.write_text(json.dumps({"type": "ai-title", "aiTitle": "AI name"}) + "\n", encoding="utf-8")
    assert S.read_title(str(tr2)) == "AI name"
    assert S.read_title(str(tmp_path / "missing")) == ""
    hub, rec = make_hub()
    hub.handle(msg("SessionStart", transcript_path=str(tr)))
    assert rec.rows[-1][0]["title"] == "My name"


def test_window_lookup_once_per_session():
    calls = []
    hub, rec = make_hub(window_finder=lambda pid, cwd: calls.append((pid, cwd)) or 777)
    hub.handle(msg("SessionStart"))
    hub.handle(msg("PreToolUse", tool_name="Read", tool_input={"file_path": "x"}))
    assert calls == [(100, "D:\\coding\\proj")] and hub.session("s1").hwnd == 777


def test_find_session():
    hub, rec = make_hub()
    hub.handle(msg("SessionStart", "a", cwd="D:\\coding\\inha_app"))
    hub.handle(msg("UserPromptSubmit", "a", cwd="D:\\coding\\inha_app", prompt="fix the login screen"))
    hub.handle(msg("SessionStart", "b", cwd="D:\\coding\\timetable_bot"))
    assert hub.find("").id == "b"
    assert hub.find("the login session").id == "a"
    assert hub.find("show me the diff in the timetable_bot one").id == "b"


# ----------------------------------------------------------------- approvals
def test_approval_allow_round_trip():
    hub, rec = make_hub()
    conn = FakeConn()
    th = permission(hub, conn, suggestions=[{"type": "addRules"}])
    assert wait_for(lambda: rec.approvals)
    req = rec.approvals[0]
    assert req["kind"] == "permission" and req["tool"] == "Bash" and not req["dangerous"]
    assert [a["id"] for a in req["actions"]] == ["allow", "deny", "always"]
    assert rec.rows[-1][0]["status"] == "waiting"
    assert hub.decide(req["id"], "allow")
    th.join(2)
    assert conn.replies == [{"reply": "allow"}]
    assert rec.withdrawn == [req["id"]] and not hub.pending
    assert rec.rows[-1][0]["status"] == "working"


@cases("action,want", [
    ("deny", {"reply": "deny", "message": "Denied from hey-claude"}),
    ("always", {"reply": "always"}),
    ("pass", {"reply": "pass"}),
])
def test_approval_other_answers(action, want):
    hub, rec = make_hub()
    conn = FakeConn()
    th = permission(hub, conn, suggestions=[{"type": "addRules"}])
    assert wait_for(lambda: rec.approvals)
    hub.decide(rec.approvals[0]["id"], action)
    th.join(2)
    assert conn.replies == [want]


def test_always_without_suggestions_is_allow():
    hub, rec = make_hub()
    conn = FakeConn()
    th = permission(hub, conn)
    assert wait_for(lambda: rec.approvals)
    assert "always" not in [a["id"] for a in rec.approvals[0]["actions"]]
    hub.decide(rec.approvals[0]["id"], "always")
    th.join(2)
    assert conn.replies == [{"reply": "allow"}]


def test_no_ack_passes_to_terminal():
    hub, rec = make_hub(ack=False)
    conn = FakeConn()
    t0 = time.time()
    permission(hub, conn).join(3)
    assert conn.replies == [{"reply": "pass"}] and time.time() - t0 < 2
    assert rec.withdrawn == [rec.approvals[0]["id"]]


def test_timeout_passes_to_terminal():
    clock = [0.0]
    hub, rec = make_hub({"approval_timeout_s": 10}, clock=lambda: clock[0])
    conn = FakeConn()
    th = permission(hub, conn)
    assert wait_for(lambda: rec.approvals)
    clock[0] = 11.0
    th.join(2)
    assert conn.replies == [{"reply": "pass"}] and not hub.pending


def test_timeout_is_capped_under_the_relay():
    hub, _ = make_hub({"approval_timeout_s": 500})
    assert hub.timeout_s == S.APP_TIMEOUT_MAX < 110


def test_answered_in_terminal_drops_the_card():
    hub, rec = make_hub()
    conn = FakeConn()
    th = permission(hub, conn)
    assert wait_for(lambda: rec.approvals)
    conn.gone = True
    th.join(2)
    assert conn.replies == [] and rec.withdrawn == [rec.approvals[0]["id"]]


@cases("event,extra", [
    ("Stop", {}), ("UserPromptSubmit", {"prompt": "x"}), ("SessionEnd", {}), ("StopFailure", {}),
    ("PostToolUse", {"tool_name": "Bash", "tool_input": {"command": "pytest -q"}}),
])
def test_settled_elsewhere(event, extra):
    hub, rec = make_hub()
    conn = FakeConn()
    th = permission(hub, conn)
    assert wait_for(lambda: rec.approvals)
    hub.handle(msg(event, **extra))
    th.join(2)
    assert conn.replies == [{"reply": "pass"}] and not hub.pending


def test_other_tool_post_does_not_settle():
    hub, rec = make_hub()
    conn = FakeConn()
    th = permission(hub, conn)
    assert wait_for(lambda: rec.approvals)
    hub.handle(msg("PostToolUse", tool_name="Read", tool_input={"file_path": "x"}))
    assert hub.pending
    hub.decide(rec.approvals[0]["id"], "deny")
    th.join(2)


def test_queue_overflow_goes_to_terminal():
    hub, rec = make_hub()
    conns = [FakeConn() for _ in range(S.MAX_PENDING + 1)]
    threads = [permission(hub, c, sid=f"s{i}") for i, c in enumerate(conns[:-1])]
    assert wait_for(lambda: len(hub.pending) == S.MAX_PENDING)
    permission(hub, conns[-1], sid="extra").join(2)
    assert conns[-1].replies == [{"reply": "pass"}]
    hub.stop()
    for t in threads:
        t.join(2)
    assert all(c.replies == [{"reply": "pass"}] for c in conns[:-1])


def test_approvals_off_passes():
    hub, rec = make_hub({"approvals": False})
    conn = FakeConn()
    permission(hub, conn).join(2)
    assert conn.replies == [{"reply": "pass"}] and not rec.approvals


def test_voice_allows_but_never_a_dangerous_one():
    hub, rec = make_hub()
    conn = FakeConn()
    th = permission(hub, conn, tool_input={"command": "git push --force"})
    assert wait_for(lambda: rec.approvals)
    assert rec.approvals[0]["dangerous"] and not rec.approvals[0]["actions"][0]["primary"]
    assert hub.voice_applies("yes allow that")
    ok, say = hub.voice("yes, allow that")
    assert ok and "click" in say and hub.pending
    assert not hub.decide(rec.approvals[0]["id"], "allow", by_voice=True)
    ok, say = hub.voice("no")                      # denying by voice is fine
    th.join(2)
    assert conn.replies == [{"reply": "deny", "message": "Denied from hey-claude"}]


def test_voice_allow():
    hub, rec = make_hub()
    conn = FakeConn()
    th = permission(hub, conn)
    assert wait_for(lambda: rec.approvals)
    assert not hub.voice_applies("what time is it")
    ok, say = hub.voice("yes, allow that")
    th.join(2)
    assert ok and say == "Allowed." and conn.replies == [{"reply": "allow"}]


QS = {"questions": [{"question": "Which database?", "header": "DB", "multiSelect": False,
                     "options": [{"label": "Postgres", "description": "big"}, {"label": "SQLite", "description": "small"}]},
                    {"question": "Extras?", "header": "Extras", "multiSelect": True,
                     "options": [{"label": "Auth"}, {"label": "Cache"}]}]}


def test_question_card_and_answers():
    hub, rec = make_hub()
    conn = FakeConn()
    th = permission(hub, conn, tool="AskUserQuestion", tool_input=QS)
    assert wait_for(lambda: rec.approvals)
    req = rec.approvals[0]
    assert req["kind"] == "question" and len(req["questions"]) == 2 and req["questions"][1]["multiSelect"]
    assert req["questions"][0]["options"][1] == {"label": "SQLite", "description": "small"}
    assert not hub.decide(req["id"], "allow")              # a question needs its answers
    ans = {"Which database?": "SQLite", "Extras?": ["Auth", "Cache"]}
    assert hub.answer(req["id"], ans)
    th.join(2)
    assert conn.replies == [{"reply": "answers", "answers": ans}]


def test_question_by_voice():
    hub, rec = make_hub()
    conn = FakeConn()
    th = permission(hub, conn, tool="AskUserQuestion", tool_input={"questions": QS["questions"][:1]})
    assert wait_for(lambda: rec.approvals)
    ok, say = hub.voice("the first one")
    th.join(2)
    assert ok and conn.replies == [{"reply": "answers", "answers": {"Which database?": "Postgres"}}]


@cases("tool_input,truncated", [
    ({"questions": [{"question": "Q?", "options": [{"label": "only one"}]}]}, False),
    ({"questions": []}, False),
    (QS, True),
])
def test_bad_or_truncated_questions_go_to_terminal(tool_input, truncated):
    hub, rec = make_hub()
    conn = FakeConn()
    permission(hub, conn, tool="AskUserQuestion", tool_input=tool_input, truncated=truncated).join(2)
    assert conn.replies == [{"reply": "pass"}] and not rec.approvals


def test_exit_plan_mode_card():
    hub, rec = make_hub()
    conn = FakeConn()
    th = permission(hub, conn, tool="ExitPlanMode", tool_input={"plan": "1. do it\n2. test"})
    assert wait_for(lambda: rec.approvals)
    assert rec.approvals[0]["title"] == "Start on the plan?" and "do it" in rec.approvals[0]["detail"]
    hub.decide(rec.approvals[0]["id"], "allow")
    th.join(2)


def test_handoff_waits_for_the_turn():
    hub, rec = make_hub()
    handed = []
    hub.on_handoff = lambda s: handed.append(s.id)
    hub.handle(msg("SessionStart"))
    assert hub.request_handoff("s1") == "now"
    hub.handle(msg("UserPromptSubmit", prompt="x"))
    assert hub.request_handoff("s1") == "after_turn" and handed == []
    hub.handle(msg("Stop", last_assistant_message="ok"))
    assert handed == ["s1"]
    assert hub.request_handoff("nope") == "unknown"
    hub.handle(msg("SessionStart", "d", env={"entrypoint": "claude-desktop"}))
    assert hub.request_handoff("d") == "already"


def test_garbage_messages_never_raise():
    hub, rec = make_hub()
    conn = FakeConn()
    hub.handle({"event": "PreToolUse", "payload": "nope"}, conn)
    hub.handle({"event": "PermissionRequest", "payload": {}, "wants_reply": True}, conn)
    assert conn.replies == [{"reply": "pass"}]
    hub.handle({}, None)


# ----------------------------------------------------------------- brain routing
def test_brain_session_route():
    from heyclaude.brain import Brain
    b = Brain.__new__(Brain)
    b.hub = None
    assert b.session_route("yes") is None
    hub, rec = make_hub({"voice_approvals": True})
    b.hub = hub
    assert b.session_route("move it to the desktop") is None          # no sessions yet
    hub.handle(msg("SessionStart"))
    assert b.session_route("move it to the desktop") == ("session", "desktop", "it")
    assert b.session_route("yes") is None                              # nothing pending
    conn = FakeConn()
    th = permission(hub, conn)
    assert wait_for(lambda: rec.approvals)
    assert b.session_route("yes, allow that") == ("approval", "yes, allow that")
    hub.cfg["voice_approvals"] = False
    assert b.session_route("yes, allow that") is None
    hub.decide(rec.approvals[0]["id"], "deny")
    th.join(2)


def test_child_env_markers():
    from heyclaude.claude_cli import child_env
    env = child_env({"HEYCLAUDE_TASK": "old", "CLAUDE_CODE_ENTRYPOINT": "claude-desktop", "PATH": "x"})
    assert "HEYCLAUDE_TASK" not in env and "CLAUDE_CODE_ENTRYPOINT" not in env and env["PATH"] == "x"
    assert child_env({}, internal=True)["HEYCLAUDE_INTERNAL"] == "1"
    assert child_env({}, task="t1")["HEYCLAUDE_TASK"] == "t1"


if __name__ == "__main__":
    import pathlib
    import tempfile
    n = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            if "tmp_path" in fn.__code__.co_varnames[: fn.__code__.co_argcount]:
                fn(pathlib.Path(tempfile.mkdtemp()))
            else:
                fn()
            n += 1
            print("ok", name)
    print(n, "passed")
