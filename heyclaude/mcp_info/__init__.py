"""hey-claude's own read-only data tools (ROADMAP M5b), served as the "info" MCP server
(`python -m heyclaude.mcp_info`) to the tool agent. Their results become island cards
(widgets/<id>/widget.json `triggers`), so the numbers on a card come from a tool, never
from the model.

Tools are plug-ins: every module in this package that defines `TOOLS` (a list of plain
functions with type hints and a docstring) is served. A module may also define
`PROMPT` (one sentence for the agent's system prompt). Settings reach the tools as the
`info:` config section, JSON in HEYCLAUDE_INFO_CONFIG (`config()`).
"""
from __future__ import annotations

import importlib
import json
import os
import pkgutil

SERVER = "info"


def modules() -> list:
    out = []
    for m in sorted(pkgutil.iter_modules(__path__), key=lambda m: m.name):
        if m.name.startswith("_"):
            continue
        try:
            mod = importlib.import_module(f"{__name__}.{m.name}")
        except Exception as e:                       # one broken module never takes the others down
            print(f"[info] {m.name}: {e}")
            continue
        if getattr(mod, "TOOLS", None):
            out.append(mod)
    return out


def tools() -> dict:
    """tool name -> function, for every module."""
    out = {}
    for mod in modules():
        for fn in mod.TOOLS:
            out[fn.__name__] = fn
    return out


def tool_names() -> list[str]:
    return [f"mcp__{SERVER}__{n}" for n in tools()]


def prompt() -> str:
    parts = [str(getattr(m, "PROMPT", "")).strip() for m in modules()]
    parts = [p for p in parts if p]
    if not parts:
        return ""
    return (" hey-claude's own tools (mcp__info__*) answer these directly, and the island shows a card from "
            "their result by itself, so just say the gist: " + " ".join(parts))


def config() -> dict:
    try:
        v = json.loads(os.environ.get("HEYCLAUDE_INFO_CONFIG", "") or "{}")
    except ValueError:
        v = {}
    return v if isinstance(v, dict) else {}
