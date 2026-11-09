"""The shared "hey claude" model: downloaded on first run, checked with SHA-256.

The model (models/hey_claude.onnx, the default `wake.model`) is trained once
on synthetic voices only (heyclaude.wake_train default) and published as a
GitHub release asset, so new users never train anything. On start-up, if
`wake.model` is that default path and the file isn't there, it's fetched from
the release and only kept if its SHA-256 matches. Any other file at that path
(your own Colab or wizard model) is left alone. If the download fails,
listen.WakeWord falls back to hey_jarvis as before, and the log says why.

Personal models from the Settings wizard live in state/wake_models/ and are
never uploaded anywhere.
"""
from __future__ import annotations

import hashlib
import os
import urllib.request
from pathlib import Path

DEFAULT_REL = "models/hey_claude.onnx"
RELEASE_TAG = "wake-model-v2"
DEFAULT_MODEL = {
    "file": "hey_claude.onnx",
    "url": f"https://github.com/kamol1dn/hey-claude/releases/download/{RELEASE_TAG}/hey_claude.onnx",
    "sha256": "45561a5979e843c0b468b372beab845394d4c0c7cce7d70185d3c29a4fc63d3e",   # filled in by tools/stage_wake_release.py when the asset is staged
    "size": 3295989,
}


def sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 16), b""):
            h.update(b)
    return h.hexdigest()


def is_default_path(model: str, root: Path | None = None) -> bool:
    """Does `wake.model` point at the shared default model's path?"""
    if root is None:
        from .config import ROOT as root
    m = str(model or "").strip()
    if not m.lower().endswith(".onnx"):
        return False
    p = Path(os.path.expandvars(os.path.expanduser(m)))
    if not p.is_absolute():
        p = Path(root) / p
    try:
        return p.resolve() == (Path(root) / DEFAULT_REL).resolve()
    except OSError:
        return False


def download_default(dest: Path, spec: dict = DEFAULT_MODEL, opener=None, timeout: float = 20.0,
                     progress=None) -> tuple[bool, str]:
    """Fetch the release asset to `dest` (atomically) if its SHA-256 matches.
    Returns (ok, message)."""
    if not spec.get("sha256"):
        return False, "no published default model in this version"
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".download")
    open_ = opener or urllib.request.urlopen
    req = urllib.request.Request(spec["url"], headers={"User-Agent": "hey-claude"})
    try:
        with open_(req, timeout=timeout) as r, open(part, "wb") as f:
            total = int(getattr(r, "headers", {}).get("Content-Length") or spec.get("size") or 0)
            got = 0
            while True:
                b = r.read(1 << 15)
                if not b:
                    break
                f.write(b)
                got += len(b)
                if progress:
                    progress(got, total)
    except Exception as e:
        part.unlink(missing_ok=True)
        return False, f"download failed ({e})"
    digest = sha256(part)
    if digest != spec["sha256"]:
        part.unlink(missing_ok=True)
        return False, f"download rejected: SHA-256 {digest[:12]}… doesn't match {spec['sha256'][:12]}…"
    part.replace(dest)
    return True, f"downloaded {dest.name} ({got / 1024:.0f} KB, SHA-256 ok)"


def ensure_default_model(model: str, root: Path | None = None, **kw) -> str | None:
    """Called before the wake word loads. Downloads the shared model when
    `wake.model` is the default path and the file is missing. Returns a log
    line, or None when there was nothing to do."""
    if root is None:
        from .config import ROOT as root
    if not is_default_path(model, root):
        return None
    dest = Path(root) / DEFAULT_REL
    if dest.is_file():
        return None
    ok, msg = download_default(dest, **kw)
    return f"[wake] {msg}" + ("" if ok else f"; {DEFAULT_REL} is missing, so the fallback applies")
