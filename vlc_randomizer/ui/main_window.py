"""
Main window — session control and the grid of active slots.

Presentation is neo-brutalist (see theme.py); behaviour is unchanged. Adds the
shuffl. wordmark, a slot counter, and a dark/light theme toggle whose choice is
persisted between launches.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPushButton,
    QScrollArea, QVBoxLayout, QWidget, QGridLayout,
)

from ..config import MAX_SLOTS, POLL_INTERVAL_MS
from ..vlc_controller import VlcError
from .settings_dialog import SettingsDialog
from .slot_widget import SlotWidget
from .theme import apply_letter_spacing

_COLUMNS = 3
_DOT_BLUE = "#2f6bff"


class MainWindow(QMainWindow):
    def __init__(self, context, theme_manager) -> None:
        super().__init__()
        self._context = context
        self._theme = theme_manager
        self._slot_widgets: list[SlotWidget] = []
        self.setWindowTitle("shuffl.")
        self.resize(1040, 700)

        self._build()

        self._timer = QTimer(self)
        self._timer.setInterval(POLL_INTERVAL_MS)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

        QTimer.singleShot(0, self._first_run_check)

    # -- construction ------------------------------------------------------

    def _build(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(18, 14, 18, 16)
        outer.setSpacing(14)
        outer.addLayout(self._build_top_bar())

        self._grid_host = QWidget()
        self._grid = QGridLayout(self._grid_host)
        self._grid.setContentsMargins(2, 2, 2, 2)
        self._grid.setSpacing(16)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(self._grid_host)
        outer.addWidget(scroll, stretch=1)

        self._empty_hint = QLabel(
            "No slots yet — hit “+ Add slot” to open a VLC window and pick a genre."
        )
        self._empty_hint.setObjectName("emptyHint")
        self._empty_hint.setAlignment(Qt.AlignCenter)
        self._grid.addWidget(self._empty_hint, 0, 0)

        self._update_controls()

    def _build_top_bar(self) -> QHBoxLayout:
        # Brand block: wordmark + tagline.
        wordmark = QLabel(f"shuffl<span style='color:{_DOT_BLUE}'>.</span>")
        wordmark.setObjectName("wordmark")
        wordmark.setTextFormat(Qt.RichText)
        tagline = QLabel("SMART VLC RANDOMIZER")
        tagline.setObjectName("tagline")
        apply_letter_spacing(tagline, 2.0)
        brand_col = QVBoxLayout()
        brand_col.setSpacing(1)
        brand_col.addWidget(wordmark)
        brand_col.addWidget(tagline)
        brand = QWidget()
        brand.setLayout(brand_col)

        self._theme_btn = QPushButton()
        self._theme_btn.setObjectName("themeToggle")
        self._theme_btn.clicked.connect(self._toggle_theme)
        self._update_theme_button()

        self._count_label = QLabel()
        self._count_label.setObjectName("slotCount")
        self._count_label.setTextFormat(Qt.RichText)

        settings_btn = QPushButton("SETTINGS")
        settings_btn.clicked.connect(self._open_settings)
        self._add_btn = QPushButton("+ ADD SLOT")
        self._add_btn.setObjectName("primaryBtn")
        self._add_btn.clicked.connect(self._add_slot)

        top = QHBoxLayout()
        top.setSpacing(10)
        top.addWidget(brand)
        top.addStretch(1)
        top.addWidget(self._theme_btn)
        top.addWidget(self._count_label)
        top.addWidget(settings_btn)
        top.addWidget(self._add_btn)
        return top

    # -- theme -------------------------------------------------------------

    def _toggle_theme(self) -> None:
        app = QApplication.instance()
        name = self._theme.toggle(app)
        self._context.set_theme(name)
        self._update_theme_button()

    def _update_theme_button(self) -> None:
        self._theme_btn.setText("☾ DARK" if self._theme.name == "dark" else "☀ LIGHT")

    # -- first run ---------------------------------------------------------

    def _first_run_check(self) -> None:
        if not self._context.vlc_path:
            QMessageBox.warning(
                self, "VLC not found",
                "VLC could not be located automatically. Open Settings and set "
                "the path to vlc.exe before adding slots.",
            )
        if not self._context.state.get_folders():
            QMessageBox.information(
                self, "Welcome",
                "Let's set up your genre folders. Add one or more folders in the "
                "next dialog; you can edit them any time from Settings.",
            )
            self._open_settings()

    # -- slot management ---------------------------------------------------

    def _add_slot(self) -> None:
        if not self._context.slots.can_add_slot:
            QMessageBox.information(self, "Slot limit",
                                    f"Maximum of {MAX_SLOTS} slots reached.")
            return
        if not self._context.vlc_path:
            QMessageBox.warning(self, "VLC not set",
                                "Set the VLC executable path in Settings first.")
            return
        try:
            slot = self._context.slots.create_slot()
        except (VlcError, RuntimeError) as exc:
            QMessageBox.critical(self, "Could not open VLC",
                                 f"Failed to launch a VLC window:\n{exc}")
            return

        widget = SlotWidget(self._context, slot)
        widget.closed.connect(self._on_slot_closed)
        self._slot_widgets.append(widget)
        self._relayout()
        self._update_controls()

    def _on_slot_closed(self, slot_id: int) -> None:
        for widget in list(self._slot_widgets):
            if widget._slot.slot_id == slot_id:
                self._slot_widgets.remove(widget)
                widget.setParent(None)
                widget.deleteLater()
                break
        self._relayout()
        self._update_controls()

    def _relayout(self) -> None:
        while self._grid.count():
            item = self._grid.takeAt(0)
            w = item.widget()
            if w is not None and w is not self._empty_hint:
                w.setParent(self._grid_host)

        if not self._slot_widgets:
            self._empty_hint.setVisible(True)
            self._grid.addWidget(self._empty_hint, 0, 0)
            return

        self._empty_hint.setVisible(False)
        for i, widget in enumerate(self._slot_widgets):
            self._grid.addWidget(widget, i // _COLUMNS, i % _COLUMNS, Qt.AlignTop)

    def _update_controls(self) -> None:
        count = self._context.slots.active_count
        self._count_label.setText(
            f"<b style='color:{_DOT_BLUE}'>{count}</b>/{MAX_SLOTS} SLOTS"
        )
        self._add_btn.setEnabled(self._context.slots.can_add_slot)

    # -- settings ----------------------------------------------------------

    def _open_settings(self) -> None:
        SettingsDialog(self._context, self).exec()
        for widget in self._slot_widgets:
            widget.refresh_folders()

    # -- timer -------------------------------------------------------------

    def _tick(self) -> None:
        for widget in self._slot_widgets:
            widget.poll_autoadvance()
            widget.refresh_status()
