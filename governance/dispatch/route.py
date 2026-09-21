"""Adapter bridging tier-space and capability-space dispatch, issue #1701.

``governance/dispatch/tiered.py`` routes in tier-space (``tier:L0/L1/L2`` ->
claude/deepseek). ``fleet/routing.py`` routes in capability-space
(``capability:*`` -> hermes/paperclip). Neither hands a task to the other, so
a task cannot flow claude -> deepseek -> hermes -> paperclip (issue #1268's
own definition of done). This module is the one adapter: it does not
reimplement either router, it only translates between their vocabularies so
a single task carrying both a ``tier`` and a ``capability`` requirement is
resolved through both, in order.

---knowledge---
module_id: governance.dispatch.route
system: governance
app: dispatch
solution_class: enterprise
patterns: [adapter, pure-function-core]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [resolve, tier_for_runtime]
invariants: "translates routing decisions only; transport (mailbox/dead-letter) is untouched, both routers already share it"
gotchas: ""
related: ["#1701", "#1268"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_PKG_DIR = Path(__file__).resolve().parent
_FLEET_DIR = _PKG_DIR.parent.parent / "fleet"
for _p in (_PKG_DIR, _FLEET_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import tiered  # noqa: E402
import routing  # noqa: E402 (fleet/routing.py — repo convention: namespace modules, not a package import)

#: fleet/routing.py's FinOps tier -> tiered.py's L0/L1/L2 tier, so a
#: capability-space route can hand off into the tier-space dispatcher.
FINOPS_TO_TIER = {"flash": "L0", "pro": "L1", "auditor": "L2"}

#: The capability-space runtimes fleet/routing.py can resolve to.
CAPABILITY_RUNTIMES = ("hermes", "paperclip")


def resolve(
    task: dict[str, Any],
    *,
    policy: dict | None = None,
    routing_policy: "routing.RoutingPolicy | None" = None,
) -> dict[str, Any]:
    """Resolve one task through both routers; return the runtime hop chain.

    ``task`` may carry a ``tier`` (L0/L1/L2, tier-space) and/or a
    ``capability`` (a fleet/routing.py capability id, capability-space). Each
    requirement present is resolved through its own router and appended to
    ``hops`` in order — tier first, then capability — so a task naming both
    flows claude/deepseek (tier-space) -> hermes/paperclip (capability-space).
    A task naming neither is a named refusal, never a silent default.
    """
    hops: list[dict[str, Any]] = []
    tier = task.get("tier")
    capability = task.get("capability")

    if tier:
        provider, model = tiered.resolve_model(tier, task.get("provider"), policy)
        hops.append({"runtime": provider, "model": model, "tier": tier})

    if capability:
        rp = routing_policy or routing.load()
        decision = rp.route(
            {"capability": capability, "lane": task.get("lane"), "title": task.get("title")}
        )
        persona = decision["persona"]
        if persona not in CAPABILITY_RUNTIMES:
            raise routing.RoutingRefusal(
                "runtime-not-capability-space",
                f"{persona!r} is not a capability-space runtime ({CAPABILITY_RUNTIMES})",
            )
        hops.append(
            {"runtime": persona, "capability": capability, "finops_tier": decision["tier"]}
        )

    if not hops:
        raise tiered.TieredRefusal(
            "route-empty", "task names neither a tier nor a capability — nothing to resolve"
        )
    return {"task": task, "hops": hops}


def tier_for_runtime(runtime: str, routing_policy: "routing.RoutingPolicy | None" = None) -> str | None:
    """The tiered.py L0/L1/L2 equivalent of a capability-space persona's FinOps tier.

    Lets a ``routing.py``-routed task that names a ``tier:`` requirement call
    into ``tiered.py``: resolve the persona's own FinOps tier through this
    function first, then ``tiered.resolve_model`` with the result. Returns
    ``None`` for a runtime fleet/routing.py does not register (not a
    capability-space persona).
    """
    rp = routing_policy or routing.load()
    if runtime not in rp.personas:
        return None
    persona_tier = str(rp.personas[runtime]["tier"])
    finops_tier = str(rp.finops["tier_map"][persona_tier])
    return FINOPS_TO_TIER.get(finops_tier)
