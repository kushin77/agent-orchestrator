"""Live module-registry admission pin probe (issue #889, lane L10).

---knowledge---
module_id: governance.modules.sync.live
system: governance
app: modules
solution_class: class
patterns: [no-false-green]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [AdmissionOverclaim, UnknownAdmissionTarget, probe]
invariants: ""
gotchas: ""
related: ["#889"]
do_not_duplicate: null
---knowledge---

Reads two real, committed stores on every call:

* the **admission register** — root ``module.json`` ``submodules``, via
  :func:`governance.modules.registry.load_register` (the same reader
  ``registry.build`` uses — never a re-implemented parser);
* the **standards pin** — ``cmr-pin.yaml`` (``bundle_ref`` / ``delivered_at``).

and reports each tracked target's admission state EXACTLY as recorded.
``docs/MODULE-REGISTRY.md`` is explicit that an admission register entry is a
*claim*, never membership — this probe never upgrades ``requested`` /
``independent`` / ``undecided`` to a membership claim. An entry whose
admission value falls outside that honest, non-membership vocabulary is
refused BY NAME (:class:`AdmissionOverclaim`) rather than silently reported —
no-false-green.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Tuple

import yaml

from governance.modules import registry

#: The three targets this lane's brief names honestly (issue #889).
TRACKED_TARGETS: Tuple[str, ...] = ("hermes-agents", "paperclip", "ollama")

#: Admission values that do NOT confer membership (docs/MODULE-REGISTRY.md).
#: Anything outside this set is an overclaim this probe refuses.
NON_MEMBERSHIP_ADMISSIONS = frozenset({"requested", "independent", "undecided", None})

CMR_PIN_FILE = "cmr-pin.yaml"


class AdmissionOverclaim(Exception):
    """Raised when a register entry claims admission beyond the honest set."""

    def __init__(self, module_id: str, admission: Any):
        self.module_id = module_id
        self.admission = admission
        super().__init__(
            f"admission overclaim: {module_id!r} declares admission={admission!r}, "
            f"outside the honest non-membership vocabulary "
            f"{sorted(a for a in NON_MEMBERSHIP_ADMISSIONS if a)}"
        )


class UnknownAdmissionTarget(Exception):
    """Raised when a tracked target has no entry in the admission register."""

    def __init__(self, module_id: str):
        self.module_id = module_id
        super().__init__(
            f"unknown admission target: {module_id!r} has no entry in the "
            f"module.json submodules register"
        )


def _read_pin(repo_root: Path) -> Dict[str, Any]:
    pin_path = repo_root / CMR_PIN_FILE
    if not pin_path.is_file():
        return {}
    with open(pin_path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def probe(
    repo_root: Path | str,
    targets: Tuple[str, ...] = TRACKED_TARGETS,
) -> Dict[str, Any]:
    """Live pin probe: register admission state for every tracked target.

    Raises :class:`UnknownAdmissionTarget` when a tracked id has no register
    entry, and :class:`AdmissionOverclaim` when an entry's admission value
    claims more than the honest non-membership vocabulary — both named, never
    silently reported.
    """
    root = Path(repo_root)
    register, source = registry.load_register(root)
    pin = _read_pin(root)

    results: Dict[str, Any] = {}
    for module_id in targets:
        entry = register.get(module_id)
        if entry is None:
            raise UnknownAdmissionTarget(module_id)
        admission = entry.get("admission")
        if admission not in NON_MEMBERSHIP_ADMISSIONS:
            raise AdmissionOverclaim(module_id, admission)
        results[module_id] = {
            "admission": admission,
            "request": entry.get("request"),
            "membership": "refused",
        }

    return {
        "schema": "ao.governance.modules.sync/admission-pin-v1",
        "register_source": source,
        "pin_bundle_ref": pin.get("bundle_ref"),
        "pin_delivered_at": pin.get("delivered_at"),
        "targets": results,
    }
