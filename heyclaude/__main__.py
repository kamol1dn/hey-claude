"""hey-claude: wake word -> quick answers or headless Claude Code tasks.

    python -m heyclaude                 # mic + island + tray
    pythonw -m heyclaude                # same, no console window (what "Start with Windows" runs)
    python -m heyclaude --cpu           # whisper on the CPU (stt.device in config.yaml is gpu by default)
    python -m heyclaude --text          # type instead of talk (no mic, no whisper)
    python -m heyclaude --simulate samples\\question.wav --exit   # feed a wav as the mic
    python -m heyclaude --simulate a.wav b.wav c.wav --exit      # a simulated conversation (follow-ups)
    python -m heyclaude --record-wake-samples [N] [--auto]       # record your own "hey claude" clips
"""
from __future__ import annotations

import argparse
import os
import signal
import sys
import threading


def main(argv=None, harness=None):
    """`harness` (tools/ux_stress.py): runs the real app in this process with a
    simulated microphone. It may have `configure(cfg)`, `mic_factory(device)` and
    `started(ctx)` (called with the app's parts just before the Qt loop starts).
    A harness run is not interactive: no single-instance lock, no global hotkeys."""
    # Hugging Face's xet transfer can stall at 0 bytes on slow lines; plain HTTP resumes.
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    ap = argparse.ArgumentParser(prog="heyclaude")
    ap.add_argument("--config", help="config file (default: config.yaml)")
    dev = ap.add_mutually_exclusive_group()
    dev.add_argument("--gpu", action="store_true", help="whisper on the GPU (overrides stt.device)")
    dev.add_argument("--cpu", action="store_true", help="whisper on the CPU (overrides stt.device)")
    ap.add_argument("--text", action="store_true", help="type requests instead of speaking")
    ap.add_argument("--simulate", metavar="WAV", nargs="+",
                    help="use 16 kHz mono wavs as the microphone; with several, each one after the first "
                         "is played into the follow-up window (or once idle and tasks are done)")
    ap.add_argument("--exit", action="store_true", help="quit after the simulated request and its tasks finish")
    ap.add_argument("--no-ui", action="store_true", help="no island window")
    ap.add_argument("--no-tray", action="store_true", help="no tray icon")
    ap.add_argument("--muted", action="store_true", help="start with listening off (mic closed)")
    ap.add_argument("--speech", choices=("local", "windows", "cloud", "auto"),
                    help="speech backend for this run (overrides speech.backend)")
    ap.add_argument("--quit", action="store_true", help="ask the running hey-claude to quit, then exit")
    ap.add_argument("--record-wake-samples", nargs="?", type=int, const=40, metavar="N",
                    help="record N (default 40) clips of you saying \"hey claude\" into state/wake_samples")
    ap.add_argument("--auto", action="store_true", help="with --record-wake-samples: no Enter between clips")
    args = ap.parse_args(argv)

    from . import config
    if args.record_wake_samples is not None:
        from . import wake_samples
        sys.exit(wake_samples.main([str(args.record_wake_samples)] + (["--auto"] if args.auto else []),
                                   cfg=config.load(args.config)))
    from . import applog
    log_path = applog.setup()
    cfg = config.load(args.config)
    if harness is not None and hasattr(harness, "configure"):
        harness.configure(cfg)
    if args.muted:
        cfg["app"]["listening"] = False
    if args.speech:
        cfg.setdefault("speech", {}).update(backend=args.speech, stt_backend=None, tts_backend=None)
    # Simulated/typed runs are tests: they don't take the single-instance lock,
    # the global hotkey or the Startup shortcut away from a running copy.
    interactive = not (args.simulate or args.text or harness is not None)
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

    app = QApplication.instance() or QApplication(sys.argv)
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
    from .island import Bridge, make_island

    bridge = Bridge()
    bridge.quit.connect(app.quit)
    asst = Assistant(cfg, bridge, stt_device="gpu" if args.gpu else "cpu" if args.cpu else None,
                     simulate=args.simulate, text_mode=args.text,
                     exit_when_done=args.exit, mic_factory=getattr(harness, "mic_factory", None))
    asst.on_listening = lambda on, until: bridge.listening.emit(on, float(until or 0))
    state = {"restart": False}

    def restart():
        state["restart"] = True
        app.quit()

    def open_logs():
        os.startfile(str(config.LOG_DIR))

    def record_wake_samples():
        """Tray -> "Record wake samples...": the recorder in its own console window,
        with listening paused meanwhile so the assistant doesn't answer every clip."""
        import subprocess
        exe = sys.executable
        if os.path.basename(exe).lower() == "pythonw.exe":       # needs a console for the prompts
            exe = os.path.join(os.path.dirname(exe), "python.exe")
        cmd = [exe, "-m", "heyclaude.wake_samples", "--pause-at-end"]
        if args.config:
            cmd += ["--config", args.config]
        was_on = asst.listening.is_set()
        if was_on:
            asst.set_listening(False)
        try:
            p = subprocess.Popen(cmd, cwd=str(config.ROOT), creationflags=subprocess.CREATE_NEW_CONSOLE)
        except Exception as e:
            print(f"[wake] couldn't start the sample recorder: {e}")
            if was_on:
                asst.set_listening(True)
            return
        print("[wake] recording samples in a new window; listening is paused until it closes")

        def wait():
            p.wait()
            print("[wake] sample recorder closed")
            if was_on and not asst.listening.is_set():
                asst.set_listening(True)
        threading.Thread(target=wait, daemon=True, name="wake-samples").start()

    # -- settings window --------------------------------------------------------
    settings = {"win": None}

    class _WakeHooks:
        """What the Settings -> Wake word wizard needs from the running app."""
        _was_on = None

        def pause(self):                         # recording clips / room sound / live test
            if self._was_on is None:
                self._was_on = asst.listening.is_set()
                if self._was_on:
                    asst.set_listening(False)

        def resume(self):
            if self._was_on:
                asst.set_listening(True)
            self._was_on = None

        def gpu_util(self):                      # GPU load without hey-claude's own work
            m = asst.monitor
            return m.gpu_util() if m is not None else None

        def loaded_label(self):
            w = getattr(asst, "wake", None)
            return (w.label, w.fallback) if w is not None else (None, None)
    wake_hooks = _WakeHooks()

    def open_settings():
        from .settings_ui import SettingsWindow
        w = settings["win"]
        if w is None:
            w = SettingsWindow(cfg, config_path=args.config, wake_hooks=wake_hooks)
            w.saved.connect(on_saved)
            if island is not None and hasattr(island, "preview_scale"):
                w.island_preview.connect(island.preview_scale)          # M5: live size preview
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
                tray.notify_action("Hotkey not registered",
                                   f"{err}. Click here to choose another key in Settings.", open_settings)
        else:
            print(f"[hotkey] {spec or 'none'}")
        if tray:
            tray.set_hotkey_label("" if err else (spec or ""))
        return err

    def set_hold_key(spec):
        """Hold-to-keep-listening key ("" = off)."""
        from .holdkey import HoldKey, parse
        if not spec:
            asst.hold = None
            print("[hold] off")
            return
        mute = cfg["listen"].get("hold_mute_key", "shift")
        try:
            parse(spec)
            if mute:
                parse(mute)
        except ValueError as e:
            print(f"[hold] {e}")
            return
        if asst.hold is None:
            asst.hold = HoldKey(spec, on_change=asst.on_hold, mute_spec=mute)
        else:
            asst.hold.set_key(spec, mute)
        print(f"[hold] {spec}" + (f" (+{mute} mutes)" if mute else ""))

    def on_saved(new: dict, _changed: list):
        changed = asst.apply_config(new)
        print(f"[settings] saved: {', '.join(changed) or 'no changes'}"
              + (f"; restart needed for {', '.join(config.needs_restart(changed))}"
                 if config.needs_restart(changed) else ""))
        if ("listen.hold_key" in changed or "listen.hold_mute_key" in changed) and interactive:
            set_hold_key(cfg["listen"].get("hold_key"))
        if "app.hotkey" in changed:
            set_hotkey(cfg["app"].get("hotkey"))
        if "dictate.key" in changed and dictation["d"] is not None:
            try:
                dictation["d"].set_key()
            except ValueError as e:
                print(f"[dictate] {e}")
        if "dictate.paste_again_hotkey" in changed and dict_hk is not None:
            set_paste_again(cfg["dictate"].get("paste_again_hotkey"))
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
        from .listen import resolve_wake_model, wake_label
        island = make_island(bridge, cfg["island"], wake_label=wake_label(resolve_wake_model(cfg["wake"]["model"])[0]))
        island.activate.connect(asst.talk_now)
        island.attach_input(asst.begin_input, asst.cancel_input_mic, asst.submit_text, closed=asst.end_input)
        island.stop_speaking.connect(asst.hush)
        # Mic button while it talks: stop, then listen (same as the hotkey: the talk-or-type field).
        island.interrupt_talk.connect(lambda: (asst.hush(), bridge.talk.emit()))
        island.quit_requested.connect(app.quit)
        # M9: tool effects. Undo runs locally; sleep/restart/shutdown only ever on this click.
        island.effect_undo.connect(asst.undo_clicked)
        island.effect_confirm.connect(asst.confirm_clicked)
        island.effect_cancel.connect(asst.cancel_clicked)

        def not_me():
            path = asst.reject_wake()
            island.last_wake_t = 0.0
            island.flash("saved: not the wake word" if path else "no recent wake to save")
        island.not_me.connect(not_me)

        # M5: response widgets that act (a timer ending is said aloud)
        def widget_action(wid, action, payload):
            if action == "timer_done":
                asst.timer_widget_done(str(payload or ""))     # M10: local timers announce themselves
        if hasattr(island, "widget_action"):
            island.widget_action.connect(widget_action)

        def attach_tm():
            asst.ready.wait()
            island.tm = asst.tasks
            if getattr(asst, "wake", None) is not None:
                island.wake_label = asst.wake.label     # what actually loaded (fallback included)
        threading.Thread(target=attach_tm, daemon=True).start()

    # -- tray -----------------------------------------------------------------------
    dictation: dict = {"d": None}                  # M4d, made once the assistant has loaded
    dict_hk = None
    tray = None
    if not args.no_tray:
        from .tray import Tray
        tray = Tray(app)
        tray.talk_now.connect(asst.talk_now)
        tray.listening_toggled.connect(lambda on: asst.set_listening(on))
        tray.pause_requested.connect(lambda secs: asst.set_listening(False, pause_seconds=secs))
        tray.settings_requested.connect(open_settings)
        tray.logs_requested.connect(open_logs)
        tray.record_samples_requested.connect(record_wake_samples)
        tray.quit_requested.connect(app.quit)
        bridge.listening.connect(lambda on, until: tray.set_listening(on, until or None))
        tray.set_listening(asst.listening.is_set())

        # M4c: the speech switch and "Free memory now", no restart
        def speech_chosen(name):
            asst.set_speech_backend(name)
            if interactive and not args.speech:
                try:
                    config.save_keys({"speech.backend": name, "speech.stt_backend": None,
                                      "speech.tts_backend": None}, args.config)
                except Exception as e:
                    print(f"[speech] couldn't save the choice: {e}")
        tray.speech_backend_chosen.connect(
            lambda n: threading.Thread(target=speech_chosen, args=(n,), daemon=True, name="speech-switch").start())
        tray.free_memory_requested.connect(asst.free_speech_memory)
        bridge.stt.connect(lambda label: tray.set_speech(
            str(cfg.get("speech", {}).get("stt_backend") or cfg.get("speech", {}).get("backend") or "local"), label))
        tray.set_speech(str(cfg.get("speech", {}).get("backend") or "local"))

    # The hotkey opens the island ready to talk or type (toggle); without an island, it just listens.
    def talk_key():
        if asst.mode in ("speaking", "thinking"):
            asst.hush()                             # the hotkey also cuts it off mid-answer
        island.toggle_input() if island and island.session else asst.talk_now()
    bridge.talk.connect(talk_key)
    bridge.hush.connect(asst.hush)

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
        set_hold_key(cfg["listen"].get("hold_key"))

        # Stop key (default Esc): registered only while it thinks or talks, so Esc is
        # never taken from other apps the rest of the time.
        stop_hk = GlobalHotkey(lambda: bridge.hush.emit())
        stop_state = {"on": False, "warned": False}

        def stop_key_for(mode, _text=""):
            spec = str(cfg["app"].get("stop_hotkey", "esc") or "").strip()
            want = bool(spec) and mode in ("speaking", "thinking")
            if want == stop_state["on"]:
                return
            err = stop_hk.set(spec if want else None)
            if err and not stop_state["warned"]:
                stop_state["warned"] = True
                print(f"[stop key] not registered: {err}")
            stop_state["on"] = want and not err
        bridge.state.connect(stop_key_for)

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

        # M4d: dictate anywhere (its own hold key; a hook that passes everything else on)
        dict_hk = GlobalHotkey(lambda: dictation["d"] and dictation["d"].paste_again())

        def set_paste_again(spec):
            err = dict_hk.set(spec or None)
            print(f"[dictate] paste again: {spec or 'none'}" + (f" (not registered: {err})" if err else ""))

        def start_dictation():
            asst.ready.wait()
            if not cfg.get("dictate", {}).get("enabled", True) or asst.speech is None:
                return
            from .dictate import attach
            try:
                dictation["d"] = attach(asst, bridge, cfg)
                print(f"[dictate] hold {cfg['dictate'].get('key') or 'right alt'} to dictate anywhere "
                      "(double tap: hands-free; Esc cancels)")
            except Exception as e:
                print(f"[dictate] off: {e}")
        threading.Thread(target=start_dictation, daemon=True, name="dictate-start").start()
        set_paste_again(cfg.get("dictate", {}).get("paste_again_hotkey"))

    print(f"[app] log: {log_path}")
    if harness is not None and hasattr(harness, "started"):
        harness.started(dict(app=app, asst=asst, bridge=bridge, island=island, tray=tray, cfg=cfg))
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
    if interactive:
        stop_hk.close()
        if dict_hk is not None:
            dict_hk.close()
    if dictation["d"] is not None:
        dictation["d"].close()
    if asst.hold:
        asst.hold.close()
    try:
        asst.brain.close()
    except Exception:
        pass
    if tray:
        tray.hide()
    if island is not None and hasattr(island, "view"):      # the QML island: stop its timers, close the window
        island.close()
    if instance:
        instance.release()
    if state["restart"]:
        from .config import ROOT
        argv = ["-m", "heyclaude", *sys.argv[1:]]
        ok = QProcess.startDetached(sys.executable, argv, str(ROOT))
        print(f"[app] restarting: {'ok' if ok else 'FAILED'}")
    print("[app] bye")
    if getattr(asst.stt, "downloading", False) or getattr(asst.stt, "_loading", False):
        # A whisper model is still downloading in the background: Python would
        # wait for Hugging Face's download threads at exit. Don't.
        print("[app] leaving a model download unfinished (it resumes next time)")
        for h in __import__("logging").getLogger().handlers:
            h.flush()
        os._exit(code)
    sys.exit(code)


if __name__ == "__main__":
    main()
