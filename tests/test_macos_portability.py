from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import macos_platform
import web_presentation
import web_ui_host


@unittest.skipUnless(sys.platform == "darwin", "macOS portability contract")
class MacOSPortabilityTests(unittest.TestCase):
    def test_native_platform_has_safe_unavailable_controllers(self) -> None:
        listener = macos_platform.DisplayChangeListener(lambda: None, lambda _error: None, lambda: None)
        listener.start()
        self.assertTrue(listener.is_active)
        self.assertTrue(listener.stop())
        self.assertFalse(listener.is_active)

        with self.assertRaises(macos_platform.PlatformError):
            macos_platform.GlobalVolumeKeyListener().start()

    def test_presentation_uses_private_unix_socket_address(self) -> None:
        socket_path = Path(tempfile.gettempdir()) / "fensoundswitch" / "test.sock"
        command = web_presentation.build_presentation_child_command(
            "web_ui_host.py", str(socket_path), b"a" * 32, packaged=False
        )
        self.assertEqual(web_presentation.PIPE_FAMILY, "AF_UNIX")
        self.assertIn(web_presentation.AUTHKEY_ENVIRONMENT_VARIABLE, command.environment)
        bootstrap = web_ui_host.parse_bootstrap(
            ["--pipe", str(socket_path)], command.environment
        )
        self.assertEqual(bootstrap.pipe, str(socket_path))
