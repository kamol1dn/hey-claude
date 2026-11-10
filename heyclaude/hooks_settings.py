"""Settings -> Sessions (M7): install / uninstall the Claude Code hooks that let
hey-claude watch every Claude Code session, and the approval options.

Installing changes the user's global `~/.claude/settings.json`, so it never
happens without a click on Install in a dialog that shows the exact change.
Our hook entries are merged in next to any existing hooks, and Uninstall puts
the file back exactly as it was (hookrelay/install.py)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QGroupBox, QHBoxLayout, QLabel, QPlainTextEdit,
                               QPushButton, QSpinBox, QVBoxLayout)


def _get(cfg: dict, dotted: str, default=None):
    cur = cfg
    for k in dotted.split("."):
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


class ChangeDialog(QDialog):
    """Shows the exact change to settings.json; Install / Uninstall only on a click."""

    def __init__(self, parent, title: str, path, diff: str, ok_label: str):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(760, 520)
        v = QVBoxLayout(self)
        head = QLabel(f"This changes <b>{path}</b> (a dated backup is made next to it first):")
        head.setTextFormat(Qt.RichText)
        head.setWordWrap(True)
        v.addWidget(head)
        ed = QPlainTextEdit()
        ed.setReadOnly(True)
        ed.setLineWrapMode(QPlainTextEdit.NoWrap)
        f = QFont("Cascadia Mono")
        f.setStyleHint(QFont.Monospace)
        f.setPointSize(9)
        ed.setFont(f)
        ed.setPlainText(diff or "(no change)")
        v.addWidget(ed, 1)
        bb = QDialogButtonBox()
        self.ok = bb.addButton(ok_label, QDialogButtonBox.AcceptRole)
        bb.addButton(QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        self.ok.setEnabled(bool(diff))
        v.addWidget(bb)


class SessionsTab:
    """Built into SettingsWindow (its _scroll_tab / _row / _form / _help helpers)."""

    def __init__(self, win):
        self.win = win
        v = win._scroll_tab("Sessions")
        v.addWidget(win._help(
            "hey-claude can watch every Claude Code session on this PC (terminal, VS Code, the Claude "
            "desktop app and its own voice tasks): the island shows what each one is doing, the diff of "
            "its edits, a \"done\" moment, and Allow / Deny / Always cards for its permission prompts. "
            "It works through Claude Code hooks in your global settings.json, which call a tiny relay "
            "that talks to hey-claude over a pipe only your Windows user can open. When hey-claude isn't "
            "running, the relay does nothing and Claude Code asks in the terminal as usual."))

        g = QGroupBox("Claude Code hooks")
        lay = QVBoxLayout(g)
        self.lb_status = QLabel("")
        self.lb_status.setWordWrap(True)
        self.lb_status.setTextFormat(Qt.RichText)
        lay.addWidget(self.lb_status)
        row = QHBoxLayout()
        self.btn_install = QPushButton("Install hooks…", clicked=self.install)
        self.btn_uninstall = QPushButton("Uninstall hooks…", clicked=self.uninstall)
        row.addWidget(self.btn_install)
        row.addWidget(self.btn_uninstall)
        row.addStretch(1)
        lay.addLayout(row)
        v.addWidget(g)

        g = QGroupBox("Approvals and the island")
        f = win._form(g)
        self.cb_enabled = QCheckBox("Watch Claude Code sessions")
        win._row(f, "hooks.enabled", "", self.cb_enabled)
        self.cb_approvals = QCheckBox("Ask on the island (Allow / Deny / Always)")
        self.cb_approvals.setToolTip("hey-claude's own agent asks here too, for any tool off its allowed list "
                                     "(screen control, sending on Telegram...). Off: those are refused.")
        win._row(f, "hooks.approvals", "", self.cb_approvals)
        self.sp_timeout = QSpinBox(minimum=10, maximum=108, suffix=" s")
        self.sp_timeout.setToolTip("After this the card goes away and Claude Code asks in the terminal "
                                   "(a headless task or hey-claude's own agent: denied). At most 108 s, under the hook's own limit.")
        win._row(f, "hooks.approval_timeout_s", "Then the terminal asks after", self.sp_timeout)
        self.cb_voice = QCheckBox("Answer by voice (\"yes, allow that\"; dangerous ones need a click)")
        win._row(f, "hooks.voice_approvals", "", self.cb_voice)
        self.cb_done = QCheckBox("Show a \"done\" moment when a session stops")
        win._row(f, "hooks.done_flash", "", self.cb_done)
        v.addWidget(g)
        v.addStretch(1)
        self.refresh()

    # -- config ---------------------------------------------------------------------
    def fill(self, cfg: dict):
        self.cb_enabled.setChecked(bool(_get(cfg, "hooks.enabled", True)))
        self.cb_approvals.setChecked(bool(_get(cfg, "hooks.approvals", True)))
        try:
            self.sp_timeout.setValue(int(float(_get(cfg, "hooks.approval_timeout_s", 100))))
        except (TypeError, ValueError):
            self.sp_timeout.setValue(100)
        self.cb_voice.setChecked(bool(_get(cfg, "hooks.voice_approvals", True)))
        self.cb_done.setChecked(bool(_get(cfg, "hooks.done_flash", True)))

    def collect(self, new: dict):
        h = new.setdefault("hooks", {})
        h["enabled"] = self.cb_enabled.isChecked()
        h["approvals"] = self.cb_approvals.isChecked()
        h["approval_timeout_s"] = int(self.sp_timeout.value())
        h["voice_approvals"] = self.cb_voice.isChecked()
        h["done_flash"] = self.cb_done.isChecked()

    # -- install / uninstall -----------------------------------------------------------
    def refresh(self):
        try:
            from .hookrelay import install
            st = install.status()
            path = install.settings_path()
        except Exception as e:
            self.lb_status.setText(f"<span style='color:#E08A6B'>Can't read the Claude Code settings: {e}</span>")
            self.btn_install.setEnabled(False)
            self.btn_uninstall.setEnabled(False)
            return
        if st.get("installed"):
            n = len(st.get("events") or [])
            stale = "" if st.get("relay_current", True) else \
                " <span style='color:#E0B04A'>The relay is out of date: Install again to update it.</span>"
            self.lb_status.setText(f"<b style='color:#6CCB7A'>Installed</b> ({n} events) in {path}.{stale}")
            self.btn_install.setText("Reinstall hooks…")
        else:
            self.lb_status.setText(f"<b>Not installed.</b> Install adds hey-claude's hooks to {path}, "
                                   "next to any hooks you already have.")
            self.btn_install.setText("Install hooks…")
        self.btn_uninstall.setEnabled(bool(st.get("installed")))
        self.btn_install.setEnabled(True)

    def _run(self, action: str):
        from .hookrelay import install
        try:
            if action == "install":
                relay = install.deploy_relay()        # the relay first: hooks must never point at a missing file
                plan = install.plan_install(relay=relay)
            else:
                plan = install.plan_uninstall()
        except Exception as e:
            self.win._set_status(f"Couldn't prepare the change: {e}", "#E08A6B")
            return
        if not plan.diff:
            self.win._set_status("Nothing to change.", "#9A9A9A")
            self.refresh()
            return
        title = "Install the Claude Code hooks" if action == "install" else "Uninstall the Claude Code hooks"
        dlg = ChangeDialog(self.win, title, plan.path, plan.diff, "Install" if action == "install" else "Uninstall")
        if dlg.exec() != QDialog.Accepted:
            self.win._set_status("Nothing changed.", "#9A9A9A")
            return
        try:
            backup = install.apply(plan)
        except Exception as e:
            self.win._set_status(f"Not changed: {e}", "#E08A6B")
            self.refresh()
            return
        done = "Installed" if action == "install" else "Uninstalled"
        self.win._set_status(f"{done}. Backup: {backup}" if backup else f"{done}.", "#6CCB7A")
        self.refresh()

    def install(self):
        self._run("install")

    def uninstall(self):
        self._run("uninstall")
