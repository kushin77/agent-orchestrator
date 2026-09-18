"""governance/modules/sync live admission pin probe (issue #889)."""

from __future__ import annotations

import json

import pytest

from governance.modules.sync.live import (
    AdmissionOverclaim,
    TRACKED_TARGETS,
    UnknownAdmissionTarget,
    probe,
)


def test_probe_reports_each_tracked_target_honestly(scratch_repo_root):
    doc = probe(scratch_repo_root)
    assert set(doc["targets"]) == set(TRACKED_TARGETS)
    for module_id, record in doc["targets"].items():
        assert record["membership"] == "refused"
        # never upgraded past the honest non-membership vocabulary
        assert record["admission"] in ("requested", "independent", "undecided", None)


def test_probe_reads_the_real_committed_admission_values(scratch_repo_root):
    doc = probe(scratch_repo_root)
    assert doc["targets"]["hermes-agents"]["admission"] == "requested"
    assert doc["targets"]["ollama"]["admission"] == "requested"
    # paperclip is recorded 'independent' in the real register — the probe
    # reports it as-is, never forcing it to 'requested'.
    assert doc["targets"]["paperclip"]["admission"] == "independent"


def test_probe_reads_the_real_pin(scratch_repo_root):
    doc = probe(scratch_repo_root)
    assert doc["pin_bundle_ref"]
    assert doc["pin_delivered_at"]


def test_unknown_target_is_refused_by_name(scratch_repo_root):
    with pytest.raises(UnknownAdmissionTarget) as excinfo:
        probe(scratch_repo_root, targets=("not-a-real-target",))
    assert "not-a-real-target" in str(excinfo.value)


def test_admission_overclaim_is_refused_by_name(scratch_repo_root):
    """Negative control: an entry claiming admission='declared' (a membership
    claim, not a request) is refused by name, not silently reported."""
    manifest_path = scratch_repo_root / "module.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for entry in manifest["submodules"]:
        if entry["id"] == "hermes-agents":
            entry["admission"] = "declared"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(AdmissionOverclaim) as excinfo:
        probe(scratch_repo_root)
    assert "hermes-agents" in str(excinfo.value)
    assert "declared" in str(excinfo.value)
