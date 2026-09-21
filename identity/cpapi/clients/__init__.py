"""Control-plane typed client (issue #38 AC4).

``ControlPlaneClient`` speaks the wire contract in ``../openapi.yaml`` over an
injected transport. See ``README.md`` for how transports are injected and how
richer clients can be generated from the spec at build time.


---knowledge---
module_id: identity.cpapi.clients
system: identity
app: cpapi
solution_class: class
patterns: [package-contract]
derives_from: null
owner_sme: platform-sme
tier: L0
interfaces: [ControlPlaneClient]
invariants: ""
gotchas: ""
related: ["#38"]
do_not_duplicate: null
---knowledge---
"""

from .control_plane_client import ControlPlaneClient, Transport

__all__ = ["ControlPlaneClient", "Transport"]
