"""The MCP server (stdio). `python -m heyclaude.mcp_windows`.

Built on the MCP Python SDK's high-level server (FastMCP in mcp 1.x, renamed
MCPServer in 2.x; both work). Each tool is a plain function from the
registry; errors the user should hear come back as tool errors.
"""
from __future__ import annotations

import functools
import inspect

from . import registry
from ._util import ToolError

INSTRUCTIONS = (
    "Comfort settings for the user's Windows PC: volume, brightness, dark mode, wallpaper, Wi-Fi and "
    "Bluetooth, power mode, media playback, apps and windows, clipboard, screenshots, Settings pages. "
    "Prefer these tools over clicking through the UI: they are exact, fast and undoable. Every change "
    "returns {old, new}; hey-claude shows it on the island with an Undo button. Sleep, restart and "
    "shutdown only ever ask (request_power_action): the user must click Confirm on the island."
)


def _sdk():
    try:
        from mcp.server.mcpserver import Image, MCPServer
        from mcp.server.mcpserver.exceptions import ToolError as SdkToolError
    except ImportError:                                     # mcp 1.x
        from mcp.server.fastmcp import FastMCP as MCPServer
        from mcp.server.fastmcp import Image
        from mcp.server.fastmcp.exceptions import ToolError as SdkToolError
    return MCPServer, Image, SdkToolError


def _wrap(fn, sdk_error):
    @functools.wraps(fn)
    def run(*a, **kw):
        from ._util import com_init
        com_init()
        try:
            return fn(*a, **kw)
        except ToolError as e:
            raise sdk_error(str(e)) from None
    run.__signature__ = inspect.signature(fn)
    return run


def build():
    MCPServer, Image, SdkToolError = _sdk()
    server = MCPServer(name="hey-claude windows", instructions=INSTRUCTIONS)
    import os
    from heyclaude.plugins import SKIP_ENV, disabled_from_env, skipped
    skip = disabled_from_env(os.environ.get(SKIP_ENV))      # switched-off plugins' tools (heyclaude/plugins.py)
    for t in registry.TOOLS.values():
        if skipped(t.full_name, skip):
            continue
        doc = (inspect.getdoc(t.func) or t.name).strip()
        if t.experimental:
            doc = "EXPERIMENTAL. " + doc
        if t.name == "screenshot":
            def shot(monitor: int = 0):
                from ._util import com_init
                com_init()
                try:
                    r = registry.call("screenshot", {"monitor": monitor})
                except ToolError as e:
                    raise SdkToolError(str(e)) from None
                # M5b: plus a text block (the size, and a small copy for the island's card:
                # hey-claude only sees a tool's text, never its images)
                import json
                from . import screen
                meta = {"width": r["width"], "height": r["height"], "monitor": monitor,
                        "preview": screen.save_preview(r["image_jpeg"])}
                return [Image(data=r["image_jpeg"], format="jpeg"), json.dumps(meta)]
            server.tool(name="screenshot", description=doc)(shot)
            continue
        server.tool(name=t.name, description=doc)(_wrap(t.func, SdkToolError))
    return server


def main():
    try:
        import ctypes
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))   # real pixels
    except Exception:
        pass
    build().run("stdio")
