"""The lane record's own suite (issue #1270).

These tests drive the same code path the gate drives -- ``lane_record``'s
functions and ``cli.main`` -- so a green suite is evidence about the rule and not
about a copy of it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
ROOT = PKG.parents[1]
for entry in (str(PKG), str(ROOT)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import cli  # noqa: E402
import controls  # noqa: E402
import lane_record  # noqa: E402

ISSUE = 4242
LANE = "governance"
WORKTREE = "/tmp/ao1270-suite/worktrees/governance"
OWNED = ["governance/lane-record/lane_record.py", "scripts/check-lane-record.sh"]
SCOPED = ["verify", "lane-record-suite"]
SHA = "0123456789abcdef0123456789abcdef01234567"


def brief(**overrides):
    document = {
        "schema": lane_record.SCHEMA_VERSION,
        "kind": "brief",
        "issue": ISSUE,
        "lane": LANE,
        # The contract's runtime ids (fleet/runtimes.yaml, #1412): the record names a
        # REGISTERED runtime, and `runtime_ids()` now answers with the contract's rows
        # rather than the identity names the notices rule used to derive for itself.
        "runtime": "claude-session",
        "ts": "2026-09-18T20:00:00Z",
        "owned_files": list(OWNED),
        "scoped_gates": list(SCOPED),
        "forbidden_verbs": ["gh pr merge"],
        "assigned_worktree": WORKTREE,
        "report_shape": {"fields": ["sha", "worktree"]},
    }
    document.update(overrides)
    return document


def result(**overrides):
    document = {
        "schema": lane_record.SCHEMA_VERSION,
        "kind": "result",
        "issue": ISSUE,
        "lane": LANE,
        "runtime": "claude-session",
        "ts": "2026-09-18T20:30:00Z",
        "sha": SHA,
        "files_touched": list(OWNED),
        "gate_tails": {"verify": "VERIFY-RC=0", "lane-record-suite": "1 passed"},
        "mergeable": True,
        "squash_rc": 0,
        "worktree": WORKTREE,
    }
    document.update(overrides)
    return document


def write(directory: Path, name: str, document) -> Path:
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=1), encoding="utf-8")
    return path


def tree(tmp_path: Path, *docs) -> Path:
    """A records tree holding the documents given as (name, document) pairs."""
    records = tmp_path / "lane-records"
    for name, document in docs:
        write(records, name, document)
    return records


def green(tmp_path: Path, *docs) -> lane_record.Report:
    records = tree(tmp_path, *docs)
    return lane_record.evaluate(
        records, lane_record.load_schema(), lane_record.runtime_ids(), ROOT
    )


def named(report: lane_record.Report) -> set:
    return {str(finding) for finding in report.findings}


def codes(report: lane_record.Report) -> set:
    return {finding.code for finding in report.findings}


# --------------------------------------------------------------------------- #
# the schema, and the mirror that keeps it honest
# --------------------------------------------------------------------------- #
def test_schema_loads_and_every_keyword_is_enforceable():
    schema = lane_record.load_schema()
    assert schema["$defs"]["record"]["properties"]["schema"]["const"] == "lane-record/v1"
    assert set(lane_record.KINDS) == {"brief", "result"}


def test_schema_mirror_is_clean():
    assert lane_record.mirror_problems(lane_record.load_schema()) == ()


def test_a_schema_whose_kinds_drift_is_refused_by_name(tmp_path):
    schema = json.loads(json.dumps(lane_record.load_schema()))
    schema["$defs"]["record"]["properties"]["kind"]["enum"] = ["brief", "result", "handback"]
    drift = lane_record.mirror_problems(schema)
    assert "lane-schema-mirror-drift:$defs.record.properties.kind.enum" in {
        str(finding) for finding in drift
    }


def test_the_schema_cannot_grow_a_keyword_nobody_measures():
    schema = {"$schema": "https://json-schema.org/draft/2020-12/schema", "oneOf": []}
    with pytest.raises(lane_record.CannotAssess):
        lane_record._subset.check_schema(schema)


def test_finding_shape_matches_the_sibling_notice_rule():
    from governance.notices.finding import Finding as Sibling

    mine = lane_record.Finding("code", "subject", "reason")
    theirs = Sibling("code", "subject", "reason")
    assert tuple(mine.__dataclass_fields__) == tuple(theirs.__dataclass_fields__)
    assert (str(mine), mine.line()) == (str(theirs), theirs.line())


# --------------------------------------------------------------------------- #
# the derived runtime set
# --------------------------------------------------------------------------- #
def test_the_runtime_set_is_derived_from_the_registry():
    """The vocabulary is the CONTRACT's (#1412): seven ids, in the order it declares.

    The set used to be the notices rule's own five-id derivation and this asserted
    it came back sorted. It is now `fleet/runtimes.yaml`'s rows in contract order --
    one list, read through one loader -- so the assertions are its size, its
    uniqueness and its members, never an order this test would have invented.
    """
    ids = lane_record.runtime_ids()
    assert len(ids) == 7, "the contract registers seven runtimes"
    assert len(set(ids)) == 7, "no runtime id is declared twice"
    for runtime in ("claude-session", "claude-subagent", "deepseek-executor", "hermes", "paperclip"):
        assert runtime in ids


def test_an_unreadable_registry_is_cannot_assess_not_an_empty_set(tmp_path):
    (tmp_path / "registry").mkdir()
    with pytest.raises(lane_record.CannotAssess):
        lane_record.runtime_ids(tmp_path)


# --------------------------------------------------------------------------- #
# the clean half: the record is satisfiable
# --------------------------------------------------------------------------- #
def test_a_clean_pair_evaluates_green(tmp_path):
    report = green(
        tmp_path,
        ("%d/%s.brief.json" % (ISSUE, LANE), brief()),
        ("%d/%s.result.json" % (ISSUE, LANE), result()),
    )
    assert report.findings == ()
    assert report.rc == 0
    assert report.counts["pairs"] == 1
    assert "records=2 briefs=1 results=1 pairs=1" in report.summary()


def test_the_same_shape_carries_every_runtime(tmp_path):
    """The record is ONE shape: the identical pair for another runtime is green."""
    for runtime in ("claude-session", "deepseek-executor", "paperclip"):
        report = green(
            tmp_path / runtime,
            ("%d/%s.brief.json" % (ISSUE, LANE), brief(runtime=runtime)),
            ("%d/%s.result.json" % (ISSUE, LANE), result(runtime=runtime)),
        )
        assert report.findings == (), runtime


def test_a_brief_with_no_result_is_a_lane_in_flight(tmp_path):
    report = green(tmp_path, ("%d/%s.brief.json" % (ISSUE, LANE), brief()))
    assert report.findings == ()
    assert report.counts["results"] == 0


# --------------------------------------------------------------------------- #
# the negative half: the halves are read apart, so the rule is not vacuous
# --------------------------------------------------------------------------- #
def test_a_result_carrying_the_briefs_field_is_refused(tmp_path):
    report = green(
        tmp_path,
        ("%d/%s.brief.json" % (ISSUE, LANE), brief()),
        ("%d/%s.result.json" % (ISSUE, LANE), result(owned_files=list(OWNED))),
    )
    assert "lane-record-malformed:/owned_files" in named(report)


def test_a_brief_is_not_refused_for_lacking_the_results_fields(tmp_path):
    report = green(tmp_path, ("%d/%s.brief.json" % (ISSUE, LANE), brief()))
    assert "lane-record-malformed:/files_touched" not in named(report)
    assert "lane-record-malformed:/squash_rc" not in named(report)


# --------------------------------------------------------------------------- #
# the shape refusals
# --------------------------------------------------------------------------- #
def test_a_runtime_writing_an_unallowed_shape_is_refused_by_name(tmp_path):
    report = green(
        tmp_path,
        ("%d/%s.brief.json" % (ISSUE, LANE), brief(kind="handback")),
    )
    assert "lane-record-kind-unknown:'handback'" in named(report)


def test_a_validating_keyword_at_the_top_level_of_the_schema_is_refused(tmp_path):
    schema = json.loads(json.dumps(lane_record.load_schema()))
    schema["required"] = ["schema"]
    assert "lane-schema-mirror-drift:(document).required" in {
        str(finding) for finding in lane_record.mirror_problems(schema)
    }


def test_a_missing_field_is_named_by_the_field_not_the_document(tmp_path):
    document = brief()
    del document["owned_files"]
    report = green(tmp_path, ("%d/%s.brief.json" % (ISSUE, LANE), document))
    assert "lane-record-malformed:/owned_files" in named(report)


def test_a_wrong_version_is_refused_by_name(tmp_path):
    report = green(
        tmp_path,
        ("%d/%s.brief.json" % (ISSUE, LANE), brief(schema="lane-record/v2")),
    )
    assert "lane-record-schema-version:'lane-record/v2'" in named(report)


# --------------------------------------------------------------------------- #
# the acceptance cases from the issue
# --------------------------------------------------------------------------- #
def test_a_file_outside_the_owned_set_is_refused(tmp_path):
    report = green(
        tmp_path,
        ("%d/%s.brief.json" % (ISSUE, LANE), brief()),
        (
            "%d/%s.result.json" % (ISSUE, LANE),
            result(files_touched=list(OWNED) + ["docs/not-ours.md"]),
        ),
    )
    assert "lane-file-outside-scope:docs/not-ours.md" in named(report)


def test_one_defect_names_one_refusal(tmp_path):
    """Precision: a lane that widened its scope is refused once, not in a cascade."""
    report = green(
        tmp_path,
        ("%d/%s.brief.json" % (ISSUE, LANE), brief()),
        (
            "%d/%s.result.json" % (ISSUE, LANE),
            result(files_touched=list(OWNED) + ["docs/not-ours.md"]),
        ),
    )
    assert codes(report) == {"lane-file-outside-scope"}


def test_a_result_from_another_worktree_is_refused(tmp_path):
    report = green(
        tmp_path,
        ("%d/%s.brief.json" % (ISSUE, LANE), brief()),
        ("%d/%s.result.json" % (ISSUE, LANE), result(worktree="/tmp/ao1270-other")),
    )
    assert "lane-worktree-mismatch:/tmp/ao1270-other" in named(report)


def test_a_lane_in_the_shared_checkout_is_refused(tmp_path):
    report = green(
        tmp_path,
        ("%d/%s.brief.json" % (ISSUE, LANE), brief()),
        ("%d/%s.result.json" % (ISSUE, LANE), result(worktree=str(ROOT))),
    )
    assert "lane-worktree-shared:%s" % ROOT in named(report)


def test_a_result_without_a_brief_is_refused(tmp_path):
    report = green(tmp_path, ("%d/%s.result.json" % (ISSUE, LANE), result()))
    assert "lane-result-no-brief:%d/%s" % (ISSUE, LANE) in named(report)


def test_an_unregistered_runtime_is_refused(tmp_path):
    report = green(tmp_path, ("%d/%s.brief.json" % (ISSUE, LANE), brief(runtime="gamma")))
    assert "lane-runtime-unregistered:gamma" in named(report)


def test_a_result_from_another_runtime_than_the_brief_is_refused(tmp_path):
    report = green(
        tmp_path,
        ("%d/%s.brief.json" % (ISSUE, LANE), brief(runtime="claude-session")),
        ("%d/%s.result.json" % (ISSUE, LANE), result(runtime="deepseek-executor")),
    )
    assert "lane-runtime-mismatch:%d/%s" % (ISSUE, LANE) in named(report)


# --------------------------------------------------------------------------- #
# the gates, the squash and the report shape
# --------------------------------------------------------------------------- #
def test_a_scoped_gate_with_no_tail_is_refused(tmp_path):
    report = green(
        tmp_path,
        ("%d/%s.brief.json" % (ISSUE, LANE), brief()),
        (
            "%d/%s.result.json" % (ISSUE, LANE),
            result(gate_tails={"verify": "VERIFY-RC=0"}),
        ),
    )
    assert "lane-gate-tail-missing:lane-record-suite" in named(report)


def test_a_tail_for_an_unscoped_gate_is_refused(tmp_path):
    tails = {"verify": "VERIFY-RC=0", "lane-record-suite": "1 passed", "invented": "ok"}
    report = green(
        tmp_path,
        ("%d/%s.brief.json" % (ISSUE, LANE), brief()),
        ("%d/%s.result.json" % (ISSUE, LANE), result(gate_tails=tails)),
    )
    assert "lane-gate-tail-unscoped:invented" in named(report)


def test_an_empty_tail_is_not_evidence(tmp_path):
    tails = {"verify": "VERIFY-RC=0", "lane-record-suite": "   "}
    report = green(
        tmp_path,
        ("%d/%s.brief.json" % (ISSUE, LANE), brief()),
        ("%d/%s.result.json" % (ISSUE, LANE), result(gate_tails=tails)),
    )
    assert "lane-gate-tail-empty:lane-record-suite" in named(report)


def test_mergeable_while_the_squash_was_not_green_is_refused(tmp_path):
    report = green(
        tmp_path,
        ("%d/%s.brief.json" % (ISSUE, LANE), brief()),
        ("%d/%s.result.json" % (ISSUE, LANE), result(squash_rc=1)),
    )
    assert "lane-squash-not-green:1" in named(report)


def test_a_brief_demanding_an_undeclared_result_field_is_refused(tmp_path):
    report = green(
        tmp_path,
        (
            "%d/%s.brief.json" % (ISSUE, LANE),
            brief(report_shape={"fields": ["sha", "stdout"]}),
        ),
    )
    assert "lane-report-shape-unallowed:stdout" in named(report)


def test_a_result_omitting_what_its_brief_demanded_is_refused(tmp_path):
    report = green(
        tmp_path,
        (
            "%d/%s.brief.json" % (ISSUE, LANE),
            brief(report_shape={"fields": ["sha", "refs"]}),
        ),
        ("%d/%s.result.json" % (ISSUE, LANE), result()),
    )
    assert "lane-report-field-missing:refs" in named(report)


def test_a_malformed_sha_and_a_zoneless_stamp_are_refused(tmp_path):
    report = green(
        tmp_path,
        ("%d/%s.brief.json" % (ISSUE, LANE), brief(ts="2026-09-18 20:00")),
        ("%d/%s.result.json" % (ISSUE, LANE), result(sha="HEAD")),
    )
    assert "lane-sha-malformed:HEAD" in named(report)
    assert "lane-timestamp-malformed:2026-09-18 20:00" in named(report)


# --------------------------------------------------------------------------- #
# the tree: the name, the read, and the inability to say anything
# --------------------------------------------------------------------------- #
def test_the_file_name_and_the_record_must_agree(tmp_path):
    report = green(
        tmp_path,
        ("%d/other-lane.brief.json" % ISSUE, brief()),
    )
    assert "lane-record-filename-mismatch:%d/other-lane.brief.json" % ISSUE in named(report)


def test_a_file_that_is_not_a_record_is_named(tmp_path):
    records = tmp_path / "lane-records"
    records.mkdir(parents=True)
    (records / "loose.json").write_text("{}", encoding="utf-8")
    report = lane_record.evaluate(
        records, lane_record.load_schema(), lane_record.runtime_ids(), ROOT
    )
    assert "lane-record-naming:loose.json" in named(report)


def test_a_file_that_is_not_json_is_named(tmp_path):
    records = tmp_path / "lane-records" / str(ISSUE)
    records.mkdir(parents=True)
    (records / ("%s.brief.json" % LANE)).write_text("{not json", encoding="utf-8")
    report = lane_record.evaluate(
        records.parent, lane_record.load_schema(), lane_record.runtime_ids(), ROOT
    )
    assert "lane-record-not-json:%d/%s.brief.json" % (ISSUE, LANE) in named(report)


def test_an_absent_records_tree_cannot_be_assessed_never_a_pass(tmp_path):
    with pytest.raises(lane_record.CannotAssess):
        lane_record.evaluate(
            tmp_path / "nothing-here",
            lane_record.load_schema(),
            lane_record.runtime_ids(),
            ROOT,
        )


# --------------------------------------------------------------------------- #
# the declaration
# --------------------------------------------------------------------------- #
def test_the_declaration_mirrors_the_code():
    declaration = controls.load()
    assert controls.problems(declaration) == ()


def test_a_declared_refusal_the_code_never_reports_is_refused():
    declaration = dict(controls.load())
    declaration["refusals"] = list(declaration["refusals"]) + [
        {
            "id": "no-such-refusal",
            "rule": "declared while the code never reports it",
            "names_in_the_finding": "nothing",
            "provoked_by": "scripts/check-lane-record.sh",
        }
    ]
    assert "refusal-unknown:no-such-refusal" in {
        str(finding) for finding in controls.problems(declaration)
    }


def test_a_refusal_the_code_reports_but_the_declaration_omits_is_refused():
    declaration = dict(controls.load())
    declaration["refusals"] = [
        entry
        for entry in declaration["refusals"]
        if entry["id"] != "lane-worktree-mismatch"
    ]
    assert "refusal-undeclared:lane-worktree-mismatch" in {
        str(finding) for finding in controls.problems(declaration)
    }


# --------------------------------------------------------------------------- #
# the verb surface
# --------------------------------------------------------------------------- #
def test_cli_exit_codes(tmp_path, capsys):
    records = tree(
        tmp_path,
        ("%d/%s.brief.json" % (ISSUE, LANE), brief()),
        ("%d/%s.result.json" % (ISSUE, LANE), result()),
    )
    assert cli.main(["--root", str(ROOT), "runtimes"]) == 0
    assert cli.main(["--root", str(ROOT), "controls"]) == 0
    assert cli.main(["--root", str(ROOT), "evaluate", "--records", str(records)]) == 0
    capsys.readouterr()

    write(records, "%d/%s.result.json" % (ISSUE, LANE), result(squash_rc=1))
    assert cli.main(["--root", str(ROOT), "evaluate", "--records", str(records)]) == 1
    assert "lane-squash-not-green:1" in capsys.readouterr().err

    gone = tmp_path / "gone"
    assert cli.main(["--root", str(ROOT), "evaluate", "--records", str(gone)]) == 2
    assert "CANNOT-ASSESS" in capsys.readouterr().err


def test_cli_reports_zero_lanes_in_flight_for_a_missing_default_tree(tmp_path, capsys):
    code = cli.main(["--root", str(ROOT), "--fleet", str(tmp_path / "fleet"), "evaluate"])
    assert code == 0
    assert "records=0" in capsys.readouterr().out
