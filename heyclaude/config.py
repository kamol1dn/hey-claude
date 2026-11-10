"""Config loading and saving: config.yaml merged over config.example.yaml defaults.

The settings window edits the merged config and writes it back with `save()`.
`apply_in_place()` copies new values into the dicts the running app already
holds, so settings that are read on every use (wake threshold, listen timings,
task permissions, projects for routing...) take effect without a restart.
`needs_restart()` says which changed keys only take effect after one.
"""
from __future__ import annotations

import copy
import os
import shutil
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = ROOT / "state"
LOG_DIR = STATE_DIR / "logs"
EXAMPLE = ROOT / "config.example.yaml"
USER = ROOT / "config.yaml"

HEADER = ("# hey-claude settings. Written by the Settings window; edit by hand if you like.\n"
          "# Every key is explained in config.example.yaml.\n\n")

# Dotted keys (or section prefixes ending in ".") that the running app picks up
# without a restart. Everything else needs one.
LIVE = (
    "wake.model", "wake.threshold", "wake.cooldown", "wake.suppress_while_speaking", "wake.speaking_tail_s",
    "stt.yield_to_premiere", "stt.gpu_busy_pct", "stt.gpu_resume_pct", "stt.cpu_busy_pct",  # read per request
    "listen.",                       # read on every utterance (device: next time the mic opens)
    "tasks.",                        # read when each task starts
    "projects",                      # spoken routing; whisper hints + quick-model prompt after restart
    "agent.filler",
    "agent.filler_min_words",
    "agent.filler_after_s",
    "tts.voice", "tts.speed", "tts.first_chunk_words",   # read per sentence
    "conversation.",                 # read per request (save: the agent's next lookup)
    "island.",
    "instant.",                      # M10: read on every request (the classifier's threshold too)
    "hooks.approvals", "hooks.approval_timeout_s", "hooks.voice_approvals", "hooks.done_flash",  # M7: per request
    "app.",                          # hotkey re-registered, autostart toggled, guard re-read
    "weather.local_route", "weather.mcp.timeout_s", "weather.mcp.fallback", "weather.mcp.cache_s",  # per question
)


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict) and k != "projects":
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def defaults() -> dict:
    with open(EXAMPLE, encoding="utf-8") as f:
        return yaml.safe_load(f)


def user_path(path: str | os.PathLike | None = None) -> Path:
    return Path(path) if path else USER


def _norm_projects(p) -> dict:
    return {" ".join(str(k).lower().split()): str(v) for k, v in (p or {}).items() if str(k).strip()}


def _migrate(user: dict) -> dict:
    """Older config.yaml keys -> current ones."""
    s = user.get("stt")
    if isinstance(s, dict):
        if "model" in s:                         # the single (CPU) model of v1
            s.setdefault("cpu_model", s.pop("model"))
        if "compute_type" in s:
            s.setdefault("cpu_compute_type", s.pop("compute_type"))
        if str(s.get("device", "")).lower().startswith("cuda"):
            s["device"] = "gpu"
    a = user.get("app")
    if isinstance(a, dict):
        a.pop("premiere_guard", None)            # replaced by the load-based choice in stt
    t = user.get("tts")
    if isinstance(t, dict) and "backend" in t and "engine" not in t:
        # Before M4: `backend` (sapi default), `voice` = SAPI voice, `kokoro_voice`.
        # The settings window wrote the default sapi into every config.yaml, so
        # it isn't a choice: move to Kokoro (falls back to SAPI by itself), keep piper.
        b = str(t.pop("backend") or "sapi").lower()
        t["engine"] = "piper" if b == "piper" else "kokoro"
        if "voice" in t:
            t["sapi_voice"] = t.pop("voice")
        kv = t.pop("kokoro_voice", None)
        if kv:
            t["voice"] = kv
    return user


def load(path: str | os.PathLike | None = None) -> dict:
    cfg = defaults()
    user = user_path(path)
    if user.exists():
        with open(user, encoding="utf-8") as f:
            cfg = _merge(cfg, _migrate(yaml.safe_load(f) or {}))
    # Normalise project keys: lowercase, single spaces.
    cfg["projects"] = _norm_projects(cfg.get("projects"))
    STATE_DIR.mkdir(exist_ok=True)
    LOG_DIR.mkdir(exist_ok=True)
    return cfg


def ensure_user_config(path: str | os.PathLike | None = None) -> Path:
    """Create config.yaml from config.example.yaml (comments included) if it's missing."""
    user = user_path(path)
    if not user.exists():
        shutil.copyfile(EXAMPLE, user)
    return user


def save(cfg: dict, path: str | os.PathLike | None = None) -> Path:
    """Write the whole config to config.yaml (atomic replace)."""
    user = user_path(path)
    data = copy.deepcopy(cfg)
    data["projects"] = _norm_projects(data.get("projects"))
    text = HEADER + yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False)
    tmp = user.with_name(user.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, user)
    return user


def diff(old: dict, new: dict, prefix: str = "") -> list[str]:
    """Dotted keys whose value differs. `projects` counts as one key."""
    out = []
    for k in sorted(set(old) | set(new), key=str):
        key = f"{prefix}{k}"
        a, b = old.get(k), new.get(k)
        if isinstance(a, dict) and isinstance(b, dict) and key != "projects":
            out += diff(a, b, key + ".")
        elif a != b:
            out.append(key)
    return out


def is_live(key: str) -> bool:
    return any(key == p or (p.endswith(".") and key.startswith(p)) for p in LIVE)


def needs_restart(keys: list[str]) -> list[str]:
    return [k for k in keys if not is_live(k)]


def apply_in_place(cfg: dict, new: dict) -> list[str]:
    """Copy `new` into `cfg` keeping the existing section dicts (other objects
    hold references to them). Returns the changed keys."""
    changed = diff(cfg, new)
    for k, v in new.items():
        if k == "projects":
            cfg[k] = _norm_projects(v)
        elif isinstance(v, dict) and isinstance(cfg.get(k), dict):
            # Update first, then drop stale keys: a reader on another thread
            # never sees a half-empty section.
            cfg[k].update(copy.deepcopy(v))
            for kk in [kk for kk in cfg[k] if kk not in v]:
                del cfg[k][kk]
        else:
            cfg[k] = copy.deepcopy(v)
    return changed
