"""The hermes projection's consistency audit (issue #942).

The projection is read from four sources that MUST agree: the persona card, the
profile seed, the FinOps tier table and the gateway catalog row. A projection
that re-states a drift across those sources would silently become a second,
wrong authority — the half-coupling ADR-0012 forbids. This module is the audit
half of the boundary: it walks a built projection and records one journal entry
per cross-source invariant, each with a status (``held`` / ``violated``), a
human detail, and the sha256 of the evidence that decided it, so the gate can
prove the invariants were exercised rather than assumed.

The invariants are closed, and each names the drifted field:

* **capability-set parity** — the persona card's and profile seed's
  ``capabilitySet`` agree;
* **tier parity** — the persona card's and profile seed's ``defaultModelTier``
  agree;
* **floor presence** — the tiering floor is present and inside the closed tier
  vocabulary;
* **tier coverage** — every persona capability has a FinOps ``taskClasses``
  mapping.

Stdlib-only by construction.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Dict, List


@dataclass(frozen=True)
class AuditEntry:
    """One invariant's verdict: what was checked, whether it held, the evidence."""

    invariant: str
    status: str  # "held" | "violated"
    detail: str
    evidence_sha: str


def _sha(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def audit_projection(projection: Dict[str, Any]) -> List[AuditEntry]:
    """Record the cross-source invariants of a built projection."""
    entries: List[AuditEntry] = []

    persona_caps = sorted(set(projection["persona"]["capabilities"]))
    profile_caps = sorted(set(projection["profile"]["capabilities"]))

    # 1. capability-set parity
    parity_ok = persona_caps == profile_caps
    entries.append(
        AuditEntry(
            invariant="capability-set parity",
            status="held" if parity_ok else "violated",
            detail="persona=%s profile=%s" % (persona_caps, profile_caps),
            evidence_sha=_sha(json.dumps(persona_caps) + json.dumps(profile_caps)),
        )
    )

    # 2. tier parity
    persona_tier = str(projection["persona"]["tier"])
    profile_tier = str(projection["profile"]["default_model_tier"])
    tier_ok = persona_tier == profile_tier
    entries.append(
        AuditEntry(
            invariant="tier parity",
            status="held" if tier_ok else "violated",
            detail="persona=%s profile=%s" % (persona_tier, profile_tier),
            evidence_sha=_sha(persona_tier + profile_tier),
        )
    )

    # 3. floor presence (policy.py's output, held to the closed vocabulary)
    floor = str(projection["tiering"]["floor_tier"])
    floor_ok = floor in ("L0", "L1", "L2")
    entries.append(
        AuditEntry(
            invariant="floor presence",
            status="held" if floor_ok else "violated",
            detail="floor_tier=%r" % floor,
            evidence_sha=_sha(floor),
        )
    )

    # 4. tier coverage (one entry per persona capability)
    for cap in persona_caps:
        entry = projection["tiering"]["capabilities"].get(cap) or {}
        covered = bool(entry.get("default_tier"))
        entries.append(
            AuditEntry(
                invariant="tier coverage",
                status="held" if covered else "violated",
                detail=cap,
                evidence_sha=_sha(cap),
            )
        )

    return entries


def violation_findings(entries: List[AuditEntry]) -> List[str]:
    """The gate-facing findings, naming the drifted field, for violated invariants."""
    findings: List[str] = []
    for entry in entries:
        if entry.status != "violated":
            continue
        if entry.invariant == "capability-set parity":
            findings.append("capabilitySet drift: %s" % entry.detail)
        elif entry.invariant == "tier parity":
            findings.append("tier drift: %s" % entry.detail)
        elif entry.invariant == "floor presence":
            findings.append("tiering floor drift: %s" % entry.detail)
        elif entry.invariant == "tier coverage":
            findings.append("capability %r has no FinOps tier mapping" % entry.detail)
        else:  # pragma: no cover - the vocabulary above is closed
            findings.append("%s drift: %s" % (entry.invariant, entry.detail))
    return findings
