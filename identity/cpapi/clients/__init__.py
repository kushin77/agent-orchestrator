"""Control-plane typed client (issue #38 AC4).

``ControlPlaneClient`` speaks the wire contract in ``../openapi.yaml`` over an
injected transport. See ``README.md`` for how transports are injected and how
richer clients can be generated from the spec at build time.
"""

from .control_plane_client import ControlPlaneClient, Transport

__all__ = ["ControlPlaneClient", "Transport"]
