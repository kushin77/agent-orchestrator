"""Test support for identity/sso (not collected by pytest).

Builds the offline SSO environment: an in-memory store + keystore with
freshly generated RSA keypairs / self-signed IdP certificates, registered
tenants, a configured ``SsoService``, and deterministic fixture builders that
produce signed SAML assertions and RS256/HS256 OIDC id_tokens. Private keys
exist only in memory for the duration of a test - nothing here is persisted.

Clock: all fixtures use ``NOW`` so tests are deterministic and the same signed
fixture can be replayed at a different ``now`` to exercise expiry/not-yet
valid windows.
"""

from __future__ import annotations

import base64
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any, Optional

from identity.sso.jose import (
    cert_der,
    generate_rsa_keypair,
    jwt_encode,
)
from identity.sso.keystore import KeyStore
from identity.sso.model import (
    DEFAULT_EMAIL_ATTRIBUTE,
    OIDC,
    SAML,
    TenantSsoConfig,
)
from identity.sso.saml import canonical_xml_bytes
from identity.sso.sessions import SsoService
from identity.sso.store import InMemoryStore

# 2027-01-12T00:00:00Z - comfortably after every fixture timestamp below.
NOW = 1_800_000_000
NOW_PLUS_HOUR = NOW + 3600
NOT_BEFORE = "2026-01-01T00:00:00Z"
NOT_ON_OR_AFTER = "2030-01-01T00:00:00Z"

_SAML_NS = "urn:oasis:names:tc:SAML:2.0:assertion"
_SAMLP_NS = "urn:oasis:names:tc:SAML:2.0:protocol"
_DS_NS = "http://www.w3.org/2000/09/xmldsig#"
RS256_ALG = "RS256"


def _self_signed_cert(private_key, common_name: str, serial: int):
    """A throwaway self-signed X.509 certificate (test fixture only)."""
    import datetime

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.x509.oid import NameOID

    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(private_key.public_key())
        .serial_number(serial)
        .not_valid_before(datetime.datetime(2026, 1, 1))
        .not_valid_after(datetime.datetime(2040, 1, 1))
        .add_extension(
            x509.BasicConstraints(ca=True, path_length=None), critical=False
        )
    )
    return builder.sign(private_key, hashes.SHA256())


@dataclass
class IdpKeys:
    """A per-tenant, per-protocol IdP keypair + self-signed cert."""

    protocol: str
    private_key: Any
    cert: Any
    cert_alias: str

    @property
    def cert_pem(self) -> bytes:
        from cryptography.hazmat.primitives import serialization

        return self.cert.public_bytes(serialization.Encoding.PEM)

    @property
    def cert_der_b64(self) -> str:
        return base64.b64encode(cert_der(self.cert)).decode("ascii")


@dataclass
class Env:
    """A complete offline SSO test environment."""

    store: InMemoryStore = field(default_factory=InMemoryStore)
    keystore: KeyStore = field(default_factory=KeyStore)
    session_hmac_key: bytes = bytes(range(32))
    relay_hmac_key: bytes = bytes(range(32, 64))
    idp_keys: dict[tuple[str, str], IdpKeys] = field(default_factory=dict)
    console_private: Any = None
    console_serial: int = 9000

    def __post_init__(self) -> None:
        if self.console_private is None:
            self.console_private, _ = generate_rsa_keypair()

    # --- key material ---------------------------------------------------------

    def _idp_keypair(self, protocol: str, tenant_id: str, serial: int) -> IdpKeys:
        private_key, _ = generate_rsa_keypair()
        cert = _self_signed_cert(private_key, f"{protocol}-{tenant_id}", serial)
        alias = f"cert:{tenant_id}-{protocol}"
        self.keystore.put_cert(alias, cert)
        keys = IdpKeys(protocol, private_key, cert, alias)
        self.idp_keys[(protocol, tenant_id)] = keys
        return keys

    def default_config(
        self, protocol: str, tenant_id: str = "acme", **overrides: Any
    ) -> dict[str, Any]:
        """Sane per-protocol config values for a tenant."""
        if protocol == SAML:
            base: dict[str, Any] = {
                "protocol": SAML,
                "idp_config_name": f"saml.{tenant_id}",
                "tenant_id": tenant_id,
                "idp_tenant_id": f"idp-{tenant_id}",
                "entity_id": f"urn:{tenant_id}:sp",
                "acs_url": f"https://{tenant_id}.example.com/acs",
                "issuer": f"https://idp.{tenant_id}.example.com",
                "idp_sso_url": f"https://idp.{tenant_id}.example.com/sso",
                "cert_alias": f"cert:{tenant_id}-{SAML}",
                "email_attribute": DEFAULT_EMAIL_ATTRIBUTE,
            }
        else:
            base = {
                "protocol": OIDC,
                "idp_config_name": f"oidc.{tenant_id}",
                "tenant_id": tenant_id,
                "idp_tenant_id": f"idp-{tenant_id}",
                "entity_id": f"{tenant_id}-client",
                "acs_url": f"https://{tenant_id}.example.com/cb",
                "issuer": f"https://issuer.{tenant_id}.example.com",
                "idp_sso_url": (
                    f"https://issuer.{tenant_id}.example.com/authorize"
                ),
                "cert_alias": f"cert:{tenant_id}-{OIDC}",
                "email_attribute": DEFAULT_EMAIL_ATTRIBUTE,
            }
        base.update(overrides)
        return base

    def register(
        self,
        protocol: str,
        tenant_id: str = "acme",
        domain: str = "",
        aliases: tuple[str, ...] = (),
        **overrides: Any,
    ) -> TenantSsoConfig:
        """Generate an IdP keypair + register the tenant (config + domain)."""
        from identity.sso.config import register_tenant_sso

        self.console_serial += 1
        self._idp_keypair(protocol, tenant_id, self.console_serial)
        values = self.default_config(protocol, tenant_id)
        values.update(overrides)
        config = TenantSsoConfig(**values)
        register_tenant_sso(
            self.store, config, primary_domain=domain, aliases=aliases
        )
        return config

    def service(self, **overrides: Any) -> SsoService:
        """A configured SsoService bound to this env's store + keystore."""
        params: dict[str, Any] = dict(
            store=self.store,
            keystore=self.keystore,
            session_hmac_key=self.session_hmac_key,
            relay_hmac_key=self.relay_hmac_key,
            console_signing_key=self.console_private,
        )
        params.update(overrides)
        return SsoService(**params)

    # --- SAML assertion builder -----------------------------------------------

    def saml_assertion(
        self,
        tenant_id: str = "acme",
        *,
        issuer: Optional[str] = None,
        audience: Optional[str] = None,
        subject: str = "alice@acme.example.com",
        email_attribute: str = DEFAULT_EMAIL_ATTRIBUTE,
        email: str = "alice@acme.example.com",
        attributes: Optional[dict[str, list[str]]] = None,
        not_before: str = NOT_BEFORE,
        not_on_or_after: str = NOT_ON_OR_AFTER,
        sign: bool = True,
        include_cert: bool = True,
        keys_override: Optional["IdpKeys"] = None,
    ) -> bytes:
        """Build (and sign) a SAML Response wrapping one Assertion.

        Returns the canonical serialization of the Response - a self-consistent
        fixture whose assertion signature is valid under the tenant's IdP cert
        (``sign=True``, the default). With ``sign=False`` the fixture is
        unsigned so tests can assert the missing-signature denial. Pass
        ``keys_override`` to sign with a *different* IdP key (forged-signature
        negatives).
        """
        config = self.store.config_for(tenant_id)
        assert config is not None
        keys = keys_override or self.idp_keys[(SAML, tenant_id)]
        response_id = f"resp_{uuid.uuid4().hex}"
        assertion_id = f"assert_{uuid.uuid4().hex}"

        issuer = issuer if issuer is not None else config.issuer
        audience = audience if audience is not None else config.entity_id
        attributes = attributes or {email_attribute: [email]}

        root = ET.Element(
            f"{{{_SAMLP_NS}}}Response",
            {
                "ID": response_id,
                "Version": "2.0",
                "IssueInstant": NOT_BEFORE,
                "Destination": config.acs_url,
            },
        )
        status = ET.SubElement(root, f"{{{_SAMLP_NS}}}Status")
        ET.SubElement(
            status, f"{{{_SAMLP_NS}}}StatusCode"
        ).set("Value", "urn:oasis:names:tc:SAML:2.0:status:Success")

        assertion = ET.SubElement(
            root,
            f"{{{_SAML_NS}}}Assertion",
            {"ID": assertion_id, "IssueInstant": NOT_BEFORE, "Version": "2.0"},
        )
        ET.SubElement(assertion, f"{{{_SAML_NS}}}Issuer").text = issuer
        subject_el = ET.SubElement(assertion, f"{{{_SAML_NS}}}Subject")
        name_id = ET.SubElement(
            subject_el,
            f"{{{_SAML_NS}}}NameID",
            {"Format": "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress"},
        )
        name_id.text = subject
        subject_conf = ET.SubElement(
            subject_el,
            f"{{{_SAML_NS}}}SubjectConfirmation",
            {"Method": "urn:oasis:names:tc:SAML:2.0:cm:bearer"},
        )
        ET.SubElement(
            subject_conf, f"{{{_SAML_NS}}}SubjectConfirmationData"
        ).set("InResponseTo", response_id)

        conditions = ET.SubElement(
            assertion,
            f"{{{_SAML_NS}}}Conditions",
            {"NotBefore": not_before, "NotOnOrAfter": not_on_or_after},
        )
        restriction = ET.SubElement(
            conditions, f"{{{_SAML_NS}}}AudienceRestriction"
        )
        ET.SubElement(restriction, f"{{{_SAML_NS}}}Audience").text = audience

        statement = ET.SubElement(
            assertion, f"{{{_SAML_NS}}}AttributeStatement"
        )
        for attr_name, values in attributes.items():
            attr = ET.SubElement(
                statement,
                f"{{{_SAML_NS}}}Attribute",
                {"Name": attr_name},
            )
            for value in values:
                ET.SubElement(attr, f"{{{_SAML_NS}}}AttributeValue").text = value

        if sign:
            self._attach_signature(assertion, keys, include_cert)
        return canonical_xml_bytes(root)

    def _attach_signature(
        self, assertion: ET.Element, keys: IdpKeys, include_cert: bool
    ) -> None:
        """Enveloped-style signature over the assertion (Signature excluded).

        Sign the canonical serialization of the assertion *without* the
        Signature element, then insert the Signature (SignatureValue + optional
        X509 cert) as the assertion's first child - matching what
        ``saml.parse_and_verify_assertion`` verifies.
        """
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        working = ET.fromstring(ET.tostring(assertion, encoding="unicode"))
        canonical = canonical_xml_bytes(working)
        signature_bytes = keys.private_key.sign(
            canonical, padding.PKCS1v15(), hashes.SHA256()
        )

        signature = ET.Element(f"{{{_DS_NS}}}Signature")
        value = ET.SubElement(signature, f"{{{_DS_NS}}}SignatureValue")
        value.text = base64.b64encode(signature_bytes).decode("ascii")
        if include_cert:
            key_info = ET.SubElement(signature, f"{{{_DS_NS}}}KeyInfo")
            x509_data = ET.SubElement(key_info, f"{{{_DS_NS}}}X509Data")
            ET.SubElement(x509_data, f"{{{_DS_NS}}}X509Certificate").text = (
                keys.cert_der_b64
            )
        assertion.insert(0, signature)

    # --- OIDC id_token builder -------------------------------------------------

    def id_token(
        self,
        tenant_id: str = "acme",
        *,
        iss: Optional[str] = None,
        aud: Optional[str] = None,
        sub: str = "google-abc123",
        email: str = "alice@acme.example.com",
        name: str = "Alice",
        nonce: Optional[str] = None,
        groups: Optional[list[str]] = None,
        iat: int = NOW,
        exp: int = NOW_PLUS_HOUR,
        alg: str = RS256_ALG,
        extra: Optional[dict[str, Any]] = None,
        key_override: Any = None,
    ) -> str:
        """Mint an OIDC id_token signed by the tenant's IdP key (RS256)."""
        config = self.store.config_for(tenant_id)
        assert config is not None
        keys = self.idp_keys[(OIDC, tenant_id)]
        claims: dict[str, Any] = {
            "iss": iss if iss is not None else config.issuer,
            "aud": aud if aud is not None else config.client_id,
            "sub": sub,
            "iat": iat,
            "exp": exp,
            "jti": f"jit_{uuid.uuid4().hex}",
        }
        if email:
            claims["email"] = email
        if name:
            claims["name"] = name
        if nonce is not None:
            claims["nonce"] = nonce
        if groups:
            claims["groups"] = groups
        if extra:
            claims.update(extra)
        key = key_override if key_override is not None else keys.private_key
        return jwt_encode(claims, alg=alg, key=key)


def new_env() -> Env:
    return Env()
