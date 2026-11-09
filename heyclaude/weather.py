"""Weather from Open-Meteo: keyless, no account (https://open-meteo.com).

`forecast("Tashkent")` -> the `weather` widget's data, straight from the API:
    {"location": "Tashkent, Uzbekistan", "temp": 21, "unit": "C", "condition": "Partly cloudy",
     "icon": "partly", "feels": 20, "high": 24, "low": 13, "wind": 11,
     "hourly": [{"time": "15:00", "temp": 22, "icon": "sun"}, ... 6],
     "days": [{"day": "Tue", "low": 13, "high": 24, "icon": "partly"}, ... 3],
     "source": "Open-Meteo"}

Places are looked up with Open-Meteo's geocoding API. Answers are cached for
10 minutes per place, so asking again doesn't hit the network. The tool agent
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


def geocode(place: str, fetch=_get) -> dict:
    """{"name", "lat", "lon", "label"} for a place name."""
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
             now: float | None = None, fetch=_get) -> dict:
    """Current conditions, the next 6 hours and 3 days, as the weather widget's data."""
    unit = "F" if str(unit).upper().startswith("F") else "C"
    if lat is None or lon is None:
        g = geocode(place, fetch)
        lat, lon, label = g["lat"], g["lon"], g["label"]
    else:
        label = place or f"{lat:.2f}, {lon:.2f}"
    key = (round(float(lat), 3), round(float(lon), 3), unit)
    t = now if now is not None else time.time()
    hit = _cache.get(key)
    if hit and t - hit[0] < TTL_S:
        return dict(hit[1], location=label, cached=True)
    p = {"latitude": lat, "longitude": lon, "timezone": "auto", "forecast_days": 3,
         "current": "temperature_2m,apparent_temperature,weather_code,is_day,wind_speed_10m",
         "hourly": "temperature_2m,weather_code,is_day",
         "daily": "weather_code,temperature_2m_max,temperature_2m_min"}
    if unit == "F":
        p["temperature_unit"] = "fahrenheit"
        p["wind_speed_unit"] = "mph"
    r = fetch(FORECAST_URL, p)
    out = to_widget(r, label, unit)
    _cache[key] = (t, out)
    return out


def to_widget(r: dict, label: str, unit: str = "C") -> dict:
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
    return out
