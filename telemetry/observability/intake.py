"""telemetry/observability — intake hook contract (issue #32, phase 5).

This module is the **emit contract** the other pillars call to feed the
observability store: the gateway proxy (issue #16) already writes its
JSONL model-call-audit records; guardrails (issue #26-30) and the engine /
agent-loop (issue #21-25) emit span-level outcome records.  Everything flows
through a :class:`TelemetrySink`; correlation ids are propagated end-to-end
with :mod:`contextvars` so one request spans gateway -> guardrails -> engine
with a single ``trace_id`` on every line.

Public surface
--------------

- ``TelemetrySink`` — the write contract (``write_span`` / ``write_trace`` /
  ``flush`` / ``close``).  Other pillars depend on this interface, never on
  a concrete store.
- ``MemorySink`` / ``NoopSink`` — in-process and disabled sinks (offline).
- ``TelemetryRecorder`` — the composition root other pillars call:
  ``trace()``/``span()`` context managers (OpenTelemetry-style nesting),
  ``ingest_gateway_call()`` to absorb a gateway call record.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Mapping, Optional

from telemetry.observability.model import (
    KIND_MODEL_CALL,
    KIND_TRACE,
    OUTCOME_FAILED,
    OUTCOME_SUCCESS,
    SERVICE_ENGINE,
    SERVICE_GATEWAY,
    SpanRecord,
    new_id,
    now_utc_iso,
    opt_str,
)

# --------------------------------------------------------------------------- #
# Sink contract
# --------------------------------------------------------------------------- #
class TelemetrySink(ABC):
    """Write contract for telemetry consumers (store, exporter, dashboard)."""

    @abstractmethod
    def write_span(self, span: SpanRecord) -> None:
        """Persist one span record."""

    def write_trace(self, trace: Any) -> None:  # pragma: no cover - optional
        """Persist a completed trace summary (default: no-op)."""

    def flush(self) -> None:
        """Flush any buffered records (default: no-op)."""

    def close(self) -> None:
        """Release resources (default: no-op)."""


class NoopSink(TelemetrySink):
    """Discards everything — used when telemetry is disabled (flag OFF)."""

    def write_span(self, span: SpanRecord) -> None:  # noqa: D102
        pass


class MemorySink(TelemetrySink):
    """Keeps spans in process memory (tests, short-lived pipelines)."""

    def __init__(self) -> None:
        self.spans: list[SpanRecord] = []
        self.traces: list[Any] = []

    def write_span(self, span: SpanRecord) -> None:  # noqa: D102
        self.spans.append(span)

    def write_trace(self, trace: Any) -> None:  # noqa: D102
        self.traces.append(trace)

    def reset(self) -> None:
        self.spans.clear()
        self.traces.clear()


# --------------------------------------------------------------------------- #
# Active trace context (propagated with contextvars)
# --------------------------------------------------------------------------- #
@dataclass
class TraceContext:
    """The active end-to-end request context visible to nested spans."""

    trace_id: str
    tenant_id: str
    request_id: str
    started_at: str
    service: str
    name: str
    agent_id: Optional[str] = None
    task_type: Optional[str] = None
    #: Active span stack (span ids); the top is the current parent.
    stack: list[str] = field(default_factory=list)


_trace_var: ContextVar[Optional[TraceContext]] = ContextVar(
    "observability_trace", default=None
)


def current_trace() -> Optional[TraceContext]:
    """The active trace in this execution context (or None)."""
    return _trace_var.get()


def current_trace_id() -> Optional[str]:
    """The correlation id of the active trace (or None)."""
    ctx = _trace_var.get()
    return ctx.trace_id if ctx else None


# --------------------------------------------------------------------------- #
# Pending span (mutable until finished)
# --------------------------------------------------------------------------- #
class PendingSpan:
    """An in-flight span handle; ``finish()`` emits the frozen SpanRecord.

    Created by :meth:`TelemetryRecorder.span` / :meth:`TelemetryRecorder.trace`
    so the recorder can measure elapsed time and attach the span to the
    current trace automatically.
    """

    def __init__(
        self,
        recorder: "TelemetryRecorder",
        *,
        trace_id: str,
        tenant_id: str,
        service: str,
        kind: str,
        name: str,
        started_at: str,
        parent_span_id: Optional[str],
        request_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        task_type: Optional[str] = None,
    ) -> None:
        self._recorder = recorder
        self.span_id = new_id()
        self.trace_id = trace_id
        self.tenant_id = tenant_id
        self.service = service
        self.kind = kind
        self.name = name
        self.started_at = started_at
        self.parent_span_id = parent_span_id
        self.request_id = request_id
        self.agent_id = agent_id
        self.task_type = task_type
        self._start = recorder.clock()
        self._finished = False

    def finish(
        self,
        *,
        outcome: str = OUTCOME_SUCCESS,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        tier: Optional[str] = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        latency_ms: Optional[float] = None,
        estimated_cost_usd: Optional[float] = None,
        error: Optional[str] = None,
        attrs: Optional[Mapping[str, Any]] = None,
    ) -> SpanRecord:
        """Finalize and emit the span; returns the immutable SpanRecord.

        When ``latency_ms`` is omitted the recorder's clock measures the
        elapsed wall time since the span was created (honest timing for
        live emission; tests inject explicit values for determinism).
        """
        if self._finished:
            raise RuntimeError(f"span {self.span_id} already finished")
        self._finished = True
        if latency_ms is None:
            latency_ms = (self._recorder.clock() - self._start) * 1000.0
        span = SpanRecord(
            trace_id=self.trace_id,
            span_id=self.span_id,
            parent_span_id=self.parent_span_id,
            request_id=self.request_id,
            tenant_id=self.tenant_id,
            agent_id=self.agent_id,
            task_type=self.task_type,
            service=self.service,
            kind=self.kind,
            name=self.name,
            outcome=outcome,
            ts=self.started_at,
            provider=provider,
            model=model,
            tier=tier,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=float(latency_ms),
            estimated_cost_usd=float(estimated_cost_usd or 0.0),
            error=error,
            attrs=dict(attrs or {}),
        )
        self._recorder.sink.write_span(span)
        return span


# --------------------------------------------------------------------------- #
# Recorder (composition root / intake hook)
# --------------------------------------------------------------------------- #
class TelemetryRecorder:
    """The intake hook other pillars call to emit telemetry.

    Usage (end-to-end correlation):

        rec = TelemetryRecorder(store)
        with rec.trace(tenant_id="t1", request_id="req-1") as trace:
            with rec.span(SERVICE_GUARDRAILS, KIND_GUARD, "policy.gate",
                          outcome=OUTCOME_DENIED):
                ...
            with rec.span(SERVICE_GATEWAY, KIND_MODEL_CALL, "model.call",
                          provider="anthropic", model="claude-3-5",
                          outcome=OUTCOME_SUCCESS, latency_ms=120.5):
                ...
        # every span shares trace.trace_id -> the store can rebuild the trace

    ``trace()`` emits a root span (kind ``trace``) representing the whole
    request; nested ``span()`` calls become its children automatically via
    the contextvar stack, giving correlation ids end-to-end.
    """

    def __init__(
        self,
        sink: TelemetrySink,
        *,
        clock: Callable[[], float] = time.monotonic,
        timestamp: Callable[[], str] = now_utc_iso,
    ) -> None:
        self.sink = sink
        self.clock = clock
        self.timestamp = timestamp

    # -- trace scope -----------------------------------------------------
    @contextmanager
    def trace(
        self,
        tenant_id: str,
        *,
        request_id: Optional[str] = None,
        service: str = SERVICE_ENGINE,
        name: str = "request",
        agent_id: Optional[str] = None,
        task_type: Optional[str] = None,
    ) -> Iterator[TraceContext]:
        """Open an end-to-end request trace; emits a root span on exit."""
        ctx = TraceContext(
            trace_id=new_id(),
            tenant_id=tenant_id,
            request_id=request_id or new_id(),
            started_at=self.timestamp(),
            service=service,
            name=name,
            agent_id=agent_id,
            task_type=task_type,
        )
        root = PendingSpan(
            self,
            trace_id=ctx.trace_id,
            tenant_id=tenant_id,
            service=service,
            kind=KIND_TRACE,
            name=name,
            started_at=ctx.started_at,
            parent_span_id=None,
            request_id=ctx.request_id,
            agent_id=agent_id,
            task_type=task_type,
        )
        token = _trace_var.set(ctx)
        ctx.stack.append(root.span_id)
        try:
            yield ctx
        except BaseException as exc:  # honest failure capture, never silent
            root.finish(
                outcome=OUTCOME_FAILED,
                error=str(exc) if not isinstance(exc, KeyboardInterrupt) else "interrupted",
            )
            raise
        else:
            root.finish(outcome=OUTCOME_SUCCESS)
        finally:
            _trace_var.reset(token)

    # -- child span ------------------------------------------------------
    @contextmanager
    def span(
        self,
        service: str,
        kind: str,
        name: str,
        *,
        outcome: Optional[str] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        tier: Optional[str] = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        latency_ms: Optional[float] = None,
        estimated_cost_usd: Optional[float] = None,
        error: Optional[str] = None,
        attrs: Optional[Mapping[str, Any]] = None,
        tenant_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        task_type: Optional[str] = None,
    ) -> Iterator[PendingSpan]:
        """Open a child span under the active trace.

        Requires an active :meth:`trace` (or ``tenant_id`` for a standalone
        emission).  On exception the span is finished as ``failed`` so a
        thrown error is never recorded as a silent success.
        """
        ctx = _trace_var.get()
        if ctx is None:
            if tenant_id is None:
                raise RuntimeError(
                    "no active trace; pass tenant_id for a standalone span"
                )
            # Standalone emission: open an implicit single-span trace so the
            # record still carries an end-to-end correlation id.
            with self.trace(
                tenant_id=tenant_id,
                service=service,
                name=name,
                agent_id=agent_id,
                task_type=task_type,
            ):
                ctx = _trace_var.get()
                assert ctx is not None
                with self._child(
                    ctx,
                    parent=None,
                    service=service,
                    kind=kind,
                    name=name,
                    outcome=outcome,
                    provider=provider,
                    model=model,
                    tier=tier,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    latency_ms=latency_ms,
                    estimated_cost_usd=estimated_cost_usd,
                    error=error,
                    attrs=attrs,
                ) as pend:
                    yield pend
            return
        parent = ctx.stack[-1] if ctx.stack else None
        with self._child(
            ctx,
            parent=parent,
            service=service,
            kind=kind,
            name=name,
            outcome=outcome,
            provider=provider,
            model=model,
            tier=tier,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            estimated_cost_usd=estimated_cost_usd,
            error=error,
            attrs=attrs,
        ) as pend:
            yield pend

    @contextmanager
    def _child(
        self,
        ctx: TraceContext,
        *,
        parent: Optional[str],
        service: str,
        kind: str,
        name: str,
        outcome: Optional[str],
        provider: Optional[str],
        model: Optional[str],
        tier: Optional[str],
        input_tokens: int,
        output_tokens: int,
        latency_ms: Optional[float],
        estimated_cost_usd: Optional[float],
        error: Optional[str],
        attrs: Optional[Mapping[str, Any]],
    ) -> Iterator[PendingSpan]:
        pend = PendingSpan(
            self,
            trace_id=ctx.trace_id,
            tenant_id=ctx.tenant_id,
            service=service,
            kind=kind,
            name=name,
            started_at=self.timestamp(),
            parent_span_id=parent,
            request_id=ctx.request_id,
            agent_id=ctx.agent_id,
            task_type=ctx.task_type,
        )
        ctx.stack.append(pend.span_id)
        try:
            yield pend
        except BaseException as exc:  # honest failure capture, never silent
            pend.finish(
                outcome=OUTCOME_FAILED,
                error=str(exc) if not isinstance(exc, KeyboardInterrupt) else "interrupted",
            )
            raise
        else:
            pend.finish(
                outcome=outcome or OUTCOME_SUCCESS,
                provider=provider,
                model=model,
                tier=tier,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=latency_ms,
                estimated_cost_usd=estimated_cost_usd,
                error=error,
                attrs=attrs,
            )
        finally:
            ctx.stack.pop()

    # -- gateway record absorption --------------------------------------
    def ingest_gateway_call(self, record: Mapping[str, Any]) -> SpanRecord:
        """Absorb a ``GatewayCallRecord`` JSON dict (gateway/proxy, issue #16).

        Maps the camelCase gateway record onto a ``model_call`` span with the
        gateway's ``requestId`` as the correlation id, so the phase-5 store
        consumes the gateway's model-call-audit lines without any gateway
        code changes.  ``requestId`` is the cross-pillar correlation key.
        """
        trace_id = str(record.get("requestId") or new_id())
        tenant_id = str(record.get("tenantId") or "")
        if not tenant_id:
            raise ValueError("gateway call record missing tenantId")
        ctx = _trace_var.get()
        parent = ctx.stack[-1] if ctx and ctx.stack else None
        active_trace_id = ctx.trace_id if ctx else None
        span = SpanRecord(
            trace_id=active_trace_id or trace_id,
            span_id=new_id(),
            parent_span_id=parent,
            request_id=opt_str(record.get("requestId")),
            tenant_id=tenant_id,
            agent_id=opt_str(record.get("agentId")),
            task_type=opt_str(record.get("taskType")),
            service=SERVICE_GATEWAY,
            kind=KIND_MODEL_CALL,
            name=f"gateway.{record.get('taskType') or 'call'}",
            outcome=str(record.get("outcome") or OUTCOME_FAILED),
            ts=str(record.get("ts") or now_utc_iso()),
            provider=opt_str(record.get("provider")),
            model=opt_str(record.get("model")),
            tier=opt_str(record.get("tier")),
            input_tokens=int(record.get("inputTokens", 0) or 0),
            output_tokens=int(record.get("outputTokens", 0) or 0),
            latency_ms=float(record.get("latencyMs", 0.0) or 0.0),
            estimated_cost_usd=float(record.get("estimatedCostUsd", 0.0) or 0.0),
            error=opt_str(record.get("error")),
        )
        self.sink.write_span(span)
        return span

    # -- one-shot emission -----------------------------------------------
    def emit(
        self,
        service: str,
        kind: str,
        name: str,
        *,
        outcome: str = OUTCOME_SUCCESS,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        tier: Optional[str] = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        latency_ms: Optional[float] = None,
        estimated_cost_usd: Optional[float] = None,
        error: Optional[str] = None,
        attrs: Optional[Mapping[str, Any]] = None,
        tenant_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        task_type: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> SpanRecord:
        """Emit a finished span in one call (post-hoc call-site recording).

        When an active :meth:`trace` exists the span is attached as a child of
        the current span (same trace id = end-to-end correlation).  Otherwise
        a standalone single-span record is written with its own correlation
        id — the gateway/guardrails/engine call sites emit this way when no
        request context is in flight.
        """
        ctx = _trace_var.get()
        if ctx is None:
            if tenant_id is None:
                raise RuntimeError("emit requires an active trace or tenant_id")
            span = SpanRecord(
                trace_id=request_id or new_id(),
                span_id=new_id(),
                parent_span_id=None,
                request_id=request_id,
                tenant_id=tenant_id,
                agent_id=agent_id,
                task_type=task_type,
                service=service,
                kind=kind,
                name=name,
                outcome=outcome,
                ts=self.timestamp(),
                provider=provider,
                model=model,
                tier=tier,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=float(latency_ms or 0.0),
                estimated_cost_usd=float(estimated_cost_usd or 0.0),
                error=error,
                attrs=dict(attrs or {}),
            )
            self.sink.write_span(span)
            return span
        parent = ctx.stack[-1] if ctx.stack else None
        span = SpanRecord(
            trace_id=ctx.trace_id,
            span_id=new_id(),
            parent_span_id=parent,
            request_id=ctx.request_id,
            tenant_id=ctx.tenant_id,
            agent_id=ctx.agent_id,
            task_type=ctx.task_type,
            service=service,
            kind=kind,
            name=name,
            outcome=outcome,
            ts=self.timestamp(),
            provider=provider,
            model=model,
            tier=tier,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=float(latency_ms or 0.0),
            estimated_cost_usd=float(estimated_cost_usd or 0.0),
            error=error,
            attrs=dict(attrs or {}),
        )
        self.sink.write_span(span)
        return span


__all__ = [
    "TelemetrySink",
    "NoopSink",
    "MemorySink",
    "TraceContext",
    "current_trace",
    "current_trace_id",
    "PendingSpan",
    "TelemetryRecorder",
]
