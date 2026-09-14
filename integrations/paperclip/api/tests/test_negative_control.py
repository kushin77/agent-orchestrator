"""Every provoked failure is refused by name (issue #413, the negative control)."""

from __future__ import annotations

from pathlib import Path

import pytest

from integrations.paperclip.api import cli


@pytest.mark.parametrize(
    "name,needle",
    (
        ("contract-drift", "drifts from the frozen contract"),
        ("taxonomy-422", "'422' is missing"),
        ("company-mapping", "company mapping is undeclared"),
        ("health-lying-ok", "ok while"),
    ),
)
def test_each_provoked_failure_is_refused_by_name(root: Path, name: str, needle: str) -> None:
    results = {entry[0]: entry for entry in cli._controls(root)}
    assert name in results, f"the control {name!r} was not provoked at all"
    refused, findings = results[name][1], results[name][2]
    assert refused, f"{name!r} was not refused (findings: {findings!r})"
    assert any(needle in finding for finding in findings)


def test_all_controls_are_provoked_and_refused(root: Path) -> None:
    results = cli._controls(root)
    assert len(results) == 4
    assert all(refused for _name, refused, _findings in results)
