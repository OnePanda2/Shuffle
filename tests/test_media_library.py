"""Tests for recursive scanning, extension filtering, caching, and rescan."""
import os

import pytest

from vlc_randomizer.media_library import MediaLibrary, MediaScanner, file_exists


@pytest.fixture
def library_tree(tmp_path):
    """Build a nested folder tree with a mix of media and non-media files."""
    (tmp_path / "a.mp4").write_bytes(b"x")
    (tmp_path / "b.MKV").write_bytes(b"x")          # case-insensitive
    (tmp_path / "photo.JPG").write_bytes(b"x")      # image counts as media
    (tmp_path / "notes.txt").write_bytes(b"x")      # ignored
    (tmp_path / "readme").write_bytes(b"x")         # no extension, ignored
    sub = tmp_path / "sub" / "deeper"
    sub.mkdir(parents=True)
    (sub / "c.avi").write_bytes(b"x")               # recursive discovery
    (sub / "d.png").write_bytes(b"x")
    return tmp_path


def test_recursive_scan_filters_by_extension(library_tree):
    scanner = MediaScanner()
    found = {os.path.basename(p).lower() for p in scanner.scan(library_tree)}
    assert found == {"a.mp4", "b.mkv", "photo.jpg", "c.avi", "d.png"}


def test_scan_missing_folder_returns_empty(tmp_path):
    scanner = MediaScanner()
    assert scanner.scan(tmp_path / "does_not_exist") == []


def test_is_media_matches_video_and_image():
    scanner = MediaScanner()
    assert scanner.is_media("x.mp4")
    assert scanner.is_media("x.PNG")
    assert not scanner.is_media("x.txt")


def test_custom_extension_set_is_respected(library_tree):
    scanner = MediaScanner(extensions={".mp4"})
    found = {os.path.basename(p).lower() for p in scanner.scan(library_tree)}
    assert found == {"a.mp4"}


def test_library_caches_until_rescan(library_tree):
    lib = MediaLibrary()
    first = lib.get_files(str(library_tree))
    # Add a new file; cached result should NOT reflect it yet.
    (library_tree / "new.mov").write_bytes(b"x")
    assert lib.get_files(str(library_tree)) == first
    # Explicit rescan picks it up.
    rescanned = lib.rescan(str(library_tree))
    assert any(p.lower().endswith("new.mov") for p in rescanned)


def test_file_exists(tmp_path):
    f = tmp_path / "x.mp4"
    f.write_bytes(b"x")
    assert file_exists(str(f))
    assert not file_exists(str(tmp_path / "gone.mp4"))


def test_file_exists_treats_urls_as_present():
    # Cloud URLs can't be cheaply verified, so they are trusted as present.
    assert file_exists("https://archive.org/download/item/movie.mp4")
    assert file_exists("http://example.com/a.mkv")
