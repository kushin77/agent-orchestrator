"""Publish / retire lifecycle against a scratch registry (append-only ledger).

Covers acceptance: create/version/publish/retire persona lifecycle with
immutable published versions (sha256) - no mutation of a published snapshot.
"""

import pytest

from registry import (
    AlreadyPublishedError,
    IntegrityError,
    RetiredPersonaError,
    UnpublishedPersonaError,
)


def test_unpublished_persona_does_not_resolve(scratch):
    reg = scratch.write_registry(
        {"alpha.yaml": scratch.card_yaml(id="alpha", name="Alpha")}
    )
    assert reg.lifecycle_status("platform", "alpha") is None
    with pytest.raises(UnpublishedPersonaError):
        reg.resolve("platform", "alpha")


def test_publish_then_resolve(scratch):
    reg = scratch.write_registry(
        {"alpha.yaml": scratch.card_yaml(id="alpha", name="Alpha")}
    )
    rec = reg.publish("platform", "alpha")
    assert rec["status"] == "published"
    assert rec["version"] == "1.0.0"
    card = reg.resolve("platform", "alpha")
    assert card["id"] == "alpha"


def test_publish_is_idempotent(scratch):
    reg = scratch.write_registry(
        {"alpha.yaml": scratch.card_yaml(id="alpha", name="Alpha")}
    )
    reg.publish("platform", "alpha")
    ledger_len = len(reg.ledger())
    reg.publish("platform", "alpha")  # same snapshot -> no-op
    assert len(reg.ledger()) == ledger_len
    assert reg.lifecycle_status("platform", "alpha") == "published"


def test_editing_a_published_card_is_an_integrity_violation(scratch):
    reg = scratch.write_registry(
        {"alpha.yaml": scratch.card_yaml(id="alpha", name="Alpha")}
    )
    reg.publish("platform", "alpha")
    # Same version, content changed -> sha mismatch on resolve.
    scratch.write({"alpha.yaml": scratch.card_yaml(id="alpha", name="Alpha v2")})
    with pytest.raises(IntegrityError):
        reg.resolve("platform", "alpha")


def test_publishing_same_version_new_content_is_refused(scratch):
    reg = scratch.write_registry(
        {"alpha.yaml": scratch.card_yaml(id="alpha", name="Alpha")}
    )
    reg.publish("platform", "alpha")
    scratch.write({"alpha.yaml": scratch.card_yaml(id="alpha", name="Alpha v2")})
    with pytest.raises(AlreadyPublishedError):
        reg.publish("platform", "alpha")  # must bump the version, not edit


def test_new_version_publishes_after_edit(scratch):
    reg = scratch.write_registry(
        {"alpha.yaml": scratch.card_yaml(id="alpha", name="Alpha")}
    )
    reg.publish("platform", "alpha")
    scratch.write(
        {"alpha.yaml": scratch.card_yaml(id="alpha", version="1.1.0", name="Alpha v2")}
    )
    rec = reg.publish("platform", "alpha")
    assert rec["version"] == "1.1.0"
    card = reg.resolve("platform", "alpha")
    assert card["name"] == "Alpha v2"


def test_retire_then_resolve_is_refused(scratch):
    reg = scratch.write_registry(
        {"alpha.yaml": scratch.card_yaml(id="alpha", name="Alpha")}
    )
    reg.publish("platform", "alpha")
    rec = reg.retire("platform", "alpha")
    assert rec["status"] == "retired"
    with pytest.raises(RetiredPersonaError):
        reg.resolve("platform", "alpha")


def test_retired_version_cannot_republish_without_bump(scratch):
    reg = scratch.write_registry(
        {"alpha.yaml": scratch.card_yaml(id="alpha", name="Alpha")}
    )
    reg.publish("platform", "alpha")
    reg.retire("platform", "alpha")
    with pytest.raises(AlreadyPublishedError):
        reg.publish("platform", "alpha")
    # A new version reactivates the persona.
    scratch.write(
        {"alpha.yaml": scratch.card_yaml(id="alpha", version="1.1.0", name="Alpha v2")}
    )
    rec = reg.publish("platform", "alpha")
    assert rec["status"] == "published"
    assert reg.resolve("platform", "alpha")["id"] == "alpha"


def test_ledger_is_append_only(scratch):
    reg = scratch.write_registry(
        {"alpha.yaml": scratch.card_yaml(id="alpha", name="Alpha")}
    )
    reg.publish("platform", "alpha")
    reg.retire("platform", "alpha")
    entries = reg.ledger()
    assert [e["status"] for e in entries] == ["published", "retired"]
    assert all(e["sha256"] for e in entries)
