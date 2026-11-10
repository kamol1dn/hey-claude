"""The Settings design system: palette, spacing, type, radii and the one stylesheet.

Everything Settings draws (settings_ui, character_settings, hooks_settings, the
wake-word wizard) takes its colours, sizes and fonts from here; no widget sets
its own colour or size by hand.

Palette source: the island's own colours ("Ember & Thread": ui/island/Island.qml
and the approved previews in docs/*.html). A theme can override any of them with
a "palette" object in ui/themes/<id>/theme.json; every other colour (surfaces,
lines, hovers, the text on the accent) is *derived* from those few base colours
and checked for contrast, so a different palette still reads correctly, light
or dark.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPixmap

ROOT = Path(__file__).resolve().parent.parent
THEME_DIR = ROOT / "ui" / "themes"
THEME_ID = "ember-thread"

# -- base palette (the island's own colours) ---------------------------------------------------
BASE = {
    "page": "#0D0C0B",      # the deepest surface (widgets-preview --page)
    "fg": "#F2F2F2",        # Island.qml cFg
    "dim": "#9A9A9A",       # cDim
    "faint": "#6F6A66",     # --faint
    "live": "#D97757",      # cLive: the ember, the one accent
    "hot": "#EC9A78",       # cHot
    "banked": "#B8664C",    # --banked: the ember pressed
    "ok": "#6CCB7A",        # cOk
    "bad": "#E5646A",       # cBad
    "warn": "#E5A04A",
}

# -- spacing, radii, type ----------------------------------------------------------------------
SPACE = (0, 4, 8, 12, 16, 24, 32)      # S[1]=4 ... S[6]=32; nothing else is used
S = SPACE
RADIUS = {"control": 6, "card": 10, "tag": 8}
CONTROL_H = 32                         # every field, combo and button
ROW_PAD_H, ROW_PAD_V = S[4], S[3]      # 16 / 12 inside a card row
PAGE_PAD = (S[6], S[5] + S[1], S[6], S[6])    # left, top (28), right, bottom
CONTENT_MAX_W = 720                    # the page column never grows wider than this
SIDEBAR_W = 216
TYPE = {                               # point sizes
    "display": 17.0,                   # page title
    "title": 10.5,                     # group title
    "body": 10.0,                      # row titles, controls
    "caption": 9.0,                    # descriptions, notes, status
    "micro": 8.0,                      # tags, sidebar section captions
}
ANIM_MS = {"fade": 160, "switch": 140}


# -- colour maths ------------------------------------------------------------------------------
def _rgb(c) -> tuple[float, float, float]:
    q = QColor(c)
    return q.redF(), q.greenF(), q.blueF()


def mix(a, b, t: float) -> str:
    """a blended toward b by t (0..1), as #RRGGBB."""
    ra, ga, ba = _rgb(a)
    rb, gb, bb = _rgb(b)
    c = QColor.fromRgbF(ra + (rb - ra) * t, ga + (gb - ga) * t, ba + (bb - ba) * t)
    return c.name().upper()


def luminance(c) -> float:
    def ch(v):
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = _rgb(c)
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def contrast(a, b) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def ensure_contrast(color, bg, toward, ratio: float) -> str:
    """`color`, pushed toward `toward` just until it reaches `ratio` against `bg`."""
    if contrast(color, bg) >= ratio:
        return QColor(color).name().upper()
    for i in range(1, 21):
        c = mix(color, toward, i / 20)
        if contrast(c, bg) >= ratio:
            return c
    return QColor(toward).name().upper()


def rgba(c, a: float) -> str:
    r, g, b = _rgb(c)
    return f"rgba({round(r * 255)}, {round(g * 255)}, {round(b * 255)}, {a:.3f})"


# -- tokens ------------------------------------------------------------------------------------
def load_palette(theme_id: str = THEME_ID) -> dict:
    """BASE, with a theme's optional "palette" overrides (ui/themes/<id>/theme.json)."""
    pal = dict(BASE)
    try:
        data = json.loads((THEME_DIR / theme_id / "theme.json").read_text(encoding="utf-8"))
        for k, v in (data.get("palette") or {}).items():
            if k in pal and QColor(str(v)).isValid():
                pal[k] = str(v)
    except Exception:
        pass
    return pal


def tokens(palette: dict | None = None) -> dict:
    """Every colour Settings uses, derived from the few base ones."""
    p = dict(BASE)
    p.update(palette or {})
    page, fg, live = p["page"], p["fg"], p["live"]
    t = dict(p)
    t["window"] = page
    t["sidebar"] = mix(page, fg, 0.03)
    t["card"] = mix(page, fg, 0.045)
    t["field"] = mix(page, fg, 0.075)
    t["field_hover"] = mix(page, fg, 0.10)
    t["raised"] = mix(page, fg, 0.10)          # popups, tooltips
    t["line"] = mix(t["card"], fg, 0.09)        # hairlines inside cards
    t["line_strong"] = mix(t["card"], fg, 0.17)  # field borders on hover, popups
    t["button"] = mix(page, fg, 0.09)
    t["button_hover"] = mix(page, fg, 0.14)
    t["button_pressed"] = mix(page, fg, 0.06)
    t["nav_hover"] = mix(t["sidebar"], fg, 0.05)
    t["nav_active"] = mix(t["sidebar"], live, 0.16)
    t["accent"] = live
    t["accent_hover"] = p["hot"]
    t["accent_pressed"] = p["banked"]
    # text on the ember: whichever of page / fg reads better (dark on the default ember)
    t["on_accent"] = mix(page, live, 0.08) if contrast(page, live) >= contrast(fg, live) else fg
    # text stays readable on the card whatever the palette: pushed toward white on a dark card,
    # toward black on a light one, just as far as it takes
    away = "#FFFFFF" if luminance(t["card"]) < 0.18 else "#000000"
    t["fg"] = ensure_contrast(fg, t["card"], away, 7.0)
    t["dim"] = ensure_contrast(p["dim"], t["card"], away, 4.5)
    t["faint"] = ensure_contrast(p["faint"], t["card"], away, 2.6)
    t["disabled"] = mix(t["card"], t["dim"], 0.55)
    for k in ("ok", "bad", "warn"):
        t[k] = ensure_contrast(p[k], t["card"], away, 4.5)
    t["accent_text"] = ensure_contrast(p["hot"], t["card"], away, 4.5)   # links, the restart tag
    t["selection"] = mix(t["card"], live, 0.40)
    t["tag_bg"] = mix(t["card"], live, 0.10)
    t["tag_line"] = mix(t["card"], live, 0.45)
    t["danger_hover"] = mix(t["card"], p["bad"], 0.16)
    t["track_off"] = mix(page, fg, 0.20)
    t["knob_off"] = mix(page, fg, 0.70)
    t["stage"] = mix(page, "#000000", 0.35)     # behind the character preview
    return t


T: dict = tokens(load_palette())      # the live tokens; settings_ui re-reads them at startup


# -- fonts -------------------------------------------------------------------------------------
def _first_family(*names: str) -> str:
    have = set(QFontDatabase.families())
    for n in names:
        if n in have:
            return n
    return names[-1]


def families() -> dict:
    return {
        "text": _first_family("Segoe UI Variable Text", "Segoe UI"),
        "display": _first_family("Segoe UI Variable Display", "Segoe UI Variable Text", "Segoe UI"),
        "mono": _first_family("Cascadia Mono", "Consolas"),
        "icons": _first_family("Segoe Fluent Icons", "Segoe MDL2 Assets", ""),
    }


def font(role: str = "body", weight=QFont.Normal) -> QFont:
    fam = families()
    f = QFont(fam["display"] if role == "display" else fam["mono"] if role == "mono" else fam["text"])
    f.setPointSizeF(TYPE.get(role, TYPE["body"]))
    f.setWeight(weight)
    return f


# -- motion ------------------------------------------------------------------------------------
_MOTION = {"on": True}


def set_motion(setting) -> bool:
    """island.motion -> whether Settings animates (only in "full": calm / off / Windows'
    animations off mean no page fade and an instant switch)."""
    try:
        from .island_qml import resolve_motion
        _MOTION["on"] = resolve_motion(setting) == "full"
    except Exception:
        _MOTION["on"] = str(setting or "auto").lower() in ("auto", "full")
    return _MOTION["on"]


def motion_on() -> bool:
    return _MOTION["on"]


# -- icons -------------------------------------------------------------------------------------
GLYPHS = {   # Segoe Fluent Icons / MDL2 code points
    "general": "", "island": "", "character": "", "wake": "", "voice": "",
    "dictation": "", "instant": "", "claude": "", "projects": "",
    "permissions": "", "sessions": "", "plugins": "", "play": "",
    "chevron_down": "", "chevron_up": "", "clear": "", "folder": "",
}


def glyph_pixmap(name: str, color: str, px: int = 16, dpr: float = 2.0) -> QPixmap:
    """A Fluent icon glyph as a pixmap (a small dot when no icon font is installed)."""
    pm = QPixmap(round(px * dpr), round(px * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.TextAntialiasing)
    fam = families()["icons"]
    if fam:
        f = QFont(fam)
        f.setPixelSize(px)
        p.setFont(f)
        p.setPen(QColor(color))
        p.drawText(QRectF(0, 0, px, px), Qt.AlignCenter, GLYPHS.get(name, name))
    else:
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(color))
        p.drawEllipse(QRectF(px * 0.35, px * 0.35, px * 0.3, px * 0.3))
    p.end()
    return pm


def glyph_icon(name: str, normal: str | None = None, active: str | None = None, px: int = 16) -> QIcon:
    """Icon in `normal` colour, and `active` when the button is checked/selected."""
    ic = QIcon()
    ic.addPixmap(glyph_pixmap(name, normal or T["dim"], px), QIcon.Normal, QIcon.Off)
    ic.addPixmap(glyph_pixmap(name, active or T["fg"], px), QIcon.Normal, QIcon.On)
    ic.addPixmap(glyph_pixmap(name, active or T["fg"], px), QIcon.Selected, QIcon.Off)
    ic.addPixmap(glyph_pixmap(name, T["disabled"], px), QIcon.Disabled, QIcon.Off)
    return ic


def ember_pixmap(px: int = 18, dpr: float = 2.0) -> QPixmap:
    """The island's resting ember (a glow, a coal, a hot core), for the sidebar header."""
    from PySide6.QtGui import QRadialGradient
    pm = QPixmap(round(px * dpr), round(px * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    c = px / 2
    g = QRadialGradient(c, c, px / 2)
    glow = QColor(T["live"])
    glow.setAlphaF(0.45)
    g.setColorAt(0, glow)
    glow.setAlphaF(0.0)
    g.setColorAt(1, glow)
    p.setPen(Qt.NoPen)
    p.setBrush(g)
    p.drawEllipse(QRectF(0, 0, px, px))
    p.setBrush(QColor(T["live"]))
    p.drawEllipse(QRectF(c - px * 0.17, c - px * 0.17, px * 0.34, px * 0.34))
    p.setBrush(QColor(T["hot"]))
    p.drawEllipse(QRectF(c - px * 0.07, c - px * 0.07, px * 0.14, px * 0.14))
    p.end()
    return pm


# Stylesheet images (combo / spin arrows) are small SVGs written once per palette.
_SVG = {
    "chevron-down": '<path d="M3.5 6 8 10.5 12.5 6" fill="none" stroke="{c}" stroke-width="1.5" '
                    'stroke-linecap="round" stroke-linejoin="round"/>',
    "chevron-up": '<path d="M3.5 10 8 5.5 12.5 10" fill="none" stroke="{c}" stroke-width="1.5" '
                  'stroke-linecap="round" stroke-linejoin="round"/>',
}


def _svg_dir(t: dict) -> Path:
    key = hashlib.sha1(json.dumps([t["dim"], t["disabled"], t["fg"]]).encode()).hexdigest()[:10]
    d = Path(tempfile.gettempdir()) / f"heyclaude-settings-{key}"
    try:
        d.mkdir(exist_ok=True)
        for name, body in _SVG.items():
            for tone in ("dim", "disabled", "fg"):
                f = d / f"{name}-{tone}.svg"
                if not f.exists():
                    f.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" '
                                 f'viewBox="0 0 16 16">{body.format(c=t[tone])}</svg>', encoding="utf-8")
    except OSError:
        pass
    return d


# -- the stylesheet ----------------------------------------------------------------------------
def stylesheet(t: dict | None = None) -> str:
    t = t or T
    fam = families()
    img = _svg_dir(t).as_posix()
    r, rc, rt = RADIUS["control"], RADIUS["card"], RADIUS["tag"]
    inner = CONTROL_H - 2          # min-height is the content box; the 1 px border is outside it
    return f"""
QWidget {{ font-family: "{fam['text']}"; font-size: {TYPE['body']}pt; color: {t['fg']}; }}
QWidget#settingsWindow, QDialog, QWidget#pageBody, QStackedWidget#pages, QScrollArea {{ background: {t['window']}; }}
QLabel#cue {{ font-family: "{fam['display']}"; font-size: {TYPE['display'] - 3}pt; }}
QScrollArea {{ border: none; }}
QLabel {{ background: transparent; }}
QToolTip {{ background: {t['raised']}; color: {t['fg']}; border: 1px solid {t['line_strong']};
           border-radius: {r}px; padding: {S[2]}px {S[3]}px; font-size: {TYPE['caption']}pt; }}

/* sidebar */
QFrame#sidebar {{ background: {t['sidebar']}; border: none; }}
QScrollArea#sidebarScroll {{ background: {t['sidebar']}; border: none; border-right: 1px solid {t['line']}; }}
QLabel#brand {{ font-family: "{fam['display']}"; font-size: {TYPE['title']}pt; font-weight: 600; }}
QLabel#brandSub {{ color: {t['dim']}; font-size: {TYPE['caption']}pt; }}
QLabel#navSection {{ color: {t['faint']}; font-size: {TYPE['micro']}pt; font-weight: 600;
                    letter-spacing: 0.6px; padding: 0 {S[3]}px; }}
QPushButton#navItem {{ background: transparent; border: none; border-radius: {r}px; color: {t['dim']};
                      text-align: left; padding: 0 {S[3]}px; min-height: {CONTROL_H}px; max-height: {CONTROL_H}px; }}
QPushButton#navItem:hover {{ background: {t['nav_hover']}; color: {t['fg']}; }}
QPushButton#navItem:checked {{ background: {t['nav_active']}; color: {t['fg']}; font-weight: 600; }}
QPushButton#navItem:focus {{ border: 1px solid {t['accent']}; }}

/* page header, groups, cards, rows */
QLabel#pageTitle {{ font-family: "{fam['display']}"; font-size: {TYPE['display']}pt; font-weight: 600; }}
QLabel#pageSubtitle {{ color: {t['dim']}; font-size: {TYPE['body']}pt; }}
QLabel#groupTitle {{ font-size: {TYPE['title']}pt; font-weight: 600; }}
QLabel#groupDesc, QLabel#rowDesc, QLabel#note, QLabel#help, QLabel#hint {{ color: {t['dim']};
    font-size: {TYPE['caption']}pt; }}
QLabel#rowTitle {{ font-size: {TYPE['body']}pt; }}
QLabel#rowTitle:disabled, QLabel#rowDesc:disabled {{ color: {t['disabled']}; }}
QLabel#value {{ color: {t['fg']}; font-size: {TYPE['body']}pt; }}
QLabel#value:disabled {{ color: {t['disabled']}; }}
QLabel[tone="ok"], QLabel#value[tone="ok"], QLabel#rowDesc[tone="ok"], QLabel#note[tone="ok"] {{ color: {t['ok']}; }}
QLabel[tone="bad"], QLabel#value[tone="bad"], QLabel#rowDesc[tone="bad"], QLabel#note[tone="bad"] {{
    color: {t['bad']}; }}
QLabel[tone="warn"], QLabel#value[tone="warn"], QLabel#rowDesc[tone="warn"], QLabel#note[tone="warn"] {{
    color: {t['warn']}; }}
QLabel#warn {{ color: {t['warn']}; font-size: {TYPE['caption']}pt; }}
QLabel[tone="accent"], QLabel#value[tone="accent"], QLabel#rowDesc[tone="accent"], QLabel#note[tone="accent"] {{
    color: {t['accent_text']}; }}
QFrame#card {{ background: {t['card']}; border: 1px solid {t['line']}; border-radius: {rc}px; }}
QFrame#divider {{ background: {t['line']}; border: none; min-height: 1px; max-height: 1px; }}
QLabel#restartTag {{ color: {t['accent_text']}; background: {t['tag_bg']}; border: 1px solid {t['tag_line']};
                    border-radius: {rt}px; padding: 0 {S[2] - 1}px; font-size: {TYPE['micro']}pt; }}
QFrame#footer {{ background: {t['window']}; border: none; border-top: 1px solid {t['line']}; }}
QLabel#status {{ font-size: {TYPE['caption']}pt; color: {t['dim']}; }}

/* fields */
QLineEdit, QComboBox, QAbstractSpinBox {{
    background: {t['field']}; border: 1px solid {t['line']}; border-radius: {r}px;
    padding: 0 {S[3] - 2}px;   /* a combo's text lands 11 px in; a line edit adds its own 2 px, see below */ min-height: {inner}px; max-height: {inner}px;
    selection-background-color: {t['selection']}; selection-color: {t['fg']}; }}
QLineEdit {{ padding-left: {S[2]}px; }}
QPlainTextEdit, QTableWidget, QListWidget {{
    background: {t['field']}; border: 1px solid {t['line']}; border-radius: {r}px;
    selection-background-color: {t['selection']}; selection-color: {t['fg']}; }}
QPlainTextEdit {{ padding: {S[1]}px {S[2] - 2}px; font-family: "{fam['mono']}"; font-size: {TYPE['caption']}pt; }}
QLineEdit:hover, QComboBox:hover, QAbstractSpinBox:hover, QPlainTextEdit:hover {{
    background: {t['field_hover']}; border-color: {t['line_strong']}; }}
QLineEdit:focus, QComboBox:focus, QAbstractSpinBox:focus, QPlainTextEdit:focus, QTableWidget:focus,
QComboBox:on {{ border: 1px solid {t['accent']}; background: {t['field']}; }}
QLineEdit:disabled, QComboBox:disabled, QAbstractSpinBox:disabled, QPlainTextEdit:disabled {{
    color: {t['disabled']}; background: {t['card']}; border-color: {t['line']}; }}
QLineEdit[recording="true"] {{ border: 1px solid {t['accent']}; background: {t['tag_bg']}; }}
QLineEdit[error="true"], QComboBox[error="true"], QPlainTextEdit[error="true"] {{ border: 1px solid {t['bad']}; }}
QComboBox QLineEdit, QAbstractSpinBox QLineEdit {{ background: transparent; border: none; padding: 0;
    min-height: 0; max-height: 9999px; }}
QComboBox::drop-down {{ subcontrol-origin: border; subcontrol-position: center right; width: 28px; border: none; }}
QComboBox::down-arrow {{ image: url({img}/chevron-down-dim.svg); width: 12px; height: 12px; }}
QComboBox::down-arrow:disabled {{ image: url({img}/chevron-down-disabled.svg); }}
QComboBox QAbstractItemView {{ background: {t['raised']}; border: 1px solid {t['line_strong']}; border-radius: {r}px;
    padding: {S[1]}px; outline: 0; selection-background-color: {t['nav_active']}; selection-color: {t['fg']}; }}
QComboBox QAbstractItemView::item {{ min-height: 28px; padding: 0 {S[2]}px; border-radius: {r - 2}px; }}
QComboBox QAbstractItemView::item:hover {{ background: {t['nav_hover']}; }}
QComboBox QAbstractItemView::item:selected {{ background: {t['nav_active']}; }}
QAbstractSpinBox {{ padding-right: 24px; }}
QComboBox:editable {{ padding-left: {S[1]}px; }}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{ subcontrol-origin: border; width: 22px;
    border: none; border-left: 1px solid {t['line']}; background: transparent; }}
QAbstractSpinBox::up-button {{ subcontrol-position: top right; border-top-right-radius: {r}px; }}
QAbstractSpinBox::down-button {{ subcontrol-position: bottom right; border-bottom-right-radius: {r}px; }}
QAbstractSpinBox::up-button:hover, QAbstractSpinBox::down-button:hover {{ background: {t['button_hover']}; }}
QAbstractSpinBox::up-arrow {{ image: url({img}/chevron-up-dim.svg); width: 10px; height: 10px; }}
QAbstractSpinBox::down-arrow {{ image: url({img}/chevron-down-dim.svg); width: 10px; height: 10px; }}
QAbstractSpinBox::up-arrow:disabled, QAbstractSpinBox::up-arrow:off {{ image: url({img}/chevron-up-disabled.svg); }}
QAbstractSpinBox::down-arrow:disabled, QAbstractSpinBox::down-arrow:off {{
    image: url({img}/chevron-down-disabled.svg); }}

/* tables and lists */
QTableWidget {{ gridline-color: transparent; padding: 0; alternate-background-color: {t['field']}; }}
QTableWidget::item {{ padding: 0 {S[2]}px; border-bottom: 1px solid {t['line']}; }}
QTableWidget::item:selected {{ background: {t['selection']}; color: {t['fg']}; }}
QHeaderView {{ background: transparent; }}
QHeaderView::section {{ background: {t['card']}; color: {t['dim']}; border: none; border-bottom: 1px solid {t['line']};
    padding: 0 {S[2]}px; min-height: 28px; font-size: {TYPE['caption']}pt; font-weight: 600; }}
QTableCornerButton::section {{ background: {t['card']}; border: none; }}
QTableWidget#flush {{ background: transparent; border: none; border-radius: 0; }}
QTableWidget#flush::item {{ padding: 0 {S[3]}px 0 {S[3] + 1}px; }}   /* + the cell's own 3 px = the 16 px row inset */
QTableWidget#flush QHeaderView::section {{ padding: 0 {S[4]}px; }}
QTableWidget#flush QLineEdit {{ min-height: 0; max-height: 9999px; border-radius: 0; padding: 0 {S[1]}px; }}
QListWidget::item {{ padding: {S[1]}px {S[2]}px; border-radius: {r - 2}px; }}
QListWidget::item:selected {{ background: {t['nav_active']}; color: {t['fg']}; }}
QListWidget#gallery {{ background: transparent; border: none; }}
QListWidget#gallery::item {{ color: {t['dim']}; border: 1px solid transparent; border-radius: {rc - 2}px;
    padding: {S[2]}px {S[1]}px; margin: 0 {S[1]}px 0 0; }}
QListWidget#gallery::item:hover {{ background: {t['nav_hover']}; color: {t['fg']}; }}
QListWidget#gallery::item:selected {{ background: {t['nav_active']}; color: {t['fg']}; border: 1px solid {t['tag_line']}; }}

/* buttons: secondary (default), primary, ghost, danger, chip */
QPushButton {{ background: {t['button']}; color: {t['fg']}; border: 1px solid {t['line']}; border-radius: {r}px;
    padding: 0 {S[4] - 2}px; min-height: {inner}px; max-height: {inner}px; }}
QPushButton:hover {{ background: {t['button_hover']}; border-color: {t['line_strong']}; }}
QPushButton:pressed {{ background: {t['button_pressed']}; }}
QPushButton:focus {{ border: 1px solid {t['accent']}; }}
QPushButton:disabled {{ color: {t['disabled']}; background: {t['card']}; border-color: {t['line']}; }}
QPushButton#primary {{ background: {t['accent']}; border: 1px solid {t['accent']}; color: {t['on_accent']};
    font-weight: 600; }}
QPushButton#primary:hover {{ background: {t['accent_hover']}; border-color: {t['accent_hover']}; }}
QPushButton#primary:pressed {{ background: {t['accent_pressed']}; border-color: {t['accent_pressed']}; }}
QPushButton#primary:focus {{ border: 1px solid {t['fg']}; }}
QPushButton#primary:disabled {{ background: {t['button']}; border-color: {t['line']}; color: {t['disabled']};
    font-weight: 400; }}
QPushButton#ghost {{ background: transparent; border: 1px solid transparent; color: {t['dim']}; }}
QPushButton#ghost:hover {{ background: {t['button']}; color: {t['fg']}; }}
QPushButton#ghost:focus {{ border: 1px solid {t['accent']}; }}
QPushButton#ghost:disabled {{ color: {t['disabled']}; background: transparent; }}
QPushButton#danger {{ background: transparent; border: 1px solid {t['line']}; color: {t['bad']}; }}
QPushButton#danger:hover {{ background: {t['danger_hover']}; border-color: {t['bad']}; }}
QPushButton#danger:focus {{ border: 1px solid {t['bad']}; }}
QPushButton#danger:disabled {{ color: {t['disabled']}; border-color: {t['line']}; background: transparent; }}
QPushButton#chip {{ min-height: 24px; max-height: 24px; padding: 0 {S[2] + 2}px; border-radius: 12px;
    font-size: {TYPE['caption']}pt; color: {t['dim']}; }}
QPushButton#chip:hover {{ color: {t['fg']}; }}
QPushButton#chip:checked {{ background: {t['nav_active']}; border-color: {t['tag_line']}; color: {t['fg']}; }}
QPushButton#icon {{ background: transparent; border: 1px solid transparent; padding: 0; min-width: {inner}px;
    max-width: {inner}px; }}
QPushButton#icon:hover {{ background: {t['button']}; }}
QPushButton#icon:focus {{ border: 1px solid {t['accent']}; }}

/* sliders */
QSlider {{ min-height: {CONTROL_H}px; background: transparent; }}
QSlider::groove:horizontal {{ height: 4px; background: {t['track_off']}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {t['accent']}; border-radius: 2px; }}
QSlider::handle:horizontal {{ width: 16px; height: 16px; margin: -6px 0; border-radius: 8px;
    background: {t['fg']}; border: 3px solid {t['accent']}; }}
QSlider::handle:horizontal:hover {{ border-color: {t['accent_hover']}; }}
QSlider::handle:horizontal:focus {{ border-color: {t['accent_hover']}; }}
QSlider::sub-page:horizontal:disabled {{ background: {t['track_off']}; }}
QSlider::handle:horizontal:disabled {{ background: {t['knob_off']}; border-color: {t['track_off']}; }}

/* scroll bars: thin, no arrows */
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {t['line_strong']}; border-radius: 3px; min-height: 32px; }}
QScrollBar::handle:vertical:hover {{ background: {t['faint']}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {t['line_strong']}; border-radius: 3px; min-width: 32px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* wizard bits */
QProgressBar {{ background: {t['track_off']}; border: none; border-radius: 3px; max-height: 6px; min-height: 6px; }}
QProgressBar::chunk {{ background: {t['accent']}; border-radius: 3px; }}
QMessageBox, QInputDialog {{ background: {t['window']}; }}
"""
