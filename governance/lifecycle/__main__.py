"""``python3 -m governance.lifecycle`` — same surface as ``cli.py``.

---knowledge---
module_id: governance.lifecycle.main
system: governance
app: lifecycle
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

from governance.lifecycle.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
