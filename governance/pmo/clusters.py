"""Load + validate ``governance/pmo/clusters.json`` (``dispatch --by-cluster``).

---knowledge---
module_id: governance.pmo.clusters
system: governance
app: pmo
solution_class: pattern
patterns: [read-only-optional-input, schema-validated-loader]
derives_from: governance/pmo/clusters.schema.json
owner_sme: pmo-sme
tier: L1
interfaces: [Cluster, Clusters, load]
invariants: "PMO never writes clusters.json; it is a read-only, optional input"
gotchas: ""
related: ["#1575"]
do_not_duplicate: null
---knowledge---

PMO adds no ledger of its own (``governance/pmo/README.md``'s own rule). This
file is a **read-only, optional input** — produced by the ``board-triage``
lane (issue/PR #1575, ``governance/pmo/clusters.schema.json`` — the schema is
that lane's, adopted verbatim here rather than forked, so the two PRs land on
the same contract for the same path) so a wave can dispatch ONE agent per
cluster and batch several similar issues in tandem (same RCA/outage family,
same recipe). The PMO never writes it and never invents its contents: when the
file is absent, ``dispatch --by-cluster`` degrades to the per-issue plan
unchanged (:mod:`dispatch`'s existing behaviour); when it is present but fails
:data:`SCHEMA <governance/pmo/clusters.schema.json>`, that is CANNOT-ASSESS
(rc 2) — an input that claims to be a cluster proposal but is not shaped like
one is never silently accepted or silently dropped.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft7Validator

from graph import CannotAssess

CLUSTERS_RELPATH = "governance/pmo/clusters.json"
SCHEMA_RELPATH = "governance/pmo/clusters.schema.json"


@dataclass(frozen=True)
class Cluster:
    id: str
    family: str
    title: str
    recipe: str
    sme: str
    tier: str
    batchable: bool
    wave: int
    priority_rank: int
    evidence: str
    issues: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class Clusters:
    generated_at: str
    source_repos: tuple[str, ...]
    clusters: tuple[Cluster, ...]
    unclustered: tuple[dict[str, Any], ...]
    hygiene: dict[str, Any]


def load(root: Path | str = ".") -> Clusters | None:
    """``None`` when no ``clusters.json`` is committed — a normal, expected state.

    Raises :class:`CannotAssess` when the file exists but does not validate
    against ``clusters.schema.json`` — never silently accepted, never silently
    dropped.
    """
    root = Path(root)
    path = root / CLUSTERS_RELPATH
    if not path.is_file():
        return None

    schema_path = root / SCHEMA_RELPATH
    if not schema_path.is_file():
        schema_path = Path(__file__).resolve().parent / "clusters.schema.json"
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CannotAssess(f"clusters schema unreadable: {SCHEMA_RELPATH} ({exc})") from exc

    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CannotAssess(f"{CLUSTERS_RELPATH} is unreadable/malformed JSON: {exc}") from exc

    validator = Draft7Validator(schema)
    errors = sorted(validator.iter_errors(document), key=lambda e: list(e.absolute_path))
    if errors:
        rendered = "; ".join(f"{'/'.join(str(p) for p in e.absolute_path)}: {e.message}" for e in errors[:5])
        raise CannotAssess(f"{CLUSTERS_RELPATH} fails schema {SCHEMA_RELPATH}: {rendered}")

    cluster_ids = [entry["id"] for entry in document["clusters"]]
    if len(cluster_ids) != len(set(cluster_ids)):
        raise CannotAssess(f"{CLUSTERS_RELPATH} declares the same cluster id twice")

    clusters = tuple(
        Cluster(
            id=entry["id"],
            family=entry["family"],
            title=entry["title"],
            recipe=entry["recipe"],
            sme=entry["sme"],
            tier=entry["tier"],
            batchable=bool(entry["batchable"]),
            wave=int(entry["wave"]),
            priority_rank=int(entry["priority_rank"]),
            evidence=entry["evidence"],
            issues=tuple(entry["issues"]),
        )
        for entry in document["clusters"]
    )
    return Clusters(
        generated_at=document["generated_at"],
        source_repos=tuple(document.get("source_repos") or ()),
        clusters=clusters,
        unclustered=tuple(document["unclustered"]),
        hygiene=dict(document.get("hygiene") or {}),
    )
