"""Allow ``python3 -m isolation`` from the guardrails/isolation directory."""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
