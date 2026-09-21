"""Allow ``python3 -m ledger`` from a directory where ``telemetry/`` is on the

---knowledge---
module_id: telemetry.ledger.__main__
system: telemetry
app: ledger
solution_class: template
patterns: [thin-entrypoint, no-drift]
derives_from: telemetry/ledger/cli.py
owner_sme: security-sme
tier: L0
interfaces: [main]
invariants: "it mirrors cli.main so the two entry points cannot drift"
gotchas: ""
related: ["#31", "#1510"]
do_not_duplicate: telemetry/ledger/cli.py
---knowledge---

path (issue #31). Mirrors ``cli.main`` so the two entry points cannot drift."""

from .cli import main

if __name__ == "__main__":  # pragma: no cover - entry point
    raise SystemExit(main())
