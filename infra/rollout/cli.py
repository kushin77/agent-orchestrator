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
        --approval ID --actor deployer-sa [--canary-health-ok] [--gradual-complete]
    python3 -m infra.rollout.cli canary FLAG --health-ok false
    python3 -m infra.rollout.cli rollback FLAG --reason canary_health_failure
    python3 -m infra.rollout.cli validate-approval FLAG STAGE ID --actor X --approvals-dir DIR
    python3 -m infra.rollout.cli demo          # end-to-end offline demo
    python3 -m infra.rollout.cli validate      # validate the declarative files
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

from infra.rollout.engine import Approval, ApprovalLedger, RolloutEngine, RolloutError
from infra.rollout.model import RolloutStage, validate_go_live_plan_doc


def _rollout_dir(root: Path) -> Path:
    return root / "infra" / "rollout"


def _load_engine(root: Path, audit_path: Optional[str] = None) -> RolloutEngine:
    rd = _rollout_dir(root)
    return RolloutEngine.load(
        stage_model_path=str(rd / "stage-model.yaml"),
        rollout_state_path=str(rd / "rollout-state.yaml"),
        audit_path=audit_path,
    )


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
    engine = _load_engine(root)
    if approvals_dir:
        engine.approvals = ApprovalLedger.load_dir(approvals_dir)
    try:
        engine.promote(
            args.flag,
            args.to,
            verify_green=args.verify_green,
            approval_id=args.approval,
            actor=args.actor,
            canary_health_ok=args.canary_health_ok,
            gradual_complete=args.gradual_complete,
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
    return 0


def cmd_canary(root: Path, args: argparse.Namespace) -> int:
    engine = _load_engine(root)
    health_ok = args.health_ok.lower() in ("true", "1", "ok", "yes")
    state = engine.observe_health(args.flag, health_ok=health_ok, actor=args.actor)
    if state.stage is RolloutStage.OFF:
        print(f"{args.flag}: health_ok={health_ok} -> stage off (rolled back)")
    else:
        print(f"{args.flag}: health_ok={health_ok} -> stage {state.stage.value} (no change)")
    return 0


def cmd_rollback(root: Path, args: argparse.Namespace) -> int:
    engine = _load_engine(root)
    try:
        engine.manual_rollback(args.flag, actor=args.actor, reason=args.reason)
    except RolloutError as exc:
        print(f"rollback blocked: {exc}", file=sys.stderr)
        return 1
    print(f"rolled back {args.flag} -> off (reason={args.reason})")
    if args.state_out:
        engine.write_state(args.state_out)
        print(f"state written to {args.state_out}")
    return 0


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
    p.set_defaults(func=cmd_promote)

    p = sub.add_parser("canary", help="record a canary health observation")
    add_root(p)
    p.add_argument("flag")
    p.add_argument("--health-ok", required=True, choices=["true", "false", "ok", "fail", "1", "0", "yes", "no"])
    p.add_argument("--actor", default="health-monitor")
    p.set_defaults(func=cmd_canary)

    p = sub.add_parser("rollback", help="manually roll a flag back to off")
    add_root(p)
    p.add_argument("flag")
    p.add_argument("--reason", default="manual_rollback")
    p.add_argument("--actor", default="deployer-sa")
    p.add_argument("--state-out", default=None)
    p.set_defaults(func=cmd_rollback)

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
