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
