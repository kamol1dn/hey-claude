"""The local weather route (ROADMAP M5b): weather-mcp as a warm MCP client, no agent hop.

A plain weather question ("will it rain tomorrow?", "what's the weather in
Paris", "when's sunset") is answered here: `WeatherService.answer(query)`
fetches the data, the island shows the card at once and a short summary built
from the same data is spoken right behind it. No model is asked, so nothing is
made up and the turn takes about as long as the data does.

Data, in order:
1. weather-mcp (`@dangahagan/weather-mcp`, MIT, keyless: Open-Meteo, NOAA,
   MeteoAlarm, MET Norway), started once at warm-up over stdio and kept open
   (client.py). Its answers are markdown, read by parse.py.
2. If it isn't running yet (it takes ~10-40 s to start), node/npx is missing, or
   it doesn't answer within `weather.mcp.timeout_s`, the built-in Open-Meteo
   tool (heyclaude/weather.py) answers instead, and the card still shows. A
   late weather-mcp answer still lands in the cache for the next question.

Pinned package (never @latest; only the official @dangahagan scope, the
README warns about stale republished forks under other scopes):
    @dangahagan/weather-mcp@1.35.8, published 2026-11-09.
Provenance checked 2026-11-10: `npm view @dangahagan/weather-mcp@1.35.8`
has a SLSA v1 provenance attestation (dist.attestations), built by GitHub
Actions (github-hosted runner) from https://github.com/weather-mcp/weather-mcp
at tag v1.35.8 (commit c3bbaf3631e573d41b9341cd388954fbe186c160), workflow
.github/workflows/publish.yml; the attested sha512 matches dist.integrity, and
`npm audit signatures` in a scratch install verified registry signatures for
all 162 packages and attestations for 23. Re-check when changing the pin.

Fair use: weather-mcp caches upstream answers itself (forecasts 2 h, alerts
5 min); ours keeps a place's bundle for `weather.mcp.cache_s` (300 s). Nothing
polls in the background, except an optional home refresh no more often than
every 30 min (`weather.mcp.refresh_min`, off by default).
"""
from __future__ import annotations

import os
import shutil
import threading
import time

from . import card as C
from . import parse as P
from .query import WeatherQuery, parse as parse_query

PACKAGE = "@dangahagan/weather-mcp@1.35.8"
SERVER_NAME = "weather-mcp"                 # never "weather": that's the built-in tool's name
TOOLS = ("get_weather_summary", "get_forecast", "get_current_conditions", "get_alerts", "get_air_quality",
         "search_location")
DEFAULTS = {"enabled": True, "package": PACKAGE, "tools": list(TOOLS), "timeout_s": 2.0, "fallback": True,
            "agent": False, "cache_s": 300, "prefetch": True, "refresh_min": 0, "command": None, "env": {}}

_INHERIT = ("PATH", "PATHEXT", "SYSTEMROOT", "SystemRoot", "SYSTEMDRIVE", "COMSPEC", "WINDIR", "TEMP", "TMP",
            "APPDATA", "LOCALAPPDATA", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "HOME", "ProgramFiles",
            "ProgramFiles(x86)", "ProgramData", "NODE_EXTRA_CA_CERTS", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY",
            "https_proxy", "http_proxy", "no_proxy", "npm_config_cache")


def mcp_cfg(cfg: dict) -> dict:
    """weather.mcp with the defaults filled in."""
    m = ((cfg or {}).get("weather") or {}).get("mcp")
    out = dict(DEFAULTS)
    if isinstance(m, dict):
        out.update({k: v for k, v in m.items() if v is not None or k == "command"})
    elif m is False:
        out["enabled"] = False
    return out


def unit_of(cfg: dict) -> str:
    return "F" if str(((cfg or {}).get("weather") or {}).get("unit") or "C").upper().startswith("F") else "C"


def server_command(cfg: dict) -> tuple[str, list[str], dict] | None:
    """(command, args, env) to start weather-mcp, or None when it can't run here.

    `weather.mcp.command` (a list: executable + args) replaces npx, e.g. for a
    local checkout; the tests use it for a fake server."""
    m = mcp_cfg(cfg)
    tools = [t for t in (m.get("tools") or TOOLS) if t in TOOLS] or list(TOOLS)
    env = {k: os.environ[k] for k in _INHERIT if os.environ.get(k)}
    env.update({"ENABLED_TOOLS": ",".join(tools),
                "WEATHER_UNITS": "imperial" if unit_of(cfg) == "F" else "metric",
                "WEATHER_TIME_FORMAT": "24h", "ANALYTICS_ENABLED": "false", "WEATHER_LIGHTNING_PREWARM": "false",
                "LOG_LEVEL": "2"})
    env.update({str(k): str(v) for k, v in (m.get("env") or {}).items()})     # e.g. its optional API keys
    if m.get("command"):
        cmd = [str(x) for x in (m["command"] if isinstance(m["command"], list) else [m["command"]])]
        return cmd[0], cmd[1:], env
    pkg = str(m.get("package") or PACKAGE)
    if not pkg.startswith("@dangahagan/weather-mcp@") or pkg.rsplit("@", 1)[1] in ("", "latest", "next"):
        print(f"[weather] weather.mcp.package must be @dangahagan/weather-mcp@<exact version>, not {pkg!r}; using {PACKAGE}")
        pkg = PACKAGE
    npx = shutil.which("npx")
    if not npx:
        return None
    return npx, ["--prefer-offline", "-y", pkg], env


def agent_servers(cfg: dict) -> dict:
    """mcpServers entry for the tool agent (`mcp__weather-mcp__...`), when weather.mcp.agent is on."""
    w = (cfg or {}).get("weather") or {}
    m = mcp_cfg(cfg)
    if not w.get("enabled", True) or not m.get("enabled") or not m.get("agent"):
        return {}
    sc = server_command(cfg)
    if sc is None:
        return {}
    cmd, args, env = sc
    keep = {k: v for k, v in env.items() if k not in _INHERIT}          # Claude Code passes its own environment
    return {SERVER_NAME: {"command": cmd, "args": args, "env": keep}}


def agent_tools(cfg: dict) -> list[str]:
    if not agent_servers(cfg):
        return []
    tools = [t for t in (mcp_cfg(cfg).get("tools") or TOOLS) if t in TOOLS] or list(TOOLS)
    return [f"mcp__{SERVER_NAME}__{t}" for t in tools]


class WeatherService:
    """The local weather route's data side. Thread-safe; one per Brain."""

    def __init__(self, cfg: dict, client=None):
        self.cfg = cfg
        self.client = client
        self._cache: dict[tuple, tuple[float, dict]] = {}
        self._places: dict[str, tuple[float, float, str]] = {}     # place -> (lat, lon, label); places don't move
        self._lock = threading.Lock()
        self._warned: set[str] = set()
        self.last: WeatherQuery | None = None        # the previous weather turn's query (follow-ups, "there")
        self.timing: dict = {}

    # -- set-up ---------------------------------------------------------------------
    def start(self):
        """Start weather-mcp in the background (non-blocking) and, if set, fetch home once it's up."""
        m = mcp_cfg(self.cfg)
        if getattr(self, "_started", False):
            return
        self._started = True
        if self.client is None and m.get("enabled"):
            sc = server_command(self.cfg)
            if sc is None:
                self._warn("npx", "[weather] node/npx not found: weather-mcp is off, the built-in Open-Meteo tool "
                                  "answers weather questions (install Node.js 18+ to get alerts, air quality, nowcast)")
            else:
                from .client import StdioClient
                from ..config import LOG_DIR, ROOT
                cmd, args, env = sc
                self.client = StdioClient(cmd, args, env, cwd=str(ROOT), name="weather-mcp",
                                          errlog_path=str(LOG_DIR / "weather-mcp.stderr.log"))
        if self.client is not None:
            self.client.start()
        if m.get("prefetch") or float(m.get("refresh_min") or 0) > 0:
            threading.Thread(target=self._prefetch_loop, daemon=True, name="weather-prefetch").start()

    def close(self):
        if self.client is not None:
            try:
                self.client.close()
            except Exception:
                pass

    def _warn(self, key: str, msg: str):
        if key not in self._warned:
            self._warned.add(key)
            print(msg)

    def _prefetch_loop(self):
        m = mcp_cfg(self.cfg)
        home = self._home()
        if not home or self.client is None:
            return
        if not self.client.wait_ready(300):
            return
        every = float(m.get("refresh_min") or 0)
        every = max(30.0, every) * 60 if every > 0 else 0
        while True:
            try:
                t = time.perf_counter()
                self._mcp_bundle(home, WeatherQuery(text="", focus="general"), wait=60)
                print(f"[weather] home forecast warmed in {time.perf_counter() - t:.1f}s")
            except Exception as e:
                print(f"[weather] prefetch: {e}")
            if not every:
                return
            time.sleep(every)

    # -- routing ----------------------------------------------------------------------
    def query(self, text: str, prev_turn_was_weather: bool = False) -> WeatherQuery | None:
        w = (self.cfg or {}).get("weather") or {}
        if not w.get("enabled", True) or not w.get("local_route", True):
            return None
        return parse_query(text, prev=self.last if prev_turn_was_weather else None)

    def _home(self) -> str:
        return str(((self.cfg or {}).get("weather") or {}).get("location") or "").strip()

    # -- answering --------------------------------------------------------------------
    def answer(self, q: WeatherQuery) -> tuple[dict | None, str]:
        """(widget spec or None, the spoken summary). Never raises."""
        t0 = time.perf_counter()
        self.timing = {}
        place = self._home() if q.home else q.place
        if not place:
            return None, "Which place? Say a city, or set your home place in Settings under weather."
        unit = unit_of(self.cfg)
        facts, label = None, place
        try:
            facts, label = self._facts(place, q)
        except Exception as e:
            print(f"[weather] {place}: {type(e).__name__}: {e}")
        if not facts:
            return None, f"I couldn't get the weather for {place.split(',')[0]} just now."
        self.last = q
        try:
            data = C.card(facts, q, label)
            speech = C.speak(facts, q, label, wind_unit="mph" if unit == "F" else "km/h")
        except Exception as e:                          # a parsing oddity never breaks the turn
            print(f"[weather] card: {type(e).__name__}: {e}")
            return None, f"I couldn't read the weather for {place.split(',')[0]} just now."
        from ..widgets import normalise
        spec = normalise({"widget": "weather", "data": data, "text": speech[:200]})
        self.timing.update(total=round(time.perf_counter() - t0, 3), source=facts.get("source", ""))
        print(f"[weather] {q.focus}/{q.when} for {label}: {facts.get('source')} in {self.timing['total']:.2f}s")
        return spec, speech

    def _facts(self, place: str, q: WeatherQuery) -> tuple[dict | None, str]:
        m = mcp_cfg(self.cfg)
        timeout = max(0.2, float(m.get("timeout_s") or 2.0))
        if self.client is not None and self.client.ready.is_set():
            try:
                facts, label = self._mcp_bundle(place, q, wait=timeout)
                if facts and (facts.get("now") or facts.get("days")):
                    return facts, label
            except Exception as e:
                print(f"[weather] weather-mcp: {type(e).__name__}: {e}"[:300])
        elif self.client is not None:
            self._warn("starting", "[weather] weather-mcp is still starting: the built-in Open-Meteo tool answers")
        if not m.get("fallback", True) and self.client is not None:
            return None, place
        return self._builtin(place, q)

    def _builtin(self, place: str, q: WeatherQuery) -> tuple[dict | None, str]:
        from .. import weather as WX
        t = time.perf_counter()
        days = 7 if q.when in ("week", "weekend") or q.day > 2 else 3
        d = WX.forecast(place, unit=unit_of(self.cfg), days=days, detail=True)
        facts = C.from_builtin(d)
        today = C._date((facts.get("now") or {}).get("date", ""))
        facts["moon"] = C.moon_phase(today)
        nf = C.next_full_moon(today)
        facts["next_full_moon"] = f"{nf.strftime('%B')} {nf.day}"
        self.timing["fallback_s"] = round(time.perf_counter() - t, 3)
        return facts, d.get("location") or place

    # -- weather-mcp ----------------------------------------------------------------------
    async def _aresolve(self, place: str) -> tuple[float, float, str]:
        """(lat, lon, label) through search_location, remembered (places don't move)."""
        with self._lock:
            hit = self._places.get(place.lower())
        if hit:
            return hit
        md = await self.client.acall("search_location", {"query": place, "limit": 1}, timeout=30)
        ll = P.location_of(md)
        if ll is None:
            raise LookupError(f"weather-mcp couldn't find {place!r}")
        full = P.place_name_of(md)
        # "London" -> "London, United Kingdom": the spoken name plus the country
        country = full.split(",")[-1].strip() if "," in full else ""
        label = place if "," in place or not country or country.lower() == place.lower() else f"{place}, {country}"
        out = (ll[0], ll[1], label)
        with self._lock:
            self._places[place.lower()] = out
        return out

    def _mcp_bundle(self, place: str, q: WeatherQuery, wait: float) -> tuple[dict | None, str]:
        """The place, then the card's calls in parallel, on the warm connection; what's back
        within `wait`. The rest keeps going in the background and lands in the cache."""
        import asyncio
        t0 = time.perf_counter()
        unit = unit_of(self.cfg)
        days = 7 if q.when in ("week", "weekend") or q.day > 2 else 3
        key = (place.lower(), unit, days)
        m = mcp_cfg(self.cfg)
        with self._lock:
            hit = self._cache.get(key)
        if hit and time.time() - hit[0] < float(m.get("cache_s") or 300):
            self.timing.update(mcp_s=round(time.perf_counter() - t0, 3), cached=True)
            return dict(hit[1]), hit[1].get("location") or place
        units = "imperial" if unit == "F" else "metric"
        st: dict = {"parts": {}, "label": place, "error": None}
        lock, done = threading.Lock(), threading.Event()
        core = ("current", "daily")

        async def one(name, tool, args):
            try:
                txt = await self.client.acall(tool, args, timeout=30)
            except Exception as e:
                txt = ""
                print(f"[weather] {tool} ({name}): {type(e).__name__}: {e}"[:200])
            with lock:
                st["parts"][name] = txt

        async def pipeline():
            try:
                lat, lon, label = await self._aresolve(place)
                st["label"] = label
                ll = {"latitude": lat, "longitude": lon}
                calls = {
                    "current": ("get_current_conditions", dict(ll, source="openmeteo", units=units)),
                    "daily": ("get_forecast", dict(ll, days=days, include_astronomy=True, source="openmeteo", units=units)),
                    "hourly": ("get_forecast", dict(ll, days=2, granularity="hourly", source="openmeteo", units=units)),
                    "alerts": ("get_alerts", dict(ll)),
                    "air": ("get_air_quality", dict(ll)),
                    "nowcast": ("get_forecast", dict(ll, granularity="minutely", units=units)),
                }
                await asyncio.gather(*(one(n, t, a) for n, (t, a) in calls.items()))
                with lock:
                    snapshot = dict(st["parts"])
                if all(snapshot.get(c) for c in core):          # a late answer still warms the cache
                    facts = self._facts_from(snapshot, unit)
                    facts["location"] = label
                    with self._lock:
                        self._cache[key] = (time.time(), facts)
            except Exception as e:
                st["error"] = e
            finally:
                done.set()
        self.client.submit(pipeline())
        done.wait(max(0.0, wait - (time.perf_counter() - t0)))
        with lock:
            snapshot = dict(st["parts"])
        self.timing["mcp_s"] = round(time.perf_counter() - t0, 3)
        self.timing["mcp_parts"] = sorted(k for k, v in snapshot.items() if v)
        if st["error"] is not None:
            raise st["error"]
        if not all(snapshot.get(c) for c in core):
            raise TimeoutError(f"no forecast within {wait:.1f}s (got {', '.join(self.timing['mcp_parts']) or 'nothing'})")
        facts = self._facts_from(snapshot, unit)
        facts["location"] = st["label"]
        return facts, st["label"]

    @staticmethod
    def _facts_from(parts: dict[str, str], unit: str) -> dict:
        facts: dict = {"unit": unit, "source": "weather-mcp"}
        if parts.get("current"):
            facts["now"] = P.current(parts["current"])
        if parts.get("daily"):
            year = None
            try:
                year = int((facts.get("now") or {}).get("date", "")[:4])
            except ValueError:
                pass
            facts["days"] = P.daily(parts["daily"], year)
            nf = P.next_full_moon(parts["daily"])
            if nf:
                facts["next_full_moon"] = nf
        if parts.get("hourly"):
            facts["hours"] = P.hourly(parts["hourly"])
        if parts.get("alerts"):
            facts["alerts"] = P.alerts(parts["alerts"])
        if parts.get("air"):
            facts["air"] = P.air(parts["air"])
        if parts.get("nowcast"):
            facts["nowcast"] = P.nowcast(parts["nowcast"])
        return facts


def widget_from_tool(args: dict, result, raw: str) -> dict | None:
    """The `weather` card from a tool result: the built-in tool's JSON as it is, or a
    weather-mcp answer (markdown) read into the same card. None when there's no temperature."""
    if isinstance(result, dict) and "temp" in result and "location" in result:
        return result
    md = raw or P.text_of(result)
    if not md or "Temperature" not in md:
        return None
    facts = P.summary(md) if md.lstrip().startswith("# Weather Summary") else {}
    if not facts:
        cur = P.current(md) if "# Current Weather" in md else {}     # a forecast alone has no "now"
        facts = {"now": cur} if cur else {}
        days = P.daily(md)
        if days:
            facts["days"] = days
        hours = P.hourly(md)
        if hours:
            facts["hours"] = hours
    if (facts.get("now") or {}).get("temp") is None:
        return None
    a = args or {}
    label = str(a.get("city_name") or a.get("location_name") or "").strip()
    if not label and a.get("latitude") is not None and a.get("longitude") is not None:
        label = f"{float(a['latitude']):.2f}, {float(a['longitude']):.2f}"
    facts.update(source="weather-mcp", unit="F" if "°F" in md else "C")
    return C.card(facts, None, label)
