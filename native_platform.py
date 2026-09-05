"""Runtime-selected native integration boundary.

Windows retains its established Win32 implementation. macOS deliberately
exposes only capabilities that can be implemented safely without private
framework calls.
"""
from __future__ import annotations

import sys

if sys.platform == "win32":
    from windows_platform import *  # noqa: F403
elif sys.platform == "darwin":
    from macos_platform import *  # noqa: F403
else:
    raise RuntimeError(f"FenSoundSwitch is unsupported on {sys.platform!r}.")
