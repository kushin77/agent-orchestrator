"""Scrub-rule catalog tests (strict, fail-closed loading).

A catalog the loader cannot fully understand must be rejected: no silent skip,
no empty-rule pass, no unknown action/class/severity downgrade. Every branch
below asserts a CatalogError (the gate refuses to run rather than guess).
"""

from __future__ import annotations

import copy

import pytest
import yaml

from dlp.catalog import CatalogError, RuleCatalog


def _catalog_doc(**overrides) -> dict:
    doc = {
        "schema_version": "1",
        "ruleset_version": "9.9.9",
        "rules": [
            {
                "id": "pii.email",
                "class": "pii",
                "severity": "medium",
                "action": "redact",
                "placeholder": "<REDACTED_EMAIL>",
                "pattern": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
            },
            {
                "id": "secret.generic",
                "class": "secret",
                "severity": "critical",
                "action": "block",
                "pattern": r"\bsecret\s*[:=]\s*[A-Za-z0-9]{20,}\b",
            },
        ],
    }
    doc.update(overrides)
    return doc


def _write(tmp_path, doc, name="rules.yml"):
    path = tmp_path / name
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return str(path)


def _load_doc(tmp_path, mutate) -> None:
    """Assert that the mutated catalog is rejected at load time."""
    doc = _catalog_doc()
    mutate(doc)
    path = _write(tmp_path, doc)
    with pytest.raises(CatalogError):
        RuleCatalog.load(path)


def test_default_catalog_loads_with_expected_rules():
    catalog = RuleCatalog.load()
    assert catalog.ruleset_version == "1.0.0"
    assert catalog.schema_version == "1"
    assert len(catalog.rules) >= 20
    ids = {r.id for r in catalog.rules}
    for expected in (
        "pii.email",
        "pii.phone",
        "pii.credit_card",
        "pii.ssn_us",
        "secret.github_token",
        "secret.github_fine_grained",
        "secret.jwt",
        "privatekey.pem_block",
        "cloud.aws_access_key_id",
        "cloud.aws_secret_access_key",
        "cloud.gcp_service_account",
        "vault.path",
        "infra.internal_hostname",
        "infra.rfc1918_ip",
        "infra.internal_url",
    ):
        assert expected in ids, f"missing rule {expected}"


def test_valid_catalog_loads(tmp_path):
    path = _write(tmp_path, _catalog_doc())
    catalog = RuleCatalog.load(path)
    assert len(catalog.rules) == 2
    assert catalog.get("pii.email").placeholder == "<REDACTED_EMAIL>"
    assert catalog.get("secret.generic").is_block


def test_missing_catalog_file_raises(tmp_path):
    with pytest.raises(CatalogError):
        RuleCatalog.load(str(tmp_path / "nope.yml"))


def test_empty_rules_list_is_rejected(tmp_path):
    path = _write(tmp_path, _catalog_doc(rules=[]))
    with pytest.raises(CatalogError):
        RuleCatalog.load(path)


def test_missing_ruleset_version_is_rejected(tmp_path):
    path = _write(tmp_path, _catalog_doc(ruleset_version=None))
    with pytest.raises(CatalogError):
        RuleCatalog.load(path)


def test_duplicate_rule_id_is_rejected(tmp_path):
    def mutate(doc):
        doc["rules"].append(copy.deepcopy(doc["rules"][0]))

    _load_doc(tmp_path, mutate)


def test_unknown_action_is_rejected(tmp_path):
    def mutate(doc):
        doc["rules"][0]["action"] = "maybe"

    _load_doc(tmp_path, mutate)


def test_unknown_class_is_rejected(tmp_path):
    def mutate(doc):
        doc["rules"][0]["class"] = "spyware"

    _load_doc(tmp_path, mutate)


def test_unknown_severity_is_rejected(tmp_path):
    def mutate(doc):
        doc["rules"][0]["severity"] = "catastrophic"

    _load_doc(tmp_path, mutate)


def test_bad_regex_is_rejected_not_skipped(tmp_path):
    def mutate(doc):
        doc["rules"][0]["pattern"] = "(unclosed"

    _load_doc(tmp_path, mutate)


def test_redact_without_placeholder_is_rejected(tmp_path):
    def mutate(doc):
        del doc["rules"][0]["placeholder"]

    _load_doc(tmp_path, mutate)


def test_missing_pattern_is_rejected(tmp_path):
    def mutate(doc):
        del doc["rules"][0]["pattern"]

    _load_doc(tmp_path, mutate)


def test_non_mapping_rule_is_rejected(tmp_path):
    def mutate(doc):
        doc["rules"].append("not-a-mapping")

    _load_doc(tmp_path, mutate)


def test_luhn_must_be_boolean(tmp_path):
    def mutate(doc):
        doc["rules"][0]["luhn"] = "yes"

    _load_doc(tmp_path, mutate)
