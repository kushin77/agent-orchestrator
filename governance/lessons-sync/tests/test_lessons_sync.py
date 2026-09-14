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
    ledger_ids = lessons_sync._load_ledger_ids(os.path.join(ROOT, "governance/lessons/ledger.jsonl"))
    return contract, peer, hints, ledger_ids


def _codes(findings):
    return {f["code"] for f in findings}


def test_real_inputs_are_clean():
    contract, peer, hints, ledger_ids = _real_inputs()
    findings = lessons_sync.evaluate(contract, peer, hints, ledger_ids,
                                     commit_exists=lambda _s: True)
    assert findings == []


def test_symmetric_stores_refused():
    contract, peer, hints, ledger_ids = _real_inputs()
    contract = copy.deepcopy(contract)
    contract["derived"]["role"] = "writer"
    findings = lessons_sync.evaluate(contract, peer, hints, ledger_ids,
                                     commit_exists=lambda _s: True)
    assert "symmetric-stores" in _codes(findings)


def test_ledger_reference_unresolved():
    contract, peer, hints, ledger_ids = _real_inputs()
    peer = copy.deepcopy(peer)
    peer["issues"] = []
    findings = lessons_sync.evaluate(contract, peer, hints, ledger_ids,
                                     commit_exists=lambda _s: True)
    assert "ledger-ref-unresolved" in _codes(findings)


def test_peer_counterpart_missing_reported_by_lesson_id():
    contract, peer, hints, ledger_ids = _real_inputs()
    hints = copy.deepcopy(hints)
    hints["hints"][1]["peer_ref"] = {"repo": "kushin77/deepseek", "issue": 999999}
    findings = lessons_sync.evaluate(contract, peer, hints, ledger_ids,
                                     commit_exists=lambda _s: True)
    named = [f for f in findings if f["code"] == "peer-counterpart-missing"]
    assert named and named[0]["id"] == hints["hints"][1]["lesson_id"]


def test_bare_string_hint_refused():
    contract, peer, _hints, ledger_ids = _real_inputs()
    findings = lessons_sync.evaluate(contract, peer, {"hints": ["feed this back"]},
                                     ledger_ids, commit_exists=lambda _s: True)
    assert "hint-without-provenance" in _codes(findings)


def test_hint_without_commit_refused():
    contract, peer, hints, ledger_ids = _real_inputs()
    hints = copy.deepcopy(hints)
    del hints["hints"][0]["commit"]
    findings = lessons_sync.evaluate(contract, peer, hints, ledger_ids,
                                     commit_exists=lambda _s: True)
    assert "hint-without-provenance" in _codes(findings)


def test_lesson_not_in_ledger_refused():
    contract, peer, hints, ledger_ids = _real_inputs()
    hints = copy.deepcopy(hints)
    hints["hints"][0]["lesson_id"] = "LESSON-9999"
    findings = lessons_sync.evaluate(contract, peer, hints, ledger_ids,
                                     commit_exists=lambda _s: True)
    assert "lesson-not-discoverable" in _codes(findings)


def test_peer_close_reference_refused():
    contract, peer, hints, ledger_ids = _real_inputs()
    hints = copy.deepcopy(hints)
    hints["hints"][0]["note"] = "Closes kushin77/deepseek#79"
    findings = lessons_sync.evaluate(contract, peer, hints, ledger_ids,
                                     commit_exists=lambda _s: True)
    assert "peer-close-refused" in _codes(findings)


def test_cannot_assess_when_input_missing():
    import argparse
    args = argparse.Namespace(
        root=ROOT,
        contract="governance/lessons-sync/does-not-exist.json",
        peer="governance/lessons-sync/peer-issues.json",
        hints="governance/lessons-sync/hints.json",
        ledger="governance/lessons/ledger.jsonl",
        report=None, live=False)
    assert lessons_sync.run(args) == lessons_sync.CANNOT_ASSESS
