"""integrations/hermes/sync — live capability state (issue #889, lane L10).

Calls the service's ``/api/capabilities`` endpoint through the existing
``HermesClient`` seam (:mod:`integrations.hermes.client` — ``HttpTransport``
live, ``FixtureTransport`` offline in tests), validates every returned entry
against the real ``capabilities.schema.json``
(:func:`integrations.hermes.mapping.validate`), and reports drift against the
static, offline projection (:func:`integrations.hermes.mapping.build_projection`).
A capability entry that fails schema validation is refused BY NAME
(no-false-green).
"""

from __future__ import annotations

from integrations.hermes.sync.live import CapabilityRejected, serve_live_capabilities

__all__ = ["serve_live_capabilities", "CapabilityRejected"]
