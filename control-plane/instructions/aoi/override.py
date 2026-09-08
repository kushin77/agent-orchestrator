"""Frozen tenant-override contract (issue #42).

Governed instruction content (the canonical source's ``managed: true`` layers)
is frozen for tenants.  A tenant may customise ONLY within a closed, schema-
bounded override contract (``schema/tenant-override.schema.json``): branding
(repository + subtitle) and extra local-layer rules.  Anything outside the
frozen contract — an unknown field at any level, an attempt to redefine a
governed rule id, an override aimed at a different canonical id/version — is
REJECTED.

Contract::

    schema: ao.instructions.override/v1
    tenant: <string>                        # ^[a-z0-9][a-z0-9-]{0,127}$
    canonical: {id: <string>, version: "<semver>"}   # must match the target
    branding: {repository: "<owner>/<repo>", subtitle?: <string>}
    local:
      extraRules:                           # tenant-owned, local-layer only
        - {id: <string>, text: <string>}    # id must NOT collide with a governed rule id

The validator here is pure Python (stdlib only) and is the load-bearing
enforcement path; the JSON Schema documents are the machine-readable contract
and are exercised against this validator in the test suite.
"""

from __future__ import annotations

import re

from .model import governed_rule_ids
from .versioning import parse_semver

OVERRIDE_SCHEMA = "ao.instructions.override/v1"

_SEMVER_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_TENANT_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,127}$")
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_RULE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

_MAX_EXTRA_RULES = 50
_TEXT_MIN = 8
_TEXT_MAX = 2000
_SUBTITLE_MAX = 200


class OverrideError(ValueError):
    """Raised when a tenant override violates the frozen customization contract."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise OverrideError(message)


def validate_override(override: dict) -> dict:
    """Validate an override against the frozen contract (raise on violation).

    The check is closed: every object level rejects unknown keys, so an
    override that attempts any field outside the frozen customization surface
    is REJECTED (negative-test guarantee).
    """
    _require(isinstance(override, dict), "override must be a mapping")
    _require(override.get("schema") == OVERRIDE_SCHEMA, "override schema must be ao.instructions.override/v1")
    _reject_unknown(override, {"schema", "tenant", "canonical", "branding", "local"}, "top level")

    tenant = override.get("tenant")
    _require(isinstance(tenant, str) and _TENANT_RE.match(tenant), "tenant must match ^[a-z0-9][a-z0-9-]{0,127}$")

    canonical = override.get("canonical")
    _require(isinstance(canonical, dict), "canonical must be a mapping")
    _reject_unknown(canonical, {"id", "version"}, "canonical")
    canonical_id = canonical.get("id")
    _require(isinstance(canonical_id, str) and canonical_id, "canonical.id must be a non-empty string")
    version = canonical.get("version")
    _require(isinstance(version, str) and _SEMVER_RE.match(version), "canonical.version must be SemVer")
    parse_semver(version)

    branding = override.get("branding")
    if branding is not None:
        _require(isinstance(branding, dict), "branding must be a mapping")
        _reject_unknown(branding, {"repository", "subtitle"}, "branding")
        repository = branding.get("repository")
        _require(isinstance(repository, str) and _REPO_RE.match(repository), "branding.repository must match <owner>/<repo>")
        subtitle = branding.get("subtitle")
        _require(subtitle is None or (isinstance(subtitle, str) and 0 < len(subtitle) <= _SUBTITLE_MAX),
                 f"branding.subtitle must be a string of 1..{_SUBTITLE_MAX} characters")

    local = override.get("local")
    if local is not None:
        _require(isinstance(local, dict), "local must be a mapping")
        _reject_unknown(local, {"extraRules"}, "local")
        extra_rules = local.get("extraRules")
        _require(isinstance(extra_rules, list), "local.extraRules must be a list")
        _require(len(extra_rules) <= _MAX_EXTRA_RULES, f"local.extraRules exceeds the {_MAX_EXTRA_RULES} rule cap")
        seen: set[str] = set()
        for rule in extra_rules:
            _require(isinstance(rule, dict), "each extra rule must be a mapping")
            _reject_unknown(rule, {"id", "text"}, "local.extraRules[]")
            rule_id = rule.get("id")
            _require(isinstance(rule_id, str) and _RULE_ID_RE.match(rule_id),
                     f"extra rule id invalid: {rule_id!r}")
            _require(rule_id not in seen, f"duplicate extra rule id: {rule_id}")
            seen.add(rule_id)
            text = rule.get("text")
            _require(isinstance(text, str) and _TEXT_MIN <= len(text) <= _TEXT_MAX,
                     f"extra rule {rule_id} text must be {_TEXT_MIN}..{_TEXT_MAX} characters")
    return override


def _reject_unknown(mapping: dict, allowed: set[str], where: str) -> None:
    unknown = sorted(set(mapping) - allowed)
    _require(not unknown, f"field(s) outside the frozen contract at {where}: {', '.join(unknown)}")


def check_override_applies(override: dict, canonical: dict) -> None:
    """Cross-check an override against the canonical source it targets.

    The freeze is enforced here: the override must name the exact canonical id
    and version it is applied to, and no extra rule may reuse a governed rule
    id (a tenant cannot redefine governed behaviour).
    """
    if override.get("canonical", {}).get("id") != canonical.get("id"):
        raise OverrideError(
            f"override targets canonical id {override.get('canonical', {}).get('id')!r} "
            f"but the source is {canonical.get('id')!r}"
        )
    if override.get("canonical", {}).get("version") != canonical.get("version"):
        raise OverrideError(
            f"override targets canonical version {override.get('canonical', {}).get('version')!r} "
            f"but the source is {canonical.get('version')!r}"
        )
    governed = governed_rule_ids(canonical)
    for rule in local_extra_rules(override):
        if rule["id"] in governed:
            raise OverrideError(
                f"extra rule id {rule['id']!r} collides with a governed rule id "
                "(governed behaviour is frozen and cannot be redefined)"
            )


def local_extra_rules(override: dict) -> list[dict]:
    """Return the validated extra local-layer rules of an override (empty ok)."""
    local = override.get("local") or {}
    return list(local.get("extraRules") or [])


def apply_local_rules(override: dict, canonical: dict) -> list[dict]:
    """Validate + cross-check an override and return its local extra rules."""
    validate_override(override)
    check_override_applies(override, canonical)
    return local_extra_rules(override)
