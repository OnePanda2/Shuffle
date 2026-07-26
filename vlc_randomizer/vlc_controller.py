"""
VLC Controller — launch and drive a single VLC instance over its local HTTP
interface.

One :class:`VlcInstance` == one operating-system VLC process bound to one
loopback HTTP control port. The class is deliberately ignorant of slots,
folders, and selection: it only knows how to start a VLC window, load a file
into it, read its playback status, and shut it down.

Networking is strictly local (127.0.0.1) and works fully offline — the HTTP
interface is a control channel, not a web feature.
"""
from __future__ import annotations

import base64
import json
import logging
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .config import HTTP_HOST, HTTP_PASSWORD, IMAGE_DURATION_SECONDS

logger = logging.getLogger(__name__)


class VlcError(Exception):
    """Base class for VLC control failures."""


class VlcLaunchError(VlcError):
    """Raised when a VLC process cannot be started or never becomes reachable."""


@dataclass
class PlaybackStatus:
    """A snapshot of a VLC instance's playback state."""
    state: str              # "playing" | "paused" | "stopped"
    time: int               # playback position in seconds (freezes while paused)
    length: int             # media length in seconds (0 for images/unknown)
    filename: Optional[str] # current media file name, if any
    plid: Optional[int]     # current playlist item id (-1/None when stopped)


class VlcInstance:
    """Controls one VLC process via ``--extraintf http`` on a dedicated port."""

    # Flags shared by every launch. Kept as a class attribute so behaviour is
    # tunable in one place rather than scattered through the code.
    _BASE_FLAGS = (
        "--no-one-instance",       # guarantee a separate process per slot
        "--extraintf", "http",     # enable the local HTTP control interface
        "--no-qt-privacy-ask",     # skip first-run privacy dialog (would block)
        "--no-qt-error-dialogs",   # never pop modal error dialogs
        "--no-video-title-show",
        "--no-loop", "--no-repeat",  # a finished item must advance, not repeat
        f"--image-duration={IMAGE_DURATION_SECONDS}",  # keep images up until Next
    )

    def __init__(
        self,
        vlc_path: str,
        port: int,
        host: str = HTTP_HOST,
        password: str = HTTP_PASSWORD,
    ) -> None:
        self._vlc_path = vlc_path
        self._port = port
        self._host = host
        self._password = password
        self._proc: Optional[subprocess.Popen] = None

    @property
    def port(self) -> int:
        return self._port

    # -- lifecycle ---------------------------------------------------------

    def launch(self, startup_timeout: float = 15.0) -> None:
        """Start the VLC process and block until its HTTP interface responds.

        Raises
        ------
        VlcLaunchError
            If the executable is missing, the process dies immediately, or the
            HTTP interface never comes up within *startup_timeout* seconds.
        """
        if not Path(self._vlc_path).is_file():
            raise VlcLaunchError(f"VLC executable not found: {self._vlc_path}")

        args = [
            self._vlc_path,
            *self._BASE_FLAGS,
            "--http-host", self._host,
            "--http-port", str(self._port),
            "--http-password", self._password,
        ]
        logger.info("Launching VLC on port %d", self._port)
        try:
            # CREATE_NEW_PROCESS_GROUP lets us signal this process independently.
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            self._proc = subprocess.Popen(args, creationflags=creationflags)
        except OSError as exc:
            raise VlcLaunchError(f"Failed to start VLC: {exc}") from exc

        if not self._wait_until_reachable(startup_timeout):
            self.stop()
            raise VlcLaunchError(
                f"VLC HTTP interface on port {self._port} did not start in time"
            )

    def _wait_until_reachable(self, timeout: float) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._proc is not None and self._proc.poll() is not None:
                return False  # process exited during startup
            if self.get_status() is not None:
                return True
            time.sleep(0.4)
        return False

    def is_process_alive(self) -> bool:
        """True if the underlying OS process is still running."""
        return self._proc is not None and self._proc.poll() is None

    def stop(self) -> None:
        """Terminate the VLC process (graceful, then forced)."""
        if self._proc is None:
            return
        try:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        except OSError as exc:
            logger.warning("Error stopping VLC on port %d: %s", self._port, exc)
        finally:
            self._proc = None

    # -- commands ----------------------------------------------------------

    def load(self, file_path: str) -> bool:
        """Load *file_path* into this already-open window and start playing it.

        The same window is always reused — never closed or recreated. We build a
        deliberate **two-item playlist**: the file, plus a queued duplicate of
        it. This is what makes VLC's own Next button (and end-of-media) detectable
        by the app: with a single item, VLC's Next merely restarts the current
        file (no observable change), whereas with a trailing duplicate it advances
        to a new playlist id the poller can see. The duplicate is the same file,
        so the brief moment before the app overrides it shows identical content.

        Steps: clear the playlist, play the file, enqueue the duplicate. Returns
        True if the play command was accepted.
        """
        try:
            mrl = Path(file_path).as_uri()
        except ValueError as exc:
            logger.error("Cannot build MRL for %s: %s", file_path, exc)
            return False
        # Clear any prior items so old files can never be navigated back to.
        self._request({"command": "pl_empty"})
        result = self._request({"command": "in_play", "input": mrl})
        if result is None:
            logger.warning("Load command failed on port %d for %s",
                           self._port, file_path)
            return False
        # Trailing duplicate: gives VLC's Next / end-of-media somewhere to go.
        self._request({"command": "in_enqueue", "input": mrl})
        return True

    def get_status(self) -> Optional[PlaybackStatus]:
        """Return the current playback status, or None if unreachable."""
        data = self._request({})
        if data is None:
            return None
        raw_plid = data.get("currentplid")
        try:
            plid = int(raw_plid) if raw_plid is not None else None
        except (TypeError, ValueError):
            plid = None
        return PlaybackStatus(
            state=str(data.get("state", "")),
            time=int(data.get("time", 0) or 0),
            length=int(data.get("length", 0) or 0),
            filename=self._extract_filename(data),
            plid=plid,
        )

    @staticmethod
    def _extract_filename(data: dict) -> Optional[str]:
        """Pull the current filename out of VLC's status metadata, if present."""
        try:
            info = data.get("information") or {}
            category = info.get("category") or {}
            meta = category.get("meta") or {}
            return meta.get("filename")
        except AttributeError:
            return None

    # -- HTTP plumbing -----------------------------------------------------

    def _request(self, params: dict, timeout: float = 3.0) -> Optional[dict]:
        """GET /requests/status.json with optional command params.

        Returns the parsed JSON dict, or None on any failure (unreachable,
        auth error, malformed response). Never raises for routine failures so
        the app stays alive when a window is closed or busy.
        """
        query = urllib.parse.urlencode(params) if params else ""
        url = f"http://{self._host}:{self._port}/requests/status.json"
        if query:
            url = f"{url}?{query}"

        req = urllib.request.Request(url)
        token = base64.b64encode(f":{self._password}".encode()).decode()
        req.add_header("Authorization", "Basic " + token)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
            return json.loads(raw.decode("utf-8", errors="replace"))
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            logger.debug("VLC request failed on port %d: %s", self._port, exc)
            return None
        except (json.JSONDecodeError, ValueError) as exc:
            logger.debug("VLC returned unparseable status on port %d: %s",
                         self._port, exc)
            return None
