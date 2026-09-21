"""Entry point for ``python3 -m aoi`` (issue #42).

---knowledge---
module_id: control-plane.instructions.aoi.__main__
system: control-plane
app: instructions
solution_class: class
patterns: [entrypoint-only]
derives_from: null
owner_sme: docs-sme
tier: L0
interfaces: [python3 -m aoi]
invariants: "the module's only job is to call aoi.cli.main and exit with its code"
gotchas: ""
related: ["#42"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
