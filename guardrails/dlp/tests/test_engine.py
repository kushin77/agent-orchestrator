"""DLP scrub-engine tests.

Negative controls (must block / must redact) use synthetic secrets and PII
assembled at runtime in ``support``; benign text must pass untouched. The
engine's contract: a block-class match aborts egress, a redact-class match
replaces the value with a stable placeholder, and an ambiguous/empty catalog
refuses to run (fail closed).
"""

from __future__ import annotations

import pytest

from dlp.catalog import CatalogError, RuleCatalog
from dlp.engine import ScrubEngine, luhn_valid

from support import (  # noqa: E402  (conftest adds this dir to sys.path)
    EMAIL,
    INTERNAL_HOST,
    INTERNAL_URL,
    PRIVATE_IP,
    SSN_US,
    VAULT_PATH,
    aws_access_key_id,
    aws_payload,
    aws_secret_access_key,
    bearer_token,
    benign_docs_payload,
    gcp_service_account_json,
    generic_api_key,
    github_fine_grained,
    github_payload,
    google_api_key,
    mixed_pii_payload,
    openai_api_key,
    pem_private_key,
    slack_token,
)

ENGINE = ScrubEngine()


# -- block-class negative controls (synthetic secrets) -----------------------


def test_aws_access_key_id_is_blocked():
    result = ENGINE.scrub(aws_payload())
    assert result.blocked
    assert "cloud.aws_access_key_id" in result.blocked_by


def test_aws_secret_access_key_assignment_is_blocked():
    result = ENGINE.scrub(f"export {aws_secret_access_key()}")
    assert result.blocked
    assert "cloud.aws_secret_access_key" in result.blocked_by


def test_github_classic_pat_is_blocked():
    result = ENGINE.scrub(github_payload())
    assert result.blocked
    assert "secret.github_token" in result.blocked_by


def test_github_fine_grained_pat_is_blocked():
    result = ENGINE.scrub(f"auth with {github_fine_grained()} now")
    assert result.blocked
    assert "secret.github_fine_grained" in result.blocked_by


def test_slack_token_is_blocked():
    result = ENGINE.scrub(f"post to channel using {slack_token()}")
    assert result.blocked
    assert "secret.slack_token" in result.blocked_by


def test_openai_api_key_is_blocked():
    result = ENGINE.scrub(f"call the model with {openai_api_key()}")
    assert result.blocked
    assert "secret.openai_api_key" in result.blocked_by


def test_google_api_key_is_blocked():
    result = ENGINE.scrub(f"use {google_api_key()} for maps")
    assert result.blocked
    assert "secret.google_api_key" in result.blocked_by


def test_generic_api_key_assignment_is_blocked():
    result = ENGINE.scrub(f"cfg {generic_api_key()} and run")
    assert result.blocked
    assert "secret.generic_api_key" in result.blocked_by


def test_bearer_token_is_blocked():
    result = ENGINE.scrub(f"Authorization: {bearer_token()}")
    assert result.blocked
    assert "secret.bearer_token" in result.blocked_by


def test_pem_private_key_block_is_blocked():
    result = ENGINE.scrub(pem_private_key())
    assert result.blocked
    assert "privatekey.pem_block" in result.blocked_by


def test_gcp_service_account_json_is_blocked():
    result = ENGINE.scrub(gcp_service_account_json())
    assert result.blocked
    assert "cloud.gcp_service_account" in result.blocked_by


def test_vault_path_is_blocked():
    result = ENGINE.scrub(f"read {VAULT_PATH} before deploy")
    assert result.blocked
    assert "vault.path" in result.blocked_by


def test_blocked_payload_is_never_sent():
    # A blocked result must not carry a "sendable" redacted text: the original
    # payload is returned untouched and the caller aborts the consult.
    payload = aws_payload()
    result = ENGINE.scrub(payload)
    assert result.blocked
    assert result.text == payload
    assert aws_access_key_id() in result.text


def test_blocked_result_reports_all_counts():
    result = ENGINE.scrub(f"keys {aws_access_key_id()} and {aws_access_key_id()}")
    assert result.blocked
    assert result.counts["cloud.aws_access_key_id"] == 2
    assert result.class_counts["cloud_cred"] == 2


# -- redact-class negative controls (PII + internal infra) -------------------


def test_email_phone_cc_ssn_all_redacted_structure_kept():
    result = ENGINE.scrub(mixed_pii_payload())
    assert result.verdict == "sent"
    text = result.text
    assert EMAIL not in text and "<REDACTED_EMAIL>" in text
    assert "555-0199" not in text and "<REDACTED_PHONE>" in text
    assert "4111111111111111" not in text and "<REDACTED_CCN>" in text
    assert SSN_US not in text and "<REDACTED_SSN>" in text
    # Structure (the surrounding sentence) survives redaction.
    assert "Send the invoice to" in text
    assert "quote SSN" in text and "<REDACTED_SSN>" in text


def test_internal_host_and_ip_redacted_but_sent():
    result = ENGINE.scrub(
        f"connect to {INTERNAL_HOST} ({PRIVATE_IP}) for the job"
    )
    assert result.verdict == "sent"
    assert "<REDACTED_INTERNAL_HOST>" in result.text
    assert "<REDACTED_PRIVATE_IP>" in result.text


def test_internal_url_outranks_inner_hostname_redact():
    # Overlapping matches: the internal URL is longer and starts earlier, so it
    # wins; the inner hostname is not double-replaced.
    result = ENGINE.scrub(f"open {INTERNAL_URL} to reconcile")
    assert result.verdict == "sent"
    assert "<REDACTED_INTERNAL_URL>" in result.text
    assert "<REDACTED_INTERNAL_HOST>" not in result.text


def test_redact_counts_are_reported():
    payload = f"mail {EMAIL} and {EMAIL} and call 415-555-0199"
    result = ENGINE.scrub(payload)
    assert result.verdict == "sent"
    assert result.counts["pii.email"] == 2
    assert result.counts["pii.phone"] == 1


# -- credit-card Luhn validation ---------------------------------------------


def test_luhn_valid_credit_card_is_redacted():
    result = ENGINE.scrub("charge card 4111111111111111 for the order")
    assert result.verdict == "sent"
    assert "<REDACTED_CCN>" in result.text


def test_luhn_invalid_digit_run_is_not_redacted():
    # Same shape, checksum-invalid: must NOT be treated as a card number
    # (guards the negative control against over-scrubbing random IDs).
    result = ENGINE.scrub("reference id 4111111111111112 is attached")
    assert result.verdict == "sent"
    assert "<REDACTED_CCN>" not in result.text
    assert "4111111111111112" in result.text


def test_luhn_helper_rejects_non_digits_and_known_invalid():
    assert luhn_valid("4111111111111111")
    assert not luhn_valid("4111111111111112")
    assert not luhn_valid("4111-1111-1111")  # not purely digits (caller strips)


# -- benign text must pass untouched (no-false-green negative control) --------


def test_benign_payload_passes_unchanged():
    result = ENGINE.scrub(benign_docs_payload())
    assert result.verdict == "sent"
    assert result.text == benign_docs_payload()
    assert result.counts == {}


def test_engine_refuses_to_run_on_empty_catalog(tmp_path):
    # Fail closed: an empty catalog must never silently pass content through.
    import yaml

    path = tmp_path / "empty.yml"
    path.write_text(
        yaml.safe_dump({"schema_version": "1", "ruleset_version": "1.0", "rules": []}),
        encoding="utf-8",
    )
    with pytest.raises(CatalogError):
        ScrubEngine(catalog=RuleCatalog.load(str(path)))


def test_engine_runs_with_explicit_catalog(tmp_path):
    import yaml

    doc = {
        "schema_version": "1",
        "ruleset_version": "7.0.0",
        "rules": [
            {
                "id": "secret.only",
                "class": "secret",
                "severity": "critical",
                "action": "block",
                "pattern": r"\bSECRET\b",
            }
        ],
    }
    path = tmp_path / "one.yml"
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    engine = ScrubEngine(catalog=RuleCatalog.load(str(path)))
    assert engine.scrub("the SECRET word").blocked
    assert engine.scrub("nothing sensitive").verdict == "sent"
    assert engine.catalog.ruleset_version == "7.0.0"
