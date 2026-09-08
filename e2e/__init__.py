"""e2e — end-to-end negative-control + smoke verification (issue #46).

The capstone E2E gate of the product build (EPIC-00 #4, phase 8): a golden
path from tenant signup to audited + billed multi-provider model calls, wired
over the REAL merged pillar modules (offline), plus negative controls that
prove each guardrail genuinely blocks (no-false-green, issue #28).

Layout:

* ``_paths.py``      sys.path bootstrap (top-level pillar roots + repo-root
                     PEP-420 namespace).
* ``wiring.py``      composition root: builds every real module instance
                     offline and records GuardAttestation evidence.
* ``golden_path.py`` the canonical tenant journey (CLI-runnable).
* ``negative_controls.py`` one runnable check per guard (CLI-runnable).
* ``tests/``         pytest suite (offline) that exercises both runners.
* ``README.md``      the golden-path contract + negative-control evidence.
"""

from __future__ import annotations

from e2e._paths import ensure_sys_paths  # noqa: F401

ensure_sys_paths()
