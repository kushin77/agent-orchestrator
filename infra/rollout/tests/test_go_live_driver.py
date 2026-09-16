"""Go-live driver tests (issue #619, child of #607).

Everything here runs OFFLINE, in a temporary fixture tree, with no cloud
credential and no network call. The fixture root carries the repository's real
declarations (``stage-model.yaml``, ``go-live-plan.yaml``, ``rollout-state.yaml``)
and an empty ``live-state.yaml``, so the driver is exercised through its real
entry point (``python3 infra/rollout/go_live.py --root <fixture>``) against the
real phase plan.

Proved here:

* the whole ordered run is planned in one command (dry-run), phase by phase;
* ``promotion_order: strict-by-phase`` - read by NO code before #619 - is
  computed and enforced, and the refusal names the blocking flag;
* a promotion is REFUSED before anything is written when the owner's approval
  code is absent, and when the approval's approver IS the executing actor;
* the declared 24h gradual dwell blocks ``gradual -> full`` and is measured
  from the ``since`` the live-state already records;
* a partial run followed by a re-run RESUMES: completed transitions are skipped,
  nothing is re-promoted and no exception is raised;
* every transition leaves a real audit record file under
  ``infra/rollout/audit/`` plus a hash-chained audit-log entry that verifies;
* the pipeline-shape regression this lane fixed: writing to ``live-state.yaml``
  keeps the rollout gate green, while the old ``--state-out rollout-state.yaml``
  shape fails it (so the fix is mutation-provable, not cosmetic).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

import pytest
import yaml

_here = Path(__file__).resolve()  # .../infra/rollout/tests/<this file>
_rollout_dir = _here.parents[1]  # .../infra/rollout
_repo_root = _here.parents[3]  # repo root
_go_live = _rollout_dir / "go_live.py"

CANARY_HEALTH = "--canary-health-ok"


# --------------------------------------------------------------------------- #
# Fixture tree + process helpers
# --------------------------------------------------------------------------- #


def _fixture_root(tmp_path: Path, *, promotion_order: str = "strict-by-phase") -> Path:
    """A repo-shaped fixture: the real declarations, an empty live-state."""
    root = tmp_path / "repo"
    target = root / "infra" / "rollout"
    (target / "audit").mkdir(parents=True)
    for name in ("stage-model.yaml", "rollout-state.yaml", "go-live-plan.yaml"):
        shutil.copy(_rollout_dir / name, target / name)
    plan_path = target / "go-live-plan.yaml"
    plan = yaml.safe_load(plan_path.read_text(encoding="utf-8"))
    plan["promotion_order"] = promotion_order
    plan_path.write_text(yaml.safe_dump(plan, sort_keys=False), encoding="utf-8")
    (target / "live-state.yaml").write_text(
        "schema_version: 1\nflags: {}\n", encoding="utf-8"
    )
    return root


def _plan(root: Path) -> dict:
    return yaml.safe_load((root / "infra" / "rollout" / "go-live-plan.yaml").read_text(encoding="utf-8"))


def _live(root: Path) -> dict:
    return yaml.safe_load((root / "infra" / "rollout" / "live-state.yaml").read_text(encoding="utf-8"))


def _write_live(root: Path, entries: Dict[str, dict]) -> None:
    path = root / "infra" / "rollout" / "live-state.yaml"
    doc = {"schema_version": 1, "flags": entries}
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")


def _merge_live(root: Path, entries: Dict[str, dict]) -> None:
    current = dict(_live(root).get("flags") or {})
    current.update(entries)
    _write_live(root, current)


def _record(root: Path, rel: str) -> Path:
    """Create the audit record file a live-state entry names."""
    path = root / "infra" / "rollout" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# planted fixture record {rel}\n", encoding="utf-8")
    return path


def _stage_below(stage: str) -> str:
    """The stage an entry at ``stage`` came from (adjacency is enforced)."""
    return {"canary": "off", "gradual": "canary", "full": "gradual"}[stage]


def _record_phase(
    root: Path, phase: str, *, stage: Optional[str] = None, since: str = "2026-09-10T00:00:00Z"
) -> None:
    """Record every surface of one phase, at its declared go-live stage by default."""
    entries: Dict[str, dict] = {}
    for surface in _plan(root)["phases"][phase]["surfaces"]:
        recorded = stage or surface["go_live_stage"]
        rel = f"audit/{surface['flag'].replace('.', '-')}-{recorded}.md"
        _record(root, rel)
        entry: Dict[str, object] = {
            "stage": recorded,
            "from_stage": _stage_below(recorded),
            "since": since,
            "audit_record": rel,
        }
        if recorded == "full":
            entry["approval_id"] = f"ao-{surface['flag']}-full"
        else:
            entry["policy"] = "low-risk-auto-approve"
        entries[surface["flag"]] = entry
    _merge_live(root, entries)


def _record_flag(root: Path, flag: str, stage: str, *, since: str) -> None:
    rel = f"audit/{flag.replace('.', '-')}-{stage}.md"
    _record(root, rel)
    entry: Dict[str, object] = {
        "stage": stage,
        "from_stage": _stage_below(stage),
        "since": since,
        "audit_record": rel,
    }
    if stage == "full":
        # live-state refuses a full entry whose approval is a policy one.
        entry["approval_id"] = f"ao-{flag}-full"
    else:
        entry["policy"] = "low-risk-auto-approve"
    _merge_live(root, {flag: entry})


def _grant(root: Path, flag: str, target: str, *, approval_id: str, approver: str = "owner-kushin77") -> None:
    path = root / "infra" / "rollout" / "approvals"
    path.mkdir(parents=True, exist_ok=True)
    (path / f"{approval_id}.yaml").write_text(
        yaml.safe_dump(
            {
                "approval_id": approval_id,
                "flag": flag,
                "target_stage": target,
                "approver": approver,
                "posture": "approver",
                "granted_at": "2026-09-16T00:00:00Z",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def _run(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_go_live), "--root", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "infra.rollout.cli", *args],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(_repo_root),
    )


def _audit_log(root: Path):
    sys.path.insert(0, str(_repo_root))
    from infra.rollout.engine import AuditLog

    return AuditLog(str(root / "infra" / "rollout" / "audit" / "promotion-audit.jsonl"))


def _promote_records(root: Path) -> List[dict]:
    return [r for r in _audit_log(root).records() if r.get("action") == "promote"]


def _age(root: Path, flag: str, *, hours: int) -> None:
    """Simulate the declared hold ELAPSING (the only thing a test may fake).

    The driver derives the hold from the recorded ``since``, so advancing time
    means rewriting that one field: no clock seam, no bypass switch.
    """
    entries = dict(_live(root).get("flags") or {})
    assert flag in entries, f"{flag} is not recorded; nothing to age"
    past = datetime.now(timezone.utc) - timedelta(hours=hours)
    entries[flag] = {**entries[flag], "since": past.strftime("%Y-%m-%dT%H:%M:%SZ")}
    _write_live(root, entries)


# --------------------------------------------------------------------------- #
# (a) the ordered run is planned in ONE command, offline
# --------------------------------------------------------------------------- #


def test_preflight_proves_readiness_offline_without_writing_anything(repo_root: str) -> None:
    root = Path(repo_root)
    live_before = (root / "infra" / "rollout" / "live-state.yaml").read_bytes()
    audit_dir = root / "infra" / "rollout" / "audit"
    jsonl_before = {p.name for p in audit_dir.glob("*.jsonl")}

    result = _run(root, "--preflight")

    assert result.returncode == 0, result.stderr
    assert "PREFLIGHT OK" in result.stdout
    assert "OWNER-GATED" in result.stdout
    assert "nothing was promoted" in result.stdout
    # read-only: no state written, no audit log created
    assert (root / "infra" / "rollout" / "live-state.yaml").read_bytes() == live_before
    assert {p.name for p in audit_dir.glob("*.jsonl")} == jsonl_before


def test_preflight_reports_the_missing_owner_approval_codes(repo_root: str) -> None:
    """Every phase-0..7 surface needs a HUMAN approval code to reach full."""
    result = _run(Path(repo_root), "--preflight")
    assert result.returncode == 0
    assert "approval MISSING: services.registry -> full" in result.stdout
    assert "approver must differ from actor 'deployer-sa'" in result.stdout


def test_preflight_with_require_approvals_exits_nonzero_when_not_ready(repo_root: str) -> None:
    result = _run(Path(repo_root), "--preflight", "--require-approvals")
    assert result.returncode == 1
    assert "NOT-OK" in result.stdout


def test_dry_run_walks_every_phase_in_declared_order(repo_root: str) -> None:
    result = _run(Path(repo_root), "--dry-run")
    assert result.returncode == 0, result.stderr
    positions = []
    for phase in ("0", "1", "2", "3", "4", "5", "6", "7", "8"):
        marker = f"phase {phase}  "
        assert marker in result.stdout, f"phase {phase} missing from the plan"
        positions.append(result.stdout.index(marker))
    assert positions == sorted(positions), "the plan did not walk the phases in declared order"
    # every planned surface of the plan appears, and the hold is announced
    assert "services.portal" in result.stdout and "surfaces.operator_terminal" in result.stdout
    assert "24h00m 'gradual' dwell" in result.stdout


# --------------------------------------------------------------------------- #
# (b) strict-by-phase, computed by the driver itself
# --------------------------------------------------------------------------- #


def test_strict_by_phase_refusal_names_the_blocking_flag(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path)
    for phase in ("0", "1", "2", "3", "4", "5"):
        _record_phase(root, phase)
    # services.identity (phase 6) is deliberately still off.
    live_before = (root / "infra" / "rollout" / "live-state.yaml").read_bytes()

    result = _run(root, "--phase", "7", CANARY_HEALTH)

    assert result.returncode == 1
    assert "strict-by-phase" in result.stderr
    assert "services.identity" in result.stderr, result.stderr
    assert "phase 6" in result.stderr
    # refused before anything was written: no phase-7 surface was promoted
    assert (root / "infra" / "rollout" / "live-state.yaml").read_bytes() == live_before


def test_a_later_phase_is_lawful_once_the_earlier_ones_are_complete(tmp_path: Path) -> None:
    """The refusal is the ORDER, not a blanket refusal of phase 7."""
    root = _fixture_root(tmp_path)
    for phase in ("0", "1", "2", "3", "4", "5", "6"):
        _record_phase(root, phase)
    # Phase 7's surfaces are held at gradual, their declared dwell already served.
    _record_phase(root, "7", stage="gradual")
    for surface in _plan(root)["phases"]["7"]["surfaces"]:
        _grant(root, surface["flag"], "full", approval_id=f"ao-{surface['flag']}-full")

    result = _run(root, "--phase", "7", CANARY_HEALTH)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "every requested transition is promoted and recorded" in result.stdout
    assert {e["stage"] for e in _live(root)["flags"].values() if e["stage"] != "full"} == set()


# --------------------------------------------------------------------------- #
# (c)/(d) the owner's approval codes gate the run, BEFORE anything is written
# --------------------------------------------------------------------------- #


def _awaited_full_fixture(tmp_path: Path, *, approver: Optional[str] = None) -> Path:
    """Phase 0 complete; services.registry held at gradual with its dwell served."""
    root = _fixture_root(tmp_path)
    _record_phase(root, "0")
    _record_flag(root, "services.registry", "gradual", since="2026-09-10T00:00:00Z")
    if approver is not None:
        _grant(root, "services.registry", "full", approval_id="ao-registry-full", approver=approver)
    return root


def test_promotion_is_refused_without_an_approval_code(tmp_path: Path) -> None:
    root = _awaited_full_fixture(tmp_path)
    live_before = (root / "infra" / "rollout" / "live-state.yaml").read_bytes()

    result = _run(root, "--only", "services.registry", CANARY_HEALTH)

    assert result.returncode == 1
    assert "refused before promoting anything" in result.stderr
    assert "services.registry -> full" in result.stderr
    assert "no approval code in the ledger" in result.stderr
    # fail-closed: nothing written, no record, no audit log
    assert (root / "infra" / "rollout" / "live-state.yaml").read_bytes() == live_before
    assert not list((root / "infra" / "rollout" / "audit").glob("*.jsonl"))


def test_promotion_is_refused_when_the_approver_is_the_actor(tmp_path: Path) -> None:
    root = _awaited_full_fixture(tmp_path, approver="deployer-sa")
    live_before = (root / "infra" / "rollout" / "live-state.yaml").read_bytes()

    result = _run(root, "--only", "services.registry", CANARY_HEALTH, "--actor", "deployer-sa")

    assert result.returncode == 1
    assert "distinct" in result.stderr, result.stderr
    assert (root / "infra" / "rollout" / "live-state.yaml").read_bytes() == live_before


def test_promotion_proceeds_when_the_owner_approved(tmp_path: Path) -> None:
    root = _awaited_full_fixture(tmp_path, approver="owner-kushin77")

    result = _run(root, "--only", "services.registry", CANARY_HEALTH)

    assert result.returncode == 0, result.stdout + result.stderr
    assert _live(root)["flags"]["services.registry"]["stage"] == "full"
    assert _live(root)["flags"]["services.registry"]["approval_id"] == "ao-registry-full"


# --------------------------------------------------------------------------- #
# the declared 24h hold, measured from the RECORDED timestamp
# --------------------------------------------------------------------------- #


def test_gradual_to_full_waits_for_the_declared_dwell(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path)
    _record_phase(root, "0")
    just_entered = datetime.now(timezone.utc) - timedelta(hours=1)
    _record_flag(root, "services.registry", "gradual", since=just_entered.strftime("%Y-%m-%dT%H:%M:%SZ"))
    _grant(root, "services.registry", "full", approval_id="ao-registry-full")

    result = _run(root, "--only", "services.registry", CANARY_HEALTH)

    assert result.returncode == 1
    assert "waiting" in result.stdout
    assert "left of the declared 24h00m 'gradual' dwell" in result.stdout
    assert "earliest" in result.stdout
    assert _live(root)["flags"]["services.registry"]["stage"] == "gradual"


def test_a_stage_model_that_declares_an_unreadable_dwell_is_rejected() -> None:
    from infra.rollout.model import StageModel

    doc = yaml.safe_load((_rollout_dir / "stage-model.yaml").read_text(encoding="utf-8"))
    doc["stages"]["gradual"]["ramp"]["dwell"] = "whenever"
    with pytest.raises(ValueError) as exc:
        StageModel.load(doc)
    assert "dwell" in str(exc.value)


def test_the_declared_dwell_is_parsed_not_dropped() -> None:
    from infra.rollout.model import RolloutStage, StageModel

    model = StageModel.load(yaml.safe_load((_rollout_dir / "stage-model.yaml").read_text(encoding="utf-8")))
    assert model.spec(RolloutStage.GRADUAL).dwell_seconds == 24 * 3600
    assert model.spec(RolloutStage.CANARY).dwell_seconds == 0


# --------------------------------------------------------------------------- #
# (e) resumability: a partial run, then a re-run that completes it
# --------------------------------------------------------------------------- #


def test_partial_run_then_rerun_resumes_without_repromoting(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path)
    for flag in ("ci_cd.verify_trigger", "ci_cd.apply_trigger"):
        _grant(root, flag, "full", approval_id=f"ao-{flag}-full")

    # Run 1: phase 0 is driven as far as the declared hold allows.
    first = _run(root, "--phase", "0", CANARY_HEALTH)
    assert first.returncode == 1, first.stdout + first.stderr
    assert "waiting" in first.stdout
    records_after_first = len(_promote_records(root))
    assert records_after_first == 4  # 2 flags x (off->canary, canary->gradual)
    assert {e["stage"] for e in _live(root)["flags"].values()} == {"gradual"}
    assert (root / "infra" / "rollout" / "audit" / "promotion-audit.jsonl").is_file()

    # Simulate the 24h hold elapsing, then re-run the SAME command.
    for flag in ("ci_cd.verify_trigger", "ci_cd.apply_trigger"):
        _age(root, flag, hours=25)
    second = _run(root, "--phase", "0", CANARY_HEALTH)
    assert second.returncode == 0, second.stdout + second.stderr
    assert {e["stage"] for e in _live(root)["flags"].values()} == {"full"}
    records = _promote_records(root)
    assert len(records) == records_after_first + 2
    # nothing was re-promoted: each flag's history is off -> canary -> gradual -> full
    for flag in ("ci_cd.verify_trigger", "ci_cd.apply_trigger"):
        steps = [(r["from_stage"], r["to_stage"]) for r in records if r["flag"] == flag]
        assert steps == [("off", "canary"), ("canary", "gradual"), ("gradual", "full")]

    # Run 3: a completed run is a no-op, and raises nothing.
    third = _run(root, "--phase", "0", CANARY_HEALTH)
    assert third.returncode == 0, third.stdout + third.stderr
    assert "already at its declared go-live stage 'full'" in third.stdout
    assert len(_promote_records(root)) == len(records)


def test_upto_caps_a_run_and_canary_needs_no_health_attestation(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path)
    _record_phase(root, "0")

    result = _run(root, "--phase", "1", "--only", "services.registry", "--upto", "canary")

    assert result.returncode == 0, result.stdout + result.stderr
    assert _live(root)["flags"]["services.registry"]["stage"] == "canary"
    assert len(_promote_records(root)) == 1


# --------------------------------------------------------------------------- #
# (f) evidence: a real record file per transition + a verifying hash chain
# --------------------------------------------------------------------------- #


def test_every_transition_leaves_a_record_file_and_a_verifiable_chain(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path)
    _record_phase(root, "0")
    _record_flag(root, "services.registry", "gradual", since="2026-09-10T00:00:00Z")
    _grant(root, "services.registry", "full", approval_id="ao-registry-full")

    result = _run(root, "--only", "services.registry", CANARY_HEALTH)
    assert result.returncode == 0, result.stdout + result.stderr

    log = _audit_log(root)
    assert log.verify(), "the promotion audit chain must verify"
    entries = _live(root)["flags"]
    records = {r["flag"]: r for r in _promote_records(root)}
    # Every entry names a record FILE that exists - including the entries this
    # run did not write, which are carried forward rather than rewritten.
    for flag, entry in entries.items():
        record_path = root / "infra" / "rollout" / entry["audit_record"]
        assert record_path.is_file(), f"{flag} names a record that does not exist: {entry['audit_record']}"
    # The entry for each flag this run promoted names ITS OWN transition's record.
    for flag, record in records.items():
        assert entries[flag]["audit_record"] == record["audit_record"]
        assert entries[flag]["since"] == record["ts"]

    # The fix is mutation-provable: editing a record breaks the chain.
    log_path = root / "infra" / "rollout" / "audit" / "promotion-audit.jsonl"
    lines = log_path.read_text(encoding="utf-8").splitlines()
    tampered = json.loads(lines[0])
    tampered["actor"] = "not-the-deployer"
    lines[0] = json.dumps(tampered, sort_keys=True)
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert not _audit_log(root).verify()
    # and a tampered chain is CANNOT-ASSESS, never a silent re-run
    result_after = _run(root, "--only", "services.registry", CANARY_HEALTH)
    assert result_after.returncode == 2
    assert "audit chain" in result_after.stderr


def test_the_record_is_evidence_with_the_command_and_the_audit_line(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path)
    _record_phase(root, "0")
    _record_flag(root, "services.registry", "gradual", since="2026-09-10T00:00:00Z")
    _grant(root, "services.registry", "full", approval_id="ao-registry-full")
    assert _run(root, "--only", "services.registry", CANARY_HEALTH).returncode == 0

    rel = _live(root)["flags"]["services.registry"]["audit_record"]
    body = (root / "infra" / "rollout" / rel).read_text(encoding="utf-8")
    assert "python3 infra/rollout/go_live.py" in body
    assert "audit log" in body and "hash `" in body
    assert "human approval_id `ao-registry-full`" in body


# --------------------------------------------------------------------------- #
# idempotency / non-forward refusal
# --------------------------------------------------------------------------- #


def test_a_recorded_stage_above_the_plan_is_refused_by_name(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path)
    for phase in ("0", "1", "2", "3", "4", "5", "6", "7"):
        _record_phase(root, phase)
    # phase 8's declared go-live stage is canary; record it ABOVE that.
    _record_flag(root, "rollout.pipeline", "full", since="2026-09-10T00:00:00Z")

    result = _run(root, "--phase", "8", CANARY_HEALTH)

    assert result.returncode == 1
    assert "non-forward move refused" in result.stderr
    assert "rollout.pipeline" in result.stderr


# --------------------------------------------------------------------------- #
# CANNOT-ASSESS (exit 2) - the declaration/state cannot be assessed
# --------------------------------------------------------------------------- #


def test_a_plan_that_does_not_declare_strict_by_phase_is_cannot_assess(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path, promotion_order="unordered")
    result = _run(root, "--preflight")
    assert result.returncode == 2
    assert "promotion_order" in result.stderr


def test_a_missing_declaration_is_cannot_assess(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path)
    (root / "infra" / "rollout" / "go-live-plan.yaml").unlink()
    result = _run(root, "--preflight")
    assert result.returncode == 2
    assert "missing" in result.stderr


def test_a_live_state_entry_without_its_record_is_cannot_assess(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path)
    _write_live(
        root,
        {
            "services.registry": {
                "stage": "canary",
                "from_stage": "off",
                "since": "2026-09-16T00:00:00Z",
                "policy": "low-risk-auto-approve",
                "audit_record": "audit/never-written.md",
            }
        },
    )
    result = _run(root, "--preflight")
    assert result.returncode == 2
    assert "does not exist" in result.stderr


# --------------------------------------------------------------------------- #
# (g) the pipeline-shape regression this lane fixed
# --------------------------------------------------------------------------- #


def test_writing_live_state_keeps_the_rollout_gate_green_but_the_old_shape_fails(tmp_path: Path) -> None:
    sys.path.insert(0, str(_repo_root))
    from infra.rollout.checks.check_rollout import check_live_state
    from infra.rollout.model import StageModel, validate_rollout_state_doc

    root = _fixture_root(tmp_path)
    model = StageModel.load(yaml.safe_load((root / "infra" / "rollout" / "stage-model.yaml").read_text()))
    live = str(root / "infra" / "rollout" / "live-state.yaml")
    audit_log = str(root / "infra" / "rollout" / "audit" / "promotion-audit.jsonl")

    # The fixed shape: --live-state-out (what the Cloud Build configs now pass).
    new = _run_cli(
        "promote", "services.registry", "--to", "canary",
        "--root", str(root), "--actor", "deployer-sa",
        "--audit-log", audit_log,
        "--live-state-out", live,
    )
    assert new.returncode == 0, new.stderr
    live_doc = yaml.safe_load((root / "infra" / "rollout" / "live-state.yaml").read_text())
    assert live_doc["flags"]["services.registry"]["stage"] == "canary"
    assert check_live_state(live_doc, ["services.registry", "services.gateway"], model,
                            rollout_dir=str(root / "infra" / "rollout")) == []
    # rollout-state.yaml (the declared-default document) is still all-off
    state_doc = yaml.safe_load((root / "infra" / "rollout" / "rollout-state.yaml").read_text())
    assert validate_rollout_state_doc(state_doc) == []

    # The OLD shape, which the Cloud Build configs used: --state-out writes the
    # promotion INTO the defaults document and the gate fails.
    old = _run_cli(
        "promote", "services.gateway", "--to", "canary",
        "--root", str(root), "--actor", "deployer-sa",
        "--state-out", str(root / "infra" / "rollout" / "rollout-state.yaml"),
    )
    assert old.returncode == 0, old.stderr
    broken = yaml.safe_load((root / "infra" / "rollout" / "rollout-state.yaml").read_text())
    errors = validate_rollout_state_doc(broken)
    assert errors
    assert any("must default to off, got 'canary'" in e for e in errors)


def test_the_cloudbuild_pipelines_write_live_state_not_the_defaults_document() -> None:
    for name in ("rollout-promote.yaml", "rollout-rollback.yaml"):
        doc = yaml.safe_load((_repo_root / "infra" / "cloudbuild" / name).read_text(encoding="utf-8"))
        # the step arguments, not the prose: the header explains the defect by
        # name, and a substring scan of the whole file would match that comment.
        args_text = "\n".join(
            str(arg) for step in doc.get("steps", []) for arg in (step.get("args") or [])
        )
        assert "--live-state-out infra/rollout/live-state.yaml" in args_text, name
        assert "--state-out" not in args_text, name
        assert "git add infra/rollout/live-state.yaml" in args_text, name
        assert "--audit-log infra/rollout/audit/promotion-audit.jsonl" in args_text, name
    for name in ("rollout-promote-trigger.yaml", "rollout-rollback-trigger.yaml"):
        doc = yaml.safe_load((_repo_root / "infra" / "cloudbuild" / name).read_text(encoding="utf-8"))
        assert doc["disabled"] is True, name
        assert doc["substitutions"]["_ENABLE_ROLLOUT"] == "false", name


# --------------------------------------------------------------------------- #
# the driver and the pipeline agree on the audit-log shape
# --------------------------------------------------------------------------- #


def test_the_promotion_audit_log_is_persisted_and_chained(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path)
    _record_phase(root, "0")
    _record_flag(root, "services.registry", "gradual", since="2026-09-10T00:00:00Z")
    _grant(root, "services.registry", "full", approval_id="ao-registry-full")
    assert _run(root, "--only", "services.registry", CANARY_HEALTH).returncode == 0

    log_path = root / "infra" / "rollout" / "audit" / "promotion-audit.jsonl"
    assert log_path.is_file()
    lines = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert [r["seq"] for r in lines] == list(range(1, len(lines) + 1))
    assert lines[0]["prev_hash"] == "0" * 64
    assert all(r.get("audit_record") for r in lines), "every record names its evidence file"


def test_the_driver_never_shells_out_to_an_apply_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No GCP/terraform surface is reachable from the driver path (GR-5)."""
    root = _fixture_root(tmp_path)
    _record_phase(root, "0")
    for var in ("GOOGLE_APPLICATION_CREDENTIALS", "CLOUDSDK_CONFIG", "GOOGLE_CLOUD_PROJECT"):
        monkeypatch.delenv(var, raising=False)

    result = _run(root, "--phase", "1", "--only", "services.registry", "--upto", "canary")

    assert result.returncode == 0, result.stdout + result.stderr
    source = Path(_go_live).read_text(encoding="utf-8")
    for forbidden in ("import subprocess", "os.system(", "gcloud", "google.cloud", "requests"):
        assert forbidden not in source
