"""
Tests for the cloud media source (Internet Archive) and URL-aware MRL building.

The network fetch is injected, so nothing here touches archive.org.
"""
from vlc_randomizer.cloud_library import CloudLibrary, item_media_urls
from vlc_randomizer.vlc_controller import to_mrl


# A metadata dict shaped like Internet Archive's /metadata/<id> response.
SAMPLE = {
    "files": [
        {"name": "Movie_A.mp4", "source": "original"},
        {"name": "Movie B.mkv", "source": "original"},   # space -> must be encoded
        {"name": "poster.jpg", "source": "derivative"},  # image -> excluded (video-only)
        {"name": "captions.srt", "source": "original"},  # not video -> excluded
        {"name": "notes.txt", "source": "original"},     # not media -> excluded
    ]
}


def _fixed_fetcher(mapping):
    """Return a fetcher that serves canned metadata from a dict, counting calls."""
    calls = {"n": 0}

    def fetch(identifier):
        calls["n"] += 1
        return mapping.get(identifier)

    fetch.calls = calls
    return fetch


# -- item_media_urls -------------------------------------------------------

def test_item_media_urls_filters_to_video_and_encodes():
    urls = item_media_urls("my_item", fetcher=lambda _id: SAMPLE)
    assert urls == [
        "https://archive.org/download/my_item/Movie_A.mp4",
        "https://archive.org/download/my_item/Movie%20B.mkv",
    ]  # images, subtitles, and text are excluded; the space is percent-encoded


def test_item_media_urls_blank_or_failed_fetch_is_empty():
    assert item_media_urls("", fetcher=lambda _id: SAMPLE) == []
    assert item_media_urls("x", fetcher=lambda _id: None) == []      # network failed
    assert item_media_urls("x", fetcher=lambda _id: {"files": []}) == []


# -- CloudLibrary ----------------------------------------------------------

def test_parse_identifiers_splits_and_trims():
    assert CloudLibrary.parse_identifiers("a, b ,c") == ["a", "b", "c"]
    assert CloudLibrary.parse_identifiers("a\n b\n") == ["a", "b"]
    assert CloudLibrary.parse_identifiers("   ") == []


def test_cloud_library_combines_and_dedupes_identifiers():
    fetch = _fixed_fetcher({
        "one": {"files": [{"name": "A.mp4"}, {"name": "B.mp4"}]},
        "two": {"files": [{"name": "B.mp4"}]},  # B duplicated across items
    })
    lib = CloudLibrary(fetcher=fetch)
    urls = lib.get_files("one, two")
    names = [u.rsplit("/", 1)[-1] for u in urls]
    assert names == ["A.mp4", "B.mp4", "B.mp4"]  # different items -> different URLs kept
    # But the *same* URL is never duplicated:
    assert len(urls) == len(set(urls))


def test_cloud_library_caches_until_rescan():
    fetch = _fixed_fetcher({"one": {"files": [{"name": "A.mp4"}]}})
    lib = CloudLibrary(fetcher=fetch)
    lib.get_files("one")
    lib.get_files("one")                      # served from cache, no new fetch
    assert fetch.calls["n"] == 1
    lib.rescan("one")                         # forces a fresh fetch
    assert fetch.calls["n"] == 2


def test_cloud_library_invalidate():
    fetch = _fixed_fetcher({"one": {"files": [{"name": "A.mp4"}]}})
    lib = CloudLibrary(fetcher=fetch)
    lib.get_files("one")
    lib.invalidate("one")
    lib.get_files("one")
    assert fetch.calls["n"] == 2


# -- to_mrl ----------------------------------------------------------------

def test_to_mrl_passes_through_urls():
    url = "https://archive.org/download/item/Movie%20A.mp4"
    assert to_mrl(url) == url
    assert to_mrl("http://x/y.mp4") == "http://x/y.mp4"


def test_to_mrl_converts_local_path_to_file_uri():
    mrl = to_mrl(r"C:\media\a.mp4")
    assert mrl.startswith("file:///")
    assert mrl.endswith("a.mp4")
