"""gateway/sync live head-agent projection (issue #889)."""

from __future__ import annotations

import pytest

from sync.live import HEAD_AGENT_ID, REACHABLE, UNKNOWN, UNREACHABLE, UnknownCatalogModule, project


class _FakeMonitor:
    def __init__(self, healthy_map):
        self._healthy_map = healthy_map

    def is_healthy(self, provider, model):
        return self._healthy_map[(provider, model)]


def test_project_reads_the_real_catalog_file(fixture_gateway_root):
    doc = project(fixture_gateway_root)
    assert doc["head_agent"] == HEAD_AGENT_ID
    assert doc["is_head"] is True
    assert doc["catalog_id"] == "hermes"
    assert doc["provider"] == "hermes"


def test_reachability_reported_unknown_without_a_live_monitor(fixture_gateway_root):
    doc = project(fixture_gateway_root)
    assert doc["reachability"] == UNKNOWN


def test_reachability_reflects_a_live_healthy_monitor(fixture_gateway_root):
    monitor = _FakeMonitor({("hermes", "hermes3"): True})
    doc = project(fixture_gateway_root, monitor=monitor)
    assert doc["reachability"] == REACHABLE


def test_reachability_reflects_a_live_unhealthy_monitor(fixture_gateway_root):
    monitor = _FakeMonitor({("hermes", "hermes3"): False})
    doc = project(fixture_gateway_root, monitor=monitor)
    assert doc["reachability"] == UNREACHABLE


def test_unknown_module_id_is_refused_by_name(fixture_gateway_root):
    """Negative control: a module with no module.json is refused, not silently unreachable."""
    with pytest.raises(UnknownCatalogModule) as excinfo:
        project(fixture_gateway_root, module_id="not-a-real-module")
    assert "not-a-real-module" in str(excinfo.value)


def test_a_non_head_module_reports_is_head_false(fixture_gateway_root):
    ollama_dir = fixture_gateway_root / "catalog" / "modules" / "ollama"
    ollama_dir.mkdir(parents=True)
    (ollama_dir / "module.json").write_text(
        '{"schema": "cmr.module/v1", "id": "ollama", "class": ["provider"], '
        '"distribution": {"package": "gateway.providers.ollama"}}',
        encoding="utf-8",
    )
    doc = project(fixture_gateway_root, module_id="ollama")
    assert doc["is_head"] is False
    assert doc["head_agent"] == "ollama"
