"""Read-only, filterable read model over the tamper-evident audit trail

---knowledge---
module_id: telemetry.audit.read_model
system: telemetry
app: audit
solution_class: enterprise
patterns: [read-only-projection, tamper-evident-consumer, fail-closed]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [AuditReadModel, open_read_model, ChainVerdict, severity_of, FilterError, ReadModelError]
invariants: "read-only by construction: the model exposes only read methods and owns no storage, key material or chain crypto"
gotchas: ""
related: ["#347", "#1510"]
do_not_duplicate: null
---knowledge---

(telemetry/audit, issue #347).

This module is the **serving seam** for the audit surface: the backend the
shell's Audit view consumes (ADOPT-0013 / ADR-0013, served through the
paperclip integration). It is a *projection* over the already-merged
tamper-evident ledger ([`telemetry/ledger`](../ledger/README.md), issue #31) —
it owns no storage, no key material and no chain crypto.

Three guarantees, each structural rather than promised:

* **Read-only.** ``AuditReadModel`` exposes only read methods (``records``,
  ``filter``, ``stats``, ``verify_chain``, ``trusted_tail``). There is no
  append/update/delete path, and the module never calls the ledger's
  ``append``/``rechain``; ``tests/test_read_only.py`` asserts the closed
  vocabulary of exposed names so a future write method fails the suite.
* **Never a silent skip.** Records are loaded through the ledger's public API
  (``LedgerStore.records``), which fails closed: a malformed or chain-broken
  file raises instead of returning partial data, so an unverifiable record is a
  finding, never a pass. ``verify_chain`` maps every tenant to an honest
  tri-state verdict and treats ``CANNOT-ASSESS`` as a failure to pass.
* **Deterministic.** ``filter`` and ``stats`` are pure functions of the stored
  trails: results are sorted by ``(tenantId, seq)`` and ``stats`` is built from
  sorted keys, so two runs over one revision are byte-identical.

Filters (``filter(**criteria)``; the field set is closed and an unknown field
is refused by name):

===========  =============================================================
``actor``    canonical ``kind:id`` principal; a bare id matches the id part
``agent``    shorthand for ``actor=agent:<id>`` (actor kind must be agent)
``action``   exact ``action`` string (e.g. ``model.call``)
``severity`` derived severity rung (see :func:`severity_of`)
``since``    inclusive RFC 3339 lower bound on ``ts``
``until``    inclusive RFC 3339 upper bound on ``ts``
``entity``   the audited object: exact ``evidence`` or ``resource``/prefix
``tenant``   restrict the scan to one tenant chain
===========  =============================================================

**Severity is a derived projection, not a stored field.** The audit-event
contract (``telemetry/ledger/audit_event.schema.json``) carries no severity, so
this read model computes one deterministically from the action's final
dot-segment through the closed vocabulary in :data:`_VERB_SEVERITY`; an unknown
verb is ``info``. The derivation is total, documented and tested, so the filter
is reproducible rather than guessed.

Trailing truncation cannot be seen from an internally-consistent file: capture
:meth:`AuditReadModel.trusted_tail` from an intact chain and pass it to
``verify_chain(expected=...)`` to detect the silent loss of the last records.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

# telemetry/ has no __init__.py (per-issue package directory, mirroring
# registry/), so make the sibling ledger package importable from anywhere.
_HERE = os.path.dirname(os.path.abspath(__file__))
_TELEMETRY = os.path.dirname(_HERE)
if _TELEMETRY not in sys.path:
    sys.path.insert(0, _TELEMETRY)

from ledger import LedgerStore, open_ledger  # noqa: E402  (after sys.path)

__all__ = [
    "AuditReadModel",
    "ChainVerdict",
    "FilterError",
    "ReadModelError",
    "FILTER_FIELDS",
    "SEVERITIES",
    "open_read_model",
    "severity_of",
]

#: The severity ladder, least to most severe. Closed vocabulary.
SEVERITIES: Tuple[str, ...] = ("info", "notice", "warning", "critical")

#: The closed set of filter fields. Anything else is refused by name.
FILTER_FIELDS: Tuple[str, ...] = (
    "actor",
    "agent",
    "action",
    "severity",
    "since",
    "until",
    "entity",
    "tenant",
)

#: Deterministic verb -> severity projection. Keyed on the action's final
#: dot-segment, lowercased. Total: an unknown verb is ``info``.
_VERB_SEVERITY: Dict[str, str] = {
    # critical - an explicit refusal or a hard stop
    "breach": "critical",
    "block": "critical",
    "deny": "critical",
    "denied": "critical",
    "kill": "critical",
    "revoke": "critical",
    # warning - destructive or override-shaped
    "delete": "warning",
    "escalate": "warning",
    "override": "warning",
    "rechain": "warning",
    "rotate": "warning",
    # notice - a state change worth surfacing
    "approve": "notice",
    "decision": "notice",
    "grant": "notice",
    "register": "notice",
    "sync": "notice",
    "topup": "notice",
    "update": "notice",
    # info - routine traffic (the default)
    "call": "info",
    "get": "info",
    "list": "info",
    "query": "info",
    "read": "info",
}


class ReadModelError(Exception):
    """Base error for the audit read model."""


class FilterError(ReadModelError):
    """A filter was malformed or named a field outside the closed set."""


def severity_of(action: str) -> str:
    """Derive the severity rung of an action (total, deterministic).

    The final dot-segment of the action (``model.call`` -> ``call``) is looked
    up in the closed :data:`_VERB_SEVERITY` projection; an unknown verb is
    ``info``. See the module docstring for why severity is derived rather than
    stored.
    """
    verb = str(action).rsplit(".", 1)[-1].strip().lower()
    return _VERB_SEVERITY.get(verb, "info")


def _parse_ts(value: Any, *, field: str) -> datetime:
    """Parse an RFC 3339 UTC stamp (or bare date) into an aware datetime."""
    if not isinstance(value, str) or not value:
        raise FilterError(f"{field} must be an RFC 3339 UTC string, got {value!r}")
    text = value[:-1] if value.endswith("Z") else value
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError as exc:
        raise FilterError(f"{field} {value!r} is not RFC 3339: {exc}") from exc
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc)


def _actor_matches(actor: str, value: Any, *, kind: Optional[str] = None) -> bool:
    """Match a canonical ``kind:id`` actor against a filter value."""
    text = str(value)
    if ":" in text:
        return actor == text
    actor_kind, _, actor_id = actor.partition(":")
    if kind is not None and actor_kind != kind:
        return False
    return actor_id == text


def _entity_matches(record: Mapping[str, Any], value: Any) -> bool:
    """Match the audited object: exact ``evidence`` or ``resource``/prefix."""
    text = str(value)
    evidence = record.get("evidence")
    if evidence is not None and evidence == text:
        return True
    resource = record.get("resource")
    if not isinstance(resource, str):
        return False
    return resource == text or resource.startswith(text.rstrip("/") + "/")


def _normalize_expected(
    expected: Optional[Mapping[str, Any]],
) -> Dict[str, Tuple[int, str]]:
    """Normalize a trusted-tail anchor into ``{tenant: (seq, hash)}``."""
    if expected is None:
        return {}
    if not isinstance(expected, Mapping):
        raise ReadModelError("expected must be a mapping of tenant -> tail anchor")
    normalized: Dict[str, Tuple[int, str]] = {}
    for tenant, anchor in expected.items():
        if isinstance(anchor, Mapping):
            seq, digest = anchor.get("seq"), anchor.get("hash")
        elif isinstance(anchor, (tuple, list)) and len(anchor) == 2:
            seq, digest = anchor[0], anchor[1]
        else:
            raise ReadModelError(
                f"expected anchor for {tenant!r} must be (seq, hash) or "
                f"{{'seq':..., 'hash':...}}"
            )
        if not isinstance(seq, int) or not isinstance(digest, str):
            raise ReadModelError(f"expected anchor for {tenant!r} is malformed")
        normalized[str(tenant)] = (seq, digest)
    return normalized


class ChainVerdict:
    """Honest tri-state aggregate of every tenant chain in the read model.

    ``status`` is ``OK`` / ``NOT-OK`` / ``CANNOT-ASSESS``. ``NOT-OK`` outranks
    ``CANNOT-ASSESS`` because a definite tamper is the stronger statement.
    ``is_pass`` is true only for ``OK`` — an unverifiable chain is never a pass.
    """

    __slots__ = ("status", "tenants", "expected")

    OK = "OK"
    NOT_OK = "NOT-OK"
    CANNOT_ASSESS = "CANNOT-ASSESS"

    def __init__(
        self,
        status: str,
        *,
        tenants: Mapping[str, Mapping[str, Any]],
        expected: Optional[Mapping[str, Tuple[int, str]]] = None,
    ) -> None:
        self.status = status
        self.tenants = {key: dict(value) for key, value in sorted(tenants.items())}
        self.expected = dict(expected) if expected else None

    @property
    def is_pass(self) -> bool:
        """True only when every tenant chain verified OK."""
        return self.status == self.OK

    @property
    def exit_code(self) -> int:
        """Wire contract shared with the ledger: OK=0, NOT-OK=1, CA=2."""
        return {self.OK: 0, self.NOT_OK: 1, self.CANNOT_ASSESS: 2}[self.status]

    def findings(self) -> List[Dict[str, Any]]:
        """Every tenant whose chain is not OK, in tenant order (never silent)."""
        return [
            entry
            for _, entry in sorted(self.tenants.items())
            if entry.get("status") != self.OK
        ]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "exitCode": self.exit_code,
            "tenants": {key: self.tenants[key] for key in sorted(self.tenants)},
            "findings": self.findings(),
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"ChainVerdict({self.status}, tenants={sorted(self.tenants)})"


class AuditReadModel:
    """A read-only, filterable view over one or more tenant audit chains."""

    def __init__(
        self,
        store: LedgerStore,
        *,
        tenants: Optional[Sequence[str]] = None,
    ) -> None:
        self._store = store
        self._tenants = tuple(tenants) if tenants else None

    @property
    def store(self) -> LedgerStore:
        """The underlying ledger store (read through its public API only)."""
        return self._store

    def tenants(self) -> List[str]:
        """The tenant chains in scope, sorted (deterministic)."""
        if self._tenants is not None:
            return sorted(self._tenants)
        return sorted(self._store.tenant_ids())

    # ------------------------------------------------------------------ #
    # reads
    # ------------------------------------------------------------------ #
    def records(self, tenant: Optional[str] = None) -> List[Dict[str, Any]]:
        """Every record in scope, sorted by ``(tenantId, seq)``.

        Fail closed: the ledger raises on a malformed or chain-broken file, so
        this never returns a partially-verified trail.
        """
        scope = [tenant] if tenant is not None else self.tenants()
        rows: List[Dict[str, Any]] = []
        for name in scope:
            rows.extend(dict(record) for record in self._store.records(name))
        rows.sort(key=lambda record: (record["tenantId"], record["seq"]))
        return rows

    def filter(self, **criteria: Any) -> List[Dict[str, Any]]:
        """Filter the trail deterministically; return rows sorted by seq.

        Accepts the closed field set in :data:`FILTER_FIELDS`; any other field
        is refused by name (:class:`FilterError`).
        """
        unknown = sorted(key for key in criteria if key not in FILTER_FIELDS)
        if unknown:
            raise FilterError(
                "unknown filter field(s): "
                + ", ".join(unknown)
                + "; known: "
                + ", ".join(FILTER_FIELDS)
            )
        severity = criteria.get("severity")
        if severity is not None and severity not in SEVERITIES:
            raise FilterError(
                f"unknown severity {severity!r}; known: " + ", ".join(SEVERITIES)
            )
        since = (
            _parse_ts(criteria["since"], field="since")
            if criteria.get("since") is not None
            else None
        )
        until = (
            _parse_ts(criteria["until"], field="until")
            if criteria.get("until") is not None
            else None
        )
        if since is not None and until is not None and since > until:
            raise FilterError("since is after until (the window is empty)")

        actor = criteria.get("actor")
        agent = criteria.get("agent")
        action = criteria.get("action")
        entity = criteria.get("entity")

        rows: List[Dict[str, Any]] = []
        for record in self.records(criteria.get("tenant")):
            if actor is not None and not _actor_matches(record["actor"], actor):
                continue
            if agent is not None and not _actor_matches(record["actor"], agent, kind="agent"):
                continue
            if action is not None and record["action"] != action:
                continue
            if severity is not None and severity_of(record["action"]) != severity:
                continue
            if entity is not None and not _entity_matches(record, entity):
                continue
            stamp = _parse_ts(record["ts"], field=f"ts of seq {record['seq']}")
            if since is not None and stamp < since:
                continue
            if until is not None and stamp > until:
                continue
            rows.append(record)
        rows.sort(key=lambda record: (record["tenantId"], record["seq"]))
        return rows

    def stats(self) -> Dict[str, Any]:
        """Deterministic summary: counts, time span and the derived histograms."""
        per_tenant: Dict[str, Dict[str, Any]] = {}
        severity_histogram = {rung: 0 for rung in SEVERITIES}
        actor_kind_histogram: Dict[str, int] = {}
        action_histogram: Dict[str, int] = {}
        total = 0
        for tenant in self.tenants():
            rows = self._store.records(tenant)
            total += len(rows)
            for record in rows:
                severity_histogram[severity_of(record["action"])] += 1
                kind = record["actor"].partition(":")[0]
                actor_kind_histogram[kind] = actor_kind_histogram.get(kind, 0) + 1
                verb = record["action"]
                action_histogram[verb] = action_histogram.get(verb, 0) + 1
            stamps = [record["ts"] for record in rows]
            per_tenant[tenant] = {
                "records": len(rows),
                "firstTs": min(stamps) if stamps else None,
                "lastTs": max(stamps) if stamps else None,
                "tailSeq": rows[-1]["seq"] if rows else 0,
            }
        return {
            "tenants": {name: per_tenant[name] for name in sorted(per_tenant)},
            "totalRecords": total,
            "severityHistogram": {
                rung: severity_histogram[rung] for rung in SEVERITIES
            },
            "actorKindHistogram": {
                key: actor_kind_histogram[key] for key in sorted(actor_kind_histogram)
            },
            "actionHistogram": {
                key: action_histogram[key] for key in sorted(action_histogram)
            },
        }

    def trusted_tail(self) -> Dict[str, Dict[str, Any]]:
        """The verified tail anchor per tenant (for truncation detection).

        Only returns tails that passed a full chain walk, so the anchor is safe
        to feed back into :meth:`verify_chain` as ``expected``.
        """
        return {
            tenant: {"seq": seq, "hash": digest}
            for tenant in self.tenants()
            for seq, digest in [self._store.tail_state(tenant)]
        }

    # ------------------------------------------------------------------ #
    # verify-chain
    # ------------------------------------------------------------------ #
    def verify_chain(
        self,
        *,
        expected: Optional[Mapping[str, Any]] = None,
    ) -> ChainVerdict:
        """Verify every tenant chain, aggregating honest tri-state verdicts.

        Detection (delegated to the ledger's own chain walk, never
        re-implemented here):

        * a **modified** record -> ``hash mismatch at record N`` (NOT-OK);
        * a **reordered** record -> ``seq`` / ``prevHash`` mismatch (NOT-OK);
        * a **removed** record -> the surviving records' ``seq`` no longer
          matches their position (NOT-OK);
        * a **removed trailing** record -> invisible to the chain alone, so
          pass ``expected`` (from :meth:`trusted_tail`) to make the tail
          mismatch a NOT-OK;
        * an **unparseable** file -> CANNOT-ASSESS, which is never a pass.

        ``expected`` is the trusted ``{tenant: (seq, hash)}`` anchor.
        """
        anchors = _normalize_expected(expected)
        tenants = self.tenants()
        if not tenants:
            return ChainVerdict(
                ChainVerdict.CANNOT_ASSESS,
                tenants={},
                expected=anchors or None,
            )
        per_tenant: Dict[str, Dict[str, Any]] = {}
        worst = ChainVerdict.OK
        for tenant in tenants:
            try:
                verdict = self._store.verify(tenant, expected=anchors.get(tenant))
                entry = verdict.as_dict()
            except Exception as exc:  # pragma: no cover - defensive, fail closed
                entry = {
                    "status": ChainVerdict.CANNOT_ASSESS,
                    "tenant": tenant,
                    "seq": 0,
                    "hash": "",
                    "detail": f"chain could not be assessed: {type(exc).__name__}: {exc}",
                    "brokenAt": None,
                }
            per_tenant[tenant] = entry
            status = entry.get("status")
            if status == ChainVerdict.NOT_OK:
                worst = ChainVerdict.NOT_OK
            elif status == ChainVerdict.CANNOT_ASSESS and worst != ChainVerdict.NOT_OK:
                worst = ChainVerdict.CANNOT_ASSESS
        return ChainVerdict(worst, tenants=per_tenant, expected=anchors or None)


def open_read_model(
    directory: str,
    *,
    keystore: Any = None,
    tenants: Optional[Sequence[str]] = None,
) -> AuditReadModel:
    """Open a read model over the ledger at ``directory`` (read-only)."""
    return AuditReadModel(open_ledger(directory, keystore=keystore), tenants=tenants)
