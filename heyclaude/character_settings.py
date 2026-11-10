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
from PySide6.QtWidgets import (QComboBox, QGridLayout, QLabel, QListWidget, QListWidgetItem, QSpinBox,
                               QStyledItemDelegate, QVBoxLayout, QWidget)

from . import characters as CH
from . import settings_kit as kit
from . import settings_theme as th
from . import states as S

ROOT = Path(__file__).resolve().parent.parent
FREQ = [("Off", "off"), ("Rare (about every 10 min)", "rare"), ("Normal (about every 4 min)", "normal"),
        ("Lively (about every 1.5 min)", "lively")]
WHERE = [("In the island", "island"), ("On the desktop (a companion above the taskbar)", "desktop"),
         ("Both", "both")]
TILE_W, TILE_H, TILE_ICON = 100, 116, 56          # a gallery tile: icon over a two-line name
PREVIEW_STATES = ["idle", "listening", "thinking", "talking", "happy", "confused", "working", "sending", "off",
                  "drowsy", "asleep", "waking", "woke", "done", "error", "approval_needed"]


def _ember_icon(size: int = 72) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    c = size / 2
    g = QRadialGradient(c, c, size * 0.42)
    glow = QColor(th.T["live"])
    glow.setAlpha(120)
    g.setColorAt(0, glow)
    glow.setAlpha(0)
    g.setColorAt(1, glow)
    p.setBrush(g)
    p.setPen(Qt.NoPen)
    p.drawEllipse(0, 0, size, size)
    p.setBrush(QColor(th.T["live"]))
    p.drawEllipse(int(c - 6), int(c - 6), 12, 12)
    p.setBrush(QColor(th.T["hot"]))
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
        self._antic_boxes: dict[str, kit.Switch] = {}
        self._off: set[str] = set()
        self._quick = None
        self.field_rows: list = []           # SettingsWindow lines these up with the rest of the page
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(kit.S[5])

        g = kit.Group("Character")
        self.gallery = QListWidget()
        self.gallery.setObjectName("gallery")
        self.gallery.setViewMode(QListWidget.IconMode)
        self.gallery.setFlow(QListWidget.LeftToRight)
        self.gallery.setWrapping(True)
        self.gallery.setIconSize(QSize(TILE_ICON, TILE_ICON))
        self.gallery.setGridSize(QSize(TILE_W + kit.S[2], TILE_H + kit.S[2]))
        self.gallery.installEventFilter(self)
        self.gallery.setMovement(QListWidget.Static)
        self.gallery.setResizeMode(QListWidget.Adjust)
        self.gallery.setUniformItemSizes(True)
        self.gallery.setWordWrap(True)
        self.gallery.setFrameShape(QListWidget.NoFrame)
        self.gallery.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.gallery.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.gallery.setSpacing(0)
        it = QListWidgetItem(_ember_icon(), "Ember")
        it.setData(Qt.UserRole, CH.NONE)
        it.setToolTip("The island theme's own ember, no character.")
        it.setSizeHint(QSize(TILE_W, TILE_H))
        self.gallery.addItem(it)
        for pid, man in self.reg.items():
            icon = QIcon(man["preview"]) if man.get("preview") else QIcon()
            it = QListWidgetItem(icon, man["name"] + ("" if man.get("builtin") else "\n(local)"))
            it.setData(Qt.UserRole, pid)
            it.setToolTip(f"{man['name']}, {man.get('kind', '')}. {man.get('line', '')}\nby {man.get('author', '?')}")
            it.setSizeHint(QSize(TILE_W, TILE_H))
            self.gallery.addItem(it)
        self.gallery.currentItemChanged.connect(self._picked)
        self.lb_line = QLabel("")
        self.lb_line.setWordWrap(True)
        self.lb_line.setObjectName("note")
        box = QWidget()
        bl = QVBoxLayout(box)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(kit.S[2])
        bl.addWidget(self.gallery)
        bl.addWidget(self.lb_line)
        g.add_widget(box, pad=True)
        self.cmb_where = self._combo(WHERE)
        self.field_rows.append(g.add(kit.SettingRow("Where it shows", "", (self.cmb_where,))))
        self.sp_size = QSpinBox()
        self.sp_size.setRange(64, 240)
        self.sp_size.setSuffix(" px")
        self.field_rows.append(g.add(kit.SettingRow("Size on the desktop", "", (self.sp_size,))))
        v.addWidget(g)

        self.g_prev = kit.Group("Live preview", "Try each state here; the island shows the character too.")
        prev = QWidget()
        pl = QVBoxLayout(prev)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(kit.S[3])
        self.prev_holder = QVBoxLayout()
        self.prev_holder.setContentsMargins(0, 0, 0, 0)
        pl.addLayout(self.prev_holder)
        self.states_grid = QGridLayout()
        self.states_grid.setHorizontalSpacing(kit.S[1] + 2)
        self.states_grid.setVerticalSpacing(kit.S[1] + 2)
        pl.addLayout(self.states_grid)
        self.g_prev.add_widget(prev, pad=True, vpad=kit.S[4])
        v.addWidget(self.g_prev)

        self.g_ant = kit.Group("Idle antics", "Little things it does on its own when nothing has happened for a "
                                              "while.")
        self.cmb_freq = self._combo(FREQ)
        self.field_rows.append(self.g_ant.add(kit.SettingRow("How often", "", (self.cmb_freq,))))
        self.g_ant.add_note("Never while you type, in a full-screen app or game, in calm motion, on battery saver, "
                            "or while the island is hidden. The wake word, the hotkey or the pointer ends one at "
                            "once.")
        self._ant_fixed = len(self.g_ant.rows)
        v.addWidget(self.g_ant)
        foot = QLabel("Add your own: a folder in state/characters/ (local) or a pull request to characters/ "
                      "(characters/README.md).")
        foot.setObjectName("note")
        foot.setWordWrap(True)
        v.addWidget(foot)

    @staticmethod
    def _combo(items) -> QComboBox:
        c = QComboBox()
        c.setItemDelegate(QStyledItemDelegate(c))
        c.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        c.setMinimumContentsLength(8)
        for lab, val in items:
            c.addItem(lab, val)
        return c

    def eventFilter(self, obj, ev):
        from PySide6.QtCore import QEvent
        if obj is self.gallery and ev.type() == QEvent.Resize:
            self._fit_gallery()
        return False

    def _fit_gallery(self):
        """The gallery is as tall as its rows of tiles: no scroll bar, nothing cut off."""
        per_row = max(1, self.gallery.width() // self.gallery.gridSize().width())
        rows = -(-self.gallery.count() // per_row)
        h = rows * self.gallery.gridSize().height() + 2
        if self.gallery.height() != h:
            self.gallery.setFixedHeight(h)

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
        for row in self.field_rows[:2]:              # where it shows / its size: a character's settings
            row.setEnabled(man is not None)
        self._clear(self.states_grid)
        # remember unticked antics of the pack we're leaving
        for aid, cb in self._antic_boxes.items():
            (self._off.discard if cb.isChecked() else self._off.add)(aid)
        self._antic_boxes = {}
        self.g_ant.clear(self._ant_fixed)
        if man is None:
            self._set_preview(None)
            return
        self._set_preview(man)
        self._chips = []
        for i, st in enumerate(PREVIEW_STATES):
            shown = CH.resolve(man, st, log=None)
            b = kit.button(st if shown == st else f"{st} → {shown}", "chip")
            b.setCheckable(True)
            b.setChecked(st == "idle")
            b.setToolTip(next((s["description"] for s in S.load()["states"] if s["id"] == st), ""))
            b.clicked.connect(lambda _=False, st=st, b=b: self._chip(st, b))
            self._chips.append(b)
            self.states_grid.addWidget(b, i // 6, i % 6)
        for a in man.get("antics", []):
            sw = kit.Switch(checked=a["id"] not in self._off)
            w = ", ".join(a.get("where", CH.WHERE))
            tip = f"{a['id']} · {a['duration']} s · {w}" + (f" · when {a['when']}" if a.get("when") else "")
            play = kit.button("", "icon", lambda _=False, a=a: self._play(a), tip="Play it now (here and on the "
                              "island)", glyph="play")
            shows = " and ".join({"pill": "island"}.get(x, x) for x in a.get("where", CH.WHERE))
            desc = f"{a['duration']} s, {shows}" + (", only at certain times" if a.get("when") else "")
            self.g_ant.add(kit.SettingRow(a["name"], desc, (play, sw), kind="trailing", tip=tip))
            self._antic_boxes[a["id"]] = sw

    def _chip(self, st: str, b):
        for c in getattr(self, "_chips", []):
            c.setChecked(c is b)
        self._preview_state(st)

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
                q.setClearColor(QColor(th.T["stage"]))
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
