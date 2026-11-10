"""M5b media packs: now_playing (cover, progress, previous / play-pause / next) and
clipboard (the clipboard's text, or a screenshot's thumbnail). Their manifests and
examples, the adapters on the recorded tool results, the text fallback, the M9
tools they read (Windows mocked), and, on the live QML island (offscreen), the
card's buttons acting through media_control and the cover going in.

    .venv\\Scripts\\python tests\\test_widgets_media.py
"""
import faulthandler
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
faulthandler.enable()
sys.stdout.reconfigure(line_buffering=True)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from heyclaude import widgets as W  # noqa: E402
from heyclaude.effects import parse_result, result_text  # noqa: E402
from heyclaude.mcp_windows import clipboard, media, registry, screen  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "tool_results"


def _fx(name):
    fx = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return fx, W.from_tool(fx["tool"], fx["args"], parse_result(fx["content"]), result_text(fx["content"]))


def _adapter(wid):
    return W._plugin_adapter(W.registry()[wid])


# ------------------------------------------------------------------ manifests
def test_examples_validate_and_bad_data_falls_back_to_text():
    for wid in ("now_playing", "clipboard"):
        man = W.registry()[wid]
        assert man.get("private") is (wid == "clipboard"), wid
        assert man["triggers"]["data"] == "adapter" and (Path(man["dir"]) / "adapter.py").exists()
        assert man["size"]["width"] <= 460 and man["size"]["max_height"] <= 280
        spec = W.normalise({"widget": wid, "data": man["example"], "text": "x"})
        assert spec["widget"] == wid and W.validate(spec["data"], man["schema"]) == [], wid
    for bad in ({"widget": "now_playing", "data": {"title": "A", "status": "dancing"}},
                {"widget": "now_playing", "data": {"status": "playing"}},
                {"widget": "clipboard", "data": {"kind": "poem"}},
                {"widget": "clipboard", "data": {"kind": "screenshot", "src": "file:///C:/secret.png"}}):
        out = W.normalise(dict(bad, text="said instead"))
        assert out["widget"] == "text" and out["data"]["text"] == "said instead", (bad, out)
        assert W.normalise(dict(bad, text="")) is None


# ------------------------------------------------------------------ now_playing
def test_now_playing_from_the_recorded_results():
    fx, spec = _fx("windows_now_playing.json")
    assert spec["widget"] == "now_playing", spec
    assert spec["data"] == {"title": "Morning stretch, 30 seconds", "status": "paused", "app": "Chrome",
                            "duration_s": 8.3, "position_s": 3.5}, spec["data"]
    assert W.title_for(W.registry()["now_playing"], spec["data"]) == fx["expect"]["title"]
    fx, spec = _fx("windows_now_playing_song.json")
    assert spec["data"]["artist"] == "The Quiet Ferns" and spec["data"]["album"] == "Low Tide Radio"


def test_now_playing_adapter_edges():
    adapt = _adapter("now_playing")
    assert adapt({}, {"status": "none"}, "") is None                     # nothing playing: no card, the answer says so
    assert adapt({}, {"title": "", "status": "playing"}, "") is None
    d = adapt({}, None, json.dumps({"title": "T", "artist": "A", "app": "Spotify.exe", "status": "Playing",
                                    "position_s": 400, "duration_s": 200}))
    assert d == {"title": "T", "artist": "A", "app": "Spotify", "status": "playing",
                 "duration_s": 200.0, "position_s": 200.0}, d                 # position clamped to the length
    d = adapt({}, {"title": "T", "status": "weird", "duration_s": 0, "position_s": 5}, "")
    assert d == {"title": "T", "status": "unknown"}, d                        # no length: no progress line
    names = adapt.__globals__["app_name"]
    assert names("Microsoft.ZuneMusic_8wekyb3d8bbwe!Microsoft.ZuneMusic") == "Media Player"
    assert names("MSEdge") == "Edge" and names("chrome") == "Chrome"
    assert names("SomeVendor.CoolPlayer_1a2b3c4d5e6f7!App") == "App"
    assert names("coolplayer.exe") == "Coolplayer" and names("") == ""


def test_media_tools_offline():
    tracks = {"title": "Paper Lanterns", "status": "playing", "art": b"\x89PNG..."}
    with mock.patch.object(media, "_os_now_playing", lambda art=False: dict(tracks)):
        assert media.album_art() == b"\x89PNG..."
        assert media.album_art("Paper Lanterns") == b"\x89PNG..."
        assert media.album_art("Another Song") is None                       # never another track's cover
        assert "art" not in media.now_playing()


# ------------------------------------------------------------------ clipboard
def test_clipboard_from_the_recorded_results():
    fx, spec = _fx("windows_get_clipboard.json")
    d = spec["data"]
    assert spec["widget"] == "clipboard" and d["kind"] == "link" and d["host"] == "example.org", d
    assert d["chars"] == len(d["text"]) and d["lines"] == 1
    assert W.title_for(W.registry()["clipboard"], d) == fx["expect"]["title"] == "Clipboard"
    fx, spec = _fx("windows_get_clipboard_code.json")
    d = spec["data"]
    assert d["kind"] == "code" and d["lines"] == 8 and d["text"].startswith("def rolling_mean") and "more" not in d, d


def test_clipboard_kinds_and_clamping():
    adapt = _adapter("clipboard")
    assert adapt({}, {"text": "Meet at the north gate at six, bring the blue folder.", "chars": 53}, "")["kind"] == "text"
    code = "const total = items\n  .filter(x => x.ok)\n  .map(x => x.price);\nconsole.log(total);\n"
    assert adapt({}, {"text": code, "chars": len(code)}, "")["kind"] == "code"
    prose = "Dear Sam,\nthanks for the notes. I'll read them tonight\nand reply tomorrow.\nBest, Alex"
    assert adapt({}, {"text": prose, "chars": len(prose)}, "")["kind"] == "text"
    assert adapt({}, {"text": "https://example.com/a b", "chars": 23}, "")["kind"] == "text"   # not one URL
    long = "\n".join(f"line {i} of a long paste" for i in range(40))
    d = adapt({}, {"text": long, "chars": len(long)}, "")
    assert d["lines"] == 40 and d["text"].count("\n") == 11 and d["more"] > 0, d
    assert W.normalise({"widget": "clipboard", "data": d, "text": ""})["widget"] == "clipboard"
    huge = "x" * 50000
    d = adapt({}, {"text": huge, "chars": len(huge)}, "")
    assert len(d["text"]) <= 900 and d["text"].endswith("…") and d["chars"] == 50000
    for holds in ("image", "files", "empty", "other"):
        assert adapt({}, {"text": None, "chars": 0, "holds": holds}, "") == {"kind": holds, "heading": "Clipboard", "chars": 0}
    assert adapt({}, {"text": "   ", "chars": 3}, "")["kind"] == "empty"
    assert adapt({}, {"text": None, "chars": 0}, "")["kind"] == "other"
    assert adapt({}, {"something": 1}, "") is None and adapt({}, None, "not json") is None


def test_get_clipboard_says_what_it_holds_when_it_isnt_text():
    with mock.patch.object(clipboard, "_os_get", lambda: None), mock.patch.object(clipboard, "_os_holds", lambda: "image"):
        assert clipboard.get_clipboard() == {"text": None, "chars": 0, "holds": "image"}
    with mock.patch.object(clipboard, "_os_get", lambda: "hi"):
        assert clipboard.get_clipboard() == {"text": "hi", "chars": 2}       # unchanged for text

    def busy():
        raise OSError("held by another app")
    with mock.patch.object(clipboard, "_os_get", lambda: None), mock.patch.object(clipboard, "_os_holds", busy):
        assert clipboard.get_clipboard()["holds"] == "other"


# ------------------------------------------------------------------ screenshot
def test_screenshot_from_the_recorded_result():
    fx, spec = _fx("windows_screenshot.json")
    d = spec["data"]
    assert [b["type"] for b in fx["content"]] == ["image", "text"]           # the model's image + the island's text
    assert spec["widget"] == "clipboard" and d["kind"] == "screenshot" and (d["width"], d["height"]) == (1920, 1080), d
    assert d["src"].startswith("data:image/jpeg;base64,") and len(d["src"]) > 5000
    assert (FIXTURES / "windows_screenshot_preview.jpg").exists()          # a repo fixture is read, never deleted
    assert W.title_for(W.registry()["clipboard"], d) == fx["expect"]["title"]


def test_screenshot_preview_is_read_once_and_only_from_its_folder():
    adapt = _adapter("clipboard")
    jpg = (FIXTURES / "windows_screenshot_preview.jpg").read_bytes()
    with tempfile.TemporaryDirectory() as tmp:
        live = Path(tmp) / "state" / "screen"
        live.mkdir(parents=True)
        other = Path(tmp) / "elsewhere.jpg"
        other.write_bytes(jpg)
        with mock.patch.object(screen, "preview_dir", lambda: live):
            p = live / "preview.jpg"
            p.write_bytes(jpg)
            d = adapt({}, {"width": 2560, "height": 1440, "monitor": 1, "preview": str(p)}, "")
            assert d["src"].startswith("data:image/jpeg;base64,") and d["monitor"] == 1, d
            assert not p.exists()                                            # the copy goes once the card has it
            d = adapt({}, {"width": 2560, "height": 1440, "monitor": 0, "preview": str(other)}, "")
            assert "src" not in d and other.exists(), d                      # anywhere else: refused, untouched
            d = adapt({}, {"width": 2560, "height": 1440, "preview": str(live / ".." / ".." / "elsewhere.jpg")}, "")
            assert "src" not in d
            (live / "fake.jpg").write_bytes(b"not a jpeg")
            assert "src" not in adapt({}, {"width": 1, "height": 1, "preview": str(live / "fake.jpg")}, "")
            # save_preview: a small copy in that folder
            from PySide6.QtGui import QImage
            path = screen.save_preview(jpg, max_width=200)
            assert Path(path) == live / "preview.jpg" and QImage(path).width() == 200
            assert screen.save_preview(b"nonsense") == ""


def test_server_screenshot_returns_the_image_and_a_text_block():
    try:
        from heyclaude.mcp_windows import server
        s = server.build()
    except ImportError as e:
        print("  (skipped: mcp SDK missing:", e, ")")
        return
    import asyncio
    w, h = 64, 36
    bgra = bytes([40, 90, 200, 255]) * (w * h)
    with tempfile.TemporaryDirectory() as tmp, mock.patch.object(screen, "_os_capture", lambda m: (bgra, w, h)), \
            mock.patch.object(screen, "preview_dir", lambda: Path(tmp)):
        res = asyncio.run(s.call_tool("screenshot", {"monitor": 0}))
        blocks = getattr(res, "content", res)
        if isinstance(blocks, tuple):
            blocks = blocks[0]
        kinds = [getattr(b, "type", None) for b in blocks]
        assert kinds == ["image", "text"], kinds
        meta = json.loads(blocks[1].text)
        assert meta["width"] == 64 and meta["height"] == 36 and meta["monitor"] == 0
        assert Path(meta["preview"]).parent == Path(tmp) and Path(meta["preview"]).exists()
        spec = W.from_tool("mcp__windows__screenshot", {"monitor": 0}, json.loads(blocks[1].text), blocks[1].text)
        assert spec["widget"] == "clipboard" and spec["data"]["src"].startswith("data:image/jpeg")
        assert not Path(meta["preview"]).exists()


# ------------------------------------------------------------------ the live card
_ISL = None


def _spin(ms):
    from PySide6.QtCore import QEventLoop, QTimer
    loop = QEventLoop()
    QTimer.singleShot(int(ms), loop.quit)
    loop.exec()


def _island():
    global _ISL
    if _ISL is None:
        from PySide6.QtWidgets import QApplication
        QApplication.instance() or QApplication([])
        from heyclaude.island import Bridge
        from heyclaude.island_qml import QmlIsland
        b = Bridge()
        _ISL = QmlIsland(b, {"screen": "primary", "top_margin": 8, "motion": "full"}, wake_label="hey claude", software=True)
        _ISL.bridge_ = b
        _spin(600)
    return _ISL


def _find(isl, name):
    from PySide6.QtQuick import QQuickItem
    return isl.root.findChild(QQuickItem, name)


def test_the_cards_buttons_act_through_media_control_and_the_card_follows():
    isl = _island()
    b = isl.bridge_
    _, spec = _fx("windows_now_playing_song.json")
    isl.clear_widget()
    b.state.emit("speaking", "Here you go.")
    _spin(200)
    b.widget.emit(spec)
    _spin(1500)
    assert str(_find(isl, "nowPlayingTitle").property("text")) == "Paper Lanterns"
    calls = []
    track = {"title": "Paper Lanterns", "artist": "The Quiet Ferns", "app": "Chrome", "status": "paused",
             "position_s": 3.5, "duration_s": 8.3}

    def fake(name, args):
        calls.append((name, dict(args)))
        if name == "media_control":
            if args["action"] == "next":
                track.update(title="Harbour Lights", artist="Wren & Tide", position_s=0.0, duration_s=184.0, status="playing")
            return {"old": {}, "new": {}, "action": args["action"]}
        return dict(track)
    wid = isl.widget["id"]
    with mock.patch.object(registry, "call", fake):
        isl._widget_action(wid, "media", "next")
        for _ in range(40):
            _spin(100)
            if isl.widget["data"]["title"] == "Harbour Lights":
                break
        _spin(300)
    assert calls[0] == ("media_control", {"action": "next"}) and calls[1][0] == "now_playing", calls
    assert isl.widget["data"]["title"] == "Harbour Lights" and isl.widget["data"]["status"] == "playing"
    assert str(_find(isl, "nowPlayingTitle").property("text")) == "Harbour Lights"
    # a stale card (replaced meanwhile) is left alone; unknown actions do nothing
    isl._update_now_playing("not-this-card", {"title": "X", "status": "playing"})
    assert isl.widget["data"]["title"] == "Harbour Lights"
    with mock.patch.object(registry, "call", fake):
        n = len(calls)
        isl._widget_action(wid, "media", "rm -rf")
        _spin(300)
        assert len(calls) == n
    # the cover: rounded here, handed to the card through the image provider
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    from PySide6.QtGui import QColor, QImage
    img = QImage(300, 200, QImage.Format_RGB32)
    img.fill(QColor("#3366cc"))
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    isl._set_now_playing_art(wid, bytes(ba.data()))
    art = isl.art.images[f"art-np-{wid}"]
    assert art.width() == 160 and art.pixelColor(0, 0).alpha() == 0 and art.pixelColor(80, 80).blue() > 150
    _spin(500)
    isl.clear_widget()
    _spin(200)


def test_the_cards_render_from_every_fixture():
    isl = _island()
    b = isl.bridge_
    for name in ("windows_now_playing.json", "windows_get_clipboard_code.json", "windows_screenshot.json"):
        fx, spec = _fx(name)
        isl.clear_widget()
        b.state.emit("speaking", "Here you go.")
        _spin(200)
        b.widget.emit(spec)
        _spin(1500)
        info = isl.call("extrasInfo")
        info = info.toVariant() if hasattr(info, "toVariant") else info
        assert info["widget"] == fx["expect"]["widget"] and info["widgetReady"], (name, info)
    frame = _find(isl, "screenshotFrame")
    assert frame is not None and frame.width() > 200 and frame.height() > 100
    isl.clear_widget()
    _spin(200)


if __name__ == "__main__":
    n = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            n += 1
            print("ok", name)
    print(n, "passed")
    if _ISL is not None:
        _ISL.close()
        _spin(300)
    sys.stdout.flush()
    sys.stderr.flush()
    if sys.platform == "win32":
        import ctypes
        k32 = ctypes.windll.kernel32
        k32.TerminateProcess(k32.GetCurrentProcess(), 0)
    os._exit(0)
