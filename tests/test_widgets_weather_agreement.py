"""M5b, the weather pack's last piece: "how sure is this?", the model-agreement chip.
The same day from seven forecast models (Open-Meteo's keyless multi-model forecast),
compared (heyclaude/weather.py: compare, agreement), asked for on the local weather
route ("how sure is that?", query.py's `sure`) or by the agent (get_weather(agreement=
true)), shown as a chip on the weather card and said in one sentence. Offline: the
network is a fake serving a recorded real multi-model answer
(tests/fixtures/weather_mcp/open_meteo_models.json, the place anonymised).

    .venv\\Scripts\\python tests\\test_widgets_weather_agreement.py
"""
import faulthandler
import json
import os
import sys
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
faulthandler.enable()
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from heyclaude import weather as WX  # noqa: E402
from heyclaude import widgets as W  # noqa: E402
from heyclaude.effects import parse_result, result_text  # noqa: E402
from heyclaude.weather_mcp import card as C  # noqa: E402
from heyclaude.weather_mcp.query import HOME, parse  # noqa: E402

RAW = json.loads((ROOT / "tests" / "fixtures" / "weather_mcp" / "open_meteo_models.json").read_text(encoding="utf-8"))
FIX = json.loads((ROOT / "tests" / "fixtures" / "tool_results" / "weather_get_weather_agreement.json")
                 .read_text(encoding="utf-8"))
MON = 0
SCHEMA = W.registry()["weather"]["schema"]


def _q(text, **kw):
    return parse(text, today_wd=MON, **kw)


def _fake_get(calls):
    """Open-Meteo, offline: geocoding, the single-model forecast (test_weather_mcp's canned
    one) and the multi-model forecast (the recorded one)."""
    from test_weather_mcp import _FC

    def fetch(url, params, timeout=8.0):
        calls.append((url, dict(params)))
        if "geocoding" in url:
            return {"results": [{"name": params["name"], "latitude": 10.0, "longitude": 20.0, "country": "Exampleland"}]}
        return RAW if "models" in params else _FC
    return fetch


def _clear():
    WX._cache.clear()
    WX._places.clear()
    WX._mm_cache.clear()


# ------------------------------------------------------------------ the comparison
def test_the_recorded_models_compare_into_high_medium_and_low_agreement():
    today, tomorrow, thursday = (WX.compare(RAW, d) for d in (0, 1, 2))
    assert [m["name"] for m in today["models"]] == ["ECMWF", "GFS", "ICON", "GEM", "Météo-France", "UKMO", "JMA"]
    # today: six models within a degree or so, the Canadian one well below on the low: agree, it's named
    assert today["level"] == "high" and today["odd"] == {"name": "GEM", "side": "cold"}
    assert today["text"] == "Models agree · ±1°, rain 67–89%" and today["day"] == "Today"
    assert today["summary"] == ("The models agree: 11 to 13 degrees, rain likely. "
                                "The Canadian model runs colder than the rest.")
    # tomorrow: rain anywhere from 11 to 67 percent: they disagree
    assert tomorrow["level"] == "low" and "odd" not in tomorrow and tomorrow["day"] == "Tomorrow"
    assert tomorrow["detail"] == "±2° · rain 11–67%"
    assert tomorrow["summary"] == "The models disagree about tomorrow: 10 to 13 degrees, and they're split on rain."
    assert thursday["level"] == "medium" and thursday["day"] == "Thu"
    assert thursday["summary"] == "The models mostly agree about Thursday: 9 to 12 degrees, rain likely."
    # Météo-France and JMA give no rain chance: their dots are there, without one
    mf = next(m for m in today["models"] if m["name"] == "Météo-France")
    assert "pop" not in mf and mf["rain"] >= 0
    for d in range(7):
        a = WX.compare(RAW, d)
        assert a and W.validate({"location": "x", "temp": 1, "agreement": a}, SCHEMA) == [], (d, a)
    assert WX.compare(RAW, 7) is None                                  # beyond the forecast


def _day(**cols):
    """A one-day multi-model answer: cols = {model id: (high, low, pop, rain)}."""
    daily = {"time": ["2026-11-12"]}
    for mid, (hi, lo, pop, rain) in cols.items():
        daily[f"temperature_2m_max_{mid}"] = [hi]
        daily[f"temperature_2m_min_{mid}"] = [lo]
        daily[f"precipitation_probability_max_{mid}"] = [pop]
        daily[f"precipitation_sum_{mid}"] = [rain]
    return {"daily": daily}


def test_lows_dry_days_fahrenheit_and_missing_models():
    ids = [m for m, _ in WX.MODELS]
    # highs agree, lows don't: said as such
    a = WX.compare(_day(**{m: (14 + i * 0.3, 2 + i * 1.5, 5, 0.0) for i, m in enumerate(ids)}))
    assert a["level"] == "low" and a["detail"] == "highs ±1°, lows ±4° · dry"
    assert a["summary"] == "The models disagree: highs of 14 to 16 degrees, but lows anywhere from 2 to 11, and dry."
    # all dry and close: agree
    a = WX.compare(_day(**{m: (20 + i * 0.2, 9 + i * 0.2, 3, 0.0) for i, m in enumerate(ids)}))
    assert a["level"] == "high" and a["head"] == "Models agree" and a["summary"].endswith("20 to 21 degrees, and dry.")
    # in Fahrenheit the same spread in °F is tighter: 3 °F apart still agree
    a = WX.compare(_day(**{m: (60 + i * 0.5, 50 + i * 0.5, 80, 3.0) for i, m in enumerate(ids)}), unit="F")
    assert a["level"] == "high" and a["summary"] == "The models agree: 60 to 63 degrees, rain likely."
    # models without a value that day are left out; fewer than two: nothing to compare
    a = WX.compare(_day(ecmwf_ifs025=(5, 1, 40, 1.0), gfs_seamless=(6, 0, 45, 2.0), icon_seamless=(None, None, None, None)))
    assert [m["name"] for m in a["models"]] == ["ECMWF", "GFS"] and a["level"] == "high"
    assert WX.compare(_day(ecmwf_ifs025=(5, 1, 40, 1.0))) is None
    # no rain chances at all: wet/dry votes from the amounts
    a = WX.compare(_day(**{m: (8, 3, None, 4.0 if i < 3 else 0.0) for i, m in enumerate(ids)}))
    assert a["level"] == "low" and "rain 0–4 mm" in a["detail"] and "split on rain" in a["summary"]


def test_one_fair_use_call_cached_ten_minutes():
    _clear()
    calls = []
    a = WX.agreement("Sampletown", day=1, now=1000.0, fetch=_fake_get(calls))
    assert a["level"] == "low"
    url, p = calls[-1]
    assert url == WX.FORECAST_URL and p["models"].split(",") == [m for m, _ in WX.MODELS]
    assert p["daily"] == "temperature_2m_max,temperature_2m_min,precipitation_probability_max,precipitation_sum"
    assert p["forecast_days"] == 7 and p["timezone"] == "auto" and "temperature_unit" not in p
    n = len(calls)
    assert WX.agreement("Sampletown", day=2, now=1500.0, fetch=_fake_get(calls))["day"] == "Thu"
    assert len(calls) == n                                               # cached, any day
    WX.agreement("Sampletown", day=0, now=1000.0 + WX.TTL_S + 1, fetch=_fake_get(calls))
    assert len(calls) == n + 1                                           # stale: asked again
    WX.agreement("Sampletown", unit="F", now=1000.0, fetch=_fake_get(calls))
    assert calls[-1][1]["temperature_unit"] == "fahrenheit"
    try:
        WX.models_forecast(lat=1.0, lon=2.0, fetch=lambda u, p, timeout=8.0: {"error": True, "reason": "bad model"})
        raise AssertionError("an empty answer should raise")
    except WX.WeatherError as e:
        assert "bad model" in str(e)
    _clear()


# ------------------------------------------------------------------ asked for: the local route
def test_how_sure_questions_set_sure_and_only_when_about_the_weather():
    prev = _q("what's the weather in London tomorrow")
    for text in ("how sure is that?", "how confident are you", "are you sure", "do the models agree",
                 "are the models agreeing", "how accurate is that", "is that right"):
        q = _q(text, prev=prev)
        assert q is not None and q.sure and (q.place, q.when) == ("London", "tomorrow"), (text, q)
    q = _q("how sure are you about Friday", prev=prev)
    assert q.sure and q.place == "London" and (q.when, q.day) == ("day", 4)
    q = _q("will it really rain tomorrow?")
    assert q.sure and (q.place, q.when, q.focus) == (HOME, "tomorrow", "rain")
    q = _q("how sure is the forecast for tomorrow")
    assert q.sure and (q.place, q.when) == (HOME, "tomorrow")
    q = _q("how confident are you about rain in Paris tomorrow")
    assert q.sure and (q.place, q.when, q.focus) == ("Paris", "tomorrow", "rain")
    q = _q("do the weather models agree on Saturday")
    assert q.sure and (q.when, q.day) == ("day", 5)
    assert _q("how reliable is the weather forecast").sure
    assert _q("how sure are you that it will rain on friday").sure
    # without a weather answer before, and nothing about the weather: not ours
    for text in ("how sure are you", "are you sure", "do the models agree", "how sure are you about tomorrow",
                 "how accurate is that", "is that right", "really?", "sure"):
        assert _q(text) is None, text
    assert _q("sure", prev=prev) is None and _q("really?", prev=prev) is None
    # plain questions stay plain, and a follow-up after a "how sure" is a plain answer again
    assert not _q("what's the weather like in London right now").sure
    sure = _q("how sure is that?", prev=prev)
    nxt = _q("and Friday?", prev=sure)
    assert nxt is not None and not nxt.sure and nxt.when == "day"


def test_the_card_and_summary_with_the_models():
    from test_weather_mcp import _facts
    f = _facts()
    f["agreement"] = WX.compare(RAW, 1)
    q = _q("how sure is the forecast in London tomorrow")
    d = C.card(f, q, "London, United Kingdom")
    assert W.validate(d, SCHEMA) == [] and d["agreement"]["level"] == "low" and "summary" not in d["agreement"]
    assert C.speak(f, q, "London, United Kingdom") == (
        "In London, the models disagree about tomorrow: 10 to 13 degrees, and they're split on rain.")
    assert C.speak(f, _q("how sure is the forecast for tomorrow")).startswith("The models disagree about tomorrow")
    # the models didn't answer: the usual summary, said so
    f.pop("agreement")
    s = C.speak(f, _q("will it really rain tomorrow"))
    assert s.endswith(" I couldn't compare the forecast models just now.") and len(s) > 60
    assert "agreement" not in C.card(f, q, "London")
    # a plain question never shows the chip, even with the data there
    f["agreement"] = WX.compare(RAW, 0)
    assert "agreement" in C.card(f, q) and not C.speak(f, _q("what's the weather")).startswith("The models")


def test_the_route_fetches_the_models_alongside_and_shows_the_chip():
    from heyclaude.weather_mcp import WeatherService
    _clear()
    calls = []
    svc = WeatherService({"weather": {"enabled": True, "location": "Sampletown", "unit": "C", "mcp": {"enabled": False}}})
    with mock.patch.object(WX, "_get", _fake_get(calls)):
        spec, speech = svc.answer(_q("how sure is the forecast for tomorrow"))
        assert spec["widget"] == "weather" and spec["data"]["agreement"]["day"] == "Tomorrow"
        assert "summary" not in spec["data"]["agreement"] and "hourly" in spec["data"]
        assert speech == "The models disagree about tomorrow: 10 to 13 degrees, and they're split on rain."
        assert "agreement_s" in svc.timing
        n = sum(1 for _, p in calls if "models" in p)
        spec, speech = svc.answer(_q("what's the weather"))                   # not asked: no chip, no call
        assert "agreement" not in spec["data"] and sum(1 for _, p in calls if "models" in p) == n
    # the models' server down: the card and the usual summary still come, said so
    _clear()

    def down(url, params, timeout=8.0):
        if "models" in params:
            raise OSError("timed out")
        return _fake_get([])(url, params, timeout)
    with mock.patch.object(WX, "_get", down):
        spec, speech = svc.answer(_q("how sure is the forecast for tomorrow"))
        assert spec["widget"] == "weather" and "agreement" not in spec["data"]
        assert speech.endswith("I couldn't compare the forecast models just now.")
    _clear()


# ------------------------------------------------------------------ asked for: the agent's tool
def test_the_built_in_tool_takes_agreement_and_the_card_shows_it():
    from heyclaude.mcp_weather.__main__ import get_weather
    _clear()
    with mock.patch.object(WX, "_get", _fake_get([])), \
            mock.patch.dict(os.environ, {"HEYCLAUDE_WEATHER_LOCATION": "Sampletown", "HEYCLAUDE_WEATHER_UNIT": "C"}):
        plain = get_weather()
        assert "agreement" not in plain and plain["temp"] == 15
        r = get_weather("", agreement=True, day=2)
        assert r["agreement"]["day"] == "Thu" and r["agreement"]["summary"].startswith("The models mostly agree about Thursday")
        spec = W.from_tool("mcp__weather__get_weather", {"agreement": True}, json.loads(json.dumps(r)))
        assert spec["widget"] == "weather" and spec["data"]["agreement"]["level"] == "medium"
        assert "summary" not in spec["data"]["agreement"]                    # the agent says it; the card doesn't
        assert get_weather("", agreement=True, day=99)["agreement"]["day"] == "Mon"     # clamped to the last day
    _clear()
    with mock.patch.object(WX, "_get", lambda u, p, timeout=8.0: (_ for _ in ()).throw(OSError("down"))
                           if "models" in p else _fake_get([])(u, p, timeout)), \
            mock.patch.dict(os.environ, {"HEYCLAUDE_WEATHER_LOCATION": "Sampletown"}):
        r = get_weather("", agreement=True)
        assert "agreement" not in r and "couldn't compare" in r["agreement_error"] and r["temp"] == 15
    _clear()
    # the MCP server lists the new arguments
    from heyclaude.mcp_weather.__main__ import build
    import asyncio
    tools = asyncio.run(build().list_tools())
    props = next(t for t in tools if t.name == "get_weather")
    props = (getattr(props, "input_schema", None) or getattr(props, "inputSchema"))["properties"]
    assert {"location", "agreement", "day"} <= set(props)


def test_the_recorded_tool_result_and_bad_data():
    spec = W.from_tool(FIX["tool"], FIX["args"], parse_result(FIX["content"]), result_text(FIX["content"]))
    a = spec["data"]["agreement"]
    assert spec["widget"] == "weather" and spec["data"]["location"] == "Sampletown, Exampleland"
    assert a["level"] == "low" and len(a["models"]) == 7 and a["day"] == "Tomorrow" and "summary" not in a
    assert W.title_for(W.registry()["weather"], spec["data"]) == FIX["expect"]["title"]
    # invalid agreement data: the whole card falls back to the text
    for bad in ({"level": "certain", "head": "x", "models": []}, {"level": "high", "head": "x"},
                {"level": "high", "head": "x", "models": [{"name": "A", "high": "warm", "low": 1}]}):
        out = W.normalise({"widget": "weather", "data": dict(spec["data"], agreement=bad), "text": "Said instead."})
        assert out["widget"] != "weather" and out["text"] == "Said instead.", (bad, out)


# ------------------------------------------------------------------ on the island
def test_the_chip_renders_and_rests_within_the_idle_budget():
    import test_widget_packs as TP
    from test_island import _card_check, _named, _texts
    isl = TP._island()
    b = isl.bridge_
    b.state.emit("speaking", "Here you go.")
    TP._spin(300)
    spec = W.from_tool(FIX["tool"], FIX["args"], parse_result(FIX["content"]), result_text(FIX["content"]))
    card = _card_check(isl, spec, FIX["expect"], "weather_agreement")
    chip = _named(card, "weatherAgreement")
    assert chip and chip[0].isVisible()
    texts = " ".join(_texts(chip[0]))
    assert "Models disagree" in texts and "Tomorrow · 7 models" in texts and "10–13°" in texts, texts
    assert not _named(card, "weatherNowcast")                    # asked about a day: no hours row either
    hours = [t for t in _texts(card) if t == "03:00"]
    assert not hours, hours
    assert card.height() <= 280 + 14, card.height()
    # at rest: no more than the idle budget on top of the island's own rate
    isl.clear_widget()
    b.state.emit("idle", "")
    TP._spin(2500)
    base = TP._rest_fps(isl)
    b.state.emit("speaking", "Here you go.")
    TP._spin(250)
    b.widget.emit(spec)
    TP._spin(1500)
    b.state.emit("idle", "")
    TP._spin(6000)
    info = isl.call("extrasInfo")
    info = info.toVariant() if hasattr(info, "toVariant") else info
    if info.get("widget") == "weather":
        fps = TP._rest_fps(isl)
        assert fps <= base + TP.IDLE_FPS, (fps, base)
    isl.clear_widget()
    TP._spin(300)


if __name__ == "__main__":
    n = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            n += 1
            print("ok", name)
    print(n, "passed")
    import test_widget_packs
    if test_widget_packs._ISL is not None:
        test_widget_packs._ISL.close()
        test_widget_packs._spin(300)
    sys.stdout.flush()
    sys.stderr.flush()
    if sys.platform == "win32":                     # as test_island: no DLL teardown with Qt threads alive
        import ctypes
        k32 = ctypes.windll.kernel32
        k32.TerminateProcess(k32.GetCurrentProcess(), 0)
    os._exit(0)
