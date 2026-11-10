"""Render a response widget on the real island, offscreen (M5b): its manifest example,
or a recorded tool result from tests/fixtures/tool_results/. Saves a png of the card as
it settles (frames at a few moments, so the entry animation shows) and, with --gif, an
animated gif. It never touches a running hey-claude.

    .venv\\Scripts\\python tools\\widget_shot.py system out.png                 # widgets/system example
    .venv\\Scripts\\python tools\\widget_shot.py --fixture info_system_status.json out.png
    .venv\\Scripts\\python tools\\widget_shot.py --spec '{"widget": "convert", "data": {...}}' out.png
    .venv\\Scripts\\python tools\\widget_shot.py system out.gif --gif --scale 2
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform == "win32":
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts"))
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QBuffer, QEventLoop, QIODevice, QRectF, QTimer  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "tool_results"


def spin(ms):
    loop = QEventLoop()
    QTimer.singleShot(int(ms), loop.quit)
    loop.exec()


def spec_from(args) -> dict:
    from heyclaude import widgets as W
    if args.spec:
        return W.normalise(json.loads(args.spec))
    if args.fixture:
        from heyclaude.effects import parse_result, result_text
        fx = json.loads((FIXTURES / args.fixture).read_text(encoding="utf-8")) if not os.path.isabs(args.fixture) \
            else json.loads(Path(args.fixture).read_text(encoding="utf-8"))
        if "answer" in fx:
            return W.strip_widget_lines(fx["answer"])[1]
        spec = W.from_tool(fx["tool"], fx["args"], parse_result(fx["content"]), result_text(fx["content"]))
        if spec is None:
            raise SystemExit(f"{args.fixture}: no widget from that tool result")
        return spec
    man = W.registry()[args.widget]
    return W.normalise({"widget": args.widget, "data": man["example"], "text": ""})


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("widget", nargs="?", help="a widget id (renders its manifest example)")
    ap.add_argument("out", help="a .png (or .gif with --gif)")
    ap.add_argument("--fixture", help="a file in tests/fixtures/tool_results (or a path)")
    ap.add_argument("--spec", help="a widget spec as JSON")
    ap.add_argument("--scale", type=float, default=2.0)
    ap.add_argument("--gif", action="store_true", help="an animated gif of the first ~2.5 s")
    ap.add_argument("--motion", default="full", choices=["full", "calm", "off"])
    args = ap.parse_args()
    if not (args.widget or args.fixture or args.spec):
        ap.error("a widget id, --fixture or --spec")
    app = QApplication(sys.argv[:1])
    from heyclaude.island import Bridge
    from heyclaude.island_qml import QmlIsland
    spec = spec_from(args)
    if spec is None:
        raise SystemExit("not a valid widget (it would fall back to text)")
    b = Bridge()
    isl = QmlIsland(b, {"screen": "primary", "top_margin": 8, "motion": args.motion, "scale": args.scale},
                    wake_label="hey claude", software=True)
    spin(700)
    b.state.emit("speaking", spec.get("text") or "Here you go.")
    spin(200)
    b.widget.emit(spec)
    frames, shots = [], []

    def crop() -> QImage:
        img = isl.grab()
        s = img.width() / 600
        info = isl.call("extrasInfo")
        info = info.toVariant() if hasattr(info, "toVariant") else info
        w = max(320, float(info.get("pillW", 400))) + 40
        h = float(info.get("pillH", 120)) + 30
        return img.copy(int((600 - w) / 2 * s), 0, int(w * s), int(min(400, h) * s))

    t0 = time.time()
    if args.gif:
        from PIL import Image
        while time.time() - t0 < 2.6:
            spin(50)
            buf = QBuffer()
            buf.open(QIODevice.ReadWrite)
            crop().save(buf, "PNG")
            frames.append(Image.open(io.BytesIO(bytes(buf.data()))).convert("RGB"))
        frames[0].save(args.out, save_all=True, append_images=frames[1:], duration=50, loop=0)
    else:
        for at in (0.12, 0.4, 2.0):
            spin(max(0, (at - (time.time() - t0)) * 1000))
            shots.append(crop())
        W = max(i.width() for i in shots)
        H = sum(i.height() for i in shots) + 8 * len(shots)
        sheet = QImage(W, H, QImage.Format_ARGB32)
        sheet.fill(QColor("#1c1f27"))
        p = QPainter(sheet)
        y = 0
        for i in shots:
            p.drawImage(0, y, i)
            y += i.height() + 8
        p.end()
        sheet.save(args.out)
    info = isl.call("extrasInfo")
    info = info.toVariant() if hasattr(info, "toVariant") else info
    rc = isl.call("restCheck")
    rc = rc.toVariant() if hasattr(rc, "toVariant") else rc
    print(json.dumps({"out": args.out, "widget": info.get("widget"), "ready": info.get("widgetReady"),
                      "panel": [info.get("pillW"), info.get("pillH")], "extras_animating": rc.get("extrasAnimating")}))
    sys.stdout.flush()
    isl.close()
    os._exit(0)


if __name__ == "__main__":
    main()
