"""Settings components, all drawn from settings_theme (no colours or sizes of their own).

    Sidebar + PageStack   the section list on the left and the pages (PageStack keeps the
                          small QTabWidget API: addTab / count / tabText / widget / setCurrentIndex)
    Page                  a scrolling column: title, one-line subtitle, then groups
    Group                 a titled card ("Startup", "Push-to-talk"...) holding rows
    SettingRow            one setting: title (+ restart tag), an optional one-line description,
                          and its control. Kinds:
                            field     the control sits in the page's control column (all left
                                      edges line up, one width per page)
                            trailing  the control keeps its natural size at the right (switches,
                                      buttons); with `icon=` this is the list row a plugin list uses
                            stacked   the control goes full width under the text (lists, tables)
                          set_details(widget) adds a chevron that shows/hides more settings
                          under the row (a plugin's own settings).
    Switch                an on/off toggle (a QAbstractButton: isChecked / setChecked / toggled)
    button(kind=)         primary / secondary / ghost / danger / chip / icon
"""
from __future__ import annotations

from PySide6.QtCore import Property, QEasingCurve, QRectF, QSize, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (QAbstractButton, QAbstractSpinBox, QButtonGroup, QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QSlider, QStackedWidget,
                               QTableWidget, QVBoxLayout, QWidget)

from . import settings_theme as th

S = th.S
SPIN_W = 132           # spin boxes keep one width; text fields and combos fill the control column
READOUT_W = 76         # a slider's value readout
SPIN_TEXT_INSET = 6    # + the editor's own margins = the 11 px text inset of fields and combos


def _label(text: str, name: str, wrap: bool = False, rich: bool = False) -> QLabel:
    lab = QLabel(text)
    lab.setObjectName(name)
    lab.setWordWrap(wrap)
    lab.setTextFormat(Qt.RichText if rich else Qt.AutoText)
    if rich:
        lab.setOpenExternalLinks(True)
    return lab


def set_tone(label: QLabel, tone: str = ""):
    """ok / bad / warn / accent / "" (dim or fg as the label's role says)."""
    if label.property("tone") == tone:
        return
    label.setProperty("tone", tone)
    label.style().unpolish(label)
    label.style().polish(label)


def set_error(widget: QWidget, on: bool):
    if bool(widget.property("error")) == on:
        return
    widget.setProperty("error", on)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def button(text: str = "", kind: str = "secondary", clicked=None, tip: str = "", glyph: str = "") -> QPushButton:
    b = QPushButton(text)
    if kind != "secondary":
        b.setObjectName(kind)
    if clicked is not None:
        b.clicked.connect(clicked)
    if tip:
        b.setToolTip(tip)
    if glyph:
        b.setIcon(th.glyph_icon(glyph, th.T["dim"], th.T["fg"], 14))
        b.setIconSize(QSize(14, 14))
    b.setFocusPolicy(Qt.TabFocus)             # a click doesn't leave a focus ring behind; Tab still reaches it
    b.setCursor(Qt.PointingHandCursor)
    return b


ICON_TILE = 32
ICON_INDENT = ICON_TILE + S[3]   # a list row's text starts here (after its icon); its sub-settings too


def icon_tile(path: str = "", glyph: str = "", name: str = "", px: int = ICON_TILE, dpr: float = 2.0):
    """A list row's icon: an image (svg/png) or a Fluent glyph, else the name's initial, on a
    rounded tile in the raised surface colour."""
    from PySide6.QtGui import QFont, QIcon, QPixmap
    t = th.T
    pm = QPixmap(round(px * dpr), round(px * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    p.setPen(QPen(QColor(t["line"]), 1))
    p.setBrush(QColor(t["raised"]))
    p.drawRoundedRect(QRectF(0.5, 0.5, px - 1, px - 1), 8, 8)
    # a logo (svg) sits small in the tile; a character preview (a raster sprite) fills more of it
    inner = round(px * (0.56 if str(path).lower().endswith(".svg") else 0.8))
    off = (px - inner) / 2
    img = QIcon(path).pixmap(round(inner * dpr), round(inner * dpr)) if path else QPixmap()
    if not img.isNull():
        img.setDevicePixelRatio(dpr)
        p.drawPixmap(QRectF(off, off, inner, inner), img, QRectF(img.rect()))
    elif glyph in th.GLYPHS or glyph in th.PLUGIN_GLYPHS:
        g = th.glyph_pixmap(th.PLUGIN_GLYPHS.get(glyph, glyph), t["dim"], round(px * 0.5), dpr)
        q = px * 0.25
        p.drawPixmap(QRectF(q, q, px * 0.5, px * 0.5), g, QRectF(g.rect()))
    else:
        f = QFont(th.families()["text"])
        f.setPixelSize(round(px * 0.42))
        f.setWeight(QFont.DemiBold)
        p.setFont(f)
        p.setPen(QColor(t["dim"]))
        p.drawText(QRectF(0, 0, px, px), Qt.AlignCenter, (name or "?")[:1].upper())
    p.end()
    return pm


def fit_spin(sp):
    """One width for every spin box, and its text starting where a combo's or a field's does
    (the stylesheet's padding doesn't reach a spin box's inner editor)."""
    sp.setFixedWidth(SPIN_W)
    le = sp.findChild(QLineEdit)
    if le is not None:
        le.setTextMargins(SPIN_TEXT_INSET, 0, 0, 0)
    return sp


def restart_tag() -> QLabel:
    t = QLabel("restart")
    t.setObjectName("restartTag")
    t.setToolTip("Takes effect after hey-claude restarts.")
    t.setAlignment(Qt.AlignCenter)
    t.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
    return t


# ---------------------------------------------------------------------------------- Switch
class Switch(QAbstractButton):
    """A pill on/off switch. The knob slides in 140 ms (instant in calm / off motion)."""

    W, H, PAD = 40, 22, 2

    def __init__(self, parent=None, checked: bool = False):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.TabFocus)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._pos = 1.0 if checked else 0.0
        self._hover = False
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(th.ANIM_MS["switch"])
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._set_pos)
        self.toggled.connect(self._slide)

    def sizeHint(self):
        return QSize(self.W + 2 * self.PAD, self.H + 2 * self.PAD)

    minimumSizeHint = sizeHint

    def _set_pos(self, v):
        self._pos = float(v)
        self.update()

    def _get_pos(self):
        return self._pos

    knob = Property(float, _get_pos, _set_pos)

    def _slide(self, on: bool):
        target = 1.0 if on else 0.0
        self._anim.stop()
        if th.motion_on() and self.isVisible():
            self._anim.setStartValue(self._pos)
            self._anim.setEndValue(target)
            self._anim.start()
        else:
            self._set_pos(target)

    def enterEvent(self, e):
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self.update()
        super().leaveEvent(e)

    def hitButton(self, pos):
        return self.rect().contains(pos)

    def paintEvent(self, _):
        t = th.T
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        track = QRectF(self.PAD, self.PAD, self.W, self.H)
        if not self.isEnabled():
            p.setOpacity(0.45)
        off, on = QColor(t["track_off"]), QColor(t["accent_hover"] if self._hover else t["accent"])
        if self._hover and not self.isChecked():
            off = QColor(th.mix(t["track_off"], t["fg"], 0.08))
        k = self._pos
        col = QColor.fromRgbF(off.redF() + (on.redF() - off.redF()) * k, off.greenF() + (on.greenF() - off.greenF()) * k,
                              off.blueF() + (on.blueF() - off.blueF()) * k)
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        p.drawRoundedRect(track, self.H / 2, self.H / 2)
        d = self.H - 6
        x = track.x() + 3 + (self.W - 6 - d) * k
        kc_off, kc_on = QColor(t["knob_off"]), QColor(t["fg"])
        kc = QColor.fromRgbF(kc_off.redF() + (kc_on.redF() - kc_off.redF()) * k,
                             kc_off.greenF() + (kc_on.greenF() - kc_off.greenF()) * k,
                             kc_off.blueF() + (kc_on.blueF() - kc_off.blueF()) * k)
        p.setBrush(kc)
        p.drawEllipse(QRectF(x, track.y() + 3, d, d))
        if self.hasFocus():
            p.setOpacity(1.0)
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(t["accent_hover"]), 1.5))
            p.drawRoundedRect(track.adjusted(-1.25, -1.25, 1.25, 1.25), self.H / 2 + 1.25, self.H / 2 + 1.25)
        p.end()


# ---------------------------------------------------------------------------------- rows
class SettingRow(QWidget):
    def __init__(self, title: str, desc: str = "", controls=(), kind: str = "field", tag: bool = False,
                 tip: str = "", icon=None):
        super().__init__()
        self.kind = kind
        self.setObjectName("row")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(th.ROW_PAD_H, th.ROW_PAD_V, th.ROW_PAD_H, th.ROW_PAD_V)
        outer.setSpacing(S[2])
        main = QHBoxLayout()
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(S[4])
        outer.addLayout(main)
        if icon is not None:                       # a list row (a plugin): 32 px icon on the left
            ic = QLabel()
            ic.setFixedSize(32, 32)
            ic.setPixmap(icon if not hasattr(icon, "pixmap") else icon.pixmap(32, 32))
            ic.setScaledContents(False)
            main.addWidget(ic, 0, Qt.AlignVCenter)
            main.setSpacing(S[3])
        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(2)
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(S[2])
        self.title_label = _label(title, "rowTitle")
        head.addWidget(self.title_label, 0)
        self.tag = restart_tag() if tag else None
        if self.tag is not None:
            head.addWidget(self.tag, 0, Qt.AlignVCenter)
        head.addStretch(1)
        text.addLayout(head)
        self.desc_label = _label(desc, "rowDesc", wrap=True)
        if not desc:                                 # hide only: show() on a label with no parent yet
            self.desc_label.hide()                   # (its layout isn't on a widget) opens it as a window
        text.addWidget(self.desc_label)
        if tip:
            self.title_label.setToolTip(tip)
            for c in controls:
                if not c.toolTip():
                    c.setToolTip(tip)

        self.holder = QWidget()
        self.holder.setObjectName("controls")
        hl = QHBoxLayout(self.holder)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(S[2])
        grow = None
        for c in controls:
            if isinstance(c, QAbstractSpinBox):
                fit_spin(c)
        for c in controls:
            if grow is None and (isinstance(c, (QLineEdit, QComboBox, QSlider, QLabel, QPlainTextEdit, QTableWidget))
                                 or c.sizePolicy().horizontalPolicy() in (QSizePolicy.Expanding,
                                                                          QSizePolicy.MinimumExpanding)):
                grow = c
        for c in controls:
            hl.addWidget(c, 1 if c is grow else 0, Qt.AlignVCenter)
        if kind == "field" and grow is None:
            hl.addStretch(1)
        if kind == "trailing":
            self.holder.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)

        self._details = None
        self._chev = None
        if kind == "stacked":
            main.addLayout(text, 1)
            outer.addWidget(self.holder)
        else:
            main.addLayout(text, 1)
            main.addWidget(self.holder, 0, Qt.AlignVCenter)
        self._main = main
        self._outer = outer

    def set_desc(self, text: str):
        self.desc_label.setText(text)
        self.desc_label.setVisible(bool(text))

    def set_details(self, widget: QWidget, open_: bool = False):
        """More settings under this row, behind a chevron (a plugin's own settings)."""
        self._details = widget
        self._chev = button("", "icon", glyph="chevron_down", tip="More settings")
        self._chev.setCheckable(True)
        self._chev.toggled.connect(self._toggle_details)
        self.holder.layout().insertWidget(0, self._chev, 0, Qt.AlignVCenter)
        self._outer.addWidget(widget)
        self._chev.setChecked(open_)
        widget.setVisible(open_)

    def _toggle_details(self, on: bool):
        self._details.setVisible(on)
        self._chev.setIcon(th.glyph_icon("chevron_up" if on else "chevron_down", th.T["dim"], th.T["fg"], 14))


class Group(QWidget):
    """A titled card of rows: the title and an optional one-line explanation above it."""

    def __init__(self, title: str = "", desc: str = ""):
        super().__init__()
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(S[2])
        if title or desc:
            head = QVBoxLayout()
            head.setSpacing(2)
            head.setContentsMargins(0, 0, 0, 0)
            if title:
                self.title_label = _label(title, "groupTitle")
                head.addWidget(self.title_label)
            if desc:
                self.desc_label = _label(desc, "groupDesc", wrap=True)
                head.addWidget(self.desc_label)
            v.addLayout(head)
        self.card = QFrame()
        self.card.setObjectName("card")
        self._lay = QVBoxLayout(self.card)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(0)
        v.addWidget(self.card)
        self.rows: list[QWidget] = []

    def _divider(self):
        holder = QWidget()
        h = QHBoxLayout(holder)
        h.setContentsMargins(th.ROW_PAD_H, 0, 0, 0)
        line = QFrame()
        line.setObjectName("divider")
        line.setFixedHeight(1)
        h.addWidget(line)
        return holder

    def add(self, w: QWidget) -> QWidget:
        if self.rows:
            div = self._divider()
            self._lay.addWidget(div)
            w._divider = div          # hidden along with the row
        self._lay.addWidget(w)
        self.rows.append(w)
        return w

    def clear(self, keep: int = 0):
        """Drop the rows after the first `keep` (a list that's rebuilt, like a pack's antics)."""
        while len(self.rows) > keep:
            w = self.rows.pop()
            for x in (w, getattr(w, "_divider", None)):
                if x is not None:
                    self._lay.removeWidget(x)
                    x.hide()
                    x.deleteLater()

    def add_widget(self, w: QWidget, pad: bool = True, vpad: int | None = None) -> QWidget:
        """Any widget as a padded block in the card (a table, a gallery, buttons)."""
        box = QWidget()
        lay = QVBoxLayout(box)
        vp = th.ROW_PAD_V if vpad is None else vpad
        lay.setContentsMargins(th.ROW_PAD_H if pad else 0, vp, th.ROW_PAD_H if pad else 0, vp)
        lay.setSpacing(S[2])
        lay.addWidget(w)
        return self.add(box)

    def add_note(self, text: str = "", rich: bool = False) -> QLabel:
        lab = _label(text, "note", wrap=True, rich=rich)
        self.add_widget(lab)
        return lab


def buttons_row(*buttons, stretch_end: bool = True) -> QWidget:
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(S[2])
    for b in buttons:
        if b is None:
            h.addStretch(1)
        else:
            h.addWidget(b)
    if stretch_end:
        h.addStretch(1)
    return w


# ---------------------------------------------------------------------------------- pages
class _Column(QWidget):
    def __init__(self, page):
        super().__init__()
        self.page = page
        self.setObjectName("pageColumn")

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.page.sync()


class Page(QScrollArea):
    """Title, subtitle, then groups, in a column at most CONTENT_MAX_W wide."""

    def __init__(self, title: str, subtitle: str = ""):
        super().__init__()
        self.title = title
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("pageBody")
        h = QHBoxLayout(body)
        h.setContentsMargins(*th.PAGE_PAD)
        h.setSpacing(0)
        self.column = _Column(self)
        self.column.setMaximumWidth(th.CONTENT_MAX_W)
        h.addWidget(self.column, 1)
        h.addStretch(0)
        self._v = QVBoxLayout(self.column)
        self._v.setContentsMargins(0, 0, 0, 0)
        self._v.setSpacing(S[5])
        head = QVBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(S[1])
        self.title_label = _label(title, "pageTitle")
        head.addWidget(self.title_label)
        self.subtitle_label = _label(subtitle, "pageSubtitle", wrap=True)
        if not subtitle:                             # hide only (see Row): a parentless show is a window
            self.subtitle_label.hide()
        head.addWidget(self.subtitle_label)
        self._v.addLayout(head)
        self._v.addStretch(1)
        self.setWidget(body)
        self._fields: list[QWidget] = []

    def add(self, w: QWidget) -> QWidget:
        self._v.insertWidget(self._v.count() - 1, w)
        return w

    def add_group(self, title: str = "", desc: str = "") -> Group:
        return self.add(Group(title, desc))

    def register(self, row: SettingRow):
        if row.kind == "field":
            self._fields.append(row.holder)
            self.sync()

    def control_width(self) -> int:
        inner = self.column.width() - 2 * th.ROW_PAD_H - 2
        return max(200, min(300, round(inner * 0.44)))

    def sync(self):
        w = self.control_width()
        for h in self._fields:
            if h.width() != w or h.minimumWidth() != w:
                h.setFixedWidth(w)


class _Fade(QWidget):
    """The old page's picture, fading out over the new one (a cheap cross-fade)."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.hide()
        self._pm = None
        self._a = 1.0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(th.ANIM_MS["fade"])
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.setStartValue(1.0)
        self._anim.setEndValue(0.0)
        self._anim.valueChanged.connect(self._step)
        self._anim.finished.connect(self.hide)

    def start(self, pm):
        self._pm = pm
        self.setGeometry(self.parentWidget().rect())
        self._a = 1.0
        self.show()
        self.raise_()
        self._anim.stop()
        self._anim.start()

    def _step(self, v):
        self._a = float(v)
        self.update()

    def paintEvent(self, _):
        if self._pm is None:
            return
        p = QPainter(self)
        p.setOpacity(self._a)
        p.drawPixmap(0, 0, self._pm)
        p.end()


class Sidebar(QFrame):
    picked = Signal(int)

    def __init__(self, brand: str = "hey-claude", sub: str = "Settings"):
        super().__init__()
        self.setObjectName("sidebar")
        self.setFixedWidth(th.SIDEBAR_W)
        v = QVBoxLayout(self)
        v.setContentsMargins(S[3], S[5], S[3], S[4])
        v.setSpacing(2)
        top = QHBoxLayout()
        top.setContentsMargins(S[3] - 2, 0, 0, 0)
        top.setSpacing(S[2])
        dot = QLabel()
        dot.setPixmap(th.ember_pixmap(20))
        dot.setFixedSize(20, 20)
        top.addWidget(dot, 0, Qt.AlignVCenter)
        names = QVBoxLayout()
        names.setSpacing(0)
        names.addWidget(_label(brand, "brand"))
        names.addWidget(_label(sub, "brandSub"))
        top.addLayout(names, 1)
        v.addLayout(top)
        v.addSpacing(S[5])
        self._v = v
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._group.idClicked.connect(self.picked.emit)
        self._section = None
        self.buttons: list[QPushButton] = []
        v.addStretch(1)

    def add(self, title: str, glyph: str = "", section: str | None = None) -> int:
        at = self._v.count() - 1
        if section is not None and section != self._section:
            if self.buttons:
                self._v.insertSpacing(at, S[4])
                at += 1
            if section:
                lab = _label(section.upper(), "navSection")
                self._v.insertWidget(at, lab)
                self._v.insertSpacing(at + 1, S[1])
                at += 2
            self._section = section
        b = QPushButton(title)
        b.setObjectName("navItem")
        b.setCheckable(True)
        b.setFocusPolicy(Qt.TabFocus)
        b.setCursor(Qt.PointingHandCursor)
        if glyph:
            b.setIcon(th.glyph_icon(glyph, th.T["dim"], th.T["accent_text"], 16))
            b.setIconSize(QSize(16, 16))
        b.installEventFilter(self)
        i = len(self.buttons)
        self._group.addButton(b, i)
        self.buttons.append(b)
        self._v.insertWidget(at, b)
        return i

    def set_current(self, i: int):
        if 0 <= i < len(self.buttons):
            self.buttons[i].setChecked(True)

    def eventFilter(self, obj, ev):
        from PySide6.QtCore import QEvent
        if ev.type() == QEvent.KeyPress and obj in self.buttons and ev.key() in (Qt.Key_Up, Qt.Key_Down):
            i = self.buttons.index(obj) + (1 if ev.key() == Qt.Key_Down else -1)
            if 0 <= i < len(self.buttons):
                self.buttons[i].setFocus(Qt.TabFocusReason)
                self.buttons[i].click()
            return True
        return False


def sidebar_scroll(sidebar: Sidebar) -> QScrollArea:
    """The sidebar in a scroll area of its own: at 200 % on a small screen the section list
    can be taller than the window."""
    sa = QScrollArea()
    sa.setObjectName("sidebarScroll")
    sa.setWidgetResizable(True)
    sa.setFrameShape(QFrame.NoFrame)
    sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    sa.setFixedWidth(th.SIDEBAR_W + 1)
    sa.setWidget(sidebar)
    return sa


class PageStack(QStackedWidget):
    """The pages, with the bit of QTabWidget's API the rest of the code (and the tests) use."""

    def __init__(self, sidebar: Sidebar):
        super().__init__()
        self.setObjectName("pages")
        self.sidebar = sidebar
        self._titles: list[str] = []
        self._fade = _Fade(self)
        sidebar.picked.connect(self.setCurrentIndex)
        self.currentChanged.connect(sidebar.set_current)

    def addTab(self, page: QWidget, title: str, glyph: str = "", section: str | None = None) -> int:
        i = self.addWidget(page)
        self._titles.append(title)
        self.sidebar.add(title, glyph, section)
        if i == 0:
            self.sidebar.set_current(0)
        return i

    def resizeEvent(self, e):
        self._fade.hide()
        super().resizeEvent(e)

    def tabText(self, i: int) -> str:
        return self._titles[i] if 0 <= i < len(self._titles) else ""

    def setCurrentIndex(self, i: int):
        if i == self.currentIndex() or not (0 <= i < self.count()):
            return
        pm = None
        if th.motion_on() and self.isVisible() and self.currentWidget() is not None:
            pm = self.currentWidget().grab()
        super().setCurrentIndex(i)
        if pm is not None:
            self._fade.start(pm)
