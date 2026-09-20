"""Live-resource leases (issue #1545): a claim on a thing that is not a file.

The file-claim tests (``test_file_leases.py``) and the claim-ledger tests
(``test_claims.py``) are deliberately NOT touched by this module's suite: they
must keep passing UNMODIFIED, which is how "the schema extension is additive,
not a breaking change" is proved rather than asserted.

Every test here drives the real module through a ledger in ``tmp_path``, so the
suite never reads (or writes) the committed board's state.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

import pytest
import resource_lease
import schema as dispatch_schema

BASE = datetime(2026, 9, 20, 12, 0, 0, tzinfo=timezone.utc)
OTHER = BASE + timedelta(seconds=1)


def _ledger(tmp_path):
    return tmp_path / "resource-claims.jsonl"


def _rows(path):
    import json

    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ── the frozen shape ────────────────────────────────────────────────────────


def test_every_record_matches_the_frozen_shape(tmp_path):
    """The ledger's records are the shape `dispatch.schema.json` declares."""
    ledger = _ledger(tmp_path)
    resource_lease.acquire("tf-state:onprem", "#1545", path=ledger, now=BASE)
    resource_lease.release("tf-state:onprem", "#1545", path=ledger, now=BASE)

    rows = _rows(ledger)
    assert [row["event"] for row in rows] == ["acquire", "release"]
    for row in rows:
        assert dispatch_schema.problems(row, dispatch_schema.SHAPE_RESOURCE_CLAIM) == ()


def test_the_frozen_shape_can_fail():
    """Negative control: a record the shape does not describe is refused by name."""
    problems = dispatch_schema.problems(
        {"event": "bogus", "resource_id": "tf-state:onprem", "holder": "#1", "at": "2026-09-20T12:00:00Z"},
        dispatch_schema.SHAPE_RESOURCE_CLAIM,
    )
    assert problems
    assert any("event" in problem for problem in problems)


def test_the_extension_is_additive(tmp_path):
    """The four shapes that existed before this issue are still declared."""
    assert set(dispatch_schema.SHAPES) >= {
        dispatch_schema.SHAPE_CLAIM_EVENT,
        dispatch_schema.SHAPE_ISSUE_ROW,
        dispatch_schema.SHAPE_QUEUE_DOCUMENT,
        dispatch_schema.SHAPE_AUDIT_RECORD,
    }
    assert dispatch_schema.SHAPE_RESOURCE_CLAIM in dispatch_schema.SHAPES


# ── who holds what ──────────────────────────────────────────────────────────


def test_a_second_holder_is_refused_by_name(tmp_path):
    """The live check: the second holder is refused, naming holder and expiry."""
    ledger = _ledger(tmp_path)
    resource_lease.acquire("tf-state:onprem", "#1545", path=ledger, now=BASE)

    with pytest.raises(resource_lease.ResourceClaimRefused) as refused:
        resource_lease.acquire("tf-state:onprem", "#1546", path=ledger, now=OTHER)

    assert refused.value.reason == resource_lease.REASON_CLAIMED
    assert "#1545" in refused.value.detail
    assert "#1546" in refused.value.detail
    assert resource_lease.expires_at(resource_lease.holder_of("tf-state:onprem", path=ledger, now=OTHER)) in refused.value.detail
    # The refusal wrote nothing: the first holder still owns it.
    assert resource_lease.holder_of("tf-state:onprem", path=ledger, now=OTHER)["holder"] == "#1545"


def test_the_same_holder_renews_rather_than_being_refused(tmp_path):
    """One holder running its own phases twice is not two owners contending."""
    ledger = _ledger(tmp_path)
    resource_lease.acquire("cloudflare-phase:remote_ssh_access", "operator:ak", path=ledger, now=BASE)
    renewed = resource_lease.acquire(
        "cloudflare-phase:remote_ssh_access", "operator:ak", path=ledger, now=OTHER
    )

    assert renewed["reason"] == "renewed by its own holder"
    assert renewed["at"] == "2026-09-20T12:00:01Z"


def test_an_unleased_resource_passes(tmp_path):
    """Negative control: an unleased resource is free, and nothing blocks on it."""
    ledger = _ledger(tmp_path)
    resource_lease.acquire("tf-state:onprem", "#1545", path=ledger, now=BASE)

    assert resource_lease.holder_of("tf-state:staging", path=ledger, now=BASE) is None
    taken = resource_lease.acquire("tf-state:staging", "#1546", path=ledger, now=BASE)
    assert taken["holder"] == "#1546"


def test_a_third_party_cannot_release_a_lease(tmp_path):
    ledger = _ledger(tmp_path)
    resource_lease.acquire("tf-state:onprem", "#1545", path=ledger, now=BASE)

    with pytest.raises(resource_lease.ResourceClaimRefused) as refused:
        resource_lease.release("tf-state:onprem", "#1546", path=ledger, now=OTHER)

    assert refused.value.reason == resource_lease.REASON_NOT_THE_HOLDER
    assert resource_lease.holder_of("tf-state:onprem", path=ledger, now=OTHER)["holder"] == "#1545"


def test_releasing_an_unleased_resource_is_a_no_op_unless_strict(tmp_path):
    ledger = _ledger(tmp_path)
    assert resource_lease.release("tf-state:onprem", "#1545", path=ledger, now=BASE) is None

    with pytest.raises(resource_lease.ResourceClaimRefused) as refused:
        resource_lease.release("tf-state:onprem", "#1545", path=ledger, now=BASE, strict=True)
    assert refused.value.reason == resource_lease.REASON_NOT_HELD


def test_a_lapsed_lease_frees_the_resource(tmp_path):
    """A holder that died mid-apply must not wedge the resource forever."""
    ledger = _ledger(tmp_path)
    resource_lease.acquire("tf-state:onprem", "#1545", path=ledger, now=BASE)
    lapsed = BASE + timedelta(seconds=resource_lease.ttl_seconds("tf-state:onprem") + 1)

    assert resource_lease.holder_of("tf-state:onprem", path=ledger, now=lapsed) is None
    taken = resource_lease.acquire("tf-state:onprem", "#1546", path=ledger, now=lapsed)
    assert taken["holder"] == "#1546"
    assert taken["reason"] == "taken over from #1545, whose lease lapsed"


# ── the TTL comes from the declared policy, per resource type ───────────────


def test_the_ttl_is_read_from_the_declared_policy():
    assert resource_lease.ttl_seconds("tf-state:onprem") == resource_lease.lease.RESOURCE_CLAIM_TTL_SECONDS["tf-state"]
    assert (
        resource_lease.ttl_seconds("cloudflare-phase:3")
        == resource_lease.lease.RESOURCE_CLAIM_TTL_SECONDS["cloudflare-phase"]
    )


def test_an_undeclared_resource_type_gets_the_declared_default():
    assert (
        resource_lease.ttl_seconds("hand-started-container:postgres")
        == resource_lease.lease.RESOURCE_CLAIM_TTL_DEFAULT_SECONDS
    )


def test_the_ttl_is_not_one_global_number():
    """An apply on shared state outlives a one-shot phase (the #1545 concern)."""
    assert resource_lease.ttl_seconds("tf-state:onprem") > resource_lease.ttl_seconds("cloudflare-phase:3")


def test_a_resource_id_without_a_type_is_refused():
    for malformed in ("onprem", "", "tf-state:", ":onprem", "   "):
        with pytest.raises(resource_lease.ResourceClaimRefused) as refused:
            resource_lease.resource_type(malformed)
        assert refused.value.reason == resource_lease.REASON_MALFORMED_RESOURCE


# ── fail closed ─────────────────────────────────────────────────────────────


def test_a_malformed_ledger_is_cannot_assess_never_free(tmp_path):
    ledger = _ledger(tmp_path)
    ledger.write_text('{"event":"acquire","resource_id":"tf-state:onprem"}\n', encoding="utf-8")

    with pytest.raises(resource_lease.ResourceLeaseUnavailable):
        resource_lease.holder_of("tf-state:onprem", path=ledger, now=BASE)


def test_a_ledger_with_invalid_json_is_cannot_assess(tmp_path):
    ledger = _ledger(tmp_path)
    ledger.write_text("not json at all\n", encoding="utf-8")

    with pytest.raises(resource_lease.ResourceLeaseUnavailable):
        resource_lease.read(ledger)


def test_an_absent_ledger_is_empty_not_an_error(tmp_path):
    ledger = _ledger(tmp_path)
    assert resource_lease.read(ledger) == []
    assert resource_lease.holder_of("tf-state:onprem", path=ledger, now=BASE) is None


# ── the guard: the claim-check wrapper ──────────────────────────────────────


def test_guard_refuses_without_running_the_command(tmp_path):
    """The property that matters: a refused guard never runs the command."""
    ledger = _ledger(tmp_path)
    marker = tmp_path / "the-command-ran"
    resource_lease.acquire("tf-state:onprem", "#1545", path=ledger, now=BASE)

    with pytest.raises(resource_lease.ResourceClaimRefused) as refused:
        resource_lease.guard(
            "tf-state:onprem",
            "#1546",
            [sys.executable, "-c", f"open({str(marker)!r}, 'w').write('ran')"],
            path=ledger,
            now=OTHER,
        )

    assert refused.value.reason == resource_lease.REASON_CLAIMED
    assert not marker.exists()
    assert resource_lease.holder_of("tf-state:onprem", path=ledger, now=OTHER)["holder"] == "#1545"


def test_guard_runs_the_command_when_free_and_releases_afterwards(tmp_path):
    ledger = _ledger(tmp_path)
    rc = resource_lease.guard(
        "tf-state:staging", "#1546", [sys.executable, "-c", "print('ran under the lease')"], path=ledger, now=BASE
    )

    assert rc == 0
    assert resource_lease.holder_of("tf-state:staging", path=ledger, now=BASE) is None
    events = [row["event"] for row in _rows(ledger)]
    assert events == ["acquire", "release"]


def test_guard_propagates_the_command_exit_code_and_still_releases(tmp_path):
    ledger = _ledger(tmp_path)
    rc = resource_lease.guard("tf-state:staging", "#1546", [sys.executable, "-c", "raise SystemExit(7)"], path=ledger, now=BASE)

    assert rc == 7
    assert resource_lease.holder_of("tf-state:staging", path=ledger, now=BASE) is None


# ── the self-control (the refusals are provably able to fire) ───────────────


def test_self_control_holds_every_arm():
    assert resource_lease.self_control() == []
