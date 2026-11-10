"""Settings -> Sessions (M7): install / uninstall the Claude Code hooks that let
hey-claude watch every Claude Code session, and the approval options.

Installing changes the user's global `~/.claude/settings.json`, so it never
happens without a click on Install in a dialog that shows the exact change.
Our hook entries are merged in next to any existing hooks, and Uninstall puts
the file back exactly as it was (hookrelay/install.py)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QPlainTextEdit, QSpinBox, QVBoxLayout, QWidget

from . import settings_kit as kit
from . import settings_theme as th


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
        v.setContentsMargins(kit.S[5], kit.S[5], kit.S[5], kit.S[4])
        v.setSpacing(kit.S[3])
        head = QLabel(f"This changes <b>{path}</b>. A dated backup is made next to it first.")
        head.setTextFormat(Qt.RichText)
        head.setWordWrap(True)
        v.addWidget(head)
        ed = QPlainTextEdit()
        ed.setReadOnly(True)
        ed.setLineWrapMode(QPlainTextEdit.NoWrap)
        ed.setPlainText(diff or "(no change)")
        v.addWidget(ed, 1)
        bb = QDialogButtonBox()
        self.ok = bb.addButton(ok_label, QDialogButtonBox.AcceptRole)
        self.ok.setObjectName("primary")
        bb.addButton(QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        self.ok.setEnabled(bool(diff))
        v.addWidget(bb)


class SessionsTab:
    """Settings > Sessions, built with SettingsWindow's page helpers (_page / _row / _switch)."""

    def __init__(self, win):
        self.win = win
        p = win._page("Sessions", "Watch every Claude Code session on this PC from the island: what each one is "
                      "doing, the diff of its edits, and its permission prompts.", "sessions")

        g = p.add_group("Claude Code hooks", "Hooks in your global settings.json call a tiny relay that only your "
                                             "Windows user can reach. When hey-claude isn't running, Claude Code "
                                             "asks in the terminal as usual.")
        self.lb_status = QLabel("")
        self.lb_status.setObjectName("value")
        self.lb_status.setWordWrap(True)
        self.lb_status.setTextFormat(Qt.RichText)
        self.btn_install = kit.button("Install hooks…", "primary", self.install,
                                      tip="Shows the exact change to settings.json first.")
        self.btn_uninstall = kit.button("Uninstall hooks…", "danger", self.uninstall,
                                        tip="Puts settings.json back as it was (a backup is made first).")
        box = QWidget()
        bl = QVBoxLayout(box)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(kit.S[3])
        bl.addWidget(self.lb_status)
        bl.addWidget(kit.buttons_row(self.btn_install, self.btn_uninstall))
        g.add_widget(box, vpad=kit.S[4])

        g = p.add_group("Approvals and the island")
        self.cb_enabled = win._switch(g, "hooks.enabled", "Watch Claude Code sessions",
                                      desc="The terminal, VS Code, the Claude desktop app and hey-claude's own tasks.")
        self.cb_approvals = win._switch(g, "hooks.approvals", "Ask on the island",
                                        desc="Allow / Deny / Always cards for permission prompts.")
        self.cb_approvals.setToolTip("hey-claude's own agent asks here too, for any tool off its allowed list "
                                     "(screen control, sending on Telegram...). Off: those are refused.")
        self.sp_timeout = QSpinBox(minimum=10, maximum=108, suffix=" s")
        self.sp_timeout.setToolTip("After this the card goes away and Claude Code asks in the terminal "
                                   "(a headless task or hey-claude's own agent: denied). At most 108 s, under the hook's own limit.")
        win._row(g, "hooks.approval_timeout_s", "Then the terminal asks after", self.sp_timeout,
                 desc="The card goes away; a headless task or hey-claude's own agent is denied.")
        self.cb_voice = win._switch(g, "hooks.voice_approvals", "Answer by voice",
                                    desc="\"Yes, allow that.\" Dangerous ones still need a click.")
        self.cb_done = win._switch(g, "hooks.done_flash", "Show a \"done\" moment",
                                   desc="When a session stops.")
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
            self.lb_status.setText(f"<span style='color:{th.T['bad']}'>Can't read the Claude Code settings: {e}</span>")
            self.btn_install.setEnabled(False)
            self.btn_uninstall.setEnabled(False)
            return
        t = th.T
        if st.get("installed"):
            n = len(st.get("events") or [])
            stale = "" if st.get("relay_current", True) else \
                f"<br><span style='color:{t['warn']}'>The relay is out of date: install again to update it.</span>"
            self.lb_status.setText(f"<span style='color:{t['ok']}'>&#9679;</span>&nbsp; <b>Installed</b> "
                                   f"<span style='color:{t['dim']}'>({n} events) in {path}</span>{stale}")
            self.btn_install.setText("Reinstall hooks…")
        else:
            self.lb_status.setText(f"<span style='color:{t['faint']}'>&#9679;</span>&nbsp; <b>Not installed</b><br>"
                                   f"<span style='color:{t['dim']}'>Install adds hey-claude's hooks to {path}, next to "
                                   "any hooks you already have.</span>")
            self.btn_install.setText("Install hooks…")
        # Install is the primary action until it's done; then Reinstall is a quiet secondary one.
        self.btn_install.setObjectName("" if st.get("installed") else "primary")
        self.btn_install.style().unpolish(self.btn_install)
        self.btn_install.style().polish(self.btn_install)
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
            self.win._set_status(f"Couldn't prepare the change: {e}", "bad")
            return
        if not plan.diff:
            self.win._set_status("Nothing to change.", "dim")
            self.refresh()
            return
        title = "Install the Claude Code hooks" if action == "install" else "Uninstall the Claude Code hooks"
        dlg = ChangeDialog(self.win, title, plan.path, plan.diff, "Install" if action == "install" else "Uninstall")
        if dlg.exec() != QDialog.Accepted:
            self.win._set_status("Nothing changed.", "dim")
            return
        try:
            backup = install.apply(plan)
        except Exception as e:
            self.win._set_status(f"Not changed: {e}", "bad")
            self.refresh()
            return
        done = "Installed" if action == "install" else "Uninstalled"
        self.win._set_status(f"{done}. Backup: {backup}" if backup else f"{done}.", "ok")
        self.refresh()

    def install(self):
        self._run("install")

    def uninstall(self):
        self._run("uninstall")
