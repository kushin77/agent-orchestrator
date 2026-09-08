"""Acceptance criterion 3 — versioned distribution + per-consumer drift check.

A rendered canonical source produces a distribution manifest; each consumer
pins its installed state.  The drift detector compares the two and flags a
consumer that is behind (or ahead), on the wrong canonical id, missing a
mirror, or carrying a locally drifted mirror.
"""

from __future__ import annotations

import copy
import json
import os

import pytest

import aoi
from aoi.model import load_canonical
from aoi.versioning import check_drift, compare_semver, parse_semver

_INSTR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_EXAMPLE = os.path.join(_INSTR, "example")
_RENDERED = os.path.join(_EXAMPLE, "rendered")
_CANONICAL = os.path.join(_EXAMPLE, "canonical.yaml")
_OVERRIDE = os.path.join(_EXAMPLE, "tenant-override.json")


def _read_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


@pytest.fixture(scope="module")
def manifest():
    return _read_json(os.path.join(_RENDERED, "distribution-manifest.json"))


def _compliant_consumer(manifest, consumer="acme/example-governed-repo"):
    return {
        "schema": "ao.instructions.consumer/v1",
        "consumer": consumer,
        "canonical": copy.deepcopy(manifest["canonical"]),
        "mirrors": copy.deepcopy(manifest["mirrors"]),
    }


# --- semver helper ----------------------------------------------------------

def test_semver_ordering():
    assert parse_semver("1.0.0") == (1, 0, 0)
    assert compare_semver("1.0.0", "1.1.0") == -1
    assert compare_semver("1.1.0", "1.1.0") == 0
    assert compare_semver("1.1.1", "1.1.0") == 1
    assert compare_semver("0.9.0", "1.0.0") == -1
    with pytest.raises(Exception):
        parse_semver("one.two.three")


# --- acceptance -------------------------------------------------------------

def test_current_consumer_is_compliant(manifest):
    consumer = _compliant_consumer(manifest)
    compliant, findings = check_drift(manifest, consumer)
    assert compliant, findings
    assert findings == []


def test_committed_example_consumer_state_is_compliant(manifest):
    consumer = _read_json(os.path.join(_EXAMPLE, "consumer-state.json"))
    compliant, findings = check_drift(manifest, consumer)
    assert compliant, findings


# --- negatives --------------------------------------------------------------

def test_consumer_behind_version_is_flagged(manifest):
    consumer = _compliant_consumer(manifest)
    consumer["canonical"]["version"] = "0.9.0"  # pinned to an OLD version
    compliant, findings = check_drift(manifest, consumer)
    assert not compliant
    assert any("behind" in finding for finding in findings)


def test_consumer_ahead_of_distribution_is_flagged(manifest):
    consumer = _compliant_consumer(manifest)
    consumer["canonical"]["version"] = "9.9.9"
    compliant, findings = check_drift(manifest, consumer)
    assert not compliant
    assert any("ahead" in finding for finding in findings)


def test_consumer_on_wrong_canonical_id_is_flagged(manifest):
    consumer = _compliant_consumer(manifest)
    consumer["canonical"]["id"] = "some-other-instruction-set"
    compliant, findings = check_drift(manifest, consumer)
    assert not compliant
    assert any("canonical id" in finding for finding in findings)


def test_locally_drifted_mirror_is_flagged(manifest):
    consumer = _compliant_consumer(manifest)
    digest = consumer["mirrors"]["AGENTS.md"]
    flipped = ("0" if digest[0] != "0" else "1") + digest[1:]  # a locally hand-edited mirror
    consumer["mirrors"]["AGENTS.md"] = flipped
    compliant, findings = check_drift(manifest, consumer)
    assert not compliant
    assert any("drifted" in finding and "AGENTS.md" in finding for finding in findings)


def test_missing_mirror_is_flagged(manifest):
    consumer = _compliant_consumer(manifest)
    del consumer["mirrors"]["copilot-instructions.md"]
    compliant, findings = check_drift(manifest, consumer)
    assert not compliant
    assert any("missing mirror copilot-instructions.md" in finding for finding in findings)


def test_unknown_extra_mirror_is_flagged(manifest):
    consumer = _compliant_consumer(manifest)
    consumer["mirrors"]["EXTRA.md"] = "0" * 64
    compliant, findings = check_drift(manifest, consumer)
    assert not compliant
    assert any("unknown mirror" in finding for finding in findings)


def test_distribution_manifest_is_self_consistent(manifest):
    # Every mirror recorded in the manifest actually exists with that digest.
    for name, digest in manifest["mirrors"].items():
        path = os.path.join(_RENDERED, name)
        assert os.path.isfile(path), f"{name} recorded in the manifest is missing on disk"
        assert aoi.versioning.sha256_file(path) == digest, f"{name} digest does not match the manifest"
