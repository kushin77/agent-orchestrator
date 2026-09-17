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

Two additions from issue #883 (ADR-0031):

* **class ceiling** — a row may declare `class_ceiling` (with a `ceiling_reason`)
  when its shape cannot honestly reach the top rung (a static asset bundle, a
  single file). The ceiling is REPORTED on every run, never silently waived; a
  declaration above it, or a ceiling the evidence has already exceeded, is an
  ERROR. Rows with a ceiling are non-product rows.
* **module declared class** — `module.json` `solution_class` declares the
  module's own rung. It may never exceed the floor: the lowest measured class
  over the product rows (every row without a ceiling). Silent when there is no
  manifest under the root; an ERROR when the manifest is present and above.

Exit-code contract (repo convention, GR-12): 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.

Usage::

    python3 governance/conformance/surfaces.py check
    python3 governance/conformance/surfaces.py check --root . --policy <path>
    python3 governance/conformance/surfaces.py check --json
    python3 governance/conformance/surfaces.py check --module <path/to/module.json>
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
MODULE_RELPATH = Path("module.json")
MODULE_CLASS_KEY = "solution_class"

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
CODE_CEILING = "surface-class-ceiling"
CODE_ABOVE_CEILING = "surface-above-class-ceiling"
CODE_CEILING_STALE = "surface-class-ceiling-stale"
CODE_MODULE_UNREADABLE = "module-manifest-unreadable"
CODE_MODULE_UNDECLARED = "module-class-undeclared"
CODE_MODULE_UNKNOWN = "module-class-unknown"
CODE_MODULE_ABOVE_FLOOR = "module-class-above-floor"

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
    class_ceiling: str = ""
    ceiling_reason: str = ""

    @property
    def is_product(self) -> bool:
        """A row without a ceiling is a product surface and counts toward the floor."""
        return not self.class_ceiling


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
        ceiling = ""
        ceiling_reason = ""
        if "class_ceiling" in entry:
            ceiling = str(entry.get("class_ceiling") or "")
            if ceiling not in ladder:
                raise SurfacePolicyUnavailable(
                    "surface %r declares class_ceiling %r, which is not a rung of "
                    "the ladder" % (name, ceiling)
                )
            if ceiling == ladder[-1]:
                raise SurfacePolicyUnavailable(
                    "surface %r declares class_ceiling %r, the top rung: a ceiling "
                    "at the top is not a ceiling" % (name, ceiling)
                )
            ceiling_reason = str(entry.get("ceiling_reason") or "").strip()
            if not ceiling_reason:
                # A ceiling without a reason is a silent waiver; refused.
                raise SurfacePolicyUnavailable(
                    "surface %r declares class_ceiling %r without a ceiling_reason"
                    % (name, ceiling)
                )
        surfaces.append(
            SurfaceSpec(
                surface=name,
                path=spath,
                declared_class=declared,
                notes=str(entry.get("notes") or ""),
                class_ceiling=ceiling,
                ceiling_reason=ceiling_reason,
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
    class_ceiling: str = ""

    @property
    def is_product(self) -> bool:
        return not self.class_ceiling

    def as_dict(self) -> Dict[str, object]:
        return {
            "surface": self.surface,
            "path": self.path,
            "declared_class": self.declared_class,
            "measured_class": self.measured_class,
            "class_ceiling": self.class_ceiling,
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
                class_ceiling=spec.class_ceiling,
            )
        )

        if spec.class_ceiling:
            # Reported on every run: a ceiling is an explicit, owner-confirmed
            # limit on what this row's shape can reach, never a silent waiver.
            findings.append(
                Finding(
                    code=CODE_CEILING,
                    message="surface '%s' carries class ceiling '%s' (declared '%s', "
                    "measured '%s'): %s"
                    % (
                        spec.surface,
                        spec.class_ceiling,
                        spec.declared_class,
                        measured,
                        spec.ceiling_reason,
                    ),
                    severity=SEVERITY_WARNING,
                    subject=subject,
                    remediation="the ceiling is a non-product row's honest limit; "
                    "remove it only when the row's shape changes",
                )
            )
            if policy.rank(spec.declared_class) > policy.rank(spec.class_ceiling):
                findings.append(
                    Finding(
                        code=CODE_ABOVE_CEILING,
                        message="surface '%s' declares class '%s' above its class "
                        "ceiling '%s'"
                        % (spec.surface, spec.declared_class, spec.class_ceiling),
                        subject=subject,
                        remediation="lower the declared class to the ceiling, or "
                        "raise the ceiling with a reason the owner confirms",
                    )
                )
            if policy.rank(measured) > policy.rank(spec.class_ceiling):
                findings.append(
                    Finding(
                        code=CODE_CEILING_STALE,
                        message="surface '%s' measures '%s', above its class ceiling "
                        "'%s': the ceiling no longer describes the row"
                        % (spec.surface, measured, spec.class_ceiling),
                        subject=subject,
                        remediation="raise or remove the ceiling (with a reason) so "
                        "the row is measured honestly",
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


# -- module declared class ----------------------------------------------------


def product_floor(
    policy: SurfacePolicy, rows: Sequence[SurfaceRow]
) -> Tuple[str, Tuple[str, ...]]:
    """The lowest measured class over the product rows, and who holds it.

    Product rows are the rows without a ``class_ceiling``. Returns ``("", ())``
    when there is no product row to measure.
    """
    product = [row for row in rows if row.is_product and row.measured_class]
    if not product:
        return "", ()
    lowest = min(policy.rank(row.measured_class) for row in product)
    floor = policy.ladder[lowest]
    holders = tuple(row.surface for row in product if row.measured_class == floor)
    return floor, holders


def evaluate_module_class(
    policy: SurfacePolicy, rows: Sequence[SurfaceRow], module_path: Path
) -> List[Finding]:
    """Hold ``module.json``'s declared class to the product floor.

    Silent when no manifest exists at ``module_path`` (a scratch root). When one
    does, its ``solution_class`` must be a rung at or below the floor, so the
    module never claims more than its weakest product surface measures.
    """
    module_path = Path(module_path)
    if not module_path.is_file():
        return []
    subject = str(module_path.name)
    try:
        manifest = json.loads(module_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [
            Finding(
                code=CODE_MODULE_UNREADABLE,
                message="module manifest %s cannot be read: %s" % (module_path, exc),
                subject=subject,
                remediation="fix the manifest so it is valid JSON",
            )
        ]
    if not isinstance(manifest, Mapping) or MODULE_CLASS_KEY not in manifest:
        return [
            Finding(
                code=CODE_MODULE_UNDECLARED,
                message="module manifest %s declares no '%s'"
                % (module_path, MODULE_CLASS_KEY),
                subject=subject,
                remediation="declare '%s' as the lowest measured product-surface "
                "class (ADR-0031)" % MODULE_CLASS_KEY,
            )
        ]
    declared = str(manifest.get(MODULE_CLASS_KEY) or "")
    if policy.rank(declared) < 0:
        return [
            Finding(
                code=CODE_MODULE_UNKNOWN,
                message="module manifest %s declares '%s' as '%s', which is not a "
                "rung of the ladder" % (module_path, MODULE_CLASS_KEY, declared),
                subject=subject,
                remediation="use one of: %s" % ", ".join(policy.ladder),
            )
        ]
    floor, holders = product_floor(policy, rows)
    if not floor:
        return [
            Finding(
                code=CODE_MODULE_ABOVE_FLOOR,
                message="module manifest %s declares '%s' but no product surface "
                "was measured, so no floor exists to hold it to"
                % (module_path, declared),
                subject=subject,
                remediation="declare at least one product surface (a row without "
                "a class_ceiling)",
            )
        ]
    if policy.rank(declared) > policy.rank(floor):
        return [
            Finding(
                code=CODE_MODULE_ABOVE_FLOOR,
                message="module manifest %s declares class '%s' above the product "
                "floor '%s' (held by: %s)"
                % (module_path, declared, floor, ", ".join(holders)),
                subject=subject,
                remediation="lower '%s' to the floor, or raise the floor surface(s) "
                "first (one flip PR per wave, docs/SURFACE-CLASS.md)"
                % MODULE_CLASS_KEY,
            )
        ]
    return []


# -- CLI ----------------------------------------------------------------------


def _print_rows(rows: Sequence[SurfaceRow]) -> None:
    print(
        "%-10s %-16s %-12s %-12s %s"
        % ("surface", "path", "declared", "measured", "ceiling")
    )
    for row in rows:
        print(
            "%-10s %-16s %-12s %-12s %s"
            % (
                row.surface,
                row.path,
                row.declared_class,
                row.measured_class,
                row.class_ceiling or "-",
            )
        )


def _module_summary(
    policy: SurfacePolicy, rows: Sequence[SurfaceRow], module_path: Path
) -> Dict[str, object]:
    floor, holders = product_floor(policy, rows)
    declared = ""
    if module_path.is_file():
        try:
            manifest = json.loads(module_path.read_text(encoding="utf-8"))
            if isinstance(manifest, Mapping):
                declared = str(manifest.get(MODULE_CLASS_KEY) or "")
        except (OSError, ValueError):
            declared = ""
    return {
        "path": str(module_path),
        "present": module_path.is_file(),
        "declared_class": declared,
        "floor_class": floor,
        "floor_surfaces": list(holders),
    }


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
    module_path = Path(args.module) if args.module else root / MODULE_RELPATH
    findings.extend(evaluate_module_class(policy, rows, module_path))
    module = _module_summary(policy, rows, module_path)
    hard = [f for f in findings if f.severity == SEVERITY_ERROR]
    soft = [f for f in findings if f.severity == SEVERITY_WARNING]

    if args.json:
        print(
            json.dumps(
                {
                    "schema": SURFACES_SCHEMA,
                    "surfaces": [row.as_dict() for row in rows],
                    "module": module,
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
        if module["present"]:
            print(
                "module     %-16s %-12s floor=%s (%s)"
                % (
                    module_path.name,
                    module["declared_class"] or "(undeclared)",
                    module["floor_class"] or "(none)",
                    ", ".join(module["floor_surfaces"]) or "no product row",
                )
            )
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
            "surface-class: FAIL (%d error(s), %d reported (manual requirements + ceilings))"
            % (len(hard), len(soft)),
            file=sys.stderr,
        )
        return 1
    summary = (
        "surface-class: OK (%d surface(s) meet their declared class, %d manual/ceiling "
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
    p_check.add_argument(
        "--module",
        default=None,
        help="module manifest to hold to the product floor (default: <root>/module.json)",
    )
    p_check.set_defaults(func=cmd_check)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
