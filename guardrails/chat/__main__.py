"""``python3 -m chat`` — the chat guard's command-line entry point.

---knowledge---
module_id: guardrails.chat.__main__
system: guardrails
app: chat
solution_class: class
patterns: [entrypoint-only, subcommand-table]
derives_from: null
owner_sme: platform-sme
tier: L0
interfaces: [python3 -m chat]
invariants: ""
gotchas: "the entry point is thin by design; the subcommands and their exit codes live in chat/cli.py"
related: ["#507"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":  # pragma: no cover - exercised by the gate script
    raise SystemExit(main())
