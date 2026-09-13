"""``python3 -m governance.reconcile`` — same surface as ``cli.py``."""

from __future__ import annotations

from governance.reconcile.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
