"""Allow ``python3 -m honesty`` from the guardrails/honesty directory.

---knowledge---
module_id: guardrails.honesty.__main__
system: guardrails
app: honesty
solution_class: class
patterns: [entrypoint-only]
derives_from: null
owner_sme: qa-sme
tier: L0
interfaces: [python3 -m honesty]
invariants: ""
gotchas: "the entry point is thin by design; the subcommands live in honesty/cli.py"
related: ["#28"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
