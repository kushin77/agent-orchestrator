"""Projection, refusal and read-guard tests for the secret vault (issue #417)."""

from __future__ import annotations

import copy
import json

import pytest

from integrations.paperclip.adapters.secrets import vault
from integrations.paperclip.adapters.secrets.model import (
    GSM_STORE,
    Caller,
    OrphanedSecretError,
    UnscopedReadError,
    UnknownSecretError,
    ValueNotPermittedError,
)

FAKE = "fake-placeholder-never-a-credential"


# --------------------------------------------------------------------------
# The projection
# --------------------------------------------------------------------------


def test_projection_is_deterministic(view, repo_root):
    assert view == vault.build_view(repo_root)


def test_projection_is_sorted_by_gsm_path(view):
    paths = [record["gsm_path"] for record in view["secrets"]]
    assert paths == sorted(paths)
    assert len(paths) >= 2


def test_projection_names_the_single_store_of_record(view):
    assert view["store"] == GSM_STORE
    assert {record["store"] for record in view["secrets"]} == {GSM_STORE}


def test_projection_carries_no_value_anywhere(view):
    for record in view["secrets"]:
        assert not (set(record) & vault.VALUE_KEYS)


def test_committed_view_conforms(view, repo_root):
    assert vault.validate_view(view, root=repo_root) == []


def test_missing_catalog_projects_an_empty_view(tmp_path):
    assert vault.build_view(tmp_path) == {"store": GSM_STORE, "secrets": []}


def _write_catalog(root, document):
    path = root / vault.CATALOG_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")


def test_projection_fails_closed_on_a_carried_value(tmp_path):
    _write_catalog(tmp_path, {"store": GSM_STORE, "secrets": [
        {"gsm_path": "projects/example/secrets/a", "agent": "a", "scope": "agent:a",
         "store": GSM_STORE, "consumer": "a", "last_rotated_at": None, "value": FAKE},
    ]})
    with pytest.raises(ValueNotPermittedError) as exc:
        vault.build_view(tmp_path)
    assert "forbidden key 'value'" in str(exc.value)
    assert FAKE not in str(exc.value)


def test_catalog_findings_reports_a_second_store_and_a_carried_value():
    document = {"store": "upstream-paperclip-secret-store", "secrets": [
        {"gsm_path": "projects/example/secrets/a", "agent": "a", "scope": "agent:a",
         "value": FAKE},
    ]}
    findings = vault.catalog_findings(document)
    assert any("is not the store of record" in finding for finding in findings)
    assert any("forbidden key 'value'" in finding for finding in findings)
    assert all(FAKE not in finding for finding in findings)


def test_a_clean_catalog_has_no_findings(repo_root):
    assert vault.catalog_findings(vault.load_catalog(repo_root)) == []


# --------------------------------------------------------------------------
# Refusals — each names the rule and the location, never a value
# --------------------------------------------------------------------------


def test_a_carried_value_is_refused_by_key_name(view, repo_root):
    mutant = copy.deepcopy(view)
    mutant["secrets"][0]["value"] = FAKE
    findings = vault.validate_view(mutant, root=repo_root)
    assert any("forbidden key 'value'" in finding for finding in findings)
    assert all(FAKE not in finding for finding in findings)


def test_value_findings_names_the_key_only():
    findings = vault.value_findings({"value": FAKE, "gsm_path": "projects/example/secrets/x"},
                                    "loc")
    assert findings and "forbidden key 'value'" in findings[0]
    assert FAKE not in findings[0]


def test_a_second_store_is_refused_by_name(view, repo_root):
    mutant = copy.deepcopy(view)
    mutant["secrets"][0]["store"] = "upstream-paperclip-secret-store"
    findings = vault.validate_view(mutant, root=repo_root)
    assert any("is not the store of record" in finding for finding in findings)


def test_a_second_store_at_document_level_is_refused(view, repo_root):
    mutant = copy.deepcopy(view)
    mutant["store"] = "upstream-paperclip-secret-store"
    findings = vault.validate_view(mutant, root=repo_root)
    assert any("is not the store of record" in finding for finding in findings)


def test_an_orphan_is_reported_by_path(view, repo_root):
    mutant = copy.deepcopy(view)
    mutant["secrets"][0]["consumer"] = None
    findings = vault.validate_view(mutant, root=repo_root)
    assert any("no consumer" in finding for finding in findings)
    assert any(mutant["secrets"][0]["gsm_path"] in finding for finding in findings)


def test_a_path_outside_the_gsm_shape_is_refused(view, repo_root):
    mutant = copy.deepcopy(view)
    mutant["secrets"][0]["gsm_path"] = "not-a-gsm-path"
    findings = vault.validate_view(mutant, root=repo_root)
    assert any("does not match pattern" in finding for finding in findings)


def test_a_missing_required_field_is_refused(view, repo_root):
    mutant = copy.deepcopy(view)
    del mutant["secrets"][0]["scope"]
    findings = vault.validate_view(mutant, root=repo_root)
    assert any("required field 'scope' is missing" in finding for finding in findings)


def test_orphan_findings_lists_every_orphan():
    document = {
        "store": GSM_STORE,
        "secrets": [
            {"gsm_path": "projects/example/secrets/a", "agent": "a", "scope": "agent:a",
             "consumer": None},
            {"gsm_path": "projects/example/secrets/b", "agent": "b", "scope": "agent:b",
             "consumer": "b"},
        ],
    }
    findings = vault.orphan_findings(document)
    assert len(findings) == 1 and "projects/example/secrets/a" in findings[0]
    assert vault.orphans(document) == ["projects/example/secrets/a"]


# --------------------------------------------------------------------------
# Rotation is expressible
# --------------------------------------------------------------------------


def test_rotation_report_expresses_time_and_consumer(view, repo_root):
    report = vault.rotation_report(vault.load_catalog(repo_root))
    assert report, "the committed catalog declares at least one secret"
    for entry in report:
        assert set(entry) == {
            "gsm_path", "agent", "scope", "consumer", "last_rotated_at", "rotated",
        }
        assert entry["consumer"], "a committed secret must have a consumer"
    assert all(entry["rotated"] is False or entry["last_rotated_at"] for entry in report)


def test_a_never_rotated_secret_is_visible():
    document = {
        "store": GSM_STORE,
        "secrets": [
            {"gsm_path": "projects/example/secrets/a", "agent": "a", "scope": "agent:a",
             "consumer": "a", "last_rotated_at": None},
        ],
    }
    report = vault.rotation_report(document)
    assert report[0]["rotated"] is False
    assert report[0]["last_rotated_at"] is None


def test_declared_scopes_are_deduplicated_and_sorted():
    document = {
        "store": GSM_STORE,
        "secrets": [
            {"gsm_path": "projects/example/secrets/a", "agent": "a", "scope": "agent:b"},
            {"gsm_path": "projects/example/secrets/b", "agent": "a", "scope": "agent:a"},
            {"gsm_path": "projects/example/secrets/c", "agent": "a", "scope": "agent:a"},
        ],
    }
    assert vault.declared_scopes(document) == ("agent:a", "agent:b")


# --------------------------------------------------------------------------
# The read guard
# --------------------------------------------------------------------------


def _first(view):
    return view["secrets"][0]


def test_read_returns_the_reference_not_a_value(view):
    record = _first(view)
    caller = Caller.of("session-1", [record["scope"]])
    result = vault.read_secret(view, record["gsm_path"], caller)
    assert result["gsm_path"] == record["gsm_path"]
    assert not (set(result) & vault.VALUE_KEYS)
    assert result == record


def test_unauthenticated_read_is_refused(view):
    record = _first(view)
    with pytest.raises(UnscopedReadError):
        vault.read_secret(view, record["gsm_path"], Caller.of(""))


def test_unscoped_read_is_refused_and_names_the_scope(view):
    record = _first(view)
    with pytest.raises(UnscopedReadError) as exc:
        vault.read_secret(view, record["gsm_path"], Caller.of("session-1", ["agent:someone-else"]))
    assert record["scope"] in str(exc.value)


def test_wildcard_scope_is_still_a_scoped_read(view):
    from integrations.paperclip.adapters.secrets.model import SECRET_WILDCARD

    record = _first(view)
    caller = Caller.of("admin-session", [SECRET_WILDCARD])
    assert vault.read_secret(view, record["gsm_path"], caller)["gsm_path"] == record["gsm_path"]


def test_reading_an_orphan_is_refused(view):
    mutant = copy.deepcopy(view)
    mutant["secrets"][0]["consumer"] = None
    record = _first(mutant)
    caller = Caller.of("session-1", [record["scope"]])
    with pytest.raises(OrphanedSecretError):
        vault.read_secret(mutant, record["gsm_path"], caller)


def test_reading_an_unknown_path_is_refused(view):
    with pytest.raises(UnknownSecretError):
        vault.read_secret(view, "projects/example/secrets/nope", Caller.of("s", ["secret:*"]))


def test_read_from_root_matches_the_projection(view, repo_root):
    record = _first(view)
    caller = Caller.of("session-1", [record["scope"]])
    assert vault.read_secret_from_root(repo_root, record["gsm_path"], caller) == record
