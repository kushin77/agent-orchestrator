"""Offline rollout pipeline CLI (issue #45) - promotion, rollback and demo.

The automated path that Cloud Build pipelines invoke
(infra/cloudbuild/rollout-promote.yaml / rollout-rollback.yaml): a promotion
or rollback happens only through this CLI (never a console click). Everything
runs offline against the declarative YAML under ``infra/rollout/``; state
transitions are in-memory and, where requested, written to a caller-supplied
path - never into the repo tree.

Usage (from the repo root):

    python3 -m infra.rollout.cli status
    python3 -m infra.rollout.cli plan
    python3 -m infra.rollout.cli grant-approval FLAG --to STAGE --approver SME \
        --approval-id ID --approvals-dir DIR
    python3 -m infra.rollout.cli promote FLAG --to STAGE --verify-green \
        --approval ID --actor deployer-sa [--canary-health-ok] [--gradual-complete] \
        [--audit-log infra/rollout/audit/promotion-audit.jsonl] \
        [--live-state-out infra/rollout/live-state.yaml] [--audit-record PATH]
    python3 -m infra.rollout.cli canary FLAG --health-ok false
    python3 -m infra.rollout.cli rollback FLAG --reason canary_health_failure
    python3 -m infra.rollout.cli surface-health SURFACE [--clear] [--json]
    python3 -m infra.rollout.cli validate-approval FLAG STAGE ID --actor X --approvals-dir DIR
    python3 -m infra.rollout.cli demo          # end-to-end offline demo
    python3 -m infra.rollout.cli validate      # validate the declarative files

`promote` moves ONE flag ONE adjacent stage. For the whole ordered phase 0-8
run use the driver (infra/rollout/go_live.py, issue #619), which drives this
same engine and enforces strict-by-phase and the declared 24h hold itself.

With `--live-state-out`, `promote` also writes the transition's audit record
file under infra/rollout/audit/ BEFORE the live-state entry that names it (a
promoted entry whose record is missing fails checks/check_rollout.py), and
`--audit-log` persists the hash-chained promotion log. Without `--audit-log`
the chain lives only in memory. Output paths are resolved against the working
directory (the repo root in the Cloud Build pipelines) and must land under
infra/rollout/ - this lane records its own promotions, nowhere else.

---knowledge---
module_id: infra.rollout.cli
system: infra
app: rollout
solution_class: class
patterns: [pre-standard-snapshot]
derives_from: null
owner_sme: iac-sme
tier: L1
interfaces: [cmd_status, cmd_plan, cmd_grant_approval, cmd_promote, cmd_canary, cmd_rollback, cmd_surface_health, cmd_validate_approval, (+4 more)]
invariants: ""
gotchas: ""
related: ["#1911"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path
from typing import List, Optional

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    raise SystemExit(f"rollout cli requires PyYAML ({exc})") from exc

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from infra.rollout.engine import (
    Approval,
    ApprovalLedger,
    RolloutEngine,
    RolloutError,
    audit_record_name,
    write_audit_record,
)
from infra.rollout.model import RolloutStage, validate_go_live_plan_doc


def _rollout_dir(root: Path) -> Path:
    return root / "infra" / "rollout"


def _live_state_path(root: Path) -> Path:
    return _rollout_dir(root) / "live-state.yaml"


def _load_engine(
    root: Path, audit_path: Optional[str] = None, live_state_path: Optional[str] = None
) -> RolloutEngine:
    rd = _rollout_dir(root)
    live_state = live_state_path if live_state_path is not None else str(_live_state_path(root))
    return RolloutEngine.load(
        stage_model_path=str(rd / "stage-model.yaml"),
        rollout_state_path=str(rd / "rollout-state.yaml"),
        audit_path=audit_path,
        live_state_path=live_state,
    )


def _lane_output(
    root: Path, value: Optional[str], flag_name: str, *, from_lane: bool = False
) -> Optional[str]:
    """Resolve a lane-owned output path, refusing one outside infra/rollout/.

    This lane records a promotion in ITS OWN live-state and audit log. A path
    resolving outside ``<root>/infra/rollout`` would write promoted state (or
    its evidence) into a tree the engine did not read - so `check_rollout.py`
    would be checking a different file than the run wrote. Measured: a
    relative path resolves against the working directory, which is the repo
    root in the Cloud Build pipelines and a temporary tree under test, so the
    guard is what keeps a test from touching the real checkout.

    ``from_lane`` selects the base for a RELATIVE path: ``--audit-record`` is
    documented relative to ``infra/rollout/``, the others relative to the
    working directory (which is the repo root in the pipelines).
    """
    if not value:
        return None
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = (_rollout_dir(root) if from_lane else Path.cwd()) / candidate
    resolved = candidate.resolve()
    lane = _rollout_dir(root).resolve()
    try:
        resolved.relative_to(lane)
    except ValueError as exc:
        raise ValueError(
            f"{flag_name} {value} is outside {lane} - this lane records promotions and their "
            "evidence only under infra/rollout/"
        ) from exc
    return str(resolved)


def _load_plan(root: Path) -> dict:
    path = _rollout_dir(root) / "go-live-plan.yaml"
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def cmd_status(root: Path, args: argparse.Namespace) -> int:
    engine = _load_engine(root)
    print("=== rollout state (current) ===")
    for name, state in sorted(engine.flags.items()):
        targeted = f" targeted={state.targeted}" if state.targeted else ""
        print(f"  {name:<22} {state.stage.value:<8} {state.rollout_pct}%{targeted}")
    return 0


def cmd_plan(root: Path, args: argparse.Namespace) -> int:
    plan = _load_plan(root)
    print("=== go-live plan (Phase 0-8) ===")
    for phase in plan.get("phase_order", []):
        body = plan["phases"].get(phase, {})
        print(f"  phase {phase}: {body.get('name', '')}")
        for surface in body.get("surfaces", []):
            print(f"      {surface['flag']:<22} -> {surface['go_live_stage']}")
    return 0


def cmd_grant_approval(root: Path, args: argparse.Namespace) -> int:
    approvals_dir = Path(args.approvals_dir)
    approvals_dir.mkdir(parents=True, exist_ok=True)
    target = RolloutStage.coerce(args.to)
    approval = Approval(
        approval_id=args.approval_id,
        flag=args.flag,
        target_stage=target,
        approver=args.approver,
        granted_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )
    path = approvals_dir / f"{args.approval_id}.yaml"
    with open(path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(
            {
                "approval_id": approval.approval_id,
                "flag": approval.flag,
                "target_stage": approval.target_stage.value,
                "approver": approval.approver,
                "posture": approval.posture,
                "granted_at": approval.granted_at,
            },
            fh,
            sort_keys=False,
        )
    print(f"granted approval {args.approval_id} ({args.flag} -> {target.value}) at {path}")
    return 0


def cmd_promote(root: Path, args: argparse.Namespace) -> int:
    approvals_dir = args.approvals_dir
    try:
        live_state_out = _lane_output(root, args.live_state_out, "--live-state-out")
        audit_log = _lane_output(root, args.audit_log, "--audit-log")
        audit_record_path = _lane_output(root, args.audit_record, "--audit-record", from_lane=True)
    except ValueError as exc:
        print(f"promote blocked: {exc}", file=sys.stderr)
        return 1
    engine = _load_engine(root, audit_path=audit_log)
    if approvals_dir:
        engine.approvals = ApprovalLedger.load_dir(approvals_dir)
    try:
        from_stage = engine.flag(args.flag).stage
        target = RolloutStage.coerce(args.to)
    except (RolloutError, ValueError) as exc:
        print(f"promote blocked: {exc}", file=sys.stderr)
        return 1
    # A promoted live-state entry must name the record file proving it, and
    # that file must exist on disk BEFORE the entry is written
    # (check_rollout.py::check_live_state resolves it). The path is therefore
    # fixed here, and the record is written before write_live_state below.
    record_rel = ""
    if live_state_out:
        if audit_record_path:
            record_rel = args.audit_record
        else:
            # Derived lane-relative by construction: audit_record_name returns
            # a path under infra/rollout/audit/.
            record_rel = audit_record_name(args.flag, from_stage, target, seq=engine.audit.next_seq())
            audit_record_path = str(_rollout_dir(root) / record_rel)
    try:
        engine.promote(
            args.flag,
            target,
            verify_green=args.verify_green,
            approval_id=args.approval,
            actor=args.actor,
            canary_health_ok=args.canary_health_ok,
            gradual_complete=args.gradual_complete,
            audit_record=record_rel,
        )
    except RolloutError as exc:
        print(f"promote blocked: {exc}", file=sys.stderr)
        return 1
    state = engine.flag(args.flag)
    print(
        f"promoted {args.flag} -> {state.stage.value} ({state.rollout_pct}%) "
        f"approval={args.approval}"
    )
    if args.state_out:
        engine.write_state(args.state_out)
        print(f"state written to {args.state_out}")
    if live_state_out:
        record = engine.audit.records()[-1]
        write_audit_record(
            str(audit_record_path),
            flag=args.flag,
            from_stage=from_stage.value,
            to_stage=target.value,
            actor=args.actor,
            approval_kind=str(record.get("approval_kind", "")),
            approval_id=str(record.get("approval_id", "")),
            policy=str(record.get("policy", "")),
            verify_green=bool(record.get("verify_green", args.verify_green)),
            command=(
                f"python3 -m infra.rollout.cli promote {args.flag} --to {target.value} "
                f"--actor {args.actor} --approval {args.approval or '<approval-id>'} "
                f"--approvals-dir {args.approvals_dir or '<approvals-dir>'} "
                f"--audit-log {args.audit_log or '<audit-log>'} "
                f"--live-state-out {args.live_state_out}"
            ),
            outcome=(
                f"promoted {args.flag} {from_stage.value} -> {target.value} "
                f"({state.rollout_pct}%)\naudit log seq={record.get('seq')} hash={record.get('hash')}"
            ),
            live_state_rel=str(args.live_state_out),
            audit_log_rel=str(args.audit_log or "(in-memory only)"),
            audit_seq=int(record.get("seq", 0)),
            audit_hash=str(record.get("hash", "")),
            recorded_at=str(record.get("ts", "")),
        )
        print(f"audit record written to {record_rel}")
        engine.write_live_state(live_state_out)
        print(f"live-state written to {args.live_state_out}")
    return 0


def cmd_canary(root: Path, args: argparse.Namespace) -> int:
    try:
        live_state_out = _lane_output(root, args.live_state_out, "--live-state-out")
        audit_log = _lane_output(root, args.audit_log, "--audit-log")
    except ValueError as exc:
        print(f"canary blocked: {exc}", file=sys.stderr)
        return 1
    engine = _load_engine(root, audit_path=audit_log)
    health_ok = args.health_ok.lower() in ("true", "1", "ok", "yes")
    state = engine.observe_health(args.flag, health_ok=health_ok, actor=args.actor)
    if state.stage is RolloutStage.OFF:
        print(f"{args.flag}: health_ok={health_ok} -> stage off (rolled back)")
    else:
        print(f"{args.flag}: health_ok={health_ok} -> stage {state.stage.value} (no change)")
    if live_state_out:
        engine.write_live_state(live_state_out, audit_record=args.audit_record or "")
        print(f"live-state written to {args.live_state_out}")
    return 0


def cmd_rollback(root: Path, args: argparse.Namespace) -> int:
    try:
        live_state_out = _lane_output(root, args.live_state_out, "--live-state-out")
        audit_log = _lane_output(root, args.audit_log, "--audit-log")
    except ValueError as exc:
        print(f"rollback blocked: {exc}", file=sys.stderr)
        return 1
    engine = _load_engine(root, audit_path=audit_log)
    try:
        engine.manual_rollback(args.flag, actor=args.actor, reason=args.reason)
    except RolloutError as exc:
        print(f"rollback blocked: {exc}", file=sys.stderr)
        return 1
    print(f"rolled back {args.flag} -> off (reason={args.reason})")
    if args.state_out:
        engine.write_state(args.state_out)
        print(f"state written to {args.state_out}")
    if live_state_out:
        # The flag is off, so live_state_doc() simply omits it - a rollback
        # never leaves a stale live-state entry behind.
        engine.write_live_state(live_state_out, audit_record=args.audit_record or "")
        print(f"live-state written to {args.live_state_out}")
    return 0


def cmd_surface_health(root: Path, args: argparse.Namespace) -> int:
    """Drive a console surface's rollback anchor (issue #802).

    Delegates to ``infra/rollout/surface_guard.py`` — one implementation, this
    pipeline-facing entry point — so the automated path and the operator run
    exactly the same reconcile: the surface's own readiness signal decides, a
    failed reading rolls the flag to OFF (audited) **and** engages the runtime
    overlay, and an unreadable reading rolls nothing back.
    """
    from infra.rollout import surface_guard

    try:
        if args.clear:
            cleared = surface_guard.clear(root, args.surface, overlay_path=args.overlay)
            print(
                f"surface {args.surface}: rollback "
                f"{'disengaged' if cleared else 'was not engaged'}"
            )
            return 0
        outcome = surface_guard.reconcile(
            root,
            args.surface,
            overlay_path=args.overlay,
            static_dir=args.static_dir,
            rollout_state_path=args.rollout_state,
            audit_path=args.audit_log,
            state_out=args.state_out,
            actor=args.actor,
        )
    except ValueError as exc:  # an undeclared surface, or an unreadable overlay
        print(f"surface-health: CANNOT-ASSESS — {exc}", file=sys.stderr)
        return 2
    print(surface_guard.report(outcome, args.json))
    return outcome.exit_code


def cmd_validate_approval(root: Path, args: argparse.Namespace) -> int:
    ledger = ApprovalLedger.load_dir(args.approvals_dir)
    try:
        ledger.require(args.flag, RolloutStage.coerce(args.to_stage), actor=args.actor, approval_id=args.approval_id)
    except RolloutError as exc:
        print(f"approval invalid: {exc}", file=sys.stderr)
        return 1
    print(f"approval {args.approval_id} valid for {args.flag} -> {args.to_stage}")
    return 0


def cmd_validate(root: Path, args: argparse.Namespace) -> int:
    errors: List[str] = []
    try:
        engine = _load_engine(root)
    except (RolloutError, OSError, ValueError) as exc:
        errors.append(f"rollout state invalid: {exc}")
        engine = None
    plan = _load_plan(root)
    known = sorted(engine.flags) if engine else []
    errors.extend(validate_go_live_plan_doc(plan, known, engine.model) if engine else [])
    if errors:
        for e in errors:
            print(f"  FAIL  {e}", file=sys.stderr)
        return 1
    print("rollout declarations: OK")
    return 0


def cmd_demo(root: Path, args: argparse.Namespace) -> int:
    """End-to-end offline demo: promote -> canary-fail -> auto rollback to off."""
    print("=== rollout demo: promote / canary-fail / rollback (offline) ===")
    with tempfile.TemporaryDirectory(prefix="ao45-rollout-demo-") as td:
        audit_path = str(Path(td) / "promotion-audit.jsonl")
        engine = _load_engine(root, audit_path=audit_path)
        approvals_dir = Path(td) / "approvals"
        approvals_dir.mkdir()
        engine.approvals = ApprovalLedger.load_dir(str(approvals_dir))
        flag = "services.registry"

        print(f"[1] initial: {flag} = {engine.flag(flag).stage.value} (default off)")
        assert engine.flag(flag).stage is RolloutStage.OFF

        def _grant(approval_id: str, target: str, approver: str) -> None:
            engine.approvals.grant(
                Approval(
                    approval_id=approval_id,
                    flag=flag,
                    target_stage=RolloutStage.coerce(target),
                    approver=approver,
                    granted_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                )
            )
            print(f"[+] approval {approval_id}: {flag} -> {target} by {approver}")

        # OFF -> CANARY (verify-green + approval).
        _grant("approval-canary", "canary", "auditor-sme")
        engine.promote(flag, "canary", verify_green=True, approval_id="approval-canary", actor="deployer-sa")
        print(f"[2] promoted {flag} -> canary ({engine.flag(flag).rollout_pct}%)")

        # CANARY -> GRADUAL (verify-green + approval + canary health ok).
        _grant("approval-gradual", "gradual", "auditor-sme")
        engine.promote(
            flag, "gradual", verify_green=True, approval_id="approval-gradual",
            actor="deployer-sa", canary_health_ok=True,
        )
        print(f"[3] promoted {flag} -> gradual ({engine.flag(flag).rollout_pct}%)")

        # Ramp through the gradual steps.
        for pct in (25, 50, 100):
            engine.ramp(flag, pct, verify_green=True)
        print(f"[4] ramped {flag} to {engine.flag(flag).rollout_pct}%")

        # Canary health FAILS -> auto rollback to OFF (never stays on).
        engine.observe_health(flag, health_ok=False, actor="health-monitor")
        final = engine.flag(flag)
        print(f"[5] canary health FAILED -> {flag} auto-rolled to {final.stage.value}")
        assert final.stage is RolloutStage.OFF, "failed canary must roll back to off"

        # Audit chain intact.
        assert engine.audit.verify(), "promotion audit chain must verify"
        print(f"[6] promotion audit log: {len(engine.audit.records())} records, chain verified")
        for record in engine.audit.records():
            action = record["action"]
            print(
                f"      #{record['seq']} {action:<14} {record['flag']:<20} "
                f"{record.get('from_stage', '')}->{record.get('to_stage', '')} "
                f"reason={record.get('reason', '')}"
            )
    print("demo: PASS (offline end-to-end)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="infra.rollout.cli", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def add_root(p: argparse.ArgumentParser) -> None:
        p.add_argument("--root", type=str, default=str(_REPO_ROOT), help="repo root (auto-detected)")

    p = sub.add_parser("status", help="print current rollout state")
    add_root(p)
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("plan", help="print the Phase 0-8 go-live plan")
    add_root(p)
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("grant-approval", help="record an approval-as-code file")
    add_root(p)
    p.add_argument("flag")
    p.add_argument("--to", dest="to", required=True)
    p.add_argument("--approver", required=True)
    p.add_argument("--approval-id", required=True)
    p.add_argument("--approvals-dir", required=True)
    p.set_defaults(func=cmd_grant_approval)

    p = sub.add_parser("promote", help="promote a flag one gated step")
    add_root(p)
    p.add_argument("flag")
    p.add_argument("--to", dest="to", required=True)
    p.add_argument("--verify-green", action="store_true", default=True)
    p.add_argument("--approval", dest="approval", default=None)
    p.add_argument("--actor", default="deployer-sa")
    p.add_argument("--canary-health-ok", action="store_true", default=False)
    p.add_argument("--gradual-complete", action="store_true", default=False)
    p.add_argument("--approvals-dir", default=None)
    p.add_argument("--state-out", default=None)
    p.add_argument(
        "--audit-log", default=None,
        help="append the hash-chained promotion audit log here (without it the chain lives only in memory)",
    )
    p.add_argument(
        "--live-state-out", default=None,
        help="write the promoted stage to this live-state.yaml (infra/rollout/live-state.yaml is the only "
        "committed file that may ever record a non-off stage - rollout-state.yaml never does); each entry "
        "names the audit record file this run writes under infra/rollout/audit/",
    )
    p.add_argument(
        "--audit-record", default=None,
        help="override the audit record path (relative to infra/rollout/) recorded in live-state and written "
        "as this transition's evidence; default: audit/<flag>-<from>-<to>-s<seq>-<utc>.md",
    )
    p.set_defaults(func=cmd_promote)

    p = sub.add_parser("canary", help="record a canary health observation")
    add_root(p)
    p.add_argument("flag")
    p.add_argument("--health-ok", required=True, choices=["true", "false", "ok", "fail", "1", "0", "yes", "no"])
    p.add_argument("--actor", default="health-monitor")
    p.add_argument("--live-state-out", default=None)
    p.add_argument("--audit-record", default=None)
    p.add_argument("--audit-log", default=None, help="append the health observation to this audit log")
    p.set_defaults(func=cmd_canary)

    p = sub.add_parser("rollback", help="manually roll a flag back to off")
    add_root(p)
    p.add_argument("flag")
    p.add_argument("--reason", default="manual_rollback")
    p.add_argument("--actor", default="deployer-sa")
    p.add_argument("--state-out", default=None)
    p.add_argument("--live-state-out", default=None)
    p.add_argument("--audit-record", default=None)
    p.add_argument("--audit-log", default=None, help="append the rollback to this audit log")
    p.set_defaults(func=cmd_rollback)

    p = sub.add_parser(
        "surface-health",
        help="read a console surface's readiness and roll it back when it fails",
    )
    add_root(p)
    p.add_argument("surface", nargs="?", default="operator_terminal")
    p.add_argument("--clear", action="store_true", help="disengage an engaged rollback")
    p.add_argument("--overlay", default=None, help="an alternate rollback overlay")
    p.add_argument("--static-dir", default=None, help="the console's static root")
    p.add_argument("--rollout-state", default=None, help="an alternate rollout state")
    p.add_argument("--audit-log", default=None, help="where the rollback audit is appended")
    p.add_argument("--state-out", default=None, help="write the transitioned rollout state")
    p.add_argument("--actor", default="surface-health-monitor")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_surface_health)

    p = sub.add_parser("validate-approval", help="fail-closed approval check for the pipeline")
    add_root(p)
    p.add_argument("flag")
    p.add_argument("to_stage")
    p.add_argument("approval_id")
    p.add_argument("--actor", required=True)
    p.add_argument("--approvals-dir", required=True)
    p.set_defaults(func=cmd_validate_approval)

    p = sub.add_parser("validate", help="validate the rollout declarations")
    add_root(p)
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("demo", help="end-to-end offline demo (promote/canary-fail/rollback)")
    add_root(p)
    p.set_defaults(func=cmd_demo)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.root)
    return int(args.func(root, args))


if __name__ == "__main__":
    raise SystemExit(main())
