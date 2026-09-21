"""Allow ``python3 -m isolation`` from the guardrails/isolation directory.

---knowledge---
module_id: guardrails.isolation.__main__
system: guardrails
app: isolation
solution_class: class
patterns: [entrypoint-only]
derives_from: null
owner_sme: platform-sme
tier: L0
interfaces: [python3 -m isolation]
invariants: ""
gotchas: "the entry point is thin by design; the subcommands live in isolation/cli.py"
related: ["#30"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
