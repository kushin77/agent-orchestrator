"""portal.server.settings — the middleware settings aggregator (issue #1756).

---knowledge---
module_id: portal.server.settings
system: portal
app: server
solution_class: pattern
patterns: [cross-domain-aggregator, join-not-own]
derives_from: portal/server/sessions.py
owner_sme: platform-sme
tier: L1
interfaces: [SettingsAggregator, SettingsRow]
invariants: "read-only projection of IaC-declared config; no secret VALUE ever leaves a row"
gotchas: "gateway.providers is not import-stable outside its own test bootstrap (no gateway/__init__.py) — provider flags are read directly off registry.yaml instead"
related: ["#1756", "#1757", "#1758", "#1759", "#1760"]
do_not_duplicate: null
---knowledge---

WHY this exists: settings in this system are IaC-declared, not clicked — every
row is ``editable: false``. Five domains are joined here, mirroring
``portal.server.sessions.SessionsView``'s posture (read fresh each call, no
cache, no second store):

* ``portal_surfaces`` — ``portal/config/feature-flags.yaml`` (via
  ``config_flags.read_config_default``), the portal's own surface registry.
  NOT ``infra/feature-flags/registry.yaml`` — that file is the
  *control-plane service* registry, kept 1:1 with
  ``infra/terraform/variables.tf`` by ``scripts/check-feature-flags.py``; a
  portal view is declared in the portal's own file instead (see
  ``config_flags.py``'s own docstring).
* ``fleet_jobs`` — ``config/fleet-jobs.json``, one row per cron job.
* ``dispatch_tier_policy`` — ``governance/dispatch/tier-policy.json``.
* ``gate_skip_budget`` — ``scripts/skip-budget.json``, projected as its
  schema id plus a count of its named sections (the file is mostly prose
  justifying entries, not a flat table).
* ``provider_flags`` — ``enable_hermes`` and ``enable_paperclip``, both read
  directly off ``infra/feature-flags/registry.yaml``'s ``services.hermes`` /
  ``services.paperclip`` entries (the same fail-closed on/off logic
  ``gateway.providers.flags`` uses, applied locally — see ``_provider_flags``
  for why that module isn't imported).
* ``nous_secret`` — ``infra/terraform/provider-credentials.json``. Only the
  declared **id** and gate are projected (``secret_id``, ``env``, ``gate``,
  ``service``); the file itself never carries a value, and this reader is
  hard-pinned to that allowed key set so a future field added to the JSON
  (should one ever carry a literal) cannot silently pass through a row.

**Honesty rule** (mirrors ``sessions.py``): a source file that does not exist
is never silently dropped — it becomes one row per domain naming the missing
path (``key="status"``), same five-field schema, so a caller iterating
``aggregate()`` sees every domain without a second channel to check.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from portal.server.config_flags import CONFIG_RELATIVE as PORTAL_FLAGS_RELATIVE
from portal.server.config_flags import DECLARED_SURFACES

SCHEMA = "ao.portal-settings/v1"

FLEET_JOBS_RELATIVE = Path("config") / "fleet-jobs.json"
TIER_POLICY_RELATIVE = Path("governance") / "dispatch" / "tier-policy.json"
SKIP_BUDGET_RELATIVE = Path("scripts") / "skip-budget.json"
REGISTRY_RELATIVE = Path("infra") / "feature-flags" / "registry.yaml"
NOUS_CREDENTIALS_RELATIVE = Path("infra") / "terraform" / "provider-credentials.json"

#: The only fields ever projected from a provider-credentials secret entry —
#: never the file's own "delivery"/"source"/"why", and never any future field
#: that isn't in this set, so a value can't slip through unnoticed.
_SECRET_ALLOWED_KEYS = ("secret_id", "env", "gate", "service")

#: skip-budget.json sections that are meta/prose, not a named entry.
_SKIP_BUDGET_META_KEYS = ("schema", "why", "tracking-lease")


@dataclass(frozen=True)
class SettingsRow:
    domain: str
    key: str
    value: Any
    source_file: str
    editable: bool = False


def _missing_row(domain: str, relative: Path) -> SettingsRow:
    return SettingsRow(
        domain=domain,
        key="status",
        value=f"not reporting: {relative.as_posix()} absent",
        source_file=relative.as_posix(),
    )


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _read_yaml(path: Path) -> Any:
    try:
        import yaml
    except ImportError:
        return None
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    except Exception:  # pragma: no cover - yaml.YAMLError isn't a ValueError
        return None


class SettingsAggregator:
    """Joins IaC-declared config domains into one read-only row set (#1756).

    Every row is ``editable: false`` — settings here are IaC-declared, never
    clicked. A root/file that is absent yields one refusal row per domain
    (never a silently empty domain, never an exception).
    """

    def __init__(self, *, repo_root: Path | str) -> None:
        self.repo_root = Path(repo_root)

    def _path(self, relative: Path) -> Path:
        return self.repo_root / relative

    def aggregate(self) -> list[SettingsRow]:
        rows: list[SettingsRow] = []
        rows.extend(self._portal_surfaces())
        rows.extend(self._fleet_jobs())
        rows.extend(self._dispatch_tier_policy())
        rows.extend(self._gate_skip_budget())
        rows.extend(self._provider_flags())
        rows.extend(self._nous_secret())
        return rows

    # -- portal surface flags ------------------------------------------- #
    def _portal_surfaces(self) -> list[SettingsRow]:
        relative = PORTAL_FLAGS_RELATIVE
        path = self._path(relative)
        if not path.is_file():
            return [_missing_row("portal_surfaces", relative)]
        document = _read_yaml(path)
        if not isinstance(document, dict) or not isinstance(
            document.get("surfaces"), dict
        ):
            return [_missing_row("portal_surfaces", relative)]
        rows = []
        for surface, entry in sorted(document["surfaces"].items()):
            default = entry.get("default") if isinstance(entry, dict) else None
            on = default is True or (
                isinstance(default, str) and default.strip().lower() == "on"
            )
            rows.append(
                SettingsRow(
                    domain="portal_surfaces",
                    key=surface,
                    value="on" if on else "off",
                    source_file=relative.as_posix(),
                )
            )
        # every declared surface known to config_flags.py should be visible
        # even if feature-flags.yaml happens to omit one — that omission
        # itself already reads "off" via config_flags' fail-closed contract,
        # so nothing further is added here; DECLARED_SURFACES exists to keep
        # this module's import honest against drift, not to synthesize rows.
        _ = DECLARED_SURFACES
        return rows

    # -- fleet job schedule ----------------------------------------------- #
    def _fleet_jobs(self) -> list[SettingsRow]:
        relative = FLEET_JOBS_RELATIVE
        path = self._path(relative)
        if not path.is_file():
            return [_missing_row("fleet_jobs", relative)]
        document = _read_json(path)
        jobs = document.get("jobs") if isinstance(document, dict) else None
        if not isinstance(jobs, list):
            return [_missing_row("fleet_jobs", relative)]
        rows = []
        for job in jobs:
            if not isinstance(job, dict) or not job.get("name"):
                continue
            schedule = job.get("schedule") or f"every {job.get('interval')}m"
            rows.append(
                SettingsRow(
                    domain="fleet_jobs",
                    key=str(job["name"]),
                    value=f"enabled={bool(job.get('enabled'))} schedule={schedule}",
                    source_file=relative.as_posix(),
                )
            )
        return rows

    # -- dispatch tier policy ---------------------------------------------- #
    def _dispatch_tier_policy(self) -> list[SettingsRow]:
        relative = TIER_POLICY_RELATIVE
        path = self._path(relative)
        if not path.is_file():
            return [_missing_row("dispatch_tier_policy", relative)]
        document = _read_json(path)
        if not isinstance(document, dict) or not isinstance(
            document.get("tiers"), dict
        ):
            return [_missing_row("dispatch_tier_policy", relative)]
        rows = [
            SettingsRow(
                domain="dispatch_tier_policy",
                key="default_provider",
                value=document.get("default_provider"),
                source_file=relative.as_posix(),
            )
        ]
        for tier, mapping in sorted(document["tiers"].items()):
            rows.append(
                SettingsRow(
                    domain="dispatch_tier_policy",
                    key=f"tier.{tier}",
                    value=json.dumps(mapping, sort_keys=True),
                    source_file=relative.as_posix(),
                )
            )
        return rows

    # -- gate skip-budget ----------------------------------------------- #
    def _gate_skip_budget(self) -> list[SettingsRow]:
        relative = SKIP_BUDGET_RELATIVE
        path = self._path(relative)
        if not path.is_file():
            return [_missing_row("gate_skip_budget", relative)]
        document = _read_json(path)
        if not isinstance(document, dict):
            return [_missing_row("gate_skip_budget", relative)]
        sections = [k for k in document if k not in _SKIP_BUDGET_META_KEYS]
        return [
            SettingsRow(
                domain="gate_skip_budget",
                key="schema",
                value=document.get("schema"),
                source_file=relative.as_posix(),
            ),
            SettingsRow(
                domain="gate_skip_budget",
                key="section_count",
                value=len(sections),
                source_file=relative.as_posix(),
            ),
        ]

    # -- gateway provider flags ------------------------------------------- #
    def _provider_flags(self) -> list[SettingsRow]:
        """``enable_hermes`` / ``enable_paperclip`` off registry.yaml's ``services``.

        Both flags are read here with the same local fail-closed logic
        ``gateway.providers.flags.read_hermes_default`` uses — that module is
        not imported: ``gateway/`` ships no ``__init__.py`` (its own tests
        bootstrap ``sys.path`` specially, see ``gateway/providers/tests/
        conftest.py``), so ``gateway.providers`` is not import-stable from
        the portal. Reading the same declared source directly keeps this
        reader dependency-free and avoids a second, fragile import path.
        """
        relative = REGISTRY_RELATIVE
        path = self._path(relative)
        if not path.is_file():
            return [_missing_row("provider_flags", relative)]
        document = _read_yaml(path)
        services = document.get("services") if isinstance(document, dict) else None
        if not isinstance(services, dict):
            return [_missing_row("provider_flags", relative)]
        rows = []
        for key, service_key in (
            ("enable_hermes", "hermes"),
            ("enable_paperclip", "paperclip"),
        ):
            entry = services.get(service_key)
            default = entry.get("default") if isinstance(entry, dict) else None
            on = default is True or (
                isinstance(default, str) and default.strip().lower() == "on"
            )
            rows.append(
                SettingsRow(
                    domain="provider_flags",
                    key=key,
                    value="on" if on else "off",
                    source_file=relative.as_posix(),
                )
            )
        return rows

    # -- Nous secret declaration (id only, never a value) ------------------ #
    def _nous_secret(self) -> list[SettingsRow]:
        relative = NOUS_CREDENTIALS_RELATIVE
        path = self._path(relative)
        if not path.is_file():
            return [_missing_row("nous_secret", relative)]
        document = _read_json(path)
        secrets = document.get("secrets") if isinstance(document, dict) else None
        if not isinstance(secrets, list):
            return [_missing_row("nous_secret", relative)]
        rows = []
        for secret in secrets:
            if not isinstance(secret, dict):
                continue
            projected = {
                k: secret.get(k) for k in _SECRET_ALLOWED_KEYS if k in secret
            }
            key = str(secret.get("secret_id") or secret.get("service") or "declared")
            rows.append(
                SettingsRow(
                    domain="nous_secret",
                    key=key,
                    value=json.dumps(projected, sort_keys=True),
                    source_file=relative.as_posix(),
                )
            )
        return rows
