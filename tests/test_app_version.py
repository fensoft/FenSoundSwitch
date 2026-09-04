from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import app_version


class AppVersionTests(unittest.TestCase):
    def test_source_tree_reports_dev(self) -> None:
        self.assertEqual(app_version.APP_VERSION, "dev")

    def test_missing_version_resource_defaults_to_dev(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(
                app_version.load_app_version(Path(directory) / "missing.txt"),
                "dev",
            )

    def test_bundled_version_preserves_the_exact_tag(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "version.txt"
            for value in ("2.0", "v2.0", "release-2.0"):
                path.write_text(value, encoding="utf-8")
                with self.subTest(value=value):
                    self.assertEqual(app_version.load_app_version(path), value)

    def test_invalid_version_resource_fails_closed_to_dev(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "version.txt"
            for value in ("", "bad\nversion", "x" * 256):
                path.write_text(value, encoding="utf-8")
                with self.subTest(value=value):
                    self.assertEqual(app_version.load_app_version(path), "dev")


if __name__ == "__main__":
    unittest.main()
