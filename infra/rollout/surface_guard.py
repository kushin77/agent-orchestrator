"""infra/rollout/surface_guard.py — the console surface's rollback anchor (#802).

WHY this exists. ``infra/feature-flags/registry.yaml`` declares a surface's
*promotion* (``surfaces.<name>.default``) and ``infra/rollout/`` owns the
*pipeline* that promotes it — but nothing connected a promoted console surface to
its own health. The enterprise bar asks for a surface that can be **rolled back**
when its health fails, audited, and provable; this module is that anchor.

The anchor drives **two layers, in one call**, because either alone is a
half-truth:

1. **the rollout layer** — the surface's flag is rolled to the stage model's
   declared rollback target (``off``) through the existing engine
   (``RolloutEngine.observe_health``), which appends a hash-chained audit record.
   This is what the declared pipeline (``infra/cloudbuild/rollout-rollback.yaml``)
   re-renders the deployment from, and what ``rollout-state.yaml`` records.
   Without a row in that file the engine *refuses the flag by name* — the state
   the console surface was in before this lane, i.e. a surface that could be
   promoted and never withdrawn;
2. **the runtime layer** — the rollback overlay of
   ``portal.server.surface_state`` is engaged, so the surface's reader answers
   ``"off"`` **now**, whatever the committed registry still declares. A rollback
   that only edited a declaration would take effect at the next deploy, which is
   not a rollback.

Honesty rules the anchor holds, each measurable:

* **an unreadable signal is not a failure.** A readiness of ``cannot-assess``
  performs **no** rollback and exits 2: acting on a signal nobody could read
  would fabricate a health failure. It is also never reported healthy.
* **a healthy surface is left alone.** A ``ready`` reading observes health and
  changes nothing (the model returns "no move" for a healthy observation) — so a
  guard that always rolls back fails the gate that drives this module, not the
  production surface it was supposed to protect.
* **drift is named, not hidden.** A surface that serves while its rollout flag is
  still ``off`` (a promotion recorded in the registry but never in
  ``rollout-state.yaml``) is reported as drift; so is a rollout flag left at an
  exposed stage while the surface reads ``off``.
* **reversible.** ``--clear`` disengages the overlay, so the round trip
  (promoted → health fails → rolled back → re-promoted) is one command each way.

Exit-code contract (the repo's tri-state): 0 healthy / not exposed · 1 rolled
back (or drift found) · 2 cannot assess.

Usage::

    python3 -m infra.rollout.surface_guard operator_terminal
    python3 -m infra.rollout.surface_guard operator_terminal --json
    python3 -m infra.rollout.surface_guard operator_terminal --clear

---knowledge---
module_id: infra.rollout.surface_guard
system: infra
app: rollout
solution_class: class
patterns: [pre-standard-snapshot]
derives_from: null
owner_sme: iac-sme
tier: L1
interfaces: [Outcome, reconcile, clear, report, build_parser, main]
invariants: ""
gotchas: ""
related: ["#1911"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from infra.rollout.engine import RolloutEngine, RolloutError  # noqa: E402
from portal.server import surface_state  # noqa: E402
from portal.server.surface_health import (  # noqa: E402
    SURFACE_CANNOT_ASSESS,
    SURFACE_OFF,
    SURFACE_OPERATOR_TERMINAL,
    SURFACE_READY,
    Readiness,
    readiness,
    spec,
)

#: What the anchor did (a closed vocabulary, so a caller cannot guess).
ACTION_HEALTHY = "healthy"
ACTION_NOT_EXPOSED = "not-exposed"
ACTION_ROLLED_BACK = "rolled-back"
ACTION_CANNOT_ASSESS = "cannot-assess"
ACTIONS = (
    ACTION_HEALTHY,
    ACTION_NOT_EXPOSED,
    ACTION_ROLLED_BACK,
    ACTION_CANNOT_ASSESS,
)

#: What happened to the rollout flag itself.
FLAG_ROLLED_OFF = "rolled-to-off"
FLAG_UNCHANGED = "unchanged"
FLAG_UNAVAILABLE = "unavailable"

#: The reason recorded on the runtime overlay when readiness fails.
ROLLBACK_REASON_PREFIX = "surface readiness failed"

EXIT_OK = 0
EXIT_ROLLED_BACK = 1
EXIT_CANNOT_ASSESS = 2


@dataclass(frozen=True)
class Outcome:
    """One reconciliation: what was read, what the anchor did, why."""

    surface: str
    flag: str
    action: str
    readiness: Readiness
    flag_stage: str = "unknown"
    flag_action: str = FLAG_UNCHANGED
    exposure_stage: Optional[str] = None
    audit_records: Tuple[Mapping[str, Any], ...] = ()
    audit_verified: bool = False
    overlay_path: Optional[str] = None
    drift: Optional[str] = None
    engine_error: Optional[str] = None
    detail: str = ""
    exit_code: int = EXIT_OK

    def as_dict(self) -> dict:
        return {
            "surface": self.surface,
            "flag": self.flag,
            "action": self.action,
            "flagStage": self.flag_stage,
            "flagAction": self.flag_action,
            "exposureStage": self.exposure_stage,
            "auditRecords": [dict(record) for record in self.audit_records],
            "auditVerified": self.audit_verified,
            "overlay": self.overlay_path,
            "drift": self.drift,
            "engineError": self.engine_error,
            "detail": self.detail,
            "readiness": self.readiness.as_dict(),
            "exitCode": self.exit_code,
        }


def _load_engine(
    repo_root: Path | str,
    *,
    stage_model_path: Optional[Path | str],
    rollout_state_path: Optional[Path | str],
    audit_path: Optional[Path | str],
) -> Tuple[Optional[RolloutEngine], Optional[str]]:
    """The rollout engine, or ``(None, reason)`` when it cannot be built.

    A refusal here (an unreadable declaration, or a flag the state file does not
    carry) is **reported**, never raised past the caller: the runtime half of the
    rollback still has to happen, and "the rollout layer refused, by name" is a
    finding the operator needs rather than a crash.

    The defaults resolve against ``repo_root``, not against this module's own
    location, so ``--root`` reconciles the tree it was pointed at.
    """
    rollout_dir = Path(repo_root) / "infra" / "rollout"
    try:
        engine = RolloutEngine.load(
            stage_model_path=str(stage_model_path or rollout_dir / "stage-model.yaml"),
            rollout_state_path=str(
                rollout_state_path or rollout_dir / "rollout-state.yaml"
            ),
            audit_path=str(audit_path) if audit_path else None,
        )
    except (RolloutError, OSError, ValueError) as exc:
        return None, f"{exc}"
    return engine, None


def _first_exposed_stage(model) -> Any:
    """The stage model's first exposed stage (``canary``), never a hardcoded token."""
    exposed = sorted(
        (spec.stage for spec in model.stages.values() if spec.exposed),
        key=lambda stage: stage.order,
    )
    if not exposed:
        raise RolloutError("the stage model declares no exposed stage")
    return exposed[0]


def _apply_exposure(engine: RolloutEngine, flag: str) -> Optional[str]:
    """Make the engine see the exposure the surface actually has, in memory.

    WHY this step exists. ``RolloutEngine.load`` runs
    ``validate_rollout_state_doc``, which **refuses any flag that is not OFF** —
    so a promoted stage can never be *persisted* in ``rollout-state.yaml``. That
    file records the withdrawal, not the exposure. But ``observe_health`` only
    rolls a flag back when the model considers it exposed, so an anchor that
    skipped this step would report a rollback it never performed — precisely the
    formality this lane exists to avoid.

    So the exposure the surface demonstrably has (the registry declares it
    promoted, so a reader serves it) is applied as in-memory state, using the
    stage model's own first exposed stage. **No promotion is audited**: nothing
    was promoted, and inventing a promotion record — with a green-verify claim
    the anchor never checked — would be a worse lie than saying plainly that the
    surface was already exposed. Returns the applied stage token, or ``None``
    when the flag was exposed already / already off.
    """
    state = engine.flag(flag)
    if state.stage.exposed:
        return None
    stage = _first_exposed_stage(engine.model)
    state.stage = stage
    state.rollout_pct = engine.model.spec(stage).rollout_pct
    return stage.value


def reconcile(
    repo_root: Path | str = _REPO_ROOT,
    surface: str = SURFACE_OPERATOR_TERMINAL,
    *,
    registry_path: Optional[Path | str] = None,
    overlay_path: Optional[Path | str] = None,
    static_dir: Optional[Path | str] = None,
    stage_model_path: Optional[Path | str] = None,
    rollout_state_path: Optional[Path | str] = None,
    audit_path: Optional[Path | str] = None,
    state_out: Optional[Path | str] = None,
    actor: str = "surface-health-monitor",
) -> Outcome:
    """Read ``surface``'s readiness and roll it back when that reading fails."""
    root = Path(repo_root)
    target = spec(surface)

    report = readiness(
        root,
        surface,
        registry_path=registry_path,
        overlay_path=overlay_path,
        static_dir=static_dir,
    )
    engine, engine_error = _load_engine(
        root,
        stage_model_path=stage_model_path,
        rollout_state_path=rollout_state_path,
        audit_path=audit_path,
    )
    flag_stage = "unknown"
    if engine is not None:
        try:
            flag_stage = engine.flag(target.flag).stage.value
        except RolloutError as exc:
            engine_error = f"{exc}"

    if report.state == SURFACE_CANNOT_ASSESS:
        return Outcome(
            surface=surface,
            flag=target.flag,
            action=ACTION_CANNOT_ASSESS,
            readiness=report,
            flag_stage=flag_stage,
            flag_action=FLAG_UNCHANGED,
            engine_error=engine_error,
            detail=(
                f"{report.detail} — no rollback is performed on an unreadable "
                "signal: acting on it would fabricate a health failure"
            ),
            exit_code=EXIT_CANNOT_ASSESS,
        )

    if report.state == SURFACE_OFF:
        drift = None
        if flag_stage not in ("off", "unknown"):
            drift = (
                f"the rollout flag is at {flag_stage!r} while the surface reads "
                "off — the withdrawal and the rollout state disagree"
            )
        return Outcome(
            surface=surface,
            flag=target.flag,
            action=ACTION_NOT_EXPOSED,
            readiness=report,
            flag_stage=flag_stage,
            flag_action=FLAG_UNCHANGED,
            overlay_path=str(surface_state.overlay_path(root, path=overlay_path)),
            drift=drift,
            engine_error=engine_error,
            detail=(
                f"{report.detail} — nothing to roll back "
                "(an unexposed surface is already OFF)"
            ),
            exit_code=EXIT_ROLLED_BACK if drift else EXIT_OK,
        )

    health_ok = report.state == SURFACE_READY
    records: list = []
    verified = False
    flag_action = FLAG_UNCHANGED
    exposure_stage: Optional[str] = None
    if engine is not None:
        if not health_ok:
            exposure_stage = _apply_exposure(engine, target.flag)
        before = len(engine.audit.records())
        engine.observe_health(target.flag, health_ok=health_ok, actor=actor)
        records = engine.audit.records()[before:]
        verified = engine.audit.verify()
        if not health_ok and engine.flag(target.flag).stage.value == surface_state.ROLLBACK_STAGE:
            flag_action = FLAG_ROLLED_OFF
    else:
        flag_action = FLAG_UNAVAILABLE

    if health_ok:
        return Outcome(
            surface=surface,
            flag=target.flag,
            action=ACTION_HEALTHY,
            readiness=report,
            flag_stage=flag_stage,
            flag_action=flag_action,
            audit_records=tuple(records),
            audit_verified=verified,
            engine_error=engine_error,
            detail=f"{report.detail} — no rollback needed",
            exit_code=EXIT_OK,
        )

    # The readiness check failed: take the surface off, at both layers.
    overlay = surface_state.record_rollback(
        root,
        surface,
        reason=f"{ROLLBACK_REASON_PREFIX}: {report.detail}",
        actor=actor,
        path=overlay_path,
    )
    if state_out and engine is not None:
        engine.write_state(str(state_out))

    drift = None
    if engine_error is not None:
        drift = f"the rollout flag was not rolled back: {engine_error}"
    elif exposure_stage is not None:
        drift = (
            f"the committed rollout row for {target.flag} is {flag_stage!r} while "
            "the registry declares the surface promoted — the exposure is "
            "declared, never recorded in the rollout state (only the withdrawal is)"
        )

    return Outcome(
        surface=surface,
        flag=target.flag,
        action=ACTION_ROLLED_BACK,
        readiness=report,
        flag_stage=flag_stage,
        flag_action=flag_action,
        exposure_stage=exposure_stage,
        audit_records=tuple(records),
        audit_verified=verified,
        overlay_path=str(overlay),
        drift=drift,
        engine_error=engine_error,
        detail=(
            f"{report.detail} — rolled back to {surface_state.ROLLBACK_STAGE} "
            f"(runtime overlay at {overlay})"
        ),
        exit_code=EXIT_ROLLED_BACK,
    )


def clear(repo_root: Path | str = _REPO_ROOT, surface: str = SURFACE_OPERATOR_TERMINAL,
          *, overlay_path: Optional[Path | str] = None) -> bool:
    """Disengage a rollback (the reversible half); True when one was engaged."""
    spec(surface)  # an undeclared surface is a programming error, not a no-op
    return surface_state.clear_rollback(repo_root, surface, path=overlay_path)


def report(outcome: Outcome, as_json: bool = False) -> str:
    if as_json:
        return json.dumps(outcome.as_dict(), indent=2, sort_keys=True)
    lines = [
        f"surface {outcome.surface} ({outcome.flag}): {outcome.action}",
        f"  readiness : {outcome.readiness.state} — {outcome.readiness.detail}",
        f"  rollout   : flag stage {outcome.flag_stage}, {outcome.flag_action}",
    ]
    if outcome.exposure_stage:
        lines.append(
            "  exposure  : the surface was exposed but the committed rollout row "
            f"was not; applied {outcome.exposure_stage!r} before the observation"
        )
    if outcome.drift:
        lines.append(f"  drift     : {outcome.drift}")
    if outcome.engine_error:
        lines.append(f"  engine    : {outcome.engine_error}")
    for record in outcome.audit_records:
        lines.append(
            "  audit     : "
            f"#{record.get('seq')} {record.get('action')} "
            f"{record.get('from_stage')}->{record.get('to_stage')} "
            f"reason={record.get('reason')} actor={record.get('actor')}"
        )
    if outcome.audit_records:
        lines.append(f"  audit chain verified: {outcome.audit_verified}")
    if outcome.overlay_path:
        lines.append(f"  overlay   : {outcome.overlay_path}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="infra.rollout.surface_guard", description=__doc__
    )
    parser.add_argument("surface", nargs="?", default=SURFACE_OPERATOR_TERMINAL)
    parser.add_argument("--repo-root", default=str(_REPO_ROOT))
    parser.add_argument("--registry", default=None, help="an alternate registry document")
    parser.add_argument("--overlay", default=None, help="an alternate rollback overlay")
    parser.add_argument("--static-dir", default=None, help="the console's static root")
    parser.add_argument("--stage-model", default=None)
    parser.add_argument("--rollout-state", default=None)
    parser.add_argument("--audit-log", default=None, help="where the audit chain is appended")
    parser.add_argument("--state-out", default=None, help="write the transitioned rollout state")
    parser.add_argument("--actor", default="surface-health-monitor")
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--clear",
        action="store_true",
        help="disengage an engaged rollback instead of reconciling",
    )
    return parser


def main(argv: Optional[list] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.clear:
            cleared = clear(args.repo_root, args.surface, overlay_path=args.overlay)
            print(
                f"surface {args.surface}: rollback "
                f"{'disengaged' if cleared else 'was not engaged'}"
            )
            return EXIT_OK
        outcome = reconcile(
            args.repo_root,
            args.surface,
            registry_path=args.registry,
            overlay_path=args.overlay,
            static_dir=args.static_dir,
            stage_model_path=args.stage_model,
            rollout_state_path=args.rollout_state,
            audit_path=args.audit_log,
            state_out=args.state_out,
            actor=args.actor,
        )
    except ValueError as exc:  # an undeclared surface, or an unreadable overlay
        print(f"surface-guard: CANNOT-ASSESS — {exc}", file=sys.stderr)
        return EXIT_CANNOT_ASSESS
    print(report(outcome, args.json))
    return outcome.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
