#!/usr/bin/env python3
"""Registry <-> canonical CMR catalog parity gate (issue #145).

The agent-orchestrator registry is a CONSUMER of the CMR role taxonomy: it
mirrors the canonical role, model-tier, worker-model and lane vocabulary so a
tenant can address any canonical role without forking a parallel taxonomy.
This gate fails when the registry's DECLARED vocabulary (the closed enums
embedded in ``registry/profiles/agent-profile.schema.json`` and
``registry/personas/persona-card.schema.json``) drifts from the canonical CMR
catalog (``vendor/CMR/onboarding/agent-profiles/role.schema.json`` plus the
presence of ``vendor/CMR/catalog/``).

Direction (documented; the doctrine is REUSE, never fork):

  * a registry id the canonical set does not carry  -> drift (the registry
    invented a role/tier/model/lane CMR does not have);
  * a canonical id the registry does not carry      -> drift (the registry
    failed to backfill a canonical role);
  * the profile schema and the persona schema         disagreeing on an axis
                                                      -> NOT-OK (internal drift).

Equality, not subset, is the contract.

Exit-code contract (tri-state, guardrails/honesty issue #28):

  0  OK             registry vocabulary == canonical CMR vocabulary
  1  NOT-OK         drift, or the two registry schemas disagree
  2  CANNOT-ASSESS  the canonical source is absent/unreadable (for example the
                    ``vendor/CMR`` submodule is unpopulated in a fresh git
                    worktree). An unreadable source is NEVER agreement: the
                    gate refuses to pass rather than passing vacuously.

No network. stdlib + PyYAML only.

Usage:
  python3 registry/parity/parity.py                 # gate against vendor/CMR
  python3 registry/parity/parity.py --cmr-root DIR  # gate against another root
  python3 registry/parity/parity.py --json          # machine-readable report
  python3 registry/parity/parity.py --self-test     # prove the check can fail
"""

from __future__ import annotations

import argparse
import json
import os
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

def canonical_vocab(cmr_root):
    """Extract the canonical vocabulary from the CMR role schema + catalog dir.

    Raises CannotAssess when the role schema or the catalog directory is
    absent, so callers cannot mistake an unpopulated submodule for agreement.
    """
    role_schema_path = os.path.join(cmr_root, CMR_ROLE_SCHEMA_REL)
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
    """Run the full parity check.

    Returns (status, report) where status is 0/1/2 and report is a list of
    human-readable lines. Raises CannotAssess only for unexpected conditions
    (caught here and mapped to status 2).
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
# self-test (prove the check is not vacuous)
# --------------------------------------------------------------------------

def self_test(registry_root, cmr_root):
    """Mutate aligned in-memory vocabularies and prove both drift directions
    are detected. Returns (ok, lines)."""
    lines = []
    try:
        registry, reg_errors = registry_vocab(registry_root)
        canonical = canonical_vocab(cmr_root)
    except CannotAssess as exc:
        return CANNOT_ASSESS, ["registry-parity self-test: CANNOT-ASSESS — %s" % exc]

    if reg_errors:
        return NOT_OK, ["registry-parity self-test: registry is already non-conforming: "
                        + "; ".join(reg_errors)]

    ok = True

    # Mutation A: a registry role CMR does not publish.
    mutant = {k: set(v) for k, v in registry.items()}
    mutant["roles"].add("registry-invented-role")
    a = compare(mutant, canonical)
    caught_a = any("absent from canonical CMR" in e for e in a)
    lines.append("  mutation A (registry-only role)      caught=%s" % caught_a)
    ok = ok and caught_a

    # Mutation B: CMR publishes a role the registry never backfilled.
    mutant_c = {k: set(v) for k, v in canonical.items()}
    mutant_c["roles"].add("cmr-new-role")
    b = compare(registry, mutant_c)
    caught_b = any("missing from the registry" in e for e in b)
    lines.append("  mutation B (canonical-only role)     caught=%s" % caught_b)
    ok = ok and caught_b

    # Mutation C: canonical source hidden -> CANNOT-ASSESS, never OK.
    hidden = evaluate(registry_root, os.path.join(cmr_root, "does-not-exist"))
    caught_c = hidden[0] == CANNOT_ASSESS
    lines.append("  mutation C (canonical source hidden) status=%d (want 2)" % hidden[0])
    ok = ok and caught_c

    lines.insert(0, "registry-parity self-test: %s" % ("OK" if ok else "NOT-OK"))
    return (OK if ok else NOT_OK), lines


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv):
    parser = argparse.ArgumentParser(
        prog="parity.py", description="registry <-> canonical CMR parity gate")
    parser.add_argument("--registry-root", default=REPO_ROOT,
                        help="registry checkout root (default: this repo)")
    parser.add_argument("--cmr-root", default=os.path.join(REPO_ROOT, "vendor", "CMR"),
                        help="canonical CMR root (default: <repo>/vendor/CMR)")
    parser.add_argument("--json", action="store_true",
                        help="emit a machine-readable JSON report")
    parser.add_argument("--self-test", action="store_true",
                        help="prove the drift + cannot-assess paths fire")
    args = parser.parse_args(argv)

    if not HAS_YAML:
        payload = {"status": CANNOT_ASSESS, "result": "CANNOT-ASSESS",
                   "reason": "PyYAML unavailable"}
        print(json.dumps(payload) if args.json else
              "registry-parity: CANNOT-ASSESS — PyYAML unavailable")
        return CANNOT_ASSESS

    if args.self_test:
        status, lines = self_test(args.registry_root, args.cmr_root)
    else:
        status, lines = evaluate(args.registry_root, args.cmr_root)

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
