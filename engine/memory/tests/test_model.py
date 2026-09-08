"""Model vocabulary tests: scopes, profile mapping, ids, expiry math."""

from __future__ import annotations

from datetime import timedelta

import pytest

from engine.memory.model import (MemoryEntry, MemoryKind, MemoryScope,
                                container_id, memory_id_for, normalize_text,
                                scope_for, validate_container)


class TestScopeDerivation:
    def test_scope_for_presence(self):
        assert scope_for("t", None, None) is MemoryScope.TENANT
        assert scope_for("t", "a", None) is MemoryScope.AGENT
        assert scope_for("t", "a", "s") is MemoryScope.SESSION
        assert scope_for("t", None, "s") is MemoryScope.SESSION

    def test_container_validation(self):
        validate_container("t", MemoryScope.TENANT, None, None)
        validate_container("t", MemoryScope.AGENT, "a", None)
        validate_container("t", MemoryScope.SESSION, None, "s")
        with pytest.raises(ValueError):
            validate_container("", MemoryScope.TENANT, None, None)
        with pytest.raises(ValueError):
            validate_container("t", MemoryScope.AGENT, None, None)
        with pytest.raises(ValueError):
            validate_container("t", MemoryScope.SESSION, None, None)
        with pytest.raises(ValueError):
            validate_container("t", MemoryScope.TENANT, "a", None)
        with pytest.raises(ValueError):
            validate_container("t", MemoryScope.TENANT, None, "s")

    def test_container_id_encoding(self):
        assert container_id(MemoryScope.TENANT, "t", None, None) == "tenant:t"
        assert container_id(MemoryScope.AGENT, "t", "a", None) == "agent:t:a"
        assert (container_id(MemoryScope.SESSION, "t", "a", "s")
                == "session:t:a:s")


class TestProfileMapping:
    """Issue #9 froze profile ``memoryScope`` = [user, session, repository];
    this lane consumes that enum and maps it to store scopes."""

    @pytest.mark.parametrize("profile,expected", [
        ("repository", MemoryScope.TENANT),
        ("user", MemoryScope.AGENT),
        ("session", MemoryScope.SESSION),
    ])
    def test_from_profile_value(self, profile, expected):
        assert MemoryScope.from_profile_value(profile) is expected

    def test_from_profile_values_dedup(self):
        got = MemoryScope.from_profile_values(
            ["session", "repository", "user", "session"])
        assert got == (MemoryScope.SESSION, MemoryScope.TENANT,
                       MemoryScope.AGENT)

    def test_unknown_profile_value_rejected(self):
        with pytest.raises(ValueError):
            MemoryScope.from_profile_value("global")

    def test_empty_profile_allows_all(self):
        assert MemoryScope.from_profile_values([]) == tuple(MemoryScope)


class TestMemoryIds:
    def test_id_deterministic_for_same_container_key(self):
        assert (memory_id_for("t", "a", "s", "k")
                == memory_id_for("t", "a", "s", "k"))
        assert len(memory_id_for("t", "a", "s", "k")) == 20

    def test_id_does_not_collide_across_tenants(self):
        # Same agent/session/key in two tenants must produce different ids:
        # the hash binds the tenant, so cross-tenant id confusion is
        # impossible (issue AC: "no cross-tenant memory leak - hash/id").
        assert (memory_id_for("t1", "a", "s", "k")
                != memory_id_for("t2", "a", "s", "k"))

    def test_id_distinguishes_scopes(self):
        assert (memory_id_for("t", "a", "s", "k")
                != memory_id_for("t", "a", None, "k"))
        assert (memory_id_for("t", "a", None, "k")
                != memory_id_for("t", None, None, "k"))


class TestEntry:
    def test_requires_tenant(self):
        with pytest.raises(ValueError):
            MemoryEntry(tenant_id="", scope=MemoryScope.TENANT,
                        key="k", text="x")

    def test_normalizes_text_whitespace(self):
        entry = MemoryEntry(tenant_id="t", scope=MemoryScope.TENANT,
                            key="k", text="  a\n\n   b  ")
        assert entry.text == "a b"

    def test_kind_default_semantic(self):
        entry = MemoryEntry(tenant_id="t", scope=MemoryScope.TENANT,
                            key="k", text="x")
        assert entry.kind is MemoryKind.SEMANTIC

    def test_scope_must_agree_with_ids(self):
        with pytest.raises(ValueError):
            MemoryEntry(tenant_id="t", scope=MemoryScope.AGENT,
                        key="k", text="x", agent_id=None)
        with pytest.raises(ValueError):
            MemoryEntry(tenant_id="t", scope=MemoryScope.SESSION,
                        key="k", text="x", session_id=None)

    def test_ttl_expiry_math(self, at):
        created = at(2026, 1, 1, 12, 0, 0)
        entry = MemoryEntry(tenant_id="t", scope=MemoryScope.SESSION,
                            agent_id="a", session_id="s", key="k",
                            text="x", ttl_seconds=600,
                            created_at=created.isoformat())
        assert entry.is_expired(at(2026, 1, 1, 12, 9, 59)) is False
        assert entry.is_expired(at(2026, 1, 1, 12, 10, 0)) is True
        assert entry.expires_at() == created + timedelta(seconds=600)

    def test_no_ttl_never_expires(self, at):
        entry = MemoryEntry(tenant_id="t", scope=MemoryScope.TENANT,
                            key="k", text="x", created_at=at(2020).isoformat())
        assert entry.is_expired(at(2100)) is False

    def test_to_from_dict_roundtrip(self):
        entry = MemoryEntry(tenant_id="t", scope=MemoryScope.SESSION,
                            agent_id="a", session_id="s", key="k",
                            text="hello world", kind=MemoryKind.EPISODIC,
                            metadata={"x": 1}, ttl_seconds=5,
                            embedding=[0.5, -0.25])
        rebuilt = MemoryEntry.from_dict(entry.to_dict())
        assert rebuilt == entry
        assert rebuilt.embedding == [0.5, -0.25]


class TestNormalize:
    def test_collapses_and_strips(self):
        assert normalize_text("  one\n two  three  ") == "one two three"

    def test_empty(self):
        assert normalize_text("   ") == ""
