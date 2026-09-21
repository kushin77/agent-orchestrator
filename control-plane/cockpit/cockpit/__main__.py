"""``python3 -m cockpit`` — the terminal cockpit's entry point (issue #566).

---knowledge---
module_id: control-plane.cockpit.cockpit.__main__
system: control-plane
app: cockpit
solution_class: class
patterns: [entrypoint-only, sys-path-bootstrap]
derives_from: null
owner_sme: frontend-sme
tier: L0
interfaces: [python3 -m cockpit]
invariants: "the module's only job is to place the package's parent directory on sys.path and call cockpit.cli.main"
gotchas: ""
related: ["#566"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import sys
from pathlib import Path

_PACKAGE = Path(__file__).resolve().parent.parent
if str(_PACKAGE) not in sys.path:
    sys.path.insert(0, str(_PACKAGE))

from cockpit.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
