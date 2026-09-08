"""Allow ``python3 -m ledger`` from a directory where ``telemetry/`` is on the
path (issue #31). Mirrors ``cli.main`` so the two entry points cannot drift."""

from .cli import main

if __name__ == "__main__":  # pragma: no cover - entry point
    raise SystemExit(main())
