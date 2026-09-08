"""Routing policy + route resolution tests (issue #16, criterion 2)."""

from __future__ import annotations

import pytest

from proxy.contract import (
    CapabilityDeniedError,
    NoHealthyRouteError,
    RoutingConfigError,
    UnknownTaskRouteError,
)
from proxy.model import TaskRequest, TierChoice
from proxy.router import Router, load_routing_config

from support import FakeChooser, make_agent, make_task


@pytest.fixture(scope="module")
def router() -> Router:
    return Router()


class TestRoutingPolicyTable:
    def test_routes_task_types_to_capability_and_task_class(self, router):
        route = router.config.route_for("classify-route")
        assert (route.capability, route.task_class) == ("orchestrate", "classify-route")
        assert router.config.route_for("code-review-verdict").capability == "code-review"
        assert router.config.route_for("summarize").capability == "research"

    def test_unknown_task_type_refused(self, router):
        with pytest.raises(UnknownTaskRouteError):
            router.config.route_for("ad-hoc-inline")

    def test_tier_map_ladder_to_registry(self, router):
        assert router.config.map_tier("L0") == "LOW"
        assert router.config.map_tier("L1") == "MED"
        assert router.config.map_tier("L2") == "HIGH"

    def test_config_load_fails_closed_on_missing_file(self):
        with pytest.raises(RoutingConfigError):
            load_routing_config(__file__ + ".does-not-exist.yaml")

    def test_default_config_is_loadable(self):
        config = load_routing_config()
        assert config.schema_version == 1
        assert set(config.provider_chains) == {"LOW", "MED", "HIGH", "MAX"}
        assert config.provider_chains["LOW"][-1] == "ollama"  # local terminal hop


class TestRouteResolution:
    def test_full_resolution_l0(self, router):
        choice = TierChoice(task_class="classify-route", tier="L0")
        decision = router.route(
            make_task("classify-route"),
            make_agent("orchestrator", capabilities=frozenset({"orchestrate"})),
            TaskRequest(tenant_id="acme", task_type="classify-route"),
            chooser=FakeChooser(choice=choice),
        )
        assert decision.task_type == "classify-route"
        assert decision.capability == "orchestrate"
        assert decision.task_class == "classify-route"
        assert decision.ladder_tier == "L0"
        assert decision.registry_tier == "LOW"
        assert decision.candidate_providers() == ("deepseek", "openai", "ollama")
        roles = {c.provider: c.role for c in decision.candidates}
        assert roles["deepseek"] == "primary"
        assert roles["openai"] == "fallback"
        assert roles["ollama"] == "local"

    def test_l1_maps_to_med(self, router):
        choice = TierChoice(task_class="code-review", tier="L1")
        decision = router.route(
            make_task("code-review-verdict", schema={"type": "object"},
                      hint="med"),
            make_agent("reviewer", capabilities=frozenset({"code-review"})),
            TaskRequest(tenant_id="acme", task_type="code-review-verdict"),
            chooser=FakeChooser(choice=choice),
        )
        assert decision.registry_tier == "MED"
        assert decision.candidate_providers() == ("deepseek", "anthropic", "ollama")

    def test_capability_boundary_fails_closed(self, router):
        choice = TierChoice(task_class="classify-route", tier="L0")
        with pytest.raises(CapabilityDeniedError) as excinfo:
            router.route(
                make_task("classify-route"),
                make_agent("coder", capabilities=frozenset({"code-author"})),
                TaskRequest(tenant_id="acme", task_type="classify-route"),
                chooser=FakeChooser(choice=choice),
            )
        assert "orchestrate" in str(excinfo.value)

    def test_chooser_provider_hoisted_to_primary(self, router):
        choice = TierChoice(task_class="research", tier="L0", provider="openai")
        decision = router.route(
            make_task("summarize", schema={"type": "object"}, hint="med"),
            make_agent("researcher", capabilities=frozenset({"research"})),
            TaskRequest(tenant_id="acme", task_type="summarize"),
            chooser=FakeChooser(choice=choice),
        )
        # the chooser's preferred provider leads the chain
        assert decision.candidate_providers()[0] == "openai"


class TestHealthFilter:
    def test_primary_unhealthy_falls_to_fallback(self, router):
        choice = TierChoice(task_class="classify-route", tier="L0")
        decision = router.route(
            make_task("classify-route"),
            make_agent(),
            TaskRequest(tenant_id="acme", task_type="classify-route"),
            chooser=FakeChooser(choice=choice),
            health={"deepseek": False},
        )
        assert decision.candidate_providers() == ("openai", "ollama")

    def test_all_unhealthy_is_explicit_failure(self, router):
        choice = TierChoice(task_class="classify-route", tier="L0")
        with pytest.raises(NoHealthyRouteError):
            router.route(
                make_task("classify-route"),
                make_agent(),
                TaskRequest(tenant_id="acme", task_type="classify-route"),
                chooser=FakeChooser(choice=choice),
                health={"deepseek": False, "openai": False, "ollama": False},
            )

    def test_absent_health_signal_means_all_healthy(self, router):
        choice = TierChoice(task_class="classify-route", tier="L0")
        decision = router.route(
            make_task("classify-route"),
            make_agent(),
            TaskRequest(tenant_id="acme", task_type="classify-route"),
            chooser=FakeChooser(choice=choice),
            health=None,
        )
        assert len(decision.candidates) == 3

    def test_health_predicate_callable(self, router):
        choice = TierChoice(task_class="classify-route", tier="L0")
        decision = router.route(
            make_task("classify-route"),
            make_agent(),
            TaskRequest(tenant_id="acme", task_type="classify-route"),
            chooser=FakeChooser(choice=choice),
            health=lambda provider: provider == "ollama",
        )
        assert decision.candidate_providers() == ("ollama",)
