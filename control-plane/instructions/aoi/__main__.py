"""Entry point for ``python3 -m aoi`` (issue #42)."""

from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
