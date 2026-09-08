"""Opt-in isolation repair (issue #30, acceptance criterion 2).

Repair is **opt-in, transactional, audited and idempotent** — there is never
a background silent mutation (the capital-underwriting doctrine: detect-first
always; mutate only when an operator explicitly applies).

* :func:`plan_repairs` — dry-run by default.  Re-derives the current state
  from the dataset (anchored on mechanism, never on message text) and returns
  the concrete actions that *would* run, mutating nothing.
* :func:`repair_execute` — the only mutation path.  It validates and applies
  every action on a private clone **first**; if anything fails or any
  isolation finding survives, it raises :class:`RepairAbortError` and the
  caller's dataset is untouched (all-or-nothing).  Only on full success is a
  new dataset (with the audit ledger appended) returned.
* Idempotent: re-planning a repaired dataset yields zero actions — the state
  is repaired, so nothing further changes.

Action vocabulary (never deletes, never guesses):

* ``quarantine``            — move a record out of a live namespace into the
                              reserved ``__quarantine__`` holding tenant.
* ``rescue_to_tenant``      — move a record to the real tenant its own
                              denormalized ``tenant_id`` / owner hint names.
* ``quarantine_duplicates`` — keep one canonical copy, quarantine the rest
                              of a cross-tenant duplicate id.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .errors import RepairAbortError
from .integrity import (DEFAULT_FALLBACK_TENANT, DEFAULT_QUARANTINE_TENANT,
                        RULE_DENORMALIZED, RULE_DUPLICATE,
                        RULE_FALLBACK_PILEUP, RULE_ORPHANED,
                        scan_dataset)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class RepairAction:
    """One concrete, evidence-backed mutation the operator may apply."""

    action: str  # quarantine | rescue_to_tenant | quarantine_duplicates
    record_id: str
    from_tenant: str
    to_tenant: str
    reason: str
    canonical: bool = True  # for duplicate handling: the surviving copy


def plan_repairs(dataset: Dict[str, Any],
                 quarantine: str = DEFAULT_QUARANTINE_TENANT) -> List[RepairAction]:
    """Compute the actions that would repair ``dataset``.  Mutates nothing.

    Actions are derived deterministically from the *current* dataset state
    (a fresh integrity scan), not from finding prose, so repair stays
    anchored on mechanism.
    """
    report = scan_dataset(dataset, quarantine=quarantine)
    tenants = set(str(t) for t in dataset.get("tenants", {}))
    fallback = str(dataset.get("fallback_tenant", DEFAULT_FALLBACK_TENANT))
    records = dataset.get("records", {})
    actions: List[RepairAction] = []

    # --- D1: orphaned buckets -> quarantine (never delete) ---------------
    orphan_buckets = sorted(
        f.tenant for f in report.findings if f.rule_id == RULE_ORPHANED
        and f.tenant is not None
    )
    for bucket in orphan_buckets:
        for rid in sorted(records.get(bucket, {})):
            actions.append(RepairAction(
                action="quarantine", record_id=rid, from_tenant=bucket,
                to_tenant=quarantine,
                reason="record bucket references no tenant namespace "
                       "(orphaned); quarantined for manual review",
            ))

    # --- D2: fallback pile-up -> rescue when the record names a real
    #         tenant, else quarantine -------------------------------------
    fallback_rows = records.get(fallback, {})
    for rid in sorted(fallback_rows):
        row = fallback_rows[rid]
        hint = None
        if isinstance(row, dict):
            for key in ("owner_tenant", "logical_owner", "owner_hint",
                        "tenant_id"):
                if key in row and str(row[key]) in tenants \
                        and str(row[key]) != fallback:
                    hint = str(row[key])
                    break
        if hint is not None:
            actions.append(RepairAction(
                action="rescue_to_tenant", record_id=rid, from_tenant=fallback,
                to_tenant=hint,
                reason=f"record in fallback tenant names {hint!r} as its "
                       f"owner; rescued to the tenant it claims",
            ))
        else:
            actions.append(RepairAction(
                action="quarantine", record_id=rid, from_tenant=fallback,
                to_tenant=quarantine,
                reason="record in fallback tenant with no real-tenant owner "
                       "hint; quarantined (never guessed)",
            ))

    # --- D3: cross-tenant duplicates -> keep canonical, quarantine the rest
    seen: Dict[str, List[str]] = {}
    for bucket in sorted(records):
        if bucket == quarantine or not isinstance(records.get(bucket), dict):
            continue
        for rid in records[bucket]:
            seen.setdefault(rid, []).append(bucket)
    for rid in sorted(seen):
        owners = [o for o in seen[rid] if o in tenants]
        if len(owners) > 1:
            canonical = sorted(owners)[0]
            for bucket in sorted(owners):
                if bucket == canonical:
                    continue
                actions.append(RepairAction(
                    action="quarantine_duplicates", record_id=rid,
                    from_tenant=bucket, to_tenant=quarantine, canonical=False,
                    reason=f"record id {rid!r} is duplicated across tenants; "
                           f"canonical copy kept in {canonical!r}, duplicate "
                           f"quarantined",
                ))

    # --- D4: denormalization violations -> move to the record's own tenant
    for bucket in sorted(records):
        if not isinstance(records.get(bucket), dict) or bucket == quarantine:
            continue
        for rid in sorted(records[bucket]):
            row = records[bucket][rid]
            row_tenant = row.get("tenant_id") if isinstance(row, dict) else None
            if row_tenant is not None and str(row_tenant) != bucket:
                if str(row_tenant) in tenants:
                    actions.append(RepairAction(
                        action="rescue_to_tenant", record_id=rid,
                        from_tenant=bucket, to_tenant=str(row_tenant),
                        reason=f"record denormalized tenant_id "
                               f"{row_tenant!r} does not match bucket "
                               f"{bucket!r}; moved to the tenant it claims",
                    ))
                else:
                    actions.append(RepairAction(
                        action="quarantine", record_id=rid,
                        from_tenant=bucket, to_tenant=quarantine,
                        reason=f"record denormalized tenant_id "
                               f"{row_tenant!r} names no tenant namespace; "
                               f"quarantined",
                    ))
    return actions


# --------------------------------------------------------------------------- #
# transactional application
# --------------------------------------------------------------------------- #

def _row_of(bucket_rows: Dict[str, Any], rid: str) -> Optional[Dict[str, Any]]:
    row = bucket_rows.get(rid)
    return row if isinstance(row, dict) else None


def _quarantine_key(bucket: Dict[str, Any], source_tenant: str,
                    rid: str) -> str:
    """Deterministic quarantine key that never collides with existing rows.

    A repeat offense for the same ``(source, rid)`` must never overwrite the
    previously quarantined row (lossless), so the key is suffixed.
    """
    base = f"{source_tenant}|{rid}"
    if base not in bucket:
        return base
    index = 2
    while f"{base}#{index}" in bucket:
        index += 1
    return f"{base}#{index}"


def _apply_actions(work: Dict[str, Any], actions: List[RepairAction],
                   quarantine: str) -> None:
    """Apply actions onto the private working copy (pre-validated)."""
    records = work.setdefault("records", {})
    q_bucket = records.setdefault(quarantine, {})

    for action in actions:
        src = records.get(action.from_tenant)
        row = _row_of(src, action.record_id) if src else None
        if row is None:
            raise RepairAbortError(
                f"source row {action.from_tenant}/{action.record_id} is gone "
                f"mid-repair; aborting",
            )
        if action.to_tenant == quarantine or action.action in (
                "quarantine", "quarantine_duplicates"):
            # Preserve provenance inside the quarantined row, then re-key it
            # under a collision-safe key so distinct sources never collide.
            qkey = _quarantine_key(q_bucket, action.from_tenant,
                                   action.record_id)
            qrow = dict(row)
            qrow["tenant_id"] = quarantine
            qrow["record_id"] = qkey
            qrow["quarantine_source_tenant"] = action.from_tenant
            qrow["quarantine_original_record_id"] = action.record_id
            qrow["quarantine_reason"] = action.reason
            q_bucket[qkey] = qrow
            del src[action.record_id]
        else:
            target = records.setdefault(action.to_tenant, {})
            if action.record_id in target:
                # Rescue into a tenant that already owns this id must never
                # overwrite the existing row; quarantine the incoming copy.
                qkey = _quarantine_key(q_bucket, action.from_tenant,
                                       action.record_id)
                qrow = dict(row)
                qrow["tenant_id"] = quarantine
                qrow["record_id"] = qkey
                qrow["quarantine_source_tenant"] = action.from_tenant
                qrow["quarantine_original_record_id"] = action.record_id
                qrow["quarantine_reason"] = (
                    "rescue target already owns this id; duplicate quarantined"
                )
                q_bucket[qkey] = qrow
                del src[action.record_id]
                continue
            moved = dict(row)
            moved["tenant_id"] = action.to_tenant
            target[action.record_id] = moved
            del src[action.record_id]
    # Drop now-empty source buckets so an orphaned namespace is fully cleared.
    for tenant in list(records):
        if isinstance(records[tenant], dict) and not records[tenant]:
            del records[tenant]


def repair_execute(dataset: Dict[str, Any], *, operator: str = "system",
                   quarantine: str = DEFAULT_QUARANTINE_TENANT,
                   audit: bool = True) -> Dict[str, Any]:
    """Apply repair to a *clone* and return the repaired dataset.

    This is the only mutation path and it is all-or-nothing: every action is
    precomputed, then applied to a working copy, then the working copy is
    re-scanned.  Any surviving isolation finding aborts the whole repair with
    :class:`RepairAbortError` and the input ``dataset`` is never mutated.
    The audit ledger is appended to the returned dataset only on success.
    """
    import copy

    actions = plan_repairs(dataset, quarantine=quarantine)
    if not actions:
        # Nothing to repair; still return an unchanged deep copy so callers
        # never hold the same mutable object twice.
        return copy.deepcopy(dataset)

    work = copy.deepcopy(dataset)
    _apply_actions(work, actions, quarantine)

    # Post-condition: a successful repair must leave zero isolation findings.
    remaining = scan_dataset(work, quarantine=quarantine)
    if remaining.has_findings:
        raise RepairAbortError(
            "repair would not clear all isolation findings; aborting "
            "(all-or-nothing)",
            reasons=[f.message for f in remaining.findings],
        )

    if audit:
        ledger = work.setdefault("audit", [])
        ledger.append({
            "ts": _utcnow(),
            "operator": operator,
            "mode": "repair",
            "actions": len(actions),
            "records_modified": len(actions),
            "success": True,
            "issue_types": sorted({a.action for a in actions}),
        })
    work.setdefault("fallback_tenant",
                    dataset.get("fallback_tenant", DEFAULT_FALLBACK_TENANT))
    work.setdefault("quarantine_tenant", quarantine)
    return work
