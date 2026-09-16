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
    validate_live_state_doc,
    validate_rollout_state_doc,
)

GENESIS_HASH = "0" * 64
DEFAULT_REASON = "promotion"

#: Directory (under ``infra/rollout/``) holding one evidence record per
#: transition. ``checks/check_rollout.py::check_live_state`` resolves every
#: live-state entry's ``audit_record`` against ``infra/rollout/``, so a record
#: named here resolves by construction.
AUDIT_RECORD_SUBDIR = "audit"

#: The record body. A record is evidence, not a claim (see
#: ``infra/rollout/audit/README.md``): it carries the command that produced
#: the transition and the audit-log line (`seq` + `hash`) a reader can
#: re-verify with ``AuditLog.verify``.
AUDIT_RECORD_TEMPLATE = """# Promotion audit record - `{flag}` {from_stage} -> {to_stage}

One transition, written by the promotion path immediately BEFORE the
live-state entry that names it (`infra/rollout/cli.py promote --live-state-out`,
and the ordered driver `infra/rollout/go_live.py`). `check_rollout.py` refuses a
promoted flag whose record is missing, so this file is what makes the promotion
provable rather than claimed.

| field | value |
|---|---|
| flag | `{flag}` |
| transition | `{from_stage}` -> `{to_stage}` |
| actor | `{actor}` |
| approval | {approval} |
| verify_green | {verify_green} |
| recorded_at | {recorded_at} |
| live-state | `{live_state_rel}` |
| audit log | `{audit_log_rel}` seq {audit_seq}, hash `{audit_hash}` |

## The command (re-runnable)

```
{command}
```

## What the engine reported

```
{outcome}
```

A record is evidence, not a claim: the audit-log line above is machine-checkable
(`AuditLog.verify` recomputes the sha-256 chain) and the transition it names is
the one the live-state entry carries.
"""


def _slug(value: str) -> str:
    """A filename-safe form of a flag name (`services.registry` -> `services-registry`)."""
    return "".join(ch if ch.isalnum() else "-" for ch in value).strip("-")


def audit_record_name(
    flag: str,
    from_stage: object,
    to_stage: object,
    *,
    seq: int = 0,
    when: Optional[str] = None,
) -> str:
    """The canonical record path for one transition, relative to ``infra/rollout/``.

    ``audit/<flag>-<from>-<to>-s<seq>-<UTC stamp>.md``. The audit-log sequence
    number is part of the name, so the name is unique per appended record even
    when two transitions land in the same second.
    """
    stamp = (when or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())).replace(":", "").replace("-", "")
    frm = RolloutStage.coerce(from_stage).value
    to = RolloutStage.coerce(to_stage).value
    return f"{AUDIT_RECORD_SUBDIR}/{_slug(flag)}-{frm}-{to}-s{seq:04d}-{stamp}.md"


def write_audit_record(
    path: str,
    *,
    flag: str,
    from_stage: str,
    to_stage: str,
    actor: str,
    approval_kind: str,
    approval_id: str,
    policy: str,
    verify_green: bool,
    command: str,
    outcome: str,
    live_state_rel: str,
    audit_log_rel: str,
    audit_seq: int,
    audit_hash: str,
    recorded_at: Optional[str] = None,
) -> str:
    """Write one transition's evidence record atomically and return the path."""
    if approval_kind == "policy":
        approval = f"policy `{policy}` (auto-approved on green verification evidence)"
    elif approval_id:
        approval = f"human approval_id `{approval_id}`"
    else:
        approval = "none (recorded as policy auto-approval)"
    body = AUDIT_RECORD_TEMPLATE.format(
        flag=flag,
        from_stage=from_stage,
        to_stage=to_stage,
        actor=actor,
        approval=approval,
        verify_green=str(bool(verify_green)).lower(),
        recorded_at=recorded_at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        live_state_rel=live_state_rel,
        audit_log_rel=audit_log_rel,
        audit_seq=audit_seq,
        audit_hash=audit_hash,
        command=command,
        outcome=outcome,
    )
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(body)
    os.replace(tmp, path)
    return path


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

    def records(self) -> List[Approval]:
        """Every loaded approval, in approval-id order (deterministic).

        A caller resolving "the approval for (flag, target)" must get the same
        record on every run; ordering by approval id makes that true, and it
        means a self-granted record is never skipped in favour of a later one
        (it is validated, and refuses the promotion - AO-GR-14).
        """
        return [self._by_id[key] for key in sorted(self._by_id)]

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
            parent = os.path.dirname(self.path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, sort_keys=True) + "\n")
        return record

    def next_seq(self) -> int:
        """The sequence number the next ``append`` will store.

        Callers use it to name a transition's audit record file BEFORE the
        record is appended (the record must exist on disk before the
        live-state entry that names it is written).
        """
        return len(self._records) + 1

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
        live_state_path: Optional[str] = None,
    ) -> "RolloutEngine":
        """Build an engine from the committed declarative YAML.

        ``rollout_state_path`` (``rollout-state.yaml``) stays the
        declared-default document - every flag there must be ``off``
        (GR-28, unchanged). When ``live_state_path`` is given and the file
        exists, its validated, promoted entries are overlaid onto the
        in-memory state (``load`` overlays live-state on defaults) so the
        engine reflects the real, currently-promoted stage without ever
        requiring a non-off flag to be committed to ``rollout-state.yaml``.
        """
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
        if live_state_path and os.path.isfile(live_state_path):
            with open(live_state_path, encoding="utf-8") as fh:
                live_doc = yaml.safe_load(fh) or {}
            live_errors = validate_live_state_doc(live_doc, list(flags), model)
            if live_errors:
                raise RolloutError("; ".join(live_errors))
            for name, raw in (live_doc.get("flags") or {}).items():
                live_stage = RolloutStage.coerce(raw.get("stage"))
                spec = model.spec(live_stage)
                flags[name].stage = live_stage
                flags[name].rollout_pct = spec.rollout_pct
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

    def live_state_doc(
        self, *, audit_record: str = "", previous: Optional[Mapping[str, object]] = None
    ) -> Dict[str, object]:
        """The live-state document - one entry per flag NOT at off.

        Sourced from the audit log's most recent ``promote`` record into the
        flag's current stage (the ``from_stage`` it actually transitioned
        from, and whether it was a human or policy approval). A flag that
        has since rolled back to off is simply absent here - rollback never
        leaves a stale live-state entry behind.

        A flag the log cannot account for (its promotion was recorded in a
        different log, e.g. a rotated or separately-written one) keeps the
        entry already committed for it: that entry IS the evidence. Nothing is
        ever fabricated - if neither the log nor the committed document can
        account for a promoted stage, this raises rather than writing an
        entry with no record.
        """
        preserved = previous if isinstance(previous, Mapping) else {}
        last_promote: Dict[str, Dict[str, object]] = {}
        for record in self.audit.records():
            if record.get("action") == "promote":
                last_promote[str(record["flag"])] = record

        entries: Dict[str, object] = {}
        for name, state in sorted(self.flags.items()):
            if state.stage is RolloutStage.OFF:
                continue
            record = last_promote.get(name)
            existing = preserved.get(name)
            if record is None and isinstance(existing, Mapping):
                entries[name] = dict(existing)
                continue
            if record is None:
                raise RolloutError(
                    f"flag '{name}' is at '{state.stage.value}' but neither this audit log nor the "
                    "committed live-state can account for it; refusing to write an entry with no evidence"
                )
            # Each flag names its OWN transition's record. Without this the
            # single ``audit_record`` argument would be stamped on every entry,
            # so a reader would be shown some other flag's evidence (#619).
            record_path = str(record.get("audit_record", ""))
            entry: Dict[str, object] = {
                "stage": state.stage.value,
                "from_stage": record["from_stage"],
                "since": record["ts"],
                "audit_record": record_path or audit_record,
            }
            if record.get("approval_kind") == "policy":
                entry["policy"] = record.get("policy", "")
            else:
                entry["approval_id"] = record.get("approval_id", "") or ""
            entries[name] = entry
        return {"schema_version": 1, "flags": entries}

    def _previous_live_state(self, path: str) -> Dict[str, object]:
        """The entries already committed at ``path`` (empty when absent)."""
        if not os.path.isfile(path):
            return {}
        with open(path, encoding="utf-8") as fh:
            doc = yaml.safe_load(fh) or {}
        flags = doc.get("flags") if isinstance(doc, dict) else None
        return dict(flags) if isinstance(flags, dict) else {}

    def write_live_state(self, path: str, *, audit_record: str = "") -> None:
        """Atomically persist the live-state document (see ``live_state_doc``)."""
        doc = self.live_state_doc(
            audit_record=audit_record, previous=self._previous_live_state(path)
        )
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            yaml.safe_dump(doc, fh, sort_keys=False, default_flow_style=False)
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
        audit_record: str = "",
    ) -> FlagState:
        """Promote ``name`` one gated step toward ``target_stage``.

        The gate (green verify evidence + approval-as-code + per-target health
        signals) is enforced here; a violation raises ``RolloutError``. The
        transition is audit-logged, and ``audit_record`` (the on-disk evidence
        file for THIS transition, relative to ``infra/rollout/``) is recorded
        on the audit entry so ``live_state_doc`` can attribute each live-state
        entry to the transition that produced it.
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
        policy = self.model.approval_policy
        auto_approved = policy is not None and policy.auto_approves(target) and not approval_id
        if approval_id:
            self.approvals.require(name, target, actor=actor, approval_id=approval_id)

        from_stage = flag.stage
        self._apply_stage(flag, target)
        if auto_approved:
            # Record the policy auto-approval explicitly (who/when/why/which
            # policy) - never a silent approval.
            self.audit.append(
                action="promote",
                flag=name,
                actor=actor,
                from_stage=from_stage.value,
                to_stage=target.value,
                rollout_pct=flag.rollout_pct,
                approval_id="",
                approval_kind="policy",
                policy=policy.policy,
                verify_green=bool(verify_green),
                audit_record=audit_record,
                reason=f"auto-approved by policy '{policy.policy}'",
            )
        else:
            self.audit.append(
                action="promote",
                flag=name,
                actor=actor,
                from_stage=from_stage.value,
                to_stage=target.value,
                rollout_pct=flag.rollout_pct,
                approval_id=approval_id or "",
                approval_kind="human" if approval_id else "",
                policy="",
                verify_green=bool(verify_green),
                audit_record=audit_record,
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
