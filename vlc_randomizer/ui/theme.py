"""
Visual theme: a blue-forward *neo-brutalism* look with dark and light variants.

Neo-brutalism relies on three things Qt style sheets do NOT provide directly:
thick solid borders (fine), hard *offset* drop shadows (no `box-shadow` in QSS),
and pressable buttons. The offset shadow is faked with :class:`ShadowBox` — a
frame painted in the border colour with the content inset so a solid slab shows
at the bottom-right. Because the shadow colour comes from the stylesheet, it
flips automatically when the theme changes.

Only presentation lives here. No selection/slot/VLC logic is touched.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QFrame, QVBoxLayout, QWidget

# Accent colours are intentionally identical across themes — the loud pops are
# the brand. Only the neutrals (ink / paper / ground / muted) flip.
_ACCENTS = {
    "blue": "#2f6bff",
    "blue_dark": "#3f78ff",
    "blue_deep": "#1c3fd6",
    "green": "#5fdc7e",
    "coral": "#ff6a3d",
    "yellow": "#ffce33",
    "on_blue": "#ffffff",
    "on_green": "#0d2417",
    "on_coral": "#2a0f06",
    "on_yellow": "#111624",
}

PALETTES = {
    "light": {
        **_ACCENTS,
        "blue": "#2f6bff",
        "ink": "#111624",       # borders + primary text
        "paper": "#ffffff",     # card / control surface
        "ground": "#e6eefc",    # window background (cool, blue-biased)
        "muted": "#5c6779",
        "hover": "#dbe6fb",
    },
    "dark": {
        **_ACCENTS,
        "blue": "#3f78ff",
        "ink": "#eaf0fb",       # off-white borders + text
        "paper": "#191d27",
        "ground": "#0e111a",
        "muted": "#98a2b5",
        "hover": "#232838",
    },
}

DEFAULT_THEME = "dark"


def build_qss(p: dict) -> str:
    """Return the full application stylesheet for palette *p*."""
    return f"""
    QWidget {{
        background: {p['ground']};
        color: {p['ink']};
        font-family: "Segoe UI", system-ui, sans-serif;
        font-size: 13px;
    }}
    /* Labels never paint their own background unless given one explicitly
       (chips, counter). Keeps text transparent over cards and the window. */
    QLabel {{ background: transparent; }}
    QToolTip {{
        background: {p['paper']}; color: {p['ink']};
        border: 2px solid {p['ink']}; padding: 4px 6px;
    }}

    /* ---- brand / top bar ---- */
    QLabel#wordmark {{ font-size: 26px; font-weight: 900; }}
    QLabel#tagline  {{ color: {p['muted']}; font-family: "Cascadia Mono", Consolas, monospace;
                       font-size: 9px; font-weight: 800; }}
    QLabel#slotCount {{ background: {p['paper']}; border: 2px solid {p['ink']};
                        border-radius: 6px; padding: 6px 10px;
                        font-family: "Cascadia Mono", Consolas, monospace; font-weight: 800; }}
    QLabel#emptyHint {{ color: {p['muted']}; font-weight: 700; }}

    /* ---- buttons ---- */
    QPushButton {{
        background: {p['paper']}; color: {p['ink']};
        border: 2px solid {p['ink']}; border-radius: 6px;
        padding: 8px 13px; font-weight: 800;
    }}
    QPushButton:hover {{ background: {p['hover']}; }}
    QPushButton:pressed {{ margin: 1px 0 0 1px; }}

    QPushButton#primaryBtn {{ background: {p['blue']}; color: {p['on_blue']}; }}
    QPushButton#primaryBtn:hover {{ background: {p['blue_deep']}; }}

    QPushButton#themeToggle {{
        background: {p['blue']}; color: {p['on_blue']};
        border: 2px solid {p['ink']}; border-radius: 999px;
        font-family: "Cascadia Mono", Consolas, monospace; font-weight: 800;
        padding: 7px 13px;
    }}

    /* ---- slot card ---- */
    QFrame#shadowBox {{ background: {p['ink']}; border-radius: 9px; }}
    QFrame#slotCard  {{ background: {p['paper']}; border: 3px solid {p['ink']};
                        border-radius: 9px; }}
    QLabel#slotName {{ font-size: 15px; font-weight: 900; }}
    QLabel#nowLabel {{ color: {p['muted']}; font-family: "Cascadia Mono", Consolas, monospace;
                       font-size: 10px; font-weight: 800; }}
    QLabel#fileLabel {{ font-size: 15px; font-weight: 800; }}
    QFrame#accentTab {{ border: 2px solid {p['ink']}; }}

    /* status + elapsed chips */
    QLabel#chip {{ border: 2px solid {p['ink']}; border-radius: 5px; padding: 5px 8px;
                   font-family: "Cascadia Mono", Consolas, monospace; font-weight: 800;
                   font-size: 10px; background: {p['paper']}; color: {p['ink']}; }}
    QLabel#chipWatched {{ background: {p['green']}; color: {p['on_green']};
                          border: 2px solid {p['ink']}; border-radius: 5px; padding: 5px 8px;
                          font-family: "Cascadia Mono", Consolas, monospace; font-weight: 800; font-size: 10px; }}
    QLabel#chipSkip {{ background: {p['coral']}; color: {p['on_coral']};
                       border: 2px solid {p['ink']}; border-radius: 5px; padding: 5px 8px;
                       font-family: "Cascadia Mono", Consolas, monospace; font-weight: 800; font-size: 10px; }}
    QLabel#warnChip {{ background: {p['coral']}; color: {p['on_coral']};
                       border: 2px solid {p['ink']}; border-radius: 5px; padding: 5px 8px;
                       font-weight: 800; font-size: 10px; }}

    /* the hero Next button */
    QPushButton#nextBtn {{
        background: {p['blue']}; color: {p['on_blue']};
        border: 3px solid {p['ink']}; border-radius: 8px;
        font-size: 16px; font-weight: 900; padding: 14px; letter-spacing: 1px;
    }}
    QPushButton#nextBtn:hover {{ background: {p['blue_deep']}; }}
    QPushButton#nextBtn:pressed {{ margin: 5px 0 0 5px; }}

    /* small per-slot action buttons */
    QPushButton#slotAction {{ font-size: 10px; padding: 8px 4px; border-radius: 5px; }}
    QPushButton#dangerAction {{ font-size: 10px; padding: 8px 4px; border-radius: 5px;
        background: {p['coral']}; color: {p['on_coral']}; border: 2px solid {p['ink']}; }}

    /* ---- inputs ---- */
    QComboBox#genre {{
        background: {p['yellow']}; color: {p['on_yellow']};
        border: 2px solid {p['ink']}; border-radius: 5px; padding: 6px 10px; font-weight: 800;
    }}
    QComboBox {{ background: {p['paper']}; color: {p['ink']}; border: 2px solid {p['ink']};
                 border-radius: 5px; padding: 6px 10px; font-weight: 700; }}
    QComboBox QAbstractItemView {{ background: {p['paper']}; color: {p['ink']};
        border: 2px solid {p['ink']}; selection-background-color: {p['blue']};
        selection-color: {p['on_blue']}; outline: none; }}
    QComboBox::drop-down {{ border: 0; width: 20px; }}

    QLineEdit, QSpinBox {{ background: {p['paper']}; color: {p['ink']};
        border: 2px solid {p['ink']}; border-radius: 5px; padding: 7px 9px; font-weight: 600; }}
    QSpinBox::up-button, QSpinBox::down-button {{ width: 16px; }}

    QListWidget {{ background: {p['paper']}; color: {p['ink']};
        border: 2px solid {p['ink']}; border-radius: 6px; outline: none; }}
    QListWidget::item {{ padding: 6px 4px; }}
    QListWidget::item:selected {{ background: {p['blue']}; color: {p['on_blue']}; }}

    QGroupBox {{ border: 2px solid {p['ink']}; border-radius: 8px; margin-top: 12px;
        font-weight: 800; padding: 10px; }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 6px;
        background: {p['ground']}; }}

    QCheckBox {{ font-weight: 700; spacing: 8px; }}
    QCheckBox::indicator {{ width: 18px; height: 18px; border: 2px solid {p['ink']};
        border-radius: 4px; background: {p['paper']}; }}
    QCheckBox::indicator:checked {{ background: {p['blue']}; }}

    QScrollBar:vertical {{ background: {p['ground']}; width: 12px; margin: 0; }}
    QScrollBar::handle:vertical {{ background: {p['muted']}; border-radius: 5px; min-height: 30px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}

    QDialog {{ background: {p['ground']}; }}
    """


def apply_letter_spacing(widget: QWidget, spacing: float = 1.5) -> None:
    """Add absolute letter-spacing to a widget's font (QSS can't do this)."""
    f = widget.font()
    f.setLetterSpacing(QFont.AbsoluteSpacing, spacing)
    widget.setFont(f)


class ShadowBox(QFrame):
    """Wraps a widget and paints a hard neo-brutalist offset shadow behind it.

    The frame is filled with the border ('ink') colour via QSS; the content is
    inset by *offset* px on the bottom-right so a solid slab of that colour shows
    there. No blur, no graphics effect — it composes cleanly and re-themes with
    the stylesheet.
    """

    def __init__(self, content: QWidget, offset: int = 5, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("shadowBox")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, offset, offset)
        layout.setSpacing(0)
        layout.addWidget(content)


class ThemeManager:
    """Applies a palette to the whole application and toggles dark/light."""

    def __init__(self, initial: str = DEFAULT_THEME) -> None:
        self.name = initial if initial in PALETTES else DEFAULT_THEME

    @property
    def palette(self) -> dict:
        return PALETTES[self.name]

    def apply(self, app: QApplication) -> None:
        app.setStyleSheet(build_qss(self.palette))

    def toggle(self, app: QApplication) -> str:
        """Flip the theme, restyle the app, and return the new theme name."""
        self.name = "light" if self.name == "dark" else "dark"
        self.apply(app)
        return self.name
