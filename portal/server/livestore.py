"""portal.server.livestore — read-only adapters over the LIVE registry + telemetry.

The console is a *projection* over the control-plane entities, never a second
source of truth (CMR portal doctrine). This module is the single seam that
reads the control plane's **real** stores on this checkout, so the console's
roster is the real registry and its budgets/quotas/usage are the real
telemetry — never hardcoded demo rows (issue #348):

* :class:`RegistrySnapshot` reads the registry lane's frozen artifacts —
  ``registry/profiles/catalog.yaml`` (the closed vocabulary, incl. the
  tier -> model ladder) and ``registry/profiles/seeds/<id>.<version>.yaml``
  (the immutable AgentProfile seeds). A reference to a profile the registry
  does not publish **fails closed**: the console can never project an agent
  the registry does not know.
  Which *revision* of a profile is live is decided in exactly one place,
  :func:`resolve_seed_path` — the highest published version, in semantic
  order. ``Path.glob`` yields the directory's *filesystem* (``scandir``) order,
  which differs between checkouts of the same commit, so a caller that picked
  the first match — or restated the rule for itself — compares the projection
  against an arbitrary revision and goes red on a correct build depending only
  on where the checkout lives (issue #794, #642 before it).
* :class:`TelemetrySnapshot` reads the telemetry lane's artifacts —
  ``telemetry/budgets/config/policies.yaml`` (per-tenant cost/token budgets),
  ``telemetry/metering/config/budgets.yaml`` (per-tenant daily token limits),
  ``telemetry/budgets/config/quotas.yaml`` (plan + per-tenant quota overrides)
  and the durable metering feed (append-only ``UsageRecord`` JSONL) for
  usage/cost. A tenant the telemetry lane declares no policy for reads as
  **zero spend against no declared budget** — an honest "nothing declared",
  never an invented number.

Everything here is read-only and pure; nothing writes to the consumed stores.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Optional

import yaml

from telemetry.metering.model import UsageRecord

#: The console's default metering feed: the durable ``UsageRecord`` JSONL the
#: telemetry lane writes, under a runtime directory that is never committed.
DEFAULT_USAGE_STORE = Path(".telemetry") / "metering.jsonl"


class RegistryDriftError(RuntimeError):
    """A roster referenced a profile the live registry does not publish."""


class TelemetryUnavailableError(RuntimeError):
    """A telemetry artifact the console consumes is missing or malformed."""


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #
# -- seed resolution: the ONE rule for "which revision of a profile is live" -- #
#: The published seed filename grammar, ``<id>.<version>.yaml``. The registry
#: lane owns the grammar (``registry/profiles/validate.py``: ``SEED_FILE_RE`` /
#: ``VERSION_RE``); this mirrors it rather than reaching into a sibling lane's
#: script module, and the resolver's tests pin the two to each other.
SEED_SUFFIX = ".yaml"

#: ``<id>.<version>.yaml`` with the registry's own id and version alphabets. The
#: version must be matched, never split on ``"."``: ``1.10.0`` has two dots in
#: it, and taking the last component reads it as ``"0"`` — which silently
#: degrades the revision comparison to the filename tiebreak.
SEED_FILENAME_RE = re.compile(
    r"^(?P<id>[a-z][a-z0-9-]*)\."
    r"(?P<version>[0-9]+\.[0-9]+\.[0-9]+)"
    + re.escape(SEED_SUFFIX)
    + r"$"
)

#: A published semantic version, exactly as the registry's validator defines it.
SEMVER_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")

#: The order of one published revision: semver rank, numeric version, version.
RevisionKey = tuple[int, tuple[int, int, int], str]


def seed_version(filename: str) -> str:
    """The ``<version>`` component of a ``<id>.<version>.yaml`` seed filename."""
    match = SEED_FILENAME_RE.match(filename)
    if match:
        return match.group("version")
    # Not a published filename — the registry's validator refuses those, so this
    # is defensive: take the last dot-separated component, which keeps the order
    # below total whatever is on disk.
    stem = filename[: -len(SEED_SUFFIX)] if filename.endswith(SEED_SUFFIX) else filename
    return stem.rpartition(".")[2]


def revision_key(version: str) -> RevisionKey:
    """A total order over versions in which the **highest revision is last**.

    Semantic order, never the lexicographic order of the filename: ``1.10.0``
    outranks ``1.9.0``, which plain string order has backwards (``"9" > "1"``).
    A version that is not semantic ``X.Y.Z`` — the registry's validator refuses
    those on a real seed, so this is defensive — sorts *below* every semantic
    one and is ordered among its peers by the version string. The order is
    therefore total: no two callers can disagree, and neither can two
    directories whose entries were written in a different order.
    """
    if SEMVER_RE.match(version):
        major, minor, patch = (int(part) for part in version.split("."))
        return (1, (major, minor, patch), version)
    return (0, (0, 0, 0), version)


def seed_selection_key(path: Path) -> tuple[RevisionKey, str]:
    """The **single** ordering rule over seed paths: revision, then filename.

    :func:`resolve_seed_path` and :meth:`RegistrySnapshot._load_seeds` both
    select the *greatest* path under this key, so the live revision of a
    profile is decided in one place. The filename is the final tiebreak, which
    makes the key total — two files publishing the same revision still resolve
    to the same one whichever way the directory is read.
    """
    return (revision_key(seed_version(path.name)), path.name)


def seed_paths(
    seeds_dir: Path | str, pattern: str = f"*{SEED_SUFFIX}"
) -> tuple[Path, ...]:
    """Every seed under ``seeds_dir`` matching ``pattern``, revision-ascending.

    Sorted by :func:`seed_selection_key`, so the sequence is a function of the
    files present and never of the order ``Path.glob`` happened to yield them
    in (``scandir`` order differs between checkouts of the same commit).
    """
    found = sorted(Path(seeds_dir).glob(pattern), key=seed_selection_key)
    return tuple(found)


def resolve_seed_path(profile_id: str, seeds_dir: Path | str) -> Path:
    """The seed the live registry resolves for ``profile_id``: **highest revision**.

    This is the registry's own rule — of every published revision of a profile
    (``registry/profiles/seeds/<id>.<version>.yaml``), the highest is live — and
    it is the ONE resolver the console and its tests share. Callers must not
    restate it: a second copy (``next(glob(...))``, or ``sorted(glob(...))[-1]``,
    which is *string* order where this is *semantic* order) is exactly how the
    roster test drifted from the code under test and went red on a correct
    build in a long-lived worktree (issue #794, #642 before it).

    Raises :class:`RegistryDriftError` when the registry publishes no such
    profile, so a caller fails closed rather than asserting against nothing.
    """
    seeds = Path(seeds_dir)
    candidates = seed_paths(seeds, f"{profile_id}.*{SEED_SUFFIX}")
    if not candidates:
        raise RegistryDriftError(
            f"no AgentProfile seed for {profile_id!r} under {seeds}"
        )
    return max(candidates, key=seed_selection_key)


@dataclass(frozen=True)
class RegistryProfile:
    """A frozen AgentProfile seed reduced to the fields the console projects.

    ``model`` is the closed-vocabulary tier ladder resolved through
    ``catalog.yaml`` (e.g. ``HIGH`` -> ``pro``), so the console consumes the
    registry's own mapping instead of restating it.
    """

    id: str
    version: str
    owner: str
    capabilities: tuple[str, ...]
    model_tier: str
    model: str


class RegistrySnapshot:
    """Read-only view of the live AgentProfile registry (``registry/profiles``)."""

    def __init__(self, repo_root: Path | str) -> None:
        self.repo_root = Path(repo_root)
        profiles_dir = self.repo_root / "registry" / "profiles"
        self._seeds_dir = profiles_dir / "seeds"
        self._catalog_path = profiles_dir / "catalog.yaml"
        self._tier_model = self._load_tier_ladder(self._catalog_path)
        self._profiles = self._load_seeds(self._seeds_dir)

    # -- loading ------------------------------------------------------------
    @staticmethod
    def _load_yaml(path: Path) -> Any:
        if not path.is_file():
            raise TelemetryUnavailableError(f"missing registry artifact: {path}")
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return yaml.safe_load(handle) or {}
        except yaml.YAMLError as exc:  # pragma: no cover - defensive
            raise RegistryDriftError(f"unparseable registry artifact {path}: {exc}") from exc

    def _load_tier_ladder(self, catalog_path: Path) -> dict[str, str]:
        catalog = self._load_yaml(catalog_path)
        tiers = catalog.get("tiers") or {}
        ladder = {
            str(tier): str(entry.get("model", ""))
            for tier, entry in tiers.items()
            if isinstance(entry, dict)
        }
        if not ladder:
            raise RegistryDriftError(
                f"closed-vocabulary catalog {catalog_path} declares no tiers"
            )
        return ladder

    def _load_seeds(self, seeds_dir: Path) -> dict[str, RegistryProfile]:
        # Read every seed once, group the paths by the id the seed declares, and
        # let the shared rule pick each profile's live revision: the HIGHEST
        # published one (``seed_selection_key``). Selecting through one rule —
        # rather than trusting the order the directory happens to be written in
        # — is what makes the projection (and every test that reads it) a
        # function of the registry alone, not of where the checkout lives.
        parsed: dict[Path, Any] = {}
        by_id: dict[str, list[Path]] = {}
        for path in seed_paths(seeds_dir):
            data = self._load_yaml(path)
            parsed[path] = data
            profile_id = str(data.get("id") or "")
            if profile_id:
                by_id.setdefault(profile_id, []).append(path)

        profiles: dict[str, RegistryProfile] = {}
        for profile_id, candidates in by_id.items():
            path = max(candidates, key=seed_selection_key)
            data = parsed[path]
            tier = str(data.get("defaultModelTier") or "")
            capabilities = tuple(str(cap) for cap in (data.get("capabilitySet") or ()))
            if not capabilities:
                raise RegistryDriftError(
                    f"profile seed {path} declares no capabilitySet"
                )
            profiles[profile_id] = RegistryProfile(
                id=profile_id,
                version=str(data.get("version") or ""),
                owner=str(data.get("owner") or ""),
                capabilities=capabilities,
                model_tier=tier,
                model=self.tier_model(tier),
            )
        if not profiles:
            raise RegistryDriftError(f"no AgentProfile seeds found under {seeds_dir}")
        return profiles

    # -- reads --------------------------------------------------------------
    def tier_model(self, tier: str) -> str:
        """Resolve a closed-vocabulary tier id to its model (fail closed)."""
        model = self._tier_model.get(tier)
        if not model:
            raise RegistryDriftError(
                f"unknown model tier {tier!r} (not in {self._catalog_path})"
            )
        return model

    def has(self, profile_id: str) -> bool:
        """Whether the live registry publishes this profile id."""
        return profile_id in self._profiles

    def profile_ids(self) -> tuple[str, ...]:
        """Every profile id the live registry publishes, sorted."""
        return tuple(sorted(self._profiles))

    def profile(self, profile_id: str) -> RegistryProfile:
        """Resolve a profile id, or fail closed when the registry lacks it."""
        try:
            return self._profiles[profile_id]
        except KeyError:
            raise RegistryDriftError(
                f"roster references profile {profile_id!r}, which the live "
                f"registry does not publish under {self._seeds_dir} "
                f"(published: {', '.join(self.profile_ids())})"
            ) from None


# --------------------------------------------------------------------------- #
# Telemetry
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class TenantBudget:
    """The cost/token budget the telemetry lane declares for one tenant."""

    monthly_budget_usd: float = 0.0
    daily_token_limit: int = 0
    mode: str = ""
    plan: str = ""
    quota: Optional[dict[str, dict[str, Any]]] = None


@dataclass(frozen=True)
class UsagePoint:
    """One day/provider/model row of the tenant's real metered usage."""

    day: str
    vendor: str
    model: str
    calls: int
    tokens: int
    cost_usd: float


class TelemetrySnapshot:
    """Read-only view of the live telemetry budget/quota policy + metering feed."""

    def __init__(
        self,
        repo_root: Path | str,
        usage_store_path: Optional[Path | str] = None,
    ) -> None:
        self.repo_root = Path(repo_root)
        budgets_dir = self.repo_root / "telemetry" / "budgets" / "config"
        metering_dir = self.repo_root / "telemetry" / "metering" / "config"
        self._policies_path = budgets_dir / "policies.yaml"
        self._metering_budgets_path = metering_dir / "budgets.yaml"
        self._quotas_path = budgets_dir / "quotas.yaml"
        self._usage_store_path = (
            Path(usage_store_path)
            if usage_store_path is not None
            else self.repo_root / DEFAULT_USAGE_STORE
        )
        self._policies = self._load_yaml(self._policies_path)
        self._metering_budgets = self._load_yaml(self._metering_budgets_path)
        self._quotas = self._load_yaml(self._quotas_path)
        self._usage = self._load_usage(self._usage_store_path)

    # -- loading ------------------------------------------------------------
    @staticmethod
    def _load_yaml(path: Path) -> dict[str, Any]:
        if not path.is_file():
            raise TelemetryUnavailableError(f"missing telemetry artifact: {path}")
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return yaml.safe_load(handle) or {}
        except yaml.YAMLError as exc:  # pragma: no cover - defensive
            raise TelemetryUnavailableError(
                f"unparseable telemetry artifact {path}: {exc}"
            ) from exc

    @staticmethod
    def _load_usage(path: Path) -> list[UsageRecord]:
        """Read the durable metering feed (the canonical ``UsageRecord`` JSONL).

        Absent file = no usage metered yet, which is an honest empty feed; a
        corrupt line is skipped exactly as the metering store skips it.
        """
        if not path.is_file():
            return []
        records: list[UsageRecord] = []
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if payload.get("kind") != "usage":
                    continue
                try:
                    records.append(UsageRecord.from_dict(payload))
                except (KeyError, ValueError, TypeError):
                    continue
        return records

    # -- budget / quota policy ---------------------------------------------
    def _policy(self, tenant_id: str) -> dict[str, Any]:
        for entry in self._policies.get("policies") or ():
            if str(entry.get("tenantId")) == tenant_id:
                return entry
        return {}

    def _metering_limit(self, tenant_id: str) -> int:
        for entry in self._metering_budgets.get("policies") or ():
            if str(entry.get("tenantId")) == tenant_id:
                return int(entry.get("dailyTokenLimit") or 0)
        return 0

    def _tenant_quota_entry(self, tenant_id: str) -> dict[str, Any]:
        for entry in self._quotas.get("tenantQuotas") or ():
            if str(entry.get("tenantId")) == tenant_id:
                return entry
        return {}

    def budget(self, tenant_id: str) -> TenantBudget:
        """The declared budget + plan + effective quota for one tenant."""
        policy = self._policy(tenant_id)
        cost = policy.get("cost") or {}
        tokens = policy.get("tokens") or {}
        quota_entry = self._tenant_quota_entry(tenant_id)
        plan = str(quota_entry.get("plan") or "")
        return TenantBudget(
            monthly_budget_usd=float(cost.get("limitUsd") or 0.0),
            daily_token_limit=int(tokens.get("limit") or 0)
            or self._metering_limit(tenant_id),
            mode=str(policy.get("mode") or self._policies.get("defaultMode") or ""),
            plan=plan,
            quota=self.effective_quota(tenant_id),
        )

    def effective_quota(self, tenant_id: str) -> dict[str, dict[str, Any]]:
        """``planDefaults[plan]`` overlaid with the tenant's explicit overrides.

        Returns ``{}`` when the telemetry lane declares no plan for the tenant —
        the console then reports no quota rather than a fabricated one.
        """
        entry = self._tenant_quota_entry(tenant_id)
        plan = str(entry.get("plan") or "")
        if not plan:
            return {}
        plan_defaults = (self._quotas.get("planDefaults") or {}).get(plan) or {}
        effective: dict[str, dict[str, Any]] = {
            str(resource): dict(spec)
            for resource, spec in plan_defaults.items()
            if isinstance(spec, dict)
        }
        for resource, spec in (entry.get("quotas") or {}).items():
            if isinstance(spec, dict):
                effective[str(resource)] = dict(spec)
        return effective

    def plan(self, tenant_id: str) -> str:
        """The entitlement plan the telemetry lane declares for a tenant."""
        return str(self._tenant_quota_entry(tenant_id).get("plan") or "")

    def mode(self, tenant_id: str) -> str:
        """The rollout mode (``observe``/``enforce``) for a tenant."""
        return str(
            self._policy(tenant_id).get("mode")
            or self._policies.get("defaultMode")
            or ""
        )

    # -- usage / cost -------------------------------------------------------
    def usage_records(self, tenant_id: str) -> list[UsageRecord]:
        """Billable metered records for one tenant (real feed, nothing else)."""
        return [
            record
            for record in self._usage
            if record.tenant_id == tenant_id and record.billable
        ]

    def usage_totals(self, tenant_id: str) -> dict[str, Any]:
        """Billable calls / tokens / cost for one tenant, summed over the feed."""
        records = self.usage_records(tenant_id)
        return {
            "calls": sum(1 for _ in records),
            "tokens": sum(record.total_tokens for record in records),
            "cost_usd": round(
                sum(float(record.cost_usd or 0.0) for record in records), 4
            ),
        }

    def daily_tokens(self, tenant_id: str, day: Optional[str] = None) -> int:
        """Billable tokens metered for one tenant on a UTC day bucket."""
        bucket = day or datetime.now(UTC).strftime("%Y-%m-%d")
        return sum(
            record.total_tokens for record in self.usage_records(tenant_id)
            if record.ts[:10] == bucket
        )

    def daily_cost(self, tenant_id: str, day: Optional[str] = None) -> float:
        """Billable cost metered for one tenant on a UTC day bucket."""
        bucket = day or datetime.now(UTC).strftime("%Y-%m-%d")
        return round(
            sum(
                float(record.cost_usd or 0.0)
                for record in self.usage_records(tenant_id)
                if record.ts[:10] == bucket
            ),
            4,
        )

    def usage_series(self, tenant_id: str) -> list[UsagePoint]:
        """The tenant's usage rolled up per (day, provider, model)."""
        buckets: dict[tuple[str, str, str], dict[str, float]] = {}
        for record in self.usage_records(tenant_id):
            key = (record.ts[:10], record.provider or "", record.model or "")
            bucket = buckets.setdefault(key, {"calls": 0, "tokens": 0, "cost": 0.0})
            bucket["calls"] += 1
            bucket["tokens"] += record.total_tokens
            bucket["cost"] += float(record.cost_usd or 0.0)
        return [
            UsagePoint(
                day=day,
                vendor=vendor,
                model=model,
                calls=int(bucket["calls"]),
                tokens=int(bucket["tokens"]),
                cost_usd=round(bucket["cost"], 4),
            )
            for (day, vendor, model), bucket in sorted(buckets.items())
        ]
