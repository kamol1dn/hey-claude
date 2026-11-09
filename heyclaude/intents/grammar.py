"""The intent grammar: one small set of anchored patterns per command.

Every pattern must match the WHOLE normalized utterance, so "what's the volume
of a sphere" or "set a timer in Python" can never trigger: there are words
left over. Slots are captured loosely and then parsed (slots.py); a slot that
doesn't parse makes the rule fail, and the next rule is tried.

A rule's build() gets the regex match and a Ctx (the step size, the last
instant command's domain, the installed apps) and returns the slots, or None.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

from . import slots as S

# -- building blocks ---------------------------------------------------------------------
_DET = r"(?:(?:the|my|this|that|your|our|all|all the)\s+)?"
_VOLW = (r"(?:(?:master|main|system|computer|pc|laptop|speaker|speakers|overall|windows|global|output|device|"
         r"music|media|sound)\s+)?(?:volume|sound level|audio level|sound|audio)(?:\s+level)?")
VOL = _DET + _VOLW
_BRW = r"(?:(?:screen|display|monitor|laptop|pc|computer)\s+)?(?:brightness|backlight)(?:\s+level)?"
BR = _DET + _BRW
SCREEN = _DET + r"(?:screen|display|monitor|monitors|screens|laptop screen|computer screen|backlight)"
SND_OBJ = (r"(?:it|this|that|things|everything|the music|music|my music|the song|this song|the tunes|the sound|"
           r"the audio|the volume|volume|sound|audio|the speakers|speakers|the track|this track)")
NUM = r"(?P<n>[a-z0-9 ]+?)"
AMT = r"(?P<a>[a-z0-9 ]+?)"
TO = r"(?:(?:to|at|on|as|of|is|equal to|=)\s+)?"
UP_V = (r"raise|increase|boost|lift|amplify|bump up|turn up|crank up|pump up|amp up|ramp up|kick up|jack up|"
        r"push up|bring up|dial up|crank|pump|bump|up|max out|louden|heighten")
DOWN_V = (r"lower|decrease|reduce|drop|lessen|soften|quieten|quiet down|dial down|turn down|bring down|"
          r"knock down|tone down|take down|drop down|down|dampen|muffle")
PHRASAL = r"turn|crank|pump|bump|bring|ramp|kick|jack|push|dial|tone|knock|take|put|move|nudge|notch"
PHRASAL_IT = r"turn|crank|pump|bump|ramp|kick|jack|dial|tone|knock|nudge|notch"
MEDIA_OBJ = (r"(?:music|song|track|tune|tunes|video|playback|player|podcast|audio|media|movie|show|episode|"
             r"audiobook|youtube|spotify|playlist|album|radio|stream)")


@dataclass
class Ctx:
    step: int = 10
    domain: str | None = None                  # domain of the last instant command still in context
    last_intent: str | None = None
    apps: dict = field(default_factory=dict)   # normalized name -> launch name
    has_timers: bool = False
    radios: bool = True


@dataclass
class Rule:
    intent: str
    rx: re.Pattern
    build: Callable[[re.Match, Ctx], dict | None]


RULES: list[Rule] = []


def rule(intent: str, *patterns: str):
    """Decorator: the same builder for several patterns."""
    def deco(fn):
        for p in patterns:
            RULES.append(Rule(intent, re.compile(p), fn))
        return fn
    return deco


def _g(m: re.Match, name: str) -> str:
    try:
        return (m.group(name) or "").strip()
    except IndexError:
        return ""


def _level(m) -> dict | None:
    lv = S.parse_level(_g(m, "n"))
    return None if lv is None else {"level": lv}


def _dir(word: str) -> str | None:
    w = word.strip()
    if re.fullmatch(r"(?:" + UP_V + r"|higher|louder|plus|increase|brighter|brighten|lighter|lighten)", w):
        return "up"
    if re.fullmatch(r"(?:" + DOWN_V + r"|lower|quieter|softer|minus|decrease|dimmer|darker|darken|dim)", w):
        return "down"
    return None


# == volume ==================================================================================
@rule("volume.set",
      r"(?:(?:set|put|change|turn|bring|adjust|make|move|get|switch|take)\s+)?" + VOL + r"\s+" + TO +
      r"(?:(?:about|around|like|roughly|exactly)\s+)?" + NUM + r"(?:\s+(?:percent|per cent))?(?:\s+(?:volume|level))?",
      r"(?:(?:set|put|turn|bring|adjust|move|drop|raise|lower|crank|pump|take|get|reduce|increase|decrease|push|"
      r"dial|knock|boost)\s+)?(?:(?:up|down)\s+)?" + VOL + r"\s+(?:(?:up|down|back)\s+)?(?:to|at)\s+" +
      r"(?:(?:about|around|like|roughly|exactly)\s+)?" + NUM + r"(?:\s+(?:percent|per cent))?",
      r"(?:(?:set|put|turn|make|give me|go to|go)\s+(?:it\s+)?(?:to\s+|at\s+)?)?" + NUM +
      r"(?:\s+(?:percent|per cent))?\s+(?:the\s+)?" + _VOLW + r"(?:\s+please)?")
def _vol_set(m, ctx):
    n = _g(m, "n")
    if n in ("up", "down", "back", "off", "on") or n.startswith(("up ", "down ")):
        return None
    return _level(m)


@rule("volume.set",
      r"(?:(?:turn|crank|pump|bring|put|take|push|set)\s+)?" + VOL + r"\s+(?:all the way|right|fully|completely)\s+(?P<d>up|down)",
      r"(?:turn|crank|pump|bring|put|take|push)\s+(?:it|the music|music|things|everything|the sound|the volume|"
      r"volume|the speakers)\s+(?:all the way|right|fully|completely)\s+(?P<d>up|down)",
      r"(?:turn|crank|pump|bring|push)\s+(?:it|the music|music|things|everything|the sound|the volume|volume)\s+"
      r"(?P<d>up)\s+to\s+(?:the\s+)?(?:max|maximum|full|eleven|11)",
      r"(?:max|maximum|crank|pump)\s+(?:out\s+)?(?:the\s+)?(?:volume|sound)(?:\s+(?P<d>up))?(?:\s+all the way)?")
def _vol_extreme(m, ctx):
    return {"level": 0 if _g(m, "d") == "down" else 100}


@rule("volume.step",
      r"(?P<v>" + UP_V + "|" + DOWN_V + r")\s+" + VOL + r"(?:\s+" + AMT + r")?",
      r"(?P<v>" + PHRASAL + r")\s+(?:" + AMT + r"\s+)?(?P<d>up|down)\s+" + VOL + r"(?:\s+" + AMT.replace("a>", "a2>") + r")?",
      r"(?P<v>" + PHRASAL + r")\s+" + VOL + r"\s+(?P<d>up|down)(?:\s+" + AMT + r")?",
      VOL + r"\s+(?P<d>up|down|higher|lower|louder|quieter|softer|increase|decrease|plus|minus)(?:\s+" + AMT + r")?",
      r"(?P<v>" + PHRASAL_IT + r"|push|put|move)\s+(?:" + AMT + r"\s+)?(?P<d>up|down)\s+(?:the\s+|my\s+)?(?:music|song|"
      r"tunes|track|speakers|sound|audio|volume)(?:\s+" + AMT.replace("a>", "a2>") + r")?",
      r"(?:(?P<more>more|less)\s+(?:volume|sound))",
      r"keep\s+(?:it|the music|the noise|the volume|the sound|things)\s+(?P<d>down)(?:\s+" + AMT + r")?")
def _vol_step(m, ctx):
    d = _g(m, "d") or _g(m, "v")
    if _g(m, "more"):
        d = "up" if _g(m, "more") == "more" else "down"
    direction = _dir(d)
    if direction is None:
        return None
    a = _g(m, "a") or _g(m, "a2")
    amt = S.parse_amount(a, ctx.step)
    if amt is None:
        return None
    return {"delta": amt if direction == "up" else -amt}


@rule("volume.step",
      r"(?P<v>" + PHRASAL_IT + r")\s+(?P<o>it|this|that|things|everything)\s+"
      r"(?:" + AMT + r"\s+)?(?P<d>up|down)(?:\s+" + AMT.replace("a>", "a2>") + r")?",
      r"(?P<v>" + PHRASAL + r")\s+(?P<o>the music|music|my music|the song|this song|the tunes|the sound|the audio|"
      r"the volume|volume|sound|audio|the speakers|speakers|the track)\s+"
      r"(?:" + AMT + r"\s+)?(?P<d>up|down)(?:\s+" + AMT.replace("a>", "a2>") + r")?",
      r"(?:(?:make|turn|set|get|have|play)\s+" + SND_OBJ + r"\s+)?(?:" + AMT + r"\s+)?(?P<d>louder|quieter|softer)"
      r"(?:\s+" + AMT.replace("a>", "a2>") + r")?",
      r"(?:make|turn|set|get|have|play)\s+(?:the music|music|the sound|sound|the volume|volume|the audio|audio|"
      r"the speakers)\s+(?:" + AMT + r"\s+)?(?P<d>higher|lower)(?:\s+" + AMT.replace("a>", "a2>") + r")?",
      r"(?:it is|its|it s|this is|that is|the music is|the sound is|the volume is|music is|sound is|volume is|"
      r"the audio is|everything is)?\s*(?:way |much |a bit |a little |a little bit |kind of |kinda |slightly |"
      r"really |so |far )?too (?P<d>loud|quiet|soft)(?:\s+in here)?")
def _vol_step_obj(m, ctx):
    d = _g(m, "d")
    if d in ("up", "down") and _g(m, "o") in ("it", "this", "that", "things", "everything") \
            and ctx.domain == "brightness":
        return None                                   # "turn it up" after "brightness 50": the brightness rule
    direction = {"loud": "down", "quiet": "up", "soft": "up"}.get(d) or _dir(d)
    if direction is None:
        return None
    amt = S.parse_amount(_g(m, "a") or _g(m, "a2"), ctx.step)
    if amt is None:
        return None
    return {"delta": amt if direction == "up" else -amt}


_MUTE_OBJ = (r"(?:(?:the|my|all|all the|this|that)\s+)?(?:volume|sound|sounds|audio|speakers?|computer|pc|laptop|"
             r"music|everything|it|all|system|output|audio output|system sound|system audio)")


@rule("volume.mute",
      r"mute(?:\s+" + _MUTE_OBJ + r")?(?:\s+(?:for now|completely|entirely))?",
      r"silence\s+" + _MUTE_OBJ,
      r"(?:turn|switch|shut|cut)\s+off\s+(?:the\s+|all\s+|all the\s+)?(?:sound|audio|volume|speakers?)",
      r"(?:turn|switch|shut|cut)\s+(?:the\s+|all\s+|all the\s+)?(?:sound|audio|volume|speakers?)\s+off",
      r"(?:kill|cut)\s+(?:the\s+|all\s+|all the\s+)?(?:sound|audio)",
      r"(?:sound|audio|volume)\s+off",
      r"(?:no\s+(?:more\s+)?sound|mute\s+mode|go\s+mute|put\s+(?:it|the computer|the pc)\s+on\s+mute)")
def _mute(m, ctx):
    return {}


@rule("volume.unmute",
      r"unmute(?:\s+" + _MUTE_OBJ + r")?",
      r"(?:turn|switch|put|bring)\s+(?:the\s+)?(?:sound|audio|volume|speakers?)\s+(?:back\s+)?on",
      r"(?:turn|switch)\s+(?:back\s+)?on\s+(?:the\s+)?(?:sound|audio|speakers?)",
      r"(?:sound|audio|volume)\s+(?:back\s+)?on",
      r"(?:bring|get)\s+(?:the\s+)?(?:sound|audio)\s+back",
      r"(?:take\s+(?:it|the computer|the pc)\s+off\s+mute)")
def _unmute(m, ctx):
    return {}


# == brightness ===============================================================================
@rule("brightness.set",
      r"(?:(?:set|put|change|turn|bring|adjust|make|move|get|switch)\s+)?" + BR + r"\s+" + TO +
      r"(?:(?:about|around|like|roughly|exactly)\s+)?" + NUM + r"(?:\s+(?:percent|per cent))?",
      r"(?:(?:set|put|turn|bring|adjust|move|drop|raise|lower|take|get|reduce|increase|decrease|push|dial|boost)\s+)?"
      r"(?:(?:up|down)\s+)?" + BR + r"\s+(?:(?:up|down|back)\s+)?(?:to|at)\s+" + NUM + r"(?:\s+(?:percent|per cent))?",
      r"(?:(?:set|put|turn|make|give me|go to|go)\s+(?:it\s+)?(?:to\s+|at\s+)?)?" + NUM +
      r"(?:\s+(?:percent|per cent))?\s+(?:the\s+)?" + _BRW,
      r"(?:set|put|make|turn|dim|brighten)\s+" + SCREEN + r"\s+(?:brightness\s+)?(?:to|at)\s+" + NUM +
      r"(?:\s+(?:percent|per cent))?(?:\s+brightness)?")
def _br_set(m, ctx):
    n = _g(m, "n")
    if n in ("up", "down", "back", "off", "on") or n.startswith(("up ", "down ")):
        return None
    return _level(m)


@rule("brightness.set",
      r"(?:(?:turn|crank|bring|put|take|push|set)\s+)?" + BR + r"\s+(?:all the way|right|fully|completely)\s+(?P<d>up|down)",
      r"(?:max|maximum|crank)\s+(?:out\s+)?(?:the\s+)?(?:brightness)(?:\s+(?P<d>up))?")
def _br_extreme(m, ctx):
    return {"level": 0 if _g(m, "d") == "down" else 100}


@rule("brightness.step",
      r"(?P<v>" + UP_V + "|" + DOWN_V + r")\s+" + BR + r"(?:\s+" + AMT + r")?",
      r"(?P<v>" + PHRASAL + r")\s+(?:" + AMT + r"\s+)?(?P<d>up|down)\s+(?:the\s+|my\s+)?(?:brightness|backlight|"
      r"screen brightness|screen|display)(?:\s+" + AMT.replace("a>", "a2>") + r")?",
      r"(?P<v>" + PHRASAL + r")\s+" + BR + r"\s+(?P<d>up|down)(?:\s+" + AMT + r")?",
      BR + r"\s+(?P<d>up|down|higher|lower|brighter|dimmer|increase|decrease|plus|minus)(?:\s+" + AMT + r")?",
      r"(?P<more>more|less)\s+brightness",
      r"(?P<d>dim|brighten|darken|lighten)\s+" + SCREEN + r"(?:\s+" + AMT + r")?",
      r"(?:(?:make|turn|set|get)\s+(?:it|things|everything|" + SCREEN + r")\s+)?(?:" + AMT + r"\s+)?(?P<d>brighter|dimmer)"
      r"(?:\s+" + AMT.replace("a>", "a2>") + r")?",
      r"(?:make|turn|set|get)\s+" + SCREEN + r"\s+(?:" + AMT + r"\s+)?(?P<d>darker|lighter|less bright|brighter|dimmer)"
      r"(?:\s+" + AMT.replace("a>", "a2>") + r")?",
      r"(?:the\s+)?" + SCREEN.replace(_DET, "") + r"\s+is\s+(?:way |much |a bit |a little |kind of |slightly |really |so )?"
      r"too\s+(?P<too>bright|dark|dim)",
      r"(?P<v>" + PHRASAL_IT + r")\s+(?P<it>it|this|that|things|everything)\s+(?:" + AMT + r"\s+)?(?P<d>up|down)"
      r"(?:\s+" + AMT.replace("a>", "a2>") + r")?")
def _br_step(m, ctx):
    if _g(m, "it") and ctx.domain != "brightness":
        return None                                    # "turn it up" is the volume, unless brightness was just changed
    if _g(m, "more"):
        direction = "up" if _g(m, "more") == "more" else "down"
    elif _g(m, "too"):
        direction = "down" if _g(m, "too") == "bright" else "up"
    else:
        d = _g(m, "d") or _g(m, "v")
        direction = "down" if d == "less bright" else _dir(d)
    if direction is None:
        return None
    amt = S.parse_amount(_g(m, "a") or _g(m, "a2"), ctx.step)
    if amt is None:
        return None
    return {"delta": amt if direction == "up" else -amt}


# == dark / light mode ========================================================================
_THEME = r"(?:mode|theme|colou?r mode|colou?r scheme|colou?r theme|appearance|look|windows theme)"
_ON_PRE = (r"(?:switch|turn|change|go|set|put|flip|enable|activate|use|start|apply|bring|give me|i want|"
           r"let us go|lets go|let us use|lets use|back|switch back|go back|change back|put it|set it|make it|"
           r"switch it|turn it|change it|could we have|can we have|can i have|can i get|i would like|i will take)")
_OFF_PRE = r"(?:turn off|switch off|disable|deactivate|stop|exit|leave|get out of|get rid of|kill|no more|end)"
_THEME_TAIL = (r"(?:\s+(?:for (?:windows|the (?:pc|computer|system|laptop))|on (?:windows|the (?:pc|computer|laptop))|"
               r"everywhere|again|instead))?")


@rule("theme.set",
      r"(?:" + _ON_PRE + r"(?:\s+(?:it|on|to|into|in|over to|back to|on the|to the|back|over|over to the|into the))*\s+)?"
      r"(?:the\s+|a\s+)?(?P<m>dark|light)\s+" + _THEME + r"(?:\s+(?P<o>on|off))?" + _THEME_TAIL,
      r"(?P<off>" + _OFF_PRE + r")\s+(?:the\s+)?(?P<m>dark|light)\s+" + _THEME + _THEME_TAIL,
      r"(?:make|turn|set|switch|change|put)\s+(?:windows|the (?:pc|computer|system|theme|colou?rs|laptop|"
      r"colou?r scheme|colou?r mode|mode|appearance)|my (?:pc|computer|laptop|theme|colou?rs)|the windows theme)\s+"
      r"(?:to\s+|into\s+)?(?P<m>dark|light)(?:\s+(?:mode|theme))?" + _THEME_TAIL,
      r"(?:go|switch|change|turn)\s+(?:to\s+)?(?P<m>dark|light)\s+(?:theme|mode)",
      r"(?:turn|switch|flip|change|set|put)\s+(?:it\s+|windows\s+)?(?:back\s+)?(?:to\s+)?(?P<m>dark|light)(?:\s+again)?")
def _theme(m, ctx):
    mode = _g(m, "m")
    if mode not in ("dark", "light"):
        return None
    if re.fullmatch(r"(?:turn|switch|flip|change|set|put)\s+(?:it\s+)?(?:back\s+)?(?:to\s+)?(?:dark|light)(?:\s+again)?",
                    m.group(0)) and ctx.domain != "theme":
        return None                                   # "turn it to dark" only right after a theme change
    flip = {"dark": "light", "light": "dark"}
    if _g(m, "o") == "off" or _g(m, "off"):
        mode = flip[mode]
    return {"mode": mode}


@rule("theme.set",
      r"(?:toggle|flip|swap|switch)\s+(?:the\s+)?(?:themes?|dark mode|light mode|colou?r mode|"
      r"between dark and light(?: modes?)?|between light and dark(?: modes?)?|dark and light modes?|"
      r"light and dark modes?|dark light mode)")
def _theme_toggle(m, ctx):
    return {"mode": "toggle"}


# == media ====================================================================================
_TRACK = r"(?:song|track|tune|video|episode|chapter|number)"


@rule("media.next",
      r"(?:(?:play|go to|skip to|jump to|switch to|change to|give me|put on|on to|onto)\s+)?(?:the\s+)?next\s+" + _TRACK +
      r"(?:\s+(?:please|on the playlist|in the playlist))?",
      r"(?:i (?:do not|dont) (?:like|want) (?:this|that) (?:song|one|track)(?: anymore)?\s+)?"
      r"skip(?:\s+(?:it|this|that|this one|that one|the song|this song|this track|the track|that song|that track|"
      r"the current song|the current track|song|track|over this|over this one|to the next(?: one| song| track)?|"
      r"forward|this tune|this video))?",
      r"(?:change|switch)\s+(?:the\s+|this\s+)?(?:song|track|tune)",
      r"(?:another|a different|different|a new|new)\s+(?:song|track|tune)(?:\s+please)?",
      r"(?:play\s+)?(?:the\s+)?next\s+(?:one|thing)",
      r"next")
def _next(m, ctx):
    t = m.group(0)
    if re.fullmatch(r"(?:play\s+)?(?:the\s+)?next(?:\s+(?:one|thing))?", t) and ctx.domain != "media":
        return None                                    # a bare "next" is only a track with music in context
    return {"action": "next"}


@rule("media.previous",
      r"(?:(?:play|go to|go back to|skip to|skip back to|jump to|jump back to|back to|return to)\s+)?(?:the\s+)?"
      r"(?:previous|prior|prev|last)\s+" + _TRACK + r"(?:\s+again)?",
      r"(?:go|skip|jump|move|step)\s+back(?:\s+(?:a|one))?\s+" + _TRACK,
      r"back\s+(?:a|one)\s+" + _TRACK,
      r"(?:play|start)\s+(?:that|this|the)\s+(?:song|track)\s+(?:again|from the (?:start|beginning)|over)",
      r"(?:replay|restart)\s+(?:that|this|the)\s+(?:song|track)",
      r"(?:previous|go back|back|skip back)(?:\s+(?:one|please))?")
def _prev(m, ctx):
    t = m.group(0)
    if re.fullmatch(r"(?:previous|go back|back|skip back)(?:\s+(?:one|please))?", t) and ctx.domain != "media":
        return None
    return {"action": "previous"}


@rule("media.pause",
      r"(?:pause|stop|halt|freeze|hold)\s+(?:the\s+|my\s+|this\s+|that\s+)?" + MEDIA_OBJ +
      r"(?:\s+(?:for (?:a (?:sec|second|moment|bit|minute)|now)|real quick))?",
      r"pause(?:\s+(?:it|this|that|everything|all|playback|playing))?(?:\s+for (?:a (?:sec|second|moment|bit|minute)|now))?",
      r"stop\s+playing(?:\s+(?:the\s+)?(?:music|song|it|this|that))?",
      r"(?:put|hit)\s+(?:it|the music|the song|this|playback)?\s*(?:on\s+)?pause")
def _pause(m, ctx):
    return {"action": "pause"}


@rule("media.play",
      r"(?:play|resume|unpause|continue|restart|start|unfreeze|put on)\s+(?:the\s+|my\s+|some\s+|this\s+)?"
      r"(?:music|song|track|playback|player|podcast|video|media|audio|tunes|audiobook|episode)(?:\s+again)?",
      r"(?:resume|unpause)(?:\s+(?:it|this|that|playing|playback|everything))?",
      r"play(?:\s+(?:it|again|it again))?",
      r"(?:keep|continue|start|resume|go on)\s+playing",
      r"(?:hit|press)\s+play")
def _play(m, ctx):
    return {"action": "play"}


@rule("media.toggle", r"play\s*(?:or|and|slash)?\s*pause", r"toggle\s+(?:the\s+)?(?:playback|music|play pause)")
def _toggle(m, ctx):
    return {"action": "play_pause"}


@rule("media.now_playing",
      r"what\s+is\s+playing(?:\s+(?:right now|now|currently|at the moment))?",
      r"what\s+is\s+(?:this|that)\s+(?:song|track|tune)(?:\s+(?:right now|now|currently|at the moment))?",
      r"what\s+(?:song|track|tune|music)\s+is\s+(?:this|that|playing|on)(?:\s+(?:right now|now|currently))?",
      r"what\s+(?:song|track|tune|music)\s+(?:are we|am i)\s+(?:listening to|playing)(?:\s+(?:right now|now))?",
      r"what\s+is\s+(?:this|that|the)\s+(?:song|track|tune)(?:\s+(?:called|playing|right now))?",
      r"what\s+is\s+(?:currently|now|presently)\s+playing",
      r"what\s+am\s+i\s+listening\s+to(?:\s+(?:right now|now))?",
      r"who\s+(?:is\s+)?(?:sings|singing|sang|performs|performing|plays|playing)\s+(?:this|that)(?:\s+(?:song|track|tune))?",
      r"who\s+is\s+(?:this|the)\s+(?:artist|singer|band)",
      r"who\s+is\s+singing(?:\s+(?:this|this song|right now|now))?",
      r"(?:name|identify)\s+(?:this|that)\s+(?:song|track|tune)",
      r"what\s+is\s+the\s+name\s+of\s+(?:this|that)\s+(?:song|track|tune)",
      r"which\s+(?:song|track)\s+is\s+(?:this|playing)",
      r"(?:now|currently)\s+playing",
      r"what\s+song\s+(?:is it|this is)")
def _now_playing(m, ctx):
    return {}


# == timers ===================================================================================
_TSET = r"(?:(?:set|start|create|make|put on|put|give me|run|begin|add|setup|set up|get|do)\s+)?(?:me\s+|us\s+)?"
_LBL_BAD = {"a", "an", "the", "my", "one", "another", "new", "me", "us", "for", "of", "to", "timer", "quick", "short",
            "simple", "second", "minute", "hour", "seconds", "minutes", "hours", "min", "mins", "sec", "secs"}


def _label(s: str) -> str | None:
    s = " ".join((s or "").split())
    if not s:
        return ""
    words = s.split()
    if any(w in _LBL_BAD for w in words) or len(s) > 40 or S.parse_duration(s) is not None:
        return None
    if S.parse_int(s) is not None:
        return None
    return s


@rule("timer.set",
      _TSET + r"(?:(?:a|an|the|one|another|my|new|a new)\s+)?(?:(?P<l1>[a-z]+(?: [a-z]+)?)\s+)?(?:timer|countdown)"
      r"\s+(?:(?:for|of|to|at|on|lasting)\s+)?(?P<d>[a-z0-9 .]+?)"
      r"(?:\s+(?:for|called|named|labeled|labelled)\s+(?:the\s+|my\s+)?(?P<l2>[a-z]+(?: [a-z]+){0,2}))?",
      _TSET + r"(?:(?:a|an|the|one|another|new|a new)\s+)?(?P<d>[a-z0-9 .]+?)\s+(?:(?P<l1>[a-z]+)\s+)?(?:timer|countdown)"
      r"(?:\s+(?:for|called|named|labeled|labelled)\s+(?:the\s+|my\s+)?(?P<l2>[a-z]+(?: [a-z]+){0,2}))?",
      r"(?:time me|count down|countdown)\s+(?:for\s+)?(?P<d>[a-z0-9 .]+?)")
def _timer_set(m, ctx):
    d, l1, l2 = _g(m, "d"), _g(m, "l1"), _g(m, "l2")
    secs = S.parse_duration(d)
    if secs is None and l1:
        secs = S.parse_duration(f"{d} {l1}")           # "a five minute timer": l1 was "minute"
        l1 = ""
    if secs is None:
        return None
    if l1 and l2:
        return None
    lbl = _label(l1 or l2)
    if lbl is None:
        return None
    out = {"seconds": secs}
    if lbl:
        out["label"] = lbl
    return out


@rule("timer.cancel",
      r"(?:cancel|stop|end|kill|delete|remove|clear|turn off|dismiss|abort|switch off|shut off|forget|"
      r"never mind|nevermind|forget about)\s+(?:the\s+|my\s+|that\s+|this\s+|all\s+(?:the\s+|my\s+|of the\s+|of my\s+)?)?"
      r"(?:(?P<l>[a-z]+(?: [a-z]+)?)\s+)?(?:timers?|countdowns?)",
      r"(?:the\s+)?(?:timers?|countdowns?)\s+(?:off|cancel|cancelled|canceled|stop)")
def _timer_cancel(m, ctx):
    lbl = _label(_g(m, "l"))
    if lbl is None:
        return None
    return {"label": lbl} if lbl else {}


@rule("timer.status",
      r"how\s+(?:much\s+time|long|many minutes)(?:\s+is|\s+do i have)?\s+(?:left|remaining|to go)"
      r"(?P<on>\s+(?:on|for|in)\s+(?:the|my)\s+(?:(?P<l>[a-z]+)\s+)?(?:timer|countdown))?",
      r"(?:check|status of|show me)\s+(?:the|my)\s+(?:(?P<l2>[a-z]+)\s+)?timer(?P<on2>)",
      r"how\s+is\s+(?:the|my)\s+timer(?:\s+(?:doing|going))?(?P<on3>)",
      r"(?:time|how much)\s+(?:left|remaining)\s+on\s+(?:the|my)\s+timer(?P<on4>)",
      r"how\s+long\s+(?:until|till|before)\s+(?:the|my)\s+timer(?:\s+(?:is done|ends|goes off|finishes|rings))?(?P<on5>)")
def _timer_status(m, ctx):
    explicit = "timer" in m.group(0) or "countdown" in m.group(0)
    if not explicit and not ctx.has_timers:
        return None                                    # "how much time is left" with no timer: not ours
    return {}


# == apps and Settings ========================================================================
COMMON_APPS = {
    "spotify": "Spotify", "chrome": "Google Chrome", "google chrome": "Google Chrome", "edge": "Microsoft Edge",
    "microsoft edge": "Microsoft Edge", "firefox": "Firefox", "notepad": "Notepad", "calculator": "Calculator",
    "calc": "Calculator", "file explorer": "File Explorer", "explorer": "File Explorer", "files": "File Explorer",
    "my files": "File Explorer", "settings": "Settings", "windows settings": "Settings", "paint": "Paint",
    "word": "Word", "microsoft word": "Word", "excel": "Excel", "microsoft excel": "Excel",
    "powerpoint": "PowerPoint", "outlook": "Outlook", "teams": "Microsoft Teams", "microsoft teams": "Microsoft Teams",
    "discord": "Discord", "steam": "Steam", "slack": "Slack", "telegram": "Telegram", "whatsapp": "WhatsApp",
    "vs code": "Visual Studio Code", "vscode": "Visual Studio Code", "visual studio code": "Visual Studio Code",
    "obs": "OBS Studio", "zoom": "Zoom", "snipping tool": "Snipping Tool",
    "photos": "Photos", "camera": "Camera", "calendar app": "Calendar", "mail app": "Mail",
    "store": "Microsoft Store", "microsoft store": "Microsoft Store", "vlc": "VLC media player",
    "media player": "Media Player", "notion": "Notion", "obsidian": "Obsidian", "figma": "Figma",
    "premiere": "Adobe Premiere Pro", "premiere pro": "Adobe Premiere Pro", "photoshop": "Adobe Photoshop",
    "after effects": "Adobe After Effects", "blender": "Blender", "task manager": "Task Manager",
    "sticky notes": "Sticky Notes", "spotify app": "Spotify",
}
_APP_VERB = r"(?:open|launch|start|fire up|bring up|load up|pull up|boot up|start up|open up)"


@rule("app.open", _APP_VERB + r"\s+(?:up\s+)?(?:the\s+|my\s+)?(?:app\s+)?(?P<app>[a-z0-9 .]+?)"
      r"(?:\s+(?:app|application|program|for me))?")
def _app(m, ctx):
    from ..mcp_windows.apps import BLOCKED_APPS
    want = _g(m, "app")
    if not want or BLOCKED_APPS.search(want):
        return None
    name = resolve_app(want, ctx.apps)
    if name is None or BLOCKED_APPS.search(name):
        return None
    return {"app": name}


def resolve_app(want: str, apps: dict) -> str | None:
    """A spoken app name -> the Start-menu name to launch, or None. Only exact names,
    known aliases, a whole-word part of a short name ("chrome" -> "Google Chrome") or
    a very close spelling: "open the pod bay doors" must not open anything."""
    import difflib
    w = " ".join(want.split())
    pool = dict(apps) if apps else {}
    if not pool:
        pool = {k: v for k, v in COMMON_APPS.items()}
    else:
        for k, v in COMMON_APPS.items():                # aliases that point at an installed app
            if _norm_app(v) in pool and k not in pool:
                pool[k] = pool[_norm_app(v)]
    if w in pool:
        return pool[w]
    if len(w) >= 4:
        hits = sorted({v for k, v in pool.items() if len(k.split()) <= 3 and f" {w} " in f" {k} "}, key=len)
        if len(hits) == 1:
            return hits[0]
    if len(w) >= 6:
        close = difflib.get_close_matches(w, list(pool), n=2, cutoff=0.9)
        if len(close) == 1 or (close and pool[close[0]] == pool[close[-1]]):
            return pool[close[0]]
    return None


def _norm_app(s: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", s.lower()).split())


def _settings_pages() -> dict:
    from ..mcp_windows.screen import PAGES
    return PAGES


@rule("settings.open",
      r"(?:show me|open|show|bring up|pull up|go to|take me to|launch)\s+(?:the\s+|my\s+)?(?:windows\s+)?"
      r"(?P<p>[a-z ]+?)\s+settings(?:\s+page)?",
      r"(?:show me|open|show|bring up|pull up|go to|take me to)\s+(?:the\s+)?settings\s+(?:for|page for|of)\s+"
      r"(?:the\s+|my\s+)?(?P<p>[a-z ]+?)")
def _settings(m, ctx):
    p = _g(m, "p")
    pages = _settings_pages()
    if p in pages:
        return {"page": p}
    if p.replace(" ", "") in pages:
        return {"page": p.replace(" ", "")}
    return None


@rule("app.open", r"(?:open|launch|show|bring up|pull up|start)\s+(?:the\s+|windows\s+|the windows\s+)?settings(?:\s+app)?")
def _settings_app(m, ctx):
    return {"app": resolve_app("settings", ctx.apps) or "Settings"}


# == lock, clock ==============================================================================
@rule("screen.lock",
      r"lock\s+(?:the\s+|my\s+|this\s+)?(?:pc|computer|laptop|screen|workstation|machine|windows|desktop|device|system)"
      r"(?:\s+(?:now|up))?",
      r"lock\s+screen")
def _lock(m, ctx):
    return {}


@rule("clock.time",
      r"what\s+(?:is\s+)?(?:the\s+)?(?:current\s+|exact\s+)?time(?:\s+is\s+it)?(?:\s+(?:here|now|right now|currently|"
      r"at the moment))?",
      r"(?:what|which)\s+time\s+(?:is\s+it|do we have|have we got|is it now)(?:\s+(?:now|right now))?",
      r"(?:tell me|give me|do you (?:know|have)|have you got|got)\s+(?:what\s+)?(?:the\s+)?(?:current\s+)?time"
      r"(?:\s+(?:it is|is it))?",
      r"(?:do you know\s+)?what\s+time\s+it\s+is(?:\s+(?:now|right now))?",
      r"(?:the|current)\s+time(?:\s+check)?",
      r"time\s+check",
      r"what\s+(?:is\s+)?the\s+clock\s+say(?:ing)?|what\s+does\s+the\s+clock\s+say")
def _time(m, ctx):
    return {}


@rule("clock.date",
      r"what\s+(?:is\s+)?(?:the\s+|todays\s+|today s\s+)?date(?:\s+(?:today|is it|is it today|now))?",
      r"what\s+day\s+is\s+(?:it|today)(?:\s+today)?",
      r"what\s+day\s+of\s+the\s+(?:week|month)\s+is\s+(?:it|today)(?:\s+today)?",
      r"what\s+is\s+today(?:s date)?",
      r"(?:tell me|give me)\s+(?:the|todays)\s+date",
      r"todays\s+date")
def _date(m, ctx):
    return {}


# == radios ===================================================================================
_RADIO = r"(?P<r>wifi|bluetooth|wireless)"


@rule("radio.set",
      r"(?:turn|switch|toggle|put|flip|set)\s+(?:the\s+|my\s+)?" + _RADIO + r"(?:\s+(?:radio|connection))?\s+"
      r"(?P<s>on|off|back on)",
      r"(?:turn|switch|flip)\s+(?P<s>on|off)\s+(?:the\s+|my\s+)?" + _RADIO + r"(?:\s+(?:radio|connection))?",
      r"(?P<s>enable|disable|activate|deactivate|kill)\s+(?:the\s+|my\s+)?" + _RADIO + r"(?:\s+(?:radio|connection))?",
      _RADIO + r"\s+(?P<s>on|off|back on)",
      r"(?P<s>toggle)\s+(?:the\s+)?" + _RADIO)
def _radio(m, ctx):
    if not ctx.radios:
        return None
    r = _g(m, "r")
    r = "wifi" if r == "wireless" else r
    s = _g(m, "s")
    on = None if s == "toggle" else S.parse_onoff(s)
    if on is None and s != "toggle":
        return None
    return {"radio": r, "on": on}


# == teach ====================================================================================
@rule("learn.teach",
      r"(?:teach|learn)\s+(?:it|that|this)(?:\s+(?:one|phrase|phrasing|command|request))?",
      r"(?:learn|remember)\s+that\s+(?:phrase|phrasing|command|request|wording)",
      r"(?:add|save)\s+that\s+(?:phrase|phrasing|command|wording)")
def _teach(m, ctx):
    return {}


# == follow-ups on the last instant command ====================================================
@rule("context.more",
      r"(?:(?P<a>[a-z0-9 ]+?)\s+)?(?:more|further|higher|up)(?:\s+(?:please|again|still))?",
      r"(?:keep going|go (?:higher|further|up)|up\s+(?P<a2>[a-z0-9 ]+?)|turn it up more|more more)")
def _more(m, ctx):
    a = _g(m, "a") or _g(m, "a2")
    amt = S.parse_amount(a, ctx.step)
    if amt is None:
        return None
    return {"direction": "up", "amount": amt}


@rule("context.less",
      r"(?:(?P<a>[a-z0-9 ]+?)\s+)?(?:less|lower|down)(?:\s+(?:please|again|still))?",
      r"(?:go (?:lower|down)|down\s+(?P<a2>[a-z0-9 ]+?)|back (?:it )?down(?:\s+(?P<a3>[a-z0-9 ]+?))?)")
def _less(m, ctx):
    a = _g(m, "a") or _g(m, "a2") or _g(m, "a3")
    amt = S.parse_amount(a, ctx.step)
    if amt is None:
        return None
    return {"direction": "down", "amount": amt}


@rule("context.again",
      r"(?:do\s+(?:it|that|this)\s+)?again(?:\s+please)?",
      r"once more|one more time|one more|same again|and again|do that one more time")
def _again(m, ctx):
    return {}


@rule("context.reverse",
      r"(?:no\s+|oops\s+|wait\s+|sorry\s+)*(?:the\s+)?(?:other|opposite)\s+(?:way|direction)(?:\s+round)?",
      r"(?:no\s+|oops\s+|wait\s+)*(?:the\s+)?opposite",
      r"(?:no\s+|oops\s+)*(?:wrong way|not that way)")
def _reverse(m, ctx):
    return {}


@rule("context.set",
      r"(?:set|make|put|turn|bring|change|take)\s+(?:it|that)\s+(?:(?:up|down|back)\s+)?(?:to|at)\s+" + NUM +
      r"(?:\s+(?:percent|per cent))?",
      r"make\s+it\s+" + NUM + r"(?:\s+(?:percent|per cent))?",
      r"(?:to\s+)?" + NUM + r"\s+(?:percent|per cent)",
      r"(?P<bare>[0-9]{1,3}|[a-z]+(?: [a-z]+)?)")
def _ctx_set(m, ctx):
    n = _g(m, "n") or _g(m, "bare")
    lv = S.parse_level(n)
    if lv is None or n in ("up", "down", "off", "on", "none", "nothing", "top", "full", "min", "mid", "middle"):
        return None
    return {"level": lv}


CONTEXT_INTENTS = ("context.more", "context.less", "context.again", "context.reverse", "context.set")


def parse(t: str, ctx: Ctx):
    """(intent, slots) for the first rule that matches all of `t` and whose slots parse."""
    for r in RULES:
        if r.intent in CONTEXT_INTENTS:
            continue
        m = r.rx.fullmatch(t)
        if m is None:
            continue
        sl = r.build(m, ctx)
        if sl is not None:
            return r.intent, sl
    return None


def parse_context(t: str, ctx: Ctx):
    """The follow-up rules ("a bit more", "again", "the other way", "make it 40")."""
    for r in RULES:
        if r.intent not in CONTEXT_INTENTS:
            continue
        m = r.rx.fullmatch(t)
        if m is None:
            continue
        sl = r.build(m, ctx)
        if sl is not None:
            return r.intent, sl
    return None
