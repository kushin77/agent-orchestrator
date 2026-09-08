#!/usr/bin/env python3
"""Provenance manifest schema + generator (issue #44, acceptance criterion 1).

A provenance manifest records, per consuming repository, every shared asset it
consumes/vendors and the canonical source each asset came from:

    source repo (owner/name) + source path + version + sha pin + local path

This is the record the drift engine (``drift.py``) compares against and the
blast-radius engine (``blast_radius.py``) reads to find affected consumers, so
an entry that ships without its pin would silently disable drift detection for
that asset. ``validate_manifest`` therefore flags an entry missing its pin
(``missing-pin``) and refuses to treat it as complete; ``generate_manifest``
never emits an unpinned asset.

Adapted (not copied) from `kushin77/CMR` `sync/vendor-manifest.schema.json`
(consumers -> pins vocabulary, schema-identity + harvested_from discipline)
and `kushin77/shared-frontend` `docs/PROVENANCE.md` (a single authoritative
pin per shared asset; derived records MUST agree and are checked, never edited
by hand). Sources recorded in ``PROVENANCE.md`` (GR-10).
"""

from __future__ import annotations

import hashlib
import json
import os

from model import (
    ASSET_KINDS,
    ASSET_REQUIRED_FIELDS,
    PROVENANCE_MANIFEST_SCHEMA,
    SHA_KINDS,
)

HEX64 = set("0123456789abcdef")


class ProvenanceError(ValueError):
    """A manifest is structurally invalid (schema mismatch, bad shape)."""


class Finding(dict):
    """One validator finding: code + severity + the asset it refers to."""


def _is_hex(text, length):
    if not isinstance(text, str) or len(text) != length:
        return False
    return all(c in HEX64 for c in text.lower())


def _hex_sha256_file(path, chunk=65536):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _validate_entry(asset_id, entry):
    """Return the list of findings for one asset entry (never raises)."""
    findings = []
    if not isinstance(entry, dict):
        return [Finding(code="malformed-entry", severity="error",
                        asset=asset_id,
                        message="asset entry is not a mapping")]
    for field in ASSET_REQUIRED_FIELDS:
        value = entry.get(field)
        if value is None or (isinstance(value, str) and not value.strip()):
            if field == "sha":
                findings.append(Finding(
                    code="missing-pin", severity="error", asset=asset_id,
                    message=("asset '%s' has no sha pin; a manifest entry "
                             "missing its pin cannot be drift-checked"
                             % asset_id)))
            else:
                findings.append(Finding(
                    code="missing-field", severity="error", asset=asset_id,
                    message="asset '%s' is missing required field '%s'"
                            % (asset_id, field)))
    sha = entry.get("sha")
    if isinstance(sha, str) and sha.strip():
        sha_kind = entry.get("sha_kind") or "commit"
        if sha_kind not in SHA_KINDS:
            findings.append(Finding(
                code="bad-sha-kind", severity="error", asset=asset_id,
                message=("asset '%s' has unknown sha_kind '%s'"
                         % (asset_id, sha_kind))))
        elif sha_kind == "content" and not _is_hex(sha, 64):
            findings.append(Finding(
                code="bad-pin", severity="error", asset=asset_id,
                message=("asset '%s' content pin must be a 64-char hex "
                         "sha256, got '%s'" % (asset_id, sha))))
        elif sha_kind == "commit" and not (
                _is_hex(sha, 40) or _is_hex(sha, 64)):
            findings.append(Finding(
                code="bad-pin", severity="error", asset=asset_id,
                message=("asset '%s' commit pin must be a 40/64-char hex "
                         "sha, got '%s'" % (asset_id, sha))))
    content_sha = entry.get("content_sha256")
    if content_sha is not None and not _is_hex(content_sha, 64):
        findings.append(Finding(
            code="bad-content-sha", severity="error", asset=asset_id,
            message=("asset '%s' content_sha256 must be a 64-char hex sha256"
                     % asset_id)))
    kind = entry.get("kind") or "vendored"
    if kind not in ASSET_KINDS:
        findings.append(Finding(
            code="bad-kind", severity="error", asset=asset_id,
            message=("asset '%s' has unknown kind '%s' (closed vocabulary %s)"
                     % (asset_id, kind, sorted(ASSET_KINDS)))))
    source_repo = entry.get("source_repo")
    if isinstance(source_repo, str) and "/" not in source_repo:
        findings.append(Finding(
            code="bad-source-repo", severity="error", asset=asset_id,
            message=("asset '%s' source_repo '%s' is not owner/repo form"
                     % (asset_id, source_repo))))
    return findings


class ProvenanceManifest:
    """One consumer's provenance manifest (schema ``ao.sync/provenance-manifest-v1``)."""

    def __init__(self, consumer, assets, generated=None, raw=None):
        self.consumer = dict(consumer)
        self.assets = dict(assets)  # asset_id -> entry dict
        self.generated = generated
        self._raw = raw

    # -- construction / persistence -----------------------------------------
    @classmethod
    def from_dict(cls, doc):
        if not isinstance(doc, dict):
            raise ProvenanceError("manifest must be a JSON/YAML object")
        if doc.get("schema") != PROVENANCE_MANIFEST_SCHEMA:
            raise ProvenanceError(
                "manifest schema must be '%s' (got '%s')"
                % (PROVENANCE_MANIFEST_SCHEMA, doc.get("schema")))
        consumer = doc.get("consumer")
        if not isinstance(consumer, dict) or not consumer.get("id"):
            raise ProvenanceError("manifest consumer.id is required")
        assets = doc.get("assets")
        if not isinstance(assets, dict):
            raise ProvenanceError("manifest assets must be a mapping")
        return cls(consumer=consumer, assets=assets,
                   generated=doc.get("generated"), raw=doc)

    @classmethod
    def load(cls, path):
        with open(path, "r", encoding="utf-8") as fh:
            try:
                doc = json.load(fh)
            except ValueError as exc:
                raise ProvenanceError("manifest %s is not valid JSON: %s"
                                      % (path, exc))
        return cls.from_dict(doc)

    def to_dict(self):
        return {
            "schema": PROVENANCE_MANIFEST_SCHEMA,
            "generated": self.generated,
            "consumer": self.consumer,
            "assets": self.assets,
        }

    def save(self, path):
        payload = json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n"
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(payload)

    # -- accessors -----------------------------------------------------------
    def asset(self, asset_id):
        return self.assets.get(asset_id)

    def consumer_id(self):
        return self.consumer.get("id")

    def validate(self):
        """Return findings across the whole manifest (never raises)."""
        findings = []
        if not isinstance(self.assets, dict):
            return [Finding(code="malformed-assets", severity="error",
                            asset="*",
                            message="assets must be a mapping")]
        for asset_id, entry in sorted(self.assets.items()):
            findings.extend(_validate_entry(asset_id, entry))
        return findings


def validate_manifest(manifest):
    """Validate a ``ProvenanceManifest``; return ``(findings, error_count)``."""
    findings = manifest.validate()
    errors = sum(1 for f in findings if f.get("severity") == "error")
    return findings, errors


def generate_manifest(consumer, inventory, local_root=None, generated=None):
    """Generate a provenance manifest from a declarative asset inventory.

    ``consumer`` = {"id": ..., "repo": ...}; ``inventory`` = list of asset
    inputs, each with ``asset_id``/``source_repo``/``source_path``/``version``/
    ``local_path`` and optional ``sha`` (source commit pin), ``sha_kind``,
    ``kind`` and ``note``.

    Pinning discipline (an unpinned asset is never emitted):

    - When ``sha`` is supplied it is recorded as the pin (``sha_kind``
      defaults to ``commit``).
    - When ``sha`` is omitted and ``local_root`` resolves the local file, the
      generator records a **content pin** (the local file's sha256) and adds an
      advisory warning that the source commit was not recorded.
    - When neither is available the generator raises ``ProvenanceError`` for
      that asset — no unpinned entry is written.

    ``content_sha256`` is always computed from the local file when it exists,
    giving the drift engine an offline canonical digest to compare against.
    """
    if not isinstance(consumer, dict) or not consumer.get("id"):
        raise ProvenanceError("generator requires consumer.id")
    assets = {}
    warnings = []
    for item in inventory:
        asset_id = item.get("asset_id")
        if not asset_id:
            raise ProvenanceError("inventory item is missing asset_id")
        if asset_id in assets:
            raise ProvenanceError("duplicate asset_id '%s'" % asset_id)
        missing = [f for f in ("source_repo", "source_path", "version",
                               "local_path") if not item.get(f)]
        if missing:
            raise ProvenanceError(
                "asset '%s' inventory is missing: %s"
                % (asset_id, ", ".join(missing)))
        entry = {
            "source_repo": item["source_repo"],
            "source_path": item["source_path"],
            "version": item["version"],
            "local_path": item["local_path"],
            "kind": item.get("kind") or "vendored",
        }
        if item.get("note"):
            entry["note"] = item["note"]
        local_file = None
        if local_root:
            candidate = os.path.join(local_root, item["local_path"])
            if os.path.isfile(candidate):
                local_file = candidate
        sha = item.get("sha")
        if sha:
            entry["sha"] = sha
            entry["sha_kind"] = item.get("sha_kind") or "commit"
        elif local_file is not None:
            digest = _hex_sha256_file(local_file)
            entry["sha"] = digest
            entry["sha_kind"] = "content"
            warnings.append(Finding(
                code="content-pin", severity="warning", asset=asset_id,
                message=("asset '%s' had no source commit pin; recorded a "
                         "content pin (sha256 of the local file) instead"
                         % asset_id)))
        else:
            raise ProvenanceError(
                "asset '%s' cannot be pinned: no source sha and no local file "
                "to hash under local_root" % asset_id)
        if local_file is not None:
            entry["content_sha256"] = _hex_sha256_file(local_file)
        assets[asset_id] = entry
    manifest = ProvenanceManifest(consumer=consumer, assets=assets,
                                  generated=generated)
    findings, _errors = validate_manifest(manifest)
    return manifest, warnings + findings


def content_sha256_of(path, chunk=65536):
    """Public digest helper (used by the drift engine and materializer)."""
    return _hex_sha256_file(path, chunk=chunk)
