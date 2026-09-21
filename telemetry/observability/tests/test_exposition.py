"""telemetry/observability — exposition exporter tests (issue #497, ADR-0022).

These tests are the acceptance, not decoration.  Four of them are the frozen
contract ADR-0022 states, and each is written so that the corresponding
*defect* turns it red:

* ``TestVocabularyProvenance`` — the exported vocabulary is produced by
  ``telemetry/observability/slos.py``.  A second literal verdict or kind
  declared at this boundary must fail the static scan, and the module must
  reference the evaluator's own closed sets by identity.
* ``TestNoSilentCoercion`` — an unhealthy verdict leaves the process unchanged.
  Coercing a breach into a healthy verdict must fail.
* ``TestBoundedCardinality`` — the label set is exactly the ADR's five
  dimensions.  Adding a refused, id-shaped dimension must fail.
* ``TestTenantScoping`` — a per-tenant payload never carries another tenant's
  identifiers.

Everything else covers the operational contract: the GR-5 flag gate, inertness
when unconfigured, the periodic render that keeps an idle window visible, honest
delivery reporting, and the fact that no HTTP exposition route exists at all.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from telemetry.observability import exposition, slos
from telemetry.observability.exposition import (
    ENV_ENDPOINT,
    ENV_HEADERS,
    ENV_INTERVAL_SECONDS,
    EXPOSITION_SURFACE,
    IDENTITY_LABELS,
    PRODUCER_SERVICE,
    REFUSED_IDENTITY_LABELS,
    REASON_DELIVERED,
    REASON_FLAG_OFF,
    REASON_REJECTED,
    REASON_TRANSPORT_ERROR,
    REASON_UNCONFIGURED,
    ExpositionConfig,
    ExpositionInputs,
    RecordingTransport,
    TelemetryExposition,
    assert_identity_labels,
    budget_state_of,
    declared_tenants,
    parse_headers,
    read_surface_default,
    surface_enabled,
    usage_state_of,
)
from telemetry.observability.slos import (
    SloDefinition,
    SloEvaluator,
    SloResult,
    definitions_from_templates,
    load_slo_templates,
)
from telemetry.observability.store import TraceStore
from telemetry.observability.tests.conftest import REPO_ROOT

WINDOW_START = "2026-09-14T00:00:00Z"
WINDOW_END = "2026-09-14T01:00:00Z"
REGISTRY_RELATIVE = Path("infra") / "feature-flags" / "registry.yaml"

#: The declared SLO template ids, read from the packaged template directory —
#: the same bounded set the exporter is allowed to label ``slo_id`` with.
TEMPLATE_IDS = tuple(
    t.name for t in load_slo_templates(str(Path(REPO_ROOT) / "telemetry" / "observability" / "slo_templates"))
)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def definition(
    tenant: str = "acme",
    *,
    name: str = "availability-requests",
    kind: str = slos.KIND_AVAILABILITY,
    **overrides,
):
    """A declared, per-tenant SLO definition built through the real model."""
    fields = {
        "name": f"{name}:{tenant}",
        "tenant_id": tenant,
        "kind": kind,
        "window_seconds": 3600,
    }
    if kind == slos.KIND_AVAILABILITY:
        fields["target_ratio"] = 0.99
    elif kind == slos.KIND_LATENCY:
        fields["target_ms"] = 250.0
    else:
        fields["budget_usd"] = 10.0
    fields.update(overrides)
    return SloDefinition(**fields)


def result(
    *,
    tenant: str = "acme",
    name: str = "availability-requests",
    kind: str = slos.KIND_AVAILABILITY,
    verdict: str,
    **overrides,
):
    """A real ``SloResult`` carrying the evaluator's own verdict token."""
    base = {
        "definition": definition(tenant, name=name, kind=kind),
        "window_start_iso": WINDOW_START,
        "window_end_iso": WINDOW_END,
        "verdict": verdict,
        "has_window_data": verdict != slos.VERDICT_NO_DATA,
        "missed_window": verdict == slos.VERDICT_NO_DATA,
    }
    base.update(overrides)
    return SloResult(**base)


def inputs(*results, tenants=("acme",), slo_ids=None, **kwargs):
    return ExpositionInputs.build(
        tenants=tenants,
        slo_ids=slo_ids if slo_ids is not None else TEMPLATE_IDS,
        slo_results=results,
        **kwargs,
    )


def exporter(**kwargs):
    """An exporter that is flag-ON, configured and non-networking by default."""
    kwargs.setdefault("repo_root", REPO_ROOT)
    kwargs.setdefault("enabled", True)
    kwargs.setdefault(
        "config", ExpositionConfig(endpoint="https://plane.example/otlp")
    )
    transport = kwargs.setdefault("transport", RecordingTransport())
    kwargs.setdefault("clock", lambda: 1_760_000_000.0)
    built = TelemetryExposition(**kwargs)
    return built, transport


def label_sets(payload):
    """Every datapoint's label map from an OTLP metrics envelope."""
    out = []
    for resource in payload["resourceMetrics"]:
        for scope in resource["scopeMetrics"]:
            for metric in scope["metrics"]:
                for point in metric["gauge"]["dataPoints"]:
                    out.append({a["key"]: a["value"]["stringValue"] for a in point["attributes"]})
    return out


def values_for(payload, metric):
    """Every ``asDouble`` of one metric."""
    return [
        point["asDouble"]
        for resource in payload["resourceMetrics"]
        for scope in resource["scopeMetrics"]
        for item in scope["metrics"]
        if item["name"] == metric
        for point in item["gauge"]["dataPoints"]
    ]


def _non_docstring_strings(source: str) -> list[str]:
    """Every string literal in ``source`` that is not a docstring.

    Docstrings are documentation; a word in prose is not a second declaration.
    A string in *code* — an assignment, a comparison, a return — would be.  The
    module, its classes and its functions may each own a docstring, and those
    are the only constants excluded.
    """
    tree = ast.parse(source)
    docstrings = set()
    owners = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    for node in ast.walk(tree):
        if isinstance(node, owners) and node.body:
            first = node.body[0]
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                docstrings.add(id(first.value))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def code_strings() -> list[str]:
    """The code-level string literals of the module under test."""
    return _non_docstring_strings(
        Path(exposition.__file__).read_text(encoding="utf-8")
    )


# --------------------------------------------------------------------------- #
# D4 — the vocabulary is imported, never re-declared
# --------------------------------------------------------------------------- #
class TestVocabularyProvenance:
    """ADR-0022 D4.1/D4.3 — the export renders slos.py's vocabulary."""

    @staticmethod
    def _non_docstring_strings(source: str) -> list[str]:
        """The module-level helper, exposed on the class for readability."""
        return _non_docstring_strings(source)

    def test_no_second_literal_for_a_kind_or_a_verdict(self):
        """A verdict/kind literal in the exporter's *code* is a defect."""
        forbidden = set(slos.VERDICTS) | set(slos.SLO_KINDS)
        found = sorted({v for v in code_strings() if v in forbidden})
        assert found == [], (
            "the exposition boundary re-declares vocabulary owned by "
            f"telemetry/observability/slos.py: {found}"
        )

    def test_verdict_and_kind_sets_are_the_evaluators_own_objects(self):
        """Identity, not equality: the exporter holds no parallel definition."""
        assert exposition.slos.VERDICTS is slos.VERDICTS
        assert exposition.slos.SLO_KINDS is slos.SLO_KINDS
        assert exposition.slos.VERDICT_BREACHED is slos.VERDICT_BREACHED
        assert exposition.slos.VERDICT_NO_DATA is slos.VERDICT_NO_DATA

    def test_every_emitted_verdict_is_a_member_of_the_closed_set(self):
        built, _ = exporter()
        payload = built.render(
            inputs(
                result(verdict=slos.VERDICT_OK),
                result(verdict=slos.VERDICT_AT_RISK),
                result(tenant="nimbus", verdict=slos.VERDICT_BREACHED),
                tenants=("acme", "nimbus"),
            ),
            now=1_760_000_000.0,
        ).metrics
        emitted = {labels["verdict"] for labels in label_sets(payload)}
        assert emitted <= set(slos.VERDICTS)
        assert emitted == {
            slos.VERDICT_OK,
            slos.VERDICT_AT_RISK,
            slos.VERDICT_BREACHED,
        }

    def test_verdicts_come_from_the_real_evaluator(self):
        """End to end: the tokens the plane receives are the evaluator's own."""
        built, _ = exporter()
        templates = load_slo_templates(
            str(Path(REPO_ROOT) / "telemetry" / "observability" / "slo_templates")
        )
        defs = definitions_from_templates(templates, ["acme"])
        results = SloEvaluator(TraceStore(spans=[])).evaluate_all(
            defs, reference_ts_iso=WINDOW_END
        )
        assert results, "the packaged templates must yield definitions"
        rendered = built.render(
            inputs(*results, slo_ids=[t.name for t in templates]),
            now=1_760_000_000.0,
        )
        emitted = {
            dict(item.labels)["verdict"]
            for item in rendered.series
            if item.metric == "ao.telemetry.slo.verdict"
        }
        # An empty store cannot evaluate anything, so every window is NO_DATA —
        # and NO_DATA is published, never omitted and never read as health.
        assert emitted == {slos.VERDICT_NO_DATA}
        assert emitted <= set(slos.VERDICTS)

    def test_measurements_travel_as_measurements_not_verdicts(self):
        """A verdict is a label; a ratio is a number.  Never the other way."""
        built, _ = exporter()
        payload = built.render(
            inputs(result(verdict=slos.VERDICT_BREACHED, measured_ratio=0.5)),
            now=1_760_000_000.0,
        ).metrics
        assert values_for(payload, "ao.telemetry.slo.verdict") == [1.0]
        assert values_for(payload, "ao.telemetry.slo.measured_ratio") == [0.5]


# --------------------------------------------------------------------------- #
# D4.2/D4.4 — no silent coercion, NO_DATA is first-class
# --------------------------------------------------------------------------- #
class TestNoSilentCoercion:
    def test_breached_leaves_as_breached(self):
        built, _ = exporter()
        rendered = built.render(
            inputs(result(verdict=slos.VERDICT_BREACHED)), now=1_760_000_000.0
        )
        assert built.render_verdict(
            result(verdict=slos.VERDICT_BREACHED)
        ) == slos.VERDICT_BREACHED
        labels = [dict(item.labels) for item in rendered.series]
        assert labels, "a breached SLO must still produce series"
        assert {row["verdict"] for row in labels} == {slos.VERDICT_BREACHED}
        assert slos.VERDICT_BREACHED != slos.VERDICT_OK

    def test_no_data_leaves_as_no_data_and_is_never_dropped(self):
        built, _ = exporter()
        rendered = built.render(
            inputs(result(verdict=slos.VERDICT_NO_DATA)), now=1_760_000_000.0
        )
        verdict_series = [
            item
            for item in rendered.series
            if item.metric == "ao.telemetry.slo.verdict"
        ]
        assert len(verdict_series) == 1
        assert verdict_series[0].label_map["verdict"] == slos.VERDICT_NO_DATA
        assert values_for(rendered.metrics, "ao.telemetry.slo.verdict") == [1.0]

    def test_a_verdict_outside_the_closed_set_is_refused(self):
        """The boundary never invents a verdict the evaluator never defined."""
        built, _ = exporter()
        with pytest.raises(ValueError):
            built.render(inputs(result(verdict="FINE")), now=1_760_000_000.0)

    def test_a_missed_window_is_published_not_silenced(self):
        built, _ = exporter()
        rendered = built.render(
            inputs(result(verdict=slos.VERDICT_NO_DATA, missed_window=True)),
            now=1_760_000_000.0,
        )
        assert values_for(rendered.metrics, "ao.telemetry.slo.missed_window") == [1.0]
        assert values_for(rendered.metrics, "ao.telemetry.slo.window_data") == [0.0]


# --------------------------------------------------------------------------- #
# D5 — the bounded label set
# --------------------------------------------------------------------------- #
class TestBoundedCardinality:
    def test_every_series_carries_exactly_the_identity_labels(self):
        built, _ = exporter()
        rendered = built.render(
            inputs(
                result(verdict=slos.VERDICT_OK),
                result(tenant="nimbus", verdict=slos.VERDICT_AT_RISK),
            ),
            now=1_760_000_000.0,
        )
        assert rendered.series
        for item in rendered.series:
            assert set(item.label_keys) == set(IDENTITY_LABELS)
        for labels in label_sets(rendered.metrics):
            assert set(labels) == set(IDENTITY_LABELS)

    def test_a_refused_id_shaped_label_is_a_defect(self):
        assert_identity_labels(
            (("service", PRODUCER_SERVICE), ("slo_kind", "availability"),
             ("slo_id", "availability-requests"), ("verdict", "OK"),
             ("tenant", "acme"))
        )
        with pytest.raises(ValueError):
            assert_identity_labels(
                (("service", PRODUCER_SERVICE), ("slo_kind", "availability"),
                 ("slo_id", "availability-requests"), ("verdict", "OK"),
                 ("tenant", "acme"), ("session_id", "sess-1"))
            )
        with pytest.raises(ValueError):
            assert_identity_labels(
                (("service", PRODUCER_SERVICE), ("slo_kind", "availability"))
            )

    def test_every_refused_shape_is_actually_refused(self):
        for name in sorted(REFUSED_IDENTITY_LABELS):
            with pytest.raises(ValueError):
                assert_identity_labels(
                    (("service", PRODUCER_SERVICE), ("slo_kind", "availability"),
                     ("slo_id", "availability-requests"), ("verdict", "OK"),
                     ("tenant", "acme"), (name, "x"))
                )

    def test_an_undeclared_tenant_is_refused_not_exported(self):
        built, _ = exporter()
        rendered = built.render(
            inputs(result(tenant="rogue", verdict=slos.VERDICT_OK), tenants=("acme",)),
            now=1_760_000_000.0,
        )
        assert rendered.series == ()
        assert rendered.refused_tenants == ("rogue",)

    def test_an_undeclared_slo_id_is_refused(self):
        built, _ = exporter()
        rendered = built.render(
            inputs(
                result(name="totally-made-up", verdict=slos.VERDICT_OK),
                slo_ids=TEMPLATE_IDS,
            ),
            now=1_760_000_000.0,
        )
        assert rendered.series == ()
        assert rendered.refused_slo_ids == ("totally-made-up:acme",)

    def test_a_declared_tenant_and_template_produces_a_bounded_series(self):
        built, _ = exporter()
        rendered = built.render(
            inputs(
                result(verdict=slos.VERDICT_OK),
                result(name="latency-p95", kind=slos.KIND_LATENCY,
                       verdict=slos.VERDICT_BREACHED),
            ),
            now=1_760_000_000.0,
        )
        ids = {item.label_map["slo_id"] for item in rendered.series}
        assert ids == {"availability-requests", "latency-p95"}


# --------------------------------------------------------------------------- #
# tenant scoping — no cross-tenant leak
# --------------------------------------------------------------------------- #
class TestTenantScoping:
    def test_a_tenants_payload_never_names_another_tenant(self):
        built, _ = exporter()
        rendered = built.render(
            inputs(
                result(tenant="acme", verdict=slos.VERDICT_OK),
                result(tenant="nimbus", verdict=slos.VERDICT_BREACHED),
                tenants=("acme", "nimbus"),
            ),
            now=1_760_000_000.0,
        )
        for item in rendered.series:
            if item.label_map["tenant"] == "acme":
                assert "nimbus" not in json.dumps(item.label_map, sort_keys=True)

    def test_a_tenants_log_record_never_names_another_tenant(self):
        built, _ = exporter()
        records = built.tenant_log_records(
            inputs(
                tenants=("acme", "nimbus"),
                budget_state={
                    "tenants": {
                        "acme": {"budget": {"mode": "enforce", "limits": {}}},
                        "nimbus": {"budget": {"mode": "observe", "limits": {}}},
                    }
                },
            )
        )
        acme = [r for r in records if r["attributes"].get("tenant") == "acme"]
        nimbus = [r for r in records if r["attributes"].get("tenant") == "nimbus"]
        assert acme and nimbus
        assert "nimbus" not in json.dumps(acme, sort_keys=True)
        assert "acme" not in json.dumps(nimbus, sort_keys=True)

    def test_an_undeclared_tenant_is_absent_from_the_whole_payload(self):
        built, _ = exporter()
        rendered = built.render(
            inputs(
                result(tenant="rogue", verdict=slos.VERDICT_OK),
                tenants=("acme",),
                budget_state={"tenants": {"acme": {"budget": {"mode": "enforce", "limits": {}}}}},
            ),
            now=1_760_000_000.0,
        )
        assert "rogue" not in json.dumps(rendered.metrics, sort_keys=True)
        assert "rogue" not in json.dumps(rendered.logs, sort_keys=True)
        assert rendered.refused_tenants == ("rogue",)


# --------------------------------------------------------------------------- #
# AO-GR-6 — the flag gate. The "flag-gated OFF by default" rule was REVERSED
# by the owner decision of 2026-09-21 (issue #1789): new capabilities ship
# ENABLED by default. Authority: docs/rca/2026-09-21-gr5-enabled-by-default.md.
# The shipped registry now declares this surface `default: on`, so the gate
# asserts the shipped default is ON — and test_the_reader_fails_closed below
# still proves a missing/broken declaration is refused rather than treated as on.
# --------------------------------------------------------------------------- #
class TestFlagGate:
    def test_the_registry_declares_the_surface_on(self):
        # Owner decision 2026-09-21 (#1789,
        # docs/rca/2026-09-21-gr5-enabled-by-default.md): new capabilities ship
        # ENABLED by default, so the shipped registry says on.
        assert read_surface_default(REPO_ROOT) == "on"
        assert surface_enabled(REPO_ROOT) is True
        import yaml

        document = yaml.safe_load((Path(REPO_ROOT) / REGISTRY_RELATIVE).read_text())
        entry = document["surfaces"][EXPOSITION_SURFACE]
        assert entry["default"] in (True, "on")
        assert entry["promoted"] is False
        assert entry["service"] == "telemetry"
        assert entry["tf_flag"] == "enable_telemetry"

    def test_the_reader_fails_closed(self, tmp_path: Path):
        empty = tmp_path / "empty.yaml"
        empty.write_text("schema_version: 1\n", encoding="utf-8")
        assert read_surface_default(tmp_path, registry_path=empty) == "off"
        assert read_surface_default(tmp_path, registry_path=tmp_path / "nope.yaml") == "off"
        broken = tmp_path / "broken.yaml"
        broken.write_text("surfaces: [\n", encoding="utf-8")
        assert read_surface_default(tmp_path, registry_path=broken) == "off"
        on = tmp_path / "on.yaml"
        on.write_text("surfaces:\n  telemetry_exposition:\n    default: 'on'\n")
        assert surface_enabled(tmp_path, registry_path=on) is True

    def test_flag_off_makes_the_exporter_inert_even_when_configured(self):
        built, transport = exporter(enabled=False)
        report = built.export_once(
            inputs(result(verdict=slos.VERDICT_BREACHED))
        )
        assert report.inert
        assert report.rendered is False and report.delivered is False
        assert report.reason == REASON_FLAG_OFF
        assert transport.calls == []

    def test_the_flag_is_read_from_the_registry_when_not_supplied(self):
        built = TelemetryExposition(
            repo_root=REPO_ROOT, environ={ENV_ENDPOINT: "https://plane.example/otlp"}
        )
        assert built.enabled is True  # the shipped registry says on (2026-09-21 policy, #1789)

    def test_promoting_the_flag_enables_the_push(self, tmp_path: Path):
        registry = tmp_path / "registry.yaml"
        registry.write_text(
            "surfaces:\n  telemetry_exposition:\n    default: 'on'\n",
            encoding="utf-8",
        )
        transport = RecordingTransport()
        built = TelemetryExposition(
            repo_root=tmp_path,
            registry_path=registry,
            config=ExpositionConfig(endpoint="https://plane.example/otlp"),
            transport=transport,
        )
        report = built.export_once(inputs(result(verdict=slos.VERDICT_OK)))
        assert report.delivered is True
        assert len(transport.calls) == 2


# --------------------------------------------------------------------------- #
# inert when unconfigured (ADR-0022 D2)
# --------------------------------------------------------------------------- #
class TestInertWhenUnconfigured:
    def test_there_is_no_default_endpoint_in_code(self):
        assert ENV_ENDPOINT.startswith("AO_TELEMETRY_")
        config = ExpositionConfig.from_env({})
        assert config.endpoint is None
        assert config.configured is False

    def test_whitespace_only_endpoint_is_unconfigured(self):
        config = ExpositionConfig.from_env({ENV_ENDPOINT: "   "})
        assert config.endpoint is None
        assert config.configured is False

    def test_unconfigured_exporter_renders_nothing_and_sends_nothing(self):
        transport = RecordingTransport()
        built = TelemetryExposition(
            repo_root=REPO_ROOT, enabled=True, config=ExpositionConfig(), transport=transport
        )
        report = built.export_once(inputs(result(verdict=slos.VERDICT_BREACHED)))
        assert report.inert
        assert report.reason == REASON_UNCONFIGURED
        assert report.rendered is False
        assert transport.calls == [], "an unconfigured exporter must not call out"

    def test_inertness_never_fabricates_a_healthy_signal(self):
        built, _ = exporter(enabled=False)
        report = built.export_once(inputs(result(verdict=slos.VERDICT_NO_DATA)))
        payload = json.dumps(report.__dict__, default=str)
        for token in sorted(slos.VERDICTS):
            assert token not in payload, "an inert exporter publishes no verdict at all"
        assert report.delivered is False

    def test_signal_urls_follow_the_otlp_http_path(self):
        config = ExpositionConfig(endpoint="https://plane.example/otlp")
        assert config.signal_url("metrics") == "https://plane.example/otlp/v1/metrics"
        assert config.signal_url("logs") == "https://plane.example/otlp/v1/logs"
        full = ExpositionConfig(endpoint="https://plane.example/v1/metrics")
        assert full.signal_url("metrics") == "https://plane.example/v1/metrics"

    def test_headers_come_from_the_environment_and_never_land_in_a_report(self):
        config = ExpositionConfig.from_env(
            {
                ENV_ENDPOINT: "https://plane.example/otlp",
                ENV_HEADERS: "authorization=Bearer s3cr3t, x-scope=tenant",
            }
        )
        assert config.headers == (
            ("authorization", "Bearer s3cr3t"),
            ("x-scope", "tenant"),
        )
        assert parse_headers("") == ()
        assert parse_headers("garbage,,=,") == ()
        built = TelemetryExposition(
            repo_root=REPO_ROOT,
            enabled=True,
            config=config,
            transport=RecordingTransport(),
        )
        report = built.export_once(inputs(result(verdict=slos.VERDICT_OK)))
        assert "s3cr3t" not in repr(report)
        assert "s3cr3t" not in str(report.__dict__)


# --------------------------------------------------------------------------- #
# the periodic render
# --------------------------------------------------------------------------- #
class TestPeriodicRender:
    def test_the_loop_re_renders_once_per_interval(self):
        built, transport = exporter(
            config=ExpositionConfig(
                endpoint="https://plane.example/otlp", interval_seconds=30.0
            )
        )
        slept: list[float] = []
        reports = built.run_periodically(
            lambda: inputs(result(verdict=slos.VERDICT_OK)),
            ticks=3,
            sleep=slept.append,
        )
        assert len(reports) == 3
        assert slept == [30.0, 30.0]
        assert len(transport.calls) == 6  # two signals per tick

    def test_an_idle_window_is_published_as_no_data_rather_than_silence(self):
        """The core reason push was chosen over scrape (ADR-0022 D2)."""
        built, transport = exporter(
            config=ExpositionConfig(
                endpoint="https://plane.example/otlp", interval_seconds=5.0
            )
        )
        templates = load_slo_templates(
            str(Path(REPO_ROOT) / "telemetry" / "observability" / "slo_templates")
        )
        defs = definitions_from_templates(templates, ["acme"])
        idle = SloEvaluator(TraceStore(spans=[])).evaluate_all(
            defs, reference_ts_iso=WINDOW_END
        )
        reports = built.run_periodically(
            lambda: inputs(*idle, slo_ids=[t.name for t in templates]),
            ticks=2,
            sleep=lambda _seconds: None,
        )
        assert [r.reason for r in reports] == [REASON_DELIVERED, REASON_DELIVERED]
        for call in transport.calls:
            payload = json.loads(call["body"].decode("utf-8"))
            if "resourceMetrics" not in payload:
                continue
            tokens = {
                labels["verdict"] for labels in label_sets(payload)
            }
            assert tokens == {slos.VERDICT_NO_DATA}

    def test_a_clock_override_keeps_the_render_deterministic(self):
        built, _ = exporter()
        rendered = built.render(
            inputs(result(verdict=slos.VERDICT_OK)), now=1_760_000_000.0
        )
        stamp = rendered.metrics["resourceMetrics"][0]["scopeMetrics"][0]["metrics"][0][
            "gauge"
        ]["dataPoints"][0]["timeUnixNano"]
        assert stamp == str(1_760_000_000 * 1_000_000_000)


# --------------------------------------------------------------------------- #
# honest delivery reporting
# --------------------------------------------------------------------------- #
class TestDeliveryHonesty:
    def test_a_successful_push_names_both_signals(self):
        built, transport = exporter()
        report = built.export_once(inputs(result(verdict=slos.VERDICT_OK)))
        assert report.delivered is True
        assert report.reason == REASON_DELIVERED
        assert report.signals == ("metrics", "logs")
        assert transport.signals == ["metrics", "logs"]
        assert report.datapoints > 0

    def test_a_rejected_push_is_reported_not_retried(self):
        transport = RecordingTransport(status=503)
        built, _ = exporter(transport=transport)
        report = built.export_once(inputs(result(verdict=slos.VERDICT_OK)))
        assert report.delivered is False
        assert report.reason == REASON_REJECTED
        assert report.status_code == 503
        assert len(transport.calls) == 1, "a failed push must not become a retry storm"

    def test_a_transport_error_is_reported_honestly(self):
        class Boom:
            def __init__(self) -> None:
                self.calls = 0

            def post(self, url, body, headers, timeout):  # noqa: ANN001
                self.calls += 1
                raise OSError("connection refused")

        boom = Boom()
        built, _ = exporter(transport=boom)
        report = built.export_once(inputs(result(verdict=slos.VERDICT_BREACHED)))
        assert report.rendered is True
        assert report.delivered is False
        assert report.reason == REASON_TRANSPORT_ERROR
        assert report.detail == "metrics: OSError"
        assert boom.calls == 1

    def test_a_delivery_failure_is_never_a_verdict(self):
        built, _ = exporter(transport=RecordingTransport(status=500))
        report = built.export_once(inputs(result(verdict=slos.VERDICT_BREACHED)))
        assert report.reason == REASON_REJECTED
        for token in sorted(slos.VERDICTS):
            assert token not in repr(report), "delivery is not an SLO verdict"

    def test_the_pushed_body_is_the_otlp_json_encoding(self):
        built, transport = exporter()
        built.export_once(inputs(result(verdict=slos.VERDICT_OK)))
        metrics_body = json.loads(transport.calls[0]["body"].decode("utf-8"))
        logs_body = json.loads(transport.calls[1]["body"].decode("utf-8"))
        assert "resourceMetrics" in metrics_body
        assert "resourceLogs" in logs_body
        scope = metrics_body["resourceMetrics"][0]["scopeMetrics"][0]["scope"]
        assert scope["name"] == exposition.SCOPE_NAME
        resource = metrics_body["resourceMetrics"][0]["resource"]["attributes"]
        assert {"key": "service.name", "value": {"stringValue": PRODUCER_SERVICE}} in resource


# --------------------------------------------------------------------------- #
# consuming the pillar's real feeds
# --------------------------------------------------------------------------- #
def _real_budget_exporter():
    """A ``BudgetStateExporter`` wired to real enforcers (issue #34)."""
    from telemetry.budgets.budget import (
        BudgetEnforcer,
        BudgetLimit,
        TenantBudgetPolicy,
    )
    from telemetry.budgets.exporter import BudgetStateExporter
    from telemetry.budgets.ledger import StaticLedger
    from telemetry.budgets.model import MODE_ENFORCE, today_utc, this_month_utc
    from telemetry.budgets.quota import QuotaEnforcer, QuotaLimit, QuotaPolicy
    from telemetry.budgets.model import RESOURCE_REQUESTS

    ledger = StaticLedger()
    ledger.seed_cost("acme", this_month_utc(), 100.0)
    ledger.seed_calls("acme", today_utc(), 12)
    budget = BudgetEnforcer(
        ledger,
        {
            "acme": TenantBudgetPolicy(
                tenant_id="acme",
                mode=MODE_ENFORCE,
                cost_limit=BudgetLimit(window="month", limit=120.0, warn_at_pct=0.8),
            )
        },
    )
    quota = QuotaEnforcer(
        ledger,
        {
            "acme": QuotaPolicy(
                tenant_id="acme",
                limits={
                    RESOURCE_REQUESTS: QuotaLimit(
                        resource=RESOURCE_REQUESTS,
                        window="day",
                        soft_limit=100,
                        hard_limit=500,
                    )
                },
            )
        },
    )
    return BudgetStateExporter(ledger, budget=budget, quota=quota)


def _real_metering_reporter():
    """A metering ``UsageReporter`` over a real in-memory store (issue #33)."""
    from telemetry.metering.model import UsageRecord
    from telemetry.metering.report import UsageReporter
    from telemetry.metering.store import MemoryUsageStore

    store = MemoryUsageStore()
    store.append(
        UsageRecord(
            tenant_id="acme",
            agent_id="coder-1",
            provider="deepseek",
            model="deepseek-chat",
            route=None,
            outcome="success",
            input_tokens=1000,
            output_tokens=500,
            billable=True,
            metered=True,
            ts="2026-09-14T00:30:00Z",
            source_type="model_call",
            source_key="call-1",
            cost_usd=0.0125,
            cost_source="rate-card",
        )
    )
    return UsageReporter(store)


class TestConsumedFeeds:
    def test_the_budget_document_is_consumed_verbatim(self):
        state = budget_state_of(_real_budget_exporter())
        assert isinstance(state, dict)
        assert "acme" in state["tenants"]
        assert state["tenants"]["acme"]["budget"]["mode"] == "enforce"

    def test_a_non_exporter_is_not_mistaken_for_one(self):
        assert budget_state_of(object()) is None
        assert usage_state_of(object()) is None

    def test_the_usage_aggregate_is_consumed_verbatim(self):
        state = usage_state_of(_real_metering_reporter())
        assert isinstance(state, dict)
        assert state["acme"]["calls"] == 1
        assert state["acme"]["totalTokens"] == 1500
        assert state["acme"]["costUsd"] == pytest.approx(0.0125)

    def test_the_declared_registry_comes_from_the_declared_sources(self):
        tenants = declared_tenants(
            budget_state={"tenants": {"acme": {}, "nimbus": {}}},
            usage_state={"acme": {}},
            extra=["acme"],
        )
        assert tenants == ("acme", "nimbus")

    def test_the_whole_export_carries_all_three_feeds(self):
        built, transport = exporter()
        budget = budget_state_of(_real_budget_exporter())
        usage = usage_state_of(_real_metering_reporter())
        report = built.export_once(
            inputs(
                result(verdict=slos.VERDICT_AT_RISK),
                tenants=declared_tenants(budget_state=budget, usage_state=usage),
                budget_state=budget,
                usage_state=usage,
            )
        )
        assert report.delivered is True
        logs = json.loads(transport.calls[1]["body"].decode("utf-8"))
        records = logs["resourceLogs"][0]["scopeLogs"][0]["logRecords"]
        kinds = {
            dict(
                (a["key"], a["value"].get("stringValue"))
                for a in record["attributes"]
            ).get("snapshot_kind")
            for record in records
        }
        assert kinds == {"budget", "quota", "usage"}

    def test_the_audit_record_carries_counts_only(self):
        """The audit feed's actor/subject detail is identity — refused here."""
        from telemetry.budgets.audit import MemoryAuditStore, record_decision
        from telemetry.budgets.model import this_month_utc

        audit = MemoryAuditStore()
        enforcer = _real_budget_exporter()
        enforcer.audit = audit
        decision = enforcer.budget.check(
            "acme", requested_cost_usd=0.1, month=this_month_utc()
        )
        record_decision(audit, decision)
        built, transport = exporter()
        state = budget_state_of(enforcer)
        assert state is not None and state["audit"]["totalEvents"] == 1
        built.export_once(
            inputs(
                result(verdict=slos.VERDICT_OK),
                tenants=("acme",),
                budget_state=state,
            )
        )
        logs = json.loads(transport.calls[1]["body"].decode("utf-8"))
        records = logs["resourceLogs"][0]["scopeLogs"][0]["logRecords"]
        audit_records = [
            r
            for r in records
            if any(
                a["key"] == "snapshot_kind"
                and a["value"].get("stringValue") == "budget_audit"
                for a in r["attributes"]
            )
        ]
        assert len(audit_records) == 1
        keys = {a["key"] for a in audit_records[0]["attributes"]}
        assert keys == {"service", "snapshot_kind", "total_events", "decision_counts"}
        assert "recent" not in json.dumps(audit_records[0])


# --------------------------------------------------------------------------- #
# ADR-0022 D2 — there is no HTTP exposition route in this repo
# --------------------------------------------------------------------------- #
class TestNoHttpRoute:
    def test_the_module_registers_no_server_and_no_metrics_route(self):
        strings = code_strings()
        for forbidden in ("BaseHTTPRequestHandler", "HTTPServer", "make_server",
                          "http.server", "/metrics", "app.route", "add_url_rule"):
            assert forbidden not in strings, (
                f"ADR-0022 fixes push, not pull: {forbidden!r} must not appear"
            )
        source = Path(exposition.__file__).read_text(encoding="utf-8")
        for forbidden in ("BaseHTTPRequestHandler", "HTTPServer", "make_server",
                          "app.route", "add_url_rule"):
            assert forbidden not in source

    def test_the_transport_only_ever_posts(self):
        source = Path(exposition.__file__).read_text(encoding="utf-8")
        assert re.findall(r'method="([A-Z]+)"', source) == ["POST"]
