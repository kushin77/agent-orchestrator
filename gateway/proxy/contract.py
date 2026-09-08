"""Gateway proxy contract — closed outcome vocabulary + error taxonomy (issue #16).

This module is the proxy lane's own contract-freeze surface: the closed set of
dispatch *outcomes* a ``dispatch()`` can return and the *error taxonomy* the
proxy raises.  The value objects live in ``model.py``; the injected seams the
core depends on are declared in ``resolver.py``/``backend.py``/``sinks.py``.

Nothing here imports a sibling lane: this file is the standalone vocabulary the
whole ``proxy`` package (and its downstream consumers, e.g. the phase-7
control-plane REST surface) shares.

Outcomes are a closed set — every terminal dispatch result carries exactly one.
Any outcome that is not ``success``/``cache_hit`` is an *explicit* non-success
and never carries fabricated typed content (no-false-green / never-silent-pass
doctrine).
"""

from __future__ import annotations

# --------------------------------------------------------------------------- #
# Closed dispatch-outcome vocabulary
# --------------------------------------------------------------------------- #
#: Served by a live provider with schema-validated typed output.
OUTCOME_SUCCESS = "success"
#: Served from the cost-control semantic cache (zero provider cost, accounted).
OUTCOME_CACHE_HIT = "cache_hit"
#: Token budget exhausted in enforce mode (backpressure queue/degrade) — a block,
#: never a silent success.
OUTCOME_BLOCKED = "blocked"
#: Rate limiter denied the call.
OUTCOME_RATE_LIMITED = "rate_limited"
#: Provider output refused by the output throttle (runaway-output guard).
OUTCOME_REFUSED = "refused"
#: Typed output failed schema validation twice (initial try + one retry).
OUTCOME_CANNOT_ASSESS = "cannot_assess"
#: Every candidate of the routed tier is unhealthy / absent — explicit failure.
OUTCOME_NO_HEALTHY_ROUTE = "no_healthy_route"
#: All healthy candidates failed at execution time (no typed output produced).
OUTCOME_FAILED = "failed"
#: The agent does not hold the capability the task type requires (boundary).
OUTCOME_DENIED = "denied"

OUTCOMES = frozenset(
    {
        OUTCOME_SUCCESS,
        OUTCOME_CACHE_HIT,
        OUTCOME_BLOCKED,
        OUTCOME_RATE_LIMITED,
        OUTCOME_REFUSED,
        OUTCOME_CANNOT_ASSESS,
        OUTCOME_NO_HEALTHY_ROUTE,
        OUTCOME_FAILED,
        OUTCOME_DENIED,
    }
)

#: Outcomes that mean the request was actually served with typed content.
SERVED_OUTCOMES = frozenset({OUTCOME_SUCCESS, OUTCOME_CACHE_HIT})


def outcome_is_served(outcome: str) -> bool:
    """Whether an outcome represents a served (typed) response."""
    return outcome in SERVED_OUTCOMES


# --------------------------------------------------------------------------- #
# Error taxonomy
# --------------------------------------------------------------------------- #


class ProxyError(Exception):
    """Base error for the model-gateway proxy."""


class AgentResolutionError(ProxyError):
    """The agent resolver could not resolve the requested agent."""


class TaskResolutionError(ProxyError):
    """The task resolver could not resolve the requested task type."""


class CapabilityDeniedError(ProxyError):
    """The agent does not hold the capability the task type requires."""


class BudgetBlockedError(ProxyError):
    """The FinOps chooser's per-tenant budget stopped the call (policy=stop)."""


class NoHealthyRouteError(ProxyError):
    """No candidate of the routed tier is healthy (injected health signal)."""


class RoutingConfigError(ProxyError):
    """The proxy routing policy is invalid (fail closed)."""


class UnknownTaskRouteError(RoutingConfigError):
    """The task type has no entry in the proxy routing policy."""
