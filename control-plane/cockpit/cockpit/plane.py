"""The RC-3 control-API client the cockpit speaks through (issue #566).

The cockpit is a CLIENT of the control API, never an owner of fleet state, and
it holds no credential of its own (ADR-0025 D2, ADR-0026 D5.3). Everything a
command needs is assembled from the two RC siblings the cockpit consumes:

* the invocation is resolved by RC-10's registry
  (``cockpit_registry.resolve_call``) — the mnemonic, its declared endpoint and
  its parameters, so no ad-hoc verb exists anywhere in this package;
* the request is built and delivered by RC-5's client half (``aoctl.plane``) —
  its ``Plane``, its ``Request`` shape and its session-cookie attachment, so
  the cockpit re-implements no transport and no wire format (ADR-0016 /
  ADR-0025 D5).

:meth:`CockpitPlane.describe` is the ``--dry-run`` document: exactly what would
go on the wire, with the session value itself never rendered (the terminal may
be shared).
"""

from __future__ import annotations

from typing import Any

from . import _paths

#: The console's own bind — the one app that serves the control family
#: (consumed from ``aoctl.plane`` rather than re-declared here).
DEFAULT_PLANE = "http://127.0.0.1:8787"
DEFAULT_TIMEOUT = 30.0


def _plane_module() -> Any:
    _paths.ensure_paths()
    import aoctl.plane

    return aoctl.plane


class CockpitPlane:
    """A thin wrapper over ``aoctl.plane.Plane`` — no wire logic lives here."""

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_PLANE,
        transport: Any = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        plane_module = _plane_module()
        self._inner = plane_module.Plane(
            base_url=base_url or DEFAULT_PLANE, transport=transport, timeout=timeout
        )
        self.base_url = self._inner.base_url

    @property
    def transport(self) -> Any:
        """The seam: the injected one, else the boundary's own transport."""
        return self._inner.transport

    def request_for(self, call: Any, command_id: str) -> Any:
        """The RC-5 ``Request`` one resolved RC-10 call becomes.

        The body is RC-3's shape and nothing more: ``commandId`` (the caller's
        key, which RC-4 binds to the effect) and ``args`` (the declared
        parameters as the lever's own argv).
        """
        plane_module = _plane_module()
        return plane_module.Request(
            method="POST",
            path=call.endpoint_path,
            body={"commandId": command_id, "args": list(call.arguments)},
        )

    def describe(self, request: Any, *, session_present: bool) -> dict[str, Any]:
        """The ``--dry-run`` document — the session value is never rendered."""
        return self._inner.describe(request, session_present=session_present)

    def send(self, request: Any, *, session: str = "") -> dict[str, Any]:
        """Deliver one request; a refusal raises (the RC-5 client's contract)."""
        return self._inner.send(request, session=session)


def mint_command_id() -> str:
    """A fresh command id for one invocation (the caller's idempotency key)."""
    return _plane_module().mint_command_id()
