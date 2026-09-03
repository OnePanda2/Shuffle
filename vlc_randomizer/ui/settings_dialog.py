"""
Settings UI: a per-folder editor dialog and the global settings dialog.

The global dialog manages the VLC executable path and the folder library
(add/edit/remove). It doubles as the first-time setup surface — when no folders
exist yet, the user is sent straight here.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from ..config import (
    DEFAULT_EXCLUDE_SKIPPED, DEFAULT_PERSONAL_ALGORITHM,
    DEFAULT_SHUFFLE_COUNT, DEFAULT_SKIP_THRESHOLD,
)
from ..state_store import Folder


class FolderEditDialog(QDialog):
    """Add or edit a single folder and all of its per-folder settings."""

    def __init__(self, parent: Optional[QWidget] = None,
                 folder: Optional[Folder] = None) -> None:
        super().__init__(parent)
        self._folder = folder
        self._is_favorites = bool(folder and folder.is_favorites)
        self.setWindowTitle("Edit Folder" if folder else "Add Folder")
        self.setMinimumWidth(460)

        self._name = QLineEdit(folder.name if folder else "")
        # The Favorites genre has no folder on disk; keep its sentinel path but
        # never expose it for editing.
        self._path = QLineEdit(folder.path if folder else "")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        path_row = QHBoxLayout()
        path_row.addWidget(self._path)
        path_row.addWidget(browse)
        path_widget = QWidget()
        path_widget.setLayout(path_row)

        self._shuffle = QSpinBox()
        self._shuffle.setRange(1, 100000)
        self._shuffle.setValue(folder.shuffle_count if folder else DEFAULT_SHUFFLE_COUNT)
        self._shuffle.setToolTip(
            "Remaining Shuffle Count (N): how many future Next presses in this "
            "folder before a watched file can be selected again."
        )

        self._threshold = QSpinBox()
        self._threshold.setRange(1, 100000)
        self._threshold.setSuffix(" s")
        self._threshold.setValue(folder.skip_threshold if folder else DEFAULT_SKIP_THRESHOLD)
        self._threshold.setToolTip(
            "Skip threshold: if elapsed time is below this when you press Next, "
            "the file is treated as skipped."
        )

        self._exclude_skipped = QCheckBox("Exclude skipped movies (add skips to cooldown)")
        self._exclude_skipped.setChecked(
            folder.exclude_skipped if folder else DEFAULT_EXCLUDE_SKIPPED
        )

        self._personal_algorithm = QCheckBox("Personal Algorithm (learn my taste over time)")
        self._personal_algorithm.setChecked(
            folder.personal_algorithm if folder else DEFAULT_PERSONAL_ALGORITHM
        )
        self._personal_algorithm.setToolTip(
            "When on, this folder favours files you genuinely enjoy (Affinity) and "
            "gently resurfaces long-neglected ones (Rediscovery), instead of pure "
            "uniform random. Every eligible file always keeps a real chance."
        )

        form = QFormLayout()
        form.addRow("Name:", self._name)
        if self._is_favorites:
            # Virtual genre: no on-disk path to edit, just a note in its place.
            note = QLabel("Virtual genre — plays only your favorited movies.")
            note.setWordWrap(True)
            form.addRow("Folder:", note)
        else:
            form.addRow("Folder:", path_widget)
        form.addRow("Shuffle count (N):", self._shuffle)
        form.addRow("Skip threshold:", self._threshold)
        form.addRow("", self._exclude_skipped)
        form.addRow("", self._personal_algorithm)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def _browse(self) -> None:
        start = self._path.text() or ""
        chosen = QFileDialog.getExistingDirectory(self, "Select genre folder", start)
        if chosen:
            self._path.setText(chosen)
            if not self._name.text().strip():
                # Default the name to the folder's own name for convenience.
                self._name.setText(chosen.replace("\\", "/").rstrip("/").split("/")[-1])

    def _on_accept(self) -> None:
        if not self._name.text().strip():
            QMessageBox.warning(self, "Missing name", "Please enter a folder name.")
            return
        if not self._path.text().strip():
            QMessageBox.warning(self, "Missing folder", "Please choose a folder path.")
            return
        self.accept()

    # -- results -----------------------------------------------------------

    @property
    def values(self) -> dict:
        return {
            "name": self._name.text().strip(),
            "path": self._path.text().strip(),
            "shuffle_count": self._shuffle.value(),
            "skip_threshold": self._threshold.value(),
            "exclude_skipped": self._exclude_skipped.isChecked(),
            "personal_algorithm": self._personal_algorithm.isChecked(),
        }


class SettingsDialog(QDialog):
    """Global settings: VLC path plus the folder library manager."""

    def __init__(self, context, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._context = context
        self.setWindowTitle("Settings")
        self.setMinimumSize(560, 440)

        layout = QVBoxLayout(self)
        layout.addWidget(self._build_vlc_group())
        layout.addWidget(self._build_folder_group(), stretch=1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.accept)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

        self._reload_folders()

    # -- VLC path ----------------------------------------------------------

    def _build_vlc_group(self) -> QGroupBox:
        group = QGroupBox("VLC executable")
        self._vlc_edit = QLineEdit(self._context.vlc_path or "")
        browse = QPushButton("Locate…")
        browse.clicked.connect(self._browse_vlc)
        save = QPushButton("Save")
        save.clicked.connect(self._save_vlc)
        row = QHBoxLayout(group)
        row.addWidget(self._vlc_edit, stretch=1)
        row.addWidget(browse)
        row.addWidget(save)
        return group

    def _browse_vlc(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(
            self, "Locate vlc.exe", self._vlc_edit.text() or "",
            "VLC executable (vlc.exe);;All files (*.*)",
        )
        if chosen:
            self._vlc_edit.setText(chosen)
            self._save_vlc()

    def _save_vlc(self) -> None:
        self._context.vlc_path = self._vlc_edit.text().strip()
        QMessageBox.information(self, "Saved", "VLC path saved.")

    # -- folder manager ----------------------------------------------------

    def _build_folder_group(self) -> QGroupBox:
        group = QGroupBox("Folders (genres)")
        self._folder_list = QListWidget()
        self._folder_list.itemDoubleClicked.connect(lambda _=None: self._edit_folder())

        add = QPushButton("Add…")
        add.clicked.connect(self._add_folder)
        edit = QPushButton("Edit…")
        edit.clicked.connect(self._edit_folder)
        remove = QPushButton("Remove")
        remove.clicked.connect(self._remove_folder)

        btns = QVBoxLayout()
        btns.addWidget(add)
        btns.addWidget(edit)
        btns.addWidget(remove)
        btns.addStretch(1)

        body = QHBoxLayout(group)
        body.addWidget(self._folder_list, stretch=1)
        body.addLayout(btns)
        return group

    def _reload_folders(self) -> None:
        self._folder_list.clear()
        for folder in self._context.state.get_folders():
            location = "your favorited movies" if folder.is_favorites else folder.path
            item = QListWidgetItem(f"{folder.name}   —   {location}")
            item.setData(256, folder.id)  # Qt.UserRole == 256
            self._folder_list.addItem(item)

    def _selected_folder_id(self) -> Optional[int]:
        item = self._folder_list.currentItem()
        return item.data(256) if item else None

    def _add_folder(self) -> None:
        dialog = FolderEditDialog(self)
        if dialog.exec() != QDialog.Accepted:
            return
        v = dialog.values
        try:
            self._context.state.add_folder(
                v["name"], v["path"], v["shuffle_count"],
                v["skip_threshold"], v["exclude_skipped"],
                v["personal_algorithm"],
            )
        except ValueError as exc:
            QMessageBox.warning(self, "Cannot add folder", str(exc))
            return
        self._reload_folders()

    def _edit_folder(self) -> None:
        folder_id = self._selected_folder_id()
        if folder_id is None:
            return
        folder = self._context.state.get_folder(folder_id)
        dialog = FolderEditDialog(self, folder=folder)
        if dialog.exec() != QDialog.Accepted:
            return
        v = dialog.values
        try:
            self._context.state.update_folder(
                folder_id, name=v["name"], path=v["path"],
                shuffle_count=v["shuffle_count"],
                skip_threshold=v["skip_threshold"],
                exclude_skipped=v["exclude_skipped"],
                personal_algorithm=v["personal_algorithm"],
            )
        except ValueError as exc:
            QMessageBox.warning(self, "Cannot update folder", str(exc))
            return
        # A path change invalidates the cached scan.
        self._context.library.invalidate(folder.path)
        self._reload_folders()

    def _remove_folder(self) -> None:
        folder_id = self._selected_folder_id()
        if folder_id is None:
            return
        folder = self._context.state.get_folder(folder_id)
        if folder.is_favorites:
            QMessageBox.information(
                self, "Can't remove",
                "The Favorites genre is permanent and can't be removed. "
                "Un-heart movies to take them out of it.",
            )
            return
        confirm = QMessageBox.question(
            self, "Remove folder",
            f"Remove '{folder.name}' and all of its history?\n"
            f"(Files on disk are not touched.)",
        )
        if confirm == QMessageBox.Yes:
            self._context.state.remove_folder(folder_id)
            self._reload_folders()
