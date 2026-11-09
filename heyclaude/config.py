"""Config loading: config.yaml merged over config.example.yaml defaults."""
from __future__ import annotations

import copy
import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = ROOT / "state"
LOG_DIR = STATE_DIR / "logs"


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict) and k != "projects":
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load(path: str | os.PathLike | None = None) -> dict:
    with open(ROOT / "config.example.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    user = Path(path) if path else ROOT / "config.yaml"
    if user.exists():
        with open(user, encoding="utf-8") as f:
            cfg = _merge(cfg, yaml.safe_load(f) or {})
    # Normalise project keys: lowercase, single spaces.
    cfg["projects"] = {
        " ".join(str(k).lower().split()): str(v) for k, v in (cfg.get("projects") or {}).items()
    }
    STATE_DIR.mkdir(exist_ok=True)
    LOG_DIR.mkdir(exist_ok=True)
    return cfg
