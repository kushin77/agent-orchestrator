"""The boundary permission vocabulary (issue #412).

Authorization at this seam is a single gate: a route declares the permission it
needs, the caller carries a derived permission set, and a caller without it is
refused **403 permission_denied** — never 404 (a known caller denied a known
route is not a missing resource). The vocabulary and the two-gate *shape*
(scope gate, then permission gate) are the merged ``identity/rbac`` /
``identity/cpapi`` convention; the sets here are the boundary's own since the
upstream surface has no fleet RBAC bindings.

An agent's permissions are **derived from its registry record**, not stored in
its token: the record is the single source of truth, so a capability removed
from ``registry/profiles/seeds`` stops granting authority on the next request.
"""

from __future__ import annotations

from typing import Iterable, Tuple

#: The closed permission set this boundary understands.
PERMISSIONS: Tuple[str, ...] = (
    "run:read",
    "run:write",
    "cost:read",
    "approval:request",
    "agent:read",
    "agent:mint",
)

#: Every agent may read runs and costs, and read agent records.
AGENT_BASELINE: Tuple[str, ...] = ("run:read", "cost:read")

#: A capability that authorises mutating an upstream run.
AGENT_WRITE_CAPABILITIES = frozenset(
    {
        "task-claim",
        "orchestrate",
        "code-author",
        "test-author",
        "test-run",
        "docs-authoring",
        "data-analysis",
        "infra-authoring",
        "research",
        "security-review",
    }
)

#: A tool that authorises mutating an upstream run.
AGENT_WRITE_TOOLS = frozenset({"file_write", "gh_pr", "gh_issue", "board_sync"})

#: The platform orchestrator capability: request budget top-ups (an approval).
AGENT_APPROVAL_CAPABILITY = "orchestrate"

#: An operator (the human board session) may do everything except mint identity
#: keys — minting is a platform act, so an operator token is refused there by
#: name (the "authenticated but not allowed" control).
OPERATOR_PERMISSIONS: Tuple[str, ...] = (
    "run:read",
    "run:write",
    "cost:read",
    "approval:request",
    "agent:read",
)


def is_permission(permission: str) -> bool:
    return permission in PERMISSIONS


def permissions_for_agent(
    capabilities: Iterable[str], tools: Iterable[str]
) -> Tuple[str, ...]:
    """Derive an agent's closed permission set from its registry record."""
    caps = set(capabilities)
    toolset = set(tools)
    granted = set(AGENT_BASELINE)
    if caps & AGENT_WRITE_CAPABILITIES or toolset & AGENT_WRITE_TOOLS:
        granted.add("run:write")
    if AGENT_APPROVAL_CAPABILITY in caps:
        granted.add("approval:request")
        granted.add("agent:read")
    return tuple(sorted(granted))


def permissions_for_operator(roles: Iterable[str]) -> Tuple[str, ...]:
    """Derive an operator's permission set from its board-session roles."""
    granted = set(OPERATOR_PERMISSIONS)
    return tuple(sorted(granted))
