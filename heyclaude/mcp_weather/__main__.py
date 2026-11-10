"""The weather MCP server (stdio): `python -m heyclaude.mcp_weather`.

One tool, get_weather. Its result is exactly the island's `weather` widget
data, so hey-claude shows the card from the tool result itself; with
agreement=true it carries the model-agreement chip ("how sure is this?").
Environment: HEYCLAUDE_WEATHER_LOCATION (the default place, from
weather.location) and HEYCLAUDE_WEATHER_UNIT (C or F).
"""
from __future__ import annotations

import os

from heyclaude import weather

INSTRUCTIONS = (
    "Current weather and a short forecast from Open-Meteo (no key). Call get_weather for any weather "
    "question; leave location empty for the user's home place. The island shows the result as a card by "
    "itself, so just say the gist in one or two short sentences. When the user asks how sure the forecast is "
    "(\"how sure is that?\", \"will it really rain tomorrow?\", \"do the models agree?\"), call it with "
    "agreement=true and the day (0 today, 1 tomorrow ...) and say its agreement.summary."
)


def _sdk():
    try:
        from mcp.server.mcpserver import MCPServer
        from mcp.server.mcpserver.exceptions import ToolError
    except ImportError:                                     # mcp 1.x
        from mcp.server.fastmcp import FastMCP as MCPServer
        from mcp.server.fastmcp.exceptions import ToolError
    return MCPServer, ToolError


def get_weather(location: str = "", agreement: bool = False, day: int = 0) -> dict:
    """Current weather, the next 6 hours and 3 days for a place (a city name, optionally
    ", country"). Empty location = the user's home place from Settings.
    agreement=true when the user asks how sure the forecast is ("how sure is that?", "will it
    really rain?", "do the models agree?"): adds "agreement", the same day from seven forecast
    models (ECMWF, GFS, ICON, GEM, Météo-France, UKMO, JMA) compared, with a ready "summary"
    sentence; the card shows it as a chip. day = which day that is about: 0 today, 1 tomorrow, ... 6."""
    place = (location or "").strip() or os.environ.get("HEYCLAUDE_WEATHER_LOCATION", "")
    unit = os.environ.get("HEYCLAUDE_WEATHER_UNIT", "C")
    out = weather.forecast(place, unit=unit)
    if agreement:
        ag = None
        try:
            ag = weather.agreement(place, day=max(0, min(weather.MODEL_DAYS - 1, int(day or 0))), unit=unit)
        except (weather.WeatherError, OSError, ValueError) as e:
            out = dict(out, agreement_error=f"couldn't compare the forecast models: {e}")
        if ag:
            out = dict(out, agreement=ag)
    return out


def build():
    MCPServer, ToolError = _sdk()
    server = MCPServer(name="hey-claude weather", instructions=INSTRUCTIONS)

    def tool(location: str = "", agreement: bool = False, day: int = 0) -> dict:
        try:
            return get_weather(location, agreement, day)
        except weather.WeatherError as e:
            raise ToolError(str(e)) from None
        except OSError as e:
            raise ToolError(f"Open-Meteo didn't answer: {e}") from None
    tool.__name__ = "get_weather"
    tool.__doc__ = get_weather.__doc__
    server.tool(name="get_weather")(tool)
    return server


def main():
    build().run("stdio")


if __name__ == "__main__":
    main()
