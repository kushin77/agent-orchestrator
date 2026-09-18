#!/usr/bin/env python3
"""The declared environment-variable surface, measured against the tree (#944).

`infra/env/registry.yaml` is the one place a variable a deploy or a local run
needs is declared. This module is what makes that declaration binding rather than
decorative: it measures the tree and refuses, **by name**, every way the
declaration and the code can drift apart.

Findings (each is provoked by `scripts/check-env-surface.sh`, so none is a
formality):

* ``declaration-schema``        — a required field is absent or blank, a name is
  declared twice, ``secret`` is not a boolean, or ``required_by`` names something
  outside the closed vocabulary. A declaration a reader cannot trust is worse
  than none.
* ``undeclared-env-read``       — the code reads a variable that is neither
  declared nor exempted, so it reached production with no record. This is the
  defect the registry exists for: ``AO_SURFACE_REGISTRY`` and ``AO_EDGE_HOST``
  both shipped this way.
* ``declared-env-unread``       — the declaration names a variable nothing reads
  (and no recorded reader mentions), so the surface has grown a stale entry.
* ``exemption-without-reason``  — an exemption with no reason, or one too short to
  be one. An exemption without a reason is indistinguishable from an omission.
* ``reader-path-missing``       — a recorded reader path that is not in the tree.
* ``indirect-read-not-recorded``— a read the scanner cannot follow
  (``os.environ.get(name)``) at a site the registry does not record. Pretending a
  statically-unresolvable read is closed would be a false green, so the
  unmeasurable part of the surface is named and a NEW one fails.
* ``secret-literal-in-tree``    — a variable declared ``secret: true`` carried as
  a literal value in a file (GR-6). A reference or a placeholder is not a leak; a
  value is.

The scanner is deliberately NOT a grep. The console reads its environment through
module-level constants (``os.environ.get(JWKS_ENV)`` where
``JWKS_ENV = "PORTAL_AUTH_GATE_JWKS"``), so a textual scan finds **nothing** in
``portal/`` while the console reads four variables — a false green of exactly the
kind this issue is about. Reads are therefore resolved with an AST, and an
indirection that cannot be resolved is a finding rather than a silent zero.

Exit-code contract (repo convention, GR-12): 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
CANNOT-ASSESS must never be reported as a pass.

Usage::

    python3 infra/env/surface.py check --root .
    python3 infra/env/surface.py check --root . --registry <path> --json
    python3 infra/env/surface.py describe --root .
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

REGISTRY_RELPATH = Path("infra") / "env" / "registry.yaml"
REGISTRY_SCHEMA = "cmr.env-surface/registry-v1"

# Finding codes. Every one is driven deliberately by the gate's provocations.
CODE_SCHEMA = "declaration-schema"
CODE_UNDECLARED = "undeclared-env-read"
CODE_UNREAD = "declared-env-unread"
CODE_EXEMPT_REASON = "exemption-without-reason"
CODE_READER_MISSING = "reader-path-missing"
CODE_INDIRECT = "indirect-read-not-recorded"
CODE_SECRET_LITERAL = "secret-literal-in-tree"

# The closed vocabulary of what supplies a variable. A name outside it is a
# declaration defect: "required by the vibes" is not a supply path.
REQUIRED_BY = ("deploy", "local-run", "ops", "tooling")

# A reason is prose, not a token. Below this it cannot distinguish an exemption
# from an omission.
MIN_REASON = 40

ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,}$")

# A value that is a reference or a placeholder is not a leak. Mirrors the
# vocabulary `infra/portal/auth_env.py` uses for the same judgement.
INDIRECTION_MARKERS = ("${", "$(", "{{", "os.environ", "env.", "getenv(")
PLACEHOLDER_MARKERS = (
    "example",
    "placeholder",
    "change-me",
    "changeme",
    "set-me",
    "replace",
    "<",
    "...",
    "xxx",
    "todo",
    "your-",
)

TEST_DIR = "tests"


class RegistryUnavailable(Exception):
    """The declaration could not be read (missing, malformed, inconsistent)."""


@dataclass(frozen=True)
class Variable:
    name: str
    purpose: str
    secret: bool
    required_by: Tuple[str, ...]
    readers: Tuple[str, ...]


@dataclass(frozen=True)
class Exemption:
    name: str
    reason: str


@dataclass(frozen=True)
class IndirectRead:
    """A read the scanner cannot follow: which file, and how many such sites.

    Recorded as a COUNT rather than the call's text: the text is an
    implementation detail of the AST, while a count answers the question that
    matters — has the unmeasurable part of this surface grown?
    """

    path: str
    sites: int
    reason: str


@dataclass(frozen=True)
class Registry:
    schema: str
    roots: Tuple[str, ...]
    exclude: Mapping[str, str]
    variables: Tuple[Variable, ...]
    exempt: Tuple[Exemption, ...]
    indirect_reads: Tuple[IndirectRead, ...]

    def declared_names(self) -> Set[str]:
        return {variable.name for variable in self.variables}

    def exempt_names(self) -> Set[str]:
        return {exemption.name for exemption in self.exempt}

    def secret_names(self) -> Set[str]:
        return {variable.name for variable in self.variables if variable.secret}

    def readers_by_name(self) -> Mapping[str, Tuple[str, ...]]:
        out: Dict[str, Tuple[str, ...]] = {
            variable.name: variable.readers for variable in self.variables
        }
        for exemption in self.exempt:
            out.setdefault(exemption.name, ())
        return out


# -- the declaration ----------------------------------------------------------


def _require_mapping(value: Any, what: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RegistryUnavailable(f"{what} must be a mapping")
    return value


def _require_list(value: Any, what: str) -> List[Any]:
    if not isinstance(value, list):
        raise RegistryUnavailable(f"{what} must be a list")
    return value


def load_registry(path: Path) -> Registry:
    try:
        import yaml  # noqa: PLC0415 - optional dependency, resolved on demand
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise RegistryUnavailable(f"PyYAML is not installed: {exc}") from exc

    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise RegistryUnavailable(f"cannot read {path}: {exc}") from exc
    except Exception as exc:  # yaml.YAMLError and friends
        raise RegistryUnavailable(f"{path} is not valid YAML: {exc}") from exc

    raw = _require_mapping(raw, f"{path}")
    if str(raw.get("schema") or "") != REGISTRY_SCHEMA:
        raise RegistryUnavailable(
            f"{path} declares schema {raw.get('schema')!r}, expected {REGISTRY_SCHEMA!r}"
        )

    scan = _require_mapping(raw.get("scan"), "scan")
    roots = tuple(str(item) for item in _require_list(scan.get("roots"), "scan.roots"))
    if not roots:
        raise RegistryUnavailable("scan.roots must name at least one root")
    exclude: Dict[str, str] = {}
    for entry in _require_list(scan.get("exclude") or [], "scan.exclude"):
        entry = _require_mapping(entry, "scan.exclude[]")
        exclude[str(entry.get("path") or "")] = str(entry.get("reason") or "")

    variables: List[Variable] = []
    for entry in _require_list(raw.get("variables") or [], "variables"):
        entry = _require_mapping(entry, "variables[]")
        variables.append(
            Variable(
                name=str(entry.get("name") or ""),
                purpose=str(entry.get("purpose") or ""),
                # Kept verbatim: `secret` is validated by the checker, never
                # coerced here, so a missing field is a finding and not a False.
                secret=entry.get("secret"),
                required_by=tuple(
                    str(item) for item in (entry.get("required_by") or [])
                ),
                readers=tuple(str(item) for item in (entry.get("readers") or [])),
            )
        )
    if not variables:
        raise RegistryUnavailable("the registry declares no variables")

    exempt: List[Exemption] = []
    for entry in _require_list(raw.get("exempt") or [], "exempt"):
        entry = _require_mapping(entry, "exempt[]")
        exempt.append(
            Exemption(
                name=str(entry.get("name") or ""), reason=str(entry.get("reason") or "")
            )
        )

    indirect: List[IndirectRead] = []
    for entry in _require_list(raw.get("indirect_reads") or [], "indirect_reads"):
        entry = _require_mapping(entry, "indirect_reads[]")
        sites = entry.get("sites")
        if not isinstance(sites, int) or sites < 1:
            raise RegistryUnavailable(
                f"indirect_reads entry {entry.get('path')!r} must declare sites as a "
                "positive integer"
            )
        indirect.append(
            IndirectRead(
                path=str(entry.get("path") or ""),
                sites=sites,
                reason=str(entry.get("reason") or ""),
            )
        )

    return Registry(
        schema=REGISTRY_SCHEMA,
        roots=roots,
        exclude=exclude,
        variables=tuple(variables),
        exempt=tuple(exempt),
        indirect_reads=tuple(indirect),
    )


def schema_findings(registry: Registry) -> List[str]:
    """The declaration's own integrity, before anything is measured against it."""
    findings: List[str] = []
    seen: Set[str] = set()
    for variable in registry.variables:
        if not ENV_NAME_RE.match(variable.name):
            findings.append(
                f"{CODE_SCHEMA}: {variable.name!r} is not an environment-variable name "
                "(uppercase, at least three characters)"
            )
        elif variable.name in seen:
            findings.append(
                f"{CODE_SCHEMA}: {variable.name} is declared more than once "
                "(one entry per variable)"
            )
        seen.add(variable.name)
        if len(variable.purpose.strip()) < MIN_REASON:
            findings.append(
                f"{CODE_SCHEMA}: {variable.name} declares no usable purpose — a "
                "reader cannot tell what it is for"
            )
        if not isinstance(variable.secret, bool):
            findings.append(
                f"{CODE_SCHEMA}: {variable.name}.secret is {variable.secret!r}; it must "
                "be true or false, because a reader has to know whether it is a secret"
            )
        if not variable.required_by:
            findings.append(
                f"{CODE_SCHEMA}: {variable.name} declares no required_by — what supplies "
                "it cannot be inferred"
            )
        for supplier in variable.required_by:
            if supplier not in REQUIRED_BY:
                findings.append(
                    f"{CODE_SCHEMA}: {variable.name}.required_by names {supplier!r}, "
                    f"which is not one of: {', '.join(REQUIRED_BY)}"
                )
        if not variable.readers:
            findings.append(
                f"{CODE_SCHEMA}: {variable.name} declares no readers — who reads it is "
                "the field this registry exists to record"
            )
    for exemption in registry.exempt:
        if exemption.name in seen:
            findings.append(
                f"{CODE_SCHEMA}: {exemption.name} is both declared and exempted "
                "(one or the other)"
            )
        if not ENV_NAME_RE.match(exemption.name):
            findings.append(
                f"{CODE_SCHEMA}: exemption {exemption.name!r} is not an "
                "environment-variable name"
            )
        if len(exemption.reason.strip()) < MIN_REASON:
            findings.append(
                f"{CODE_EXEMPT_REASON}: {exemption.name} is exempted without a reason — an "
                "exemption without a reason is indistinguishable from an omission"
            )
    return findings


# -- the measurement ----------------------------------------------------------


def _is_test_artifact(rel: str) -> bool:
    parts = rel.split("/")
    return TEST_DIR in parts or parts[-1].startswith("test_")


def _in_excluded_scope(rel: str, exclude: Mapping[str, str]) -> bool:
    first = rel.split("/")[0]
    return first in exclude


def _iter_python(root: Path, registry: Registry) -> Iterable[Tuple[str, Path]]:
    for scope in registry.roots:
        base = root / scope
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            rel = path.relative_to(root).as_posix()
            if _is_test_artifact(rel) or _in_excluded_scope(rel, registry.exclude):
                continue
            yield rel, path


def _module_constants(tree: ast.Module) -> Dict[str, str]:
    """Module-level ``NAME = "literal"`` constants — the console's own idiom."""
    out: Dict[str, str] = {}
    for node in tree.body:
        targets: Sequence[ast.AST] = ()
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = (node.target,)
        for target in targets:
            if not isinstance(target, ast.Name) or not target.id.isupper():
                continue
            value = getattr(node, "value", None)
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                out[target.id] = value.value
    return out


def _literal(node: Optional[ast.AST]) -> Optional[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _is_os_environ(node: ast.AST) -> bool:
    """``os.environ`` — attribute `environ` on the name `os`."""
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "environ"
        and isinstance(node.value, ast.Name)
        and node.value.id == "os"
    )


@dataclass
class Measurement:
    """What the tree actually reads, plus what the scan could not resolve."""

    reads: Dict[str, Set[str]] = field(default_factory=dict)
    indirect: Dict[str, int] = field(default_factory=dict)
    unparseable: List[str] = field(default_factory=list)

    def add(self, name: str, rel: str) -> None:
        self.reads.setdefault(name, set()).add(rel)

    def add_indirect(self, rel: str) -> None:
        self.indirect[rel] = self.indirect.get(rel, 0) + 1


def _fold_source(rel: str, path: Path, out: Measurement) -> None:
    """Parse one source file and fold its env reads into ``out``."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError, UnicodeDecodeError):
        # An unparsable source is not "no reads": the gate cannot assess the
        # file, so it must not report the surface as closed over it.
        out.unparseable.append(rel)
        return
    constants = _module_constants(tree)
    for node in ast.walk(tree):
        found: Optional[str] = None
        unresolved = False

        if isinstance(node, ast.Subscript):
            # os.environ["NAME"]
            if _is_os_environ(node.value) or (
                isinstance(node.value, ast.Name) and node.value.id == "environ"
            ):
                found = _literal(node.slice)
                unresolved = found is None
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr in ("get", "getenv"):
                owner = func.value
                is_environ = (
                    _is_os_environ(owner)
                    or (isinstance(owner, ast.Name) and owner.id == "environ")
                    or (isinstance(owner, ast.Attribute) and owner.attr == "environ")
                )
                if is_environ and node.args:
                    found = _literal(node.args[0])
                    if found is None and isinstance(node.args[0], ast.Name):
                        found = constants.get(node.args[0].id)
                    if found is None:
                        out.add_indirect(rel)
                        continue
        if found:
            out.add(found, rel)
        elif unresolved:
            out.add_indirect(rel)


def measure(root: Path, registry: Registry) -> Measurement:
    """Every env read in scope, with module-level constants resolved."""
    out = Measurement()
    for rel, path in _iter_python(root, registry):
        _fold_source(rel, path, out)
    return out


def measure_one(path: Path, registry: Registry) -> Measurement:
    """One source, wherever it lives — the gate's provocation seam.

    A gate must not dirty the worktree it measures, so the tree-shaped rules are
    provoked against a planted file outside the repository and measured with the
    same parser the tree walk uses.
    """
    out = Measurement()
    _fold_source(str(path), path, out)
    return out


def reader_findings(root: Path, registry: Registry) -> List[str]:
    """Every recorded reader must exist, and must actually mention the variable."""
    findings: List[str] = []
    for name, readers in registry.readers_by_name().items():
        for rel in readers:
            path = root / rel
            if not path.is_file():
                findings.append(
                    f"{CODE_READER_MISSING}: {name} records reader {rel}, which is not "
                    "in the tree"
                )
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                findings.append(
                    f"{CODE_READER_MISSING}: {name} records reader {rel}, which cannot be read"
                )
                continue
            if name not in text:
                findings.append(
                    f"{CODE_READER_MISSING}: {name} records reader {rel}, which never "
                    "names it — the record is the drift the registry exists to catch"
                )
    return findings


def undeclared_findings(registry: Registry, measurement: Measurement) -> List[str]:
    """Direction one: the code reads a name the declaration does not account for."""
    findings: List[str] = []
    declared = registry.declared_names()
    exempt = registry.exempt_names()
    for name in sorted(measurement.reads):
        if name in declared or name in exempt:
            continue
        where = ", ".join(sorted(measurement.reads[name]))
        findings.append(
            f"{CODE_UNDECLARED}: {where} reads {name}, which {REGISTRY_RELPATH} does not "
            "declare and does not exempt with a reason — a variable cannot ship "
            "without a record"
        )
    return findings


def unread_findings(
    root: Path, registry: Registry, measurement: Measurement
) -> List[str]:
    """Direction two: a declared variable that nothing reads is a stale entry."""
    findings: List[str] = []
    for name, readers in sorted(registry.readers_by_name().items()):
        if name in measurement.reads:
            continue
        # Not found by the scan. That is legitimate when a recorded reader names
        # it (a shell/terraform-only seam the Python scan cannot see), and a stale
        # entry when nothing does.
        if any(
            (root / rel).is_file()
            and name in (root / rel).read_text(encoding="utf-8", errors="replace")
            for rel in readers
        ):
            continue
        findings.append(
            f"{CODE_UNREAD}: {name} is declared, and nothing in the measured scope reads "
            "it — the declaration has grown a stale entry"
        )
    return findings


def parity_findings(
    root: Path, registry: Registry, measurement: Measurement
) -> List[str]:
    """The measured parity, both directions, without the secret scan."""
    findings = undeclared_findings(registry, measurement)
    findings.extend(unread_findings(root, registry, measurement))
    findings.extend(indirect_findings(root, registry, measurement))
    return findings


def indirect_findings(root: Path, registry: Registry, measurement: Measurement) -> List[str]:
    """A read the scanner cannot follow must be RECORDED, never silently dropped.

    Compared as a count per file, so both directions bite: a new unresolvable read
    in a recorded file raises the count, and one that has gone lowers it and leaves
    a stale record.
    """
    recorded = {entry.path: entry.sites for entry in registry.indirect_reads}
    findings: List[str] = []

    for rel in sorted(measurement.unparseable):
        findings.append(
            f"{CODE_INDIRECT}: the scanner could not parse {rel}, so the surface is not "
            "closed over it — an unreadable file is not an empty one"
        )

    for rel in sorted(measurement.indirect):
        measured = measurement.indirect[rel]
        if rel not in recorded:
            findings.append(
                f"{CODE_INDIRECT}: {rel} performs {measured} env read(s) the scanner cannot "
                "resolve (a dynamic name); record the file in indirect_reads with its "
                "reason, so the part of the surface that cannot be measured stays visible"
            )
        elif recorded[rel] != measured:
            findings.append(
                f"{CODE_INDIRECT}: {rel} records {recorded[rel]} unresolvable env read(s), "
                f"the tree has {measured} — the unmeasurable part of the surface changed"
            )

    for rel in sorted(set(recorded) - set(measurement.indirect)):
        findings.append(
            f"{CODE_INDIRECT}: indirect_reads records {rel}, which no longer performs an "
            "unresolvable env read — the record is stale"
        )
    return findings


# -- the secret rule ----------------------------------------------------------

_ASSIGNMENT = re.compile(r"\b([A-Z][A-Z0-9_]{2,})\s*(?:=|:)\s*(\S.*)?$")

# Files the secret rule measures. Configuration and declaration artifacts, plus
# the source that could carry a literal; a value belongs to none of them.
SECRET_SCAN_SUFFIXES = (".env", ".json", ".md", ".py", ".sh", ".tf", ".toml", ".yaml", ".yml")


def secret_findings_in(path: Path, registry: Registry) -> List[str]:
    """The secret rule over ONE file — the gate's provocation seam."""
    return _secret_scan([(str(path), path)], registry)


def secret_findings(root: Path, registry: Registry) -> List[str]:
    """A `secret: true` variable must never be carried as a literal value (GR-6)."""
    secrets = registry.secret_names()
    if not secrets:
        return []
    items: List[Tuple[str, Path]] = []
    for scope in registry.roots:
        base = root / scope
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(root).as_posix()
            if _in_excluded_scope(rel, registry.exclude):
                continue
            if not rel.endswith(SECRET_SCAN_SUFFIXES):
                continue
            if _is_test_artifact(rel):
                continue
            items.append((rel, path))
    return _secret_scan(items, registry)


def _secret_scan(items: Sequence[Tuple[str, Path]], registry: Registry) -> List[str]:
    secrets = registry.secret_names()
    if not secrets:
        return []
    # A cheap byte pre-filter before decoding and line-scanning: the tree holds
    # thousands of files, and all but a handful mention no declared secret at all.
    needles = tuple(name.encode("utf-8") for name in secrets)
    findings: List[str] = []
    for rel, path in items:
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if not any(needle in data for needle in needles):
            continue
        text = data.decode("utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if len(line) > 4000:
                continue
            match = _ASSIGNMENT.search(line)
            if match is None or match.group(1) not in secrets:
                continue
            value = (match.group(2) or "").strip().strip("'\"")
            if not value:
                continue
            if any(marker in value for marker in INDIRECTION_MARKERS):
                continue
            if any(marker in value.lower() for marker in PLACEHOLDER_MARKERS):
                continue
            findings.append(
                f"{CODE_SECRET_LITERAL}: {rel}:{lineno} carries a value for "
                f"{match.group(1)}, which is declared secret — declare a reference, "
                "never the material (GR-6)"
            )
    return findings


# -- driver -------------------------------------------------------------------


def declaration_findings(root: Path, registry: Registry) -> List[str]:
    """The declaration's own integrity — no tree walk, no measurement."""
    findings = schema_findings(registry)
    findings.extend(reader_findings(root, registry))
    return findings


def check(root: Path, registry: Registry) -> List[str]:
    measurement = measure(root, registry)
    findings = declaration_findings(root, registry)
    findings.extend(parity_findings(root, registry, measurement))
    findings.extend(secret_findings(root, registry))
    return findings


def describe(root: Path, registry: Registry) -> List[str]:
    lines = [
        f"declared variables: {len(registry.variables)} "
        f"({len(registry.secret_names())} secret, {len(registry.exempt)} exempted)",
    ]
    for variable in registry.variables:
        mark = "secret" if variable.secret else "plain "
        lines.append(
            f"  {mark} {variable.name:<26} {', '.join(variable.required_by):<22} "
            f"{', '.join(variable.readers)}"
        )
    for exemption in registry.exempt:
        lines.append(f"  exempt {exemption.name:<26} {exemption.reason.split('.')[0]}.")
    return lines


def _report(
    findings: List[str],
    as_json: bool,
    measurement: Optional[Measurement],
    registry: Registry,
) -> int:
    """Print a verdict and return the exit code for it. 0 OK / 1 NOT-OK."""
    if findings:
        for finding in findings:
            print(f"  FAIL  {finding}", file=sys.stderr)
        print(f"env-surface: {len(findings)} finding(s)", file=sys.stderr)
        return 1
    if as_json:
        print(
            json.dumps(
                {
                    "schema": REGISTRY_SCHEMA,
                    "declared": len(registry.variables),
                    "findings": [],
                    "error_count": 0,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    total = len(measurement.reads) if measurement is not None else 0
    print(
        f"  OK    the declaration holds: {len(registry.variables)} variable(s) declared "
        f"({len(registry.secret_names())} secret), {len(registry.exempt)} exempted"
        + (f", {total} read(s) measured" if measurement is not None else "")
    )
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="infra/env/surface.py",
        description="the declared environment-variable surface (#944)",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("check", "the verdict of record: declaration + parity + secrets"),
        ("check-declaration", "the declaration's own integrity (no tree walk)"),
        ("check-parity", "the measured parity, both directions (tree walk)"),
        ("describe", "print the declared surface"),
        ("scan-file", "measure ONE source file's env reads against the declaration"),
        ("scan-secret", "apply the declared-secret rule to ONE file"),
    ):
        child = sub.add_parser(name, help=help_text)
        child.add_argument("--root", default=".")
        child.add_argument("--registry", default=None)
        child.add_argument("--json", action="store_true")
        if name.startswith("scan-"):
            child.add_argument("--file", default="", help="the file to scan")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    registry_path = Path(args.registry) if args.registry else root / REGISTRY_RELPATH

    if not root.is_dir():
        print(f"env-surface: CANNOT-ASSESS — no root at {root}", file=sys.stderr)
        return 2
    if not registry_path.is_file():
        print(
            f"env-surface: CANNOT-ASSESS — no declared env surface at {registry_path}",
            file=sys.stderr,
        )
        return 2
    try:
        registry = load_registry(registry_path)
    except RegistryUnavailable as exc:
        print(f"env-surface: CANNOT-ASSESS — {exc}", file=sys.stderr)
        return 2

    if args.command == "scan-file":
        if not args.file:
            print("env-surface: CANNOT-ASSESS — --file is required", file=sys.stderr)
            return 2
        target = Path(args.file)
        if not target.is_file():
            print(
                f"env-surface: CANNOT-ASSESS — no file at {target}", file=sys.stderr
            )
            return 2
        findings = undeclared_findings(registry, measure_one(target, registry))
        if findings:
            for finding in findings:
                print(f"  FAIL  {finding}", file=sys.stderr)
            return 1
        print("  OK    every env read in the file is declared or exempted")
        return 0

    if args.command == "scan-secret":
        if not args.file:
            print("env-surface: CANNOT-ASSESS — --file is required", file=sys.stderr)
            return 2
        target = Path(args.file)
        if not target.is_file():
            print(
                f"env-surface: CANNOT-ASSESS — no file at {target}", file=sys.stderr
            )
            return 2
        findings = secret_findings_in(target, registry)
        if findings:
            for finding in findings:
                print(f"  FAIL  {finding}", file=sys.stderr)
            return 1
        print("  OK    no declared-secret variable carries a literal value here")
        return 0

    if args.command == "describe":
        payload = describe(root, registry)
        if args.json:
            print(
                json.dumps(
                    {
                        "schema": REGISTRY_SCHEMA,
                        "variables": [
                            {
                                "name": v.name,
                                "secret": bool(v.secret),
                                "required_by": list(v.required_by),
                                "readers": list(v.readers),
                            }
                            for v in registry.variables
                        ],
                        "exempt": [{"name": e.name} for e in registry.exempt],
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
        else:
            for line in payload:
                print(f"  {line}")
        return 0

    # `check` is the verdict of record and always runs EVERY family. The two
    # narrower verbs exist so the gate's provocations can isolate one rule without
    # paying for a full tree walk each time; neither is ever the gate's verdict.
    if args.command == "check-declaration":
        return _report(declaration_findings(root, registry), args.json, None, registry)
    if args.command == "check-parity":
        measurement = measure(root, registry)
        return _report(
            parity_findings(root, registry, measurement), args.json, measurement, registry
        )

    measurement = measure(root, registry)
    findings = check(root, registry)

    if args.json:
        print(
            json.dumps(
                {
                    "schema": REGISTRY_SCHEMA,
                    "declared": len(registry.variables),
                    "exempt": len(registry.exempt),
                    "measured_reads": len(measurement.reads),
                    "indirect_measured": dict(sorted(measurement.indirect.items())),
                    "indirect_recorded": [
                        {"path": entry.path, "sites": entry.sites}
                        for entry in sorted(
                            registry.indirect_reads, key=lambda item: item.path
                        )
                    ],
                    "findings": findings,
                    "error_count": len(findings),
                },
                indent=2,
                sort_keys=True,
            )
        )
    if findings:
        for finding in findings:
            print(f"  FAIL  {finding}", file=sys.stderr)
        print(f"env-surface: {len(findings)} finding(s)", file=sys.stderr)
        return 1

    if not args.json:
        print(
            f"  OK    the declaration is closed over the measured tree: "
            f"{len(registry.variables)} variable(s) declared "
            f"({len(registry.secret_names())} secret), {len(registry.exempt)} exempted, "
            f"{len(measurement.reads)} read(s) measured, "
            f"{len(registry.indirect_reads)} indirect read(s) recorded"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
