"""Offline tests for the local weather route (ROADMAP M5b, weather-mcp): which
requests are plain weather questions, reading weather-mcp's real (recorded)
answers, the card and the spoken summary built from them, the warm MCP client
against a fake stdio server (tests/fixtures/fake_weather_mcp.py), the
built-in Open-Meteo fallback when weather-mcp is slow or missing, the routing
order, the turn in the assistant, the agent's registration and the card on
the live QML island. No network, no npm.

    .venv\\Scripts\\python tests\\test_weather_mcp.py
"""
import faulthandler
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
faulthandler.enable()
sys.stdout.reconfigure(line_buffering=True)
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from heyclaude import widgets as W  # noqa: E402
from heyclaude import weather_mcp as WM  # noqa: E402
from heyclaude.weather_mcp import card as C  # noqa: E402
from heyclaude.weather_mcp import parse as P  # noqa: E402
from heyclaude.weather_mcp.query import HOME, parse  # noqa: E402

FX = ROOT / "tests" / "fixtures" / "weather_mcp"
FAKE = ROOT / "tests" / "fixtures" / "fake_weather_mcp.py"
MON = 0                                                     # the recordings were made on a Monday (London time)


def _md(name: str) -> str:
    return P.text_of(json.loads((FX / name).read_text(encoding="utf-8")))


def _facts() -> dict:
    parts = {"current": _md("get_current_conditions.json"), "daily": _md("get_forecast_daily.json"),
             "hourly": _md("get_forecast_hourly.json"), "alerts": _md("get_alerts.json"),
             "air": _md("get_air_quality.json"), "nowcast": _md("get_forecast_minutely.json")}
    f = WM.WeatherService._facts_from(parts, "C")
    f["location"] = "London, United Kingdom"
    return f


def _q(text, **kw):
    return parse(text, today_wd=MON, **kw)


# ------------------------------------------------------------------ which requests are weather questions
POSITIVE = {
    "What's the weather today?": (HOME, "today", "general"),
    "Can you check what the weather is going to be like later this afternoon?": (HOME, "today", "general"),
    "what's the weather like in London right now": ("London", "now", "general"),
    "weather": (HOME, "now", "general"),
    "forecast for the weekend": (HOME, "weekend", "general"),
    "will it rain tomorrow": (HOME, "tomorrow", "rain"),
    "do I need an umbrella": (HOME, "today", "umbrella"),
    "is it cold outside": (HOME, "now", "temp"),
    "how hot is it in Rome": ("Rome", "now", "temp"),
    "what's the temperature": (HOME, "now", "temp"),
    "any weather alerts": (HOME, "today", "alerts"),
    "are there any weather warnings for Lisbon": ("Lisbon", "today", "alerts"),
    "what's the air quality": (HOME, "today", "air"),
    "how's the air today": (HOME, "today", "air"),
    "what's the UV index": (HOME, "today", "uv"),
    "pollen count today": (HOME, "today", "pollen"),
    "when's sunset": (HOME, "today", "sun"),
    "what time is sunrise tomorrow": (HOME, "tomorrow", "sun"),
    "when does it get dark": (HOME, "today", "sun"),
    "what's the moon phase tonight": (HOME, "tonight", "moon"),
    "will it snow this weekend": (HOME, "weekend", "snow"),
    "weather in New York on Friday": ("New York", "day", "general"),
    "Tashkent weather": ("Tashkent", "now", "general"),
    "hey claude, what's the weather like tomorrow in Paris, France?": ("Paris, France", "tomorrow", "general"),
    "is it going to be windy tomorrow": (HOME, "tomorrow", "wind"),
    "should I wear a jacket": (HOME, "now", "temp"),
    "what's the forecast this week": (HOME, "week", "general"),
    "Is it going to rain in the next few days?": (HOME, "week", "rain"),
}
NEAR_MISSES = [
    "whether or not", "we'll weather the storm", "I'm under the weather", "can I take a rain check",
    "what's the CPU temperature", "do you like it hot or cold", "set the temperature to 20 degrees",
    "tell me a joke about the weather", "what's the weather there", "any alerts?", "what is the capital of France",
    "is it cold in here", "I have a cold", "what's the weather like on Mars", "why is the sky blue",
    "how does weather work", "weather next week", "open the weather app", "volume 30", "purple rain by prince",
    "what's the temperature of the sun", "write a poem about rain", "brainstorm some ideas", "nice weather",
    "what's on my calendar today", "what time is it", "set a timer for 5 minutes", "is the coffee hot",
    "whether it rains or not, we're going",
]


def test_plain_weather_questions_are_claimed():
    for text, (place, when, focus) in POSITIVE.items():
        q = _q(text)
        assert q is not None, text
        assert (q.place, q.when, q.focus) == (place, when, focus), (text, q)
    assert _q("weather in New York on Friday").day == 4                  # Monday -> Friday
    assert _q("will it snow this weekend").day == 5                      # Monday -> Saturday
    assert _q("what time is sunrise tomorrow").sub == "rise" and _q("when's sunset").sub == "set"
    assert _q("Can you check what the weather is going to be like later this afternoon?").part == "afternoon"


def test_near_misses_fall_through():
    for text in NEAR_MISSES:
        assert _q(text) is None, (text, _q(text))


def test_follow_ups_take_the_rest_from_the_previous_weather_question():
    prev = _q("what's the weather in London")
    assert (_q("and tomorrow?", prev=prev).place, _q("and tomorrow?", prev=prev).when) == ("London", "tomorrow")
    assert _q("what about Paris", prev=prev).place == "Paris"
    assert _q("and in Berlin?", prev=prev).place == "Berlin"
    assert _q("how about the weekend", prev=prev).when == "weekend"
    assert _q("and the air quality?", prev=prev).focus == "air" and _q("and the air quality?", prev=prev).place == "London"
    assert _q("what's the weather there", prev=prev).place == "London"           # "there" = the last weather place
    for text in ("thanks", "volume 30", "what is the capital of France", "ok"):
        assert _q(text, prev=prev) is None, text
    rain = _q("will it rain in Oslo tomorrow")
    assert _q("and on Friday?", prev=rain).focus == "rain" and _q("and on Friday?", prev=rain).place == "Oslo"


# ------------------------------------------------------------------ weather-mcp's answers, read
def test_each_recorded_answer_is_read():
    cur = P.current(_md("get_current_conditions.json"))
    assert cur["temp"] == 8 and cur["feels"] == 5 and cur["condition"] == "Overcast" and cur["time"] == "19:30"
    assert cur["gust"] == 25 and cur["date"] == "2026-11-09"
    days = P.daily(_md("get_forecast_daily.json"))
    assert [d["date"] for d in days] == ["2026-11-09", "2026-11-10", "2026-11-11"]
    assert days[1]["pop"] == 93 and days[1]["icon"] == "rain" and days[0]["sunset"] == "16:20"
    assert days[2]["moon"] == {"phase": "Waxing Crescent", "illum": 5.0, "rise": "10:02", "set": "16:51"}
    assert P.next_full_moon(_md("get_forecast_daily.json")) == "Nov 24"
    hrs = P.hourly(_md("get_forecast_hourly.json"))
    assert len(hrs) == 24 and hrs[0]["time"] == "00:00" and hrs[-1]["condition"] == "Partly cloudy"
    nc = P.nowcast(_md("get_forecast_minutely.json"))
    assert nc["covered"] and len(nc["steps"]) == 8 and nc["times"][0] == "20:30" and nc["model"].startswith("ICON-D2")
    assert P.nowcast(_md("get_forecast_minutely_not_covered.json")) == {"covered": False}
    al = P.alerts(_md("get_alerts.json"))
    a = al["items"][0]
    assert al["count"] == 1 and al["region"] == "United Kingdom" and al["country_level"]
    assert a["title"] == "Yellow fog warning" and a["severity"] == "moderate" and a["level"] == "yellow"
    assert a["sender"] == "UK Met Office" and "London" in a["area"] and a["expires"].startswith("2026-11-10 10:00")
    assert P.alerts(_md("get_alerts_not_covered.json")) == {"covered": False}
    air = P.air(_md("get_air_quality.json"))
    assert air["aqi"] == 57 and air["scale"] == "EU" and air["label"] == "Moderate" and air["color"] == "yellow"
    assert air["pollen"]["level"] == "none"
    s = P.summary(_md("get_weather_summary.json"))
    assert s["now"]["temp"] == 8 and len(s["days"]) == 3 and s["alerts"]["count"] == 1 and s["air"]["aqi"] == 57
    assert P.location_of(_md("search_location.json")) == (51.5074456, -0.1277653)
    assert P.place_name_of(_md("search_location.json")).endswith("United Kingdom")


def test_the_card_and_the_summary_from_the_recorded_answers():
    f = _facts()
    schema = W.registry()["weather"]["schema"]
    d = C.card(f, _q("what's the weather in London"), "London, United Kingdom")
    assert W.validate(d, schema) == [], W.validate(d, schema)
    assert d["temp"] == 8 and d["icon"] == "cloud" and d["condition"] == "Overcast" and d["source"] == "weather-mcp"
    assert [h["time"] for h in d["hourly"]][:2] == ["20:00", "21:00"]                 # after 19:30, place time
    assert [x["day"] for x in d["days"]] == ["Today", "Tue", "Wed"]
    assert d["alert"] == {"title": "Yellow fog warning", "severity": "moderate", "level": "yellow",
                          "source": "UK Met Office", "until": "Tue 10:00"}                # it names London's area
    assert d["air"] == {"aqi": 57, "scale": "EU", "label": "Moderate", "color": "yellow", "uv": 1}
    assert d["sun"] == {"rise": "07:08", "set": "16:20"} and d["moon"]["phase"] == "New Moon"
    assert "nowcast" not in d                                                          # dry, and not asked
    rain = C.card(f, _q("will it rain in London"), "London")
    assert rain["nowcast"]["text"] == "No rain for 2 h" and rain["nowcast"]["steps"] == [0] * 8
    say = {
        "what's the weather in London": "In London, 8 degrees and overcast, feels like 5.",
        "will it rain tomorrow in London": "In London, rain likely tomorrow, 93% chance.",
        "do I need an umbrella in London": "In London, no rain expected today; you won't need an umbrella.",
        "any weather alerts in London": "UK Met Office has a yellow fog warning, until 10 am tomorrow.",
        "what's the air quality in London": "In London, air quality is moderate, European index 57.",
        "when's sunset in London": "Sunset was at 4:20 pm; tomorrow it's at 4:18 pm.",
        "what time is sunrise tomorrow in London": "Tomorrow, sunrise is at 7:09 am.",
        "weather in London this week": "In London, highs of 10 to 11 degrees this week, with rain likely on "
                                       "Tuesday and Wednesday.",
        "weather in London on Wednesday": "In London, on Wednesday: light rain, 8 to 11 degrees, 92% chance of rain.",
        "how windy is it in London": "In London, wind is 12 km/h, gusting to 25.",
    }
    for text, want in say.items():
        assert C.speak(f, _q(text), "London, United Kingdom") == want, (text, C.speak(f, _q(text), "London"))
    assert C.speak(f, _q("what's the moon phase"), "") .startswith("It's a new moon")
    # an alert that is about the whole country but not this place: on the card only when asked about alerts
    other = dict(f, location="Lisbon, Portugal")
    assert "alert" not in C.card(other, _q("weather in Lisbon"), "Lisbon, Portugal")
    assert C.card(other, _q("any weather alerts in Lisbon"), "Lisbon, Portugal")["alert"]["title"].endswith("United Kingdom")
    nc = {"covered": True, "steps": [0, 0, 2, 3, 3, 1, 0, 0], "times": ["20:30"]}
    assert C.nowcast_text(nc) == "Rain in 30 min" and C.nowcast_text({"steps": [3, 2, 0, 0]}) == "Rain now, easing in 30 min"
    wet = dict(f, nowcast=nc)
    assert C.speak(wet, _q("do I need an umbrella in London"), "London") == "In London, rain in 30 minutes. Take an umbrella."
    assert C.card(wet, _q("weather in London"), "London")["nowcast"]["text"] == "Rain in 30 min"


_FC = {"current": {"time": "2026-11-09T21:45", "temperature_2m": 15.2, "apparent_temperature": 13.9, "weather_code": 1,
                   "is_day": 0, "wind_speed_10m": 11.6},
       "hourly": {"time": [f"2026-11-09T{h:02d}:00" for h in range(24)] + [f"2026-11-10T{h:02d}:00" for h in range(24)],
                  "temperature_2m": [10 + h % 7 for h in range(48)], "weather_code": [61 if h % 5 == 0 else 2 for h in range(48)],
                  "is_day": [1 if 7 <= h % 24 <= 17 else 0 for h in range(48)],
                  "precipitation_probability": [70 if h % 5 == 0 else 5 for h in range(48)]},
       "daily": {"time": ["2026-11-09", "2026-11-10", "2026-11-11"], "weather_code": [61, 0, 3],
                 "temperature_2m_max": [20.1, 16.4, 18.0], "temperature_2m_min": [13.6, 9.2, 8.8],
                 "sunrise": ["2026-11-09T07:08", "2026-11-10T07:09", "2026-11-11T07:11"],
                 "sunset": ["2026-11-09T16:52", "2026-11-10T16:51", "2026-11-11T16:50"],
                 "uv_index_max": [2.1, 3.4, 1.0], "precipitation_probability_max": [80, 5, 20]}}


from heyclaude import weather as _WX  # noqa: E402
_FORECAST = _WX.forecast                                    # the real one (tests patch the module's name)


def _builtin(place="Sampletown", **kw):
    """The built-in Open-Meteo tool's answer, from canned API data (no network)."""
    _WX._cache.clear()
    _WX._places.clear()
    geo = {"results": [{"name": place, "latitude": 10.0, "longitude": 20.0, "country": "Exampleland"}]}
    return _FORECAST(place, fetch=lambda url, params, timeout=8.0: geo if "geocoding" in url else _FC, **kw)


def test_the_card_still_renders_from_the_built_in_fallback():
    d = _builtin(detail=True)
    f = C.from_builtin(d)
    f["moon"] = C.moon_phase(C._date("2026-11-09"))
    card = C.card(f, _q("what's the weather"), d["location"])
    assert W.validate(card, W.registry()["weather"]["schema"]) == []
    assert card["sun"] == {"rise": "07:08", "set": "16:52"} and card["air"] == {"uv": 2} and "moon" in card
    assert "alert" not in card and "nowcast" not in card
    assert C.speak(f, _q("what's the weather")) == "15 degrees and mostly clear. Rain likely from around 1 am."
    assert C.speak(f, _q("will it rain tomorrow")) == "No rain expected tomorrow, 5% chance."
    # the agent's built-in tool keeps its plain result (no detail keys), plus sunrise/sunset
    plain = _builtin()
    assert not any(k.startswith("_") for k in plain) and plain["sun"]["rise"] == "07:08"
    assert C.moon_phase(C._date("2026-11-09"))["phase"] == "New Moon" and str(C.next_full_moon(C._date("2026-11-09"))) == "2026-11-24"


def test_agent_tool_results_make_the_same_card():
    spec = W.from_tool("mcp__weather-mcp__get_weather_summary", {"city_name": "London"}, None, _md("get_weather_summary.json"))
    assert spec["widget"] == "weather" and spec["data"]["temp"] == 8 and spec["data"]["location"] == "London"
    assert spec["data"]["air"]["aqi"] == 57 and len(spec["data"]["days"]) == 3
    cur = W.from_tool("mcp__weather-mcp__get_current_conditions", {"latitude": 51.5074, "longitude": -0.1278}, None,
                      _md("get_current_conditions.json"))
    assert cur["data"]["temp"] == 8 and cur["data"]["location"] == "51.51, -0.13"
    assert W.from_tool("mcp__weather-mcp__get_alerts", {}, None, _md("get_alerts.json")) is None       # no temperature
    assert W.from_tool("mcp__weather-mcp__get_forecast", {}, None, _md("get_forecast_daily.json")) is None
    assert W.from_tool("mcp__other__get_weather_summary", {}, None, _md("get_weather_summary.json")) is None
    built = _builtin()
    assert W.from_tool("mcp__weather__get_weather", {}, built)["data"]["temp"] == 15                   # the built-in still


# ------------------------------------------------------------------ the warm client and the fallback
def _cfg(tmp, **mcp):
    m = {"command": [sys.executable, str(FAKE)], "prefetch": False, "timeout_s": 5.0}
    m.update(mcp)
    return {"weather": {"enabled": True, "location": "London", "unit": "C", "mcp": m}}


def _service(cfg, env=None):
    cfg["weather"]["mcp"]["env"] = env or {}                    # weather.mcp.env reaches the server process
    s = WM.WeatherService(cfg)
    s.start()
    assert s.client.wait_ready(60), s.client.last_error
    return s


def test_the_real_client_keeps_one_warm_connection_to_the_server():
    with tempfile.TemporaryDirectory() as tmp:
        log = Path(tmp) / "calls.txt"
        s = _service(_cfg(tmp), {"FAKE_WX_LOG": str(log)})
        try:
            assert sorted(s.client.tools) == sorted(WM.TOOLS)
            spec, speech = s.answer(_q("what's the weather"))
            assert spec["widget"] == "weather" and spec["data"]["source"] == "weather-mcp"
            assert spec["data"]["location"] == "London, United Kingdom" and spec["data"]["alert"]["source"] == "UK Met Office"
            assert speech == "8 degrees and overcast, feels like 5."
            calls = log.read_text(encoding="utf-8").split()
            assert calls[0] == "search_location" and len(calls) == 7, calls        # the place once, then 6 in parallel
            s.answer(_q("will it rain tomorrow"))                                  # cached: no new calls
            s.answer(_q("and the air quality?", prev=s.last))
            assert len(log.read_text(encoding="utf-8").split()) == 7
            assert s.last.focus == "air"
        finally:
            s.close()


def test_a_slow_server_falls_back_to_the_built_in_tool_and_warms_the_cache():
    from heyclaude import weather as WX
    with tempfile.TemporaryDirectory() as tmp:
        s = _service(_cfg(tmp, timeout_s=0.5), {"FAKE_WX_DELAY": "1.5"})
        try:
            with mock.patch.object(WX, "forecast", lambda place, **kw: _builtin(place, **kw)):
                t = time.perf_counter()
                spec, speech = s.answer(_q("what's the weather in Sampletown"))
                took = time.perf_counter() - t
            assert took < 1.4, took                                        # the search alone takes 1.5 s
            assert spec["data"]["source"] == "Open-Meteo" and spec["data"]["temp"] == 15
            assert speech.startswith("In Sampletown, 15 degrees")
            deadline = time.time() + 10                                    # the late answers land in the cache
            while time.time() < deadline and not s._cache:
                time.sleep(0.1)
            assert s._cache
            t = time.perf_counter()
            spec, _ = s.answer(_q("what's the weather in Sampletown"))
            assert spec["data"]["source"] == "weather-mcp" and time.perf_counter() - t < 0.5
        finally:
            s.close()


def test_no_npx_means_the_built_in_tool_logged_once():
    from heyclaude import weather as WX
    cfg = {"weather": {"enabled": True, "location": "Sampletown", "unit": "C", "mcp": {"prefetch": False}}}
    with mock.patch("shutil.which", lambda name: None):
        assert WM.server_command(cfg) is None and WM.agent_servers(dict(cfg, weather=dict(cfg["weather"], mcp={"agent": True}))) == {}
        s = WM.WeatherService(cfg)
        out = []
        with mock.patch("builtins.print", lambda *a, **k: out.append(" ".join(map(str, a)))):
            s.start()
            s.start()
        assert s.client is None and sum("npx not found" in x for x in out) == 1, out
    with mock.patch.object(WX, "forecast", lambda place, **kw: _builtin(place, **kw)):
        spec, speech = s.answer(_q("will it rain"))
    assert spec["data"]["source"] == "Open-Meteo" and speech.startswith("Rain likely")


def test_the_package_is_pinned_and_from_the_official_scope():
    assert WM.PACKAGE == "@dangahagan/weather-mcp@1.35.8"
    with mock.patch("shutil.which", lambda name: r"C:\Program Files\nodejs\npx.cmd"):
        cmd, args, env = WM.server_command({"weather": {"unit": "F", "mcp": {"package": "@someone/weather-mcp@latest"}}})
        assert args == ["--prefer-offline", "-y", WM.PACKAGE]                    # a fork or @latest is refused
        assert env["ENABLED_TOOLS"] == ",".join(WM.TOOLS) and env["WEATHER_UNITS"] == "imperial"
        assert env["ANALYTICS_ENABLED"] == "false" and "ANTHROPIC_API_KEY" not in env
        cfg = {"weather": {"mcp": {"agent": True}}}
        srv = WM.agent_servers(cfg)["weather-mcp"]
        assert srv["args"][-1] == WM.PACKAGE and "PATH" not in srv["env"]
        assert WM.agent_tools(cfg) == [f"mcp__weather-mcp__{t}" for t in WM.TOOLS]
    text = (ROOT / "config.example.yaml").read_text(encoding="utf-8")
    assert '"@dangahagan/weather-mcp@1.35.8"' in text and "@latest" not in text.split("weather-mcp", 1)[1].split("island:")[0].replace("never @latest", "")


def test_the_agent_gets_weather_mcp_only_behind_its_switch(tmp_path=None):
    from heyclaude import brain as B
    from heyclaude import config
    tmp_path = Path(tmp_path or tempfile.mkdtemp())
    cfg = config.defaults()
    assert cfg["weather"]["mcp"]["agent"] is False and cfg["weather"]["local_route"] is True
    cfg["agent"]["allowed_tools"] = ["Read"]
    with mock.patch("shutil.which", lambda name: r"C:\Program Files\nodejs\npx.cmd"):
        a = B.Agent.__new__(B.Agent)
        a.cfg = cfg
        with mock.patch.object(config, "STATE_DIR", tmp_path):
            args = a._args()
        assert args[args.index("--allowedTools") + 1] == "Read,mcp__weather__get_weather"     # off by default
        cfg["weather"]["mcp"]["agent"] = True
        with mock.patch.object(config, "STATE_DIR", tmp_path):
            args = a._args()
    allowed = args[args.index("--allowedTools") + 1].split(",")
    assert allowed[:2] == ["Read", "mcp__weather__get_weather"]
    assert allowed[2:] == [f"mcp__weather-mcp__{t}" for t in WM.TOOLS]
    data = json.load(open(args[args.index("--mcp-config") + 1:][-1], encoding="utf-8"))
    assert "weather" in data["mcpServers"] and data["mcpServers"]["weather-mcp"]["args"][-1] == WM.PACKAGE
    assert "mcp__weather-mcp tools" in args[args.index("--append-system-prompt") + 1]


# ------------------------------------------------------------------ routing and the turn
class _Wx:
    """WeatherService's surface for the router and the assistant."""

    def __init__(self):
        self.last, self.asked = None, []

    def query(self, text, prev_turn_was_weather=False):
        return parse(text, prev=self.last if prev_turn_was_weather else None)

    def answer(self, q):
        self.asked.append(q)
        self.last = q
        f = _facts()
        label = "London, United Kingdom" if not q.home else "Hometown"
        return W.normalise({"widget": "weather", "data": C.card(f, q, label), "text": ""}), C.speak(f, q, label)


def test_routing_order_local_commands_still_win():
    from test_conversation import _brain
    b = _brain(fast=[["Paris."]], agent=[])
    b.wx = _Wx()
    assert list(b.handle("undo that")) == [("undo",)]
    assert list(b.handle("start a task in sandbox: add a weather page"))[0][:2] == ("task", "sandbox")
    assert list(b.handle("never mind")) == [("noop",)]
    ev = list(b.handle("what's the weather in London tomorrow?"))
    assert ev[0][0] == "weather" and ev[0][1].place == "London" and b.timing["route"] == "local-weather"
    assert list(b.handle("volume 30")) == [("control", "volume 30")]
    assert list(b.handle("what's the weather there?")) == [("lookup", "what's the weather there?")]   # agent has context
    assert list(b.handle("what is the capital of France?")) == [("say", "Paris.")]
    b.wx = None
    assert list(b.handle("what's the weather?")) == [("lookup", "what's the weather?")]             # route off: as before


def test_the_turn_shows_the_card_then_speaks_and_joins_the_conversation(tmp_path=None):
    from test_conversation import _assistant
    from test_offline import _Sig
    a = _assistant(Path(tmp_path or tempfile.mkdtemp()), fast=[["Yes, a light coat."]])
    a.ui.widget, a.ui.tool = _Sig(), _Sig()
    a.brain.wx = _Wx()
    order = []
    a.ui.widget.emit = lambda spec: order.append(("card", spec))
    feed = a.speaker.feed
    a.speaker.feed = lambda chunk: (order.append(("say", chunk)), feed(chunk))
    timings = []
    a._log_timing = timings.append
    a.request("What's the weather in London?", wait=True)
    assert order[0][0] == "card" and order[0][1]["widget"] == "weather" and order[1] == ("say", "In London, 8 degrees and "
                                                                                          "overcast, feels like 5. ")
    t = a.brain.convo.turns[-1]
    assert t.tier == "weather" and t.reply == "In London, 8 degrees and overcast, feels like 5."
    assert timings[-1]["card"] <= timings[-1]["first_audio"] and timings[-1]["route"] == "local-weather"
    assert a._tool_widget == (a.gen, "weather")
    a.request("and tomorrow?", source="followup", wait=True)                     # a follow-up stays local
    assert a.brain.wx.asked[-1].when == "tomorrow" and a.brain.wx.asked[-1].place == "London"
    a.request("Is that unusual for November?", source="followup", wait=True)     # other tiers hear the weather turns
    assert "8 degrees and overcast" in a.brain.chat.sent[-1]


# ------------------------------------------------------------------ the card on the live island
def test_every_weather_state_renders_on_the_island():
    from test_island import _card_check, _island, _named, _spin
    isl = _island()
    isl.bridge_.state.emit("speaking", "Here you go.")
    _spin(300)
    f = _facts()
    f["nowcast"] = {"covered": True, "steps": [0, 0, 2, 3, 3, 1, 0, 0], "times": ["20:30"]}
    full = W.normalise({"widget": "weather", "data": C.card(f, _q("will it rain in London"), "London, United Kingdom")})
    assert {"alert", "nowcast", "air", "sun", "moon"} <= set(full["data"])
    card = _card_check(isl, full, {"widget": "weather", "title": "Weather · London, United Kingdom", "icon": True}, "weather_all")
    for name in ("weatherAlert", "weatherNowcast", "weatherStrip", "weatherMoon"):
        assert _named(card, name), name
    texts = " ".join(str(i.property("text")) for i in _named(card, "weatherAlert")[0].childItems() if i.property("text"))
    assert "Yellow fog warning" in texts and "UK Met Office" in texts
    assert card.height() <= 280 + 14, card.height()                       # max_height + the card's bottom margin
    # the built-in fallback: no alert, no nowcast; sun, UV and the moon
    d = _builtin(detail=True)
    fb = C.from_builtin(d)
    fb["moon"] = C.moon_phase(C._date("2026-11-09"))
    spec = W.normalise({"widget": "weather", "data": C.card(fb, _q("what's the weather"), d["location"])})
    card = _card_check(isl, spec, {"widget": "weather", "title": "Weather · Sampletown, Exampleland", "icon": True}, "weather_fallback")
    assert _named(card, "weatherStrip") and _named(card, "weatherMoon")
    assert not _named(card, "weatherAlert") and not _named(card, "weatherNowcast")
    # and the plain M5 card, with none of the new data: as before
    plain = W.normalise({"widget": "weather", "data": {k: v for k, v in _builtin().items() if k != "sun"}})
    card = _card_check(isl, plain, {"widget": "weather", "title": "Weather · Sampletown, Exampleland", "icon": True}, "weather_plain")
    for name in ("weatherAlert", "weatherNowcast", "weatherStrip"):
        assert not _named(card, name), name
    isl.clear_widget()
    _spin(300)


if __name__ == "__main__":
    n = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            n += 1
            print("ok", name)
    print(n, "passed")
    import test_island
    if test_island._ISL is not None:
        test_island._ISL.close()
        test_island._spin(300)
    sys.stdout.flush()
    sys.stderr.flush()
    if sys.platform == "win32":                     # as test_island: no DLL teardown with Qt threads alive
        import ctypes
        k32 = ctypes.windll.kernel32
        k32.TerminateProcess(k32.GetCurrentProcess(), 0)
    os._exit(0)
