"""Canonical instruction-source model (issue #42).

The canonical instruction source is the single source of truth for a governed
instruction set.  It is structured (YAML or JSON), SemVer-versioned, and
validated before any mirror is rendered: per-tool mirrors are *generated* from
it and never hand-forked.

Contract (mirrored by ``schema/canonical.schema.json``)::

    schema: ao.instructions.canonical/v1
    id: <string>                      # canonical instruction-set id
    version: "<major>.<minor>.<patch>"
    title: <string>
    summary: <string>
    layers:                           # ordered, most governing first
      - id: <string>                  # unique layer id
        label: <string>
        managed: <bool>               # True = platform-governed (frozen to tenants)
        rules:
          - id: <string>              # unique rule id across the whole set
            text: <string>            # the behaviour statement (model-agnostic)

Layers are listed in descending governance order: a rule in a later layer never
overrides a rule in an earlier layer.  ``managed: true`` layers are governed
and may not be redefined by a tenant override; only the local (tenant-owned)
layer may be extended, through the frozen override contract.
"""

from __future__ import annotations

import json
import os
import re

import yaml

from .versioning import parse_semver

CANONICAL_SCHEMA = "ao.instructions.canonical/v1"

_SEMVER_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


class CanonicalError(ValueError):
    """Raised when a canonical instruction source violates its contract."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise CanonicalError(message)


def _load_text(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except OSError as exc:
        raise CanonicalError(f"cannot read canonical source {path}: {exc}") from exc


def parse_canonical(text: str, fmt: str) -> dict:
    """Parse + validate canonical-source text in ``fmt`` (``yaml`` or ``json``)."""
    try:
        if fmt == "json":
            data = json.loads(text)
        else:
            data = yaml.safe_load(text)
    except (ValueError, yaml.YAMLError) as exc:
        raise CanonicalError(f"unparseable canonical source ({fmt}): {exc}") from exc
    return validate_canonical(data)


def load_canonical(path: str) -> dict:
    """Load + validate a canonical instruction source from a file (YAML/JSON)."""
    if not os.path.isfile(path):
        raise CanonicalError(f"canonical source not found: {path}")
    text = _load_text(path)
    ext = os.path.splitext(path)[1].lower()
    fmt = "json" if ext == ".json" else "yaml"
    return parse_canonical(text, fmt)


def validate_canonical(canonical: dict) -> dict:
    """Validate a canonical-source document; return it unchanged on success."""
    _require(isinstance(canonical, dict), "canonical source must be a mapping")
    _require(canonical.get("schema") == CANONICAL_SCHEMA, "schema must be ao.instructions.canonical/v1")
    canonical_id = canonical.get("id")
    _require(isinstance(canonical_id, str) and canonical_id, "id must be a non-empty string")
    version = canonical.get("version")
    _require(isinstance(version, str) and _SEMVER_RE.match(version), "version must be SemVer major.minor.patch")
    parse_semver(version)  # range sanity
    _require(isinstance(canonical.get("title"), str) and canonical["title"], "title must be a non-empty string")
    _require(isinstance(canonical.get("summary"), str), "summary must be a string")

    layers = canonical.get("layers")
    _require(isinstance(layers, list) and layers, "layers must be a non-empty list")
    layer_ids: set[str] = set()
    rule_ids: set[str] = set()
    for layer in layers:
        _require(isinstance(layer, dict), "each layer must be a mapping")
        layer_id = layer.get("id")
        _require(isinstance(layer_id, str) and _ID_RE.match(layer_id), f"layer id invalid: {layer_id!r}")
        _require(layer_id not in layer_ids, f"duplicate layer id: {layer_id}")
        layer_ids.add(layer_id)
        _require(isinstance(layer.get("label"), str) and layer["label"], f"layer {layer_id} label missing")
        _require(isinstance(layer.get("managed"), bool), f"layer {layer_id} managed must be a boolean")
        rules = layer.get("rules")
        _require(isinstance(rules, list) and rules, f"layer {layer_id} rules must be a non-empty list")
        for rule in rules:
            _require(isinstance(rule, dict), f"layer {layer_id} has a non-mapping rule")
            rule_id = rule.get("id")
            _require(isinstance(rule_id, str) and _ID_RE.match(rule_id), f"rule id invalid: {rule_id!r}")
            _require(rule_id not in rule_ids, f"duplicate rule id across layers: {rule_id}")
            rule_ids.add(rule_id)
            _require(isinstance(rule.get("text"), str) and rule["text"], f"rule {rule_id} text must be a non-empty string")
    return canonical


def layer_ids(canonical: dict) -> list[str]:
    """Ordered governed layer ids (declaration order = descending governance)."""
    return [layer["id"] for layer in canonical["layers"]]


def governed_rule_ids(canonical: dict) -> set[str]:
    """Ids of every rule in a managed (platform-governed) layer."""
    return {rule["id"] for layer in canonical["layers"] if layer["managed"] for rule in layer["rules"]}


def iter_rules(canonical: dict):
    """Yield ``(layer, rule)`` for every rule in declaration order."""
    for layer in canonical["layers"]:
        for rule in layer["rules"]:
            yield layer, rule


def rule_ids(canonical: dict) -> list[str]:
    """Every rule id in declaration order (the canonical semantics order)."""
    return [rule["id"] for _, rule in iter_rules(canonical)]


def canonical_version(canonical: dict) -> str:
    return canonical["version"]
