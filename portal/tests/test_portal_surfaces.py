"""Portal-surfaces feed server surface (issue #350).

The OS shell's Settings→Modules needs the fleet-wide surface catalog, and the
copy it pins in its own repo sat on ``state:"awaiting-upstream"`` with an empty
``surfaces`` while ``kushin77/CMR#708`` was open. This suite pins the serving
layer's half of the fix: ``registry/portal-surfaces.pinned.json`` is a REAL
pinned revision of the upstream artifact (no invented surface), the app serves
it at ``GET /api/portal/surfaces``, and the surface is feature-flag-gated OFF —
the acceptance criterion of #350 ("Settings→Modules renders a non-empty surface
projection") is asserted here as a non-empty projection, not as a promise.

The provenance claim is checkable from outside in one command (the revision and
blob sha256 are recorded in the pin block):

    gh api -H 'Accept: application/vnd.github.raw' \\
      'repos/kushin77/CMR/contents/catalog/portal-surfaces.json?ref=<pin.revision>' \\
      | sha256sum        # must equal pin.sha256

Empty is allowed; invention is not: a pin the adapter cannot read is served as
an ``unresolved`` document with an empty list and a ``note`` naming the defect,
and that behaviour is asserted too.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from conftest import ApiClient, console_sso  # noqa: E402
from portal.server.app import build_app  # noqa: E402
from portal.server.fleet import surface_enabled  # noqa: E402
from portal.server.surfaces import (  # noqa: E402
    PINNED_RELATIVE,
    PORTAL_SURFACES,
    SCHEMA,
    PortalSurfacesFeed,
)

#: The committed pin document (the serving layer's source of truth).
COMMITTED_PIN = REPO_ROOT / PINNED_RELATIVE

#: The surface ids the real CMR catalog carries at the pinned revision
#: (aab865e3ba58fff271d092a76c383792abe37509, 7 surfaces). Pinned here on
#: purpose: a re-pin to a different catalog must be a deliberate test edit.
EXPECTED_SURFACE_IDS = [
    "code-indexing",
    "diagrams",
    "erp-crm",
    "googleworkspace",
    "pmo",
    "saas-rbac",
    "shared-frontend",
]


def _feed(*, enabled: bool, feed_path: Path | str | None = None) -> PortalSurfacesFeed:
    return PortalSurfacesFeed(repo_root=REPO_ROOT, enabled=enabled, feed_path=feed_path)


def _app(feed: PortalSurfacesFeed):
    return build_app(sso=console_sso(), portal_surfaces=feed)


def _authed(app) -> ApiClient:
    api = ApiClient(app)
    api.authenticate("root@platform.example.com", "acme")
    return api


# --- the pin is a real revision of the real catalog --------------------------


def test_committed_pin_records_a_real_revision_and_the_real_catalog():
    document = json.loads(COMMITTED_PIN.read_text(encoding="utf-8"))
    assert document["schema"] == SCHEMA

    pin = document["pin"]
    assert pin["repo"] == "kushin77/CMR"
    assert pin["path"] == "catalog/portal-surfaces.json"
    assert pin["state"] == "pinned", "the committed pin must not sit unresolved"
    assert re.fullmatch(r"[0-9a-f]{40}", pin["revision"]), pin["revision"]
    assert re.fullmatch(r"[0-9a-f]{64}", pin["sha256"]), pin["sha256"]
    # The provenance numbers describe the upstream blob this copy was taken
    # from; a re-pin updates them (and EXPECTED_SURFACE_IDS) deliberately.
    assert pin["bytes"] == 18017

    assert [surface["id"] for surface in document["surfaces"]] == EXPECTED_SURFACE_IDS


# --- AC: the served projection is non-empty ----------------------------------


def test_served_projection_is_non_empty_and_is_the_committed_pin():
    app = _app(_feed(enabled=True))
    api = _authed(app)

    status, payload = api.get("/api/portal/surfaces")
    assert status == 200
    served = payload["data"]

    # (a) the acceptance criterion of #350: a NON-EMPTY surface projection.
    assert served["schema"] == SCHEMA
    assert served["surfaces"], "Settings→Modules would render nothing"
    assert [surface["id"] for surface in served["surfaces"]] == EXPECTED_SURFACE_IDS

    # (b) every entry carries what the Settings→Modules projection renders.
    for surface in served["surfaces"]:
        assert surface["id"]
        assert surface["type"]
        assert isinstance(surface["mandatory"], bool)

    # (c) delegation, not duplication: what is served IS the committed pin,
    # provenance block included, so a consumer can audit the revision it got.
    pinned = json.loads(COMMITTED_PIN.read_text(encoding="utf-8"))
    assert served["surfaces"] == pinned["surfaces"]
    assert served["pin"] == pinned["pin"]

    # (d) the adapter's own accessor returns the same non-empty projection.
    assert [surface["id"] for surface in _feed(enabled=True).surfaces()] == EXPECTED_SURFACE_IDS


def test_default_app_builds_the_feed_from_the_committed_pin():
    # policy-gr5-enabled-by-default (2026-09-21): this test hardcoded the OLD off-by-default policy; updated to assert the new correct default.
    app = build_app(sso=console_sso())
    assert app.surfaces.feed_path == COMMITTED_PIN
    assert app.surfaces.enabled is True, "the committed registry ships the surface ON by default (GR-5 reversal)"


# --- empty is honest, invention is not ---------------------------------------


def test_projection_is_empty_but_honest_when_the_feed_declares_no_surfaces(tmp_path):
    feed_path = tmp_path / "awaiting-upstream.json"
    feed_path.write_text(
        json.dumps(
            {
                "schema": SCHEMA,
                "pin": {
                    "repo": "kushin77/CMR",
                    "path": "catalog/portal-surfaces.json",
                    "revision": None,
                    "retrievedAt": "2026-09-11T00:00:00Z",
                    "state": "awaiting-upstream",
                },
                "note": "upstream artifact not yet published",
                "surfaces": [],
            }
        ),
        encoding="utf-8",
    )
    app = _app(_feed(enabled=True, feed_path=feed_path))
    api = _authed(app)

    status, payload = api.get("/api/portal/surfaces")
    assert status == 200
    served = payload["data"]
    assert served["surfaces"] == [], "an empty feed must not be padded with entries"
    assert served["pin"]["state"] == "awaiting-upstream", "the state must stay truthful"
    assert _feed(enabled=True, feed_path=feed_path).surfaces() == []


def test_unreadable_pin_is_served_unresolved_and_invents_nothing(tmp_path):
    missing = _feed(enabled=True, feed_path=tmp_path / "absent.json").document()
    assert missing["surfaces"] == []
    assert missing["pin"]["state"] == "unresolved"
    assert "unreadable" in missing["note"]
    assert "invented" in missing["note"]

    invalid = tmp_path / "invalid.json"
    invalid.write_text("{ not json", encoding="utf-8")
    document = _feed(enabled=True, feed_path=invalid).document()
    assert document["surfaces"] == []
    assert document["pin"]["state"] == "unresolved"
    assert "not valid JSON" in document["note"]

    wrong_schema = tmp_path / "wrong-schema.json"
    wrong_schema.write_text(json.dumps({"schema": "x/v1", "surfaces": []}), encoding="utf-8")
    document = _feed(enabled=True, feed_path=wrong_schema).document()
    assert document["surfaces"] == []
    assert document["pin"]["state"] == "unresolved"
    assert "expected" in document["note"]

    not_a_list = tmp_path / "not-a-list.json"
    not_a_list.write_text(json.dumps({"schema": SCHEMA, "surfaces": {}}), encoding="utf-8")
    document = _feed(enabled=True, feed_path=not_a_list).document()
    assert document["surfaces"] == []
    assert document["pin"]["state"] == "unresolved"
    assert "surfaces[]" in document["note"]


# --- AC: the surface ships feature-flag-gated OFF ----------------------------


def test_registry_declares_the_portal_surfaces_surface_on():
    # policy-gr5-enabled-by-default (2026-09-21): this test hardcoded the OLD off-by-default policy; updated to assert the new correct default.
    import yaml

    document = yaml.safe_load(
        (REPO_ROOT / "infra" / "feature-flags" / "registry.yaml").read_text(encoding="utf-8")
    )
    entry = document["surfaces"][PORTAL_SURFACES]
    assert entry["default"] in (True, "on")
    assert entry["promoted"] is False  # promoted still false; it's enabled by default now instead
    assert entry["service"] == "portal"


def test_flag_on_serves_the_route_and_requires_authn():
    # policy-gr5-enabled-by-default (2026-09-21): this test hardcoded the OLD off-by-default policy; updated to assert the new correct default.
    assert surface_enabled(REPO_ROOT, surface=PORTAL_SURFACES) is True
    app = _app(_feed(enabled=True))

    # A promoted surface is visible, and requires authentication.
    # Unauthenticated callers get 401 (unauthorized), not 404 (invisible).
    api = ApiClient(app)
    status, payload = api.get("/api/portal/surfaces")
    assert status == 401, "unauthenticated caller should be unauthorized, not feature-disabled"
    assert payload["error"]["code"] == "unauthorized"

    # Authenticated caller can access
    auth_api = _authed(app)
    status, _ = auth_api.get("/api/portal/surfaces")
    assert status == 200


def test_flag_on_requires_a_session():
    app = _app(_feed(enabled=True))
    status, payload = ApiClient(app).get("/api/portal/surfaces")
    assert status == 401
    assert payload["error"]["code"] == "unauthorized"


def test_flag_on_route_is_get_only_and_unknown_routes_are_404():
    app = _app(_feed(enabled=True))
    api = _authed(app)

    status, payload = api.post("/api/portal/surfaces", body={})
    assert status == 405
    assert payload["error"]["code"] == "method_not_allowed"

    for path in ("/api/portal/nonsense", "/api/portal/surfaces/code-indexing"):
        status, payload = api.get(path)
        assert status == 404, path
        assert payload["error"]["code"] == "not_found"


def test_surface_enabled_reads_the_registry_and_fails_closed(tmp_path):
    registry = tmp_path / "registry.yaml"
    registry.write_text(
        f"surfaces:\n  {PORTAL_SURFACES}:\n    default: on\n", encoding="utf-8"
    )
    assert (
        surface_enabled(REPO_ROOT, registry_path=registry, surface=PORTAL_SURFACES) is True
    )
    registry.write_text(
        f"surfaces:\n  {PORTAL_SURFACES}:\n    default: off\n", encoding="utf-8"
    )
    assert (
        surface_enabled(REPO_ROOT, registry_path=registry, surface=PORTAL_SURFACES) is False
    )
    assert (
        surface_enabled(
            REPO_ROOT, registry_path=tmp_path / "absent.yaml", surface=PORTAL_SURFACES
        )
        is False
    )
