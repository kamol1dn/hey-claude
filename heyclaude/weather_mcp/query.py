"""Which spoken requests are plain weather questions (the local weather route).

`parse(text)` -> a `WeatherQuery` (place or home, when, focus), or None for
anything that isn't plainly about the weather, which then goes to the quick
model or the tool agent as before. It is strict on purpose: every word of the
request has to be accounted for (a weather word, a time, a place, filler), so
"whether or not", "weather the storm", "I'm under the weather", "what's the
CPU temperature" and "can I take a rain check" all fall through.

    parse("will it rain in Paris tomorrow?")
    -> WeatherQuery(place="Paris", when="tomorrow", focus="rain")

After a weather answer, short follow-ups ("and tomorrow?", "what about
London?", "and the air quality?") take the rest from the previous query
(`prev`); "there" means the previous place.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace

DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
HOME = "home"          # the configured home place
THERE = "@there"       # "what's the weather there": the previous weather answer's place


@dataclass(frozen=True)
class WeatherQuery:
    text: str
    place: str = HOME            # a place name, or HOME
    when: str = "now"            # now | today | tonight | tomorrow | day (a weekday, the day after tomorrow) | weekend | week
    day: int = 0                 # days from today (tomorrow 1, Friday 0..6)
    part: str = ""               # morning | afternoon | evening | night (of `day`)
    focus: str = "general"       # general | rain | umbrella | snow | temp | wind | alerts | air | uv | pollen | sun | moon
    sub: str = ""                # sun: rise | set

    @property
    def home(self) -> bool:
        return self.place == HOME


_CONTRACTIONS = [
    (r"\bwhat'?s\b", "what is"), (r"\bhow'?s\b", "how is"), (r"\bwhen'?s\b", "when is"), (r"\bit'?s\b", "it is"),
    (r"\bit'll\b", "it will"), (r"\bgonna\b", "going to"), (r"\bi'?m\b", "i am"), (r"\bisn'?t\b", "is not"),
    (r"\bwill'?nt\b|\bwon'?t\b", "will not"), (r"\bdoes'?nt\b|\bdoesn'?t\b", "does not"), (r"\bthere'?s\b", "there is"),
    (r"\bwhat'?ll\b", "what will"), (r"\bdo i\b", "do i"), (r"\b(today|tomorrow|tonight|week|weekend)'s\b", r"\1"),
    (r"\bweather'?s\b", "weather is"), (r"\bforecast'?s\b", "forecast is"), (r"\bu\.?v\.?\b", "uv"),
    (r"\bsun ?rise\b", "sunrise"), (r"\bsun ?set\b", "sunset"), (r"\bhay ?fever\b", "hayfever"),
]
# a polite or chatty front, taken off before parsing
_FRONT = re.compile(
    r"^(?:(?:hey|hi|ok|okay|so|and|um+|uh+|well|right|quick question|just|please|claude|hey claude|"
    r"can you|could you|would you|will you|can you please|could you please|please tell me|tell me|let me know|"
    r"do you know|i want to know|i would like to know|i wonder|i was wondering|check|look up|show me|"
    r"give me)[, ]+)+")
_BACK = re.compile(r"(?:[, ]+(?:please|thanks|thank you|for me|then|again|today please))+$")

# things that use weather words but aren't weather questions
_NOT = re.compile(
    r"\b(?:under the weather|rain ?check|whether|"
    r"(?:a|my|the) cold\b(?! (?:day|night|morning|evening|front|snap|spell|wind))|cold (?:feet|shoulder|case|call|brew)|"
    r"hot (?:dog|take|sauce|spot|key|keys|fix|reload|tub|water)|warm (?:up|welcome|regards)|"
    r"brain ?storm|storm (?:in|out|off)|"
    r"joke|poem|song|story|movie|film|book|game|lyrics|meaning|define|definition|translate|explain|"
    r"why|history|climate change|in here|in this room|indoors|inside|cpu|gpu|computer|laptop|pc|oven|fridge|"
    r"water|coffee|tea|room|house|office temperature|body|fever)\b")

# after the time phrase is out: "weather the storm" (not "weather this week"), numbers, small talk
_NOT_AFTER = re.compile(r"\bweather (?:the|a|an|this|that|it|out|through)\b|\d|"
                        r"^(?:nice|good|bad|great|lovely|beautiful|terrible|awful|crazy|weird|strange|perfect) weather$")
_PART = r"(?:morning|afternoon|evening|night)"
_PREP = r"(?:(?:for|on|over|during|in|by|until|through|throughout|later|at)\s+)?"
# (pattern, when, day, part); `day` None = from the weekday name
_WHEN = [
    (r"(?:right now|just now|at the moment|currently|now|at this moment)", "now", 0, ""),
    (_PREP + r"(?:the )?day after tomorrow", "day", 2, ""),
    (_PREP + r"tomorrow (?P<part>" + _PART + r")", "tomorrow", 1, None),
    (_PREP + r"tomorrow", "tomorrow", 1, ""),
    (_PREP + r"(?:later )?tonight|" + _PREP + r"(?:this|later this) (?:evening|night)|overnight", "tonight", 0, "night"),
    (_PREP + r"(?:this|later this) (?P<part>morning|afternoon)", "today", 0, None),
    (_PREP + r"(?:the )?(?:this |coming |next )?weekend", "weekend", None, ""),
    (_PREP + r"(?:the )?(?:rest of the|this|coming) week|" + _PREP + r"the week(?: ahead)?|"
     r"(?:for |over |in )?the (?:next|coming) (?:few |couple of |several )?days|(?:for )?this week", "week", 0, ""),
    (_PREP + r"(?:this |coming |next )?(?P<wd>" + "|".join(DAYS) + r")(?: (?P<part>" + _PART + r"))?", "day", None, None),
    (_PREP + r"(?:later )?today|later on|later", "today", 0, ""),
]
_WHEN = [(re.compile(r"\b" + p + r"\b"), w, d, pt) for p, w, d, pt in _WHEN]

_HOMEWORDS = {"here", "home", "my area", "the area", "this area", "my city", "the city", "my town", "town", "my place",
              "my location", "where i am", "where i live", "outside", "out", "around here", "near me", "my house"}
_PLACE = re.compile(r"\b(?:over in|out in|in|for|at|around|near)\s+(?P<place>[a-z][a-z .'\-]{0,40}?)$")

# words a weather question may contain besides its time and place
_OK = set("""
what how when is it will would be going to the a an any there are do does did i need should take bring wear
like look looks looking outside out weather forecast forecasts temperature temp temps degrees current currently
rain raining rainy rains rained drizzle drizzling showers shower precipitation wet umbrella
snow snowing snowy sleet hail storm stormy storms thunderstorm thunderstorms thunder lightning coming
jacket coat sunny cloudy cloud clouds foggy fog misty mist windy wind breezy gusty hot cold warm chilly freezing frosty
frost humid humidity muggy chance get gets getting me please now right here my local today tonight
alerts alert warnings warning severe air quality aqi pollution smog uv index level levels count sunscreen sunburn
pollen hayfever allergy allergies sunrise sunset sun up down come comes go goes dark daylight light
moon phase full new expected expect supposed forecasted predicted and or so ok okay hey claude also still
much report update conditions sky clear nice time at like feel feels kind sort tell check probability heat
high low highs lows going outdoors yet anything bad good nasty not
""".split())
_STRONG = re.compile(
    r"\b(?:weather|forecasts?|temperature|temps?|rain\w*|drizzl\w*|showers?|precipitation|umbrella|snow\w*|sleet|hail|"
    r"storm\w*|thunder\w*|lightning|uv|sunscreen|sunburn|pollen|hayfever|sunrise|sunset|moon|air quality|aqi|"
    r"pollution|smog|humidity|degrees|frost\w*|sun (?:come|comes|go|goes) (?:up|down)|sun (?:rise|set)|"
    r"(?:get|gets|getting) dark)\b")
_WEAK = re.compile(r"\b(?:hot|cold|warm|chilly|freezing|windy|wind|breezy|gusty|sunny|cloudy|foggy|misty|humid|muggy|"
                   r"jacket|coat|dark|daylight|the air|heat)\b")
_ALERT = re.compile(r"\b(?:alerts?|warnings?)\b")

_FOCUS = [
    ("alerts", r"\b(?:alerts?|warnings?|severe weather)\b"),
    ("air", r"\b(?:air quality|aqi|pollution|smog|the air)\b"),
    ("uv", r"\b(?:uv|sunscreen|sunburn)\b"),
    ("pollen", r"\b(?:pollen|hayfever|allerg\w*)\b"),
    ("moon", r"\bmoon\b"),
    ("sun", r"\b(?:sunrise|sunset|sun (?:come|comes|go|goes) (?:up|down)|sun (?:rise|set)|get dark|gets dark|"
            r"getting dark|dark|daylight|light out)\b"),
    ("umbrella", r"\bumbrella\b"),
    ("snow", r"\b(?:snow\w*|sleet|hail|frost\w*)\b"),
    ("rain", r"\b(?:rain\w*|drizzl\w*|showers?|precipitation|wet|storm\w*|thunder\w*|lightning)\b"),
    ("temp", r"\b(?:temperature|temps?|degrees|hot|cold|warm|chilly|freezing|jacket|coat|heat|highs?|lows?)\b"),
    ("wind", r"\b(?:wind|windy|breezy|gusty)\b"),
]
_FOCUS = [(n, re.compile(p)) for n, p in _FOCUS]
# "and tomorrow?", "what about Paris", "how about the weekend" (only right after a weather answer)
_FOLLOW = re.compile(r"^(?:and|what about|how about|and what about|and how about|what is it like|and in|and for|"
                     r"same for|what about in|how about in)\b\s*")
_NOT_PLACE = set("""
the it that this those them him her me you us my your and or of to with about what how when is be will there
weather forecast temperature rain snow wind air quality moon sun sunset sunrise uv pollen alerts warnings outside
now today tonight tomorrow week weekend morning afternoon evening night later
thanks thank ok okay cool great nice yes no yeah nope sure stop bye good perfect alright fine please
""".split())


def _norm(text: str) -> str:
    s = (text or "").lower().replace("’", "'").replace("‘", "'")
    s = re.sub(r"[^a-z0-9' .,\-]+", " ", s)
    for p, r in _CONTRACTIONS:
        s = re.sub(p, r, s)
    s = re.sub(r"[.,]+(?=\s|$)", " ", s)
    s = " ".join(s.split())
    for _ in range(3):
        s2 = _FRONT.sub("", s).strip()
        s2 = _BACK.sub("", s2).strip()
        if s2 == s:
            break
        s = s2
    return s


def _take_when(s: str, today_wd: int):
    """(rest, when, days ahead, part) with the time phrase taken out."""
    for rx, when, day, part in _WHEN:
        m = rx.search(s)
        if not m:
            continue
        gd = m.groupdict()
        pt = part if part is not None else (gd.get("part") or "")
        d = day
        if when == "day" and day is None:
            d = (DAYS.index(gd["wd"]) - today_wd) % 7
        if when == "weekend":
            d = 0 if today_wd == 6 else (5 - today_wd) % 7
        rest = (s[:m.start()] + " " + s[m.end():]).strip()
        rest = re.sub(r"\b(?:for|on|over|during|in|by|at)\s*$", "", rest).strip()
        return " ".join(rest.split()), when, d, pt
    return s, "", 0, ""


def _take_place(s: str):
    """(rest, place or "", via_preposition)"""
    m = _PLACE.search(s)
    if m:
        p = m.group("place").strip(" .'-")
        p = re.sub(r"^(?:the city of|the town of)\s+", "", p)
        if p in _HOMEWORDS:
            return s[:m.start()].strip(), HOME, True
        if p == "there":
            return s[:m.start()].strip(), THERE, True
        words = p.split()
        if 0 < len(words) <= 4 and not any(w in _NOT_PLACE or w in DAYS for w in words):
            return s[:m.start()].strip(), p, True
    for hw in sorted(_HOMEWORDS, key=len, reverse=True):          # "is it cold outside", "weather near me"
        if re.search(r"\b" + hw + r"$", s) and hw not in ("out",):
            return s[: -len(hw)].strip(), "", False
    if re.search(r"\bthere$", s) and not re.search(r"\b(?:is|are|will|be) there$", s):
        return s[:-5].strip(), THERE, False
    return s, "", False


def _bare_place(s: str):
    """"london weather", "weather london": a place without a preposition."""
    m = re.match(r"^(?P<p>[a-z][a-z .'\-]{1,30}?) (?:weather|forecast)$", s) or \
        re.match(r"^(?:weather|forecast) (?P<p>[a-z][a-z .'\-]{1,30})$", s)
    if not m:
        return s, ""
    words = m.group("p").split()
    if len(words) > 3 or any(w in _NOT_PLACE or w in DAYS for w in words):
        return s, ""
    return ("weather"), m.group("p")


def _focus(s: str) -> tuple[str, str]:
    for name, rx in _FOCUS:
        m = rx.search(s)
        if m:
            sub = ""
            if name == "sun":
                t = m.group(0)
                sub = "rise" if re.search(r"rise|up", t) else "set"
            return name, sub
    return "general", ""


def _title(place: str, original: str) -> str:
    m = re.search(r"[\s,\-]+".join(re.escape(w) for w in place.split()), original, re.I)
    raw = m.group(0) if m else place
    return " ".join(w if any(c.isupper() for c in w) else w.capitalize() for w in raw.split()).strip(" ,")


def parse(text: str, prev: WeatherQuery | None = None, today_wd: int | None = None) -> WeatherQuery | None:
    if today_wd is None:
        from datetime import date
        today_wd = date.today().weekday()
    s = _norm(text)
    if not s or len(s) > 140 or _NOT.search(s):
        return None
    follow, lead = None, False
    if prev is not None:
        m = _FOLLOW.match(s)
        lead = bool(m or re.match(r"^\W*(?:and|what about|how about|same for)\b", (text or "").lower()))
        if m or len(s.split()) <= 3:
            follow = s[m.end():] if m else s
    rest, when, day, part = _take_when(s, today_wd)
    if re.search(r"\bnext week\b", rest) or _NOT_AFTER.search(rest):
        return None                                  # next week: beyond the card; the agent can do it
    rest, place, _ = _take_place(rest)
    focus, sub = _focus(s)
    strong, weak = bool(_STRONG.search(s)), bool(_WEAK.search(s))
    if _ALERT.search(s) and not strong and not re.search(r"\b(?:severe|heat|wind|flood)\b", s):
        return None                                  # "any alerts?" could be anything
    if not strong and not weak and follow is None:
        return None
    if not strong and weak and follow is None:
        # "is it cold outside", "how hot is it in Rome", "will it be windy tomorrow"; not "do you like it hot"
        if not (place or when or re.search(r"\b(?:is it|it is|will it|it will|be|should i|do i need)\b", rest + " ")):
            return None
    if not place:
        rest2, bare = _bare_place(rest)
        if bare:
            rest, place = rest2, bare
    left = [w for w in re.findall(r"[a-z']+", rest) if w not in _OK]
    if follow is not None and (left or not (strong or weak)):
        # "and tomorrow?" / "what about Paris" / "how about the weekend"
        f_rest, f_when, f_day, f_part = _take_when(follow, today_wd)
        f_rest, f_place, _ = _take_place(f_rest)
        f_focus, f_sub = _focus(follow)
        f_rest = re.sub(r"^(?:in|for|at)\s+", "", f_rest).strip()
        words = f_rest.split()
        if lead and not f_place and words and len(words) <= 3 and not any(w in _NOT_PLACE or w in DAYS for w in words):
            f_place, f_rest = f_rest, ""
        f_left = [w for w in re.findall(r"[a-z']+", f_rest) if w not in _OK]
        if f_left or not (f_when or f_place or f_focus != "general"):
            return None
        place = f_place or prev.place
        if place == THERE:
            place = prev.place
        return replace(prev, text=text, place=_title(place, text) if place not in (HOME, THERE) and place != prev.place else place,
                       when=f_when or prev.when, day=f_day if f_when else prev.day, part=f_part if f_when else prev.part,
                       focus=f_focus if f_focus != "general" else prev.focus, sub=f_sub if f_focus != "general" else prev.sub)
    if left:
        return None
    if follow is not None:                           # "and the air quality?": the same place and time
        place = place or prev.place
        if not when:
            when, day, part = prev.when, prev.day, prev.part
    if place == THERE:
        if prev is None:
            return None                              # "there" from another tier's answer: the agent has the context
        place = prev.place
    elif place and place != HOME:
        place = _title(place, text)
    if not when:
        when = "now" if focus in ("general", "temp", "wind") else "today"
    return WeatherQuery(text=text, place=place or HOME, when=when, day=day if day is not None else 0, part=part,
                        focus=focus, sub=sub)
