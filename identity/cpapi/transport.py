"""In-process transport: run the control-plane client against a ControlPlane.

Bridges the :class:`cpapi.clients.Transport` protocol to a
:class:`cpapi.control.ControlPlane` instance in the same process — the offline
way to exercise the full client -> envelope -> authN/authZ -> handler stack
with no network and no server socket. A deployment replaces this with an HTTP
transport (the client module is transport-agnostic by design).
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
