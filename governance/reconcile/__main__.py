"""``python3 -m governance.reconcile`` — same surface as ``cli.py``.

---knowledge---
module_id: governance.reconcile.main
system: governance
app: reconcile
solution_class: template
patterns: []
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: []
invariants: ""
gotchas: ""
related: []
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

from governance.reconcile.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
