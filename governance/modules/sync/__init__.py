"""governance/modules/sync — live module-registry admission pin probe (issue #889).

---knowledge---
module_id: governance.modules.sync
system: governance
app: modules
solution_class: template
patterns: [no-false-green]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: []
invariants: ""
gotchas: ""
related: ["#889"]
do_not_duplicate: null
---knowledge---

Reads the real, committed admission register (root ``module.json``
``submodules``, via :func:`governance.modules.registry.load_register`) and the
real standards pin (``cmr-pin.yaml``) off disk on every call, and reports each
tracked target's admission state exactly as recorded — never upgrading
``requested``/``independent`` to a membership claim
(``docs/MODULE-REGISTRY.md`` admission-state doctrine). An entry that claims
an admission state beyond the honest, non-membership set is refused BY NAME
(no-false-green).

``governance/`` and ``governance/modules/`` are PEP-420 namespace packages
(no ``__init__.py`` needed on the path, but this package carries one so
``governance.modules.sync`` imports plainly); the repository root goes on
``sys.path`` (mirrors ``governance/modules/tests/conftest.py``).
"""

from __future__ import annotations

from governance.modules.sync.live import (
    AdmissionOverclaim,
    UnknownAdmissionTarget,
    probe,
)

__all__ = ["probe", "AdmissionOverclaim", "UnknownAdmissionTarget"]
