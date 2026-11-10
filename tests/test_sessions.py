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


# ----------------------------------------------------------------- hey-claude's own agent
def agent_permission(hub, conn, tool="mcp__windows-mcp__Click", tool_input=None):
    m = {"v": 1, "event": "PermissionRequest", "pid": 100, "ppid": 99, "env": {}, "agent": True,
         "truncated": False, "wants_reply": True,
         "payload": {"session_id": "agent-1", "cwd": "D:\\coding\\hey-claude", "hook_event_name": "PermissionRequest",
                     "tool_name": tool, "tool_input": tool_input if tool_input is not None else {"loc": [10, 20]},
                     "permission_suggestions": [{"type": "addRules"}]}}
    th = threading.Thread(target=hub.handle, args=(m, conn), daemon=True)
    th.start()
    return th


def test_agent_card_has_no_session_row_and_screen_ok_lasts_the_request():
    hub, rec = make_hub()
    conn = FakeConn()
    th = agent_permission(hub, conn)
    assert wait_for(lambda: rec.approvals)
    req = rec.approvals[0]
    assert req["agent"] and req["session"] == "hey-claude" and req["title"] == "Click on the screen"
    assert req["detail"] == "at 10, 20" and req["note"] and not req["dangerous"]
    assert [a["id"] for a in req["actions"]] == ["allow", "deny"]          # no "Always" for screen control
    assert hub.agent_pending() and hub.sessions == {} and hub.snapshot() == []
    assert hub.decide(req["id"], "allow")
    th.join(2)
    assert conn.replies == [{"reply": "allow"}] and not hub.pending
    # the rest of the request: other screen tools go through without a card
    conn2 = FakeConn()
    agent_permission(hub, conn2, "mcp__windows-mcp__Type", {"text": "hello"}).join(2)
    assert conn2.replies == [{"reply": "allow"}] and len(rec.approvals) == 1
    # ...but not a dangerous one, and not after the request ended
    conn3 = FakeConn()
    th = agent_permission(hub, conn3, "mcp__windows-mcp__Process", {"mode": "kill", "name": "chrome"})
    assert wait_for(lambda: len(rec.approvals) == 2)
    assert rec.approvals[1]["dangerous"] and rec.approvals[1]["title"] == "Close chrome"
    hub.decide(rec.approvals[1]["id"], "deny")
    th.join(2)
    assert conn3.replies == [{"reply": "deny", "message": S.AGENT_DENY}]
    hub.agent_turn_done()
    conn4 = FakeConn()
    th = agent_permission(hub, conn4, "mcp__windows-mcp__Type", {"text": "x"})
    assert wait_for(lambda: len(rec.approvals) == 3)
    hub.agent_turn_done()                                  # the request ended with the card up: denied
    th.join(2)
    assert conn4.replies == [{"reply": "deny", "message": S.AGENT_MOVED_ON}]
    assert hub.snapshot() == [] and all(r == [] for r in rec.rows)       # never a session row


def test_agent_timeout_denies_and_tells():
    clock = [0.0]
    told = []
    hub, rec = make_hub({"approval_timeout_s": 10}, clock=lambda: clock[0])
    hub.on_agent_timeout = told.append
    conn = FakeConn()
    th = agent_permission(hub, conn, "mcp__claude_ai_Gmail__create_draft", {"to": "a@b.c"})
    assert wait_for(lambda: rec.approvals)
    clock[0] = 11.0
    th.join(2)
    assert conn.replies == [{"reply": "deny", "message": S.AGENT_TIMEOUT}]
    assert [r["id"] for r in told] == [rec.approvals[0]["id"]] and not hub.pending


def test_agent_always_is_kept_by_the_app():
    kept = []
    allowed = set()
    hub, rec = make_hub()
    hub.on_agent_always = lambda tool: (kept.append(tool), allowed.add(tool))
    hub.agent_allowed = lambda tool: tool in allowed
    conn = FakeConn()
    th = agent_permission(hub, conn, "mcp__claude_ai_Spotify__search", {"query": "x"})
    assert wait_for(lambda: rec.approvals)
    assert [a["id"] for a in rec.approvals[0]["actions"]] == ["allow", "deny", "always"]
    hub.decide(rec.approvals[0]["id"], "always")
    th.join(2)
    assert conn.replies == [{"reply": "allow"}]            # never "always": no settings file gets a rule
    assert kept == ["mcp__claude_ai_Spotify__search"]
    conn2 = FakeConn()
    agent_permission(hub, conn2, "mcp__claude_ai_Spotify__search", {"query": "y"}).join(2)
    assert conn2.replies == [{"reply": "allow"}] and len(rec.approvals) == 1


@cases("tool,inp,always", [
    ("Bash", {"command": "dir"}, False),                       # too broad to keep
    ("Write", {"file_path": "D:\\coding\\hey-claude\\x.txt", "content": "x"}, False),
    ("WebFetch", {"url": "https://example.com"}, True),
    ("mcp__info__x", {}, True),
])
def test_agent_always_only_for_narrow_tools(tool, inp, always):
    hub, rec = make_hub()
    conn = FakeConn()
    th = agent_permission(hub, conn, tool, inp)
    assert wait_for(lambda: rec.approvals)
    assert ("always" in [a["id"] for a in rec.approvals[0]["actions"]]) == always
    hub.decide(rec.approvals[0]["id"], "deny")
    th.join(2)


def test_telegram_send_card_shows_the_draft_and_needs_a_click():
    hub, rec = make_hub()
    conn = FakeConn()
    th = agent_permission(hub, conn, "mcp__telegram__telegram_send_message",
                          {"chat": "@alex", "message": "Running ten minutes late, sorry!"})
    assert wait_for(lambda: rec.approvals)
    req = rec.approvals[0]
    assert req["title"] == "Send a Telegram message to @alex"
    assert req["detail"] == "Running ten minutes late, sorry!" and req["prose"]
    assert req["dangerous"] and [a["id"] for a in req["actions"]] == ["allow", "deny"]
    assert hub.voice("yes allow it") == (True, "That one needs a click on the island.")
    assert hub.pending                                   # still up
    hub.decide(req["id"], "allow")
    th.join(2)
    assert conn.replies == [{"reply": "allow"}]
    t, d = S.agent_card("mcp__telegram__telegram_send_file", {"chat": "me", "file_path": "C:\\a\\b.pdf", "caption": "hi"})
    assert t == "Send a file on Telegram to me" and d == "b.pdf · hi"


def test_agent_voice_answers():
    hub, rec = make_hub()
    conn = FakeConn()
    th = agent_permission(hub, conn, "mcp__windows-mcp__App", {"mode": "launch", "name": "Spotify"})
    assert wait_for(lambda: rec.approvals)
    assert rec.approvals[0]["title"] == "Open Spotify"
    assert hub.voice_applies("yes") and hub.voice("yes, allow that") == (True, "Allowed.")
    th.join(2)
    assert conn.replies == [{"reply": "allow"}]
    hub.agent_turn_done()
    conn2 = FakeConn()
    th = agent_permission(hub, conn2, "mcp__windows-mcp__Shortcut", {"shortcut": "ctrl+s"})
    assert wait_for(lambda: len(rec.approvals) == 2)
    assert hub.voice("I'll do it in the terminal") == (True, "Denied.")    # no terminal: it's a deny
    th.join(2)
    assert conn2.replies == [{"reply": "deny", "message": S.AGENT_DENY}]


def test_agent_moved_on_and_other_agent_events():
    hub, rec = make_hub()
    conn = FakeConn()
    th = agent_permission(hub, conn, "mcp__windows-mcp__Scroll", {"direction": "down"})
    assert wait_for(lambda: rec.approvals)
    assert hub.agent_moved_on() and not hub.agent_moved_on()
    th.join(2)
    assert conn.replies == [{"reply": "deny", "message": S.AGENT_MOVED_ON}]
    hub.handle({"event": "PreToolUse", "agent": True, "payload": {"session_id": "a", "tool_name": "Read"}}, None)
    assert hub.sessions == {}


@cases("tool,title", [
    ("mcp__claude_ai_Spotify__search", "Spotify · search"),
    ("mcp__claude_ai_Gmail__create_draft", "Gmail · create draft"),
    ("mcp__telegram__telegram_mark_read", "Telegram · mark read"),
    ("Bash", "Runs · dir"),
])
def test_agent_card_titles(tool, title):
    assert S.agent_card(tool, {"command": "dir"} if tool == "Bash" else {})[0] == title


@cases("tool,inp,danger", [
    ("mcp__windows-mcp__Click", {"loc": [1, 2]}, False),
    ("mcp__windows-mcp__Type", {"text": "rm -rf /"}, False),     # typing is approved per request, not a command
    ("mcp__windows-mcp__Process", {"mode": "list"}, False),
    ("mcp__windows-mcp__Process", {"mode": "kill", "pid": 4}, True),
    ("mcp__windows-mcp__FileSystem", {"mode": "read", "path": "a"}, False),
    ("mcp__windows-mcp__FileSystem", {"mode": "delete", "path": "a"}, True),
    ("mcp__windows-mcp__App", {"mode": "launch", "name": "Notepad"}, False),
    ("mcp__windows-mcp__App", {"mode": "launch_executable", "executable": "x.exe"}, True),
    ("mcp__telegram__telegram_send_message", {}, True),
    ("mcp__telegram__telegram_download_media", {}, False),
    ("Bash", {"command": "git push"}, True),
])
def test_agent_danger(tool, inp, danger):
    assert S.agent_danger(tool, inp, "D:\\coding\\hey-claude") is danger


def test_screen_tools_and_gate():
    from heyclaude import brain as B
    assert S.is_screen_tool("mcp__windows-mcp__Click") and not S.is_screen_tool("mcp__windows-mcp__Wait")
    assert not S.is_screen_tool("mcp__windows__set_volume")
    assert set(B.WINDOWS_MCP_PASSIVE) <= set(B.WINDOWS_MCP_READ_ONLY) and set(S.SCREEN_PASSIVE) == set(B.WINDOWS_MCP_READ_ONLY)
    acting = ["Click", "Type", "Scroll", "Move", "Shortcut", "App", "Scrape", "MultiSelect", "MultiEdit",
              "Clipboard", "Notification", "Process", "FileSystem", "PowerShell", "Registry"]
    kept, dropped = B.gate_allowed_tools([f"mcp__windows-mcp__{n}" for n in acting + ["Wait", "WaitFor", "Snapshot"]])
    assert kept == ["mcp__windows-mcp__Wait", "mcp__windows-mcp__WaitFor", "mcp__windows-mcp__Snapshot"]
    assert len(dropped) == len(acting)


def test_child_env_agent_mark():
    from heyclaude.claude_cli import child_env
    assert child_env({}, internal="agent")["HEYCLAUDE_INTERNAL"] == "agent"
    assert "HEYCLAUDE_INTERNAL" not in child_env({"HEYCLAUDE_INTERNAL": "agent"})


def test_agent_approval_settings_file(tmp_path):
    from unittest import mock
    from heyclaude import brain as B
    from heyclaude import config as C
    ag = B.Agent.__new__(B.Agent)
    ag.cfg = {"agent": {}}
    assert ag._approval_settings() is None                  # no hooks section (hand-made test configs)
    ag.cfg = {"agent": {}, "hooks": {"enabled": True, "approvals": False}}
    assert ag._approval_settings() is None
    ag.cfg = {"agent": {}, "hooks": {"enabled": True, "approvals": True}}
    with mock.patch.object(C, "STATE_DIR", tmp_path), \
            mock.patch.dict(os.environ, {"LOCALAPPDATA": str(tmp_path / "local")}):
        f = ag._approval_settings()
        if os.name != "nt":
            assert f is None
            return
        data = json.load(open(f, encoding="utf-8"))
        cmd = data["hooks"]["PermissionRequest"][0]["hooks"][0]["command"]
        assert cmd.endswith(" PermissionRequest agent") and (tmp_path / "local" / "hey-claude" / "hook" /
                                                             "heyclaude-hook.py").is_file()


def test_config_add_to_list(tmp_path):
    from heyclaude import config as C
    p = tmp_path / "config.yaml"
    p.write_text("agent:\n  model: sonnet\n  allowed_tools:\n  - Read\nwake:\n  threshold: 0.5\n", encoding="utf-8")
    bdir = tmp_path / "bak"
    assert C.add_to_list("agent.allowed_tools", "mcp__x__y", p, backup_dir=bdir) is not None
    assert C.add_to_list("agent.allowed_tools", "mcp__x__y", p, backup_dir=bdir) is None    # once
    import yaml
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    assert data["agent"]["allowed_tools"] == ["Read", "mcp__x__y"] and data["agent"]["model"] == "sonnet"
    assert data["wake"]["threshold"] == 0.5
    (b,) = list(bdir.iterdir())
    assert "mcp__x__y" not in b.read_text(encoding="utf-8")


def test_assistant_answers_an_agent_card_while_its_turn_runs(tmp_path):
    """A spoken "yes" while the agent waits on a card is handled at once (the agent's
    turn holds the conversation lock); another request denies the card instead."""
    import types
    from heyclaude.assistant import Assistant
    a = Assistant.__new__(Assistant)
    cfgp = tmp_path / "config.yaml"
    cfgp.write_text("agent:\n  allowed_tools:\n  - Read\n", encoding="utf-8")
    a.cfg = {"hooks": {"voice_approvals": True}, "agent": {"allowed_tools": ["Read"]}}
    a.config_path = str(cfgp)
    hub, rec = make_hub()
    a.hub = hub
    said = []
    a.speaker = types.SimpleNamespace(announce=said.append)
    a.conv_lock = threading.Lock()
    a.gen = 5
    conn = FakeConn()
    th = agent_permission(hub, conn, "mcp__claude_ai_Spotify__search", {"query": "x"})
    assert wait_for(lambda: rec.approvals)
    assert a.request("yes, allow it") is None and a.gen == 5 and said == ["Allowed."]
    th.join(2)
    assert conn.replies == [{"reply": "allow"}]
    conn2 = FakeConn()
    th = agent_permission(hub, conn2, "mcp__windows-mcp__Click", {"loc": [1, 1]})
    assert wait_for(lambda: len(rec.approvals) == 2)
    assert a._agent_approval_reply("what time is it") is False       # a new request: the card goes
    th.join(2)
    assert conn2.replies == [{"reply": "deny", "message": S.AGENT_MOVED_ON}]
    # "Always": allowed at once, and saved to config.yaml (with a backup)
    from unittest import mock
    from heyclaude import config as C
    with mock.patch.object(C, "STATE_DIR", tmp_path):
        a._agent_always("mcp__claude_ai_Spotify__search")
    assert a._agent_allowed("mcp__claude_ai_Spotify__search") and not a._agent_allowed("mcp__x__PowerShell")
    assert "mcp__claude_ai_Spotify__search" in cfgp.read_text(encoding="utf-8")
    assert list((tmp_path / "config-backups").iterdir())


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
