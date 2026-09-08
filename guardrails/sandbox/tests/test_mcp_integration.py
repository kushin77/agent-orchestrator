"""MCP tool-gateway integration: tool Execute routes through sandbox + audit.

This is the wiring proof for acceptance criterion 4. The merged gateway/mcp
surface (issue #20) is imported READ-ONLY and never modified: sandbox-routed
tools are declared capabilities registered additively into the gateway's
``ToolRegistry`` (via ``sandbox.mcp_seam.sandboxed_tool``), and their handlers
route every call through a :class:`SandboxExecutor` (offline runtime) before
acting. End to end, through the real ``MCPToolGateway.handle_message`` path:

* an allowed execution (network category -> standard profile, bridge network)
  returns the sandbox result and is audited status ``ok`` by the gateway's
  append-only ledger, with an execution-level record on the sandbox's ledger;
* a restricted-profile denial (unknown category -> restricted, or exec
  capability escalation) surfaces as an ``isError`` result and is audited
  status ``error`` - never a silent success;
* the gateway's own unchanged behaviour (unknown tool -> -32601, denial audit)
  and its informational tools still work, so the additive wiring broke nothing.
"""

from __future__ import annotations

import json

from sandbox.catalog import default_category_map
from sandbox.executor import SandboxExecutor
from sandbox.mcp_seam import sandboxed_tool
from sandbox.model import ExecutionRequest
from sandbox.offline import OfflineRuntime

# gateway/ is on sys.path via this suite's conftest (read-only import).
from mcp import authn  # noqa: E402
from mcp.audit import HashChainAuditLog  # noqa: E402
from mcp.authz import AuthzDecision  # noqa: E402
from mcp.gateway import MCPToolGateway  # noqa: E402
from mcp.tools import build_registry  # noqa: E402

SIGNING_KEY = bytes(range(32))
TENANT = "acme"
AGENT = "agent-a"


class AllowAllGuard:
    """PermissionGuard that allows every call (isolates the sandbox seam)."""

    def authorize(self, session, permission: str) -> AuthzDecision:
        return AuthzDecision(allowed=True, permission=permission)


def _mint_session(tools):
    session = authn.mint_session(
        TENANT, AGENT, SIGNING_KEY, allowed_tools=tuple(tools)
    )
    return authn.session_to_token(session, SIGNING_KEY)


def _text(response):
    return response["result"]["content"][0]["text"]


def _call(gateway, token, name, arguments=None):
    return gateway.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": name,
                "arguments": arguments or {},
                "context": {"session": token, "tenantId": TENANT},
            },
        }
    )


def _net_request(args, session, backend, name, category):
    return ExecutionRequest(
        category=category, tool=name, operation="http-get",
        requires_network=True,
    )


def _mount_request(args, session, backend, name, category):
    return ExecutionRequest(
        category=category, tool=name, operation="mount",
        requires_caps=("SYS_ADMIN",),
    )


def _build_gateway():
    """Build a gateway wired additively; return (gateway, gateway_audit,
    sandbox_audit) with fresh ledgers per test."""
    gateway_audit = HashChainAuditLog()
    sandbox_audit = HashChainAuditLog()

    registry = build_registry()
    executor = SandboxExecutor(
        runtime=OfflineRuntime(enabled=True), audit=sandbox_audit
    )
    registry.register(
        sandboxed_tool(
            executor=executor,
            name="sandbox.net.fetch",
            description="Fetch over the network (network category, offline).",
            input_schema={
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
            category="network",
            request_builder=_net_request,
        )
    )
    registry.register(
        sandboxed_tool(
            executor=executor,
            name="sandbox.exec.mount",
            description="Mount a filesystem (needs SYS_ADMIN, exec category).",
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
            category="exec",
            request_builder=_mount_request,
        )
    )
    registry.register(
        sandboxed_tool(
            executor=executor,
            name="sandbox.unknown.submit",
            description="A tool whose category is not in the closed map.",
            input_schema={
                "type": "object",
                "properties": {"job": {"type": "string"}},
                "required": ["job"],
            },
            category="scheduler",  # undeclared -> fail closed to restricted
            request_builder=_net_request,
        )
    )
    gateway = MCPToolGateway(
        registry,
        authz=AllowAllGuard(),
        audit=gateway_audit,
        signing_key=SIGNING_KEY,
    )
    return gateway, gateway_audit, sandbox_audit


def test_allowed_network_execution_routes_through_sandbox_and_is_audited():
    token = _mint_session(
        ("platform.whoami", "sandbox.net.fetch", "sandbox.exec.mount",
         "sandbox.unknown.submit")
    )
    gateway, gateway_audit, sandbox_audit = _build_gateway()

    response = _call(gateway, token, "sandbox.net.fetch", {"url": "https://example.test"})

    assert "error" not in response
    assert response["result"]["isError"] in (False, None)
    payload = json.loads(_text(response))
    assert payload["profile"] == "standard"
    assert payload["category"] == "network"
    assert payload["runtime"] == "offline"
    assert payload["sandbox"]["sandboxed"] is True

    # gateway audit: one tool_call ok naming the tool (who/what/tenant/result)
    gateway_records = gateway_audit.events()
    assert len(gateway_records) == 1
    assert gateway_records[0]["event"] == "tool_call"
    assert gateway_records[0]["status"] == "ok"
    assert gateway_records[0]["detail"]["tool"] == "sandbox.net.fetch"
    assert gateway_records[0]["tenantId"] == TENANT

    # sandbox execution-level audit: profile/category/outcome + who
    sandbox_records = sandbox_audit.events()
    assert len(sandbox_records) == 1
    assert sandbox_records[0]["detail"]["profile"] == "standard"
    assert sandbox_records[0]["detail"]["category"] == "network"
    assert sandbox_records[0]["detail"]["outcome"] == "accepted"
    assert sandbox_records[0]["tenantId"] == TENANT
    assert sandbox_records[0]["agentId"] == AGENT

    gateway_audit.verify()
    sandbox_audit.verify()


def test_unknown_category_network_op_fails_closed_and_is_audited():
    token = _mint_session(("sandbox.unknown.submit",))
    gateway, gateway_audit, sandbox_audit = _build_gateway()

    response = _call(gateway, token, "sandbox.unknown.submit", {"job": "x"})

    assert "error" not in response
    assert response["result"]["isError"] is True
    text = _text(response)
    assert "denied by sandbox" in text
    assert "network access blocked" in text

    gateway_records = gateway_audit.events()
    assert len(gateway_records) == 1
    assert gateway_records[0]["event"] == "tool_call"
    assert gateway_records[0]["status"] == "error"
    assert "network access blocked" in gateway_records[0]["detail"]["result"]["message"]

    sandbox_records = sandbox_audit.events()
    assert len(sandbox_records) == 1
    assert sandbox_records[0]["detail"]["outcome"] == "sandbox_denied"
    assert sandbox_records[0]["detail"]["profile"] == "restricted"
    assert sandbox_records[0]["detail"]["category"] == "scheduler"

    gateway_audit.verify()
    sandbox_audit.verify()


def test_restricted_profile_blocks_capability_escalation():
    token = _mint_session(("sandbox.exec.mount",))
    gateway, gateway_audit, sandbox_audit = _build_gateway()

    response = _call(gateway, token, "sandbox.exec.mount", {"path": "/data"})

    assert response["result"]["isError"] is True
    assert "SYS_ADMIN" in _text(response)

    sandbox_records = sandbox_audit.events()
    assert len(sandbox_records) == 1
    assert sandbox_records[0]["detail"]["outcome"] == "sandbox_denied"
    assert sandbox_records[0]["detail"]["profile"] == "restricted"
    assert "dropped" in sandbox_records[0]["detail"]["reason"]

    assert gateway_audit.events()[0]["status"] == "error"
    gateway_audit.verify()
    sandbox_audit.verify()


def test_gateway_unknown_tool_denial_still_audited_unconditionally():
    token = _mint_session(("platform.whoami",))
    gateway, gateway_audit, sandbox_audit = _build_gateway()

    response = gateway.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 9,
            "method": "tools/call",
            "params": {
                "name": "no.such.tool",
                "arguments": {},
                "context": {"session": token, "tenantId": TENANT},
            },
        }
    )
    assert response["error"]["code"] == -32601

    record = gateway_audit.events()[-1]
    assert record["event"] == "tool_call_denied"
    assert record["status"] == "unknown"
    assert record["detail"]["tool"] == "no.such.tool"

    # the sandbox never ran (no execution-level record)
    assert len(sandbox_audit.events()) == 0
    gateway_audit.verify()


def test_gateway_informational_tools_still_work_after_additive_wiring():
    token = _mint_session(("platform.whoami",))
    gateway, gateway_audit, sandbox_audit = _build_gateway()

    response = _call(gateway, token, "platform.whoami")
    assert "error" not in response
    payload = json.loads(_text(response))
    assert payload["tenantId"] == TENANT
    assert payload["agentId"] == AGENT

    assert gateway_audit.events()[-1]["event"] == "tool_call"
    assert gateway_audit.events()[-1]["status"] == "ok"
    assert len(sandbox_audit.events()) == 0

    gateway_audit.verify()


def test_default_category_map_is_consumed_not_redefined():
    # The seam consumes the packaged category map (file->restricted,
    # network->standard, docker->privileged) - sanity check it is in force.
    category_map = default_category_map()
    assert category_map.profile_for("network") == "standard"
    assert category_map.profile_for("exec") == "restricted"
    assert category_map.profile_for("docker") == "privileged"
    assert category_map.profile_for("scheduler") == "restricted"
