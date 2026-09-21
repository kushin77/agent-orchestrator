"""Health probes — a spec per module, honest about whether it ever ran.

---knowledge---
module_id: governance.modules.health
system: governance
app: modules
solution_class: pattern
patterns: [offline-hermetic, deterministic]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [catalog_spec, pending_spec, well_formed, claims_ok_without_running]
invariants: ""
gotchas: ""
related: []
do_not_duplicate: null
---knowledge---

Every registry entry carries a health probe, and the probe is a **spec** plus a
**status**. The deterministic offline build never runs a probe, and an unrun
probe reports ``not-run`` — never ``ok``. ``--probe`` is the live mode: it runs
``git ls-remote --tags`` for a registered module's pin and records what it
actually found (``ok`` / ``no-tag`` / ``unreachable`` / ``unknown``).

This mirrors the hub's own ``catalog/validate.py::tag_gate`` (a manifest whose
``versions.latest`` has no matching remote tag is a real finding), with one
difference that matters offline: an unverifiable pin is *recorded as
unverified* here, whereas the hub's gate refuses. The gate of record for this
repository must stay honest and runnable without the network, so the refusal
lives with the hub and the honesty lives here.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import Any, Dict, Optional

from governance.modules.model import (
    PROBE_ABSENT,
    PROBE_NOT_RUN,
    PROBE_NO_TAG,
    PROBE_OK,
    PROBE_UNKNOWN,
    PROBE_UNREACHABLE,
)

GIT_TAG = "git-tag"
CATALOG_ENTRY = "catalog-entry"

OFFLINE_REASON = "deterministic offline build: the pin probe runs only with --probe"


def _tags(repo: str, timeout: int = 30) -> Optional[set]:
    """Best-effort ``git ls-remote --tags``; ``None`` means it could not run."""
    if not shutil.which("git"):
        return None
    try:
        proc = subprocess.run(
            ["git", "ls-remote", "--tags", "https://github.com/{}.git".format(repo)],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    tags = set()
    for line in proc.stdout.splitlines():
        if "\t" not in line:
            continue
        ref = line.split("\t", 1)[1]
        if ref.endswith("^{}"):
            ref = ref[:-3]
        if ref.startswith("refs/tags/"):
            tags.add(ref[len("refs/tags/"):])
    return tags


def catalog_spec(repo: Optional[str], pin: Optional[str], live: bool = False) -> Dict[str, Any]:
    """A registered module's probe: does its declared pin exist as a remote tag?"""
    target = "https://github.com/{}.git".format(repo) if repo else None
    spec: Dict[str, Any] = {
        "kind": GIT_TAG,
        "target": target,
        "expect": pin,
        "status": PROBE_NOT_RUN,
        "reason": OFFLINE_REASON,
    }
    if repo is None:
        spec["status"] = PROBE_UNKNOWN
        spec["reason"] = "the manifest declares no source.repo to probe"
        return spec
    if not live:
        return spec
    if pin is None:
        spec["status"] = PROBE_UNKNOWN
        spec["reason"] = "the manifest declares no versions.latest pin to probe"
        return spec
    tags = _tags(repo)
    if tags is None:
        spec["status"] = PROBE_UNREACHABLE
        spec["reason"] = "git ls-remote could not run (offline, or the repo is absent)"
    elif pin in tags:
        spec["status"] = PROBE_OK
        spec["reason"] = "pin {} resolves to a tag on {}".format(pin, repo)
    else:
        spec["status"] = PROBE_NO_TAG
        spec["reason"] = "pin {} has no matching tag on {}".format(pin, repo)
    return spec


def pending_spec(reference_path: str, expect: str = "a catalog entry") -> Dict[str, Any]:
    """An unregistered name's probe: is the catalog entry there yet?"""
    return {
        "kind": CATALOG_ENTRY,
        "target": reference_path,
        "expect": expect,
        "status": PROBE_ABSENT,
        "reason": "the hub catalog carries no such entry at this revision",
    }


def well_formed(spec: Any) -> bool:
    """The shape every entry's probe must have — asserted by the gate."""
    if not isinstance(spec, dict):
        return False
    if spec.get("kind") not in (GIT_TAG, CATALOG_ENTRY):
        return False
    if not isinstance(spec.get("expect", None), (str, type(None))):
        return False
    if not isinstance(spec.get("reason"), str) or not spec["reason"]:
        return False
    return spec.get("status") in (
        PROBE_OK,
        PROBE_NOT_RUN,
        PROBE_UNREACHABLE,
        PROBE_NO_TAG,
        PROBE_ABSENT,
        PROBE_UNKNOWN,
    )


def claims_ok_without_running(spec: Any, live: bool) -> bool:
    """A probe may never report ``ok`` on a build that did not run it."""
    if not isinstance(spec, dict):
        return False
    return spec.get("status") == PROBE_OK and not live
