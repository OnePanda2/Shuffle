"""
Composition root.

Wires the low-level modules together into an :class:`AppContext`, then starts the
Qt user interface. Keeping construction here means every module below stays
unaware of how it is assembled, and the UI depends only on this small facade.
"""
from __future__ import annotations

import logging
import sys

from .config import AppPaths, detect_vlc_path
from .file_ops import move_to_recycle_bin
from .logging_setup import configure_logging
from .media_library import MediaLibrary
from .slot_manager import SlotManager
from .state_store import StateStore
from .vlc_controller import VlcInstance

logger = logging.getLogger(__name__)

SETTING_VLC_PATH = "vlc_path"
SETTING_THEME = "theme"


class AppContext:
    """Facade the UI uses to reach application services.

    Holds the singletons (state store, media library, slot manager) and exposes
    the few cross-cutting helpers the UI needs (VLC path config, rescan, delete).
    """

    def __init__(self, paths: AppPaths) -> None:
        self.paths = paths
        self.state = StateStore(paths.db_path)
        self.library = MediaLibrary()

        # Seed the VLC path once from auto-detection if the user hasn't set it.
        if self.state.get_setting(SETTING_VLC_PATH) is None:
            detected = detect_vlc_path()
            if detected:
                self.state.set_setting(SETTING_VLC_PATH, detected)
                logger.info("Auto-detected VLC at %s", detected)

        # The factory reads the *current* VLC path each time a slot is created,
        # so a path change in settings takes effect for subsequent slots.
        def vlc_factory(port: int) -> VlcInstance:
            vlc_path = self.state.get_setting(SETTING_VLC_PATH) or ""
            return VlcInstance(vlc_path, port)

        self.slots = SlotManager(self.state, self.library, vlc_factory)

    # -- cross-cutting helpers used by the UI ------------------------------

    @property
    def vlc_path(self) -> str | None:
        return self.state.get_setting(SETTING_VLC_PATH)

    @vlc_path.setter
    def vlc_path(self, value: str) -> None:
        self.state.set_setting(SETTING_VLC_PATH, value)

    @property
    def theme(self) -> str:
        return self.state.get_setting(SETTING_THEME, "dark") or "dark"

    def set_theme(self, name: str) -> None:
        self.state.set_setting(SETTING_THEME, name)

    def rescan_folder(self, folder_id: int) -> int:
        """Rescan a folder's files, returning the new file count."""
        folder = self.state.get_folder(folder_id)
        return len(self.library.rescan(folder.path))

    def delete_permanently(self, folder_id: int, file_path: str) -> bool:
        """Recycle the file on disk and drop it from the review list on success."""
        ok = move_to_recycle_bin(file_path)
        if ok:
            self.state.remove_from_review(folder_id, file_path)
        return ok

    def shutdown(self) -> None:
        """Close all slots and the database connection."""
        try:
            self.slots.shutdown()
        finally:
            self.state.close()


def run() -> int:
    """Application entry point. Returns a process exit code."""
    paths = AppPaths.create()
    configure_logging(paths.log_path)
    logger.info("Starting Smart VLC Randomizer")

    context = AppContext(paths)

    # Import Qt lazily so the non-UI modules and tests never require PySide6.
    from PySide6.QtWidgets import QApplication
    from .ui.main_window import MainWindow
    from .ui.theme import ThemeManager

    qt_app = QApplication(sys.argv)
    qt_app.setApplicationName("shuffl.")

    theme = ThemeManager(context.theme)
    theme.apply(qt_app)
    window = MainWindow(context, theme)

    # Ensure every VLC window is closed when the app exits.
    qt_app.aboutToQuit.connect(context.shutdown)

    window.show()
    return qt_app.exec()
