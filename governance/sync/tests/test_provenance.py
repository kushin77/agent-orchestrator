"""Provenance manifest schema + generator tests (issue #44, criterion 1).

Covers the pinning discipline: every generated entry carries a pin, validation
flags an entry whose pin is missing (negative test), and the generator refuses
to emit an asset it cannot pin.
"""

from __future__ import annotations

import pytest

import provenance
from provenance import ProvenanceError, ProvenanceManifest, generate_manifest

COMMIT_SHA = "a" * 40  # well-formed 40-hex commit pin placeholder


def _inventory(extra=None):
    assets = [
        {"asset_id": "core-guardrails",
         "source_repo": "kushin77/CMR",
         "source_path": "shared/core-guardrails.txt",
         "version": "1.0.0",
         "sha": COMMIT_SHA,
         "local_path": "vendor/core-guardrails.txt",
         "kind": "vendored"},
        {"asset_id": "design-tokens",
         "source_repo": "kushin77/shared-frontend",
         "source_path": "shared/design-tokens.txt",
         "version": "1.4.0",
         "sha": COMMIT_SHA,
         "local_path": "vendor/design-tokens.txt",
         "kind": "vendored"},
    ]
    return {"consumer": {"id": "acme", "repo": "acme/acme"},
            "assets": assets if extra is None else extra}


class TestGenerateManifest:
    def test_generates_full_manifest_with_pins(self):
        manifest, findings = generate_manifest(
            {"id": "acme", "repo": "acme/acme"},
            _inventory()["assets"], generated="2026-09-08T00:00:00Z")
        errors = [f for f in findings if f.get("severity") == "error"]
        assert errors == []
        doc = manifest.to_dict()
        assert doc["schema"] == provenance.PROVENANCE_MANIFEST_SCHEMA
        assert doc["consumer"] == {"id": "acme", "repo": "acme/acme"}
        assert sorted(doc["assets"]) == ["core-guardrails", "design-tokens"]
        entry = doc["assets"]["core-guardrails"]
        assert entry["sha"] == COMMIT_SHA
        assert entry["sha_kind"] == "commit"
        assert entry["source_repo"] == "kushin77/CMR"
        assert entry["version"] == "1.0.0"

    def test_generator_content_pins_when_no_source_sha(self, tmp_path):
        local = tmp_path / "vendor"
        local.mkdir(parents=True)
        (local / "design-tokens.txt").write_text("design-tokens v1.4.0\n",
                                                 encoding="utf-8")
        inv = _inventory()["assets"]
        for item in inv:
            if item["asset_id"] == "design-tokens":
                item.pop("sha", None)  # no source commit pin for this asset
        manifest, findings = generate_manifest(
            {"id": "acme", "repo": "acme/acme"}, inv,
            local_root=str(tmp_path))
        errors = [f for f in findings if f.get("severity") == "error"]
        warnings = [f for f in findings if f.get("severity") == "warning"]
        assert errors == []
        entry = manifest.asset("design-tokens")
        assert entry["sha_kind"] == "content"
        assert len(entry["sha"]) == 64  # content pin is a sha256 hex digest
        assert any(w.get("code") == "content-pin" for w in warnings)

    def test_generator_refuses_unpinnable_asset(self, tmp_path):
        inv = _inventory()["assets"]
        for item in inv:
            item.pop("sha", None)  # no source sha, and no local file to hash
        with pytest.raises(ProvenanceError):
            generate_manifest({"id": "acme", "repo": "acme/acme"}, inv)

    def test_generator_rejects_duplicate_and_incomplete_assets(self):
        inv = _inventory()["assets"]
        with pytest.raises(ProvenanceError):
            generate_manifest({"id": "acme", "repo": "acme/acme"},
                              inv + [dict(inv[0])])
        incomplete = [dict(inv[0])]
        incomplete[0].pop("version")
        with pytest.raises(ProvenanceError):
            generate_manifest({"id": "acme", "repo": "acme/acme"}, incomplete)

    def test_generator_requires_consumer_id(self):
        with pytest.raises(ProvenanceError):
            generate_manifest({"repo": "acme/acme"}, [])

    def test_round_trip_save_load(self, tmp_path):
        manifest, _ = generate_manifest({"id": "acme", "repo": "acme/acme"},
                                        _inventory()["assets"])
        path = tmp_path / "manifest.json"
        manifest.save(str(path))
        loaded = ProvenanceManifest.load(str(path))
        assert loaded.to_dict() == manifest.to_dict()


class TestValidateMissingPin:
    def test_manifest_entry_missing_pin_is_flagged(self):
        """Negative test: an entry whose sha pin is stripped is flagged."""
        manifest, _ = generate_manifest({"id": "acme", "repo": "acme/acme"},
                                        _inventory()["assets"])
        # hand-edit a derived record (the shared-frontend provenance
        # anti-pattern: a derived pin that no longer agrees / went missing)
        stripped = manifest.to_dict()
        del stripped["assets"]["design-tokens"]["sha"]
        tampered = ProvenanceManifest.from_dict(stripped)
        findings, errors = provenance.validate_manifest(tampered)
        codes = [f.get("code") for f in findings]
        assert "missing-pin" in codes
        assert errors >= 1
        missing = [f for f in findings if f.get("code") == "missing-pin"]
        assert missing[0]["asset"] == "design-tokens"

    def test_empty_sha_also_flagged(self):
        manifest, _ = generate_manifest({"id": "acme", "repo": "acme/acme"},
                                        _inventory()["assets"])
        doc = manifest.to_dict()
        doc["assets"]["design-tokens"]["sha"] = ""
        findings, errors = provenance.validate_manifest(
            ProvenanceManifest.from_dict(doc))
        assert any(f.get("code") == "missing-pin" for f in findings)
        assert errors >= 1

    def test_malformed_pin_and_kind_flagged(self):
        manifest, _ = generate_manifest({"id": "acme", "repo": "acme/acme"},
                                        _inventory()["assets"])
        doc = manifest.to_dict()
        doc["assets"]["core-guardrails"]["sha_kind"] = "git"
        doc["assets"]["design-tokens"]["sha_kind"] = "content"
        doc["assets"]["design-tokens"]["sha"] = "not-hex"
        findings, errors = provenance.validate_manifest(
            ProvenanceManifest.from_dict(doc))
        codes = [f.get("code") for f in findings]
        assert "bad-sha-kind" in codes
        assert "bad-pin" in codes
        assert errors >= 2

    def test_bad_content_sha_and_unknown_kind_flagged(self):
        manifest, _ = generate_manifest({"id": "acme", "repo": "acme/acme"},
                                        _inventory()["assets"])
        doc = manifest.to_dict()
        doc["assets"]["core-guardrails"]["kind"] = "bundle"
        doc["assets"]["design-tokens"]["content_sha256"] = "zzz"
        findings, errors = provenance.validate_manifest(
            ProvenanceManifest.from_dict(doc))
        codes = [f.get("code") for f in findings]
        assert "bad-kind" in codes
        assert "bad-content-sha" in codes


class TestManifestShape:
    def test_from_dict_rejects_wrong_schema(self):
        doc = _inventory()
        doc["schema"] = "some-other/v1"
        with pytest.raises(ProvenanceError):
            ProvenanceManifest.from_dict(doc)

    def test_from_dict_requires_consumer(self):
        doc = {"schema": provenance.PROVENANCE_MANIFEST_SCHEMA,
               "assets": {}}
        with pytest.raises(ProvenanceError):
            ProvenanceManifest.from_dict(doc)

    def test_load_rejects_invalid_json(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(ProvenanceError):
            ProvenanceManifest.load(str(path))
