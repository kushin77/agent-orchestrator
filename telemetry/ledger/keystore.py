"""Injected per-tenant key store (telemetry/ledger, issue #31).

Every tenant's sensitive payloads are encrypted with a per-tenant AES-256 key.
Keys NEVER live in code or in the ledger: they are injected from environment,
a keystore file, or an in-process mapping supplied by the operator. If no key
can be resolved for a tenant, appending a sensitive payload is refused
(:class:`KeyUnavailableError`) - fail closed, no plaintext fallback.

Key sources (in resolution order per ``get``):

1. A :class:`DictKeystore` (tests / in-process injection).
2. An :class:`EnvKeystore` reading ``AO_AUDIT_LEDGER_KEY_<TENANT>`` (64 hex
   chars, i.e. 32 bytes) with optional ``AO_AUDIT_LEDGER_KEY_ID_<TENANT>``.
3. A :class:`FileKeystore` reading a JSON file mapping tenant to
   ``{"key": "<64 hex>", "id": "<key id>"}`` (``id`` optional).

A per-tenant key material is ``(key bytes, key id)``; the key id is stored in
each envelope so a future key rotation is auditable without the key.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional
from abc import ABC, abstractmethod

from .errors import KeyUnavailableError, LedgerCryptoError

# Env prefix for per-tenant keys: AO_AUDIT_LEDGER_KEY_<TENANT>.
_ENV_KEY_PREFIX = "AO_AUDIT_LEDGER_KEY_"
_ENV_KEY_ID_PREFIX = "AO_AUDIT_LEDGER_KEY_ID_"

# Tenant names can carry almost anything on the wire; env var names cannot.
# Non-alphanumerics map to '_' so acme-corp / acme_corp stay distinct enough.
_NON_ALNUM_RE = re.compile(r"[^A-Za-z0-9]")
_HEX_RE = re.compile(r"^[0-9a-fA-F]{64}$")


@dataclass(frozen=True)
class KeyMaterial:
    """One tenant's resolved encryption key material."""

    key: bytes
    key_id: str


def _env_name(tenant_id: str) -> str:
    return _ENV_KEY_PREFIX + _NON_ALNUM_RE.sub("_", tenant_id).upper()


def _env_key_id_name(tenant_id: str) -> str:
    return _ENV_KEY_ID_PREFIX + _NON_ALNUM_RE.sub("_", tenant_id).upper()


def parse_key_hex(value: str) -> bytes:
    """Parse a 64-hex (32-byte AES-256) key string, failing closed otherwise."""
    if not isinstance(value, str) or not _HEX_RE.match(value):
        raise LedgerCryptoError(
            "key must be a 64-character hex string (32 bytes, AES-256)"
        )
    return bytes.fromhex(value)


class Keystore(ABC):
    """Resolves a per-tenant :class:`KeyMaterial`; abstract base."""

    @abstractmethod
    def get(self, tenant_id: str) -> KeyMaterial:
        """Return the tenant's key material or raise :class:`KeyUnavailableError`."""


class DictKeystore(Keystore):
    """In-process mapping tenant -> key material (tests, embedded injection)."""

    def __init__(self, materials: Mapping[str, KeyMaterial]) -> None:
        self._materials = dict(materials)

    def get(self, tenant_id: str) -> KeyMaterial:
        material = self._materials.get(tenant_id)
        if material is None:
            raise KeyUnavailableError(
                f"no key material configured for tenant {tenant_id!r}"
            )
        return material


class EnvKeystore(Keystore):
    """Per-tenant keys from the environment (deployment / secret injection)."""

    def __init__(self, environ: Optional[Mapping[str, str]] = None) -> None:
        self._environ = dict(os.environ if environ is None else environ)

    def get(self, tenant_id: str) -> KeyMaterial:
        raw = self._environ.get(_env_name(tenant_id))
        if raw is None or not raw:
            raise KeyUnavailableError(
                f"no key for tenant {tenant_id!r} in environment "
                f"({_env_name(tenant_id)})"
            )
        key = parse_key_hex(raw)
        key_id = self._environ.get(_env_key_id_name(tenant_id)) or f"env:{tenant_id}"
        return KeyMaterial(key=key, key_id=key_id)


class FileKeystore(Keystore):
    """Keys from a JSON keystore file: ``{"<tenant>": {"key": hex, "id": str}}``."""

    def __init__(self, path: str) -> None:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError) as exc:
            raise LedgerCryptoError(f"cannot read keystore {path}: {exc}") from exc
        if not isinstance(data, dict):
            raise LedgerCryptoError(f"keystore {path} must be a JSON object")
        self._data: Dict[str, Dict[str, Any]] = data

    def get(self, tenant_id: str) -> KeyMaterial:
        entry = self._data.get(tenant_id)
        if entry is None or not isinstance(entry, dict):
            raise KeyUnavailableError(
                f"no key for tenant {tenant_id!r} in keystore file"
            )
        raw = entry.get("key")
        if not isinstance(raw, str):
            raise LedgerCryptoError(
                f"keystore entry for {tenant_id!r} has no hex 'key' string"
            )
        key = parse_key_hex(raw)
        key_id = entry.get("id") or f"file:{tenant_id}"
        if not isinstance(key_id, str) or not key_id:
            raise LedgerCryptoError(f"keystore key id for {tenant_id!r} is invalid")
        return KeyMaterial(key=key, key_id=key_id)
