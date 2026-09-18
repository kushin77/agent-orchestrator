"""Standing notices and their acks (issue #1269, EPIC #1268).

The suite drives the same functions the gate drives, on a SCRATCH registry: a
fixture tree with two releases' worth of identities, one of which the gateway
carries no transport for. The live tree gets one test of its own (the derivation
is a claim about this repository), and it asserts membership rather than an exact
set, so registering another runtime does not red the suite -- that is the rule
working, not the suite breaking.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from governance.notices.notice_records import (
    ACK_FROM_UNREGISTERED,
    ACK_WITHOUT_EVIDENCE,
    EMPTY_RUNTIME_REGISTRY,
    HAND_MAINTAINED_ACK_LIST,
    NOTICE_NOT_FANNED_OUT,
    NOTICE_UNACKED,
    TRANSPORT_SURFACE_MISSING,
    NoticeError,
    acknowledge,
    ack_path,
    evaluate,
    pending_path,
    publish,
)
from governance.notices.runtime_registry import (
    RegistryUnavailable,
    registered_runtimes,
    unregistered_profiles,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
NOTICE_ID = "notice-fixture"


def build_registry(root: Path, *, lifecycle: str = "live") -> Path:
    """A scratch tree with a pack registry and a gateway catalog.

    `alpha` and `beta` are bundled by the live pack and carried by a catalog
    module, so they are RUNTIMES. `gamma` is bundled and carried by nothing, so it
    is a ROLE -- the intersection is what classifies, and this is the fixture that
    proves it. The mailbox the fan-out falls back to is part of the tree.
    """
    packs = root / "registry" / "packs" / "releases"
    packs.mkdir(parents=True)
    (packs / "fixture-pack.1.0.0.yaml").write_text(
        "schema: agent-pack/v1\n"
        "id: fixture-pack\n"
        "version: 1.0.0\n"
        "lifecycle: %s\n"
        "contents:\n"
        "  profile:\n"
        "  - ref: alpha@1.0.0\n"
        "  - ref: beta@1.0.0\n"
        "  - ref: gamma@1.0.0\n" % lifecycle,
        encoding="utf-8",
    )
    for identity in ("alpha", "beta"):
        module = root / "gateway" / "catalog" / "modules" / identity
        module.mkdir(parents=True)
        (module / "module.json").write_text(
            json.dumps(
                {
                    "id": identity,
                    "class": ["model-gateway", "provider", identity],
                    "distribution": {"package": "gateway.providers.%s" % identity},
                }
            ),
            encoding="utf-8",
        )
    mailbox = root / "fleet" / "channel.py"
    mailbox.parent.mkdir(parents=True)
    mailbox.write_text("# the mailbox: the transport of record (ADR-0011)\n", encoding="utf-8")
    return root


@pytest.fixture()
def scratch(tmp_path: Path) -> tuple[Path, Path]:
    root = build_registry(tmp_path / "tree")
    return root, tmp_path / "fleet"


def publish_fixture(root: Path, fleet: Path) -> Path:
    return publish(
        root,
        fleet,
        notice_id=NOTICE_ID,
        subject="the merge path requires green evidence at the verified head",
        body="Never merge work that fails its gate.",
        issued="2026-09-18T20:00:00Z",
        refs=("kushin77/agent-orchestrator#1269",),
    )


def findings(root: Path, fleet: Path) -> list[str]:
    return [str(finding) for finding in evaluate(root, fleet).findings]


def ack_all(root: Path, fleet: Path, runtime_ids: tuple[str, ...]) -> None:
    for runtime_id in runtime_ids:
        acknowledge(
            root,
            fleet,
            notice_id=NOTICE_ID,
            runtime_id=runtime_id,
            evidence="read the notice; the rule is understood",
            acked_at="2026-09-18T20:05:00Z",
        )


# -- the derivation -----------------------------------------------------------


def test_a_runtime_is_a_registered_identity_with_a_transport(scratch) -> None:
    root, _ = scratch
    assert [runtime.id for runtime in registered_runtimes(root)] == ["alpha", "beta"]
    assert unregistered_profiles(root) == ("gamma",)


def test_a_paused_pack_registers_nothing(scratch) -> None:
    paused = build_registry(scratch[0].parent / "paused", lifecycle="paused")
    assert registered_runtimes(paused) == ()


def test_an_unreadable_registry_is_not_an_empty_one(scratch) -> None:
    root, _ = scratch
    for release in (root / "registry" / "packs" / "releases").glob("*.yaml"):
        release.unlink()
    with pytest.raises(RegistryUnavailable):
        registered_runtimes(root)


def test_the_live_tree_registers_the_fleet_runtimes() -> None:
    ids = {runtime.id for runtime in registered_runtimes(REPO_ROOT)}
    roles = set(unregistered_profiles(REPO_ROOT))
    assert {"claude", "deepseek", "hermes", "paperclip"} <= ids
    assert "coder" in roles, "a role bundled by a live pack has no transport: it owes no ack"


# -- the record ---------------------------------------------------------------


def test_a_literal_ack_list_is_refused(scratch) -> None:
    root, fleet = scratch
    publish_fixture(root, fleet)
    record = json.loads((fleet / "notices" / NOTICE_ID / "notice.json").read_text(encoding="utf-8"))
    record["requires_ack"] = ["alpha"]
    (fleet / "notices" / NOTICE_ID / "notice.json").write_text(
        json.dumps(record), encoding="utf-8"
    )
    assert any(finding.startswith(HAND_MAINTAINED_ACK_LIST) for finding in findings(root, fleet))


def test_publish_refuses_when_nothing_is_registered(scratch) -> None:
    root = build_registry(scratch[0].parent / "empty", lifecycle="paused")
    with pytest.raises(NoticeError) as refusal:
        publish_fixture(root, scratch[1])
    assert refusal.value.code == EMPTY_RUNTIME_REGISTRY


def test_a_published_notice_is_immutable(scratch) -> None:
    root, fleet = scratch
    publish_fixture(root, fleet)
    with pytest.raises(NoticeError):
        publish_fixture(root, fleet)


def test_publish_fans_out_to_every_registered_runtime(scratch) -> None:
    root, fleet = scratch
    publish_fixture(root, fleet)
    assert pending_path(fleet, NOTICE_ID, "alpha").is_file()
    assert pending_path(fleet, NOTICE_ID, "beta").is_file()
    assert not pending_path(fleet, NOTICE_ID, "gamma").is_file()


def test_an_ack_from_an_unregistered_runtime_is_refused(scratch) -> None:
    root, fleet = scratch
    publish_fixture(root, fleet)
    with pytest.raises(NoticeError) as refusal:
        acknowledge(root, fleet, notice_id=NOTICE_ID, runtime_id="gamma",
                    evidence="understood")
    assert refusal.value.code == ACK_FROM_UNREGISTERED


def test_an_ack_without_evidence_is_refused(scratch) -> None:
    root, fleet = scratch
    publish_fixture(root, fleet)
    with pytest.raises(NoticeError) as refusal:
        acknowledge(root, fleet, notice_id=NOTICE_ID, runtime_id="alpha", evidence="   ")
    assert refusal.value.code == ACK_WITHOUT_EVIDENCE


def test_a_second_ack_is_refused(scratch) -> None:
    root, fleet = scratch
    publish_fixture(root, fleet)
    ack_all(root, fleet, ("alpha",))
    with pytest.raises(NoticeError):
        acknowledge(root, fleet, notice_id=NOTICE_ID, runtime_id="alpha",
                    evidence="understood again")


# -- the evaluator ------------------------------------------------------------


def test_a_fully_acked_notice_is_ok(scratch) -> None:
    root, fleet = scratch
    publish_fixture(root, fleet)
    ack_all(root, fleet, ("alpha", "beta"))
    report = evaluate(root, fleet)
    assert report.rc() == 0, report.lines()
    assert findings(root, fleet) == []


def test_a_silent_runtime_is_refused_by_name(scratch) -> None:
    root, fleet = scratch
    publish_fixture(root, fleet)
    ack_all(root, fleet, ("alpha", "beta"))
    ack_path(fleet, NOTICE_ID, "beta").unlink()
    assert findings(root, fleet) == ["%s:%s:beta" % (NOTICE_UNACKED, NOTICE_ID)]


def test_a_runtime_registered_after_the_notice_must_be_told(scratch) -> None:
    """The fan-out is derived too: a new registration owes a copy AND an ack."""
    root, fleet = scratch
    publish_fixture(root, fleet)
    ack_all(root, fleet, ("alpha", "beta"))
    pending_path(fleet, NOTICE_ID, "beta").unlink()
    assert findings(root, fleet) == ["%s:%s:beta" % (NOTICE_NOT_FANNED_OUT, NOTICE_ID)]


def test_an_ack_from_an_unregistered_runtime_proves_nothing(scratch) -> None:
    root, fleet = scratch
    publish_fixture(root, fleet)
    ack_all(root, fleet, ("alpha", "beta"))
    ack_path(fleet, NOTICE_ID, "gamma").write_text(
        json.dumps({
            "schema": "notice-ack/v1",
            "notice": NOTICE_ID,
            "runtime": "gamma",
            "acked_at": "2026-09-18T20:06:00Z",
            "transport": "mailbox:fleet/channel.py",
            "evidence": "read it",
        }),
        encoding="utf-8",
    )
    assert findings(root, fleet) == ["%s:%s:gamma" % (ACK_FROM_UNREGISTERED, NOTICE_ID)]


def test_a_withdrawn_notice_owes_no_ack(scratch) -> None:
    root, fleet = scratch
    publish(root, fleet, notice_id="notice-retired", subject="retired",
            body="", issued="2026-09-18T20:10:00Z", status="withdrawn")
    assert findings(root, fleet) == []
    assert evaluate(root, fleet).rc() == 0


def test_an_ack_claiming_another_runtime_is_refused_by_name(scratch) -> None:
    """The filename and the record must agree: `ack-runtime-mismatch:<notice>:<runtime>`.

    An ack that claims to come from somebody else is worse than a missing one -- it
    reads as receipt from a runtime that never answered.
    """
    root, fleet = scratch
    publish_fixture(root, fleet)
    ack_all(root, fleet, ("alpha", "beta"))
    path = ack_path(fleet, NOTICE_ID, "beta")
    record = json.loads(path.read_text(encoding="utf-8"))
    record["runtime"] = "alpha"
    path.write_text(json.dumps(record), encoding="utf-8")
    assert findings(root, fleet) == ["ack-runtime-mismatch:%s:alpha" % NOTICE_ID]


def test_a_malformed_notice_record_is_named_and_not_skipped(scratch) -> None:
    """A record the schema refuses is `malformed-record:<id>`, never silently ignored."""
    root, fleet = scratch
    publish_fixture(root, fleet)
    ack_all(root, fleet, ("alpha", "beta"))
    record = json.loads((fleet / "notices" / NOTICE_ID / "notice.json").read_text(encoding="utf-8"))
    record.pop("subject")
    (fleet / "notices" / NOTICE_ID / "notice.json").write_text(
        json.dumps(record), encoding="utf-8"
    )
    assert findings(root, fleet) == ["malformed-record:%s" % NOTICE_ID]


def test_a_missing_transport_surface_is_refused_by_name(scratch) -> None:
    """A registered runtime whose transport is gone: `transport-surface-missing:<runtime>:<path>`."""
    root, fleet = scratch
    publish_fixture(root, fleet)
    ack_all(root, fleet, ("alpha", "beta"))
    (root / "fleet" / "channel.py").unlink()
    # Both runtimes fall back to the one mailbox, so both are named: the fan-out
    # that landed in a maildir with no mailbox was never a fan-out.
    assert findings(root, fleet) == [
        "%s:alpha:fleet/channel.py" % TRANSPORT_SURFACE_MISSING,
        "%s:beta:fleet/channel.py" % TRANSPORT_SURFACE_MISSING,
    ]


def test_removing_the_registry_is_cannot_assess(scratch) -> None:
    """An input that cannot be read is rc 2 -- never an empty registry, never OK."""
    root, fleet = scratch
    publish_fixture(root, fleet)
    ack_all(root, fleet, ("alpha", "beta"))
    for release in (root / "registry" / "packs" / "releases").glob("*.yaml"):
        release.unlink()
    report = evaluate(root, fleet)
    assert report.rc() == 2
    assert report.cannot_assess
    assert report.findings == ()


def test_an_empty_registry_is_not_a_pass(scratch) -> None:
    """The vacuity guard: nothing owed must not read as everything satisfied."""
    root = build_registry(scratch[0].parent / "roles-only", lifecycle="paused")
    assert findings(root, scratch[1]) == ["%s:(none)" % EMPTY_RUNTIME_REGISTRY]
