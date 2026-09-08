"""Model gateway proxy — route / dispatch / log funnel (issue #16).

This module owns the *dispatch core*: the decoupled route -> dispatch -> log
funnel that accepts a task for an agent, resolves profile/persona ->
promptModule -> provider/model, enforces boundaries (capability, context/token
budgets, rate), calls, validates typed output, and returns a typed result —
with a full gateway call record emitted to the audit + metering sinks on every
dispatch.

Everything downstream is an injected seam (see ``resolver.py`` /
``backend.py`` / ``sinks.py``):

- agent resolver  (issue #9/#11 profiles/personas, consumed)
- task resolver  (issue #13 prompt modules, consumed)
- chooser        (issue #17 FinOps model chooser, consumed)
- model backend  (issue #15 provider registry, consumed)
- limits engine  (issue #19 cost/capacity facade, consumed)
- health signal  (issue #18, consumed as an injected signal)
- audit/metering sinks (phase-5 telemetry seam)

The core never calls a provider or opens a socket itself, so it is
deterministic and offline-testable.

Contract-level guarantees (never-silent-pass / no-false-green):

- a capability boundary miss is an explicit ``denied`` result;
- an exhausted budget or rate limit is an explicit ``blocked`` /
  ``rate_limited`` result (backpressure), never a silent success;
- schema-invalid typed output is retried once, then ``cannot_assess``;
- an all-unhealthy route is an explicit ``no_healthy_route`` failure;
- every dispatch emits one ``GatewayCallRecord`` to the audit and metering
  sinks, whatever the outcome.
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable, Iterator

from limits import model as limits_model

from proxy import contract
from proxy.backend import (
    BackendError,
    BackendOutputInvalidError,
    BackendResult,
    BackendUnavailableError,
    ModelBackend,
)
from proxy.contract import (
    AgentResolutionError,
    BudgetBlockedError,
    CapabilityDeniedError,
    NoHealthyRouteError,
    RoutingConfigError,
    TaskResolutionError,
    UnknownTaskRouteError,
)
from proxy.model import (
    STAGE_AGENT_RESOLVED,
    STAGE_ATTEMPT,
    STAGE_COMPLETED,
    STAGE_GUARD,
    STAGE_RECEIVED,
    STAGE_ROUTE_SELECTED,
    STAGE_TASK_RESOLVED,
    AgentView,
    ChatInvocation,
    DispatchEvent,
    GatewayCallRecord,
    RouteDecision,
    TaskRequest,
    TaskResult,
    TaskView,
    now_utc_iso,
)
from proxy.resolver import (
    AgentResolver,
    Chooser,
    HealthSignal,
    LimitsEngineLike,
    TaskResolver,
)
from proxy.router import Router
from proxy.schema import validate_typed_output
from proxy.sinks import CallRecordSink, NoopCallRecordSink

#: How many output-producing attempts a dispatch makes before it gives up on
#: schema-invalid output: one initial attempt + ONE retry, then CANNOT-ASSESS.
MAX_OUTPUT_TRIES = 2


class ModelGateway:
    """The gateway proxy dispatch core (issue #16)."""

    def __init__(
        self,
        *,
        agent_resolver: AgentResolver,
        task_resolver: TaskResolver,
        chooser: Chooser,
        backend: ModelBackend,
        limits: LimitsEngineLike,
        router: Router | None = None,
        health: HealthSignal = None,
        validator: Callable[..., tuple[bool, Any, str | None]] | None = None,
        audit_sink: CallRecordSink | None = None,
        metering_sink: CallRecordSink | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.agent_resolver = agent_resolver
        self.task_resolver = task_resolver
        self.chooser = chooser
        self.backend = backend
        self.limits = limits
        self.router = router or Router()
        self.health = health
        self._validator = validator or validate_typed_output
        # NB: never `sink or Default` — an empty ListCallRecordSink is falsy
        # (it defines __len__); use explicit None checks so records are kept.
        self.audit_sink = audit_sink if audit_sink is not None else NoopCallRecordSink()
        self.metering_sink = (
            metering_sink if metering_sink is not None else NoopCallRecordSink()
        )
        self._clock = clock or time.monotonic

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def dispatch(self, agent_id: str, task_request: TaskRequest) -> TaskResult:
        """Route, dispatch and log one task for an agent (typed result).

        ``agent_id`` is the REST path parameter (``/v1/agents/{agentId}/tasks``);
        the tenant and task payload live on ``task_request``.  Returns a
        ``TaskResult`` whose ``outcome`` is one of the closed set and whose
        ``record`` was emitted to the audit + metering sinks.
        """
        events: list[DispatchEvent] = []
        return self._dispatch(agent_id, task_request, events=events)

    def dispatch_stream(
        self, agent_id: str, task_request: TaskRequest
    ) -> Iterator[DispatchEvent | TaskResult]:
        """Streaming interface: incremental dispatch events, then the result.

        Yields each pipeline ``DispatchEvent`` (received -> agent/task resolved
        -> route selected -> guard -> attempts -> completed) then the terminal
        ``TaskResult``.  A thin HTTP handler (phase-7 REST surface) relays these
        events as SSE-style chunks; the synchronous core emits the whole
        incremental trace, so this iterator is the streaming seam (incremental
        result support in the dispatch interface).
        """
        events: list[DispatchEvent] = []
        result = self._dispatch(agent_id, task_request, events=events)
        for event in events:
            yield event
        yield result

    # ------------------------------------------------------------------ #
    # Dispatch funnel
    # ------------------------------------------------------------------ #
    def _dispatch(
        self,
        agent_id: str,
        request: TaskRequest,
        *,
        events: list[DispatchEvent],
    ) -> TaskResult:
        rid = request.request_id

        def ev(stage: str, **data: Any) -> None:
            events.append(DispatchEvent(request_id=rid, stage=stage, data=data))

        ev(STAGE_RECEIVED, agentId=agent_id, tenantId=request.tenant_id,
           taskType=request.task_type, stream=request.stream)

        # --- 1. resolve the agent (profile/persona boundary) --------------- #
        try:
            agent: AgentView = self.agent_resolver.resolve(
                request.tenant_id, agent_id
            )
        except AgentResolutionError as exc:
            return self._finish(events, agent_id, request,
                                outcome=contract.OUTCOME_FAILED, error=str(exc))
        ev(STAGE_AGENT_RESOLVED, profile=agent.profile_id, persona=agent.persona_id,
           capabilities=sorted(agent.capability_set))

        # --- 2. resolve the task (prompt module, issue #13) ---------------- #
        try:
            task: TaskView = self.task_resolver.resolve(
                request.task_type, variables=dict(request.input)
            )
        except TaskResolutionError as exc:
            return self._finish(events, agent_id, request,
                                outcome=contract.OUTCOME_FAILED, error=str(exc))
        ev(STAGE_TASK_RESOLVED, prompt=task.prompt_id, hint=task.model_tier_hint)

        # --- 3. route: task-type -> capability -> model tier + chain ------- #
        try:
            decision: RouteDecision = self.router.route(
                task, agent, request, chooser=self.chooser, health=self.health
            )
        except CapabilityDeniedError as exc:
            return self._finish(events, agent_id, request,
                                outcome=contract.OUTCOME_DENIED, error=str(exc))
        except NoHealthyRouteError as exc:
            return self._finish(events, agent_id, request,
                                outcome=contract.OUTCOME_NO_HEALTHY_ROUTE,
                                error=str(exc))
        except BudgetBlockedError as exc:
            return self._finish(events, agent_id, request,
                                outcome=contract.OUTCOME_BLOCKED, error=str(exc))
        except (RoutingConfigError, UnknownTaskRouteError) as exc:
            return self._finish(events, agent_id, request,
                                outcome=contract.OUTCOME_FAILED, error=str(exc))
        ev(STAGE_ROUTE_SELECTED, capability=decision.capability,
           taskClass=decision.task_class, ladderTier=decision.ladder_tier,
           tier=decision.registry_tier,
           candidates=list(decision.candidate_providers()))

        # --- 4. cost/capacity guard (limits facade, issue #19) ------------- #
        limits_request = limits_model.ModelCallRequest(
            tenant=request.tenant_id,
            agent=agent_id,
            model_tier=decision.registry_tier,
            task_type=task.task_type,
            prompt=task.user_prompt,
            request_id=rid,
        )
        guard = self.limits.guard(limits_request)
        ev(STAGE_GUARD, kind=getattr(guard, "kind", "unknown"),
           served=bool(guard.served()))

        if guard.kind == "cache_hit":
            # Zero-cost cache hit: the cached response was schema-validated when
            # it was first stored (only valid outputs reach complete()).
            cached = guard.response if guard.response is not None else ""
            ok, content, verr = self._validator(task.output_schema, cached)
            return self._finish(
                events, agent_id, request,
                outcome=contract.OUTCOME_CACHE_HIT,
                content=content if ok else None,
                tier=decision.registry_tier,
                capability=decision.capability,
                task_class=decision.task_class,
                ladder_tier=decision.ladder_tier,
                cost=decision.estimated_cost_usd,
                budget_action=decision.budget_action,
                error=None if ok else verr,
            )
        if not guard.served():
            kind = getattr(guard, "kind", "")
            metering = getattr(guard, "metering", None)
            reason = getattr(metering, "reason", None) or kind
            backpressure = getattr(guard, "backpressure", None)
            detail = f"limits guard {kind}: {reason}"
            if backpressure is not None and getattr(backpressure, "queued", False):
                detail += " (backpressure: queued)"
            outcome = (
                contract.OUTCOME_BLOCKED
                if kind == "budget_exceeded"
                else contract.OUTCOME_RATE_LIMITED
            )
            return self._finish(
                events, agent_id, request,
                outcome=outcome, error=detail,
                tier=decision.registry_tier,
                capability=decision.capability,
                task_class=decision.task_class,
                ladder_tier=decision.ladder_tier,
                cost=decision.estimated_cost_usd,
                budget_action=decision.budget_action,
            )

        # --- 5. execute over the healthy chain (typed-output fail closed) --- #
        invocation = ChatInvocation(
            task_type=task.task_type,
            tenant_id=request.tenant_id,
            agent_id=agent_id,
            messages=task.messages,
            schema=task.output_schema,
            parameters=dict(task.parameters),
            request_id=rid,
        )
        terminal = self._run_candidates(
            agent_id, request, task, decision, invocation, events
        )
        return self._finish(
            events, agent_id, request,
            capability=decision.capability,
            task_class=decision.task_class,
            ladder_tier=decision.ladder_tier,
            tier=decision.registry_tier,
            cost=decision.estimated_cost_usd,
            budget_action=decision.budget_action,
            **terminal,
        )

    def _run_candidates(
        self,
        agent_id: str,
        request: TaskRequest,
        task: TaskView,
        decision: RouteDecision,
        invocation: ChatInvocation,
        events: list[DispatchEvent],
    ) -> dict[str, Any]:
        """Walk the healthy candidate chain; return a terminal payload.

        Semantics (documented in ``README.md``):

        - an unavailable/failed candidate is skipped (availability fallback);
        - schema-invalid output is a retryable attempt: it is retried ONCE
          (against the next healthy candidate, or the same candidate when it is
          the last healthy hop), and a second invalid output is an explicit
          CANNOT-ASSESS — never a silent pass;
        - an all-unavailable chain is FAILED (an empty unhealthy chain was
          already refused by the router as NO_HEALTHY_ROUTE).
        """
        rid = request.request_id

        def ev(stage: str, **data: Any) -> None:
            events.append(DispatchEvent(request_id=rid, stage=stage, data=data))

        plan = list(decision.candidates)
        i = 0
        output_tries = 0
        invalid_seen = 0
        provider_failures = 0
        last_error: str | None = None

        while i < len(plan) and output_tries < MAX_OUTPUT_TRIES:
            candidate = plan[i]
            ev(STAGE_ATTEMPT, provider=candidate.provider, role=candidate.role,
               attempt=output_tries + 1)
            try:
                result = self.backend.execute(candidate, invocation)
            except BackendOutputInvalidError as exc:
                output_tries += 1
                invalid_seen += 1
                last_error = str(exc)
                ev(STAGE_ATTEMPT, provider=candidate.provider,
                   outcome="output_invalid", error=str(exc))
                if i + 1 < len(plan):
                    i += 1  # the retry targets the next healthy candidate
                continue  # same (last) candidate is retried when budget remains
            except BackendUnavailableError as exc:
                provider_failures += 1
                last_error = str(exc)
                ev(STAGE_ATTEMPT, provider=candidate.provider,
                   outcome="unavailable", error=str(exc))
                i += 1
                continue
            except BackendError as exc:
                provider_failures += 1
                last_error = str(exc)
                ev(STAGE_ATTEMPT, provider=candidate.provider, outcome="failed",
                   error=str(exc))
                i += 1
                continue

            # Raw output received: validate the TYPED response at the gateway.
            output_tries += 1
            raw = result.raw_text
            if raw is None:
                raw = json.dumps(result.content) if result.content is not None else ""
            ok, content, verr = self._validator(task.output_schema, raw)
            if ok:
                refused = self._complete_limits(
                    agent_id, request, task, decision, result, raw
                )
                if refused:
                    return refused  # output throttle refused the output
                return {
                    "outcome": contract.OUTCOME_SUCCESS,
                    "content": content,
                    "provider": result.provider,
                    "model": result.model,
                    "input_tokens": result.input_tokens,
                    "output_tokens": result.output_tokens,
                    "latency_ms": result.latency_ms,
                    "attempts": output_tries,
                    "error": None,
                }
            invalid_seen += 1
            last_error = verr
            ev(STAGE_ATTEMPT, provider=candidate.provider,
               outcome="output_invalid", error=verr)
            if i + 1 < len(plan):
                i += 1  # the single retry targets the next healthy candidate
            # else: retry the same (last) candidate once when budget remains

        if invalid_seen:
            return {
                "outcome": contract.OUTCOME_CANNOT_ASSESS,
                "error": f"typed output invalid after {output_tries} attempt(s): "
                         f"{last_error}",
                "attempts": output_tries,
            }
        if provider_failures:
            return {
                "outcome": contract.OUTCOME_FAILED,
                "error": f"all {len(plan)} candidate(s) failed at execution time: "
                         f"{last_error}",
                "attempts": output_tries,
            }
        return {
            "outcome": contract.OUTCOME_NO_HEALTHY_ROUTE,
            "error": "no healthy candidate could be executed",
            "attempts": output_tries,
        }

    def _complete_limits(
        self,
        agent_id: str,
        request: TaskRequest,
        task: TaskView,
        decision: RouteDecision,
        result: BackendResult,
        raw: str,
    ) -> dict[str, Any] | None:
        """Finish the allowed call through the limits facade (throttle+meter).

        Returns a terminal payload when the output throttle REFUSED the output
        (outcome=refused); None when the call completed normally.  Real usage
        and the cacheable response are recorded by the limits engine.
        """
        limits_request = limits_model.ModelCallRequest(
            tenant=request.tenant_id,
            agent=agent_id,
            model_tier=decision.registry_tier,
            task_type=task.task_type,
            prompt=task.user_prompt,
            request_id=request.request_id,
        )
        completed = self.limits.complete(
            limits_request,
            raw,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
        )
        if getattr(completed, "refused", False):
            reason = getattr(getattr(completed, "metering", None), "reason", None)
            return {
                "outcome": contract.OUTCOME_REFUSED,
                "error": f"output throttle refused the response: {reason}",
                "provider": result.provider,
                "model": result.model,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "latency_ms": result.latency_ms,
            }
        return None

    # ------------------------------------------------------------------ #
    # Record emission + result construction
    # ------------------------------------------------------------------ #
    def _finish(
        self,
        events: list[DispatchEvent],
        agent_id: str,
        request: TaskRequest,
        *,
        outcome: str,
        content: Any = None,
        provider: str | None = None,
        model: str | None = None,
        tier: str | None = None,
        ladder_tier: str | None = None,
        capability: str | None = None,
        task_class: str | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        latency_ms: float = 0.0,
        cost: float = 0.0,
        budget_action: str = "",
        attempts: int = 0,
        error: str | None = None,
    ) -> TaskResult:
        record = GatewayCallRecord(
            request_id=request.request_id,
            ts=now_utc_iso(),
            tenant_id=request.tenant_id,
            agent_id=agent_id,
            task_type=request.task_type,
            outcome=outcome,
            capability=capability,
            task_class=task_class,
            tier=tier,
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            estimated_cost_usd=cost,
            budget_action=budget_action,
            attempts=attempts,
            error=error,
        )
        # Fail closed: an audit/metering record is never silently lost.
        self.audit_sink.record(record)
        self.metering_sink.record(record)
        events.append(
            DispatchEvent(
                request_id=request.request_id,
                stage=STAGE_COMPLETED,
                data={
                    "outcome": outcome,
                    "provider": provider,
                    "model": model,
                    "tier": tier,
                    "ladderTier": ladder_tier,
                    "error": error,
                },
            )
        )
        return TaskResult(
            request_id=request.request_id,
            tenant_id=request.tenant_id,
            agent_id=agent_id,
            task_type=request.task_type,
            outcome=outcome,
            content=content,
            provider=provider,
            model=model,
            tier=tier,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            error=error,
            record=record,
            events=tuple(events),
        )
