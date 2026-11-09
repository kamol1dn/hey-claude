"""hey-claude: wake word -> quick answers or headless Claude Code tasks.

    python -m heyclaude                 # mic + island + tray
    pythonw -m heyclaude                # same, no console window (what "Start with Windows" runs)
    python -m heyclaude --cpu           # whisper on the CPU (stt.device in config.yaml is gpu by default)
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
    dev = ap.add_mutually_exclusive_group()
    dev.add_argument("--gpu", action="store_true", help="whisper on the GPU (overrides stt.device)")
    dev.add_argument("--cpu", action="store_true", help="whisper on the CPU (overrides stt.device)")
    ap.add_argument("--text", action="store_true", help="type requests instead of speaking")
    ap.add_argument("--simulate", metavar="WAV", help="use a 16 kHz mono wav as the microphone")
    ap.add_argument("--exit", action="store_true", help="quit after the simulated request and its tasks finish")
    ap.add_argument("--no-ui", action="store_true", help="no island window")
    ap.add_argument("--no-tray", action="store_true", help="no tray icon")
    ap.add_argument("--muted", action="store_true", help="start with listening off (mic closed)")
    ap.add_argument("--quit", action="store_true", help="ask the running hey-claude to quit, then exit")
    args = ap.parse_args()

    from . import applog, config
    log_path = applog.setup()
    cfg = config.load(args.config)
    if args.muted:
        cfg["app"]["listening"] = False
    # Simulated/typed runs are tests: they don't take the single-instance lock,
    # the global hotkey or the Startup shortcut away from a running copy.
    interactive = not (args.simulate or args.text)
    if args.quit:
        from PySide6.QtCore import QCoreApplication
        from .winsys import SingleInstance
        _qapp = QCoreApplication(sys.argv)
        inst = SingleInstance()
        if inst.acquire():
            inst.release()
            print("[app] hey-claude isn't running")
            sys.exit(1)
        ok = inst.poke("quit")
        print(f"[app] asked the running copy to quit: {'ok' if ok else 'no answer'}")
        sys.exit(0 if ok else 1)

    from PySide6.QtCore import QProcess, QTimer
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("hey-claude")

    instance = None
    if interactive:
        from .winsys import SingleInstance
        instance = SingleInstance()
        if not instance.acquire():
            ok = instance.poke("show")
            print(f"[app] hey-claude is already running{' (asked it to show itself)' if ok else ''}; exiting")
            sys.exit(0)

    from .assistant import Assistant
    from .island import Bridge, Island

    bridge = Bridge()
    bridge.quit.connect(app.quit)
    asst = Assistant(cfg, bridge, stt_device="gpu" if args.gpu else "cpu" if args.cpu else None,
                     simulate=args.simulate, text_mode=args.text,
                     exit_when_done=args.exit)
    asst.on_listening = lambda on, until: bridge.listening.emit(on, float(until or 0))
    state = {"restart": False}

    def restart():
        state["restart"] = True
        app.quit()

    def open_logs():
        os.startfile(str(config.LOG_DIR))

    # -- settings window --------------------------------------------------------
    settings = {"win": None}

    def open_settings():
        from .settings_ui import SettingsWindow
        w = settings["win"]
        if w is None:
            w = SettingsWindow(cfg, config_path=args.config)
            w.saved.connect(on_saved)
            w.restart_requested.connect(restart)
            settings["win"] = w
        else:
            w.reload(cfg)
        w.show()
        w.raise_()
        w.activateWindow()

    # -- global hotkey ------------------------------------------------------------
    hk = None

    def set_hotkey(spec):
        if hk is None:
            return None
        err = hk.set(spec or None)
        if err:
            print(f"[hotkey] not registered: {err if str(spec) in err else f'{spec}: {err}'}")
            if tray:
                tray.notify("Hotkey not registered", f"{spec}: {err}")
        else:
            print(f"[hotkey] {spec or 'none'}")
        if tray:
            tray.set_hotkey_label("" if err else (spec or ""))
        return err

    def on_saved(new: dict, _changed: list):
        changed = asst.apply_config(new)
        print(f"[settings] saved: {', '.join(changed) or 'no changes'}"
              + (f"; restart needed for {', '.join(config.needs_restart(changed))}"
                 if config.needs_restart(changed) else ""))
        if "app.hotkey" in changed:
            set_hotkey(cfg["app"].get("hotkey"))
        if "app.start_with_windows" in changed and interactive:
            from .winsys import set_autostart
            try:
                set_autostart(bool(cfg["app"]["start_with_windows"]))
                print(f"[autostart] {'on' if cfg['app']['start_with_windows'] else 'off'}")
            except Exception as e:
                print(f"[autostart] failed: {e}")
        if island and any(k.startswith("island.") for k in changed):
            island._place()

    # -- island -------------------------------------------------------------------
    island = None
    if not args.no_ui:
        wake = os.path.splitext(os.path.basename(cfg["wake"]["model"]))[0]
        island = Island(bridge, cfg["island"], wake_label=re.sub(r"_v?[\d.]+$", "", wake).replace("_", " "))
        island.activate.connect(asst.talk_now)
        island.stop_speaking.connect(lambda: asst.speaker.stop())
        island.quit_requested.connect(app.quit)

        def attach_tm():
            asst.ready.wait()
            island.tm = asst.tasks
        threading.Thread(target=attach_tm, daemon=True).start()

    # -- tray -----------------------------------------------------------------------
    tray = None
    if not args.no_tray:
        from .tray import Tray
        tray = Tray(app)
        tray.talk_now.connect(asst.talk_now)
        tray.listening_toggled.connect(lambda on: asst.set_listening(on))
        tray.pause_requested.connect(lambda secs: asst.set_listening(False, pause_seconds=secs))
        tray.settings_requested.connect(open_settings)
        tray.logs_requested.connect(open_logs)
        tray.quit_requested.connect(app.quit)
        bridge.listening.connect(lambda on, until: tray.set_listening(on, until or None))
        tray.set_listening(asst.listening.is_set())

    bridge.talk.connect(asst.talk_now)

    if interactive:
        from .hotkey import GlobalHotkey
        from .winsys import autostart_enabled
        # The Startup shortcut is the truth for "start with Windows".
        try:
            cfg["app"]["start_with_windows"] = autostart_enabled()
        except Exception as e:
            print(f"[autostart] can't check: {e}")
        hk = GlobalHotkey(lambda: bridge.talk.emit())
        set_hotkey(cfg["app"].get("hotkey"))

        def poked(msg):
            print(f"[app] second launch: {msg}")
            if msg == "quit":
                app.quit()
                return
            if msg == "restart":
                restart()
                return
            if island:
                island.show()
                island.raise_()
                island.show_tasks_until = __import__("time").time() + 4
                island._place()
            if tray:
                tray.notify("hey-claude is already running", "It's in the tray. Right-click it for settings.")
        instance.poked.connect(poked)

    print(f"[app] log: {log_path}")
    threading.Thread(target=asst.run, daemon=True, name="assistant").start()
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    # Let Python see Ctrl+C while Qt's loop runs.
    t = QTimer(interval=300)
    t.timeout.connect(lambda: None)
    t.start()
    code = app.exec()

    asst.stop()
    if hk:
        hk.close()
    try:
        asst.brain.close()
    except Exception:
        pass
    if tray:
        tray.hide()
    if instance:
        instance.release()
    if state["restart"]:
        from .config import ROOT
        argv = ["-m", "heyclaude", *sys.argv[1:]]
        ok = QProcess.startDetached(sys.executable, argv, str(ROOT))
        print(f"[app] restarting: {'ok' if ok else 'FAILED'}")
    print("[app] bye")
    sys.exit(code)


if __name__ == "__main__":
    main()
