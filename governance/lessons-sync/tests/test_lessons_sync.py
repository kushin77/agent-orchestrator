"""Behavioural proof for the cross-repo lessons-sync contract gate (#424).

One negative control per refusal the gate claims, so the gate cannot pass
vacuously. Deterministic and offline: no network, no `gh`, no wall clock.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_MODULE = os.path.join(os.path.dirname(_HERE), "lessons_sync.py")
_spec = importlib.util.spec_from_file_location("lessons_sync", _MODULE)
assert _spec and _spec.loader
lessons_sync = importlib.util.module_from_spec(_spec)
sys.modules["lessons_sync"] = lessons_sync
_spec.loader.exec_module(lessons_sync)

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))  # repository root


def _real_inputs():
    contract = json.load(open(os.path.join(ROOT, "governance/lessons-sync/contract.json")))
    peer = json.load(open(os.path.join(ROOT, "governance/lessons-sync/peer-issues.json")))
    hints = json.load(open(os.path.join(ROOT, "governance/lessons-sync/hints.json")))
    cmr = json.load(open(os.path.join(ROOT, "governance/lessons-sync/cmr-ledger.json")))
    ledger_ids = lessons_sync._load_ledger_ids(os.path.join(ROOT, "governance/lessons/ledger.jsonl"))
    return contract, peer, hints, cmr, ledger_ids


def _evaluate(contract, peer, hints, cmr, ledger_ids):
    return lessons_sync.evaluate(contract, peer, hints, ledger_ids, cmr,
                                 commit_exists=lambda _s: True)


def _codes(findings):
    return {f["code"] for f in findings}


def test_real_inputs_are_clean():
    contract, peer, hints, cmr, ledger_ids = _real_inputs()
    assert _evaluate(contract, peer, hints, cmr, ledger_ids) == []


def test_symmetric_stores_refused():
    contract, peer, hints, cmr, ledger_ids = _real_inputs()
    contract = copy.deepcopy(contract)
    contract["derived"]["role"] = "writer"
    findings = _evaluate(contract, peer, hints, cmr, ledger_ids)
    assert "symmetric-stores" in _codes(findings)


def test_ledger_reference_unresolved():
    contract, peer, hints, cmr, ledger_ids = _real_inputs()
    peer = copy.deepcopy(peer)
    peer["issues"] = []
    findings = _evaluate(contract, peer, hints, cmr, ledger_ids)
    assert "ledger-ref-unresolved" in _codes(findings)


def test_peer_counterpart_missing_reported_by_lesson_id():
    contract, peer, hints, cmr, ledger_ids = _real_inputs()
    hints = copy.deepcopy(hints)
    hints["hints"][1]["peer_ref"] = {"repo": "kushin77/deepseek", "issue": 999999}
    findings = _evaluate(contract, peer, hints, cmr, ledger_ids)
    named = [f for f in findings if f["code"] == "peer-counterpart-missing"]
    assert named and named[0]["id"] == hints["hints"][1]["lesson_id"]


def test_bare_string_hint_refused():
    contract, peer, _hints, cmr, ledger_ids = _real_inputs()
    findings = lessons_sync.evaluate(contract, peer, {"hints": ["feed this back"]},
                                     ledger_ids, cmr, commit_exists=lambda _s: True)
    assert "hint-without-provenance" in _codes(findings)


def test_hint_without_commit_refused():
    contract, peer, hints, cmr, ledger_ids = _real_inputs()
    hints = copy.deepcopy(hints)
    del hints["hints"][0]["commit"]
    findings = _evaluate(contract, peer, hints, cmr, ledger_ids)
    assert "hint-without-provenance" in _codes(findings)


def test_lesson_not_in_ledger_refused():
    contract, peer, hints, cmr, ledger_ids = _real_inputs()
    hints = copy.deepcopy(hints)
    hints["hints"][0]["lesson_id"] = "LESSON-9999"
    findings = _evaluate(contract, peer, hints, cmr, ledger_ids)
    assert "lesson-not-discoverable" in _codes(findings)


def test_peer_close_reference_refused():
    contract, peer, hints, cmr, ledger_ids = _real_inputs()
    hints = copy.deepcopy(hints)
    hints["hints"][0]["note"] = "Closes kushin77/deepseek#79"
    findings = _evaluate(contract, peer, hints, cmr, ledger_ids)
    assert "peer-close-refused" in _codes(findings)


def test_undeclared_cmr_ledger_refused():
    contract, peer, hints, cmr, ledger_ids = _real_inputs()
    contract = copy.deepcopy(contract)
    del contract["cmr_hub"]
    findings = _evaluate(contract, peer, hints, cmr, ledger_ids)
    assert "ledger-undeclared" in _codes(findings)


def test_cmr_ledger_reference_unresolved():
    contract, peer, hints, cmr, ledger_ids = _real_inputs()
    contract = copy.deepcopy(contract)
    contract["cmr_hub"]["ledger_ref"]["path"] = "docs/OTHER.md"
    findings = _evaluate(contract, peer, hints, cmr, ledger_ids)
    assert "ledger-ref-unresolved" in _codes(findings)


def test_cmr_record_without_disposition_reported_by_id():
    contract, peer, hints, cmr, ledger_ids = _real_inputs()
    cmr = copy.deepcopy(cmr)
    cmr["records"][0].pop("disposition")
    findings = _evaluate(contract, peer, hints, cmr, ledger_ids)
    named = [f for f in findings if f["code"] == "cmr-record-undisclosed"]
    assert named and named[0]["id"] == cmr["records"][0]["id"]


def test_cmr_mirror_without_local_counterpart_reported_by_id():
    contract, peer, hints, cmr, ledger_ids = _real_inputs()
    cmr = copy.deepcopy(cmr)
    cmr["records"][0]["disposition"] = "mirrors"
    cmr["records"][0]["mirrors"] = "LESSON-9999"
    findings = _evaluate(contract, peer, hints, cmr, ledger_ids)
    named = [f for f in findings if f["code"] == "local-counterpart-missing"]
    assert named and named[0]["id"] == cmr["records"][0]["id"]


def test_local_record_without_declaration_reported_by_id():
    contract, peer, hints, cmr, ledger_ids = _real_inputs()
    cmr = copy.deepcopy(cmr)
    cmr["local_only"] = []
    findings = _evaluate(contract, peer, hints, cmr, ledger_ids)
    assert "local-record-undisclosed" in _codes(findings)


def test_cmr_source_missing_is_cannot_assess(tmp_path):
    _c, _p, _h, cmr, _l = _real_inputs()
    try:
        lessons_sync.check_cmr_source(cmr, str(tmp_path / "absent" / "LESSONS.md"))
    except lessons_sync.InputError:
        return
    raise AssertionError("a missing live CMR source must be CANNOT-ASSESS, never a pass")


def test_cmr_source_stale_baseline_refused(tmp_path):
    _c, _p, _h, cmr, _l = _real_inputs()
    fake = tmp_path / "LESSONS.md"
    fake.write_text("| LESSON-001 | [#1](u) | lesson | closed | x | y |\n", encoding="utf-8")
    findings = lessons_sync.check_cmr_source(cmr, str(fake))
    assert "cmr-baseline-stale" in _codes(findings)


def test_report_is_deterministic():
    contract, peer, hints, cmr, ledger_ids = _real_inputs()
    first = _evaluate(contract, peer, hints, cmr, ledger_ids)
    second = _evaluate(copy.deepcopy(contract), copy.deepcopy(peer),
                       copy.deepcopy(hints), copy.deepcopy(cmr), set(ledger_ids))
    assert first == second


def test_cannot_assess_when_input_missing():
    import argparse
    args = argparse.Namespace(
        root=ROOT,
        contract="governance/lessons-sync/does-not-exist.json",
        peer="governance/lessons-sync/peer-issues.json",
        hints="governance/lessons-sync/hints.json",
        ledger="governance/lessons/ledger.jsonl",
        cmr="governance/lessons-sync/cmr-ledger.json",
        report=None, live=False, verify_cmr_source=False, cmr_source=None)
    assert lessons_sync.run(args) == lessons_sync.CANNOT_ASSESS


def test_cannot_assess_when_cmr_baseline_missing():
    import argparse
    args = argparse.Namespace(
        root=ROOT,
        contract="governance/lessons-sync/contract.json",
        peer="governance/lessons-sync/peer-issues.json",
        hints="governance/lessons-sync/hints.json",
        ledger="governance/lessons/ledger.jsonl",
        cmr="governance/lessons-sync/does-not-exist.json",
        report=None, live=False, verify_cmr_source=False, cmr_source=None)
    assert lessons_sync.run(args) == lessons_sync.CANNOT_ASSESS
