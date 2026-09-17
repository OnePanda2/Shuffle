"""
Smart VLC Randomizer
====================

A local, offline Windows companion for VLC Media Player that delivers a
genuinely uniform, unbiased shuffle across a large media library and manages
per-folder watch history so recently seen files do not repeat too soon.

This package is a *selector and controller* only. VLC remains the sole media
player; this application never decodes or renders media itself.

Module map (dependency order, low-level first):

    config            Immutable defaults, paths, VLC auto-detection.
    logging_setup     Rotating file + console logging.
    media_library     Recursive folder scanning and extension filtering.
    state_store       SQLite persistence: folders, exclusion list, review list.
    selection_engine  Eligible-pool construction + uniform random selection.
    vlc_controller    Launch/control a single VLC instance over local HTTP.
    slot_manager      Slot lifecycle + the 8-step "Next" pipeline (the crux).
    ui/               PySide6 user interface.

See docs/DEVELOPER.md for architecture, schema, and communication flow.
"""

__version__ = "1.5.0"
