"""
Slot widget — the per-slot control panel, styled as a neo-brutalist card.

Only presentation changed from the original: the control logic (folder assign,
Next, review, rescan, close, status polling) is identical. The card sits inside
a :class:`ShadowBox` for the hard offset shadow, the Next button is the blue
hero, and a live status chip shows whether the current elapsed time will count
as watched or as a skip.
"""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QMessageBox, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from ..slot_manager import NextStatus
from .review_panel import ReviewPanel
from .settings_dialog import FolderEditDialog
from .theme import ShadowBox, apply_letter_spacing

_NO_FOLDER_DATA = -1
# Per-slot accent colours (theme-independent) so quad-view windows are tellable.
_SLOT_ACCENTS = ["#2f6bff", "#ff6a3d", "#5fdc7e", "#ffce33", "#a06bff", "#ff5ca8"]


def _restyle(widget: QWidget) -> None:
    """Re-apply the stylesheet to a widget after its objectName changed."""
    widget.style().unpolish(widget)
    widget.style().polish(widget)


class SlotWidget(QWidget):
    """UI for one player slot. Emits ``closed`` when the user closes the slot."""

    closed = Signal(int)  # slot_id

    def __init__(self, context, slot, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._context = context
        self._slot = slot
        self._populating = False
        self._build()
        self.refresh_folders()
        self._update_current_file()

    # -- construction ------------------------------------------------------

    def _build(self) -> None:
        card = QFrame()
        card.setObjectName("slotCard")
        inner = QVBoxLayout(card)
        inner.setContentsMargins(15, 15, 15, 15)
        inner.setSpacing(9)

        # Header: accent tab + slot name + genre dropdown.
        tab = QFrame()
        tab.setObjectName("accentTab")
        tab.setFixedSize(13, 13)
        accent = _SLOT_ACCENTS[(self._slot.slot_id - 1) % len(_SLOT_ACCENTS)]
        tab.setStyleSheet(f"background: {accent};")

        name = QLabel(f"SLOT {self._slot.slot_id}")
        name.setObjectName("slotName")

        self._folder_combo = QComboBox()
        self._folder_combo.setObjectName("genre")
        self._folder_combo.currentIndexChanged.connect(self._on_folder_changed)

        header = QHBoxLayout()
        header.setSpacing(9)
        header.addWidget(tab)
        header.addWidget(name)
        header.addStretch(1)
        header.addWidget(self._folder_combo)
        inner.addLayout(header)

        # Now playing.
        now = QLabel("NOW PLAYING")
        now.setObjectName("nowLabel")
        apply_letter_spacing(now, 1.8)
        inner.addWidget(now)

        self._file_label = QLabel("—")
        self._file_label.setObjectName("fileLabel")
        self._file_label.setWordWrap(True)
        inner.addWidget(self._file_label)

        # Meta chips: elapsed + live watched/skip status.
        self._elapsed_chip = QLabel("IDLE")
        self._elapsed_chip.setObjectName("chip")
        self._status_chip = QLabel("")
        self._status_chip.setObjectName("chip")
        meta = QHBoxLayout()
        meta.setSpacing(8)
        meta.addWidget(self._elapsed_chip)
        meta.addWidget(self._status_chip)
        meta.addStretch(1)
        inner.addLayout(meta)

        # The hero Next button, wrapped in its hard shadow.
        self._next_btn = QPushButton("NEXT  →")
        self._next_btn.setObjectName("nextBtn")
        self._next_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._next_btn.clicked.connect(self._on_next)
        inner.addWidget(ShadowBox(self._next_btn, offset=5))

        # Secondary actions.
        actions = QGridLayout()
        actions.setSpacing(8)
        for i, (text, handler) in enumerate([
            ("FOLDER", self._on_folder_settings),
            ("REVIEW", self._on_review),
            ("RESCAN", self._on_rescan),
        ]):
            b = QPushButton(text)
            b.setObjectName("slotAction")
            b.clicked.connect(handler)
            actions.addWidget(b, i // 4, i % 4)
        close_btn = QPushButton("CLOSE")
        close_btn.setObjectName("dangerAction")
        close_btn.clicked.connect(self._on_close)
        actions.addWidget(close_btn, 0, 3)
        inner.addLayout(actions)

        # Card gets its own hard shadow inside this widget's layout.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(ShadowBox(card, offset=6))

    # -- folder combo ------------------------------------------------------

    def refresh_folders(self) -> None:
        """Rebuild the folder dropdown from current folders, keeping selection."""
        self._populating = True
        try:
            self._folder_combo.clear()
            self._folder_combo.addItem("— pick genre —", _NO_FOLDER_DATA)
            for folder in self._context.state.get_folders():
                self._folder_combo.addItem(folder.name, folder.id)
            if self._slot.folder_id is not None:
                idx = self._folder_combo.findData(self._slot.folder_id)
                if idx >= 0:
                    self._folder_combo.setCurrentIndex(idx)
        finally:
            self._populating = False

    def _on_folder_changed(self, _index: int) -> None:
        if self._populating:
            return
        folder_id = self._folder_combo.currentData()
        if folder_id is None or folder_id == _NO_FOLDER_DATA:
            return
        try:
            self._context.slots.assign_folder(self._slot.slot_id, folder_id)
        except Exception as exc:
            QMessageBox.warning(self, "Folder assignment failed", str(exc))
            return
        self._update_current_file()

    # -- actions -----------------------------------------------------------

    def _on_next(self) -> None:
        result = self._context.slots.press_next(self._slot.slot_id)
        if result.status is NextStatus.NO_FOLDER:
            QMessageBox.information(self, "No folder", "Pick a genre for this slot first.")
        elif result.status is NextStatus.EMPTY_POOL:
            QMessageBox.information(self, "Nothing to play", result.message)
        elif result.status is NextStatus.LOAD_FAILED:
            QMessageBox.warning(self, "Load failed", result.message)
        self._update_current_file()

    def _on_folder_settings(self) -> None:
        if self._slot.folder_id is None:
            QMessageBox.information(self, "No folder", "Assign a folder first.")
            return
        folder = self._context.state.get_folder(self._slot.folder_id)
        dialog = FolderEditDialog(self, folder=folder)
        if dialog.exec() != QDialog.Accepted:
            return
        v = dialog.values
        try:
            self._context.state.update_folder(
                folder.id, name=v["name"], path=v["path"],
                shuffle_count=v["shuffle_count"],
                skip_threshold=v["skip_threshold"],
                exclude_skipped=v["exclude_skipped"],
                personal_algorithm=v["personal_algorithm"],
            )
        except ValueError as exc:
            QMessageBox.warning(self, "Cannot update folder", str(exc))
            return
        if v["path"] != folder.path:
            self._context.library.invalidate(folder.path)
        self.refresh_folders()

    def _on_review(self) -> None:
        if self._slot.folder_id is None:
            QMessageBox.information(self, "No folder", "Assign a folder first.")
            return
        ReviewPanel(self._context, self._slot.folder_id, self).exec()

    def _on_rescan(self) -> None:
        if self._slot.folder_id is None:
            QMessageBox.information(self, "No folder", "Assign a folder first.")
            return
        count = self._context.rescan_folder(self._slot.folder_id)
        QMessageBox.information(self, "Rescan complete", f"Found {count} media file(s).")

    def _on_close(self) -> None:
        self._context.slots.close_slot(self._slot.slot_id)
        self.closed.emit(self._slot.slot_id)

    # -- display refresh ---------------------------------------------------

    def _update_current_file(self) -> None:
        current = self._slot.current_file
        self._file_label.setText(os.path.basename(current) if current else "—")

    def _set_status_chip(self, object_name: str, text: str) -> None:
        if self._status_chip.objectName() != object_name:
            self._status_chip.setObjectName(object_name)
            _restyle(self._status_chip)
        self._status_chip.setText(text)

    def poll_autoadvance(self) -> None:
        """Detect VLC's native Next / end-of-media and react (logic unchanged)."""
        result = self._context.slots.poll_and_maybe_advance(self._slot.slot_id)
        if result is not None and result.status is NextStatus.OK:
            self._update_current_file()

    def refresh_status(self) -> None:
        """Update the elapsed + watched/skip chips and window liveness."""
        if not self._slot.vlc.is_process_alive():
            self._elapsed_chip.setText("—")
            self._set_status_chip("warnChip", "⚠ VLC WINDOW CLOSED")
            return

        elapsed = self._context.slots.elapsed_for(self._slot.slot_id)
        if elapsed is None or self._slot.current_file is None:
            self._elapsed_chip.setText("IDLE")
            self._set_status_chip("chip", "")
            return

        mins, secs = divmod(int(elapsed), 60)
        self._elapsed_chip.setText(f"⏱ {mins}:{secs:02d}")

        # Live prediction of how this file will be classified on the next advance.
        threshold = 60
        if self._slot.folder_id is not None:
            try:
                threshold = self._context.state.get_folder(self._slot.folder_id).skip_threshold
            except KeyError:
                pass
        if elapsed >= threshold:
            self._set_status_chip("chipWatched", "✓ WATCHED")
        else:
            self._set_status_chip("chipSkip", "↻ SKIP → REVIEW")
