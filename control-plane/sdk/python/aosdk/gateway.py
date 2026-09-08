"""Gateway client — typed tasks + streaming (issue #41).

Wraps the model-gateway dispatch surface ``POST /v1/agents/{agentId}/tasks``
(issue #16) behind typed, authenticated calls:

- :meth:`GatewayClient.dispatch` submits one task and returns a typed
  :class:`~aosdk.model.TaskResult` whose ``content`` is the schema-validated
  typed object on a served outcome (``success``/``cache_hit``) and whose
  ``outcome`` is one of the closed set otherwise — the SDK never fabricates
  content for a non-served outcome.
- :meth:`GatewayClient.stream` uses streaming semantics: it yields the
  incremental dispatch events then the terminal :class:`TaskResult`,
  mirroring the platform's streaming handler (issue #16).

Every request carries a short-lived per-tenant session token from the
:class:`~aosdk.auth.TokenSource` (env or callback) — never a hardcoded key.
"""

from __future__ import annotations

from typing import Any, Dict, Iterator, Mapping, Optional, Union

from .auth import TokenSource, verify_not_expired
from .errors import ApiError, ConfigurationError
from .model import (
    DispatchEvent,
    TaskEnvelope,
    TaskRequest,
    TaskResult,
)


class GatewayClient:
    """Typed client for the model-gateway dispatch surface."""

    def __init__(
        self,
        transport: Any,
        *,
        token_source: Optional[TokenSource] = None,
        tenant_id: Optional[str] = None,
    ) -> None:
        self._transport = transport
        self._token_source = token_source or TokenSource()
        self._tenant_id = tenant_id

    # -- auth / tenant resolution ------------------------------------------- #
    def _token_and_tenant(self) -> tuple:
        token = self._token_source.require()
        session = verify_not_expired(token)  # fail closed on a lapsed token
        tenant_id = self._tenant_id or session.tenant_id
        if not tenant_id:
            raise ConfigurationError(
                "no tenant context: pass tenant_id or use a session token with a tenantId claim"
            )
        return token, tenant_id

    # -- single dispatch ----------------------------------------------------- #
    def dispatch(
        self,
        agent_id: str,
        task_type: str,
        *,
        input_: Optional[Mapping[str, Any]] = None,
        complexity: Optional[float] = None,
        tokens: Optional[int] = None,
        request_id: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> TaskResult:
        """Submit one task and return the typed :class:`TaskResult`.

        A served outcome carries the schema-validated typed ``content``; any
        other outcome is explicit (``denied``/``blocked``/``failed``/...) and
        carries no fabricated content.  Use :meth:`run` to raise on a
        non-served outcome.
        """
        token, tenant_id = self._token_and_tenant()
        request = TaskRequest(
            tenant_id=tenant_id,
            task_type=task_type,
            input=dict(input_ or {}),
            complexity=complexity,
            tokens=tokens,
            stream=False,
            request_id=request_id,
            metadata=dict(metadata or {}),
        )
        response = self._transport.request(
            "POST",
            f"/v1/agents/{agent_id}/tasks",
            body=request.to_wire(),
            token=token,
        )
        return self._parse_gateway_response(response, agent_id, tenant_id, task_type)

    def run(self, agent_id: str, task_type: str, **kwargs: Any) -> TaskResult:
        """Dispatch and raise :class:`TaskNotServedError` on a non-served outcome."""
        return self.dispatch(agent_id, task_type, **kwargs).ensure_served()

    # -- streaming dispatch --------------------------------------------------- #
    def stream(
        self,
        agent_id: str,
        task_type: str,
        *,
        input_: Optional[Mapping[str, Any]] = None,
        complexity: Optional[float] = None,
        tokens: Optional[int] = None,
        request_id: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> Iterator[Union[DispatchEvent, TaskResult]]:
        """Stream one dispatch: yields incremental events, then the terminal result.

        The transport must implement the :class:`~aosdk.transport.StreamTransport`
        contract (the offline doubles do; production wiring provides an SSE
        relay adapter).
        """
        request_stream = getattr(self._transport, "request_stream", None)
        if request_stream is None:
            raise ConfigurationError(
                "transport does not support streaming; provide a StreamTransport"
            )
        token, tenant_id = self._token_and_tenant()
        request = TaskRequest(
            tenant_id=tenant_id,
            task_type=task_type,
            input=dict(input_ or {}),
            complexity=complexity,
            tokens=tokens,
            stream=True,
            request_id=request_id,
            metadata=dict(metadata or {}),
        )
        for chunk in request_stream(
            "POST",
            f"/v1/agents/{agent_id}/tasks",
            body=request.to_wire(),
            token=token,
        ):
            if "event" in chunk:
                yield DispatchEvent.from_dict(chunk["event"])
            else:
                yield self._parse_gateway_response(chunk, agent_id, tenant_id, task_type)

    # -- parsing -------------------------------------------------------------- #
    @staticmethod
    def _parse_gateway_response(
        response: Dict[str, Any], agent_id: str, tenant_id: str, task_type: str
    ) -> TaskResult:
        if not isinstance(response, Mapping):
            raise ApiError(500, "malformed_response", "gateway response was not an object")
        if "result" not in response:
            from .envelope import error_from_envelope

            raise error_from_envelope(response)
        envelope = TaskEnvelope.from_dict(response)
        result = envelope.result
        if not result.request_id and not result.tenant_id:
            # Defensive: never hand back an empty result on a 4xx/5xx error body.
            raise ApiError(
                envelope.status, "malformed_response", "gateway envelope had no result payload"
            )
        return result
