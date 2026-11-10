"""Learning from misses (all local, under state/, which git ignores).

A request that went to Claude and then ran exactly one Windows settings
tool that an instant command could have run ("it's a bit loud for me" ->
set_volume delta -10) is a phrasing the engine missed. It is appended to
state/intents_misses.jsonl. Saying "teach it" adds the newest one not yet
taught to state/intents_taught.yaml, which the engine reloads: from then on
that exact phrasing is instant, and the classifier learns from it too.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

MISSES = "intents_misses.jsonl"
TAUGHT = "intents_taught.yaml"


def _dir(state_dir) -> Path | None:
    if state_dir is None:
        return None
    p = Path(state_dir)
    return p


def tool_to_intent(tool: str, args: dict | None, result: dict | None = None) -> tuple[str, dict] | None:
    """The instant intent that does what this tool call did, or None."""
    name = tool.split("__")[-1]
    a = dict(args or {})
    if name == "set_volume":
        if a.get("level") is not None:
            return "volume.set", {"level": int(a["level"])}
        if a.get("delta") is not None:
            return "volume.step", {"delta": int(a["delta"])}
    if name == "set_mute":
        return ("volume.mute", {}) if a.get("muted", True) else ("volume.unmute", {})
    if name == "set_brightness" and not a.get("monitor"):
        if a.get("level") is not None:
            return "brightness.set", {"level": int(a["level"])}
        if a.get("delta") is not None:
            return "brightness.step", {"delta": int(a["delta"])}
    if name == "set_theme" and a.get("mode") in ("dark", "light", "toggle") and a.get("scope", "both") == "both":
        return "theme.set", {"mode": a["mode"]}
    if name == "media_control":
        act = str(a.get("action", "")).lower()
        return {"next": ("media.next", {"action": "next"}), "previous": ("media.previous", {"action": "previous"}),
                "pause": ("media.pause", {"action": "pause"}), "play": ("media.play", {"action": "play"}),
                "play_pause": ("media.toggle", {"action": "play_pause"})}.get(act)
    if name == "lock_pc":
        return "screen.lock", {}
    if name == "launch_app" and a.get("name"):
        return "app.open", {"app": str((result or {}).get("launched") or a["name"])}
    if name in ("set_wifi", "set_bluetooth"):
        return "radio.set", {"radio": "wifi" if name == "set_wifi" else "bluetooth", "on": a.get("on")}
    return None


def intent_label(intent: str, slots: dict) -> str | None:
    """A taught intent -> the classifier label it trains (None: exact matches only)."""
    if intent == "volume.step":
        return "volume.up" if slots.get("delta", 0) > 0 else "volume.down"
    if intent == "brightness.step":
        return "brightness.up" if slots.get("delta", 0) > 0 else "brightness.down"
    if intent == "theme.set" and slots.get("mode") in ("dark", "light"):
        return f"theme.{slots['mode']}"
    if intent in ("volume.mute", "volume.unmute", "media.next", "media.previous", "media.pause", "media.play"):
        return intent
    return None


def log_miss(state_dir, text: str, tool: str, args: dict | None, result: dict | None = None) -> dict | None:
    """Remember a request Claude handled with one settings tool. Returns the row (None if not an instant kind)."""
    d = _dir(state_dir)
    hit = tool_to_intent(tool, args, result)
    if d is None or hit is None or not (text or "").strip():
        return None
    row = {"when": time.strftime("%Y-%m-%d %H:%M:%S"), "text": text.strip(), "tool": tool.split("__")[-1],
           "args": args or {}, "intent": hit[0], "slots": hit[1]}
    d.mkdir(parents=True, exist_ok=True)
    with open(d / MISSES, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def misses(state_dir) -> list[dict]:
    d = _dir(state_dir)
    if d is None or not (d / MISSES).exists():
        return []
    out = []
    for line in (d / MISSES).read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def load_taught(state_dir) -> list[dict]:
    d = _dir(state_dir)
    if d is None or not (d / TAUGHT).exists():
        return []
    import yaml
    try:
        data = yaml.safe_load((d / TAUGHT).read_text(encoding="utf-8")) or []
    except Exception as e:
        print(f"[instant] {TAUGHT}: {e}")
        return []
    return [x for x in data if isinstance(x, dict) and x.get("text") and x.get("intent")]


def taught_mtime(state_dir) -> float | None:
    """When state/intents_taught.yaml last changed (None: no file)."""
    d = _dir(state_dir)
    try:
        return (d / TAUGHT).stat().st_mtime_ns if d is not None else None
    except OSError:
        return None


def _write_taught(d: Path, items: list[dict]):
    import yaml
    d.mkdir(parents=True, exist_ok=True)
    (d / TAUGHT).write_text("# Phrasings taught by voice (\"teach it\"). Edit or delete freely.\n" +
                            yaml.safe_dump(items, sort_keys=False, allow_unicode=True), encoding="utf-8")


def remove_taught(state_dir, text: str) -> bool:
    """Forget a taught phrasing (Settings > Instant). True when one was removed.
    A running engine picks the change up on its next request."""
    from .normalize import normalize
    d = _dir(state_dir)
    if d is None:
        return False
    items = load_taught(state_dir)
    want = normalize(text)
    keep = [x for x in items if normalize(x["text"]) != want]
    if len(keep) == len(items):
        return False
    _write_taught(d, keep)
    return True


def teach_last(state_dir, max_age_s: float = 3600) -> dict | None:
    """Add the newest missed phrasing not taught yet. Returns it, or None."""
    from .normalize import normalize
    d = _dir(state_dir)
    if d is None:
        return None
    taught = {normalize(x["text"]) for x in load_taught(state_dir)}
    now = time.time()
    for row in reversed(misses(state_dir)):
        try:
            age = now - time.mktime(time.strptime(row["when"], "%Y-%m-%d %H:%M:%S"))
        except (KeyError, ValueError):
            age = 0
        if age > max_age_s:
            return None
        if normalize(row["text"]) in taught:
            continue
        items = load_taught(state_dir)
        # a step taught by voice keeps its direction, not the exact amount Claude chose
        items.append({"text": row["text"], "intent": row["intent"], "slots": row.get("slots") or {},
                      "taught": time.strftime("%Y-%m-%d %H:%M:%S")})
        _write_taught(d, items)
        return row
    return None
