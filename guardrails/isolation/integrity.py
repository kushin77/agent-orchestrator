"""Data-integrity detector + runtime per-tenant probes (issue #30, AC1).

Two complementary "integrity scans run per tenant on cadence; findings
reported with evidence":

1. :func:`scan_dataset` — static integrity scan of a tenant-scoped dataset
   (the capital-underwriting account-isolation shape): orphaned records
   (tenant namespace does not exist), records piled up in the reserved
   fallback tenant, the same record id under more than one tenant (ambiguous
   ownership), and denormalization violations (a record's own ``tenant_id``
   disagrees with the bucket it lives in).

2. :func:`run_cadence_probes` — runtime cross-tenant read/write probes
   executed per tenant: for each tenant the runner attempts to read/delete a
   foreign tenant's row and to write a record whose denormalized tenant does
   not match the bucket.  Every attempt must fail closed (``None``, no-op or
   :class:`IsolationScopeError`); any probe that does *not* fail closed is a
   ``PROBE_FAILURE`` finding with evidence.  The runner itself is
   negative-controlled in the test suite against a deliberately leaky store,
   so it is not a formality.

Canonical dataset shape (JSON-serializable):

    {
      "version": 1,
      "tenants": {"t1": {}, "t2": {}},          # tenant namespaces that exist
      "fallback_tenant": "__fallback__",        # reserved unscoped bucket
      "quarantine_tenant": "__quarantine__",    # reserved holding bucket
      "records": {
        "t1": {"agent-1": {"tenant_id": "t1", "record_id": "agent-1", ...}},
        ...
      }
    }
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .model import Finding, FindingCategory, ScanReport, Severity
from .store import TenantScopedStore

DEFAULT_FALLBACK_TENANT = "__fallback__"
DEFAULT_QUARANTINE_TENANT = "__quarantine__"

# Rule ids for data-integrity findings (consumed by triage + repair).
RULE_ORPHANED = "D1-ORPHANED-RECORD"
RULE_FALLBACK_PILEUP = "D2-FALLBACK-TENANT-PILEUP"
RULE_DUPLICATE = "D3-CROSS-TENANT-DUPLICATE"
RULE_DENORMALIZED = "D4-DENORMALIZED-RECORD"
RULE_PROBE = "P1-PROBE-FAILURE"


# --------------------------------------------------------------------------- #
# dataset integrity scan (static)
# --------------------------------------------------------------------------- #

def scan_dataset(dataset: Dict[str, Any], *, target: str = "dataset",
                 fallback: str = DEFAULT_FALLBACK_TENANT,
                 quarantine: str = DEFAULT_QUARANTINE_TENANT) -> ScanReport:
    """Detect data-integrity violations in a tenant-scoped dataset.

    Read-only and deterministic.  Findings carry the offending tenant and
    record so a triage gate can act on mechanism.
    """
    report = ScanReport()
    tenants = set(str(t) for t in dataset.get("tenants", {}))
    fallback = str(dataset.get("fallback_tenant", fallback))
    quarantine = str(dataset.get("quarantine_tenant", quarantine))
    reserved = {fallback, quarantine}
    records = dataset.get("records", {})
    if not isinstance(records, dict):
        raise ValueError("dataset.records must be a mapping")

    # Buckets that exist in the records but reference no tenant namespace.
    known_buckets = set(tenants) | reserved

    # --- D1: orphaned buckets / records ----------------------------------
    for bucket in sorted(records):
        if bucket in reserved:
            continue
        if bucket not in tenants:
            _emit(report, RULE_ORPHANED, FindingCategory.ORPHANED_RECORD,
                  Severity.CRITICAL,
                  f"record bucket {bucket!r} references no tenant namespace "
                  f"(orphaned)",
                  target=target, scope=f"tenant {bucket}", tenant=bucket)

    # --- D4: denormalization violations ----------------------------------
    # A record whose denormalized tenant_id disagrees with its bucket.
    for bucket in sorted(records):
        bucket_rows = records[bucket]
        if not isinstance(bucket_rows, dict):
            continue
        for rid in sorted(bucket_rows):
            row = bucket_rows[rid]
            row_tenant = row.get("tenant_id") if isinstance(row, dict) else None
            if row_tenant is not None and str(row_tenant) != bucket:
                _emit(report, RULE_DENORMALIZED,
                      FindingCategory.DENORMALIZED_RECORD, Severity.CRITICAL,
                      f"record {rid!r} in bucket {bucket!r} carries "
                      f"tenant_id {row_tenant!r} (denormalization violation)",
                      target=target, scope=f"tenant {bucket} / {rid}",
                      tenant=bucket, evidence=_row_snippet(row))

    # --- D2: fallback-tenant pile-up -------------------------------------
    # Real records must never live in the reserved fallback tenant.
    if fallback in records:
        for rid in sorted(records[fallback]):
            row = records[fallback][rid]
            _emit(report, RULE_FALLBACK_PILEUP,
                  FindingCategory.FALLBACK_TENANT_PILEUP, Severity.CRITICAL,
                  f"record {rid!r} is piled up in the reserved fallback "
                  f"tenant {fallback!r} (unscoped / ambiguous)",
                  target=target, scope=f"tenant {fallback} / {rid}",
                  tenant=fallback, evidence=_row_snippet(row))

    # --- D3: cross-tenant duplicates -------------------------------------
    seen: Dict[str, List[str]] = {}
    for bucket in sorted(records):
        if bucket == quarantine:
            continue
        if not isinstance(records[bucket], dict):
            continue
        for rid in records[bucket]:
            seen.setdefault(rid, []).append(bucket)
    for rid in sorted(seen):
        owners = seen[rid]
        if len(owners) > 1:
            _emit(report, RULE_DUPLICATE, FindingCategory.CROSS_TENANT_DUPLICATE,
                  Severity.HIGH,
                  f"record id {rid!r} is present under {len(owners)} tenants "
                  f"({', '.join(owners)}) — ambiguous ownership",
                  target=target, scope=f"record {rid}", tenant=None)

    return report


def _row_snippet(row: Any) -> str:
    try:
        return json.dumps(row, sort_keys=True, default=str)[:400]
    except (TypeError, ValueError):
        return str(row)[:400]


def _emit(report: ScanReport, rule_id: str, category: FindingCategory,
          severity: Severity, message: str, *, target: str, scope: str,
          tenant: Optional[str], evidence: str = "") -> None:
    report.add_finding(Finding(
        rule_id=rule_id, category=category, severity=severity, message=message,
        target=target, scope=scope, evidence=evidence, tenant=tenant,
    ))


# --------------------------------------------------------------------------- #
# runtime cross-tenant probes (per tenant, on cadence)
# --------------------------------------------------------------------------- #

@dataclass
class ProbeOutcome:
    """Outcome of one cadence run over a store."""

    probes_run: int = 0
    passed: int = 0
    failed: int = 0

    @property
    def all_passed(self) -> bool:
        return self.failed == 0 and self.probes_run > 0


def run_cadence_probes(store: TenantScopedStore, tenants: Iterable[str],
                       *, target: str = "probes",
                       sample_foreign: int = 2) -> Tuple[List[Finding], ProbeOutcome]:
    """Run per-tenant cross-tenant read/delete/denorm-write probes.

    For each tenant ``T`` and each of up to ``sample_foreign`` other tenants
    ``F`` that actually owns rows, the runner:
      * reads one of F's rows as T            (must come back None)
      * deletes F's row as T                  (must be a no-op)
      * ``require``s F's row as T             (must raise IsolationScopeError)
      * writes a row into T's bucket carrying F as its denormalized tenant
                                             (must raise IsolationScopeError)

    Returns ``(findings, outcome)`` where every failure to fail closed is a
    ``PROBE_FAILURE`` finding with evidence.  The default store is
    fail-closed by construction, so a clean run yields no findings; the test
    suite negative-controls the runner against a leaky store that returns
    foreign rows, which must be reported.
    """
    findings: List[Finding] = []
    outcome = ProbeOutcome()
    tenants = sorted(set(tenants))

    for tenant in tenants:
        foreign = [f for f in tenants if f != tenant]
        # Determine which foreign tenants actually own rows to probe against.
        foreign_with_rows: List[str] = []
        for f in foreign:
            if store.list_records(f):
                foreign_with_rows.append(f)
        for f in foreign_with_rows[:sample_foreign]:
            rows = store.list_records(f)
            if not rows:
                continue
            rid = rows[0]["record_id"]

            # read probe
            outcome.probes_run += 1
            leaky = store.get(tenant, rid)
            if leaky is not None:
                outcome.failed += 1
                findings.append(_probe_finding(
                    tenant, f, rid, "read",
                    f"read of {f!r}'s row {rid!r} as {tenant!r} returned a "
                    f"row instead of failing closed",
                    target=target))
            else:
                outcome.passed += 1

            # require probe
            outcome.probes_run += 1
            try:
                store.require(tenant, rid)
                outcome.failed += 1
                findings.append(_probe_finding(
                    tenant, f, rid, "require",
                    f"require of {f!r}'s row {rid!r} as {tenant!r} did not "
                    f"raise IsolationScopeError",
                    target=target))
            except Exception:
                outcome.passed += 1

            # delete probe (must be a no-op on the foreign row)
            outcome.probes_run += 1
            before = store.get(f, rid) is not None
            store.delete(tenant, rid)
            after = store.get(f, rid) is not None
            if before and not after:
                outcome.failed += 1
                findings.append(_probe_finding(
                    tenant, f, rid, "delete",
                    f"delete as {tenant!r} removed {f!r}'s row {rid!r} "
                    f"(cross-tenant write)",
                    target=target))
            else:
                outcome.passed += 1

            # denormalized-write probe (must raise)
            outcome.probes_run += 1
            try:
                store.put(tenant, {"tenant_id": f, "record_id": rid,
                                   "note": "probe"})
                outcome.failed += 1
                findings.append(_probe_finding(
                    tenant, f, rid, "write",
                    f"write into {tenant!r} with denormalized tenant {f!r} "
                    f"was accepted (denormalization gate bypassed)",
                    target=target))
            except Exception:
                outcome.passed += 1

    return findings, outcome


def _probe_finding(tenant: str, foreign: str, rid: str, kind: str,
                   message: str, *, target: str) -> Finding:
    return Finding(
        rule_id=RULE_PROBE, category=FindingCategory.PROBE_FAILURE,
        severity=Severity.CRITICAL, message=message, target=target,
        scope=f"probe {kind} {tenant}->{foreign} {rid}", tenant=tenant,
    )


# --------------------------------------------------------------------------- #
# dataset helpers (used by the repair CLI)
# --------------------------------------------------------------------------- #

def load_dataset(path: str) -> Dict[str, Any]:
    """Load and structurally validate a dataset file."""
    with open(path, "r", encoding="utf-8") as handle:
        dataset = json.load(handle)
    if not isinstance(dataset, dict) or "records" not in dataset:
        raise ValueError(f"{path}: not a tenant dataset (missing 'records')")
    return dataset


def store_from_dataset(dataset: Dict[str, Any]) -> TenantScopedStore:
    """Build the scope-gated store view of a dataset."""
    return TenantScopedStore.from_dataset(dataset)


def dataset_tenants(dataset: Dict[str, Any]) -> List[str]:
    """The tenant namespaces a cadence scan runs over (registry order)."""
    return sorted(str(t) for t in dataset.get("tenants", {}))


def clone_dataset(dataset: Dict[str, Any]) -> Dict[str, Any]:
    """Deep copy (repair never mutates its input in place)."""
    return copy.deepcopy(dataset)
