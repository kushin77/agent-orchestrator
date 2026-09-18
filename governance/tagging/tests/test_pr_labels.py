"""Suite for `governance/tagging/cli.py pr-labels` (issue #1254 step 5c / #1328).

The verb derives the class:/posture:/lifecycle:/pillar: labels a PR's own
`## Classification` block implies, and (with `--apply`) sets them via `gh`. The
vocabulary is never re-declared here: every value is judged against
`governance/tagging/taxonomy.yaml`, the same authority `check-tagging` already
proves matches `governance/conformance/policy.yaml`'s ladder.

`gh` is injected through the `AO_GH_BIN` environment variable (SP-4: nothing in
this repository defines a function named after a binary), so `--apply` is
tested against a fake `gh` script rather than the real network tool.

Run directly (the `check-tagging` gate runs it):

    python3 -m pytest governance/tagging/tests/test_pr_labels.py -q
"""

from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "governance" / "tagging"))

import cli as C  # noqa: E402
import model as M  # noqa: E402

TAXONOMY = ROOT / "governance" / "tagging" / "taxonomy.yaml"


@pytest.fixture(scope="module")
def taxonomy() -> M.Taxonomy:
    return M.load_taxonomy(TAXONOMY)


GOOD_BODY = """\
## Closes

Closes #1328

## Classification

class: pattern
posture: overall
lifecycle: build
pillar: governance
pattern: none
lane: issue-1328
"""


# -- parse_classification ------------------------------------------------------
def test_parse_classification_reads_the_block():
    fields = C.parse_classification(GOOD_BODY)
    assert fields == {
        "class": "pattern",
        "posture": "overall",
        "lifecycle": "build",
        "pillar": "governance",
        "pattern": "none",
        "lane": "issue-1328",
    }


def test_parse_classification_is_empty_when_the_block_is_absent():
    assert C.parse_classification("## Closes\n\nCloses #1\n") == {}


def test_parse_classification_strips_html_comments_and_inline_comments():
    body = """\
## Classification

<!-- a comment block that must not be parsed as a field -->
class: pattern  # inline note
"""
    assert C.parse_classification(body) == {"class": "pattern"}


# -- derive_pr_labels -----------------------------------------------------------
def test_derive_pr_labels_emits_one_label_per_valid_field(taxonomy):
    fields = C.parse_classification(GOOD_BODY)
    labels, findings = C.derive_pr_labels(fields, taxonomy)
    assert set(labels) == {
        "class:pattern",
        "posture:overall",
        "lifecycle:build",
        "pillar:governance",
    }
    assert findings == []


def test_derive_pr_labels_refuses_an_unknown_class_by_name(taxonomy):
    fields = dict(C.parse_classification(GOOD_BODY))
    fields["class"] = "not-a-rung"
    labels, findings = C.derive_pr_labels(fields, taxonomy)
    assert "class:not-a-rung" not in labels
    assert ("pr-class-unknown", "not-a-rung") in findings
    # the clean twin: every OTHER field still resolves.
    assert "posture:overall" in labels


def test_derive_pr_labels_refuses_an_unknown_posture_by_name(taxonomy):
    fields = dict(C.parse_classification(GOOD_BODY))
    fields["posture"] = "made-up"
    _, findings = C.derive_pr_labels(fields, taxonomy)
    assert findings[0][0] == "pr-posture-unknown"


def test_derive_pr_labels_refuses_an_unknown_lifecycle_by_name(taxonomy):
    fields = dict(C.parse_classification(GOOD_BODY))
    fields["lifecycle"] = "made-up"
    _, findings = C.derive_pr_labels(fields, taxonomy)
    assert ("pr-lifecycle-unknown", "made-up") in findings


def test_derive_pr_labels_refuses_an_unknown_pillar_by_name(taxonomy):
    fields = dict(C.parse_classification(GOOD_BODY))
    fields["pillar"] = "made-up"
    _, findings = C.derive_pr_labels(fields, taxonomy)
    assert ("pr-pillar-unknown", "made-up") in findings


def test_derive_pr_labels_accepts_a_comma_posture_list(taxonomy):
    fields = dict(C.parse_classification(GOOD_BODY))
    fields["posture"] = "overall, iac"
    labels, findings = C.derive_pr_labels(fields, taxonomy)
    assert "posture:overall" in labels and "posture:iac" in labels
    assert findings == []


# -- cmd_pr_labels via --body-file (no gh needed) -------------------------------
def test_cmd_pr_labels_body_file_ok(tmp_path, capsys):
    body_file = tmp_path / "body.md"
    body_file.write_text(GOOD_BODY, encoding="utf-8")
    args = _ns(pr=0, body_file=str(body_file), apply=False)
    rc = C.cmd_pr_labels(args)
    out = capsys.readouterr().out
    assert rc == C.OK
    assert "class:pattern" in out


def test_cmd_pr_labels_missing_block_is_cannot_assess(tmp_path, capsys):
    body_file = tmp_path / "body.md"
    body_file.write_text("## Closes\n\nCloses #1\n", encoding="utf-8")
    args = _ns(pr=0, body_file=str(body_file), apply=False)
    rc = C.cmd_pr_labels(args)
    err = capsys.readouterr().err
    assert rc == C.CANNOT_ASSESS
    assert "pr-classification-missing" in err


def test_cmd_pr_labels_no_pr_and_no_body_file_is_cannot_assess(capsys):
    args = _ns(pr=0, body_file="", apply=False)
    rc = C.cmd_pr_labels(args)
    err = capsys.readouterr().err
    assert rc == C.CANNOT_ASSESS
    assert "pr-context-missing" in err


# -- --apply, against a fake gh --------------------------------------------------
FAKE_GH = """\
#!/usr/bin/env bash
set -u
log="$FAKE_GH_LOG"
printf '%s\\n' "$*" >>"$log"
case "$*" in
  "pr view "*"--json body"*)
    cat "$FAKE_GH_BODY_FILE"
    ;;
  "pr view "*"--json labels"*)
    printf '%s' "$FAKE_GH_EXISTING_LABELS"
    ;;
  "pr edit "*)
    exit 0
    ;;
  *)
    echo "fake-gh: unhandled invocation: $*" >&2
    exit 1
    ;;
esac
"""


def _install_fake_gh(tmp_path: Path) -> Path:
    script = tmp_path / "gh"
    script.write_text(FAKE_GH, encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def _ns(**kwargs):
    class NS:
        pass

    ns = NS()
    for key, value in kwargs.items():
        setattr(ns, key, value)
    return ns


def test_cmd_pr_labels_apply_adds_the_derived_labels(tmp_path, monkeypatch, capsys):
    body_file = tmp_path / "body.md"
    body_file.write_text(GOOD_BODY, encoding="utf-8")
    log = tmp_path / "gh.log"
    fake = _install_fake_gh(tmp_path)
    monkeypatch.setenv("AO_GH_BIN", str(fake))
    monkeypatch.setenv("FAKE_GH_LOG", str(log))
    monkeypatch.setenv("FAKE_GH_BODY_FILE", str(body_file))
    monkeypatch.setenv("FAKE_GH_EXISTING_LABELS", "")

    args = _ns(pr=1328, body_file="", apply=True)
    rc = C.cmd_pr_labels(args)
    out = capsys.readouterr().out
    assert rc == C.OK
    assert "applied 4 label(s), removed 0" in out
    invocations = log.read_text(encoding="utf-8")
    assert "--add-label class:pattern" in invocations


def test_cmd_pr_labels_apply_reports_hand_label_drift(tmp_path, monkeypatch, capsys):
    body_file = tmp_path / "body.md"
    body_file.write_text(GOOD_BODY, encoding="utf-8")
    log = tmp_path / "gh.log"
    fake = _install_fake_gh(tmp_path)
    monkeypatch.setenv("AO_GH_BIN", str(fake))
    monkeypatch.setenv("FAKE_GH_LOG", str(log))
    monkeypatch.setenv("FAKE_GH_BODY_FILE", str(body_file))
    # a hand-applied label that disagrees with the body's declared class.
    monkeypatch.setenv("FAKE_GH_EXISTING_LABELS", "class:elite")

    args = _ns(pr=1328, body_file="", apply=True)
    rc = C.cmd_pr_labels(args)
    err = capsys.readouterr().err
    assert rc == C.NOT_OK
    assert "pr-label-drift: class:elite" in err
    invocations = log.read_text(encoding="utf-8")
    assert "--remove-label class:elite" in invocations


def test_cmd_pr_labels_apply_is_clean_when_labels_already_match(tmp_path, monkeypatch, capsys):
    body_file = tmp_path / "body.md"
    body_file.write_text(GOOD_BODY, encoding="utf-8")
    log = tmp_path / "gh.log"
    fake = _install_fake_gh(tmp_path)
    monkeypatch.setenv("AO_GH_BIN", str(fake))
    monkeypatch.setenv("FAKE_GH_LOG", str(log))
    monkeypatch.setenv("FAKE_GH_BODY_FILE", str(body_file))
    monkeypatch.setenv(
        "FAKE_GH_EXISTING_LABELS", "class:pattern,posture:overall,lifecycle:build,pillar:governance"
    )

    args = _ns(pr=1328, body_file="", apply=True)
    rc = C.cmd_pr_labels(args)
    out = capsys.readouterr().out
    assert rc == C.OK
    assert "applied 0 label(s), removed 0" in out
    invocations = log.read_text(encoding="utf-8")
    assert "--add-label" not in invocations
    assert "--remove-label" not in invocations
