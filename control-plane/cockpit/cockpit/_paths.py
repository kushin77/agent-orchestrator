"""Path bootstrap for the cockpit package (issue #566, RC-11 of #551).

The repository has no dependency manifest or packaging, so every in-tree module
reaches its siblings by placing a directory on ``sys.path``. The cockpit
consumes three of them, and the insertion order matters (first wins):

* ``control-plane/cli`` — the ``aoctl`` package (RC-5, the RC-3 API client the
  cockpit imports rather than re-implementing);
* ``control-plane/functions`` — ``cockpit_registry`` / ``cockpit_render``
  (RC-10); the module names are deliberately unique so the repository's
  top-level ``registry/`` package can never shadow them;
* the repository root — ``portal.*``, ``integrations.*`` (the paperclip seam
  and the console contract modules).

Nothing here imports a package that requires a network or a TTY.


---knowledge---
module_id: control-plane.cockpit.cockpit._paths
system: control-plane
app: cockpit
solution_class: class
patterns: [path-bootstrap, declared-dependency-order]
derives_from: null
owner_sme: frontend-sme
tier: L0
interfaces: [ensure_paths, ROOT, PACKAGE, FUNCTIONS_DIR, CLI_DIR, FIXTURES]
invariants: "the sys.path insertion order is the contract (control-plane/cli, then control-plane/functions, then the repository root) because first wins"
gotchas: "the functions module names are deliberately unique so the repository's top-level registry/ package can never shadow them"
related: ["#566"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import sys
from pathlib import Path

#: control-plane/cockpit/cockpit -> the repository root (three parents up).
ROOT = Path(__file__).resolve().parents[3]
PACKAGE = Path(__file__).resolve().parent
FUNCTIONS_DIR = ROOT / "control-plane" / "functions"
CLI_DIR = ROOT / "control-plane" / "cli"
FIXTURES = PACKAGE / "fixtures"


def ensure_paths() -> None:
    """Make the consumed sibling packages importable, once per process."""
    for entry in (str(CLI_DIR), str(FUNCTIONS_DIR), str(ROOT)):
        if entry not in sys.path:
            sys.path.insert(0, entry)
