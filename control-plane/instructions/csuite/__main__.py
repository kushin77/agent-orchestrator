"""``python3 -m csuite`` — derive the C-suite instruction-layer mirrors (#643).

---knowledge---
module_id: control-plane.instructions.csuite.__main__
system: control-plane
app: instructions
solution_class: class
patterns: [entrypoint-only]
derives_from: null
owner_sme: docs-sme
tier: L0
interfaces: [python3 -m csuite]
invariants: "the module's only job is to call csuite.derive._main and exit with its code"
gotchas: ""
related: ["#643"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

from .derive import _main

if __name__ == "__main__":
    raise SystemExit(_main())
