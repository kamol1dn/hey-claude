"""hey-claude's end of the hook relay: a named-pipe server (M7).

``\\\\.\\pipe\\hey-claude-<user SID>``, one JSON line each way (see
heyclaude/hookrelay/heyclaude_hook.py for the relay side).

- An explicit DACL granting only the current user (``D:P(A;;GA;;;<SID>)``) and
  PIPE_REJECT_REMOTE_CLIENTS. The first instance is created with
  FILE_FLAG_FIRST_PIPE_INSTANCE: if that fails, another hey-claude owns the name
  and start() returns False.
- A fresh instance per connection and after every failed connect. Each accepted
  connection goes to its own worker thread, which checks that the client process
  runs as our user (else drops it), reads one line (capped at 1.5 MB; malformed
  JSON is dropped) and calls ``handler(msg, conn)``.
- The connection is closed after the handler returns, for every message. A
  handler for a ``wants_reply`` message therefore replies (``conn.reply``) before
  it returns; ``conn.closed()`` tells it when the relay went away meanwhile.
- All I/O is overlapped, so stop() just signals an event: the pending connect and
  any reads return at once.

ctypes only. Design after Coucou (github.com/Louis-CFM/coucou, MIT), with the DACL
and client check it lacks (their #1076); no code copied.
"""
from __future__ import annotations

import json
import logging
import sys
import threading
import time

log = logging.getLogger(__name__)

LINE_CAP = 1_572_864            # ~1.5 MB
FIRST_LINE_TIMEOUT = 10.0       # a relay writes its line right after connecting
REPLY_TIMEOUT = 5.0

_PIPE_ACCESS_DUPLEX = 0x3
_FILE_FLAG_FIRST_PIPE_INSTANCE = 0x00080000
_FILE_FLAG_OVERLAPPED = 0x40000000
_PIPE_REJECT_REMOTE_CLIENTS = 0x8
_PIPE_UNLIMITED_INSTANCES = 255
_ERROR_IO_PENDING = 997
_ERROR_PIPE_CONNECTED = 535
_ERROR_BROKEN_PIPE = 109
_ERROR_PIPE_NOT_CONNECTED = 233
_ERROR_NO_DATA = 232
_ERROR_PIPE_BUSY = 231
_WAIT_OBJECT_0 = 0

_W = None


def _win():
    global _W
    if _W is not None:
        return _W
    import ctypes
    from ctypes import wintypes as wt

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    adv = ctypes.WinDLL("advapi32", use_last_error=True)

    class OVERLAPPED(ctypes.Structure):
        _fields_ = [("Internal", ctypes.c_void_p), ("InternalHigh", ctypes.c_void_p),
                    ("Offset", wt.DWORD), ("OffsetHigh", wt.DWORD), ("hEvent", wt.HANDLE)]

    class SECURITY_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("nLength", wt.DWORD), ("lpSecurityDescriptor", ctypes.c_void_p),
                    ("bInheritHandle", wt.BOOL)]

    def fn(dll, name, res, *args):
        f = getattr(dll, name)
        f.restype = res
        f.argtypes = list(args)
        return f

    H, B, D, P = wt.HANDLE, wt.BOOL, wt.DWORD, ctypes.c_void_p
    LPD = ctypes.POINTER(D)
    POV = ctypes.POINTER(OVERLAPPED)
    ns = type("W", (), {})()
    ns.ct = ctypes
    ns.OVERLAPPED = OVERLAPPED
    ns.SECURITY_ATTRIBUTES = SECURITY_ATTRIBUTES
    ns.CreateNamedPipeW = fn(k32, "CreateNamedPipeW", H, wt.LPCWSTR, D, D, D, D, D, D,
                             ctypes.POINTER(SECURITY_ATTRIBUTES))
    ns.ConnectNamedPipe = fn(k32, "ConnectNamedPipe", B, H, POV)
    ns.CreateFileW = fn(k32, "CreateFileW", H, wt.LPCWSTR, D, D, P, D, D, H)
    ns.WaitNamedPipeW = fn(k32, "WaitNamedPipeW", B, wt.LPCWSTR, D)
    ns.CloseHandle = fn(k32, "CloseHandle", B, H)
    ns.ReadFile = fn(k32, "ReadFile", B, H, P, D, LPD, POV)
    ns.WriteFile = fn(k32, "WriteFile", B, H, P, D, LPD, POV)
    ns.GetOverlappedResult = fn(k32, "GetOverlappedResult", B, H, POV, LPD, B)
    ns.CancelIoEx = fn(k32, "CancelIoEx", B, H, POV)
    ns.CreateEventW = fn(k32, "CreateEventW", H, P, B, B, wt.LPCWSTR)
    ns.SetEvent = fn(k32, "SetEvent", B, H)
    ns.WaitForMultipleObjects = fn(k32, "WaitForMultipleObjects", D, D, ctypes.POINTER(H), B, D)
    ns.PeekNamedPipe = fn(k32, "PeekNamedPipe", B, H, P, D, LPD, LPD, LPD)
    ns.GetNamedPipeClientProcessId = fn(k32, "GetNamedPipeClientProcessId", B, H, ctypes.POINTER(wt.ULONG))
    ns.OpenProcess = fn(k32, "OpenProcess", H, D, B, D)
    ns.GetCurrentProcess = fn(k32, "GetCurrentProcess", H)
    ns.LocalFree = fn(k32, "LocalFree", P, P)
    ns.OpenProcessToken = fn(adv, "OpenProcessToken", B, H, D, ctypes.POINTER(H))
    ns.GetTokenInformation = fn(adv, "GetTokenInformation", B, H, ctypes.c_int, P, D, LPD)
    ns.ConvertSidToStringSidW = fn(adv, "ConvertSidToStringSidW", B, P, ctypes.POINTER(wt.LPWSTR))
    ns.ConvertStringSecurityDescriptorToSecurityDescriptorW = fn(
        adv, "ConvertStringSecurityDescriptorToSecurityDescriptorW", B, wt.LPCWSTR, D,
        ctypes.POINTER(P), LPD)
    ns.INVALID = ctypes.c_void_p(-1).value
    _W = ns
    return ns


def _valid(h) -> bool:
    return bool(h) and h != _win().INVALID


# --------------------------------------------------------------------------- identity

def _process_sid(hproc) -> str | None:
    w = _win()
    ct = w.ct
    tok = ct.c_void_p()
    if not w.OpenProcessToken(hproc, 0x0008, ct.byref(tok)):        # TOKEN_QUERY
        return None
    try:
        need = ct.c_ulong(0)
        w.GetTokenInformation(tok, 1, None, 0, ct.byref(need))       # TokenUser
        if not need.value:
            return None
        buf = ct.create_string_buffer(need.value)
        if not w.GetTokenInformation(tok, 1, buf, need.value, ct.byref(need)):
            return None
        psid = ct.c_void_p.from_buffer(buf).value
        s = ct.c_wchar_p()
        if not w.ConvertSidToStringSidW(psid, ct.byref(s)):
            return None
        try:
            return s.value
        finally:
            w.LocalFree(s)
    finally:
        w.CloseHandle(tok)


_SID: list[str] = []


def current_user_sid() -> str:
    """The current process's user SID string (S-1-5-21-...)."""
    if not _SID:
        sid = _process_sid(_win().GetCurrentProcess())
        if not sid:
            raise OSError("cannot read the current user's SID")
        _SID.append(sid)
    return _SID[0]


def pid_sid(pid: int) -> str | None:
    w = _win()
    h = w.OpenProcess(0x1000, False, int(pid))                      # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return None
    try:
        return _process_sid(h)
    finally:
        w.CloseHandle(h)


def pipe_name() -> str:
    return "\\\\.\\pipe\\hey-claude-" + current_user_sid()


# --------------------------------------------------------------------------- overlapped I/O

class _Stopped(Exception):
    pass


def _io(h, write: bool, buf, n: int, timeout: float, stop_ev=None) -> int:
    """One overlapped ReadFile/WriteFile, waited on for at most `timeout` s (or until
    stop_ev is signalled). Returns the byte count; 0 at EOF / broken pipe."""
    w = _win()
    ct = w.ct
    ev = w.CreateEventW(None, True, False, None)
    if not ev:
        raise OSError("CreateEvent failed")
    ov = w.OVERLAPPED()
    ov.hEvent = ev
    got = ct.c_ulong(0)
    try:
        ok = (w.WriteFile if write else w.ReadFile)(h, buf, n, None, ct.byref(ov))
        if not ok:
            err = ct.get_last_error()
            if err in (_ERROR_BROKEN_PIPE, _ERROR_PIPE_NOT_CONNECTED, _ERROR_NO_DATA):
                return 0
            if err != _ERROR_IO_PENDING:
                raise OSError(err, "pipe I/O failed")
        handles = (ct.c_void_p * 2)(ev, stop_ev) if stop_ev else (ct.c_void_p * 1)(ev)
        r = w.WaitForMultipleObjects(len(handles), handles, False, max(0, int(timeout * 1000)))
        if r != _WAIT_OBJECT_0:
            w.CancelIoEx(h, ct.byref(ov))
            w.GetOverlappedResult(h, ct.byref(ov), ct.byref(got), True)
            raise _Stopped() if r == 1 else TimeoutError()
        if not w.GetOverlappedResult(h, ct.byref(ov), ct.byref(got), False):
            err = ct.get_last_error()
            if err in (_ERROR_BROKEN_PIPE, _ERROR_PIPE_NOT_CONNECTED, _ERROR_NO_DATA):
                return 0
            raise OSError(err, "pipe I/O failed")
        return got.value
    finally:
        w.CloseHandle(ev)


def _write_all(h, data: bytes, timeout: float, stop_ev=None) -> bool:
    w = _win()
    buf = w.ct.create_string_buffer(data, len(data))
    end = time.monotonic() + timeout
    off = 0
    while off < len(data):
        n = _io(h, True, w.ct.byref(buf, off), len(data) - off, max(0.0, end - time.monotonic()), stop_ev)
        if n <= 0:
            return False
        off += n
    return True


def _read_line(h, cap: int, timeout: float, stop_ev=None) -> bytes | None:
    """One '\\n'-terminated line (without it), or None (EOF first, over cap, timeout)."""
    w = _win()
    buf = w.ct.create_string_buffer(65536)
    data = bytearray()
    end = time.monotonic() + timeout
    while True:
        n = _io(h, False, buf, 65536, max(0.0, end - time.monotonic()), stop_ev)
        if n <= 0:
            return None
        data += buf.raw[:n]
        i = data.find(b"\n")
        if i >= 0:
            return bytes(data[:i]) if i <= cap else None
        if len(data) > cap:
            return None


# --------------------------------------------------------------------------- server

class Connection:
    """One relay's connection, handed to the handler. Thread-safe."""

    def __init__(self, handle, client_pid: int, stop_ev=None):
        self._h = handle
        self.client_pid = client_pid
        self._stop_ev = stop_ev
        self._lock = threading.Lock()

    def reply(self, obj: dict) -> bool:
        """Send the one reply line. False if the relay is gone or the write failed."""
        try:
            data = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
        except (TypeError, ValueError):
            return False
        with self._lock:
            if self._h is None:
                return False
            try:
                return _write_all(self._h, data, REPLY_TIMEOUT, self._stop_ev)
            except Exception:
                return False

    def closed(self) -> bool:
        """True once the relay went away (EOF / broken pipe) or we closed it; does not
        consume anything."""
        with self._lock:
            if self._h is None:
                return True
            w = _win()
            avail = w.ct.c_ulong(0)
            if w.PeekNamedPipe(self._h, None, 0, None, w.ct.byref(avail), None):
                return False
            return True

    def close(self) -> None:
        """Close the server end. Unread reply data stays readable by the relay
        (CloseHandle, not DisconnectNamedPipe, which would discard it)."""
        with self._lock:
            if self._h is not None:
                _win().CloseHandle(self._h)
                self._h = None


class PipeServer:
    """The app's pipe server: ``handler(msg: dict, conn: Connection)`` runs on a
    worker thread per connection; the connection closes when it returns."""

    def __init__(self, handler, name: str | None = None):
        self.handler = handler
        self.name = name
        self._thread: threading.Thread | None = None
        self._stop_ev = None
        self._sa = None
        self._sd = None
        self._pending = None
        self._conns: set[Connection] = set()
        self._lock = threading.Lock()
        self._workers: set[threading.Thread] = set()

    # -- setup
    def _security(self):
        w = _win()
        ct = w.ct
        sd = ct.c_void_p()
        sddl = "D:P(A;;GA;;;%s)" % current_user_sid()
        if not w.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ct.byref(sd), None):
            raise OSError(ct.get_last_error(), "security descriptor")
        self._sd = sd
        sa = w.SECURITY_ATTRIBUTES()
        sa.nLength = ct.sizeof(sa)
        sa.lpSecurityDescriptor = sd.value
        sa.bInheritHandle = False
        self._sa = sa

    def _instance(self, first: bool = False):
        w = _win()
        mode = _PIPE_ACCESS_DUPLEX | _FILE_FLAG_OVERLAPPED | (_FILE_FLAG_FIRST_PIPE_INSTANCE if first else 0)
        h = w.CreateNamedPipeW(self.name, mode, _PIPE_REJECT_REMOTE_CLIENTS,   # byte mode, wait mode
                               _PIPE_UNLIMITED_INSTANCES, 65536, 65536, 0, w.ct.byref(self._sa))
        return h if _valid(h) else None

    def start(self) -> bool:
        if sys.platform != "win32":
            return False
        if self._thread is not None:
            return True
        try:
            if self.name is None:
                self.name = pipe_name()
            self._security()
            first = self._instance(first=True)
        except Exception:
            log.exception("hook pipe: setup failed")
            return False
        if first is None:
            log.info("hook pipe: %s is owned by another process", self.name)
            self._free_sd()
            return False
        w = _win()
        self._stop_ev = w.CreateEventW(None, True, False, None)
        self._pending = first
        self._thread = threading.Thread(target=self._listen, name="hook-pipe", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        t = self._thread
        if t is None:
            return
        w = _win()
        w.SetEvent(self._stop_ev)
        t.join(2.0)
        with self._lock:
            conns = list(self._conns)
            workers = list(self._workers)
        for c in conns:
            c.close()                                    # relays see EOF: they print nothing
        end = time.monotonic() + 1.0
        for wt_ in workers:
            wt_.join(max(0.0, end - time.monotonic()))
        if self._pending is not None:
            w.CloseHandle(self._pending)
            self._pending = None
        self._thread = None
        if not any(wt_.is_alive() for wt_ in workers):   # a stuck handler may still hold the event
            w.CloseHandle(self._stop_ev)
        self._stop_ev = None
        self._free_sd()

    def _free_sd(self):
        if self._sd is not None and self._sd.value:
            _win().LocalFree(self._sd)
        self._sd = None
        self._sa = None

    def _stopping(self) -> bool:
        return self._stop_ev is None or _win().WaitForMultipleObjects(
            1, (_win().ct.c_void_p * 1)(self._stop_ev), False, 0) == _WAIT_OBJECT_0

    # -- accept loop
    def _connect(self, h) -> bool | None:
        """Wait for a client on instance h: True connected, False failed, None stopping."""
        w = _win()
        ct = w.ct
        ev = w.CreateEventW(None, True, False, None)
        ov = w.OVERLAPPED()
        ov.hEvent = ev
        got = ct.c_ulong(0)
        try:
            if w.ConnectNamedPipe(h, ct.byref(ov)):
                return True
            err = ct.get_last_error()
            if err in (_ERROR_PIPE_CONNECTED, _ERROR_NO_DATA):
                return True                     # NO_DATA: it connected, wrote and closed already

            if err != _ERROR_IO_PENDING:
                return False
            handles = (ct.c_void_p * 2)(ev, self._stop_ev)
            r = w.WaitForMultipleObjects(2, handles, False, 0xFFFFFFFF)
            if r != _WAIT_OBJECT_0:
                w.CancelIoEx(h, ct.byref(ov))
                w.GetOverlappedResult(h, ct.byref(ov), ct.byref(got), True)
                return None
            return bool(w.GetOverlappedResult(h, ct.byref(ov), ct.byref(got), False))
        finally:
            w.CloseHandle(ev)

    def _listen(self):
        w = _win()
        while not self._stopping():
            h = self._pending
            if h is None:
                h = self._instance()
                if h is None:
                    log.warning("hook pipe: CreateNamedPipe failed (%s)", w.ct.get_last_error())
                    if w.WaitForMultipleObjects(1, (w.ct.c_void_p * 1)(self._stop_ev), False, 200) == 0:
                        return
                    continue
                self._pending = h
            ok = self._connect(h)
            if ok is None:
                return                                   # stop(): the pending instance is closed there
            self._pending = None
            if not ok:
                w.CloseHandle(h)                         # a fresh instance after every failed connect
                continue
            self._pending = self._instance()             # keep one instance listening at all times
            t = threading.Thread(target=self._serve, args=(h,), name="hook-pipe-conn", daemon=True)
            with self._lock:
                self._workers.add(t)
            t.start()

    # -- one connection
    def _serve(self, h):
        w = _win()
        conn = None
        try:
            pid = w.ct.c_ulong(0)
            if not w.GetNamedPipeClientProcessId(h, w.ct.byref(pid)):
                return
            if pid_sid(pid.value) != current_user_sid():
                log.warning("hook pipe: dropped a client (pid %s) not running as this user", pid.value)
                return
            try:
                line = _read_line(h, LINE_CAP, FIRST_LINE_TIMEOUT, self._stop_ev)
            except (_Stopped, TimeoutError, OSError):
                return
            if not line:
                return
            try:
                msg = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                log.debug("hook pipe: malformed line dropped")
                return
            if not isinstance(msg, dict):
                return
            conn = Connection(h, pid.value, self._stop_ev)
            h = None
            with self._lock:
                self._conns.add(conn)
            try:
                self.handler(msg, conn)
            except Exception:
                log.exception("hook pipe: handler failed")
        except Exception:
            log.exception("hook pipe: connection failed")
        finally:
            if conn is not None:
                conn.close()
                with self._lock:
                    self._conns.discard(conn)
            elif h is not None:
                w.CloseHandle(h)
            with self._lock:
                self._workers.discard(threading.current_thread())


# --------------------------------------------------------------------------- test client

def send(msg: dict, name: str | None = None, wait_reply: bool = False, timeout: float = 5.0):
    """A tiny client for tests and diagnostics (the relay has its own): connect, send
    one line, optionally read the reply line. Returns the reply dict (wait_reply), True
    when sent, or None on any failure."""
    if sys.platform != "win32":
        return None
    w = _win()
    name = name or pipe_name()
    end = time.monotonic() + timeout
    while True:
        h = w.CreateFileW(name, 0xC0000000, 0, None, 3, _FILE_FLAG_OVERLAPPED, None)
        if _valid(h):
            break
        if w.ct.get_last_error() != _ERROR_PIPE_BUSY:
            return None
        left = end - time.monotonic()
        if left <= 0 or not w.WaitNamedPipeW(name, max(1, int(left * 1000))):
            return None
    try:
        data = json.dumps(msg, ensure_ascii=False).encode("utf-8") + b"\n"
        if not _write_all(h, data, max(0.0, end - time.monotonic())):
            return None
        if not wait_reply:
            return True
        line = _read_line(h, LINE_CAP, max(0.0, end - time.monotonic()))
        if line is None:
            return None
        try:
            out = json.loads(line.decode("utf-8"))
        except ValueError:
            return None
        return out if isinstance(out, dict) else None
    except Exception:
        return None
    finally:
        w.CloseHandle(h)
