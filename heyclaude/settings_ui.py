"""Settings window: edits config.yaml through a few tabs.

Works on a copy of the merged config, so keys it doesn't show (claude.exe,
listen.*, island.*, piper/kokoro paths...) are written back unchanged. Save
writes the file, emits `saved(new_cfg, changed_keys)` and says inline which of
the changes only apply after a restart (with a "Restart now" button).
"""
from __future__ import annotations

import copy
import os
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
                               QFrame, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox,
                               QPlainTextEdit, QPushButton, QScrollArea, QSlider, QSpinBox, QTableWidget,
                               QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from . import config, holdkey, hotkey

ACCENT = "#D97757"

STYLE = f"""
QWidget {{ background: #141414; color: #EDEDED; font-family: 'Segoe UI'; font-size: 10pt; }}
QToolTip {{ background: #262626; color: #EDEDED; border: 1px solid #3A3A3A; padding: 4px; }}
QTabWidget::pane {{ border: 1px solid #2A2A2A; border-radius: 6px; top: -1px; }}
QTabBar::tab {{ background: transparent; color: #9A9A9A; padding: 7px 14px; border: none;
               border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: #F2F2F2; border-bottom: 2px solid {ACCENT}; }}
QTabBar::tab:hover {{ color: #F2F2F2; }}
QScrollArea, QScrollArea > QWidget > QWidget {{ border: none; background: #141414; }}
QGroupBox {{ border: 1px solid #2A2A2A; border-radius: 6px; margin-top: 14px; padding: 12px 10px 8px 10px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {ACCENT}; }}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTableWidget {{
    background: #0C0C0C; border: 1px solid #2E2E2E; border-radius: 5px; padding: 4px 6px;
    selection-background-color: {ACCENT}; selection-color: #141414; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QPlainTextEdit:focus {{
    border: 1px solid {ACCENT}; }}
QLineEdit[recording="true"] {{ border: 1px solid {ACCENT}; background: #2A1A14; }}
QComboBox QAbstractItemView {{ background: #1A1A1A; border: 1px solid #3A3A3A; selection-background-color: #333; }}
QPlainTextEdit {{ font-family: Consolas, monospace; }}
QTableWidget {{ gridline-color: #262626; padding: 0; }}
QHeaderView::section {{ background: #1C1C1C; color: #9A9A9A; border: none; border-bottom: 1px solid #2A2A2A;
                        padding: 5px 6px; }}
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 14px; height: 14px; border: 1px solid #555; border-radius: 3px; background: #0C0C0C; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border: 1px solid {ACCENT}; }}
QPushButton {{ background: #262626; border: 1px solid #3A3A3A; padding: 6px 12px; border-radius: 6px; }}
QPushButton:hover {{ background: #333; }}
QPushButton:disabled {{ color: #666; }}
QPushButton#primary {{ background: {ACCENT}; border: 1px solid {ACCENT}; color: #141414; font-weight: 600; }}
QPushButton#primary:hover {{ background: #E58A6B; }}
QLabel#restartTag {{ color: {ACCENT}; border: 1px solid #5A3A2E; border-radius: 7px; padding: 0 6px;
                     font-size: 8pt; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #3A3A3A; border-radius: 3px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: #4A4A4A; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #3A3A3A; border-radius: 3px; min-width: 24px; }}
QLabel#help {{ color: #9A9A9A; }}
QLabel#hint {{ color: #9A9A9A; font-size: 9pt; }}
QLabel#warn {{ color: #E5A04A; font-size: 9pt; }}
"""

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
}
BYPASS_TIP = ("bypassPermissions skips every permission check: tasks can run any command and edit any file "
              "without asking. Only use it in folders you can afford to lose.")


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
        self.edit.setPlaceholderText("press the key combination… (Esc cancels)")
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
        self.edit.setPlaceholderText("e.g. ctrl+alt+space (empty = no hotkey)")
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

    def __init__(self, cfg: dict, config_path=None, parent=None, wake_hooks=None):
        super().__init__(parent, Qt.Window)
        self.config_path = config_path
        self.wake_hooks = wake_hooks          # __main__._WakeHooks (pause/resume listening, GPU load)
        self._wizard = None
        self._orig: dict = copy.deepcopy(cfg)
        self._pending_restart: list[str] = []
        self._tts_test = None
        self.setWindowTitle("hey-claude settings")
        self.resize(720, 640)
        self.setStyleSheet(STYLE)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 10)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(False)
        lay.addWidget(self.tabs, 1)
        self._build_general()
        self._build_voice()
        self._build_wake()
        self._build_models()
        self._build_projects()
        self._build_permissions()

        # Bottom: status line + Restart now + Save / Close.
        bottom = QHBoxLayout()
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.RichText)
        self.btn_restart = QPushButton("Restart now", clicked=lambda: self.restart_requested.emit())
        self.btn_restart.hide()
        self.btn_save = QPushButton("Save", clicked=lambda: self.save())
        self.btn_save.setObjectName("primary")
        self.btn_save.setDefault(True)
        self.btn_close = QPushButton("Close", clicked=self.close)
        bottom.addWidget(self.status, 1)
        bottom.addWidget(self.btn_restart)
        bottom.addSpacing(8)
        bottom.addWidget(self.btn_save)
        bottom.addWidget(self.btn_close)
        lay.addLayout(bottom)

        self._fill(self._orig)

    # -- building blocks ------------------------------------------------------
    def _tag(self, key: str) -> QWidget | None:
        if not config.needs_restart([key]):
            return None
        t = QLabel("restart")
        t.setObjectName("restartTag")
        t.setToolTip("Takes effect after hey-claude restarts.")
        t.setFixedHeight(18)
        t.setAlignment(Qt.AlignCenter)
        return t

    def _row(self, form: QFormLayout, key: str, label: str, *widgets: QWidget):
        box = QHBoxLayout()
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(6)
        for w in widgets:
            box.addWidget(w, 1 if isinstance(w, (QLineEdit, QComboBox)) else 0)
        if not any(isinstance(w, (QLineEdit, QComboBox)) for w in widgets):
            box.addStretch(1)
        tag = self._tag(key)
        if tag:
            box.addWidget(tag, 0, Qt.AlignVCenter)
        lab = QLabel(label)
        tip = TIPS.get(key)
        if tip:
            lab.setToolTip(tip)
            for w in widgets:
                if not w.toolTip():
                    w.setToolTip(tip)
        holder = QWidget()
        holder.setLayout(box)
        form.addRow(lab, holder)

    @staticmethod
    def _form(group: QGroupBox | None = None) -> QFormLayout:
        f = QFormLayout(group) if group is not None else QFormLayout()
        f.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        f.setHorizontalSpacing(12)
        f.setVerticalSpacing(8)
        f.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        return f

    def _scroll_tab(self, title: str) -> QVBoxLayout:
        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setContentsMargins(14, 10, 14, 14)
        v.setSpacing(6)
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setFrameShape(QFrame.NoFrame)
        sa.setWidget(inner)
        self.tabs.addTab(sa, title)
        return v

    @staticmethod
    def _help(text: str) -> QLabel:
        lab = QLabel(text)
        lab.setObjectName("help")
        lab.setWordWrap(True)
        return lab

    @staticmethod
    def _combo(items, editable=False) -> QComboBox:
        c = QComboBox()
        c.setEditable(editable)
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

    # -- tabs -----------------------------------------------------------------
    def _build_general(self):
        v = self._scroll_tab("General")
        g = QGroupBox("Startup")
        f = self._form(g)
        self.cb_listening = QCheckBox("Listen for the wake word at start")
        self._row(f, "app.listening", "Wake word", self.cb_listening)
        self.cb_autostart = QCheckBox("Start hey-claude with Windows")
        self._row(f, "app.start_with_windows", "Windows", self.cb_autostart)
        v.addWidget(g)

        g = QGroupBox("Push-to-talk")
        f = self._form(g)
        self.ed_hotkey = QLineEdit()
        self.ed_hotkey.setPlaceholderText("e.g. ctrl+alt+space (empty = no hotkey)")
        self.btn_record = QPushButton("Record…", clicked=self._record_hotkey)
        self.btn_record.setFocusPolicy(Qt.NoFocus)   # don't steal focus from the field being recorded
        self.btn_record.setToolTip("Click, then press the key combination. Esc cancels.")
        self.btn_hk_clear = QPushButton("Clear", clicked=lambda: self.ed_hotkey.setText(""))
        self._row(f, "app.hotkey", "Hotkey", self.ed_hotkey, self.btn_record, self.btn_hk_clear)
        self.lb_hotkey = QLabel("")
        self.lb_hotkey.setObjectName("hint")
        f.addRow("", self.lb_hotkey)
        self._recorder = _HotkeyRecorder(self.ed_hotkey, self._hotkey_changed)
        self.ed_hotkey.textChanged.connect(self._hotkey_changed)
        self.ed_hold = QLineEdit()
        self.ed_hold.setPlaceholderText("e.g. ctrl, right ctrl, caps lock (empty = off)")
        self._row(f, "listen.hold_key", "Hold to keep listening", self.ed_hold)
        v.addWidget(g)
        g = QGroupBox("Island")
        f = self._form(g)
        self.sl_scale = QSlider(Qt.Horizontal)
        self.sl_scale.setRange(16, 40)                 # x5 %: 80 % .. 200 %
        self.sl_scale.setPageStep(1)
        self.sl_scale.setFixedWidth(220)
        self.lb_scale = QLabel("100 %")
        self.lb_scale.setMinimumWidth(44)
        self.cb_scale_auto = QCheckBox("Auto")
        self.sl_scale.valueChanged.connect(self._scale_changed)
        self.cb_scale_auto.toggled.connect(self._scale_changed)
        self._row(f, "island.scale", "Size", self.sl_scale, self.lb_scale, self.cb_scale_auto)
        self.cmb_motion = self._combo(["auto", "full", "calm", "off"])
        self._row(f, "island.motion", "Motion", self.cmb_motion)
        self.cb_hide_idle = QCheckBox("Hide when idle")
        self._row(f, "island.hide_when_idle", "At rest", self.cb_hide_idle)
        self.ed_weather = QLineEdit()
        self.ed_weather.setPlaceholderText("e.g. Tashkent (empty = say a place when you ask)")
        self._row(f, "weather.location", "Weather place", self.ed_weather)
        v.addWidget(g)
        v.addWidget(self._help("Settings marked <span style='color:%s'>restart</span> take effect the next time "
                               "hey-claude starts; everything else applies as soon as you save. "
                               "Keys not shown here (listen timings, island position, claude.exe path, piper/kokoro "
                               "paths…) can be edited in config.yaml by hand." % ACCENT))
        v.addStretch(1)

    def _build_wake(self):
        v = self._scroll_tab("Wake word")
        g = QGroupBox("Wake word")
        f = self._form(g)
        self.lb_wake_now = QLabel("")
        self.lb_wake_now.setWordWrap(True)
        f.addRow("Using", self.lb_wake_now)
        self.cmb_wake = self._combo(WAKE_MODELS, editable=True)
        self.btn_wake_browse = QPushButton("Browse…", clicked=self._browse_wake)
        self._row(f, "wake.model", "Model", self.cmb_wake, self.btn_wake_browse)
        self.sp_threshold = QDoubleSpinBox(minimum=0.05, maximum=0.95, singleStep=0.05, decimals=2)
        self._row(f, "wake.threshold", "Threshold", self.sp_threshold)
        self.sp_cooldown = QDoubleSpinBox(minimum=0.0, maximum=30.0, singleStep=0.5, decimals=2, suffix=" s")
        self._row(f, "wake.cooldown", "Cooldown", self.sp_cooldown)
        v.addWidget(g)

        g = QGroupBox("Personal tune-up (optional)")
        gv = QVBoxLayout(g)
        gv.addWidget(self._help(
            "The shared \"hey claude\" model works for most voices. If it often misses you, or you want a "
            "different phrase, this trains a model on your voice, here on this PC, in about 15 minutes. "
            "Your recordings and the model stay in the state folder."))
        row = QHBoxLayout()
        self.btn_wizard = QPushButton("Tune it to my voice…", clicked=self._open_wizard)
        self.btn_wizard.setObjectName("primary")
        self.btn_wake_default = QPushButton("Use the shared default", clicked=self._use_default_wake)
        self.btn_wake_undo = QPushButton("Undo the last change", clicked=self._undo_wake)
        row.addWidget(self.btn_wizard)
        row.addWidget(self.btn_wake_default)
        row.addWidget(self.btn_wake_undo)
        row.addStretch(1)
        gv.addLayout(row)
        self.lb_false_wakes = QLabel("")
        self.lb_false_wakes.setObjectName("hint")
        self.lb_false_wakes.setWordWrap(True)
        gv.addWidget(self.lb_false_wakes)
        v.addWidget(g)
        v.addStretch(1)

    def _build_voice(self):
        v = self._scroll_tab("Voice")
        g = QGroupBox("Speech recognition (whisper)")
        f = self._form(g)
        self.cmb_stt_device = self._combo([("GPU, CPU as fallback", "gpu"), ("CPU only", "cpu")])
        self._row(f, "stt.device", "Device", self.cmb_stt_device)
        self.cmb_stt_gpu_model = self._combo(STT_GPU_MODELS, editable=True)
        self._row(f, "stt.gpu_model", "GPU model", self.cmb_stt_gpu_model)
        self.cmb_stt_gpu_compute = self._combo(["int8_float16", "float16"])
        self._row(f, "stt.gpu_compute_type", "GPU precision", self.cmb_stt_gpu_compute)
        self.cmb_stt_model = self._combo(STT_MODELS, editable=True)
        self._row(f, "stt.cpu_model", "CPU model", self.cmb_stt_model)
        self.sp_gpu_busy = QSpinBox(minimum=10, maximum=100, singleStep=5, suffix=" %")
        self._row(f, "stt.gpu_busy_pct", "GPU busy at", self.sp_gpu_busy)
        self.sp_cpu_busy = QSpinBox(minimum=10, maximum=100, singleStep=5, suffix=" %")
        self._row(f, "stt.cpu_busy_pct", "CPU busy at", self.sp_cpu_busy)
        self.cb_premiere = QCheckBox("Never use the GPU while Premiere Pro runs, even idle")
        self._row(f, "stt.yield_to_premiere", "Premiere", self.cb_premiere)
        v.addWidget(g)

        g = QGroupBox("Speech output")
        f = self._form(g)
        from .tts import KOKORO_VOICES
        self.cmb_tts_backend = self._combo([("Kokoro", "kokoro"), ("Windows voices (SAPI)", "sapi"),
                                            ("Piper", "piper")])
        self._row(f, "tts.engine", "Engine", self.cmb_tts_backend)
        self.cmb_tts_device = self._combo([("GPU (CPU when busy)", "gpu"), ("CPU", "cpu")])
        self._row(f, "tts.device", "Device", self.cmb_tts_device)
        self.cmb_kokoro_voice = self._combo([(x, x) for x in KOKORO_VOICES], editable=True)
        self._row(f, "tts.voice", "Kokoro voice", self.cmb_kokoro_voice)
        self.sp_tts_speed = QDoubleSpinBox(minimum=0.5, maximum=2.0, singleStep=0.05, decimals=2)
        self._row(f, "tts.speed", "Speed", self.sp_tts_speed)
        self.sp_rate = QSpinBox(minimum=80, maximum=400, singleStep=10, suffix=" wpm")
        self._row(f, "tts.rate", "SAPI rate", self.sp_rate)
        self.cmb_voice = self._combo([("(Windows default)", "")] + [(d, d) for d in self._sapi_voices()],
                                     editable=True)
        self.btn_test_voice = QPushButton("Test SAPI voice", clicked=self._test_voice)
        self._row(f, "tts.sapi_voice", "SAPI voice", self.cmb_voice, self.btn_test_voice)
        v.addWidget(g)
        v.addStretch(1)

    def _build_models(self):
        v = self._scroll_tab("Models")
        g = QGroupBox("Quick answers")
        f = self._form(g)
        self.cmb_fast_backend = self._combo([("auto (SDK if ANTHROPIC_API_KEY is set, else CLI)", "auto"),
                                             ("claude CLI (your subscription login)", "cli"),
                                             ("Anthropic SDK (API key)", "sdk")])
        self._row(f, "fast.backend", "Backend", self.cmb_fast_backend)
        self.cmb_fast_cli = self._combo(CLI_MODELS, editable=True)
        self._row(f, "fast.cli_model", "CLI model", self.cmb_fast_cli)
        self.ed_fast_sdk = QLineEdit()
        self._row(f, "fast.sdk_model", "SDK model", self.ed_fast_sdk)
        v.addWidget(g)

        g = QGroupBox("Tool agent (calendar, mail, MCP lookups)")
        f = self._form(g)
        self.cb_agent = QCheckBox("Keep a warm Claude Code process for questions that need tools")
        self._row(f, "agent.enabled", "Enabled", self.cb_agent)
        self.cmb_agent_model = self._combo(CLI_MODELS, editable=True)
        self._row(f, "agent.model", "Model", self.cmb_agent_model)
        self.ed_filler = QLineEdit()
        self._row(f, "agent.filler", "Filler phrase", self.ed_filler)
        v.addWidget(g)

        g = QGroupBox("Conversation")
        f = self._form(g)
        self.sp_followup = QDoubleSpinBox(minimum=0.0, maximum=30.0, singleStep=1.0, decimals=1, suffix=" s")
        self._row(f, "conversation.followup_s", "Follow-up window", self.sp_followup)
        self.sp_idle_reset = QSpinBox(minimum=30, maximum=86400, singleStep=60, suffix=" s")
        self._row(f, "conversation.idle_reset_s", "New conversation after", self.sp_idle_reset)
        self.cb_save_conv = QCheckBox("Save this conversation (per day)")
        self._row(f, "conversation.save", "Save", self.cb_save_conv)
        v.addWidget(g)

        g = QGroupBox("Background tasks (claude -p)")
        f = self._form(g)
        self.cmb_tasks_model = self._combo([("(Claude Code default)", "")] + CLI_MODELS, editable=True)
        self._row(f, "tasks.model", "Model", self.cmb_tasks_model)
        self.cmb_perm = self._combo(PERMISSION_MODES)
        self.cmb_perm.setItemData(PERMISSION_MODES.index("bypassPermissions"), BYPASS_TIP, Qt.ToolTipRole)
        self._row(f, "tasks.permission_mode", "Permission mode", self.cmb_perm)
        self.lb_bypass = QLabel("Warning: bypassPermissions lets tasks run anything without asking.")
        self.lb_bypass.setObjectName("warn")
        self.lb_bypass.setToolTip(BYPASS_TIP)
        self.lb_bypass.hide()
        f.addRow("", self.lb_bypass)
        self.cmb_perm.currentIndexChanged.connect(self._perm_changed)
        self.sp_parallel = QSpinBox(minimum=1, maximum=10)
        self._row(f, "tasks.max_parallel", "Max parallel", self.sp_parallel)
        v.addWidget(g)
        v.addStretch(1)

    def _build_projects(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(14, 12, 14, 14)
        v.addWidget(self._help("Say \"start a task in <name>: <thing>\". Several spoken names can point at the same "
                               "folder. Names are lowercased when saved. Changes apply right away."))
        self.tbl = QTableWidget(0, 2)
        self.tbl.setHorizontalHeaderLabels(["Spoken name", "Folder"])
        self.tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.Interactive)
        self.tbl.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.tbl.setColumnWidth(0, 180)
        self.tbl.verticalHeader().hide()
        self.tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tbl.itemChanged.connect(self._mark_folder)
        v.addWidget(self.tbl, 1)
        bar = QHBoxLayout()
        bar.addWidget(QPushButton("Add", clicked=lambda: self._add_project()))
        bar.addWidget(QPushButton("Remove", clicked=self._remove_project))
        bar.addWidget(QPushButton("Browse…", clicked=self._browse_project))
        bar.addStretch(1)
        v.addLayout(bar)
        self.tabs.addTab(w, "Projects")

    def _build_permissions(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(14, 12, 14, 14)
        v.addWidget(self._help("One tool per line; blank lines are ignored. Anything not listed is denied "
                               "automatically. An MCP server name prefix such as mcp__telegram allows all of that "
                               "server's tools; Bash(git status:*) allows one command."))
        head = QHBoxLayout()
        head.addWidget(QLabel("Tool agent (questions that need tools)"))
        tag = self._tag("agent.allowed_tools")
        if tag:
            head.addWidget(tag)
        head.addStretch(1)
        v.addLayout(head)
        self.ed_agent_tools = QPlainTextEdit()
        self.ed_agent_tools.setToolTip("Leave out tools that send or change things unless you want "
                                       "\"send Sam a message saying …\" to just do it.")
        v.addWidget(self.ed_agent_tools, 3)
        head = QHBoxLayout()
        head.addWidget(QLabel("Background tasks (claude -p)"))
        tag = self._tag("tasks.allowed_tools")
        if tag:
            head.addWidget(tag)
        head.addStretch(1)
        v.addLayout(head)
        self.ed_tasks_tools = QPlainTextEdit()
        self.ed_tasks_tools.setToolTip("Tools the headless run may use without asking; anything else that would "
                                       "need a permission prompt is denied.")
        v.addWidget(self.ed_tasks_tools, 2)
        self.tabs.addTab(w, "Permissions")

    # -- filling / collecting -----------------------------------------------
    def _fill(self, cfg: dict):
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

        self._set_combo(self.cmb_wake, _get(cfg, "wake.model", "hey_jarvis"))
        self.sp_threshold.setValue(float(_get(cfg, "wake.threshold", 0.5)))
        self.sp_cooldown.setValue(float(_get(cfg, "wake.cooldown", 1.0)))
        self._wake_status(cfg)
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

        self._set_combo(self.cmb_fast_backend, _get(cfg, "fast.backend", "auto"))
        self._set_combo(self.cmb_fast_cli, _get(cfg, "fast.cli_model", "haiku"))
        self.ed_fast_sdk.setText(str(_get(cfg, "fast.sdk_model") or ""))
        self.cb_agent.setChecked(bool(_get(cfg, "agent.enabled", True)))
        self._set_combo(self.cmb_agent_model, _get(cfg, "agent.model", "haiku"))
        self.ed_filler.setText(str(_get(cfg, "agent.filler") or ""))
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
        _set(new, "island.motion", self.cmb_motion.currentText())
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
        _set(new, "tts.engine", self._combo_value(self.cmb_tts_backend))
        _set(new, "tts.device", self._combo_value(self.cmb_tts_device))
        _set(new, "tts.voice", self._combo_value(self.cmb_kokoro_voice) or "af_heart")
        _set(new, "tts.speed", round(self.sp_tts_speed.value(), 2))
        _set(new, "tts.rate", self.sp_rate.value())
        _set(new, "tts.sapi_voice", self._combo_value(self.cmb_voice) or None)

        _set(new, "fast.backend", self._combo_value(self.cmb_fast_backend))
        _set(new, "fast.cli_model", self._combo_value(self.cmb_fast_cli))
        _set(new, "fast.sdk_model", self._text(self.ed_fast_sdk, "fast.sdk_model"))
        _set(new, "agent.enabled", self.cb_agent.isChecked())
        _set(new, "agent.model", self._combo_value(self.cmb_agent_model))
        _set(new, "agent.filler", self._text(self.ed_filler, "agent.filler"))
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
            self._set_status("Not saved. " + " ".join(errors), "#E5646A")
            return False
        changed = config.diff(self._orig, new)
        if not changed:
            msg = "No changes to save."
            if warnings:
                msg += " " + " ".join(warnings)
            self._set_status(msg, "#9A9A9A")
            return True
        try:
            config.ensure_user_config(self.config_path)
            config.save(new, self.config_path)
        except OSError as e:
            self._set_status(f"Couldn't write the config file: {e}", "#E5646A")
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
            self._set_status("Saved. Applied now." + extra, "#6CCB7A" if not warnings else "#E5A04A")
        return True

    # -- status ------------------------------------------------------------------
    def _set_status(self, text: str, color: str = "#9A9A9A"):
        from html import escape
        self.status.setText(f"<span style='color:{color}'>{escape(text)}</span>" if text else "")

    def _show_restart(self, keys: list[str], head: str, extra: str = ""):
        self._set_status(f"{head}. Needs a restart to apply: {', '.join(keys)}.{extra}", "#E5A04A")
        self.btn_restart.show()

    # -- slots ------------------------------------------------------------------------
    _filling_scale = False

    def _scale_changed(self, *_):
        """The island size slider: shown on the running island as you drag (saved on Save)."""
        auto = self.cb_scale_auto.isChecked()
        self.sl_scale.setEnabled(not auto)
        self.lb_scale.setText("auto" if auto else f"{self.sl_scale.value() * 5} %")
        if not self._filling_scale:
            self.island_preview.emit("auto" if auto else self.sl_scale.value() * 5 / 100)
    def _hotkey_changed(self, *_):
        if self._recorder.active:
            return
        text = self.ed_hotkey.text().strip()
        if not text:
            self.lb_hotkey.setText("No push-to-talk hotkey.")
            self.lb_hotkey.setStyleSheet("color: #9A9A9A;")
            return
        try:
            norm = hotkey.normalize(text)
            self.lb_hotkey.setText(f"Will be saved as: {norm}")
            self.lb_hotkey.setStyleSheet("color: #9A9A9A;")
        except ValueError as e:
            self.lb_hotkey.setText(f"Invalid hotkey: {e}")
            self.lb_hotkey.setStyleSheet("color: #E5646A;")

    def _record_hotkey(self):
        if self._recorder.active:
            self._recorder.stop(None)
        else:
            self.lb_hotkey.setText("Press the combination now (Esc cancels).")
            self.lb_hotkey.setStyleSheet(f"color: {ACCENT};")
            self._recorder.start()

    def _perm_changed(self, *_):
        bypass = self._combo_value(self.cmb_perm) == "bypassPermissions"
        self.lb_bypass.setVisible(bypass)
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
            self._set_status(f"Couldn't write the config file: {e}", "#E5646A")
            return f"Couldn't write the config file: {e}"
        changed = config.diff(self._orig, new)
        self._orig = copy.deepcopy(new)
        self._set_combo(self.cmb_wake, _get(new, "wake.model"))
        self.sp_threshold.setValue(float(_get(new, "wake.threshold", 0.5)))
        self.saved.emit(copy.deepcopy(new), list(changed))
        self._wake_status(new)
        self._set_status(msg, "#6CCB7A")
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
        item.setForeground(QColor("#EDEDED") if ok else QColor("#E5A04A"))
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
            self._set_status(f"Couldn't play a test sentence: {e}", "#E5646A")

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
        e.accept()
