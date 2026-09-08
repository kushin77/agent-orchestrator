"""Per-tenant API-key vault - encrypted at rest, never logged (issue #15).

Design (mirrors the repo secret doctrine GR-6 and the harvested provider key
handling):

- API keys are stored ENCRYPTED AT REST. The master key comes from the
  environment variable ``AO_VAULT_KEY`` (a Fernet key); it is never a literal
  in code or committed anywhere.
- Encryption uses ``cryptography`` (Fernet = AES-128-CBC + HMAC-SHA256,
  authenticated). If the library is absent the vault REFUSES to operate
  (``VaultUnavailableError``) rather than downgrade to plaintext or to a
  hand-rolled cipher - honest fail-closed, never security theater.
- Keys never appear in logs, exceptions, ``repr``/``str`` or the persisted
  vault file (which holds only ciphertext tokens + tenant/provider metadata).
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from providers.errors import VaultError, VaultUnavailableError

log = logging.getLogger("gateway.providers.vault")

MASTER_KEY_ENV = "AO_VAULT_KEY"


class CryptoBackend(Protocol):
    """Encrypt/decrypt string payloads (authenticated encryption)."""

    def encrypt(self, plaintext: str) -> str:
        ...

    def decrypt(self, token: str) -> str:
        ...


class FernetBackend:
    """Fernet (AES-128-CBC + HMAC) via the ``cryptography`` package."""

    def __init__(self, master_key: bytes) -> None:
        try:
            from cryptography.fernet import Fernet
        except ImportError as exc:  # pragma: no cover - guarded by from_env too
            raise VaultUnavailableError(
                "the cryptography package is required for at-rest key encryption "
                "and is not installed; refusing to store keys in plaintext"
            ) from exc
        self._fernet = Fernet(master_key)

    def encrypt(self, plaintext: str) -> str:
        token = self._fernet.encrypt(plaintext.encode("utf-8"))
        return token.decode("utf-8")

    def decrypt(self, token: str) -> str:
        plaintext = self._fernet.decrypt(token.encode("utf-8"))
        return plaintext.decode("utf-8")


def new_master_key() -> str:
    """Generate a fresh Fernet master key (operator bootstrap; never stored)."""
    from cryptography.fernet import Fernet

    return Fernet.generate_key().decode("ascii")


def load_master_key(env: str | None = None) -> bytes:
    """Read and validate the master key from ``AO_VAULT_KEY``.

    Raises VaultUnavailableError when unset or malformed (fail closed).
    """
    raw = os.environ.get(env or MASTER_KEY_ENV, "")
    if not raw:
        raise VaultUnavailableError(
            f"{MASTER_KEY_ENV} is not set; refusing to operate an unencrypted vault"
        )
    try:
        key = base64.urlsafe_b64decode(raw.encode("ascii"))
    except Exception as exc:
        raise VaultUnavailableError(
            f"{MASTER_KEY_ENV} is not valid base64"
        ) from exc
    if len(key) != 32:
        raise VaultUnavailableError(
            f"{MASTER_KEY_ENV} must decode to exactly 32 bytes (a Fernet key)"
        )
    return raw.encode("ascii")


def fernet_available() -> bool:
    """Whether the ``cryptography`` package is importable."""
    import importlib.util

    return importlib.util.find_spec("cryptography") is not None


@dataclass
class VaultEntry:
    """One tenant+provider key, stored encrypted at rest."""

    tenant_id: str
    provider: str
    token: str
    created_at: float
    updated_at: float


class ApiKeyVault:
    """Per-tenant, per-provider API-key store encrypted at rest.

    Backed by an optional JSON file on disk whose contents are ciphertext
    only (never plaintext keys). An in-memory vault (no ``path``) is also
    supported for tests.
    """

    def __init__(self, backend: CryptoBackend, path: str | None = None) -> None:
        self._backend = backend
        self._path = Path(path) if path else None
        self._entries: dict[tuple[str, str], VaultEntry] = {}
        self._load()

    @classmethod
    def from_env(cls, path: str | None = None) -> "ApiKeyVault":
        """Build a vault from ``AO_VAULT_KEY`` (fail closed without it)."""
        if not fernet_available():
            raise VaultUnavailableError(
                "cryptography is not installed; refusing to store API keys at rest "
                "without real encryption (fail closed)"
            )
        master_key = load_master_key()
        return cls(FernetBackend(master_key), path=path)

    # -- key lifecycle ------------------------------------------------------ #
    def set_key(self, tenant_id: str, provider: str, api_key: str) -> None:
        """Encrypt and store a key. Never logs or persists the plaintext."""
        if not api_key:
            raise VaultError("refusing to store an empty API key")
        token = self._backend.encrypt(api_key)
        now = time.time()
        existing = self._entries.get((tenant_id, provider))
        self._entries[(tenant_id, provider)] = VaultEntry(
            tenant_id=tenant_id,
            provider=provider,
            token=token,
            created_at=existing.created_at if existing else now,
            updated_at=now,
        )
        self._persist()
        log.info("api_key.updated", extra={"tenant_id": tenant_id, "provider": provider})

    def get_key(self, tenant_id: str, provider: str) -> str | None:
        """Return the plaintext key for a tenant+provider (None when absent)."""
        entry = self._entries.get((tenant_id, provider))
        if entry is None:
            return None
        return self._backend.decrypt(entry.token)

    def has_key(self, tenant_id: str, provider: str) -> bool:
        return (tenant_id, provider) in self._entries

    def delete_key(self, tenant_id: str, provider: str) -> bool:
        removed = self._entries.pop((tenant_id, provider), None)
        if removed is not None:
            self._persist()
            log.info("api_key.deleted", extra={"tenant_id": tenant_id, "provider": provider})
        return removed is not None

    def providers_for(self, tenant_id: str) -> tuple[str, ...]:
        """Providers that have a key for a tenant (metadata only, no keys)."""
        return tuple(sorted(p for (t, p) in self._entries if t == tenant_id))

    def count(self) -> int:
        return len(self._entries)

    # -- persistence -------------------------------------------------------- #
    def _serialize(self) -> dict[str, Any]:
        return {
            "version": 1,
            "entries": [
                {
                    "tenant_id": e.tenant_id,
                    "provider": e.provider,
                    "token": e.token,  # ciphertext only - never plaintext
                    "created_at": e.created_at,
                    "updated_at": e.updated_at,
                }
                for e in self._entries.values()
            ],
        }

    def _persist(self) -> None:
        if self._path is None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w", encoding="utf-8") as fh:
            json.dump(self._serialize(), fh, indent=2)
            fh.write("\n")
        # The on-disk file must never contain plaintext key material.
        os.chmod(self._path, 0o600)

    def _load(self) -> None:
        if self._path is None or not self._path.exists():
            return
        with open(self._path, "r", encoding="utf-8") as fh:
            document = json.load(fh)
        for raw in document.get("entries", []):
            entry = VaultEntry(
                tenant_id=raw["tenant_id"],
                provider=raw["provider"],
                token=raw["token"],
                created_at=raw.get("created_at", 0.0),
                updated_at=raw.get("updated_at", 0.0),
            )
            self._entries[(entry.tenant_id, entry.provider)] = entry

    # -- non-leaking representations --------------------------------------- #
    def __repr__(self) -> str:
        return f"<ApiKeyVault entries={self.count()} path={self._path}>"

    __str__ = __repr__
