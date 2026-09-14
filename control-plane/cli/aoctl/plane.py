"""The plane client — it speaks **only** the RC-3 control API.

Three rules, and this module is where they are enforced:

**One transport, the boundary's own.** Every call goes through
``integrations/paperclip/client.py`` (ADR-0016, consumed by ADR-0025 D5). The
CLI imports its ``Transport`` seam, its ``HttpTransport`` and its typed errors and
re-implements none of it: no ``urllib`` call, no second opinion about headers, no
second retry policy. The seam's ``FixtureTransport`` is what lets the CLI's whole
suite — and therefore the gate — run without touching the network.

**One address, and it is the existing console.** The plane is
``POST /api/control/<family>/<action>`` on the console app the fleet already runs
(``portal/server/control_api.py``; RC-3). There is no second listener, so the
default address is the console's own bind — ``127.0.0.1:8787`` — and
``--plane`` is how an operator points at a declared, flag-gated exposure instead
(ADR-0025 D1.5).

**One caller, and it is the console session.** The request carries the
``os-session-token`` cookie and nothing else: no bearer token, no machine
identity, no service account (ADR-0025 D2). The cookie's name is read from the
module that declares it (``contract``), and the token itself is attached by
:meth:`Plane.send` — the last moment before the wire — so a ``--dry-run`` request
object cannot carry a credential at all.

What this module deliberately does **not** do: it never inspects the fleet's
files, never signals a process, and never writes anything — the only side effect a
command can have is the plane applying it.
"""

from __future__ import annotations

import re
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:  # the seam is a repo-root package
    sys.path.insert(0, str(REPO_ROOT))

from integrations.paperclip.client import HttpTransport, Transport  # noqa: E402
from integrations.paperclip.model import PaperclipError  # noqa: E402

from . import contract  # noqa: E402
from .receipt import replay_receipt  # noqa: E402
from .refusals import Refusal, local_refusal, named_refusal  # noqa: E402
from .vocabulary import Row  # noqa: E402

#: The path prefix the seam insists on, and the route root RC-3 serves.
API_PREFIX = "/api"
ROUTE_ROOT = "control"
#: The console's own bind — the one app that serves the control family.
DEFAULT_PLANE = "http://127.0.0.1:8787"
#: A control command is one-shot: short enough to be an operator's wait, long
#: enough to cover a lever the registry bounds. A long-lived verb is served by
#: the SSE surface instead (RC-3's own note on ``fleet.watch``).
DEFAULT_TIMEOUT = 30.0
#: The command id this client mints is the caller's own key (RC-3: "the caller's
#: commandId when it sent one"). RC-4 binds it, so it is a durable key: full
#: width, and prefixed to say which side minted it.
COMMAND_ID_PREFIX = "aoc_"

#: The console's envelope, as the seam renders it into the error it raises:
#: ``error_for_status(status, path, str({"code": …, "message": …}))``. Reading
#: the code back is what makes a refusal **named** on the wire the plane actually
#: used; it is honoured only when the matrix allows it at that status (see
#: ``refusals``).
_ENVELOPE_CODE = re.compile(r"'code'\s*:\s*'([A-Za-z0-9_]+)'")
#: A seam that carried a bare error string instead of the envelope object.
_BARE_CODE = re.compile(r"HTTP \d{3} — ([a-z][a-z0-9_]*)\s*$")


def mint_command_id() -> str:
    """A fresh command id for one invocation (the operator can pin one instead)."""
    return f"{COMMAND_ID_PREFIX}{uuid.uuid4().hex}"


def path_for(row: Row) -> str:
    """The RC-3 route one declared command id is addressed by.

    Derived from the registry row's own id: ``<family>.<action>`` becomes
    ``POST /api/control/<family>/<action>``. The CLI holds no route table of its
    own, so a verb cannot be addressed that the registry does not declare.
    """
    return f"{API_PREFIX}/{ROUTE_ROOT}/{row.family}/{row.action}"


def envelope_code(message: str) -> Optional[str]:
    """The machine code the plane's own envelope carried, or ``None``."""
    if not message:
        return None
    found = _ENVELOPE_CODE.search(message)
    if found:
        return found.group(1)
    found = _BARE_CODE.search(message.strip())
    return found.group(1) if found else None


@dataclass(frozen=True)
class Request:
    """The request the CLI would send — address and body, and no credential.

    The session is added in :meth:`Plane.send`, so this object can be printed,
    logged or asserted on without leaking a token.
    """

    method: str
    path: str
    body: Mapping[str, Any]

    def as_json(self) -> dict[str, Any]:
        return {"method": self.method, "path": self.path, "body": dict(self.body)}


class Plane:
    """The RC-3 control API, over the boundary's transport seam."""

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_PLANE,
        transport: Optional[Transport] = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.base_url = (base_url or DEFAULT_PLANE).rstrip("/")
        self.timeout = float(timeout)
        self._injected = transport
        self._built: Optional[Transport] = None

    @property
    def transport(self) -> Transport:
        """The seam: the injected one, else the boundary's own ``HttpTransport``."""
        if self._injected is not None:
            return self._injected
        if self._built is None:
            if not self.base_url.lower().startswith(("http://", "https://")):
                raise local_refusal(
                    "address_invalid",
                    f"{self.base_url!r} is not an http(s) plane address — the console "
                    "serves the control family over http",
                )
            try:
                self._built = HttpTransport(self.base_url, timeout=self.timeout)
            except ValueError as exc:
                raise local_refusal(
                    "address_invalid",
                    f"{self.base_url!r} is not a plane address this client can use: {exc}",
                ) from exc
        return self._built

    # -- the request --------------------------------------------------------
    def request_for(
        self, row: Row, *, command_id: str, args: Sequence[str] = ()
    ) -> Request:
        """The request one declared command id becomes (pure, sends nothing).

        The body is RC-3's shape and nothing more: ``commandId`` (the caller's
        key, which RC-4 binds to the effect) and ``args`` (forwarded verbatim to
        the lever, whose own parser owns their meaning).
        """
        return Request(
            method="POST",
            path=path_for(row),
            body={"commandId": command_id, "args": [str(item) for item in args]},
        )

    def describe(self, request: Request, *, session_present: bool) -> dict[str, Any]:
        """The ``--dry-run`` document: exactly what would go on the wire.

        The session value is never rendered — only whether one is configured —
        because this document is printed to a shared terminal.
        """
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if session_present:
            headers["Cookie"] = f"{contract.session_cookie_name()}=<set>"
        else:
            headers["Cookie"] = "(none — no console session configured)"
        payload = request.as_json()
        payload["url"] = f"{self.base_url}{request.path}"
        payload["headers"] = headers
        return payload

    # -- the wire -----------------------------------------------------------
    def send(self, request: Request, *, session: str = "") -> dict[str, Any]:
        """Deliver one request and return the plane's receipt, or refuse.

        Every failure is a :class:`Refusal` — a transport that cannot connect, a
        plane that answered a refusal, an envelope this client cannot read. The
        method has exactly one way to return: a receipt the plane produced.
        """
        headers: dict[str, str] = {}
        if session:
            try:
                headers["Cookie"] = f"{contract.session_cookie_name()}={session}"
            except contract.ContractUnavailable as exc:
                raise local_refusal("contract_unavailable", str(exc)) from exc
        try:
            response = self.transport.request(
                request.method,
                request.path,
                json=dict(request.body),
                headers=headers,
            )
        except Refusal:
            raise
        except PaperclipError as exc:
            raise self._refusal(exc) from exc
        except OSError as exc:
            raise named_refusal(
                status=0, detail=f"{type(exc).__name__}: {exc} ({self.base_url})"
            ) from exc
        except ValueError as exc:
            raise local_refusal("address_invalid", str(exc)) from exc
        return self._data(response)

    def _refusal(self, exc: PaperclipError) -> Refusal:
        """The named refusal one raised seam error carries.

        On a ``409`` the message may carry the **original** receipt after the
        marker RC-4 documents; it is parsed and attached so a replay is reported
        as the effect that already happened rather than as a bare failure.
        """
        status = int(getattr(exc, "status", 0) or 0)
        message = str(exc)
        refusal = named_refusal(
            status=status, envelope_code=envelope_code(message), detail=message
        )
        if status == 409:
            refusal = refusal.with_receipt(replay_receipt(message))
        return refusal

    @staticmethod
    def _data(response: Any) -> dict[str, Any]:
        """The receipt out of the console's success envelope.

        ``{"ok": true, "status": 200, "requestId", "data": …, "error": null}`` is
        the console's shape (``portal/server/app.py::_ok``). Anything else is
        CANNOT-ASSESS: the CLI will not read a receipt out of a shape it does not
        recognise, because a guessed receipt is a reported effect that may not
        have happened.
        """
        body = getattr(response, "body", None)
        status = int(getattr(response, "status", 0) or 0)
        if not isinstance(body, Mapping):
            raise local_refusal(
                "plane_malformed",
                f"the plane answered {status} with {type(body).__name__}, not the "
                "console envelope",
            )
        if body.get("ok") is not True:
            raise local_refusal(
                "plane_malformed",
                f"the plane answered {status} with ok={body.get('ok')!r} and no refusal",
            )
        data = body.get("data")
        if not isinstance(data, Mapping):
            raise local_refusal(
                "plane_malformed",
                f"the plane's {status} envelope carried no `data` object to use "
                "as a receipt",
            )
        return dict(data)
