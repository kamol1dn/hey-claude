"""Settings → Character (ROADMAP M6): pick a pack, see it live, tune its idle antics.

A gallery of the installed packs (plus the island theme's own ember), a live preview
with a button per registry state, where it shows (island / desktop / both), the
companion's size, and Idle antics: how often, a checkbox per antic and a "play" button.
Picking a pack shows it on the running island at once (`preview_pack`); Save keeps it,
closing without saving puts the saved one back.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap, QRadialGradient
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
                               QListWidget, QListWidgetItem, QPushButton, QSpinBox, QVBoxLayout, QWidget)

from . import characters as CH
from . import states as S

ROOT = Path(__file__).resolve().parent.parent
FREQ = [("Off", "off"), ("Rare (about every 10 min)", "rare"), ("Normal (about every 4 min)", "normal"),
        ("Lively (about every 1.5 min)", "lively")]
WHERE = [("In the island", "island"), ("On the desktop (a companion above the taskbar)", "desktop"),
         ("Both", "both")]
PREVIEW_STATES = ["idle", "listening", "thinking", "talking", "happy", "confused", "working", "sending", "off",
                  "drowsy", "asleep", "waking", "woke", "done", "error", "approval_needed"]


def _ember_icon(size: int = 72) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    c = size / 2
    g = QRadialGradient(c, c, size * 0.42)
    g.setColorAt(0, QColor(217, 119, 87, 120))
    g.setColorAt(1, QColor(217, 119, 87, 0))
    p.setBrush(g)
    p.setPen(Qt.NoPen)
    p.drawEllipse(0, 0, size, size)
    p.setBrush(QColor("#D97757"))
    p.drawEllipse(int(c - 6), int(c - 6), 12, 12)
    p.setBrush(QColor("#EC9A78"))
    p.drawEllipse(int(c - 2.5), int(c - 2.5), 5, 5)
    p.end()
    return QIcon(pm)


class CharacterTab(QWidget):
    preview_pack = Signal(object)        # a pack id to show on the island now, or None = back to the saved one
    play_antic = Signal(str)             # "play now" on the running island

    def __init__(self, parent=None):
        super().__init__(parent)
        self.reg = CH.registry(refresh=True)
        self._filling = False
        self._antic_boxes: dict[str, QCheckBox] = {}
        self._off: set[str] = set()
        self._quick = None
        v = QVBoxLayout(self)
        v.setContentsMargins(14, 10, 14, 14)
        v.setSpacing(8)

        g = QGroupBox("Character")
        gl = QVBoxLayout(g)
        self.gallery = QListWidget()
        self.gallery.setViewMode(QListWidget.IconMode)
        self.gallery.setIconSize(QSize(72, 72))
        self.gallery.setGridSize(QSize(104, 110))
        self.gallery.setMovement(QListWidget.Static)
        self.gallery.setResizeMode(QListWidget.Adjust)
        self.gallery.setFixedHeight(126)
        self.gallery.setWordWrap(True)
        it = QListWidgetItem(_ember_icon(), "Ember\n(no character)")
        it.setData(Qt.UserRole, CH.NONE)
        it.setToolTip("The island theme's own ember, no character.")
        self.gallery.addItem(it)
        for pid, man in self.reg.items():
            icon = QIcon(man["preview"]) if man.get("preview") else QIcon()
            it = QListWidgetItem(icon, man["name"] + ("" if man.get("builtin") else "\n(local)"))
            it.setData(Qt.UserRole, pid)
            it.setToolTip(f"{man['name']}, {man.get('kind', '')}. {man.get('line', '')}\nby {man.get('author', '?')}")
            self.gallery.addItem(it)
        self.gallery.currentItemChanged.connect(self._picked)
        gl.addWidget(self.gallery)
        self.lb_line = QLabel("")
        self.lb_line.setWordWrap(True)
        self.lb_line.setObjectName("help")
        gl.addWidget(self.lb_line)
        f = QFormLayout()
        f.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.cmb_where = QComboBox()
        for lab, val in WHERE:
            self.cmb_where.addItem(lab, val)
        f.addRow("Where", self.cmb_where)
        self.sp_size = QSpinBox()
        self.sp_size.setRange(64, 240)
        self.sp_size.setSuffix(" px")
        f.addRow("Desktop size", self.sp_size)
        gl.addLayout(f)
        v.addWidget(g)

        self.g_prev = QGroupBox("Live preview")
        pl = QVBoxLayout(self.g_prev)
        self.prev_holder = QVBoxLayout()
        pl.addLayout(self.prev_holder)
        self.states_grid = QGridLayout()
        self.states_grid.setSpacing(4)
        pl.addLayout(self.states_grid)
        v.addWidget(self.g_prev)

        self.g_ant = QGroupBox("Idle antics")
        al = QVBoxLayout(self.g_ant)
        fa = QFormLayout()
        self.cmb_freq = QComboBox()
        for lab, val in FREQ:
            self.cmb_freq.addItem(lab, val)
        fa.addRow("How often", self.cmb_freq)
        al.addLayout(fa)
        al.addWidget(self._help("Little things it does on its own when nothing has happened for a while. Never while "
                                "you type, in a full-screen app or game, in calm motion, on battery saver, or while the "
                                "island is hidden; the wake word, the hotkey or the pointer ends one at once."))
        self.antics_grid = QGridLayout()
        self.antics_grid.setSpacing(4)
        al.addLayout(self.antics_grid)
        v.addWidget(self.g_ant)
        v.addWidget(self._help("Add your own: a folder in state/characters/ (local) or a pull request to characters/ "
                               "(characters/README.md)."))
        v.addStretch(1)

    @staticmethod
    def _help(text: str) -> QLabel:
        lab = QLabel(text)
        lab.setObjectName("help")
        lab.setWordWrap(True)
        return lab

    # -- config ----------------------------------------------------------------------
    def fill(self, cfg: dict):
        cc = ((cfg.get("island") or {}).get("character") or {})
        self._filling = True
        pid = str(cc.get("pack", CH.NONE) or CH.NONE)
        self._off = set(cc.get("antics_off") or [])
        for i in range(self.gallery.count()):
            if self.gallery.item(i).data(Qt.UserRole) == pid:
                self.gallery.setCurrentRow(i)
                break
        else:
            self.gallery.setCurrentRow(0)
        i = self.cmb_where.findData(str(cc.get("where", "island")))
        self.cmb_where.setCurrentIndex(max(0, i))
        self.sp_size.setValue(int(cc.get("desktop_size", 120) or 120))
        i = self.cmb_freq.findData(str(cc.get("antics", "normal")))
        self.cmb_freq.setCurrentIndex(max(0, i))
        self._filling = False
        self._show(self.current())

    def collect(self, new: dict):
        isl = new.setdefault("island", {})
        cc = isl.setdefault("character", {})
        cc["pack"] = self.current()
        cc["where"] = self.cmb_where.currentData()
        cc["desktop_size"] = int(self.sp_size.value())
        cc["antics"] = self.cmb_freq.currentData()
        man = self.reg.get(self.current())
        if man:
            ids = {a["id"] for a in man.get("antics", [])}
            off = (self._off - ids) | {aid for aid, cb in self._antic_boxes.items() if not cb.isChecked()}
        else:
            off = set(self._off)
        cc["antics_off"] = sorted(off)

    def current(self) -> str:
        it = self.gallery.currentItem()
        return it.data(Qt.UserRole) if it else CH.NONE

    # -- ui ----------------------------------------------------------------------------
    def _picked(self, *_):
        pid = self.current()
        self._show(pid)
        if not self._filling:
            self.preview_pack.emit(pid)

    def _clear(self, grid):
        while grid.count():
            it = grid.takeAt(0)
            if it.widget():
                it.widget().deleteLater()

    def _show(self, pid: str):
        man = self.reg.get(pid)
        if man is None:
            self.lb_line.setText("The island's own ember: no character. Pick one above to give the island a face.")
        else:
            self.lb_line.setText(f"<b>{man['name']}</b>, {man.get('kind', '')}. {man.get('line', '')}")
        self.g_prev.setVisible(man is not None)
        self.g_ant.setVisible(man is not None)
        self.sp_size.setEnabled(man is not None)
        self.cmb_where.setEnabled(man is not None)
        self._clear(self.states_grid)
        # remember unticked antics of the pack we're leaving
        for aid, cb in self._antic_boxes.items():
            (self._off.discard if cb.isChecked() else self._off.add)(aid)
        self._antic_boxes = {}
        self._clear(self.antics_grid)
        if man is None:
            self._set_preview(None)
            return
        self._set_preview(man)
        for i, st in enumerate(PREVIEW_STATES):
            shown = CH.resolve(man, st, log=None)
            b = QPushButton(st if shown == st else f"{st} → {shown}")
            b.setToolTip(next((s["description"] for s in S.load()["states"] if s["id"] == st), ""))
            b.clicked.connect(lambda _=False, st=st: self._preview_state(st))
            self.states_grid.addWidget(b, i // 5, i % 5)
        for i, a in enumerate(man.get("antics", [])):
            cb = QCheckBox(a["name"])
            cb.setChecked(a["id"] not in self._off)
            w = ", ".join(a.get("where", CH.WHERE))
            cb.setToolTip(f"{a['id']} · {a['duration']} s · {w}" + (f" · when {a['when']}" if a.get("when") else ""))
            play = QPushButton("▶")
            play.setFixedWidth(30)
            play.setToolTip("Play it now (here and on the island)")
            play.clicked.connect(lambda _=False, a=a: self._play(a))
            self._antic_boxes[a["id"]] = cb
            r, c = divmod(i, 2)
            self.antics_grid.addWidget(cb, r, c * 2)
            self.antics_grid.addWidget(play, r, c * 2 + 1)

    # -- the live preview (a small QQuickWidget with a CharacterHost) --------------------
    def _set_preview(self, man):
        if man is None:
            if self._quick is not None:
                self._quick.hide()
            return
        try:
            if self._quick is None:
                from PySide6.QtQuickWidgets import QQuickWidget
                q = QQuickWidget()
                q.engine().addImportPath(str(CH.KIT_IMPORT_DIR))
                q.setResizeMode(QQuickWidget.SizeRootObjectToView)
                q.setClearColor(QColor("#080808"))
                q.setSource(QUrl.fromLocalFile(str(ROOT / "tools" / "character_lab.qml")))
                q.setFixedHeight(200)
                self.prev_holder.addWidget(q)
                self._quick = q
            from PySide6.QtQuick import QQuickItem
            host = self._quick.rootObject().findChild(QQuickItem, "host")
            host.setProperty("source", QUrl())
            host.setProperty("manifest", {"bounds": man["bounds"], "desktop_bounds": man["desktop_bounds"],
                                          "oneshot": man.get("oneshot", {})})
            host.setProperty("small", False)
            host.setProperty("size", 100)
            host.setProperty("budgets", dict(S.load().get("budgets", {})))
            host.setProperty("stateId", "idle")
            host.setProperty("source", QUrl.fromLocalFile(man["qml"]))
            self._host = host
            self._quick.show()
        except Exception as e:                       # no QtQuickWidgets / no GPU: the gallery still works
            print(f"[settings] character preview unavailable: {e}")
            self._host = None

    def _preview_state(self, st: str):
        man = self.reg.get(self.current())
        if not man or getattr(self, "_host", None) is None:
            return
        self._host.setProperty("stateId", CH.resolve(man, st, log=None))
        lvl = 0.6 if st in ("listening", "holding", "followup", "dictating") else 0.0
        self._host.setProperty("level", lvl)
        self._host.setProperty("amplitude", 0.6 if st == "talking" else 0.0)

    def _play(self, a: dict):
        if getattr(self, "_host", None) is not None:
            from PySide6.QtCore import QMetaObject, Q_ARG, Q_RETURN_ARG
            self._host.setProperty("stateId", "idle")
            QMetaObject.invokeMethod(self._host, "playAntic", Qt.DirectConnection, Q_RETURN_ARG("QVariant"),
                                     Q_ARG("QVariant", a["id"]), Q_ARG("QVariant", float(a["duration"])))
        self.play_antic.emit(a["id"])
