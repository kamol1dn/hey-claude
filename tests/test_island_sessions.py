"""Offline tests for M7's island side: watched Claude Code sessions in the hover
list (rows, satellites, the row's actions), the done / error moments, approval
cards (dangerous, pass), question cards (single + multiSelect answers, Submit
only when complete), the approval_needed state, and the diff window.

    .venv\\Scripts\\python tests\\test_island_sessions.py
"""
import faulthandler
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
faulthandler.enable()
sys.stdout.reconfigure(line_buffering=True)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from heyclaude.island_qml import merged_rows, route, session_rows  # noqa: E402

NOW = time.time()
SESSIONS = [
    {"id": "aaaa1111", "title": "fix login bug", "project": "webapp", "cwd": "D:/x/webapp", "status": "working",
     "activity": "Edits login.py", "plus": 42, "minus": 7, "diffs": 3, "desktop": False, "task": "", "updated": NOW},
    {"id": "bbbb2222", "title": "", "project": "hey-claude", "cwd": "D:/coding/hey-claude", "status": "waiting",
     "activity": "Runs pytest -q", "plus": 0, "minus": 0, "diffs": 0, "desktop": True, "task": "", "updated": NOW - 30},
    {"id": "cccc3333", "title": "release notes", "project": "docs", "cwd": "D:/x/docs", "status": "done",
     "activity": "Writes CHANGELOG.md", "plus": 120, "minus": 3, "diffs": 1, "desktop": False, "task": "",
     "updated": NOW - 90, "last": "Release notes written."},
]
QUESTION = {
    "id": "q1", "title": "Claude asks", "tool": "AskUserQuestion", "session": "hey-claude", "session_id": "bbbb2222",
    "kind": "question", "dangerous": False, "actions": [], "expires": NOW + 108,
    "questions": [
        {"question": "Which database?", "header": "Database", "multiSelect": False,
         "options": [{"label": "SQLite", "description": "file based"}, {"label": "Postgres", "description": "server"}]},
        {"question": "Which checks?", "header": "Checks", "multiSelect": True,
         "options": [{"label": "ruff"}, {"label": "pytest"}, {"label": "mypy"}]},
    ]}


def _permission(ap_id, dangerous=False):
    return {"id": ap_id, "title": "Run this command?", "detail": "git push --force origin main", "tool": "Bash",
            "session": "fix login bug · webapp", "session_id": "aaaa1111", "dangerous": dangerous, "kind": "permission",
            "actions": [{"id": "deny", "label": "Deny"}, {"id": "always", "label": "Always"},
                        {"id": "allow", "label": "Allow", "primary": True}],
            "expires": time.time() + 108}


# ------------------------------------------------------------------ pure helpers
def test_session_rows_map_the_hub_snapshot():
    rows = session_rows(SESSIONS)
    assert [r["sid"] for r in rows] == ["cccc3333", "bbbb2222", "aaaa1111"]      # newest last, like tasks
    by = {r["sid"]: r for r in rows}
    a = by["aaaa1111"]
    assert a["id"] == "s:aaaa1111" and a["kind"] == "session" and a["status"] == "running"
    assert a["name"] == "fix login bug" and a["right"] == "Edits login.py" and a["elapsed"] == "+42 \u22127"
    assert a["diffs"] == 3 and a["desktop"] is False
    assert by["bbbb2222"]["name"] == "hey-claude" and by["bbbb2222"]["status"] == "waiting" and by["bbbb2222"]["elapsed"] == ""
    assert by["cccc3333"]["status"] == "done" and by["cccc3333"]["right"] == "Release notes written."
    assert session_rows([{"id": "x", "status": "error"}])[0]["status"] == "failed"
    assert session_rows([{"status": "working"}]) == []                              # no id, no row


def test_tasks_and_sessions_share_five_rows():
    tasks = [{"id": f"t{i}", "project": "p", "prompt": f"job {i}", "status": "running", "activity": "x"} for i in range(5)]
    rows = merged_rows(tasks, SESSIONS)
    assert len(rows) == 5
    assert sum(r.get("kind") == "session" for r in rows) == 2                     # sessions keep 2 places
    assert [r["id"] for r in rows[:3]] == ["t2", "t3", "t4"]                       # the newest tasks
    assert len(merged_rows([], SESSIONS)) == 3 and len(merged_rows(tasks[:1], [])) == 1


def test_route_approval_needed_falls_back_to_the_resting_state():
    ctx = {"engine": "ready", "now": 100, "rest": "idle"}
    assert route("approval_needed", "", ctx) == [("go", ["ready", {}])]
    assert route("approval_needed", "", dict(ctx, rest="off")) == [("go", ["off", {}])]
    paused = dict(ctx, rest="off", paused_until=200, paused_since=50)
    assert route("approval_needed", "", paused)[0][1][0] == "paused"
    # a theme that only has the core states: confused -> idle as well
    assert route("approval_needed", "", dict(ctx, implements=["idle", "off", "listening", "thinking", "talking"])) == \
        [("go", ["ready", {}])]


def test_diff_window_lists_edits_and_colours_the_diff():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from heyclaude.hookrelay.diffview import DiffWindow
    diffs = [
        {"file": "D:/x/webapp/app/login.py", "tool": "Edit", "plus": 2, "minus": 1, "huge": False, "t": NOW - 60,
         "unified": "--- a/login.py\n+++ b/login.py\n@@ -1,3 +1,4 @@\n import os\n-x = 1\n+x = 2\n+y = 3\n"},
        {"file": "D:/x/webapp/data/big.json", "tool": "Write", "plus": 90000, "minus": 0, "huge": True, "t": NOW, "unified": ""},
    ]
    w = DiffWindow("fix login bug", diffs)
    assert w.files.count() == 2
    assert "login.py" in w.files.item(0).text() and "+2 \u22121" in w.files.item(0).text()
    assert w.files.currentRow() == 1                                       # the newest edit first
    assert "too large to show" in w.selected_text() and "+90000" in w.selected_text()
    w.files.setCurrentRow(0)
    txt = w.selected_text()
    assert "+x = 2" in txt and "-x = 1" in txt and "@@ -1,3 +1,4 @@" in txt
    # the highlighter colours + green, - red
    doc = w.view.document()
    w.highlighter.rehighlight()
    colours = {}
    for i in range(doc.blockCount()):
        b = doc.findBlockByNumber(i)
        fmts = b.layout().formats()
        if fmts:
            colours[b.text()] = fmts[0].format.foreground().color().name().upper()
    assert colours.get("+x = 2") == "#6CCB7A" and colours.get("-x = 1") == "#E5646A"
    assert "Diffs" in w.windowTitle() and "fix login bug" in w.windowTitle()
    w.set_diffs([])
    assert w.files.count() == 0 and "no edits" in w.selected_text()
    w.close()


# ------------------------------------------------------------------ the QML island
_APP = None
_ISL = None


def _island():
    global _APP, _ISL
    if _ISL is not None:
        return _ISL
    from PySide6.QtWidgets import QApplication
    _APP = QApplication.instance() or QApplication([])
    from heyclaude.island import Bridge
    from heyclaude.island_qml import QmlIsland
    b = Bridge()
    isl = QmlIsland(b, {"screen": "primary", "top_margin": "auto", "motion": "full"}, wake_label="hey claude", software=True)
    isl.bridge_ = b
    _ISL = isl
    _spin(600)
    return isl


def _spin(ms):
    from PySide6.QtCore import QEventLoop, QTimer
    loop = QEventLoop()
    QTimer.singleShot(int(ms), loop.quit)
    loop.exec()


def _v(x):
    return x.toVariant() if hasattr(x, "toVariant") else x


def _click(isl, rect):
    """A real left click in the island window at the middle of an item's rect (window pixels)."""
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    QTest.mouseClick(isl.view, Qt.LeftButton, Qt.NoModifier, QPoint(int(rect["x"] + rect["w"] / 2), int(rect["y"] + rect["h"] / 2)))
    _spin(120)


def _rect(isl, name, card=""):
    r = _v(isl.call("itemRect", name, card))
    return r or None


def _reset(isl):
    b = isl.bridge_
    for ap in list(isl.approvals):
        b.approval_withdrawn.emit(ap)
    b.sessions.emit([])
    b.tasks.emit([])
    isl._send("pinList", [False])
    b.state.emit("idle", "")
    _spin(500)


def test_controls_dont_stick_after_a_short_think():
    """Thinking left before the mic/stop fade-in's 140 ms delay (an instant command) must not
    leave the buttons over the resting label."""
    isl = _island()
    _reset(isl)
    b = isl.bridge_
    b.state.emit("thinking", "")
    _spin(40)
    b.state.emit("idle", "")
    _spin(700)
    p = _v(isl.call("probeUx"))
    assert p["state"] == "ready" and p["ctl"] < 0.01, (p["state"], p["ctl"])
    _reset(isl)


def test_sessions_show_in_the_hover_list_with_tasks():
    isl = _island()
    _reset(isl)
    b = isl.bridge_
    b.tasks.emit([{"id": "t1", "project": "hey-claude", "prompt": "update the README", "status": "running",
                   "activity": "Reads README.md", "elapsed": "1:12"}])
    b.sessions.emit(SESSIONS)
    isl._send("pinList", [True])
    _spin(900)
    info = _v(isl.call("rowsInfo"))
    assert info["geom"], info
    kinds = [(r["id"], r["kind"], r["status"]) for r in info["rows"]]
    assert ("t1", "task", "running") in kinds
    assert ("s:aaaa1111", "session", "running") in kinds and ("s:bbbb2222", "session", "waiting") in kinds
    assert ("s:cccc3333", "session", "done") in kinds
    # working sessions count with tasks (satellites, the ready label)
    assert info["label"] == "2 running", info["label"]
    snap = _v(isl.call("probe"))
    assert snap["state"] == "ready"
    b.tasks.emit([])
    _spin(300)
    assert _v(isl.call("rowsInfo"))["label"] == "1 session working"
    # the row draws "title · activity" and its +N −M
    assert _rect(isl, "sessionCounts") is not None
    _reset(isl)


def test_a_session_row_click_shows_its_actions_and_they_route():
    isl = _island()
    _reset(isl)
    b = isl.bridge_
    got = []
    isl.session_action.connect(lambda sid, a: got.append((sid, a)))
    b.sessions.emit(SESSIONS)
    isl._send("pinList", [True])
    _spin(900)
    # a real click on the working session's row opens its actions
    row = _rect(isl, "row:s:aaaa1111")
    assert row, "no row for the session"
    _click(isl, {"x": row["x"] + 40, "y": row["y"], "w": 20, "h": row["h"]})
    _spin(300)
    assert _v(isl.call("rowsInfo"))["open"] == "aaaa1111"
    for name in ("act:diff", "act:jump", "act:desktop", "act:dismiss"):
        assert _rect(isl, name) is not None, name              # diffs > 0 and not a desktop session: all four
    _click(isl, _rect(isl, "act:diff"))
    assert got == [("aaaa1111", "diff")], got
    assert _v(isl.call("rowsInfo"))["open"] == ""               # an action closes them
    # a desktop session has no "To desktop", no diffs -> no "Diff"
    isl.call("rowTapped", {"kind": "session", "sid": "bbbb2222"})
    _spin(300)
    assert _rect(isl, "act:desktop") is None and _rect(isl, "act:diff") is None
    assert _rect(isl, "act:jump") is not None
    _click(isl, _rect(isl, "act:jump"))
    assert got[-1] == ("bbbb2222", "jump")
    # the model slot too (what a click ends in), and unknown actions are dropped
    isl.model.sessionAction("cccc3333", "dismiss")
    isl.model.sessionAction("cccc3333", "rm -rf")
    assert got[-1] == ("cccc3333", "dismiss") and len(got) == 3
    assert isl.find_session("cccc3333") is None                       # gone from the island at once
    _spin(300)
    assert all(r["sid"] != "cccc3333" for r in _v(isl.call("rowsInfo"))["rows"])
    _reset(isl)


def test_a_finished_session_gets_the_check_and_an_error_the_flinch():
    isl = _island()
    _reset(isl)
    b = isl.bridge_
    b.sessions.emit(SESSIONS[:1])
    _spin(300)
    b.session_event.emit({"session": "aaaa1111", "kind": "tool", "tool": "Bash", "input": {"command": "pytest"}})
    assert "session:aaaa1111" in isl.tools                       # the tool ticker as before
    b.session_event.emit({"session": "aaaa1111", "kind": "done", "text": "All tests pass."})
    assert not isl.tools
    _spin(700)
    snap = _v(isl.call("snapshot"))
    assert any(l["text"] == "done · fix login bug" and not l["out"] for l in snap["labels"]), snap["labels"]
    _spin(3700)
    snap = _v(isl.call("snapshot"))
    assert not any(l["text"].startswith("done ·") and not l["out"] for l in snap["labels"])
    b.session_event.emit({"session": "aaaa1111", "kind": "error", "text": "API error: overloaded"})
    _spin(400)
    assert _v(isl.call("probe"))["state"] == "error"
    snap = _v(isl.call("snapshot"))
    assert any("fix login bug: API error: overloaded" in l["text"] for l in snap["labels"]), snap["labels"]
    _spin(5000)
    assert _v(isl.call("probe"))["state"] == "ready"             # it settles back by itself
    # in a voice state: no takeover
    b.state.emit("thinking", "what's up")
    _spin(300)
    b.session_event.emit({"session": "aaaa1111", "kind": "error", "text": "boom"})
    _spin(300)
    assert _v(isl.call("probe"))["state"] == "thinking"
    _reset(isl)


def test_a_dangerous_approval_card_and_approval_needed():
    isl = _island()
    _reset(isl)
    b = isl.bridge_
    got = []
    isl.approval_decided.connect(lambda i, a: got.append((i, a)))
    assert isl.island_state == "idle"
    b.approval.emit(_permission("ap1", dangerous=True))
    assert "ap1" in isl.approvals                                  # synchronous: the hub acks right after
    assert isl.island_state == "approval_needed"
    _spin(600)
    assert _v(isl.call("probe"))["state"] == "ready"               # Ember falls back to rest; the card shows it
    assert _rect(isl, "dangerMarker", "ap1") is not None
    for a in ("deny", "always", "allow"):
        assert _rect(isl, "action:" + a, "ap1") is not None, a
    detail = _v(isl.call("cardDo", "ap1", "req", []))
    assert detail["detail"].startswith("git push")
    # a second, harmless card has no marker
    b.approval.emit(_permission("ap2"))
    _spin(500)
    assert _rect(isl, "dangerMarker", "ap2") is None
    assert _v(isl.call("extrasInfo"))["approvals"] == 2
    # a real click on Always answers ap2; still one waiting -> still approval_needed
    _click(isl, _rect(isl, "action:always", "ap2"))
    assert got == [("ap2", "always")], got
    assert isl.island_state == "approval_needed"
    # "pass" is a valid answer too (the terminal asks); junk isn't
    isl.model.approvalDecided("ap1", "explode")
    assert "ap1" in isl.approvals
    isl.model.approvalDecided("ap1", "pass")
    assert got[-1] == ("ap1", "pass") and not isl.approvals
    assert isl.island_state == "idle"                                # back to the previous mode
    _spin(400)
    assert _v(isl.call("extrasInfo"))["approvals"] == 0
    # off with a card: approval_needed, and the engine stays off (Ember's fallback)
    b.state.emit("off", "listening off")
    _spin(400)
    b.approval.emit(_permission("ap3"))
    assert isl.island_state == "approval_needed"
    _spin(400)
    assert _v(isl.call("probe"))["state"] == "off"
    b.approval_withdrawn.emit("ap3")
    assert isl.island_state == "off"
    # in a voice state the card waits under it, the state stays
    b.state.emit("thinking", "hm")
    b.approval.emit(_permission("ap4"))
    assert isl.island_state == "thinking"
    b.state.emit("idle", "")
    assert isl.island_state == "approval_needed"
    b.approval_withdrawn.emit("ap4")
    assert isl.island_state == "idle"
    _reset(isl)


def test_question_card_single_and_multi_select():
    isl = _island()
    _reset(isl)
    b = isl.bridge_
    answered, decided = [], []
    isl.approval_answered.connect(lambda i, a: answered.append((i, a)))
    isl.approval_decided.connect(lambda i, a: decided.append((i, a)))
    b.approval.emit(QUESTION)
    _spin(700)
    assert isl.island_state == "approval_needed"
    sub = _rect(isl, "submit", "q1")
    assert sub and sub["enabled"] is False                          # nothing picked yet
    _click(isl, sub)
    assert not answered
    # single select is radio-like
    _click(isl, _rect(isl, "option:0:SQLite", "q1"))
    _click(isl, _rect(isl, "option:0:Postgres", "q1"))
    assert _v(isl.call("cardDo", "q1", "payload", []))["Which database?"] == "Postgres"
    assert _v(isl.call("cardDo", "q1", "complete", [])) is False   # the second question has no answer yet
    # multiSelect toggles
    _click(isl, _rect(isl, "option:1:ruff", "q1"))
    _click(isl, _rect(isl, "option:1:mypy", "q1"))
    isl.call("cardDo", "q1", "toggle", [1, "mypy"])                # off again
    isl.call("cardDo", "q1", "toggle", [1, "pytest"])
    _spin(200)
    assert _v(isl.call("cardDo", "q1", "complete", [])) is True
    assert _rect(isl, "submit", "q1")["enabled"] is True
    _click(isl, _rect(isl, "submit", "q1"))
    assert answered == [("q1", {"Which database?": "Postgres", "Which checks?": ["ruff", "pytest"]})], answered
    assert not decided and not isl.approvals and isl.island_state == "idle"
    _spin(400)
    assert _v(isl.call("extrasInfo"))["approvals"] == 0
    # "In terminal" passes it back
    b.approval.emit(dict(QUESTION, id="q2"))
    _spin(600)
    _click(isl, _rect(isl, "inTerminal", "q2"))
    assert decided == [("q2", "pass")] and len(answered) == 1
    # an answer for a card that's gone does nothing
    isl.model.approvalAnswered("q2", {"Which database?": "SQLite"})
    assert len(answered) == 1
    _reset(isl)


def test_open_diff_keeps_one_window_per_title():
    isl = _island()
    d = [{"file": "a.py", "tool": "Edit", "plus": 1, "minus": 0, "huge": False, "t": NOW, "unified": "@@ -0,0 +1 @@\n+x\n"}]
    w = isl.open_diff("fix login bug", d)
    assert isl.diff_windows["fix login bug"] is w and w.files.count() == 1
    w2 = isl.open_diff("fix login bug", d + d)
    assert w2 is w and w.files.count() == 2
    w.close()
    _spin(200)
    assert "fix login bug" not in isl.diff_windows


def test_rest_stays_quiet_with_idle_sessions():
    """No new always-running timer at rest: idle / done sessions don't keep frames going."""
    isl = _island()
    _reset(isl)
    b = isl.bridge_
    b.sessions.emit([dict(SESSIONS[1], status="idle"), SESSIONS[2]])
    _spin(7500)                                                    # past the 6 s pop-up
    rc = _v(isl.call("restCheck"))
    assert rc["state"] == "ready" and not rc["frameAnimation"], rc
    assert not rc["timer"] or rc["interval"] >= 100, rc            # ready's slow tick at most
    _reset(isl)


if __name__ == "__main__":
    n = 0
    failed = []
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            try:
                fn()
                n += 1
                print("ok", name)
            except Exception:
                import traceback
                traceback.print_exc()
                failed.append(name)
                print("FAIL", name)
    print(n, "passed", f"{len(failed)} failed: {', '.join(failed)}" if failed else "")
    if _ISL is not None:
        _ISL.close()
        _spin(300)
    sys.stdout.flush()
    sys.stderr.flush()
    code = 1 if failed else 0
    if sys.platform == "win32":                    # no DLL teardown (see test_island.py)
        import ctypes
        k32 = ctypes.windll.kernel32
        k32.TerminateProcess(k32.GetCurrentProcess(), code)
    os._exit(code)
