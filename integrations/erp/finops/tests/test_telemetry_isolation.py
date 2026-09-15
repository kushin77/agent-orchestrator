"""Acceptance criterion 3: the telemetry pillar is consumed, never edited.

The criterion is not "we promise not to write there" — it is measurable, so it
is measured. :func:`~integrations.erp.finops.cli.telemetry_digest` takes the
content digest of every tracked file under ``telemetry/``, and the lane's check
runs it around a full metered pass.

Two tests here, and the second is the one that makes the first mean anything:
one proves a full run changes nothing, and one proves the digest *can* change —
a measurement that cannot fail is the formality the doctrine names.
"""

from __future__ import annotations

import subprocess

from integrations.erp.finops.cli import telemetry_digest
from integrations.erp.finops.harness import (
    DEFAULT_TENANT,
    build_workspace,
    golden_path,
    run_matrix,
)


def test_a_full_metered_run_edits_no_tracked_telemetry_file() -> None:
    before = telemetry_digest()
    assert before is not None, "the tracked telemetry digest could not be taken"

    matrix = build_workspace()
    run_matrix(matrix, DEFAULT_TENANT)
    golden = build_workspace()
    golden_path(golden, DEFAULT_TENANT)

    assert telemetry_digest() == before


def test_the_digest_actually_notices_an_edit(tmp_path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    telemetry = tmp_path / "telemetry"
    telemetry.mkdir()
    tracked = telemetry / "store.py"
    tracked.write_text("original\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)

    before = telemetry_digest(tmp_path)
    assert before is not None

    # An untracked neighbour is not an edit and must not raise a false alarm.
    (telemetry / "store.pyc").write_text("bytecode\n", encoding="utf-8")
    assert telemetry_digest(tmp_path) == before

    tracked.write_text("tampered\n", encoding="utf-8")
    assert telemetry_digest(tmp_path) != before
