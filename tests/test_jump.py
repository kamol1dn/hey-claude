"""heyclaude.hookrelay.jump: finding a session's window, jumping, desktop hand-off.

Offline: the process table and the window list are faked through
``_process_table`` / ``_top_windows`` / ``_creation_time``; raising a window is
mocked. One live smoke test only checks that the real Win32 path doesn't raise.

    .venv\\Scripts\\python tests\\test_jump.py   (or python -m pytest tests/test_jump.py)
"""
import os
import subprocess
import sys
from contextlib import contextmanager
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from heyclaude.hookrelay import jump  # noqa: E402
from heyclaude.hookrelay.jump import Win  # noqa: E402


@contextmanager
def world(procs, wins, times=None):
    """A fake process table + window list (+ creation times, default unknown)."""
    with mock.patch.object(jump, "_process_table", lambda: dict(procs)), \
            mock.patch.object(jump, "_top_windows", lambda: list(wins)), \
            mock.patch.object(jump, "_creation_time", lambda pid: (times or {}).get(pid)):
        yield


def find(procs, wins, pid=10, cwd="", times=None):
    with world(procs, wins, times):
        return jump.session_window(pid, cwd)


def fail(*a, **k):
    raise AssertionError(f"should not be called: {a!r}")


class FakePopen:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, **kw):
        self.calls.append((argv, kw))
        return self


# ---------------------------------------------------------------- desktop_link

def test_desktop_link_adds_local_prefix():
    assert jump.desktop_link("abc-123") == "claude://claude.ai/epitaxy/local_abc-123"


def test_desktop_link_keeps_a_single_local_prefix():
    assert jump.desktop_link("local_abc-123") == "claude://claude.ai/epitaxy/local_abc-123"


# ---------------------------------------------------------------- session_window

def test_nearest_ancestor_with_a_window_wins():
    # relay 10 -> claude 11 -> pwsh 12 -> Code 13 (window) -> GUI app 14 (window)
    procs = {10: (11, "python.exe"), 11: (12, "claude.exe"), 12: (13, "pwsh.exe"),
             13: (14, "Code.exe"), 14: (1, "someapp.exe")}
    wins = [Win(900, 14, "Some App"), Win(500, 13, "app.py - proj - Visual Studio Code")]
    assert find(procs, wins) == 500


def test_prefers_the_title_with_the_folder_name():
    procs = {10: (11, "claude.exe"), 11: (12, "Code.exe"), 12: (1, "explorer.exe")}
    wins = [Win(501, 11, "README.md - other - Visual Studio Code"),
            Win(502, 11, "app.py - MyProj - Visual Studio Code"),
            Win(503, 11, "notes - third - Visual Studio Code")]
    assert find(procs, wins, cwd=r"C:\code\myproj") == 502
    assert find(procs, wins, cwd="C:\\code\\myproj\\") == 502
    assert find(procs, wins, cwd=r"C:\code\nothing") == 501     # no match -> topmost
    assert find(procs, wins) == 501


def test_skips_invisible_untitled_and_owned_windows():
    procs = {10: (11, "claude.exe"), 11: (2, "app.exe"), 2: (0, "System")}
    wins = [Win(600, 11, "hidden", visible=False), Win(601, 11, "  "),
            Win(602, 11, "a dialog", root_owner=603), Win(603, 11, "Main")]
    assert find(procs, wins) == 603


def test_stops_at_explorer():
    # Nothing in the chain has a window; explorer's own windows must not be used.
    procs = {10: (11, "claude.exe"), 11: (12, "cmd.exe"), 12: (13, "explorer.exe"),
             13: (1, "userinit.exe")}
    assert find(procs, [Win(700, 12, "File Explorer"), Win(701, 13, "x")]) is None


def test_stops_at_system_processes():
    for stop in ("svchost.exe", "services.exe", "wininit.exe", "winlogon.exe", "csrss.exe",
                 "smss.exe", "System", "sihost.exe", "RuntimeBroker.exe", "SVCHOST.EXE"):
        procs = {10: (11, "claude.exe"), 11: (12, stop), 12: (1, "app.exe")}
        assert find(procs, [Win(710, 11, "sys window"), Win(711, 12, "beyond")]) is None, stop


def test_stops_at_our_own_process():
    # A task hey-claude started: the chain reaches hey-claude itself; its
    # island window is not the session's.
    me = os.getpid()
    procs = {10: (11, "claude.exe"), 11: (me, "cmd.exe"), me: (13, "python.exe"),
             13: (1, "WindowsTerminal.exe")}
    assert find(procs, [Win(720, me, "hey-claude"), Win(721, 13, "term")]) is None


def test_windows_terminal_chain():
    # WT started the shell: pwsh's parent is WindowsTerminal.exe (OpenConsole is a sibling).
    procs = {10: (11, "node.exe"), 11: (12, "claude.exe"), 12: (20, "pwsh.exe"),
             21: (20, "OpenConsole.exe"), 20: (30, "WindowsTerminal.exe"), 30: (1, "explorer.exe")}
    wins = [Win(800, 20, "Claude Code"), Win(801, 20, "Windows Terminal", visible=False)]
    assert find(procs, wins) == 800


def test_openconsole_ancestor_returns_the_terminal_window():
    procs = {10: (11, "claude.exe"), 11: (21, "cmd.exe"), 21: (20, "OpenConsole.exe"),
             20: (30, "WindowsTerminal.exe"), 30: (1, "explorer.exe")}
    wins = [Win(811, 20, "other"), Win(810, 20, "proj - cmd")]
    assert find(procs, wins, cwd="D:/x/proj") == 810


def test_default_terminal_via_pseudo_console_window():
    # WT as the default terminal (seen live): cmd's parent is explorer, WT was
    # started by svchost, and cmd "owns" a PseudoConsoleWindow (reported under
    # cmd's pid, untitled) whose root owner is the WT window.
    procs = {10: (11, "claude.exe"), 11: (12, "cmd.exe"), 12: (13, "explorer.exe"),
             40: (11, "conhost.exe"), 20: (99, "WindowsTerminal.exe"),
             21: (99, "OpenConsole.exe"), 99: (1, "svchost.exe")}
    wins = [Win(820, 20, "m7-jump-test"), Win(822, 20, "Default IME", visible=False, root_owner=820),
            Win(821, 11, "", visible=True, root_owner=820)]
    assert find(procs, wins) == 820


def test_classic_conhost_child_window():
    # A console window reported under the conhost.exe child of the shell.
    procs = {10: (11, "claude.exe"), 11: (12, "cmd.exe"), 12: (13, "explorer.exe"),
             40: (11, "conhost.exe")}
    assert find(procs, [Win(830, 40, "Command Prompt - claude")]) == 830


def test_loop_guard():
    # 11 <-> 12 point at each other (pid reuse); ppid == pid too.
    assert find({10: (11, "claude.exe"), 11: (12, "a.exe"), 12: (11, "b.exe")},
                [Win(1, 99, "unrelated")]) is None
    assert find({10: (10, "claude.exe")}, []) is None


def test_depth_limit():
    procs = {i: (i + 1, f"p{i}.exe") for i in range(100, 140)}
    assert find(procs, [Win(840, 130, "far away")], pid=100) is None   # 31 levels up
    assert find(procs, [Win(841, 110, "near")], pid=100) == 841


def test_parent_younger_than_child_is_a_reused_pid():
    procs = {10: (11, "claude.exe"), 11: (1, "app.exe")}
    assert find(procs, [Win(850, 11, "reused")], times={10: 1000, 11: 2000}) is None
    assert find(procs, [Win(850, 11, "real")], times={10: 2000, 11: 1000}) == 850


def test_unknown_pid_and_failures_return_none():
    assert find({}, [], pid=12345) is None
    assert find({10: (1, "a.exe")}, [], pid=0) is None

    def boom():
        raise RuntimeError("x")
    with mock.patch.object(jump, "_process_table", boom):
        assert jump.session_window(10) is None


def test_session_window_live_smoke():
    hwnd = jump.session_window(os.getpid(), os.getcwd())
    assert hwnd is None or isinstance(hwnd, int)
    assert isinstance(jump._process_table(), dict)
    assert isinstance(jump._top_windows(), list)


def test_raise_window_rejects_nothing():
    assert jump.raise_window(0) is False


# ---------------------------------------------------------------- jump

def test_jump_desktop_opens_the_link():
    opened, popen = [], FakePopen()
    with mock.patch.object(jump, "raise_window", fail):
        r = jump.jump({"hwnd": 5, "cwd": "", "desktop": True, "session_id": "s-1"},
                      open_url=opened.append, popen=popen)
    assert r == "desktop"
    assert opened == ["claude://claude.ai/epitaxy/local_s-1"]
    assert popen.calls == []


def test_jump_raises_the_window(tmp_path):
    raised, popen = [], FakePopen()
    with mock.patch.object(jump, "raise_window", lambda h: raised.append(h) or True):
        r = jump.jump({"hwnd": 4242, "cwd": str(tmp_path), "desktop": False, "session_id": "s"},
                      open_url=fail, popen=popen)
    assert r == "window" and raised == [4242] and popen.calls == []


def test_jump_falls_back_to_vscode(tmp_path):
    fake_code = str(tmp_path / "code.exe")
    popen = FakePopen()
    with mock.patch.object(jump, "raise_window", lambda h: False), \
            mock.patch.object(jump.shutil, "which", lambda name: fake_code if name == "code" else None):
        r = jump.jump({"hwnd": 77, "cwd": str(tmp_path), "desktop": False, "session_id": "s"},
                      open_url=lambda u: None, popen=popen)
    assert r == "vscode"
    assert popen.calls[0][0] == [fake_code, str(tmp_path)]


def test_jump_vscode_shim_uses_code_exe(tmp_path):
    # bin/code.cmd -> ../Code.exe, so the folder never goes through cmd.exe.
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "code.cmd").write_text("@echo off")
    (tmp_path / "Code.exe").write_bytes(b"")
    work = tmp_path / "a&b"
    work.mkdir()
    popen = FakePopen()
    with mock.patch.object(jump.shutil, "which", lambda name: str(tmp_path / "bin" / "code.cmd")):
        assert jump.jump({"hwnd": None, "cwd": str(work)}, open_url=None, popen=popen) == "vscode"
    assert popen.calls[0][0] == [str(tmp_path / "Code.exe"), str(work)]


def test_jump_vscode_shim_refuses_cmd_metachars(tmp_path):
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "code.cmd").write_text("@echo off")       # no Code.exe next to it
    work = tmp_path / "a&calc"
    work.mkdir()
    popen = FakePopen()
    with mock.patch.object(jump.shutil, "which", lambda name: str(tmp_path / "bin" / "code.cmd")):
        assert jump.jump({"hwnd": None, "cwd": str(work)}, popen=popen) == "none"
    assert popen.calls == []


def test_jump_nothing(tmp_path):
    popen = FakePopen()
    with mock.patch.object(jump, "raise_window", lambda h: False):
        with mock.patch.object(jump.shutil, "which", lambda name: None):
            assert jump.jump({"hwnd": 1, "cwd": str(tmp_path), "desktop": False, "session_id": "s"},
                             open_url=lambda u: None, popen=popen) == "none"
        with mock.patch.object(jump.shutil, "which", lambda name: "C:/x/code.exe"):
            # code exists but the folder is gone / no folder at all
            assert jump.jump({"hwnd": None, "cwd": str(tmp_path / "gone")}, popen=popen) == "none"
            assert jump.jump({}, popen=popen) == "none"
            assert jump.jump(None, popen=popen) == "none"
    assert popen.calls == []


def test_jump_desktop_without_id_falls_through():
    opened = []
    with mock.patch.object(jump, "raise_window", lambda h: True):
        assert jump.jump({"hwnd": 3, "desktop": True, "session_id": ""},
                         open_url=opened.append, popen=fail) == "window"
    assert opened == []


def test_jump_never_raises(tmp_path):
    def boom(*a, **k):
        raise OSError("nope")
    with mock.patch.object(jump, "raise_window", boom), \
            mock.patch.object(jump.shutil, "which", lambda name: "C:/x/code.exe"):
        # desktop link fails -> falls through; raise fails -> caught; popen fails -> "none"
        assert jump.jump({"hwnd": 1, "cwd": str(tmp_path), "desktop": True, "session_id": "s"},
                         open_url=boom, popen=boom) == "none"


# ---------------------------------------------------------------- handoff

def test_handoff_argv_console_and_no_redirection(tmp_path):
    popen = FakePopen()
    env = {"A": "1"}
    r = jump.handoff_to_desktop("C:/bin/claude.exe", "local_0b1c-22", str(tmp_path),
                                popen=popen, env=env)
    assert r is popen
    argv, kw = popen.calls[0]
    assert argv == ["C:/bin/claude.exe", "--desktop", "--resume", "0b1c-22"]
    assert kw["cwd"] == str(tmp_path)
    assert kw["env"] is env
    assert kw["creationflags"] & subprocess.CREATE_NEW_CONSOLE
    si = kw["startupinfo"]
    assert si.dwFlags & subprocess.STARTF_USESHOWWINDOW
    assert si.wShowWindow == 7          # SW_SHOWMINNOACTIVE
    for k in ("stdin", "stdout", "stderr"):
        assert k not in kw, k


def test_handoff_default_env_is_child_env(tmp_path):
    popen = FakePopen()
    with mock.patch.dict(os.environ, {"CLAUDECODE": "1", "ANTHROPIC_API_KEY": "sk-test"}):
        jump.handoff_to_desktop("claude", "abc", str(tmp_path), popen=popen)
    env = popen.calls[0][1]["env"]
    assert "CLAUDECODE" not in env and "ANTHROPIC_API_KEY" not in env
    assert env.get("PATH") or env.get("Path")


def test_handoff_rejects_bad_ids(tmp_path):
    for sid in ("", "--dangerously-skip-permissions", "a b", "x;y", "local_", None):
        popen = FakePopen()
        assert jump.handoff_to_desktop("claude", sid, str(tmp_path), popen=popen) is None, sid
        assert popen.calls == []
    assert jump.handoff_to_desktop("", "abc", str(tmp_path), popen=fail) is None


def test_handoff_start_failure_returns_none(tmp_path):
    def boom(*a, **k):
        raise FileNotFoundError("claude")
    assert jump.handoff_to_desktop("claude", "abc", str(tmp_path), popen=boom, env={}) is None


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
