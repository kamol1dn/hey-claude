"""Finding and launching the claude CLI."""
from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Variables that tie a child process to a *parent* Claude session. They are
# only present when hey-claude itself is started from inside Claude Code; a
# spawned task must be its own top-level session, so drop them.
_SESSION_VARS = (
    "CLAUDECODE", "CLAUDE_PID", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_HOST_SESSION_ID",
    "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN",
    "CLAUDE_CODE_SESSION_ATTENDED",
)


def _version_key(path: str):
    m = re.search(r"claude-code[\\/](\d+)\.(\d+)\.(\d+)", path)
    return tuple(int(x) for x in m.groups()) if m else (0, 0, 0)


def find_claude(configured: str | None = None) -> str:
    if configured:
        if os.path.exists(configured):
            return configured
        raise FileNotFoundError(f"claude.exe not found at configured path {configured}")
    on_path = shutil.which("claude")
    if on_path:
        return on_path
    home = os.path.expanduser("~")
    candidates = [os.path.join(home, ".local", "bin", "claude.exe")]
    local = os.environ.get("LOCALAPPDATA", "")
    # Desktop-app bundled CLI: newest version wins.
    bundled = glob.glob(os.path.join(local, "Claude-Data", "*", "claude-code", "*", "*", "claude.exe"))
    bundled += glob.glob(os.path.join(os.environ.get("APPDATA", ""), "Claude", "claude-code", "*", "claude.exe"))
    candidates += sorted(bundled, key=_version_key, reverse=True)
    for c in candidates:
        if os.path.exists(c):
            return c
    raise FileNotFoundError(
        "Could not find the claude CLI. Install it (`claude install` from the desktop "
        "app's copy, or https://claude.com/claude-code) or set claude.exe in config.yaml."
    )


def child_env() -> dict:
    env = dict(os.environ)
    for k in _SESSION_VARS:
        env.pop(k, None)
    return env


def fast_args(model: str, system_prompt: str) -> list[str]:
    """Flags for the quick-answer path: no tools, no MCP, no settings, no history on disk."""
    return [
        "--model", model,
        "--output-format", "stream-json", "--verbose", "--include-partial-messages",
        "--tools", "",
        "--strict-mcp-config",
        "--setting-sources", "",
        "--disable-slash-commands",
        "--no-session-persistence",
        "--system-prompt", system_prompt,
    ]
