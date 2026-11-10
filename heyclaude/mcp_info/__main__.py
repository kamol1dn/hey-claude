"""The info MCP server (stdio): `python -m heyclaude.mcp_info`. Serves every tool in
heyclaude/mcp_info/*.py (see __init__)."""
from __future__ import annotations

import functools

from heyclaude import mcp_info

INSTRUCTIONS = ("hey-claude's own read-only data tools: each result is shown on the island as a card by itself, "
                "so answer with the gist in a sentence or two.")


def _sdk():
    try:
        from mcp.server.mcpserver import MCPServer
        from mcp.server.mcpserver.exceptions import ToolError
    except ImportError:                                     # mcp 1.x
        from mcp.server.fastmcp import FastMCP as MCPServer
        from mcp.server.fastmcp.exceptions import ToolError
    return MCPServer, ToolError


def build():
    MCPServer, ToolError = _sdk()
    server = MCPServer(name="hey-claude info", instructions=INSTRUCTIONS)
    for name, fn in mcp_info.tools().items():
        def make(fn):
            @functools.wraps(fn)
            def tool(*a, **k):
                try:
                    return fn(*a, **k)
                except (ValueError, LookupError, OSError) as e:
                    raise ToolError(str(e)) from None
            return tool
        server.tool(name=name)(make(fn))
    return server


def main():
    build().run("stdio")


if __name__ == "__main__":
    main()
