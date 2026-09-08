"""Store-layer scope-gate tests (AC3) — pentest-style negatives.

Cross-tenant access attempts must fail closed: a caller scoped to tenant A
can never read, require, delete or write tenant B's rows, and the
denormalized-tenant invariant is enforced at the store (never an app-layer
filter).
"""

from __future__ import annotations

import pytest

from isolation.errors import IsolationScopeError
from isolation.store import Record, TenantScopedStore


def _row(tenant_id: str, rid: str, **extra) -> dict:
    return {"tenant_id": tenant_id, "record_id": rid, **extra}


@pytest.fixture
def seeded() -> TenantScopedStore:
    store = TenantScopedStore()
    store.put("acme", _row("acme", "a1", mark="acme-only"))
    store.put("acme", _row("acme", "a2", mark="acme-2"))
    store.put("globex", _row("globex", "a1", mark="globex-a1"))
    store.put("globex", _row("globex", "g9", mark="globex-g9"))
    return store


class TestCrossTenantReadFailsClosed:
    def test_same_id_under_another_tenant_is_invisible(self, seeded):
        # acme's a1 and globex's a1 share an id; a globex-scoped read of a1
        # must return globex's row, never acme's.
        row = seeded.get("globex", "a1")
        assert row is not None
        assert row["mark"] == "globex-a1"

    def test_foreign_only_row_returns_none(self, seeded):
        # g9 exists only under globex; from acme it is simply absent.
        assert seeded.get("acme", "g9") is None
        assert not seeded.has("acme", "g9")

    def test_require_foreign_row_raises(self, seeded):
        with pytest.raises(IsolationScopeError):
            seeded.require("acme", "g9")

    def test_list_is_scoped(self, seeded):
        acme_ids = {r["record_id"] for r in seeded.list_records("acme")}
        assert acme_ids == {"a1", "a2"}
        assert "g9" not in acme_ids


class TestCrossTenantWriteFailsClosed:
    def test_put_rejects_denormalized_tenant(self):
        store = TenantScopedStore()
        with pytest.raises(IsolationScopeError):
            store.put("acme", _row("globex", "x1"))

    def test_record_shape_requires_tenant_and_id(self):
        store = TenantScopedStore()
        with pytest.raises(IsolationScopeError):
            store.put("acme", {"record_id": "x1"})  # missing tenant_id
        with pytest.raises(IsolationScopeError):
            store.put("acme", {"tenant_id": "acme"})  # missing record_id

    def test_delete_is_a_noop_on_foreign_row(self, seeded):
        assert seeded.delete("acme", "g9") is False
        assert seeded.get("globex", "g9") is not None

    def test_delete_only_removes_own_row(self, seeded):
        assert seeded.delete("globex", "a1") is True
        assert seeded.get("globex", "a1") is None
        # acme's a1 is untouched.
        assert seeded.get("acme", "a1")["mark"] == "acme-only"


class TestRecordAndSnapshots:
    def test_record_dataclass_round_trip(self):
        store = TenantScopedStore()
        record = Record(tenant_id="t1", record_id="r1", data={"k": "v"})
        store.put("t1", record)
        assert store.get("t1", "r1") == {"tenant_id": "t1",
                                         "record_id": "r1", "k": "v"}

    def test_snapshot_and_reload(self):
        store = TenantScopedStore()
        store.put("acme", _row("acme", "a1"))
        snap = store.snapshot()
        clone = TenantScopedStore(snap)
        assert clone.get("acme", "a1") is not None

    def test_get_returns_a_copy(self, seeded):
        row = seeded.get("acme", "a1")
        row["mark"] = "mutated"
        assert seeded.get("acme", "a1")["mark"] == "acme-only"


class TestStoreLayerScopeGateIsTheContract:
    """The scope gate lives at the store, not in an app-layer filter."""

    def test_no_unscoped_accessor_exists(self):
        # A TenantScopedStore has no single-id accessor that would read
        # across tenants; every public data path takes a tenant_id first.
        public = {name for name in dir(TenantScopedStore)
                  if not name.startswith("_")}
        assert {"get", "put", "delete", "require", "list_records", "has"} \
            <= public
        # no method with an unscoped signature reading by record id alone
        assert "get_by_record_id" not in public
