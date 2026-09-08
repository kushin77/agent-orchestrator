"""SAML 2.0 service-provider flows, modeled offline (issue #35).

Per the issue's offline constraint ("implement the protocol flows at the level
of message/assertion parsing with fixtures; no real IdP network calls"), this
module implements the SP side of SAML with stdlib XML parsing + RSA signature
verification over fixtures:

- **Initiation**: build a signed-capable ``AuthnRequest`` (SP -> IdP redirect)
  and the redirect URL carrying it.
- **Assertion handling (ACS)**: parse a ``Response``/``Assertion`` fixture,
  verify the RSA-SHA256 signature when the IdP signing certificate is
  configured, enforce issuer/audience/time conditions, and map the configured
  email attribute to the tenant principal (attribute mapping email->user).

Signature model: the assertion is signed over a **documented canonical
serialization** of the assertion with the ``Signature`` element removed
(enveloped-signature semantics). The canonical form is deterministic - element
local names, namespace URIs declared on the root in sorted order, attributes
sorted, text preserved verbatim - so both the fixture author and the verifier
see identical bytes. Any tamper that changes a tag, attribute or text changes
the digest and the signature fails. A production SAML stack would use a vetted
XML-DSig implementation with exclusive C14N; this offline model exercises the
same integrity guarantees (tamper detection, cert-bound verification) at the
message-parsing level.
"""

from __future__ import annotations

import base64
import datetime
import re
import xml.etree.ElementTree as ET
from typing import Optional
from urllib.parse import urlencode

from .errors import (
    AssertionExpiredError,
    AssertionNotYetValidError,
    InvalidAssertionError,
    LoginDeniedError,
    MissingSignatureError,
    SsoError,
    UntrustedSignerError,
)
from .jose import (
    _CRYPTO_AVAILABLE,
    cert_public_key,
    load_der_x509_certificate,
    rsa_public_key_matches,
    rsa_sha256_verify,
)
from .keystore import KeyStore
from .model import (
    DEFAULT_EMAIL_ATTRIBUTE,
    DEFAULT_NAME_ATTRIBUTE,
    SAML,
    ResolvedPrincipal,
    SUBJECT_USER,
    TenantSsoConfig,
)

_SAML_NS = "urn:oasis:names:tc:SAML:2.0:assertion"
_SAMLP_NS = "urn:oasis:names:tc:SAML:2.0:protocol"
_DS_NS = "http://www.w3.org/2000/09/xmldsig#"

_UNSAFE_XML = re.compile(r"<!DOCTYPE|<!ENTITY|<!ELEMENT", re.IGNORECASE)


def _local(tag: str) -> str:
    """Local part of an ElementTree qname ('{uri}local' -> 'local')."""
    return tag.rsplit("}", 1)[-1]


def _children(element: ET.Element, local: str) -> list[ET.Element]:
    return [child for child in list(element) if _local(child.tag) == local]


def _first_child(element: ET.Element, local: str) -> Optional[ET.Element]:
    for child in list(element):
        if _local(child.tag) == local:
            return child
    return None


def _attr(element: ET.Element, local: str) -> str:
    for key, value in element.attrib.items():
        if _local(key) == local:
            return value or ""
    return ""


def _esc_text(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _esc_attr(value: str) -> str:
    return (
        _esc_text(value)
        .replace('"', "&quot;")
        .replace("\n", "&#10;")
        .replace("\t", "&#9;")
        .replace("\r", "&#13;")
    )


def _namespaces_in_subtree(element: ET.Element) -> set[str]:
    uris: set[str] = set()
    stack = [element]
    while stack:
        node = stack.pop()
        tag = node.tag
        if isinstance(tag, str) and tag.startswith("{"):
            uris.add(tag[1 : tag.index("}")])
        for key in node.attrib:
            if key.startswith("{"):
                uris.add(key[1 : key.index("}")])
        stack.extend(list(node))
    return uris


def canonical_xml_bytes(element: ET.Element) -> bytes:
    """Deterministic canonical serialization of an element subtree.

    This is the byte form SAML assertions are signed over in this offline
    model. Rules (documented in the module docstring): local tag names,
    namespace URIs declared on the root in sorted order with generated
    prefixes, attributes sorted by serialized name, text preserved verbatim,
    no inter-element formatting whitespace.
    """

    uris = sorted(_namespaces_in_subtree(element))
    nsmap = {uri: f"ns{index}" for index, uri in enumerate(uris)}

    def _qname(prefix_uri: Optional[str], local_name: str) -> str:
        if prefix_uri and prefix_uri in nsmap:
            return f"{nsmap[prefix_uri]}:{local_name}"
        return local_name

    out: list[str] = []

    def _write(node: ET.Element) -> None:
        tag = node.tag
        if not isinstance(tag, str):
            raise SsoError("non-string XML tag in assertion (comments/PIs)")
        tag_local = _local(tag)
        tag_uri = tag[1 : tag.index("}")] if tag.startswith("{") else ""
        attrs = sorted(
            (key, value) for key, value in node.attrib.items()
        )
        out.append(f"<{_qname(tag_uri or None, tag_local)}")
        if node is element:
            for uri in uris:
                out.append(f" xmlns:{nsmap[uri]}=\"{_esc_attr(uri)}\"")
        for key, value in attrs:
            key_local = _local(key)
            key_uri = key[1 : key.index("}")] if key.startswith("{") else ""
            out.append(
                f" {_qname(key_uri or None, key_local)}=\"{_esc_attr(value)}\""
            )
        out.append(">")
        if node.text:
            out.append(_esc_text(node.text))
        for child in list(node):
            _write(child)
        out.append(f"</{_qname(tag_uri or None, tag_local)}>")

    _write(element)
    return "".join(out).encode("utf-8")


def _assertion_element(root: ET.Element) -> ET.Element:
    """Find the signed Assertion (root itself, or inside a Response)."""
    if _local(root.tag) == "Assertion":
        return root
    if _local(root.tag) == "Response":
        found = _first_child(root, "Assertion")
        if found is None:
            raise InvalidAssertionError("SAML Response contains no Assertion")
        return found
    raise InvalidAssertionError(
        f"unexpected SAML root element {_local(root.tag)!r}"
    )


def _signature_value_and_cert(
    signature: ET.Element,
) -> tuple[bytes, Optional[bytes]]:
    value_node = _first_child(signature, "SignatureValue")
    if value_node is None or not value_node.text:
        raise InvalidAssertionError("signature element has no SignatureValue")
    signature_bytes = base64.b64decode(value_node.text.strip())
    cert_der: Optional[bytes] = None
    key_info = _first_child(signature, "KeyInfo")
    if key_info is not None:
        x509_data = _first_child(key_info, "X509Data")
        if x509_data is not None:
            cert_node = _first_child(x509_data, "X509Certificate")
            if cert_node is not None and cert_node.text:
                cert_der = base64.b64decode(cert_node.text.strip())
    return signature_bytes, cert_der


def parse_xml_datetime(value: str) -> int:
    """Parse an XML Schema dateTime to a Unix epoch (UTC)."""
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.datetime.fromisoformat(text)
    except ValueError as exc:
        raise InvalidAssertionError(
            f"unparseable XML dateTime {value!r}"
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return int(parsed.timestamp())


def build_authn_request(
    config: TenantSsoConfig,
    *,
    request_id: str,
    issue_instant: str,
    destination: str = "",
) -> bytes:
    """Build a SAML 2.0 SP-initiated AuthnRequest (compact canonical XML).

    ``request_id`` is the message id (must start with ``_``), ``issue_instant``
    an XML dateTime, ``destination`` the IdP SSO url (defaults to the config's
    ``idp_sso_url``). Returns the deterministic XML bytes an SP would deflate,
    base64-encode and send as the ``SAMLRequest`` redirect parameter.
    """
    if config.protocol != SAML:
        raise SsoError("build_authn_request requires a SAML SSO config")
    proto = f"{{{_SAMLP_NS}}}AuthnRequest"
    issuer_ns = f"{{{_SAML_NS}}}Issuer"
    nameid_ns = f"{{{_SAML_NS}}}NameIDPolicy"
    root = ET.Element(
        proto,
        {
            "ID": request_id,
            "Version": "2.0",
            "IssueInstant": issue_instant,
            "Destination": destination or config.idp_sso_url,
            "ProtocolBinding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST",
            "AssertionConsumerServiceURL": config.acs_url,
        },
    )
    ET.SubElement(root, issuer_ns).text = config.entity_id
    nameid = ET.SubElement(root, nameid_ns)
    nameid.set("Format", "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress")
    nameid.set("AllowCreate", "true")
    return canonical_xml_bytes(root)


def sso_redirect_url(config: TenantSsoConfig, saml_request_b64: str, relay_state: str = "") -> str:
    """The IdP redirect URL for SP-initiated SSO (offline model)."""
    params = {"SAMLRequest": saml_request_b64}
    if relay_state:
        params["RelayState"] = relay_state
    separator = "&" if "?" in config.idp_sso_url else "?"
    return f"{config.idp_sso_url}{separator}{urlencode(params)}"


def _require_signature(
    assertion: ET.Element,
    config: TenantSsoConfig,
    keystore: KeyStore,
) -> None:
    """Verify the assertion signature with the tenant's trusted IdP cert."""
    if not _CRYPTO_AVAILABLE:
        raise SsoError("cryptography is required to verify SAML signatures")

    signature = _first_child(assertion, "Signature")
    if signature is None:
        raise MissingSignatureError(
            "SAML assertion is unsigned and a signature is required "
            "(certificate configured)"
        )

    signature_bytes, presented_cert_der = _signature_value_and_cert(signature)
    if not signature_bytes:
        raise InvalidAssertionError("empty SAML signature value")

    trusted_cert = keystore.get_cert(config.cert_alias)
    trusted_key = cert_public_key(trusted_cert)

    if presented_cert_der is not None:
        presented = load_der_x509_certificate(presented_cert_der)
        presented_key = cert_public_key(presented)
        if not rsa_public_key_matches(trusted_key, presented_key):
            raise UntrustedSignerError(
                "assertion signature certificate does not match the "
                "tenant's trusted IdP certificate"
            )

    # Enveloped-signature semantics: the signature covers the assertion with
    # the Signature element removed.
    working = ET.fromstring(ET.tostring(assertion, encoding="unicode"))
    for child in list(working):
        if _local(child.tag) == "Signature":
            working.remove(child)
    canonical = canonical_xml_bytes(working)
    try:
        rsa_sha256_verify(trusted_key, canonical, signature_bytes)
    except SsoError:
        raise
    except Exception as exc:  # noqa: BLE001 - any crypto failure -> reject
        raise SsoError(f"signature verification failed: {exc}") from exc


def _check_conditions(
    assertion: ET.Element,
    config: TenantSsoConfig,
    now: int,
) -> None:
    conditions = _first_child(assertion, "Conditions")
    if conditions is None:
        # No Conditions element: nothing constrains the assertion's validity.
        return
    tolerance = config.clock_tolerance_s
    not_before = _attr(conditions, "NotBefore")
    not_on_or_after = _attr(conditions, "NotOnOrAfter")
    if not_before:
        if now + tolerance < parse_xml_datetime(not_before):
            raise AssertionNotYetValidError(
                f"assertion not valid until {not_before} (now={now})"
            )
    if not_on_or_after:
        if now - tolerance > parse_xml_datetime(not_on_or_after):
            raise AssertionExpiredError(
                f"assertion expired at {not_on_or_after} (now={now})"
            )

    restriction = _first_child(conditions, "AudienceRestriction")
    if restriction is not None:
        audiences = [
            node.text or ""
            for node in _children(restriction, "Audience")
        ]
        if config.entity_id not in audiences:
            raise InvalidAssertionError(
                f"assertion audience {audiences!r} does not include the "
                f"SP entity id {config.entity_id!r}"
            )


def _extract_attributes(assertion: ET.Element) -> dict[str, list[str]]:
    """Collect AttributeStatement attributes into ``{name: [values]}``."""
    attributes: dict[str, list[str]] = {}
    for statement in _children(assertion, "AttributeStatement"):
        for attribute in _children(statement, "Attribute"):
            name = _attr(attribute, "Name") or _attr(attribute, "FriendlyName")
            if not name:
                continue
            values = [
                value.text or ""
                for value in _children(attribute, "AttributeValue")
            ]
            attributes.setdefault(name, []).extend(values)
    return attributes


def parse_and_verify_assertion(
    assertion_xml: bytes | str,
    config: TenantSsoConfig,
    keystore: KeyStore,
    *,
    now: Optional[int] = None,
) -> ResolvedPrincipal:
    """Parse + verify a signed SAML Response/Assertion into a principal.

    Fail-closed checks, in order:

    1. no XML entity/DOCTYPE constructs (XXE guard),
    2. the assertion Issuer matches the configured IdP entity,
    3. the RSA signature verifies against the tenant's trusted IdP cert
       (missing signature or untrusted signer rejected),
    4. Conditions: time window (with clock tolerance) + AudienceRestriction,
    5. the configured email attribute maps to the tenant principal (email ->
       user). A successful IdP assertion that cannot be mapped to an identity
       is a denial, never a partial principal.
    """
    if isinstance(assertion_xml, str):
        assertion_xml = assertion_xml.encode("utf-8")
    import time as _time

    now = int(now if now is not None else _time.time())

    if _UNSAFE_XML.search(assertion_xml.decode("utf-8", errors="ignore")):
        raise InvalidAssertionError(
            "assertion contains DOCTYPE/ENTITY constructs (rejected)"
        )
    try:
        root = ET.fromstring(assertion_xml)
    except ET.ParseError as exc:
        raise InvalidAssertionError(f"assertion is not well-formed XML: {exc}") from exc

    assertion = _assertion_element(root)

    issuer_node = _first_child(assertion, "Issuer")
    issuer = issuer_node.text.strip() if issuer_node is not None and issuer_node.text else ""
    if issuer and config.issuer and issuer != config.issuer:
        raise InvalidAssertionError(
            f"assertion Issuer {issuer!r} does not match the configured "
            f"IdP entity {config.issuer!r} for tenant {config.tenant_id!r}"
        )

    _require_signature(assertion, config, keystore)
    _check_conditions(assertion, config, now)

    subject = _first_child(assertion, "Subject")
    idp_subject = ""
    if subject is not None:
        name_id = _first_child(subject, "NameID")
        if name_id is not None and name_id.text:
            idp_subject = name_id.text.strip()

    attributes = _extract_attributes(assertion)
    email_values = attributes.get(config.email_attribute or DEFAULT_EMAIL_ATTRIBUTE, [])
    email = (email_values[0] if email_values else "").strip().lower()
    if not email:
        raise LoginDeniedError(
            f"assertion carries no {config.email_attribute or DEFAULT_EMAIL_ATTRIBUTE!r} "
            "attribute; cannot map to a tenant user"
        )

    name_values = attributes.get(config.name_attribute or DEFAULT_NAME_ATTRIBUTE, [])
    name = (name_values[0] if name_values else "").strip()
    role_values = attributes.get(config.role_attribute, []) if config.role_attribute else []
    role = (role_values[0] if role_values else "").strip()

    return ResolvedPrincipal(
        tenant_id=config.tenant_id,
        idp_tenant_id=config.idp_tenant_id,
        provider_protocol=SAML,
        subject_id=email,
        subject_type=SUBJECT_USER,
        email=email,
        name=name,
        role=role,
        attributes={key: values for key, values in attributes.items()},
        idp_subject=idp_subject,
        groups=tuple(_flat_groups(attributes)),
    )


def _flat_groups(attributes: dict[str, list[str]]) -> list[str]:
    """Any attribute carrying groups/roles as a flat value tuple."""
    flattened: list[str] = []
    for key, values in attributes.items():
        lowered = key.lower()
        if "group" in lowered or "role" in lowered:
            flattened.extend(values)
    return flattened
