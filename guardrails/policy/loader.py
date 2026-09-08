"""Loader: discover policy files, YAML-parse documents, validate, build models.

A *bundle* is a directory (or explicit file list) of policy YAML files.  Each
file holds either a single policy document at the top level or a
``policies:`` container whose elements are single-policy documents (the CMR /
defragsuite bundle shape).  Every single-policy document is checked against
``schema/policy.schema.json`` plus semantic rules *as it is loaded* — so an
invalid policy fails at startup/deploy, never at runtime (issue #26
acceptance #1 and #5).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Iterable, List, Mapping, Optional, Sequence

import yaml

from policy.conditions import validate_condition
from policy.decision import DecisionLevel
from policy.errors import (
    DuplicatePolicyError,
    PolicyLoadError,
    PolicyValidationError,
)
from policy.model import DEFAULT_POLICY_DECISION, Policy, PolicyRule
from policy.schemas import POLICY_SCHEMA_PATH, SchemaValidationError, load_schema, validate

#: YAML suffixes loader discovery understands.
_YAML_SUFFIXES = (".yaml", ".yml")


def _schema() -> Any:
    here = Path(__file__).resolve().parent
    return load_schema(str(here / POLICY_SCHEMA_PATH))


def discover_policy_files(paths: Iterable[str]) -> List[Path]:
    """Resolve *paths* (files or directories) to a sorted, de-duplicated list.

    Directories are walked recursively for ``*.yaml``/``*.yml`` files.  Raises
    :class:`PolicyLoadError` when a path does not exist.
    """
    found: List[Path] = []
    for raw in paths:
        path = Path(raw)
        if not path.exists():
            raise PolicyLoadError(f"policy path does not exist: {path}")
        if path.is_file():
            if path.suffix in _YAML_SUFFIXES:
                found.append(path)
            continue
        if path.is_dir():
            for candidate in sorted(path.rglob("*")):
                if candidate.is_file() and candidate.suffix in _YAML_SUFFIXES:
                    found.append(candidate)
            continue
        raise PolicyLoadError(f"policy path is neither file nor directory: {path}")
    seen: List[Path] = []
    for path in sorted(found, key=lambda p: str(p)):
        if path.resolve() not in {p.resolve() for p in seen}:
            seen.append(path)
    return seen


def read_documents(path: Path) -> List[Mapping[str, Any]]:
    """Read one YAML file into single-policy documents.

    Handles a top-level policy mapping or a ``policies:`` container.  Raises
    :class:`PolicyLoadError` on unreadable/unparseable/non-mapping content.
    """
    try:
        with open(path, encoding="utf-8") as handle:
            loaded = yaml.safe_load(handle)
    except OSError as exc:
        raise PolicyLoadError(f"cannot read {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise PolicyLoadError(f"cannot parse {path}: {exc}") from exc

    if isinstance(loaded, Mapping) and "policies" in loaded:
        container = loaded["policies"]
        if not isinstance(container, list) or not container:
            raise PolicyLoadError(f"{path}: 'policies' must be a non-empty list")
        return [document for document in container if isinstance(document, Mapping)]

    if not isinstance(loaded, Mapping):
        raise PolicyLoadError(f"{path}: policy file must contain a mapping")
    return [loaded]


def _semantic_problems(document: Mapping[str, Any], where: str) -> List[str]:
    """Semantic checks that JSON Schema alone cannot express."""
    problems: List[str] = []

    rule_ids: set[str] = set()
    for index, rule in enumerate(document.get("rules") or []):
        if not isinstance(rule, Mapping):
            continue
        rule_id = rule.get("id")
        rule_where = f"{where}.rules[{index}]"
        if rule_id in rule_ids:
            problems.append(f"{rule_where}: duplicate rule id {rule_id!r} within policy")
        rule_ids.add(rule_id)

        if rule.get("decision") == "block":
            reason = rule.get("reason")
            if not isinstance(reason, str) or not reason.strip():
                problems.append(
                    f"{rule_where}: BLOCK rule {rule_id!r} must carry a non-empty 'reason' "
                    "(an unexplained block is unauditable)"
                )

        condition = rule.get("condition")
        if condition is not None:
            problems.extend(
                f"{rule_where}.condition: {problem}"
                for problem in validate_condition(condition)
            )
    return problems


def policy_from_mapping(
    document: Mapping[str, Any],
    source: str = "",
    schema: Optional[Any] = None,
) -> Policy:
    """Validate one single-policy document and build a :class:`Policy`.

    Raises :class:`PolicyValidationError` listing every schema and semantic
    violation.  The startup gate calls this so a malformed or unsafe policy
    fails the deploy.
    """
    try:
        validate(document, schema if schema is not None else _schema())
    except SchemaValidationError as exc:
        raise PolicyValidationError(f"{source}: invalid policy document\n" + str(exc)) from exc

    problems = _semantic_problems(document, "policy")
    if problems:
        raise PolicyValidationError(f"{source}: invalid policy document\n" + "\n".join(
            f"- {problem}" for problem in problems
        ))

    rules: List[PolicyRule] = []
    for rule in document["rules"]:
        rules.append(
            PolicyRule(
                id=rule["id"],
                actions=tuple(rule["actions"]),
                decision=DecisionLevel.from_token(rule["decision"]),
                reason=rule.get("reason", ""),
                condition=rule.get("condition"),
                subjects=tuple(rule.get("subjects") or ()),
                tenants=tuple(rule.get("tenants") or ()),
            )
        )
    return Policy(
        id=document["id"],
        version=int(document.get("version", 1)),
        name=str(document.get("name", document["id"])),
        description=str(document.get("description", "")),
        default=DecisionLevel.from_token(document.get("default", DEFAULT_POLICY_DECISION.value)),
        enabled=bool(document.get("enabled", True)),
        controls=tuple(document.get("controls") or ()),
        rules=tuple(rules),
        source=source,
    )


def load_policy_file(path: Path, schema: Optional[Any] = None) -> List[Policy]:
    """Load, validate and build every policy in one YAML file."""
    return [
        policy_from_mapping(document, source=str(path), schema=schema)
        for document in read_documents(path)
    ]
