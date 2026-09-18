"""Live hermes capability state (issue #889, lane L10).

``serve_live_capabilities`` is the read-only live counterpart to
:func:`integrations.hermes.mapping.build_projection` (the static, offline
projection): it calls the service through the existing ``HermesClient`` seam
(the ``Transport`` protocol — ``HttpTransport`` live, ``FixtureTransport``
offline, exactly as the rest of this adapter does), validates every returned
capability entry against the real ``capabilities.schema.json``, and reports
which declared (static) capabilities the live service confirms, which it
omits, and which extra ones it serves.

No-false-green: a live capability entry that fails schema validation is
refused BY NAME (:class:`CapabilityRejected`) — never silently dropped or
silently accepted.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from integrations.hermes.client import HermesClient
from integrations.hermes.mapping import build_projection, load_capabilities_schema, validate
from integrations.hermes.model import HermesError


class CapabilityRejected(Exception):
    """Raised when a live capability entry fails ``capabilities.schema.json``."""

    def __init__(self, capability: str, errors: list[str]):
        self.capability = capability
        self.errors = errors
        super().__init__(
            f"live capability {capability!r} rejected by capabilities.schema.json: "
            + "; ".join(errors)
        )


def serve_live_capabilities(root: Path | str, client: HermesClient) -> Dict[str, Any]:
    """Fetch, validate and diff the live capability state against the static projection.

    ``root`` is the repository root (mapping's paths are relative to it).
    ``client`` is a real ``HermesClient`` — pass one built with
    ``FixtureTransport`` for an offline, deterministic call.
    """
    root = Path(root)
    schema = load_capabilities_schema(root)

    try:
        response = client.capabilities()
    except HermesError as exc:
        return {
            "schema": "ao.integrations.hermes.sync/capability-state-v1",
            "reachable": False,
            "error": str(exc),
            "capabilities": {},
        }

    body = response.body if isinstance(response.body, dict) else {}
    for name, entry in body.items():
        errors = validate(entry, schema, path=f"$.{name}")
        if errors:
            raise CapabilityRejected(str(name), errors)

    static_caps = set((build_projection(root).get("tiering", {}).get("capabilities")) or {})
    live_caps = set(body.keys())

    return {
        "schema": "ao.integrations.hermes.sync/capability-state-v1",
        "reachable": True,
        "capabilities": body,
        # declared (persona-derived) capabilities the live service does not serve
        "live_missing": sorted(static_caps - live_caps),
        # capabilities the live service serves beyond the declared, static set
        "live_extra": sorted(live_caps - static_caps),
    }
