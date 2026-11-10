"""Offline tests for the Settings design system (heyclaude/settings_theme.py, settings_kit.py):
the derived palette stays readable whatever the base colours, the switch, the sidebar and
page stack, rows lining up, a row's details, reduced motion, and the whole window keeping
every value it shows (nothing changes on an untouched save).

    .venv\\Scripts\\python tests\\test_settings_look.py
    .venv\\Scripts\\python -m pytest tests\\test_settings_look.py
"""
import faulthandler
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
faulthandler.enable()
sys.stdout.reconfigure(line_buffering=True)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PySide6.QtWidgets import QApplication, QComboBox, QLineEdit, QWidget  # noqa: E402

APP = QApplication.instance() or QApplication([])

from heyclaude import config  # noqa: E402
from heyclaude import settings_kit as kit  # noqa: E402
from heyclaude import settings_theme as th  # noqa: E402

PALETTES = {
    "island": {},
    "light": {"page": "#F4F1EE", "fg": "#1A1714", "dim": "#8A8580", "faint": "#C9C4BF", "live": "#C4613F"},
    "murky": {"page": "#202020", "fg": "#B0B0B0", "dim": "#3A3A3A", "faint": "#2C2C2C", "ok": "#1E4024"},
}


def test_palette_stays_readable():
    """Every derived text colour clears its contrast bar on a card, for the island's palette,
    a light one and a deliberately muddy one; the text on the ember reads too."""
    for name, pal in PALETTES.items():
        t = th.tokens(pal)
        assert th.contrast(t["fg"], t["card"]) >= 7.0, name
        assert th.contrast(t["dim"], t["card"]) >= 4.5, name
        for k in ("ok", "bad", "warn", "accent_text"):
            assert th.contrast(t[k], t["card"]) >= 4.5, (name, k)
        assert th.contrast(t["on_accent"], t["accent"]) >= 3.0, name
        # surfaces step away from the page in the same direction as the text (light or dark)
        assert (th.luminance(t["card"]) > th.luminance(t["page"])) == (th.luminance(t["fg"]) > th.luminance(t["page"]))
        css = th.stylesheet(t)
        assert t["card"] in css and t["accent"] in css            # the stylesheet is built from the tokens
    t = th.tokens({})
    assert t["accent"] == th.BASE["live"] and t["page"] == th.BASE["page"]      # the island's own colours


def test_theme_json_palette_overrides():
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "x").mkdir()
        (Path(d) / "x" / "theme.json").write_text('{"palette": {"live": "#3A7BD5", "nope": "#000000", '
                                                  '"fg": "not a colour"}}', encoding="utf-8")
        old = th.THEME_DIR
        th.THEME_DIR = Path(d)
        try:
            pal = th.load_palette("x")
        finally:
            th.THEME_DIR = old
    assert pal["live"] == "#3A7BD5" and pal["fg"] == th.BASE["fg"] and "nope" not in pal


def test_switch():
    th._MOTION["on"] = False
    sw = kit.Switch()
    seen = []
    sw.toggled.connect(seen.append)
    assert not sw.isChecked() and sw._pos == 0.0
    sw.setChecked(True)
    assert sw.isChecked() and sw._pos == 1.0 and seen == [True]      # no motion: the knob is there at once
    sw.click()
    assert not sw.isChecked() and seen == [True, False]
    assert sw.sizeHint().width() >= sw.W and sw.grab().width() > 0


def test_motion_follows_island_setting():
    assert th.set_motion("off") is False and not th.motion_on()
    assert th.set_motion("calm") is False
    assert th.set_motion("full") is True and th.motion_on()
    th._MOTION["on"] = False


def test_sidebar_and_pages():
    sb = kit.Sidebar()
    st = kit.PageStack(sb)
    for i, (title, sec) in enumerate((("A", ""), ("B", None), ("C", "Group"))):
        assert st.addTab(kit.Page(title, "sub"), title, "general", sec) == i
    assert st.count() == 3 and [st.tabText(i) for i in range(3)] == ["A", "B", "C"]
    assert sb.buttons[0].isChecked()
    sb.buttons[2].click()
    assert st.currentIndex() == 2 and sb.buttons[2].isChecked()
    st.setCurrentIndex(1)
    assert sb.buttons[1].isChecked() and not sb.buttons[2].isChecked()


def test_rows_line_up_and_details():
    page = kit.Page("P")
    g = page.add_group("G", "one line")
    rows = [kit.SettingRow("Short", "", (QLineEdit(),)), kit.SettingRow("A much longer title here", "desc",
                                                                         (QComboBox(),))]
    for r in rows:
        g.add(r)
        page.register(r)
    sw_row = kit.SettingRow("Toggle", "", (kit.Switch(),), kind="trailing")
    g.add(sw_row)
    more = QWidget()
    sw_row.set_details(more)
    page.resize(900, 600)
    page.show()
    APP.processEvents()
    xs = {r.holder.mapTo(page, r.holder.rect().topLeft()).x() for r in rows}
    ws = {r.holder.width() for r in rows}
    assert len(xs) == 1 and len(ws) == 1, (xs, ws)          # one control column per page
    assert 200 <= ws.pop() <= 300
    assert not more.isVisible()
    sw_row._chev.click()
    assert more.isVisible()
    page.hide()


def test_window_keeps_every_value():
    """The restyled window: the sections in the sidebar, switches for booleans, and an
    untouched collect gives back exactly the config it was filled from."""
    from heyclaude.settings_ui import SettingsWindow
    cfg = config.defaults()
    with tempfile.TemporaryDirectory() as d:
        w = SettingsWindow(cfg, config_path=Path(d) / "config.yaml", state_dir=d)
        names = [w.tabs.tabText(i) for i in range(w.tabs.count())]
        for want in ("General", "Island", "Character", "Wake word", "Voice", "Dictation", "Instant", "Claude",
                     "Projects", "Permissions", "Sessions"):
            assert want in names, names
        for attr in ("cb_listening", "cb_autostart", "cb_hide_idle", "cb_scale_auto", "cb_instant", "cb_agent",
                     "cb_dictate", "cb_lean", "cb_premiere", "cb_save_conv"):
            assert isinstance(getattr(w, attr), kit.Switch), attr
        new, errors, _ = w._collect()
        assert not errors and not config.diff(cfg, new), config.diff(cfg, new)
        for v in ("full", "calm", "off", "auto"):                  # motion labels changed, values didn't
            w._set_combo(w.cmb_motion, v)
            assert w._collect()[0]["island"]["motion"] == v
        w.ed_hotkey.setText("ctrl+nonsense+q")
        assert w.ed_hotkey.property("error") is True and w.lb_hotkey.property("tone") == "bad"
        w.ed_hotkey.setText("ctrl+alt+space")
        assert not w.ed_hotkey.property("error")
        w._set_combo(w.cmb_perm, "bypassPermissions")
        assert not w._bypass_row.isHidden()
        w._set_combo(w.cmb_perm, "acceptEdits")
        assert w._bypass_row.isHidden()
        w._set_status("Saved.", "#6CCB7A")                         # old hex colours still map to a tone
        assert th.T["ok"] in w.status.text()
        w.has_unsaved_changes = lambda: False
        w.close()
        w.deleteLater()


def _window(cfg, d, reg=None):
    from heyclaude import plugins
    from heyclaude.settings_ui import SettingsWindow
    plugins.reset(reg)
    w = SettingsWindow(cfg, config_path=Path(d) / "config.yaml", state_dir=d)
    w.has_unsaved_changes = lambda: False      # tests close without the dialog
    return w


def test_frame_rate_round_trip():
    """island.fps: the four choices with their labels, an untouched value (even one only the
    engine knows, like 90) written back exactly, a picked one saved as its value."""
    from heyclaude.island_qml import FPS_CHOICES
    with tempfile.TemporaryDirectory() as d:
        cfg = config.defaults()
        assert cfg["island"]["fps"] == "auto"
        w = _window(cfg, d)
        data = [w.cmb_fps.itemData(i) for i in range(w.cmb_fps.count())]
        assert data == list(FPS_CHOICES)
        assert [w.cmb_fps.itemText(i) for i in range(4)] == ["Auto", "30 fps", "60 fps", "Every refresh"]
        assert "whole fraction" in w.row_fps.desc_label.text() and not w.row_fps.tag      # live, no restart tag
        assert not config.diff(cfg, w._collect()[0])
        w._set_combo(w.cmb_fps, "native")
        new = w._collect()[0]
        assert new["island"]["fps"] == "native" and config.diff(cfg, new) == ["island.fps"]
        w.live_fps = lambda: 72.0
        w._fps_now()
        assert "Now 72 fps." in w.row_fps.desc_label.text()
        w.close()
        for raw in (90, "45fps", 60):                              # by hand in config.yaml
            cfg = config.defaults()
            cfg["island"]["fps"] = raw
            w = _window(cfg, d)
            new, errors, _ = w._collect()
            assert not errors and new["island"]["fps"] == raw and type(new["island"]["fps"]) is type(raw)
            assert not config.diff(cfg, new)
            if raw == 90:
                assert w.cmb_fps.currentText() == "90 fps"
            w._set_combo(w.cmb_fps, "auto")
            assert w._collect()[0]["island"]["fps"] == "auto"
            w.close()


def _demo_registry(cfg, d):
    import json
    from heyclaude import plugins
    user = Path(d) / "user-plugins"
    (user / "lyrics").mkdir(parents=True)
    (user / "lyrics" / "plugin.json").write_text(json.dumps({
        "id": "lyrics", "api": "1.0", "name": "Lyrics", "description": "Song lyrics.",
        "permissions": ["network"], "contributes": {
            "settings": [{"key": "source", "type": "enum", "options": ["lrclib", "musixmatch"],
                          "label": "Lyrics from", "default": "lrclib"}]}}), encoding="utf-8")
    (user / "phone").mkdir()
    (user / "phone" / "plugin.json").write_text(json.dumps({"id": "phone", "api": "2.0", "name": "Phone",
                                                            "description": "Calls."}), encoding="utf-8")
    return plugins.PluginRegistry(cfg, dirs=[plugins.BUILTIN_DIR, user], persist=False)


def test_plugins_page():
    """Rows match the registry; core has no switch; a broken plugin shows why; yours start off and
    show what they can do; switches are staged, saved with Save (plugins.disabled / .enabled) and
    never overwritten with a stale copy; a plugin's own settings are written only when changed."""
    import yaml
    from heyclaude import plugins
    with tempfile.TemporaryDirectory() as d:
        cfg = config.defaults()
        reg = _demo_registry(cfg, d)
        w = _window(cfg, d, reg)
        try:
            assert "Plugins" in [w.tabs.tabText(i) for i in range(w.tabs.count())]
            listed = {p.id for p in reg.list()}
            assert set(w._plugin_rows) == listed
            core = w._plugin_rows["core"]
            assert core["switch"] is None and "Always on" in [x.text() for x in core["row"].findChildren(
                type(core["row"].title_label))]
            phone = w._plugin_rows["phone"]
            assert phone["switch"] is None and "isn't compatible" in phone["row"].desc_label.text()
            assert phone["row"].desc_label.property("tone") == "bad"
            lyr = w._plugin_rows["lyrics"]
            assert not lyr["switch"].isChecked() and "talks to the internet" in lyr["row"].desc_label.text()
            assert lyr["row"]._chev is not None                       # its own settings, behind the chevron
            assert w._plugin_rows["telegram"]["switch"].isChecked()
            assert not config.diff(cfg, w._collect()[0])              # untouched: nothing changes
            # staged: nothing applied before Save
            w._plugin_rows["telegram"]["switch"].setChecked(False)
            lyr["switch"].setChecked(True)
            assert reg.is_enabled("telegram") and not reg.is_enabled("lyrics")
            new = w._collect()[0]
            assert new["plugins"]["disabled"] == ["telegram"] and new["plugins"]["enabled"] == ["lyrics"]
            # something else switches news off meanwhile (the app's registry saves it at once): the
            # window follows it (any thread -> GUI), and Save doesn't put back a stale list
            reg.persist, reg.config_path = True, Path(d) / "config.yaml"
            reg.set_enabled("news", False)
            APP.processEvents()
            assert not w._plugin_rows["news"]["switch"].isChecked()
            new = w._collect()[0]
            assert new["plugins"]["disabled"] == ["news", "telegram"]
            assert w.save()
            written = yaml.safe_load((Path(d) / "config.yaml").read_text(encoding="utf-8"))
            assert written["plugins"]["disabled"] == ["news", "telegram"] and written["plugins"]["enabled"] == ["lyrics"]
            assert not w._plugin_staged
            # what the app does with the saved config: apply it in place, then sync
            config.apply_in_place(reg.cfg, new)
            reg.sync()
            assert not reg.is_enabled("telegram") and reg.is_enabled("lyrics")
            # a plugin's own setting: written only once changed
            assert "lyrics" not in (new.get("plugins", {}).get("settings") or {})
            combo = lyr["row"]._details.findChild(type(w.cmb_motion))
            w._set_combo(combo, "musixmatch")
            assert w._collect()[0]["plugins"]["settings"]["lyrics"]["source"] == "musixmatch"
            # reload drops what's staged
            w._plugin_rows["weather"]["switch"].setChecked(False)
            w.reload(w._orig)
            assert w._plugin_rows["weather"]["switch"].isChecked() and not w._plugin_staged
        finally:
            w.close()
            plugins.reset(None)


def test_plugins_page_bare_and_character_follows():
    """With no app registry the page still builds; switching off the pack in use moves the
    Character page to the ember at once (the pack is hidden, not selectable) and back again."""
    from heyclaude import plugins
    with tempfile.TemporaryDirectory() as d:
        cfg = config.defaults()
        cfg["island"]["character"]["pack"] = "wren"
        w = _window(cfg, d, None)                       # plugins.get(): a fresh default registry
        try:
            assert "character-wren" in w._plugin_rows and w.char_tab.current() == "wren"
            seen = []
            w.char_tab.preview_pack.connect(seen.append)
            w._plugin_rows["character-wren"]["switch"].setChecked(False)
            assert w.char_tab.current() == "none" or w.char_tab.current() == w.char_tab.gallery.item(0).data(0x0100)
            assert not w.char_tab.available("wren") and seen[-1] == w.char_tab.gallery.item(0).data(0x0100)
            new = w._collect()[0]
            assert new["plugins"]["disabled"] == ["character-wren"]
            assert new["island"]["character"]["pack"] == "wren"       # kept: switching it back on restores it
            w._plugin_rows["character-wren"]["switch"].setChecked(True)
            assert w.char_tab.current() == "wren" and w.char_tab.available("wren") and seen[-1] == "wren"
            assert not config.diff(cfg, w._collect()[0])
            # a config whose pack's plugin is already off: the ember shows, the pack is kept
            cfg2 = config.defaults()
            cfg2["island"]["character"]["pack"] = "echo"
            cfg2["plugins"]["disabled"] = ["character-echo"]
            w.reload(cfg2)
            assert not w.char_tab.available("echo") and w.char_tab.current() != "echo"
            assert not w._plugin_rows["character-echo"]["switch"].isChecked()
            assert not config.diff(cfg2, w._collect()[0])
        finally:
            w.close()
            plugins.reset(None)


if __name__ == "__main__":
    n = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            n += 1
            print("ok", name)
    print(n, "passed")
    sys.stdout.flush()
    sys.stderr.flush()
    faulthandler.disable()                  # the hard exit below isn't a crash
    if sys.platform == "win32":
        import ctypes
        k32 = ctypes.windll.kernel32
        k32.TerminateProcess(k32.GetCurrentProcess(), 0)
    os._exit(0)
