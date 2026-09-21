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


---knowledge---
module_id: portal.server.livestore
system: portal
app: server
solution_class: pattern
patterns: [read-only-adapter, fail-closed, single-seam, content-addressed-selection]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [RegistrySnapshot, TelemetrySnapshot, resolve_seed_path, BoardSnapshot, load_board_rows]
invariants: "the console can never project an agent the registry does not know; nothing here writes to the consumed stores"
gotchas: "Path.glob yields filesystem order, so live-revision selection lives in exactly one place (resolve_seed_path), never restated"
related: ["#348", "#794"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, List, Mapping, Optional, Sequence

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


# --------------------------------------------------------------------------- #
# Fleet board (issue #880, EPIC #878 lane L1)
# --------------------------------------------------------------------------- #
# The console's fleet-board surface projects the SAME two files the fleet
# CLI/cron already treats as live state — ``.board/snapshot.json`` (the issue
# roster) and ``.board/claims.jsonl`` (the append-only claim/release/reap
# event log) — never a copy. Every row this module emits is validated against
# ``portal/schemas/fleet-board-row.schema.json`` before it is returned; a row
# that fails validation is refused **by name** (its issue number and the
# defect are reported) rather than served best-effort, so a malformed source
# document degrades the board honestly instead of silently.
#
# The schema validator here is intentionally a small, dependency-free subset
# (``type`` / ``required`` / ``properties`` / ``additionalProperties`` /
# ``enum`` / ``const`` / ``minimum`` / ``minLength``) — the same posture
# ``gateway/sme-routing/jsonschema_lite.py`` and ``guardrails/policy/
# schemas.py`` take for the identical reason: the platform's stdlib+PyYAML
# stack must stay offline, and a pillar does not reach into a sibling
# pillar's private validator module.

BOARD_ROW_SCHEMA = "ao.portal-fleet-board-row/v1"

#: Repo-root-relative locations of the source documents this surface reads.
DEFAULT_SNAPSHOT_PATH = Path(".board") / "snapshot.json"
DEFAULT_CLAIMS_PATH = Path(".board") / "claims.jsonl"
#: The row schema's own location (issue #880): the single source of truth for
#: what a served row may contain — this module names no field the schema
#: does not also declare.
BOARD_ROW_SCHEMA_PATH = Path("portal") / "schemas" / "fleet-board-row.schema.json"


class BoardRowInvalid(ValueError):
    """One board row failed schema validation, named by its issue number."""

    def __init__(self, identifier: str, errors: Sequence[str]) -> None:
        self.identifier = identifier
        self.errors = list(errors)
        super().__init__(
            f"board row {identifier!r} failed {BOARD_ROW_SCHEMA}: "
            + "; ".join(self.errors)
        )


def load_board_row_schema(repo_root: Path | str) -> dict[str, Any]:
    """Read ``portal/schemas/fleet-board-row.schema.json`` (fail closed)."""
    path = Path(repo_root) / BOARD_ROW_SCHEMA_PATH
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise TelemetryUnavailableError(
            f"missing fleet-board row schema: {path}"
        ) from exc
    except ValueError as exc:
        raise TelemetryUnavailableError(
            f"unparseable fleet-board row schema {path}: {exc}"
        ) from exc
    if not isinstance(document, dict):
        raise TelemetryUnavailableError(f"schema {path} is not a JSON object")
    return document


def _validate_row(row: Mapping[str, Any], schema: Mapping[str, Any]) -> List[str]:
    """Validate ``row`` against the (small, explicit) supported keyword subset."""
    errors: List[str] = []
    properties: Mapping[str, Any] = schema.get("properties") or {}
    required: Sequence[str] = schema.get("required") or ()
    additional = schema.get("additionalProperties", True)

    for name in required:
        if name not in row:
            errors.append(f"missing required field {name!r}")

    if additional is False:
        for name in row:
            if name not in properties:
                errors.append(f"unexpected field {name!r}")

    for name, subschema in properties.items():
        if name not in row:
            continue
        value = row[name]
        errors.extend(_validate_value(f"{name}", value, subschema))
    return errors


_TYPE_MAP = {
    "string": str,
    "integer": int,
    "array": list,
    "object": dict,
    "boolean": bool,
    "number": (int, float),
}


def _validate_value(path: str, value: Any, subschema: Mapping[str, Any]) -> List[str]:
    errors: List[str] = []
    expected_type = subschema.get("type")
    if expected_type is not None:
        py_type = _TYPE_MAP.get(expected_type)
        # bool is an int subclass in Python; an integer field must not accept
        # True/False (that would silently coerce a malformed row into "valid").
        if py_type is int and isinstance(value, bool):
            errors.append(f"{path}: expected integer, got boolean")
        elif py_type is not None and not isinstance(value, py_type):
            errors.append(f"{path}: expected {expected_type}, got {type(value).__name__}")
    if "const" in subschema and value != subschema["const"]:
        errors.append(f"{path}: expected constant {subschema['const']!r}, got {value!r}")
    if "enum" in subschema and value not in subschema["enum"]:
        errors.append(f"{path}: {value!r} is not one of {subschema['enum']!r}")
    if isinstance(value, str) and "minLength" in subschema:
        if len(value) < subschema["minLength"]:
            errors.append(f"{path}: shorter than minLength {subschema['minLength']}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in subschema and value < subschema["minimum"]:
            errors.append(f"{path}: {value} is below minimum {subschema['minimum']}")
    if isinstance(value, list) and isinstance(subschema.get("items"), Mapping):
        item_schema = subschema["items"]
        for index, item in enumerate(value):
            errors.extend(_validate_value(f"{path}[{index}]", item, item_schema))
    return errors


def _latest_claims(claims_path: Path) -> dict[int, dict[str, Any]]:
    """The most recent event per issue from the append-only claims log.

    Corrupt lines are skipped (mirrors ``TelemetrySnapshot._load_usage``'s
    honest-skip posture); a missing file reads as "nothing claimed yet".
    """
    latest: dict[int, dict[str, Any]] = {}
    if not claims_path.is_file():
        return latest
    with open(claims_path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            try:
                issue = int(event.get("issue"))
            except (TypeError, ValueError):
                continue
            latest[issue] = event
    return latest


@dataclass(frozen=True)
class BoardRowRejection:
    """One source row the schema refused, named rather than dropped silently."""

    identifier: str
    errors: tuple[str, ...]


@dataclass(frozen=True)
class BoardSnapshot:
    """The fleet board: every schema-valid row, plus every named rejection."""

    schema: str
    rows: tuple[dict[str, Any], ...]
    rejected: tuple[BoardRowRejection, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "rows": list(self.rows),
            "rejected": [
                {"identifier": item.identifier, "errors": list(item.errors)}
                for item in self.rejected
            ],
        }


def load_board_rows(
    repo_root: Path | str,
    *,
    snapshot_path: Optional[Path | str] = None,
    claims_path: Optional[Path | str] = None,
) -> BoardSnapshot:
    """Join ``.board/snapshot.json`` issues with their latest claim state.

    Every joined row is validated against ``fleet-board-row.schema.json``
    before being served: a row a malformed source document produced is
    refused **by name** (its issue number, in ``rejected``) rather than
    served — so a corrupt snapshot degrades the board honestly instead of
    silently. A tenant/board with no issues is an empty, valid board.
    """
    root = Path(repo_root)
    snap_path = (
        Path(snapshot_path) if snapshot_path is not None else root / DEFAULT_SNAPSHOT_PATH
    )
    claim_path = (
        Path(claims_path) if claims_path is not None else root / DEFAULT_CLAIMS_PATH
    )
    schema = load_board_row_schema(root)

    try:
        snapshot_raw = snap_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise TelemetryUnavailableError(f"missing board snapshot: {snap_path}") from exc
    try:
        snapshot_doc = json.loads(snapshot_raw)
    except ValueError as exc:
        raise TelemetryUnavailableError(
            f"unparseable board snapshot {snap_path}: {exc}"
        ) from exc
    issues = snapshot_doc.get("issues") if isinstance(snapshot_doc, dict) else None
    if not isinstance(issues, list):
        raise TelemetryUnavailableError(f"board snapshot {snap_path} has no issues[] list")

    claims = _latest_claims(claim_path)

    rows: List[dict[str, Any]] = []
    rejected: List[BoardRowRejection] = []
    for entry in issues:
        if not isinstance(entry, dict):
            rejected.append(BoardRowRejection("<non-object>", ("issue entry is not an object",)))
            continue
        number = entry.get("number")
        identifier = str(number) if number is not None else "<no-number>"
        claim = claims.get(number) if isinstance(number, int) else None
        claimed = bool(claim) and claim.get("event") == "claim"
        row = {
            "schema": BOARD_ROW_SCHEMA,
            "number": number,
            "title": entry.get("title"),
            "state": entry.get("state"),
            "lane": (claim.get("lane") or "") if claimed else "",
            "claimed_by": (claim.get("agent") or "") if claimed else "",
            "claimed_at": (claim.get("at") or "") if claimed else "",
            "labels": entry.get("labels") if isinstance(entry.get("labels"), list) else [],
        }
        errors = _validate_row(row, schema)
        if errors:
            rejected.append(BoardRowRejection(identifier, tuple(errors)))
            continue
        rows.append(row)

    return BoardSnapshot(schema=BOARD_ROW_SCHEMA, rows=tuple(rows), rejected=tuple(rejected))


class BoardSurface:
    """The route-facing wrapper ``portal.server.app`` composes (issue #880).

    Feature-flag-gated OFF (GR-5) through the same fail-closed reader every
    workbook-11 view uses (``portal.server.config_flags.surface_enabled``);
    the flag is declared in ``portal/config/feature-flags.yaml``
    (``surfaces.fleet_board``).
    """

    def __init__(
        self,
        *,
        repo_root: Path | str,
        enabled: Optional[bool] = None,
        config_path: Optional[Path | str] = None,
        snapshot_path: Optional[Path | str] = None,
        claims_path: Optional[Path | str] = None,
    ) -> None:
        from portal.server.config_flags import FLEET_BOARD_SURFACE, surface_enabled

        self.repo_root = Path(repo_root)
        self.config_path = Path(config_path) if config_path is not None else None
        self.snapshot_path = snapshot_path
        self.claims_path = claims_path
        if enabled is None:
            enabled = surface_enabled(
                self.repo_root,
                config_path=self.config_path,
                surface=FLEET_BOARD_SURFACE,
            )
        self.enabled = bool(enabled)

    def rows(self) -> dict[str, Any]:
        """The board snapshot, transport-shaped for the HTTP route."""
        snapshot = load_board_rows(
            self.repo_root,
            snapshot_path=self.snapshot_path,
            claims_path=self.claims_path,
        )
        return snapshot.as_dict()
