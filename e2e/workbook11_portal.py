"""E2E probe: the workbook-11 portal views, joined over one request path.

Issue #642's acceptance criterion is a *portal* criterion, but the three views
are only worth anything if they render the platform's real declarations — and
the failure this probe guards against is the one every serving adapter is
tempted into: building a second copy of a fact it should have read.

This probe drives the real ``ConsoleApplication`` (the same app ``httpd`` binds,
driven through ``handle`` with no sockets) with the three workbook-11 surfaces
wired to the **real** producers, and asserts the served documents still carry
the producers' own values:

* ``/api/orgchart/chart`` renders ``registry/personas/org-chart.yaml`` through
  the registry's own ``validate_org_chart`` — node ids, edges, tiers and caps
  are the declaration's, and the chart is *resolved* (not served unresolved);
* ``/api/taskboard/tickets`` replays a ticket that the e2e control plane
  actually ran through ``engine/core/tickets`` to ``closed`` — the board's row
  is the engine's projection, not a fixture;
* ``/api/skillstudio/skills`` serves the workbook-9 studio's own catalog with
  its own category vocabulary;
* all three are **OFF by default** and answer ``404 feature_disabled`` before
  authentication, and each one renders once its flag is flipped — the GR-5
  contract, proven over the real app.

Offline by construction: no sockets, no network, no real keys. It reads the
merged modules through their public APIs and never edits a pillar file.

---knowledge---
module_id: e2e.workbook11_portal
system: e2e
app: portal
solution_class: enterprise
patterns: [no-false-green, offline-composition-root]
derives_from: e2e/wiring.py
owner_sme: qa-sme
tier: L1
interfaces: [workbook-11 portal views joined over one request path]
invariants: "served documents still carry the producers' own values, never a second copy"
gotchas: ""
related: ["#642"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from e2e._paths import REPO_ROOT, ensure_sys_paths

ensure_sys_paths()

# ``e2e/_paths.py`` exposes the pillars the *golden path* needs (gateway,
# identity, registry, guardrails). The workbook-11 views consume two more, each
# under its own lane's bootstrap convention: ``packs`` (from ``registry/``) and
# ``core.tickets`` (from ``engine/``). Added here, idempotently, so the probe is
# runnable on its own as well as under the e2e conftest.
for _rel in ("registry", "engine"):
    _path = str(Path(REPO_ROOT) / _rel)
    if _path not in sys.path:
        sys.path.insert(0, _path)

from portal.server.app import build_app  # noqa: E402
from portal.server.config_flags import (  # noqa: E402
    ORG_CHART_SURFACE,
    SKILL_STUDIO_SURFACE,
    TASK_BOARD_SURFACE,
    surface_enabled,
)
from portal.server.org_chart import OrgChartView  # noqa: E402
from portal.server.skill_studio import SkillStudioSurface  # noqa: E402
from portal.server.task_board import TaskBoardSurface  # noqa: E402

#: The workbook-11 route family, one representative read per surface.
SURFACE_ROUTES = {
    ORG_CHART_SURFACE: "/api/orgchart/chart",
    SKILL_STUDIO_SURFACE: "/api/skillstudio/skills",
    TASK_BOARD_SURFACE: "/api/taskboard/tickets",
}

TENANT = "acme"
TICKET_ID = "TCK-E2E-642"
MISSION_ID = "mission-tck-e2e-642"


# --------------------------------------------------------------------------- #
# The app under test
# --------------------------------------------------------------------------- #
def _studio():
    """The real workbook-9 ``SkillStudio`` over a real ``PackRegistry``."""
    from packs.registry import PackRegistry
    from packs.skills import SkillStudio

    return SkillStudio(PackRegistry())


def _drive_a_ticket() -> object:
    """Run one real ticket to ``closed`` and return its ``TicketRuntime``."""
    from core.events import InMemoryEventStore
    from core.namespaces import NamespaceRegistry
    from core.runtime import Engine
    from core.tickets import TicketRuntime, register_ticket_handlers, ticket_workflow

    from engine.multiagent.model import (
        FanOutPlan,
        HierarchyConfig,
        Lane,
        LaneRole,
        Subtask,
        ok_result,
    )
    from engine.multiagent.planner import HierarchicalPlanner
    from engine.multiagent.runner import ScriptedRunner

    registry = NamespaceRegistry()
    registry.create(TENANT, plan="standard")
    engine = Engine(store=InMemoryEventStore(), namespaces=registry)

    runner = (
        ScriptedRunner()
        .on("analyst-1", "t1", ok_result("analyst-1", "t1", output={"cause": "stale"}))
        .on("reviewer-1", "t2", ok_result("reviewer-1", "t2", output={"totals": "ok"}))
    )
    planner = HierarchicalPlanner(
        runner,
        planner_lane=Lane(lane_id="planner", role=LaneRole.PLANNER, agent_ids=("planner-1",)),
        specialist_lanes=(
            Lane(lane_id="analyst", role=LaneRole.SPECIALIST, agent_ids=("analyst-1",)),
            Lane(lane_id="reviewer", role=LaneRole.SPECIALIST, agent_ids=("reviewer-1",)),
        ),
        config=HierarchyConfig(max_escalation_rounds=2),
    )
    register_ticket_handlers(engine, decomposer=planner, max_escalation_rounds=2)
    tickets = TicketRuntime(engine, tenant=TENANT)

    def plan(ctx):
        return FanOutPlan(
            mission_id=ctx.mission.mission_id,
            planner_lane_id="planner",
            subtasks=(
                Subtask(
                    subtask_id="t1",
                    objective="diagnose the feed failure",
                    lane_id="analyst",
                    agent_id="analyst-1",
                ),
                Subtask(
                    subtask_id="t2",
                    objective="verify reconciliation totals",
                    lane_id="reviewer",
                    agent_id="reviewer-1",
                ),
            ),
        )

    spec = ticket_workflow(
        tenant=TENANT,
        ticket_id=TICKET_ID,
        mission_id=MISSION_ID,
        objective="restore the nightly reconciliation feed",
        decomposer=plan,
        dispatch_lane="analyst",
        review={"reviewed_by": "ops-lead", "approved": True, "rationale": "ok"},
    )
    tickets.run(TICKET_ID, spec, title="reconciliation feed down")
    return tickets


def _gate_session() -> tuple[dict, dict]:
    """Play the shared-frontend auth gate: return ``(jwks, cookie)``.

    The console issues no credential of its own — the auth gate does, and the
    console verifies it offline against the gate's published JWKS. The probe
    therefore holds the signing key, publishes the matching JWKS to the console,
    and mints a token the console will verify, exactly as
    ``portal/tests/conftest.py`` does. Offline: key generation is local and no
    JWKS is fetched over the network.
    """
    import time

    from identity.sso.jose import generate_rsa_keypair, jwks_for_keys
    from identity.sso.tokens import console_kid_for, issue_console_session_token
    from portal.server.sso import SESSION_COOKIE

    private_key, public_key = generate_rsa_keypair()
    kid = console_kid_for(public_key)
    token, _ = issue_console_session_token(
        private_key,
        kid=kid,
        tenant_id=TENANT,
        subject_id="root@platform.example.com",
        email="root@platform.example.com",
        name="root",
        role="admin",
        now=int(time.time()),
        ttl=3600,
    )
    return jwks_for_keys([(kid, public_key)]), {SESSION_COOKIE: token}


def _app(*, enabled: bool):
    """The console with the three workbook-11 surfaces wired to real producers."""
    from portal.server.sso import ConsoleSso

    jwks, cookies = _gate_session()
    tickets = _drive_a_ticket()
    return build_app(
        repo_root=REPO_ROOT,
        sso=ConsoleSso(
            jwks=jwks, root_admin_emails=("root@platform.example.com",)
        ),
        org_chart_view=OrgChartView(repo_root=REPO_ROOT, enabled=enabled),
        skill_studio_surface=SkillStudioSurface(
            repo_root=REPO_ROOT, enabled=enabled, studio=_studio()
        ),
        task_board_surface=TaskBoardSurface(
            repo_root=REPO_ROOT,
            enabled=enabled,
            runtime=tickets,
            ticket_ids=(TICKET_ID,),
            tenant=TENANT,
        ),
    ), cookies


def _read(app, path: str, *, cookies: dict | None = None) -> tuple[int, dict]:
    """One request; anonymous unless a session cookie is supplied."""
    response = app.handle("GET", path, cookies=cookies or {})
    payload = json.loads(response.as_bytes().decode("utf-8")) if response.is_json else {}
    return response.status, payload


def _write(
    app, path: str, body: dict, *, cookies: dict | None = None
) -> tuple[int, dict]:
    """One POST (a studio action); anonymous unless a session cookie is supplied."""
    response = app.handle("POST", path, body=body, cookies=cookies or {})
    payload = json.loads(response.as_bytes().decode("utf-8")) if response.is_json else {}
    return response.status, payload


# --------------------------------------------------------------------------- #
# The probes
# --------------------------------------------------------------------------- #
def probe_flags_off_by_default() -> dict:
    """Every workbook-11 surface is OFF, and refusal happens before authN."""
    app, _cookies = _app(enabled=False)
    observations = {}
    for surface, route in SURFACE_ROUTES.items():
        status, payload = _read(app, route)
        error = payload.get("error") or {}
        observations[surface] = {
            "route": route,
            "configDeclaresOff": not surface_enabled(REPO_ROOT, surface=surface),
            "status": status,
            "code": error.get("code"),
        }
    return observations


def probe_org_chart_renders_the_declaration() -> dict:
    app, cookies = _app(enabled=True)
    status, payload = _read(app, "/api/orgchart/chart", cookies=cookies)
    document = payload.get("data") or {}
    return {
        "status": status,
        "state": document.get("state"),
        "root": document.get("root"),
        "roleIds": [node.get("id") for node in document.get("nodes") or []],
        "tiers": {
            node.get("id"): node.get("defaultModelTier")
            for node in document.get("nodes") or []
        },
        "caps": {
            node.get("id"): node.get("monthlyBudgetCapUsd")
            for node in document.get("nodes") or []
        },
    }


def probe_task_board_replays_a_real_ticket() -> dict:
    app, cookies = _app(enabled=True)
    status, payload = _read(app, "/api/taskboard/tickets", cookies=cookies)
    document = payload.get("data") or {}
    tickets = document.get("tickets") or []
    row = tickets[0] if tickets else {}
    return {
        "status": status,
        "lifecycle": document.get("lifecycle"),
        "ticketIds": [ticket.get("ticketId") for ticket in tickets],
        "state": row.get("state"),
        "closed": row.get("closed"),
    }


def probe_skill_studio_serves_the_studio_catalog() -> dict:
    app, cookies = _app(enabled=True)
    status, payload = _read(app, "/api/skillstudio/skills", cookies=cookies)
    document = payload.get("data") or {}
    return {
        "status": status,
        "schema": document.get("schema"),
        "categories": document.get("categories"),
        "skillCount": len(document.get("skills") or []),
    }


def probe_portal_view_documents() -> dict:
    """The two new console view documents are served and wired to their backends.

    The console ships its views as static frames with no build step, so "the
    view exists and is reachable" is a fact about the served document, and "it
    is wired to its backend" is a fact about the routes the document names in
    its own script. Reading the served bytes (no browser) keeps the probe
    offline while still asserting the wiring a browser would execute: the view
    is what calls ``/api/orgchart/*`` and ``/api/skillstudio/*``, so the routes
    must appear in the document itself.
    """
    app, _cookies = _app(enabled=True)
    expected = {
        "orgchart": ["/api/orgchart/chart", "/api/orgchart/health"],
        "skillstudio": [
            "/api/skillstudio/skills",
            "/api/skillstudio/author",
            "/api/skillstudio/test",
            "/api/skillstudio/publish",
        ],
    }
    observations = {}
    for view, routes in expected.items():
        response = app.handle("GET", f"/views/{view}.html", cookies={})
        html = response.as_bytes().decode("utf-8") if not response.is_json else ""
        observations[view] = {
            "status": response.status,
            "isHtml": "<!DOCTYPE html>" in html,
            "wired": {route: route in html for route in routes},
        }
    return observations


def probe_skill_studio_lifecycle_roundtrip() -> dict:
    """The studio's three actions drive the workbook-9 lifecycle over HTTP.

    ``author`` → ``draft``, ``test`` → ``tested`` (eval evidence recorded),
    ``publish`` → refused without green evidence — the studio's own gate, not
    the adapter's. Each POST is the ``ACTION_*`` edge the view's buttons issue,
    so this is the backend half of "clicking each action reflects the result".
    """
    app, cookies = _app(enabled=True)
    skill_id, version = "e2e-skill", "1.0.0"

    s1, p1 = _write(
        app, "/api/skillstudio/author",
        {"skillId": skill_id, "skillVersion": version, "category": "analysis",
         "description": "e2e probe skill"}, cookies=cookies,
    )
    s2, p2 = _write(
        app, "/api/skillstudio/test",
        {"skillId": skill_id, "skillVersion": version}, cookies=cookies,
    )
    s3, p3 = _write(
        app, "/api/skillstudio/publish",
        {"skillId": skill_id, "skillVersion": version}, cookies=cookies,
    )
    return {
        "author": {
            "status": s1,
            "lifecycle": (p1.get("data") or {}).get("skill", {}).get("skillLifecycle"),
        },
        "test": {
            "status": s2,
            "lifecycle": (p2.get("data") or {}).get("skill", {}).get("skillLifecycle"),
            "hasEvalEvidence": bool(
                ((p2.get("data") or {}).get("skill", {}) or {}).get("evalEvidence")
            ),
        },
        "publish": {
            "status": s3,
            "code": (p3.get("error") or {}).get("code"),
        },
    }


PROBES = (
    ("flags-off-by-default", probe_flags_off_by_default),
    ("org-chart", probe_org_chart_renders_the_declaration),
    ("task-board", probe_task_board_replays_a_real_ticket),
    ("skill-studio", probe_skill_studio_serves_the_studio_catalog),
    ("portal-view-documents", probe_portal_view_documents),
    ("skill-studio-lifecycle", probe_skill_studio_lifecycle_roundtrip),
)


def run_probes() -> dict:
    """Run every probe and return the evidence document."""
    return {name: probe() for name, probe in PROBES}


def main() -> int:
    print(json.dumps(run_probes(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - a manual entry point
    raise SystemExit(main())
