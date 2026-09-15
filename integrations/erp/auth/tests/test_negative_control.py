"""The control that keeps the control set honest.

``negative_control`` provokes every refusal, and this file fails the suite the
moment the provoked set and the declared vocabulary diverge — in *either*
direction. A declared refusal that nothing provokes is a rule nobody can
demonstrate, and a provocation for a code that is not declared is a control
exercising something the module does not actually refuse for.
"""

from __future__ import annotations

from integrations.erp.auth import negative_control as nc
from integrations.erp.auth.model import REFUSALS


def test_every_declared_refusal_is_provoked_by_name():
    provoked, failures = nc.provoke()
    assert failures == (), "\n".join(failures)
    assert set(provoked) == set(REFUSALS)


def test_no_declared_refusal_is_unreachable():
    assert nc.uncovered() == ()


def test_there_is_exactly_one_provocation_per_declared_code():
    codes = [row[0] for row in nc.PROVOCATIONS]
    assert len(codes) == len(set(codes)), "a code is provoked more than once"
    assert set(codes) == set(REFUSALS)


def test_every_provocation_carries_a_description_and_a_callable():
    for code, description, provoker in nc.PROVOCATIONS:
        assert isinstance(description, str) and description.strip(), code
        assert callable(provoker), code


def test_the_provocation_set_is_not_vacuous():
    # If provoke() reported success without running anything, the coverage claim
    # above would be empty. This is the floor under it.
    provoked, _ = nc.provoke()
    assert len(provoked) >= 10
