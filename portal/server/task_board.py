"""portal.server.task_board — the tenant task-board adapter (issue #642, workbook-11).

---knowledge---
module_id: portal.server.task_board
system: portal
app: server
solution_class: pattern
patterns: [cross-domain-aggregator, join-not-own]
derives_from: null
owner_sme: sync-sme
tier: L1
interfaces: [TaskBoardError, TaskBoardSurface]
invariants: ""
gotchas: ""
related: ["#642"]
do_not_duplicate: null
---knowledge---

WHY this exists: the workbook-3 lane ships the tenant ticket lifecycle as a
durable library (``engine.core.tickets.TicketRuntime``) — a ticket is *created →
decomposed → dispatched → executed → reviewed → closed*, and its state is a
replay of the engine's own event log, never a stored copy. A tenant operator
cannot read a library, so the board needs a serving half; the danger is that a
serving half is tempted to keep its own table of tickets, which would immediately
be a second, drifting source of truth.

Cannibalize, do not duplicate. This adapter holds **no ticket state at all**:

* every read is ``TicketRuntime.resume`` / ``.project`` over the engine's own
  event store, so a board row is a replay of the log the engine appended;
* the lifecycle vocabulary — the state names, their legal order and the legal
  moves — is ``engine.core.tickets.model``'s (``TicketState``,
  ``lifecycle_order``, ``legal_moves``), so the board cannot offer a transition
  the engine would refuse;
* the ticket's own fields (title, decomposition, dispatch, execution, review,
  outcomes) are the projection's, serialized by the projection's own accessors.

The adapter owns transport shape and the honesty rules of the board:

* **a ticket the log does not contain is absent.** An unknown or wrong-tenant
  ticket id is a 404, never an empty shell that would read as "exists, no
  activity".
* **a corrupt log fails closed.** ``TicketRuntime.project`` refuses a log that
  skips a state, revisits one or changes tenant mid-flight; that refusal surfaces
  as an error naming the ticket rather than as a plausible-looking row.
* **a tenant with no tickets is empty, not broken.** An empty board is a valid
  answer (a new tenant), and it is stated as an empty list, never as a fabricated
  row.

The surface ships **feature-flag-gated OFF** (GR-5): the flag is declared in the
portal's own ``portal/config/feature-flags.yaml`` and read here through
``portal.server.config_flags``; while it is off the app refuses every
``/api/taskboard/*`` route before authentication.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from portal.server.config_flags import TASK_BOARD_SURFACE, surface_enabled

#: The schema tag this surface emits.
SCHEMA = "ao.portal-task-board/v1"


class TaskBoardError(Exception):
    """An HTTP-addressable board error (``error.code``)."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _load_tickets_module():
    """Import ``core.tickets`` under the engine bootstrap (engine on path)."""
    try:
        from engine.core import tickets as module  # type: ignore
    except ImportError:  # pragma: no cover - depends on the caller's bootstrap
        from core import tickets as module  # type: ignore

    return module


class TaskBoardSurface:
    """Projects a tenant's ticket lifecycle over HTTP.

    ``ticket_ids`` names the tickets the board lists (the engine's store is
    keyed per ticket, so listing is by id — the id set is the board's one
    input, supplied by the caller or discovered from the store). Each read
    replays that ticket through the injected ``TicketRuntime``; nothing is
    cached between reads, so a ticket that advanced is never served stale.
    """

    def __init__(
        self,
        *,
        repo_root: Path | str,
        enabled: Optional[bool] = None,
        config_path: Optional[Path | str] = None,
        runtime: Optional[Any] = None,
        ticket_ids: Optional[Sequence[str]] = None,
        tenant: str = "",
    ) -> None:
        self.repo_root = Path(repo_root)
        self.config_path = (
            Path(config_path) if config_path is not None else None
        )
        if enabled is None:
            enabled = surface_enabled(
                self.repo_root,
                config_path=self.config_path,
                surface=TASK_BOARD_SURFACE,
            )
        self.enabled = bool(enabled)
        self._runtime = runtime
        self._ticket_ids = tuple(ticket_ids or ())
        self.tenant = tenant

    # -- reads ---------------------------------------------------------------
    def board(self, *, tenant: str = "") -> dict:
        """Every listed ticket for ``tenant``, each one a replayed projection.

        The vocabulary (the lifecycle order and the legal next moves) is the
        model's, attached once to the board so a client can render the state
        machine without hard-coding it. A tenant that owns no listed ticket
        serves an empty ``tickets`` list — valid, not broken.
        """
        runtime = self._require_runtime(tenant)
        module = _load_tickets_module()
        tickets: List[dict] = []
        absent: List[str] = []
        for ticket_id in self._ticket_ids:
            try:
                tickets.append(self._row(runtime, ticket_id))
            except TaskBoardError as exc:
                if exc.code != "not_found":
                    raise
                # A listed id the store does not hold is reported, not dropped:
                # one stale id must not hide the rest of the board, and it must
                # not silently vanish either.
                absent.append(ticket_id)
        return {
            "schema": SCHEMA,
            "tenant": runtime.tenant,
            "lifecycle": [state.value for state in module.lifecycle_order()],
            "tickets": tickets,
            "absent": absent,
        }

    def ticket(self, ticket_id: str, *, tenant: str = "") -> dict:
        """One ticket's projection; an unknown or foreign ticket is a 404."""
        runtime = self._require_runtime(tenant)
        if self._ticket_ids and ticket_id not in self._ticket_ids:
            raise TaskBoardError(
                404, "not_found", f"ticket {ticket_id!r} is not on this board"
            )
        return {
            "schema": SCHEMA,
            "tenant": runtime.tenant,
            "ticket": self._row(runtime, ticket_id),
        }

    def legal_moves(self, state: str, *, tenant: str = "") -> dict:
        """The model's own legal next states for ``state`` (no second table)."""
        module = _load_tickets_module()
        try:
            current = module.TicketState(state)
        except ValueError:
            raise TaskBoardError(
                400,
                "invalid_state",
                f"unknown ticket state {state!r}",
            ) from None
        return {
            "schema": SCHEMA,
            "state": current.value,
            "moves": [nxt.value for nxt in module.legal_moves(current)],
        }

    # -- internals -----------------------------------------------------------
    def _require_runtime(self, tenant: str) -> Any:
        if self._runtime is None:
            raise TaskBoardError(
                503,
                "board_unavailable",
                "no ticket runtime is wired for this board "
                "(engine/core/tickets is not reachable)",
            )
        if tenant and tenant != getattr(self._runtime, "tenant", tenant):
            # The runtime is bound to one tenant; a request for another tenant's
            # board is refused rather than served the wrong tenant's tickets.
            raise TaskBoardError(
                403,
                "tenant_mismatch",
                f"this board is bound to tenant "
                f"{getattr(self._runtime, 'tenant', '')!r}, not {tenant!r}",
            )
        return self._runtime

    @staticmethod
    def _row(runtime: Any, ticket_id: str) -> dict:
        """One row: the runtime's own replayed projection, serialized.

        The engine's store is read first, because an *empty* log is not a
        ticket: ``TicketRuntime.project`` over zero events yields a plausible
        ``created`` projection for any id you ask about, which on a board would
        read as "this ticket exists and has not started". A ticket the store
        does not hold is therefore ``absent`` (404), never an invented row.
        """
        try:
            records = runtime.engine.store.events(runtime.tenant, ticket_id)
        except Exception as exc:
            raise TaskBoardError(
                500,
                "corrupt_log",
                f"ticket {ticket_id!r} could not be read: {exc}",
            ) from None
        if not records:
            raise TaskBoardError(
                404,
                "not_found",
                f"tenant {runtime.tenant!r} has no ticket {ticket_id!r} in the "
                "engine's event store (an empty log is not a ticket)",
            )
        try:
            projection = runtime.resume(ticket_id)
        except Exception as exc:
            raise TaskBoardError(
                500,
                "corrupt_log",
                f"ticket {ticket_id!r} could not be replayed: {exc}",
            ) from None
        return _projection_row(projection)


def _projection_row(projection: Any) -> dict:
    """Serialize a ``TicketProjection`` through its own ``as_dict``.

    The projection owns its serialization; this adapter renames two keys to the
    board's camelCase transport shape and adds the derived
    ``closed``/``approved``/``lifecycle`` accessors the pane renders — it never
    re-implements a field.
    """
    row: Dict[str, Any] = projection.as_dict()
    row["ticketId"] = row.pop("ticket_id")
    row["correlationId"] = row.pop("correlation_id")
    row["closed"] = projection.closed
    row["approved"] = projection.approved
    row["lifecycle"] = list(projection.lifecycle())
    return row
