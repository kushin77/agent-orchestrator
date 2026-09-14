"""Cross-boundary auth for the paperclip seam (issue #412, ADR-0013).

The upstream operator surface authenticates with an agent key/JWT or a board
token; the fleet has its own identity records and a ``correlation_id`` run rail.
This package makes the two models meet **once**, at the process boundary:

* :mod:`~integrations.paperclip.auth.registry` — mint and verify an agent key
  bound to a **registered agent**, carrying the company scope in the claim; no
  parallel identity store (ADR-0012).
* :mod:`~integrations.paperclip.auth.board` — map a human operator onto the
  fleet's existing board session path, so no third login is introduced.
* :mod:`~integrations.paperclip.auth.runbridge` — bridge the upstream
  ``X-Paperclip-Run-Id`` to the fleet's own ``correlation_id``.
* :mod:`~integrations.paperclip.auth.guard` — the request pipeline that resolves
  a caller and refuses, by name, every negative control.

Stdlib-only (``hmac``/``hashlib``/``base64``), so the gate and the tests need no
third-party dependency and never touch the network. No secret value is stored,
written or echoed anywhere in this package (GR-6).
"""

from __future__ import annotations

__all__ = [
    "board",
    "guard",
    "jwt",
    "model",
    "policy",
    "registry",
    "runbridge",
]
