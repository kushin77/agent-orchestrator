#!/usr/bin/env python3
"""Per-surface target solution-class enforcement (issue #351).

The issue-class gate (`policy.yaml`, issue #140) holds a piece of *work* to a
rung of the CMR quality ladder. This module is its surface counterpart: every
product surface declares the rung it is held to, and the check fails while the
surface sits below its declaration.

Three ideas stay separate, the same discipline `model.py` applies:

* **machine evidence** — measured against the tree by this module. A declared
  class whose machine evidence is absent is an ERROR: the surface is below its
  declared class.
* **manual evidence** — a requirement that cannot be machine-checked (a
  documented rollout/rollback procedure). It is REPORTED on every run and is
  never counted as met; silence would be a false green.
* **scope** — a declared surface root that exists but is declared by no surface
  is a finding, so no surface goes unclassified by omission.

The ladder vocabulary is closed and identical to `model.py` / `policy.yaml`.

Exit-code contract (repo convention, GR-12): 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.

Usage::

    python3 governance/conformance/surfaces.py check
    python3 governance/conformance/surfaces.py check --root . --policy <path>
    python3 governance/conformance/surfaces.py check --json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Set, Tuple

_PKG_DIR = os.path.dirname(os.path.abspath(__file__))
if _PKG_DIR not in sys.path:
    sys.path.insert(0, _PKG_DIR)

from model import SEVERITY_ERROR, SEVERITY_WARNING, Finding  # noqa: E402

SURFACES_RELPATH = Path("governance") / "conformance" / "surfaces.yaml"
SURFACES_SCHEMA = "cmr.surface-class/policy-v1"

KIND_MACHINE = "machine"
KIND_MANUAL = "manual"

# -- finding codes (each is driven deliberately by the suite) -----------------
CODE_BELOW = "surface-below-declared-class"
CODE_UNKNOWN = "surface-unknown"
CODE_PATH_MISSING = "surface-path-missing"
CODE_PATH_ESCAPES = "surface-path-escapes-root"
CODE_UNDECLARED = "surface-undeclared"
CODE_DUPLICATE = "surface-duplicate"
CODE_MANUAL = "surface-manual-requirement"

# -- evidence vocabulary ------------------------------------------------------
E_CONTRACT = "contract"
E_TESTS = "tests"
E_CONTROLS = "controls"
E_AUDIT = "audit"
E_SCHEMA = "schema"
E_GATE = "gate"
E_LIVE_SYNC = "live_sync"
E_ROLLBACK = "rollback"

EVIDENCE_KEYS = (
    E_CONTRACT,
    E_TESTS,
    E_CONTROLS,
    E_AUDIT,
    E_SCHEMA,
    E_GATE,
    E_LIVE_SYNC,
    E_ROLLBACK,
)

# File-name tokens that mark a real control and an audit trail. Kept explicit so
# the mapping is auditable rather than an implicit guess.
CONTROL_TOKENS = frozenset(
    {
        "control",
        "controls",
        "policy",
        "policies",
        "guard",
        "guardrail",
        "guardrails",
        "limit",
        "limits",
        "quota",
        "quotas",
        "killswitch",
        "ratelimit",
        "backpressure",
        "parity",
        "throttle",
    }
)
AUDIT_TOKENS = frozenset(
    {
        "audit",
        "audits",
        "auditlog",
        "audit_log",
        "audit_event",
        "event_log",
        "eventlog",
        "attestation",
        "ledger",
        "journal",
    }
)
LIVE_TOKENS = frozenset(
    {
        "live",
        "livestore",
        "live_feed",
        "livefeed",
        "feed",
        "feeds",
        "sync",
        "syncer",
        "resync",
    }
)

_NAME_SUFFIXES = (".schema.json", ".json", ".yaml", ".yml", ".py")


class SurfacePolicyUnavailable(Exception):
    """The surface policy could not be read (missing, malformed, inconsistent)."""


# -- policy ------------------------------------------------------------------


@dataclass(frozen=True)
class Evidence:
    name: str
    kind: str
    description: str = ""


@dataclass(frozen=True)
class SurfaceSpec:
    surface: str
    path: str
    declared_class: str
    notes: str = ""


@dataclass(frozen=True)
class SurfacePolicy:
    ladder: Tuple[str, ...]
    evidence: Mapping[str, Evidence]
    requirements: Mapping[str, Tuple[str, ...]]
    surfaces: Tuple[SurfaceSpec, ...]
    surface_roots: Tuple[str, ...]
    waived_roots: Tuple[str, ...]

    def rank(self, name: str) -> int:
        """Position on the ladder; ``-1`` when the name is not a rung."""
        try:
            return self.ladder.index(name)
        except ValueError:
            return -1

    def requirements_for(self, rung: str) -> Tuple[str, ...]:
        return tuple(self.requirements.get(rung, ()))

    def _by_kind(self, rung: str, kind: str) -> Tuple[str, ...]:
        return tuple(
            name
            for name in self.requirements_for(rung)
            if self.evidence[name].kind == kind
        )

    def machine_requirements(self, rung: str) -> Tuple[str, ...]:
        return self._by_kind(rung, KIND_MACHINE)

    def manual_requirements(self, rung: str) -> Tuple[str, ...]:
        return self._by_kind(rung, KIND_MANUAL)


def _as_str_tuple(value, what: str) -> Tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise SurfacePolicyUnavailable("%s must be a list" % what)
    return tuple(str(item) for item in value)


def load_surface_policy(path: Path) -> SurfacePolicy:
    try:
        import yaml  # noqa: PLC0415 - optional dependency, resolved on demand
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise SurfacePolicyUnavailable("PyYAML is not installed: %s" % exc) from exc

    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise SurfacePolicyUnavailable("cannot read policy %s: %s" % (path, exc)) from exc
    except Exception as exc:  # yaml.YAMLError and friends
        raise SurfacePolicyUnavailable(
            "policy %s is not valid YAML: %s" % (path, exc)
        ) from exc

    if not isinstance(raw, Mapping):
        raise SurfacePolicyUnavailable("policy %s must be a mapping" % path)

    ladder = _as_str_tuple(raw.get("ladder"), "ladder")
    if len(ladder) < 2:
        raise SurfacePolicyUnavailable("policy declares a ladder shorter than two rungs")

    evidence_raw = raw.get("evidence") or {}
    if not isinstance(evidence_raw, Mapping):
        raise SurfacePolicyUnavailable("policy declares `evidence` as a non-mapping")
    evidence: Dict[str, Evidence] = {}
    for name, spec in evidence_raw.items():
        spec = spec or {}
        if not isinstance(spec, Mapping):
            raise SurfacePolicyUnavailable("evidence %r must be a mapping" % name)
        if str(name) not in EVIDENCE_KEYS:
            # The vocabulary is closed: an evidence key this check cannot measure
            # would be permanently unmet, so it is refused as a policy defect
            # rather than silently reported as a gap (no-false-green).
            raise SurfacePolicyUnavailable(
                "evidence %r is not in the measurable vocabulary (%s)"
                % (name, ", ".join(EVIDENCE_KEYS))
            )
        kind = str(spec.get("kind") or "")
        if kind not in (KIND_MACHINE, KIND_MANUAL):
            raise SurfacePolicyUnavailable(
                "evidence %r declares kind %r, expected %s or %s"
                % (name, kind, KIND_MACHINE, KIND_MANUAL)
            )
        evidence[str(name)] = Evidence(
            name=str(name), kind=kind, description=str(spec.get("description") or "")
        )
    if not evidence:
        raise SurfacePolicyUnavailable("policy declares no evidence vocabulary")

    requirements_raw = raw.get("requirements") or {}
    if not isinstance(requirements_raw, Mapping):
        raise SurfacePolicyUnavailable("policy declares `requirements` as a non-mapping")
    requirements: Dict[str, Tuple[str, ...]] = {}
    for rung, names in requirements_raw.items():
        if str(rung) not in ladder:
            raise SurfacePolicyUnavailable(
                "requirements name %r, which is not a rung of the ladder" % rung
            )
        reqs = _as_str_tuple(names, "requirements.%s" % rung)
        for name in reqs:
            if name not in evidence:
                raise SurfacePolicyUnavailable(
                    "requirements.%s names %r, which is not declared evidence"
                    % (rung, name)
                )
        requirements[str(rung)] = reqs
    if not requirements:
        raise SurfacePolicyUnavailable("policy declares no requirements")

    # The rungs are cumulative: a rung must repeat every lower rung's
    # requirement and add its own, so the superset chain cannot be broken.
    previous: Set[str] = set()
    for rung in ladder:
        current = set(requirements.get(rung, ()))
        if not previous.issubset(current):
            missing = ", ".join(sorted(previous - current))
            raise SurfacePolicyUnavailable(
                "requirements.%s drops lower-rung requirement(s): %s" % (rung, missing)
            )
        previous = current

    surfaces: List[SurfaceSpec] = []
    for entry in raw.get("surfaces") or ():
        if not isinstance(entry, Mapping):
            raise SurfacePolicyUnavailable("every surface entry must be a mapping")
        name = str(entry.get("surface") or "")
        spath = str(entry.get("path") or "")
        declared = str(entry.get("declared_class") or "")
        if not name or not spath or not declared:
            raise SurfacePolicyUnavailable(
                "surface %r must declare surface, path and declared_class" % name
            )
        if declared not in ladder:
            raise SurfacePolicyUnavailable(
                "surface %r declares class %r, which is not a rung of the ladder"
                % (name, declared)
            )
        surfaces.append(
            SurfaceSpec(
                surface=name,
                path=spath,
                declared_class=declared,
                notes=str(entry.get("notes") or ""),
            )
        )
    if not surfaces:
        raise SurfacePolicyUnavailable("policy declares no surfaces")

    surface_roots = _as_str_tuple(raw.get("surface_roots"), "surface_roots")
    for root_name in surface_roots:
        if "/" in root_name or root_name in ("", ".", ".."):
            raise SurfacePolicyUnavailable(
                "surface_roots entry %r must be a plain top-level directory name"
                % root_name
            )
    waived_roots = _as_str_tuple(raw.get("waived_roots"), "waived_roots")

    return SurfacePolicy(
        ladder=ladder,
        evidence=evidence,
        requirements=requirements,
        surfaces=tuple(surfaces),
        surface_roots=surface_roots,
        waived_roots=waived_roots,
    )


# -- evidence measurement -----------------------------------------------------


def _tokens(name: str) -> Set[str]:
    """The meaningful name tokens of a file (stem plus its `.`/`_`/`-` parts)."""
    stem = name
    for suffix in _NAME_SUFFIXES:
        if name.endswith(suffix):
            stem = name[: -len(suffix)]
            break
    tokens = {stem}
    tokens.update(part for part in re.split(r"[._-]+", stem) if part)
    return tokens


def _surface_files(base: Path) -> List[Tuple[Path, Tuple[str, ...]]]:
    """Every file under a surface path, with its path parts relative to the base."""
    if base.is_file():
        return [(base, (base.name,))]
    if not base.is_dir():
        return []
    out: List[Tuple[Path, Tuple[str, ...]]] = []
    for path in sorted(base.rglob("*")):
        if path.is_file():
            out.append((path, path.relative_to(base).parts))
    return out


def _is_test_artifact(path: Path, parts: Sequence[str]) -> bool:
    return "tests" in parts or path.name.startswith("test_")


def measure_path_evidence(root: Path, relpath: str) -> Dict[str, bool]:
    """Measure the path-scoped evidence for one surface (never the `gate`)."""
    base = root / relpath
    entries = _surface_files(base)
    implementation = [
        (path, parts)
        for path, parts in entries
        if not _is_test_artifact(path, parts)
    ]
    return {
        E_CONTRACT: any(path.name == "README.md" for path, _ in entries),
        E_TESTS: any(
            path.name == "conftest.py"
            or (path.name.startswith("test_") and path.suffix == ".py")
            for path, _ in entries
        ),
        E_CONTROLS: any(_tokens(path.name) & CONTROL_TOKENS for path, _ in implementation),
        E_AUDIT: any(_tokens(path.name) & AUDIT_TOKENS for path, _ in implementation),
        E_SCHEMA: any(path.name.endswith(".schema.json") for path, _ in implementation),
        # Live sync is a running module, not a data file: require a `.py` so a
        # persona card or a pinned feed cannot pass as live state (no-false-green).
        E_LIVE_SYNC: any(
            path.suffix == ".py" and _tokens(path.name) & LIVE_TOKENS
            for path, _ in implementation
        ),
    }


def measure_gate(root: Path, surface: str) -> bool:
    """A surface has a dedicated gate when scripts/check-* names it."""
    scripts = root / "scripts"
    if not scripts.is_dir():
        return False
    return any(
        surface in entry.name
        for entry in scripts.iterdir()
        if entry.is_file() and entry.name.startswith("check-")
    )


def measured_class(policy: SurfacePolicy, evidence: Mapping[str, bool]) -> str:
    """The highest rung whose machine requirements the evidence satisfies."""
    best = policy.ladder[0]
    for rung in policy.ladder:
        needed = policy.machine_requirements(rung)
        if all(evidence.get(name, False) for name in needed):
            best = rung
        else:
            break
    return best


# -- checking -----------------------------------------------------------------


def _path_problem(relpath: str) -> Optional[str]:
    cleaned = relpath.replace("\\", "/").strip()
    if not cleaned:
        return "the path is empty"
    if cleaned.startswith("/"):
        return "the path is absolute"
    parts = cleaned.split("/")
    if any(part == ".." for part in parts):
        return "the path escapes the repository root"
    return None


@dataclass
class SurfaceRow:
    surface: str
    path: str
    declared_class: str
    measured_class: str = ""
    evidence: Mapping[str, bool] = None  # type: ignore[assignment]
    missing: Tuple[str, ...] = ()

    def as_dict(self) -> Dict[str, object]:
        return {
            "surface": self.surface,
            "path": self.path,
            "declared_class": self.declared_class,
            "measured_class": self.measured_class,
            "evidence": dict(self.evidence or {}),
            "missing": list(self.missing),
        }


def evaluate_surfaces(
    policy: SurfacePolicy, root: Path
) -> Tuple[List[SurfaceRow], List[Finding]]:
    """Return one row per declared surface plus every finding."""
    root = Path(root)
    rows: List[SurfaceRow] = []
    findings: List[Finding] = []
    seen: Set[str] = set()

    for spec in policy.surfaces:
        subject = spec.surface
        if spec.surface in seen:
            findings.append(
                Finding(
                    code=CODE_DUPLICATE,
                    message="surface '%s' is declared more than once" % spec.surface,
                    subject=subject,
                    remediation="keep a single row per surface",
                )
            )
            continue
        seen.add(spec.surface)

        if policy.rank(spec.declared_class) < 0:
            findings.append(
                Finding(
                    code=CODE_UNKNOWN,
                    message="surface '%s' declares class '%s', which is not a rung of "
                    "the ladder" % (spec.surface, spec.declared_class),
                    subject=subject,
                    remediation="use one of: %s" % ", ".join(policy.ladder),
                )
            )
            continue

        problem = _path_problem(spec.path)
        if problem is not None:
            findings.append(
                Finding(
                    code=CODE_PATH_ESCAPES,
                    message="surface '%s' declares path '%s': %s"
                    % (spec.surface, spec.path, problem),
                    subject=subject,
                    remediation="use a repository-relative path inside the root",
                )
            )
            continue

        relpath = spec.path.replace("\\", "/").strip().strip("/")
        base = root / relpath
        if not base.is_dir() and not base.is_file():
            findings.append(
                Finding(
                    code=CODE_PATH_MISSING,
                    message="surface '%s' declares path '%s', which does not exist"
                    % (spec.surface, spec.path),
                    subject=subject,
                    remediation="fix the path, or remove the surface if it is gone",
                )
            )
            continue

        evidence = measure_path_evidence(root, relpath)
        evidence[E_GATE] = measure_gate(root, spec.surface)
        measured = measured_class(policy, evidence)
        missing = tuple(
            name
            for name in policy.machine_requirements(spec.declared_class)
            if not evidence.get(name, False)
        )
        rows.append(
            SurfaceRow(
                surface=spec.surface,
                path=relpath,
                declared_class=spec.declared_class,
                measured_class=measured,
                evidence=evidence,
                missing=missing,
            )
        )

        if policy.rank(spec.declared_class) > policy.rank(measured):
            findings.append(
                Finding(
                    code=CODE_BELOW,
                    message="surface '%s' declares class '%s' but measures '%s' "
                    "(missing machine evidence: %s)"
                    % (
                        spec.surface,
                        spec.declared_class,
                        measured,
                        ", ".join(missing) or "(none named)",
                    ),
                    subject=subject,
                    remediation="ship the missing evidence, or lower the declared "
                    "class to what the surface actually meets",
                )
            )

        for name in policy.manual_requirements(spec.declared_class):
            findings.append(
                Finding(
                    code=CODE_MANUAL,
                    message="surface '%s' declares class '%s', which requires '%s' "
                    "(manual): %s" % (spec.surface, spec.declared_class, name,
                                      policy.evidence[name].description or name),
                    severity=SEVERITY_WARNING,
                    subject=subject,
                    remediation="record the evidence, or state explicitly that it is "
                    "not held (never assume it met)",
                )
            )

    declared_paths = [
        spec.path.replace("\\", "/").strip().strip("/") for spec in policy.surfaces
    ]
    for root_name in policy.surface_roots:
        if not (root / root_name).is_dir():
            continue
        if root_name in policy.waived_roots:
            continue
        covered = any(
            path == root_name or path.startswith(root_name + "/")
            for path in declared_paths
        )
        if not covered:
            findings.append(
                Finding(
                    code=CODE_UNDECLARED,
                    message="surface root '%s' exists but no surface declares it"
                    % root_name,
                    subject=root_name,
                    remediation="declare the surface in surfaces.yaml, or waive the "
                    "root by name with a reason",
                )
            )

    return rows, findings


def check_surfaces(policy: SurfacePolicy, root: Path) -> List[Finding]:
    return evaluate_surfaces(policy, root)[1]


# -- CLI ----------------------------------------------------------------------


def _print_rows(rows: Sequence[SurfaceRow]) -> None:
    print("%-10s %-16s %-12s %-12s" % ("surface", "path", "declared", "measured"))
    for row in rows:
        print(
            "%-10s %-16s %-12s %-12s"
            % (row.surface, row.path, row.declared_class, row.measured_class)
        )


def cmd_check(args: argparse.Namespace) -> int:
    root = Path(args.root)
    policy_path = Path(args.policy) if args.policy else root / SURFACES_RELPATH

    if not root.is_dir():
        print("surface-class: CANNOT-ASSESS — no root at %s" % root, file=sys.stderr)
        return 2
    try:
        policy = load_surface_policy(policy_path)
    except SurfacePolicyUnavailable as exc:
        print("surface-class: CANNOT-ASSESS — %s" % exc, file=sys.stderr)
        return 2

    rows, findings = evaluate_surfaces(policy, root)
    hard = [f for f in findings if f.severity == SEVERITY_ERROR]
    soft = [f for f in findings if f.severity == SEVERITY_WARNING]

    if args.json:
        print(
            json.dumps(
                {
                    "schema": SURFACES_SCHEMA,
                    "surfaces": [row.as_dict() for row in rows],
                    "error_count": len(hard),
                    "warning_count": len(soft),
                    "findings": [f.as_dict() for f in findings],
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        _print_rows(rows)
        if findings:
            for finding in findings:
                print(
                    "  %-7s %-30s %s"
                    % (finding.severity.upper(), finding.code, finding.message)
                )
        else:
            print("  (no findings)")

    if hard:
        print(
            "surface-class: FAIL (%d error(s), %d manual report(s))"
            % (len(hard), len(soft)),
            file=sys.stderr,
        )
        return 1
    summary = (
        "surface-class: OK (%d surface(s) meet their declared class, %d manual "
        "requirement(s) reported)" % (len(rows), len(soft))
    )
    # Under --json stdout stays a single JSON document, so the human summary
    # goes to stderr.
    print(summary, file=sys.stderr if args.json else sys.stdout)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="governance/conformance/surfaces.py",
        description="Per-surface target solution-class enforcement (issue #351).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_check = sub.add_parser("check", help="measure every declared surface")
    p_check.add_argument("--root", default=".")
    p_check.add_argument("--policy", default=None)
    p_check.add_argument("--json", action="store_true")
    p_check.set_defaults(func=cmd_check)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
