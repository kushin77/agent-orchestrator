"""The health read — real dependencies, honestly reported (issue #651, ERP-06).

A health route that returns ``ok`` unconditionally is the clearest possible
formality: it is green when the module is broken and green when it is not, so it
measures nothing. This one reads three dependencies the surface actually needs,
and reports each one's real state:

* **``erp-02-model``** — the model the surface serves loads, and its own asset
  invariants hold (``DocumentModel.check_assets``). A model whose schemas and
  workflows disagree is *stale* here, by name, with the first problem reported.
* **``auth-declarations``** — the role map the ERP-08 layer decides with covers
  every kind ERP-02 declares. A kind no role covers is a request that can never
  be authorized, so it is a broken dependency rather than a policy.
* **``openapi-artifact``** — the committed ``openapi.json`` equals a fresh
  emission from the model. A hand-edited or stale contract is a *stale*
  dependency, not a healthy one.

The statuses follow the paperclip surface's precedent
(``integrations/paperclip/api/health.py``): ``ok`` → 200, ``degraded`` (a
dependency is stale but readable) → 200 with the real status, ``unhealthy`` (a
dependency is unreachable) → the surface answers ``503`` through its own
``unavailable`` refusal. **A non-ok dependency is never reported as ok** — and
:func:`check_report` re-runs the probes and refuses a report that claims
otherwise, so the honesty of the read is itself measurable rather than trusted.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Sequence, Tuple

__all__ = [
    "PROBES",
    "DependencyState",
    "HealthReport",
    "STATE_MISSING",
    "STATE_OK",
    "STATE_STALE",
    "STATUS_DEGRADED",
    "STATUS_OK",
    "STATUS_UNHEALTHY",
    "check_report",
    "health",
]

STATE_OK = "ok"
STATE_STALE = "stale"
STATE_MISSING = "missing"

STATUS_OK = "ok"
STATUS_DEGRADED = "degraded"
STATUS_UNHEALTHY = "unhealthy"

#: A dependency that is missing makes the whole read unhealthy; one that is
#: merely stale degrades it. Anything else is a bug in a probe, and is treated as
#: unhealthy rather than as healthy — the direction that cannot hide a problem.
_STATE_TO_STATUS = {
    STATE_OK: STATUS_OK,
    STATE_STALE: STATUS_DEGRADED,
    STATE_MISSING: STATUS_UNHEALTHY,
}


@dataclass(frozen=True)
class DependencyState:
    """One dependency's real reading."""

    name: str
    state: str
    detail: str

    def to_dict(self) -> Dict[str, str]:
        return {"name": self.name, "state": self.state, "detail": self.detail}


@dataclass(frozen=True)
class HealthReport:
    """The verdict and the readings behind it."""

    status: str
    http_status: int
    dependencies: Tuple[DependencyState, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "httpStatus": self.http_status,
            "dependencies": [state.to_dict() for state in self.dependencies],
        }


def probe_model(root: Path, model: Any, role_map: Any) -> DependencyState:
    """The ERP-02 model loads and its assets agree with each other."""
    problems = model.check_assets()
    if problems:
        return DependencyState("erp-02-model", STATE_STALE, f"{len(problems)} asset problem(s): {problems[0]}")
    return DependencyState(
        "erp-02-model",
        STATE_OK,
        f"{len(model.document_kinds())} kind(s), {len(model.lifecycle_kinds())} with a lifecycle",
    )


def probe_declarations(root: Path, model: Any, role_map: Any) -> DependencyState:
    """The role map covers every kind the model declares."""
    uncovered = tuple(kind for kind in model.document_kinds() if kind not in role_map.kinds)
    if uncovered:
        return DependencyState(
            "auth-declarations",
            STATE_MISSING,
            f"the role map covers no kind for: {', '.join(uncovered)}",
        )
    return DependencyState(
        "auth-declarations",
        STATE_OK,
        f"{len(role_map.kinds)} kind(s) covered by {len(role_map.role_names)} role(s)",
    )


def probe_artifact(root: Path, model: Any, role_map: Any) -> DependencyState:
    """The committed OpenAPI artifact equals a fresh emission from the model."""
    from . import openapi as openapi_module  # local: keeps this module import-light

    artifact = Path(root) / openapi_module.EMITTED_ARTIFACT
    if not artifact.is_file():
        return DependencyState(
            "openapi-artifact", STATE_MISSING, f"{openapi_module.EMITTED_ARTIFACT} is not committed"
        )
    fresh_document = openapi_module.build_document(Path(root))
    fresh = openapi_module.serialize(fresh_document)
    committed = artifact.read_text(encoding="utf-8")
    if committed != fresh:
        return DependencyState(
            "openapi-artifact",
            STATE_STALE,
            f"{openapi_module.EMITTED_ARTIFACT} differs from a fresh emission (re-emit it)",
        )
    return DependencyState(
        "openapi-artifact",
        STATE_OK,
        f"{openapi_module.byte_size(fresh_document)} bytes, byte-identical to a fresh emission",
    )


#: The probes, by the dependency name they read.
PROBES: Mapping[str, Callable[[Path, Any, Any], DependencyState]] = {
    "erp-02-model": probe_model,
    "auth-declarations": probe_declarations,
    "openapi-artifact": probe_artifact,
}


def health(
    root: Path,
    *,
    model: Any,
    role_map: Any,
    probes: Optional[Sequence[Callable[[Path, Any, Any], DependencyState]]] = None,
) -> HealthReport:
    """Read every dependency and report the verdict."""
    chosen = tuple(probes) if probes is not None else tuple(PROBES.values())
    states = tuple(probe(Path(root), model, role_map) for probe in chosen)
    status = STATUS_OK
    for state in states:
        reached = _STATE_TO_STATUS.get(state.state)
        if reached is None or reached == STATUS_UNHEALTHY:
            status = STATUS_UNHEALTHY
            break
        if reached == STATUS_DEGRADED:
            status = STATUS_DEGRADED
    http_status = 503 if status == STATUS_UNHEALTHY else 200
    return HealthReport(status=status, http_status=http_status, dependencies=states)


def check_report(
    report: HealthReport,
    root: Path,
    *,
    model: Any,
    role_map: Any,
    probes: Optional[Sequence[Callable[[Path, Any, Any], DependencyState]]] = None,
) -> Tuple[str, ...]:
    """Every way ``report`` disagrees with a fresh reading; empty means honest.

    The findings name the dependency and both readings, so "the health read lied"
    is a sentence about a dependency rather than a suspicion.
    """
    findings = []
    fresh = {
        state.name: state
        for state in (
            probe(Path(root), model, role_map)
            for probe in (probes if probes is not None else tuple(PROBES.values()))
        )
    }
    for state in report.dependencies:
        reading = fresh.get(state.name)
        if reading is None:
            findings.append(f"the health report names the dependency {state.name!r}, which no probe reads")
            continue
        if state.state == STATE_OK and reading.state != STATE_OK:
            findings.append(
                f"the health report calls {state.name!r} ok while the fresh reading is "
                f"{reading.state!r} ({reading.detail})"
            )
    expected = {name: state for name, state in fresh.items()}
    reported = {state.name for state in report.dependencies}
    for name in sorted(set(expected) - reported):
        findings.append(f"the health report omits the dependency {name!r}")
    return tuple(findings)
