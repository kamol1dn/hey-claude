"""The weather MCP server (stdio): `python -m heyclaude.mcp_weather`.

One tool, get_weather. Its result is exactly the island's `weather` widget
data, so hey-claude shows the card from the tool result itself.
Environment: HEYCLAUDE_WEATHER_LOCATION (the default place, from
weather.location) and HEYCLAUDE_WEATHER_UNIT (C or F).
"""
from __future__ import annotations

import os

from heyclaude import weather

INSTRUCTIONS = (
    "Current weather and a short forecast from Open-Meteo (no key). Call get_weather for any weather "
    "question; leave location empty for the user's home place. The island shows the result as a card by "
    "itself, so just say the gist in one or two short sentences."
)


def _sdk():
    try:
        from mcp.server.mcpserver import MCPServer
        from mcp.server.mcpserver.exceptions import ToolError
    except ImportError:                                     # mcp 1.x
        from mcp.server.fastmcp import FastMCP as MCPServer
        from mcp.server.fastmcp.exceptions import ToolError
    return MCPServer, ToolError


def get_weather(location: str = "") -> dict:
    """Current weather, the next 6 hours and 3 days for a place (a city name, optionally
    ", country"). Empty location = the user's home place from Settings."""
    place = (location or "").strip() or os.environ.get("HEYCLAUDE_WEATHER_LOCATION", "")
    return weather.forecast(place, unit=os.environ.get("HEYCLAUDE_WEATHER_UNIT", "C"))


def build():
    MCPServer, ToolError = _sdk()
    server = MCPServer(name="hey-claude weather", instructions=INSTRUCTIONS)

    def tool(location: str = "") -> dict:
        try:
            return get_weather(location)
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
