"""The offline CLI and its tri-state exit contract (issue #650)."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from integrations.erp.crm import cli, flows
from integrations.erp.crm.definitions import LOCAL_DECLARATION
from integrations.erp.crm.model import KINDS


def run(argv):
    out = io.StringIO()
    code = cli.main(argv, sink=out)
    return code, out.getvalue()


def test_check_is_ok_and_reports_what_it_measured() -> None:
    code, output = run(["check"])
    assert code == cli.OK
    assert "declaration coverage: 8 kind(s)" in output
    assert "inspection outcomes failed, passed" in output
    assert "golden path: 11 document(s)" in output
    assert "rollup 210 min / 31500 EUR minor" in output
    assert "negative-control: OK" in output
    assert output.strip().endswith("erp-crm check: OK")


def test_check_reports_cannot_assess_for_an_unreadable_declaration(tmp_path: Path) -> None:
    path = tmp_path / "definitions.json"
    path.write_text("{not json", encoding="utf-8")
    code, output = run(["--definitions", str(path), "check"])
    assert code == cli.CANNOT_ASSESS
    assert "CANNOT-ASSESS" in output


def test_check_reports_cannot_assess_for_a_declaration_that_fails_its_own_schema(
    tmp_path: Path,
) -> None:
    path = tmp_path / "definitions.json"
    payload = json.loads(LOCAL_DECLARATION.read_text(encoding="utf-8"))
    del payload["vocabularies"]
    path.write_text(json.dumps(payload), encoding="utf-8")
    code, output = run(["--definitions", str(path), "check"])
    assert code == cli.CANNOT_ASSESS
    assert "vocabularies" in output


def test_check_reports_cannot_assess_for_an_unreadable_harvest_record(tmp_path: Path) -> None:
    path = tmp_path / "provenance.json"
    path.write_text("{}", encoding="utf-8")
    code, output = run(["check", "--provenance", str(path)])
    assert code == cli.CANNOT_ASSESS
    assert "harvest record" in output


def test_demo_prints_the_scenario_as_json() -> None:
    code, output = run(["demo"])
    assert code == cli.OK
    payload = json.loads(output)
    assert payload == flows.golden_path("demo").to_dict()
    assert payload["auditEntries"] == 33
    assert payload["rollup"]["minutes"] == 210
    assert set(payload["sla"]) == {
        "ISS-0001/at-open",
        "ISS-0001/after-close",
        "ISS-0002/running",
        "ISS-0002/due",
        "ISS-0002/breached",
    }


def test_definitions_prints_the_validated_declaration_set() -> None:
    code, output = run(["definitions"])
    assert code == cli.OK
    payload = json.loads(output)
    assert payload["source"] == "local-lane-declaration"
    assert sorted(payload["kinds"]) == sorted(KINDS)


def test_no_verb_is_cannot_assess_not_a_silent_success() -> None:
    code, output = run([])
    assert code == cli.CANNOT_ASSESS
    assert "usage" in output.lower()


def test_an_unknown_verb_is_refused_by_argparse() -> None:
    with pytest.raises(SystemExit):
        run(["teleport"])
