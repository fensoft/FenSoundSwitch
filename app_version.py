from __future__ import annotations

from pathlib import Path


VERSION_FILE = Path(__file__).resolve().with_name("fensoundswitch-version.txt")
DEFAULT_VERSION = "dev"


def load_app_version(path: Path = VERSION_FILE) -> str:
    try:
        version = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return DEFAULT_VERSION
    if not version or len(version) > 255 or any(ord(character) < 32 for character in version):
        return DEFAULT_VERSION
    return version


APP_VERSION = load_app_version()
