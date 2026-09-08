"""API-key vault tests (issue #15, criterion 2).

Covers: encrypt/decrypt round-trip, encryption AT REST (the on-disk file
holds ciphertext only - never the plaintext key), cross-instance persistence,
per-tenant isolation, key deletion, and the no-plaintext-in-logs guarantee.
Also asserts the vault FAILS CLOSED when the master key is absent (no
unencrypted fallback) - an honest gate, never security theater.
"""

from __future__ import annotations

import json
import logging

import pytest

from providers.vault import (
    MASTER_KEY_ENV,
    ApiKeyVault,
    FernetBackend,
    VaultUnavailableError,
    load_master_key,
    new_master_key,
)

from conftest import FAKE_KEY, FAKE_KEY_2


def _backend() -> FernetBackend:
    return FernetBackend(new_master_key().encode("ascii"))


def test_encrypt_decrypt_round_trip() -> None:
    vault = ApiKeyVault(_backend())
    vault.set_key("acme", "anthropic", FAKE_KEY)
    assert vault.get_key("acme", "anthropic") == FAKE_KEY
    assert vault.has_key("acme", "anthropic")
    assert vault.providers_for("acme") == ("anthropic",)


def test_keys_are_encrypted_at_rest(tmp_path) -> None:
    path = tmp_path / "vault.json"
    vault = ApiKeyVault(_backend(), path=str(path))
    vault.set_key("acme", "deepseek", FAKE_KEY)
    raw = path.read_text(encoding="utf-8")
    # The plaintext key never touches the file.
    assert FAKE_KEY not in raw
    # The file holds an encrypted token per entry and metadata only.
    document = json.loads(raw)
    assert document["version"] == 1
    entry = document["entries"][0]
    assert entry["tenant_id"] == "acme"
    assert entry["provider"] == "deepseek"
    assert entry["token"]
    assert entry["token"] != FAKE_KEY


def test_persistence_across_instances(tmp_path) -> None:
    path = tmp_path / "vault.json"
    backend = _backend()  # same master key on both sides
    writer = ApiKeyVault(backend, path=str(path))
    writer.set_key("acme", "openai", FAKE_KEY)
    reader = ApiKeyVault(backend, path=str(path))
    assert reader.get_key("acme", "openai") == FAKE_KEY
    assert reader.count() == 1


def test_per_tenant_isolation() -> None:
    vault = ApiKeyVault(_backend())
    vault.set_key("acme", "deepseek", FAKE_KEY)
    vault.set_key("globex", "deepseek", FAKE_KEY_2)
    assert vault.get_key("acme", "deepseek") == FAKE_KEY
    assert vault.get_key("globex", "deepseek") == FAKE_KEY_2
    assert vault.providers_for("acme") == ("deepseek",)
    assert vault.providers_for("globex") == ("deepseek",)
    assert vault.get_key("acme", "anthropic") is None


def test_get_missing_key_returns_none() -> None:
    vault = ApiKeyVault(_backend())
    assert vault.get_key("acme", "anthropic") is None
    assert not vault.has_key("acme", "anthropic")


def test_delete_key_removes_entry() -> None:
    vault = ApiKeyVault(_backend())
    vault.set_key("acme", "gemini", FAKE_KEY)
    assert vault.delete_key("acme", "gemini") is True
    assert vault.get_key("acme", "gemini") is None
    assert vault.delete_key("acme", "gemini") is False


def test_empty_key_refused() -> None:
    vault = ApiKeyVault(_backend())
    with pytest.raises(Exception):
        vault.set_key("acme", "anthropic", "")


def test_no_plaintext_in_logs(caplog) -> None:
    caplog.set_level(logging.DEBUG, logger="gateway.providers.vault")
    vault = ApiKeyVault(_backend())
    vault.set_key("acme", "anthropic", FAKE_KEY)
    assert vault.get_key("acme", "anthropic") == FAKE_KEY
    vault.delete_key("acme", "anthropic")
    # repr/str never disclose key material.
    assert FAKE_KEY not in repr(vault)
    assert FAKE_KEY not in str(vault)
    # No emitted log record contains the plaintext key.
    assert FAKE_KEY not in caplog.text


def test_from_env_fails_closed_without_master_key(monkeypatch) -> None:
    monkeypatch.delenv(MASTER_KEY_ENV, raising=False)
    with pytest.raises(VaultUnavailableError):
        ApiKeyVault.from_env()


def test_from_env_works_with_master_key(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(MASTER_KEY_ENV, new_master_key())
    vault = ApiKeyVault.from_env(path=str(tmp_path / "vault.json"))
    vault.set_key("acme", "ollama", FAKE_KEY)
    assert vault.get_key("acme", "ollama") == FAKE_KEY


def test_load_master_key_validation(monkeypatch) -> None:
    monkeypatch.setenv(MASTER_KEY_ENV, "not-valid-base64!!!")
    with pytest.raises(VaultUnavailableError):
        load_master_key()
    monkeypatch.setenv(MASTER_KEY_ENV, "too-short")
    with pytest.raises(VaultUnavailableError):
        load_master_key()
