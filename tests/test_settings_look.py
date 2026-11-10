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
