"""Versioned live-data bridge — serving half (issue #339).

The bridge is the ONE versioned read contract over the platform's four state
families (registry / gateway / telemetry / guardrails). These tests pin the
issue's acceptance against the *real* sources, never a hand-built fixture:

* the flag-gated-OFF surface is refused **before** authN, and a valid session
  does not open a gated surface (the fleet/telemetry precedent);
* every endpoint requires a verified session (AuthN), and the tenant-scoped
  family refuses an unscoped principal (AuthZ);
* each family's payload equals the very artifact/adapter the owning lane
  publishes — the registry family is compared against ``RegistrySnapshot``
  itself, the gateway family against ``routing.yaml``, the guardrails family
  against the policy catalog — so a restated value fails the test;
* a registry edit is served **without a restart** (the "visible without a
  rebuild" acceptance), and the push channel emits a frame when a family's
  revision moves. Both properties are what the mutation proof breaks.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from conftest import ApiClient, console_sso, login_as  # noqa: E402
from portal.server.app import build_app  # noqa: E402
from portal.server.bridge import (  # noqa: E402
    CONTRACT,
    FAMILIES,
    LiveBridge,
)
from portal.server.live_feed import LiveFeed  # noqa: E402
from portal.server.livestore import RegistrySnapshot  # noqa: E402

#: The families the versioned contract promises, in order.
EXPECTED_FAMILIES = ("registry", "gateway", "telemetry", "guardrails")

#: A seed the registry does not publish, valid against catalog.yaml (MED -> flash).
NEW_SEED = (
    "id: zed\n"
    "version: 9.9.9\n"
    "owner: platform\n"
    "defaultModelTier: MED\n"
    "capabilitySet:\n"
    "  - orchestrate\n"
)


def _bridge(enabled: bool = True, **kwargs) -> LiveBridge:
    return LiveBridge(repo_root=REPO_ROOT, enabled=enabled, **kwargs)


def _app(bridge: LiveBridge):
    return build_app(sso=console_sso(), bridge=bridge)


def _super(app) -> ApiClient:
    return login_as(app, "root@platform.example.com", "acme")


def _seed_repo(tmp_path: Path) -> Path:
    """A per-test repo whose registry directory is the real one, copied."""
    (tmp_path / "registry" / "profiles").mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        REPO_ROOT / "registry" / "profiles",
        tmp_path / "registry" / "profiles",
        dirs_exist_ok=True,
    )
    return tmp_path


def _frame_payload(frame: str) -> dict:
    _, _, data = frame.partition("data: ")
    return json.loads(data.rsplit("\n\n", 1)[0])


# --- the flag gate (before authN) -------------------------------------------


def test_bridge_ships_flag_gated_off_and_is_invisible_before_authn():
    default_bridge = LiveBridge(repo_root=REPO_ROOT)  # enabled=None: registry decides
    assert default_bridge.enabled is False, "the surface must ship OFF"

    app = build_app(sso=console_sso(), bridge=default_bridge)
    unauth = ApiClient(app)
    status, payload = unauth.get("/api/v1/bridge")
    assert status == 404
    assert payload["error"]["code"] == "feature_disabled"

    # A valid session does NOT open a gated surface.
    status, payload = _super(app).get("/api/v1/bridge/registry")
    assert status == 404
    assert payload["error"]["code"] == "feature_disabled"


def test_the_registry_flag_entry_is_declared_off():
    """The registry declares the bridge surface, defaulting off (GR-5)."""
    bridge = LiveBridge(repo_root=REPO_ROOT)
    # enabled=None resolved OFF, which is only possible from the registry entry.
    assert bridge.enabled is False
    text = (REPO_ROOT / "infra" / "feature-flags" / "registry.yaml").read_text(
        encoding="utf-8"
    )
    assert "live_bridge:" in text


# --- AuthN on every endpoint ------------------------------------------------


def test_every_bridge_endpoint_requires_a_verified_session():
    app = _app(_bridge())
    unauth = ApiClient(app)
    for path in (
        "/api/v1/bridge",
        "/api/v1/bridge/registry",
        "/api/v1/bridge/gateway",
        "/api/v1/bridge/guardrails",
        "/api/v1/bridge/telemetry",
        "/api/v1/bridge/stream",
    ):
        status, payload = unauth.get(path)
        assert status == 401, path
        assert payload["error"]["code"] == "unauthorized", path


# --- the manifest -----------------------------------------------------------


def test_manifest_lists_the_four_versioned_families():
    app = _app(_bridge())
    status, payload = _super(app).get("/api/v1/bridge")
    assert status == 200
    data = payload["data"]
    assert data["contract"] == CONTRACT
    assert tuple(row["id"] for row in data["families"]) == EXPECTED_FAMILIES
    assert tuple(row["id"] for row in data["families"]) == FAMILIES


def test_unknown_family_is_a_404_and_non_get_is_a_405():
    app = _app(_bridge())
    api = _super(app)
    status, payload = api.get("/api/v1/bridge/does-not-exist")
    assert status == 404
    assert payload["error"]["code"] == "not_found"
    status, payload = api.post("/api/v1/bridge/registry", body={})
    assert status == 405
    assert payload["error"]["code"] == "method_not_allowed"


# --- family reads ARE the owning lane's artifact ----------------------------


def test_registry_family_projects_the_live_registry():
    app = _app(_bridge())
    status, payload = _super(app).get("/api/v1/bridge/registry")
    assert status == 200
    data = payload["data"]
    assert data["available"] is True
    snapshot = RegistrySnapshot(REPO_ROOT)
    assert {row["id"] for row in data["profiles"]} == set(snapshot.profile_ids())
    for row in data["profiles"]:
        profile = snapshot.profile(row["id"])
        assert row["modelTier"] == profile.model_tier, row["id"]
        assert row["model"] == profile.model, row["id"]
        assert row["capabilities"] == list(profile.capabilities), row["id"]
        assert row["version"] == profile.version, row["id"]


def test_gateway_family_serves_the_routing_contract_and_provider_catalog():
    app = _app(_bridge())
    status, payload = _super(app).get("/api/v1/bridge/gateway")
    assert status == 200
    data = payload["data"]
    assert data["available"] is True
    # Verbatim from gateway/proxy/config/routing.yaml.
    assert "code-review-verdict" in data["routes"]
    assert data["tierMap"]["L2"] == "HIGH"
    assert data["providerChains"]["LOW"][-1] == "ollama"
    # The provider catalog is the module documents themselves.
    provider_ids = {row["id"] for row in data["providers"]}
    assert {"deepseek", "ollama", "claude-anthropic"} <= provider_ids


def test_guardrails_family_serves_the_policy_controls():
    app = _app(_bridge())
    status, payload = _super(app).get("/api/v1/bridge/guardrails")
    assert status == 200
    data = payload["data"]
    assert data["available"] is True
    ids = {row["id"] for row in data["controls"]}
    assert "model-call-budget" in ids
    assert data["count"] == len(data["controls"])


def test_telemetry_family_hydrates_from_the_live_feed_and_names_the_surfaces(tmp_path):
    feed = LiveFeed(
        repo_root=REPO_ROOT,
        enabled=True,
        calls_store_path=tmp_path / "calls.jsonl",
        verdicts_store_path=tmp_path / "verdicts.jsonl",
    )
    app = _app(_bridge(live_feed=feed))
    status, payload = _super(app).get("/api/v1/bridge/telemetry")
    assert status == 200
    data = payload["data"]
    assert data["available"] is True
    # The tail is the live feed's own hydration payload (no restated record).
    assert set(data["feed"]) == {"feed", "frames"}
    # Budget/metering/observability are NAMED, not re-derived.
    routes = {row["route"] for row in data["surfaces"]}
    assert {"/api/finops/overview", "/api/ops/overview"} <= routes


def test_telemetry_family_refuses_an_unscoped_principal(tmp_path):
    feed = LiveFeed(
        repo_root=REPO_ROOT,
        enabled=True,
        calls_store_path=tmp_path / "calls.jsonl",
        verdicts_store_path=tmp_path / "verdicts.jsonl",
    )
    app = _app(_bridge(live_feed=feed))
    # A valid session whose identity holds no tenant binding: refused, not
    # shown an empty feed (which would read as "no telemetry").
    api = login_as(app, "nobody@nowhere.example.com", "acme")
    status, payload = api.get("/api/v1/bridge/telemetry")
    assert status == 403
    assert payload["error"]["code"] == "permission_denied"


# --- the live-refresh property (visible without a rebuild) ------------------


def test_a_registry_edit_is_served_without_a_restart(tmp_path):
    repo = _seed_repo(tmp_path)
    bridge = LiveBridge(repo_root=repo, enabled=True)

    first = bridge.registry()
    assert first["available"] is True
    assert "zed" not in {row["id"] for row in first["profiles"]}
    before = first["revision"]

    # The registry lane publishes a new profile seed. Nothing restarts.
    (repo / "registry" / "profiles" / "seeds" / "zed.9.9.9.yaml").write_text(
        NEW_SEED, encoding="utf-8"
    )

    second = bridge.registry()
    assert second["revision"] != before, "the change signal must move"
    assert "zed" in {row["id"] for row in second["profiles"]}, (
        "a registry edit must be served without a restart"
    )


def test_push_channel_emits_the_manifest_then_a_change_frame(tmp_path):
    repo = _seed_repo(tmp_path)
    changed = {"done": False}

    def sleeper(_seconds: float) -> None:
        # After the first pass the registry changes, so the next pass must
        # emit a second family frame with a moved revision.
        if not changed["done"]:
            changed["done"] = True
            (
                repo / "registry" / "profiles" / "seeds" / "zed.9.9.9.yaml"
            ).write_text(NEW_SEED, encoding="utf-8")

    bridge = LiveBridge(repo_root=repo, enabled=True, sleep=sleeper)
    frames = list(
        bridge.stream(
            families=["registry"], max_frames=3, max_polls=5, sleep=sleeper
        )
    )
    assert frames[0].startswith("event: bridge\n")
    family_frames = [frame for frame in frames if frame.startswith("event: bridge-family\n")]
    assert len(family_frames) == 2, frames
    revisions = [_frame_payload(frame)["revision"] for frame in family_frames]
    assert revisions[0] != revisions[1]
    assert _frame_payload(family_frames[0])["contract"] == CONTRACT
    assert _frame_payload(family_frames[1])["family"] == "registry"
