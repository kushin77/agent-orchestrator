"""portal skill-studio surface tests (issue #642, workbook-11, server half).

Proves, against the served API, the acceptance criterion the serving half can
prove end to end:

* the **author → test → publish** lifecycle is driven through the real
  workbook-9 ``SkillStudio`` — every state, verdict, hash and refusal is the
  studio's own, and the adapter re-checks no gate;
* **publish is refused without green eval evidence** (never tested, zero cases,
  or a failing case) — the refusal is served as a 409 carrying the studio's own
  reason, so the gate cannot become a formality at the transport layer;
* an unknown revision is **absent** (404), never an invented empty shell;
* the surface is **feature-flag-gated OFF** until promoted, before authN;
* importing the server **borrows** the pillar root for the one import that needs
  it and gives it back — no import-time ``sys.path`` edit survives the import.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from conftest import REPO_ROOT, console_sso, login_as

from portal.server.app import ConsoleApplication, build_app
from portal.server.config_flags import SKILL_STUDIO_SURFACE, surface_enabled
from portal.server.skill_studio import SCHEMA, SkillStudioSurface


def _surface(*, enabled: bool) -> SkillStudioSurface:
    """The surface as the server builds it: a real studio, constructed by the
    adapter's own resolver.

    No studio is injected on purpose — this is the production path
    (``ConsoleApplication`` passes no ``studio``), so the suite exercises the
    exact module resolution and studio construction the server uses.
    """
    return SkillStudioSurface(repo_root=REPO_ROOT, enabled=enabled)


def _app(surface: SkillStudioSurface) -> ConsoleApplication:
    return build_app(sso=console_sso(), skill_studio_surface=surface)


def _authed(app: ConsoleApplication):
    return login_as(app, "root@platform.example.com", "acme")


def _author(api, *, skill_id="release-notes", version="1.0.0", category="analysis"):
    return api.post(
        "/api/skillstudio/author",
        body={
            "skillId": skill_id,
            "skillVersion": version,
            "category": category,
            "description": "draft a release note from the merged PR set",
        },
    )


# --------------------------------------------------------------------------- #
# The flag gate (GR-5: a new surface ships OFF)
# --------------------------------------------------------------------------- #
def _cases_file(tmp_path: Path, *, observed: str) -> Path:
    """A real eval-cases file in the harness's own on-disk shape.

    The studio's publish gate re-derives its verdict from the workbook-8
    harness over this *file*, so a green publish has to be evidenced by a file
    the harness itself can load — in-memory cases alone would pass ``test`` and
    still be refused at publish (the studio's last line of defence).
    """
    cases = tmp_path / f"cases-{observed}.yaml"
    cases.write_text(
        "cases:\n"
        "  - promptId: release-notes@1.0.0\n"
        "    caseId: c1\n"
        "    expected: [notes]\n"
        f"    observed: [{observed}]\n",
        encoding="utf-8",
    )
    return cases


def test_config_declares_the_surface_off():
    document = yaml.safe_load(
        (REPO_ROOT / "portal" / "config" / "feature-flags.yaml").read_text(
            encoding="utf-8"
        )
    )
    entry = document["surfaces"][SKILL_STUDIO_SURFACE]
    assert entry["default"] in (False, "off"), (
        "the skill-studio surface must ship OFF (GR-5)"
    )
    assert surface_enabled(REPO_ROOT, surface=SKILL_STUDIO_SURFACE) is False


def test_surface_is_refused_while_the_flag_is_off():
    app = build_app(sso=console_sso())
    api = _authed(app)
    status, payload = api.get("/api/skillstudio/skills")
    assert status == 404
    assert payload["error"]["code"] == "feature_disabled"
    assert "portal/config/feature-flags.yaml" in payload["error"]["message"]
    # the write edge is refused too, and *before* authN — the surface (and any
    # hint that it exists) is invisible to an unauthenticated probe.
    status, _ = api.post("/api/skillstudio/publish", body={"skillId": "x"})
    assert status == 404


def test_the_surface_renders_once_its_flag_is_flipped_on():
    app = _app(_surface(enabled=True))
    status, payload = _authed(app).get("/api/skillstudio/skills")
    assert status == 200
    assert payload["data"]["schema"] == SCHEMA
    assert payload["data"]["skills"] == []  # authored here, empty at boot
    assert payload["data"]["categories"]  # the studio's own category vocabulary


# --------------------------------------------------------------------------- #
# author -> test -> publish, through the studio's own lifecycle
# --------------------------------------------------------------------------- #
def test_author_then_read_back_the_revision():
    app = _app(_surface(enabled=True))
    api = _authed(app)

    status, payload = _author(api)
    assert status == 200
    authored = payload["data"]["skill"]
    assert authored["skillId"] == "release-notes"
    assert authored["skillVersion"] == "1.0.0"
    assert authored["skillLifecycle"] == "draft"
    assert len(authored["contentSha256"]) == 64
    assert authored["hasEvalEvidence"] is False

    status, payload = api.get("/api/skillstudio/skills")
    assert [row["skillId"] for row in payload["data"]["skills"]] == [
        "release-notes"
    ]
    status, payload = api.get("/api/skillstudio/skills/release-notes")
    assert status == 200
    assert payload["data"]["skill"]["skillLifecycle"] == "draft"


def test_authoring_a_duplicate_is_refused():
    """An authored revision is immutable — the studio's own refusal."""
    app = _app(_surface(enabled=True))
    api = _authed(app)
    assert _author(api)[0] == 200

    status, payload = _author(api)
    assert status == 409
    assert payload["error"]["code"] == "already_exists"
    assert "immutable" in payload["error"]["message"]


def test_publish_is_refused_before_the_skill_is_tested():
    """``draft`` is not ``tested``: the lifecycle gate refuses."""
    app = _app(_surface(enabled=True))
    api = _authed(app)
    _author(api)

    status, payload = api.post(
        "/api/skillstudio/publish",
        body={"skillId": "release-notes", "skillVersion": "1.0.0"},
    )
    assert status == 409
    assert payload["error"]["code"] == "lifecycle_refused"
    assert "author -> test -> publish" in payload["error"]["message"]


def test_test_records_the_harness_evidence_and_unevaluated_publish_is_refused():
    """The eval harness's own verdict is what the publish gate re-checks.

    A skill with no cases of its own scores ``cases: 0`` (UNEVALUATED), which
    the studio refuses to publish — the acceptance criterion, over HTTP.
    """
    app = _app(_surface(enabled=True))
    api = _authed(app)
    _author(api)

    status, payload = api.post(
        "/api/skillstudio/test",
        body={"skillId": "release-notes", "skillVersion": "1.0.0"},
    )
    assert status == 200
    evidence = payload["data"]["skill"]["evalEvidence"]
    assert evidence["harness"] == "registry/prompts/evals.py"
    assert evidence["evalId"] == "release-notes@1.0.0"
    assert evidence["cases"] == 0  # unevaluated, stated not hidden
    assert payload["data"]["skill"]["skillLifecycle"] == "tested"

    status, payload = api.post(
        "/api/skillstudio/publish",
        body={"skillId": "release-notes", "skillVersion": "1.0.0"},
    )
    assert status == 409
    assert payload["error"]["code"] == "publish_refused"
    assert "no eval evidence" in payload["error"]["message"] or (
        "refused" in payload["error"]["message"]
    )


def test_a_failing_case_blocks_publish(tmp_path: Path):
    """A RED case is recorded, and publish stays refused.

    The case is a real cases file whose ``observed`` disagrees with
    ``expected``, so the harness's own scorer returns RED — and the studio's
    publish gate, which re-derives that verdict, refuses.
    """
    app = _app(_surface(enabled=True))
    api = _authed(app)
    _author(api)

    status, payload = api.post(
        "/api/skillstudio/test",
        body={
            "skillId": "release-notes",
            "skillVersion": "1.0.0",
            "casesPath": str(_cases_file(tmp_path, observed="changelog")),
        },
    )
    assert status == 200
    evidence = payload["data"]["skill"]["evalEvidence"]
    assert evidence["cases"] == 1
    assert evidence["failed"] == 1, "the harness must score the case RED"
    assert payload["data"]["skill"]["skillLifecycle"] == "tested"

    status, payload = api.post(
        "/api/skillstudio/publish",
        body={"skillId": "release-notes", "skillVersion": "1.0.0"},
    )
    assert status == 409
    assert payload["error"]["code"] == "publish_refused"


def test_inline_json_cases_are_scored_but_cannot_satisfy_the_publish_gate(
    tmp_path: Path,
):
    """An unevidenced publish is refused — the gate re-derives, it does not trust.

    Cases supplied inline are scored (so an author gets a verdict), but publish
    re-runs the harness over its own cases file. With no such file the skill is
    UNEVALUATED and the refusal is the honest outcome.
    """
    app = _app(_surface(enabled=True))
    api = _authed(app)
    _author(api)

    status, payload = api.post(
        "/api/skillstudio/test",
        body={
            "skillId": "release-notes",
            "skillVersion": "1.0.0",
            "cases": [
                {"caseId": "c1", "expected": ["notes"], "observed": ["notes"]}
            ],
        },
    )
    assert status == 200
    assert payload["data"]["skill"]["evalEvidence"]["passed"] == 1

    status, payload = api.post(
        "/api/skillstudio/publish",
        body={"skillId": "release-notes", "skillVersion": "1.0.0"},
    )
    assert status == 409
    assert payload["error"]["code"] == "publish_refused"


def test_a_green_case_allows_publish(tmp_path: Path):
    """The other direction: green evidence *does* publish (the gate is real)."""
    app = _app(_surface(enabled=True))
    api = _authed(app)
    _author(api)

    status, payload = api.post(
        "/api/skillstudio/test",
        body={
            "skillId": "release-notes",
            "skillVersion": "1.0.0",
            "casesPath": str(_cases_file(tmp_path, observed="notes")),
        },
    )
    assert status == 200
    assert payload["data"]["skill"]["evalEvidence"]["failed"] == 0
    assert payload["data"]["skill"]["evalEvidence"]["passed"] == 1

    status, payload = api.post(
        "/api/skillstudio/publish",
        body={"skillId": "release-notes", "skillVersion": "1.0.0"},
    )
    assert status == 200
    assert payload["data"]["skill"]["skillLifecycle"] == "published"


def test_an_unknown_revision_is_absent_never_invented():
    app = _app(_surface(enabled=True))
    api = _authed(app)

    status, payload = api.get("/api/skillstudio/skills/never-authored")
    assert status == 404
    assert payload["error"]["code"] == "not_found"


def test_a_missing_required_field_is_a_400():
    app = _app(_surface(enabled=True))
    api = _authed(app)

    status, payload = api.post(
        "/api/skillstudio/author", body={"skillId": "incomplete"}
    )
    assert status == 400
    assert payload["error"]["code"] == "invalid_request"


def test_search_returns_the_studios_own_rows():
    app = _app(_surface(enabled=True))
    api = _authed(app)
    _author(api, skill_id="alpha-skill", category="analysis")
    _author(api, skill_id="beta-skill", category="testing")

    status, payload = api.get(
        "/api/skillstudio/skills", query={"text": "alpha"}
    )
    assert status == 200
    assert [row["skillId"] for row in payload["data"]["skills"]] == ["alpha-skill"]


# --------------------------------------------------------------------------- #
# Transport contract
# --------------------------------------------------------------------------- #
def test_unknown_action_and_bad_method_are_refused():
    app = _app(_surface(enabled=True))
    api = _authed(app)

    status, _ = api.post("/api/skillstudio/nope", body={})
    assert status == 404
    status, payload = api.post("/api/skillstudio/skills", body={})
    assert status == 404
    assert payload["error"]["code"] == "not_found"


def test_the_surface_requires_a_console_session():
    app = _app(_surface(enabled=True))
    api = login_as(app, "nobody@example.com", "acme")
    api.cookies.clear()
    status, payload = api.get("/api/skillstudio/skills")
    assert status == 401
    assert payload["error"]["code"] == "unauthorized"


@pytest.mark.parametrize("action", ["author", "test", "publish"])
def test_every_write_action_is_a_real_route(action: str):
    """No declared action may be a 404 — a studio UI needs all three edges."""
    app = _app(_surface(enabled=True))
    api = _authed(app)
    status, payload = api.post(f"/api/skillstudio/{action}", body={})
    # the body is empty on purpose: the point is that the *route* exists, so the
    # answer is a validation refusal (400) rather than "no such action" (404).
    assert status == 400
    assert payload["error"]["code"] == "invalid_request"


# --------------------------------------------------------------------------- #
# Import hygiene
# --------------------------------------------------------------------------- #
def test_importing_the_server_borrows_the_pillar_root_and_gives_it_back():
    """Importing the portal may change nothing in the process but ``sys.modules``.

    The studio's pillar bootstrap is a *scoped* loan of ``registry/`` around the
    one import that needs it. At module scope it would instead re-order every
    later import in the process — ``packs``, ``personas``, ``profiles`` and
    ``prompts`` would start resolving out of the pillar's own directory rather
    than the namespace package the caller had already resolved, and a sibling
    suite's pillar resolution would depend on whether the portal had been
    imported first.

    The import is once-per-process, so the measurement is taken in a subprocess.
    Only the pillar root this adapter owns is asserted here; importing the
    server also pulls in the pre-existing ``identity/`` bootstrap
    (``portal/server/control_api.py``, on ``master``, another issue's file),
    which this lane neither owns nor changes.
    """
    code = (
        "import json, sys;"
        "before = list(sys.path);"
        "import portal.server.app;"
        "print('ADDED:' + json.dumps([p for p in sys.path if p not in before]))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    added = json.loads(
        next(
            line[len("ADDED:") :]
            for line in proc.stdout.splitlines()
            if line.startswith("ADDED:")
        )
    )
    assert str(REPO_ROOT / "registry") not in added, added
