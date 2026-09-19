"""Negative controls for the approval record (issue #1272): each refusal fires by name."""

from __future__ import annotations

import json

import pytest

from integrations.paperclip.adapters.approvals import record as record_mod

KEY = b"test-only-key-not-a-secret"


def test_valid_grant_then_check_once(tmp_path):
    store = tmp_path / "approvals"
    rec = record_mod.grant("operator", "merge:pr#1", 300, store=store, key=KEY)
    assert rec.verify_signature(KEY)
    checked = record_mod.check("merge:pr#1", store=store, key=KEY)
    assert checked.actor == "operator"


def test_merge_scope_is_single_use(tmp_path):
    store = tmp_path / "approvals"
    record_mod.grant("operator", "merge:pr#2", 300, store=store, key=KEY)
    record_mod.check("merge:pr#2", store=store, key=KEY)  # consumes it
    with pytest.raises(record_mod.ApprovalRefused) as exc:
        record_mod.check("merge:pr#2", store=store, key=KEY)
    assert exc.value.code == "approval-used"


def test_non_merge_scope_is_reusable(tmp_path):
    store = tmp_path / "approvals"
    record_mod.grant("operator", "pause:scheduler", 300, store=store, key=KEY)
    record_mod.check("pause:scheduler", store=store, key=KEY)
    record_mod.check("pause:scheduler", store=store, key=KEY)  # reusable — no refusal


def test_missing_record_refused_by_name(tmp_path):
    store = tmp_path / "approvals"
    with pytest.raises(record_mod.ApprovalRefused) as exc:
        record_mod.check("merge:pr#999", store=store, key=KEY)
    assert exc.value.code == "approval-missing"


def test_expired_record_refused_by_name(tmp_path):
    store = tmp_path / "approvals"
    record_mod.grant("operator", "delete:branch-foo", 10, store=store, key=KEY, now=1000.0)
    with pytest.raises(record_mod.ApprovalRefused) as exc:
        record_mod.check("delete:branch-foo", store=store, key=KEY, now=1011.0)
    assert exc.value.code == "approval-expired"


def test_tampered_signature_refused_by_name(tmp_path):
    store = tmp_path / "approvals"
    store.mkdir(parents=True)
    rec = record_mod.grant("operator", "flip:AO_FLAG", 300, store=store, key=KEY)
    path = store / "flip__AO_FLAG.json"
    data = json.loads(path.read_text())
    data["actor"] = "attacker"  # tamper a signed field without re-signing
    path.write_text(json.dumps(data))
    with pytest.raises(record_mod.ApprovalRefused) as exc:
        record_mod.check("flip:AO_FLAG", store=store, key=KEY)
    assert exc.value.code == "approval-tampered"
    assert rec.actor == "operator"


def test_wrong_key_is_a_tamper_too(tmp_path):
    store = tmp_path / "approvals"
    record_mod.grant("operator", "merge:pr#3", 300, store=store, key=KEY)
    with pytest.raises(record_mod.ApprovalRefused) as exc:
        record_mod.check("merge:pr#3", store=store, key=b"a-different-key")
    assert exc.value.code == "approval-tampered"


def test_malformed_scope_refused_by_name():
    with pytest.raises(record_mod.ApprovalRefused) as exc:
        record_mod.parse_scope("no-colon-here")
    assert exc.value.code == "scope-malformed"
    with pytest.raises(record_mod.ApprovalRefused) as exc2:
        record_mod.parse_scope("unknownkind:target")
    assert exc2.value.code == "scope-malformed"


def test_no_key_fails_closed(tmp_path, monkeypatch):
    monkeypatch.delenv(record_mod._ENV_KEY, raising=False)
    with pytest.raises(record_mod.ApprovalKeyError):
        record_mod.grant("operator", "merge:pr#4", 300, store=tmp_path / "approvals")


def test_list_records(tmp_path):
    store = tmp_path / "approvals"
    record_mod.grant("operator", "merge:pr#5", 300, store=store, key=KEY)
    record_mod.grant("operator", "pause:scheduler", 300, store=store, key=KEY)
    records = record_mod.list_records(store=store)
    assert len(records) == 2
