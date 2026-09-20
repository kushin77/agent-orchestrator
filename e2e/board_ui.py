"""E2E probe: the console's board views (issue #1522).

Issue #1522 adds the **views** for two backends that already serve and are
already tested: the tenant task board (``GET /api/taskboard/tickets[/<id>]``,
``portal/server/task_board.py``) and the fleet board (``GET /api/board/rows``,
``portal/server/livestore.py``). The defect this family exists to catch is the
**built-but-unwired surface**: a view that exists as a file but that nothing
serves, or that names no backend route, renders nothing in production while
every backend test stays green.

So this probe asserts the two facts that make a view real, over the **real**
``ConsoleApplication`` (the same app ``httpd`` binds, driven through ``handle``
with no sockets), and each fact has a negative control that CAN fail:

* **served** — ``GET /views/<view>.html`` answers 200 with an HTML document.
  Negative control: a view that does not exist answers 404, so the serving
  probe is not vacuously true.
* **wired** — the served document names the backend route it must call, read
  from the served bytes (the wiring a browser would execute).
  Negative control: the same predicate over a copy of the document with the
  route reference removed must go **red**, so the wiring check measures the
  document rather than a constant.
* **the API behind it is real** — with the surface's flag flipped on for the
  probe, the board route returns the joined, schema-validated roster and the
  task board returns a ticket the engine actually ran; with the flag off both
  answer ``404 feature_disabled`` **before** authentication (GR-5).

Offline by construction: no sockets, no network, no real keys. It reads the
merged modules through their public APIs and never edits a pillar file.

Lane note: this module deliberately READS ``e2e/workbook11_portal.py`` (its
session-minting and ticket-driving helpers) rather than copying them —
cannibalize, do not duplicate — and never writes it, so the two sibling lanes
share no file.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from e2e._paths import REPO_ROOT, ensure_sys_paths

ensure_sys_paths()

# ``e2e/_paths.py`` exposes the pillars the golden path + workbook-11 views need.
# The board views consume two more, each under its own lane's bootstrap
# convention: ``packs`` (from ``registry/``) and ``core.tickets`` (from
# ``engine/``). Added here, idempotently, so the probe is runnable standalone.
for _rel in ("registry", "engine"):
    _path = str(Path(REPO_ROOT) / _rel)
    if _path not in sys.path:
        sys.path.insert(0, _path)

from e2e.workbook11_portal import (  # noqa: E402
    TENANT,
    TICKET_ID,
    _drive_a_ticket,
    _gate_session,
)

from portal.server.app import build_app  # noqa: E402
from portal.server.config_flags import (  # noqa: E402
    FLEET_BOARD_SURFACE,
    TASK_BOARD_SURFACE,
    surface_enabled,
)
from portal.server.livestore import BoardSurface  # noqa: E402
from portal.server.sso import ConsoleSso  # noqa: E402
from portal.server.task_board import TaskBoardSurface  # noqa: E402

#: The two frames this issue adds, and the backend route each must name in its
#: own served document. The route list is the wiring contract: a frame that
#: stops naming its route is a frame that renders nothing.
VIEW_FRAMES: dict[str, tuple[str, ...]] = {
    "taskboard": ("/api/taskboard/tickets", "/api/taskboard/moves/"),
    "board": ("/api/board/rows",),
}

#: The console shell's own navigation source (read, never written, by this lane).
SHELL_JS = "portal/static/js/console.js"


# --------------------------------------------------------------------------- #
# The app under test
# --------------------------------------------------------------------------- #
def _board_documents(tmp: Path) -> tuple[Path, Path]:
    """A committed-shaped ``.board`` pair: one good row, one the schema refuses.

    The corrupt row is deliberate: the board's honesty rule is that a malformed
    source row is served in ``rejected`` (named, with its defect) rather than
    dropped or best-effort served, and the probe asserts that behaviour.
    """
    snapshot = tmp / "snapshot.json"
    claims = tmp / "claims.jsonl"
    snapshot.write_text(
        json.dumps(
            {
                "generated_at": "2026-09-20T00:00:00Z",
                "source": "probe",
                "issues": [
                    {
                        "number": 1522,
                        "title": "console: add Task Board + fleet board views",
                        "state": "OPEN",
                        "labels": ["handoff:deepseek", "tier:L0"],
                    },
                    {
                        "number": 9,
                        "title": "weeks-old snapshot entry",
                        "state": "CLOSED",
                        "labels": [],
                    },
                    # 'state' is outside the schema's closed OPEN/CLOSED enum.
                    {"number": 2, "title": "corrupt", "state": "NOPE", "labels": []},
                ],
            }
        ),
        encoding="utf-8",
    )
    claims.write_text(
        json.dumps(
            {
                "event": "claim",
                "issue": 1522,
                "agent": "console-sme",
                "lane": "console",
                "at": "2026-09-20T20:00:00Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return snapshot, claims


def build_probe_app(*, enabled: bool, tmp: Path):
    """The real console with both board surfaces wired, and a gate session."""
    jwks, cookies = _gate_session()
    snapshot, claims = _board_documents(tmp)
    app = build_app(
        repo_root=REPO_ROOT,
        sso=ConsoleSso(jwks=jwks, root_admin_emails=("root@platform.example.com",)),
        board_surface=BoardSurface(
            repo_root=REPO_ROOT,
            enabled=enabled,
            snapshot_path=snapshot,
            claims_path=claims,
        ),
        task_board_surface=TaskBoardSurface(
            repo_root=REPO_ROOT,
            enabled=enabled,
            runtime=_drive_a_ticket(),
            ticket_ids=(TICKET_ID,),
            tenant=TENANT,
        ),
    )
    return app, cookies


def _read(app, path: str, *, cookies: dict | None = None) -> tuple[int, object]:
    """One request; anonymous unless a session cookie is supplied."""
    response = app.handle("GET", path, cookies=cookies or {})
    if response.is_json:
        payload = json.loads(response.as_bytes().decode("utf-8"))
    else:
        payload = response.as_bytes().decode("utf-8", "replace")
    return response.status, payload


# --------------------------------------------------------------------------- #
# The probes
# --------------------------------------------------------------------------- #
def probe_view_documents(tmp: Path) -> dict:
    """Each frame is served as HTML and names the backend route it must call."""
    app, _cookies = build_probe_app(enabled=True, tmp=tmp)
    observations: dict[str, dict] = {}
    for view, routes in VIEW_FRAMES.items():
        # A view frame is a static document: it is served without a session and
        # is NOT authorisation-gated (the API behind it is). Mirrors the SPoG
        # gate's row for /views/fleet.html.
        status, html = _read(app, f"/views/{view}.html")
        document = html if isinstance(html, str) else ""
        observations[view] = {
            "status": status,
            "isHtml": document.lstrip().startswith("<!DOCTYPE html>"),
            "bytes": len(document.encode("utf-8")),
            "wired": {route: route in document for route in routes},
        }
    return observations


def probe_absent_view_is_not_served(tmp: Path) -> dict:
    """Negative control: a frame that does not exist is a 404, not a 200."""
    app, _cookies = build_probe_app(enabled=True, tmp=tmp)
    status, _payload = _read(app, "/views/no-such-board-view.html")
    return {"status": status}


def probe_wiring_check_is_falsifiable(tmp: Path) -> dict:
    """Negative control: strip a route from the served bytes; the check must go red.

    This is the falsification the whole family needs. If ``"<route> in
    document`` were vacuously true (an empty read, a fallback document, a
    helper that never looks), the mutation below would still read as wired.
    Measured both ways, so the wiring assertion is provably load-bearing.
    """
    app, _cookies = build_probe_app(enabled=True, tmp=tmp)
    observations: dict[str, dict] = {}
    for view, routes in VIEW_FRAMES.items():
        status, html = _read(app, f"/views/{view}.html")
        document = html if isinstance(html, str) else ""
        route = routes[0]
        mutated = document.replace(route, "")
        observations[view] = {
            "servedStatus": status,
            "route": route,
            "wiredAsServed": route in document,
            "wiredWhenRouteStripped": route in mutated,
            "mutationActuallyChangedTheBytes": mutated != document,
        }
    return observations


def probe_shell_navigation(tmp: Path) -> dict:
    """Which of the two frames the console shell's own nav registers.

    The shell keeps two arrays (``NAV_TENANT`` for tenant-scoped views,
    ``NAV_GLOBAL`` for org-scoped ones). Registration is what puts a view on the
    rail; **absence from it does not make a frame unreachable** — the shell
    resolves ``?view=<id>`` to ``/views/<id>.html`` for any id, which the
    ``dispatchTemplate`` reading below proves. A mutation (the dispatch template
    removed from a copy) must move ``dispatchFalsifiable`` to False, so this
    reader is not vacuous either.
    """
    source = (Path(REPO_ROOT) / SHELL_JS).read_text(encoding="utf-8")
    tenant_block = source.split("var NAV_GLOBAL", 1)[0]
    global_block = source.split("var NAV_GLOBAL", 1)[1] if "var NAV_GLOBAL" in source else ""
    dispatch = '"/views/" + view + ".html"'
    registered = {}
    for view in VIEW_FRAMES:
        entry = f'id: "{view}"'
        registered[view] = {
            "inTenantNav": entry in tenant_block,
            "inGlobalNav": entry in global_block,
            "onRail": entry in tenant_block or entry in global_block,
        }
    return {
        "views": registered,
        "shellReadable": bool(tenant_block) and bool(global_block),
        "dispatchTemplate": dispatch,
        "dispatchPresent": dispatch in source,
        "dispatchFalsifiable": dispatch not in source.replace(dispatch, ""),
    }


def probe_flags_off_by_default(tmp: Path) -> dict:
    """Both surfaces are OFF in their declaration and refuse BEFORE authN."""
    app, _cookies = build_probe_app(enabled=False, tmp=tmp)
    observations: dict[str, dict] = {}
    routes = {
        TASK_BOARD_SURFACE: "/api/taskboard/tickets",
        FLEET_BOARD_SURFACE: "/api/board/rows",
    }
    for surface, route in routes.items():
        status, payload = _read(app, route)
        error = payload.get("error") if isinstance(payload, dict) else {}
        error = error or {}
        observations[surface] = {
            "route": route,
            "configDeclaresOff": not surface_enabled(REPO_ROOT, surface=surface),
            "status": status,
            "code": error.get("code"),
        }
    return observations


def probe_board_rows_serve_the_joined_roster(tmp: Path) -> dict:
    """The fleet board serves the joined roster AND names the row it refused."""
    app, cookies = build_probe_app(enabled=True, tmp=tmp)
    status, payload = _read(app, "/api/board/rows", cookies=cookies)
    data = payload.get("data") or {} if isinstance(payload, dict) else {}
    rows = data.get("rows") or []
    rejected = data.get("rejected") or []
    by_number = {row["number"]: row for row in rows}
    return {
        "status": status,
        "schema": data.get("schema"),
        "numbers": sorted(by_number),
        "claim": {
            "lane": by_number.get(1522, {}).get("lane"),
            "claimed_by": by_number.get(1522, {}).get("claimed_by"),
        },
        "unclaimedLane": by_number.get(9, {}).get("lane"),
        "rejectedIdentifiers": [entry.get("identifier") for entry in rejected],
        "rejectedErrors": [
            error for entry in rejected for error in (entry.get("errors") or [])
        ],
    }


def probe_task_board_replays_a_real_ticket(tmp: Path) -> dict:
    """The tenant task board serves a ticket the engine really ran to closed.

    The board holds no state of its own: the row is ``TicketRuntime``'s replay
    of the durable log, and the lifecycle vocabulary is attached from
    ``engine/core/tickets``' own ``lifecycle_order``.
    """
    app, cookies = build_probe_app(enabled=True, tmp=tmp)
    status, payload = _read(app, "/api/taskboard/tickets", cookies=cookies)
    data = payload.get("data") or {} if isinstance(payload, dict) else {}
    tickets = data.get("tickets") or []
    row = tickets[0] if tickets else {}
    moves_status, moves = _read(app, "/api/taskboard/moves/closed", cookies=cookies)
    moves_data = moves.get("data") or {} if isinstance(moves, dict) else {}
    return {
        "status": status,
        "schema": data.get("schema"),
        "tenant": data.get("tenant"),
        "lifecycle": data.get("lifecycle"),
        "ticketIds": [ticket.get("ticketId") for ticket in tickets],
        "state": row.get("state"),
        "closed": row.get("closed"),
        "reached": row.get("lifecycle"),
        "absent": data.get("absent"),
        "movesStatus": moves_status,
        "terminalMoves": moves_data.get("moves"),
        "dispatchLane": (row.get("dispatch") or {}).get("lane"),
        "findings": len((row.get("decomposition") or {}).get("findings") or []),
    }


def probe_task_board_refuses_a_foreign_tenant(tmp: Path) -> dict:
    """The task board is tenant-scoped: another tenant is refused, not served.

    This is what makes ``taskboard`` a ``NAV_TENANT`` view rather than a global
    one — the route reads ``?tenant=`` and the runtime is bound to one tenant,
    so a cross-tenant read is a ``403 tenant_mismatch`` instead of the wrong
    tenant's tickets.
    """
    app, cookies = build_probe_app(enabled=True, tmp=tmp)
    response = app.handle(
        "GET", "/api/taskboard/tickets", query={"tenant": "other"}, cookies=cookies
    )
    payload = json.loads(response.as_bytes().decode("utf-8"))
    error = payload.get("error") or {}
    return {"status": response.status, "code": error.get("code")}


PROBES = (
    ("view-documents", probe_view_documents),
    ("absent-view", probe_absent_view_is_not_served),
    ("wiring-falsifiable", probe_wiring_check_is_falsifiable),
    ("shell-navigation", probe_shell_navigation),
    ("flags-off-by-default", probe_flags_off_by_default),
    ("board-rows", probe_board_rows_serve_the_joined_roster),
    ("task-board", probe_task_board_replays_a_real_ticket),
    ("task-board-scope", probe_task_board_refuses_a_foreign_tenant),
)


def run_probes(tmp: Path) -> dict:
    """Run every probe and return the evidence document."""
    return {name: probe(tmp) for name, probe in PROBES}


def main() -> int:
    import tempfile

    with tempfile.TemporaryDirectory(prefix="ao-board-ui.") as work:
        print(json.dumps(run_probes(Path(work)), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - a manual entry point
    raise SystemExit(main())
