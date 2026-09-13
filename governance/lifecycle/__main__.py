"""``python3 -m governance.lifecycle`` — same surface as ``cli.py``."""

from __future__ import annotations

from governance.lifecycle.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
