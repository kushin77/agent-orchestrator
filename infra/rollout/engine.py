"""Rollout and deployment pipeline engine (issue #45) - promotion + rollback.

Reads the declarative stage model, the current rollout state and the go-live
plan, then drives promotions strictly forward (OFF -> CANARY -> GRADUAL ->
FULL) only when the promotion gate is green (verify evidence + approval-as-code
with a distinct approver), auto-rolls a flag OFF when a canary/gradual health
check fails, and appends every transition to an append-only, hash-chained
promotion audit log. This engine is the code the Cloud Build promotion and
rollback pipelines invoke (infra/cloudbuild/rollout-*.yaml) - promotion is
only via that automated path, never a console click (AO-GR-5).

Offline by design: no network, no GCP calls. State transitions are applied to
an in-memory state seeded from the committed rollout-state.yaml and, where a
caller asks, persisted atomically to a supplied path (never written into the
repo tree by the offline CLI/demo).
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional

try:
    import yaml
except ImportError as exc:  # pragma: no cover - offline env always has PyYAML
    raise SystemExit(f"rollout engine requires PyYAML ({exc})") from exc

from infra.rollout.model import (
    FlagState,
    PromotionSignals,
    PromotionVerdict,
    RolloutStage,
    StageModel,
    check_promotion,
    rollback_decision,
    validate_rollout_state_doc,
)

GENESIS_HASH = "0" * 64
DEFAULT_REASON = "promotion"


class RolloutError(Exception):
    """Raised when a rollout operation violates a gate or invariant."""


# --------------------------------------------------------------------------- #
# Approval-as-code
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Approval:
    """A recorded approval to promote one flag to one target stage.

    Posture must be ``approver`` and the approver must be distinct from the
    executing actor (AO-GR-14 separation of duties; the issue #43
    ``merge_verdict`` reviewer-distinct rule, applied to rollout).
    """

    approval_id: str
    flag: str
    target_stage: RolloutStage
    approver: str
    granted_at: str = ""
    posture: str = "approver"

    @classmethod
    def from_doc(cls, doc: Mapping[str, object]) -> "Approval":
        if not isinstance(doc, dict):
            raise RolloutError("approval record must be a mapping")
        approval_id = doc.get("approval_id")
        flag = doc.get("flag")
        target = doc.get("target_stage")
        approver = doc.get("approver")
        if not all(isinstance(v, str) and v for v in (approval_id, flag, target, approver)):
            raise RolloutError("approval requires approval_id, flag, target_stage and approver")
        posture = doc.get("posture", "approver")
        if posture != "approver":
            raise RolloutError(f"approval posture must be 'approver', got {posture!r}")
        return cls(
            approval_id=approval_id,
            flag=flag,
            target_stage=RolloutStage.coerce(target),
            approver=approver,
            granted_at=str(doc.get("granted_at", "")),
            posture=posture,
        )


class ApprovalLedger:
    """In-memory approval ledger loaded from an approvals directory.

    Each file is one approval record (or a list of records). Records are
    declarative approval-as-code; a promotion only proceeds when a matching,
    valid approval exists.
    """

    def __init__(self, approvals: Optional[Mapping[str, Approval]] = None) -> None:
        self._by_id: Dict[str, Approval] = dict(approvals or {})

    @classmethod
    def load_dir(cls, directory: str) -> "ApprovalLedger":
        ledger = cls()
        if not os.path.isdir(directory):
            return ledger
        for filename in sorted(os.listdir(directory)):
            if not filename.endswith((".yaml", ".yml")):
                continue
            path = os.path.join(directory, filename)
            with open(path, encoding="utf-8") as fh:
                doc = yaml.safe_load(fh)
            records = doc if isinstance(doc, list) else [doc]
            for record in records:
                if not isinstance(record, dict):
                    continue
                approval = Approval.from_doc(record)
                ledger._by_id[approval.approval_id] = approval
        return ledger

    def grant(self, approval: Approval) -> Approval:
        """Record an approval in the ledger (idempotent per approval id)."""
        self._by_id[approval.approval_id] = approval
        return approval

    def require(self, flag: str, target: RolloutStage, actor: str, approval_id: str) -> Approval:
        """Return the approval permitting this promotion or raise ``RolloutError``."""
        approval = self._by_id.get(approval_id)
        if approval is None:
            raise RolloutError(f"no approval record '{approval_id}' for {flag} -> {target.value}")
        if approval.flag != flag:
            raise RolloutError(
                f"approval '{approval_id}' is for {approval.flag}, not {flag}"
            )
        if approval.target_stage is not target:
            raise RolloutError(
                f"approval '{approval_id}' targets {approval.target_stage.value}, "
                f"not {target.value}"
            )
        if approval.posture != "approver":
            raise RolloutError(f"approval '{approval_id}' was not granted by an approver")
        if approval.approver == actor:
            raise RolloutError("approver must be distinct from the executing actor")
        return approval


# --------------------------------------------------------------------------- #
# Promotion audit log (append-only, hash-chained JSONL)
# --------------------------------------------------------------------------- #


class AuditLog:
    """Append-only promotion audit log with a sha-256 hash chain.

    Every record chains to the previous one: ``hash = sha256(canonical JSON of
    every field except hash)`` and ``prev_hash`` of record *n* equals ``hash``
    of record *n-1* (the first record's ``prev_hash`` is the fixed genesis
    hash). Any edit, deletion or reordering of a past record breaks ``verify``.
    """

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path
        self._records: List[Dict[str, object]] = []
        if path and os.path.isfile(path):
            self._load(path)

    @staticmethod
    def _canonical(record: Dict[str, object]) -> str:
        body = {k: v for k, v in record.items() if k != "hash"}
        return json.dumps(body, sort_keys=True, separators=(",", ":"))

    def _hash(self, record: Dict[str, object]) -> str:
        return hashlib.sha256(self._canonical(record).encode("utf-8")).hexdigest()

    def _load(self, path: str) -> None:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                self._records.append(json.loads(line))

    def append(self, **fields: object) -> Dict[str, object]:
        """Append a record (without ``hash``) and return the stored record."""
        prev_hash = self._records[-1]["hash"] if self._records else GENESIS_HASH
        record: Dict[str, object] = {
            "seq": len(self._records) + 1,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "prev_hash": prev_hash,
            **fields,
        }
        record["hash"] = self._hash(record)
        self._records.append(record)
        if self.path:
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, sort_keys=True) + "\n")
        return record

    def records(self) -> List[Dict[str, object]]:
        return list(self._records)

    def verify(self) -> bool:
        """Recompute the chain; False on tamper, reorder or truncation."""
        prev = GENESIS_HASH
        for record in self._records:
            if record.get("prev_hash") != prev:
                return False
            if record.get("hash") != self._hash(record):
                return False
            prev = record["hash"]
        return True


# --------------------------------------------------------------------------- #
# Rollout engine
# --------------------------------------------------------------------------- #


@dataclass
class RolloutEngine:
    """Drives flag promotions and rollbacks against an in-memory state."""

    model: StageModel
    flags: Dict[str, FlagState]
    approvals: ApprovalLedger = field(default_factory=ApprovalLedger)
    audit: AuditLog = field(default_factory=AuditLog)
    default_policy: str = "off"

    @classmethod
    def load(
        cls,
        stage_model_path: str,
        rollout_state_path: str,
        approvals_dir: Optional[str] = None,
        audit_path: Optional[str] = None,
    ) -> "RolloutEngine":
        """Build an engine from the committed declarative YAML."""
        with open(stage_model_path, encoding="utf-8") as fh:
            model = StageModel.load(yaml.safe_load(fh))
        with open(rollout_state_path, encoding="utf-8") as fh:
            state_doc = yaml.safe_load(fh)
        errors = validate_rollout_state_doc(state_doc)
        if errors:
            raise RolloutError("; ".join(errors))
        flags: Dict[str, FlagState] = {}
        for name, raw in state_doc["flags"].items():
            flags[str(name)] = FlagState.from_doc(str(name), raw)
        approvals = ApprovalLedger.load_dir(approvals_dir) if approvals_dir else ApprovalLedger()
        return cls(
            model=model,
            flags=flags,
            approvals=approvals,
            audit=AuditLog(audit_path),
            default_policy=str(state_doc.get("default_policy", "off")),
        )

    # -- state helpers ----------------------------------------------------- #

    def flag(self, name: str) -> FlagState:
        if name not in self.flags:
            raise RolloutError(f"unknown flag '{name}' (not declared in rollout state)")
        return self.flags[name]

    def snapshot_doc(self) -> Dict[str, object]:
        return {
            "schema_version": 1,
            "default_policy": self.default_policy,
            "flags": {name: state.to_doc() for name, state in sorted(self.flags.items())},
        }

    def write_state(self, path: str) -> None:
        """Atomically persist the current state (never to the repo by the CLI)."""
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            yaml.safe_dump(self.snapshot_doc(), fh, sort_keys=False, default_flow_style=False)
        os.replace(tmp, path)

    # -- transitions ------------------------------------------------------- #

    def promote(
        self,
        name: str,
        target_stage: object,
        *,
        verify_green: bool = True,
        approval_id: Optional[str] = None,
        actor: str = "rollout-pipeline",
        canary_health_ok: Optional[bool] = None,
        gradual_complete: bool = False,
    ) -> FlagState:
        """Promote ``name`` one gated step toward ``target_stage``.

        The gate (green verify evidence + approval-as-code + per-target health
        signals) is enforced here; a violation raises ``RolloutError``. The
        transition is audit-logged.
        """
        flag = self.flag(name)
        target = RolloutStage.coerce(target_stage)
        signals = PromotionSignals(
            verify_green=verify_green,
            approval_id=approval_id,
            canary_health_ok=canary_health_ok,
            gradual_complete=gradual_complete,
        )
        verdict: PromotionVerdict = check_promotion(self.model, flag, target, signals)
        if verdict.blocked:
            raise RolloutError(
                f"promotion {flag.stage.value} -> {target.value} for '{name}' blocked: "
                + "; ".join(verdict.reasons)
            )
        if approval_id:
            self.approvals.require(name, target, actor=actor, approval_id=approval_id)

        from_stage = flag.stage
        self._apply_stage(flag, target)
        self.audit.append(
            action="promote",
            flag=name,
            actor=actor,
            from_stage=from_stage.value,
            to_stage=target.value,
            rollout_pct=flag.rollout_pct,
            approval_id=approval_id or "",
            verify_green=bool(verify_green),
            reason=DEFAULT_REASON,
        )
        return flag

    def _apply_stage(self, flag: FlagState, target: RolloutStage) -> None:
        """Set a flag to a target stage with the stage model's default pct."""
        if target == RolloutStage.OFF:
            flag.stage = RolloutStage.OFF
            flag.rollout_pct = 0
            return
        spec = self.model.spec(target)
        flag.stage = target
        if target == RolloutStage.GRADUAL and spec.ramp_steps:
            flag.rollout_pct = spec.ramp_steps[0]
        else:
            flag.rollout_pct = spec.rollout_pct

    def ramp(self, name: str, pct: int, *, verify_green: bool = True) -> FlagState:
        """Advance a GRADUAL flag to the next declared ramp percentage.

        Every ramp step is a gated, audited promotion within the gradual stage.
        """
        flag = self.flag(name)
        if flag.stage is not RolloutStage.GRADUAL:
            raise RolloutError(f"flag '{name}' is at {flag.stage.value}; ramp requires gradual")
        steps = self.model.spec(RolloutStage.GRADUAL).ramp_steps
        if not steps:
            raise RolloutError("gradual stage declares no ramp steps")
        if pct not in steps or pct <= flag.rollout_pct:
            raise RolloutError(
                f"ramp for '{name}' must move to a higher declared step {list(steps)}, "
                f"got {pct} (current {flag.rollout_pct})"
            )
        if not verify_green:
            raise RolloutError("ramp requires green verification evidence")
        if pct == steps[-1]:
            self.audit.append(
                action="ramp-complete",
                flag=name,
                from_pct=flag.rollout_pct,
                to_pct=pct,
                reason="gradual ramp complete",
            )
        else:
            self.audit.append(
                action="ramp",
                flag=name,
                from_pct=flag.rollout_pct,
                to_pct=pct,
                reason="gradual ramp step",
            )
        flag.rollout_pct = pct
        return flag

    def observe_health(self, name: str, health_ok: bool, *, actor: str = "health-monitor") -> FlagState:
        """Record a canary/gradual health observation and act on it.

        On a failed health check the flag auto-rolls to OFF (audit-logged with
        reason ``canary_health_failure``); on a healthy signal no state change
        occurs. A flag that is already OFF stays OFF.
        """
        flag = self.flag(name)
        target = rollback_decision(self.model, flag, health_ok)
        if target is None:
            return flag
        from_stage = flag.stage
        self._apply_stage(flag, target)
        self.audit.append(
            action="rollback",
            flag=name,
            actor=actor,
            from_stage=from_stage.value,
            to_stage=RolloutStage.OFF.value,
            rollout_pct=0,
            reason="canary_health_failure",
        )
        return flag

    def manual_rollback(self, name: str, *, actor: str, reason: str = "manual_rollback") -> FlagState:
        """Manually revert a flag to OFF (still audit-logged and gated)."""
        flag = self.flag(name)
        if flag.stage is RolloutStage.OFF:
            raise RolloutError(f"flag '{name}' is already off")
        from_stage = flag.stage
        self._apply_stage(flag, RolloutStage.OFF)
        self.audit.append(
            action="rollback",
            flag=name,
            actor=actor,
            from_stage=from_stage.value,
            to_stage=RolloutStage.OFF.value,
            rollout_pct=0,
            reason=reason,
        )
        return flag
