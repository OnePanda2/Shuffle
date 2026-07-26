"""
Review / Delete panel — an on-demand dialog listing a folder's skipped files.

Opened per folder from a slot. Offers the two spec'd actions:
  * Delete Permanently  — send the file to the Recycle Bin and drop the entry.
  * Remove From List    — drop the entry only; the file on disk stays.

The panel is not persistently visible; it is created when requested and closed
when done.
"""
from __future__ import annotations

import os
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox,
    QPushButton, QVBoxLayout, QWidget,
)


class ReviewPanel(QDialog):
    """Lists and acts on the Review/Delete list for one folder."""

    def __init__(self, context, folder_id: int, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._context = context
        self._folder_id = folder_id
        folder = context.state.get_folder(folder_id)
        self.setWindowTitle(f"Review / Delete — {folder.name}")
        self.setMinimumSize(560, 400)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Skipped files held for review. Deleting sends the file to the "
            "Windows Recycle Bin (recoverable)."
        ))

        self._list = QListWidget()
        self._list.setSelectionMode(QListWidget.ExtendedSelection)
        layout.addWidget(self._list, stretch=1)

        delete_btn = QPushButton("Delete Permanently")
        delete_btn.clicked.connect(self._delete_selected)
        remove_btn = QPushButton("Remove From List")
        remove_btn.clicked.connect(self._remove_selected)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)

        row = QHBoxLayout()
        row.addWidget(delete_btn)
        row.addWidget(remove_btn)
        row.addStretch(1)
        row.addWidget(close_btn)
        layout.addLayout(row)

        self._reload()

    def _reload(self) -> None:
        self._list.clear()
        items = self._context.state.get_review_items(self._folder_id)
        if not items:
            placeholder = QListWidgetItem("(No files awaiting review.)")
            placeholder.setFlags(Qt.NoItemFlags)
            self._list.addItem(placeholder)
            return
        for entry in items:
            exists = os.path.isfile(entry.file_path)
            label = os.path.basename(entry.file_path)
            if not exists:
                label += "   (missing on disk)"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, entry.file_path)
            item.setToolTip(entry.file_path)
            self._list.addItem(item)

    def _selected_paths(self) -> list[str]:
        paths = []
        for item in self._list.selectedItems():
            path = item.data(Qt.UserRole)
            if path:
                paths.append(path)
        return paths

    def _delete_selected(self) -> None:
        paths = self._selected_paths()
        if not paths:
            return
        confirm = QMessageBox.question(
            self, "Delete permanently",
            f"Send {len(paths)} file(s) to the Recycle Bin?",
        )
        if confirm != QMessageBox.Yes:
            return
        failures = []
        for path in paths:
            if not self._context.delete_permanently(self._folder_id, path):
                failures.append(path)
        if failures:
            QMessageBox.warning(
                self, "Some deletions failed",
                "Could not recycle:\n" + "\n".join(os.path.basename(p) for p in failures),
            )
        self._reload()

    def _remove_selected(self) -> None:
        for path in self._selected_paths():
            self._context.state.remove_from_review(self._folder_id, path)
        self._reload()
