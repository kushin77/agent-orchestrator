"""Startup validation gate (issue #26 acceptance #1 and #5).

The gate loads every policy in a bundle and validates it *before* the engine
starts: JSON-Schema conformance (``schema/policy.schema.json``), semantic
safety checks (a BLOCK rule without a reason, malformed conditions, duplicate
rule ids) and cross-policy integrity (duplicate ids across the bundle, control
references that resolve against the deployed controls registry).  An invalid
policy fails the deploy with a nonzero exit — never at runtime.  The engine
itself additionally fails closed at evaluation time (see :mod:`policy.engine`)
as a second line of defense, but a malformed bundle should never get that far.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

from policy.bundle import PolicyBundle, assemble
from policy.controls import ControlRegistry
from policy.errors import PolicyLoadError, PolicyValidationError
from policy.loader import discover_policy_files, policy_from_mapping, read_documents
from policy.model import Policy


def package_root() -> Path:
    """Absolute path of this package (``guardrails/policy``)."""
    return Path(__file__).resolve().parent


def default_bundle_dir() -> str:
    """Shipped example bundle shipped with the lane (bundles/platform)."""
    return str(package_root() / "bundles" / "platform")


def default_controls_file() -> Optional[str]:
    """Shipped controls registry, or None when not present."""
    path = package_root() / "controls.yaml"
    return str(path) if path.exists() else None


@dataclass
class StartupReport:
    """Outcome of a startup validation run over a policy bundle."""

    ok: bool
    file_count: int = 0
    policy_count: int = 0
    errors: list[str] = field(default_factory=list)
    policies: tuple[Policy, ...] = ()

    def summary(self) -> str:
        status = "valid" if self.ok else "INVALID"
        return (
            f"startup validation: {status} "
            f"({self.file_count} file(s), {self.policy_count} policy(ies), "
            f"{len(self.errors)} error(s))"
        )


def validate_paths(
    paths: Iterable[str],
    controls: Optional[ControlRegistry] = None,
) -> StartupReport:
    """Validate every policy under *paths*; never raises for policy content.

    Returns a :class:`StartupReport` aggregating all discovered problems (a
    bad file does not hide a second bad file).  Raises only for genuinely
    unresolvable paths (:class:`PolicyLoadError`).
    """
    errors: list[str] = []
    loaded: list[Policy] = []

    files = discover_policy_files(paths)
    if not files:
        errors.append(f"no policy YAML files found under: {', '.join(paths)}")
        return StartupReport(ok=False, errors=errors)

    for path in files:
        try:
            documents = read_documents(path)
        except PolicyLoadError as exc:
            errors.append(str(exc))
            continue
        for document in documents:
            try:
                loaded.append(policy_from_mapping(document, source=str(path)))
            except PolicyValidationError as exc:
                errors.append(str(exc))

    # Cross-policy integrity: unique ids across the whole bundle.
    ids = [policy.id for policy in loaded]
    duplicates = sorted({policy_id for policy_id in ids if ids.count(policy_id) > 1})
    if duplicates:
        errors.append(f"duplicate policy id(s) across bundle: {', '.join(duplicates)}")

    # Control references must resolve against the deployed registry.
    gated = [policy for policy in loaded if policy.controls]
    if gated and controls is None:
        errors.append(
            "policy(ies) declare controls but no controls registry was provided: "
            + ", ".join(policy.id for policy in gated)
        )
    else:
        for policy in gated:
            missing = [cid for cid in policy.controls if cid not in (controls or ControlRegistry())]
            if missing:
                errors.append(
                    f"policy {policy.id!r} references unregistered control(s): "
                    + ", ".join(missing)
                )

    return StartupReport(
        ok=not errors,
        file_count=len(files),
        policy_count=len(loaded),
        errors=errors,
        policies=tuple(loaded),
    )


def build_bundle(
    paths: Iterable[str],
    controls: Optional[ControlRegistry] = None,
) -> PolicyBundle:
    """Validate *paths* and assemble the validated bundle (deploy gate).

    Raises :class:`PolicyValidationError` (with every problem listed) when the
    bundle is invalid, so startup fails fast instead of running a broken gate.
    """
    report = validate_paths(paths, controls=controls)
    if not report.ok:
        raise PolicyValidationError(
            "policy bundle failed startup validation\n"
            + "\n".join(f"- {error}" for error in report.errors)
        )
    return assemble(report.policies)


def build_engine(
    paths: Iterable[str],
    controls: Optional[ControlRegistry] = None,
    uncovered_decision: Any = "block",
):
    """Convenience: validate a bundle and construct its gate engine."""
    from policy.engine import PolicyEngine

    bundle = build_bundle(paths, controls=controls)
    return PolicyEngine(
        bundle=bundle,
        controls=controls if controls is not None else ControlRegistry(),
        uncovered_decision=uncovered_decision,
    )
