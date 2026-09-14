"""verify-chain tests: every tamper class is detected, never silently skipped.

Each tamper is provoked on a *temp copy* of a chain file; the committed ledger
is never mutated.
"""

from __future__ import annotations

import os
import shutil

import pytest

import read_model
from conftest import (
    chain_path,
    corrupt_line,
    remove_record,
    reorder_first_two,
    tamper_text,
    truncate_last,
)


def _copy(ledger_dir: str, tmp_path, name: str) -> str:
    target = str(tmp_path / name)
    shutil.copytree(ledger_dir, target)
    return target


def test_intact_chain_verifies_ok(model):
    verdict = model.verify_chain()
    assert verdict.status == read_model.ChainVerdict.OK
    assert verdict.is_pass is True
    assert verdict.exit_code == 0
    assert verdict.findings() == []
    assert sorted(verdict.tenants) == ["acme", "beta"]


def test_modified_record_is_detected_and_named(model, ledger_dir, tmp_path):
    work = _copy(ledger_dir, tmp_path, "modified")
    tamper_text(chain_path(work, "acme"), '"model.call"', '"model.read"')
    verdict = read_model.open_read_model(work).verify_chain()
    assert verdict.status == read_model.ChainVerdict.NOT_OK
    assert verdict.is_pass is False
    assert verdict.exit_code == 1
    finding = verdict.tenants["acme"]
    assert finding["brokenAt"] == 1
    assert "hash mismatch" in finding["detail"]


def test_reordered_records_are_detected(model, ledger_dir, tmp_path):
    work = _copy(ledger_dir, tmp_path, "reordered")
    reorder_first_two(chain_path(work, "acme"))
    verdict = read_model.open_read_model(work).verify_chain()
    assert verdict.status == read_model.ChainVerdict.NOT_OK
    assert verdict.tenants["acme"]["brokenAt"] == 1
    assert "record 1" in verdict.tenants["acme"]["detail"]
    # The untouched tenant is still reported OK, and the verdict stays NOT-OK.
    assert verdict.tenants["beta"]["status"] == read_model.ChainVerdict.OK


def test_removed_middle_record_is_detected(model, ledger_dir, tmp_path):
    work = _copy(ledger_dir, tmp_path, "removed")
    remove_record(chain_path(work, "acme"), 1)
    verdict = read_model.open_read_model(work).verify_chain()
    assert verdict.status == read_model.ChainVerdict.NOT_OK
    assert verdict.tenants["acme"]["brokenAt"] == 2
    assert "seq" in verdict.tenants["acme"]["detail"]


def test_truncated_tail_needs_the_trusted_anchor(model, ledger_dir, tmp_path):
    anchor = model.trusted_tail()
    work = _copy(ledger_dir, tmp_path, "truncated")
    truncate_last(chain_path(work, "acme"))
    truncated = read_model.open_read_model(work)
    # Silently dropping the last records leaves an internally consistent file,
    # so the chain alone cannot see it - and we say so rather than pretend.
    assert truncated.verify_chain().status == read_model.ChainVerdict.OK
    verdict = truncated.verify_chain(expected=anchor)
    assert verdict.status == read_model.ChainVerdict.NOT_OK
    assert "does not match expected" in verdict.tenants["acme"]["detail"]


def test_unparseable_chain_is_cannot_assess_never_a_pass(model, ledger_dir, tmp_path):
    work = _copy(ledger_dir, tmp_path, "corrupt")
    corrupt_line(chain_path(work, "acme"))
    verdict = read_model.open_read_model(work).verify_chain()
    assert verdict.status == read_model.ChainVerdict.CANNOT_ASSESS
    assert verdict.is_pass is False
    assert verdict.exit_code == 2
    assert verdict.tenants["acme"]["status"] == read_model.ChainVerdict.CANNOT_ASSESS
    assert verdict.findings()


def test_empty_scope_is_cannot_assess_not_a_vacuous_pass(tmp_path):
    empty = read_model.open_read_model(str(tmp_path / "nothing"))
    verdict = empty.verify_chain()
    assert verdict.status == read_model.ChainVerdict.CANNOT_ASSESS
    assert verdict.is_pass is False
    assert verdict.tenants == {}


def test_filter_fails_closed_on_a_tampered_chain(model, ledger_dir, tmp_path):
    work = _copy(ledger_dir, tmp_path, "filter-tamper")
    tamper_text(chain_path(work, "acme"), '"model.call"', '"model.read"')
    tampered = read_model.open_read_model(work)
    with pytest.raises(Exception) as excinfo:
        tampered.filter(action="model.call")
    # A partially-verified trail is never returned: the read fails closed.
    assert "acme" in str(excinfo.value) or "hash" in str(excinfo.value).lower()


def test_expected_anchor_shape_is_validated(model):
    with pytest.raises(read_model.ReadModelError):
        model.verify_chain(expected={"acme": ("not-a-seq", "hash")})
    with pytest.raises(read_model.ReadModelError):
        model.verify_chain(expected={"acme": "not-an-anchor"})
