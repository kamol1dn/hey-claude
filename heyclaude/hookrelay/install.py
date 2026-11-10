"""Install / uninstall hey-claude's Claude Code hooks in settings.json (M7).

The writer (docs/m7-hooks-notes.md "Hook installation"):
1. strict parse: a missing file is ``{}``; an unreadable file, invalid JSON
   (including NaN/Infinity and duplicate keys) or a non-object is SettingsError;
   a UTF-8 BOM is stripped (and kept on write);
2. ``plan_install`` / ``plan_uninstall`` return a Plan with a unified diff and a
   fingerprint (sha256 of the exact bytes the plan was based on);
3. ``apply`` refuses when the file changed since the plan;
4. a dated backup ``settings.json.heyclaude-YYYYMMDD-HHMMSS[-N].bak`` created
   exclusively (O_EXCL);
5. a temp file in the target's folder + os.replace, following symlinks (the
   resolved target is written; the link stays a link).

Merging never clobbers: only OUR individual hook entries (type "command" whose
command contains our exact quoted relay path) are removed, then ours appended;
a user's hook sharing a group with ours stays. A ``hooks`` that isn't the
documented shape (event -> list of {matcher?, hooks: [{type, command, ...}]})
is refused. Output: JSON, indent 2, ensure_ascii=False, original key order, the
original's trailing newline / CRLF / BOM kept (a new file gets none, like
Claude Code writes it).

Uninstall restores the exact original bytes while the file is still
byte-identical to what our install wrote (record in relay_dir()/install-record.json:
base64 of the bytes before, sha256 of the bytes after). If the file did not
exist before, that exact restore deletes it. Otherwise it removes only our
entries (and groups / event keys we emptied; ``hooks`` itself only when it is
now empty and the record says it did not exist before).

Design after Coucou (github.com/Louis-CFM/coucou, MIT), minus its group-wide
removal; no code copied.
"""
from __future__ import annotations

import base64
import difflib
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

EVENTS: dict[str, int] = {
    "SessionStart": 10, "SessionEnd": 10, "UserPromptSubmit": 10,
    "PreToolUse": 10, "PostToolUse": 10, "PostToolUseFailure": 10,
    "PermissionRequest": 120,
    "Notification": 10, "Stop": 10, "StopFailure": 10,
    "SubagentStart": 10, "SubagentStop": 10,
}
TOOL_EVENTS = frozenset({"PreToolUse", "PostToolUse", "PostToolUseFailure", "PermissionRequest"})
RELAY_NAME = "heyclaude-hook.py"
RECORD_NAME = "install-record.json"
_SOURCE = Path(__file__).with_name("heyclaude_hook.py")
_BOM = b"\xef\xbb\xbf"
_ABSENT = "absent"


class SettingsError(Exception):
    """settings.json can't be read or changed safely; nothing was written."""


@dataclass
class Plan:
    path: Path
    before: bytes | None
    after: bytes
    diff: str
    fingerprint: str
    action: str                     # "install" | "uninstall"
    delete: bool = False            # uninstall of a file that did not exist before install
    relay: str = ""

    @property
    def changed(self) -> bool:
        if self.delete:
            return self.before is not None
        if self.before is None:
            return self.action == "install"         # uninstall with no file: nothing to do
        return self.after != self.before


# --------------------------------------------------------------------------- paths

def settings_path() -> Path:
    base = os.environ.get("CLAUDE_CONFIG_DIR")
    return (Path(base) if base else Path.home() / ".claude") / "settings.json"


def relay_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    return (Path(base) if base else Path.home() / "AppData" / "Local") / "hey-claude" / "hook"


def _fwd(p) -> str:
    return str(p).replace("\\", "/")


def hook_python() -> str:
    """The real base interpreter (a venv's python.exe is a launcher shim), as
    python.exe even when we run under pythonw.exe (hooks need stdout)."""
    exe = getattr(sys, "_base_executable", None) or sys.executable
    p = Path(exe)
    if p.name.lower() == "pythonw.exe" and p.with_name("python.exe").exists():
        p = p.with_name("python.exe")
    return _fwd(p)


def hook_command(python, relay, event: str) -> str:
    return f'"{_fwd(python)}" -I -S "{_fwd(relay)}" {event}'


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def relay_current(path=None) -> bool:
    path = Path(path) if path else relay_dir() / RELAY_NAME
    try:
        return _sha(path.read_bytes()) == _sha(_SOURCE.read_bytes())
    except OSError:
        return False


def deploy_relay(dest_dir=None) -> Path:
    """Copy heyclaude_hook.py to <dir>/heyclaude-hook.py when its sha256 differs."""
    d = Path(dest_dir) if dest_dir else relay_dir()
    d.mkdir(parents=True, exist_ok=True)
    dest = d / RELAY_NAME
    src = _SOURCE.read_bytes()
    try:
        if _sha(dest.read_bytes()) == _sha(src):
            return dest
    except OSError:
        pass
    fd, tmp = tempfile.mkstemp(dir=d, prefix=RELAY_NAME + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(src)
        os.replace(tmp, dest)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return dest


# --------------------------------------------------------------------------- parsing

def _target(path) -> Path:
    return Path(os.path.realpath(path))


def _read(path) -> bytes | None:
    try:
        return _target(path).read_bytes()
    except FileNotFoundError:
        return None
    except OSError as e:
        raise SettingsError(f"can't read {path}: {e}") from e


def _no_dupes(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            raise SettingsError(f"duplicate key {k!r}")
        out[k] = v
    return out


def _bad_constant(name):
    raise SettingsError(f"invalid JSON constant {name}")


def _parse(data: bytes | None) -> dict:
    if data is None:
        return {}
    if data.startswith(_BOM):
        data = data[len(_BOM):]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as e:
        raise SettingsError(f"settings.json is not UTF-8: {e}") from e
    if not text.strip():
        raise SettingsError("settings.json is empty")
    try:
        obj = json.loads(text, object_pairs_hook=_no_dupes, parse_constant=_bad_constant)
    except ValueError as e:
        raise SettingsError(f"settings.json is not valid JSON: {e}") from e
    if not isinstance(obj, dict):
        raise SettingsError("settings.json is not a JSON object")
    _check_hooks(obj.get("hooks", {}))
    return obj


def _check_hooks(hooks) -> None:
    def bad(why):
        raise SettingsError(f"settings.json 'hooks' has an unexpected shape ({why}); not touching it")

    if not isinstance(hooks, dict):
        bad("not an object")
    for ev, groups in hooks.items():
        if not isinstance(groups, list):
            bad(f"{ev} is not a list")
        for g in groups:
            if not isinstance(g, dict):
                bad(f"a {ev} entry is not an object")
            if "matcher" in g and not isinstance(g["matcher"], str):
                bad(f"a {ev} matcher is not a string")
            if not isinstance(g.get("hooks"), list):
                bad(f"a {ev} entry has no 'hooks' list")
            for h in g["hooks"]:
                if not isinstance(h, dict) or not isinstance(h.get("type"), str):
                    bad(f"a {ev} hook is not an object with a 'type'")
                if h["type"] == "command" and not isinstance(h.get("command"), str):
                    bad(f"a {ev} command hook has no 'command' string")


def _dump(obj: dict, like: bytes | None) -> bytes:
    text = json.dumps(obj, indent=2, ensure_ascii=False)
    if like is not None:
        body = like[len(_BOM):] if like.startswith(_BOM) else like
        if body.endswith(b"\n"):
            text += "\n"
        if b"\r\n" in body:
            text = text.replace("\n", "\r\n")
    out = text.encode("utf-8")
    if like is not None and like.startswith(_BOM):
        out = _BOM + out
    return out


def _diff(path, before: bytes | None, after: bytes | None) -> str:
    def lines(b):
        if b is None:
            return []
        if b.startswith(_BOM):
            b = b[len(_BOM):]
        return b.decode("utf-8", "replace").replace("\r\n", "\n").splitlines(keepends=True)
    name = _fwd(path)
    out = "".join(difflib.unified_diff(lines(before), lines(after),
                                       fromfile=name if before is not None else "/dev/null",
                                       tofile=name if after is not None else "/dev/null"))
    if not out and before != after:                 # never an empty diff for a real change
        out = (f"--- {name if before is not None else '/dev/null'}\n+++ {name if after is not None else '/dev/null'}\n"
               "(only the byte-order mark, line endings or file presence change)\n")
    return out


def _fingerprint(data: bytes | None) -> str:
    return _ABSENT if data is None else _sha(data)


# --------------------------------------------------------------------------- merging

def _ours(hook, relay) -> bool:
    if not isinstance(hook, dict) or hook.get("type") != "command":
        return False
    cmd = hook.get("command")
    return isinstance(cmd, str) and f'"{_fwd(relay)}"'.lower() in cmd.replace("\\", "/").lower()


def _desired_group(event, python, relay) -> dict:
    hook = {"type": "command", "command": hook_command(python, relay, event), "timeout": EVENTS[event]}
    return {"matcher": "*", "hooks": [hook]} if event in TOOL_EVENTS else {"hooks": [hook]}


def _strip(groups: list, relay) -> tuple[list, bool]:
    """groups without our hook entries (groups we emptied dropped); whether we found any."""
    out, found = [], False
    for g in groups:
        keep = [h for h in g["hooks"] if not _ours(h, relay)]
        if len(keep) == len(g["hooks"]):
            out.append(g)
            continue
        found = True
        if keep:
            g = dict(g)
            g["hooks"] = keep
            out.append(g)
    return out, found


def _installed_ok(groups: list, desired: dict, relay) -> bool:
    n = sum(1 for g in groups for h in g["hooks"] if _ours(h, relay))
    return n == 1 and desired in groups


def _merge(obj: dict, python, relay) -> dict:
    obj = dict(obj)
    hooks = dict(obj.get("hooks", {}))
    for ev in list(hooks):
        if ev in EVENTS and _installed_ok(hooks[ev], _desired_group(ev, python, relay), relay):
            continue
        groups, found = _strip(hooks[ev], relay)
        if found and not groups and ev not in EVENTS:
            del hooks[ev]
        else:
            hooks[ev] = groups
    for ev in EVENTS:
        desired = _desired_group(ev, python, relay)
        groups = hooks.get(ev, [])
        if not _installed_ok(groups, desired, relay):
            hooks[ev] = groups + [desired]
    obj["hooks"] = hooks
    return obj


def _unmerge(obj: dict, relay, drop_empty_hooks: bool) -> dict:
    if not isinstance(obj.get("hooks"), dict):
        return obj
    obj = dict(obj)
    hooks = dict(obj["hooks"])
    for ev in list(hooks):
        groups, found = _strip(hooks[ev], relay)
        if found and not groups:
            del hooks[ev]
        else:
            hooks[ev] = groups
    if not hooks and drop_empty_hooks:
        del obj["hooks"]
    else:
        obj["hooks"] = hooks
    return obj


# --------------------------------------------------------------------------- record

def _record_file(record_dir) -> Path:
    return Path(record_dir or relay_dir()) / RECORD_NAME


def _record_key(path) -> str:
    return os.path.normcase(str(_target(path)))


def _load_records(record_dir) -> dict:
    try:
        data = json.loads(_record_file(record_dir).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and isinstance(data.get("entries"), dict) else {"entries": {}}
    except (OSError, ValueError):
        return {"entries": {}}


def _save_records(record_dir, records: dict) -> None:
    f = _record_file(record_dir)
    if not records["entries"]:
        try:
            f.unlink()
        except FileNotFoundError:
            pass
        return
    f.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=f.parent, prefix=RECORD_NAME + ".", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({"version": 1, **records}, fh, indent=2)
    os.replace(tmp, f)


# --------------------------------------------------------------------------- plans

def _default_relay(relay):
    return _fwd(relay) if relay else _fwd(relay_dir() / RELAY_NAME)


def plan_install(path=None, python=None, relay=None) -> Plan:
    path = Path(path) if path else settings_path()
    relay = _default_relay(relay)
    before = _read(path)
    obj = _parse(before)
    after_obj = _merge(obj, python or hook_python(), relay)
    after = before if (before is not None and after_obj == obj) else _dump(after_obj, before)
    return Plan(path=path, before=before, after=after, diff=_diff(path, before, after),
                fingerprint=_fingerprint(before), action="install", relay=relay)


def plan_uninstall(path=None, relay=None, record_dir=None) -> Plan:
    path = Path(path) if path else settings_path()
    relay = _default_relay(relay)
    before = _read(path)
    fp = _fingerprint(before)
    if before is None:
        return Plan(path, None, b"", "", fp, "uninstall", relay=relay)
    entry = _load_records(record_dir)["entries"].get(_record_key(path))
    if entry and entry.get("after_sha256") == _sha(before):
        orig = entry.get("before")
        if orig is None:                                 # the file was ours alone: remove it
            return Plan(path, before, b"", _diff(path, before, None), fp, "uninstall", delete=True, relay=relay)
        orig = base64.b64decode(orig)
        return Plan(path, before, orig, _diff(path, before, orig), fp, "uninstall", relay=relay)
    obj = _parse(before)
    drop = bool(entry) and entry.get("had_hooks") is False
    after_obj = _unmerge(obj, relay, drop)
    after = before if after_obj == obj else _dump(after_obj, before)
    return Plan(path, before, after, _diff(path, before, after), fp, "uninstall", relay=relay)


def _backup(target: Path, data: bytes) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    for i in range(1000):
        name = f"{target.name}.heyclaude-{stamp}{'' if i == 0 else '-%d' % i}.bak"
        b = target.with_name(name)
        try:
            fd = os.open(b, flags, 0o600)
        except FileExistsError:
            continue
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        return b
    raise SettingsError("could not create a backup file")


def apply(plan: Plan, record_dir=None) -> Path | None:
    """Write the plan. Refuses (SettingsError) when the file changed since the plan.
    Returns the backup's path (None when there was no file before or nothing changed)."""
    target = _target(plan.path)
    current = _read(plan.path)
    if _fingerprint(current) != plan.fingerprint:
        raise SettingsError(f"{plan.path} changed since the plan was made; nothing written")
    records = _load_records(record_dir)
    key = _record_key(plan.path)
    if not plan.changed:
        if plan.action == "uninstall":
            records["entries"].pop(key, None)
            _save_records(record_dir, records)
        return None
    backup = _backup(target, current) if current is not None else None
    if plan.delete:
        os.remove(target)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=target.name + ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(plan.after)
                f.flush()
                os.fsync(f.fileno())
            try:
                shutil.copymode(target, tmp)
            except OSError:
                pass
            os.replace(tmp, target)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    if plan.action == "install":
        prev = records["entries"].get(key)
        if prev and current is not None and prev.get("after_sha256") == _sha(current):
            orig, had = prev.get("before"), prev.get("had_hooks")      # keep the pre-hey-claude original
        else:
            orig = None if current is None else base64.b64encode(current).decode("ascii")
            had = current is not None and "hooks" in _parse(current)
        records["entries"][key] = {"settings": str(target), "before": orig, "had_hooks": had,
                                   "after_sha256": _sha(plan.after), "relay": plan.relay,
                                   "time": time.strftime("%Y-%m-%dT%H:%M:%S")}
    else:
        records["entries"].pop(key, None)
    _save_records(record_dir, records)
    return backup


def status(path=None, relay=None) -> dict:
    path = Path(path) if path else settings_path()
    relay = _default_relay(relay)
    out = {"installed": False, "events": [], "relay": relay, "relay_current": relay_current(relay)}
    try:
        obj = _parse(_read(path))
    except SettingsError as e:
        out["error"] = str(e)
        return out
    hooks = obj.get("hooks", {})
    out["events"] = [ev for ev, groups in hooks.items()
                     if any(_ours(h, relay) for g in groups for h in g["hooks"])]
    out["installed"] = set(EVENTS) <= set(out["events"])
    return out
