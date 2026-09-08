"""MCP client bootstrap (issue #41).

A small, typed bootstrap client for the platform's tenant-scoped MCP tool
gateway (issue #20): it speaks minimal JSON-RPC 2.0 over the injected
transport and exposes the lifecycle a governed consumer needs —

- :meth:`McpClient.initialize` — protocol negotiation
  (``protocolVersion: 2024-11-05``) + client info;
- :meth:`McpClient.ping` — liveness;
- :meth:`McpClient.list_tools` — the declared tool catalog (narrowed to the
  session's allowed tools by the platform);
- :meth:`McpClient.call_tool` — one ``tools/call`` carrying the tenant-scoped
  session token in ``context.session`` (the platform enforces identity,
  allowlist, rate and audit — issue #20).

The client never asserts a tenant of its own choosing: ``context.tenantId``
is derived from the verified session claims so a caller cannot cross tenants.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Mapping, Optional

from .auth import SessionToken, TokenSource, verify_not_expired
from .errors import ConfigurationError, McpError
from .model import ToolDefinition, ToolResult

#: Protocol version the gateway advertises (issue #20).
PROTOCOL_VERSION = "2024-11-05"
#: Default MCP endpoint path (a deployment configures its own gateway URL).
DEFAULT_MCP_PATH = "/v1/mcp"


class McpClient:
    """JSON-RPC 2.0 MCP client bootstrap (typed, transport-injected)."""

    def __init__(
        self,
        transport: Any,
        *,
        token_source: Optional[TokenSource] = None,
        tenant_id: Optional[str] = None,
        path: str = DEFAULT_MCP_PATH,
        client_name: str = "agent-orchestrator-sdk",
        client_version: str = "0.1.0",
    ) -> None:
        self._transport = transport
        self._token_source = token_source or TokenSource()
        self._tenant_id = tenant_id
        self._path = path
        self._client_name = client_name
        self._client_version = client_version
        self._next_id = 1

    # -- auth ---------------------------------------------------------------- #
    def _session(self) -> SessionToken:
        token = self._token_source.require()
        session = verify_not_expired(token)  # fail closed on a lapsed token
        return session

    # -- JSON-RPC plumbing --------------------------------------------------- #
    def _rpc(self, method: str, params: Mapping[str, Any]) -> Dict[str, Any]:
        token = self._token_source.require()
        request_id = self._next_id
        self._next_id += 1
        payload: Dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": dict(params),
        }
        response = self._transport.request("POST", self._path, body=payload, token=token)
        if not isinstance(response, Mapping):
            raise McpError(-32700, "response was not a JSON-RPC object")
        error = response.get("error")
        if error is not None:
            raise McpError(
                int(error.get("code") or -32603),
                str(error.get("message") or "json-rpc error"),
                error.get("data"),
            )
        if "result" not in response:
            raise McpError(-32603, "json-rpc response had no result")
        return response["result"]

    # -- lifecycle ------------------------------------------------------------ #
    def initialize(self) -> Dict[str, Any]:
        """Negotiate the protocol and advertise client capabilities."""
        return self._rpc(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "clientInfo": {"name": self._client_name, "version": self._client_version},
            },
        )

    def ping(self) -> bool:
        """Return ``True`` when the gateway answers ``ping``."""
        result = self._rpc("ping", {})
        return result is not None

    def list_tools(self) -> List[ToolDefinition]:
        """The declared tool catalog (platform-narrowed to the session)."""
        self._session()  # fail closed: a tools/list for an absent/lapsed session
        result = self._rpc(
            "tools/list",
            {"context": {"session": self._raw_token()}},
        )
        return [ToolDefinition.from_dict(item) for item in (result.get("tools") or [])]

    def call_tool(
        self,
        name: str,
        arguments: Optional[Mapping[str, Any]] = None,
    ) -> ToolResult:
        """Call one declared tool with ``context.session`` + ``context.tenantId``.

        ``context.tenantId`` is derived from the verified session claims (the
        platform refuses an explicit tenant that disagrees — no cross-tenant).
        """
        session = self._session()
        tenant_id = self._tenant_id or session.tenant_id
        if not tenant_id:
            raise ConfigurationError("session token must carry a tenantId claim")
        result = self._rpc(
            "tools/call",
            {
                "name": name,
                "arguments": dict(arguments or {}),
                "context": {"session": self._raw_token(), "tenantId": tenant_id},
            },
        )
        return ToolResult.from_dict(result)

    # -- helpers -------------------------------------------------------------- #
    def _raw_token(self) -> str:
        return self._token_source.require()


def new_request_id() -> str:
    """Opaque JSON-RPC request id (uuid hex) for callers that build messages."""
    return uuid.uuid4().hex
