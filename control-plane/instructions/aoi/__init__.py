"""agent-orchestrator model-agnostic instruction layer (issue #42).

Control-plane product: ONE canonical instruction source, per-tool mirrors
generated (AGENTS.md / CLAUDE.md / .cursorrules / copilot-instructions.md —
never hand-forked), a frozen tenant-override contract, versioned standards
distribution with a per-consumer drift check, and a model-agnostic conformance
suite (same rules + precedence in every mirror, so the same task yields the
same behaviour under any harness).

Owner lane: ``control-plane/instructions/**`` (issue
``kushin77/agent-orchestrator#42``, work item 38, phase 7).  Parent:
EPIC-00 (issue #4).  Everything here is offline by construction: Python
standard library + PyYAML only, no network, no third-party templating.


---knowledge---
module_id: control-plane.instructions.aoi
system: control-plane
app: instructions
solution_class: class
patterns: [package-contract, public-surface, offline-by-construction]
derives_from: null
owner_sme: docs-sme
tier: L0
interfaces: [aoi.conformance, aoi.model, aoi.override, aoi.render, aoi.versioning]
invariants: "one canonical instruction source is rendered into per-tool mirrors that are generated and never hand-forked; everything here is offline, Python standard library plus PyYAML only"
gotchas: "the package carries the model-agnostic proof that the same ordered rules and precedence reach every harness"
related: ["#42"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

__version__ = "1.0.0"

from .conformance import conformance_check
from .model import load_canonical
from .override import (
    apply_local_rules,
    check_override_applies,
    validate_override,
)
from .render import (
    MIRROR_TARGETS,
    distribution_manifest,
    render_all,
)
from .versioning import check_drift, parse_semver

__all__ = [
    "MIRROR_TARGETS",
    "apply_local_rules",
    "check_drift",
    "check_override_applies",
    "conformance_check",
    "distribution_manifest",
    "load_canonical",
    "parse_semver",
    "render_all",
    "validate_override",
]
