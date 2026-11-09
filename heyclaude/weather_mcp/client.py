"""One warm stdio connection to an MCP server, from any thread.

`StdioClient(command, args, env)` starts the server process in a background
thread with its own asyncio loop, initializes the MCP session once and keeps
it open. `call(tool, args, timeout)` (any thread) returns the tool's text, or
raises. A broken connection (the process died, a call failed for a reason
other than a timeout) is reopened in the background with a growing pause
(2 s ... 60 s), so a later call finds it warm again.

Uses the `mcp` Python SDK as a client (1.x and 2.x both work).
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import os
import threading
import time


class ToolFailed(RuntimeError):
    """The server answered the call with isError (bad place, upstream down...)."""


def _is_error(res) -> bool:
    v = getattr(res, "is_error", None)
    if v is None:
        v = getattr(res, "isError", False)
    return bool(v)


class StdioClient:
    def __init__(self, command: str, args: list[str], env: dict[str, str] | None = None, *, cwd: str | None = None,
                 name: str = "mcp", init_timeout: float = 120.0, errlog_path: str | None = None):
        self.command, self.args, self.env, self.cwd = command, list(args), dict(env or {}), cwd
        self.name, self.init_timeout, self.errlog_path = name, float(init_timeout), errlog_path
        self.ready = threading.Event()          # a session is open
        self.tools: list[str] = []
        self.server_info: dict = {}
        self.failures = 0
        self.last_error = ""
        self.started_at: float | None = None
        self.ready_after: float | None = None   # seconds from start() to the first ready
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._session = None
        self._broken: asyncio.Event | None = None
        self._stopping = False

    # -- lifecycle --------------------------------------------------------------
    def start(self) -> "StdioClient":
        if self._thread is not None:
            return self
        self.started_at = time.perf_counter()
        ready = threading.Event()

        def run():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            ready.set()
            self._loop.run_until_complete(self._runner())
        self._thread = threading.Thread(target=run, daemon=True, name=f"{self.name}-mcp")
        self._thread.start()
        ready.wait(5)
        return self

    def close(self):
        self._stopping = True
        if self._loop is not None and self._broken is not None:
            self._loop.call_soon_threadsafe(self._broken.set)
        if self._thread is not None:
            self._thread.join(timeout=5)

    def wait_ready(self, timeout: float) -> bool:
        return self.ready.wait(timeout)

    async def _runner(self):
        delay = 2.0
        while not self._stopping:
            self._broken = asyncio.Event()
            try:
                await self._session_once()
                delay = 2.0
            except Exception as e:                       # the process didn't start, died, or initialize failed
                self.failures += 1
                self.last_error = f"{type(e).__name__}: {e}"[:300]
                if self.failures <= 3 or self.failures % 10 == 0:
                    print(f"[{self.name}] connection failed ({self.failures}): {self.last_error}")
            finally:
                self._session = None
                self.ready.clear()
            if self._stopping:
                break
            await asyncio.sleep(delay)
            delay = min(60.0, delay * 2.5)

    async def _session_once(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(command=self.command, args=self.args, env=self.env or None, cwd=self.cwd)
        err = open(self.errlog_path, "a", encoding="utf-8", errors="replace") if self.errlog_path else open(os.devnull, "w")
        try:
            err.write(f"--- {time.ctime()} start {self.name}\n")
            err.flush()
            async with stdio_client(params, errlog=err) as (r, w):
                async with ClientSession(r, w) as s:
                    init = await asyncio.wait_for(s.initialize(), self.init_timeout)
                    info = getattr(init, "server_info", None) or getattr(init, "serverInfo", None)
                    self.server_info = {"name": getattr(info, "name", ""), "version": getattr(info, "version", "")}
                    tl = await asyncio.wait_for(s.list_tools(), 30)
                    self.tools = [t.name for t in tl.tools]
                    self._session = s
                    if self.ready_after is None and self.started_at is not None:
                        self.ready_after = time.perf_counter() - self.started_at
                    self.ready.set()
                    print(f"[{self.name}] ready: {self.server_info.get('name')} {self.server_info.get('version')}, "
                          f"{len(self.tools)} tools")
                    await self._broken.wait()
        finally:
            err.close()

    # -- calls --------------------------------------------------------------------
    async def acall(self, tool: str, args: dict, timeout: float = 30.0) -> str:
        """On the client's loop: the tool's text (raises ToolFailed / TimeoutError / ConnectionError)."""
        from .parse import text_of
        s = self._session
        if s is None:
            raise ConnectionError(f"{self.name} isn't connected")
        try:
            res = await asyncio.wait_for(s.call_tool(tool, args), timeout)
        except asyncio.TimeoutError:
            raise TimeoutError(f"{tool} took over {timeout:.0f}s") from None
        except Exception as e:
            if self._broken is not None:
                self._broken.set()                     # reopen the connection in the background
            raise ConnectionError(f"{tool}: {type(e).__name__}: {e}") from None
        text = text_of(res)
        if _is_error(res):
            raise ToolFailed(text[:300] or f"{tool} failed")
        return text

    def submit(self, coro) -> concurrent.futures.Future:
        """Run a coroutine on the client's loop; returns a concurrent Future."""
        if self._loop is None:
            raise ConnectionError(f"{self.name} isn't started")
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def call(self, tool: str, args: dict, timeout: float = 30.0) -> str:
        if not self.ready.is_set():
            raise ConnectionError(f"{self.name} isn't connected yet")
        return self.submit(self.acall(tool, args, timeout)).result(timeout + 1)
