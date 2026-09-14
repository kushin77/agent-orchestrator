"""The declared external-identity map: exactly one tenant, or a refusal.

The load-bearing property under test is the one ADR-0023 fixes: a client's own
user table is advisory, never authoritative. The map decides, an unmapped
identity is *refused*, an ambiguous one is refused, and our role vocabulary
always wins over the client's.
"""

from __future__ import annotations

import json

import pytest

from identity.chat.binding import (
    CHAT_ROLES,
    DEFAULT_MAP_PATH,
    ClientBinding,
    IdentityMap,
    forwarded_identity,
    normalize_external_id,
)
from identity.chat.errors import (
    AmbiguousClientIdentity,
    InvalidIdentityMap,
    UnmappedClientIdentity,
)

CLIENT = "openwebui"
EXTERNAL_ADA = "ada@acme.example"
EXTERNAL_OPS = "ops@acme.example"
EXTERNAL_GRACE = "grace@globex.example"
EXTERNAL_UNKNOWN = "mallory@evil.example"
TENANT_A = "tenant-acme"
TENANT_B = "tenant-globex"
AGENT_A = "coder"
AGENT_C = "analyst"

TRUSTED_HEADER = "X-Forwarded-Email"


def _entry(external_id: str, tenant_id: str, agent_id: str, role: str = "chat-user"):
    return {
        "client": CLIENT,
        "externalId": external_id,
        "tenantId": tenant_id,
        "agentId": agent_id,
        "role": role,
    }


def test_the_shipped_map_declares_exactly_one_tenant_per_identity(identity_map):
    for external_id, tenant_id in (
        (EXTERNAL_ADA, TENANT_A),
        (EXTERNAL_OPS, TENANT_A),
        (EXTERNAL_GRACE, TENANT_B),
    ):
        matches = identity_map.matching(client=CLIENT, external_id=external_id)
        assert len(matches) == 1, f"{external_id} must resolve to exactly one binding"
        assert matches[0].tenant_id == tenant_id


def test_a_mapped_identity_resolves_to_exactly_one_tenant(identity_map):
    binding = identity_map.resolve(client=CLIENT, external_id=EXTERNAL_GRACE)
    assert binding.tenant_id == TENANT_B
    assert binding.agent_id == AGENT_C
    assert binding.role in CHAT_ROLES


def test_an_unmapped_identity_is_refused_not_defaulted(identity_map):
    with pytest.raises(UnmappedClientIdentity) as caught:
        identity_map.resolve(client=CLIENT, external_id=EXTERNAL_UNKNOWN)
    assert caught.value.code == "unmapped_client_identity"
    # Control: the neighbouring mapped identity resolves, so the lookup is live.
    assert identity_map.resolve(client=CLIENT, external_id=EXTERNAL_ADA).tenant_id == (
        TENANT_A
    )


def test_an_empty_identity_is_refused(identity_map):
    for empty in ("", "   ", None):
        with pytest.raises(UnmappedClientIdentity):
            identity_map.resolve(client=CLIENT, external_id=empty)
    # Control: the same client with a declared identity resolves.
    assert identity_map.resolve(client=CLIENT, external_id=EXTERNAL_ADA)


def test_an_unknown_client_is_refused(identity_map):
    with pytest.raises(UnmappedClientIdentity):
        identity_map.resolve(client="some-other-product", external_id=EXTERNAL_ADA)


def test_a_duplicated_identity_is_ambiguous_and_refused():
    """Identity -> exactly one tenant: a second declaration is a refusal."""
    payload = {
        "format": 1,
        "bindings": [
            _entry(EXTERNAL_ADA, TENANT_A, AGENT_A),
            _entry(EXTERNAL_ADA, TENANT_B, AGENT_C),
            _entry(EXTERNAL_GRACE, TENANT_B, AGENT_C),
        ],
    }
    ambiguous = IdentityMap.from_payload(payload)
    with pytest.raises(AmbiguousClientIdentity) as caught:
        ambiguous.resolve(client=CLIENT, external_id=EXTERNAL_ADA)
    assert caught.value.code == "ambiguous_client_identity"
    # Control: the singly-declared identity in the same payload resolves.
    assert ambiguous.resolve(client=CLIENT, external_id=EXTERNAL_GRACE).tenant_id == (
        TENANT_B
    )


def test_a_typo_in_a_field_name_invalidates_the_map():
    """An unknown field is refused, never ignored: a dropped tenant is worse."""
    entry = _entry(EXTERNAL_ADA, TENANT_A, AGENT_A)
    entry["tenantID"] = TENANT_A
    with pytest.raises(InvalidIdentityMap):
        IdentityMap.from_payload({"format": 1, "bindings": [entry]})


def test_a_role_outside_our_vocabulary_invalidates_the_map():
    with pytest.raises(InvalidIdentityMap):
        IdentityMap.from_payload(
            {"format": 1, "bindings": [_entry(EXTERNAL_ADA, TENANT_A, AGENT_A, "root")]}
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"format": 2, "bindings": [_entry(EXTERNAL_ADA, TENANT_A, AGENT_A)]},
        {"bindings": [_entry(EXTERNAL_ADA, TENANT_A, AGENT_A)]},
        {"format": 1, "bindings": []},
        {"format": 1, "bindings": "not-a-list"},
        {"format": 1, "bindings": [_entry(EXTERNAL_ADA, "", AGENT_A)]},
        {"format": 1, "bindings": [_entry(EXTERNAL_ADA, TENANT_A, AGENT_A)], "x": 1},
        ["not", "an", "object"],
    ],
)
def test_every_malformed_map_is_refused(payload):
    with pytest.raises(InvalidIdentityMap):
        IdentityMap.from_payload(payload)
    # Control: the well-formed payload for the same identity is accepted.
    IdentityMap.from_payload({"format": 1, "bindings": [_entry(EXTERNAL_ADA, TENANT_A, AGENT_A)]})


def test_a_missing_map_file_is_refused_not_defaulted():
    with pytest.raises(InvalidIdentityMap):
        IdentityMap.load(DEFAULT_MAP_PATH.with_name("no-such-map.json"))


def test_where_the_client_role_disagrees_ours_wins(identity_map):
    """The client's own role vocabulary is recorded and discarded."""
    resolution = identity_map.resolve_request(
        client=CLIENT,
        headers={TRUSTED_HEADER: EXTERNAL_ADA},
        claimed_role="root_admin",
    )
    assert resolution.claimed_role == "root_admin"
    assert resolution.role_ignored is True
    assert resolution.role == "chat-user"
    assert resolution.tenant_id == TENANT_A


def test_a_client_claiming_our_role_is_not_credited_either(identity_map):
    """A role claim never becomes the source, whether it asks for more or the same."""
    escalated = identity_map.resolve_request(
        client=CLIENT,
        headers={TRUSTED_HEADER: EXTERNAL_ADA},
        claimed_role="chat-operator",
    )
    assert escalated.claimed_role == "chat-operator"
    assert escalated.role_ignored is True
    assert escalated.role == "chat-user"
    assert escalated.tenant_id == TENANT_A
    # The same holds when the claim agrees with the map: the map is still the
    # source, and ops@acme.example keeps the operator role it was declared with.
    agreeing = identity_map.resolve_request(
        client=CLIENT,
        headers={TRUSTED_HEADER: EXTERNAL_OPS},
        claimed_role="chat-operator",
    )
    assert agreeing.role_ignored is False
    assert agreeing.role == "chat-operator"
    assert agreeing.tenant_id == TENANT_A


def test_only_the_trusted_proxy_header_names_the_identity(identity_map):
    """A self-declared header or an authorization header is not an identity."""
    for headers in (
        {},
        {"X-Email": EXTERNAL_ADA},
        {"X-User-Email": EXTERNAL_ADA},
        {"Authorization": f"Bearer {EXTERNAL_ADA}"},
        {TRUSTED_HEADER: "   "},
    ):
        with pytest.raises(UnmappedClientIdentity):
            identity_map.resolve_request(client=CLIENT, headers=headers)
    # Control: the declared trusted header resolves.
    assert identity_map.resolve_request(
        client=CLIENT, headers={TRUSTED_HEADER: EXTERNAL_ADA}
    ).tenant_id == TENANT_A


def test_the_trusted_header_is_read_case_insensitively(identity_map):
    for name in ("X-Forwarded-Email", "x-forwarded-email", "X-FORWARDED-EMAIL"):
        assert identity_map.resolve_request(
            client=CLIENT, headers={name: EXTERNAL_GRACE}
        ).tenant_id == TENANT_B


def test_identity_normalization_is_trimmed_and_case_folded(identity_map):
    assert normalize_external_id("  Ada@ACME.Example  ") == EXTERNAL_ADA
    assert identity_map.resolve(
        client=CLIENT, external_id="  ADA@ACME.EXAMPLE "
    ).tenant_id == TENANT_A


def test_forwarded_identity_ignores_non_string_values():
    assert forwarded_identity({"X-Forwarded-Email": None}) == ""
    assert forwarded_identity({"X-Forwarded-Email": "a@b.example"}) == "a@b.example"


def test_the_declared_map_carries_no_secret_material():
    """The tracked map is data about identities - never a credential (GR-6)."""
    payload = json.loads(DEFAULT_MAP_PATH.read_text(encoding="utf-8"))
    forbidden = (
        "key",
        "secret",
        "token",
        "password",
        "credential",
        "signingKey",
        "privateKey",
        "apiKey",
    )
    for entry in payload["bindings"]:
        for field_name in entry:
            assert not any(
                word.lower() in field_name.lower() for word in forbidden
            ), f"identity map field {field_name!r} looks like secret material"
    text = DEFAULT_MAP_PATH.read_text(encoding="utf-8").lower()
    # Secret *shapes*, not the words: the map's own note explains that it holds
    # no secret material, so scanning for the bare word would flag the note.
    for shape in (
        "-----begin",
        "bearer ",
        '"signingkey"',
        '"privatekey"',
        '"apikey"',
        '"password"',
        '"secret"',
        '"token"',
    ):
        assert shape not in text, f"identity-map.json contains a secret shape {shape!r}"
