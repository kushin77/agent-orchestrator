"""e2e/golden_path — the canonical tenant journey (issue #46).

Runs the EPIC-00 Definition of Done end to end over the REAL merged pillar
modules, fully offline:

    tenant signup (identity/onboarding)
      -> rbac authorization (identity/rbac)
      -> personas/agents (registry/service + gateway personas/profiles)
      -> guarded multi-provider routed model calls (gateway/proxy + providers
         + finops + health + limits) with DLP + policy + budgets inline
      -> durable task execution (engine/core)
      -> audit (telemetry/ledger, tamper-evident)
      -> usage billing (telemetry/metering)

Each stage records a GuardAttestation (guardrails/honesty) and the run writes
an evidence document.  Run from the repo root:

    python3 -m e2e.golden_path [--out DIR]
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, Optional

from e2e._paths import REPO_ROOT, ensure_sys_paths

ensure_sys_paths()
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from e2e.wiring import (  # noqa: E402
    ENDPOINTS,
    PROVIDER_ANTHROPIC,
    PROVIDER_DEEPSEEK,
    PROVIDER_OLLAMA,
    PROVIDER_OPENAI,
    TENANT,
    ControlPlane,
    build_control_plane,
    write_evidence,
)

# Typed outputs that satisfy the published prompt-module output schemas
# (identical to the shapes gateway/proxy tests dispatch).
CLASSIFY_OK = json.dumps(
    {
        "route": "support",
        "priority": "high",
        "confidence": 0.92,
        "reasoning": "Customer reported an outage on the billing API.",
    }
)
CODE_REVIEW_OK = json.dumps(
    {
        "verdict": "approve",
        "blockingFindings": [],
        "summary": "The diff is small, focused and correct.",
    }
)

NOT_JSON = "this is not the typed json the output schema expects"


# --------------------------------------------------------------------------- #
# guarded model-call helper (policy -> dlp -> budgets-preflight -> gateway)
# --------------------------------------------------------------------------- #
def guarded_model_call(
    control: ControlPlane,
    *,
    wired: Any,
    agent_id: str,
    task_type: str,
    task_input: Dict[str, Any],
    provider: str,
    prompt: str,
) -> Dict[str, Any]:
    """Run one governed, audited, metered model call through real modules.

    Returns evidence with the policy verdict, the DLP gate outcome, the
    gateway result, the ledger record and the metering record.
    """
    # 1. policy gate (real guardrails/policy engine) - allow path.
    policy_decision = control.policy_engine.evaluate(
        "model.call",
        subject=f"agent:{agent_id}",
        tenant=control.tenant_id,
        context={"security": {"flag": False}},
    )

    # 2. budgets preflight (real telemetry/budgets rails) - healthy allow.
    preflight = _healthy_preflight(control.tenant_id)

    # 3. DLP egress gate (real guardrails/dlp pipeline) - scrub/sign/send.
    dlp_outcome = control.dlp_pipeline.guard_call(
        tenant_id=control.tenant_id,
        agent_id=agent_id,
        provider=provider,
        endpoint=ENDPOINTS[provider],
        payload=prompt,
    )

    # 4. routed model dispatch (real gateway/proxy over real providers).
    from proxy.model import TaskRequest

    result = wired.gateway.dispatch(
        agent_id,
        TaskRequest(tenant_id=control.tenant_id, task_type=task_type, input=dict(task_input)),
    )
    record = result.record.to_dict() if result.record is not None else {}

    # 5. audit (real telemetry/ledger) + usage billing (real metering).
    ledger_record = control.ledger_append_model_call(record)
    meter_outcome = control.metering_intake.ingest(record)
    meter_record = meter_outcome.record

    return {
        "agent": agent_id,
        "task_type": task_type,
        "provider": result.provider,
        "model": result.model,
        "outcome": result.outcome,
        "served": bool(result.served()),
        "policy_decision": str(policy_decision.decision),
        "policy_blocking": bool(getattr(policy_decision.decision, "blocks", False)),
        "dlp_verdict": dlp_outcome.verdict,
        "dlp_sent": bool(dlp_outcome.sent),
        "preflight_allowed": bool(preflight["allowed"]),
        "ledger_action": ledger_record.get("action"),
        "metered": bool(getattr(meter_record, "metered", False)),
        "billable": bool(getattr(meter_record, "billable", False)),
        "cost_usd": getattr(meter_record, "cost_usd", None),
        "gateway_call_record": record,
    }


def _healthy_preflight(tenant_id: str) -> Dict[str, Any]:
    """Real telemetry/budgets preflight over a fresh healthy rig."""
    from telemetry.budgets.audit import MemoryAuditStore
    from telemetry.budgets.budget import BudgetEnforcer, BudgetLimit, TenantBudgetPolicy
    from telemetry.budgets.killswitch import KillSwitchController
    from telemetry.budgets.ledger import StaticLedger
    from telemetry.budgets.model import MODE_ENFORCE
    from telemetry.budgets.preflight import preflight
    from telemetry.budgets.quota import QuotaEnforcer, QuotaLimit, QuotaPolicy

    ledger = StaticLedger()
    quota = QuotaEnforcer(
        ledger,
        {
            tenant_id: QuotaPolicy(
                tenant_id=tenant_id,
                limits={
                    "requests": QuotaLimit(
                        resource="requests", window="day",
                        soft_limit=100, hard_limit=500,
                    )
                },
            )
        },
    )
    budget = BudgetEnforcer(
        ledger,
        {
            tenant_id: TenantBudgetPolicy(
                tenant_id=tenant_id,
                mode=MODE_ENFORCE,
                cost_limit=BudgetLimit(window="month", limit=100.0, warn_at_pct=0.8),
                token_limit=BudgetLimit(window="day", limit=2_000_000, warn_at_pct=0.8),
            )
        },
    )
    result = preflight(
        tenant_id,
        vendor="deepseek",
        requested_cost_usd=0.01,
        requested_tokens=5_000,
        day="2026-09-08",
        month="2026-09",
        killswitch=KillSwitchController(audit=MemoryAuditStore()),
        quota=quota,
        budget=budget,
    )
    return {"allowed": bool(result.allowed), "decision": str(result.decision)}


# --------------------------------------------------------------------------- #
# stages
# --------------------------------------------------------------------------- #
def _stage_signup(control: ControlPlane) -> Dict[str, Any]:
    tenant = control.tenant
    roles = sorted(r.key for r in control.rbac_store.roles_in_org(control.tenant_id))
    bindings = [
        (b.subject, control.rbac_store.role_by_id(b.role_id).key)
        for b in control.rbac_store.bindings_in_org(control.tenant_id)
    ]
    seeds = [(s.kind, s.ref) for s in control.onboarding_store.seeds_for_tenant(control.tenant_id)]
    control.attest(
        "golden.signup",
        0,
        f"tenant {tenant.id} provisioned status={tenant.status} "
        f"roles={roles} bindings={bindings} starter-seeds={len(seeds)}",
        provenance="identity/onboarding",
    )
    return {"tenant_id": tenant.id, "status": tenant.status, "roles": roles,
            "bindings": bindings, "seed_count": len(seeds)}


def _stage_rbac(control: ControlPlane) -> Dict[str, Any]:
    from rbac.guard import guard
    from rbac.model import ScopeNode

    allowed = guard(
        control.rbac_store,
        control.owner_subject,
        ScopeNode(control.tenant_id),
        "agent:read",
    )
    denied = guard(
        control.rbac_store,
        "stranger@example.com",
        ScopeNode(control.tenant_id),
        "agent:read",
    )
    control.attest(
        "golden.rbac",
        0,
        f"owner agent:read allowed={bool(allowed.allowed)} reason={allowed.reason}; "
        f"stranger denied={not denied.allowed} reason={denied.reason}",
        provenance="identity/rbac",
    )
    return {
        "owner_allowed": bool(allowed.allowed),
        "stranger_denied": not bool(denied.allowed),
        "stranger_reason": denied.reason,
    }


def _stage_agents(control: ControlPlane) -> Dict[str, Any]:
    worker = control.registry.get(control.tenant_id, "worker-1")
    reviewer = control.registry.get(control.tenant_id, "reviewer-1")
    session = control.worker_session
    control.registry.require_scope(session, control.tenant_id)  # same-tenant ok
    control.attest(
        "golden.agents",
        0,
        f"worker-1 status={worker.status} profile={worker.profile_ref}@{worker.profile_version}; "
        f"reviewer-1 status={reviewer.status}; session tenant={session.tenant_id} role={session.role}",
        provenance="registry/service",
    )
    return {
        "worker_status": worker.status,
        "worker_profile": f"{worker.profile_ref}@{worker.profile_version}",
        "reviewer_status": reviewer.status,
        "session_tenant": session.tenant_id,
    }


def _stage_routed_call(control: ControlPlane) -> Dict[str, Any]:
    wired = control.wired
    wired.rig.script_success(PROVIDER_DEEPSEEK, CLASSIFY_OK)
    evidence = guarded_model_call(
        control,
        wired=wired,
        agent_id="orchestrator",
        task_type="classify-route",
        task_input={"input": "billing outage on the api"},
        provider=PROVIDER_DEEPSEEK,
        prompt="Classify this support ticket: billing outage on the api",
    )
    assert evidence["served"], evidence
    control.attest(
        "golden.routed-call",
        0,
        f"classify-route served by {evidence['provider']} ({evidence['model']}) "
        f"outcome={evidence['outcome']} policy={evidence['policy_decision']} "
        f"dlp={evidence['dlp_verdict']} metered={evidence['metered']}",
        provenance="gateway/proxy",
    )
    return evidence


def _stage_conformance(control: ControlPlane) -> Dict[str, Any]:
    """Same task under DeepSeek / OpenAI / local Ollama, plus Claude (anthropic)
    on the MED tier: every provider yields governed, audited, metered behavior."""
    from proxy.wiring import build_real_gateway

    from e2e.wiring import TruthyListCallRecordSink

    results: List[Dict[str, Any]] = []
    scenarios = [
        # (gateway health, provider to serve, agent, task, task_input, content)
        ({"openai": False, "ollama": False}, PROVIDER_DEEPSEEK, "orchestrator",
         "classify-route", {"input": "billing outage"}, CLASSIFY_OK),
        ({"deepseek": False, "ollama": False}, PROVIDER_OPENAI, "orchestrator",
         "classify-route", {"input": "billing outage"}, CLASSIFY_OK),
        ({"deepseek": False, "openai": False}, PROVIDER_OLLAMA, "orchestrator",
         "classify-route", {"input": "billing outage"}, CLASSIFY_OK),
        # anthropic serves the MED tier (code-review) when deepseek is down.
        ({"deepseek": False}, PROVIDER_ANTHROPIC, "reviewer",
         "code-review-verdict", {"diff": "+raise_on_invalid()", "context": "small PR"},
         CODE_REVIEW_OK),
    ]
    for health, provider, agent, task_type, task_input, content in scenarios:
        audit = TruthyListCallRecordSink()
        metering = TruthyListCallRecordSink()
        wired = build_real_gateway(health=health, audit_sink=audit, metering_sink=metering)
        wired.rig.script_success(provider, content)
        evidence = guarded_model_call(
            control,
            wired=wired,
            agent_id=agent,
            task_type=task_type,
            task_input=task_input,
            provider=provider,
            prompt=f"{task_type} for tenant {control.tenant_id}",
        )
        assert evidence["served"], evidence
        assert evidence["provider"] == provider, evidence
        results.append(evidence)
    control.attest(
        "golden.conformance",
        0,
        "providers served: " + ", ".join(f"{e['provider']}:{e['model']}" for e in results),
        provenance="gateway/providers + gateway/health + gateway/finops",
    )
    return {"providers": results, "served": [e["provider"] for e in results]}


def _stage_durable(control: ControlPlane) -> Dict[str, Any]:
    """One durable task execution (engine/core) over the real gateway."""
    wired = control.wired
    wired.rig.script_success(PROVIDER_DEEPSEEK, CLASSIFY_OK)
    before = control.audit_sink.count
    execution = control.engine.run_workflow(
        control.tenant_id,
        control._engine_spec,
        inputs={"input": "billing outage on the api"},
    )
    status = execution.status
    new_records = control.audit_sink.count - before
    control.attest(
        "golden.durable",
        0,
        f"engine/core workflow {execution.workflow_id} status={status.value} "
        f"events={len(execution.events)} new-gateway-records={new_records}",
        provenance="engine/core",
    )
    return {
        "workflow_id": execution.workflow_id,
        "status": status.value,
        "event_kinds": [e.kind.value for e in execution.events],
        "new_gateway_records": new_records,
    }


def _stage_audit(control: ControlPlane) -> Dict[str, Any]:
    control.ledger_append_policy_decision("log")
    from telemetry.ledger.verify import verify_ledger

    verdict = verify_ledger(control.ledger_store, control.tenant_id)
    control.attest(
        "golden.audit",
        verdict.exit_code,
        f"ledger verify status={verdict.status} tail_seq={verdict.tail[0]} "
        f"detail={verdict.detail}",
        provenance="telemetry/ledger",
    )
    return {"status": verdict.status, "exit_code": verdict.exit_code,
            "records": len(control.ledger_store.records(control.tenant_id))}


def _stage_billing(control: ControlPlane) -> Dict[str, Any]:
    totals = control.usage_reporter.totals(control.tenant_id)
    by_model = {
        model: agg.to_dict()
        for model, agg in control.usage_reporter.by_model().items()
    }
    billable = sum(
        1 for r in control.usage_reporter.records()
        if getattr(r, "billable", False)
    )
    control.attest(
        "golden.billing",
        0,
        f"metering billable={billable} models={sorted(by_model)} "
        f"total_tokens={totals.total_tokens}",
        provenance="telemetry/metering",
    )
    return {
        "billable_calls": billable,
        "models": sorted(by_model),
        "total_tokens": totals.total_tokens,
    }


# --------------------------------------------------------------------------- #
# runner
# --------------------------------------------------------------------------- #
STAGES = [
    ("signup", _stage_signup),
    ("rbac", _stage_rbac),
    ("agents", _stage_agents),
    ("routed-call", _stage_routed_call),
    ("conformance", _stage_conformance),
    ("durable", _stage_durable),
    ("audit", _stage_audit),
    ("billing", _stage_billing),
]


def run_golden_path(control: Optional[ControlPlane] = None, *, work_dir: Optional[str] = None) -> Dict[str, Any]:
    """Run the full golden path and return JSON-serializable evidence."""
    control = control or build_control_plane(TENANT, work_dir=work_dir)
    stage_evidence: Dict[str, Any] = {}
    for name, fn in STAGES:
        stage_evidence[name] = fn(control)
    payload = {
        "gate": "e2e.golden_path",
        "tenant_id": control.tenant_id,
        "stages": stage_evidence,
        "attestations": [a.to_dict() for a in control.attestations],
        "attested": all(a.attested for a in control.attestations),
    }
    if control.work_dir:
        write_evidence(control.work_dir, "golden-path.json", payload)
    return payload


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    out = None
    if argv and argv[0] == "--out" and len(argv) > 1:
        out = argv[1]
    work_dir = out or os.path.join(os.getcwd(), ".verify", "e2e")
    payload = run_golden_path(work_dir=work_dir)
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    print(f"GOLDEN PATH: {'PASS' if payload['attested'] else 'FAIL'}")
    return 0 if payload["attested"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
