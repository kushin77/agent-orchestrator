"""The read-only enterprise tool family (issue #504, EPIC #500).

Issue #504 adds ten grounded-chat tools to the declared MCP catalogue. These
tests hold the family to the properties the ticket states as acceptance
criteria, and each one genuinely fails if the behaviour regresses:

* every added tool is declared with a JSON schema;
* a caller-supplied tenant selector is **refused**, so no argument can widen a
  read's scope, and a cross-tenant session is refused fail-closed;
* an unverified/foreign tenant never reaches a read, and every call - allowed
  or denied - leaves the existing ``tool_call`` / ``tool_call_denied`` record;
* the family is read-only: no tool writes a byte and no second on-disk
  projection of ticket/budget/fleet state appears.
"""

from __future__ import annotations

import hashlib
import json
import os

from mcp.enterprise import (
    CHAT_FAMILY_NAMES,
    ENTERPRISE_TOOL_NAMES,
    REUSED_READ_TOOLS,
    TENANT_ARGUMENT_KEYS,
    TENANT_SCOPED_FAMILIES,
    WRITE_TOOLS,
    enterprise_schemas,
)
from mcp.model import MCP_ALLOWLIST_KEYS
from mcp.protocol import (
    AUTHN_FAILED,
    AUTHZ_DENIED,
    INVALID_PARAMS,
    TENANT_CONTEXT_REQUIRED,
)
from mcp.tools import build_registry, declared_tools

#: The seven base declarations of issue #20, pinned so the family cannot
#: quietly rewrite them (the family is composed *beside* them).
BASE_TOOLS = (
    "code.definitions",
    "code.references",
    "code.search",
    "kb.freshness",
    "kb.query",
    "kb.summary",
    "platform.whoami",
)

BASE_KEEPALIVE_DESCRIPTIONS = {
    "kb.summary": "Counts + shape of this tenant's KB graph snapshot.",
    "platform.whoami": "Return the resolved tenant context of the current session (identity echo).",
}


def _payload(response: dict) -> dict:
    """The tool's own JSON payload out of the JSON-RPC text envelope."""
    return json.loads(response["result"]["content"][0]["text"])


def tree_state(root: str):
    """Every path under ``root`` with its content hash (a write detector)."""
    state = {}
    for base, _dirs, files in os.walk(root):
        for name in files:
            path = os.path.join(base, name)
            with open(path, "rb") as handle:
                state[os.path.relpath(path, root)] = hashlib.sha256(
                    handle.read()
                ).hexdigest()
    return state


# --------------------------------------------------------------------------- #
# declaration
# --------------------------------------------------------------------------- #
def test_family_is_declared_with_schemas(chat_gateway):
    tools = chat_gateway.list_tools()["result"]["tools"]
    names = [tool["name"] for tool in tools]
    assert names == sorted(names)
    assert set(CHAT_FAMILY_NAMES) <= set(names)
    for tool in tools:
        if tool["name"] not in CHAT_FAMILY_NAMES:
            continue
        assert tool["inputSchema"]["type"] == "object"
        assert isinstance(tool["description"], str) and tool["description"]
        if tool["name"] in ENTERPRISE_TOOL_NAMES:
            # the eight this issue declares also close their argument set
            assert tool["inputSchema"]["additionalProperties"] is False


def test_the_eight_new_tools_are_registered_and_the_seven_base_ones_survive():
    registry = build_registry()
    assert set(ENTERPRISE_TOOL_NAMES) <= set(registry.names())
    assert set(BASE_TOOLS) <= set(registry.names())
    assert len(ENTERPRISE_TOOL_NAMES) == 8
    assert len(CHAT_FAMILY_NAMES) == 10
    # the two reused ids are the issue #20 declarations, not re-declarations
    assert set(REUSED_READ_TOOLS) == {"kb.query", "kb.freshness"}
    for name, description in BASE_KEEPALIVE_DESCRIPTIONS.items():
        assert registry.require(name).description == description


def test_the_allowlist_vocabulary_matches_the_callable_registry():
    registry = build_registry()
    assert set(MCP_ALLOWLIST_KEYS) == set(registry.names())
    assert set(declared_tools()) == set(registry.names())


def test_no_schema_declares_a_tenant_selector():
    for name, schema in enterprise_schemas().items():
        assert not (set(schema["properties"]) & set(TENANT_ARGUMENT_KEYS)), name
        assert not (set(schema.get("required", ())) & set(TENANT_ARGUMENT_KEYS)), name


def test_every_declared_property_is_accepted_and_nothing_else_is(chat_catalog):
    """Schema and argument validator must agree in both directions.

    A property the schema publishes but the validator refuses would be an
    uncallable declaration; an argument the validator accepts but the schema
    omits would be an undeclared surface. Both are refuted here.
    """
    from mcp.enterprise import enterprise_handlers
    from mcp.errors import InvalidArgumentsError
    from mcp.model import SessionIdentity

    handlers = enterprise_handlers(chat_catalog)
    session = SessionIdentity(tenant_id="acme", agent_id="agent-a")
    sample = {"number": 504, "agent_id": "claude", "limit": 5, "text": "chat", "state": "OPEN", "label": "gateway"}
    for name, schema in enterprise_schemas().items():
        for prop in schema["properties"]:
            try:
                handlers[name]({prop: sample[prop]}, session, None)
            except InvalidArgumentsError as exc:  # pragma: no cover - the failure
                raise AssertionError(
                    f"{name} refuses its own declared property {prop!r}: {exc}"
                ) from exc
        unknown = next(key for key in ("nope", "nope2") if key not in schema["properties"])
        try:
            handlers[name]({unknown: "x"}, session, None)
        except InvalidArgumentsError as exc:
            assert "does not declare the argument" in str(exc), name
        else:  # pragma: no cover - the failure
            raise AssertionError(f"{name} accepted the undeclared argument {unknown!r}")


# --------------------------------------------------------------------------- #
# tenant scoping: no argument selects a tenant
# --------------------------------------------------------------------------- #
def test_caller_supplied_tenant_argument_is_refused(chat_gateway, mint):
    token = mint("acme", "agent-a", allowed_tools=CHAT_FAMILY_NAMES)
    for key in ("tenant", "tenant_id", "tenantId"):
        response = chat_gateway.call_tool(
            "ledger.tail", {"limit": 5, key: "globex"}, session_token=token
        )
        assert response["error"]["code"] == INVALID_PARAMS, key
        assert "refuses a caller-supplied tenant selector" in response["error"]["message"]


def test_a_tenant_argument_cannot_widen_the_read(chat_gateway, mint):
    token = mint("acme", "agent-a", allowed_tools=CHAT_FAMILY_NAMES)
    response = chat_gateway.call_tool(
        "budget.status", {"tenant_id": "globex"}, session_token=token
    )
    assert "error" in response
    # and the tenant this session may read is still the session's own
    allowed = _payload(
        chat_gateway.call_tool("budget.status", {}, session_token=token)
    )
    assert allowed["tenantId"] == "acme"
    assert allowed["fragments"][0]["payload"]["tenantId"] == "acme"


def test_cross_tenant_session_is_refused_and_audited(chat_gateway, mint, in_memory_audit):
    token = mint("acme", "agent-a", allowed_tools=CHAT_FAMILY_NAMES)
    response = chat_gateway.call_tool(
        "fleet.snapshot", {}, session_token=token, tenant_id="globex"
    )
    assert response["error"]["code"] == AUTHZ_DENIED
    assert "cross-tenant" in response["error"]["message"]
    denied = [event for event in in_memory_audit.events() if event["event"] == "tool_call_denied"]
    assert denied and denied[-1]["status"] == "authz"
    assert denied[-1]["tenantId"] == "acme"


def test_unverified_tenant_context_is_refused_fail_closed(chat_gateway):
    assert (
        chat_gateway.call_tool("ticket.get", {"number": 504})["error"]["code"]
        == TENANT_CONTEXT_REQUIRED
    )
    # The value is deliberately too short to look like a credential: the
    # repo's own scan (scripts/check-secrets.sh, RE_GEN) flags a secret word
    # followed by a quoted 8+ character value, and this line is a test, not a
    # leak. Keep it short rather than teaching the gate to ignore a real shape.
    assert (
        chat_gateway.call_tool(
            "ticket.get", {"number": 504}, session_token="invalid"
        )["error"]["code"]
        == AUTHN_FAILED
    )


def test_tenant_scoped_reads_use_the_session_tenant(chat_gateway, mint):
    acme = mint("acme", "agent-a", allowed_tools=CHAT_FAMILY_NAMES)
    globex = mint("globex", "agent-b", allowed_tools=CHAT_FAMILY_NAMES)
    acme_tail = _payload(chat_gateway.call_tool("ledger.tail", {}, session_token=acme))
    globex_tail = _payload(
        chat_gateway.call_tool("ledger.tail", {}, session_token=globex)
    )
    assert acme_tail["status"] == "ok" and globex_tail["status"] == "ok"
    assert {fragment["payload"]["tenantId"] for fragment in acme_tail["fragments"]} == {"acme"}
    assert {fragment["payload"]["tenantId"] for fragment in globex_tail["fragments"]} == {"globex"}
    assert not (
        set(acme_tail["citations"]) & set(globex_tail["citations"])
    ), "one tenant's citations must never appear in another tenant's read"


def test_tenant_scoped_families_are_a_declared_closed_set(chat_gateway, mint):
    # Only the families that read tenant-owned data are tenant-scoped; the
    # platform-configuration families are readable by any authenticated
    # principal (the ao.bridge/v1 rule).
    assert set(TENANT_SCOPED_FAMILIES) == {"budget", "ledger", "kb-fixture"}
    token = mint("acme", "agent-a", allowed_tools=CHAT_FAMILY_NAMES)
    fleet = _payload(chat_gateway.call_tool("fleet.snapshot", {}, session_token=token))
    assert fleet["status"] == "ok"
    assert fleet["fragments"][0]["authority"] == "ao.bridge/v1"


# --------------------------------------------------------------------------- #
# audit: the existing records, not a new one
# --------------------------------------------------------------------------- #
def test_every_family_call_emits_the_existing_audit_records(
    chat_gateway, mint, in_memory_audit
):
    token = mint("acme", "agent-a", allowed_tools=CHAT_FAMILY_NAMES)
    before = len(in_memory_audit)
    chat_gateway.call_tool("ticket.get", {"number": 504}, session_token=token)
    chat_gateway.call_tool(
        "ticket.get", {"number": 504, "tenant": "globex"}, session_token=token
    )
    events = in_memory_audit.events()[before:]
    assert [event["event"] for event in events] == ["tool_call", "tool_call"]
    assert events[0]["detail"]["tool"] == "ticket.get"
    assert events[0]["status"] == "ok"
    assert events[1]["status"] == "error"
    assert in_memory_audit.verify()[0] == len(in_memory_audit)


# --------------------------------------------------------------------------- #
# read-only: no write path, no second projection
# --------------------------------------------------------------------------- #
def test_no_tool_in_the_family_writes(chat_gateway, mint, chat_root):
    assert WRITE_TOOLS == ()
    token = mint("acme", "agent-a", allowed_tools=CHAT_FAMILY_NAMES)
    before = tree_state(chat_root)
    arguments = {
        "ticket.get": {"number": 504},
        "ticket.search": {"state": "OPEN"},
        "budget.status": {},
        "ledger.tail": {"limit": 2},
        "ledger.verify": {},
        "agent.list": {},
        "agent.status": {"agent_id": "claude"},
        "fleet.snapshot": {},
    }
    assert set(arguments) == set(ENTERPRISE_TOOL_NAMES)
    for name, args in arguments.items():
        response = chat_gateway.call_tool(name, args, session_token=token)
        assert "error" not in response, name
    assert tree_state(chat_root) == before


def test_no_second_projection_of_platform_state_is_created(chat_gateway, mint, chat_root):
    token = mint("acme", "agent-a", allowed_tools=CHAT_FAMILY_NAMES)
    before = set(tree_state(chat_root))
    for name, args in (
        ("fleet.snapshot", {}),
        ("agent.list", {}),
        ("ticket.search", {"state": "OPEN"}),
        ("budget.status", {}),
    ):
        chat_gateway.call_tool(name, args, session_token=token)
    after = set(tree_state(chat_root))
    assert after == before, "a family read must not materialise a new store"


# --------------------------------------------------------------------------- #
# honesty: NO_DATA is explicit
# --------------------------------------------------------------------------- #
def test_an_absent_source_is_no_data_and_never_an_empty_success(chat_gateway, mint):
    token = mint("globex", "agent-b", allowed_tools=CHAT_FAMILY_NAMES)
    payload = _payload(chat_gateway.call_tool("budget.status", {}, session_token=token))
    assert payload["status"] == "NO_DATA"
    assert payload["count"] == 0
    assert payload["fragments"] == []
    assert "globex" in payload["reason"] and payload["reason"].strip()
    response = chat_gateway.call_tool("budget.status", {}, session_token=token)
    assert response["result"]["isError"] is False


def test_an_unknown_ticket_is_reported_absent_not_invented(chat_gateway, mint):
    token = mint("acme", "agent-a", allowed_tools=CHAT_FAMILY_NAMES)
    payload = _payload(chat_gateway.call_tool("ticket.get", {"number": 999999}, session_token=token))
    assert payload["status"] == "NO_DATA"
    assert "999999" in payload["reason"]


def test_unknown_arguments_are_refused(chat_gateway, mint):
    token = mint("acme", "agent-a", allowed_tools=CHAT_FAMILY_NAMES)
    response = chat_gateway.call_tool(
        "agent.status", {"agent_id": "claude", "status": "ok"}, session_token=token
    )
    assert response["error"]["code"] == INVALID_PARAMS
    assert "does not declare the argument" in response["error"]["message"]


def test_an_out_of_window_limit_is_refused(chat_gateway, mint):
    token = mint("acme", "agent-a", allowed_tools=CHAT_FAMILY_NAMES)
    for value in (0, 201, -3):
        response = chat_gateway.call_tool("agent.list", {"limit": value}, session_token=token)
        assert response["error"]["code"] == INVALID_PARAMS, value
        assert "must be within 1..200" in response["error"]["message"]


def test_missing_required_argument_is_refused_by_the_pipeline(chat_gateway, mint):
    token = mint("acme", "agent-a", allowed_tools=CHAT_FAMILY_NAMES)
    response = chat_gateway.call_tool("ticket.get", {}, session_token=token)
    assert response["error"]["code"] == INVALID_PARAMS
    assert "missing required arguments" in response["error"]["message"]


def test_the_family_reads_a_real_ticket_payload(chat_gateway, mint):
    token = mint("acme", "agent-a", allowed_tools=CHAT_FAMILY_NAMES)
    payload = _payload(chat_gateway.call_tool("ticket.get", {"number": 504}, session_token=token))
    fragment = payload["fragments"][0]
    assert fragment["payload"]["number"] == 504
    assert fragment["payload"]["blocked_by"] == [501]
    # a board fragment never carries a timestamp into a cacheable prefix
    assert "closed_at" not in fragment["payload"]
    assert payload["citations"] == [fragment["sourceId"]]
