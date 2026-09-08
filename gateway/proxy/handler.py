"""Thin handler for ``POST /v1/agents/{agentId}/tasks`` (issue #16).

The gateway proxy's acceptance criterion is expressed as a REST surface:
``POST /v1/agents/:agentId/tasks`` (+ streaming).  This module is the *thin
handler* that maps that REST semantic onto the dispatch core.  It is
transport-free — no sockets, no HTTP framework — so it runs fully offline; the
phase-7 control-plane REST issue mounts this handler behind real HTTP and
chunks the streaming events as SSE.  The real request auth (tenant identity)
is out of scope here: ``tenantId`` rides on the task body exactly as the
core's ``TaskRequest.tenant_id``.
"""

from __future__ import annotations

from typing import Any, Iterator, Mapping

from proxy import contract
from proxy.gateway import ModelGateway
from proxy.model import (
    DispatchEvent,
    TaskRequest,
    TaskResult,
    new_request_id,
)

#: Outcome -> HTTP status semantic (documented contract of the REST surface).
OUTCOME_STATUS: dict[str, int] = {
    contract.OUTCOME_SUCCESS: 200,
    contract.OUTCOME_CACHE_HIT: 200,
    contract.OUTCOME_BLOCKED: 429,
    contract.OUTCOME_RATE_LIMITED: 429,
    contract.OUTCOME_REFUSED: 422,
    contract.OUTCOME_CANNOT_ASSESS: 422,
    contract.OUTCOME_NO_HEALTHY_ROUTE: 503,
    contract.OUTCOME_FAILED: 502,
    contract.OUTCOME_DENIED: 403,
}


def outcome_status(outcome: str) -> int:
    """The HTTP status semantic for a dispatch outcome (default 502)."""
    return OUTCOME_STATUS.get(outcome, 502)


class TaskBodyError(ValueError):
    """A task body could not be mapped onto a TaskRequest (fail closed)."""


def parse_task_request(body: Mapping[str, Any]) -> TaskRequest:
    """Map a JSON task body onto a ``TaskRequest`` (validating, fail closed).

    Body shape (the ``POST .../tasks`` payload):

    .. code-block:: json

        {
          "tenantId": "acme",
          "taskType": "classify-route",
          "input": {"thread": "..."},
          "complexity": 30,
          "tokens": 1200,
          "stream": false
        }

    ``taskType`` and ``tenantId`` are required.  Unknown fields are ignored so
    forward-compatible bodies do not break dispatch; malformed required fields
    raise ``TaskBodyError``.
    """
    if not isinstance(body, Mapping):
        raise TaskBodyError("task body must be a JSON object")
    tenant_id = body.get("tenantId")
    task_type = body.get("taskType")
    if not isinstance(tenant_id, str) or not tenant_id:
        raise TaskBodyError("task body requires a non-empty string tenantId")
    if not isinstance(task_type, str) or not task_type:
        raise TaskBodyError("task body requires a non-empty string taskType")
    raw_input = body.get("input") or {}
    if not isinstance(raw_input, Mapping):
        raise TaskBodyError("task body 'input' must be an object of variables")
    complexity = body.get("complexity")
    tokens = body.get("tokens")
    if complexity is not None and not isinstance(complexity, (int, float)):
        raise TaskBodyError("task body 'complexity' must be a number")
    if tokens is not None and not isinstance(tokens, int):
        raise TaskBodyError("task body 'tokens' must be an integer")
    return TaskRequest(
        tenant_id=str(tenant_id),
        task_type=str(task_type),
        input=dict(raw_input),
        complexity=float(complexity) if complexity is not None else None,
        tokens=tokens,
        stream=bool(body.get("stream", False)),
        request_id=str(body.get("requestId") or new_request_id()),
        metadata=dict(body.get("metadata") or {}),
    )


class GatewayHandler:
    """Thin REST handler: POST /v1/agents/{agentId}/tasks (+ streaming)."""

    def __init__(self, gateway: ModelGateway) -> None:
        self.gateway = gateway

    # -- single task -------------------------------------------------------- #
    def handle_task(self, agent_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        """Handle one task dispatch; returns a serializable HTTP-style envelope.

        Envelope: ``{"status": <int>, "result": {...}, "record": {...}}`` — the
        thin handler maps outcome -> status; the result and the full gateway
        call record ride along so the HTTP layer needs no proxy knowledge.
        """
        request = parse_task_request(body)
        result = self.gateway.dispatch(agent_id, request)
        return self._envelope(result)

    # -- streaming task ----------------------------------------------------- #
    def handle_task_stream(
        self, agent_id: str, body: Mapping[str, Any]
    ) -> Iterator[dict[str, Any]]:
        """Handle one task with streaming semantics.

        Yields one envelope per incremental ``DispatchEvent``
        (``{"event": {...}}``), then the terminal envelope
        (``{"status": ..., "result": ..., "record": ...}``).  An HTTP layer
        relays the event envelopes as SSE-style chunks.
        """
        request = parse_task_request(body)
        for item in self.gateway.dispatch_stream(agent_id, request):
            if isinstance(item, DispatchEvent):
                yield {"event": item.to_dict()}
            elif isinstance(item, TaskResult):
                yield self._envelope(item)

    # -- helpers ------------------------------------------------------------ #
    @staticmethod
    def _envelope(result: TaskResult) -> dict[str, Any]:
        return {
            "status": outcome_status(result.outcome),
            "result": result.to_dict(),
            "record": result.record.to_dict() if result.record is not None else None,
        }
