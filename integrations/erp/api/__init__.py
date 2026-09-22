"""The ERP module's REST surface (ERP-06, issue #651, EPIC #645).

An OpenAPI-declared REST API over the indexer-fed document model of ERP-02, so the
portal (ERP-07, #652) and external consumers integrate through one contract. The
package is a front door over seven modules:

* :mod:`.openapi` — the document, emitted from ``integrations/erp/core/schemas`` and
  from the route table and the refusal vocabulary; nothing in it is hand-written,
  and the committed ``openapi.json`` is compared to a fresh emission byte for byte.
* :mod:`.routes` — the route table, whose kind vocabulary and transition actions are
  derived from the model rather than enumerated.
* :mod:`.surface` — the transport-free dispatcher: route, kind, caller,
  authorization, body, model — in that order, with one call into the auth layer.
* :mod:`.store` — the validating document store; every write goes through ERP-02.
* :mod:`.errors` — the boundary refusals, the mapping onto the ERP-08 vocabulary, and
  the model's own statuses read out of the model's factories.
* :mod:`.health` — real readings of the dependencies the surface names.
* :mod:`.provenance` — the GR-10 harvest record for what this lane cannibalized.

Three properties the lane is built on, each measured rather than promised:

1. **The document is generated.** Every component is an ERP-02 schema with only its
   ``$ref`` targets rewritten, and the gate refuses an artifact that differs from a
   fresh emission — so "never hand-maintained" is a diff, not a claim.
2. **Authorization is delegated, and there is no back door.**
   :meth:`~integrations.erp.api.surface.Surface.authorize_request` is the module's
   only call into ``integrations/erp/auth``, the request's tenant is always the
   principal's own, no parameter anywhere could name a tenant, and the gate mutates
   the authorizer to prove the driver notices.
3. **A refusal is refused by name, or it does not ship.**
   :mod:`.negative_control` provokes every refusal the surface can deliver, and
   declares — with the mechanism — every one it cannot.

Nothing here opens a socket, reads a clock or touches the network: the module is
offline, deterministic and exercised entirely by its own gate.

---knowledge---
module_id: integrations.erp.api.__init__
system: integrations
app: erp
solution_class: enterprise
patterns: []
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: []
invariants: ""
gotchas: ""
related: ["#1910"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

from . import errors, fixtures, health, openapi, provenance, routes, store, surface  # noqa: F401
from .errors import BOUNDARY_CODES, STATUSES, SurfaceError  # noqa: F401
from .openapi import EMITTED_ARTIFACT, build_document, emit, serialize, validate_document  # noqa: F401
from .routes import ACTION_PERMISSION, API_PREFIX, TABLE, Route, RouteTable  # noqa: F401
from .store import DocumentStore  # noqa: F401
from .surface import SCOPE_TEAM, Call, Surface  # noqa: F401

__all__ = [
    "ACTION_PERMISSION",
    "API_PREFIX",
    "BOUNDARY_CODES",
    "Call",
    "DocumentStore",
    "EMITTED_ARTIFACT",
    "Route",
    "RouteTable",
    "SCOPE_TEAM",
    "STATUSES",
    "Surface",
    "SurfaceError",
    "TABLE",
    "build_document",
    "emit",
    "errors",
    "fixtures",
    "health",
    "openapi",
    "provenance",
    "routes",
    "serialize",
    "store",
    "surface",
    "validate_document",
]
