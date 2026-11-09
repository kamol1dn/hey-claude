"""Live check for M9's Windows settings tools: runs each tool on this PC,
checks it did what it says, then puts every value back (through the same
undo calls "undo that" uses) and checks that too.

    .venv\\Scripts\\python tools\\check_windows_settings.py            # the safe set
    .venv\\Scripts\\python tools\\check_windows_settings.py --all      # + radios, output device, lock

What it changes for a moment (and restores): volume, mute, one app's volume,
brightness, dark/light mode, wallpaper, night light, power mode, clipboard,
play/pause (briefly audible if something is paused), a Character Map window it
opens itself (snapped, minimized, restored, closed), a Settings page it opens
and closes. Opt-in, because they can cut you off: --radios (Wi-Fi and
Bluetooth off and on again), --device (default output device), --lock.
Sleep / restart / shutdown are only *requested* (nothing happens without a
click on the island), and that is all this checks.

It also starts the real MCP server over stdio and calls it once, the way the
tool agent does.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile
import time
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from heyclaude.mcp_windows import apps, clipboard, display, media, power, registry  # noqa: E402
from heyclaude.mcp_windows._util import ToolError, com_init  # noqa: E402

RESULTS: list[tuple[str, str, str]] = []


def step(name):
    def deco(fn):
        def run(*a, **kw):
            t = time.perf_counter()
            try:
                note = fn(*a, **kw) or ""
                status = "ok"
            except Skip as e:
                status, note = "skip", str(e)
            except Exception as e:
                status, note = "FAIL", f"{type(e).__name__}: {e}"
                traceback.print_exc()
            ms = (time.perf_counter() - t) * 1000
            RESULTS.append((name, status, note))
            print(f"[{status:4}] {name} ({ms:.0f} ms) {note}")
        return run
    return deco


class Skip(Exception):
    pass


def call(_tool, **args):
    return registry.call(_tool, args)


def undo(name, args, result):
    calls = registry.inverse("mcp__windows__" + name, args, result)
    assert calls, f"{name} returned no undo for {result}"
    out = None
    for n, a in calls:
        out = registry.call(n, a)
    return out


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)


# == audio ===========================================================================
@step("volume: set, step, undo")
def t_volume():
    before = call("get_volume")
    target = 20 if before["level"] > 40 else before["level"] + 15
    r = call("set_volume", level=target)
    check(call("get_volume")["level"] == target and r["old"] == before["level"], f"set_volume {r}")
    r2 = call("set_volume", delta=-5)
    check(r2["new"] == max(0, target - 5), f"delta {r2}")
    undo("set_volume", {}, r2)
    undo("set_volume", {}, r)
    after = call("get_volume")
    check(after == before, f"not restored: {after} != {before}")
    return f"{before['level']} -> {target} -> {target - 5} -> back to {after['level']}"


@step("mute: toggle, undo")
def t_mute():
    before = call("get_volume")
    r = call("set_mute")
    check(call("get_volume")["muted"] == (not before["muted"]), "mute didn't toggle")
    undo("set_mute", {}, r)
    check(call("get_volume") == before, "mute not restored")
    return f"muted {before['muted']} -> {r['new']} -> back"


@step("per-app volume: set, undo")
def t_app_volume():
    apps_ = [a for a in call("list_app_volumes")["apps"] if a["app"] != "system sounds"]
    if not apps_:
        raise Skip("no app has an audio session")
    a = apps_[0]
    r = call("set_app_volume", app=a["app"], level=max(5, a["level"] - 20))
    undo("set_app_volume", {}, r)
    now = next(x for x in call("list_app_volumes")["apps"] if x["app"] == a["app"])
    check(now["level"] == a["level"], f"{a['app']} not restored: {now['level']} != {a['level']}")
    return f"{a['app']} {a['level']} -> {r['new']} -> back"


@step("output devices: list (+ switch with --device)")
def t_devices(switch: bool):
    devs = call("list_audio_devices")["devices"]
    check(any(d["default"] for d in devs), "no default device")
    if not switch:
        return f"{len(devs)} devices; switching skipped (experimental, --device)"
    other = next((d for d in devs if not d["default"]), None)
    if other is None:
        raise Skip("only one output device")
    r = call("set_default_output_device", device=other["id"])
    time.sleep(0.5)
    undo("set_default_output_device", {}, r)
    now = next(d for d in call("list_audio_devices")["devices"] if d["default"])
    check(now["name"] == r["old"], "default device not restored")
    return f"{r['old']} -> {r['new']} -> back"


# == display =========================================================================
@step("brightness: down, undo")
def t_brightness():
    before = call("get_brightness")
    if before["level"] is None:
        raise Skip(before.get("note", "no monitor reports brightness"))
    r = call("set_brightness", delta=-20 if before["level"] > 25 else 20)
    time.sleep(0.3)
    mid = call("get_brightness")
    check(mid["level"] == r["new"], f"brightness {mid} != {r['new']}")
    undo("set_brightness", {}, r)
    time.sleep(0.3)
    after = call("get_brightness")
    check(after["monitors"] == before["monitors"], f"not restored {after} != {before}")
    return f"{before['level']} -> {r['new']} -> back ({', '.join(m['id'] for m in before['monitors'])})"


@step("dark/light mode: toggle, undo")
def t_theme():
    before = call("get_theme")
    r = call("set_theme", mode="toggle")
    check(call("get_theme")["mode"] == r["new"] != before["mode"], f"theme {r}")
    time.sleep(1.0)
    undo("set_theme", {}, r)
    check(call("get_theme") == before, "theme not restored")
    return f"{before['mode']} -> {r['new']} -> back"


def _test_image(path):
    from PySide6.QtGui import QColor, QImage
    img = QImage(320, 180, QImage.Format_RGB32)
    img.fill(QColor("#2b3a42"))
    img.save(path)


@step("wallpaper: set, undo")
def t_wallpaper():
    before = call("get_wallpaper")
    if not any(m["path"] and os.path.isfile(m["path"]) for m in before["monitors"]):
        raise Skip("the current wallpaper isn't an image file (Spotlight / colour), so it can't be put back")
    path = os.path.join(tempfile.mkdtemp(prefix="hc-wall-"), "hey-claude-check.png")
    _test_image(path)
    r = call("set_wallpaper", path=path)
    time.sleep(0.6)
    check(all(m["path"].lower() == path.lower() for m in call("get_wallpaper")["monitors"]), "wallpaper not set")
    undo("set_wallpaper", {}, r)
    time.sleep(0.6)
    after = call("get_wallpaper")
    check([m["path"].lower() for m in after["monitors"]] == [m["path"].lower() for m in before["monitors"]],
          f"not restored: {after}")
    return f"{os.path.basename(before['path'])} -> test image -> back"


@step("night light (experimental): toggle, undo")
def t_night_light():
    raw = display._os_night_blob()
    if raw is None:
        raise Skip("night light has never been used on this account")
    before = call("get_night_light")
    try:
        r = call("set_night_light", on=not before["on"])
        check(call("get_night_light")["on"] == r["new"], "night light state didn't change")
        time.sleep(1.0)
        undo("set_night_light", {}, r)
        check(call("get_night_light") == before, "night light not restored")
    finally:
        cur = display._os_night_blob()
        if cur is not None and (cur[18] == 0x15) != (raw[18] == 0x15):
            display._os_set_night_blob(display.night_light_blob(cur, raw[18] == 0x15))
    return f"on={before['on']} -> {r['new']} -> back"


# == radios (opt-in) ==================================================================
@step("radios: read (+ Wi-Fi / Bluetooth off and on with --radios)")
def t_radios(switch: bool):
    before = call("get_radios")
    kinds = {r["kind"]: r["state"] for r in before["radios"]}
    if not switch:
        return f"{kinds}, airplane={before['airplane_mode']}; switching skipped (--radios)"
    notes = []
    for kind, tool in (("bluetooth", "set_bluetooth"), ("wifi", "set_wifi")):
        if kind not in kinds:
            continue
        r = call(tool)
        time.sleep(1.5)
        undo(tool, {}, r)
        time.sleep(1.5)
        now = {x["kind"]: x["state"] for x in call("get_radios")["radios"]}
        check(now[kind] == kinds[kind], f"{kind} not restored")
        notes.append(f"{kind} {r['old']}->{r['new']}->back")
    return "; ".join(notes)


# == power ===========================================================================
@step("battery and power mode: set, undo")
def t_power():
    bat = call("battery_status")
    before = call("get_power_mode")["mode"]
    target = "best_performance" if before != "best_performance" else "balanced"
    try:
        r = call("set_power_mode", mode=target)
    except ToolError as e:
        raise Skip(str(e))
    check(call("get_power_mode")["mode"] == target, "power mode not set")
    undo("set_power_mode", {}, r)
    check(call("get_power_mode")["mode"] == before, "power mode not restored")
    return f"battery {bat.get('percent')}% plugged={bat.get('plugged_in')}; mode {before} -> {target} -> back"


@step("sleep / restart / shutdown: only asked, never done")
def t_power_ask():
    done = []
    real = power._os_power_action
    power._os_power_action = lambda a: done.append(a)
    try:
        for a in power.POWER_ACTIONS:
            r = call("request_power_action", action=a)
            check(r["needs_click"] and r["pending"] == a, str(r))
    finally:
        power._os_power_action = real
    check(done == [], "a power action ran!")
    return "3 requests, 0 actions"


@step("lock (with --lock)")
def t_lock(go: bool):
    if not go:
        raise Skip("would lock this PC (--lock)")
    call("lock_pc")
    return "locked"


# == media ===========================================================================
@step("media: now playing, play/pause, undo")
def t_media():
    before = media._os_now_playing()
    if before.get("status") in (None, "none", "closed"):
        raise Skip("no media session")
    action = "pause" if before["status"] == "playing" else "play"
    try:
        r = call("media_control", action=action)
    except ToolError as e:
        raise Skip(f"player refused {action}: {e}")
    undo("media_control", {}, r)
    time.sleep(0.8)
    after = media._os_now_playing()
    check(after.get("status") == before.get("status"), f"status {after.get('status')} != {before.get('status')}")
    art = media.album_art()
    return (f"{before.get('app')}: {before.get('status')} -> {r['new'].get('status')} -> back; "
            f"art {len(art) if art else 0} bytes")


# == apps and windows =================================================================
@step("apps and windows: launch, snap, minimize, undo, focus, close")
def t_windows():
    names = call("list_apps", query="character map")["apps"]
    app = "Character Map" if names else "Notepad"
    before = {w["hwnd"] for w in apps._os_windows()}
    call("launch_app", name=app)
    new = None
    for _ in range(40):
        time.sleep(0.25)
        new = next((w for w in apps._os_windows() if w["hwnd"] not in before and app.split()[0].lower()
                    in (w["title"] + w["app"]).lower()), None)
        if new:
            break
    check(new is not None, f"{app} window didn't appear")
    h, title = new["hwnd"], new["title"]
    try:
        time.sleep(0.5)
        pl0 = apps._os_placement(h)
        r = registry.call("snap_window", {"side": "left", "title": title})
        check(r["hwnd"] == h, "snapped another window")
        undo("snap_window", {}, r)
        r = registry.call("minimize_window", {"title": title})
        time.sleep(0.3)
        check(any(w["hwnd"] == h and w["minimized"] for w in apps._os_windows()), "not minimized")
        undo("minimize_window", {}, r)
        time.sleep(0.3)
        check(any(w["hwnd"] == h and not w["minimized"] for w in apps._os_windows()), "not restored")
        check(apps._os_placement(h)[4] == pl0[4], "position not restored")
        f = registry.call("focus_window", {"title": title})
        check(any(w["hwnd"] == h for w in apps._os_windows()), "window gone")
        n = len(call("list_windows")["windows"])
    finally:
        apps._os_close(h)
    time.sleep(0.6)
    check(not any(w["hwnd"] == h for w in apps._os_windows()), "didn't close")
    return f"{app}: snapped, minimized, restored, focused={f['focused']}, closed; {n} windows listed"


@step("close_window tool (on the test window only)")
def t_close():
    names = call("list_apps", query="character map")["apps"]
    if not names:
        raise Skip("no Character Map to open and close")
    if any("character map" in w["title"].lower() for w in apps._os_windows()):
        raise Skip("a Character Map window was already open")
    call("launch_app", name="Character Map")
    for _ in range(40):
        time.sleep(0.25)
        if any("character map" in w["title"].lower() for w in apps._os_windows()):
            break
    r = call("close_window", title="Character Map")
    time.sleep(0.6)
    check(not any("character map" in w["title"].lower() for w in apps._os_windows()), "still open")
    return str(r)


# == clipboard, screen, settings ======================================================
@step("clipboard: set, undo")
def t_clipboard():
    before = clipboard._os_get()
    if before is None:
        raise Skip("the clipboard holds something other than text; not touching it")
    r = call("set_clipboard", text="hey-claude live check")
    check(clipboard._os_get() == "hey-claude live check", "clipboard not set")
    if r.get("undoable") is False:
        clipboard._os_set(before)
    else:
        undo("set_clipboard", {}, r)
    check(clipboard._os_get() == before, "clipboard not restored")
    return f"{len(before)} chars -> test text -> back"


@step("screenshot")
def t_screenshot():
    r = call("screenshot", monitor=0)
    check(r["image_jpeg"][:2] == b"\xff\xd8" and len(r["image_jpeg"]) > 5000, "not a jpeg")
    return f"{r['width']}x{r['height']}, {len(r['image_jpeg']) // 1024} KB jpeg (not saved)"


@step("Settings page: open, close")
def t_settings():
    def settings_windows():
        return [w for w in apps._os_windows() if w["title"] == "Settings"]
    if settings_windows():
        raise Skip("Settings is already open; not closing the user's window")
    r = call("open_settings_page", page="display")
    w = None
    for _ in range(40):
        time.sleep(0.25)
        ws = settings_windows()
        if ws:
            w = ws[0]
            break
    check(w is not None, "Settings didn't open")
    time.sleep(0.5)
    apps._os_close(w["hwnd"])
    return f"{r['opened']} opened and closed"


@step("MCP server over stdio (as the agent runs it)")
def t_stdio():
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    from heyclaude.brain import windows_mcp_servers
    spec = windows_mcp_servers({"windows_settings": True})["windows"]

    async def go():
        params = StdioServerParameters(command=spec["command"], args=spec["args"],
                                       env={**os.environ, **spec["env"]})
        async with stdio_client(params) as streams:
            async with ClientSession(streams[0], streams[1]) as s:
                await s.initialize()
                tools = (await s.list_tools()).tools
                res = await s.call_tool("get_volume", {})
                return len(tools), res
    t = time.perf_counter()
    n, res = asyncio.run(go())
    check(n == len(registry.TOOLS), f"{n} tools listed")
    text = next(c.text for c in res.content if getattr(c, "text", None))
    return f"{n} tools, get_volume -> {text} ({time.perf_counter() - t:.1f}s incl. start)"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--all", action="store_true", help="also --radios --device --lock")
    ap.add_argument("--radios", action="store_true", help="switch Wi-Fi and Bluetooth off and on")
    ap.add_argument("--device", action="store_true", help="switch the default output device and back")
    ap.add_argument("--lock", action="store_true", help="lock the PC at the end")
    a = ap.parse_args()
    com_init()
    t_volume()
    t_mute()
    t_app_volume()
    t_devices(a.device or a.all)
    t_brightness()
    t_theme()
    t_wallpaper()
    t_night_light()
    t_radios(a.radios or a.all)
    t_power()
    t_power_ask()
    t_media()
    t_windows()
    t_close()
    t_clipboard()
    t_screenshot()
    t_settings()
    t_stdio()
    t_lock(a.lock or a.all)
    bad = [r for r in RESULTS if r[1] == "FAIL"]
    print(f"\n{sum(r[1] == 'ok' for r in RESULTS)} ok, {sum(r[1] == 'skip' for r in RESULTS)} skipped, "
          f"{len(bad)} failed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
