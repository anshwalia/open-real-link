"""The app's visual theme: one consistent look on every platform.

Applied once from :func:`tlapp.app.main`, right after the ``QApplication`` is
built. Widgets in :mod:`tlapp.window` only *tag* themselves with a role
(``primary`` / ``secondary`` / ``danger`` / ``ghost``) or a state
(``connected``, ``compatible``, ``refused``, …); the colors, spacing and fonts
live here, resolved against the running palette so light and dark both work.

Qt's ``Fusion`` style is selected first, so the widget geometry is identical on
macOS, Windows and Linux; the stylesheet then paints it. That is a deliberate
trade of native chrome for a consistent, intentional identity.

This module imports PySide6, so ``import tlapp`` must never import it -- only
:mod:`tlapp.window` and :mod:`tlapp.app` do.
"""
from __future__ import annotations

from string import Template

from PySide6.QtGui import QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication, QWidget

__all__ = [
    "PAGE_MARGIN",
    "PAGE_SPACING",
    "SPACE",
    "apply_theme",
    "is_dark",
    "make_mono",
    "mono_font",
    "set_role",
    "set_state",
]

#: A 4-based spacing scale, so margins and gaps stay on a rhythm.
SPACE = {"xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24}

#: The standard inset and gap for a tab page / view.
PAGE_MARGIN = SPACE["md"]
PAGE_SPACING = SPACE["sm"] + 2

#: Button roles. ``secondary`` is the default button look, so it is not listed.
PRIMARY = "primary"
DANGER = "danger"
GHOST = "ghost"

_LIGHT = {
    "bg": "#f4f4f6",
    "surface": "#ffffff",
    "alt": "#f7f7f9",
    "hover": "#ececef",
    "pressed": "#e2e2e6",
    "border": "#d6d6dc",
    "text": "#1c1c1f",
    "muted": "#6a6a72",
    "disabled_text": "#a6a6ae",
    "accent": "#2563eb",
    "accent_hover": "#1d4fd7",
    "accent_pressed": "#1a46bd",
    "accent_text": "#ffffff",
    "ok": "#15803d",
    "ok_bg": "#e6f5ec",
    "warn": "#a15c00",
    "warn_bg": "#fdf3e2",
    "error": "#b3261e",
    "error_bg": "#fdecea",
}

_DARK = {
    "bg": "#1e1e21",
    "surface": "#28282d",
    "alt": "#2e2e34",
    "hover": "#35353c",
    "pressed": "#3d3d45",
    "border": "#3a3a42",
    "text": "#f0f0f2",
    "muted": "#9c9ca6",
    "disabled_text": "#5f5f68",
    "accent": "#4f8cff",
    "accent_hover": "#6a9dff",
    "accent_pressed": "#3b78e6",
    "accent_text": "#0b1220",
    "ok": "#5ed08a",
    "ok_bg": "#15301f",
    "warn": "#e0a94a",
    "warn_bg": "#33280f",
    "error": "#f2857e",
    "error_bg": "#351a18",
}

_QSS = Template("""
QWidget {
    color: $text;
    font-size: 13px;
}
QMainWindow, QDialog, QStackedWidget {
    background: $bg;
}
QToolBar {
    background: $bg;
    border: none;
    spacing: 6px;
    padding: 7px 8px;
}
QToolBar QLabel { color: $muted; }
QGroupBox {
    background: $surface;
    border: 1px solid $border;
    border-radius: 8px;
    margin-top: 10px;
    padding: 12px 10px 10px 10px;
    font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 10px;
    padding: 0 4px;
    color: $muted;
}
QPushButton {
    background: $surface;
    color: $text;
    border: 1px solid $border;
    border-radius: 6px;
    padding: 5px 14px;
    min-height: 18px;
}
QPushButton:hover { background: $hover; }
QPushButton:pressed { background: $pressed; }
QPushButton:checked {
    background: $accent;
    color: $accent_text;
    border-color: $accent;
    font-weight: 600;
}
QPushButton:checked:hover { background: $accent_hover; border-color: $accent_hover; }
QPushButton:disabled {
    background: $bg;
    color: $disabled_text;
    border-color: $border;
}
QPushButton[role="primary"] {
    background: $accent;
    color: $accent_text;
    border: 1px solid $accent;
    font-weight: 600;
}
QPushButton[role="primary"]:hover {
    background: $accent_hover;
    border-color: $accent_hover;
}
QPushButton[role="primary"]:pressed {
    background: $accent_pressed;
    border-color: $accent_pressed;
}
QPushButton[role="primary"]:disabled {
    background: $border;
    border-color: $border;
    color: $disabled_text;
}
QPushButton[role="danger"] {
    background: transparent;
    color: $error;
    border: 1px solid $error;
    font-weight: 600;
}
QPushButton[role="danger"]:hover { background: $error; color: $surface; }
QPushButton[role="danger"]:disabled {
    background: transparent;
    color: $disabled_text;
    border-color: $border;
}
QPushButton[role="ghost"] {
    background: transparent;
    border: 1px solid transparent;
    color: $muted;
}
QPushButton[role="ghost"]:hover { background: $hover; color: $text; }
QLineEdit, QComboBox {
    background: $surface;
    color: $text;
    border: 1px solid $border;
    border-radius: 6px;
    padding: 4px 8px;
    min-height: 18px;
    selection-background-color: $accent;
    selection-color: $accent_text;
}
QLineEdit:focus, QComboBox:focus { border: 1px solid $accent; }
QLineEdit:disabled, QComboBox:disabled { color: $disabled_text; background: $bg; }
QComboBox::drop-down { border: none; width: 20px; }
QComboBox QAbstractItemView {
    background: $surface;
    color: $text;
    border: 1px solid $border;
    selection-background-color: $accent;
    selection-color: $accent_text;
}
QCheckBox { spacing: 6px; }
QTableWidget {
    background: $surface;
    alternate-background-color: $alt;
    border: 1px solid $border;
    border-radius: 8px;
    gridline-color: $border;
    selection-background-color: $accent;
    selection-color: $accent_text;
}
QTableWidget::item { padding: 3px 6px; }
QHeaderView::section {
    background: $bg;
    color: $muted;
    border: none;
    border-bottom: 1px solid $border;
    padding: 5px 8px;
    font-weight: 600;
}
QTableCornerButton::section { background: $bg; border: none; }
QTabWidget::pane {
    border: 1px solid $border;
    border-radius: 8px;
    top: -1px;
}
QTabBar::tab {
    background: transparent;
    color: $muted;
    padding: 6px 14px;
    margin-right: 2px;
    border-bottom: 2px solid transparent;
}
QTabBar::tab:hover { color: $text; }
QTabBar::tab:selected {
    color: $accent;
    border-bottom: 2px solid $accent;
    font-weight: 600;
}
QStatusBar { color: $muted; }
QStatusBar::item { border: none; }
QToolTip {
    background: $surface;
    color: $text;
    border: 1px solid $border;
    padding: 4px 6px;
}
QLabel[role="muted"] { color: $muted; }
QLabel[role="heading"] { font-size: 14px; font-weight: 600; }
QLabel[role="mono"] { font-family: "$mono"; }
QLabel#connBadge {
    border-radius: 9px;
    padding: 2px 10px;
    font-weight: 600;
}
QLabel#connBadge[state="connected"] { background: $ok_bg; color: $ok; }
QLabel#connBadge[state="disconnected"] { background: $error_bg; color: $error; }
QLabel#rawCommandHint { color: $muted; }
QLabel#rawCommandHint[state="known"] { color: $text; }
QLabel#rawCommandHint[state="refused"] { color: $error; font-weight: 600; }
QLabel#rawCommandHint[state="error"] { color: $error; }
QFrame#compatBanner { border-radius: 8px; }
QFrame#compatBanner[state="idle"] { background: $bg; border: 1px solid $border; }
QFrame#compatBanner[state="idle"] QLabel { color: $muted; }
QFrame#compatBanner[state="compatible"] { background: $ok_bg; border: 1px solid $ok; }
QFrame#compatBanner[state="compatible"] QLabel { color: $ok; }
QFrame#compatBanner[state="limited"] { background: $warn_bg; border: 1px solid $warn; }
QFrame#compatBanner[state="limited"] QLabel { color: $warn; }
QFrame#compatBanner[state="unsupported"] { background: $error_bg; border: 1px solid $error; }
QFrame#compatBanner[state="unsupported"] QLabel { color: $error; }
""")


def is_dark(app: QApplication) -> bool:
    """Whether the running palette is dark, so the theme can follow it."""
    window = app.palette().color(QPalette.ColorRole.Window)
    return window.lightness() < 128


def _palette_for(app: QApplication) -> dict:
    return dict(_DARK if is_dark(app) else _LIGHT)


def mono_font(point_size: int | None = None):
    """The platform's fixed-width font, for hex, addresses and command ids."""
    font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    if point_size is not None:
        font.setPointSize(point_size)
    return font


def make_mono(widget: QWidget, point_size: int | None = None) -> None:
    """Set a widget's font to the fixed-width family."""
    widget.setFont(mono_font(point_size))


def _repolish(widget: QWidget) -> None:
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


def set_role(widget: QWidget, role: str) -> None:
    """Tag a button with a role the stylesheet understands, and repaint it."""
    widget.setProperty("role", role)
    _repolish(widget)


def set_state(widget: QWidget, state: str) -> None:
    """Tag a widget with a state (``connected``, ``refused``, …) and repaint."""
    widget.setProperty("state", state)
    _repolish(widget)


def _qt_palette(p: dict) -> QPalette:
    """A matching QPalette for the parts Qt paints outside the stylesheet."""
    from PySide6.QtGui import QColor

    palette = QPalette()
    role = QPalette.ColorRole
    group = QPalette.ColorGroup
    palette.setColor(role.Window, QColor(p["bg"]))
    palette.setColor(role.WindowText, QColor(p["text"]))
    palette.setColor(role.Base, QColor(p["surface"]))
    palette.setColor(role.AlternateBase, QColor(p["alt"]))
    palette.setColor(role.Text, QColor(p["text"]))
    palette.setColor(role.Button, QColor(p["surface"]))
    palette.setColor(role.ButtonText, QColor(p["text"]))
    palette.setColor(role.Highlight, QColor(p["accent"]))
    palette.setColor(role.HighlightedText, QColor(p["accent_text"]))
    palette.setColor(role.ToolTipBase, QColor(p["surface"]))
    palette.setColor(role.ToolTipText, QColor(p["text"]))
    palette.setColor(role.PlaceholderText, QColor(p["muted"]))
    for disabled in (role.Text, role.ButtonText, role.WindowText):
        palette.setColor(group.Disabled, disabled, QColor(p["disabled_text"]))
    return palette


def apply_theme(app: QApplication) -> None:
    """Select Fusion and paint it with the theme. Call once, after QApplication."""
    app.setStyle("Fusion")
    p = _palette_for(app)
    p["mono"] = mono_font().family()
    app.setPalette(_qt_palette(p))
    app.setStyleSheet(_QSS.substitute(p))
