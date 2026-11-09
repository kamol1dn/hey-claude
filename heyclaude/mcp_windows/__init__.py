"""hey-claude's own Windows settings MCP server (ROADMAP M9).

Comfort settings only: audio, display (brightness, dark mode, wallpaper,
night light), radios, power, media, apps and windows, clipboard, screenshots
and Settings pages. Every write tool returns {"old", "new"}, which feeds the
island's effect and "undo that".

Never exposed: Defender, firewall, UAC, user accounts, raw registry editing,
arbitrary PowerShell, drivers and services. Sleep / restart / shutdown are
only *requested* by a tool; the user confirms with a click on the island.

    python -m heyclaude.mcp_windows        # stdio server (the tool agent starts it)
"""
