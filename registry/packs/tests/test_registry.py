"""PackRegistry tests: publish (schema-validated), lifecycle state machine,
catalog search/crossref, and consumption tracking (issue #40).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import packhelpers  # noqa: E402

from packs.pack_events import PackEventLog  # noqa: E402
from packs.registry import (  # noqa: E402
    PackAlreadyExistsError,
    PackLifecycleError,
    PackNotFoundError,
    PackPublishError,
    PackRegistry,
)


def make_registry():
    return PackRegistry(event_log=PackEventLog(), schema=packhelpers.load_schema(),
                        catalog=packhelpers.load_catalog())


# -- publish: schema-validated (invalid fails publish) -----------------------

def test_publish_valid_pack_succeeds():
    reg = make_registry()
    assert reg.publish(packhelpers.pack_doc()) == "live"
    assert reg.is_installable("worker-platform")
    events = [e["event"] for e in reg.event_log.records()]
    assert "publish" in events


def test_publish_invalid_pack_fails():
    reg = make_registry()
    bad = packhelpers.pack_doc(category="portal")  # not a closed category
    try:
        reg.publish(bad)
        raise AssertionError("invalid pack must fail publish")
    except PackPublishError as exc:
        assert "refused" in str(exc)
    # not published
    assert reg.state("worker-platform") is None


def test_publish_unsigned_pack_fails():
    reg = make_registry()
    try:
        reg.publish(packhelpers.pack_doc(with_attestation=False))
        raise AssertionError("unsigned pack must fail publish")
    except PackPublishError as exc:
        assert "signature" in str(exc)


def test_publish_duplicate_version_is_immutable():
    reg = make_registry()
    reg.publish(packhelpers.pack_doc())
    try:
        reg.publish(packhelpers.pack_doc())
        raise AssertionError("duplicate publish must be refused")
    except PackAlreadyExistsError:
        pass


def test_publish_version_requires_semver():
    reg = make_registry()
    try:
        reg.publish(packhelpers.pack_doc(version="1.0"))
        raise AssertionError("non-semver version must fail publish")
    except PackPublishError:
        pass


# -- lifecycle state machine -------------------------------------------------

def test_lifecycle_transitions_and_events():
    reg = make_registry()
    reg.publish(packhelpers.pack_doc())
    assert reg.state("worker-platform") == "live"

    assert reg.pause("worker-platform") == "paused"
    assert reg.state("worker-platform") == "paused"
    assert reg.resume("worker-platform") == "live"
    assert reg.retire("worker-platform") == "retired"
    assert reg.state("worker-platform") == "retired"

    kinds = [e["event"] for e in reg.event_log.records()]
    assert kinds == ["publish", "pause", "resume", "retire"]


def test_retired_is_terminal():
    reg = make_registry()
    reg.publish(packhelpers.pack_doc())
    reg.retire("worker-platform")
    for target in ("live", "paused"):
        try:
            reg.transition("worker-platform", target)
            raise AssertionError("terminal retired pack must not transition")
        except PackLifecycleError:
            pass


def test_illegal_transition_rejected():
    reg = make_registry()
    reg.publish(packhelpers.pack_doc())
    try:
        reg.transition("worker-platform", "planned")  # live -> planned illegal
        raise AssertionError("live -> planned must be illegal")
    except PackLifecycleError:
        pass


def test_pause_blocks_installability():
    reg = make_registry()
    reg.publish(packhelpers.pack_doc())
    assert reg.is_installable("worker-platform") is True
    reg.pause("worker-platform")
    assert reg.is_installable("worker-platform") is False


# -- catalog search ----------------------------------------------------------

def test_search_by_category_and_text():
    reg = make_registry()
    reg.publish(packhelpers.pack_doc(pack_id="worker-platform",
                                     category="coding",
                                     name="Worker platform"))
    reg.publish(packhelpers.pack_doc(pack_id="data-ops", category="data",
                                     name="Data operations",
                                     description="analytics and reporting"))
    coding = reg.search(category="coding")
    assert [r["id"] for r in coding] == ["worker-platform"]
    hits = reg.search(text="analytics")
    assert [r["id"] for r in hits] == ["data-ops"]
    all_live = reg.live_packs()
    assert sorted(r["id"] for r in all_live) == ["data-ops", "worker-platform"]


def test_search_excludes_non_live():
    reg = make_registry()
    reg.publish(packhelpers.pack_doc(pack_id="worker-platform"))
    reg.pause("worker-platform")
    assert reg.search() == []


# -- catalog crossref --------------------------------------------------------

def test_crossref_dependencies_and_dependents():
    reg = make_registry()
    reg.publish(packhelpers.pack_doc(
        pack_id="base-ops", category="coding"))
    reg.publish(packhelpers.pack_doc(
        pack_id="worker-platform", category="coding",
        deps=[{"id": "base-ops", "version": "1.0.0"}]))
    xr = reg.crossref("base-ops")
    assert [d["id"] for d in xr["dependents"]] == ["worker-platform"]
    xr2 = reg.crossref("worker-platform")
    assert [d["id"] for d in xr2["dependencies"]] == ["base-ops"]


def test_crossref_unknown_pack():
    reg = make_registry()
    try:
        reg.crossref("nope")
        raise AssertionError("unknown crossref must raise")
    except PackNotFoundError:
        pass


# -- consumption tracking ----------------------------------------------------

def test_consumption_tracking():
    reg = make_registry()
    reg.publish(packhelpers.pack_doc(pack_id="worker-platform"))
    reg.record_install("acme", "worker-platform", "1.0.0")
    rows = reg.consumption("worker-platform")
    assert rows == [{"tenantId": "acme", "pack": "worker-platform",
                     "version": "1.0.0"}]
    assert reg.active_version("acme", "worker-platform") == "1.0.0"
    assert reg.previous_version("acme", "worker-platform") is None
    assert len(reg.consumption_by_tenant("acme")) == 1
    assert reg.consumption_by_tenant("globex") == []


def test_consumption_tracks_upgrade_history():
    reg = make_registry()
    reg.publish(packhelpers.pack_doc(pack_id="worker-platform", version="1.0.0"))
    reg.publish(packhelpers.pack_doc(pack_id="worker-platform", version="1.1.0"))
    reg.record_install("acme", "worker-platform", "1.0.0")
    reg.record_install("acme", "worker-platform", "1.1.0")
    assert reg.active_version("acme", "worker-platform") == "1.1.0"
    assert reg.previous_version("acme", "worker-platform") == "1.0.0"
    by_tenant = reg.consumption_by_tenant("acme")
    assert [r["version"] for r in by_tenant] == ["1.0.0", "1.1.0"]


def test_record_install_refuses_non_live():
    reg = make_registry()
    reg.publish(packhelpers.pack_doc(pack_id="worker-platform"))
    reg.retire("worker-platform")
    try:
        reg.record_install("acme", "worker-platform", "1.0.0")
        raise AssertionError("install of retired pack must be refused")
    except PackLifecycleError:
        pass
