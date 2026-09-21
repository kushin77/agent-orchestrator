"""In-process transport: run the control-plane client against a ControlPlane.

Bridges the :class:`cpapi.clients.Transport` protocol to a
:class:`cpapi.control.ControlPlane` instance in the same process — the offline
way to exercise the full client -> envelope -> authN/authZ -> handler stack
with no network and no server socket. A deployment replaces this with an HTTP
transport (the client module is transport-agnostic by design).


---knowledge---
module_id: identity.cpapi.transport
system: identity
app: cpapi
solution_class: pattern
patterns: [offline-by-construction, dependency-inversion]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [InProcessTransport]
invariants: "the in-process transport exercises the full client -> envelope -> authN/authZ -> handler stack with no network"
gotchas: "a deployment replaces this with an HTTP transport; the client is transport-agnostic"
related: ["#38"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .control import ControlPlane


class InProcessTransport:
    """Transport that forwards requests to a ControlPlane in the same process."""

    def __init__(self, control_plane: ControlPlane) -> None:
        self._app = control_plane

    def request(
        self,
        method: str,
        path: str,
        *,
        body: Optional[Dict[str, Any]] = None,
        query: Optional[Dict[str, Any]] = None,
        token: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self._app.handle(
            method, path, body=body, query=query, token=token
        )
