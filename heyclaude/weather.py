"""Weather from Open-Meteo: keyless, no account (https://open-meteo.com).

`forecast("Tashkent")` -> the `weather` widget's data, straight from the API:
    {"location": "Tashkent, Uzbekistan", "temp": 21, "unit": "C", "condition": "Partly cloudy",
     "icon": "partly", "feels": 20, "high": 24, "low": 13, "wind": 11,
     "hourly": [{"time": "15:00", "temp": 22, "icon": "sun"}, ... 6],
     "days": [{"day": "Tue", "low": 13, "high": 24, "icon": "partly"}, ... 3],
     "sun": {"rise": "07:08", "set": "16:52"}, "source": "Open-Meteo"}

`detail=True` (the local weather route's fallback, heyclaude/weather_mcp) adds
"_hours" (the next 24 h with rain chances), "_days" (with sunrise, sunset, UV
and rain chance) and the place's "_date"/"_time"; the card never sees them.

`agreement("Tashkent", day=1)` -> "how sure is this?" (M5b): the same day from
several forecast models (ECMWF, GFS, ICON, GEM, Meteo-France, UKMO, JMA) in
one call to Open-Meteo's keyless multi-model forecast, compared: how far apart
their highs, lows and rain chances are -> high / medium / low agreement, a
lone outlier named, and a sentence to say. Only when asked (the local weather
route's "how sure is that?", or get_weather(agreement=true)).

Places are looked up with Open-Meteo's geocoding API. Answers are cached for
10 minutes per place (the multi-model answer too), so asking again doesn't
hit the network; nothing polls. The tool agent
reaches it through heyclaude.mcp_weather (`mcp__weather__get_weather`), and
the island shows the card from the tool result: the model never makes up the
numbers.
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request

GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
TTL_S = 600
_cache: dict[tuple, tuple[float, dict]] = {}
_places: dict[str, dict] = {}            # place name -> geocode result (places don't move)

# WMO weather interpretation codes -> (condition, icon by day, icon by night)
WMO = {
    0: ("Clear", "sun", "night"), 1: ("Mainly clear", "sun", "night"), 2: ("Partly cloudy", "partly", "night"),
    3: ("Overcast", "cloud", "cloud"), 45: ("Fog", "fog", "fog"), 48: ("Freezing fog", "fog", "fog"),
    51: ("Light drizzle", "rain", "rain"), 53: ("Drizzle", "rain", "rain"), 55: ("Heavy drizzle", "rain", "rain"),
    56: ("Freezing drizzle", "rain", "rain"), 57: ("Freezing drizzle", "rain", "rain"),
    61: ("Light rain", "rain", "rain"), 63: ("Rain", "rain", "rain"), 65: ("Heavy rain", "rain", "rain"),
    66: ("Freezing rain", "rain", "rain"), 67: ("Freezing rain", "rain", "rain"),
    71: ("Light snow", "snow", "snow"), 73: ("Snow", "snow", "snow"), 75: ("Heavy snow", "snow", "snow"),
    77: ("Snow grains", "snow", "snow"), 80: ("Rain showers", "rain", "rain"), 81: ("Rain showers", "rain", "rain"),
    82: ("Violent rain showers", "storm", "storm"), 85: ("Snow showers", "snow", "snow"), 86: ("Snow showers", "snow", "snow"),
    95: ("Thunderstorm", "storm", "storm"), 96: ("Thunderstorm with hail", "storm", "storm"),
    99: ("Thunderstorm with hail", "storm", "storm"),
}
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


class WeatherError(Exception):
    pass


def _get(url: str, params: dict, timeout: float = 8.0) -> dict:
    q = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(q, headers={"User-Agent": "hey-claude"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def describe(code, is_day: bool = True) -> tuple[str, str]:
    c = WMO.get(int(code) if code is not None else -1)
    if c is None:
        return "Unknown", "cloud"
    return c[0], c[1] if is_day else c[2]


def geocode(place: str, fetch=None) -> dict:
    """{"name", "lat", "lon", "label"} for a place name."""
    fetch = fetch or _get                  # looked up when called, so a test can stand in for the network
    place = (place or "").strip()
    if not place:
        raise WeatherError("no place given (set weather.location in Settings, or say a city)")
    if place.lower() in _places:
        return _places[place.lower()]
    # "Tashkent, Uzbekistan" works better as just the city for the search
    name = place.split(",")[0].strip()
    r = fetch(GEO_URL, {"name": name, "count": 5, "language": "en", "format": "json"})
    res = r.get("results") or []
    if not res:
        raise WeatherError(f"couldn't find a place called {place!r}")
    want = place.split(",")[1].strip().lower() if "," in place else ""
    best = next((x for x in res if want and want in (str(x.get("country", "")) + str(x.get("admin1", ""))).lower()), res[0])
    label = ", ".join(x for x in (best.get("name"), best.get("country")) if x)
    out = {"name": best.get("name"), "lat": best["latitude"], "lon": best["longitude"], "label": label}
    _places[place.lower()] = out
    return out


def forecast(place: str = "", *, lat: float | None = None, lon: float | None = None, unit: str = "C",
             now: float | None = None, fetch=None, days: int = 3, detail: bool = False) -> dict:
    """Current conditions, the next 6 hours and 3 days, as the weather widget's data
    (`days` up to 16 are fetched for `detail`; the card shows 3)."""
    fetch = fetch or _get
    unit = "F" if str(unit).upper().startswith("F") else "C"
    if lat is None or lon is None:
        g = geocode(place, fetch)
        lat, lon, label = g["lat"], g["lon"], g["label"]
    else:
        label = place or f"{lat:.2f}, {lon:.2f}"
    days = max(3, min(16, int(days)))
    key = (round(float(lat), 3), round(float(lon), 3), unit, days)
    t = now if now is not None else time.time()
    hit = _cache.get(key)
    if hit and t - hit[0] < TTL_S:
        return _detail(dict(hit[1], location=label, cached=True), detail)
    p = {"latitude": lat, "longitude": lon, "timezone": "auto", "forecast_days": days,
         "current": "temperature_2m,apparent_temperature,weather_code,is_day,wind_speed_10m",
         "hourly": "temperature_2m,weather_code,is_day,precipitation_probability",
         "daily": "weather_code,temperature_2m_max,temperature_2m_min,sunrise,sunset,uv_index_max,"
                  "precipitation_probability_max"}
    if unit == "F":
        p["temperature_unit"] = "fahrenheit"
        p["wind_speed_unit"] = "mph"
    r = fetch(FORECAST_URL, p)
    out = to_widget(r, label, unit, detail=True)
    _cache[key] = (t, out)
    return _detail(out, detail)


def _detail(d: dict, keep: bool) -> dict:
    return d if keep else {k: v for k, v in d.items() if not k.startswith("_")}


def to_widget(r: dict, label: str, unit: str = "C", detail: bool = False) -> dict:
    cur = r.get("current") or {}
    is_day = bool(cur.get("is_day", 1))
    cond, icon = describe(cur.get("weather_code"), is_day)
    daily = r.get("daily") or {}
    hourly = r.get("hourly") or {}
    out = {"location": label[:40], "temp": round(float(cur.get("temperature_2m", 0))), "unit": unit,
           "condition": cond, "icon": icon, "source": "Open-Meteo"}
    if cur.get("apparent_temperature") is not None:
        out["feels"] = round(float(cur["apparent_temperature"]))
    if cur.get("wind_speed_10m") is not None:
        out["wind"] = round(float(cur["wind_speed_10m"]))
    if daily.get("temperature_2m_max"):
        out["high"] = round(float(daily["temperature_2m_max"][0]))
        out["low"] = round(float(daily["temperature_2m_min"][0]))
    # the next 6 hours from now (the current time is in the place's own zone)
    times = hourly.get("time") or []
    now_s = str(cur.get("time") or "")[:13]
    start = next((i for i, x in enumerate(times) if x[:13] > now_s), 0)
    hrs = []
    for i in range(start, min(start + 6, len(times))):
        _, ic = describe((hourly.get("weather_code") or [None] * len(times))[i],
                         bool((hourly.get("is_day") or [1] * len(times))[i]))
        hrs.append({"time": times[i][11:16], "temp": round(float(hourly["temperature_2m"][i])), "icon": ic})
    if hrs:
        out["hourly"] = hrs
    days = []
    for i, d in enumerate(daily.get("time") or []):
        try:
            wd = DAYS[time.strptime(d, "%Y-%m-%d").tm_wday]
        except ValueError:
            wd = d[-5:]
        _, ic = describe(daily["weather_code"][i], True)
        days.append({"day": "Today" if i == 0 else wd, "low": round(float(daily["temperature_2m_min"][i])),
                     "high": round(float(daily["temperature_2m_max"][i])), "icon": ic})
    if days:
        out["days"] = days[:3]
    sr, ss = daily.get("sunrise") or [], daily.get("sunset") or []
    if sr and ss and sr[0] and ss[0]:
        out["sun"] = {"rise": str(sr[0])[11:16], "set": str(ss[0])[11:16]}
    if detail:
        out.update(_extra(r, cur, hourly, daily, start))
    return out


def _extra(r: dict, cur: dict, hourly: dict, daily: dict, start: int) -> dict:
    """The fuller picture for the local weather route's spoken summary (not card data)."""
    def col(d, k, i, default=None):
        v = d.get(k) or []
        return v[i] if i < len(v) else default
    times = hourly.get("time") or []
    hrs = []
    for i in range(start, min(start + 24, len(times))):
        cond, ic = describe(col(hourly, "weather_code", i), bool(col(hourly, "is_day", i, 1)))
        h = {"date": times[i][:10], "time": times[i][11:16], "temp": col(hourly, "temperature_2m", i),
             "condition": cond, "icon": ic, "pop": col(hourly, "precipitation_probability", i)}
        hrs.append({k: v for k, v in h.items() if v is not None})
    ds = []
    for i, d in enumerate(daily.get("time") or []):
        cond, ic = describe(col(daily, "weather_code", i), True)
        x = {"date": d, "high": col(daily, "temperature_2m_max", i), "low": col(daily, "temperature_2m_min", i),
             "condition": cond, "icon": ic, "pop": col(daily, "precipitation_probability_max", i),
             "uv": col(daily, "uv_index_max", i),
             "sunrise": str(col(daily, "sunrise", i) or "")[11:16], "sunset": str(col(daily, "sunset", i) or "")[11:16]}
        ds.append({k: v for k, v in x.items() if v not in (None, "")})
    now_s = str(cur.get("time") or "")
    return {"_hours": hrs, "_days": ds, "_date": now_s[:10], "_time": now_s[11:16]}


# ------------------------------------------------------------------ "how sure is this?": the models compared
# Open-Meteo model ids -> the names on the card (all global; a model that doesn't
# reach a day answers null there and is left out of that day)
MODELS = [("ecmwf_ifs025", "ECMWF"), ("gfs_seamless", "GFS"), ("icon_seamless", "ICON"), ("gem_seamless", "GEM"),
          ("meteofrance_seamless", "Météo-France"), ("ukmo_seamless", "UKMO"), ("jma_seamless", "JMA")]
SPOKEN = {"ECMWF": "the European model", "GFS": "the American model", "ICON": "the German model",
          "GEM": "the Canadian model", "Météo-France": "Météo-France", "UKMO": "the Met Office model",
          "JMA": "the Japanese model"}
MODEL_DAYS = 7
_MM_VARS = ("temperature_2m_max", "temperature_2m_min", "precipitation_probability_max", "precipitation_sum")
# a spread (max - min across the models, °C) up to the first number is "agree", up to the
# second "mostly agree"; every model's lows wander more than its highs, so lows get more room
_LIMITS = {"high": (2.0, 4.0), "low": (3.0, 6.0), "pop": (30, 55)}
LEVELS = ("low", "medium", "high")            # index = the agreement class 0..2
_LONG_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_mm_cache: dict[tuple, tuple[float, dict]] = {}


def models_forecast(place: str = "", *, lat: float | None = None, lon: float | None = None, unit: str = "C",
                    now: float | None = None, fetch=None, timeout: float = 6.0) -> dict:
    """Open-Meteo's daily forecast from every model in MODELS (7 days) as it answers:
    {"daily": {"time": [...], "temperature_2m_max_ecmwf_ifs025": [...], ...}}. Cached 10 min."""
    fetch = fetch or _get
    unit = "F" if str(unit).upper().startswith("F") else "C"
    if lat is None or lon is None:
        g = geocode(place, fetch)
        lat, lon = g["lat"], g["lon"]
    key = (round(float(lat), 3), round(float(lon), 3), unit)
    t = now if now is not None else time.time()
    hit = _mm_cache.get(key)
    if hit and t - hit[0] < TTL_S:
        return hit[1]
    p = {"latitude": lat, "longitude": lon, "timezone": "auto", "forecast_days": MODEL_DAYS,
         "daily": ",".join(_MM_VARS), "models": ",".join(m for m, _ in MODELS)}
    if unit == "F":
        p["temperature_unit"] = "fahrenheit"
    r = fetch(FORECAST_URL, p, timeout=timeout)
    if not isinstance(r, dict) or not (r.get("daily") or {}).get("time"):
        why = r.get("reason", "an empty answer") if isinstance(r, dict) else "a bad answer"
        raise WeatherError(f"no multi-model forecast ({why})")
    _mm_cache[key] = (t, r)
    return r


def agreement(place: str = "", *, lat: float | None = None, lon: float | None = None, day: int = 0,
              unit: str = "C", now: float | None = None, fetch=None, timeout: float = 6.0) -> dict | None:
    """How well the forecast models agree on `day` (0 today ... 6): the card's `agreement`
    data plus "summary", a sentence to say. None when fewer than two models reach that day."""
    r = models_forecast(place, lat=lat, lon=lon, unit=unit, now=now, fetch=fetch, timeout=timeout)
    return compare(r, day, unit)


def _median(xs: list[float]) -> float:
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def _class(rng: float, lim: tuple) -> int:
    return 2 if rng <= lim[0] else 1 if rng <= lim[1] else 0


def _spread(vals: list[float], lim: tuple) -> dict:
    """{"range", "cls", "odd", "trim_cls", "dev"}: with five or more models, one far from
    the rest (leaving it out lifts the class) is the odd one out; `dev` is how far, in limits."""
    rng = max(vals) - min(vals)
    out = {"range": rng, "cls": _class(rng, lim), "odd": None, "trim_cls": None, "dev": 0.0}
    if len(vals) >= 5:
        med = _median(vals)
        i = max(range(len(vals)), key=lambda k: abs(vals[k] - med))
        rest = vals[:i] + vals[i + 1:]
        tc = _class(max(rest) - min(rest), lim)
        if tc > out["cls"]:
            out.update(odd=i, trim_cls=tc, dev=(vals[i] - _median(rest)) / lim[0])
    return out


def _say_deg(x: float) -> str:
    v = int(round(x))
    return f"minus {-v}" if v < 0 else str(v)


def compare(r: dict, day: int = 0, unit: str = "C") -> dict | None:
    """A multi-model answer (models_forecast) -> the agreement on one day:
    {"level": high|medium|low, "head": "Models mostly agree", "detail": "±2° · rain 70–90%",
     "text": the two together, "day": "Tomorrow", "date", "models": [{"name", "high", "low",
     "pop"?, "rain"?}, ...], "odd"?: {"name", "side": cold|warm}, "summary": a sentence to say}."""
    unit = "F" if str(unit).upper().startswith("F") else "C"
    daily = r.get("daily") or {}
    times = daily.get("time") or []
    day = max(0, int(day))
    if day >= len(times):
        return None

    def at(var, model):
        v = daily.get(f"{var}_{model}") or []
        return v[day] if day < len(v) else None
    models = []
    for key, name in MODELS:
        hi, lo = at("temperature_2m_max", key), at("temperature_2m_min", key)
        if hi is None or lo is None:
            continue
        m = {"name": name, "high": round(float(hi), 1), "low": round(float(lo), 1)}
        pop, rain = at("precipitation_probability_max", key), at("precipitation_sum", key)
        if pop is not None:
            m["pop"] = int(round(float(pop)))
        if rain is not None:
            m["rain"] = round(float(rain), 1)
        models.append(m)
    if len(models) < 2:
        return None
    k = 1.8 if unit == "F" else 1.0
    his, los = [m["high"] for m in models], [m["low"] for m in models]
    sh = _spread(his, (_LIMITS["high"][0] * k, _LIMITS["high"][1] * k))
    sl = _spread(los, (_LIMITS["low"][0] * k, _LIMITS["low"][1] * k))
    # temperature: a lone outlier is named and lifts the others' agreement one step at most
    odd = None
    for s in (sh, sl):
        s["eff"] = s["cls"] if s["odd"] is None else min(s["trim_cls"], s["cls"] + 1)
        if s["odd"] is not None and (odd is None or abs(s["dev"]) > abs(odd[1])):
            odd = (s["odd"], s["dev"])
    t_cls = min(sh["eff"], sl["eff"])
    # rain: the models' chances (not every model gives one), else wet/dry votes from the amounts
    pops = [m["pop"] for m in models if "pop" in m]
    sums = [m["rain"] for m in models if "rain" in m]
    dry = all(p <= 15 for p in pops) and all(x < 0.5 for x in sums)
    if dry:
        r_cls, rain_txt, med_pop = 2, "dry", 0.0
    elif pops:
        a, b = min(pops), max(pops)
        r_cls = 2 if a >= 70 or b <= 30 else _class(b - a, _LIMITS["pop"])     # all likely / all unlikely
        rain_txt = f"rain {a}%" if a == b else f"rain {a}–{b}%"
        med_pop = _median(pops)
    else:
        wet = sum(1 for x in sums if x >= 1.0)
        dissent = min(wet, len(sums) - wet)
        r_cls = 2 if dissent == 0 else 1 if dissent == 1 else 0
        rain_txt = f"rain {min(sums):.0f}–{max(sums):.0f} mm"
        med_pop = 100.0 * wet / max(1, len(sums))
    level = min(t_cls, r_cls)
    head = ("Models disagree", "Models mostly agree", "Models agree")[level]

    def pm(rng):
        return max(1, int(round(rng / 2)))
    lows_worse = sl["eff"] < sh["eff"]
    temp_txt = f"highs ±{pm(sh['range'])}°, lows ±{pm(sl['range'])}°" if lows_worse else f"±{pm(sh['range'])}°"
    try:
        wday = time.strptime(times[day], "%Y-%m-%d").tm_wday
    except ValueError:
        wday = None
    day_label = "Today" if day == 0 else "Tomorrow" if day == 1 else DAYS[wday] if wday is not None else times[day][-5:]
    out = {"level": LEVELS[level], "head": head, "detail": f"{temp_txt} · {rain_txt}",
           "text": f"{head} · {temp_txt}, {rain_txt}", "day": day_label, "date": times[day], "models": models}
    if odd is not None:
        out["odd"] = {"name": models[odd[0]]["name"], "side": "cold" if odd[1] < 0 else "warm"}
    # the sentence to say
    lead = ("The models disagree", "The models mostly agree", "The models agree")[level]
    when = "" if day == 0 else " about tomorrow" if day == 1 else \
        (f" about {_LONG_DAYS[wday]}" if wday is not None else f" about {times[day]}")
    a, b = min(his), max(his)
    temps = f"{_say_deg(a)} degrees" if int(round(a)) == int(round(b)) else f"{_say_deg(a)} to {_say_deg(b)} degrees"
    if lows_worse:
        temps = f"highs of {temps}, but lows anywhere from {_say_deg(min(los))} to {_say_deg(max(los))}"
    if r_cls == 0:
        rain_say = ", and they're split on rain"
    elif dry:
        rain_say = ", and dry"
    elif med_pop >= 70:
        rain_say = ", rain likely"
    elif med_pop >= 35:
        rain_say = ", a chance of rain"
    else:
        rain_say = ", mostly dry"
    say = f"{lead}{when}: {temps}{rain_say}."
    if odd is not None:
        who = SPOKEN.get(out["odd"]["name"], out["odd"]["name"])
        say += f" {who[:1].upper() + who[1:]} runs {'colder' if odd[1] < 0 else 'warmer'} than the rest."
    out["summary"] = say
    return out
