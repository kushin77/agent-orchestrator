from __future__ import annotations

from integrations.erp.webhooks import negative_control
from integrations.erp.webhooks.model import REFUSALS


def test_negative_control_provokes_every_declared_refusal():
    provoked = {code: negative_control.provoke(code) for code in REFUSALS}
    for code, reason in provoked.items():
        assert reason.startswith(code), f"{code} refused with unexpected reason {reason!r}"


def test_negative_control_driver_exits_zero(capsys):
    exit_code = negative_control.run()
    out = capsys.readouterr().out
    assert exit_code == 0
    assert f"{len(REFUSALS)}/{len(REFUSALS)} refusals provoked" in out


def test_negative_control_driver_can_fail(monkeypatch, capsys):
    """The driver itself must be able to fail (crm's own doctrine): neuter one
    provocation and require ``run()`` to turn non-zero."""

    original_provoke = negative_control.provoke

    def _broken_provoke(code):
        if code == "auth-failed":
            return "not-the-right-code: oops"
        return original_provoke(code)

    monkeypatch.setattr(negative_control, "provoke", _broken_provoke)
    exit_code = negative_control.run()
    out = capsys.readouterr().out
    assert exit_code == 1
    assert "FAIL" in out
