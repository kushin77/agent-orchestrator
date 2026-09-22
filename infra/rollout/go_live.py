"""Ordered, resumable, owner-gated go-live driver (issue #619, child of #607).

ONE command drives the whole owner-approved go-live run: phase 0 -> the
apply-trigger import -> phases 1-6 -> phase 7 (and phase 8, the pipeline
dogfooding itself). The runbook it replaces was ~62-86 manual CLI invocations
plus one hand-edited trigger, with one flag moved ONE adjacent stage per
invocation, no phase awareness, no resume and no dry run.

What this driver owns that no other component did:

* **the declared order.** ``go-live-plan.yaml`` declares
  ``promotion_order: strict-by-phase`` and, until this driver, NO code read it.
  The driver computes the phase order itself and REFUSES phase N+1 while any
  earlier phase's flag is short of its declared ``go_live_stage``, naming the
  blocking flag.
* **the declared hold.** ``stage-model.yaml`` declares
  ``gradual.ramp.dwell: 24h``; only ``ramp.steps`` was ever parsed, and
  ``gradual_complete`` was a caller-supplied boolean. The driver derives the
  hold from the transition timestamp the live-state already records
  (``since``), and refuses ``gradual -> full`` before it elapses. A run that
  enters a dwell reports ``waiting`` and the exact earliest resumption time;
  re-running the same command afterwards resumes it.
* **evidence per transition.** Each transition writes a real audit record file
  under ``infra/rollout/audit/`` BEFORE the live-state entry that names it, so
  ``check_rollout.py`` can never find a promoted flag with no provable trail.
* **idempotency.** The current stage is read LIVE-STATE-AWARE (never from the
  committed all-off defaults), a flag already at its target is skipped, and a
  recorded stage ABOVE its declared target is refused by name rather than
  re-promoted.

Everything is offline: no network, no GCP call, no credential. The only apply
route stays ``infra/cloudbuild/apply.yaml`` as the deployer service account
(GR-5 — no console, no ad-hoc ``terraform apply``), and the owner's approval
codes are the only thing this driver cannot supply.

Exit codes (tri-state, mirroring ``surface_guard.py`` and ``scripts/check-*.sh``):

* **0** — the requested scope is complete (or, in a dry run, lawful and ready).
* **1** — NOT-OK: refused (strict-by-phase, non-forward, missing gate input) or
  incomplete (a transition is waiting on the declared dwell, or deferred).
* **2** — CANNOT-ASSESS: a declaration is missing/unparseable, the plan does
  not cover the phases, the live-state or its audit records cannot be assessed,
  or the audit chain does not verify.

Usage (from the repo root):

    python3 infra/rollout/go_live.py --preflight            # offline readiness
    python3 infra/rollout/go_live.py --dry-run              # what would run
    python3 infra/rollout/go_live.py --phase 0 \
        --actor deployer-sa --approvals-dir infra/rollout/approvals \
        --canary-health-ok
    python3 infra/rollout/go_live.py --phase 1-6 --canary-health-ok \
        --actor deployer-sa --approvals-dir infra/rollout/approvals
    python3 infra/rollout/go_live.py --phase 7 --canary-health-ok \
        --actor deployer-sa --approvals-dir infra/rollout/approvals

---knowledge---
module_id: infra.rollout.go_live
system: infra
app: rollout
solution_class: class
patterns: [pre-standard-snapshot]
derives_from: null
owner_sme: iac-sme
tier: L1
interfaces: [GoLiveCannotAssess, GoLiveRefused, Surface, Step, Assessment, GoLiveDriver, surface_stage_key, build_parser, (+2 more)]
invariants: ""
gotchas: ""
related: ["#1911"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import argparse
import shlex
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

try:
    import yaml
except ImportError as exc:  # pragma: no cover - offline env always has PyYAML
    raise SystemExit(f"go-live driver requires PyYAML ({exc})") from exc

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from infra.rollout.checks.check_rollout import check_live_state  # noqa: E402
from infra.rollout.engine import (  # noqa: E402
    ApprovalLedger,
    RolloutEngine,
    RolloutError,
    audit_record_name,
    write_audit_record,
)
from infra.rollout.model import (  # noqa: E402
    RolloutStage,
    validate_go_live_plan_doc,
)

EXIT_OK = 0
EXIT_NOT_OK = 1
EXIT_CANNOT_ASSESS = 2

#: The only promotion order this driver can honour. The plan declares it; no
#: other component read it before #619, so the driver refuses to run under any
#: other value rather than silently driving an order nobody declared.
STRICT_BY_PHASE = "strict-by-phase"

#: What the driver decided about one planned transition.
D_ATTEMPTABLE = "attemptable"
D_PROMOTED = "promoted"
D_DONE = "done"
D_WAITING = "waiting"
D_DEFERRED = "deferred"
D_BLOCKED = "blocked"

#: Defaults, all relative to ``<root>/infra/rollout``.
DEFAULT_LIVE_STATE = "live-state.yaml"
DEFAULT_AUDIT_LOG = "audit/promotion-audit.jsonl"
DEFAULT_APPROVALS_DIR = "approvals"


class GoLiveCannotAssess(Exception):
    """A declaration or recorded state cannot be assessed (exit 2)."""


class GoLiveRefused(Exception):
    """The run is refused: not lawful, or a required gate input is absent (exit 1)."""


# --------------------------------------------------------------------------- #
# Time helpers
# --------------------------------------------------------------------------- #


def _parse_ts(value: object) -> Optional[datetime]:
    """A recorded UTC timestamp, or ``None`` when it is not readable.

    An unreadable timestamp is never treated as "long ago": the caller decides
    (a dwell cannot be proven, so the transition waits).
    """
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _fmt_ts(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _human_seconds(seconds: float) -> str:
    total = int(max(seconds, 0))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    if minutes:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"


# --------------------------------------------------------------------------- #
# The plan
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Surface:
    """One planned surface: a phase, the flag that gates it, its go-live stage."""

    phase: str
    flag: str
    go_live_stage: RolloutStage


@dataclass(frozen=True)
class Step:
    """One adjacent transition the plan asks for."""

    surface: Surface
    frm: RolloutStage
    to: RolloutStage


@dataclass
class Assessment:
    """What the driver decided about one step, before anything was written."""

    step: Step
    disposition: str = D_ATTEMPTABLE
    note: str = ""
    approval_id: str = ""
    needs_health: bool = False
    audit_record: str = ""

    @property
    def flag(self) -> str:
        return self.step.surface.flag


# --------------------------------------------------------------------------- #
# The driver
# --------------------------------------------------------------------------- #


@dataclass
class GoLiveDriver:
    """Drives the ordered go-live run against recorded (live-state) state.

    ``root`` is the repository root (a fixture root in tests). Everything the
    driver reads and writes lives under ``<root>/infra/rollout`` so the whole
    run can be exercised offline in a temporary tree.
    """

    root: Path
    live_state_rel: str = DEFAULT_LIVE_STATE
    audit_log_rel: str = DEFAULT_AUDIT_LOG
    approvals_rel: str = DEFAULT_APPROVALS_DIR
    actor: str = "deployer-sa"
    canary_health_ok: bool = False
    gradual_complete: bool = False
    verify_green: bool = True
    upto: Optional[RolloutStage] = None
    phase_tokens: Sequence[str] = ()
    only: Sequence[str] = ()
    require_approvals: bool = False
    dry_run: bool = True
    command_line: str = ""
    now: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    engine: Optional[RolloutEngine] = None
    plan_doc: Dict[str, object] = field(default_factory=dict)
    assessments: List[Assessment] = field(default_factory=list)
    recorded: Dict[str, Dict[str, object]] = field(default_factory=dict)
    selected: List[str] = field(default_factory=list)
    surfaces_by_phase: Dict[str, List[Surface]] = field(default_factory=dict)
    phase_order: List[str] = field(default_factory=list)

    # -- paths ------------------------------------------------------------- #

    @property
    def rollout_dir(self) -> Path:
        return self.root / "infra" / "rollout"

    @property
    def live_state_path(self) -> Path:
        return self.rollout_dir / self.live_state_rel

    @property
    def audit_log_path(self) -> Path:
        return self.rollout_dir / self.audit_log_rel

    @property
    def approvals_dir(self) -> Path:
        return self.rollout_dir / self.approvals_rel

    # -- load -------------------------------------------------------------- #

    def _read_yaml(self, path: Path) -> object:
        try:
            with open(path, encoding="utf-8") as fh:
                return yaml.safe_load(fh)
        except FileNotFoundError as exc:
            raise GoLiveCannotAssess(f"missing declaration: {path}") from exc
        except OSError as exc:
            raise GoLiveCannotAssess(f"cannot read {path}: {exc}") from exc
        except yaml.YAMLError as exc:
            raise GoLiveCannotAssess(f"{path} does not parse: {exc}") from exc

    def load(self) -> None:
        """Load the declarations, the recorded live-state and the engine."""
        stage_model = self.rollout_dir / "stage-model.yaml"
        state_path = self.rollout_dir / "rollout-state.yaml"
        plan_path = self.rollout_dir / "go-live-plan.yaml"
        if not (stage_model.is_file() and state_path.is_file() and plan_path.is_file()):
            missing = [str(p) for p in (stage_model, state_path, plan_path) if not p.is_file()]
            raise GoLiveCannotAssess("missing rollout declaration(s): " + ", ".join(missing))
        self._read_yaml(stage_model)
        self._read_yaml(state_path)
        plan_doc = self._read_yaml(plan_path)
        if not isinstance(plan_doc, dict):
            raise GoLiveCannotAssess("go-live-plan.yaml must be a mapping")
        self.plan_doc = plan_doc

        if self.plan_doc.get("promotion_order") != STRICT_BY_PHASE:
            raise GoLiveCannotAssess(
                "go-live-plan promotion_order must be "
                f"'{STRICT_BY_PHASE}' for this driver to honour it, got "
                f"{self.plan_doc.get('promotion_order')!r}"
            )

        try:
            engine = RolloutEngine.load(
                stage_model_path=str(stage_model),
                rollout_state_path=str(state_path),
                audit_path=str(self.audit_log_path),
                live_state_path=str(self.live_state_path),
            )
        except (RolloutError, ValueError, OSError) as exc:
            raise GoLiveCannotAssess(f"rollout state cannot be assessed: {exc}") from exc
        engine.approvals = ApprovalLedger.load_dir(str(self.approvals_dir))
        self.engine = engine

        errors = validate_go_live_plan_doc(self.plan_doc, list(engine.flags), engine.model)
        if errors:
            raise GoLiveCannotAssess("go-live plan invalid: " + "; ".join(errors))

        self.recorded = self._recorded_entries()
        self.phase_order = [str(p) for p in (self.plan_doc.get("phase_order") or [])]
        if not self.phase_order:
            raise GoLiveCannotAssess("go-live-plan declares no phase_order")
        self._check_audit_chain()

    def _recorded_entries(self) -> Dict[str, Dict[str, object]]:
        """The recorded (live-state) entries - NEVER the all-off defaults.

        ``rollout-state.yaml`` is the declared-default document (GR-28, always
        all-off); reading the current stage from it would make every re-run
        re-promote a flag that is already live. Resuming therefore reads
        ``live-state.yaml``, the only committed file that records a promotion.
        """
        if not self.live_state_path.is_file():
            return {}
        doc = self._read_yaml(self.live_state_path)
        if not isinstance(doc, dict):
            raise GoLiveCannotAssess(f"{self.live_state_path} must be a mapping")
        flags = doc.get("flags") or {}
        if not isinstance(flags, dict):
            raise GoLiveCannotAssess(f"{self.live_state_path} flags must be a mapping")
        return {
            str(name): entry if isinstance(entry, dict) else {}
            for name, entry in flags.items()
        }

    def _check_audit_chain(self) -> None:
        """A promoted flag whose evidence cannot be checked is CANNOT-ASSESS."""
        assert self.engine is not None
        if self.engine.audit.path and not self.engine.audit.verify():
            raise GoLiveCannotAssess(
                f"the promotion audit chain at {self.audit_log_path} does not verify "
                "(a record was edited, reordered or truncated)"
            )
        live_doc = {"schema_version": 1, "flags": self.recorded}
        errors = check_live_state(
            live_doc, list(self.engine.flags), self.engine.model, rollout_dir=str(self.rollout_dir)
        )
        if errors:
            raise GoLiveCannotAssess("live-state cannot be assessed: " + "; ".join(errors))

    # -- planning ---------------------------------------------------------- #

    def _surfaces(self) -> Dict[str, List[Surface]]:
        phases = self.plan_doc.get("phases") or {}
        if not isinstance(phases, dict):
            raise GoLiveCannotAssess("go-live-plan phases must be a mapping")
        out: Dict[str, List[Surface]] = {}
        for phase in self.phase_order:
            body = phases.get(phase) or {}
            surfaces = body.get("surfaces") if isinstance(body, dict) else None
            if not isinstance(surfaces, list) or not surfaces:
                raise GoLiveCannotAssess(f"go-live-plan phase {phase} declares no surfaces")
            entries: List[Surface] = []
            for surface in surfaces:
                stage = surface.get("go_live_stage") if isinstance(surface, dict) else None
                if not isinstance(stage, str) or not stage:
                    raise GoLiveCannotAssess(
                        f"go-live-plan phase {phase} surface {surface!r} declares no go_live_stage"
                    )
                try:
                    entries.append(
                        Surface(phase=phase, flag=str(surface["flag"]), go_live_stage=RolloutStage.coerce(stage))
                    )
                except (KeyError, ValueError) as exc:
                    raise GoLiveCannotAssess(f"go-live-plan phase {phase} surface is unusable: {exc}") from exc
            out[phase] = entries
        return out

    def _expand_phases(self, tokens: Sequence[str]) -> List[str]:
        """Expand ``--phase`` tokens ("0", "1-6", "7,8") in plan order."""
        if not tokens:
            return list(self.phase_order)
        wanted: List[str] = []
        for token in tokens:
            for part in str(token).split(","):
                part = part.strip()
                if not part:
                    continue
                if "-" in part:
                    lo, _, hi = part.partition("-")
                    lo, hi = lo.strip(), hi.strip()
                    if not (lo.isdigit() and hi.isdigit()) or int(lo) > int(hi):
                        raise GoLiveCannotAssess(f"--phase range {part!r} is not 'N' or 'N-M'")
                    candidates = [str(n) for n in range(int(lo), int(hi) + 1)]
                else:
                    candidates = [part]
                for candidate in candidates:
                    if candidate not in self.phase_order:
                        raise GoLiveCannotAssess(
                            f"--phase {candidate!r} is not declared in go-live-plan phase_order "
                            f"{self.phase_order}"
                        )
                    if candidate not in wanted:
                        wanted.append(candidate)
        return [phase for phase in self.phase_order if phase in wanted]

    def _phase_blockers(
        self, phase: str, projected: Dict[str, RolloutStage]
    ) -> List[Tuple[str, str, RolloutStage]]:
        """Earlier-phase surfaces short of their go-live stage (plan order).

        ``projected`` is the state being *simulated*: the phases earlier in the
        same run count as done, so ONE command can lawfully drive 0 -> 1 -> ...
        -> 7 in a single pass while a run that starts at phase 7 is refused.
        """
        index = self.phase_order.index(phase)
        blockers: List[Tuple[str, str, RolloutStage]] = []
        for earlier in self.phase_order[:index]:
            for surface in self.surfaces_by_phase.get(earlier, []):
                if projected[surface.flag].order < surface.go_live_stage.order:
                    blockers.append((earlier, surface.flag, surface.go_live_stage))
        return blockers

    def build_plan(self) -> None:
        assert self.engine is not None
        if not self.live_state_path.is_file():
            raise GoLiveCannotAssess(
                f"missing {self.live_state_path} (the driver resumes from recorded state; "
                "the committed file with an empty flags map is the starting point)"
            )
        self.surfaces_by_phase = self._surfaces()
        self.selected = self._expand_phases(self.phase_tokens)
        known = {s.flag for surfaces in self.surfaces_by_phase.values() for s in surfaces}
        unknown = [flag for flag in self.only if flag not in known]
        if unknown:
            raise GoLiveCannotAssess(
                f"--only flag(s) not declared in go-live-plan.yaml: {', '.join(sorted(unknown))}"
            )
        if self.only:
            # Narrowing to named flags narrows the phases that are driven - and
            # therefore which phases must already be complete. The phase order
            # for those phases is still enforced in full: driving one flag of
            # phase 1 still requires phase 0 to be live.
            self.selected = [
                phase
                for phase in self.selected
                if any(surface.flag in self.only for surface in self.surfaces_by_phase.get(phase, []))
            ]
        projected: Dict[str, RolloutStage] = {
            name: state.stage for name, state in self.engine.flags.items()
        }
        assessments: List[Assessment] = []
        for phase in self.selected:
            blockers = self._phase_blockers(phase, projected)
            if blockers:
                earlier, flag, declared = blockers[0]
                detail = "; ".join(
                    f"'{f}' is at '{projected[f].value}' and its declared go-live stage is '{d.value}'"
                    for _, f, d in blockers
                )
                raise GoLiveRefused(
                    f"strict-by-phase: phase {phase} may not run before phase {earlier} is complete - "
                    f"blocking flag '{flag}' ({detail})"
                )
            for surface in self.surfaces_by_phase[phase]:
                if self.only and surface.flag not in self.only:
                    continue
                assessments.extend(self._plan_surface(surface, projected))
        self.assessments = assessments

    def _plan_surface(self, surface: Surface, projected: Dict[str, RolloutStage]) -> List[Assessment]:
        flag = surface.flag
        current = projected[flag]
        declared = surface.go_live_stage
        if current.order > declared.order:
            raise GoLiveRefused(
                f"non-forward move refused: flag '{flag}' is recorded at '{current.value}', ABOVE its "
                f"declared go-live stage '{declared.value}' - the driver will not move a flag backwards "
                "(roll it back deliberately, or correct go-live-plan.yaml)"
            )
        target = declared
        if self.upto is not None and self.upto.order < declared.order:
            target = self.upto
        if current.order >= target.order:
            note = (
                f"already at its declared go-live stage '{declared.value}'"
                if current.order == declared.order
                else f"at '{current.value}', at or above the requested cap '{target.value}'"
            )
            return [Assessment(Step(surface, current, target), disposition=D_DONE, note=note)]
        out: List[Assessment] = []
        stage = current
        while stage.order < target.order:
            nxt = RolloutStage(surface_stage_key(stage))
            step = Step(surface=surface, frm=stage, to=nxt)
            out.append(self._assess(step))
            projected[flag] = nxt
            stage = nxt
        return out

    def _assess(self, step: Step) -> Assessment:
        """Decide one step's disposition WITHOUT writing anything."""
        assert self.engine is not None
        model = self.engine.model
        flag = step.surface.flag
        notes: List[str] = []
        assessment = Assessment(step=step)

        policy = model.approval_policy
        auto_approved = policy is not None and policy.auto_approves(step.to)
        if auto_approved:
            notes.append(f"policy auto-approve '{policy.policy}'")

        if not self.verify_green:
            assessment.disposition = D_BLOCKED
            assessment.note = "blocked: green verification evidence is not asserted (--no-verify-green)"
            return assessment

        requirements = (*model.every_transition_requires, *model.target_requirements(step.to))

        # The declared hold on the stage being LEFT, measured from the recorded
        # transition timestamp - never from a caller-supplied boolean.
        hold = model.spec(step.frm).dwell_seconds
        hold_elapsed = False
        if hold > 0:
            entry = self.recorded.get(flag) or {}
            entered = _parse_ts(entry.get("since"))
            recorded_here = str(entry.get("stage", "")) == step.frm.value
            if not recorded_here or entered is None:
                assessment.disposition = D_DEFERRED
                assessment.note = (
                    f"deferred: the declared {_human_seconds(hold)} '{step.frm.value}' dwell starts only "
                    f"once '{step.frm.value}' is RECORDED for {flag} (it is reached later in this run); "
                    "re-run this command after the hold"
                )
                return assessment
            elapsed = (self.now - entered).total_seconds()
            if elapsed < hold:
                earliest = entered + timedelta(seconds=hold)
                assessment.disposition = D_WAITING
                assessment.note = (
                    f"waiting: {_human_seconds(hold - elapsed)} left of the declared "
                    f"{_human_seconds(hold)} '{step.frm.value}' dwell (entered {_fmt_ts(entered)}; "
                    f"earliest {_fmt_ts(earliest)})"
                )
                return assessment
            hold_elapsed = True
            notes.append(f"{_human_seconds(hold)} '{step.frm.value}' dwell elapsed (entered {_fmt_ts(entered)})")

        if "canary_health_ok" in requirements:
            assessment.needs_health = True
            if not self.canary_health_ok:
                assessment.disposition = D_BLOCKED
                assessment.note = (
                    "blocked: missing gate signal canary_health_ok - pass --canary-health-ok only after "
                    "the canary health check is green (the driver does not measure health)"
                )
                return assessment

        if "gradual_complete" in requirements and not (hold_elapsed or self.gradual_complete):
            assessment.disposition = D_BLOCKED
            assessment.note = (
                "blocked: missing gate signal gradual_complete - the driver derives it from the declared "
                f"'{step.frm.value}' dwell, which this stage declares none of; pass --gradual-complete only "
                "after the gradual ramp is complete"
            )
            return assessment

        if "approval_code" in requirements and not auto_approved:
            approval_id = self.approval_choice(flag, step.to)
            if not approval_id:
                assessment.disposition = D_BLOCKED
                assessment.note = (
                    "blocked: no approval code in the ledger for this flag and this target stage "
                    "(grant one with `python3 -m infra.rollout.cli grant-approval`; the approver must be "
                    "an owner distinct from the executing actor)"
                )
                return assessment
            try:
                self.engine.approvals.require(flag, step.to, actor=self.actor, approval_id=approval_id)
            except RolloutError as exc:
                assessment.disposition = D_BLOCKED
                assessment.note = f"blocked: approval '{approval_id}' is not usable - {exc}"
                return assessment
            assessment.approval_id = approval_id
            notes.append(f"approval-as-code '{approval_id}'")

        assessment.note = " | ".join(notes) or "no additional signals required"
        return assessment

    def approval_choice(self, flag: str, target: RolloutStage) -> str:
        """The deterministic approval record for (flag, target), or ``""``.

        Deterministic so a re-run resolves the same record: approvals are
        ordered by approval id and the first match wins. A self-granted record
        is NOT skipped in favour of a later one - it is validated, so a
        self-granted approval refuses the run (AO-GR-14) rather than being
        silently bypassed.
        """
        assert self.engine is not None
        for approval in self.engine.approvals.records():
            if approval.flag == flag and approval.target_stage is target:
                return approval.approval_id
        return ""

    # -- gating ------------------------------------------------------------ #

    def blocked(self) -> List[Assessment]:
        return [a for a in self.assessments if a.disposition == D_BLOCKED]

    def refuse_if_inputs_missing(self) -> None:
        """Fail closed: nothing is written when a planned step lacks a gate input.

        The owner's approval codes are the point: a run that would promote
        without them is refused BEFORE the first transition, so a partial run
        can never be produced by a missing approval.
        """
        blocked = self.blocked()
        if not blocked:
            return
        lines = [f"  {a.flag} -> {a.step.to.value}: {a.note}" for a in blocked]
        raise GoLiveRefused(
            f"refused before promoting anything: {len(blocked)} planned transition(s) are missing a "
            "required gate input (owner approval code, health attestation or verification evidence)\n"
            + "\n".join(lines)
        )

    # -- execution --------------------------------------------------------- #

    def execute(self) -> None:
        """Promote every attemptable step, in plan order, recording evidence."""
        assert self.engine is not None
        for assessment in self.assessments:
            if assessment.disposition != D_ATTEMPTABLE:
                continue
            step = assessment.step
            seq = self.engine.audit.next_seq()
            record_rel = audit_record_name(step.surface.flag, step.frm, step.to, seq=seq)
            try:
                self.engine.promote(
                    step.surface.flag,
                    step.to,
                    verify_green=True,
                    approval_id=assessment.approval_id or None,
                    actor=self.actor,
                    canary_health_ok=True if assessment.needs_health else None,
                    gradual_complete=self._gradual_complete_for(step),
                    audit_record=record_rel,
                )
            except RolloutError as exc:
                assessment.disposition = D_BLOCKED
                assessment.note = f"refused by the engine: {exc}"
                break
            record = self.engine.audit.records()[-1]
            state = self.engine.flag(step.surface.flag)
            # The record is written BEFORE the live-state entry that names it.
            write_audit_record(
                str(self.rollout_dir / record_rel),
                flag=step.surface.flag,
                from_stage=step.frm.value,
                to_stage=step.to.value,
                actor=self.actor,
                approval_kind=str(record.get("approval_kind", "")),
                approval_id=str(record.get("approval_id", "")),
                policy=str(record.get("policy", "")),
                verify_green=True,
                command=self.command_line or "python3 infra/rollout/go_live.py --help",
                outcome=(
                    f"promoted {step.surface.flag} {step.frm.value} -> {step.to.value} "
                    f"({state.rollout_pct}%)\naudit log seq={record.get('seq')} hash={record.get('hash')}"
                ),
                live_state_rel=self.live_state_rel,
                audit_log_rel=self.audit_log_rel,
                audit_seq=int(record.get("seq", 0)),
                audit_hash=str(record.get("hash", "")),
                recorded_at=str(record.get("ts", "")),
            )
            assessment.audit_record = record_rel
            assessment.disposition = D_PROMOTED
            self.engine.write_live_state(str(self.live_state_path))

    def _gradual_complete_for(self, step: Step) -> bool:
        """``gradual_complete`` is DERIVED, not taken on the caller's word.

        Reaching here means the departing stage's declared hold was observed
        (``_assess`` refuses otherwise), so a ``gradual -> full`` step carries
        measured evidence. A stage model that declares no hold cannot be
        measured by the driver, and ``_assess`` requires ``--gradual-complete``.
        """
        if step.frm is RolloutStage.GRADUAL:
            assert self.engine is not None
            return self.engine.model.spec(RolloutStage.GRADUAL).dwell_seconds > 0
        return False

    # -- reporting --------------------------------------------------------- #

    def _print_plan(self) -> None:
        assert self.engine is not None
        mode = "preflight (dry-run)" if self.dry_run else "run"
        print(f"=== go-live driver - {mode} ===")
        print(f"root: {self.root}")
        print("declarations: OK (stage-model.yaml, go-live-plan.yaml, rollout-state.yaml, live-state.yaml)")
        print(f"promotion_order: '{self.plan_doc.get('promotion_order')}' (read and honoured by this driver)")
        upto = self.upto.value if self.upto else "the plan's declared go-live stage"
        print(
            f"scope: phase(s) {', '.join(self.selected)}; up to: {upto}; actor: {self.actor}; "
            f"dry_run: {str(self.dry_run).lower()}"
        )
        print("")
        for phase in self.selected:
            body = (self.plan_doc.get("phases") or {}).get(phase) or {}
            name = body.get("name", "") if isinstance(body, dict) else ""
            print(f"phase {phase}  {name}")
            for assessment in self.assessments:
                if assessment.step.surface.phase != phase:
                    continue
                step = assessment.step
                marks = {
                    D_ATTEMPTABLE: "+",
                    D_PROMOTED: "*",
                    D_DONE: "=",
                    D_WAITING: "~",
                    D_DEFERRED: "...",
                    D_BLOCKED: "-",
                }
                mark = marks.get(assessment.disposition, "?")
                print(f"  {mark} {step.surface.flag:<26} {step.frm.value} -> {step.to.value}")
                if assessment.note:
                    print(f"      {assessment.note}")
        print("")

    def _print_summary(self) -> int:
        assert self.engine is not None
        promoted = [a for a in self.assessments if a.disposition == D_PROMOTED]
        waiting = [a for a in self.assessments if a.disposition == D_WAITING]
        deferred = [a for a in self.assessments if a.disposition == D_DEFERRED]
        blocked = self.blocked()
        owner_steps = [
            a
            for a in self.assessments
            if self._needs_approval(a) and a.disposition != D_DONE
        ]
        missing_approvals = [a for a in owner_steps if not a.approval_id]

        for assessment in promoted:
            print(
                f"  promoted: {assessment.flag} {assessment.step.frm.value} -> "
                f"{assessment.step.to.value} (record {assessment.audit_record})"
            )
        print(
            f"transitions: {len(promoted)} promoted, {len(waiting)} waiting on the declared dwell, "
            f"{len(deferred)} deferred (the declared hold starts later), {len(blocked)} blocked"
        )
        if owner_steps:
            print(
                f"OWNER-GATED: {len(owner_steps)} planned transition(s) need the owner's approval code "
                f"(the approvals ledger at {self.approvals_dir} holds "
                f"{len(self.engine.approvals.records())} record(s); "
                f"{len(missing_approvals)} unresolvable)"
            )
            for assessment in owner_steps:
                state = "resolved" if assessment.approval_id else "MISSING"
                print(
                    f"  approval {state}: {assessment.flag} -> {assessment.step.to.value} "
                    f"(approver must differ from actor '{self.actor}')"
                )
        for assessment in waiting + deferred + blocked:
            print(f"  {assessment.flag} -> {assessment.step.to.value}: {self._short(assessment)}")

        if not self.dry_run:
            records = self.engine.audit.records()
            verified = self.engine.audit.verify()
            print(
                f"audit log: {self.audit_log_rel} "
                + (f"({len(records)} record(s), chain {'verified' if verified else 'BROKEN'})" if records else "(no records)")
            )
            print(f"live-state: {self.live_state_rel} ({len(self._promoted_entries())} promoted flag(s))")
            if not verified:
                print("go-live driver: NOT-OK - the promotion audit chain does not verify")
                return EXIT_NOT_OK
        else:
            print(f"audit chain: {'verified' if self.engine.audit.verify() else 'BROKEN'} (read-only)")
            print("nothing was promoted: no state written, no cloud call, no credential used")

        if self.dry_run:
            if self.require_approvals and missing_approvals:
                print(
                    f"go-live driver: NOT-OK - --require-approvals is set and {len(missing_approvals)} "
                    "transition(s) have no approval code"
                )
                return EXIT_NOT_OK
            print("go-live driver: PREFLIGHT OK - the run is ordered and lawful; the owner's approval codes")
            print("and the declared holds are the only things between this and production")
            return EXIT_OK
        if blocked:
            print(f"go-live driver: NOT-OK - {len(blocked)} transition(s) blocked")
            return EXIT_NOT_OK
        if waiting or deferred:
            print(
                f"go-live driver: NOT-OK - {len(waiting) + len(deferred)} transition(s) still to come; "
                "re-run this exact command after the declared hold elapses"
            )
            return EXIT_NOT_OK
        print("go-live driver: OK - every requested transition is promoted and recorded")
        return EXIT_OK

    def _needs_approval(self, assessment: Assessment) -> bool:
        assert self.engine is not None
        model = self.engine.model
        policy = model.approval_policy
        if policy is not None and policy.auto_approves(assessment.step.to):
            return False
        return "approval_code" in model.every_transition_requires

    @staticmethod
    def _short(assessment: Assessment) -> str:
        """The summary's one-line reason (the plan section carries the full text)."""
        if assessment.disposition == D_DEFERRED:
            return "deferred: the declared hold on the stage being left starts later in this run"
        note = assessment.note
        for suffix in (" - pass ", " (grant "):
            if suffix in note:
                note = note.split(suffix, 1)[0]
        return note

    def _promoted_entries(self) -> Dict[str, RolloutStage]:
        """Every flag currently above 'off' in the engine's view."""
        assert self.engine is not None
        return {
            name: state.stage
            for name, state in self.engine.flags.items()
            if state.stage is not RolloutStage.OFF
        }

    def run(self) -> int:
        try:
            self.load()
            self.build_plan()
        except GoLiveCannotAssess as exc:
            print(f"go-live driver: CANNOT-ASSESS - {exc}", file=sys.stderr)
            return EXIT_CANNOT_ASSESS
        except GoLiveRefused as exc:
            print(f"go-live driver: NOT-OK - {exc}", file=sys.stderr)
            return EXIT_NOT_OK
        self._print_plan()
        if not self.dry_run:
            try:
                self.refuse_if_inputs_missing()
            except GoLiveRefused as exc:
                print(f"go-live driver: NOT-OK - {exc}", file=sys.stderr)
                return EXIT_NOT_OK
            self.execute()
        return self._print_summary()


def surface_stage_key(stage: RolloutStage) -> str:
    """The token of the next stage up from ``stage`` (adjacency, strict-forward)."""
    order = ("off", "canary", "gradual", "full")
    if stage.order + 1 >= len(order):
        raise GoLiveRefused(f"there is no stage above '{stage.value}'")
    return order[stage.order + 1]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="infra/rollout/go_live.py",
        description="Ordered, resumable, owner-gated go-live driver (issue #619).",
    )
    parser.add_argument("--root", type=str, default=str(_REPO_ROOT), help="repository root (auto-detected)")
    parser.add_argument("--phase", action="append", default=[], metavar="N|N-M",
                        help="phase(s) to drive, e.g. --phase 0 --phase 1-6 (default: every phase, in order)")
    parser.add_argument("--only", action="append", default=[], metavar="FLAG",
                        help="restrict to these flag(s); the phase order still applies")
    parser.add_argument("--upto", default=None, choices=["off", "canary", "gradual", "full"],
                        help="stop at this stage (e.g. --upto canary for a canary-only run)")
    parser.add_argument("--actor", default="deployer-sa", help="the executing identity (approver must differ)")
    parser.add_argument("--approvals-dir", default=DEFAULT_APPROVALS_DIR,
                        help="approval ledger directory, relative to infra/rollout/")
    parser.add_argument("--live-state-out", default=DEFAULT_LIVE_STATE,
                        help="recorded live-state file, relative to infra/rollout/")
    parser.add_argument("--audit-log", default=DEFAULT_AUDIT_LOG,
                        help="hash-chained promotion audit log, relative to infra/rollout/")
    parser.add_argument("--canary-health-ok", action="store_true",
                        help="the canary health check is green (the driver does not measure health)")
    parser.add_argument("--gradual-complete", action="store_true",
                        help="the gradual ramp is complete (only needed when no dwell is declared)")
    parser.add_argument("--verify-green", dest="verify_green", action="store_true", default=True)
    parser.add_argument("--no-verify-green", dest="verify_green", action="store_false",
                        help="assert the gate is NOT green, so every transition is refused")
    parser.add_argument("--dry-run", action="store_true", help="plan and report only; write nothing")
    parser.add_argument("--preflight", action="store_true",
                        help="offline readiness proof: dry-run plus state/evidence assertions")
    parser.add_argument("--require-approvals", action="store_true",
                        help="exit 1 in a dry run when an attemptable transition has no approval code")
    return parser


def driver_from_args(args: argparse.Namespace, argv: Sequence[str]) -> GoLiveDriver:
    dry_run = bool(args.dry_run or args.preflight)
    return GoLiveDriver(
        root=Path(args.root),
        live_state_rel=args.live_state_out,
        audit_log_rel=args.audit_log,
        approvals_rel=args.approvals_dir,
        actor=args.actor,
        canary_health_ok=args.canary_health_ok,
        gradual_complete=args.gradual_complete,
        verify_green=args.verify_green,
        upto=RolloutStage.coerce(args.upto) if args.upto else None,
        phase_tokens=args.phase,
        only=args.only,
        require_approvals=args.require_approvals,
        dry_run=dry_run,
        command_line=shlex.join(["python3", "infra/rollout/go_live.py", *argv]),
    )


def main(argv: Optional[List[str]] = None) -> int:
    raw = list(argv) if argv is not None else sys.argv[1:]
    args = build_parser().parse_args(raw)
    try:
        return driver_from_args(args, raw).run()
    except ValueError as exc:  # an unknown --upto token reaches here
        print(f"go-live driver: CANNOT-ASSESS - {exc}", file=sys.stderr)
        return EXIT_CANNOT_ASSESS


if __name__ == "__main__":
    raise SystemExit(main())
