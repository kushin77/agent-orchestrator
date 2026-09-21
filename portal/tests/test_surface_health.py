"""The console surface's readiness signal and its rollback anchor (issue #802).

Two halves of one contract, and the tests keep them coupled: the readiness signal
decides, and a failed reading withdraws the surface — through the rollout engine
(audited) *and* the runtime overlay the surface reader consults. The failure
modes worth pinning are the quiet ones: a reader that fails OPEN on a corrupt
document, a readiness signal that reports "ready" for something it could not
measure, and an anchor that withdraws a healthy surface.

These run offline against scratch documents built FROM the committed
declarations, so a reviewed promotion of the real surface cannot turn them red.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from infra.rollout import surface_guard  # noqa: E402
from infra.rollout.checks.check_rollout import check_all as check_rollout_all  # noqa: E402
from portal.server import surface_state  # noqa: E402
from portal.server.app import build_app  # noqa: E402
from portal.server.fleet import read_surface_default, read_registry_surfaces  # noqa: E402
from portal.server.surface_health import (  # noqa: E402
    SURFACE_CANNOT_ASSESS,
    SURFACE_NOT_READY,
    SURFACE_OFF,
    SURFACE_READY,
    readiness,
)

SURFACE = "operator_terminal"
FLAG = "surfaces.operator_terminal"


def _committed_registry() -> dict:
    return yaml.safe_load(
        (REPO_ROOT / "infra" / "feature-flags" / "registry.yaml").read_text(encoding="utf-8")
    )


@pytest.fixture()
def promoted_registry(tmp_path: Path) -> Path:
    """A declaration identical to the committed one but with the surface ON."""
    document = _committed_registry()
    document["surfaces"] = dict(document["surfaces"])
    document["surfaces"][SURFACE] = dict(document["surfaces"][SURFACE])
    document["surfaces"][SURFACE]["default"] = "on"
    path = tmp_path / "registry-promoted.yaml"
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return path


@pytest.fixture()
def broken_static(tmp_path: Path) -> Path:
    """A static root missing the view the console redirects to."""
    root = tmp_path / "static"
    shutil.copytree(REPO_ROOT / "portal" / "static", root)
    (root / "views" / "console.html").unlink()
    return root


def test_reader_fails_closed_on_a_malformed_declaration(tmp_path: Path) -> None:
    """Regression: a malformed document parses to a YAMLError, not a ValueError.

    It must read as ``off`` — never raise out of the reader, which sits behind a
    health route and a rollout gate.
    """
    broken = tmp_path / "registry-broken.yaml"
    broken.write_text("surfaces: [not a mapping\n", encoding="utf-8")
    assert read_registry_surfaces(REPO_ROOT, registry_path=broken) is None
    assert read_surface_default(REPO_ROOT, registry_path=broken, surface=SURFACE) == "off"


def test_an_engaged_rollback_beats_the_declaration(
    tmp_path: Path, promoted_registry: Path
) -> None:
    overlay = tmp_path / "surface-state.json"
    assert (
        read_surface_default(
            REPO_ROOT, registry_path=promoted_registry, surface=SURFACE, overlay_path=overlay
        )
        == "on"
    )
    surface_state.record_rollback(
        REPO_ROOT, SURFACE, reason="test", actor="pytest", path=overlay
    )
    assert (
        read_surface_default(
            REPO_ROOT, registry_path=promoted_registry, surface=SURFACE, overlay_path=overlay
        )
        == "off"
    )
    # …and it is reversible, which is what makes it a rollback and not a kill.
    assert surface_state.clear_rollback(REPO_ROOT, SURFACE, path=overlay) is True
    assert (
        read_surface_default(
            REPO_ROOT, registry_path=promoted_registry, surface=SURFACE, overlay_path=overlay
        )
        == "on"
    )


def test_an_unreadable_overlay_is_never_assumed_disengaged(
    tmp_path: Path, promoted_registry: Path
) -> None:
    """A kill switch nobody can read must not be read as 'off duty'."""
    overlay = tmp_path / "surface-state.json"
    overlay.write_text("{ not json", encoding="utf-8")
    assert surface_state.read_rollbacks(REPO_ROOT, path=overlay) is None
    assert surface_state.is_rolled_back(REPO_ROOT, SURFACE, path=overlay) is True
    assert (
        read_surface_default(
            REPO_ROOT, registry_path=promoted_registry, surface=SURFACE, overlay_path=overlay
        )
        == "off"
    )
    report = readiness(REPO_ROOT, SURFACE, registry_path=promoted_registry, overlay_path=overlay)
    assert report.state == SURFACE_CANNOT_ASSESS
    assert not report.healthy


def test_readiness_is_a_four_state_reading(tmp_path: Path, promoted_registry: Path) -> None:
    # policy-gr5-enabled-by-default (2026-09-21): this test hardcoded the OLD off-by-default policy; updated to assert the new correct default.
    overlay = tmp_path / "surface-state.json"
    # With GR-5 reversal, unpromoted surfaces now ship ON by default, so they should be READY (and promoted=True because default is on)
    unpromoted = readiness(REPO_ROOT, SURFACE, overlay_path=overlay)
    assert unpromoted.state == SURFACE_READY and unpromoted.healthy and unpromoted.promoted

    ready = readiness(REPO_ROOT, SURFACE, registry_path=promoted_registry, overlay_path=overlay)
    assert ready.state == SURFACE_READY and ready.healthy
    # The composed halves are reported, never gating: an unpromoted panel renders
    # its own disabled condition instead of breaking the console.
    assert set(ready.dependencies) == {"fleet_projection", "remote_control"}

    broken = readiness(
        REPO_ROOT,
        SURFACE,
        registry_path=promoted_registry,
        overlay_path=overlay,
        static_dir=tmp_path / "empty-static",
    )
    assert broken.state == SURFACE_NOT_READY
    assert any("console.html" in missing for missing in broken.missing)
    assert not broken.healthy


def test_reconcile_withdraws_on_a_failed_reading_and_audits_it(
    tmp_path: Path, promoted_registry: Path, broken_static: Path
) -> None:
    overlay = tmp_path / "surface-state.json"
    audit = tmp_path / "audit.jsonl"
    outcome = surface_guard.reconcile(
        REPO_ROOT,
        SURFACE,
        registry_path=promoted_registry,
        overlay_path=overlay,
        static_dir=broken_static,
        audit_path=audit,
        actor="pytest",
    )
    assert outcome.action == surface_guard.ACTION_ROLLED_BACK
    assert outcome.exit_code == surface_guard.EXIT_ROLLED_BACK
    assert outcome.flag_action == surface_guard.FLAG_ROLLED_OFF
    assert outcome.audit_verified
    record = [r for r in outcome.audit_records if r["action"] == "rollback"][0]
    assert record["to_stage"] == "off" and record["actor"] == "pytest"
    # The withdrawal is real at the reader, and the declaration was never edited.
    assert surface_state.is_rolled_back(REPO_ROOT, SURFACE, path=overlay)
    assert (
        read_surface_default(
            REPO_ROOT, registry_path=promoted_registry, surface=SURFACE, overlay_path=overlay
        )
        == "off"
    )


def test_reconcile_leaves_a_healthy_surface_alone(
    tmp_path: Path, promoted_registry: Path
) -> None:
    """The control: a guard that always rolled back would pass the test above."""
    overlay = tmp_path / "surface-state.json"
    outcome = surface_guard.reconcile(
        REPO_ROOT, SURFACE, registry_path=promoted_registry, overlay_path=overlay
    )
    assert outcome.action == surface_guard.ACTION_HEALTHY
    assert outcome.exit_code == surface_guard.EXIT_OK
    assert not surface_state.is_rolled_back(REPO_ROOT, SURFACE, path=overlay)
    assert not overlay.exists()


def test_reconcile_rolls_nothing_back_it_cannot_assess(tmp_path: Path) -> None:
    broken = tmp_path / "registry-broken.yaml"
    broken.write_text("surfaces: [not a mapping\n", encoding="utf-8")
    overlay = tmp_path / "surface-state.json"
    outcome = surface_guard.reconcile(
        REPO_ROOT, SURFACE, registry_path=broken, overlay_path=overlay
    )
    assert outcome.action == surface_guard.ACTION_CANNOT_ASSESS
    assert outcome.exit_code == surface_guard.EXIT_CANNOT_ASSESS
    assert not overlay.exists()


def test_readiness_route_names_only_what_exists(
    tmp_path: Path, promoted_registry: Path, broken_static: Path, monkeypatch
) -> None:
    """The rail is honest and enumerates enabled surfaces (GR-5 reversal: surfaces now ON by default)."""
    # policy-gr5-enabled-by-default (2026-09-21): this test hardcoded the OLD off-by-default policy; updated to assert the new correct default.
    overlay = tmp_path / "surface-state.json"
    monkeypatch.setenv("AO_SURFACE_STATE", str(overlay))

    # With GR-5 reversal, the committed registry now has surfaces ON by default, so they are enumerated
    monkeypatch.delenv("AO_SURFACE_REGISTRY", raising=False)
    response = build_app(repo_root=REPO_ROOT).handle("GET", "/api/healthz/ready")
    assert response.status == 200
    assert response.payload["data"]["surfaces"][SURFACE]["state"] == SURFACE_READY

    monkeypatch.setenv("AO_SURFACE_REGISTRY", str(promoted_registry))
    response = build_app(repo_root=REPO_ROOT).handle("GET", "/api/healthz/ready")
    assert response.status == 200
    assert response.payload["data"]["surfaces"][SURFACE]["state"] == SURFACE_READY

    monkeypatch.setenv("AO_SURFACE_REGISTRY", str(tmp_path / "registry-broken.yaml"))
    (tmp_path / "registry-broken.yaml").write_text("surfaces: [nope\n", encoding="utf-8")
    response = build_app(repo_root=REPO_ROOT).handle("GET", "/api/healthz/ready")
    assert response.status == 503
    assert response.payload["data"]["state"] == SURFACE_CANNOT_ASSESS
    assert response.payload["data"]["surfaces"] == {}


def test_the_rollout_declaration_carries_the_console_surface() -> None:
    """Without the state row the engine refuses the flag by name — nothing to drive."""
    assert check_rollout_all() == []
    document = yaml.safe_load(
        (REPO_ROOT / "infra" / "rollout" / "rollout-state.yaml").read_text(encoding="utf-8")
    )
    assert document["flags"][FLAG]["stage"] == "off"
    plan = yaml.safe_load(
        (REPO_ROOT / "infra" / "rollout" / "go-live-plan.yaml").read_text(encoding="utf-8")
    )
    planned = [row["flag"] for phase in plan["phases"].values() for row in phase["surfaces"]]
    assert FLAG in planned
