"""Shared builders for the governance/sync tests (issue #44).

Not a conftest (mirrors ``registry/packs/tests/packhelpers.py``). Builds a
small offline ecosystem — canonical shared assets, consumer provenance
manifests with real content pins, and a dependency catalog — that the drift,
blast-radius and sync-plan tests all drive.
"""

from __future__ import annotations

import hashlib
import os

from provenance import ProvenanceManifest

# --- canonical shared assets: asset_id -> (canonical rel path, content) ------
CANON = {
    "core-guardrails": ("shared/core-guardrails.txt", "core-guardrails v1.0.0\n"),
    "pack-sync-engine": ("shared/pack-sync-engine.txt",
                         "pack-sync-engine v2.1.0\n"),
    "portal-shell": ("shared/portal-shell.txt", "portal-shell v3.0.0\n"),
    "design-tokens": ("shared/design-tokens.txt", "design-tokens v1.4.0\n"),
}

# canonical versions per asset (semver the manifests pin)
VERSIONS = {
    "core-guardrails": "1.0.0",
    "pack-sync-engine": "2.1.0",
    "portal-shell": "3.0.0",
    "design-tokens": "1.4.0",
}

# --- dependency catalog (forward: asset -> its dependencies) ----------------
CATALOG = {
    "core-guardrails": {"dependencies": []},
    "pack-sync-engine": {"dependencies": ["core-guardrails"]},
    "portal-shell": {"dependencies": ["core-guardrails", "pack-sync-engine"]},
    "design-tokens": {"dependencies": []},
}

# --- consumers: which shared assets each consumer vendors -------------------
CONSUMERS = {
    "acme": ["core-guardrails", "pack-sync-engine", "design-tokens"],
    "globex": ["core-guardrails", "pack-sync-engine"],
    "nexus": ["core-guardrails", "portal-shell"],
    "zeta": ["core-guardrails"],
    # rogue vendors ONLY an unrelated asset: it is NOT in the dependency
    # graph of any shared asset and must never be reported as affected.
    "rogue": ["unrelated-asset"],
}

UNRELATED = ("vendor/unrelated-asset.txt", "unrelated v0.1.0\n",
             "kushin77/other-repo")


def _sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path):
    with open(path, "rb") as fh:
        return _sha256_bytes(fh.read())


def write(root, rel, content):
    path = os.path.join(str(root), rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    return path


def build_canonical(canonical_root):
    """Materialize the canonical shared assets under ``canonical_root``."""
    for asset, (rel, content) in CANON.items():
        write(canonical_root, rel, content)
    return canonical_root


def _entry(asset_id, local_file, source_repo="kushin77/CMR"):
    """A manifest entry with a real content pin for the local file."""
    digest = _sha256_file(local_file)
    rel = CANON[asset_id][0]
    return {
        "source_repo": source_repo,
        "source_path": rel,
        "version": VERSIONS[asset_id],
        "sha": digest,            # content pin (sha256 of canonical bytes)
        "sha_kind": "content",
        "content_sha256": digest,  # offline drift comparison target
        "local_path": rel,
        "kind": "vendored",
    }


def build_consumer(base, consumer_id, assets, *, source_repo="kushin77/CMR"):
    """Materialize one consumer's local tree + return its manifest."""
    local_root = os.path.join(str(base), "locals", consumer_id)
    entries = {}
    for asset in assets:
        if asset in CANON:
            rel, content = CANON[asset]
            local_file = write(local_root, rel, content)
            entries[asset] = _entry(asset, local_file, source_repo)
        else:
            rel, content, repo = UNRELATED
            local_file = write(local_root, rel, content)
            digest = _sha256_file(local_file)
            entries[asset] = {
                "source_repo": repo,
                "source_path": rel,
                "version": "0.1.0",
                "sha": digest,
                "sha_kind": "content",
                "content_sha256": digest,
                "local_path": rel,
                "kind": "vendored",
            }
    manifest = ProvenanceManifest(
        consumer={"id": consumer_id, "repo": "%s/%s" % (consumer_id,
                                                        consumer_id)},
        assets=entries, generated="2026-09-08T00:00:00Z")
    return local_root, manifest


def build_ecosystem(base):
    """Full offline ecosystem: canonical root + every consumer manifest."""
    canonical_root = os.path.join(str(base), "canonical")
    build_canonical(canonical_root)
    consumers = {}
    for consumer_id, assets in CONSUMERS.items():
        consumers[consumer_id] = build_consumer(base, consumer_id, assets)
    return canonical_root, consumers


def save_manifests(consumers, out_dir):
    """Persist each consumer's manifest as JSON under ``out_dir``."""
    os.makedirs(str(out_dir), exist_ok=True)
    paths = []
    for consumer_id, (_local, manifest) in sorted(consumers.items()):
        path = os.path.join(str(out_dir), "%s.json" % consumer_id)
        manifest.save(path)
        paths.append(path)
    return paths
