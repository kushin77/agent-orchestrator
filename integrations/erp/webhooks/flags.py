"""The feature flag gating this lane's surface (GR-5, epic #665 non-goals).

The ERP module ships OFF as a whole (``erp-module``, declared in
``integrations/erp/module.yaml``). This lane adds its **own** flag,
``erp-webhooks-bridge``, defaulting off, gating the bridge specifically —
because the epic's non-goals require every *new* surface a child lane adds to
ship flag-gated off, independent of whether the parent module flag is later
flipped. A tenant should not get live conversion→ledger posting merely because
someone turned the module on; this bridge needs its own, explicit promotion.

The promotion row lives at ``services.erp_webhooks_bridge`` in
``infra/feature-flags/registry.yaml`` with the matching terraform variable
``enable_erp_webhooks_bridge`` (both added by #995). Flipping it on still
belongs to a reviewed go-live; this file only declares the id and the
fail-closed default and never promotes the flag.

---knowledge---
module_id: integrations.erp.webhooks.flags
system: integrations
app: erp
solution_class: enterprise
patterns: []
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [is_enabled]
invariants: ""
gotchas: ""
related: ["#1910"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

from typing import Mapping, Optional

#: This lane's own flag id. Not registered in the central promotion registry
#: by this lane — see the module docstring.
FLAG_ID = "erp-webhooks-bridge"

#: Ships off. A malformed or missing flag map is treated as off, never on —
#: the same fail-closed posture the rest of this lane applies to auth and
#: schema.
DEFAULT_ENABLED = False


def is_enabled(flags: Optional[Mapping[str, bool]]) -> bool:
    """Whether the bridge is enabled, given a tenant's resolved flag map.

    ``flags`` is expected to be whatever the caller's flag-resolution layer
    produced (out of scope for this lane); if it is absent, not a mapping, or
    silent on this flag, the answer is the closed default: off.
    """
    if not isinstance(flags, Mapping):
        return DEFAULT_ENABLED
    value = flags.get(FLAG_ID, DEFAULT_ENABLED)
    return bool(value)
