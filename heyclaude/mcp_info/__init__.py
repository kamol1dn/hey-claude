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


def _skip(skip) -> list[str]:
    """Tool patterns of switched-off plugins (heyclaude/plugins.py): given by the caller in
    hey-claude's process, or HEYCLAUDE_SKIP_TOOLS when this runs as the server."""
    if skip is not None:
        return list(skip)
    from heyclaude.plugins import SKIP_ENV, disabled_from_env
    return disabled_from_env(os.environ.get(SKIP_ENV))


def tools(skip=None) -> dict:
    """tool name -> function, for every module, without switched-off plugins' tools."""
    from heyclaude.plugins import skipped
    sk = _skip(skip)
    out = {}
    for mod in modules():
        for fn in mod.TOOLS:
            if not skipped(f"mcp__{SERVER}__{fn.__name__}", sk):
                out[fn.__name__] = fn
    return out


def tool_names(skip=None) -> list[str]:
    return [f"mcp__{SERVER}__{n}" for n in tools(skip)]


def prompt(skip=None) -> str:
    from heyclaude.plugins import skipped
    sk = _skip(skip)
    parts = [str(getattr(m, "PROMPT", "")).strip() for m in modules()
             if any(not skipped(f"mcp__{SERVER}__{fn.__name__}", sk) for fn in m.TOOLS)]
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
