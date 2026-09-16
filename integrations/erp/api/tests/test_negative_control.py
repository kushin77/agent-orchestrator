"""The negative controls can fail — which is what makes them controls (#651).

A driver that reports "every refusal was refused" is evidence only if it *could*
have reported otherwise. So each test here neuters one real mechanism and requires
the driver to turn red: the model's validator (nothing validates any more) and the
authorizer (nothing is denied any more). The second is the strongest statement this
lane makes about "no back door": if the surface stopped delegating, the driver
would notice rather than staying green.
"""

from __future__ import annotations

import io

import pytest

from integrations.erp.api import fixtures, negative_control, surface as surface_module
from integrations.erp.api.negative_control import UNREACHABLE_AUTH, UNREACHABLE_MODEL
from integrations.erp.api import errors as err
from integrations.erp.auth import model as auth_model
from integrations.erp.auth.model import Decision
from integrations.erp.core import validators as core_validators


def test_the_driver_is_clean():
    sink = io.StringIO()
    assert negative_control.run(sink) == 0
    report = sink.getvalue()
    assert "negative-control: OK" in report
    assert report.count("refused ->") == 21, "21 provoked refusals, no more and no fewer"


def test_the_driver_provokes_every_boundary_code():
    sink = io.StringIO()
    negative_control.run(sink)
    report = sink.getvalue()
    for code in err.BOUNDARY_CODES:
        assert f" {code}):" in report, code


def test_the_auth_coverage_accounts_for_the_whole_vocabulary():
    declared = {code for codes in UNREACHABLE_AUTH.values() for code in codes}
    provoked = 5  # permission-denied, scope-denied, unknown-role, field-write-denied, contract-unavailable
    assert len(declared) + provoked == len(auth_model.REFUSALS)
    assert declared <= set(auth_model.REFUSALS)


def test_the_model_coverage_accounts_for_the_whole_vocabulary():
    from integrations.erp.core import errors as model_errors

    assert set(UNREACHABLE_MODEL) <= set(model_errors.CODES)
    assert len(UNREACHABLE_MODEL) == 5
    for code, reason in UNREACHABLE_MODEL.items():
        assert len(reason) > 40, f"{code}: an unreachability claim must name its mechanism"


def test_a_neutered_validator_turns_the_driver_red(monkeypatch, capsys):
    """With nothing validating, `schema_violation` cannot be provoked — and the driver says so."""
    monkeypatch.setattr(
        core_validators.DocumentModel,
        "validate_document",
        lambda self, kind, document: dict(document),
    )
    sink = io.StringIO()
    assert negative_control.run(sink) == 1
    # The per-refusal verdicts go to the sink; the FAIL lines go to stderr.
    failures = capsys.readouterr().err
    assert "model-schema_violation" in failures
    assert "was NOT refused" in failures


def test_a_neutered_authorizer_turns_the_driver_red(monkeypatch, capsys):
    """The back-door mutant: if the surface stopped delegating, this would catch it."""
    monkeypatch.setattr(
        surface_module.auth_scope, "authorize", lambda *args, **kwargs: Decision(allowed=True)
    )
    sink = io.StringIO()
    assert negative_control.run(sink) == 1
    failures = capsys.readouterr().err
    assert "authorization-denied" in failures
    assert "was NOT refused" in failures


def test_a_wrongly_coded_refusal_turns_the_driver_red(monkeypatch, capsys):
    """'Something was refused' is not evidence that the rule under test refused."""
    original = err.from_decision

    def mislabel(decision, **kwargs):
        raised = original(decision, **kwargs)
        return err.SurfaceError(raised.status, "not_found", raised.message, raised.details)

    # `surface.py` reaches the error model through the same module object, so one
    # patch covers the surface's call site as well as any direct caller.
    monkeypatch.setattr(err, "from_decision", mislabel)
    sink = io.StringIO()
    assert negative_control.run(sink) == 1
    assert "was refused as 'not_found'" in capsys.readouterr().err


def test_every_provocation_claims_a_reason_or_a_code():
    """A provocation is either a wire code, an auth reason, or a named honesty control."""
    world = negative_control.World()
    for provocation in negative_control.provocations(world):
        assert provocation.name
        assert provocation.expected.code
        assert provocation.expected.needle
        if provocation.origin == "auth":
            assert provocation.expected.reason in auth_model.REFUSALS
        elif provocation.origin == "model":
            assert provocation.expected.code in err.MODEL_STATUS
        elif provocation.origin == "surface":
            assert provocation.expected.code in err.BOUNDARY_CODES


def test_the_world_is_rebuilt_per_provocation():
    """A provocation cannot depend on what ran before it — a control must be order-free."""
    first = negative_control.World()
    second = negative_control.World()
    assert first.documents.ids(fixtures.DEFAULT_TENANT, "party") == second.documents.ids(
        fixtures.DEFAULT_TENANT, "party"
    )
    assert len(first.documents) == len(second.documents)


@pytest.mark.parametrize("why", ["the same request twice"])
def test_the_driver_is_deterministic(why):
    """Two runs of the driver produce the same verdict and the same refusals."""
    first, second = io.StringIO(), io.StringIO()
    assert negative_control.run(first) == 0
    assert negative_control.run(second) == 0
    assert first.getvalue() == second.getvalue()
