"""Keystore - where per-tenant IdP certificates/keys and platform signing
material live, by alias. Mirrors the harvested model (saas-rbac ``x509`` /
capital-underwriting ``idpCertEnc`` / shared-frontend ``JWT_RS256_*`` env):
configs reference **aliases**, never raw material, and secret/private key
material is injected at runtime from env/secrets - it is never stored in the
repo and never written to disk by this module.

Aliases held here:

- ``cert:<alias>``  - an X.509 certificate (IdP SAML signing cert, or the
  container of an OIDC RS256 public key).
- ``pub:<alias>``   - a bare RSA public key (SPKI PEM / key object).
- ``priv:<alias>``  - the platform's RSA private key(s) (console RS256
  session signing; current + retained verification keys).
- ``sec:<alias>``   - opaque bytes secrets (HS256 session HMAC secret,
  OIDC client secret, relay-state HMAC secret).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .errors import SsoError, UnknownKeyError
from .jose import (
    _CRYPTO_AVAILABLE,
    cert_public_key,
    load_public_key,
    load_x509_certificate,
    rsa_public_key_matches,
)


@dataclass
class KeyStore:
    """Thread-unsafe alias -> key-material store (runtime injection only)."""

    _certs: dict[str, Any] = field(default_factory=dict)
    _public_keys: dict[str, Any] = field(default_factory=dict)
    _private_keys: dict[str, Any] = field(default_factory=dict)
    _secrets: dict[str, bytes] = field(default_factory=dict)

    # --- certificates --------------------------------------------------------

    def put_cert(self, alias: str, cert: Any) -> None:
        self._certs[alias] = cert

    def put_cert_pem(self, alias: str, pem: bytes) -> None:
        self._certs[alias] = load_x509_certificate(pem)

    def get_cert(self, alias: str) -> Any:
        if alias not in self._certs:
            raise UnknownKeyError(f"no certificate for alias {alias!r}")
        return self._certs[alias]

    def cert_public_key(self, alias: str) -> Any:
        """The RSA public key inside a stored certificate."""
        if not _CRYPTO_AVAILABLE:
            raise SsoError("cryptography is required for certificate handling")
        return cert_public_key(self.get_cert(alias))

    def cert_matches(self, alias: str, cert_or_key: Any) -> bool:
        """True when a presented cert/key is the trusted one for ``alias``."""
        trusted = self.cert_public_key(alias)
        if type(cert_or_key).__name__ == "Certificate":
            cert_or_key = cert_public_key(cert_or_key)
        return rsa_public_key_matches(trusted, cert_or_key)

    # --- bare public keys ------------------------------------------------------

    def put_public_key(self, alias: str, key: Any) -> None:
        self._public_keys[alias] = key

    def put_public_key_pem(self, alias: str, pem: bytes) -> None:
        self._public_keys[alias] = load_public_key(pem)

    def get_public_key(self, alias: str) -> Any:
        if alias not in self._public_keys:
            raise UnknownKeyError(f"no public key for alias {alias!r}")
        return self._public_keys[alias]

    def resolve_signing_public_key(self, alias: str) -> Any:
        """The RSA public key for an alias, from a bare public key or a cert.

        OIDC IdP RS256 verification keys arrive as bare JWKS public keys while
        SAML IdP keys arrive as x509 certificates - both are trusted through
        the same ``cert_alias`` and resolved here.
        """
        if alias in self._public_keys:
            return self._public_keys[alias]
        if alias in self._certs:
            if not _CRYPTO_AVAILABLE:
                raise SsoError("cryptography is required for certificate handling")
            return cert_public_key(self._certs[alias])
        raise UnknownKeyError(f"no public key or certificate for alias {alias!r}")

    # --- private keys (platform session signing only) ---------------------------

    def put_private_key(self, alias: str, key: Any) -> None:
        self._private_keys[alias] = key

    def get_private_key(self, alias: str) -> Any:
        if alias not in self._private_keys:
            raise UnknownKeyError(f"no private key for alias {alias!r}")
        return self._private_keys[alias]

    def list_private_aliases(self) -> tuple[str, ...]:
        return tuple(self._private_keys)

    # --- opaque secrets ---------------------------------------------------------

    def put_secret(self, alias: str, secret: bytes) -> None:
        self._secrets[alias] = secret

    def get_secret(self, alias: str) -> bytes:
        if alias not in self._secrets:
            raise UnknownKeyError(f"no secret for alias {alias!r}")
        return self._secrets[alias]

    def require(self, alias: str, kind: str) -> Any:
        """Fetch an alias by kind, raising a clear UnknownKeyError otherwise."""
        if kind == "cert":
            return self.get_cert(alias)
        if kind == "pub":
            return self.get_public_key(alias)
        if kind == "priv":
            return self.get_private_key(alias)
        if kind == "sec":
            return self.get_secret(alias)
        raise SsoError(f"unknown keystore kind {kind!r}")
