from __future__ import annotations

import sys


MESSAGE = "This launcher is no longer supported. Run: python app.py"


def main() -> int:
    print(MESSAGE, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
