"""Settings window: edits config.yaml, one page per section in a sidebar.

Works on a copy of the merged config, so keys it doesn't show (claude.exe,
listen.*, island.*, piper/kokoro paths...) are written back unchanged. Save
writes the file, emits `saved(new_cfg, changed_keys)` and says inline which of
the changes only apply after a restart (with a "Restart now" button).

Looks: heyclaude/settings_theme.py (palette, spacing, type, the one stylesheet)
and heyclaude/settings_kit.py (sidebar, pages, groups, rows, the switch). This
module only says which settings go on which page.
"""
from __future__ import annotations

import copy
import os
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDoubleSpinBox, QFileDialog, QFrame,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QSlider,
                               QSpinBox, QStyledItemDelegate, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from . import config, holdkey, hotkey
from . import settings_kit as kit
from . import settings_theme as th

ACCENT = th.BASE["live"]

WAKE_MODELS = ["models/hey_claude.onnx", "hey_jarvis", "alexa", "hey_mycroft", "hey_rhasspy"]
STT_MODELS = ["tiny.en", "base.en", "small.en", "small", "medium"]
STT_GPU_MODELS = ["large-v3", "large-v3-turbo", "distil-large-v3", "medium", "small"]
CLI_MODELS = ["haiku", "sonnet", "opus"]
PERMISSION_MODES = ["acceptEdits", "auto", "dontAsk", "plan", "bypassPermissions"]

# Tooltips, taken from the comments in config.example.yaml.
TIPS = {
    "island.scale": "How big the island is: the pill, the panel, cards, text and icons together. Everything is "
                    "vector, so it stays sharp at any size. Auto = 100 %, or 125 % on a screen of 27\" or more.",
    "island.motion": "Auto follows Windows' Animation effects. Calm keeps what carries information and drops "
                     "decoration; off is instant.",
    "island.hide_when_idle": "Nothing on screen while it waits for the wake word. Touch the top-centre edge "
                             "with the pointer to peek.",
    "weather.location": "Your home place for \"what's the weather?\" (a city, optionally \", country\"). "
                        "Open-Meteo, no key.",
    "app.listening": "Wake word on at start. Off = the mic stays closed until you turn listening on.",
    "app.hotkey": "Global push-to-talk, e.g. ctrl+alt+space, ctrl+shift+h, win+alt+j, f9. Empty = no hotkey.",
    "app.start_with_windows": "Adds a Startup-folder shortcut (pythonw -m heyclaude, no console window).",
    "listen.hold_key": "Hold while talking to keep listening through pauses; let go and it stops when you stop "
                       "talking. ctrl (either), right ctrl, caps lock, f13... Empty = off. Pressing another key "
                       "during the hold (Ctrl+C...) makes it a shortcut, not a hold.",
    "wake.model": "The trained \"hey claude\" model (models/hey_claude.onnx; see docs/train-hey-claude.md), "
                  "another .onnx path, or a stock openWakeWord name (hey_jarvis, alexa, hey_mycroft, "
                  "hey_rhasspy). A missing file falls back to hey_jarvis at 0.5.",
    "wake.threshold": "How sure the wake-word model must be. Higher = fewer false wakes, but it may miss you. "
                      "tools/wake_eval.py recommends one.",
    "wake.cooldown": "Seconds to ignore the wake word after an interaction ends.",
    "stt.device": "GPU: the GPU model is loaded once and kept in VRAM; falls back to the CPU when CUDA is "
                  "missing, the load fails or runs out of memory, or a transcription hits a CUDA error.\n"
                  "--gpu / --cpu on the command line override this.",
    "stt.gpu_model": "Whisper model on the GPU. large-v3-turbo (default): ~0.25 s per request, ~1.6 GB "
                     "download. large-v3: ~0.4 s, ~2 GB VRAM in int8_float16. tools/stt_eval.py compares "
                     "them on your voice.",
    "stt.gpu_compute_type": "int8_float16 (less VRAM) or float16.",
    "stt.cpu_model": "Whisper model on the CPU (fallback): tiny.en / base.en / small.en / small. "
                     "Loaded only when needed.",
    "stt.yield_to_premiere": "Opt-in: never use the GPU while Adobe Premiere Pro is running, even when it's idle "
                             "(the load check handles a busy Premiere anyway).",
    "stt.gpu_busy_pct": "Use the CPU for a request when the GPU is at least this busy (averaged over ~0.5 s), "
                        "unless the CPU is busier.",
    "stt.cpu_busy_pct": "If the CPU is at least this busy too, stay on the GPU anyway.",
    "fast.backend": "auto = Anthropic SDK if ANTHROPIC_API_KEY is set, otherwise the claude CLI.\n"
                    "cli = always the claude CLI (your subscription login).\nsdk = always the Anthropic SDK.",
    "fast.cli_model": "Model for quick answers through the claude CLI.",
    "fast.sdk_model": "Model id for quick answers through the Anthropic SDK.",
    "agent.enabled": "Questions that need your tools (\"who messaged me on Telegram\", \"what's on my calendar\") "
                     "go to ONE warm Claude Code process, started at launch and reused.",
    "agent.model": "Model for the tool agent; sonnet for harder lookups, at some latency cost.",
    "agent.filler": "Spoken right away while the agent looks things up.",
    "agent.filler_min_words": "Only say the filler when the request has at least this many words (0 = always).",
    "conversation.followup_s": "After an answer, keep listening this long without the wake word, once its own "
                               "voice has finished. \"Thanks\" or silence ends it. 0 = off.",
    "conversation.save": "Save the conversation: turns go to state/conversations/<date>.jsonl and come back "
                         "after a restart, and the tool agent's session is saved as \"hey-claude <date>\". "
                         "Off: nothing is written. Saying \"start fresh\" forgets it either way.",
    "conversation.idle_reset_s": "Start a new conversation after this long without a request.",
    "tasks.model": "Model for headless tasks. Empty = your Claude Code default model.",
    "tasks.permission_mode": "Permission mode for headless tasks (claude -p).",
    "tasks.max_parallel": "How many background tasks may run at once.",
    "tts.engine": "Kokoro (default): natural voice, GPU with a CPU fallback, then Windows voices if it can't "
                  "load. Windows voices (SAPI) can't be talked over (no barge-in). Piper: set its paths in config.yaml.",
    "tts.device": "GPU: picked per reply from GPU load, like speech-to-text (uses the CPU while the GPU is busy). "
                  "CPU: always the CPU (slower first word, ~0.5-0.8 s).",
    "tts.voice": "Kokoro voice. a = American, b = British; f = female, m = male. Applies from the next sentence.",
    "tts.speed": "Kokoro speaking speed (1.0 = normal).",
    "tts.rate": "SAPI speaking rate in words per minute.",
    "tts.sapi_voice": "SAPI voice name substring, e.g. \"Zira\". Empty = the Windows default voice.",
    "instant.enabled": "Simple commands (\"volume 30\", \"mute\", \"dark mode\", \"next song\", \"set a timer for 5 "
                       "minutes\", \"what time is it\") run here in about a millisecond, with the same island effect "
                       "and \"undo that\" as when Claude does them. Anything it isn't sure about still goes to Claude.",
    "instant.ack": "Speak: a short ack (\"Volume 30.\"). Effect only: just the island effect. Answers (the time, "
                   "what's playing) and errors are always spoken.",
    "instant.step": "How much \"turn it up\" changes the volume or brightness. \"A bit\" is half of it, \"a lot\" "
                    "two and a half times.",
    "instant.context_s": "\"A bit more\", \"again\", \"the other way\", \"make it 40\" apply to the last instant "
                         "command if it was this recent.",
    "instant.radios": "\"Wi-Fi off\" / \"Bluetooth on\" instantly. Off: those go to Claude.",
    "instant.classifier": "A small local classifier for wordings the grammar doesn't know (\"my ears hurt, it's too "
                          "loud\"). English only. Off: the grammar alone.",
    "instant.threshold": "How close to one of the classifier's example phrasings a request must be. Higher = "
                         "stricter (more goes to Claude). The grammar ignores this.",
    "instant.learn": "When Claude handles a request with exactly one settings change, the wording is noted in "
                     "state/intents_misses.jsonl (on this PC only). Say \"teach it\" right after to make it instant.",
}
BYPASS_TIP = ("bypassPermissions skips every permission check: tasks can run any command and edit any file "
              "without asking. Only use it in folders you can afford to lose.")

HOTKEY_HINT = "e.g. ctrl+alt+space"

# One line under a setting, where its name alone doesn't say enough. The longer story stays in TIPS
# (the tooltip on the setting's name and control).
DESC = {
    "app.listening": "Off: the mic stays closed until you turn listening on.",
    "app.start_with_windows": "Adds a shortcut to your Startup folder.",
    "listen.hold_key": "Hold it while you talk, so a pause doesn't end your turn. Empty: off.",
    "weather.location": "For \"what's the weather?\". Empty: say a place when you ask.",
    "island.motion": "Calm keeps what carries meaning and drops decoration. Off is instant.",
    "island.hide_when_idle": "Nothing on screen while it waits. Touch the top edge with the pointer to peek.",
    "wake.threshold": "Higher: fewer false wakes, but it may miss you.",
    "wake.cooldown": "Ignore the wake word this long after an answer.",
    "wake.model": "The trained \"hey claude\" model, another .onnx file, or a built-in name like hey_jarvis.",
    "speech.backend": "Also in the tray menu.",
    "speech.cloud_stt_provider": "Only used when speech runs in the cloud.",
    "speech.cloud_tts_provider": "The voice used when speech runs in the cloud.",
    "stt.device": "The GPU is much faster; the CPU takes over when the GPU is missing or busy.",
    "stt.gpu_busy_pct": "Use the CPU for a request when the GPU is at least this busy.",
    "stt.cpu_busy_pct": "If the CPU is this busy too, stay on the GPU anyway.",
    "stt.yield_to_premiere": "Even when Premiere is idle.",
    "tts.voice": "a = American, b = British; f = female, m = male.",
    "tts.device": "GPU: picked per reply from GPU load. CPU: always the CPU.",
    "tts.rate": "For Windows voices.",
    "dictate.key": "Hold it to dictate. Double tap for hands-free; tap again to finish.",
    "dictate.paste_again_hotkey": "Types the last dictation again. Empty: none.",
    "dictate.cleanup": "Adds a second or two. Per app: dictate.cleanup_apps in config.yaml.",
    "instant.ack": "Answers, like the time, and errors are always spoken.",
    "instant.step": "\"A bit\" is half of it, \"a lot\" two and a half times.",
    "instant.context_s": "\"A bit more\", \"again\" and \"make it 40\" apply to the last command this recent.",
    "instant.threshold": "Higher: stricter, more goes to Claude.",
    "fast.backend": "The SDK needs ANTHROPIC_API_KEY; the CLI uses your subscription login.",
    "agent.model": "Sonnet for harder lookups, at some latency cost.",
    "agent.filler": "Said right away while the agent looks things up.",
    "agent.filler_min_words": "Shorter requests get no filler. 0: always.",
    "conversation.followup_s": "Keep listening this long after an answer, without the wake word. 0: off.",
    "conversation.idle_reset_s": "After this long without a request.",
    "tasks.model": "Empty: your Claude Code default.",
    "tasks.max_parallel": "How many background tasks may run at once.",
}


def _get(cfg: dict, dotted: str, default=None):
    cur = cfg
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def _set(cfg: dict, dotted: str, value):
    parts = dotted.split(".")
    cur = cfg
    for part in parts[:-1]:
        if not isinstance(cur.get(part), dict):
            cur[part] = {}
        cur = cur[part]
    cur[parts[-1]] = value


def _vk_names() -> dict[int, str]:
    """Windows virtual-key code -> our canonical key name (from hotkey.py's table)."""
    keys = getattr(hotkey, "_KEYS", {})
    aliases = getattr(hotkey, "_ALIASES", {})
    out: dict[int, str] = {}
    for name, vk in keys.items():
        canon = aliases.get(name, name)
        out.setdefault(vk, canon)
    return out


_QT_KEYS = {
    Qt.Key_Space: "space", Qt.Key_Return: "enter", Qt.Key_Enter: "enter", Qt.Key_Tab: "tab",
    Qt.Key_Backspace: "backspace", Qt.Key_Delete: "delete", Qt.Key_Insert: "insert", Qt.Key_Home: "home",
    Qt.Key_End: "end", Qt.Key_PageUp: "pageup", Qt.Key_PageDown: "pagedown", Qt.Key_Left: "left",
    Qt.Key_Up: "up", Qt.Key_Right: "right", Qt.Key_Down: "down", Qt.Key_Pause: "pause",
    Qt.Key_Print: "printscreen", Qt.Key_ScrollLock: "scrolllock", Qt.Key_CapsLock: "capslock",
    Qt.Key_QuoteLeft: "`", Qt.Key_Minus: "-", Qt.Key_Equal: "=", Qt.Key_BracketLeft: "[",
    Qt.Key_BracketRight: "]", Qt.Key_Backslash: "\\", Qt.Key_Semicolon: ";", Qt.Key_Apostrophe: "'",
    Qt.Key_Comma: ",", Qt.Key_Period: ".", Qt.Key_Slash: "/",
}
_MOD_KEYS = {Qt.Key_Control, Qt.Key_Alt, Qt.Key_Shift, Qt.Key_Meta, Qt.Key_Super_L, Qt.Key_Super_R,
             Qt.Key_AltGr}


def key_event_to_text(e) -> str | None:
    """A QKeyEvent -> 'ctrl+alt+space' style text, or None for a lone modifier / unknown key."""
    k = e.key()
    if k in _MOD_KEYS:
        return None
    name = None
    vk = e.nativeVirtualKey() if hasattr(e, "nativeVirtualKey") else 0
    if vk:
        name = _vk_names().get(vk)
    if name is None:
        if k in _QT_KEYS:
            name = _QT_KEYS[k]
        elif Qt.Key_A <= k <= Qt.Key_Z:
            name = chr(k).lower()
        elif Qt.Key_0 <= k <= Qt.Key_9:
            name = (f"num{chr(k)}" if e.modifiers() & Qt.KeypadModifier else chr(k))
        elif Qt.Key_F1 <= k <= Qt.Key_F24:
            name = f"f{k - Qt.Key_F1 + 1}"
    if name is None:
        return None
    m = e.modifiers()
    mods = [n for flag, n in ((Qt.ControlModifier, "ctrl"), (Qt.AltModifier, "alt"),
                              (Qt.ShiftModifier, "shift"), (Qt.MetaModifier, "win")) if m & flag]
    return "+".join(mods + [name])


class _HotkeyRecorder(QObject):
    """While active, swallows key presses on a QLineEdit and writes the combo into it."""

    def __init__(self, edit: QLineEdit, on_done):
        super().__init__(edit)
        self.edit, self.on_done = edit, on_done
        self.active = False
        self._before = ""

    def start(self):
        self._before = self.edit.text()
        self.active = True
        self.edit.setReadOnly(True)
        self.edit.setProperty("recording", True)
        self.edit.style().unpolish(self.edit)
        self.edit.style().polish(self.edit)
        self.edit.clear()
        self.edit.setPlaceholderText("Press the keys…")
        self.edit.installEventFilter(self)
        self.edit.setFocus(Qt.OtherFocusReason)

    def stop(self, text: str | None):
        if not self.active:
            return
        self.active = False
        self.edit.removeEventFilter(self)
        self.edit.setReadOnly(False)
        self.edit.setProperty("recording", False)
        self.edit.style().unpolish(self.edit)
        self.edit.style().polish(self.edit)
        self.edit.setPlaceholderText(HOTKEY_HINT)
        self.edit.setText(self._before if text is None else text)
        self.on_done()

    def eventFilter(self, obj, ev):
        t = ev.type()
        if t == QEvent.ShortcutOverride:
            ev.accept()
            return True
        if t == QEvent.KeyPress:
            if ev.key() == Qt.Key_Escape and not ev.modifiers():
                self.stop(None)
                return True
            text = key_event_to_text(ev)
            if text:
                self.stop(text)
            return True
        if t == QEvent.KeyRelease:
            return True
        if t == QEvent.FocusOut:
            self.stop(None)
        return False


class SettingsWindow(QWidget):
    saved = Signal(dict, list)       # (new full config, changed dotted keys)
    island_preview = Signal(object)  # a scale to show on the island now (float), or None = back to the saved one
    restart_requested = Signal()
    character_preview = Signal(object)   # M6: a pack id to show on the island now, or None = the saved one
    character_play = Signal(str)         # M6: "play now" an antic on the island

    def __init__(self, cfg: dict, config_path=None, parent=None, wake_hooks=None, state_dir=None):
        super().__init__(parent, Qt.Window)
        self.config_path = config_path
        self.state_dir = Path(state_dir) if state_dir else config.STATE_DIR   # taught phrasings (M10)
        self.wake_hooks = wake_hooks          # __main__._WakeHooks (pause/resume listening, GPU load)
        self._wizard = None
        self._orig: dict = copy.deepcopy(cfg)
        self._pending_restart: list[str] = []
        self._tts_test = None
        self._cur_page: kit.Page | None = None
        self._sized = False
        th.T.update(th.tokens(th.load_palette()))
        th.set_motion(_get(cfg, "island.motion", "auto"))
        self.setObjectName("settingsWindow")
        self.setWindowTitle("hey-claude settings")
        self.setStyleSheet(th.stylesheet())
        self.setFont(th.font("body"))
        self.setMinimumSize(760, 480)
        self.resize(1000, 760)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.sidebar = kit.Sidebar("hey-claude", "Settings")
        root.addWidget(kit.sidebar_scroll(self.sidebar))
        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)
        self.tabs = kit.PageStack(self.sidebar)
        right.addWidget(self.tabs, 1)
        root.addLayout(right, 1)

        self._build_general()
        self._build_island()
        self._build_character()
        self._build_wake()
        self._build_voice()
        self._build_dictation()
        self._build_instant()
        self._build_models()
        self._build_projects()
        self._build_permissions()
        from .hooks_settings import SessionsTab
        self.sessions_tab = SessionsTab(self)       # M7: Claude Code hooks, approvals
        # A "Plugins" page goes last, in its own sidebar section: one kit.Group of list rows
        # (kit.SettingRow(name, line, (kit.Switch(),), kind="trailing", icon=pixmap)), and
        # row.set_details(widget) for a plugin with settings of its own.

        # Bottom: status line + Restart now + Save / Close.
        footer = QFrame()
        footer.setObjectName("footer")
        bottom = QHBoxLayout(footer)
        bottom.setContentsMargins(th.PAGE_PAD[0], th.S[3], th.S[5], th.S[3])
        bottom.setSpacing(th.S[2])
        self.status = QLabel("")
        self.status.setObjectName("status")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.RichText)
        self.btn_restart = kit.button("Restart now", "secondary", lambda: self.restart_requested.emit())
        self.btn_restart.hide()
        self.btn_save = kit.button("Save", "primary", lambda: self.save())
        self.btn_save.setDefault(True)
        self.btn_save.setMinimumWidth(88)
        self.btn_close = kit.button("Close", "secondary", self.close)
        self.btn_close.setMinimumWidth(88)
        bottom.addWidget(self.status, 1)
        bottom.addWidget(self.btn_restart)
        bottom.addSpacing(th.S[2])
        bottom.addWidget(self.btn_save)
        bottom.addWidget(self.btn_close)
        right.addWidget(footer)

        self._fill(self._orig)

    def showEvent(self, e):
        """The first time: no bigger than the screen (at 150 / 200 % scaling 1000 x 760 may not fit)."""
        if not self._sized:
            self._sized = True
            scr = self.screen() or QGuiApplication.primaryScreen()
            if scr is not None:
                av = scr.availableGeometry()
                w = min(self.width(), int(av.width() * 0.92))
                h = min(self.height(), int(av.height() * 0.92))
                if (w, h) != (self.width(), self.height()):
                    self.resize(max(w, self.minimumWidth()), max(h, self.minimumHeight()))
        super().showEvent(e)

    # -- building blocks ------------------------------------------------------
    def _page(self, title: str, subtitle: str, glyph: str, section: str | None = None,
              nav: str | None = None) -> kit.Page:
        p = kit.Page(title, subtitle)
        self.tabs.addTab(p, nav or title, glyph, section)
        self._cur_page = p
        return p

    def _row(self, group: kit.Group, key: str, title: str, *widgets: QWidget, desc: str | None = None,
             kind: str = "field") -> kit.SettingRow:
        """One setting: `title` (+ a restart tag when `key` needs one), a description line
        (DESC[key] unless given) and its control(s). The tooltip is TIPS[key]."""
        row = kit.SettingRow(title, DESC.get(key, "") if desc is None else desc, widgets, kind=kind,
                             tag=bool(key) and bool(config.needs_restart([key])), tip=TIPS.get(key, ""))
        group.add(row)
        if self._cur_page is not None:
            self._cur_page.register(row)
        return row

    def _switch(self, group: kit.Group, key: str, title: str, desc: str | None = None) -> kit.Switch:
        sw = kit.Switch()
        sw.row = self._row(group, key, title, sw, desc=desc, kind="trailing")
        return sw

    @staticmethod
    def _help(text: str) -> QLabel:
        lab = QLabel(text)
        lab.setObjectName("note")
        lab.setWordWrap(True)
        return lab

    @staticmethod
    def _readout(text: str = "", width: int = kit.READOUT_W) -> QLabel:
        lab = QLabel(text)
        lab.setObjectName("value")
        lab.setFixedWidth(width)
        lab.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        return lab

    @staticmethod
    def _combo(items, editable=False) -> QComboBox:
        c = QComboBox()
        c.setEditable(editable)
        c.setItemDelegate(QStyledItemDelegate(c))          # lets the stylesheet size the popup's rows
        c.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        c.setMinimumContentsLength(8)
        if editable:
            c.setInsertPolicy(QComboBox.NoInsert)
        for it in items:
            if isinstance(it, tuple):
                c.addItem(it[0], it[1])
            else:
                c.addItem(it, it)
        return c

    @staticmethod
    def _set_combo(c: QComboBox, value):
        """Select `value` (matched on item data); unknown values are added so they round-trip."""
        value = "" if value is None else str(value)
        i = c.findData(value)
        if i < 0 and c.isEditable():
            c.setCurrentText(value)
            return
        if i < 0:
            c.addItem(value, value)
            i = c.count() - 1
        c.setCurrentIndex(i)

    @staticmethod
    def _combo_value(c: QComboBox) -> str:
        if c.isEditable():
            # Editable combos: what's typed, unless it's an item's label (then that item's data).
            text = c.currentText().strip()
            i = c.findText(text)
            return str(c.itemData(i)) if i >= 0 and c.itemData(i) is not None else text
        return str(c.currentData())

    @staticmethod
    def _text_box(placeholder: str = "", height: int = 96) -> QPlainTextEdit:
        e = QPlainTextEdit()
        e.setPlaceholderText(placeholder)
        e.setFixedHeight(height)                 # in a scrolling page a box only gets its own height
        e.setTabChangesFocus(True)
        return e

    @staticmethod
    def _table(headers: list[str]) -> QTableWidget:
        t = QTableWidget(0, len(headers))
        t.setObjectName("flush")
        t.setHorizontalHeaderLabels(headers)
        t.horizontalHeader().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        t.horizontalHeader().setHighlightSections(False)
        t.verticalHeader().hide()
        t.verticalHeader().setDefaultSectionSize(34)
        t.setShowGrid(False)
        t.setFrameShape(QFrame.NoFrame)
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
        t.setSelectionMode(QAbstractItemView.SingleSelection)
        t.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        return t

    # -- pages ----------------------------------------------------------------
    def _build_general(self):
        p = self._page("General", "How hey-claude starts, and how you call it.", "general", section="")
        g = p.add_group("Startup")
        self.cb_listening = self._switch(g, "app.listening", "Listen for the wake word at start")
        self.cb_autostart = self._switch(g, "app.start_with_windows", "Start with Windows")

        g = p.add_group("Push-to-talk")
        self.ed_hotkey = QLineEdit()
        self.ed_hotkey.setPlaceholderText(HOTKEY_HINT)
        self.btn_record = kit.button("Record", "secondary", self._record_hotkey,
                                     tip="Click, then press the key combination. Esc cancels.")
        self.btn_record.setFocusPolicy(Qt.NoFocus)   # don't steal focus from the field being recorded
        # the clear "x" sits inside the field, at its right end (shown only when there's something to clear)
        self.btn_hk_clear = self.ed_hotkey.addAction(th.glyph_icon("clear", th.T["dim"], th.T["fg"], 12),
                                                     QLineEdit.TrailingPosition)
        self.btn_hk_clear.setToolTip("No hotkey")
        self.btn_hk_clear.triggered.connect(lambda: self.ed_hotkey.setText(""))
        row = self._row(g, "app.hotkey", "Push-to-talk hotkey", self.ed_hotkey, self.btn_record, desc=" ")
        self.lb_hotkey = row.desc_label              # says what will be saved, or what's wrong
        self._recorder = _HotkeyRecorder(self.ed_hotkey, self._hotkey_changed)
        self.ed_hotkey.textChanged.connect(self._hotkey_changed)
        self.ed_hold = QLineEdit()
        self.ed_hold.setPlaceholderText("e.g. ctrl, right ctrl, caps lock")
        self._row(g, "listen.hold_key", "Hold to keep listening", self.ed_hold)

        g = p.add_group("Weather")
        self.ed_weather = QLineEdit()
        self.ed_weather.setPlaceholderText("e.g. Tashkent")
        self._row(g, "weather.location", "Home place", self.ed_weather)

        note = QLabel(f"Settings marked <span style='color:{th.T['accent_text']}'>restart</span> take effect the next "
                      "time hey-claude starts; everything else applies when you save. Settings not shown here "
                      "(listen timings, the island's position, the claude.exe path, voice model paths…) can be "
                      "edited in config.yaml by hand.")
        note.setObjectName("note")
        note.setWordWrap(True)
        note.setTextFormat(Qt.RichText)
        p.add(note)

    def _build_island(self):
        p = self._page("Island", "How the island at the top of the screen looks and moves.", "island")
        g = p.add_group("Size")
        self.cb_scale_auto = self._switch(g, "island.scale", "Automatic size",
                                          desc="100 %, or 125 % on a screen of 27 inches or more.")
        self.sl_scale = QSlider(Qt.Horizontal)
        self.sl_scale.setRange(16, 40)                 # x5 %: 80 % .. 200 %
        self.sl_scale.setPageStep(1)
        self.lb_scale = self._readout("100 %")
        self.row_scale = self._row(g, "island.scale", "Size", self.sl_scale, self.lb_scale,
                                   desc="Shown on the island while you drag; kept when you save.")
        self.sl_scale.valueChanged.connect(self._scale_changed)
        self.cb_scale_auto.toggled.connect(self._scale_changed)

        g = p.add_group("Motion and visibility")
        self.cmb_motion = self._combo([("Follow Windows", "auto"), ("Full", "full"), ("Calm", "calm"), ("Off", "off")])
        self._row(g, "island.motion", "Motion", self.cmb_motion)
        # The island's frame rate (auto / 30 / 60 / display rate) goes here, as a field row like Motion.
        self.cb_hide_idle = self._switch(g, "island.hide_when_idle", "Hide when idle")

    def _build_character(self):
        """M6: Settings > Character (heyclaude/character_settings.py)."""
        from .character_settings import CharacterTab
        p = self._page("Character", "Give the island a face, and choose what it does when nothing is happening.",
                       "character")
        self.char_tab = CharacterTab()
        self.char_tab.preview_pack.connect(self.character_preview.emit)
        self.char_tab.play_antic.connect(self.character_play.emit)
        p.add(self.char_tab)
        for r in self.char_tab.field_rows:
            p.register(r)

    def _build_wake(self):
        p = self._page("Wake word", "What wakes hey-claude up, and how easily.", "wake", section="Speech")
        g = p.add_group("Model")
        self.lb_wake_now = QLabel("")
        self.lb_wake_now.setObjectName("value")
        self.lb_wake_now.setWordWrap(True)
        self._row(g, "", "In use now", self.lb_wake_now, desc="")
        self.cmb_wake = self._combo(WAKE_MODELS, editable=True)
        self.btn_wake_browse = kit.button("Browse…", "secondary", self._browse_wake)
        self._row(g, "wake.model", "Model", self.cmb_wake, self.btn_wake_browse, kind="stacked")
        self.sp_threshold = QDoubleSpinBox(minimum=0.05, maximum=0.95, singleStep=0.05, decimals=2)
        self._row(g, "wake.threshold", "Threshold", self.sp_threshold)
        self.sp_cooldown = QDoubleSpinBox(minimum=0.0, maximum=30.0, singleStep=0.5, decimals=2, suffix=" s")
        self._row(g, "wake.cooldown", "Cooldown", self.sp_cooldown)

        g = p.add_group("Personal tune-up", "Optional. Trains a model on your voice, here on this PC, in about "
                                            "15 minutes.")
        g.add_note("The shared \"hey claude\" model works for most voices. Tune it if it often misses you, or to "
                   "use a different phrase. Your recordings and the model stay in the state folder.")
        self.btn_wizard = kit.button("Tune it to my voice…", "primary", self._open_wizard)
        self.btn_wake_default = kit.button("Use the shared default", "secondary", self._use_default_wake)
        self.btn_wake_undo = kit.button("Undo the last change", "ghost", self._undo_wake)
        g.add_widget(kit.buttons_row(self.btn_wizard, self.btn_wake_default, self.btn_wake_undo))
        self.lb_false_wakes = g.add_note()

    def _build_voice(self):
        p = self._page("Voice", "How hey-claude hears you, and how it sounds.", "voice")
        self._build_speech(p)
        g = p.add_group("Speech to text", "Whisper, on this PC.")
        self.cmb_stt_device = self._combo([("GPU, CPU as fallback", "gpu"), ("CPU only", "cpu")])
        self._row(g, "stt.device", "Device", self.cmb_stt_device)
        self.cmb_stt_gpu_model = self._combo(STT_GPU_MODELS, editable=True)
        self._row(g, "stt.gpu_model", "GPU model", self.cmb_stt_gpu_model)
        self.cmb_stt_gpu_compute = self._combo(["int8_float16", "float16"])
        self._row(g, "stt.gpu_compute_type", "GPU precision", self.cmb_stt_gpu_compute,
                  desc="int8_float16 uses less video memory.")
        self.cmb_stt_model = self._combo(STT_MODELS, editable=True)
        self._row(g, "stt.cpu_model", "CPU model", self.cmb_stt_model, desc="Loaded only when needed.")
        self.sp_gpu_busy = QSpinBox(minimum=10, maximum=100, singleStep=5, suffix=" %")
        self._row(g, "stt.gpu_busy_pct", "GPU counts as busy at", self.sp_gpu_busy)
        self.sp_cpu_busy = QSpinBox(minimum=10, maximum=100, singleStep=5, suffix=" %")
        self._row(g, "stt.cpu_busy_pct", "CPU counts as busy at", self.sp_cpu_busy)
        self.cb_premiere = self._switch(g, "stt.yield_to_premiere", "Leave the GPU to Premiere Pro")

        g = p.add_group("Voice")
        from .tts import KOKORO_VOICES
        self.cmb_tts_backend = self._combo([("Kokoro", "kokoro"), ("Windows voices", "sapi"), ("Piper", "piper")])
        self._row(g, "tts.engine", "Engine", self.cmb_tts_backend,
                  desc="Windows voices can't be talked over. Piper: set its paths in config.yaml.")
        self.cmb_tts_device = self._combo([("GPU (CPU when busy)", "gpu"), ("CPU", "cpu")])
        self._row(g, "tts.device", "Device", self.cmb_tts_device)
        self.cmb_kokoro_voice = self._combo([(x, x) for x in KOKORO_VOICES], editable=True)
        self._row(g, "tts.voice", "Kokoro voice", self.cmb_kokoro_voice)
        self.sp_tts_speed = QDoubleSpinBox(minimum=0.5, maximum=2.0, singleStep=0.05, decimals=2, suffix=" ×")
        self._row(g, "tts.speed", "Kokoro speed", self.sp_tts_speed)
        self.cmb_voice = self._combo([("Windows default", "")] + [(d, d) for d in self._sapi_voices()],
                                     editable=True)
        self.btn_test_voice = kit.button("Test", "secondary", self._test_voice, tip="Say a sentence in this voice.")
        self._row(g, "tts.sapi_voice", "Windows voice", self.cmb_voice, self.btn_test_voice)
        self.sp_rate = QSpinBox(minimum=80, maximum=400, singleStep=10, suffix=" wpm")
        self._row(g, "tts.rate", "Windows voice speed", self.sp_rate)

    # -- M4d: dictation and the personal dictionary --------------------------------------
    CLEANUP = [("Off (fastest)", "off"), ("As said: punctuation only", "as_said"),
               ("Natural: drop um, uh and false starts", "natural"), ("Polished", "polished")]

    def _build_dictation(self):
        p = self._page("Dictation", "Hold a key, speak, let go: it types where the cursor is.", "dictation")
        g = p.add_group("Dictate anywhere")
        self.cb_dictate = self._switch(g, "dictate.enabled", "Dictation",
                                       desc="Your clipboard is put back after each paste; nothing is typed into "
                                            "password fields.")
        self.ed_dictate_key = QLineEdit()
        self.ed_dictate_key.setPlaceholderText("right alt, caps lock, f13…")
        self._row(g, "dictate.key", "Key", self.ed_dictate_key)
        self.ed_paste_again = QLineEdit()
        self.ed_paste_again.setPlaceholderText("e.g. win+alt+v")
        self._row(g, "dictate.paste_again_hotkey", "Paste again", self.ed_paste_again)
        self.cmb_cleanup = self._combo(self.CLEANUP)
        self._row(g, "dictate.cleanup", "Clean-up by Claude", self.cmb_cleanup)
        self.lbl_dictate_stats = g.add_note()

        g = p.add_group("Personal dictionary", "Used by dictation and by the assistant.")
        self.ed_dict_words = self._text_box("One per line")
        self._row(g, "dictionary.words", "Words", self.ed_dict_words, kind="stacked",
                  desc="Names, course names, words it gets wrong.")
        self.ed_dict_repl = self._text_box("hey cloud => hey Claude")
        self._row(g, "dictionary.replacements", "Replacements", self.ed_dict_repl, kind="stacked",
                  desc="heard => meant, one per line.")
        self.ed_dict_snip = self._text_box("my address => 12 Main St\\nTashkent")
        self._row(g, "dictionary.snippets", "Snippets", self.ed_dict_snip, kind="stacked",
                  desc="spoken phrase => text, one per line; \\n for a new line.")

    @staticmethod
    def _pairs_text(d: dict) -> str:
        return "\n".join(f"{k} => {str(v).replace(chr(10), chr(92) + 'n')}" for k, v in (d or {}).items())

    @staticmethod
    def _pairs(edit: QPlainTextEdit) -> tuple[dict, list[str]]:
        out, bad = {}, []
        for line in edit.toPlainText().splitlines():
            if not line.strip():
                continue
            if "=>" not in line:
                bad.append(line.strip())
                continue
            k, v = line.split("=>", 1)
            if k.strip():
                out[k.strip()] = v.strip().replace("\\n", "\n")
        return out, bad

    def _dictate_stats(self):
        import json
        try:
            st = json.loads((config.STATE_DIR / "dictation.json").read_text(encoding="utf-8"))
            self.lbl_dictate_stats.setText(f"{st.get('dictations', 0)} dictations, {st.get('words', 0)} words, "
                                           f"about {float(st.get('saved_s', 0)) / 60:.0f} min saved vs typing "
                                           "(kept on this PC only).")
        except Exception:
            self.lbl_dictate_stats.setText("No dictations yet.")

    def _build_instant(self):
        """M10: simple commands run locally, no model (heyclaude/intents, config `instant:`)."""
        p = self._page("Instant commands", "Simple commands run here in about a millisecond, without Claude, with "
                       "the same island effect and \"undo that\".", "instant", nav="Instant")
        g = p.add_group("Instant commands")
        self.cb_instant = self._switch(g, "instant.enabled", "Run simple commands locally",
                                       desc="\"Volume 30\", \"mute\", \"dark mode\", \"next song\", \"timer for 5 "
                                            "minutes\". Anything it isn't sure about still goes to Claude.")
        self.cmb_instant_ack = self._combo([("Speak a short ack (\"Volume 30.\")", "speak"),
                                            ("Effect only", "silent")])
        self._row(g, "instant.ack", "Acknowledge", self.cmb_instant_ack)
        self.sp_instant_step = QSpinBox(minimum=2, maximum=50, singleStep=1, suffix=" %")
        self._row(g, "instant.step", "\"Turn it up\" step", self.sp_instant_step)
        self.sp_instant_context = QSpinBox(minimum=0, maximum=900, singleStep=15, suffix=" s")
        self._row(g, "instant.context_s", "Follow-ups for", self.sp_instant_context)
        self.cb_instant_radios = self._switch(g, "instant.radios", "Wi-Fi and Bluetooth on and off",
                                              desc="Off: those go to Claude.")
        g.add_note("Russian and Uzbek wordings work too when speech recognition writes them (stt.language in "
                   "config.yaml: empty to auto-detect). Sleep, restart and shutdown are never instant.")

        g = p.add_group("Looser wordings")
        self.cb_instant_clf = self._switch(g, "instant.classifier", "Catch looser wordings",
                                           desc="Like \"my ears hurt, it's too loud\". English only.")
        self.sl_instant_threshold = QSlider(Qt.Horizontal)
        self.sl_instant_threshold.setRange(50, 90)                  # x0.01: 0.50 .. 0.90
        self.sl_instant_threshold.setPageStep(2)
        self.lb_instant_threshold = self._readout("0.62", 104)
        self.row_instant_threshold = self._row(g, "instant.threshold", "Strictness", self.sl_instant_threshold,
                                               self.lb_instant_threshold)
        self.sl_instant_threshold.valueChanged.connect(self._instant_threshold_changed)
        self.cb_instant_clf.toggled.connect(self._instant_threshold_changed)

        g = p.add_group("Teach it", "Say \"teach it\" right after Claude did something, and that wording is "
                                    "instant from then on.")
        self.cb_instant_learn = self._switch(g, "instant.learn", "Note wordings Claude handled",
                                             desc="Only ones done with a single settings change; kept on this PC.")
        g.add_note("Ask for something the long way (\"give the boombox some juice\"); when Claude has done it, say "
                   "\"teach it\" (or \"запомни это\", \"buni eslab qol\"). Taught wordings are kept in "
                   "state/intents_taught.yaml.")
        self.tbl_taught = self._table(["Wording", "Means"])
        self.tbl_taught.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tbl_taught.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tbl_taught.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_taught.setMinimumHeight(140)
        g.add_widget(self.tbl_taught, pad=False, vpad=0)
        self.btn_taught_remove = kit.button("Remove", "danger", self._remove_taught,
                                            tip="Forget the selected wording now (it goes back to Claude).")
        self.lb_taught = QLabel("")
        self.lb_taught.setObjectName("note")
        bar = kit.buttons_row(self.lb_taught, None, self.btn_taught_remove, stretch_end=False)
        g.add_widget(bar)
        self.tbl_taught.itemSelectionChanged.connect(
            lambda: self.btn_taught_remove.setEnabled(self.tbl_taught.currentRow() >= 0))

    def _instant_threshold_changed(self, *_):
        val = self.sl_instant_threshold.value() / 100
        word = "strict" if val >= 0.75 else "loose" if val < 0.58 else "balanced"
        self.lb_instant_threshold.setText(f"{val:.2f} ({word})")
        self.sl_instant_threshold.setEnabled(self.cb_instant_clf.isChecked())
        self.row_instant_threshold.setEnabled(self.cb_instant_clf.isChecked())

    def _load_taught(self):
        """The wordings taught by voice, read-only, with Remove."""
        from .intents import learn
        from .intents.executors import describe_intent
        rows = learn.load_taught(self.state_dir)
        self.tbl_taught.setRowCount(0)
        for x in rows:
            r = self.tbl_taught.rowCount()
            self.tbl_taught.insertRow(r)
            self.tbl_taught.setItem(r, 0, QTableWidgetItem(str(x["text"])))
            self.tbl_taught.setItem(r, 1, QTableWidgetItem(describe_intent(x["intent"], x.get("slots") or {})))
        n_miss = len(learn.misses(self.state_dir))
        self.lb_taught.setText(
            (f"{len(rows)} taught" if rows else "Nothing taught yet") +
            (f"; {n_miss} wording{'s' if n_miss != 1 else ''} noted in state/intents_misses.jsonl." if n_miss else "."))
        self.btn_taught_remove.setEnabled(False)

    def _remove_taught(self):
        from .intents import learn
        r = self.tbl_taught.currentRow()
        if r < 0:
            return
        text = self._taught_cell(r)
        if learn.remove_taught(self.state_dir, text):
            self._set_status(f"Forgot \"{text}\" (applies now).", "ok")
        self._load_taught()

    def _taught_cell(self, r: int) -> str:
        it = self.tbl_taught.item(r, 0)
        return it.text() if it else ""

    # -- M4c: speech backends ---------------------------------------------------------
    SPEECH_BACKENDS = [("Local: Whisper and Kokoro on this PC", "local"), ("Windows: no models", "windows"),
                       ("Cloud: Groq or OpenAI", "cloud"), ("Auto: local while memory is free", "auto")]

    def _build_speech(self, p: kit.Page):
        g = p.add_group("Where speech runs")
        self.cmb_speech = self._combo(self.SPEECH_BACKENDS)
        self.cmb_speech.currentIndexChanged.connect(self._speech_status)
        self._row(g, "speech.backend", "Speech runs", self.cmb_speech)
        self.cb_lean = self._switch(g, "speech.lean", "Free memory when idle",
                                    desc="Unloads Whisper and Kokoro; the next request loads them again.")
        self.sp_unload = QSpinBox(minimum=1, maximum=240, suffix=" min")
        self.row_unload = self._row(g, "speech.lean", "Unload after", self.sp_unload, desc="")
        self.cb_lean.toggled.connect(self.row_unload.setEnabled)
        self.cmb_cloud_stt = self._combo([("Groq: whisper-large-v3-turbo", "groq"),
                                          ("OpenAI: gpt-4o-mini-transcribe", "openai")])
        self.cmb_cloud_stt.currentIndexChanged.connect(self._speech_status)
        self._row(g, "speech.cloud_stt_provider", "Cloud speech to text", self.cmb_cloud_stt)
        self.btn_key = kit.button("Set key…", "secondary", self._set_cloud_key)
        self._row(g, "", "Cloud key", self.btn_key, kind="trailing",
                  desc="Kept in the Windows credential store, never in config.yaml.")
        self.cmb_cloud_tts = self._combo([("Windows voice (free)", ""), ("OpenAI", "openai"),
                                          ("ElevenLabs", "elevenlabs")])
        self._row(g, "speech.cloud_tts_provider", "Cloud voice", self.cmb_cloud_tts)
        self.lbl_speech = g.add_note(rich=True)

    def _speech_status(self, *_):
        """Key source, Windows online recognition, cost estimate and where audio goes."""
        t = th.T
        lines = []
        backend = self._combo_value(self.cmb_speech)
        prov = self._combo_value(self.cmb_cloud_stt) or "groq"
        try:
            from .speech import cloud
            src = cloud.key_source(prov)
            lines.append(f"{prov.title()} key: " + ({"env": f"from {cloud.ENV_VARS[prov]}", "credential store":
                                                     "in the Windows credential store"}.get(src) or
                                                    f"none (set {cloud.ENV_VARS[prov]} or use Set key…)"))
            model = _get(self._orig, "speech.groq_model" if prov == "groq" else "speech.openai_stt_model",
                         "whisper-large-v3-turbo" if prov == "groq" else "gpt-4o-mini-transcribe")
            logs = sorted(str(p) for p in config.STATE_DIR.glob("latency*.csv"))
            lines.append("Cloud cost: " + cloud.cost_line(logs, prov, model))
        except Exception as e:
            lines.append(f"Cloud: {e}")
        try:
            from .speech.windows import online_speech_enabled
            on = online_speech_enabled()
            lines.append("Windows online speech recognition: " + {True: "on", False:
                         f"<span style='color:{t['fg']}'>off</span> (Settings › Privacy &amp; security › Speech); "
                         "the offline recognizer is used", None: "unknown"}[on])
        except Exception:
            pass
        where = {"local": "Audio never leaves this PC.",
                 "windows": "Windows online recognition sends your audio to Microsoft.",
                 "cloud": f"<span style='color:{t['warn']}'>Your audio is sent to {prov.title()}</span> in this "
                          "mode (only this mode).",
                 "auto": "Auto may use Windows or the cloud when memory is short: then audio leaves this PC."}
        lines.append(where.get(backend, ""))
        self.lbl_speech.setText("<br>".join(x for x in lines if x))

    def _set_cloud_key(self):
        from PySide6.QtWidgets import QInputDialog, QLineEdit as _LE
        prov = self._combo_value(self.cmb_cloud_stt) or "groq"
        key, ok = QInputDialog.getText(self, "Cloud key", f"{prov.title()} API key (kept in the Windows "
                                       "credential store, never in config.yaml). Empty = remove it:",
                                       _LE.Password)
        if not ok:
            return
        from .speech import cloud
        try:
            if key.strip():
                cloud.set_key(prov, key.strip())
            else:
                cloud.delete_key(prov)
        except Exception as e:
            self._set_status(f"Couldn't store the key: {e}", "bad")
        self._speech_status()

    def _build_models(self):
        p = self._page("Claude", "Which models answer, and how a conversation carries on.", "claude",
                       section="Claude")
        g = p.add_group("Quick answers")
        self.cmb_fast_backend = self._combo([("Auto: SDK when there's an API key", "auto"),
                                             ("claude CLI (your subscription)", "cli"),
                                             ("Anthropic SDK (API key)", "sdk")])
        self._row(g, "fast.backend", "Backend", self.cmb_fast_backend)
        self.cmb_fast_cli = self._combo(CLI_MODELS, editable=True)
        self._row(g, "fast.cli_model", "CLI model", self.cmb_fast_cli)
        self.ed_fast_sdk = QLineEdit()
        self._row(g, "fast.sdk_model", "SDK model", self.ed_fast_sdk)

        g = p.add_group("Tool agent", "For questions that need your tools: calendar, mail, MCP lookups.")
        self.cb_agent = self._switch(g, "agent.enabled", "Keep a tool agent ready",
                                     desc="One warm Claude Code process, started at launch and reused.")
        self.cmb_agent_model = self._combo(CLI_MODELS, editable=True)
        self._row(g, "agent.model", "Model", self.cmb_agent_model)
        self.ed_filler = QLineEdit()
        self._row(g, "agent.filler", "Filler phrase", self.ed_filler)
        self.sp_filler_words = QSpinBox(minimum=0, maximum=40, suffix=" words")
        self._row(g, "agent.filler_min_words", "Say it for requests of at least", self.sp_filler_words)

        g = p.add_group("Conversation")
        self.sp_followup = QDoubleSpinBox(minimum=0.0, maximum=30.0, singleStep=1.0, decimals=1, suffix=" s")
        self._row(g, "conversation.followup_s", "Follow-up window", self.sp_followup)
        self.sp_idle_reset = QSpinBox(minimum=30, maximum=86400, singleStep=60, suffix=" s")
        self._row(g, "conversation.idle_reset_s", "Start a new conversation", self.sp_idle_reset)
        self.cb_save_conv = self._switch(g, "conversation.save", "Save conversations",
                                         desc="Per day, so they come back after a restart. Off: nothing is written.")

        g = p.add_group("Background tasks", "Tasks you start by voice; they run claude -p in a project folder.")
        self.cmb_tasks_model = self._combo([("Claude Code default", "")] + CLI_MODELS, editable=True)
        self._row(g, "tasks.model", "Model", self.cmb_tasks_model)
        self.cmb_perm = self._combo(PERMISSION_MODES)
        self.cmb_perm.setItemData(PERMISSION_MODES.index("bypassPermissions"), BYPASS_TIP, Qt.ToolTipRole)
        self._row(g, "tasks.permission_mode", "Permission mode", self.cmb_perm,
                  desc="acceptEdits: edits files without asking, asks for the rest.")
        self.lb_bypass = g.add_note("Careful: bypassPermissions lets tasks run anything without asking.")
        kit.set_tone(self.lb_bypass, "warn")
        self.lb_bypass.setToolTip(BYPASS_TIP)
        self._bypass_row = self.lb_bypass.parentWidget()
        self.cmb_perm.currentIndexChanged.connect(self._perm_changed)
        self.sp_parallel = QSpinBox(minimum=1, maximum=10)
        self._row(g, "tasks.max_parallel", "At most at once", self.sp_parallel, desc="")

    def _build_projects(self):
        p = self._page("Projects", "Say \"start a task in <name>: <thing>\". Several spoken names can point at the "
                       "same folder.", "projects")
        g = p.add_group("Spoken names", "Names are lowercased when saved. Changes apply right away.")
        self.tbl = self._table(["Spoken name", "Folder"])
        self.tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.Interactive)
        self.tbl.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.tbl.setColumnWidth(0, 200)
        self.tbl.setMinimumHeight(260)
        self.tbl.itemChanged.connect(self._mark_folder)
        g.add_widget(self.tbl, pad=False, vpad=0)
        g.add_widget(kit.buttons_row(kit.button("Add", "secondary", lambda: self._add_project()),
                                     kit.button("Choose folder…", "secondary", self._browse_project), None,
                                     kit.button("Remove", "danger", self._remove_project), stretch_end=False))

    def _build_permissions(self):
        p = self._page("Permissions", "Which tools Claude may use without asking. Anything not listed is denied "
                       "automatically.", "permissions")
        g = p.add_group("Tool agent", "Questions that need your tools.")
        self.ed_agent_tools = self._text_box("mcp__telegram\nBash(git status:*)", 240)
        self.ed_agent_tools.setToolTip("Leave out tools that send or change things unless you want "
                                       "\"send Sam a message saying …\" to just do it.")
        self._row(g, "agent.allowed_tools", "Allowed tools", self.ed_agent_tools, kind="stacked",
                  desc="One per line. A server prefix like mcp__telegram allows all its tools; "
                       "Bash(git status:*) allows one command.")
        g = p.add_group("Background tasks", "Tasks you start by voice (claude -p).")
        self.ed_tasks_tools = self._text_box("Read\nEdit", 168)
        self.ed_tasks_tools.setToolTip("Tools the headless run may use without asking; anything else that would "
                                       "need a permission prompt is denied.")
        self._row(g, "tasks.allowed_tools", "Allowed tools", self.ed_tasks_tools, kind="stacked",
                  desc="Anything else that would need a permission prompt is denied.")

    # -- filling / collecting -----------------------------------------------
    def _fill(self, cfg: dict):
        self.sessions_tab.fill(cfg)
        self.cb_listening.setChecked(bool(_get(cfg, "app.listening", True)))
        self.ed_hotkey.setText(str(_get(cfg, "app.hotkey") or ""))
        self.cb_autostart.setChecked(bool(_get(cfg, "app.start_with_windows", False)))
        sc = str(_get(cfg, "island.scale", "auto")).strip().lower()
        self._filling_scale = True
        self.cb_scale_auto.setChecked(sc in ("", "auto"))
        try:
            v = float(sc.rstrip("%")) if sc not in ("", "auto") else 1.0
            v = v / 100 if v > 4 else v
        except ValueError:
            v = 1.0
        self.sl_scale.setValue(round(max(0.8, min(2.0, v)) * 20))
        self._filling_scale = False
        self._scale_changed()
        self._set_combo(self.cmb_motion, str(_get(cfg, "island.motion", "auto")))
        self.cb_hide_idle.setChecked(bool(_get(cfg, "island.hide_when_idle", False)))
        self.ed_weather.setText(str(_get(cfg, "weather.location", "") or ""))
        self.char_tab.fill(cfg)

        self._set_combo(self.cmb_wake, _get(cfg, "wake.model", "hey_jarvis"))
        self.sp_threshold.setValue(float(_get(cfg, "wake.threshold", 0.5)))
        self.sp_cooldown.setValue(float(_get(cfg, "wake.cooldown", 1.0)))
        self._wake_status(cfg)
        self.cb_dictate.setChecked(bool(_get(cfg, "dictate.enabled", True)))
        self.ed_dictate_key.setText(str(_get(cfg, "dictate.key") or ""))
        self.ed_paste_again.setText(str(_get(cfg, "dictate.paste_again_hotkey") or ""))
        self._set_combo(self.cmb_cleanup, str(_get(cfg, "dictate.cleanup", "off") or "off"))
        self.ed_dict_words.setPlainText("\n".join(str(w) for w in (_get(cfg, "dictionary.words") or [])))
        self.ed_dict_repl.setPlainText(self._pairs_text(_get(cfg, "dictionary.replacements") or {}))
        self.ed_dict_snip.setPlainText(self._pairs_text(_get(cfg, "dictionary.snippets") or {}))
        self._dictate_stats()
        self._set_combo(self.cmb_speech, str(_get(cfg, "speech.backend", "local") or "local"))
        self.cb_lean.setChecked(bool(_get(cfg, "speech.lean", False)))
        self.sp_unload.setValue(int(_get(cfg, "speech.unload_after_min", 10) or 10))
        self.row_unload.setEnabled(self.cb_lean.isChecked())
        self._set_combo(self.cmb_cloud_stt, str(_get(cfg, "speech.cloud_stt_provider", "groq") or "groq"))
        self._set_combo(self.cmb_cloud_tts, str(_get(cfg, "speech.cloud_tts_provider") or ""))
        self._speech_status()
        self._set_combo(self.cmb_stt_model, _get(cfg, "stt.cpu_model", "base.en"))
        dev = str(_get(cfg, "stt.device", "gpu")).lower()
        self._set_combo(self.cmb_stt_device, "gpu" if dev in ("gpu", "cuda") else "cpu")
        self._set_combo(self.cmb_stt_gpu_model, _get(cfg, "stt.gpu_model", "large-v3-turbo"))
        self._set_combo(self.cmb_stt_gpu_compute, _get(cfg, "stt.gpu_compute_type", "int8_float16"))
        self.cb_premiere.setChecked(bool(_get(cfg, "stt.yield_to_premiere", False)))
        self.sp_gpu_busy.setValue(int(_get(cfg, "stt.gpu_busy_pct", 70)))
        self.sp_cpu_busy.setValue(int(_get(cfg, "stt.cpu_busy_pct", 85)))
        self._set_combo(self.cmb_tts_backend, _get(cfg, "tts.engine", "kokoro"))
        self._set_combo(self.cmb_tts_device, str(_get(cfg, "tts.device", "gpu")).lower())
        self._set_combo(self.cmb_kokoro_voice, _get(cfg, "tts.voice", "af_heart"))
        self.sp_tts_speed.setValue(float(_get(cfg, "tts.speed", 1.0) or 1.0))
        self.sp_rate.setValue(int(_get(cfg, "tts.rate", 190)))
        self._set_combo(self.cmb_voice, _get(cfg, "tts.sapi_voice"))

        self.cb_instant.setChecked(bool(_get(cfg, "instant.enabled", True)))
        self._set_combo(self.cmb_instant_ack, str(_get(cfg, "instant.ack", "speak") or "speak").lower())
        self.sp_instant_step.setValue(int(_get(cfg, "instant.step", 10) or 10))
        self.sp_instant_context.setValue(int(_get(cfg, "instant.context_s", 120) or 0))
        self.cb_instant_radios.setChecked(bool(_get(cfg, "instant.radios", True)))
        self.cb_instant_clf.setChecked(bool(_get(cfg, "instant.classifier", True)))
        try:
            thr = float(_get(cfg, "instant.threshold", 0.62))
        except (TypeError, ValueError):
            thr = 0.62
        pos = round(max(0.5, min(0.9, thr)) * 100)
        self._instant_thr = (thr, pos)        # an out-of-range value from config.yaml survives an untouched save
        self.sl_instant_threshold.setValue(pos)
        self._instant_threshold_changed()
        self.cb_instant_learn.setChecked(bool(_get(cfg, "instant.learn", True)))
        self._load_taught()

        self._set_combo(self.cmb_fast_backend, _get(cfg, "fast.backend", "auto"))
        self._set_combo(self.cmb_fast_cli, _get(cfg, "fast.cli_model", "haiku"))
        self.ed_fast_sdk.setText(str(_get(cfg, "fast.sdk_model") or ""))
        self.cb_agent.setChecked(bool(_get(cfg, "agent.enabled", True)))
        self._set_combo(self.cmb_agent_model, _get(cfg, "agent.model", "haiku"))
        self.ed_filler.setText(str(_get(cfg, "agent.filler") or ""))
        self.sp_filler_words.setValue(int(_get(cfg, "agent.filler_min_words", 8) or 0))
        self.sp_followup.setValue(float(_get(cfg, "conversation.followup_s", 5) or 0))
        self.sp_idle_reset.setValue(int(_get(cfg, "conversation.idle_reset_s", 600) or 600))
        self.cb_save_conv.setChecked(bool(_get(cfg, "conversation.save", False)))
        self._set_combo(self.cmb_tasks_model, _get(cfg, "tasks.model"))
        self._set_combo(self.cmb_perm, _get(cfg, "tasks.permission_mode", "acceptEdits"))
        self.sp_parallel.setValue(int(_get(cfg, "tasks.max_parallel", 3)))

        self.tbl.setRowCount(0)
        for name, folder in (cfg.get("projects") or {}).items():
            self._add_project(name, folder)

        self.ed_agent_tools.setPlainText("\n".join(str(t) for t in (_get(cfg, "agent.allowed_tools") or [])))
        self.ed_tasks_tools.setPlainText("\n".join(str(t) for t in (_get(cfg, "tasks.allowed_tools") or [])))
        self._perm_changed()
        self._hotkey_changed()
        self.ed_hold.setText(str(_get(cfg, "listen.hold_key") or ""))

    @staticmethod
    def _tools(edit: QPlainTextEdit) -> list[str]:
        out = []
        for line in edit.toPlainText().splitlines():
            t = line.strip()
            if len(t) >= 2 and t[0] == t[-1] and t[0] in "\"'":
                t = t[1:-1].strip()
            if t.startswith("- "):
                t = t[2:].strip()
            if t and t not in out:
                out.append(t)
        return out

    def _collect(self) -> tuple[dict, list[str], list[str]]:
        """(new config, errors, warnings). Works on a deep copy of the original."""
        new = copy.deepcopy(self._orig)
        errors: list[str] = []
        warnings: list[str] = []
        self.sessions_tab.collect(new)

        _set(new, "app.listening", self.cb_listening.isChecked())
        hk = self.ed_hotkey.text().strip()
        if hk:
            try:
                hk = hotkey.normalize(hk)
            except ValueError as e:
                errors.append(f"Hotkey: {e}")
        _set(new, "app.hotkey", hk)
        _set(new, "app.start_with_windows", self.cb_autostart.isChecked())
        _set(new, "island.scale", "auto" if self.cb_scale_auto.isChecked() else self.sl_scale.value() * 5 / 100)
        _set(new, "island.motion", self._combo_value(self.cmb_motion))
        _set(new, "island.hide_when_idle", self.cb_hide_idle.isChecked())
        _set(new, "weather.location", self.ed_weather.text().strip())
        hold = " ".join(self.ed_hold.text().lower().split())
        if hold:
            try:
                holdkey.parse(hold)
            except ValueError as e:
                errors.append(f"Hold key: {e}")
        _set(new, "listen.hold_key", hold)

        wake = self._combo_value(self.cmb_wake)
        if not wake:
            errors.append("Wake word model can't be empty.")
        _set(new, "wake.model", wake)
        _set(new, "wake.threshold", round(self.sp_threshold.value(), 2))
        _set(new, "wake.cooldown", round(self.sp_cooldown.value(), 2))
        stt_model = self._combo_value(self.cmb_stt_model)
        gpu_model = self._combo_value(self.cmb_stt_gpu_model)
        if not stt_model or not gpu_model:
            errors.append("Whisper models can't be empty.")
        _set(new, "stt.cpu_model", stt_model)
        _set(new, "stt.gpu_model", gpu_model)
        _set(new, "stt.gpu_compute_type", self._combo_value(self.cmb_stt_gpu_compute))
        _set(new, "stt.device", self._combo_value(self.cmb_stt_device))
        _set(new, "stt.yield_to_premiere", self.cb_premiere.isChecked())
        _set(new, "stt.gpu_busy_pct", self.sp_gpu_busy.value())
        _set(new, "stt.cpu_busy_pct", self.sp_cpu_busy.value())
        # keep the resume point 10 below the busy point unless set by hand lower
        if int(_get(new, "stt.gpu_resume_pct", 60)) >= self.sp_gpu_busy.value():
            _set(new, "stt.gpu_resume_pct", max(5, self.sp_gpu_busy.value() - 10))
        _set(new, "dictate.enabled", self.cb_dictate.isChecked())
        dkey = self.ed_dictate_key.text().strip()
        try:
            holdkey.parse(dkey or "right alt")
        except ValueError as e:
            errors.append(f"Dictation key: {e}")
        _set(new, "dictate.key", dkey or "right alt")
        pa = self.ed_paste_again.text().strip()
        if pa:
            try:
                hotkey.parse(pa)
            except ValueError as e:
                errors.append(f"Paste-again hotkey: {e}")
        _set(new, "dictate.paste_again_hotkey", pa)
        _set(new, "dictate.cleanup", self._combo_value(self.cmb_cleanup) or "off")
        _set(new, "dictionary.words", [w.strip() for w in self.ed_dict_words.toPlainText().splitlines() if w.strip()])
        for key, edit in (("dictionary.replacements", self.ed_dict_repl), ("dictionary.snippets", self.ed_dict_snip)):
            pairs, bad = self._pairs(edit)
            if bad:
                errors.append(f"{key.split('.')[1].title()}: write each as \"heard => meant\" ({bad[0]!r})")
            _set(new, key, pairs)
        if self._combo_value(self.cmb_speech) != str(_get(self._orig, "speech.backend", "local") or "local"):
            _set(new, "speech.stt_backend", None)        # the one switch sets both sides
            _set(new, "speech.tts_backend", None)
        _set(new, "speech.backend", self._combo_value(self.cmb_speech))
        _set(new, "speech.lean", self.cb_lean.isChecked())
        _set(new, "speech.unload_after_min", self.sp_unload.value())
        _set(new, "speech.cloud_stt_provider", self._combo_value(self.cmb_cloud_stt))
        _set(new, "speech.cloud_tts_provider", self._combo_value(self.cmb_cloud_tts) or None)
        _set(new, "tts.engine", self._combo_value(self.cmb_tts_backend))
        _set(new, "tts.device", self._combo_value(self.cmb_tts_device))
        _set(new, "tts.voice", self._combo_value(self.cmb_kokoro_voice) or "af_heart")
        _set(new, "tts.speed", round(self.sp_tts_speed.value(), 2))
        _set(new, "tts.rate", self.sp_rate.value())
        _set(new, "tts.sapi_voice", self._combo_value(self.cmb_voice) or None)

        _set(new, "instant.enabled", self.cb_instant.isChecked())
        _set(new, "instant.ack", self._combo_value(self.cmb_instant_ack))
        _set(new, "instant.step", self.sp_instant_step.value())
        _set(new, "instant.context_s", self.sp_instant_context.value())
        _set(new, "instant.radios", self.cb_instant_radios.isChecked())
        _set(new, "instant.classifier", self.cb_instant_clf.isChecked())
        thr, pos = getattr(self, "_instant_thr", (None, None))
        _set(new, "instant.threshold", thr if self.sl_instant_threshold.value() == pos
             else round(self.sl_instant_threshold.value() / 100, 2))
        _set(new, "instant.learn", self.cb_instant_learn.isChecked())

        _set(new, "fast.backend", self._combo_value(self.cmb_fast_backend))
        _set(new, "fast.cli_model", self._combo_value(self.cmb_fast_cli))
        _set(new, "fast.sdk_model", self._text(self.ed_fast_sdk, "fast.sdk_model"))
        _set(new, "agent.enabled", self.cb_agent.isChecked())
        _set(new, "agent.model", self._combo_value(self.cmb_agent_model))
        _set(new, "agent.filler", self._text(self.ed_filler, "agent.filler"))
        _set(new, "agent.filler_min_words", int(self.sp_filler_words.value()))
        _set(new, "conversation.followup_s", self.sp_followup.value())
        _set(new, "conversation.idle_reset_s", self.sp_idle_reset.value())
        _set(new, "conversation.save", self.cb_save_conv.isChecked())
        _set(new, "tasks.model", self._combo_value(self.cmb_tasks_model) or None)
        _set(new, "tasks.permission_mode", self._combo_value(self.cmb_perm))
        _set(new, "tasks.max_parallel", self.sp_parallel.value())

        projects: dict[str, str] = {}
        missing = []
        for r in range(self.tbl.rowCount()):
            name = " ".join(self._cell(r, 0).lower().split())
            folder = self._cell(r, 1).strip().strip('"')
            if not name and not folder:
                continue
            if not name:
                errors.append(f"Project row {r + 1} has a folder but no spoken name.")
                continue
            if not folder:
                errors.append(f"Project \"{name}\" has no folder.")
                continue
            if name in projects:
                errors.append(f"Project name \"{name}\" is listed twice.")
                continue
            projects[name] = folder
            if not Path(os.path.expandvars(folder)).is_dir():
                missing.append(name)
        if missing:
            warnings.append("Folder not found for: " + ", ".join(missing))
        new["projects"] = projects

        _set(new, "agent.allowed_tools", self._tools(self.ed_agent_tools))
        _set(new, "tasks.allowed_tools", self._tools(self.ed_tasks_tools))
        self.char_tab.collect(new)
        return new, errors, warnings

    def _text(self, edit: QLineEdit, key: str):
        """Stripped text; an empty field keeps a null original as null."""
        t = edit.text().strip()
        return None if not t and _get(self._orig, key) is None else t

    def _cell(self, r: int, c: int) -> str:
        it = self.tbl.item(r, c)
        return it.text() if it else ""

    # -- public API -------------------------------------------------------------
    def reload(self, cfg: dict):
        """Refill every field from `cfg` (e.g. when the window is reopened)."""
        self._orig = copy.deepcopy(cfg)
        self._fill(self._orig)
        if self._pending_restart:
            self._show_restart(self._pending_restart, "Restart pending")
        else:
            self._set_status("")
            self.btn_restart.hide()

    def has_unsaved_changes(self) -> bool:
        new, errors, _ = self._collect()
        if errors:
            # An invalid field only counts as a change if it differs from what was loaded.
            return (self.ed_hotkey.text().strip() != str(_get(self._orig, "app.hotkey") or "")
                    or bool(config.diff(self._orig, new)))
        return bool(config.diff(self._orig, new))

    def save(self) -> bool:
        new, errors, warnings = self._collect()
        if errors:
            self._set_status("Not saved. " + " ".join(errors), "bad")
            return False
        changed = config.diff(self._orig, new)
        if not changed:
            msg = "No changes to save."
            if warnings:
                msg += " " + " ".join(warnings)
            self._set_status(msg, "dim")
            return True
        try:
            config.ensure_user_config(self.config_path)
            config.save(new, self.config_path)
        except OSError as e:
            self._set_status(f"Couldn't write the config file: {e}", "bad")
            return False
        self._orig = copy.deepcopy(new)
        self.ed_hotkey.setText(str(new["app"].get("hotkey") or ""))   # show the normalized form
        self.saved.emit(copy.deepcopy(new), list(changed))
        restart = config.needs_restart(changed)
        for k in restart:
            if k not in self._pending_restart:
                self._pending_restart.append(k)
        extra = (" " + " ".join(warnings)) if warnings else ""
        if self._pending_restart:
            self._show_restart(self._pending_restart, "Saved", extra)
        else:
            self.btn_restart.hide()
            self._set_status("Saved. Applied now." + extra, "ok" if not warnings else "warn")
        return True

    # -- status ------------------------------------------------------------------
    _TONES = {"#6CCB7A": "ok", "#E5646A": "bad", "#E08A6B": "bad", "#E07A5F": "bad", "#E5A04A": "warn",
              "#E0B04A": "warn", "#9A9A9A": "dim"}

    def _set_status(self, text: str, tone: str = "dim"):
        """The footer line. tone: ok / bad / warn / dim (or one of the old hex colours)."""
        from html import escape
        tone = self._TONES.get(str(tone).upper(), tone)
        color = th.T.get(tone, th.T["dim"])
        dot = f"<span style='color:{color}'>&#9679;</span>&nbsp;&nbsp;" if tone in ("ok", "bad", "warn") else ""
        body = th.T["fg"] if tone == "ok" else color
        self.status.setText(f"{dot}<span style='color:{body}'>{escape(text)}</span>" if text else "")

    def _show_restart(self, keys: list[str], head: str, extra: str = ""):
        self._set_status(f"{head}. Needs a restart to apply: {', '.join(keys)}.{extra}", "warn")
        self.btn_restart.show()

    # -- slots ------------------------------------------------------------------------
    _filling_scale = False

    def _scale_changed(self, *_):
        """The island size slider: shown on the running island as you drag (saved on Save)."""
        auto = self.cb_scale_auto.isChecked()
        self.sl_scale.setEnabled(not auto)
        self.row_scale.setEnabled(not auto)
        self.lb_scale.setText("auto" if auto else f"{self.sl_scale.value() * 5} %")
        if not self._filling_scale:
            self.island_preview.emit("auto" if auto else self.sl_scale.value() * 5 / 100)

    def _hotkey_changed(self, *_):
        if self._recorder.active:
            return
        text = self.ed_hotkey.text().strip()
        kit.set_error(self.ed_hotkey, False)
        kit.set_tone(self.lb_hotkey, "")
        self.btn_hk_clear.setVisible(bool(text))
        if not text:
            self.lb_hotkey.setText("Press it anywhere to talk. Empty: no hotkey.")
            return
        try:
            norm = hotkey.normalize(text)
            self.lb_hotkey.setText("Press it anywhere to talk." if norm == text else f"Will be saved as {norm}.")
        except ValueError as e:
            self.lb_hotkey.setText(f"Not a hotkey: {e}")
            kit.set_tone(self.lb_hotkey, "bad")
            kit.set_error(self.ed_hotkey, True)

    def _record_hotkey(self):
        if self._recorder.active:
            self._recorder.stop(None)
        else:
            self.lb_hotkey.setText("Press the combination now. Esc cancels.")
            kit.set_tone(self.lb_hotkey, "accent")
            kit.set_error(self.ed_hotkey, False)
            self._recorder.start()

    def _perm_changed(self, *_):
        bypass = self._combo_value(self.cmb_perm) == "bypassPermissions"
        self.lb_bypass.setVisible(bypass)
        self._bypass_row.setVisible(bypass)
        getattr(self._bypass_row, "_divider", self._bypass_row).setVisible(bypass)
        self.cmb_perm.setToolTip(BYPASS_TIP if bypass else TIPS["tasks.permission_mode"])

    def _browse_wake(self):
        start = self._combo_value(self.cmb_wake)
        start = str(Path(start).parent) if start.lower().endswith(".onnx") else str(config.ROOT)
        path, _ = QFileDialog.getOpenFileName(self, "Wake word model", start, "ONNX models (*.onnx)")
        if path:
            self.cmb_wake.setCurrentText(str(Path(path)))

    # -- wake word: status, wizard, default, undo --------------------------------------
    def _wake_status(self, cfg: dict | None = None):
        from .listen import wake_label
        from .wake_wizard import FALSE_WAKES, describe_model, load_history
        cfg = cfg or self._orig
        model = str(_get(cfg, "wake.model", ""))
        loaded = self.wake_hooks.loaded_label() if self.wake_hooks is not None else (None, None)
        if loaded[0] and loaded[1]:
            text = f"\"{loaded[0]}\" (fallback: {loaded[1]})"
        else:
            text = f"\"{wake_label(model)}\": {describe_model(model)}"
        self.lb_wake_now.setText(text)
        kit.set_tone(self.lb_wake_now, "warn" if ("not found" in text or "fallback" in text) else "")
        self.btn_wake_undo.setEnabled(bool(load_history()))
        n = len(list(FALSE_WAKES.glob("*.wav"))) if FALSE_WAKES.is_dir() else 0
        self.lb_false_wakes.setText(
            f"{n} false wake{'s' if n != 1 else ''} saved with \"not me\" on the island; the next tune-up "
            f"learns from them." if n else
            "When it wakes by mistake, click \"not me\" on the island (or right-click it, That wasn't me): the "
            "audio is saved and the next tune-up learns from it.")

    def _apply_wake(self, new: dict, msg: str) -> str:
        """Save a config that only changes wake.model / wake.threshold (built
        from the last saved config, so unsaved edits in the form stay out)."""
        try:
            config.ensure_user_config(self.config_path)
            config.save(new, self.config_path)
        except OSError as e:
            self._set_status(f"Couldn't write the config file: {e}", "bad")
            return f"Couldn't write the config file: {e}"
        changed = config.diff(self._orig, new)
        self._orig = copy.deepcopy(new)
        self._set_combo(self.cmb_wake, _get(new, "wake.model"))
        self.sp_threshold.setValue(float(_get(new, "wake.threshold", 0.5)))
        self.saved.emit(copy.deepcopy(new), list(changed))
        self._wake_status(new)
        self._set_status(msg, "ok")
        return msg

    def _open_wizard(self):
        from .wake_wizard import apply_model, make_wizard
        if self._wizard is not None and self._wizard.isVisible():
            self._wizard.raise_()
            return

        def on_apply(model):
            return self._apply_wake(apply_model(self._orig, model),
                                    f"Now using {Path(model).name} (live, no restart).")
        self._wizard = make_wizard(self._orig, self.wake_hooks, self, on_apply=on_apply, on_undo=self._undo_wake)
        self._wizard.finished.connect(lambda *_: self._wake_status())
        self._wizard.show()

    def _use_default_wake(self):
        from .wake_models import DEFAULT_REL
        from .wake_wizard import apply_model
        if str(_get(self._orig, "wake.model")) == DEFAULT_REL and float(_get(self._orig, "wake.threshold", 0.5)) == 0.5:
            self._set_status("Already using the shared default model.")
            return
        self._apply_wake(apply_model(self._orig, config.ROOT / DEFAULT_REL), "Back to the shared default model.")

    def _undo_wake(self) -> str:
        from .wake_wizard import undo_model
        new = undo_model(self._orig)
        if new is None:
            self._set_status("Nothing to undo.")
            return "Nothing to undo."
        return self._apply_wake(new, f"Back to {_get(new, 'wake.model')} (threshold {_get(new, 'wake.threshold')}).")

    def _add_project(self, name: str = "", folder: str = ""):
        r = self.tbl.rowCount()
        self.tbl.insertRow(r)
        self.tbl.setItem(r, 0, QTableWidgetItem(str(name or "")))
        self._set_folder(r, str(folder or ""))
        if not name and not folder:
            self.tbl.setCurrentCell(r, 0)
            self.tbl.editItem(self.tbl.item(r, 0))

    def _set_folder(self, r: int, folder: str):
        self.tbl.setItem(r, 1, QTableWidgetItem(folder))
        self._mark_folder(self.tbl.item(r, 1))

    def _mark_folder(self, item: QTableWidgetItem):
        """Folder cells that don't exist turn amber (saving is still allowed)."""
        if item.column() != 1:
            return
        folder = item.text().strip().strip('"')
        ok = not folder or Path(os.path.expandvars(folder)).is_dir()
        self.tbl.blockSignals(True)
        item.setForeground(QColor(th.T["fg"]) if ok else QColor(th.T["warn"]))
        item.setToolTip("" if ok else "This folder doesn't exist.")
        self.tbl.blockSignals(False)

    def _remove_project(self):
        r = self.tbl.currentRow()
        if r >= 0:
            self.tbl.removeRow(r)

    def _browse_project(self):
        r = self.tbl.currentRow()
        if r < 0:
            self._add_project("", "")
            r = self.tbl.rowCount() - 1
        cur = self._cell(r, 1).strip()
        path = QFileDialog.getExistingDirectory(self, "Project folder", cur if Path(cur).is_dir() else "")
        if path:
            self._set_folder(r, str(Path(path)))
            if not self._cell(r, 0).strip():
                self.tbl.setItem(r, 0, QTableWidgetItem(" ".join(Path(path).name.replace("_", " ")
                                                                 .replace("-", " ").lower().split())))

    @staticmethod
    def _sapi_voices() -> list[str]:
        try:
            import win32com.client
            voices = win32com.client.Dispatch("SAPI.SpVoice").GetVoices()
            return [voices.Item(i).GetDescription() for i in range(voices.Count)]
        except Exception:
            return []

    def _test_voice(self):
        try:
            import win32com.client
            if self._tts_test is None:
                self._tts_test = win32com.client.Dispatch("SAPI.SpVoice")
            v = self._tts_test
            want = self._combo_value(self.cmb_voice).lower()
            if want:
                voices = v.GetVoices()
                for i in range(voices.Count):
                    if want in voices.Item(i).GetDescription().lower():
                        v.Voice = voices.Item(i)
                        break
            # Same words-per-minute -> SAPI rate mapping as tts.py.
            v.Rate = max(-10, min(10, round((self.sp_rate.value() - 180) / 15)))
            v.Speak("", 3)    # purge anything still playing
            v.Speak("Hi, this is how I'll sound.", 1)
        except Exception as e:
            self._set_status(f"Couldn't play a test sentence: {e}", "bad")

    def closeEvent(self, e):
        if self._recorder.active:
            self._recorder.stop(None)
        if self.has_unsaved_changes():
            ans = QMessageBox.question(self, "Unsaved changes", "Save your changes before closing?",
                                       QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
                                       QMessageBox.Save)
            if ans == QMessageBox.Cancel:
                e.ignore()
                return
            if ans == QMessageBox.Save and not self.save():
                e.ignore()
                return
            if ans == QMessageBox.Discard:
                self._fill(self._orig)
        self.island_preview.emit(None)               # drop an unsaved size preview
        self.character_preview.emit(None)            # ...and an unsaved character
        e.accept()
