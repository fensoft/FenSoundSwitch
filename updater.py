"""Opt-in, bounded downloads of the official Windows release installer.

Importing this module has no side effects. Call network functions on a worker,
and call ``launch_installer`` only after the user has accepted the update.
The visible Windows Installer owns the installation and elevation prompts.
An installer handed off successfully remains in its unique temporary directory
so that exiting the application cannot remove a file still needed by MSI.
"""

from __future__ import annotations

import ctypes
import hashlib
import hmac
from http.client import HTTPException
import json
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


LATEST_RELEASE_URL = "https://api.github.com/repos/fensoft/FenSoundSwitch/releases/latest"
RELEASE_DOWNLOAD_PREFIX = "https://github.com/fensoft/FenSoundSwitch/releases/download/"
ASSET_NAME = "FenSoundSwitch.msi"
MAX_API_BYTES = 1024 * 1024
MAX_INSTALLER_BYTES = 512 * 1024 * 1024
SOCKET_TIMEOUT_SECONDS = 10
CHECK_TIMEOUT_SECONDS = 30
DOWNLOAD_TIMEOUT_SECONDS = 10 * 60
CHUNK_BYTES = 64 * 1024
TEMP_DIRECTORY_PREFIX = "fensoundswitch-update-"
_VERSION = re.compile(r"v?([0-9]{1,3})\.([0-9]{1,3})(?:\.([0-9]{1,5}))?\Z")
_SHA256 = re.compile(r"[0-9a-fA-F]{64}\Z")


class UpdateError(Exception):
    """An update could not be checked, verified, downloaded, or started."""


@dataclass(frozen=True)
class ReleaseUpdate:
    version: str
    download_url: str
    size: int
    sha256: str


@dataclass
class DownloadedUpdate:
    release: ReleaseUpdate
    path: Path
    directory: Path
    _owned_directory: Path | None = field(default=None, init=False, repr=False)
    _directory_identity: tuple[int, int] | None = field(default=None, init=False, repr=False)
    _verified_release: ReleaseUpdate | None = field(default=None, init=False, repr=False)
    _file_identity: tuple[int, int, int, int, int] | None = field(default=None, init=False, repr=False)
    _handed_off: bool = field(default=False, init=False, repr=False)

    def cleanup(self) -> None:
        """Remove only our two known filenames, never another directory tree.

        A manually constructed record owns nothing. A replaced/symlinked
        directory or a successful installer handoff is deliberately left alone.
        Unexpected files prevent removal of the directory and are preserved.
        """
        directory = self._owned_directory
        if self._handed_off or directory is None:
            return
        try:
            info = directory.lstat()
            if (
                not stat.S_ISDIR(info.st_mode)
                or (info.st_dev, info.st_ino) != self._directory_identity
            ):
                return
            for filename in (ASSET_NAME + ".part", ASSET_NAME):
                try:
                    (directory / filename).unlink()
                except OSError:
                    pass
            directory.rmdir()
        except OSError:
            pass


def _numeric_version(value: object) -> tuple[int, int, int] | None:
    if not isinstance(value, str):
        return None
    match = _VERSION.fullmatch(value)
    if match is None:
        return None
    major, minor, build = (int(part or "0") for part in match.groups())
    if major > 255 or minor > 255 or build > 65535:
        return None
    return major, minor, build


def is_update_supported(version: str, *, packaged: bool | None = None) -> bool:
    """Enable release updates only for versioned, packaged Windows builds."""
    if packaged is None:
        packaged = bool(getattr(sys, "frozen", False) or "__compiled__" in globals())
    return sys.platform == "win32" and bool(packaged) and _numeric_version(version) is not None


def _check_cancelled(cancel: threading.Event, deadline: float) -> None:
    if cancel.is_set():
        raise UpdateError("Update cancelled.")
    if time.monotonic() >= deadline:
        raise UpdateError("The update request timed out.")


def _safe_https_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
        return (
            parts.scheme == "https"
            and bool(parts.hostname)
            and parts.username is None
            and parts.password is None
            and parts.port in (None, 443)
            and not parts.fragment
            and not any(ord(character) < 33 for character in url)
            and "\\" not in url
        )
    except ValueError:
        return False


def _allowed_response_url(url: str, original: str, *, download: bool) -> bool:
    if not _safe_https_url(url):
        return False
    if url == original:
        return True
    return download and urlsplit(url).hostname == "release-assets.githubusercontent.com"


class _RestrictedRedirectHandler(HTTPRedirectHandler):
    max_redirections = 4
    max_repeats = 2

    def __init__(self, original: str, *, download: bool) -> None:
        super().__init__()
        self.original = original
        self.download = download

    def redirect_request(self, request, response, code, message, headers, new_url):
        if not self.download or not _allowed_response_url(new_url, self.original, download=True):
            raise UpdateError("The update server returned an untrusted redirect.")
        return super().redirect_request(request, response, code, message, headers, new_url)


def _open_response(url: str, cancel: threading.Event, deadline: float, *, download: bool):
    _check_cancelled(cancel, deadline)
    opener = build_opener(_RestrictedRedirectHandler(url, download=download))
    request = Request(
        url,
        headers={
            "User-Agent": "FenSoundSwitch-Updater",
            "Accept": "application/octet-stream" if download else "application/vnd.github+json",
            "Accept-Encoding": "identity",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    response = opener.open(request, timeout=min(SOCKET_TIMEOUT_SECONDS, max(0.1, deadline - time.monotonic())))
    try:
        _check_cancelled(cancel, deadline)
        if response.status != 200:
            raise UpdateError("The update server did not return a complete response.")
        if not _allowed_response_url(response.geturl(), url, download=download):
            raise UpdateError("The update server returned an untrusted address.")
        encoding = response.headers.get("Content-Encoding", "identity")
        if encoding.lower() != "identity":
            raise UpdateError("The update server returned an unsupported encoding.")
    except BaseException:
        response.close()
        raise
    return response


def _content_length(response, maximum: int) -> int | None:
    value = response.headers.get("Content-Length")
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,12}", value):
        raise UpdateError("The update server returned an invalid response size.")
    size = int(value)
    if size > maximum:
        raise UpdateError("The update response is too large.")
    return size


def _read_chunk(response, count: int) -> bytes:
    # read1 performs at most one underlying read, allowing deadline/cancel
    # checks between chunks even when a peer sends a slow trickle of bytes.
    reader = getattr(response, "read1", response.read)
    value = reader(count)
    if not isinstance(value, bytes) or len(value) > count:
        raise UpdateError("The update server returned invalid data.")
    return value


def _validated_release(release: ReleaseUpdate) -> None:
    if _numeric_version(release.version) is None:
        raise UpdateError("The release version is not a supported stable Windows Installer version.")
    if release.download_url != RELEASE_DOWNLOAD_PREFIX + release.version + "/" + ASSET_NAME:
        raise UpdateError("The installer is not an official FenSoundSwitch release asset.")
    if type(release.size) is not int or not 0 < release.size <= MAX_INSTALLER_BYTES:
        raise UpdateError("The release installer size is invalid.")
    if not isinstance(release.sha256, str) or _SHA256.fullmatch(release.sha256) is None:
        raise UpdateError("The release installer has no valid SHA-256 digest.")


def check_for_update(current_version: str, cancel: threading.Event) -> ReleaseUpdate | None:
    """Read only the official latest stable release; never download its MSI."""
    current = _numeric_version(current_version)
    if current is None:
        return None
    deadline = time.monotonic() + CHECK_TIMEOUT_SECONDS
    try:
        with _open_response(LATEST_RELEASE_URL, cancel, deadline, download=False) as response:
            declared_size = _content_length(response, MAX_API_BYTES)
            data = bytearray()
            while True:
                _check_cancelled(cancel, deadline)
                chunk = _read_chunk(response, min(CHUNK_BYTES, MAX_API_BYTES + 1 - len(data)))
                _check_cancelled(cancel, deadline)
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > MAX_API_BYTES:
                    raise UpdateError("The update response is too large.")
            if declared_size is not None and len(data) != declared_size:
                raise UpdateError("The update response was incomplete.")
        document = json.loads(data)
        if not isinstance(document, dict) or document.get("draft") is not False or document.get("prerelease") is not False:
            raise UpdateError("The update server did not return a stable release.")
        version = document.get("tag_name")
        newest = _numeric_version(version)
        if newest is None:
            raise UpdateError("The release version is not supported.")
        if newest <= current:
            return None
        assets = document.get("assets")
        if not isinstance(assets, list):
            raise UpdateError("The release has no installer assets.")
        matches = [asset for asset in assets if isinstance(asset, dict) and asset.get("name") == ASSET_NAME]
        if len(matches) != 1:
            raise UpdateError("The release must contain exactly one FenSoundSwitch installer.")
        asset = matches[0]
        digest = asset.get("digest")
        if not isinstance(digest, str) or not digest.startswith("sha256:"):
            raise UpdateError("The release installer has no SHA-256 digest.")
        release = ReleaseUpdate(version, asset.get("browser_download_url"), asset.get("size"), digest[7:])
        _validated_release(release)
        _check_cancelled(cancel, deadline)
        return release
    except UpdateError:
        raise
    except HTTPError as error:
        error.close()
        if error.code == 404:
            return None
        raise UpdateError("The latest release could not be checked.") from None
    except (OSError, URLError, HTTPException, ValueError, TypeError, RecursionError):
        raise UpdateError("The latest release could not be checked.") from None


def download_update(release: ReleaseUpdate, cancel: threading.Event) -> DownloadedUpdate:
    """Download into an owned temp directory and verify exact size and SHA-256."""
    _validated_release(release)
    deadline = time.monotonic() + DOWNLOAD_TIMEOUT_SECONDS
    _check_cancelled(cancel, deadline)
    download = None
    try:
        directory = Path(tempfile.mkdtemp(prefix=TEMP_DIRECTORY_PREFIX)).absolute()
        download = DownloadedUpdate(release, directory / ASSET_NAME, directory)
        info = directory.lstat()
        download._owned_directory = directory
        download._directory_identity = (info.st_dev, info.st_ino)
        partial = directory / (ASSET_NAME + ".part")
        with _open_response(release.download_url, cancel, deadline, download=True) as response:
            declared_size = _content_length(response, MAX_INSTALLER_BYTES)
            if declared_size is not None and declared_size != release.size:
                raise UpdateError("The downloaded installer size does not match the release.")
            digest = hashlib.sha256()
            total = 0
            with partial.open("xb") as target:
                while True:
                    _check_cancelled(cancel, deadline)
                    chunk = _read_chunk(response, min(CHUNK_BYTES, release.size + 1 - total))
                    _check_cancelled(cancel, deadline)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > release.size:
                        raise UpdateError("The downloaded installer is larger than the release.")
                    digest.update(chunk)
                    target.write(chunk)
            if total != release.size or not hmac.compare_digest(digest.hexdigest(), release.sha256.lower()):
                raise UpdateError("The downloaded installer failed SHA-256 or size verification.")
        _check_cancelled(cancel, deadline)
        partial.replace(download.path)
        info = download.path.lstat()
        download._verified_release = release
        download._file_identity = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        return download
    except UpdateError:
        if download is not None:
            download.cleanup()
        raise
    except HTTPError as error:
        error.close()
        if download is not None:
            download.cleanup()
        raise UpdateError("The release installer could not be downloaded.") from None
    except (OSError, URLError, HTTPException, ValueError, TypeError):
        if download is not None:
            download.cleanup()
        raise UpdateError("The release installer could not be downloaded.") from None


def _windows_installer_path() -> Path:
    # Read the trusted Windows system directory through Win32, not PATH or a
    # caller-controlled SystemRoot environment variable.
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_system_directory = kernel32.GetSystemDirectoryW
    get_system_directory.argtypes = [ctypes.c_wchar_p, ctypes.c_uint]
    get_system_directory.restype = ctypes.c_uint
    buffer = ctypes.create_unicode_buffer(32768)
    length = get_system_directory(buffer, len(buffer))
    if not length or length >= len(buffer):
        raise UpdateError("The Windows Installer system directory could not be resolved.")
    directory = Path(buffer.value)
    executable = directory / "msiexec.exe"
    if not directory.is_absolute() or directory.name.lower() != "system32" or not executable.is_file():
        raise UpdateError("The Windows Installer executable could not be found.")
    return executable


def launch_installer(download: DownloadedUpdate) -> None:
    """Start visible MSI UI. The caller must then perform normal app shutdown.

    The worker has verified the digest. Identity, size, and modification metadata
    must still match before handoff, without hashing a large file on the UI
    thread. A failed process creation leaves the application running.
    """
    if sys.platform != "win32":
        raise UpdateError("Installing updates requires Windows.")
    _validated_release(download.release)
    try:
        directory = download._owned_directory
        if (
            download._handed_off
            or directory is None
            or download.directory != directory
            or download.path != directory / ASSET_NAME
            or download.release != download._verified_release
        ):
            raise UpdateError("The installer is not an owned update download.")
        directory_info = directory.lstat()
        info = download.path.lstat()
        if (
            (directory_info.st_dev, directory_info.st_ino) != download._directory_identity
            or not stat.S_ISDIR(directory_info.st_mode)
            or not stat.S_ISREG(info.st_mode)
            or (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns) != download._file_identity
        ):
            raise UpdateError("The downloaded installer is no longer valid.")
        executable = _windows_installer_path()
        subprocess.Popen(
            [str(executable), "/i", str(download.path), "/norestart"],
            shell=False,
            close_fds=True,
            cwd=str(directory),
        )
        download._handed_off = True
    except UpdateError:
        raise
    except (OSError, ValueError, TypeError):
        raise UpdateError("Windows Installer could not be started. The application will remain open.") from None
