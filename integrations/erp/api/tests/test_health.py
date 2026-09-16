"""The health read is real, and it is honest (#651).

The paperclip precedent's rule applies here: a health route that says ``ok``
unconditionally measures nothing. This one reads three dependencies and can answer
``503``; the tests below check that it reads the *real* ones, that a lie is
refused, and that an unreachable dependency actually changes the answer a client
sees.
"""

from __future__ import annotations

import pytest

from integrations.erp.api import health as health_module
from integrations.erp.api import openapi


def test_the_read_covers_the_dependencies_the_surface_names(root, model, declarations):
    report = health_module.health(root, model=model, role_map=declarations.role_map)
    assert report.status == health_module.STATUS_OK
    assert report.http_status == 200
    assert {state.name for state in report.dependencies} == set(health_module.PROBES)
    assert all(state.state == health_module.STATE_OK for state in report.dependencies)


def test_the_report_is_honest(root, model, declarations):
    report = health_module.health(root, model=model, role_map=declarations.role_map)
    assert (
        health_module.check_report(
            report, root, model=model, role_map=declarations.role_map
        )
        == ()
    )


def test_a_lying_report_is_refused_by_name(root, model, declarations):
    """The honesty control: a report that claims ok while a reading says otherwise."""
    probes = (
        lambda *_args: health_module.DependencyState(
            "erp-02-model", health_module.STATE_MISSING, "provoked absent"
        ),
        lambda *_args: health_module.DependencyState(
            "auth-declarations", health_module.STATE_OK, "readable"
        ),
        lambda *_args: health_module.DependencyState(
            "openapi-artifact", health_module.STATE_OK, "byte-identical"
        ),
    )
    lying = health_module.HealthReport(
        status=health_module.STATUS_OK,
        http_status=200,
        dependencies=(
            health_module.DependencyState("erp-02-model", health_module.STATE_OK, "claimed ok"),
            health_module.DependencyState("auth-declarations", health_module.STATE_OK, "claimed ok"),
            health_module.DependencyState("openapi-artifact", health_module.STATE_OK, "claimed ok"),
        ),
    )
    findings = health_module.check_report(
        lying, root, model=model, role_map=declarations.role_map, probes=probes
    )
    assert any("'erp-02-model'" in finding and "ok while" in finding for finding in findings), findings


def test_a_report_that_omits_a_dependency_is_refused(root, model, declarations):
    partial = health_module.HealthReport(
        status=health_module.STATUS_OK,
        http_status=200,
        dependencies=(
            health_module.DependencyState("erp-02-model", health_module.STATE_OK, "readable"),
        ),
    )
    findings = health_module.check_report(partial, root, model=model, role_map=declarations.role_map)
    assert any("omits the dependency" in finding for finding in findings), findings


def test_a_missing_dependency_makes_the_route_503(world, monkeypatch):
    """An unreachable dependency is a 503 through the surface's own `unavailable`."""
    monkeypatch.setattr(
        health_module,
        "PROBES",
        {
            "erp-02-model": lambda *_args: health_module.DependencyState(
                "erp-02-model", health_module.STATE_MISSING, "provoked absent"
            ),
            "auth-declarations": health_module.probe_declarations,
            "openapi-artifact": health_module.probe_artifact,
        },
    )
    refused = world.get("/v1/erp/health")
    assert refused["status"] == 503
    assert refused["error"]["code"] == "unavailable"
    assert refused["error"]["details"]["reason"] == "health-unhealthy"
    assert refused["error"]["details"]["status"] == health_module.STATUS_UNHEALTHY


def test_a_stale_dependency_degrades_but_answers_200(world, monkeypatch):
    """Stale is readable: the route answers, carrying the real status."""
    monkeypatch.setattr(
        health_module,
        "PROBES",
        {
            "erp-02-model": lambda *_args: health_module.DependencyState(
                "erp-02-model", health_module.STATE_STALE, "provoked stale"
            ),
            "auth-declarations": health_module.probe_declarations,
            "openapi-artifact": health_module.probe_artifact,
        },
    )
    answered = world.get("/v1/erp/health")
    assert answered["status"] == 200
    assert answered["data"]["status"] == health_module.STATUS_DEGRADED


def test_a_stale_artifact_is_a_stale_dependency(tmp_path, model, declarations):
    """Point the probe at a tree whose artifact is missing: the reading says so."""
    state = health_module.probe_artifact(tmp_path, model, declarations.role_map)
    assert state.state == health_module.STATE_MISSING
    assert openapi.EMITTED_ARTIFACT.as_posix() in state.detail


def test_an_uncovered_kind_is_a_missing_declaration(root, model):
    """A role map that does not cover every kind is a broken dependency, not a policy."""

    class _Map:
        kinds = ()

    state = health_module.probe_declarations(root, model, _Map())
    assert state.state == health_module.STATE_MISSING
    assert "covers no kind for" in state.detail


def test_the_health_component_is_derived_from_the_report():
    """The document's HealthReport shape is the module's own, not a parallel description."""
    schema = openapi.health_component()
    assert schema["title"] == "HealthReport"
    assert set(schema["properties"]) == set(
        health_module.HealthReport(
            status="ok",
            http_status=200,
            dependencies=(health_module.DependencyState("n", "ok", "d"),),
        ).to_dict()
    )
    assert set(schema["properties"]["dependencies"]["items"]["properties"]) == {
        "name",
        "state",
        "detail",
    }


@pytest.mark.parametrize("status_name", ["STATUS_OK", "STATUS_DEGRADED", "STATUS_UNHEALTHY"])
def test_the_statuses_are_distinct(status_name):
    assert getattr(health_module, status_name)
    assert len({health_module.STATUS_OK, health_module.STATUS_DEGRADED, health_module.STATUS_UNHEALTHY}) == 3
