"""The deterministic mapper: repo hermes surface -> one read-only projection (issue #942).

The mapper is a pure function of the tree it is given. It reads hermes's
declared surface — ``registry/personas/cards/hermes.yaml``,
``registry/profiles/seeds/hermes.1.0.0.yaml``, ``gateway/finops/tiers.yaml``
and ``gateway/catalog/modules/hermes/module.json`` — and emits one canonical
document that validates against the inline contract (``CONTRACT``). Same input,
byte-identical output (the gate asserts sha256 equality across two builds).

The two stdlib-only tools the adapter may not take a third-party dependency for
(the same constraint ``integrations/paperclip/`` carries) — a **YAML subset
loader** (``load_yaml``) and a **JSON-Schema subset validator** (``validate``) —
live in the seam the two adapters share (``integrations/_seam/``, issue #1208)
and are re-exported here. The validator is the **superset** of the two copies
that used to exist, which is to say paperclip's stricter rules:
``format: date-time``, ``minimum`` and ``maximum`` are enforced here now, where
this adapter's own copy had drifted below them.

The binding is stated once, here, so the projection, the CLI and the gate
cannot drift apart: this adapter binds the **hermes-agents routing service**
(Flask on port 9501) and names the gateway's Ollama namesake
(``gateway/providers/hermes.py``) as explicitly excluded (ADR-0012).
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

# The model-tier vocabulary this projection EMITS is read from its declared
# authority (#1494) — the AgentProfile catalog `registry/profiles/catalog.yaml`
# ``tiers``, through its one reader — rather than restated here. The projection
# is a pure function of the tree it is given, so a tree that carries this
# adapter carries the authority too; the gate's scratch tree names both
# (scripts/check-hermes-integration.sh).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from registry.profiles.tiers import authority as _tier_authority  # noqa: E402

from .._seam.schema import validate  # noqa: F401 - re-exported at this adapter's seam
from .._seam.yaml_subset import load_yaml, load_yaml_file  # noqa: F401
from . import audit as audit_mod
from . import policy as policy_mod

# governance/spawn/tiering is the tier-PARITY consumer hook (issue #1274): the
# same gateway/finops/tiers.yaml window that judges a fleet or Claude subagent
# spawn also judges a hermes persona's assigned tier, so a persona cannot carry
# a tier the table forbids for its capability's task class. This module only
# exposes the check to callers of this mapping (``judge_persona_tier`` below);
# it is NOT wired into ``build_projection`` or the render path
# (``governance/spawn/render.py`` is owned by another lane this wave — see the
# PR's "Wiring needed" section).
try:  # pragma: no cover - import guarded so a missing sibling can't be silent
    from governance.spawn import tiering as tiering_mod
except ImportError:  # pragma: no cover
    tiering_mod = None  # type: ignore[assignment]

#: The frozen source files the projection reads (hermes's declared surface).
PERSONA_PATH = "registry/personas/cards/hermes.yaml"
PROFILE_PATH = "registry/profiles/seeds/hermes.1.0.0.yaml"
TIERS_PATH = "gateway/finops/tiers.yaml"
MODULE_JSON_PATH = "gateway/catalog/modules/hermes/module.json"
CAPABILITIES_SCHEMA_PATH = "integrations/hermes/capabilities.schema.json"

#: The binding (ADR-0012): the routing service, not the gateway namesake.
BOUND = "hermes-agents routing service"
NAMESAKE_PROVIDER = "gateway/providers/hermes.py"

#: The service contract (ADR-0012 Context 7): Flask on 9501, not wired here.
SERVICE_PORT = 9501
SERVICE_ENDPOINTS = ("/health", "/api/capabilities", "/api/router", "/api/tiering")
SERVICE_RUNTIME = "deployable-not-running"

#: The closed model-tier vocabulary, READ from its authority
#: (registry/profiles/catalog.yaml) through its one reader.
MODEL_TIERS = _tier_authority()


# ==========================================================================
# The shared seam: the YAML subset loader and the schema-subset validator
# ==========================================================================
# Both tools used to be defined here, and identically in
# ``integrations/paperclip/mapping.py`` — the copy this adapter was written from.
# They live in ``integrations/_seam/`` now (issue #1208) and are re-exported
# under their original names, so every existing caller keeps importing them from
# ``integrations.hermes.mapping``.


# ==========================================================================
# The inline frozen contract (the closed vocabulary the check validates)
# ==========================================================================

CONTRACT: Dict[str, Any] = {
    "type": "object",
    "required": ["hermes", "persona", "profile", "tiering", "module_binding"],
    "additionalProperties": False,
    "properties": {
        "hermes": {
            "type": "object",
            "required": ["bound", "adr", "mode", "service", "namesake_excluded"],
            "additionalProperties": False,
            "properties": {
                "bound": {"type": "string"},
                "adr": {"type": "string"},
                "mode": {"type": "string"},
                "service": {
                    "type": "object",
                    "required": ["port", "endpoints", "runtime"],
                    "additionalProperties": False,
                    "properties": {
                        "port": {"type": "integer"},
                        "endpoints": {"type": "array", "items": {"type": "string"}},
                        "runtime": {"type": "string"},
                    },
                },
                "namesake_excluded": {
                    "type": "object",
                    "required": ["provider", "wire", "model", "endpoint", "reason"],
                    "additionalProperties": False,
                    "properties": {
                        "provider": {"type": "string"},
                        "wire": {"type": "string"},
                        "model": {"type": "string"},
                        "endpoint": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                },
            },
        },
        "persona": {
            "type": "object",
            "required": ["id", "name", "posture", "tier", "lanes", "capabilities"],
            "additionalProperties": False,
            "properties": {
                "id": {"type": "string"},
                "name": {"type": "string"},
                "posture": {"type": "string"},
                "tier": {"type": "string", "enum": list(MODEL_TIERS)},
                "lanes": {"type": "array", "items": {"type": "string"}},
                "capabilities": {"type": "array", "items": {"type": "string"}},
            },
        },
        "profile": {
            "type": "object",
            "required": ["id", "version", "owner", "default_model_tier", "capabilities"],
            "additionalProperties": False,
            "properties": {
                "id": {"type": "string"},
                "version": {"type": "string"},
                "owner": {"type": "string"},
                "default_model_tier": {"type": "string", "enum": list(MODEL_TIERS)},
                "capabilities": {"type": "array", "items": {"type": "string"}},
                "tools": {"type": "array", "items": {"type": "string"}},
                "constraints": {"type": "array", "items": {"type": "string"}},
            },
        },
        "tiering": {
            "type": "object",
            "required": ["capabilities", "floor_tier", "escalation_thresholds"],
            "additionalProperties": False,
            "properties": {
                "capabilities": {
                    "type": "object",
                    "additionalProperties": {
                        "type": "object",
                        "required": ["default_tier", "max_tier"],
                        "additionalProperties": False,
                        "properties": {
                            "default_tier": {"type": "string", "enum": ["L0", "L1", "L2"]},
                            "max_tier": {"type": "string", "enum": ["L0", "L1", "L2"]},
                        },
                    },
                },
                "floor_tier": {"type": "string"},
                "escalation_thresholds": {"type": "object"},
            },
        },
        "module_binding": {
            "type": "object",
            "required": ["catalog", "type", "wire_shape", "role"],
            "additionalProperties": False,
            "properties": {
                "catalog": {"type": "string"},
                "type": {"type": "string"},
                "wire_shape": {"type": "string"},
                "role": {"type": "string"},
            },
        },
    },
}


# ==========================================================================
# Source readers
# ==========================================================================


def read_persona(root: Path) -> Dict[str, Any]:
    return load_yaml_file(root / PERSONA_PATH) or {}


def read_profile(root: Path) -> Dict[str, Any]:
    return load_yaml_file(root / PROFILE_PATH) or {}


def read_tiers(root: Path) -> Dict[str, Any]:
    return load_yaml_file(root / TIERS_PATH) or {}


def read_module_binding(root: Path) -> Dict[str, Any]:
    path = root / MODULE_JSON_PATH
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def load_capabilities_schema(root: Path) -> Dict[str, Any]:
    """Load the surface's capability-registry schema (the `schema` evidence)."""
    path = root / CAPABILITIES_SCHEMA_PATH
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


# ==========================================================================
# The projection
# ==========================================================================


def build_projection(root: Path) -> Dict[str, Any]:
    """Build the canonical read-only projection (offline, deterministic)."""
    persona = read_persona(root)
    profile = read_profile(root)
    tiers = read_tiers(root)
    module = read_module_binding(root)

    persona_caps = tuple(sorted(str(c) for c in (persona.get("capabilitySet") or ())))
    profile_caps = tuple(sorted(str(c) for c in (profile.get("capabilitySet") or ())))

    hermes_policy = policy_mod.build_policy(tiers, TIERS_PATH)

    return {
        "hermes": {
            "bound": BOUND,
            "adr": "ADR-0012",
            "mode": "read-only-projection",
            "service": {
                "port": SERVICE_PORT,
                "endpoints": list(SERVICE_ENDPOINTS),
                "runtime": SERVICE_RUNTIME,
            },
            "namesake_excluded": {
                "provider": NAMESAKE_PROVIDER,
                "wire": "Ollama /api/chat",
                "model": "hermes3",
                "endpoint": "http://localhost:8080/api/chat",
                "reason": (
                    "inference-only Ollama endpoint for the Hermes-3 LLM, not the "
                    "routing service (ADR-0012 Context 5 and 7)"
                ),
            },
        },
        "persona": {
            "id": str(persona.get("id") or "hermes"),
            "name": str(persona.get("name") or "Hermes"),
            "posture": str(persona.get("posture") or "executor"),
            "tier": str(persona.get("defaultModelTier") or ""),
            "lanes": list(persona.get("ownedLanes") or ()),
            "capabilities": list(persona_caps),
        },
        "profile": {
            "id": str(profile.get("id") or "hermes"),
            "version": str(profile.get("version") or ""),
            "owner": str(profile.get("owner") or ""),
            "default_model_tier": str(profile.get("defaultModelTier") or ""),
            "capabilities": list(profile_caps),
            "tools": list(profile.get("toolAllowlist") or ()),
            "constraints": list(profile.get("constraintSet") or ()),
        },
        "tiering": {
            "capabilities": policy_mod.tier_projection(hermes_policy, persona_caps),
            "floor_tier": hermes_policy.floor_tier,
            "escalation_thresholds": dict(hermes_policy.escalation_thresholds),
        },
        "module_binding": {
            "catalog": MODULE_JSON_PATH,
            "type": str(module.get("type") or ""),
            "wire_shape": "Ollama-compatible /api/chat",
            "role": "namesake inference endpoint (excluded from the routing contract)",
        },
    }


def judge_persona_tier(capability: str, tier: str):
    """Tier-parity consumer hook (issue #1274).

    Judges a hermes persona's assigned ``tier`` for one ``capability`` against
    the SAME ``gateway/finops/tiers.yaml`` window the fleet and Claude
    subagent spawn paths are judged against (``governance.spawn.tiering``).
    Returns the ``tiering.Judgment`` (``.allowed`` / ``.finding`` /
    ``str(judgment)`` carrying ``FINOPS-ROLE-NOT-ALLOWED`` on refusal).

    Raises ``RuntimeError`` if ``governance.spawn.tiering`` cannot be
    imported (a packaging problem, not a tiering verdict — callers should not
    treat that as "allowed"). Callers own wiring this into an actual gate;
    this function only exposes the check.
    """
    if tiering_mod is None:
        raise RuntimeError("governance.spawn.tiering is not importable")
    return tiering_mod.judge("hermes-persona", capability, tier)


def canonical_document(projection: Dict[str, Any]) -> str:
    """The byte-stable serialization the gate hashes."""
    return json.dumps(projection, indent=2, sort_keys=True)


def canonical_sha(projection: Dict[str, Any]) -> str:
    """sha256 of the canonical document (same tree, same digest)."""
    return hashlib.sha256(canonical_document(projection).encode("utf-8")).hexdigest()


# ==========================================================================
# Conformance
# ==========================================================================


def check_projection(root: Path) -> Tuple[List[str], Dict[str, Any]]:
    """Validate the projection; return (findings, projection).

    The findings are closed and each names the drifted field:

    * schema conformance (``CONTRACT``, plus the per-entry ``capabilities.schema.json``);
    * the cross-source consistency audit (``audit.py``): persona/profile
      capability-set parity, persona/profile tier parity, tiering-floor presence,
      and tier coverage (every persona capability must have a FinOps tier
      mapping).
    """
    projection = build_projection(root)
    findings = validate(projection, CONTRACT, "$")
    findings.extend(_check_capabilities_schema(root, projection))

    entries = audit_mod.audit_projection(projection)
    findings.extend(audit_mod.violation_findings(entries))
    return findings, projection


def _check_capabilities_schema(
    root: Path, projection: Dict[str, Any]
) -> List[str]:
    """Validate every projected capability entry against the surface schema."""
    schema = load_capabilities_schema(root)
    capabilities = projection["tiering"]["capabilities"]
    if not schema:
        return ["capabilities.schema.json is missing from integrations/hermes"]
    findings: List[str] = []
    for cap, entry in capabilities.items():
        findings.extend(validate(entry, schema, "tiering.capabilities.%s" % cap))
    return findings
