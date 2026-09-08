"""Installer tests: tenant install/upgrade path with drift detection + rollback
(issue #40). Covers the negative gates: missing/bad signature fails install,
content drift fails install, post-install drift is detected, and an upgrade
failure rolls back to the previous version.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import packhelpers  # noqa: E402

from packs import attestation  # noqa: E402
from packs.installer import (  # noqa: E402
    ContentDriftError,
    Installer,
    PackInstallError,
    PackSignatureError,
    UpgradeRollbackError,
)
from packs.pack_events import PackEventLog  # noqa: E402
from packs.registry import PackRegistry  # noqa: E402


def make_env():
    private_pem, public_pem = packhelpers.keypair()
    reg = PackRegistry(event_log=PackEventLog(),
                       schema=packhelpers.load_schema(),
                       catalog=packhelpers.load_catalog())
    root = tempfile.mkdtemp(prefix="ao40-install-")
    inst = Installer(reg, root, public_pem, event_log=reg.event_log)
    return private_pem, public_pem, reg, root, inst


def install_file(root, pack_id, version, tenant_id):
    return os.path.join(root, pack_id, version, "tool",
                        "worker-tools")


# -- signature gate (consumer trust) -----------------------------------------

def test_install_valid_signed_pack():
    private_pem, _, reg, root, inst = make_env()
    reg.publish(packhelpers.signed_doc(private_pem))
    result = inst.install("acme", "worker-platform", "1.0.0")
    assert result["version"] == "1.0.0"
    # contents materialized and verified
    path = install_file(root, "worker-platform", "1.0.0", "acme")
    assert os.path.exists(path)
    # registry consumption recorded
    assert reg.active_version("acme", "worker-platform") == "1.0.0"
    kinds = [e["event"] for e in reg.event_log.records()]
    assert "install" in kinds and "consume" in kinds


def test_install_bad_signature_fails():
    private_pem, _, reg, root, inst = make_env()
    doc = packhelpers.signed_doc(private_pem)
    att = dict(doc["attestation"])
    att["signature"] = "AAAAAAAAAAAAAAAAAAAAAAAAAAA="  # corrupted
    doc["attestation"] = att
    reg.publish(doc)
    try:
        inst.install("acme", "worker-platform", "1.0.0")
        raise AssertionError("bad signature must fail install")
    except PackSignatureError:
        pass
    assert reg.active_version("acme", "worker-platform") is None
    assert not os.path.exists(
        install_file(root, "worker-platform", "1.0.0", "acme"))


def test_install_missing_signature_fails():
    private_pem, _, reg, root, inst = make_env()
    doc = packhelpers.signed_doc(private_pem)
    reg.publish(doc)
    # the registry copy shares the dict; strip its attestation to simulate an
    # unsigned pack reaching install (consumer trust must still refuse it)
    doc.pop("attestation", None)
    assert "attestation" not in reg.get("worker-platform", "1.0.0")
    try:
        inst.install("acme", "worker-platform", "1.0.0")
        raise AssertionError("missing signature must fail install")
    except PackSignatureError:
        pass


def test_install_tampered_pack_fails():
    private_pem, _, reg, root, inst = make_env()
    doc = packhelpers.signed_doc(private_pem)
    tampered = dict(doc)
    tampered["version"] = "2.0.0"  # content changed after signing
    reg.publish(tampered)
    try:
        inst.install("acme", "worker-platform", "2.0.0")
        raise AssertionError("tampered pack must fail install")
    except PackSignatureError:
        pass


# -- lifecycle + content gates ------------------------------------------------

def test_install_refuses_paused_pack():
    private_pem, _, reg, root, inst = make_env()
    reg.publish(packhelpers.signed_doc(private_pem))
    reg.pause("worker-platform")
    try:
        inst.install("acme", "worker-platform", "1.0.0")
        raise AssertionError("paused pack must not be installable")
    except PackInstallError:
        pass


def test_install_content_drift_fails():
    private_pem, _, reg, root, inst = make_env()
    doc = packhelpers.pack_doc()
    doc["contents"]["tool"][0]["sha256"] = "0" * 64  # does not match data
    signed = attestation.sign_pack(doc, private_pem, kid="ao-pack-publisher-v1")
    reg.publish(signed)
    try:
        inst.install("acme", "worker-platform", "1.0.0")
        raise AssertionError("content hash mismatch must fail install")
    except ContentDriftError:
        pass
    assert reg.active_version("acme", "worker-platform") is None


def test_verify_installed_detects_drift():
    private_pem, _, reg, root, inst = make_env()
    reg.publish(packhelpers.signed_doc(private_pem))
    inst.install("acme", "worker-platform", "1.0.0")
    # tamper the on-disk artifact after install
    path = install_file(root, "worker-platform", "1.0.0", "acme")
    with open(path, "wb") as fh:
        fh.write(b"tampered bytes")
    try:
        inst.verify_installed("acme", "worker-platform")
        raise AssertionError("post-install drift must be detected")
    except ContentDriftError:
        pass


# -- upgrade + rollback -------------------------------------------------------

def test_upgrade_success():
    private_pem, _, reg, root, inst = make_env()
    reg.publish(packhelpers.signed_doc(private_pem, version="1.0.0"))
    inst.install("acme", "worker-platform", "1.0.0")
    reg.publish(packhelpers.signed_doc(private_pem, version="1.1.0"))
    result = inst.upgrade("acme", "worker-platform", "1.1.0")
    assert result["version"] == "1.1.0"
    assert reg.active_version("acme", "worker-platform") == "1.1.0"
    assert reg.previous_version("acme", "worker-platform") == "1.0.0"
    assert os.path.exists(install_file(root, "worker-platform", "1.1.0",
                                       "acme"))
    kinds = [e["event"] for e in reg.event_log.records()]
    assert "upgrade" in kinds
    assert kinds.count("install") == 2


def test_upgrade_failure_rolls_back():
    private_pem, _, reg, root, inst = make_env()
    reg.publish(packhelpers.signed_doc(private_pem, version="1.0.0"))
    inst.install("acme", "worker-platform", "1.0.0")

    # v1.1.0 is signed but its signature is then corrupted
    bad = packhelpers.signed_doc(private_pem, version="1.1.0")
    att = dict(bad["attestation"])
    att["signature"] = "AAAAAAAAAAAAAAAAAAAAAAAAAAA="
    bad["attestation"] = att
    reg.publish(bad)

    try:
        inst.upgrade("acme", "worker-platform", "1.1.0")
        raise AssertionError("upgrade with a bad signature must roll back")
    except UpgradeRollbackError as exc:
        assert exc.previous_version == "1.0.0"
        assert exc.reason == "signature"

    # rolled back: previous stays active, prior tree present, event appended
    assert reg.active_version("acme", "worker-platform") == "1.0.0"
    assert os.path.exists(install_file(root, "worker-platform", "1.0.0",
                                       "acme"))
    events = reg.event_log.records()
    assert events[-1]["event"] == "rollback"
    assert events[-1]["status"] == "rolled_back"


def test_direct_rollback_to_previous():
    private_pem, _, reg, root, inst = make_env()
    reg.publish(packhelpers.signed_doc(private_pem, version="1.0.0"))
    inst.install("acme", "worker-platform", "1.0.0")
    reg.publish(packhelpers.signed_doc(private_pem, version="1.1.0"))
    inst.upgrade("acme", "worker-platform", "1.1.0")
    result = inst.rollback("acme", "worker-platform", reason="manual-drift")
    assert result["to"] == "1.0.0"
    assert reg.active_version("acme", "worker-platform") == "1.0.0"
    assert os.path.exists(install_file(root, "worker-platform", "1.0.0",
                                       "acme"))


# -- #44 sync-engine seam -----------------------------------------------------

def test_sync_plan_seam():
    private_pem, _, reg, root, inst = make_env()
    plan = inst.sync_plan("acme", {"worker-platform": "1.0.0"})
    assert plan[0]["op"] == "install"
    reg.publish(packhelpers.signed_doc(private_pem, version="1.0.0"))
    inst.install("acme", "worker-platform", "1.0.0")
    plan = inst.sync_plan("acme", {"worker-platform": "1.0.0"})
    assert plan[0]["op"] == "noop"
    reg.publish(packhelpers.signed_doc(private_pem, version="1.1.0"))
    plan = inst.sync_plan("acme", {"worker-platform": "1.1.0"})
    assert plan[0]["op"] == "upgrade"
    assert plan[0]["detail"] == "1.0.0 -> 1.1.0"
