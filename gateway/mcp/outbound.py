"""Outbound MCP server management: registry, health, authz, pinned versions.

This module is the **outbound half** of the model-gateway pillar (issue #641,
workbook-10). It is deliberately *not* the inbound gateway: ``mcp.gateway``
exposes this platform's declared tools **to** external agents; this module
governs the third-party MCP servers this platform's own agents **call** (the
CTO workbook row: "automatically invoke draw.io MCP servers").

The two halves share the platform's enforcement vocabulary - a closed
allowlist, an injected authorization seam, an append-only hash-chained audit
ledger - but never share state: an outbound call is audited in its own ledger
with its own closed event kinds, and an inbound tool name can never resolve to
an outbound server or vice versa. Conflating them would let a server the
platform *calls* masquerade as a capability the platform *serves*, which is
exactly the confusion the allowlist doctrine exists to prevent.

What is declared per server (``outbound.config.json``)
------------------------------------------------------

- ``id`` - the stable **server id** callers reference. A prompt module names
  *this*, never a raw URL, so an endpoint move is a config change and not a
  prompt rewrite (the workbook-8 CTO rule; see :data:`DRAWIO_SERVER_ID`).
- ``endpoint`` - where the server actually lives. Declared, never accepted
  from a caller: a caller-supplied endpoint is refused rather than honoured,
  so a call cannot be redirected at an attacker's server.
- ``pinned_version`` - the exact version this platform will speak. A request
  naming any other version is **refused** (see :class:`PinViolationError`);
  there is no "latest" fallback and no floating tag.
- ``authz_scope`` - the permission a session must hold, checked through the
  injected :class:`~mcp.authz.PermissionGuard` (the same rbac seam the inbound
  gateway consumes - one authorization core, two surfaces).
- ``enabled`` - the per-entry flag. Every seeded entry ships **OFF** (GR-5: new
  surfaces ship flag-gated OFF); the registry-level flag
  (:data:`OUTBOUND_FLAG_ENV`) must also be ON, so enabling a deployment and
  enabling one server are two independent decisions.

Refusal posture
---------------

An unreachable server is a *graceful, audited refusal* - never an exception
that escapes the call path and never an invented result. :class:`OutboundCall`
returns an :class:`OutboundOutcome` whose ``status`` is a member of the closed
:data:`OUTBOUND_STATUSES` set, with a ``reason`` naming why; the offline
default probe reports every server unreachable, which is the honest answer for
a checkout with no network. A server that answers but whose version does not
match the pin is refused the same way. Both refusals are values, not crashes,
so a caller can branch on them and the audit ledger always records them.

---knowledge---
module_id: gateway.mcp.outbound
system: gateway
app: mcp
solution_class: enterprise
patterns: [separate-from-inbound, closed-allowlist, pinned-versions, append-only-audit]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [OutboundRegistry, OutboundServer, OutboundAuditLog, build_registry, load_config, PinViolationError, EndpointOverrideError]
invariants: "an inbound tool name can never resolve to an outbound server or vice versa: conflating the halves would let a called server masquerade as a served capability"
gotchas: "outbound calls are audited in their own ledger with their own closed event kinds"
related: ["#641"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Protocol, Tuple

from .audit import GENESIS_HASH, _chain_verify, _hash_record, now_utc
from .authz import PermissionGuard
from .model import SessionIdentity

# --------------------------------------------------------------------------- #
# the flag seam (mirrors kb.py's codeidx opt-in: compiled OFF, env can enable)
# --------------------------------------------------------------------------- #

#: Environment flag opting a deployment into outbound MCP calls. Absent or empty
#: means the compiled default - **OFF** - so a checkout that has not opted in
#: never dials a third-party server, however the entries are declared.
OUTBOUND_FLAG_ENV = "AO_MCP_OUTBOUND_ENABLED"
DEFAULT_OUTBOUND_ENABLED = False

#: The declared config carrying the seeded entries (this module's own config,
#: not the shared feature-flag registry: an outbound server is gateway/mcp's
#: declaration to own).
OUTBOUND_CONFIG_BASENAME = "outbound.config.json"

#: The permission an outbound call requires. Distinct from the inbound
#: ``tool:call``: calling a third-party server is a different authority from
#: serving this platform's tools, and a session holding one is not automatically
#: granted the other.
OUTBOUND_CALL_PERMISSION = "mcp:outbound"

#: The permission to *register* or *re-pin* a server (an administrative act).
OUTBOUND_ADMIN_PERMISSION = "mcp:outbound:admin"

#: The seeded draw.io server id. The CTO prompt module references this id (the
#: workbook-8 rule ``drawio.mcp_tool_list_cacheable``); a prompt that named a
#: raw URL instead would bypass this registry, so the id - not the endpoint - is
#: the contract.
DRAWIO_SERVER_ID = "drawio"

#: Closed status vocabulary of an outbound call outcome. ``ok`` is the only
#: success; every other member is a refusal a caller can branch on.
OUTBOUND_STATUSES: Tuple[str, ...] = (
    "ok",
    "disabled",
    "unknown_server",
    "authz",
    "pin_violation",
    "unreachable",
    "endpoint_override",
)

#: Closed event kinds of the outbound ledger (the inbound ledger keeps its own;
#: sharing an enum would let one surface's vocabulary widen the other's).
OUTBOUND_EVENT_KINDS: Tuple[str, ...] = ("mcp_outbound_call", "mcp_outbound_denied")


def env_outbound_enabled(environ: Optional[Mapping[str, str]] = None) -> bool:
    """Whether outbound MCP calls are opted in through the environment.

    An absent or empty value returns the compiled default (:data:`False`); the
    truthy spellings ``1``/``true``/``yes``/``on`` enable it and anything else
    leaves it OFF - the same seam ``kb.env_codeidx_enabled`` uses, so an
    operator has one flag idiom to learn.
    """
    env = os.environ if environ is None else environ
    raw = str(env.get(OUTBOUND_FLAG_ENV, "")).strip().lower()
    if not raw:
        return DEFAULT_OUTBOUND_ENABLED
    return raw in ("1", "true", "yes", "on")


# --------------------------------------------------------------------------- #
# errors
# --------------------------------------------------------------------------- #
class OutboundError(Exception):
    """Base for outbound-registry failures."""


class DuplicateServerError(OutboundError):
    """A server id was registered twice (the allowlist is a closed set)."""


class UnknownServerError(OutboundError):
    """A caller named a server id the registry does not declare."""


class PinViolationError(OutboundError):
    """A caller asked for a version other than the declared pin."""


class EndpointOverrideError(OutboundError):
    """A caller supplied an endpoint, trying to redirect a declared server."""


# --------------------------------------------------------------------------- #
# declarations
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class OutboundServer:
    """One declared outbound MCP server.

    ``endpoint`` is the declared location and is never accepted from a caller;
    ``pinned_version`` is the exact version this platform speaks, and a request
    for any other version is refused; ``authz_scope`` is the permission a
    session must hold before the call is attempted. ``enabled`` defaults to
    ``False`` so a server added without an explicit opt-in stays OFF.
    """

    id: str
    display_name: str
    endpoint: str
    pinned_version: str
    authz_scope: str = OUTBOUND_CALL_PERMISSION
    transport: str = "stdio"
    enabled: bool = False
    tools: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "tools", tuple(self.tools))
        if not self.id or not str(self.id).strip():
            raise OutboundError("an outbound server must declare a non-empty id")
        if not self.pinned_version or not str(self.pinned_version).strip():
            raise OutboundError(
                f"outbound server {self.id!r} must declare a pinned_version "
                "(a floating tag is not a pin)"
            )

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "displayName": self.display_name,
            "endpoint": self.endpoint,
            "pinnedVersion": self.pinned_version,
            "authzScope": self.authz_scope,
            "transport": self.transport,
            "enabled": self.enabled,
            "tools": list(self.tools),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "OutboundServer":
        return cls(
            id=str(payload["id"]),
            display_name=str(payload.get("displayName", payload["id"])),
            endpoint=str(payload.get("endpoint", "")),
            pinned_version=str(payload["pinnedVersion"]),
            authz_scope=str(payload.get("authzScope", OUTBOUND_CALL_PERMISSION)),
            transport=str(payload.get("transport", "stdio")),
            enabled=bool(payload.get("enabled", False)),
            tools=tuple(str(t) for t in payload.get("tools", ())),
        )


@dataclass(frozen=True)
class OutboundOutcome:
    """The result of one outbound call attempt (a value, never an exception).

    ``status`` is a member of :data:`OUTBOUND_STATUSES`; ``reason`` names why a
    refusal happened (empty when ``status == "ok"``); ``server_id`` is always
    populated, so a caller can attribute the outcome even for an
    ``unknown_server`` refusal.
    """

    server_id: str
    status: str
    reason: str = ""
    version: str = ""
    payload: Optional[Dict[str, Any]] = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    @property
    def refused(self) -> bool:
        return not self.ok

    def as_dict(self) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "serverId": self.server_id,
            "status": self.status,
            "reason": self.reason,
            "version": self.version,
        }
        if self.payload is not None:
            body["payload"] = self.payload
        return body


class HealthProbe(Protocol):
    """Injected reachability seam for one outbound server.

    Returns the server's reported version when it answers, or raises
    :class:`OutboundUnreachableError` when it cannot. The registry never dials
    anything itself: the offline default probe
    (:class:`DeclaredOfflineProbe`) reports every server unreachable, which is
    the honest answer for a checkout with no network, and a deployment injects
    a real probe.
    """

    def probe(self, server: OutboundServer) -> str: ...


class OutboundUnreachableError(OutboundError):
    """A server could not be reached (no socket, no process, timeout).

    Raised by a :class:`HealthProbe`; the registry turns it into an
    ``unreachable`` :class:`OutboundOutcome` - never an escaping exception and
    never an invented result (the no-false-green doctrine).
    """


@dataclass
class DeclaredOfflineProbe:
    """The default probe: every server is unreachable, and says so.

    This is not a stub that hides a gap - it is the truthful answer offline.
    A deployment that has a real transport injects its own probe; while this
    one is in place no outbound call can report ``ok``, so an unconfigured
    deployment cannot silently look healthy (AO-GR-19: no fake on a live path).
    """

    reason: str = (
        "no outbound transport is configured in this deployment (declared "
        "offline probe); a live probe must be injected to reach a server"
    )

    def probe(self, server: OutboundServer) -> str:  # noqa: ARG002 - by design
        raise OutboundUnreachableError(self.reason)


# --------------------------------------------------------------------------- #
# the registry
# --------------------------------------------------------------------------- #
class OutboundRegistry:
    """The declared, closed set of outbound MCP servers this platform may call.

    Registration is closed and duplicates are refused, exactly as the inbound
    :class:`~mcp.registry.ToolRegistry` refuses a tool declared twice: an id
    that could be overwritten is an id an attacker could shadow. Every lookup
    answers only for a declared server; an unknown id is a refusal, never a
    pass-through.

    ``enabled`` is the registry-level flag. While it is OFF - the compiled
    default - :meth:`call` refuses every request with ``status="disabled"``
    before any probe runs, so nothing is dialled. Turning it ON *and* the
    entry's own ``enabled`` ON are both required before a server is reachable:
    two decisions, so one flag can never widen reach on its own.
    """

    def __init__(
        self,
        *,
        enabled: Optional[bool] = None,
        probe: Optional[HealthProbe] = None,
        servers: Optional[Mapping[str, OutboundServer]] = None,
    ) -> None:
        self._enabled = env_outbound_enabled() if enabled is None else bool(enabled)
        self._probe: HealthProbe = probe if probe is not None else DeclaredOfflineProbe()
        self._servers: Dict[str, OutboundServer] = {}
        for server in (servers or {}).values():
            self.register(server)

    # ---- declaration -------------------------------------------------- #
    @property
    def enabled(self) -> bool:
        """Whether the outbound registry is opted in (OFF by default)."""
        return self._enabled

    def register(self, server: OutboundServer) -> "OutboundRegistry":
        """Declare one server; a duplicate id is refused, never overwritten."""
        if server.id in self._servers:
            raise DuplicateServerError(
                f"duplicate outbound server registration: {server.id!r} "
                "(a shadowed id is not a declaration)"
            )
        self._servers[server.id] = server
        return self

    def replace(self, server: OutboundServer) -> "OutboundRegistry":
        """Re-declare an existing server (the explicit re-pin path).

        Distinct from :meth:`register` so that a re-pin is a deliberate call and
        not something that happens by accident behind the duplicate guard.
        """
        if server.id not in self._servers:
            raise UnknownServerError(
                f"cannot re-declare unknown outbound server {server.id!r}"
            )
        self._servers[server.id] = server
        return self

    # ---- lookup ------------------------------------------------------- #
    def get(self, server_id: str) -> Optional[OutboundServer]:
        return self._servers.get(server_id)

    def require(self, server_id: str) -> OutboundServer:
        server = self._servers.get(server_id)
        if server is None:
            raise UnknownServerError(
                f"unknown outbound server {server_id!r} (not a declared server)"
            )
        return server

    def __contains__(self, server_id: object) -> bool:
        return server_id in self._servers

    def names(self) -> List[str]:
        """Declared server ids in sorted order (deterministic listing)."""
        return sorted(self._servers)

    def __len__(self) -> int:
        return len(self._servers)

    def servers(self) -> List[Dict[str, Any]]:
        """Every declaration in sorted id order (deterministic, redacted)."""
        return [self._servers[name].as_dict() for name in self.names()]

    def health(self) -> List[Dict[str, Any]]:
        """Probe every declared server; one row per server, in sorted id order.

        A row is never omitted because a probe failed: an unreachable server is
        reported ``unreachable`` with the probe's reason, so the health view
        answers for the *declared* set rather than for whatever happened to
        answer (a health view that can only show healthy servers is a
        formality).
        """
        rows: List[Dict[str, Any]] = []
        for name in self.names():
            server = self._servers[name]
            row: Dict[str, Any] = {
                "id": server.id,
                "pinnedVersion": server.pinned_version,
                "enabled": server.enabled and self._enabled,
                "status": "disabled" if not (server.enabled and self._enabled) else "unknown",
                "reason": "",
            }
            if not (server.enabled and self._enabled):
                row["reason"] = (
                    "server entry is OFF" if not server.enabled
                    else "outbound registry is OFF"
                )
                rows.append(row)
                continue
            try:
                observed = self._probe.probe(server)
            except OutboundUnreachableError as exc:
                row["status"] = "unreachable"
                row["reason"] = str(exc)
                rows.append(row)
                continue
            row["observedVersion"] = observed
            if observed != server.pinned_version:
                row["status"] = "pin_violation"
                row["reason"] = (
                    f"observed {observed!r} != pinned {server.pinned_version!r}"
                )
            else:
                row["status"] = "ok"
            rows.append(row)
        return rows

    # ---- the call path ------------------------------------------------ #
    def call(
        self,
        server_id: str,
        session: SessionIdentity,
        *,
        guard: Optional[PermissionGuard] = None,
        tool: str = "",
        arguments: Optional[Mapping[str, Any]] = None,
        version: Optional[str] = None,
        endpoint: Optional[str] = None,
        audit: Optional["OutboundAuditLog"] = None,
        requested_tenant: Optional[str] = None,
    ) -> OutboundOutcome:
        """Attempt one outbound call, returning a refused value or a result.

        The checks run in a fixed order so the *cause* of a refusal is always
        the first thing that failed, and every refusal is audited:

        1. **registry flag** - OFF means ``disabled`` (nothing is dialled);
        2. **declared server** - an unknown id is ``unknown_server``;
        3. **per-entry flag** - an OFF entry is ``disabled``;
        4. **endpoint override** - a caller-supplied endpoint is ``endpoint_override``
           (a caller may never redirect a declared server);
        5. **tenant scope** - a requested tenant that disagrees with the session
           is ``authz`` (no cross-tenant fallback);
        6. **authz** - the injected guard must allow the server's scope;
        7. **pin** - a requested version other than the pin is ``pin_violation``;
        8. **reachability** - an unreachable server is ``unreachable`` (graceful);
        9. **observed version** - a server answering off-pin is ``pin_violation``;
        10. otherwise ``ok`` with the probe's payload.
        """
        record = _make_auditor(audit, session, server_id, tool)

        def refuse(status: str, reason: str, declared: Optional[OutboundServer] = None) -> OutboundOutcome:
            record(status, reason, declared)
            return OutboundOutcome(
                server_id=server_id,
                status=status,
                reason=reason,
                version=declared.pinned_version if declared else "",
            )

        if not self._enabled:
            return refuse(
                "disabled",
                "outbound MCP calls are OFF in this deployment "
                f"({OUTBOUND_FLAG_ENV} is not truthy)",
            )

        server = self._servers.get(server_id)
        if server is None:
            return refuse(
                "unknown_server",
                f"unknown outbound server {server_id!r} (not a declared server)",
            )

        if not server.enabled:
            return refuse(
                "disabled",
                f"outbound server {server_id!r} is declared but its entry is OFF",
                server,
            )

        if endpoint is not None and endpoint != server.endpoint:
            return refuse(
                "endpoint_override",
                "a caller may not supply an endpoint; the declared server "
                "endpoint is authoritative (a redirect is refused, not honoured)",
                server,
            )

        if requested_tenant is not None and requested_tenant != session.tenant_id:
            return refuse(
                "authz",
                f"session tenant {session.tenant_id!r} may not call a server "
                f"scoped to {requested_tenant!r} (no cross-tenant fallback)",
                server,
            )

        if guard is not None:
            decision = guard.authorize(session, server.authz_scope)
            if decision.denied:
                return refuse(
                    "authz",
                    f"permission {server.authz_scope!r} denied "
                    f"(reason={decision.reason or 'denied'}, code={decision.code or 'denied'})",
                    server,
                )

        if version is not None and version != server.pinned_version:
            return refuse(
                "pin_violation",
                f"requested version {version!r} != pinned {server.pinned_version!r} "
                "(there is no floating fallback)",
                server,
            )

        try:
            observed = self._probe.probe(server)
        except OutboundUnreachableError as exc:
            return refuse("unreachable", str(exc), server)

        if observed != server.pinned_version:
            return refuse(
                "pin_violation",
                f"server {server_id!r} answered version {observed!r} != pinned "
                f"{server.pinned_version!r}",
                server,
            )

        payload = {
            "serverId": server.id,
            "tool": tool,
            "arguments": dict(arguments or {}),
            "version": observed,
        }
        record("ok", "", server, payload=payload)
        return OutboundOutcome(
            server_id=server.id,
            status="ok",
            version=observed,
            payload=payload,
        )


# --------------------------------------------------------------------------- #
# the outbound audit ledger
# --------------------------------------------------------------------------- #
class OutboundAuditLog:
    """Append-only hash-chained ledger for outbound MCP calls.

    The record shape is the same one ``mcp.audit`` consumes (monotonic ``seq``,
    RFC 3339 ``ts``, ``prevHash`` chaining, ``hash`` over the canonical body),
    but the closed event-kind vocabulary is this surface's own: an outbound
    call is not a ``tool_call`` on this platform's inbound surface, and sharing
    the enum would mean one surface's referee could widen the other's
    vocabulary. The chain helpers are imported rather than re-implemented, so
    both ledgers are verifiable by the same code and a divergence is a build
    error rather than a quiet drift.
    """

    def __init__(self) -> None:
        self._records: List[Dict[str, Any]] = []

    def append(
        self,
        event: str,
        *,
        status: str,
        tenant_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        actor: Optional[str] = None,
        detail: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if event not in OUTBOUND_EVENT_KINDS:
            raise OutboundError(
                f"unknown outbound event kind {event!r} "
                f"(closed set: {', '.join(OUTBOUND_EVENT_KINDS)})"
            )
        seq = len(self._records) + 1
        previous = self._records[-1]["hash"] if self._records else GENESIS_HASH
        record: Dict[str, Any] = {
            "seq": seq,
            "ts": now_utc(),
            "event": event,
            "status": status,
            "tenantId": tenant_id,
            "agentId": agent_id,
            "actor": actor,
            "detail": detail,
        }
        record["prevHash"] = previous
        record["hash"] = _hash_record(record)
        self._records.append(record)
        return dict(record)

    def events(self) -> List[Dict[str, Any]]:
        return [dict(record) for record in self._records]

    def verify(self) -> Tuple[int, str]:
        """Recompute the chain; raises ``AuditLogIntegrityError`` on tampering."""
        return _chain_verify(self._records)

    def __len__(self) -> int:
        return len(self._records)


def _make_auditor(
    audit: Optional[OutboundAuditLog],
    session: SessionIdentity,
    server_id: str,
    tool: str,
):
    """Return a closure appending exactly one record per call attempt.

    The closure answers whether the call succeeded by the *event kind* it
    writes: ``mcp_outbound_call`` for ``ok`` and ``mcp_outbound_denied`` for
    every refusal, so the ledger counts attempts and denials without the
    reader having to parse the status string.
    """

    def record(
        status: str,
        reason: str,
        server: Optional[OutboundServer] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        if audit is None:
            return
        event = "mcp_outbound_call" if status == "ok" else "mcp_outbound_denied"
        detail: Dict[str, Any] = {
            "serverId": server_id,
            "tool": tool,
            "status": status,
            "reason": reason,
            "pinnedVersion": server.pinned_version if server else None,
        }
        if payload is not None:
            detail["result"] = payload
        audit.append(
            event,
            status=status,
            tenant_id=session.tenant_id,
            agent_id=session.agent_id,
            actor=session.subject or session.agent_id,
            detail=detail,
        )

    return record


# --------------------------------------------------------------------------- #
# the declared config
# --------------------------------------------------------------------------- #
def load_config(path: Path) -> Dict[str, Any]:
    """Read ``outbound.config.json`` (the gateway/mcp-owned declaration)."""
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def config_path(root: Path) -> Path:
    return Path(root) / "gateway" / "mcp" / OUTBOUND_CONFIG_BASENAME


def _server_from_config(entry: Mapping[str, Any]) -> OutboundServer:
    return OutboundServer(
        id=str(entry["id"]),
        display_name=str(entry.get("displayName", entry["id"])),
        endpoint=str(entry.get("endpoint", "")),
        pinned_version=str(entry["pinnedVersion"]),
        authz_scope=str(entry.get("authzScope", OUTBOUND_CALL_PERMISSION)),
        transport=str(entry.get("transport", "stdio")),
        enabled=bool(entry.get("enabled", False)),
        tools=tuple(str(t) for t in entry.get("tools", ())),
    )


def seeded_config(root: Path) -> Dict[str, Any]:
    """The on-disk seed declaration (``gateway/mcp/outbound.config.json``)."""
    return load_config(config_path(root))


def build_registry(
    root: Optional[Path] = None,
    *,
    enabled: Optional[bool] = None,
    probe: Optional[HealthProbe] = None,
) -> OutboundRegistry:
    """Build the outbound registry from the declared config.

    With no ``root`` the seeded config is loaded from this checkout. The
    registry flag defaults to the compiled OFF value, so a registry built with
    no arguments declares the seeded draw.io entry **and refuses every call** -
    the fail-closed, opt-in posture the issue requires (an OFF default under
    AO-GR-6's named-exception clause).
    """
    if root is None:
        root = Path(__file__).resolve().parents[2]
    config = seeded_config(Path(root))
    servers = {
        entry["id"]: _server_from_config(entry) for entry in config.get("servers", ())
    }
    return OutboundRegistry(enabled=enabled, probe=probe, servers=servers)


#: Alias for the package-level export: ``mcp.__init__`` already re-exports the
#: *inbound* ``tools.build_registry``, so the outbound builder is published
#: under a name that cannot be mistaken for it. Inside this module the two are
#: never ambiguous (``from .outbound import build_registry`` is scoped), but at
#: the package boundary a single ``build_registry`` would be.
build_outbound_registry = build_registry
