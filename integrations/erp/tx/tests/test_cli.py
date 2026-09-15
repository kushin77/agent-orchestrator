"""Controls for the tri-state CLI: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS."""

from __future__ import annotations

import io
import json

from integrations.erp.tx import cli


def test_check_reports_ok_and_names_its_measurements() -> None:
    sink = io.StringIO()
    assert cli.main(["check"], sink=sink) == 0
    text = sink.getvalue()
    assert "erp-tx check: OK" in text
    assert "derived cycle: quotation -> sales-order -> delivery-note -> sales-invoice" in text
    assert "golden path:" in text
    assert "cancellation path:" in text
    assert "negative-control: OK" in text
    assert "NOTE  declared but not driven" in text


def test_no_command_is_cannot_assess() -> None:
    sink = io.StringIO()
    assert cli.main([], sink=sink) == cli.CANNOT_ASSESS
    assert "usage" in sink.getvalue()


def test_a_definition_path_cannot_be_substituted() -> None:
    sink = io.StringIO()
    rc = cli.main(["--definitions", "somewhere.json", "check"], sink=sink)
    assert rc == cli.CANNOT_ASSESS
    assert "CANNOT-ASSESS" in sink.getvalue()


def test_definitions_prints_the_resolved_set() -> None:
    sink = io.StringIO()
    assert cli.main(["definitions"], sink=sink) == 0
    payload = json.loads(sink.getvalue())
    assert payload["chain"] == ["quotation", "sales-order", "delivery-note", "sales-invoice"]
    assert payload["accountingKind"] == "gl-posting"
    assert payload["undriven"]


def test_demo_prints_both_paths() -> None:
    sink = io.StringIO()
    assert cli.main(["demo"], sink=sink) == 0
    payload = json.loads(sink.getvalue())
    assert payload["goldenPath"]["ok"] is True
    assert payload["cancellationPath"]["ok"] is True
    assert payload["goldenPath"]["steps"]
    assert payload["goldenPath"]["summary"]["documents"]
