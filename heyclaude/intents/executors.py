"""Run a Match: the same Windows settings tools the agent uses (M9's
registry), called in this process, so the result has the same {old, new}
shape and the assistant feeds it through the same effect + undo path.

Sleep, restart and shutdown are not here and never will be: they stay
click-to-confirm through the agent. Only comfort settings, media, timers,
app launch, lock and the local clock.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from . import learn
from .slots import say_duration

PREFIX = "mcp__windows__"
NEVER = {"request_power_action", "perform_power_action", "close_window", "set_clipboard", "set_wallpaper",
         "set_default_output_device", "set_night_light"}


@dataclass
class Outcome:
    say: str = ""                    # the spoken ack or answer
    tool: str | None = None          # full tool name (for effects + undo), when a tool ran
    args: dict = field(default_factory=dict)
    result: object = None
    widget: dict | None = None       # a card to show (the timer)
    always_say: bool = False         # an answer or an error: spoken even with instant.ack: silent
    error: bool = False
    clear_timer_card: bool = False


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:] if s else s


def _default_runner(name: str, args: dict):
    from ..mcp_windows import registry
    from ..mcp_windows._util import com_init
    com_init()
    return registry.call(name, args)


class Executor:
    def __init__(self, engine=None, runner=None, timers=None, now=datetime.now, state_dir=None):
        self.engine = engine
        self.runner = runner or _default_runner
        self.timers = timers
        self.now = now
        self.state_dir = state_dir

    def call(self, tool: str, args: dict):
        if tool in NEVER:
            raise PermissionError(f"{tool} is never an instant command")
        return self.runner(tool, args)

    def _tool(self, tool: str, args: dict, say_fn) -> Outcome:
        r = self.call(tool, args)
        return Outcome(say=say_fn(r if isinstance(r, dict) else {}), tool=PREFIX + tool, args=args, result=r)

    def run(self, m) -> Outcome:
        """Act on a Match. A ToolError (nothing playing, no such app) becomes a spoken
        Outcome; anything unexpected is raised (the assistant then asks the agent)."""
        from ..mcp_windows._util import ToolError
        fn = getattr(self, "_" + m.intent.replace(".", "_"), None)
        if fn is None:
            raise KeyError(f"no executor for {m.intent}")
        try:
            return fn(m.slots)
        except ToolError as e:
            return Outcome(say=_cap(str(e).rstrip(".")) + ".", always_say=True, error=True)

    # -- volume ---------------------------------------------------------------------------
    def _volume_set(self, s):
        return self._tool("set_volume", {"level": int(s["level"])}, lambda r: f"Volume {r.get('new', s['level'])}.")

    def _volume_step(self, s):
        def say(r):
            if r.get("old") == r.get("new"):
                return f"Volume is already at {r.get('new')}."
            return f"Volume {r.get('new')}."
        return self._tool("set_volume", {"delta": int(s["delta"])}, say)

    def _volume_mute(self, s):
        return self._tool("set_mute", {"muted": True}, lambda r: "Already muted." if r.get("old") is True else "Muted.")

    def _volume_unmute(self, s):
        return self._tool("set_mute", {"muted": False},
                          lambda r: "Sound is already on." if r.get("old") is False else "Sound on.")

    # -- brightness ---------------------------------------------------------------------------
    def _brightness_set(self, s):
        return self._tool("set_brightness", {"level": int(s["level"])},
                          lambda r: f"Brightness {r.get('new', s['level'])}.")

    def _brightness_step(self, s):
        def say(r):
            if r.get("old") == r.get("new"):
                return f"Brightness is already at {r.get('new')}."
            return f"Brightness {r.get('new')}."
        return self._tool("set_brightness", {"delta": int(s["delta"])}, say)

    # -- theme ------------------------------------------------------------------------------
    def _theme_set(self, s):
        def say(r):
            new = r.get("new") or s["mode"]
            if r.get("old") == new and s["mode"] != "toggle":
                return f"It's already in {new} mode."
            return f"{_cap(str(new))} mode."
        return self._tool("set_theme", {"mode": s["mode"]}, say)

    # -- media ------------------------------------------------------------------------------
    def _media(self, action, said):
        return self._tool("media_control", {"action": action}, lambda r: said)

    def _media_next(self, s):
        return self._media("next", "Skipped.")

    def _media_previous(self, s):
        return self._media("previous", "Going back.")

    def _media_pause(self, s):
        return self._media("pause", "Paused.")

    def _media_play(self, s):
        return self._media("play", "Playing.")

    def _media_toggle(self, s):
        return self._media("play_pause", "Okay.")

    def _media_now_playing(self, s):
        r = self.call("now_playing", {})
        r = r if isinstance(r, dict) else {}
        title, artist = r.get("title") or "", r.get("artist") or ""
        if r.get("status") in (None, "none", "closed") or not (title or artist):
            say = "Nothing is playing right now."
        else:
            say = f"{title} by {artist}." if title and artist else f"{title or artist}."
            if r.get("status") == "paused":
                say = say[:-1] + ", paused."
        playing = bool(title or artist)
        return Outcome(say=say, tool=PREFIX + "now_playing" if playing else None, args={}, result=r, always_say=True)

    # -- timers -----------------------------------------------------------------------------
    def _timer_set(self, s):
        if self.timers is None:
            raise RuntimeError("no timer scheduler")
        secs, label = int(s["seconds"]), s.get("label", "")
        self.timers.start(secs, label)
        spec = {"widget": "timer", "data": {"seconds": secs, **({"label": label} if label else {})}}
        what = f"{_cap(label)} timer" if label else "Timer"
        return Outcome(say=f"{what} set for {say_duration(secs)}.", widget=spec)

    def _timer_cancel(self, s):
        if self.timers is None:
            raise RuntimeError("no timer scheduler")
        gone = self.timers.cancel(s.get("label"))
        if not gone:
            if s.get("label") and self.timers.running():
                return Outcome(say=f"There's no {s['label']} timer.", always_say=True)
            return Outcome(say="There's no timer running.", always_say=True)
        say = "Timer cancelled." if len(gone) == 1 else f"Cancelled {len(gone)} timers."
        return Outcome(say=say, clear_timer_card=True)

    def _timer_status(self, s):
        run = self.timers.running() if self.timers is not None else []
        if not run:
            return Outcome(say="There's no timer running.", always_say=True)
        now = self.timers.clock()
        parts = []
        for t in sorted(run, key=lambda t: t.ends)[:3]:
            left = int(round(t.left(now)))
            if left >= 120:
                left = int(round(left / 60.0)) * 60
            parts.append(f"{say_duration(left)} left" + (f" on the {t.label} timer" if t.label and len(run) > 1 else ""))
        return Outcome(say=_cap("; ".join(parts)) + ".", always_say=True)

    # -- apps, Settings, lock -----------------------------------------------------------------
    def _app_open(self, s):
        return self._tool("launch_app", {"name": s["app"]}, lambda r: f"Opening {r.get('launched') or s['app']}.")

    def _settings_open(self, s):
        page = s["page"]
        nice = {"wifi": "Wi-Fi", "wi-fi": "Wi-Fi", "vpn": "VPN"}.get(page, page)
        return self._tool("open_settings_page", {"page": page}, lambda r: f"Opening {nice} settings.")

    def _screen_lock(self, s):
        return self._tool("lock_pc", {}, lambda r: "Locked.")

    def _radio_set(self, s):
        tool = "set_wifi" if s["radio"] == "wifi" else "set_bluetooth"
        nice = "Wi-Fi" if s["radio"] == "wifi" else "Bluetooth"

        def say(r):
            new = r.get("new", s.get("on"))
            if r.get("old") == new and s.get("on") is not None:
                return f"{nice} is already {'on' if new else 'off'}."
            return f"{nice} {'on' if new else 'off'}."
        args = {} if s.get("on") is None else {"on": bool(s["on"])}
        return self._tool(tool, args, say)

    # -- the clock --------------------------------------------------------------------------
    def _clock_time(self, s):
        n = self.now()
        h = n.hour % 12 or 12
        return Outcome(say=f"It's {h}:{n.minute:02d} {'AM' if n.hour < 12 else 'PM'}.", always_say=True)

    def _clock_date(self, s):
        n = self.now()
        return Outcome(say=f"It's {n.strftime('%A')}, {n.strftime('%B')} {n.day}.", always_say=True)

    # -- teach ------------------------------------------------------------------------------
    def _learn_teach(self, s):
        row = learn.teach_last(self.state_dir)
        if row is None:
            return Outcome(say="There's nothing new to learn. Ask for something the long way first.",
                           always_say=True)
        if self.engine is not None:
            self.engine.reload()
        what = describe_intent(row["intent"], row.get("slots") or {})
        return Outcome(say=f"Got it. \"{row['text']}\" now means {what}.", always_say=True)


def describe_intent(intent: str, slots: dict) -> str:
    if intent == "volume.step":
        return "volume up" if slots.get("delta", 0) > 0 else "volume down"
    if intent == "brightness.step":
        return "brightness up" if slots.get("delta", 0) > 0 else "brightness down"
    if intent in ("volume.set", "brightness.set"):
        return f"{intent.split('.')[0]} {slots.get('level')}"
    if intent == "theme.set":
        return f"{slots.get('mode')} mode"
    if intent == "app.open":
        return f"open {slots.get('app')}"
    if intent == "radio.set":
        return f"{slots.get('radio')} {'on' if slots.get('on') else 'off'}"
    return intent.replace(".", " ").replace("_", " ")
