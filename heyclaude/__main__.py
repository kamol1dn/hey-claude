"""hey-claude: wake word -> quick answers or headless Claude Code tasks.

    python -m heyclaude                 # mic + island
    python -m heyclaude --gpu           # whisper on CUDA (leave off while Premiere runs)
    python -m heyclaude --text          # type instead of talk (no mic, no whisper)
    python -m heyclaude --simulate samples\\question.wav --exit   # feed a wav as the mic
"""
from __future__ import annotations

import argparse
import os
import re
import signal
import sys
import threading


def main():
    ap = argparse.ArgumentParser(prog="heyclaude")
    ap.add_argument("--config", help="config file (default: config.yaml)")
    ap.add_argument("--gpu", action="store_true", help="run whisper on the GPU (off by default)")
    ap.add_argument("--text", action="store_true", help="type requests instead of speaking")
    ap.add_argument("--simulate", metavar="WAV", help="use a 16 kHz mono wav as the microphone")
    ap.add_argument("--exit", action="store_true", help="quit after the simulated request and its tasks finish")
    ap.add_argument("--no-ui", action="store_true", help="no island window")
    args = ap.parse_args()

    from . import config
    cfg = config.load(args.config)

    from PySide6.QtWidgets import QApplication
    from .island import Bridge, Island
    from .assistant import Assistant

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    bridge = Bridge()
    bridge.quit.connect(app.quit)
    asst = Assistant(cfg, bridge, use_gpu=args.gpu, simulate=args.simulate, text_mode=args.text,
                     exit_when_done=args.exit)
    island = None
    if not args.no_ui:
        wake = os.path.splitext(os.path.basename(cfg["wake"]["model"]))[0]
        island = Island(bridge, cfg["island"], wake_label=re.sub(r"_v?[\d.]+$", "", wake).replace("_", " "))
        island.activate.connect(asst.manual.set)
        island.stop_speaking.connect(lambda: asst.speaker.stop())
        island.quit_requested.connect(app.quit)

        def attach_tm():
            asst.ready.wait()
            island.tm = asst.tasks
        threading.Thread(target=attach_tm, daemon=True).start()

    threading.Thread(target=asst.run, daemon=True, name="assistant").start()
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    # Let Python see Ctrl+C while Qt's loop runs.
    from PySide6.QtCore import QTimer
    t = QTimer(interval=300)
    t.timeout.connect(lambda: None)
    t.start()
    code = app.exec()
    try:
        asst.brain.close()
    except Exception:
        pass
    sys.exit(code)


if __name__ == "__main__":
    main()
