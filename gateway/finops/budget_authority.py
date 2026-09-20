#!/usr/bin/env python3
"""budget_authority.py — the budget-decision authority's model and its checker.

Issue #1495 (child lane of the #1458 register, residual row R6). The register
measured 22 enforcement-shaped `*Budget*` classes across 8 pillars, four class
names declared more than once with different semantics, and five distinct
enforcement entry points — so which class was authoritative for a given spend
decision was not readable from the tree.

`gateway/finops/budget-authority.yaml` is that reading (the declaration).
This module is its only reader: it resolves the declaration against the tree
and returns findings. `scripts/check-budget-authority.sh` is the thin gate
around it, auto-discovered into `make verify`.

WHAT IT REFUSES, BY NAME
  1. `class-unregistered`       an enforcement-shaped budget class the
                                declaration does not carry.
  2. `register-class-missing`   a declared class the tree no longer has.
  3. `duplicate-model-name`     two classes with the same name and no
                                collision row recording the pair.
  4. `collision-unresolved`     a collision row whose current names are STILL
                                a duplicate — a resolution on paper only.
  5. `collision-choice-missing` a collision row with no recorded resolution
                                and reason.
  6. `borrowed-drift`           a borrowed anchor whose source no longer
                                holds the declared value.
  7. `borrowed-member-invalid`  a value in a borrowed config that the borrowing
                                vocabulary cannot represent.
  8. `model-outcome-drift`      the declared outcome vocabulary and the code's
                                own enum disagree.
  9. `model-symbol-missing`, `maker-symbol-missing`, `collision-site-missing`,
     `delegates-target-missing` — a declared symbol that does not resolve.
 10. `disposition-invalid`, `boundary-without-owns`, `delegates-without-target`
     — a disposition outside the closed set, or a boundary that does not name
     the boundary it owns (the acceptance criterion's own requirement).

THE CENSUS BOUNDARY IS THE ISSUE'S OWN DEFINITION
  A class whose name CONTAINS one of the ten enforcement-shaped tokens
  (`BudgetEnforcer|BudgetController|BudgetGuard|BudgetLedger|BudgetPolicy|
  BudgetDecision|BudgetVerdict|BudgetLimit|BudgetBurn|BudgetState`), excluding
  test paths — the definition the register measured the 22 with. The duplicate
  rule is wider: it covers every non-test class whose name contains `Budget`.

STDLIB + PyYAML ONLY, VIA `ast` — the module imports nothing from the rest of
the package and never imports the code it inspects: a checker that imported
the modules under test could be answered by a stale `.pyc`, and it would run
`gateway/*` import side effects inside a gate.

EXIT CONTRACT (the repo's honesty tri-state)
  0  OK              the declaration resolves and the self-test passed
  1  NOT-OK          a finding, refused by name
  2  CANNOT-ASSESS   the declaration is missing/unparseable, or a bad
                     invocation — never a pass
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

AUTHORITY_REL = "gateway/finops/budget-authority.yaml"

# The issue's own definition (see the census boundary note above).
ENFORCEMENT_TOKENS = (
    "BudgetEnforcer",
    "BudgetController",
    "BudgetGuard",
    "BudgetLedger",
    "BudgetPolicy",
    "BudgetDecision",
    "BudgetVerdict",
    "BudgetLimit",
    "BudgetBurn",
    "BudgetState",
)

SKIP_DIRS = frozenset(
    {"tests", "test", "vendor", ".git", "node_modules", "__pycache__", "build", "dist", ".venv"}
)

DISPOSITIONS = frozenset({"authority", "delegates", "boundary"})

OK, NOT_OK, CANNOT_ASSESS = 0, 1, 2


# --------------------------------------------------------------------------- #
# findings
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Finding:
    """One refusal, with the code the register names and the sites it names."""

    code: str
    message: str

    def render(self) -> str:
        return f"budget-authority: FAIL {self.code} — {self.message}"


# --------------------------------------------------------------------------- #
# reading the tree (ast only — never an import)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ClassSite:
    """One class definition found in the tree."""

    rel: str
    name: str
    line: int
    bases: Tuple[str, ...]
    doc: str

    @property
    def symbol(self) -> str:
        return f"{self.rel}::{self.name}"

    def at(self) -> str:
        return f"{self.symbol}:{self.line}"


def _is_test_path(rel: Path) -> bool:
    if set(rel.parts) & {"tests", "test"}:
        return True
    name = rel.name
    return name.startswith("test_") or name.endswith("_test.py")


def iter_python_files(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root)
        if set(rel.parts) & SKIP_DIRS:
            continue
        if _is_test_path(rel):
            continue
        yield path


def iter_class_sites(root: Path) -> List[ClassSite]:
    """Every non-test class whose name contains `Budget`, in tree order."""
    sites: List[ClassSite] = []
    for path in iter_python_files(root):
        rel = str(path.relative_to(root))
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and "Budget" in node.name:
                sites.append(
                    ClassSite(
                        rel=rel,
                        name=node.name,
                        line=node.lineno,
                        bases=tuple(ast.unparse(b) for b in node.bases),
                        doc=(ast.get_docstring(node) or "").strip(),
                    )
                )
    return sites


def enforcement_shaped(sites: Sequence[ClassSite]) -> List[ClassSite]:
    """The census: the classes the issue's own definition selects."""
    return [
        s for s in sites if any(token in s.name for token in ENFORCEMENT_TOKENS)
    ]


def resolve_symbol(root: Path, symbol: str) -> Optional[ClassSite]:
    """Resolve `path::ClassName` against the tree, or None."""
    if "::" not in symbol:
        return None
    rel, _, name = symbol.partition("::")
    path = root / rel
    if not path.is_file():
        return None
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return ClassSite(
                rel=rel,
                name=name,
                line=node.lineno,
                bases=tuple(ast.unparse(b) for b in node.bases),
                doc=(ast.get_docstring(node) or "").strip(),
            )
    return None


def enum_values(root: Path, symbol: str) -> Optional[List[str]]:
    """The string values of an `Enum` class, read from source (never imported)."""
    if "::" not in symbol:
        return None
    rel, _, name = symbol.partition("::")
    path = root / rel
    if not path.is_file():
        return None
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == name:
            values: List[str] = []
            for stmt in node.body:
                if isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Constant):
                    if isinstance(stmt.value.value, str):
                        values.append(stmt.value.value)
                elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.value, ast.Constant):
                    if isinstance(stmt.value.value, str):
                        values.append(stmt.value.value)
            return values
    return None


# --------------------------------------------------------------------------- #
# reading the declaration
# --------------------------------------------------------------------------- #
def load_authority(path: Path) -> Dict[str, Any]:
    """Load the declaration. Raises CannotAssess on anything unusable."""
    try:
        import yaml
    except Exception as exc:  # pragma: no cover - PyYAML ships with the repo
        raise CannotAssess(f"PyYAML is unavailable to read {path}: {exc}") from exc
    if not path.is_file():
        raise CannotAssess(f"the authority declaration {path} does not exist")
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise CannotAssess(f"{path} is not valid YAML: {exc}") from exc
    if not isinstance(doc, dict):
        raise CannotAssess(f"{path}: the declaration root must be a mapping")
    return doc


class CannotAssess(Exception):
    """The question cannot be answered — never a pass."""


def load_document(path: Path, fmt: str) -> Any:
    """Load a borrowed source document."""
    text = path.read_text(encoding="utf-8")
    if fmt == "json":
        return json.loads(text)
    if fmt == "yaml":
        import yaml

        return yaml.safe_load(text)
    raise CannotAssess(f"unknown borrowed source format {fmt!r} for {path}")


def pointer_values(doc: Any, pointer: str) -> List[Any]:
    """Resolve a `/a/*/b` pointer to every value it reaches (`*` expands)."""
    parts = [p for p in pointer.split("/") if p]
    current: List[Any] = [doc]
    for part in parts:
        nxt: List[Any] = []
        for node in current:
            if part == "*":
                if isinstance(node, dict):
                    nxt.extend(node.values())
                elif isinstance(node, list):
                    nxt.extend(node)
            elif isinstance(node, dict) and part in node:
                nxt.append(node[part])
        current = nxt
    return current


# --------------------------------------------------------------------------- #
# the evaluation
# --------------------------------------------------------------------------- #
def evaluate(root: Path, doc: Dict[str, Any]) -> List[Finding]:
    """Resolve the declaration against `root`. The ONE code path: the gate, the
    self-test and every provocation below run exactly this function."""
    findings: List[Finding] = []

    sites = iter_class_sites(root)
    by_name: Dict[str, List[ClassSite]] = {}
    for site in sites:
        by_name.setdefault(site.name, []).append(site)

    # -- 1. the model ----------------------------------------------------- #
    model = doc.get("model") or {}
    for key in ("record", "outcome_enum", "response_policy", "maker"):
        symbol = model.get(key)
        if not symbol:
            findings.append(Finding("model-symbol-missing", f"model.{key} is not declared"))
            continue
        if resolve_symbol(root, str(symbol)) is None:
            findings.append(
                Finding(
                    "model-symbol-missing",
                    f"model.{key} = {symbol!r} does not resolve to a class in the tree",
                )
            )
    declared_outcomes = [str(v) for v in (model.get("outcomes") or [])]
    outcome_symbol = model.get("outcome_enum")
    if outcome_symbol and resolve_symbol(root, str(outcome_symbol)) is not None:
        actual = enum_values(root, str(outcome_symbol)) or []
        if sorted(actual) != sorted(declared_outcomes):
            findings.append(
                Finding(
                    "model-outcome-drift",
                    f"model.outcomes {declared_outcomes} != {outcome_symbol} values {actual}",
                )
            )

    # -- 2. borrowed anchors ---------------------------------------------- #
    for anchor in doc.get("borrowed_from") or []:
        rel = str(anchor.get("path") or "")
        pointer = str(anchor.get("pointer") or "")
        source = root / rel
        if not source.is_file():
            findings.append(
                Finding("borrowed-source-unreadable", f"borrowed source {rel} does not exist")
            )
            continue
        try:
            data = load_document(source, str(anchor.get("format") or "yaml"))
        except CannotAssess as exc:
            findings.append(Finding("borrowed-source-unreadable", str(exc)))
            continue
        except Exception as exc:
            findings.append(Finding("borrowed-source-unreadable", f"{rel}: {exc}"))
            continue
        reachable = pointer_values(data, pointer)
        if not reachable:
            findings.append(
                Finding("borrowed-pointer-missing", f"{rel}{pointer} resolves to nothing")
            )
            continue
        flat: List[Any] = []
        for item in reachable:
            if isinstance(item, list):
                flat.extend(item)
            else:
                flat.append(item)
        if "expect" in anchor:
            expected = anchor["expect"]
            if isinstance(expected, list):
                if sorted(map(str, flat)) != sorted(map(str, expected)):
                    findings.append(
                        Finding(
                            "borrowed-drift",
                            f"{rel}{pointer} = {sorted(map(str, flat))} "
                            f"but this authority declares {sorted(map(str, expected))}",
                        )
                    )
            elif str(flat[0]) if flat else "" != str(expected):
                findings.append(
                    Finding("borrowed-drift", f"{rel}{pointer} = {flat} but declares {expected!r}")
                )
        elif "each_member_of" in anchor:
            allowed = enum_values(root, str(anchor["each_member_of"]))
            if allowed is None:
                findings.append(
                    Finding(
                        "borrowed-member-invalid",
                        f"{anchor['each_member_of']} does not resolve to an enum",
                    )
                )
            else:
                for value in sorted({str(v) for v in flat}):
                    if value not in allowed:
                        findings.append(
                            Finding(
                                "borrowed-member-invalid",
                                f"{rel}{pointer} names {value!r}, which "
                                f"{anchor['each_member_of']} cannot represent "
                                f"(declares {allowed})",
                            )
                        )
        elif "each_member_of_inline" in anchor:
            allowed = [str(v) for v in anchor["each_member_of_inline"]]
            for value in sorted({str(v) for v in flat}):
                if value not in allowed:
                    findings.append(
                        Finding(
                            "borrowed-member-invalid",
                            f"{rel}{pointer} names {value!r}, not one of {allowed}",
                        )
                    )

    # -- 3. makers -------------------------------------------------------- #
    for maker in doc.get("makers") or []:
        symbol = str(maker.get("symbol") or "")
        if resolve_symbol(root, symbol) is None:
            findings.append(
                Finding("maker-symbol-missing", f"maker {symbol!r} does not resolve")
            )

    # -- 4. the register -------------------------------------------------- #
    rows = doc.get("classes") or []
    registered = {str(row.get("symbol") or "") for row in rows}
    for row in rows:
        symbol = str(row.get("symbol") or "")
        if resolve_symbol(root, symbol) is None:
            findings.append(
                Finding(
                    "register-class-missing",
                    f"the register declares {symbol!r} but the tree has no such class",
                )
            )
        disposition = row.get("disposition")
        if disposition not in DISPOSITIONS:
            findings.append(
                Finding(
                    "disposition-invalid",
                    f"{symbol}: disposition {disposition!r} is not one of "
                    f"{sorted(DISPOSITIONS)}",
                )
            )
        elif disposition == "boundary" and not str(row.get("owns") or "").strip():
            findings.append(
                Finding(
                    "boundary-without-owns",
                    f"{symbol} is recorded as a boundary but does not name the "
                    f"boundary it owns",
                )
            )
        elif disposition == "delegates":
            target = str(row.get("delegates_to") or "")
            if not target:
                findings.append(
                    Finding("delegates-without-target", f"{symbol} delegates but names no target")
                )
            elif resolve_symbol(root, target) is None:
                findings.append(
                    Finding("delegates-target-missing", f"{symbol} delegates to {target!r}, absent")
                )
    for site in enforcement_shaped(sites):
        if site.symbol not in registered:
            findings.append(
                Finding(
                    "class-unregistered",
                    f"the enforcement-shaped class {site.at()} is not recorded in "
                    f"the authority register",
                )
            )

    # -- 5. collisions ---------------------------------------------------- #
    recorded: Dict[str, int] = {}
    for row in doc.get("collisions") or []:
        name = str(row.get("name") or "")
        recorded[name] = recorded.get(name, 0) + 1
        site_rows = row.get("sites") or []
        if not str(row.get("resolution") or "").strip() or not str(row.get("choice") or "").strip():
            findings.append(
                Finding(
                    "collision-choice-missing",
                    f"the {name} collision records no resolution/choice",
                )
            )
        current_names: List[str] = []
        for site_row in site_rows:
            symbol = str(site_row.get("symbol") or "")
            resolved = resolve_symbol(root, symbol)
            if resolved is None:
                findings.append(
                    Finding("collision-site-missing", f"the {name} collision names {symbol!r}, absent")
                )
                continue
            current_names.append(resolved.name)
        if len(current_names) != len(set(current_names)):
            findings.append(
                Finding(
                    "collision-unresolved",
                    f"the {name} collision is recorded but its current names are still "
                    f"a duplicate: {sorted(current_names)}",
                )
            )
        # A recorded collision is site-EXACT. Without this half a name that has a
        # collision row would be exempt from the duplicate rule, so a third
        # definition of an already-collided name could be added and the gate
        # would stay green (measured: the plant in the self-test did exactly
        # that and came back as `class-unregistered` only).
        keepers = sorted(
            str(site_row.get("symbol") or "")
            for site_row in site_rows
            if not site_row.get("was")
        )
        actual_here = by_name.get(name, [])
        actual_symbols = {site.symbol for site in actual_here}
        for site in actual_here:
            if site.symbol not in keepers:
                findings.append(
                    Finding(
                        "duplicate-model-name",
                        f"{name} is declared at "
                        + " and ".join(s.at() for s in actual_here)
                        + f" but the {name} collision records it resolving to {keepers}",
                    )
                )
                break
        for symbol in keepers:
            if symbol not in actual_symbols:
                findings.append(
                    Finding(
                        "collision-site-missing",
                        f"the {name} collision keeps the name at {symbol!r}, "
                        f"which the tree does not have",
                    )
                )
    for name, group in sorted(by_name.items()):
        if len(group) > 1 and name not in recorded:
            findings.append(
                Finding(
                    "duplicate-model-name",
                    f"{name} is declared at "
                    + " and ".join(site.at() for site in group)
                    + " with no collision row recording the pair",
                )
            )

    return findings


# --------------------------------------------------------------------------- #
# the provocation — the gate proves itself on every run
# --------------------------------------------------------------------------- #
def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def materialise(root: Path, doc: Dict[str, Any], scratch: Path) -> None:
    """Copy exactly the files the declaration names, preserving relpaths.

    The provocation drives `evaluate` over a tree assembled from the
    declaration's OWN sources — so the scan that is proven is the scan the
    repository run uses, and nothing else. `iter_class_sites` uses `rglob`, and
    a symlinked directory can defeat that, so real copies are used.
    """
    wanted = {AUTHORITY_REL}
    for row in doc.get("classes") or []:
        wanted.add(str(row.get("symbol") or "").split("::")[0])
    for row in doc.get("collisions") or []:
        for site_row in row.get("sites") or []:
            wanted.add(str(site_row.get("symbol") or "").split("::")[0])
    for row in doc.get("makers") or []:
        wanted.add(str(row.get("symbol") or "").split("::")[0])
    model = doc.get("model") or {}
    for key in ("record", "outcome_enum", "response_policy", "maker"):
        wanted.add(str(model.get(key) or "").split("::")[0])
    for anchor in doc.get("borrowed_from") or []:
        wanted.add(str(anchor.get("path") or ""))
    for rel in sorted(wanted):
        if not rel or rel == "":
            continue
        source = root / rel
        if not source.is_file():
            raise CannotAssess(f"the declaration names {rel}, which the tree does not have")
        target = scratch / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)


@dataclass(frozen=True)
class Arm:
    """One provocation: a mutation, and the finding it must produce."""

    label: str
    rel: str
    mutate: Any  # Callable[[str], str]
    expect_code: str
    expect_needle: str


def _replace_once(text: str, old: str, new: str) -> str:
    if text.count(old) < 1:
        raise CannotAssess(f"provocation anchor {old!r} is not in the file")
    return text.replace(old, new, 1)


def provocations(doc: Dict[str, Any]) -> List[Arm]:
    """The arms. Each must be REFUSED by its own code, naming its own plant."""
    arms = [
        Arm(
            label="planted duplicate name is refused",
            rel="gateway/limits/budget.py",
            mutate=lambda text: text
            + "\n\nclass BudgetPolicy:  # planted duplicate\n    pass\n",
            expect_code="duplicate-model-name",
            expect_needle="BudgetPolicy is declared at",
        ),
        Arm(
            label="planted unregistered enforcement class is refused",
            rel="gateway/limits/budget.py",
            mutate=lambda text: text + "\n\nclass PlantedBudgetEnforcer:\n    pass\n",
            expect_code="class-unregistered",
            expect_needle="PlantedBudgetEnforcer",
        ),
        Arm(
            label="a boundary recorded without the boundary it owns is refused",
            rel=AUTHORITY_REL,
            mutate=lambda text: _replace_once(
                text,
                '    owns: "one limit record (cost USD or token count) over one window"',
                '    owns: ""',
            ),
            expect_code="boundary-without-owns",
            expect_needle="BudgetLimit",
        ),
        Arm(
            label="a collision row that is still a duplicate is refused",
            rel=AUTHORITY_REL,
            mutate=lambda text: _replace_once(
                text,
                '      - symbol: "gateway/limits/budget.py::TokenBudgetDecision"\n'
                "        was: BudgetDecision\n",
                "      - symbol: \"gateway/finops/budget.py::BudgetDecision\"\n"
                "        was: BudgetDecision\n",
            ),
            expect_code="collision-unresolved",
            expect_needle="still a duplicate",
        ),
        Arm(
            label="a deleted keeper of a recorded collision is refused",
            rel="gateway/mcp/grounding.py",
            mutate=lambda text: _replace_once(
                text, "class TokenBudget:", "class TokenBudgetRecord:"
            ),
            expect_code="collision-site-missing",
            expect_needle="keeps the name at",
        ),
        Arm(
            label="a drifted borrowed anchor is refused",
            rel="governance/finops/policy.json",
            mutate=lambda text: _replace_once(text, '"auditor"', '"auditor-v2"'),
            expect_code="borrowed-drift",
            expect_needle="vocabulary/tiers",
        ),
        Arm(
            label="a borrowed config naming a policy the enum cannot represent is refused",
            rel="gateway/finops/budgets.yaml",
            mutate=lambda text: _replace_once(text, "policy: fallback", "policy: throttle"),
            expect_code="borrowed-member-invalid",
            expect_needle="throttle",
        ),
        Arm(
            label="a real source file deleted from the register is refused",
            rel="gateway/limits/budget.py",
            mutate=lambda text: _replace_once(
                text, "class BudgetController:", "class BudgetControllerGone:"
            ),
            expect_code="register-class-missing",
            expect_needle="BudgetController",
        ),
        Arm(
            label="a drifted outcome vocabulary is refused",
            rel=AUTHORITY_REL,
            mutate=lambda text: _replace_once(text, '  outcomes: ["allow", "warn", "fallback", "stop"]', '  outcomes: ["allow", "warn", "fallback"]'),
            expect_code="model-outcome-drift",
            expect_needle="model.outcomes",
        ),
    ]
    return arms


def self_test(root: Path, doc: Dict[str, Any], verbose: bool = True) -> List[str]:
    """Run every provocation against a scratch tree. Returns the failures."""
    failures: List[str] = []
    printed: List[str] = []

    def emit(line: str) -> None:
        printed.append(line)
        if verbose:
            print(line)

    scratch_root = Path(tempfile.mkdtemp(prefix="budget-authority."))
    try:
        clean = scratch_root / "clean"
        materialise(root, doc, clean)
        clean_findings = evaluate(clean, load_authority(clean / AUTHORITY_REL))
        if clean_findings:
            emit(
                "  FAIL  clean twin is not clean — the register does not describe "
                "its own sources:"
            )
            for finding in clean_findings:
                emit(f"        {finding.render()}")
            failures.append("clean twin not clean")
        else:
            emit("  ok    clean twin over the declaration's own sources -> rc 0")

        arms = provocations(doc)
        for index, arm in enumerate(arms):
            arm_dir = scratch_root / f"arm-{index}"
            materialise(root, doc, arm_dir)
            target = arm_dir / arm.rel
            before = _sha256(target)
            original = target.read_text(encoding="utf-8")
            mutated = arm.mutate(original)
            if mutated == original:
                failures.append(f"{arm.label}: the mutation did not change the file")
                emit(f"  FAIL  {arm.label} — the mutation did not change the file")
                continue
            target.write_text(mutated, encoding="utf-8")
            after = _sha256(target)
            if before == after:
                failures.append(f"{arm.label}: sha256 unchanged after the mutation")
                emit(f"  FAIL  {arm.label} — sha256 unchanged after the mutation")
                continue
            arm_findings = evaluate(arm_dir, load_authority(arm_dir / AUTHORITY_REL))
            codes = [f.code for f in arm_findings]
            hit = [
                f
                for f in arm_findings
                if f.code == arm.expect_code and arm.expect_needle in f.message
            ]
            if hit:
                emit(f"  ok    {arm.label} -> rc 1 {arm.expect_code} ({arm.expect_needle!r})")
            else:
                failures.append(
                    f"{arm.label}: expected {arm.expect_code} naming "
                    f"{arm.expect_needle!r}, observed {codes}"
                )
                emit(
                    f"  FAIL  {arm.label} — expected {arm.expect_code} naming "
                    f"{arm.expect_needle!r}, observed {codes}"
                )
                for finding in arm_findings:
                    emit(f"        {finding.render()}")
            # restore, byte-identically
            target.write_text(original, encoding="utf-8")
            if _sha256(target) != before:
                failures.append(f"{arm.label}: restore was not byte-identical")
    finally:
        shutil.rmtree(scratch_root, ignore_errors=True)
    if verbose:
        print(f"  self-test: {len(provocations(doc)) + 1} arms, {len(failures)} failed")
    return failures


# --------------------------------------------------------------------------- #
# the gate
# --------------------------------------------------------------------------- #
def describe(doc: Dict[str, Any]) -> str:
    rows = doc.get("classes") or []
    collisions = doc.get("collisions") or []
    counts: Dict[str, int] = {}
    for row in rows:
        key = str(row.get("disposition") or "?")
        counts[key] = counts.get(key, 0) + 1
    parts = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
    return (
        f"authority {doc.get('authority')} v{doc.get('authority_version')} "
        f"(issue #{doc.get('issue')}, register #{doc.get('register')}): "
        f"classes={len(rows)} ({parts}), makers={len(doc.get('makers') or [])}, "
        f"collisions={len(collisions)}, borrowed={len(doc.get('borrowed_from') or [])}"
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="budget_authority.py",
        description="resolve the budget-decision authority against a tree",
    )
    parser.add_argument("--root", default=None, help="the tree to scan (default: repo root)")
    parser.add_argument("--authority", default=None, help="the declaration path")
    parser.add_argument("--self-test", action="store_true", help="run the provocation alone")
    parser.add_argument("--list", action="store_true", help="print the declared register")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parents[2]
    authority_path = (
        Path(args.authority).resolve() if args.authority else root / AUTHORITY_REL
    )

    try:
        doc = load_authority(authority_path)
    except CannotAssess as exc:
        print(f"budget-authority: CANNOT-ASSESS — {exc}")
        return CANNOT_ASSESS

    # A scratch/foreign tree carries its own copy of the declaration; reading it
    # from the tree under test keeps the gate honest about what it measured.
    in_tree = root / AUTHORITY_REL
    if in_tree.is_file() and in_tree.resolve() != authority_path.resolve():
        try:
            doc = load_authority(in_tree)
        except CannotAssess:
            pass

    print(f"budget-authority: {describe(doc)}")

    if args.list:
        for row in doc.get("classes") or []:
            owns = str(row.get("owns") or "").replace("\n", " ")
            was = f" (was {row['was']})" if row.get("was") else ""
            print(f"  {row.get('disposition'):<9} {row.get('symbol')}{was} {owns}".rstrip())
        return OK

    print("  provocation (the gate proves itself before it looks at the tree):")
    arms = self_test(root, doc, verbose=True)
    if arms:
        return NOT_OK

    if args.self_test:
        return OK

    findings = evaluate(root, doc)

    census = enforcement_shaped(iter_class_sites(root))
    print(f"  census: {len(census)} enforcement-shaped classes in {root.name}")

    if findings:
        for finding in findings:
            print(finding.render())
        print(f"budget-authority: NOT-OK — {len(findings)} finding(s)")
        return NOT_OK

    print("budget-authority: OK — the declaration resolves, no unrecorded duplicate")
    return OK


if __name__ == "__main__":
    sys.exit(main())
