"""Input loading + validation for the enterprise roll-up (issue #151).

Reading is the only thing this module does. The org declaration and the per-repo
fleet inventories are parsed, validated against ``schema.yaml``, and turned into
immutable facts; every input's sha256 travels with the facts so the report can
prove which bytes produced it.

Failures are collected rather than raised *while loading a directory* — an
operator with three broken declarations needs all three names, not the first —
but a failure is never quietly dropped: ``LoadedInputs.problems`` is reported as
CANNOT-ASSESS findings, which no OK status survives.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from model import Org, RepoFleet, SmeFact, finding, file_digest
import schema as contract

ORG_RELPATH = Path("governance") / "rollup" / "pilot" / "org.yaml"
SCHEMA_RELPATH = Path("governance") / "rollup" / "schema.yaml"
INVENTORY_SUFFIXES = (".yaml", ".yml")


class PyYamlMissing(Exception):
    """PyYAML is not installed, so no declaration can be read."""


@dataclass(frozen=True)
class InputProblem:
    """A declaration that could not be read or did not satisfy the schema."""

    kind: str
    path: str
    messages: Tuple[str, ...]

    def to_finding(self) -> Any:
        return finding(
            "INPUT_%s" % self.kind.upper(),
            "cannot-assess",
            self.path,
            "%s %s could not be assessed: %s"
            % (self.kind, self.path, "; ".join(self.messages)),
            "correct the declaration (see governance/rollup/schema.yaml)",
        )


@dataclass(frozen=True)
class LoadedInputs:
    org: Optional[Org]
    fleets: Tuple[RepoFleet, ...]
    problems: Tuple[InputProblem, ...]
    inputs: Tuple[Tuple[str, str, str], ...]  # (kind, path, sha256)
    schema_path: str
    schema_digest: str


def _yaml() -> Any:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise PyYamlMissing("PyYAML is not installed: %s" % exc) from exc
    return yaml


def _normalise(value: Any) -> Any:
    """Coerce scalar YAML conveniences into the schema's declared types.

    PyYAML resolves an unquoted ``2026-09-07`` to ``datetime.date``, which is
    the same declaration a human means by the string. Normalising it at the
    boundary keeps ``schema.yaml`` honest (dates are strings) without making an
    operator quote every date to pass the contract. Nothing else is coerced: a
    number is never stringified and a string is never parsed.
    """
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _normalise(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalise(item) for item in value]
    return value


def load_schema(path: Path) -> Dict[str, Any]:
    """Parse the contract, refusing it if it uses an unsupported keyword."""
    document = _yaml().safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise contract.SchemaUnsupported("schema %s is not a mapping" % path)
    contract.check_keywords(contract.definition(document, "org"), document)
    contract.check_keywords(contract.definition(document, "inventory"), document)
    return document


def _parse(path: Path, kind: str) -> Tuple[Optional[Dict[str, Any]], Optional[InputProblem]]:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        return None, InputProblem(kind, str(path), ("unreadable: %s" % exc,))
    if not text.strip():
        return None, InputProblem(kind, str(path), ("document is empty",))
    try:
        document = _yaml().safe_load(text)
    except Exception as exc:  # yaml.YAMLError and anything the loader raises
        return None, InputProblem(kind, str(path), ("not valid YAML: %s" % exc,))
    if not isinstance(document, dict):
        return None, InputProblem(kind, str(path), ("document is not a mapping",))
    return _normalise(document), None


def _validate(
    document: Dict[str, Any], root: Dict[str, Any], definition: str, path: Path, kind: str
) -> Optional[InputProblem]:
    try:
        violations = contract.validate(document, contract.definition(root, definition), root)
    except contract.SchemaUnsupported as exc:
        return InputProblem(kind, str(path), (str(exc),))
    if violations:
        return InputProblem(kind, str(path), tuple(violations))
    return None


def load_org(path: Path, root: Dict[str, Any]) -> Tuple[Optional[Org], Optional[InputProblem]]:
    path = Path(path)
    document, problem = _parse(path, "org")
    if problem is not None:
        return None, problem
    assert document is not None
    problem = _validate(document, root, "org", path, "org")
    if problem is not None:
        return None, problem

    enterprise = document["enterprise"]
    tenants: List[Tuple[str, str, float, Tuple[str, ...]]] = []
    for tenant in document["tenants"]:
        tenants.append(
            (
                tenant["id"],
                tenant["name"],
                float(tenant["weekly_spend_ceiling_usd"]),
                tuple(tenant["repos"]),
            )
        )
    org = Org(
        enterprise_id=enterprise["id"],
        enterprise_name=enterprise["name"],
        enterprise_ceiling_usd=float(enterprise["weekly_spend_ceiling_usd"]),
        source=document.get("source", "declared"),
        tenants=tuple(tenants),
        digest=file_digest(path),
    )
    return org, None


def load_inventory(
    path: Path, root: Dict[str, Any]
) -> Tuple[Optional[RepoFleet], Optional[InputProblem]]:
    path = Path(path)
    document, problem = _parse(path, "inventory")
    if problem is not None:
        return None, problem
    assert document is not None
    problem = _validate(document, root, "inventory", path, "inventory")
    if problem is not None:
        return None, problem

    window = document["window"]
    smes = tuple(
        SmeFact(
            repo=document["repo"],
            tenant=document["tenant"],
            id=sme["id"],
            persona_tenant=sme["persona_tenant"],
            ceiling_usd=float(sme["weekly_spend_ceiling_usd"]),
            spend_usd=float(sme["spend_usd"]),
            capacity_hours=float(sme["capacity_hours"]),
            engaged_hours=float(sme["engaged_hours"]),
            dispatched=int(sme["dispatched"]),
            closed=int(sme["closed"]),
        )
        for sme in document["smes"]
    )
    drift = document["drift"]
    fleet = RepoFleet(
        repo=document["repo"],
        tenant=document["tenant"],
        source=document.get("source", "declared"),
        window=(window["start"], window["end"]),
        smes=smes,
        drift_checks=int(drift["checks"]),
        drift_findings=int(drift["findings"]),
        digest=file_digest(path),
    )
    return fleet, None


def inventory_paths(directory: Path) -> Sequence[Path]:
    directory = Path(directory)
    if not directory.is_dir():
        return ()
    return tuple(
        sorted(
            (p for p in directory.iterdir() if p.suffix in INVENTORY_SUFFIXES and p.is_file()),
            key=lambda p: p.name,
        )
    )


def load_inputs(
    org_path: Path, inventory_dir: Path, schema_path: Path
) -> LoadedInputs:
    """Read the org and every inventory beneath ``inventory_dir``.

    Nothing is written: the loader opens each input read-only and returns facts.
    """
    problems: List[InputProblem] = []
    entries: List[Tuple[str, str, str]] = []

    try:
        root = load_schema(schema_path)
        schema_digest = file_digest(schema_path)
        entries.append(("schema", str(schema_path), schema_digest))
    except (OSError, contract.SchemaUnsupported) as exc:
        return LoadedInputs(
            org=None,
            fleets=(),
            problems=(InputProblem("schema", str(schema_path), (str(exc),)),),
            inputs=(),
            schema_path=str(schema_path),
            schema_digest="",
        )

    org, org_problem = load_org(org_path, root)
    if org_problem is not None:
        problems.append(org_problem)
    elif org is not None:
        entries.append(("org", str(org_path), org.digest))

    fleets: List[RepoFleet] = []
    paths = inventory_paths(inventory_dir)
    if not paths:
        problems.append(
            InputProblem("inventory-dir", str(inventory_dir), ("no *.yaml inventory found",))
        )
    for path in paths:
        fleet, problem = load_inventory(path, root)
        if problem is not None:
            problems.append(problem)
            continue
        assert fleet is not None
        fleets.append(fleet)
        entries.append(("inventory", str(path), fleet.digest))

    return LoadedInputs(
        org=org,
        fleets=tuple(fleets),
        problems=tuple(problems),
        inputs=tuple(entries),
        schema_path=str(schema_path),
        schema_digest=schema_digest,
    )


def with_problems(report: Any, problems: Sequence[InputProblem]) -> Any:
    """Fold load problems into a report as CANNOT-ASSESS findings.

    The aggregate is kept — an operator still needs to see what is known — but
    the added findings make ``status`` CANNOT-ASSESS, so an incomplete view can
    never be read as a clean one.
    """
    if not problems:
        return report

    extra = tuple(problem.to_finding() for problem in problems)
    ordered = tuple(
        sorted(
            tuple(report.findings) + extra,
            key=lambda f: (
                {"cannot-assess": 0, "error": 1, "warning": 2, "info": 3}[f.severity],
                f.subject,
                f.code,
            ),
        )
    )
    return replace(report, findings=ordered)
