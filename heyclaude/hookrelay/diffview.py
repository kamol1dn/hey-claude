"""A watched session's edits, as diffs (M7).

    DiffWindow("fix login bug", hub.diffs(session_id)).show()

`diffs` (hookrelay/diff.py, newest last): [{"file", "tool", "plus", "minus",
"unified" (may be "" when huge), "huge", "t"}]. On the left, one row per edit
with its +N −M; on the right, the selected edit's unified diff (+ green,
− red, @@ dim). A huge edit shows its counts only. Dark, like the task log
window (island.LogWindow).
"""
from __future__ import annotations

import os
import time

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat
from PySide6.QtWidgets import (QLabel, QListWidget, QListWidgetItem, QPlainTextEdit, QSplitter,
                               QVBoxLayout, QWidget)

GREEN, RED, DIM, HUNK, FG = "#6CCB7A", "#E5646A", "#7A7570", "#8FA8C8", "#EDEDED"
MINUS = "−"


def _fmt(color: str, bg: str | None = None, bold: bool = False) -> QTextCharFormat:
    f = QTextCharFormat()
    f.setForeground(QColor(color))
    if bg:
        f.setBackground(QColor(bg))
    if bold:
        f.setFontWeight(QFont.Bold)
    return f


class DiffHighlighter(QSyntaxHighlighter):
    """Unified diff colours: +lines green, -lines red, @@ hunks blue-grey, file headers dim."""

    def __init__(self, doc):
        super().__init__(doc)
        self.add = _fmt(GREEN, "#11261A")
        self.rem = _fmt(RED, "#2A1416")
        self.hunk = _fmt(HUNK)
        self.head = _fmt(DIM, bold=True)
        self.note = _fmt(DIM)

    def highlightBlock(self, text: str):
        if text.startswith(("+++", "---")):
            f = self.head
        elif text.startswith("@@"):
            f = self.hunk
        elif text.startswith("+"):
            f = self.add
        elif text.startswith("-"):
            f = self.rem
        elif text.startswith("\\") or text.startswith("#"):
            f = self.note
        else:
            return
        self.setFormat(0, len(text), f)


def counts(d: dict) -> str:
    return f"+{int(d.get('plus') or 0)} {MINUS}{int(d.get('minus') or 0)}"


def entry_label(d: dict) -> str:
    name = os.path.basename(str(d.get("file") or "")) or "(file)"
    return f"{name}   {counts(d)}"


def entry_text(d: dict) -> str:
    """What the right pane shows for one edit."""
    path = str(d.get("file") or "")
    when = ""
    try:
        if d.get("t"):
            when = time.strftime("%H:%M:%S", time.localtime(float(d["t"])))
    except (TypeError, ValueError, OverflowError):
        pass
    head = f"# {path}\n# {d.get('tool') or 'Edit'}{'  ·  ' + when if when else ''}  ·  {counts(d)}\n"
    unified = str(d.get("unified") or "")
    if d.get("huge") or not unified:
        why = "too large to show" if d.get("huge") else "no text diff"
        return head + f"#\n# {why}: +{int(d.get('plus') or 0)} lines added, {int(d.get('minus') or 0)} removed\n"
    return head + "\n" + unified


class DiffWindow(QWidget):
    def __init__(self, title: str, diffs: list[dict], parent=None):
        super().__init__(parent, Qt.Window)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.title = title or "session"
        self.resize(980, 600)
        self.setStyleSheet(f"""
            QWidget {{ background: #141414; color: {FG}; font-family: 'Segoe UI'; }}
            QLabel#head {{ font-size: 11pt; padding: 2px 2px 6px 2px; }}
            QListWidget {{ background: #0F0F0F; border: 1px solid #2A2A2A; font-family: Consolas, monospace; font-size: 9.5pt;
                           outline: none; }}
            QListWidget::item {{ padding: 5px 6px; border-bottom: 1px solid #1C1C1C; }}
            QListWidget::item:selected {{ background: #2A211D; color: {FG}; }}
            QPlainTextEdit {{ background: #0C0C0C; border: 1px solid #2A2A2A; font-family: Consolas, monospace; font-size: 10pt; }}
            QSplitter::handle {{ background: #141414; width: 6px; }}
        """)
        lay = QVBoxLayout(self)
        self.head = QLabel(objectName="head")
        self.head.setTextFormat(Qt.RichText)
        lay.addWidget(self.head)
        split = QSplitter(Qt.Horizontal)
        self.files = QListWidget()
        self.files.setMinimumWidth(220)
        self.view = QPlainTextEdit(readOnly=True)
        self.view.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.highlighter = DiffHighlighter(self.view.document())
        split.addWidget(self.files)
        split.addWidget(self.view)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([280, 700])
        lay.addWidget(split, 1)
        self.files.currentRowChanged.connect(self._select)
        self.diffs: list[dict] = []
        self.set_diffs(diffs)

    def set_diffs(self, diffs: list[dict]):
        self.diffs = [d for d in (diffs or []) if isinstance(d, dict)]
        plus = sum(int(d.get("plus") or 0) for d in self.diffs)
        minus = sum(int(d.get("minus") or 0) for d in self.diffs)
        nfiles = len({str(d.get("file") or "") for d in self.diffs})
        self.setWindowTitle(f"Diffs — {self.title}")
        self.head.setText(f"<b>{_esc(self.title)}</b>&nbsp;&nbsp;<span style='color:{DIM}'>{len(self.diffs)} edit"
                          f"{'s' if len(self.diffs) != 1 else ''} in {nfiles} file{'s' if nfiles != 1 else ''}</span>"
                          f"&nbsp;&nbsp;<span style='color:{GREEN}'>+{plus}</span> <span style='color:{RED}'>{MINUS}{minus}</span>")
        self.files.blockSignals(True)
        self.files.clear()
        for d in self.diffs:
            it = QListWidgetItem(entry_label(d))
            it.setToolTip(str(d.get("file") or ""))
            if d.get("huge"):
                it.setForeground(QColor(DIM))
            self.files.addItem(it)
        self.files.blockSignals(False)
        if self.diffs:
            self.files.setCurrentRow(len(self.diffs) - 1)       # the newest edit
            self._select(len(self.diffs) - 1)
        else:
            self.view.setPlainText("# no edits recorded for this session yet")

    def _select(self, i: int):
        if 0 <= i < len(self.diffs):
            self.view.setPlainText(entry_text(self.diffs[i]))
            self.view.verticalScrollBar().setValue(0)

    def selected_text(self) -> str:
        return self.view.toPlainText()


def _esc(s: str) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
