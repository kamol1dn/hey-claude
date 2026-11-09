"""A stand-in for weather-mcp over stdio, for tests (no network, no npm).

It has weather-mcp's six tools with their real argument names, and answers
each with the real answer recorded in tests/fixtures/weather_mcp/. Env:
FAKE_WX_DELAY (seconds before every answer), FAKE_WX_LOG (a file that gets one
line per call: the tool name).
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent / "weather_mcp"
_LOG = threading.Lock()          # tools may run in threads: one line at a time (Windows appends aren't atomic)


def _text(name: str) -> str:
    fx = json.loads((HERE / name).read_text(encoding="utf-8"))
    return "\n".join(c["text"] for c in fx["content"])


def _answer(tool: str, name: str) -> str:
    log = os.environ.get("FAKE_WX_LOG")
    if log:
        with _LOG, open(log, "a", encoding="utf-8") as f:
            f.write(tool + "\n")
    time.sleep(float(os.environ.get("FAKE_WX_DELAY") or 0))
    return _text(name)


def build():
    try:
        from mcp.server.mcpserver import MCPServer
    except ImportError:                                     # mcp 1.x
        from mcp.server.fastmcp import FastMCP as MCPServer
    s = MCPServer(name="weather-mcp")

    @s.tool()
    def search_location(query: str, limit: int = 5) -> str:
        """Geocode a place name."""
        return _answer("search_location", "search_location.json")

    @s.tool()
    def get_weather_summary(latitude: float | None = None, longitude: float | None = None, city_name: str | None = None,
                            include: list[str] | None = None, days: int = 7, units: str | None = None) -> str:
        """One-call overview."""
        return _answer("get_weather_summary", "get_weather_summary.json")

    @s.tool()
    def get_forecast(latitude: float | None = None, longitude: float | None = None, city_name: str | None = None,
                     days: int = 7, granularity: str = "daily", include_astronomy: bool = False,
                     source: str = "auto", units: str | None = None) -> str:
        """Daily / hourly / minutely forecast."""
        name = {"hourly": "get_forecast_hourly.json", "minutely": "get_forecast_minutely.json"}.get(
            granularity, "get_forecast_daily.json")
        return _answer("get_forecast:" + granularity, name)

    @s.tool()
    def get_current_conditions(latitude: float | None = None, longitude: float | None = None,
                               city_name: str | None = None, source: str = "auto", units: str | None = None) -> str:
        """Current weather."""
        return _answer("get_current_conditions", "get_current_conditions.json")

    @s.tool()
    def get_alerts(latitude: float | None = None, longitude: float | None = None, city_name: str | None = None,
                   active_only: bool = True) -> str:
        """Active warnings."""
        return _answer("get_alerts", "get_alerts.json")

    @s.tool()
    def get_air_quality(latitude: float | None = None, longitude: float | None = None, city_name: str | None = None,
                        forecast: bool = False) -> str:
        """AQI, UV, pollen."""
        return _answer("get_air_quality", "get_air_quality.json")

    return s


if __name__ == "__main__":
    build().run("stdio")
