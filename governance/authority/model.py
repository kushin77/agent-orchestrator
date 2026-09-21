#!/usr/bin/env python3
"""Authority model — repo separation, scoped admin rights, SoD and closure.

---knowledge---
module_id: governance.authority.model
system: governance
app: authority
solution_class: enterprise
patterns: [provoked-negative-control, no-false-green, honesty-tri-state, offline-hermetic, deterministic]
derives_from: null
owner_sme: security-sme
tier: L1
interfaces: [AuthorityError, SchemaError, MatrixLoadError, Verdict, Decision, allow, deny, cannot_assess, assert_schema_supported, validate_instance, (+17 more)]
invariants: ""
gotchas: ""
related: ["#144", "#150", "#1494"]
do_not_duplicate: null
---knowledge---

Productizes the repo-separation doctrine of issue #150 (EPIC-00 #144) into an
offline, deterministic enforcement engine over a schema-validated authority
matrix (``governance/authority/matrix.yaml``):

* **Repo separation.** Every repo declares the ONE fleet that administers it and
  the admin rights it delegates. ``can_act()`` denies any action on a repo the
  acting principal is not scoped to — files, issues, state or merge. There is no
  path that lets a fleet touch another repo's artifacts: the authority record
  for that pair simply does not exist, and an absent record is never a pass.
* **One cross-repo actor.** Exactly one principal carries ``cross_repo: true``
  and it must be the ``enterprise-controller``; it is the only principal that
  may act on more than one repo (its scope covers every declared repo) and the
  only one that may hold the ``rollup`` right. A second ``cross_repo: true``
  principal is a validation failure (NOT-OK), because it re-opens exactly the
  cross-repo hole the matrix exists to close.
* **Separation of duties.** ``separation_of_duties()`` requires the executor,
  reviewer and auditor of a work item to be three DISTINCT principals, each
  filling the duty its posture names (closed persona vocabulary from
  ``registry/personas/persona-card.schema.json``: an auditor never executes what
  it audits), each scoped to the work item's repo, and the auditor never
  dispatched below the executor it audits.
* **End-to-end closure.** ``is_closed()`` is true only when BOTH the item's own
  gate evidence AND the repo-level ``make verify`` evidence are recorded with
  real output naming the commit under test. A missing, empty or whitespace-only
  evidence field, a failing exit code, a commit that is not the item's head, a
  repo mismatch or a recorder with no authority there is an explicit DENIAL —
  never a pass. Never merge failing work: a non-zero exit code can never close.

Tri-state results (``ALLOW`` / ``DENY`` / ``CANNOT-ASSESS``) are the honesty
doctrine shared with ``guardrails/honesty`` (issue #28): a malformed matrix, an
unreadable schema, an unknown principal/repo/action or a schema keyword this
engine does not implement is CANNOT-ASSESS — the engine reports that it cannot
decide — and is NEVER ALLOW. The module is stdlib + PyYAML only, reads nothing
but the matrix and the schema, and performs no network or write I/O.

Design line between the document and the decision (this is the whole reason the
tri-state exists):

* **Schema + semantic invariants = document validity.** Wrong type, unknown
  vocabulary, a second cross-repo principal, a fleet scoped to two repos, a
  cross-repo right held by a scoped fleet, merge authority outside the
  commander, an undeclared repo in a scope: all are real defects (NOT-OK).
* **Assignments and evidence = decisions.** An unassigned duty, a SoD collision,
  a missing/empty evidence field or a failing exit code are DENIALS on a
  perfectly well-formed document — the document is readable, the work simply is
  not compliant.
* **Cannot-load / cannot-decide = CANNOT-ASSESS.** Unparseable YAML, a
  non-mapping root, an unreadable or unsupported schema, an unknown version, or
  a reference to a principal/repo that the matrix never declares.
"""

from __future__ import annotations

import copy
import json
import re
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

try:  # PyYAML is the single non-stdlib dependency this module is allowed.
    import yaml
except ImportError:  # pragma: no cover - the CLI reports CANNOT-ASSESS instead.
    yaml = None  # type: ignore[assignment]

# The tier vocabulary the matrix's tier column is drawn from is READ from its
# declared authority (#1494) — the AgentProfile catalog
# `registry/profiles/catalog.yaml` ``tiers``, through its one reader
# `registry/profiles/tiers.py` — so the authority model cannot drift from the
# ladder every other surface routes on.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from registry.profiles.tiers import authority as _tier_authority  # noqa: E402
from registry.profiles.tiers import rank as _tier_rank  # noqa: E402

PKG_DIR = Path(__file__).resolve().parent
DEFAULT_SCHEMA_PATH = PKG_DIR / "schema.json"
DEFAULT_MATRIX_PATH = PKG_DIR / "matrix.yaml"

MATRIX_VERSION = 1
ACTIONS: Tuple[str, ...] = ("files", "issues", "state", "merge", "rollup")
ROLLUP = "rollup"
MERGE = "merge"
POSTURES: Tuple[str, ...] = ("executor", "reviewer", "auditor")
DUTIES: Tuple[str, ...] = ("executor", "reviewer", "auditor")
ROLES: Tuple[str, ...] = (
    "commander",
    "lieutenant-commander",
    "general",
    "platoon-leader",
    "soldier",
    "auditor",
    "scribe",
)
TIERS: Tuple[str, ...] = _tier_authority()
TIER_RANK: Mapping[str, int] = _tier_rank()

_EVIDENCE_SLOTS: Tuple[str, ...] = ("gate_evidence", "verify_evidence")
_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
_SLOT_SLUG: Mapping[str, str] = {
    "gate_evidence": "gate",
    "verify_evidence": "verify",
}


class AuthorityError(Exception):
    """Base class for authority-model errors."""


class SchemaError(AuthorityError):
    """The schema itself cannot be used (unreadable, or a keyword we do not implement)."""


class MatrixLoadError(AuthorityError):
    """The matrix cannot be loaded at all (missing, unparseable, not a mapping)."""


# --------------------------------------------------------------------------- #
# Closed reason-code vocabulary
# --------------------------------------------------------------------------- #

REASONS: frozenset = frozenset(
    {
        # --- ALLOW -----------------------------------------------------------
        "ok",
        # --- CANNOT-ASSESS: could not decide --------------------------------
        "matrix-fault",
        "matrix-invalid",
        "matrix-version-unsupported",
        "action-unknown",
        "repo-unknown",
        "principal-unknown",
        "work-item-unknown",
        # --- DENY: authority -------------------------------------------------
        "out-of-scope",
        "cross-repo-denied",
        "rollup-scope-incomplete",
        "admin-right-not-granted",
        "merge-authority-denied",
        # --- DENY: separation of duties --------------------------------------
        "sod-duty-unassigned",
        "sod-actor-unknown",
        "sod-executor-equals-reviewer",
        "sod-executor-equals-auditor",
        "sod-reviewer-equals-auditor",
        "sod-posture-mismatch",
        "sod-auditor-cannot-execute",
        "sod-actor-out-of-scope",
        "sod-auditor-tier-below-executor",
        # --- DENY: closure ---------------------------------------------------
        "closure-gate-evidence-missing",
        "closure-verify-evidence-missing",
        "closure-gate-evidence-command-missing",
        "closure-verify-evidence-command-missing",
        "closure-gate-evidence-output-missing",
        "closure-verify-evidence-output-missing",
        "closure-gate-evidence-failed",
        "closure-verify-evidence-failed",
        "closure-gate-evidence-sha-missing",
        "closure-verify-evidence-sha-missing",
        "closure-gate-evidence-sha-mismatch",
        "closure-verify-evidence-sha-mismatch",
        "closure-gate-evidence-repo-mismatch",
        "closure-verify-evidence-repo-mismatch",
        "closure-gate-evidence-recorder-unknown",
        "closure-verify-evidence-recorder-unknown",
        "closure-gate-evidence-recorder-out-of-scope",
        "closure-verify-evidence-recorder-out-of-scope",
        "closure-evidence-sha-divergence",
    }
)


class Verdict(str, Enum):
    """Tri-state result (guardrails/honesty vocabulary, issue #28)."""

    ALLOW = "ALLOW"
    DENY = "DENY"
    CANNOT_ASSESS = "CANNOT-ASSESS"

    @property
    def exit_code(self) -> int:
        """CLI exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS."""
        return {"ALLOW": 0, "DENY": 1, "CANNOT-ASSESS": 2}[self.value]


@dataclass(frozen=True)
class Decision:
    """An explicit, auditable verdict with a closed-vocabulary reason code."""

    verdict: Verdict
    reason: str
    detail: str = ""
    subject: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.reason not in REASONS:
            raise AuthorityError(f"reason code {self.reason!r} is not in the closed vocabulary")

    @property
    def ok(self) -> bool:
        return self.verdict is Verdict.ALLOW

    @property
    def denied(self) -> bool:
        return self.verdict is Verdict.DENY

    @property
    def cannot_assess(self) -> bool:
        return self.verdict is Verdict.CANNOT_ASSESS

    def to_dict(self) -> Dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "reason": self.reason,
            "detail": self.detail,
            "subject": dict(self.subject),
            "exit_code": self.verdict.exit_code,
        }

    def render(self) -> str:
        head = f"{self.verdict.value} ({self.reason})"
        if self.detail:
            return f"{head}: {self.detail}"
        return head


def _deny(reason: str, detail: str = "", **subject: Any) -> Decision:
    return Decision(Verdict.DENY, reason, detail, subject)


def _cannot_assess(reason: str, detail: str = "", **subject: Any) -> Decision:
    return Decision(Verdict.CANNOT_ASSESS, reason, detail, subject)


def _allow(detail: str = "", **subject: Any) -> Decision:
    return Decision(Verdict.ALLOW, "ok", detail, subject)


def allow(detail: str = "", **subject: Any) -> Decision:
    """Public factory: an ALLOW decision (used by consumers such as isolation.py)."""
    return _allow(detail, **subject)


def deny(reason: str, detail: str = "", **subject: Any) -> Decision:
    """Public factory: an explicit DENIAL carrying a closed-vocabulary reason code."""
    return _deny(reason, detail, **subject)


def cannot_assess(reason: str, detail: str = "", **subject: Any) -> Decision:
    """Public factory: a CANNOT-ASSESS decision (never a pass)."""
    return _cannot_assess(reason, detail, **subject)


# --------------------------------------------------------------------------- #
# Minimal JSON-Schema (draft-07 subset) engine
# --------------------------------------------------------------------------- #
#
# The schema must be a REAL JSON-Schema document, so the engine implements the
# draft-07 subset the schema uses and refuses to run against a schema that uses
# a keyword it does not implement. Silently ignoring a keyword would turn an
# unimplemented constraint into a false green — the failure mode this repo's
# no-false-green doctrine forbids.

SUPPORTED_SCHEMA_KEYWORDS: frozenset = frozenset(
    {
        "$schema",
        "$id",
        "title",
        "description",
        "definitions",
        "$ref",
        "type",
        "required",
        "properties",
        "additionalProperties",
        "enum",
        "const",
        "pattern",
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
        "uniqueItems",
        "items",
        "minimum",
        "maximum",
        "default",
        "allOf",
    }
)

_NOT_TYPE_CONSTRAINTS = frozenset({"$schema", "$id", "title", "description", "default"})


def assert_schema_supported(schema: Mapping[str, Any], path: str = "#") -> None:
    """Walk the schema; raise SchemaError on any keyword the engine does not implement."""
    if not isinstance(schema, Mapping):
        raise SchemaError(f"{path}: schema node is not an object ({type(schema).__name__})")
    for keyword, value in schema.items():
        if keyword not in SUPPORTED_SCHEMA_KEYWORDS:
            raise SchemaError(f"{path}: unsupported JSON-Schema keyword {keyword!r}")
        if keyword == "definitions":
            for name, sub in (value or {}).items():
                assert_schema_supported(sub, f"{path}/definitions/{name}")
        elif keyword == "properties":
            for name, sub in (value or {}).items():
                assert_schema_supported(sub, f"{path}/properties/{name}")
        elif keyword == "items":
            if isinstance(value, list):
                raise SchemaError(f"{path}/items: tuple validation (array of schemas) is not implemented")
            assert_schema_supported(value, f"{path}/items")
        elif keyword == "additionalProperties":
            if isinstance(value, Mapping):
                assert_schema_supported(value, f"{path}/additionalProperties")
        elif keyword == "allOf":
            for index, sub in enumerate(value or []):
                assert_schema_supported(sub, f"{path}/allOf/{index}")


def _resolve_ref(ref: str, root: Mapping[str, Any], path: str) -> Mapping[str, Any]:
    if not ref.startswith("#/"):
        raise SchemaError(f"{path}: only local $ref pointers are implemented (got {ref!r})")
    node: Any = root
    for raw in ref[2:].split("/"):
        part = raw.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, Mapping) or part not in node:
            raise SchemaError(f"{path}: $ref {ref!r} does not resolve")
        node = node[part]
    if not isinstance(node, Mapping):
        raise SchemaError(f"{path}: $ref {ref!r} does not resolve to a schema object")
    return node


def _type_matches(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, Mapping)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "null":
        return value is None
    raise SchemaError(f"unsupported JSON-Schema type {expected!r}")


def _validate_node(node: Any, schema: Mapping[str, Any], root: Mapping[str, Any], path: str, errors: List[str]) -> None:
    if "$ref" in schema:
        _validate_node(node, _resolve_ref(str(schema["$ref"]), root, path), root, path, errors)
    for sub in schema.get("allOf", []) or []:
        _validate_node(node, sub, root, path, errors)
    if "const" in schema and node != schema["const"]:
        errors.append(f"{path}: expected {schema['const']!r}, got {node!r}")
    if "enum" in schema and node not in list(schema["enum"]):
        errors.append(f"{path}: {node!r} is not one of {sorted(map(str, schema['enum']))}")
    if "type" in schema:
        allowed = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_type_matches(node, str(one)) for one in allowed):
            errors.append(f"{path}: expected type {'|'.join(map(str, allowed))}, got {type(node).__name__}")
            return
    if isinstance(node, str):
        if "pattern" in schema and not re.search(str(schema["pattern"]), node):
            errors.append(f"{path}: {node!r} does not match pattern {schema['pattern']!r}")
        if "minLength" in schema and len(node) < int(schema["minLength"]):
            errors.append(f"{path}: shorter than minLength {schema['minLength']}")
        if "maxLength" in schema and len(node) > int(schema["maxLength"]):
            errors.append(f"{path}: longer than maxLength {schema['maxLength']}")
    if isinstance(node, (int, float)) and not isinstance(node, bool):
        if "minimum" in schema and node < schema["minimum"]:
            errors.append(f"{path}: {node} below minimum {schema['minimum']}")
        if "maximum" in schema and node > schema["maximum"]:
            errors.append(f"{path}: {node} above maximum {schema['maximum']}")
    if isinstance(node, list):
        if "minItems" in schema and len(node) < int(schema["minItems"]):
            errors.append(f"{path}: fewer than minItems {schema['minItems']}")
        if "maxItems" in schema and len(node) > int(schema["maxItems"]):
            errors.append(f"{path}: more than maxItems {schema['maxItems']}")
        if schema.get("uniqueItems"):
            seen = [json.dumps(item, sort_keys=True, default=str) for item in node]
            if len(set(seen)) != len(seen):
                errors.append(f"{path}: items are not unique")
        if "items" in schema:
            for index, item in enumerate(node):
                _validate_node(item, schema["items"], root, f"{path}[{index}]", errors)
    if isinstance(node, Mapping):
        for key in schema.get("required", []) or []:
            if key not in node:
                errors.append(f"{path}: missing required property {key!r}")
        props = schema.get("properties", {}) or {}
        if "minProperties" in schema and len(node) < int(schema["minProperties"]):
            errors.append(f"{path}: fewer than minProperties {schema['minProperties']}")
        additional = schema.get("additionalProperties", True)
        for key, value in node.items():
            if key in props:
                _validate_node(value, props[key], root, f"{path}/{key}", errors)
            elif additional is False:
                errors.append(f"{path}: unexpected property {key!r}")
            elif isinstance(additional, Mapping):
                _validate_node(value, additional, root, f"{path}/{key}", errors)


def validate_instance(instance: Any, schema: Mapping[str, Any]) -> List[str]:
    """Validate an instance against the schema; return human-readable findings."""
    assert_schema_supported(schema)
    errors: List[str] = []
    _validate_node(instance, schema, schema, "$", errors)
    return errors


# --------------------------------------------------------------------------- #
# Matrix document -> typed model
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Actor:
    """One identity inside a principal (the unit separation of duties reasons about)."""

    id: str
    role: str
    posture: str
    model_tier: str
    merge_authority: bool
    principal: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "role": self.role,
            "posture": self.posture,
            "model_tier": self.model_tier,
            "merge_authority": self.merge_authority,
            "principal": self.principal,
        }


@dataclass(frozen=True)
class Principal:
    """An authority holder: a per-repo fleet, or the single enterprise controller."""

    id: str
    kind: str
    scope: Tuple[str, ...]
    cross_repo: bool
    admin_rights: Tuple[str, ...]
    actors: Tuple[Actor, ...]

    def actor(self, actor_id: str) -> Optional[Actor]:
        for one in self.actors:
            if one.id == actor_id:
                return one
        return None

    def covers(self, repo_id: str) -> bool:
        return repo_id in self.scope

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "scope": list(self.scope),
            "cross_repo": self.cross_repo,
            "admin_rights": list(self.admin_rights),
            "actors": [one.to_dict() for one in self.actors],
        }


@dataclass(frozen=True)
class Repo:
    """A repo under governance: who administers it, and what it delegates."""

    id: str
    fleet: str
    gate: str
    admin_rights: Tuple[str, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "fleet": self.fleet,
            "gate": self.gate,
            "admin_rights": list(self.admin_rights),
        }


@dataclass(frozen=True)
class WorkItem:
    """A work item under closure governance (evidence stays a raw record)."""

    id: str
    repo: str
    head_sha: Optional[str]
    sod: Mapping[str, str]
    evidence: Mapping[str, Mapping[str, Any]]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "repo": self.repo,
            "head_sha": self.head_sha,
            "sod": dict(self.sod),
            "evidence": {slot: dict(record) for slot, record in self.evidence.items()},
        }


@dataclass(frozen=True)
class Matrix:
    """A loaded authority matrix plus its assessment.

    ``faults`` are CANNOT-ASSESS findings (the engine cannot read/decide on this
    document), ``violations`` are NOT-OK findings (the document is readable and
    is wrong). An empty pair is a valid matrix.
    """

    document: Mapping[str, Any]
    repos: Tuple[Repo, ...] = ()
    principals: Tuple[Principal, ...] = ()
    work_items: Tuple[WorkItem, ...] = ()
    violations: Tuple[str, ...] = ()
    faults: Tuple[str, ...] = ()

    # -- lookups ------------------------------------------------------------
    def repo(self, repo_id: str) -> Optional[Repo]:
        for one in self.repos:
            if one.id == repo_id:
                return one
        return None

    def principal(self, principal_id: str) -> Optional[Principal]:
        for one in self.principals:
            if one.id == principal_id:
                return one
        return None

    def actor(self, actor_id: str) -> Optional[Actor]:
        for principal in self.principals:
            found = principal.actor(actor_id)
            if found is not None:
                return found
        return None

    def work_item(self, item_id: str) -> Optional[WorkItem]:
        for one in self.work_items:
            if one.id == item_id:
                return one
        return None

    # -- assessment ---------------------------------------------------------
    @property
    def assessable(self) -> bool:
        return not self.faults

    @property
    def valid(self) -> bool:
        return not self.faults and not self.violations

    def verdict(self) -> Tuple[Verdict, List[str]]:
        if self.faults:
            return Verdict.CANNOT_ASSESS, list(self.faults)
        if self.violations:
            return Verdict.DENY, list(self.violations)
        return Verdict.ALLOW, []

    def gate_decision(self) -> Decision:
        """The matrix's own validity as a Decision (used by the gate and the CLI)."""
        verdict, findings = self.verdict()
        if verdict is Verdict.CANNOT_ASSESS:
            return _cannot_assess("matrix-fault", "; ".join(findings), findings=findings)
        if verdict is Verdict.DENY:
            return _deny("matrix-invalid", "; ".join(findings), findings=findings)
        return _allow(
            f"{len(self.repos)} repo(s), {len(self.principals)} principal(s), "
            f"{len(self.work_items)} work item(s)",
            repos=len(self.repos),
            principals=len(self.principals),
            work_items=len(self.work_items),
        )


def read_matrix_document(path: Path | str = DEFAULT_MATRIX_PATH) -> Mapping[str, Any]:
    """Read the matrix document; raise MatrixLoadError when it cannot be read."""
    path = Path(path)
    if yaml is None:
        raise MatrixLoadError("PyYAML is not importable — the matrix cannot be parsed")
    if not path.is_file():
        raise MatrixLoadError(f"matrix not found: {path}")
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise MatrixLoadError(f"matrix unreadable: {path}: {exc}") from exc
    try:
        document = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise MatrixLoadError(f"matrix is not parseable YAML: {exc}") from exc
    if not isinstance(document, Mapping):
        raise MatrixLoadError(f"matrix root must be a mapping, got {type(document).__name__}")
    return document


def read_schema(path: Path | str = DEFAULT_SCHEMA_PATH) -> Mapping[str, Any]:
    """Read the JSON-Schema; raise SchemaError when it cannot be used."""
    path = Path(path)
    if not path.is_file():
        raise SchemaError(f"schema not found: {path}")
    try:
        schema = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SchemaError(f"schema unreadable/invalid JSON: {path}: {exc}") from exc
    if not isinstance(schema, Mapping):
        raise SchemaError(f"schema root must be an object, got {type(schema).__name__}")
    assert_schema_supported(schema)
    return schema


def _text(record: Mapping[str, Any], key: str) -> Optional[str]:
    value = record.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _build_actors(raw: Sequence[Any], principal_id: str) -> Tuple[Actor, ...]:
    actors: List[Actor] = []
    for node in raw or []:
        if not isinstance(node, Mapping):
            continue
        actors.append(
            Actor(
                id=str(node.get("id", "")),
                role=str(node.get("role", "")),
                posture=str(node.get("posture", "")),
                model_tier=str(node.get("model_tier", "")),
                merge_authority=bool(node.get("merge_authority", False)),
                principal=principal_id,
            )
        )
    return tuple(actors)


def build_matrix(
    document: Mapping[str, Any],
    schema_path: Path | str = DEFAULT_SCHEMA_PATH,
) -> Matrix:
    """Type the document, then assess it (schema findings + semantic invariants)."""
    schema = read_schema(schema_path)
    schema_errors = validate_instance(document, schema)
    faults: List[str] = []
    if document.get("version") != MATRIX_VERSION:
        faults.append(
            f"version: this engine implements version {MATRIX_VERSION}, got {document.get('version')!r}"
            " — an unreadable format is CANNOT-ASSESS, never a pass"
        )

    repos: List[Repo] = []
    for node in document.get("repos", []) or []:
        if not isinstance(node, Mapping):
            continue
        repos.append(
            Repo(
                id=str(node.get("id", "")),
                fleet=str(node.get("fleet", "")),
                gate=str(node.get("gate", "")),
                admin_rights=tuple(str(one) for one in node.get("admin_rights", []) or []),
            )
        )

    principals: List[Principal] = []
    for node in document.get("principals", []) or []:
        if not isinstance(node, Mapping):
            continue
        pid = str(node.get("id", ""))
        principals.append(
            Principal(
                id=pid,
                kind=str(node.get("kind", "")),
                scope=tuple(str(one) for one in node.get("scope", []) or []),
                cross_repo=bool(node.get("cross_repo", False)),
                admin_rights=tuple(str(one) for one in node.get("admin_rights", []) or []),
                actors=_build_actors(node.get("actors", []) or [], pid),
            )
        )

    work_items: List[WorkItem] = []
    for node in document.get("work_items", []) or []:
        if not isinstance(node, Mapping):
            continue
        sod_raw = node.get("sod")
        sod = {
            duty: str(value)
            for duty, value in (sod_raw or {}).items()
            if isinstance(sod_raw, Mapping) and isinstance(value, str)
        }
        evidence = {
            slot: dict(node[slot])
            for slot in _EVIDENCE_SLOTS
            if isinstance(node.get(slot), Mapping)
        }
        head_sha = node.get("head_sha")
        work_items.append(
            WorkItem(
                id=str(node.get("id", "")),
                repo=str(node.get("repo", "")),
                head_sha=str(head_sha) if isinstance(head_sha, str) else None,
                sod=sod,
                evidence=evidence,
            )
        )

    provisional = Matrix(
        document=document,
        repos=tuple(repos),
        principals=tuple(principals),
        work_items=tuple(work_items),
        violations=(),
        faults=tuple(faults),
    )
    if schema_errors:
        return Matrix(
            document=document,
            repos=provisional.repos,
            principals=provisional.principals,
            work_items=provisional.work_items,
            violations=tuple(f"schema: {one}" for one in schema_errors),
            faults=provisional.faults,
        )
    return Matrix(
        document=document,
        repos=provisional.repos,
        principals=provisional.principals,
        work_items=provisional.work_items,
        violations=check_invariants(provisional),
        faults=provisional.faults,
    )


def load_matrix(
    path: Path | str = DEFAULT_MATRIX_PATH,
    schema_path: Path | str = DEFAULT_SCHEMA_PATH,
) -> Matrix:
    """Load and assess the shipped matrix (the only I/O this module performs)."""
    return build_matrix(read_matrix_document(path), schema_path)


# --------------------------------------------------------------------------- #
# Semantic invariants (document validity — NOT-OK, never a silent pass)
# --------------------------------------------------------------------------- #


def check_invariants(matrix: Matrix) -> Tuple[str, ...]:
    """The rules the schema cannot express; each finding names the offending subject."""
    findings: List[str] = []

    repo_ids = [one.id for one in matrix.repos]
    if len(set(repo_ids)) != len(repo_ids):
        findings.append("repo-ids-unique: repos[] declares the same repo id more than once")

    principal_ids = [one.id for one in matrix.principals]
    if len(set(principal_ids)) != len(principal_ids):
        findings.append("principal-ids-unique: principals[] declares the same principal id more than once")

    all_actors = [actor.id for one in matrix.principals for actor in one.actors]
    if len(set(all_actors)) != len(all_actors):
        findings.append("actor-ids-unique: an actor id is declared by more than one principal")

    item_ids = [one.id for one in matrix.work_items]
    if len(set(item_ids)) != len(item_ids):
        findings.append("work-item-ids-unique: work_items[] declares the same item id more than once")

    cross_repo = [one for one in matrix.principals if one.cross_repo]
    if len(cross_repo) != 1:
        findings.append(
            "cross-repo-single: exactly one principal may be cross_repo: true "
            f"(found {len(cross_repo)}: {sorted(one.id for one in cross_repo) or 'none'})"
        )
    else:
        sole = cross_repo[0]
        if sole.kind != "enterprise-controller":
            findings.append(
                f"cross-repo-kind: the cross_repo principal {sole.id!r} must be kind "
                f"'enterprise-controller', not {sole.kind!r}"
            )
        if sorted(sole.scope) != sorted(repo_ids):
            findings.append(
                f"enterprise-covers-all: cross_repo principal {sole.id!r} must be scoped to every declared "
                f"repo (scope={sorted(sole.scope)}, repos={sorted(repo_ids)})"
            )

    controllers = [one for one in matrix.principals if one.kind == "enterprise-controller"]
    if len(controllers) != 1:
        findings.append(f"enterprise-single: exactly one enterprise-controller is required (found {len(controllers)})")

    for principal in matrix.principals:
        if principal.kind != "repo-fleet":
            continue
        if len(principal.scope) != 1:
            findings.append(
                f"fleet-scope-single: repo-fleet {principal.id!r} must be scoped to exactly one repo "
                f"(scope={list(principal.scope)}) — a fleet that spans repos is the cross-repo hole"
            )
            continue
        repo = matrix.repo(principal.scope[0])
        if repo is None:
            findings.append(
                f"scope-repo-declared: repo-fleet {principal.id!r} is scoped to undeclared repo "
                f"{principal.scope[0]!r}"
            )
            continue
        if repo.fleet != principal.id:
            findings.append(
                f"fleet-scope-single: repo {repo.id!r} names fleet {repo.fleet!r} but {principal.id!r} "
                "claims it (exactly one fleet administers a repo)"
            )
        extra = sorted(set(principal.admin_rights) - set(repo.admin_rights))
        if extra:
            findings.append(
                f"fleet-rights-subset: fleet {principal.id!r} holds rights {extra} that repo {repo.id!r} "
                "does not delegate"
            )
        postures = {actor.posture for actor in principal.actors}
        missing = [duty for duty in DUTIES if duty not in postures]
        if missing:
            findings.append(
                f"fleet-duty-coverage: fleet {principal.id!r} cannot satisfy separation of duties — "
                f"no actor with posture(s) {missing}"
            )

    for principal in matrix.principals:
        if ROLLUP in principal.admin_rights and not principal.cross_repo:
            findings.append(
                f"rollup-cross-repo-only: principal {principal.id!r} holds the {ROLLUP!r} right but is "
                "not cross_repo — roll-up is inherently cross-repo"
            )
    for principal in matrix.principals:
        for actor in principal.actors:
            if actor.role == "auditor" and actor.posture != "auditor":
                findings.append(
                    f"auditor-posture: actor {actor.id!r} has role 'auditor' but posture {actor.posture!r} — "
                    "an auditor never executes what it audits"
                )
            if actor.merge_authority and actor.role != "commander":
                findings.append(
                    f"merge-authority-commander: actor {actor.id!r} holds merge authority with role "
                    f"{actor.role!r} — the commander is the only role with merge authority"
                )

    for item in matrix.work_items:
        if matrix.repo(item.repo) is None:
            findings.append(
                f"work-item-repo-undeclared: work item {item.id!r} names undeclared repo {item.repo!r}"
            )
    return tuple(findings)


# --------------------------------------------------------------------------- #
# Authority decision
# --------------------------------------------------------------------------- #


def _principal_covers_repo(principal: Principal, repo_id: str) -> bool:
    """The scoping rule, isolated so the negative controls can attack it directly."""
    return principal.covers(repo_id)


def effective_rights(principal: Principal, repo: Repo) -> Tuple[str, ...]:
    """Rights a principal actually holds on a repo (a repo may delegate less)."""
    return tuple(
        right
        for right in principal.admin_rights
        if right == ROLLUP or right in repo.admin_rights
    )


def can_act(matrix: Matrix, principal: str, repo: str, action: str) -> Decision:
    """Decide whether ``principal`` may perform ``action`` on ``repo``.

    Denies any action on a repo the principal is not scoped to. The enterprise
    controller is the only principal that may act cross-repo (every other
    principal is scoped to a single repo, and a repo absent from every scope is
    actionable by nobody). Merge additionally requires an actor that carries
    merge authority (the commander), so no cross-repo actor can land another
    repo's work.
    """
    if matrix.faults:
        return _cannot_assess("matrix-fault", "; ".join(matrix.faults), matrix_faults=list(matrix.faults))
    if matrix.violations:
        return _cannot_assess(
            "matrix-invalid",
            "; ".join(matrix.violations),
            matrix_violations=list(matrix.violations),
        )
    if action not in ACTIONS:
        return _cannot_assess(
            "action-unknown",
            f"action {action!r} is not in the closed vocabulary {list(ACTIONS)}",
            action=action,
        )
    target = matrix.repo(repo)
    if target is None:
        return _cannot_assess("repo-unknown", f"repo {repo!r} is not declared in the matrix", repo=repo)

    actor = None
    holder = matrix.principal(principal)
    if holder is None:
        actor = matrix.actor(principal)
        if actor is None:
            return _cannot_assess(
                "principal-unknown", f"principal {principal!r} is not declared in the matrix", principal=principal
            )
        holder = matrix.principal(actor.principal)
    if holder is None:  # pragma: no cover - defensive: actor without a principal
        return _cannot_assess("principal-unknown", f"principal {principal!r} is not resolvable", principal=principal)

    subject = {"principal": holder.id, "actor": actor.id if actor else None, "repo": repo, "action": action}

    if not _principal_covers_repo(holder, repo):
        return _deny(
            "out-of-scope",
            f"{holder.id!r} is scoped to {list(holder.scope)} and may not touch {repo!r}",
            **subject,
        )

    if action == ROLLUP:
        if not holder.cross_repo:
            return _deny(
                "cross-repo-denied",
                f"{holder.id!r} is not the cross-repo actor — roll-up is reserved for the enterprise controller",
                **subject,
            )
        if sorted(holder.scope) != sorted(one.id for one in matrix.repos):
            return _deny(
                "rollup-scope-incomplete",
                f"{holder.id!r} is cross_repo but does not cover every declared repo",
                **subject,
            )
        return _allow(f"{holder.id!r} may roll up across {len(matrix.repos)} repo(s)", **subject)

    if action == MERGE and (actor is None or not actor.merge_authority):
        who = actor.id if actor else holder.id
        return _deny(
            "merge-authority-denied",
            f"{who!r} does not carry merge authority — merge stays with the repo's own fleet commander",
            **subject,
        )

    if action not in effective_rights(holder, target):
        return _deny(
            "admin-right-not-granted",
            f"{holder.id!r} holds {list(holder.admin_rights)} and {repo!r} delegates "
            f"{list(target.admin_rights)} — no match for {action!r}",
            **subject,
        )
    return _allow(f"{holder.id!r} may act {action!r} on {repo!r}", **subject)


# --------------------------------------------------------------------------- #
# Separation of duties
# --------------------------------------------------------------------------- #


def _distinct_duty_violation(
    assigned: Mapping[str, Actor],
) -> Optional[Tuple[str, Tuple[str, str]]]:
    """The distinctness rule, isolated so the negative controls can attack it directly.

    Returns the denial reason code and the offending duty pair, or None when the
    executor, reviewer and auditor are three distinct principals.
    """
    for left, right, reason in (
        ("executor", "reviewer", "sod-executor-equals-reviewer"),
        ("executor", "auditor", "sod-executor-equals-auditor"),
        ("reviewer", "auditor", "sod-reviewer-equals-auditor"),
    ):
        if assigned[left].id == assigned[right].id:
            return reason, (left, right)
    return None


def _posture_violation(assigned: Mapping[str, Actor]) -> Optional[Tuple[str, str]]:
    """The posture rule, isolated so the negative controls can attack it directly.

    Returns the denial reason code and the offending duty, or None when every
    duty is filled by an actor whose posture names that duty. Note the two rules
    overlap on purpose (defence in depth): because an actor carries exactly one
    posture, a posture-correct assignment is also distinct, and a distinct
    assignment with a shared actor violates the posture rule. Disabling either
    rule alone therefore still denies a collision — both must be weakened before
    separation of duties is bypassed, and the control set proves exactly that.
    """
    for duty in DUTIES:
        actor = assigned[duty]
        if actor.posture != duty:
            if duty == "executor" and actor.role == "auditor":
                return "sod-auditor-cannot-execute", duty
            return "sod-posture-mismatch", duty
    return None


def separation_of_duties(matrix: Matrix, work_item: str) -> Decision:
    """Require executor, reviewer and auditor to be three distinct principals.

    Each duty must be filled by a distinct actor whose posture names that duty
    (an auditor never executes what it audits), whose principal is scoped to the
    work item's repo, and the auditor must not be dispatched below the executor
    it audits.
    """
    if matrix.faults:
        return _cannot_assess("matrix-fault", "; ".join(matrix.faults))
    if matrix.violations:
        return _cannot_assess("matrix-invalid", "; ".join(matrix.violations))
    item = matrix.work_item(work_item)
    if item is None:
        return _cannot_assess(
            "work-item-unknown", f"work item {work_item!r} is not declared in the matrix", work_item=work_item
        )

    subject = {"work_item": item.id, "repo": item.repo}
    assigned: Dict[str, Actor] = {}
    for duty in DUTIES:
        actor_id = item.sod.get(duty)
        if not actor_id:
            return _deny(
                "sod-duty-unassigned",
                f"work item {item.id!r} has no {duty} assigned — separation of duties cannot be satisfied "
                "by an unassigned duty",
                **subject,
            )
        actor = matrix.actor(actor_id)
        if actor is None:
            return _deny(
                "sod-actor-unknown",
                f"the {duty} {actor_id!r} of {item.id!r} is not a declared principal",
                duty=duty,
                actor=actor_id,
                **subject,
            )
        assigned[duty] = actor

    collision = _distinct_duty_violation(assigned)
    if collision is not None:
        reason, (left, right) = collision
        return _deny(
            reason,
            f"{assigned[left].id!r} is both {left} and {right} of {item.id!r} — the three duties must be "
            "filled by three distinct principals",
            pair=[left, right],
            **subject,
        )

    posture_fault = _posture_violation(assigned)
    if posture_fault is not None:
        reason, duty = posture_fault
        actor = assigned[duty]
        if reason == "sod-auditor-cannot-execute":
            return _deny(
                reason,
                f"{actor.id!r} audits but was assigned as executor of {item.id!r} — an auditor can never "
                "execute what it audits",
                duty=duty,
                actor=actor.id,
                **subject,
            )
        return _deny(
            reason,
            f"{actor.id!r} carries posture {actor.posture!r} and cannot fill the {duty} duty of {item.id!r}",
            duty=duty,
            actor=actor.id,
            **subject,
        )

    for duty in DUTIES:
        actor = assigned[duty]
        holder = matrix.principal(actor.principal)
        if holder is None or not _principal_covers_repo(holder, item.repo):
            return _deny(
                "sod-actor-out-of-scope",
                f"the {duty} {actor.id!r} belongs to {actor.principal!r}, which is not scoped to {item.repo!r}",
                duty=duty,
                actor=actor.id,
                **subject,
            )

    executor, auditor = assigned["executor"], assigned["auditor"]
    if TIER_RANK.get(auditor.model_tier, -1) < TIER_RANK.get(executor.model_tier, -1):
        return _deny(
            "sod-auditor-tier-below-executor",
            f"auditor {auditor.id!r} runs at {auditor.model_tier} while executor {executor.id!r} runs at "
            f"{executor.model_tier} — an audit cannot out-reason the work it audits",
            auditor=auditor.id,
            executor=executor.id,
            **subject,
        )
    return _allow(
        f"{executor.id} executed, {assigned['reviewer'].id} reviewed, {auditor.id} audits {item.id}",
        **subject,
    )


# --------------------------------------------------------------------------- #
# End-to-end closure
# --------------------------------------------------------------------------- #


def _check_evidence(
    matrix: Matrix,
    item: WorkItem,
    slot: str,
    record: Optional[Mapping[str, Any]],
    expect_sha: Optional[str],
) -> Optional[str]:
    """Return the first denial reason code for one evidence slot, or None when it is real."""
    slug = _SLOT_SLUG[slot]
    if not isinstance(record, Mapping):
        return f"closure-{slug}-evidence-missing"

    command = _text(record, "command")
    if command is None:
        return f"closure-{slug}-evidence-command-missing"

    output = record.get("output")
    if not isinstance(output, str) or not output.strip():
        return f"closure-{slug}-evidence-output-missing"

    exit_code = record.get("exit_code")
    if not isinstance(exit_code, int) or isinstance(exit_code, bool) or exit_code != 0:
        return f"closure-{slug}-evidence-failed"

    sha = _text(record, "git_sha")
    if sha is None or not _SHA_RE.match(sha):
        return f"closure-{slug}-evidence-sha-missing"
    if expect_sha and sha != expect_sha:
        return f"closure-{slug}-evidence-sha-mismatch"

    repo = _text(record, "repo")
    if repo != item.repo:
        return f"closure-{slug}-evidence-repo-mismatch"

    recorder = _text(record, "recorded_by")
    if recorder is None:
        return f"closure-{slug}-evidence-recorder-unknown"
    holder = matrix.principal(recorder) or matrix.actor(recorder)
    if holder is None:
        return f"closure-{slug}-evidence-recorder-unknown"
    principal = holder if isinstance(holder, Principal) else matrix.principal(holder.principal)
    if principal is None or not _principal_covers_repo(principal, item.repo):
        return f"closure-{slug}-evidence-recorder-out-of-scope"
    return None


def is_closed(matrix: Matrix, work_item: str) -> Decision:
    """True only when both the item's gate AND the repo's ``make verify`` evidence are real.

    A missing or empty evidence field is an explicit DENIAL, never a pass
    (issue #150). Failing work never closes: ``exit_code`` must be 0 — never
    merge failing work.
    """
    if matrix.faults:
        return _cannot_assess("matrix-fault", "; ".join(matrix.faults))
    if matrix.violations:
        return _cannot_assess("matrix-invalid", "; ".join(matrix.violations))
    item = matrix.work_item(work_item)
    if item is None:
        return _cannot_assess(
            "work-item-unknown", f"work item {work_item!r} is not declared in the matrix", work_item=work_item
        )

    subject = {"work_item": item.id, "repo": item.repo, "head_sha": item.head_sha}
    records: Dict[str, Optional[Mapping[str, Any]]] = {}
    for slot in _EVIDENCE_SLOTS:
        record = item.evidence.get(slot)
        reason = _check_evidence(matrix, item, slot, record, item.head_sha)
        if reason is not None:
            return _deny(
                reason,
                f"work item {item.id!r} cannot be closed: {slot} is not real recorded evidence",
                slot=slot,
                **subject,
            )
        records[slot] = record

    shas = [str((records[slot] or {}).get("git_sha", "")) for slot in _EVIDENCE_SLOTS]
    if any(not _SHA_RE.match(one) for one in shas) or len(set(shas)) != 1:
        return _deny(
            "closure-evidence-sha-divergence",
            f"the gate and verify evidence of {item.id!r} do not name one real commit ({shas}) — closure "
            "evidence must describe a single commit",
            shas=shas,
            **subject,
        )
    return _allow(
        f"{item.id} closed: gate + make verify recorded green at {shas[0]}",
        **subject,
    )


def closure_report(matrix: Matrix) -> List[Decision]:
    """Closure decisions for every declared work item (shipped order)."""
    return [is_closed(matrix, item.id) for item in matrix.work_items]


# --------------------------------------------------------------------------- #
# Overlays (used by the declared controls to attack the shipped matrix in memory)
# --------------------------------------------------------------------------- #


def apply_overlay(document: Mapping[str, Any], overlay: Mapping[str, Any]) -> Dict[str, Any]:
    """Deep-merge ``overlay`` into ``document``; lists merge by ``id`` when keyed.

    The overlay never touches disk: it lets a control assert a verdict against a
    *mutated* matrix (a second cross-repo principal, a SoD collision, an empty
    evidence field) without shipping that mutation as a document.
    """
    merged = copy.deepcopy(dict(document))
    _merge_into(merged, overlay)
    return merged


def _merge_into(target: Dict[str, Any], overlay: Mapping[str, Any]) -> None:
    for key, value in overlay.items():
        if isinstance(value, Mapping) and isinstance(target.get(key), Mapping):
            _merge_into(target[key], value)
        elif isinstance(value, list) and isinstance(target.get(key), list):
            target[key] = _merge_lists(target[key], value)
        else:
            target[key] = copy.deepcopy(value)


def _merge_lists(base: List[Any], patch: List[Any]) -> List[Any]:
    keyed = all(isinstance(one, Mapping) and "id" in one for one in base + patch)
    if not keyed:
        return copy.deepcopy(patch)
    merged = copy.deepcopy(base)
    index = {one["id"]: position for position, one in enumerate(merged)}
    for node in patch:
        position = index.get(node["id"])
        if position is None:
            merged.append(copy.deepcopy(node))
        elif isinstance(node, Mapping):
            _merge_into(merged[position], node)
        else:  # pragma: no cover - keyed lists hold mappings
            merged[position] = copy.deepcopy(node)
    return merged


def matrix_from_overlay(
    document: Mapping[str, Any],
    overlay: Optional[Mapping[str, Any]] = None,
    schema_path: Path | str = DEFAULT_SCHEMA_PATH,
) -> Matrix:
    """Assess the shipped document with an optional in-memory overlay applied."""
    return build_matrix(apply_overlay(document, overlay or {}), schema_path)
