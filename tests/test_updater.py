from __future__ import annotations

import hashlib
from http.client import IncompleteRead
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError
from urllib.request import Request

import updater


PACKAGE = b"verified test MSI bytes; never an executable package"
TAG = "v2.1.3"
ASSET_URL = updater.RELEASE_DOWNLOAD_PREFIX + TAG + "/" + updater.ASSET_NAME
DIGEST = hashlib.sha256(PACKAGE).hexdigest()


class FakeResponse(io.BytesIO):
    def __init__(self, body: bytes, *, url=updater.LATEST_RELEASE_URL, status=200, headers=None):
        super().__init__(body)
        self.url = url
        self.status = status
        self.headers = headers if headers is not None else {"Content-Length": str(len(body))}

    def geturl(self):
        return self.url


def release_document():
    return {
        "tag_name": TAG,
        "draft": False,
        "prerelease": False,
        "assets": [{
            "name": updater.ASSET_NAME,
            "browser_download_url": ASSET_URL,
            "size": len(PACKAGE),
            "digest": "sha256:" + DIGEST,
        }],
    }


def release():
    return updater.ReleaseUpdate(TAG, ASSET_URL, len(PACKAGE), DIGEST)


class UpdaterTests(unittest.TestCase):
    def patch(self, patcher):
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def setUp(self):
        # Every test intercepts both external boundaries, including tests that
        # should reject inputs before either boundary can be reached.
        self.opener = MagicMock()
        self.opener.open.side_effect = AssertionError("Live network is forbidden in updater tests")
        self.build_opener = self.patch(patch.object(updater, "build_opener", return_value=self.opener))
        self.Popen = self.patch(patch.object(updater.subprocess, "Popen", side_effect=AssertionError("Live installer is forbidden in updater tests")))
        self.cancel = threading.Event()
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        real_mkdtemp = tempfile.mkdtemp
        self.patch(patch.object(updater.tempfile, "mkdtemp", side_effect=lambda **kwargs: real_mkdtemp(dir=self.root, **kwargs)))

    def serve(self, body: bytes, **kwargs):
        response = FakeResponse(body, **kwargs)
        self.opener.open.side_effect = None
        self.opener.open.return_value = response
        return response

    def serve_document(self, document):
        return self.serve(json.dumps(document).encode("utf-8"))

    def download(self):
        self.serve(PACKAGE, url=ASSET_URL)
        return updater.download_update(release(), self.cancel)

    def test_updates_only_supported_for_versioned_packaged_windows(self):
        with patch.object(updater.sys, "platform", "win32"):
            for value in ("1.0", "v1.0", "1.0.1", "v255.255.65535", "0.0.0"):
                with self.subTest(value=value):
                    self.assertTrue(updater.is_update_supported(value, packaged=True))
                    self.assertFalse(updater.is_update_supported(value, packaged=False))
            for value in ("dev", "", "1", "1.2.3.4", "1.2-beta", "1.2.3+build", "256.0", "0.256", "0.0.65536", "1.2\n", "v١.٢", None):
                with self.subTest(value=value):
                    self.assertFalse(updater.is_update_supported(value, packaged=True))
        with patch.object(updater.sys, "platform", "linux"):
            self.assertFalse(updater.is_update_supported("1.0", packaged=True))

    def test_nuitka_and_frozen_packaging_detection(self):
        with patch.object(updater.sys, "platform", "win32"), patch.object(updater.sys, "frozen", False, create=True):
            with patch.dict(updater.__dict__, {"__compiled__": object()}):
                self.assertTrue(updater.is_update_supported("1.0"))
            with patch.object(updater.sys, "frozen", True, create=True):
                self.assertTrue(updater.is_update_supported("1.0"))

    def test_check_returns_only_newer_official_installer_and_never_downloads_it(self):
        response = self.serve_document(release_document())
        self.assertEqual(updater.check_for_update("2.1.2", self.cancel), release())
        self.assertTrue(response.closed)
        self.opener.open.assert_called_once()
        request = self.opener.open.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.github.com/repos/fensoft/FenSoundSwitch/releases/latest")
        self.assertEqual(request.get_header("Accept"), "application/vnd.github+json")
        self.assertEqual(request.get_header("Accept-encoding"), "identity")
        self.assertLessEqual(self.opener.open.call_args.kwargs["timeout"], updater.SOCKET_TIMEOUT_SECONDS)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_versions_compare_numerically_with_optional_v_and_patch(self):
        document = release_document()
        for current, tag, expected in (("2.9", "2.10", True), ("2.1.3", TAG, False), ("2.1.4", TAG, False), ("2.1.0", "v2.1", False)):
            with self.subTest(current=current, tag=tag):
                document["tag_name"] = tag
                document["assets"][0]["browser_download_url"] = updater.RELEASE_DOWNLOAD_PREFIX + tag + "/" + updater.ASSET_NAME
                self.serve_document(document)
                self.assertEqual(updater.check_for_update(current, self.cancel) is not None, expected)

    def test_invalid_current_version_does_not_contact_network(self):
        self.assertIsNone(updater.check_for_update("dev", self.cancel))
        self.build_opener.assert_not_called()

    def test_draft_prerelease_and_invalid_stable_tags_are_rejected(self):
        for field, value in (("draft", True), ("prerelease", True), ("draft", 0), ("tag_name", "v2.2-rc1"), ("tag_name", "256.1")):
            with self.subTest(field=field, value=value):
                document = release_document()
                document[field] = value
                self.serve_document(document)
                with self.assertRaises(updater.UpdateError):
                    updater.check_for_update("1.0", self.cancel)

    def test_missing_duplicate_or_untrusted_installer_assets_are_rejected(self):
        for change in ("missing", "duplicate", "wrong_repository", "wrong_tag", "query", "http", "no_digest", "bad_digest", "bool_size", "zero_size", "oversize"):
            with self.subTest(change=change):
                document = release_document()
                asset = document["assets"][0]
                if change == "missing":
                    document["assets"] = []
                elif change == "duplicate":
                    document["assets"].append(dict(asset))
                elif change == "wrong_repository":
                    asset["browser_download_url"] = ASSET_URL.replace("fensoft", "someone")
                elif change == "wrong_tag":
                    asset["browser_download_url"] = ASSET_URL.replace(TAG, "v99.0")
                elif change == "query":
                    asset["browser_download_url"] += "?other=1"
                elif change == "http":
                    asset["browser_download_url"] = ASSET_URL.replace("https", "http")
                elif change == "no_digest":
                    asset["digest"] = None
                elif change == "bad_digest":
                    asset["digest"] = "sha256:" + "a" * 63
                elif change == "bool_size":
                    asset["size"] = True
                elif change == "zero_size":
                    asset["size"] = 0
                else:
                    asset["size"] = updater.MAX_INSTALLER_BYTES + 1
                self.serve_document(document)
                with self.assertRaises(updater.UpdateError):
                    updater.check_for_update("1.0", self.cancel)

    def test_malformed_json_nonobject_and_broken_streams_are_reported(self):
        for data in (b"not json", b"[]", b"null", b"\xff"):
            with self.subTest(data=data):
                self.serve(data)
                with self.assertRaises(updater.UpdateError):
                    updater.check_for_update("1.0", self.cancel)
        response = self.serve(b"{}")
        response.read1 = MagicMock(side_effect=IncompleteRead(b"x"))
        with self.assertRaises(updater.UpdateError):
            updater.check_for_update("1.0", self.cancel)
        self.assertTrue(response.closed)

    def test_api_size_and_content_length_are_bounded(self):
        for body, headers in ((b"x" * (updater.MAX_API_BYTES + 1), {}), (b"{}", {"Content-Length": str(updater.MAX_API_BYTES + 1)}), (b"{}", {"Content-Length": "-1"}), (b"{}", {"Content-Length": "3"})):
            with self.subTest(headers=headers):
                response = self.serve(body, headers=headers)
                with self.assertRaises(updater.UpdateError):
                    updater.check_for_update("1.0", self.cancel)
                self.assertTrue(response.closed)

    def test_untrusted_final_url_partial_status_and_compression_are_rejected(self):
        for options in ({"url": "https://evil.example/latest"}, {"status": 206}, {"headers": {"Content-Encoding": "gzip"}}):
            with self.subTest(options=options):
                response = self.serve(b"{}", **options)
                with self.assertRaises(updater.UpdateError):
                    updater.check_for_update("1.0", self.cancel)
                self.assertTrue(response.closed)

    def test_missing_latest_release_is_benign_and_network_errors_are_sanitized(self):
        self.opener.open.side_effect = HTTPError(updater.LATEST_RELEASE_URL, 404, "Not Found", {}, None)
        self.assertIsNone(updater.check_for_update("1.0", self.cancel))
        for error in (HTTPError(updater.LATEST_RELEASE_URL, 403, "private token", {}, None), URLError("private token"), TimeoutError("private token")):
            with self.subTest(error=error):
                self.opener.open.side_effect = error
                with self.assertRaises(updater.UpdateError) as raised:
                    updater.check_for_update("1.0", self.cancel)
                self.assertNotIn("private token", str(raised.exception))

    def test_check_cancellation_and_deadline_close_response(self):
        self.cancel.set()
        with self.assertRaisesRegex(updater.UpdateError, "cancelled"):
            updater.check_for_update("1.0", self.cancel)
        self.build_opener.assert_not_called()
        self.cancel.clear()
        response = self.serve_document(release_document())
        with patch.object(updater.time, "monotonic", side_effect=[0, 0, 0, 0, 31]):
            with self.assertRaisesRegex(updater.UpdateError, "timed out"):
                updater.check_for_update("1.0", self.cancel)
        self.assertTrue(response.closed)

    def test_download_verifies_size_and_digest_in_owned_unique_directory(self):
        download = self.download()
        self.assertEqual(download.path.read_bytes(), PACKAGE)
        self.assertEqual(download.path.name, updater.ASSET_NAME)
        self.assertEqual(download.path.parent, download.directory)
        self.assertTrue(download.directory.name.startswith(updater.TEMP_DIRECTORY_PREFIX))
        self.assertFalse(download.path.with_suffix(".msi.part").exists())
        self.assertFalse(download._handed_off)
        self.Popen.assert_not_called()
        download.cleanup()
        download.cleanup()
        self.assertFalse(download.directory.exists())

    def test_download_accepts_only_https_github_release_assets_redirect_target(self):
        response = self.serve(PACKAGE, url="https://release-assets.githubusercontent.com/github-production-release-asset/123/file?sig=test")
        download = updater.download_update(release(), self.cancel)
        self.assertTrue(response.closed)
        download.cleanup()
        handler = self.build_opener.call_args.args[0]
        self.assertIsInstance(handler, updater._RestrictedRedirectHandler)
        self.assertTrue(handler.download)

    def test_download_wrong_hash_size_and_response_length_cleanup_all_owned_files(self):
        cases = ((PACKAGE[:-1], {}), (PACKAGE + b"x", {}), (b"x" * len(PACKAGE), {}), (PACKAGE, {"Content-Length": "1"}))
        for body, headers in cases:
            with self.subTest(body=body, headers=headers):
                response = self.serve(body, url=ASSET_URL, headers=headers)
                with self.assertRaises(updater.UpdateError):
                    updater.download_update(release(), self.cancel)
                self.assertTrue(response.closed)
                self.assertEqual(list(self.root.iterdir()), [])

    def test_download_cancellation_and_deadline_remove_partial_file(self):
        response = self.serve(PACKAGE, url=ASSET_URL)
        original_read = response.read1

        def cancel_after_read(count):
            value = original_read(count)
            self.cancel.set()
            return value

        response.read1 = cancel_after_read
        with self.assertRaisesRegex(updater.UpdateError, "cancelled"):
            updater.download_update(release(), self.cancel)
        self.assertTrue(response.closed)
        self.assertEqual(list(self.root.iterdir()), [])
        self.cancel.clear()
        response = self.serve(PACKAGE, url=ASSET_URL)
        with patch.object(updater.time, "monotonic", side_effect=[0, 0, 0, 0, 0, updater.DOWNLOAD_TIMEOUT_SECONDS + 1]):
            with self.assertRaisesRegex(updater.UpdateError, "timed out"):
                updater.download_update(release(), self.cancel)
        self.assertTrue(response.closed)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_download_network_and_disk_errors_cleanup_and_are_sanitized(self):
        self.opener.open.side_effect = URLError("private signed download URL")
        with self.assertRaises(updater.UpdateError) as raised:
            updater.download_update(release(), self.cancel)
        self.assertNotIn("private", str(raised.exception))
        self.assertEqual(list(self.root.iterdir()), [])
        self.serve(PACKAGE, url=ASSET_URL)
        with patch.object(Path, "replace", side_effect=OSError("disk failure")):
            with self.assertRaises(updater.UpdateError):
                updater.download_update(release(), self.cancel)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_invalid_download_metadata_is_rejected_before_network_or_temp_files(self):
        for candidate in (updater.ReleaseUpdate("2.0-rc1", ASSET_URL, len(PACKAGE), DIGEST), updater.ReleaseUpdate(TAG, "https://evil.example/installer.msi", len(PACKAGE), DIGEST), updater.ReleaseUpdate(TAG, ASSET_URL, True, DIGEST), updater.ReleaseUpdate(TAG, ASSET_URL, len(PACKAGE), "bad")):
            with self.subTest(candidate=candidate):
                with self.assertRaises(updater.UpdateError):
                    updater.download_update(candidate, self.cancel)
        self.build_opener.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_cleanup_does_not_remove_unowned_or_unexpected_files(self):
        unrelated = self.root / updater.ASSET_NAME
        unrelated.write_bytes(b"not ours")
        updater.DownloadedUpdate(release(), unrelated, self.root).cleanup()
        self.assertTrue(unrelated.exists())
        download = self.download()
        unexpected = download.directory / "unrelated.txt"
        unexpected.write_text("keep me", encoding="utf-8")
        download.cleanup()
        self.assertFalse(download.path.exists())
        self.assertEqual(unexpected.read_text(encoding="utf-8"), "keep me")
        self.assertTrue(unrelated.exists())

    def test_cleanup_ignores_mutated_public_paths_and_filesystem_failures(self):
        download = self.download()
        original_path = download.path
        unrelated = self.root / "unrelated.msi"
        unrelated.write_bytes(b"keep")
        download.path = unrelated
        download.directory = self.root
        download.cleanup()
        self.assertFalse(original_path.exists())
        self.assertTrue(unrelated.exists())
        download = self.download()
        with patch.object(Path, "unlink", side_effect=OSError("in use")):
            download.cleanup()
        self.assertTrue(download.path.exists())
        download.cleanup()

    def test_cleanup_does_not_touch_a_replacement_directory(self):
        download = self.download()
        moved = self.root / "original-download"
        download.directory.rename(moved)
        download.directory.mkdir()
        replacement = download.directory / updater.ASSET_NAME
        replacement.write_bytes(b"someone else's file")
        download.cleanup()
        self.assertEqual(replacement.read_bytes(), b"someone else's file")
        self.assertTrue((moved / updater.ASSET_NAME).exists())

    def test_launcher_uses_absolute_system_installer_and_visible_ui_without_shell(self):
        download = self.download()
        installer = self.root / "System32" / "msiexec.exe"
        self.Popen.side_effect = None
        with patch.object(updater.sys, "platform", "win32"), patch.object(updater, "_windows_installer_path", return_value=installer):
            updater.launch_installer(download)
        self.Popen.assert_called_once_with([str(installer), "/i", str(download.path), "/norestart"], shell=False, close_fds=True, cwd=str(download.directory))
        self.assertTrue(download._handed_off)
        download.cleanup()
        self.assertTrue(download.path.exists(), "MSI must survive app shutdown after handoff")
        with patch.object(updater.sys, "platform", "win32"):
            with self.assertRaises(updater.UpdateError):
                updater.launch_installer(download)
        self.assertEqual(self.Popen.call_count, 1)

    def test_launcher_failure_preserves_application_and_allows_cleanup(self):
        download = self.download()
        self.Popen.side_effect = OSError("process creation failed")
        with patch.object(updater.sys, "platform", "win32"), patch.object(updater, "_windows_installer_path", return_value=self.root / "msiexec.exe"):
            with self.assertRaisesRegex(updater.UpdateError, "remain open"):
                updater.launch_installer(download)
        self.assertFalse(download._handed_off)
        download.cleanup()
        self.assertFalse(download.directory.exists())

    def test_launcher_refuses_unsupported_platform_unowned_or_changed_download(self):
        download = self.download()
        with patch.object(updater.sys, "platform", "linux"):
            with self.assertRaises(updater.UpdateError):
                updater.launch_installer(download)
        with patch.object(updater.sys, "platform", "win32"):
            with self.assertRaises(updater.UpdateError):
                updater.launch_installer(updater.DownloadedUpdate(release(), download.path, download.directory))
            download.path.write_bytes(b"changed size")
            with self.assertRaises(updater.UpdateError):
                updater.launch_installer(download)
        self.Popen.assert_not_called()
        download.cleanup()

    def test_launcher_rejects_changed_release_or_relocated_path(self):
        download = self.download()
        original_path = download.path
        with patch.object(updater.sys, "platform", "win32"):
            download.path = self.root / updater.ASSET_NAME
            with self.assertRaises(updater.UpdateError):
                updater.launch_installer(download)
            download.path = original_path
            download.release = updater.ReleaseUpdate(TAG, ASSET_URL, len(PACKAGE), "0" * 64)
            with self.assertRaises(updater.UpdateError):
                updater.launch_installer(download)
        self.Popen.assert_not_called()
        download.cleanup()

    def test_system_installer_path_comes_from_win32_not_environment(self):
        directory = self.root / "System32"
        directory.mkdir()
        (directory / "msiexec.exe").touch()
        kernel32 = MagicMock()

        def get_directory(buffer, size):
            buffer.value = str(directory)
            return len(buffer.value)

        kernel32.GetSystemDirectoryW.side_effect = get_directory
        with patch.object(updater.ctypes, "WinDLL", return_value=kernel32, create=True), patch.dict(os.environ, {"SystemRoot": "Z:/untrusted", "PATH": "Z:/untrusted"}):
            self.assertEqual(updater._windows_installer_path(), directory / "msiexec.exe")
        kernel32.GetSystemDirectoryW.assert_called_once()

    def test_system_directory_resolution_fails_closed(self):
        kernel32 = MagicMock()
        kernel32.GetSystemDirectoryW.return_value = 0
        with patch.object(updater.ctypes, "WinDLL", return_value=kernel32, create=True):
            with self.assertRaises(updater.UpdateError):
                updater._windows_installer_path()


class RedirectTests(unittest.TestCase):
    def test_only_official_asset_https_redirects_are_allowed(self):
        handler = updater._RestrictedRedirectHandler(ASSET_URL, download=True)
        request = Request(ASSET_URL)
        allowed = "https://release-assets.githubusercontent.com/path?sig=value"
        redirected = handler.redirect_request(request, None, 302, "Found", {}, allowed)
        self.assertEqual(redirected.full_url, allowed)
        for url in ("http://release-assets.githubusercontent.com/path", "https://evil.example/package", "https://release-assets.githubusercontent.com.evil.example/path", "https://evil@release-assets.githubusercontent.com/path", "https://release-assets.githubusercontent.com:444/path", "https://release-assets.githubusercontent.com/path#fragment", "https://release-assets.githubusercontent.com/\npath", "https://github.com/someone/other/releases/download/v1/a.msi", "file:///C:/Temp/package.msi"):
            with self.subTest(url=url):
                with self.assertRaises(updater.UpdateError):
                    handler.redirect_request(request, None, 302, "Found", {}, url)

    def test_release_api_does_not_follow_redirects(self):
        handler = updater._RestrictedRedirectHandler(updater.LATEST_RELEASE_URL, download=False)
        with self.assertRaises(updater.UpdateError):
            handler.redirect_request(Request(updater.LATEST_RELEASE_URL), None, 302, "Found", {}, updater.LATEST_RELEASE_URL)


if __name__ == "__main__":
    unittest.main()
