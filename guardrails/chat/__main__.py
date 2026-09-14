"""``python3 -m chat`` — the chat guard's command-line entry point."""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":  # pragma: no cover - exercised by the gate script
    raise SystemExit(main())
