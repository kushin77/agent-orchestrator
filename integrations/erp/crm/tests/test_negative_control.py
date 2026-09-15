"""The negative-control driver itself (issue #650, GR-12).

A driver that only ever reports OK proves nothing about the controls it claims
to run. These tests therefore measure three things about it: that it covers the
whole refusal vocabulary, that it refuses *by name*, and — the one that matters
most — that it **turns non-zero when a validator is neutered**. Each mutation
makes one real validator stop refusing and requires the driver to notice.
"""

from __future__ import annotations

import io

import pytest

from integrations.erp.crm import audit, negative_control, schema, workflow
from integrations.erp.crm.model import ACTIONS, REFUSALS, Document


def test_the_driver_passes_on_the_shipped_module() -> None:
    sink = io.StringIO()
    assert negative_control.run(sink) == 0
    output = sink.getvalue()
    assert "negative-control: OK" in output
    assert "27 refusal code(s)" in output


def test_every_refusal_is_named_in_the_drivers_output() -> None:
    sink = io.StringIO()
    negative_control.run(sink)
    output = sink.getvalue()
    missed = sorted(code for code in REFUSALS if f"  OK    {code} refused by name:" not in output)
    assert missed == []


def test_the_provoked_set_is_exactly_the_refusal_vocabulary() -> None:
    """A new refusal added without a control fails here, not in review."""
    assert negative_control.covered_codes() == frozenset(REFUSALS)


def test_every_provocation_declares_a_code_from_the_vocabulary_and_a_needle() -> None:
    checks = negative_control.provocations(negative_control.flows.golden_path("test"))
    assert len({check.name for check in checks}) == len(checks)
    for check in checks:
        assert check.code in REFUSALS, check.name
        assert check.needle, check.name


def test_the_driver_reports_a_neutered_machine(monkeypatch) -> None:
    """Mutate the real state machine so it stops refusing, and require a red driver."""

    def lax_advance(document, target, *, actor, at, definitions, rail, note="", action=ACTIONS[0]):
        moved = document.with_state(target)
        extended = rail.append(
            at=at,
            actor=actor,
            action=action,
            kind=moved.kind,
            ref=moved.id,
            from_state=document.state,
            to_state=target,
            note=note,
        )
        return moved, extended

    monkeypatch.setattr(workflow, "advance", lax_advance)

    sink = io.StringIO()
    assert negative_control.run(sink) != 0
    assert "illegal-transition" in sink.getvalue()
    assert "NOT refused" in sink.getvalue()


def test_the_driver_reports_a_neutered_schema_freeze(monkeypatch) -> None:
    """Mutate the keyword freeze so an unimplemented keyword stops failing."""

    def lax_check_schema(document, where="schema"):
        return []

    monkeypatch.setattr(schema, "check_schema", lax_check_schema)

    sink = io.StringIO()
    assert negative_control.run(sink) != 0
    assert "unsupported-schema-keyword" in sink.getvalue()


def test_the_driver_reports_an_unexpected_exception_as_a_failed_control(monkeypatch) -> None:
    """A control that never learned to refuse has not failed — it has thrown."""
    import io as _io

    def exploding() -> None:
        raise RuntimeError("this validator is gone")

    base = negative_control.flows.golden_path("test")
    checks = negative_control.provocations(base)
    broken = checks[0].__class__(
        name="exploding", code="unknown-kind", needle="x", check=exploding
    )
    monkeypatch.setattr(negative_control, "provocations", lambda path: (broken,))
    sink = _io.StringIO()
    assert negative_control.run(sink) != 0
    assert "raised RuntimeError instead of a refusal" in sink.getvalue()


def test_the_driver_reports_a_base_it_cannot_build(monkeypatch) -> None:
    """The driver's own inability to start is NOT-OK, never a silent pass."""
    import io as _io

    from integrations.erp.crm import flows as flows_module

    def cannot_start(tenant="acme", definitions=None):
        raise RuntimeError("no declarations")

    monkeypatch.setattr(flows_module, "golden_path", cannot_start)
    sink = _io.StringIO()
    assert negative_control.run(sink) != 0
    assert "the golden path could not be built" in sink.getvalue()


def test_the_driver_fails_when_the_golden_path_itself_is_broken(monkeypatch) -> None:
    """A control run against an invalid base proves nothing about the base."""
    from integrations.erp.crm import flows as flows_module

    original = flows_module.golden_path

    def broken_path(tenant="acme", definitions=None):
        good = original(tenant, definitions)
        drifted = good.workspace.get("LEAD-0001").with_state("delivered")
        space = flows_module.Workspace(
            tenant=good.workspace.tenant,
            definitions=good.workspace.definitions,
            documents={**good.workspace.documents, "LEAD-0001": drifted},
            rail=good.workspace.rail,
        )
        return flows_module.GoldenPath(
            workspace=space,
            rollup=good.rollup,
            sla_states=good.sla_states,
            findings=tuple(space.findings()),
        )

    monkeypatch.setattr(flows_module, "golden_path", broken_path)
    sink = io.StringIO()
    assert negative_control.run(sink) != 0
    output = sink.getvalue()
    assert "does not satisfy its own invariants" in output
    assert "unknown-state" in output


def test_a_provocation_that_raises_nothing_is_reported_as_a_formality(monkeypatch) -> None:
    """The three ways a provocation can fail are all reported, not just the wrong code."""
    base = negative_control.flows.golden_path("test")
    checks = negative_control.provocations(base)
    quiet = checks[0].__class__(name="quiet", code="unknown-kind", needle="x", check=lambda: None)
    monkeypatch.setattr(negative_control, "provocations", lambda path: (quiet,))
    sink = io.StringIO()
    assert negative_control.run(sink) != 0
    output = sink.getvalue()
    assert "NOT refused" in output
    assert "no provocation covers" in output


def test_audit_and_model_are_reachable_from_the_driver() -> None:
    """The driver must provoke the real rail, not a stand-in."""
    assert negative_control.audit is audit
