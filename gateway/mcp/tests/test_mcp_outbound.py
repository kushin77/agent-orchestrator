"""Outbound MCP server management: registry, health, authz, pinned versions.

Issue #641 (workbook-10) adds the **outbound** half of the model-gateway
pillar beside the inbound tool gateway: the platform managing the third-party
MCP servers its own agents call (the CTO workbook row names draw.io). These
tests exercise the controls the issue's acceptance criteria name, and each
refusal is asserted as a *value* (an ``OutboundOutcome``), never as an
escaping exception - a refusal a caller cannot branch on is a crash, and a
crash is not a control.

The suite is hermetic and offline: the default probe reports every server
unreachable, which is the honest answer with no transport, and the reachability
tests inject a probe rather than opening a socket.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcp import authn
from mcp.authz import AuthzDecision
from mcp.model import SessionIdentity
from mcp.outbound import (
    DEFAULT_OUTBOUND_ENABLED,
    DRAWIO_SERVER_ID,
    DuplicateServerError,
    EndpointOverrideError,
    OUTBOUND_CALL_PERMISSION,
    OUTBOUND_FLAG_ENV,
    OUTBOUND_STATUSES,
    OutboundAuditLog,
    OutboundError,
    OutboundRegistry,
    OutboundServer,
    OutboundUnreachableError,
    PinViolationError,
    UnknownServerError,
    build_registry,
    config_path,
    env_outbound_enabled,
    seeded_config,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
SIGNING_KEY = bytes(range(32))


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def session_for(tenant: str = "acme", agent: str = "cto-agent") -> SessionIdentity:
    """A verified session identity (no token round-trip needed for this seam)."""
    return SessionIdentity(tenant_id=tenant, agent_id=agent, subject=agent)


class AllowGuard:
    def authorize(self, session, permission):  # noqa: ARG002
        return AuthzDecision(allowed=True, permission=permission)


class DenyGuard:
    def authorize(self, session, permission):
        return AuthzDecision(
            allowed=False, permission=permission, reason="permission", code="denied"
        )


class ReachableProbe:
    """A probe that answers the pinned version (a server that is up and on-pin)."""

    def __init__(self, version: str) -> None:
        self.version = version
        self.calls = 0

    def probe(self, server: OutboundServer) -> str:
        self.calls += 1
        return self.version


class DownProbe:
    """A probe that reports the server unreachable (the graceful-refusal path)."""

    def __init__(self, reason: str = "connection refused") -> None:
        self.reason = reason

    def probe(self, server: OutboundServer) -> str:
        raise OutboundUnreachableError(self.reason)


def drawio_server(*, enabled: bool = True, version: str = "1.0.0") -> OutboundServer:
    return OutboundServer(
        id=DRAWIO_SERVER_ID,
        display_name="draw.io (diagrams.net) MCP server",
        endpoint="mcp+stdio://drawio-mcp-server",
        pinned_version=version,
        enabled=enabled,
        tools=("drawio.create_diagram", "drawio.export_svg"),
    )


# --------------------------------------------------------------------------- #
# acceptance criterion 1: register / health / authz / pin
# --------------------------------------------------------------------------- #
def test_registry_registers_and_lists_deterministically():
    registry = OutboundRegistry(enabled=False)
    registry.register(drawio_server())
    registry.register(
        OutboundServer(
            id="aaa-first",
            display_name="A",
            endpoint="mcp+stdio://a",
            pinned_version="0.1.0",
        )
    )
    assert registry.names() == sorted(registry.names())
    assert registry.names() == ["aaa-first", DRAWIO_SERVER_ID]
    assert len(registry) == 2


def test_duplicate_registration_is_refused_never_shadowed():
    """A shadowed id is not a declaration: the second register must refuse."""
    registry = OutboundRegistry(enabled=False)
    registry.register(drawio_server(version="1.0.0"))
    with pytest.raises(DuplicateServerError):
        registry.register(drawio_server(version="9.9.9"))
    # the original declaration is intact - the attempt changed nothing
    assert registry.require(DRAWIO_SERVER_ID).pinned_version == "1.0.0"


def test_unknown_server_refused_and_get_returns_none():
    registry = OutboundRegistry(enabled=True)
    assert registry.get("nope") is None
    with pytest.raises(UnknownServerError):
        registry.require("nope")


def test_a_server_without_a_pin_is_refused_at_declaration():
    """A floating tag is not a pin: the declaration itself must refuse."""
    with pytest.raises(OutboundError, match="pinned_version"):
        OutboundServer(
            id="floaty",
            display_name="Floaty",
            endpoint="mcp+stdio://floaty",
            pinned_version="",
        )


def test_health_reports_every_declared_server_even_when_unreachable():
    """A health view that can only show healthy servers is a formality."""
    registry = OutboundRegistry(enabled=True, probe=DownProbe("no socket"))
    registry.register(drawio_server(enabled=True))
    rows = registry.health()
    assert [row["id"] for row in rows] == [DRAWIO_SERVER_ID]
    assert rows[0]["status"] == "unreachable"
    assert rows[0]["reason"] == "no socket"


def test_health_names_off_entries_and_the_registry_flag():
    off_entry = OutboundRegistry(enabled=True, probe=ReachableProbe("1.0.0"))
    off_entry.register(drawio_server(enabled=False))
    assert off_entry.health()[0]["status"] == "disabled"
    assert "OFF" in off_entry.health()[0]["reason"]

    off_registry = OutboundRegistry(enabled=False, probe=ReachableProbe("1.0.0"))
    off_registry.register(drawio_server(enabled=True))
    assert off_registry.health()[0]["status"] == "disabled"
    assert "registry is OFF" in off_registry.health()[0]["reason"]


def test_health_flags_a_server_answering_off_pin():
    registry = OutboundRegistry(enabled=True, probe=ReachableProbe("2.0.0"))
    registry.register(drawio_server(version="1.0.0"))
    row = registry.health()[0]
    assert row["status"] == "pin_violation"
    assert "1.0.0" in row["reason"] and "2.0.0" in row["reason"]


def test_registry_and_entry_flags_are_independent_decisions():
    """One flag may never widen reach on its own (GR-5)."""
    registry = OutboundRegistry(enabled=False, probe=ReachableProbe("1.0.0"))
    registry.register(drawio_server(enabled=True))
    outcome = registry.call(DRAWIO_SERVER_ID, session_for(), guard=AllowGuard())
    assert outcome.status == "disabled"

    entry_off = OutboundRegistry(enabled=True, probe=ReachableProbe("1.0.0"))
    entry_off.register(drawio_server(enabled=False))
    assert entry_off.call(DRAWIO_SERVER_ID, session_for()).status == "disabled"

    both_on = OutboundRegistry(enabled=True, probe=ReachableProbe("1.0.0"))
    both_on.register(drawio_server(enabled=True))
    assert both_on.call(DRAWIO_SERVER_ID, session_for()).status == "ok"


# --------------------------------------------------------------------------- #
# acceptance criterion 2: draw.io seeded, flag-gated OFF
# --------------------------------------------------------------------------- #
def test_flag_compiles_off_and_env_truthiness_mirrors_the_codeidx_seam():
    assert DEFAULT_OUTBOUND_ENABLED is False
    assert env_outbound_enabled({}) is False
    assert env_outbound_enabled({OUTBOUND_FLAG_ENV: ""}) is False
    assert env_outbound_enabled({OUTBOUND_FLAG_ENV: "false"}) is False
    assert env_outbound_enabled({OUTBOUND_FLAG_ENV: "no"}) is False
    for truthy in ("1", "true", "YES", "On"):
        assert env_outbound_enabled({OUTBOUND_FLAG_ENV: truthy}) is True


def test_seeded_config_declares_drawio_off_and_the_registry_refuses_by_default():
    registry = build_registry(REPO_ROOT)
    assert registry.enabled is DEFAULT_OUTBOUND_ENABLED is False
    assert DRAWIO_SERVER_ID in registry
    seeded = registry.require(DRAWIO_SERVER_ID)
    assert seeded.enabled is False
    outcome = registry.call(
        DRAWIO_SERVER_ID, session_for(), guard=AllowGuard(), tool="drawio.list_templates"
    )
    assert outcome.status == "disabled"
    assert outcome.refused is True


def test_seeded_config_file_is_well_formed_and_off():
    config = seeded_config(REPO_ROOT)
    assert config_path(REPO_ROOT).is_file()
    ids = [entry["id"] for entry in config["servers"]]
    assert DRAWIO_SERVER_ID in ids
    for entry in config["servers"]:
        assert entry["enabled"] is False, "a seeded entry must ship flag-gated OFF"
        assert entry["pinnedVersion"], "every seeded entry must declare a pin"
        assert "://" not in entry["pinnedVersion"]


def test_no_seeded_endpoint_is_a_raw_url():
    """An endpoint is a declared local transport name, not a URL a caller reads."""
    config = seeded_config(REPO_ROOT)
    for entry in config["servers"]:
        endpoint = entry["endpoint"]
        assert not endpoint.startswith(("http://", "https://")), (
            "a declared outbound endpoint must not be a raw URL: the server ID "
            "is the contract, so an endpoint move stays a config change"
        )


# --------------------------------------------------------------------------- #
# acceptance criterion 3: the CTO prompt module references the server ID
# --------------------------------------------------------------------------- #
def _load_prompt_module():
    yaml = pytest.importorskip("yaml")
    path = REPO_ROOT / "registry" / "prompts" / "modules" / "cto-primary.v1.yaml"
    assert path.is_file(), f"the CTO prompt module is missing: {path}"
    with open(path, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def test_cto_prompt_module_references_the_registered_drawio_server_id():
    """The prompt module names the server ID, and that ID resolves here.

    This asserts *against* ``registry/prompts/**`` (read-only for this lane):
    the module is the consumer whose reference must resolve to the registry's
    declared id, so adding a server the prompt names but the registry does not
    declare - or renaming the registry id out from under the prompt - fails.
    """
    module = _load_prompt_module()
    registry = build_registry(REPO_ROOT)

    rule = module["enforcement"]
    attribute = rule["attribute"]
    # The workbook-8 attribute is namespaced by the server id: the module
    # refers to draw.io by id (``drawio.mcp_tool_list_cacheable``), not by an
    # endpoint. Derive the id from the reference and require it to resolve.
    referenced_id = str(attribute).split(".", 1)[0]
    assert referenced_id == DRAWIO_SERVER_ID, (
        "the CTO prompt module's draw.io reference must name the registered "
        f"server id {DRAWIO_SERVER_ID!r} (got {referenced_id!r})"
    )
    assert referenced_id in registry, (
        f"prompt module references server {referenced_id!r} which the outbound "
        "registry does not declare (an unresolved reference is a broken contract)"
    )

    # The module must not carry a raw URL: a URL in a prompt bypasses the
    # registry, so an endpoint move would silently keep calling the old host.
    serialized = json.dumps(module, default=str)
    assert "http://" not in serialized and "https://" not in serialized, (
        "the CTO prompt module must reference the server by id, never by URL"
    )


# --------------------------------------------------------------------------- #
# acceptance criterion 4: per-call audit + the two named refusals
# --------------------------------------------------------------------------- #
def test_unreachable_server_refuses_gracefully_and_never_crashes():
    """The named test: an unreachable server is a value, not an exception."""
    registry = OutboundRegistry(enabled=True, probe=DownProbe("connection refused"))
    registry.register(drawio_server(enabled=True))
    outcome = registry.call(
        DRAWIO_SERVER_ID, session_for(), tool="drawio.create_diagram"
    )
    assert outcome.status == "unreachable"
    assert outcome.refused is True
    assert "connection refused" in outcome.reason
    assert outcome.version == "1.0.0"  # the declared pin is still reported
    assert outcome.payload is None


def test_pin_violation_is_refused_for_a_requested_version():
    """The named test: a caller may not float off the declared pin."""
    registry = OutboundRegistry(enabled=True, probe=ReachableProbe("1.0.0"))
    registry.register(drawio_server(version="1.0.0"))
    outcome = registry.call(
        DRAWIO_SERVER_ID, session_for(), version="1.2.0", guard=AllowGuard()
    )
    assert outcome.status == "pin_violation"
    assert "1.2.0" in outcome.reason and "1.0.0" in outcome.reason


def test_pin_violation_is_refused_when_the_server_answers_off_pin():
    registry = OutboundRegistry(enabled=True, probe=ReachableProbe("2.0.0"))
    registry.register(drawio_server(version="1.0.0"))
    outcome = registry.call(DRAWIO_SERVER_ID, session_for(), guard=AllowGuard())
    assert outcome.status == "pin_violation"


def test_omitting_a_version_uses_the_pin_and_succeeds():
    registry = OutboundRegistry(enabled=True, probe=ReachableProbe("1.0.0"))
    registry.register(drawio_server(version="1.0.0"))
    outcome = registry.call(
        DRAWIO_SERVER_ID, session_for(), guard=AllowGuard(), tool="drawio.export_svg"
    )
    assert outcome.status == "ok"
    assert outcome.version == "1.0.0"
    assert outcome.payload["tool"] == "drawio.export_svg"
    assert outcome.payload["serverId"] == DRAWIO_SERVER_ID


def test_a_caller_supplied_endpoint_is_refused_not_honoured():
    """A redirect is refused: the declared endpoint is authoritative."""
    registry = OutboundRegistry(enabled=True, probe=ReachableProbe("1.0.0"))
    registry.register(drawio_server(enabled=True))
    outcome = registry.call(
        DRAWIO_SERVER_ID,
        session_for(),
        endpoint="mcp+stdio://evil.example",
        guard=AllowGuard(),
    )
    assert outcome.status == "endpoint_override"
    assert outcome.refused is True


def test_authz_denial_is_reported_with_its_cause():
    registry = OutboundRegistry(enabled=True, probe=ReachableProbe("1.0.0"))
    registry.register(drawio_server(enabled=True))
    outcome = registry.call(DRAWIO_SERVER_ID, session_for(), guard=DenyGuard())
    assert outcome.status == "authz"
    assert "denied" in outcome.reason


def test_a_cross_tenant_request_is_refused_with_no_fallback():
    registry = OutboundRegistry(enabled=True, probe=ReachableProbe("1.0.0"))
    registry.register(drawio_server(enabled=True))
    outcome = registry.call(
        DRAWIO_SERVER_ID,
        session_for("acme"),
        guard=AllowGuard(),
        requested_tenant="globex",
    )
    assert outcome.status == "authz"
    assert "cross-tenant" in outcome.reason


def test_unknown_server_call_is_a_value_not_a_raise():
    registry = OutboundRegistry(enabled=True, probe=ReachableProbe("1.0.0"))
    outcome = registry.call("not-declared", session_for(), guard=AllowGuard())
    assert outcome.status == "unknown_server"
    assert "not-declared" in outcome.reason


def test_every_status_is_from_the_closed_vocabulary():
    registry = OutboundRegistry(enabled=True, probe=DownProbe())
    registry.register(drawio_server(enabled=True))
    seen = {
        registry.call(DRAWIO_SERVER_ID, session_for()).status,
        OutboundRegistry(enabled=False).call("x", session_for()).status,
        registry.call("missing", session_for()).status,
    }
    assert seen <= set(OUTBOUND_STATUSES)


def test_every_call_appends_exactly_one_audit_record():
    registry = OutboundRegistry(enabled=True, probe=DownProbe("down"))
    registry.register(drawio_server(enabled=True))
    ledger = OutboundAuditLog()

    registry.call(DRAWIO_SERVER_ID, session_for(), tool="t", audit=ledger)
    registry.call(
        DRAWIO_SERVER_ID, session_for(), version="9.9.9", audit=ledger
    )
    assert len(ledger) == 2

    events = ledger.events()
    assert [record["event"] for record in events] == [
        "mcp_outbound_denied",
        "mcp_outbound_denied",
    ]
    assert events[0]["detail"]["serverId"] == DRAWIO_SERVER_ID
    assert events[0]["detail"]["tool"] == "t"
    assert events[0]["tenantId"] == "acme"
    assert events[1]["status"] == "pin_violation"
    # the chain is intact and each record links to its predecessor
    assert ledger.verify()[0] == 2
    assert events[1]["prevHash"] == events[0]["hash"]


def test_a_successful_call_audits_as_a_call_not_a_denial():
    registry = OutboundRegistry(enabled=True, probe=ReachableProbe("1.0.0"))
    registry.register(drawio_server(enabled=True))
    ledger = OutboundAuditLog()
    registry.call(DRAWIO_SERVER_ID, session_for(), guard=AllowGuard(), audit=ledger)
    record = ledger.events()[0]
    assert record["event"] == "mcp_outbound_call"
    assert record["status"] == "ok"
    assert record["detail"]["result"]["version"] == "1.0.0"


def test_outbound_audit_rejects_a_foreign_event_kind():
    """The outbound vocabulary is closed and distinct from the inbound one."""
    ledger = OutboundAuditLog()
    with pytest.raises(OutboundError, match="closed set"):
        ledger.append("tool_call", status="ok")


def test_outbound_audit_chain_detects_tampering():
    from mcp.audit import AuditLogIntegrityError

    ledger = OutboundAuditLog()
    ledger.append("mcp_outbound_call", status="ok", detail={"serverId": "x"})
    ledger._records[0]["status"] = "forged"  # tamper the body
    with pytest.raises(AuditLogIntegrityError):
        ledger.verify()


# --------------------------------------------------------------------------- #
# the outbound and inbound halves never share a namespace
# --------------------------------------------------------------------------- #
def test_an_inbound_tool_id_is_not_an_outbound_server_id():
    """Conflating the halves would let a called server masquerade as a served tool."""
    from mcp.model import MCP_ALLOWLIST_KEYS

    registry = build_registry(REPO_ROOT)
    assert set(registry.names()).isdisjoint(set(MCP_ALLOWLIST_KEYS))
    # the inbound permission is ``tool:call``; the outbound one is distinct, so
    # holding one is not holding the other.
    assert OUTBOUND_CALL_PERMISSION != authn.DEFAULT_ROLE
    assert OUTBOUND_CALL_PERMISSION == "mcp:outbound"


def test_seeded_entry_declares_the_outbound_scope_not_the_inbound_permission():
    registry = build_registry(REPO_ROOT)
    assert registry.require(DRAWIO_SERVER_ID).authz_scope == OUTBOUND_CALL_PERMISSION


def test_unknown_or_duplicate_errors_share_the_outbound_base():
    for exc in (DuplicateServerError, UnknownServerError, PinViolationError, EndpointOverrideError):
        assert issubclass(exc, OutboundError)
