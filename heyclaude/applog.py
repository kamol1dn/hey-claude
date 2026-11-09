"""App log: everything printed (and every uncaught exception) also goes to
state/logs/app.log, rotated at 1 MB with 3 old files kept.

The code base reports with print(); rather than rewrite that, stdout and
stderr are wrapped so each line is written to the console as before (when
there is one; under pythonw there isn't) and to the log file with a timestamp.
"""
from __future__ import annotations

import logging
import logging.handlers
import sys
import threading

from .config import LOG_DIR

log = logging.getLogger("heyclaude")


class _Tee:
    """File-like: passes text to the original stream and logs complete lines."""

    def __init__(self, orig, level: int):
        self.orig, self.level = orig, level
        self._buf = ""
        self._lock = threading.Lock()

    def write(self, s):
        if self.orig is not None:
            try:
                self.orig.write(s)
            except Exception:
                pass
        with self._lock:
            self._buf += s
            if "\n" not in self._buf:
                if "\r" in self._buf:           # progress bars: keep only the latest frame
                    self._buf = self._buf.rsplit("\r", 1)[1]
                return len(s)
            *lines, self._buf = self._buf.split("\n")
        for line in lines:
            line = line.rsplit("\r", 1)[-1].rstrip()
            if line:
                log.log(self.level, line)
        return len(s)

    def flush(self):
        if self.orig is not None:
            try:
                self.orig.flush()
            except Exception:
                pass

    def isatty(self):
        return bool(self.orig is not None and getattr(self.orig, "isatty", lambda: False)())

    @property
    def encoding(self):
        return getattr(self.orig, "encoding", None) or "utf-8"

    def fileno(self):
        if self.orig is None:
            raise OSError("no console")
        return self.orig.fileno()


_done = False


def setup(max_bytes: int = 1_000_000, backups: int = 3) -> str:
    """Idempotent. Returns the log file path."""
    global _done
    path = LOG_DIR / "app.log"
    if _done:
        return str(path)
    _done = True
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    h = logging.handlers.RotatingFileHandler(path, maxBytes=max_bytes, backupCount=backups, encoding="utf-8",
                                             delay=False)
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)-5s %(threadName)s: %(message)s"))
    log.addHandler(h)
    log.setLevel(logging.INFO)
    log.propagate = False
    sys.stdout = _Tee(sys.stdout, logging.INFO)
    sys.stderr = _Tee(sys.stderr, logging.WARNING)

    def hook(exc_type, exc, tb):
        log.error("uncaught exception", exc_info=(exc_type, exc, tb))
        if sys.__stderr__ is not None:
            sys.__excepthook__(exc_type, exc, tb)

    def thread_hook(args):
        if args.exc_type is SystemExit:
            return
        log.error("uncaught exception in thread %s", getattr(args.thread, "name", "?"),
                  exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    sys.excepthook = hook
    threading.excepthook = thread_hook
    log.info("---- hey-claude starting (python %s) ----", sys.version.split()[0])
    return str(path)
