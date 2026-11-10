"""Every widget pack (M5 + M5b) against the M5 frame budget: rendered from its manifest
example on the live island (offscreen, software), then, once the entry is over and the
island rests, the window must not repaint faster than the idle budget (a 1 s countdown
tick is fine; an endless animation is not). Also: every pack is a valid plug-in, has a
schema-valid example, and every recorded tool result in tests/fixtures/tool_results
turns into its widget.

    .venv\\Scripts\\python tests\\test_widget_packs.py
"""
import faulthandler
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
faulthandler.enable()
sys.stdout.reconfigure(line_buffering=True)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from heyclaude import widgets as W  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "tool_results"
IDLE_FPS = 8          # ui/states.json budgets.idle_fps


def _spin(ms):
    from PySide6.QtCore import QEventLoop, QTimer
    loop = QEventLoop()
    QTimer.singleShot(int(ms), loop.quit)
    loop.exec()


_ISL = None


def _island():
    global _ISL
    if _ISL is None:
        from PySide6.QtWidgets import QApplication
        QApplication.instance() or QApplication([])
        from heyclaude.island import Bridge
        from heyclaude.island_qml import QmlIsland
        b = Bridge()
        _ISL = QmlIsland(b, {"screen": "primary", "top_margin": 8, "motion": "full"}, wake_label="hey claude",
                         software=True)
        _ISL.bridge_ = b
        _ISL.swaps = 0

        def swapped():
            _ISL.swaps += 1
        _ISL.view.frameSwapped.connect(swapped)
        _spin(600)
    return _ISL


def test_every_pack_is_a_valid_plugin_with_a_valid_example():
    reg = W.registry()
    assert len(reg) >= 11
    for wid, man in reg.items():
        spec = W.normalise({"widget": wid, "data": man["example"], "text": "fallback"})
        assert spec["widget"] == wid, (wid, spec)
        assert (Path(man["dir"]) / "Widget.qml").exists(), wid
        trig = man.get("triggers") or {}
        if trig.get("data") == "adapter":
            assert (Path(man["dir"]) / "adapter.py").exists(), wid
        bad = W.normalise({"widget": wid, "data": {"__nonsense__": True}, "text": "said instead"})
        assert bad.get("widget") in (None, "text") or bad == spec or "text" in bad, (wid, bad)


def test_every_recorded_tool_result_becomes_its_widget():
    from heyclaude.effects import parse_result, result_text
    for f in sorted(FIXTURES.glob("*.json")):
        fx = json.loads(f.read_text(encoding="utf-8"))
        if "answer" in fx:
            continue
        spec = W.from_tool(fx["tool"], fx["args"], parse_result(fx["content"]), result_text(fx["content"]))
        want = fx["expect"]["widget"]
        if want in W.registry():
            assert spec is not None and spec["widget"] == want, (f.name, spec)


def _rest_fps(isl, secs=3.0):
    isl.swaps = 0
    _spin(secs * 1000)
    return isl.swaps / secs


def test_every_pack_rests_within_the_idle_frame_budget():
    isl = _island()
    b = isl.bridge_
    # the island's own rate at rest after a turn, with no card (the software renderer may swap
    # more than once per engine tick): a card may add at most the idle budget on top
    isl.clear_widget()
    b.state.emit("speaking", "Here you go.")
    _spin(600)
    b.state.emit("idle", "")
    _spin(2500)
    base = _rest_fps(isl)
    over = {}
    for wid, man in W.registry().items():
        isl.clear_widget()
        b.state.emit("speaking", "Here you go.")
        _spin(250)
        b.widget.emit(W.normalise({"widget": wid, "data": man["example"], "text": ""}))
        _spin(1500)                                    # the card's entry
        b.state.emit("idle", "")
        _spin(6000)                                    # the island settles; a private card's fade is over
        info = isl.call("extrasInfo")
        info = info.toVariant() if hasattr(info, "toVariant") else info
        if info.get("widget") != wid:
            continue                                   # a card that closed at rest is within budget anyway
        fps = _rest_fps(isl)
        if fps > base + IDLE_FPS:
            over[wid] = round(fps - base, 1)
    isl.clear_widget()
    assert not over, f"repainting above the idle budget at rest (extra frames/s over the island's {base:.0f}): {over}"


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
        _spin(200)
    sys.stdout.flush()
    sys.stderr.flush()
    faulthandler.disable()
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.kernel32.TerminateProcess(ctypes.windll.kernel32.GetCurrentProcess(), 0)
    os._exit(0)
