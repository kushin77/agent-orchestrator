"""Versioned standards distribution + per-consumer drift check (issue #42).

Rendering a canonical source produces a **distribution manifest**
(``ao.instructions.distribution/v1``) recording the canonical id + SemVer
version and the sha256 of every generated mirror.  Each consumer pins its
installed state in a **consumer state** (``ao.instructions.consumer/v1``) —
the same shape the #41 consumer-repo template's ``MANIFEST`` and the
GLOBAL_STANDARDS ``VERSION`` pin model use, productized offline.

The drift detector compares a consumer state against the current distribution
manifest and flags: a canonical id mismatch, a consumer that is behind (or
ahead of) the manifest version, a mirror the consumer is missing, or a mirror
whose sha256 differs from the manifest (a locally hand-edited mirror).  Any
finding is a drift -> gate failure (honest, negative-testable).
"""

from __future__ import annotations

import hashlib
import json
import os
import re

DISTRIBUTION_SCHEMA = "ao.instructions.distribution/v1"
CONSUMER_SCHEMA = "ao.instructions.consumer/v1"

_SEMVER_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


class DistributionError(ValueError):
    """Raised for malformed distribution manifests / consumer states."""


def parse_semver(version: str) -> tuple[int, int, int]:
    """Parse ``major.minor.patch`` into an orderable integer triple."""
    if not isinstance(version, str) or not _SEMVER_RE.match(version):
        raise DistributionError(f"not a SemVer version: {version!r}")
    major, minor, patch = (int(part) for part in version.split("."))
    if patch > 999999:
        raise DistributionError(f"patch component out of range: {version!r}")
    return (major, minor, patch)


def compare_semver(left: str, right: str) -> int:
    """Compare two SemVer strings: -1 / 0 / 1."""
    lhs = parse_semver(left)
    rhs = parse_semver(right)
    if lhs < rhs:
        return -1
    if lhs > rhs:
        return 1
    return 0


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str) -> str:
    try:
        with open(path, "rb") as handle:
            return sha256_bytes(handle.read())
    except OSError as exc:
        raise DistributionError(f"cannot hash {path}: {exc}") from exc


def load_json(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        raise DistributionError(f"cannot read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise DistributionError(f"{path} must contain a JSON object")
    return data


def build_distribution_manifest(canonical_id: str, canonical_version: str,
                                files: dict[str, str]) -> dict:
    """Build a deterministic distribution manifest over rendered mirror bytes."""
    mirrors = {name: sha256_bytes(content.encode("utf-8")) for name, content in files.items()}
    return {
        "schema": DISTRIBUTION_SCHEMA,
        "canonical": {"id": canonical_id, "version": canonical_version},
        "mirrors": dict(sorted(mirrors.items())),
    }


def write_distribution_manifest(path: str, manifest: dict) -> None:
    _write_json(path, manifest)


def load_consumer_state(path: str) -> dict:
    state = load_json(path)
    if state.get("schema") != CONSUMER_SCHEMA:
        raise DistributionError(f"{path} is not an ao.instructions.consumer/v1 state")
    return state


def _write_json(path: str, data: dict) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")


def check_drift(manifest: dict, consumer: dict) -> tuple[bool, list[str]]:
    """Compare a consumer state to a distribution manifest.

    Returns ``(compliant, findings)``.  Any finding is a drift: an old-version
    consumer, an id mismatch, a missing mirror, or a locally drifted mirror.
    """
    findings: list[str] = []
    if manifest.get("schema") != DISTRIBUTION_SCHEMA:
        findings.append(f"distribution manifest schema is not {DISTRIBUTION_SCHEMA}")
    if consumer.get("schema") != CONSUMER_SCHEMA:
        findings.append(f"consumer state schema is not {CONSUMER_SCHEMA}")

    man_canonical = manifest.get("canonical") or {}
    con_canonical = consumer.get("canonical") or {}
    if con_canonical.get("id") != man_canonical.get("id"):
        findings.append(
            f"consumer canonical id {con_canonical.get('id')!r} != manifest "
            f"{man_canonical.get('id')!r}"
        )
    con_version = con_canonical.get("version")
    man_version = man_canonical.get("version")
    if con_version and man_version:
        try:
            order = compare_semver(con_version, man_version)
        except DistributionError as exc:
            findings.append(f"unparseable consumer version: {exc}")
            order = None
        if order is not None and order < 0:
            findings.append(f"consumer is behind the distribution: pinned {con_version} < current {man_version}")
        elif order is not None and order > 0:
            findings.append(f"consumer is ahead of the distribution: pinned {con_version} > current {man_version}")

    man_mirrors = manifest.get("mirrors") or {}
    con_mirrors = consumer.get("mirrors") or {}
    for name in sorted(man_mirrors):
        if name not in con_mirrors:
            findings.append(f"consumer is missing mirror {name}")
        elif con_mirrors[name] != man_mirrors[name]:
            findings.append(
                f"mirror {name} drifted in the consumer (sha {con_mirrors[name][:12]} "
                f"!= manifest {man_mirrors[name][:12]})"
            )
    for name in sorted(set(con_mirrors) - set(man_mirrors)):
        findings.append(f"consumer carries an unknown mirror {name} (not in the distribution)")
    return (not findings, findings)
