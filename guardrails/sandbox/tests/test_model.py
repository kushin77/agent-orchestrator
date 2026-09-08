"""Security-profile model tests: wire (de)serialisation + fail-closed shape."""

from __future__ import annotations

import pytest

from sandbox.errors import SandboxConfigError
from sandbox.model import (
    DEFAULT_PROFILE,
    NETWORK_BRIDGE,
    NETWORK_HOST,
    NETWORK_MODES,
    NETWORK_NONE,
    PROFILE_NAMES,
    TOOL_CATEGORIES,
    ExecutionRequest,
    SecurityProfile,
)

RESTRICTED_DOC = {
    "readOnlyRootfs": True,
    "noNewPrivs": True,
    "capDrop": ["ALL"],
    "networkMode": NETWORK_NONE,
    "user": "1000:1000",
    "workDir": "/workspace",
    "cpuQuota": 50000,
    "memoryMb": 512,
    "pidsLimit": 100,
    "timeoutSeconds": 30,
}


def test_profile_round_trip_preserves_wire_keys():
    profile = SecurityProfile.from_doc("restricted", RESTRICTED_DOC)
    assert profile.to_doc() == RESTRICTED_DOC
    assert profile.name == "restricted"
    assert profile.cap_drop == ("ALL",)
    assert profile.network_mode == NETWORK_NONE


def test_profile_from_doc_rejects_unknown_network_mode():
    bad = dict(RESTRICTED_DOC, networkMode="internet")
    with pytest.raises(SandboxConfigError):
        SecurityProfile.from_doc("restricted", bad)


def test_profile_from_doc_rejects_unknown_field():
    bad = dict(RESTRICTED_DOC, privileged_escape=True)
    with pytest.raises(SandboxConfigError, match="unknown field"):
        SecurityProfile.from_doc("restricted", bad)


def test_profile_from_doc_rejects_missing_required_field():
    bad = {key: value for key, value in RESTRICTED_DOC.items() if key != "cpuQuota"}
    with pytest.raises(SandboxConfigError, match="missing required"):
        SecurityProfile.from_doc("restricted", bad)


def test_profile_from_doc_rejects_non_positive_resources():
    for field, value in (
        ("memoryMb", 0),
        ("pidsLimit", 0),
        ("timeoutSeconds", 0),
    ):
        bad = dict(RESTRICTED_DOC, **{field: value})
        with pytest.raises(SandboxConfigError, match="must be positive"):
            SecurityProfile.from_doc("restricted", bad)


def test_profile_from_doc_rejects_non_mapping():
    with pytest.raises(SandboxConfigError):
        SecurityProfile.from_doc("restricted", ["not", "a", "doc"])


def test_cap_semantics():
    profile = SecurityProfile.from_doc("restricted", RESTRICTED_DOC)
    assert profile.drops_all_caps()
    assert profile.drops_cap("SYS_ADMIN")
    assert profile.drops_cap("NET_ADMIN")

    privileged = SecurityProfile.from_doc(
        "privileged",
        {
            "readOnlyRootfs": False,
            "noNewPrivs": True,
            "capDrop": ["SYS_ADMIN", "SYS_PTRACE"],
            "networkMode": NETWORK_HOST,
            "user": "root",
            "workDir": "/workspace",
            "cpuQuota": 200000,
            "memoryMb": 2048,
            "pidsLimit": 500,
            "timeoutSeconds": 120,
        },
    )
    assert not privileged.drops_all_caps()
    assert privileged.drops_cap("SYS_ADMIN")
    assert not privileged.drops_cap("NET_ADMIN")


def test_closed_vocabularies():
    assert PROFILE_NAMES == ("restricted", "standard", "privileged")
    assert NETWORK_MODES == (NETWORK_NONE, NETWORK_BRIDGE, NETWORK_HOST)
    assert DEFAULT_PROFILE == "restricted"
    assert TOOL_CATEGORIES == (
        "file",
        "exec",
        "network",
        "db",
        "cloud",
        "git",
        "docker",
        "k8s",
    )


def test_execution_request_identity_copy_carries_who():
    request = ExecutionRequest(category="exec", tool="exec.sh", operation="shell")
    with_identity = request.with_identity(
        tenant_id="acme", agent_id="agent-a", actor="subject-1"
    )
    assert with_identity.tenant_id == "acme"
    assert with_identity.agent_id == "agent-a"
    assert with_identity.actor == "subject-1"
    # the original is immutable and unchanged
    assert request.tenant_id is None
    assert request.args == {}
    assert request.requires_caps == ()
