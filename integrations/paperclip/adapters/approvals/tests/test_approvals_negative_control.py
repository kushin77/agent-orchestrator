"""The gate's own provocations are provoked (issue #416, GR-12)."""

from __future__ import annotations

from integrations.paperclip.adapters.approvals import negative_control


def test_the_negative_controls_all_provoke() -> None:
    assert negative_control.run() == 0


def test_the_clean_tree_projects_five_approvals() -> None:
    # The driver's positive control is the shape the gate depends on: a grant, a
    # deny and a pending across the three kinds.
    assert negative_control.run() == 0
