"""The enterprise/GDC roll-up projection engine (issue #151).

Repos hold fleets; fleets belong to tenants; tenants belong to the
enterprise/GDC org. This module computes the org-level view of that hierarchy
from **declared inputs only**, and it computes nothing else — no discovery, no
measurement, no caching, no state.

Three properties are load-bearing and each is enforced here rather than
asserted in prose:

**It is a projection, never a second source of truth.** The engine takes
immutable facts and returns a report object. It opens no file for writing,
persists nothing, keeps no cache, and mutates no input: every fact it reports is
a pure function of the declarations it was handed, and the report carries the
sha256 of each input so a reader can re-derive the same view from the same
files. Calling ``project`` twice on the same inputs returns equal reports.

**Unassessable is never a pass.** A missing inventory, a malformed document, an
unreadable schema, an empty fleet, a utilisation with no capacity behind it —
each is CANNOT-ASSESS, is named in the report, and prevents an OK status. The
aggregate is still printed (an operator needs to see what *is* known), but it is
marked ``assessed: false`` so it cannot be read as complete.

**Nothing is clamped.** Spend above a ceiling is reported as an explicit
finding with the excess, and utilisation above 1.0 is reported as the ratio it
actually is. Silently capping either would make the org view disagree with the
repo it claims to summarise.

Status is the repo's tri-state (GR-28): ``ok`` / ``not-ok`` / ``cannot-assess``,
mapped onto exit codes 0 / 1 / 2 by ``cli.py``. CANNOT-ASSESS dominates NOT-OK:
an incomplete input set can understate findings, so a view that cannot be
trusted must not be reported in the same breath as one that was checked.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

REPORT_SCHEMA = "ao.rollup/enterprise-view-v1"

STATUS_OK = "ok"
STATUS_NOT_OK = "not-ok"
STATUS_CANNOT_ASSESS = "cannot-assess"

SEVERITY_ERROR = "error"
SEVERITY_INFO = "info"
SEVERITY_CANNOT_ASSESS = "cannot-assess"

EXIT_CODES = {STATUS_OK: 0, STATUS_NOT_OK: 1, STATUS_CANNOT_ASSESS: 2}

# The org view summarises these declarations and nothing else. It is listed in
# every report so a reader can tell a projection from a source of truth.
SOURCE_OF_TRUTH = (
    "governance/rollup/pilot/org.yaml (tenant hierarchy)",
    "the per-repo fleet inventory declared for each repo in the org",
)

# Money is compared at cent precision: a spend equal to its ceiling is at
# ceiling, not over it, and a ceiling of 0 means "no spend permitted".
CENT = 0.01


def money(value: float) -> float:
    """Round a monetary amount to cents for reporting and comparison."""
    return round(float(value) + 0.0, 2)


class InputUnreadable(Exception):
    """An input could not be read at all (absent, unreadable, not decodable)."""


@dataclass(frozen=True)
class Finding:
    """One computed observation about the hierarchy or the aggregate."""

    code: str
    severity: str
    subject: str
    message: str
    remediation: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "code": self.code,
            "severity": self.severity,
            "subject": self.subject,
            "message": self.message,
        }
        if self.remediation:
            d["remediation"] = self.remediation
        return d


def finding(
    code: str, severity: str, subject: str, message: str, remediation: str = ""
) -> Finding:
    return Finding(code, severity, subject, message, remediation)


@dataclass(frozen=True)
class SmeFact:
    """One SME's declared week inside one repo.

    The SME's identity in the roll-up is ``repo#id`` and never the bare id: two
    repos may both run a ``qa-sme``, and merging them would let one repo's spend
    hide inside another's total.
    """

    repo: str
    tenant: str
    id: str
    persona_tenant: str
    ceiling_usd: float
    spend_usd: float
    capacity_hours: float
    engaged_hours: float
    dispatched: int
    closed: int

    @property
    def ref(self) -> str:
        return "%s#%s" % (self.repo, self.id)

    @property
    def over_ceiling(self) -> bool:
        return money(self.spend_usd) > money(self.ceiling_usd)

    @property
    def at_ceiling(self) -> bool:
        return money(self.spend_usd) == money(self.ceiling_usd)

    @property
    def excess_usd(self) -> float:
        if not self.over_ceiling:
            return 0.0
        return money(money(self.spend_usd) - money(self.ceiling_usd))

    @property
    def utilization(self) -> Optional[float]:
        """Engaged / capacity, or ``None`` when there is no capacity to divide by."""
        if self.capacity_hours <= 0:
            return None
        return self.engaged_hours / self.capacity_hours

    def to_dict(self) -> Dict[str, Any]:
        utilization = self.utilization
        return {
            "ref": self.ref,
            "repo": self.repo,
            "tenant": self.tenant,
            "id": self.id,
            "persona_tenant": self.persona_tenant,
            "spend_usd": money(self.spend_usd),
            "weekly_spend_ceiling_usd": money(self.ceiling_usd),
            "over_ceiling": self.over_ceiling,
            "at_ceiling": self.at_ceiling,
            "excess_usd": self.excess_usd,
            "utilization": None if utilization is None else round(utilization, 4),
            "dispatched": self.dispatched,
            "closed": self.closed,
        }


@dataclass(frozen=True)
class RepoFleet:
    """One repo's declared fleet for one window."""

    repo: str
    tenant: str
    source: str
    window: Tuple[str, str]
    smes: Tuple[SmeFact, ...]
    drift_checks: int
    drift_findings: int
    digest: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "repo": self.repo,
            "tenant": self.tenant,
            "source": self.source,
            "window": {"start": self.window[0], "end": self.window[1]},
            "digest": self.digest,
            "smes": [s.to_dict() for s in self.smes],
            "drift": {"checks": self.drift_checks, "findings": self.drift_findings},
        }


@dataclass(frozen=True)
class Org:
    """The declared hierarchy: one enterprise, its tenants, and their repos."""

    enterprise_id: str
    enterprise_name: str
    enterprise_ceiling_usd: float
    source: str
    tenants: Tuple[Tuple[str, str, float, Tuple[str, ...]], ...]
    digest: str

    @property
    def tenant_ids(self) -> Tuple[str, ...]:
        return tuple(t[0] for t in self.tenants)

    def repos_of(self, tenant_id: str) -> Tuple[str, ...]:
        for tid, _name, _ceiling, repos in self.tenants:
            if tid == tenant_id:
                return repos
        return ()

    @property
    def all_repos(self) -> Tuple[str, ...]:
        out: List[str] = []
        for _tid, _name, _ceiling, repos in self.tenants:
            out.extend(repos)
        return tuple(out)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.enterprise_id,
            "name": self.enterprise_name,
            "weekly_spend_ceiling_usd": money(self.enterprise_ceiling_usd),
            "source": self.source,
            "digest": self.digest,
            "tenants": [
                {
                    "id": tid,
                    "name": name,
                    "weekly_spend_ceiling_usd": money(ceiling),
                    "repos": list(repos),
                }
                for tid, name, ceiling, repos in self.tenants
            ],
        }


@dataclass(frozen=True)
class SpendView:
    """Spend rolled up over a scope, measured against that scope's ceiling.

    Two different overspends are reported and never conflated:

    * ``excess_usd`` / ``over`` are the **scope's** breach — what this tenant or
      the enterprise spent past its own ceiling. It is ``max(0, total − ceiling)``,
      never a clamp: ``total_usd`` keeps the real figure either way.
    * ``smes_over_ceiling`` names the **SMEs** that breached their own ceilings
      inside the scope, and ``smes_at_ceiling`` the ones sitting exactly on it.
      A scope can be within its ceiling while an SME inside it is over, and that
      is a fact about the SME, not about the tenant.
    """

    assessed: bool
    total_usd: float
    ceiling_usd: float
    excess_usd: float
    over: bool
    smes_over_ceiling: Tuple[str, ...]
    smes_at_ceiling: Tuple[str, ...]
    unassessable: Tuple[str, ...]

    @property
    def headroom_usd(self) -> float:
        return money(max(0.0, money(self.ceiling_usd) - money(self.total_usd)))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "assessed": self.assessed,
            "total_usd": money(self.total_usd),
            "ceiling_usd": money(self.ceiling_usd),
            "excess_usd": money(self.excess_usd),
            "over": self.over,
            "headroom_usd": self.headroom_usd,
            "smes_over_ceiling": list(self.smes_over_ceiling),
            "smes_at_ceiling": list(self.smes_at_ceiling),
            "unassessable": list(self.unassessable),
        }


@dataclass(frozen=True)
class RatioView:
    """A numerator/denominator metric that is ``None`` when it cannot be formed."""

    assessed: bool
    numerator: float
    denominator: float
    ratio: Optional[float]
    unassessable: Tuple[str, ...]
    kind: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "assessed": self.assessed,
            "numerator": round(self.numerator, 2),
            "denominator": round(self.denominator, 2),
            "ratio": None if self.ratio is None else round(self.ratio, 4),
            "unassessable": list(self.unassessable),
            "kind": self.kind,
        }


@dataclass(frozen=True)
class TenantRollup:
    id: str
    name: str
    ceiling_usd: float
    repos: Tuple[str, ...]
    missing_repos: Tuple[str, ...]
    smes: Tuple[SmeFact, ...]
    spend: SpendView
    utilization: RatioView
    closure: RatioView
    drift: RatioView

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "weekly_spend_ceiling_usd": money(self.ceiling_usd),
            "repos": list(self.repos),
            "missing_repos": list(self.missing_repos),
            "sme_inventory": {
                "count": len(self.smes),
                "by_repo": _sme_ids_by_repo(self.smes),
            },
            "spend": self.spend.to_dict(),
            "utilization": self.utilization.to_dict(),
            "closure": self.closure.to_dict(),
            "drift": self.drift.to_dict(),
        }


@dataclass(frozen=True)
class EnterpriseRollup:
    id: str
    name: str
    ceiling_usd: float
    repos: Tuple[str, ...]
    missing_repos: Tuple[str, ...]
    smes: Tuple[SmeFact, ...]
    spend: SpendView
    utilization: RatioView
    closure: RatioView
    drift: RatioView
    tenants: Tuple[TenantRollup, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "weekly_spend_ceiling_usd": money(self.ceiling_usd),
            "repos": list(self.repos),
            "missing_repos": list(self.missing_repos),
            "sme_inventory": {
                "count": len(self.smes),
                "by_repo": _sme_ids_by_repo(self.smes),
            },
            "spend": self.spend.to_dict(),
            "utilization": self.utilization.to_dict(),
            "closure": self.closure.to_dict(),
            "drift": self.drift.to_dict(),
            "tenants": [t.to_dict() for t in self.tenants],
        }


@dataclass(frozen=True)
class RollupReport:
    """The org view. Derived, disposable, and reproducible from the inputs."""

    org: Org
    enterprise: EnterpriseRollup
    fleets: Tuple[RepoFleet, ...]
    findings: Tuple[Finding, ...]
    inputs: Tuple[Tuple[str, str, str], ...]  # (kind, path, sha256), ordered
    schema_path: str
    schema_digest: str
    generated_from: Tuple[str, ...] = SOURCE_OF_TRUTH
    projection: bool = True
    persisted: bool = False
    report_schema: str = REPORT_SCHEMA

    @property
    def status(self) -> str:
        if any(f.severity == SEVERITY_CANNOT_ASSESS for f in self.findings):
            return STATUS_CANNOT_ASSESS
        if any(f.severity == SEVERITY_ERROR for f in self.findings):
            return STATUS_NOT_OK
        return STATUS_OK

    @property
    def exit_code(self) -> int:
        return EXIT_CODES[self.status]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": self.report_schema,
            "projection": self.projection,
            "persisted": self.persisted,
            "generated_from": list(self.generated_from),
            "status": self.status,
            "exit_code": self.exit_code,
            "org": self.org.to_dict(),
            "enterprise": self.enterprise.to_dict(),
            "repos": [f.to_dict() for f in self.fleets],
            "findings": [f.to_dict() for f in self.findings],
            "inputs": [
                {"kind": kind, "path": path, "sha256": digest}
                for kind, path, digest in self.inputs
            ],
            "schema_file": {"path": self.schema_path, "sha256": self.schema_digest},
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)


def file_digest(path: Path) -> str:
    """sha256 of the file's bytes — the input's identity in the report."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# -- aggregation --------------------------------------------------------------


def _sme_ids_by_repo(smes: Sequence[SmeFact]) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for sme in smes:
        out.setdefault(sme.repo, []).append(sme.id)
    return {repo: sorted(ids) for repo, ids in sorted(out.items())}


def _spend_view(
    smes: Sequence[SmeFact], ceiling_usd: float, unassessable: Sequence[str]
) -> SpendView:
    total = money(sum(money(s.spend_usd) for s in smes))
    ceiling = money(ceiling_usd)
    excess = money(max(0.0, money(total - ceiling)))
    return SpendView(
        assessed=not unassessable,
        total_usd=total,
        ceiling_usd=ceiling,
        excess_usd=excess,
        over=excess > 0,
        smes_over_ceiling=tuple(s.ref for s in smes if s.over_ceiling),
        smes_at_ceiling=tuple(s.ref for s in smes if s.at_ceiling),
        unassessable=tuple(unassessable),
    )


def _ratio_view(
    numerator: float,
    denominator: float,
    unassessable: Sequence[str],
    kind: str,
) -> RatioView:
    if denominator <= 0:
        return RatioView(
            assessed=False,
            numerator=numerator,
            denominator=denominator,
            ratio=None,
            unassessable=tuple(unassessable),
            kind=kind,
        )
    return RatioView(
        assessed=not unassessable,
        numerator=numerator,
        denominator=denominator,
        ratio=numerator / denominator,
        unassessable=tuple(unassessable),
        kind=kind,
    )


def _utilization_view(smes: Sequence[SmeFact], kind: str) -> RatioView:
    """Aggregate utilisation: engaged hours over capacity hours.

    An SME with no declared capacity has no utilisation, so it is *excluded*
    from both halves of the ratio and named in ``unassessable``: charging its
    hours to the numerator while it contributes no denominator would inflate
    the org view.
    """
    usable = [s for s in smes if s.utilization is not None]
    unassessable = [s.ref for s in smes if s.utilization is None]
    engaged = sum(s.engaged_hours for s in usable)
    capacity = sum(s.capacity_hours for s in usable)
    return _ratio_view(engaged, capacity, unassessable, kind)


def _tenant_scope(
    org: Org, tenant_id: str, fleets: Sequence[RepoFleet], missing: Sequence[str]
) -> Tuple[TenantRollup, List[Finding]]:
    _tid, name, ceiling, repos = next(t for t in org.tenants if t[0] == tenant_id)
    in_scope = [f for f in fleets if f.tenant == tenant_id]
    smes = tuple(s for f in sorted(in_scope, key=lambda f: f.repo) for s in f.smes)

    findings: List[Finding] = []
    spend = _spend_view(smes, ceiling, missing)
    if spend.over:
        findings.append(
            finding(
                "TENANT_OVER_CEILING",
                SEVERITY_ERROR,
                tenant_id,
                "tenant %s spent $%.2f against a $%.2f weekly ceiling, over by $%.2f (%d SME(s) "
                "inside it are over their own ceilings)"
                % (tenant_id, spend.total_usd, spend.ceiling_usd, spend.excess_usd,
                   len(spend.smes_over_ceiling)),
                "reduce or re-declare the tenant ceiling; the aggregate is not clamped",
            )
        )

    utilization = _utilization_view(smes, "engaged_hours_per_capacity_hour")
    if utilization.unassessable:
        findings.append(
            finding(
                "UTILIZATION_UNASSESSABLE",
                SEVERITY_CANNOT_ASSESS,
                tenant_id,
                "tenant %s cannot assess utilisation for %d SME(s) with no declared capacity: %s"
                % (tenant_id, len(utilization.unassessable), ", ".join(utilization.unassessable)),
                "declare capacity_hours for every SME in the scope",
            )
        )

    dispatched = sum(s.dispatched for s in smes)
    closed = sum(s.closed for s in smes)
    closure = _ratio_view(closed, dispatched, (), "closed_per_dispatched")
    if closure.ratio is None:
        findings.append(
            finding(
                "CLOSURE_UNASSESSABLE",
                SEVERITY_CANNOT_ASSESS,
                tenant_id,
                "tenant %s declared no dispatched work, so its closure rate cannot be formed"
                % tenant_id,
                "declare dispatched/closed counts for the window",
            )
        )

    drift_checks = sum(f.drift_checks for f in in_scope if f.repo not in missing)
    drift_findings = sum(f.drift_findings for f in in_scope if f.repo not in missing)
    drift_unassessable = [f.repo for f in in_scope if f.drift_checks == 0]
    drift = _ratio_view(drift_findings, drift_checks, drift_unassessable, "drift_findings_per_check")
    if drift.ratio is None:
        findings.append(
            finding(
                "DRIFT_UNASSESSABLE",
                SEVERITY_CANNOT_ASSESS,
                tenant_id,
                "tenant %s has no drift checks in scope, so its drift rate cannot be formed"
                % tenant_id,
                "record drift checks/findings for the window",
            )
        )

    rollup = TenantRollup(
        id=tenant_id,
        name=name,
        ceiling_usd=ceiling,
        repos=repos,
        missing_repos=tuple(missing),
        smes=smes,
        spend=spend,
        utilization=utilization,
        closure=closure,
        drift=drift,
    )
    return rollup, findings


def _scrutinise_fleet(fleet: RepoFleet) -> Tuple[RepoFleet, List[Finding]]:
    """Check one repo's fleet and return the facts that may be aggregated.

    A duplicated SME id is reported and the repeat is *excluded*: the first
    declaration is the repo's fact, and counting the repeat would inflate the
    org view by an amount nobody declared. The finding names what was dropped,
    so the exclusion is visible rather than silent.
    """
    findings: List[Finding] = []
    kept: List[SmeFact] = []
    seen: List[str] = []

    for sme in fleet.smes:
        if sme.id in seen:
            findings.append(
                finding(
                    "SME_DUPLICATE",
                    SEVERITY_ERROR,
                    sme.ref,
                    "SME %s is declared twice in %s; the repeat is excluded from the aggregate "
                    "so its spend is not counted twice" % (sme.id, fleet.repo),
                    "merge the duplicate declarations into one",
                )
            )
            continue
        seen.append(sme.id)
        kept.append(sme)

        if sme.over_ceiling:
            findings.append(
                finding(
                    "SPEND_OVER_CEILING",
                    SEVERITY_ERROR,
                    sme.ref,
                    "%s spent $%.2f against a $%.2f weekly ceiling, over by $%.2f"
                    % (sme.ref, money(sme.spend_usd), money(sme.ceiling_usd), sme.excess_usd),
                    "reduce the SME's spend or raise the declared ceiling deliberately",
                )
            )
        elif sme.at_ceiling:
            findings.append(
                finding(
                    "SPEND_AT_CEILING",
                    SEVERITY_INFO,
                    sme.ref,
                    "%s spent exactly its $%.2f weekly ceiling"
                    % (sme.ref, money(sme.ceiling_usd)),
                    "no action: at ceiling is within budget, and is reported so the margin "
                    "is visible",
                )
            )

        if sme.utilization is None:
            findings.append(
                finding(
                    "UTILIZATION_UNASSESSABLE",
                    SEVERITY_CANNOT_ASSESS,
                    sme.ref,
                    "%s declares no capacity, so its utilisation cannot be formed" % sme.ref,
                    "declare capacity_hours for the SME",
                )
            )
        elif sme.utilization > 1.0:
            findings.append(
                finding(
                    "UTILIZATION_OVER_CAPACITY",
                    SEVERITY_ERROR,
                    sme.ref,
                    "%s engaged %.2fh against %.2fh of declared capacity (%.0f%%)"
                    % (sme.ref, sme.engaged_hours, sme.capacity_hours, sme.utilization * 100),
                    "the ratio is reported as measured, never clamped to 100%; reconcile "
                    "capacity or engagement",
                )
            )

        if sme.closed > sme.dispatched:
            findings.append(
                finding(
                    "CLOSURE_INCONSISTENT",
                    SEVERITY_ERROR,
                    sme.ref,
                    "%s closed %d of %d dispatched items" % (sme.ref, sme.closed, sme.dispatched),
                    "correct the declared counts; a closure rate above 100% is not a rate",
                )
            )

    return replace(fleet, smes=tuple(kept)), findings


def project(org: Org, fleets: Sequence[RepoFleet], **meta: Any) -> RollupReport:
    """Compute the enterprise view over the declared hierarchy.

    Pure: the inputs are immutable facts, nothing is written anywhere, and no
    state survives the call. The org's declared repo list defines the scope; an
    inventory the org does not list is a ``REPO_UNKNOWN`` finding rather than an
    extra repo the org silently gains.
    """
    findings: List[Finding] = []

    by_repo: Dict[str, RepoFleet] = {}
    for fleet in fleets:
        if fleet.repo in by_repo:
            findings.append(
                finding(
                    "REPO_DUPLICATED",
                    SEVERITY_ERROR,
                    fleet.repo,
                    "repo %s declared two fleet inventories; the roll-up would double count it"
                    % fleet.repo,
                    "declare exactly one inventory per repo per window",
                )
            )
            continue
        by_repo[fleet.repo] = fleet

    declared_repos = list(org.all_repos)
    seen: List[str] = []
    for repo in declared_repos:
        if repo in seen:
            findings.append(
                finding(
                    "REPO_DUPLICATED",
                    SEVERITY_ERROR,
                    repo,
                    "repo %s is declared by more than one tenant; the hierarchy is a tree, not a "
                    "graph, and the repo is counted under its first tenant only. The tenant that "
                    "lists it second has no facts for it, so its scope is unassessable" % repo,
                    "keep every repo under exactly one tenant so tenant totals stay disjoint",
                )
            )
        seen.append(repo)

    in_scope: List[RepoFleet] = []
    seen_in_scope: List[str] = []
    for repo in declared_repos:
        if repo in seen_in_scope:
            # Declared by a second tenant: already reported as REPO_DUPLICATED, and
            # already counted once. Adding it again would inflate every total above.
            continue
        seen_in_scope.append(repo)
        fleet = by_repo.get(repo)
        if fleet is None:
            findings.append(
                finding(
                    "INVENTORY_MISSING",
                    SEVERITY_CANNOT_ASSESS,
                    repo,
                    "repo %s is declared in the org but has no fleet inventory, so its share of "
                    "the aggregate is unknown" % repo,
                    "declare governance/rollup/<repo>/ or remove the repo from the org",
                )
            )
            continue
        if fleet.tenant not in org.tenant_ids:
            findings.append(
                finding(
                    "TENANT_UNKNOWN",
                    SEVERITY_ERROR,
                    repo,
                    "inventory %s declares tenant %s, which the org does not define"
                    % (repo, fleet.tenant),
                    "add the tenant to the org or correct the inventory",
                )
            )
            continue
        if repo not in org.repos_of(fleet.tenant):
            findings.append(
                finding(
                    "TENANT_MISMATCH",
                    SEVERITY_ERROR,
                    repo,
                    "inventory %s files itself under tenant %s, but the org places it under a "
                    "different tenant" % (repo, fleet.tenant),
                    "align the inventory's tenant with the org declaration",
                )
            )
            continue
        in_scope.append(fleet)

    # Every declared repo that did not reach the aggregate leaves a hole in it,
    # whichever reason kept it out — the reasons differ, the consequence does not.
    in_scope_repos = {fleet.repo for fleet in in_scope}
    missing: List[str] = [
        repo for repo in dict.fromkeys(declared_repos) if repo not in in_scope_repos
    ]

    for repo, fleet in sorted(by_repo.items()):
        if repo not in declared_repos:
            findings.append(
                finding(
                    "REPO_UNKNOWN",
                    SEVERITY_ERROR,
                    repo,
                    "an inventory exists for %s but the org does not declare it, so it is out of "
                    "scope for the enterprise view" % repo,
                    "declare the repo under a tenant in the org, or remove the inventory",
                )
            )
            continue
        if not fleet.smes:
            findings.append(
                finding(
                    "FLEET_EMPTY",
                    SEVERITY_CANNOT_ASSESS,
                    repo,
                    "repo %s declares an empty fleet, so its inventory, utilisation and closure "
                    "are unknown" % repo,
                    "declare the repo's SME facts for the window",
                )
            )

    cleaned: List[RepoFleet] = []
    for fleet in in_scope:
        clean, fleet_findings = _scrutinise_fleet(fleet)
        findings.extend(fleet_findings)
        cleaned.append(clean)
    in_scope = cleaned

    tenant_rollups: List[TenantRollup] = []
    for tid, _name, _ceiling, _repos in org.tenants:
        tenant_missing = [r for r in missing if r in org.repos_of(tid)]
        rollup, tenant_findings = _tenant_scope(org, tid, in_scope, tenant_missing)
        tenant_rollups.append(rollup)
        findings.extend(tenant_findings)

    smes = tuple(s for t in tenant_rollups for s in t.smes)
    enterprise_spend = _spend_view(smes, org.enterprise_ceiling_usd, missing)
    if enterprise_spend.over:
        findings.append(
            finding(
                "ENTERPRISE_OVER_CEILING",
                SEVERITY_ERROR,
                org.enterprise_id,
                "enterprise %s spent $%.2f against a $%.2f weekly ceiling, over by $%.2f (%d "
                "SME(s) inside it are over their own ceilings)"
                % (
                    org.enterprise_id,
                    enterprise_spend.total_usd,
                    enterprise_spend.ceiling_usd,
                    enterprise_spend.excess_usd,
                    len(enterprise_spend.smes_over_ceiling),
                ),
                "reduce or re-declare the enterprise ceiling; the aggregate is not clamped",
            )
        )

    enterprise = EnterpriseRollup(
        id=org.enterprise_id,
        name=org.enterprise_name,
        ceiling_usd=org.enterprise_ceiling_usd,
        repos=org.all_repos,
        missing_repos=tuple(missing),
        smes=smes,
        spend=enterprise_spend,
        utilization=_utilization_view(smes, "engaged_hours_per_capacity_hour"),
        closure=_ratio_view(
            sum(s.closed for s in smes), sum(s.dispatched for s in smes), (), "closed_per_dispatched"
        ),
        drift=_ratio_view(
            sum(f.drift_findings for f in in_scope if f.repo not in missing),
            sum(f.drift_checks for f in in_scope if f.repo not in missing),
            [f.repo for f in in_scope if f.drift_checks == 0],
            "drift_findings_per_check",
        ),
        tenants=tuple(tenant_rollups),
    )

    ordered = tuple(
        sorted(
            findings,
            key=lambda f: (
                {"cannot-assess": 0, "error": 1, "warning": 2, "info": 3}[f.severity],
                f.subject,
                f.code,
            ),
        )
    )
    return RollupReport(
        org=org,
        enterprise=enterprise,
        fleets=tuple(sorted(in_scope, key=lambda f: f.repo)),
        findings=ordered,
        inputs=meta.get("inputs", ()),
        schema_path=meta.get("schema_path", ""),
        schema_digest=meta.get("schema_digest", ""),
    )


def summarise(report: RollupReport) -> Iterable[str]:
    """Human-readable lines for the CLI: computed numbers, never narration."""
    enterprise = report.enterprise
    yield "enterprise %s (%s): %d tenant(s), %d repo(s), %d SME(s)" % (
        enterprise.id,
        enterprise.name,
        len(enterprise.tenants),
        len(enterprise.repos),
        len(enterprise.smes),
    )
    yield "  spend: $%.2f of $%.2f ceiling (headroom $%.2f, excess $%.2f)%s" % (
        enterprise.spend.total_usd,
        enterprise.spend.ceiling_usd,
        enterprise.spend.headroom_usd,
        enterprise.spend.excess_usd,
        "" if enterprise.spend.assessed else " [INCOMPLETE]",
    )
    use = enterprise.utilization
    yield "  utilization: %s (%s)" % (
        "cannot assess" if use.ratio is None else "%.2f%%" % (use.ratio * 100),
        "%.1fh engaged / %.1fh capacity" % (use.numerator, use.denominator),
    )
    closure = enterprise.closure
    yield "  closure: %s (%d closed / %d dispatched)" % (
        "cannot assess" if closure.ratio is None else "%.2f%%" % (closure.ratio * 100),
        int(closure.numerator),
        int(closure.denominator),
    )
    drift = enterprise.drift
    yield "  drift: %s (%d finding(s) / %d check(s))" % (
        "cannot assess" if drift.ratio is None else "%.2f%%" % (drift.ratio * 100),
        int(drift.numerator),
        int(drift.denominator),
    )
    for tenant in enterprise.tenants:
        yield "tenant %s: %d repo(s), $%.2f of $%.2f, closure %s, drift %s" % (
            tenant.id,
            len(tenant.repos),
            tenant.spend.total_usd,
            tenant.spend.ceiling_usd,
            "cannot assess" if tenant.closure.ratio is None else "%.2f%%" % (tenant.closure.ratio * 100),
            "cannot assess" if tenant.drift.ratio is None else "%.2f%%" % (tenant.drift.ratio * 100),
        )
    for repo in report.fleets:
        yield "  repo %s: %d SME(s), $%.2f, %d closed / %d dispatched" % (
            repo.repo,
            len(repo.smes),
            money(sum(money(s.spend_usd) for s in repo.smes)),
            sum(s.closed for s in repo.smes),
            sum(s.dispatched for s in repo.smes),
        )
    if report.findings:
        yield "findings:"
        for item in report.findings:
            yield "  [%s] %s — %s" % (item.severity, item.subject, item.message)
    else:
        yield "findings: none"
