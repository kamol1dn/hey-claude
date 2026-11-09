"""Offline tests for M9 (control Windows by voice): every settings tool with
the Windows APIs mocked, the tool registry and the never-exposed list, the
effects mapping, undo, the agent's allow-list gating and tool-event
plumbing, and the island's effect row. No Windows state is touched.

    .venv\\Scripts\\python tests\\test_windows.py
"""
import json
import os
import sys
import time
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from heyclaude import brain as B  # noqa: E402
from heyclaude import effects as E  # noqa: E402
from heyclaude.mcp_windows import apps, audio, clipboard, display, media, power, radios, registry, screen  # noqa: E402
from heyclaude.mcp_windows._util import ToolError  # noqa: E402
from heyclaude.undo import UndoStack, describe  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")


def raises(exc, fn, *a, **kw):
    try:
        fn(*a, **kw)
    except exc as e:
        return e
    raise AssertionError(f"{fn.__name__} did not raise {exc.__name__}")


# == fakes ===========================================================================
class FakeEndpoint:
    def __init__(self, level=0.45, muted=False):
        self.level, self.muted = level, muted

    def GetMasterVolumeLevelScalar(self):
        return self.level

    def SetMasterVolumeLevelScalar(self, v, ctx):
        self.level = v

    def GetMute(self):
        return 1 if self.muted else 0

    def SetMute(self, m, ctx):
        self.muted = bool(m)


class FakeSimpleVolume:
    def __init__(self, v):
        self.v, self.m = v, False

    def GetMasterVolume(self):
        return self.v

    def SetMasterVolume(self, v, ctx):
        self.v = v

    def GetMute(self):
        return self.m


class FakeProc:
    def __init__(self, name):
        self._n = name

    def name(self):
        return self._n


class FakeSession:
    def __init__(self, exe, v):
        self.Process = FakeProc(exe)
        self.DisplayName = ""
        self.SimpleAudioVolume = FakeSimpleVolume(v)


# == audio ===========================================================================
def test_volume_set_step_and_unmute():
    ep = FakeEndpoint(0.45, muted=True)
    with mock.patch.object(audio, "_os_endpoint", lambda: ep):
        assert audio.get_volume() == {"level": 45, "muted": True}
        r = audio.set_volume(level=30)
        assert (r["old"], r["new"], r["old_muted"], r["muted"]) == (45, 30, True, False)
        assert abs(ep.level - 0.30) < 1e-9 and not ep.muted
        r = audio.set_volume(delta=-50)
        assert (r["old"], r["new"]) == (30, 0)            # clamped
        assert audio.set_volume(delta=500)["new"] == 100
        raises(ToolError, audio.set_volume)


def test_mute_toggle():
    ep = FakeEndpoint(0.2, muted=False)
    with mock.patch.object(audio, "_os_endpoint", lambda: ep):
        assert audio.set_mute()["new"] is True and ep.muted
        r = audio.set_mute(False)
        assert (r["old"], r["new"]) == (True, False)


def test_app_volume():
    ss = [FakeSession("Spotify.exe", 0.8), FakeSession("chrome.exe", 1.0)]
    with mock.patch.object(audio, "_os_sessions", lambda: ss):
        assert {a["app"] for a in audio.list_app_volumes()["apps"]} == {"Spotify", "chrome"}
        r = audio.set_app_volume("spotify", level=40)
        assert (r["app"], r["old"], r["new"]) == ("Spotify", 80, 40) and abs(ss[0].SimpleAudioVolume.v - 0.4) < 1e-9
        assert "Spotify" in str(raises(ToolError, audio.set_app_volume, "vlc", level=3))


def test_default_device_experimental():
    devs = [{"id": "a", "name": "Speakers (Realtek)", "default": True}, {"id": "b", "name": "Headset", "default": False}]
    called = []
    with mock.patch.object(audio, "_os_output_devices", lambda: devs), \
            mock.patch.object(audio, "_os_set_default", called.append):
        r = audio.set_default_output_device("headset")
        assert called == ["b"] and (r["old"], r["new"], r["old_id"]) == ("Speakers (Realtek)", "Headset", "a")
        raises(ToolError, audio.set_default_output_device, "tv")
    assert registry.TOOLS["set_default_output_device"].experimental
    assert not registry.TOOLS["set_default_output_device"].allow


# == display =========================================================================
class FakeDDC:
    def __init__(self, v):
        self.v = v


def _display_mocks(panel=[70], ddcs=None):
    ddcs = ddcs if ddcs is not None else [FakeDDC(40)]

    def pset(v):
        panel[0] = v

    return [mock.patch.object(display, "_os_panel_get", lambda: panel[0]),
            mock.patch.object(display, "_os_panel_set", pset),
            mock.patch.object(display, "_os_ddc_monitors", lambda: ddcs),
            mock.patch.object(display, "_os_ddc_get", lambda m: m.v),
            mock.patch.object(display, "_os_ddc_set", lambda m, v: setattr(m, "v", v))], panel, ddcs


def test_brightness_all_and_one_and_restore():
    ms, panel, ddcs = _display_mocks()
    for m in ms:
        m.start()
    try:
        assert display.get_brightness()["level"] == 55
        r = display.set_brightness(delta=-20)
        assert (r["old"], r["new"]) == (55, 35) and panel[0] == 50 and ddcs[0].v == 20
        r2 = display.set_brightness(level=90, monitor="ddc0")
        assert ddcs[0].v == 90 and panel[0] == 50 and r2["monitors"] == [{"id": "ddc0", "old": 20, "new": 90}]
        display.restore_brightness(r["monitors"])            # undo puts each one back separately
        assert panel[0] == 70 and ddcs[0].v == 40
        raises(ToolError, display.set_brightness, level=5, monitor="ddc9")
    finally:
        for m in ms:
            m.stop()


def test_brightness_without_monitors():
    with mock.patch.object(display, "_os_panel_get", lambda: None), \
            mock.patch.object(display, "_os_ddc_monitors", lambda: []):
        assert display.get_brightness()["level"] is None
        raises(ToolError, display.set_brightness, level=50)


def test_theme_and_restore():
    reg = {"AppsUseLightTheme": 0, "SystemUsesLightTheme": 0}
    sent = []
    with mock.patch.object(display, "_os_reg_get", reg.get), \
            mock.patch.object(display, "_os_reg_set", reg.__setitem__), \
            mock.patch.object(display, "_os_broadcast_theme", lambda: sent.append(1)):
        assert display.get_theme()["mode"] == "dark"
        r = display.set_theme("light")
        assert (r["old"], r["new"], r["old_apps"], r["old_system"]) == ("dark", "light", 0, 0) and sent
        assert display.set_theme("toggle")["new"] == "dark"
        display.set_theme("light", scope="apps")
        assert display.get_theme()["mode"] == "mixed"
        display.restore_theme(0, 0)
        assert reg == {"AppsUseLightTheme": 0, "SystemUsesLightTheme": 0}
        raises(ToolError, display.set_theme, "blue")


def test_wallpaper(tmp_path):
    new = tmp_path / "mountains.png"
    new.write_bytes(b"x")
    old = tmp_path / "flower.jpg"
    old.write_bytes(b"x")
    walls = [{"monitor": 0, "id": "M0", "path": str(old)}, {"monitor": 1, "id": "M1", "path": ""}]
    calls = []

    def setw(mid, path):
        calls.append((mid, path))
        for w in walls:
            if w["id"] == mid:
                w["path"] = path
    with mock.patch.object(display, "_os_wallpapers", lambda: [dict(w) for w in walls]), \
            mock.patch.object(display, "_os_set_wallpaper", setw):
        assert display.get_wallpaper()["path"] == str(old)
        r = display.set_wallpaper(str(new))
        assert r["old"] == str(old) and r["new"] == str(new) and len(calls) == 2
        undo = registry.inverse("mcp__windows__set_wallpaper", {}, r)
        assert undo == [("restore_wallpaper", {"monitors": [{"monitor": 0, "old": str(old), "new": str(new)}]})]
        registry.call(*undo[0])
        assert walls[0]["path"] == str(old) and walls[1]["path"] == str(new)   # monitor 1 had no image to restore
        raises(ToolError, display.set_wallpaper, str(tmp_path / "nope.jpg"))
        txt = tmp_path / "a.txt"
        txt.write_text("x")
        raises(ToolError, display.set_wallpaper, str(txt))
        raises(ToolError, display.set_wallpaper, str(new), monitor=5)


def test_night_light_blob():
    off = bytes(range(18)) + b"\x13" + bytes(10)
    on = display.night_light_blob(off, True)
    assert on[18] == 0x15 and on[23:25] == b"\x10\x00" and len(on) == len(off) + 2 and on[10] == off[10] + 1
    back = display.night_light_blob(on, False)
    assert back[18] == 0x13 and len(back) == len(off)
    assert display.night_light_blob(on, True) == on                  # already on: untouched
    raises(ToolError, display.night_light_blob, b"\x00" * 5, True)
    store = {"b": off}
    with mock.patch.object(display, "_os_night_blob", lambda: store["b"]), \
            mock.patch.object(display, "_os_set_night_blob", lambda d: store.__setitem__("b", d)):
        assert display.get_night_light() == {"on": False}
        assert display.set_night_light(True) == {"old": False, "new": True}
        assert display.get_night_light() == {"on": True}


# == radios ==========================================================================
def test_radios():
    state = {"wifi": "on", "bluetooth": "off"}

    def lst():
        return [{"kind": k, "name": k, "state": v} for k, v in state.items()]

    def setr(kind, on):
        state[kind] = "on" if on else "off"
        return ["allowed"]
    with mock.patch.object(radios, "_os_list", lst), mock.patch.object(radios, "_os_set", setr), \
            mock.patch.object(radios, "_os_airplane", lambda: False):
        assert radios.get_radios()["airplane_mode"] is False
        assert radios.set_bluetooth(True) == {"old": False, "new": True, "radio": "bluetooth"}
        assert radios.set_wifi()["new"] is False and state["wifi"] == "off"        # toggle
    with mock.patch.object(radios, "_os_list", lambda: [{"kind": "wifi", "name": "w", "state": "on"}]), \
            mock.patch.object(radios, "_os_set", lambda k, on: ["denied_by_user"]):
        assert "refused" in str(raises(ToolError, radios.set_wifi, False))
        raises(ToolError, radios.set_bluetooth, True)                                # no such radio


# == power ===========================================================================
def test_power_mode_and_battery():
    cur = {"g": power.MODES["balanced"]}
    with mock.patch.object(power, "_os_get_overlay", lambda: cur["g"]), \
            mock.patch.object(power, "_os_set_overlay", lambda g: cur.__setitem__("g", g)):
        assert power.get_power_mode() == {"mode": "balanced"}
        assert power.set_power_mode("best performance") == {"old": "balanced", "new": "best_performance"}
        assert power.set_power_mode("efficiency")["new"] == "best_efficiency"
        raises(ToolError, power.set_power_mode, "turbo")
    with mock.patch.object(power, "_os_battery", lambda: {"has_battery": True, "percent": 92}):
        assert power.battery_status()["percent"] == 92


def test_sleep_restart_shutdown_only_ask():
    done = []
    with mock.patch.object(power, "_os_power_action", done.append), mock.patch.object(power, "_os_lock", lambda: None):
        for a in power.POWER_ACTIONS:
            r = power.request_power_action(a)
            assert r["pending"] == a and r["needs_click"]
        raises(ToolError, power.request_power_action, "hibernate")
        assert done == []                                   # asking never does it
        assert power.lock_pc() == {"locked": True}
        power.perform_power_action("sleep")                 # what the island's Confirm click calls
        assert done == ["sleep"]
    assert registry.TOOLS["request_power_action"].kind == "ask"
    assert "perform_power_action" not in registry.TOOLS and "perform_power_action" not in registry.INTERNAL


# == media ===========================================================================
def test_media_control_next_and_inverse():
    tracks = iter([{"title": "A", "artist": "x", "status": "playing"}] * 2 + [{"title": "B", "artist": "y", "status": "playing"}] * 9)
    sent = []
    with mock.patch.object(media, "_os_now_playing", lambda art=False: next(tracks)), \
            mock.patch.object(media, "_os_control", lambda a: sent.append(a) or True), \
            mock.patch.object(media.time, "sleep", lambda s: None):
        r = media.media_control("skip")
        assert sent == ["next"] and r["old"]["title"] == "A" and r["new"]["title"] == "B" and r["action"] == "next"
    assert registry.inverse("mcp__windows__media_control", {}, r) == [("media_control", {"action": "previous"})]
    with mock.patch.object(media, "_os_now_playing", lambda art=False: {"status": "none"}):
        raises(ToolError, media.media_control, "play")
        assert media.now_playing() == {"status": "none"}
    raises(ToolError, media.media_control, "rewind")


# == apps and windows ================================================================
START = [{"name": "Notepad", "id": "Microsoft.WindowsNotepad_8wekyb3d8bbwe!App"},
         {"name": "Spotify", "id": "SpotifyAB.SpotifyMusic!Spotify"},
         {"name": "Windows PowerShell", "id": "{1AC14E77}\\WindowsPowerShell\\v1.0\\powershell.exe"},
         {"name": "Registry Editor", "id": "{F38BF404}\\regedit.exe"},
         {"name": "Calculator", "id": "Microsoft.WindowsCalculator!App"}]


def test_launch_app_and_blocklist():
    launched = []
    with mock.patch.object(apps, "_os_start_apps", lambda: START), mock.patch.object(apps, "_os_launch", launched.append):
        assert apps.launch_app("spotify") == {"launched": "Spotify"}
        assert apps.launch_app("calculater") == {"launched": "Calculator"}     # close enough
        for bad in ("powershell", "registry editor", "cmd", "windows security", "regedit"):
            raises(ToolError, apps.launch_app, bad)
        raises(ToolError, apps.launch_app, "photoshop")
        assert launched == [START[1]["id"], START[4]["id"]]
        assert apps.list_apps("note")["apps"] == ["Notepad"]


def test_windows_snap_minimize_and_undo():
    wins = [{"hwnd": 11, "title": "notes.txt - Notepad", "app": "Notepad", "pid": 1, "minimized": False,
             "foreground": True},
            {"hwnd": 22, "title": "Inbox - Chrome", "app": "chrome", "pid": 2, "minimized": False, "foreground": False}]
    pl = [0, 1, [-1, -1], [-1, -1], [10, 10, 500, 400]]
    log = []
    with mock.patch.object(apps, "_os_windows", lambda: wins), \
            mock.patch.object(apps, "_os_placement", lambda h: pl), \
            mock.patch.object(apps, "_os_set_placement", lambda h, p: log.append(("place", h, p))), \
            mock.patch.object(apps, "_os_show", lambda h, c: log.append(("show", h, c))), \
            mock.patch.object(apps, "_os_work_area", lambda h: (0, 0, 1920, 1040)), \
            mock.patch.object(apps, "_os_move", lambda *a: log.append(("move",) + a)), \
            mock.patch.object(apps, "_os_focus", lambda h: True), \
            mock.patch.object(apps, "_os_close", lambda h: log.append(("close", h))):
        r = apps.snap_window("right")
        assert log[-1] == ("move", 11, 960, 0, 960, 1040) and r["new"] == "right" and r["placement"] == pl
        r = apps.minimize_window("chrome")
        assert log[-1] == ("show", 22, apps.SW_MINIMIZE)
        undo = registry.inverse("mcp__windows__minimize_window", {}, r)
        registry.call(*undo[0])
        assert log[-1] == ("place", 22, pl)
        assert apps.focus_window("chrome")["new"] == "Inbox - Chrome"
        raises(ToolError, apps.snap_window, "up")
        raises(ToolError, apps.focus_window, "photoshop")
        raises(ToolError, apps.close_window, " ")
        assert apps.close_window("notepad") == {"closed": "notes.txt - Notepad"}
    assert not registry.TOOLS["close_window"].allow


# == clipboard, settings pages, screenshot ===========================================
def test_clipboard():
    box = {"t": "old text"}
    with mock.patch.object(clipboard, "_os_get", lambda: box["t"]), \
            mock.patch.object(clipboard, "_os_set", lambda t: box.__setitem__("t", t)):
        assert clipboard.get_clipboard() == {"text": "old text", "chars": 8}
        r = clipboard.set_clipboard("new")
        assert r == {"old": "old text", "new": "new"}
        assert registry.inverse("mcp__windows__set_clipboard", {}, r) == [("set_clipboard", {"text": "old text"})]
        box["t"] = "x" * (clipboard.MAX_UNDO + 1)
        r = clipboard.set_clipboard("y")
        assert r["undoable"] is False and r["old"] is None
        assert registry.inverse("mcp__windows__set_clipboard", {}, r) is None


def test_settings_pages():
    opened = []
    with mock.patch.object(screen, "_os_open", opened.append):
        assert screen.open_settings_page("Wi-Fi") == {"opened": "ms-settings:network-wifi"}
        assert screen.open_settings_page("night light")["opened"] == "ms-settings:nightlight"
        assert screen.open_settings_page("ms-settings:display")["opened"] == "ms-settings:display"
        for bad in ("windowsdefender", "ms-settings:otherusers", "yourinfo", "signinoptions", "x; del", "../a"):
            raises(ToolError, screen.open_settings_page, bad)
    assert opened == ["ms-settings:network-wifi", "ms-settings:nightlight", "ms-settings:display"]


def test_screenshot_encode():
    w, h = 64, 32
    data = bytes([30, 60, 90, 255]) * (w * h)
    jpg = screen.encode(data, w, h, max_width=32)
    assert jpg[:2] == b"\xff\xd8"
    with mock.patch.object(screen, "_os_capture", lambda m: (data, w, h)):
        r = registry.call("screenshot", {"monitor": 0})
        assert r["width"] == 64 and r["image_jpeg"][:2] == b"\xff\xd8"


# == registry, never-exposed, server ===================================================
def test_registry_shape():
    assert registry.never_exposed_violations() == []
    for t in registry.TOOLS.values():
        assert t.kind in ("read", "write", "ask"), t.name
        assert (t.func.__doc__ or "").strip(), f"{t.name} has no description"
        if t.kind == "read":
            assert t.undo is None
    allow = registry.allow_list()
    assert all(n.startswith("mcp__windows__") for n in allow)
    for off in ("close_window", "set_default_output_device", "set_night_light"):
        assert f"mcp__windows__{off}" not in allow
    for on in ("get_volume", "set_volume", "set_theme", "set_brightness", "media_control", "set_wallpaper"):
        assert f"mcp__windows__{on}" in allow
    assert registry.short_name("mcp__windows__set_volume") == "set_volume"
    assert registry.get("mcp__telegram__send") is None
    raises(KeyError, registry.call, "format_disk")


def test_never_exposed_words_nowhere():
    import re
    bad = re.compile(r"defender|firewall|\buac\b|user account|regedit|powershell|driver|services?\b", re.I)
    for t in registry.TOOLS.values():
        assert not bad.search(t.name), t.name


def test_server_registers_every_tool():
    try:
        from heyclaude.mcp_windows import server
        s = server.build()
    except ImportError as e:                    # mcp not installed: the registry tests cover the rest
        print("  (skipped: mcp SDK missing:", e, ")")
        return
    import asyncio
    names = {t.name for t in asyncio.run(s.list_tools())}
    assert names == set(registry.TOOLS)
    for internal in registry.INTERNAL:
        assert internal not in names
    with mock.patch.object(audio, "_os_endpoint", lambda: FakeEndpoint(0.5)):
        res = asyncio.run(s.call_tool("set_volume", {"level": 20}))
    data = getattr(res, "structured_content", None) or getattr(res, "structuredContent", None)
    if data is None:
        texts = [c.text for c in (res.content if hasattr(res, "content") else res) if getattr(c, "text", None)]
        data = E.parse_result([{"type": "text", "text": texts[0]}])
    data = data.get("result", data)
    assert data["old"] == 50 and data["new"] == 20


def test_every_write_tool_returns_old_and_new_or_says_why():
    """Write tools return {old, new} (the undo/effect contract); the few that
    have no 'old' (launch, lock, close, open a page) have no undo either."""
    no_change_shape = {"launch_app", "lock_pc", "close_window", "open_settings_page", "restore_window"}
    for t in registry.TOOLS.values():
        if t.kind == "write" and t.name not in no_change_shape:
            assert t.undo is not None or t.name in ("focus_window",), f"{t.name}: write tool without undo"
        if t.name in no_change_shape:
            assert t.undo is None


# == effects =========================================================================
def test_effects_mapping():
    ev = E.build("mcp__windows__set_volume", {"level": 30}, {"old": 45, "new": 30}, undoable=True)
    assert (ev["effect"], ev["from"], ev["to"], ev["text_from"], ev["text_to"], ev["undo"]) == \
        ("volume", 45, 30, "45%", "30%", True)
    assert ev["label"] == "Volume" and ev["ttl_s"] == 8 and len(ev["id"]) == 12
    assert E.build("mcp__windows__get_volume", {}, {"level": 3}) is None             # silent read
    th = E.build("mcp__windows__set_theme", {}, {"old": "dark", "new": "light"})
    assert th["effect"] == "theme" and th["text_to"] == "light"
    b = E.build("mcp__windows__set_brightness", {}, {"old": 95, "new": 75, "monitors": []})
    assert b["effect"] == "brightness"
    m = E.build("mcp__windows__media_control", {}, {"old": {"title": "A"}, "new": {"title": "B", "artist": "C"},
                                                       "action": "next"})
    assert m["effect"] == "media" and m["extra"]["title"] == "B" and m["extra"]["action"] == "next"
    assert m["label"] == "Next"
    w = E.build("mcp__windows__set_wallpaper", {}, {"old": "C:/a/flower.jpg", "new": "D:/b/hill.png"})
    assert (w["text_from"], w["text_to"]) == ("flower.jpg", "hill.png")
    assert E.build("mcp__windows__screenshot", {}, None)["effect"] == "flash"
    assert E.build("mcp__windows-mcp__Screenshot", {}, None)["effect"] == "flash"
    c = E.build("mcp__windows__request_power_action", {}, {"pending": "restart", "needs_click": True})
    assert c["effect"] == "confirm" and c["extra"]["action"] == "restart" and c["ttl_s"] == 20
    # fallback: an unlisted tool that returns {old, new}
    f = E.build("mcp__other__set_fan", {}, {"old": 1, "new": 2})
    assert f["effect"] == "generic" and f["text_from"] == "1" and f["label"] == "Set fan"
    assert E.build("mcp__claude_ai_Gmail__search_threads", {}, {"threads": []}) is None
    assert E.build("mcp__other__x", {}, "text") is None


def test_effects_json_matches_the_tools():
    m = E.load()
    for name in list(m["tools"]) + list(m["silent"]):
        if name.startswith("mcp__windows-mcp__"):
            assert name.split("__")[-1] in B.WINDOWS_MCP_READ_ONLY, name
        else:
            assert registry.get(name) is not None, f"effects.json names an unknown tool {name}"
    for t in registry.TOOLS.values():
        if t.kind == "read" and t.name not in ("screenshot", "now_playing"):
            assert t.full_name in m["silent"], f"{t.name} should be silent"
        else:
            assert t.full_name in m["tools"], f"{t.name} has no effect"
        spec = m["tools"].get(t.full_name)
        if spec:
            assert spec["effect"] in E.KINDS


def test_parse_result_shapes():
    assert E.parse_result('{"old": 1, "new": 2}') == {"old": 1, "new": 2}
    assert E.parse_result([{"type": "text", "text": '{"result": {"old": 1, "new": 2}}'}]) == {"old": 1, "new": 2}
    assert E.parse_result([{"type": "image", "data": "..."}]) is None
    assert E.parse_result("Error: nope") is None
    assert E.parse_result({"old": 1}) == {"old": 1}


def test_reversed_effect():
    ev = E.build("mcp__windows__set_volume", {}, {"old": 45, "new": 30}, undoable=True)
    r = E.reversed_event(ev)
    assert (r["from"], r["to"], r["text_to"], r["reverse"], r["undo"]) == (30, 45, "45%", True, False)
    assert r["id"] != ev["id"] and r["label"].startswith("Undone")
    m = E.build("mcp__windows__media_control", {}, {"old": {"title": "A"}, "new": {"title": "B"}, "action": "next"})
    rm = E.reversed_event(m, {"old": {"title": "B"}, "new": {"title": "A", "artist": "Z"}, "action": "previous"})
    assert rm["extra"]["title"] == "A" and rm["extra"]["artist"] == "Z"


# == undo ============================================================================
def test_undo_stack():
    ran = []
    st = UndoStack(runner=lambda n, a: ran.append((n, a)) or {"old": 30, "new": a.get("level")})
    ev = E.build("mcp__windows__set_volume", {}, {"old": 45, "new": 30}, undoable=True)
    assert st.record("mcp__windows__set_volume", {"level": 30}, {"old": 45, "new": 30}, effect=ev)
    assert st.record("mcp__windows__get_volume", {}, {"level": 3}) is None             # reads aren't changes
    assert st.record("mcp__windows__launch_app", {}, {"launched": "x"}) is None
    ch, res = st.undo()
    assert ran == [("set_volume", {"level": 45})] and describe(ch) == "Volume back to 45%."
    assert st.undo() is None                                                          # nothing left
    # undo by the island's effect id, the newest first otherwise
    e1 = E.build("mcp__windows__set_mute", {}, {"old": False, "new": True}, undoable=True)
    e2 = E.build("mcp__windows__set_theme", {}, {"old": "dark", "new": "light", "old_apps": 0, "old_system": 0},
                 undoable=True)
    st.record("mcp__windows__set_mute", {}, {"old": False, "new": True}, effect=e1)
    st.record("mcp__windows__set_theme", {}, {"old": "dark", "new": "light", "old_apps": 0, "old_system": 0}, effect=e2)
    ran.clear()
    st.undo(e1["id"])
    assert ran == [("set_mute", {"muted": False})]
    ch, _ = st.undo()
    assert ran[-1] == ("restore_theme", {"apps": 0, "system": 0}) and describe(ch) == "Colour mode back to dark."


def test_undo_volume_restores_mute_too():
    calls = registry.inverse("mcp__windows__set_volume", {}, {"old": 45, "new": 30, "old_muted": True, "muted": False})
    assert calls == [("set_volume", {"level": 45}), ("set_mute", {"muted": True})]


def test_undo_failure_keeps_the_change_and_old_changes_expire():
    def boom(n, a):
        raise OSError("device gone")
    st = UndoStack(runner=boom)
    st.record("mcp__windows__set_mute", {}, {"old": False, "new": True})
    raises(OSError, st.undo)
    assert len(st.items) == 1                                   # still there to retry
    st.items[0].at = time.time() - 10_000
    assert st.last() is None and st.pop() is None               # too old for "undo that"


def test_undo_route():
    P = B.Projects({"hey claude": "x"})
    for t in ("undo that", "Undo.", "okay, undo that please", "put it back", "revert it", "undo the last change"):
        assert B.local_route(t, P) == ("undo",), t
    for t in ("volume 30", "dark mode", "next song", "brightness down", "change my wallpaper to C:\\a.jpg",
              "what's playing", "open bluetooth settings", "lock my pc", "take a screenshot", "mute"):
        assert B.local_route(t, P) == ("control", t), t
    assert B.local_route("how many legs does a spider have", P) is None
    assert B.local_route("what's the weather", P)[0] == "lookup"
    assert B.local_route("stop the task", P)[0] == "task_stop"


# == agent wiring: allow-list gating, MCP config, tool events ========================
def test_gate_allowed_tools():
    kept, dropped = B.gate_allowed_tools([
        "Read", "mcp__windows__set_volume", "mcp__windows-mcp__Snapshot", "mcp__windows-mcp__Screenshot",
        "mcp__windows-mcp__Click", "mcp__windows-mcp__Type", "mcp__windows-mcp__Shortcut", "mcp__windows-mcp__App",
        "mcp__windows-mcp__Drag", "mcp__windows-mcp", "mcp__windows-mcp__PowerShell", "mcp__windows-mcp__Registry",
        "Bash(powershell:*)"])
    assert kept == ["Read", "mcp__windows__set_volume", "mcp__windows-mcp__Snapshot", "mcp__windows-mcp__Screenshot"]
    assert "mcp__windows-mcp__Click" in dropped and "mcp__windows-mcp" in dropped and len(dropped) == 9


def test_config_example_allow_list():
    import yaml
    cfg = yaml.safe_load(open(os.path.join(ROOT, "config.example.yaml"), encoding="utf-8"))
    a = cfg["agent"]
    allowed = a["allowed_tools"]
    for name in registry.allow_list():
        assert name in allowed, f"{name} missing from config.example.yaml"
    for t in registry.TOOLS.values():
        if not t.allow:
            assert t.full_name not in allowed, f"{t.name} must stay off the allow-list"
    wm = [x for x in allowed if x.startswith("mcp__windows-mcp")]
    assert sorted(wm) == ["mcp__windows-mcp__Screenshot", "mcp__windows-mcp__Snapshot"]
    assert B.gate_allowed_tools(allowed)[1] == []               # nothing in the example gets dropped
    assert a["windows_settings"] is True and a["windows_mcp"]["enabled"] is False
    assert {"PowerShell", "Registry"} <= set(a["windows_mcp"]["exclude_tools"])


def test_windows_mcp_servers_config():
    s = B.windows_mcp_servers({"windows_settings": True})
    assert s["windows"]["args"] == ["-m", "heyclaude.mcp_windows"] and "windows-mcp" not in s
    assert not s["windows"]["command"].lower().endswith("pythonw.exe")
    with mock.patch.object(B, "find_windows_mcp", lambda c=None: r"C:\u\.local\bin\windows-mcp.exe"):
        s = B.windows_mcp_servers({"windows_settings": False, "windows_mcp": {"enabled": True, "exclude_tools": ["Process"]}})
    assert "windows" not in s
    args = s["windows-mcp"]["args"]
    assert args[:3] == ["serve", "--transport", "stdio"] and args[3] == "--exclude-tools"
    assert args[4].split(",")[:2] == ["PowerShell", "Registry"] and "Process" in args[4]
    assert s["windows-mcp"]["env"]["ANONYMIZED_TELEMETRY"] == "false"
    with mock.patch.object(B, "find_windows_mcp", lambda c=None: None):
        assert "windows-mcp" not in B.windows_mcp_servers({"windows_mcp": {"enabled": True}})


def test_agent_args_have_windows_server_and_gated_tools(tmp_path):
    from heyclaude import config
    cfg = config.defaults()
    cfg["agent"]["allowed_tools"] = ["Read", "mcp__windows__set_volume", "mcp__windows-mcp__Click"]
    a = B.Agent.__new__(B.Agent)
    a.cfg = cfg
    with mock.patch.object(config, "STATE_DIR", tmp_path):
        args = a._args()
    allowed = args[args.index("--allowedTools") + 1]
    assert allowed == "Read,mcp__windows__set_volume,mcp__weather__get_weather"   # M5: the keyless weather tool
    mcp_files = args[args.index("--mcp-config") + 1:]
    data = json.load(open(mcp_files[-1], encoding="utf-8"))
    assert "windows" in data["mcpServers"] and "weather" in data["mcpServers"]
    assert "mcp__windows tools" in args[args.index("--append-system-prompt") + 1]


def test_agent_tool_events_reach_the_callback():
    lines = [
        json.dumps({"type": "system", "subtype": "init"}),
        json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "mcp__windows__set_volume", "input": {"level": 30}}]}}),
        json.dumps({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": [{"type": "text", "text": '{"old": 45, "new": 30}'}]}]}}),
        json.dumps({"type": "result", "result": "Volume 30."}),
    ]
    chat = B._CliChat.__new__(B._CliChat)
    chat.final_only, chat.timeout, chat.name, chat.init_info = True, 5, "agent", None
    import queue
    chat.lines = queue.Queue()
    for ln in lines:
        chat.lines.put(ln + "\n")
    agent = B.Agent.__new__(B.Agent)
    agent._pending_tools = {}
    got = []
    agent.on_tool_result = lambda *a, **k: got.append(a + (k.get("raw"),))
    chat.on_tool = agent._tool_event
    assert "".join(chat._read_turn()) == "Volume 30."
    assert got == [("mcp__windows__set_volume", {"level": 30}, {"old": 45, "new": 30}, False,
                    '{"old": 45, "new": 30}')]


def test_assistant_effect_and_undo_flow():
    """The agent's tool result -> effect event + undo entry; "undo that" runs the inverse locally."""
    from heyclaude.assistant import Assistant

    class Sig:
        def __init__(self):
            self.got = []

        def emit(self, *a):
            self.got.append(a)

    class UI:
        effect, effect_art, state = Sig(), Sig(), Sig()

    asst = Assistant.__new__(Assistant)
    asst.ui = UI()
    asst._pending_power = {}
    ran = []
    asst.undo_stack = UndoStack(runner=lambda n, a: ran.append((n, a)) or {"old": 30, "new": 45})
    asst.brain = type("Br", (), {"convo": None})()
    asst._on_tool_result("mcp__windows__set_volume", {"level": 30}, {"old": 45, "new": 30}, False)
    ev = UI.effect.got[-1][0]
    assert ev["effect"] == "volume" and ev["undo"] is True
    asst._on_tool_result("mcp__windows__set_volume", {"level": 30}, "error text", True)   # errors: nothing
    assert len(UI.effect.got) == 1
    assert asst.undo() == "Volume back to 45%."
    assert ran == [("set_volume", {"level": 45})]
    back = UI.effect.got[-1][0]
    assert back["reverse"] and back["to"] == 45
    assert asst.undo() == "There's nothing to undo."
    # power: the request shows Confirm; only confirm_clicked acts, and only once
    acted = []
    asst._on_tool_result("mcp__windows__request_power_action", {"action": "sleep"},
                         {"pending": "sleep", "needs_click": True}, False)
    c = UI.effect.got[-1][0]
    assert c["effect"] == "confirm" and c["undo"] is False
    with mock.patch.object(power, "_os_power_action", acted.append):
        asst.cancel_clicked("other")
        asst.confirm_clicked(c["id"])
        time.sleep(0.2)
        asst.confirm_clicked(c["id"])
        time.sleep(0.1)
    assert acted == ["sleep"]


# == island ==========================================================================
def _app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_effect_player_frame_budget():
    _app()
    from heyclaude.island_effects import ENTRY_DELAY, ENTRY_S, EffectPlayer
    fx = EffectPlayer()
    assert not fx.needs_frame(0.0) and not fx.visible()
    fx.show(E.build("mcp__windows__set_volume", {}, {"old": 45, "new": 30}, undoable=True), now=100.0)
    assert fx.needs_frame(100.05) and fx.needs_frame(100.06)          # entry: every tick
    t = 100.0 + ENTRY_DELAY + ENTRY_S + 0.2
    frames = sum(fx.needs_frame(t + i * 0.033) for i in range(60))       # 2 s of ticks during the hold
    assert 6 <= frames <= 10, frames                                    # ~4 fps for the countdown only
    assert fx.visible(107.9) and fx.expired(108.1)
    assert abs(fx.progress(100 + ENTRY_DELAY + ENTRY_S) - 1.0) < 1e-6
    # growing values overshoot a little (inhale), shrinking ones never do (exhale)
    grow = E.build("mcp__windows__set_volume", {}, {"old": 20, "new": 70})
    fx.show(grow, now=0.0)
    assert max(fx.progress(ENTRY_DELAY + ENTRY_S * k / 50) for k in range(51)) > 1.01
    fx.show(E.build("mcp__windows__set_volume", {}, {"old": 70, "new": 20}), now=0.0)
    assert max(fx.progress(ENTRY_DELAY + ENTRY_S * k / 50) for k in range(51)) <= 1.0 + 1e-9


def test_island_effect_row_and_buttons():
    _app()
    from PySide6.QtCore import QPointF
    from heyclaude.island import Bridge, Island
    from heyclaude.island_effects import EFFECT_H
    b = Bridge()
    isl = Island(b, {"screen": "primary", "top_margin": 8})
    isl.set_state("idle", "")
    h0 = isl._target_size()[1]
    clicks = []
    isl.effect_undo.connect(lambda i: clicks.append(("undo", i)))
    isl.effect_confirm.connect(lambda i: clicks.append(("confirm", i)))
    ev = E.build("mcp__windows__set_volume", {}, {"old": 45, "new": 30}, undoable=True)
    b.effect.emit(ev)
    _app().processEvents()
    assert isl.fx.ev["id"] == ev["id"] and isl._target_size()[1] == h0 + EFFECT_H
    isl._place(immediate=True)
    img = isl.grab()                                         # paints without errors
    assert not img.isNull()
    rect = isl._effect_rect()
    undo_btn = isl.fx.buttons(rect)["undo"]
    assert isl.fx.hit(rect, undo_btn.center()) == "undo" and isl.fx.hit(rect, QPointF(5, rect.top() + 2)) is None

    class Click:
        def __init__(self, pos):
            self._p = pos

        def position(self):
            return self._p

        def button(self):
            from PySide6.QtCore import Qt
            return Qt.LeftButton
    isl.mousePressEvent(Click(undo_btn.center()))
    assert clicks == [("undo", ev["id"])] and isl.fx.ev is None
    # every kind paints
    for tool, res in (("mcp__windows__set_theme", {"old": "dark", "new": "light"}),
                      ("mcp__windows__set_brightness", {"old": 90, "new": 70, "monitors": []}),
                      ("mcp__windows__set_mute", {"old": False, "new": True}),
                      ("mcp__windows__media_control", {"old": {"title": "A"}, "new": {"title": "B"}, "action": "next"}),
                      ("mcp__windows__screenshot", None),
                      ("mcp__windows__request_power_action", {"pending": "sleep"}),
                      ("mcp__windows__set_wallpaper", {"old": "a.jpg", "new": "b.png"})):
        e = E.build(tool, {}, res, undoable=True)
        isl.show_effect(e)
        isl.fx.t0 -= 0.3
        isl._place(immediate=True)
        assert not isl.grab().isNull()
    conf = isl.fx.buttons(isl._effect_rect())
    assert set(conf) == {"undo"}                             # wallpaper (last) has Undo
    isl.show_effect(E.build("mcp__windows__request_power_action", {}, {"pending": "sleep"}))
    btns = isl.fx.buttons(isl._effect_rect())
    assert set(btns) == {"confirm", "cancel"} and not btns["confirm"].intersects(btns["cancel"])
    # an art blob for another effect is ignored; a stale effect clears itself on the tick
    isl._effect_art("nope", b"123")
    assert isl.fx.art is None
    isl.fx.t0 -= 100
    isl._tick()
    assert isl.fx.ev is None
    isl.close()


if __name__ == "__main__":
    import pathlib
    import tempfile
    n = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            if "tmp_path" in fn.__code__.co_varnames[: fn.__code__.co_argcount]:
                fn(pathlib.Path(tempfile.mkdtemp()))
            else:
                fn()
            n += 1
            print("ok", name)
    print(n, "passed")
