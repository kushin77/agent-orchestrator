#!/usr/bin/env python3
"""Registry <-> canonical CMR vocabulary parity gate (issue #145).

---knowledge---
module_id: registry.parity.parity
system: registry
app: parity
solution_class: enterprise
patterns: [drift-detection, baseline-integrity, fail-closed, two-mode-verification]
derives_from: null
owner_sme: sync-sme
tier: L1
interfaces: [compare, evaluate, evaluate_offline, verify_source, check_baseline_integrity, refresh_baseline, self_test, main]
invariants: "the registry MIRRORS the canonical CMR taxonomy; a divergence is named, never smoothed into a passing comparison"
gotchas: "the frozen canonical vocabulary is registry/parity/canonical/cmr-role-vocabulary.json, the pinned side rather than this module"
related: ["#145"]
do_not_duplicate: null
---knowledge---

The agent-orchestrator registry is a CONSUMER of the CMR role taxonomy: it
mirrors the canonical role, model-tier, worker-model and lane vocabulary so a
tenant can address any canonical role without forking a parallel taxonomy.

There are TWO modes, and they answer two different questions:

  * default (offline) -- "does the registry still mirror the FROZEN canonical
    vocabulary?"  It compares the registry's DECLARED vocabulary (the closed
    enums in ``registry/profiles/agent-profile.schema.json`` and
    ``registry/personas/persona-card.schema.json``) against the committed
    baseline ``registry/parity/canonical/cmr-role-vocabulary.json``. It is
    deterministic, needs no network and never touches ``vendor/`` -- so it can
    run in ``make verify`` on every clone, including a fresh worktree where the
    ``vendor/CMR`` submodule is unpopulated. This is the enforced mode.

  * ``--verify-source`` -- "is the freeze still current?"  It compares the frozen
    baseline against the LIVE ``vendor/CMR`` source (content sha256 + vocabulary)
    so a stale freeze is reported instead of drifting silently. It needs the
    populated submodule; when that is absent the honest outcome is
    CANNOT-ASSESS (rc 2), never a pass.

Direction (documented; the doctrine is REUSE, never fork):

  * an id the canonical set does not carry -> drift (the registry invented a
    role/tier/model/lane CMR does not have);
  * a canonical id the registry does not carry -> drift (the registry failed to
    backfill a canonical role);
  * the profile schema and the persona schema disagreeing on an axis -> NOT-OK
    (internal drift).

Equality, not subset, is the contract.

Exit-code contract (tri-state, guardrails/honesty issue #28):

  0  OK             the two vocabularies under comparison are equal
  1  NOT-OK         drift, the two registry schemas disagree, or the frozen
                    baseline was edited (its payload / sha256 integrity broke)
  2  CANNOT-ASSESS  a required source is absent/unreadable -- the frozen
                    baseline in offline mode, the ``vendor/CMR`` source in
                    ``--verify-source`` mode. An unreadable source is NEVER
                    agreement: the gate refuses to pass rather than passing
                    vacuously.

No network. stdlib + PyYAML only.

Usage:
  python3 registry/parity/parity.py                  # offline vs the frozen baseline
  python3 registry/parity/parity.py --verify-source  # frozen baseline vs vendor/CMR
  python3 registry/parity/parity.py --refresh-baseline   # re-freeze from vendor/CMR
  python3 registry/parity/parity.py --json           # machine-readable report
  python3 registry/parity/parity.py --self-test      # prove the check can fail
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import sys

try:
    import yaml
    HAS_YAML = True
except ImportError:  # pragma: no cover - guarded in main()
    HAS_YAML = False

# registry/parity/parity.py -> repo root is three directories up.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PROFILE_SCHEMA_REL = os.path.join("registry", "profiles", "agent-profile.schema.json")
PERSONA_SCHEMA_REL = os.path.join("registry", "personas", "persona-card.schema.json")
CARDS_DIR_REL = os.path.join("registry", "personas", "cards")
SEEDS_DIR_REL = os.path.join("registry", "profiles", "seeds")

CMR_ROLE_SCHEMA_REL = os.path.join(
    "onboarding", "agent-profiles", "role.schema.json")
CMR_CATALOG_DIR_REL = "catalog"

# The committed freeze of the canonical CMR vocabulary (issue #145). It is what
# the offline mode compares the registry against, and what --verify-source
# compares the live vendor/CMR source against.
DEFAULT_BASELINE_REL = os.path.join(
    "registry", "parity", "canonical", "cmr-role-vocabulary.json")

# canonical axis order (stable serialization + payload hashing)
AXIS_ORDER = ("roles", "tiers", "models", "lanes")

# Provenance stamped into the frozen baseline by --refresh-baseline.
BASELINE_VENDOR_REPO = "kushin77/CMR"
BASELINE_VENDOR_COMMIT = "b6c49aa03992dba9fe4b87b46104b8fc2f69f224"
BASELINE_NOTE = (
    "Canonical CMR agent-role vocabulary, frozen from the source schema so the "
    "default parity gate is deterministic and offline. The four axes are the "
    "canonical role/tier/worker-model/lane ids the registry must mirror exactly.")
BASELINE_FROZEN_BECAUSE = (
    "vendor/CMR is a git submodule that is UNPOPULATED in a fresh git worktree, "
    "so the parity gate cannot read the canonical source there and would return "
    "CANNOT-ASSESS (rc 2) on every clone. Freezing the vocabulary in-repo lets "
    "the default mode run in make verify anywhere; --verify-source re-checks the "
    "freeze against the live submodule when it is present.")
BASELINE_REFRESH_COMMAND = (
    "python3 registry/parity/parity.py --refresh-baseline   # run where "
    "vendor/CMR is populated")
BASELINE_VERIFY_COMMAND = "bash scripts/check-registry-parity.sh --verify-source"

# vocabulary axis -> the schema $definitions key that declares it (both schemas)
REGISTRY_AXES = {
    "roles": "roleId",
    "tiers": "modelTier",
    "models": "workerModel",
    "lanes": "canonicalLane",
}

# axis -> human label used in messages
AXIS_LABEL = {
    "roles": "role",
    "tiers": "model tier",
    "models": "worker model",
    "lanes": "canonical lane",
}

OK = 0
NOT_OK = 1
CANNOT_ASSESS = 2


class CannotAssess(Exception):
    """Raised when a required source is absent/unreadable (-> exit 2)."""


# --------------------------------------------------------------------------
# loading
# --------------------------------------------------------------------------

def load_json(path):
    """Load a JSON document; raises CannotAssess when it is absent/unreadable."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError as exc:
        raise CannotAssess("missing source: %s" % path) from exc
    except (OSError, ValueError) as exc:
        raise CannotAssess("unreadable source: %s (%s)" % (path, exc)) from exc


def sha256_file(path):
    """Return the lowercase hex sha256 of a file; CannotAssess if unreadable."""
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except OSError as exc:
        raise CannotAssess("unreadable source: %s (%s)" % (path, exc)) from exc


def _enum_of(schema, def_name):
    """Return the enum list of schema['definitions'][def_name], or None."""
    defs = schema.get("definitions") or {}
    entry = defs.get(def_name)
    if not isinstance(entry, dict):
        return None
    enum = entry.get("enum")
    return list(enum) if isinstance(enum, list) else None


# --------------------------------------------------------------------------
# registry vocabulary (what the registry DECLARES)
# --------------------------------------------------------------------------

def registry_vocab(registry_root):
    """Extract the declared vocabulary from both registry schemas.

    Returns (vocab, errors). ``vocab[axis]`` is the set the *profile* schema
    declares; ``errors`` carries any schema that does not declare an axis, and
    any axis on which the profile and persona schemas disagree (internal drift).
    """
    errors = []
    prof = load_json(os.path.join(registry_root, PROFILE_SCHEMA_REL))
    pers = load_json(os.path.join(registry_root, PERSONA_SCHEMA_REL))

    vocab = {}
    for axis, def_name in REGISTRY_AXES.items():
        a = _enum_of(prof, def_name)
        b = _enum_of(pers, def_name)
        if a is None:
            errors.append(
                "registry: profile schema declares no '%s' enum for axis %s"
                % (def_name, axis))
        if b is None:
            errors.append(
                "registry: persona schema declares no '%s' enum for axis %s"
                % (def_name, axis))
        if a is None or b is None:
            continue
        sa, sb = set(a), set(b)
        if sa != sb:
            errors.append(
                "registry: profile and persona schemas disagree on %s "
                "(profile-only=%s persona-only=%s)"
                % (AXIS_LABEL[axis],
                   ", ".join(sorted(sa - sb)) or "-",
                   ", ".join(sorted(sb - sa)) or "-"))
        vocab[axis] = sa
    return vocab, errors


# --------------------------------------------------------------------------
# canonical CMR vocabulary (what CMR PUBLISHES)
# --------------------------------------------------------------------------

def canonical_vocab(cmr_root, role_schema_rel=CMR_ROLE_SCHEMA_REL):
    """Extract the canonical vocabulary from the CMR role schema + catalog dir.

    Raises CannotAssess when the role schema or the catalog directory is
    absent, so callers cannot mistake an unpopulated submodule for agreement.
    """
    role_schema_path = os.path.join(cmr_root, role_schema_rel)
    schema = load_json(role_schema_path)
    props = schema.get("properties")
    if not isinstance(props, dict):
        raise CannotAssess("malformed CMR role schema: %s" % role_schema_path)

    catalog_dir = os.path.join(cmr_root, CMR_CATALOG_DIR_REL)
    if not os.path.isdir(catalog_dir):
        raise CannotAssess("missing canonical catalog dir: %s" % catalog_dir)

    try:
        roles = set(props["role"]["enum"])
        tiers = set(props["model"]["properties"]["tier"]["enum"])
        models = set(props["model"]["properties"]["model"]["enum"])
        lanes = set(props["ownedLanes"]["items"]["enum"])
    except (KeyError, TypeError) as exc:
        raise CannotAssess(
            "CMR role schema lacks a canonical vocabulary axis: %s" % exc) from exc
    return {"roles": roles, "tiers": tiers, "models": models, "lanes": lanes}


# --------------------------------------------------------------------------
# the frozen canonical baseline (committed; the offline mode's reference)
# --------------------------------------------------------------------------

def load_baseline(path):
    """Load the committed frozen baseline; CannotAssess if it is absent/unreadable.

    A readable-but-malformed baseline is *integrity* NOT-OK, not
    CANNOT-ASSESS -- see :func:`check_baseline_integrity`.
    """
    doc = load_json(path)
    if not isinstance(doc, dict):
        raise CannotAssess("malformed frozen baseline (not an object): %s" % path)
    return doc


def _axes_from(baseline):
    """Return {axis: set(ids)} for a baseline document, or None if malformed."""
    axes = {}
    for axis in AXIS_ORDER:
        values = baseline.get(axis)
        if not isinstance(values, list) or not values:
            return None
        if not all(isinstance(v, str) for v in values):
            return None
        axes[axis] = set(values)
    return axes


def baseline_axes(baseline):
    """Strict accessor: the baseline's four vocabulary axes as sets.

    Raises CannotAssess when an axis is missing or empty, so a structurally
    incomplete baseline cannot be treated as an empty canonical set.
    """
    axes = _axes_from(baseline)
    if axes is None:
        raise CannotAssess(
            "frozen baseline does not declare the %s vocabulary axes"
            % "/".join(AXIS_ORDER))
    return axes


def _payload_sha256(axes):
    """Stable digest over the four axes, used to detect an edited baseline.

    The canonical serialization is ``json.dumps`` of a key-sorted object whose
    values are the sorted axis ids, with compact separators -- so the digest is
    independent of the file's own key order, indentation or list order.
    """
    payload = {axis: sorted(axes[axis]) for axis in AXIS_ORDER}
    blob = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def check_baseline_integrity(baseline):
    """Return integrity errors for the frozen baseline ([] when trustworthy).

    Offline we cannot recompute the *source* sha256 (the source is absent), so
    we check that it is a well-formed digest and that ``payload_sha256`` still
    matches the axes -- i.e. nobody edited the frozen vocabulary without
    re-freezing it.
    """
    errors = []
    prov = baseline.get("_provenance")
    if not isinstance(prov, dict):
        errors.append("baseline: missing _provenance block")
        prov = {}
    digest = prov.get("sha256")
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        errors.append(
            "baseline: _provenance.sha256 %r is not a lowercase 64-hex digest"
            % (digest,))
    rel = prov.get("source_path")
    if not isinstance(rel, str) or not rel:
        errors.append("baseline: _provenance.source_path is missing")
    axes = _axes_from(baseline)
    if axes is None:
        errors.append(
            "baseline: one of the %s axes is missing, empty or non-string"
            % "/".join(AXIS_ORDER))
        return errors
    recorded = prov.get("payload_sha256")
    actual = _payload_sha256(axes)
    if recorded != actual:
        errors.append(
            "baseline: payload_sha256 mismatch (recorded %s, computed %s) -- the "
            "frozen vocabulary was edited without re-freezing"
            % (recorded, actual))
    return errors


def refresh_baseline(cmr_root, extracted=None):
    """Build a frozen-baseline document from the LIVE canonical source.

    Raises CannotAssess when the live source is absent -- there is nothing to
    freeze in a fresh worktree, which is exactly why the baseline is committed.
    """
    live = canonical_vocab(cmr_root)
    digest = sha256_file(os.path.join(cmr_root, CMR_ROLE_SCHEMA_REL))
    doc = {
        "_provenance": {
            "vendor_repo": BASELINE_VENDOR_REPO,
            "source_path": CMR_ROLE_SCHEMA_REL.replace(os.sep, "/"),
            "vendor_commit": BASELINE_VENDOR_COMMIT,
            "sha256": digest,
            "payload_sha256": _payload_sha256(live),
            "extracted": extracted or datetime.date.today().isoformat(),
            "note": BASELINE_NOTE,
            "frozen_because": BASELINE_FROZEN_BECAUSE,
            "refresh_command": BASELINE_REFRESH_COMMAND,
            "verify_command": BASELINE_VERIFY_COMMAND,
        },
    }
    for axis in AXIS_ORDER:
        doc[axis] = sorted(live[axis])
    return doc


# --------------------------------------------------------------------------
# committed-asset membership (the registry's USE of the vocabulary)
# --------------------------------------------------------------------------

def _load_yaml_docs(directory):
    """Yield (path, parsed) for every .yaml file directly under ``directory``."""
    if not HAS_YAML:
        raise CannotAssess("PyYAML unavailable; cannot scan %s" % directory)
    if not os.path.isdir(directory):
        return
    for name in sorted(os.listdir(directory)):
        if not name.endswith((".yaml", ".yml")):
            continue
        path = os.path.join(directory, name)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                yield path, yaml.safe_load(fh)
        except (OSError, yaml.YAMLError) as exc:
            raise CannotAssess("unreadable committed asset %s (%s)" % (path, exc))


def membership_errors(registry_root, canonical):
    """Every committed card/seed that declares a ``role``/``canonicalLanes``
    value must use an id from the canonical set (a registry asset may not
    reference a role or lane CMR does not publish)."""
    errors = []
    cards = os.path.join(registry_root, CARDS_DIR_REL)
    seeds = os.path.join(registry_root, SEEDS_DIR_REL)
    for directory in (cards, seeds):
        for path, doc in _load_yaml_docs(directory):
            if not isinstance(doc, dict):
                continue
            rel = os.path.relpath(path, registry_root)
            role = doc.get("role")
            if role is not None and role not in canonical["roles"]:
                errors.append(
                    "drift: %s declares role %r absent from the canonical CMR "
                    "role set" % (rel, role))
            for lane in doc.get("canonicalLanes") or []:
                if lane not in canonical["lanes"]:
                    errors.append(
                        "drift: %s declares canonical lane %r absent from the "
                        "canonical CMR lane set" % (rel, lane))
    return errors


# --------------------------------------------------------------------------
# comparison
# --------------------------------------------------------------------------

def compare(registry, canonical):
    """Return drift errors for every axis whose two sets differ."""
    errors = []
    for axis in ("roles", "tiers", "models", "lanes"):
        reg = registry.get(axis)
        can = canonical.get(axis)
        if reg is None or can is None:
            continue
        only_registry = reg - can
        only_canonical = can - reg
        if only_registry:
            errors.append(
                "drift: registry %s(s) absent from canonical CMR: %s"
                % (AXIS_LABEL[axis], ", ".join(sorted(only_registry))))
        if only_canonical:
            errors.append(
                "drift: canonical CMR %s(s) missing from the registry: %s"
                % (AXIS_LABEL[axis], ", ".join(sorted(only_canonical))))
    return errors


def evaluate(registry_root, cmr_root):
    """Compare the registry against an arbitrary LIVE canonical root.

    This is the direct registry-vs-source comparison (used by the tests, which
    point it at hermetic fixtures, and available when you have a populated
    source). The two supported gate modes are :func:`evaluate_offline` (default)
    and :func:`verify_source` (``--verify-source``).

    Returns (status, report) where status is 0/1/2 and report is a list of
    human-readable lines.
    """
    try:
        registry, reg_errors = registry_vocab(registry_root)
        canonical = canonical_vocab(cmr_root)
    except CannotAssess as exc:
        return CANNOT_ASSESS, [
            "registry-parity: CANNOT-ASSESS — %s" % exc,
            "registry-parity: refusing to pass on an unreadable canonical source",
        ]

    errors = list(reg_errors)
    errors.extend(compare(registry, canonical))
    errors.extend(membership_errors(registry_root, canonical))

    if errors:
        report = ["registry-parity: NOT-OK — registry/canonical drift detected"]
        report.extend("  " + e for e in errors)
        return NOT_OK, report

    report = [
        "registry-parity: OK — registry vocabulary matches canonical CMR "
        "(%d roles, %d tiers, %d models, %d lanes)"
        % (len(canonical["roles"]), len(canonical["tiers"]),
           len(canonical["models"]), len(canonical["lanes"])),
    ]
    return OK, report


# --------------------------------------------------------------------------
# mode 1 (default): registry  <->  frozen baseline   (offline, deterministic)
# --------------------------------------------------------------------------

def evaluate_offline(registry_root, baseline_path):
    """Compare the registry vocabulary against the FROZEN canonical baseline.

    No network, no ``vendor/`` dependency. Returns (status, report) with status
    0 (OK), 1 (NOT-OK: drift or a tampered baseline) or 2 (CANNOT-ASSESS: the
    frozen baseline is absent/unreadable).
    """
    try:
        baseline = load_baseline(baseline_path)
    except CannotAssess as exc:
        return CANNOT_ASSESS, [
            "registry-parity: CANNOT-ASSESS — %s" % exc,
            "registry-parity: refusing to pass without the frozen canonical baseline",
        ]

    integrity = check_baseline_integrity(baseline)

    try:
        registry, reg_errors = registry_vocab(registry_root)
    except CannotAssess as exc:
        return CANNOT_ASSESS, [
            "registry-parity: CANNOT-ASSESS — %s" % exc,
            "registry-parity: refusing to pass on an unreadable registry schema",
        ]

    if integrity:
        report = ["registry-parity: NOT-OK — the frozen baseline failed its integrity check"]
        report.extend("  " + e for e in integrity)
        return NOT_OK, report

    axes = baseline_axes(baseline)
    errors = list(reg_errors)
    errors.extend(compare(registry, axes))
    errors.extend(membership_errors(registry_root, axes))

    if errors:
        report = ["registry-parity: NOT-OK — registry/frozen-baseline drift detected"]
        report.extend("  " + e for e in errors)
        return NOT_OK, report

    report = [
        "registry-parity: OK — registry vocabulary matches the frozen canonical "
        "baseline (%d roles, %d tiers, %d models, %d lanes)"
        % (len(axes["roles"]), len(axes["tiers"]),
           len(axes["models"]), len(axes["lanes"])),
    ]
    return OK, report


# --------------------------------------------------------------------------
# mode 2 (--verify-source): frozen baseline  <->  live vendor/CMR source
# --------------------------------------------------------------------------

def verify_source(baseline_path, cmr_root):
    """Check the freeze is current against the LIVE canonical source.

    Returns (status, report):
      0  OK             the live source matches the freeze (sha256 + vocabulary)
      1  NOT-OK         the live source has drifted from the freeze -- refresh it
      2  CANNOT-ASSESS  the live source (or the frozen baseline) is unavailable
    """
    try:
        baseline = load_baseline(baseline_path)
        axes = baseline_axes(baseline)
    except CannotAssess as exc:
        return CANNOT_ASSESS, [
            "registry-parity: CANNOT-ASSESS — %s" % exc,
            "registry-parity: refusing to verify the freeze without the frozen baseline",
        ]

    integrity = check_baseline_integrity(baseline)
    if integrity:
        report = ["registry-parity: NOT-OK — the frozen baseline failed its integrity check"]
        report.extend("  " + e for e in integrity)
        return NOT_OK, report

    prov = baseline["_provenance"]
    rel = prov.get("source_path") or CMR_ROLE_SCHEMA_REL
    try:
        live = canonical_vocab(cmr_root, rel)
        actual = sha256_file(os.path.join(cmr_root, rel))
    except CannotAssess as exc:
        return CANNOT_ASSESS, [
            "registry-parity: CANNOT-ASSESS — %s" % exc,
            "registry-parity: the live canonical source is unavailable (for "
            "example the vendor/CMR submodule is unpopulated here); refusing "
            "to pass on an unreadable source",
        ]

    errors = []
    if actual != prov["sha256"]:
        errors.append(
            "stale freeze: %s sha256 is %s but the frozen baseline records %s — "
            "refresh the baseline and re-commit it"
            % (rel, actual, prov["sha256"]))
    errors.extend(compare(axes, live))

    if errors:
        report = ["registry-parity: NOT-OK — the live canonical source has drifted from the freeze"]
        report.extend("  " + e for e in errors)
        return NOT_OK, report

    report = [
        "registry-parity: OK — the freeze is current: %s sha256=%s matches the "
        "frozen baseline" % (rel, actual),
    ]
    return OK, report


def refresh_baseline_file(baseline_path, cmr_root, extracted=None):
    """(Re)write the frozen baseline from the live source. Returns (status, lines)."""
    try:
        doc = refresh_baseline(cmr_root, extracted=extracted)
    except CannotAssess as exc:
        return CANNOT_ASSESS, [
            "registry-parity: CANNOT-ASSESS — cannot refresh the frozen baseline: %s" % exc,
            "registry-parity: run --refresh-baseline where vendor/CMR is populated",
        ]
    try:
        with open(baseline_path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2)
            fh.write("\n")
    except OSError as exc:
        return CANNOT_ASSESS, [
            "registry-parity: CANNOT-ASSESS — cannot write the frozen baseline %s (%s)"
            % (baseline_path, exc),
        ]
    return OK, [
        "registry-parity: refreshed the frozen baseline %s from %s "
        "(sha256=%s, payload_sha256=%s)"
        % (baseline_path, doc["_provenance"]["source_path"],
           doc["_provenance"]["sha256"], doc["_provenance"]["payload_sha256"]),
    ]


# --------------------------------------------------------------------------
# self-test (prove the check is not vacuous)
# --------------------------------------------------------------------------

def self_test(registry_root, cmr_root, baseline_path=None):
    """Prove every refusal path fires, in memory / on copies. Returns (ok, lines).

    The drift mutations (A/B) are in-memory comparisons so the self-test needs
    no canonical source; the CANNOT-ASSESS mutations (C/D) point at absent
    paths; the integrity mutation (E) edits a copy of the committed baseline.
    """
    if baseline_path is None:
        baseline_path = os.path.join(REPO_ROOT, DEFAULT_BASELINE_REL)
    lines = []
    try:
        registry, reg_errors = registry_vocab(registry_root)
    except CannotAssess as exc:
        return CANNOT_ASSESS, ["registry-parity self-test: CANNOT-ASSESS — %s" % exc]

    if reg_errors:
        return NOT_OK, ["registry-parity self-test: registry is already non-conforming: "
                        + "; ".join(reg_errors)]

    ok = True
    reference = {k: set(v) for k, v in registry.items()}

    # Mutation A: a registry role the canonical set does not carry.
    mutant = {k: set(v) for k, v in registry.items()}
    mutant["roles"].add("registry-invented-role")
    a = compare(mutant, reference)
    caught_a = any("absent from canonical CMR" in e for e in a)
    lines.append("  mutation A (registry-only role)       caught=%s" % caught_a)
    ok = ok and caught_a

    # Mutation B: a canonical role the registry never backfilled.
    mutant_ref = {k: set(v) for k, v in reference.items()}
    mutant_ref["roles"].add("cmr-new-role")
    b = compare(registry, mutant_ref)
    caught_b = any("missing from the registry" in e for e in b)
    lines.append("  mutation B (canonical-only role)      caught=%s" % caught_b)
    ok = ok and caught_b

    # Mutation C: the frozen baseline hidden -> CANNOT-ASSESS, never OK.
    hidden_baseline = os.path.join(
        os.path.dirname(baseline_path), "does-not-exist-baseline.json")
    c_status, _ = evaluate_offline(registry_root, hidden_baseline)
    caught_c = c_status == CANNOT_ASSESS
    lines.append("  mutation C (frozen baseline hidden)   status=%d (want 2)" % c_status)
    ok = ok and caught_c

    # Mutation D: the live vendor source hidden -> CANNOT-ASSESS, never OK.
    d_status, _ = verify_source(
        baseline_path, os.path.join(cmr_root, "does-not-exist"))
    caught_d = d_status == CANNOT_ASSESS
    lines.append("  mutation D (vendor source hidden)     status=%d (want 2)" % d_status)
    ok = ok and caught_d

    # Mutation E: the frozen baseline's vocabulary edited -> integrity NOT-OK.
    try:
        baseline = load_baseline(baseline_path)
        tampered = json.loads(json.dumps(baseline))
        tampered["roles"] = list(tampered.get("roles") or []) + ["tamper-role"]
        caught_e = bool(check_baseline_integrity(tampered))
    except CannotAssess:
        caught_e = False
    lines.append("  mutation E (baseline payload edited)  caught=%s" % caught_e)
    ok = ok and caught_e

    lines.insert(0, "registry-parity self-test: %s" % ("OK" if ok else "NOT-OK"))
    return (OK if ok else NOT_OK), lines


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv):
    parser = argparse.ArgumentParser(
        prog="parity.py", description="registry <-> canonical CMR vocabulary parity gate")
    parser.add_argument("--registry-root", default=REPO_ROOT,
                        help="registry checkout root (default: this repo)")
    parser.add_argument("--baseline",
                        default=os.path.join(REPO_ROOT, DEFAULT_BASELINE_REL),
                        help="frozen canonical baseline JSON (default: the committed one)")
    parser.add_argument("--cmr-root", default=os.path.join(REPO_ROOT, "vendor", "CMR"),
                        help="live canonical CMR root, used by --verify-source / "
                             "--refresh-baseline (default: <repo>/vendor/CMR)")
    parser.add_argument("--verify-source", action="store_true",
                        help="compare the frozen baseline against the live vendor/CMR source")
    parser.add_argument("--refresh-baseline", action="store_true",
                        help="(re)write the frozen baseline from the live source")
    parser.add_argument("--date", default=None,
                        help="extraction date for --refresh-baseline (default: today)")
    parser.add_argument("--json", action="store_true",
                        help="emit a machine-readable JSON report")
    parser.add_argument("--self-test", action="store_true",
                        help="prove the drift + cannot-assess paths fire")
    args = parser.parse_args(argv)

    if args.refresh_baseline:
        status, lines = refresh_baseline_file(args.baseline, args.cmr_root, args.date)
    elif args.verify_source:
        status, lines = verify_source(args.baseline, args.cmr_root)
    elif not HAS_YAML:
        payload = {"status": CANNOT_ASSESS, "result": "CANNOT-ASSESS",
                   "reason": "PyYAML unavailable"}
        print(json.dumps(payload) if args.json else
              "registry-parity: CANNOT-ASSESS — PyYAML unavailable")
        return CANNOT_ASSESS
    elif args.self_test:
        status, lines = self_test(args.registry_root, args.cmr_root, args.baseline)
    else:
        status, lines = evaluate_offline(args.registry_root, args.baseline)

    result = {OK: "OK", NOT_OK: "NOT-OK", CANNOT_ASSESS: "CANNOT-ASSESS"}[status]
    if args.json:
        print(json.dumps({"status": status, "result": result, "lines": lines},
                         indent=2))
    else:
        for line in lines:
            print(line)
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
