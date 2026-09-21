#!/usr/bin/env bash
# check-profiles.sh — RUN the registry/profiles suite in the gate, and prove the
# published-version ledger can still refuse a mutated seed (issue #1292).
#
# WHY THIS EXISTS. `registry/profiles` was declared in scripts/pytest-suites.txt
# but SWEPT-ONLY in scripts/gate-coverage-baseline.txt (#524): reachable only via
# `make gate`'s manifest sweep, never by the gate of record. So a published-profile
# immutability drift could sit red on master with `make verify` green — and it did:
# `982647c` (#1210) added the provider-parity rationale markers as an IN-PLACE edit
# of the frozen `claude@1.0.0` / `deepseek@1.0.0` seeds and never re-published, so
# `test_full_coverage_is_green` and `test_ledger_accepts_unchanged_published_tree`
# failed on the tip while no gate ran the suite that would have said so.
#
# This check closes that hole two ways, and neither arm is a formality:
#   1. it RUNS the suite, so a drift is a red gate rather than an invisible red;
#   2. it MUTATES a published seed in a scratch copy and REQUIRES the ledger to
#      refuse it by name — so a green run cannot be a run whose cases were quietly
#      deleted.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS (no python3, no pytest, a
# required input missing). CANNOT-ASSESS is never a pass.
#
# Usage: bash scripts/check-profiles.sh
#
# ---knowledge---
# module_id: scripts.check-profiles
# system: governance
# app: gates
# solution_class: enterprise
# patterns: [tri-state-exit, provoked-negative-control, named-refusal, schema-validation]
# derives_from: null
# owner_sme: qa-sme
# tier: L1
# interfaces: [exit 0 OK, exit 1 NOT-OK, exit 2 CANNOT-ASSESS]
# invariants: ""
# gotchas: ""
# related: ["#524", "#1210", "#1292"]
# do_not_duplicate: null
# ---knowledge---
set -u

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 2

suite="registry/profiles/tests"

# --- required inputs: absent => CANNOT-ASSESS, never a pass -------------------
for required in \
  registry/profiles/validate.py \
  registry/profiles/agent-profile.schema.json \
  registry/profiles/catalog.yaml \
  registry/profiles/versions/manifest.yaml \
  "$suite"; do
  if [ ! -e "$required" ]; then
    echo "check-profiles: CANNOT-ASSESS — $required is missing" >&2
    exit 2
  fi
done

for tool in python3 sha256sum; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "check-profiles: CANNOT-ASSESS — $tool not found" >&2
    exit 2
  fi
done

if ! python3 -c 'import pytest' >/dev/null 2>&1; then
  echo "check-profiles: CANNOT-ASSESS — pytest is not importable" >&2
  exit 2
fi

# ==========================================================================
# 1. the real suite
# ==========================================================================
echo "== the published-profile suite ($suite) =="
out="$(PYTHONDONTWRITEBYTECODE=1 python3 -m pytest "$suite" -q -p no:cacheprovider 2>&1)"
rc=$?

if [ "$rc" -ne 0 ]; then
  if [ "$rc" -eq 1 ]; then
    echo "check-profiles: NOT-OK — $suite is RED: a published profile and its ledger record disagree" >&2
    printf '%s\n' "$out" | grep -E 'IMMUTABLE|^FAILED' | head -20 | sed 's/^/        /' >&2
    exit 1
  fi
  # rc 2/4/5: a collection/usage error, or a selector that matched nothing. No
  # verdict was reached, so this is CANNOT-ASSESS — never a pass, never a NOT-OK.
  echo "check-profiles: CANNOT-ASSESS — pytest exited rc=$rc (no verdict was reached)" >&2
  printf '%s\n' "$out" | tail -8 | sed 's/^/        /' >&2
  exit 2
fi
echo "  OK    the suite is green"

# ==========================================================================
# 2. the negative control: a mutated PUBLISHED seed must be refused BY NAME
# ==========================================================================
# The scratch path carries an explicit private template (SP-9). The usual mktemp
# placeholder form is unusable here: docs-lint reads a run of three capital X's as
# an unfinished marker, so the path is built from the clock instead.
work="${TMPDIR:-/tmp}/profiles-ledger.$(date +%s%N)"
if ! mkdir -p "$work" 2>/dev/null; then
  echo "check-profiles: CANNOT-ASSESS — cannot create a scratch directory" >&2
  exit 2
fi
trap 'rm -rf "$work"' EXIT

if ! cp -a registry/profiles "$work/profiles" 2>/dev/null; then
  echo "check-profiles: CANNOT-ASSESS — cannot copy registry/profiles into the scratch tree" >&2
  exit 2
fi

# The copy validates ITSELF: validate.py resolves its seeds, catalog and manifest
# relative to its own directory, so the control never touches the real tree.
seed_copy="$work/profiles/seeds/coder.1.0.0.yaml"
if [ ! -f "$seed_copy" ]; then
  echo "check-profiles: CANNOT-ASSESS — the scratch copy has no seeds/coder.1.0.0.yaml, so there is no published seed to mutate" >&2
  exit 2
fi

echo "== negative control (a mutated published seed must be refused) =="
# Control E — the untouched copy must be green, or the refusal below would prove
# nothing about the plant.
out_e="$(python3 "$work/profiles/validate.py" 2>&1)"
rc_e=$?
if [ "$rc_e" -ne 0 ]; then
  echo "check-profiles: CANNOT-ASSESS — the pristine scratch copy does not validate (rc=$rc_e); the plant below would be meaningless" >&2
  printf '%s\n' "$out_e" | tail -8 | sed 's/^/        /' >&2
  exit 2
fi
echo "  OK    control E: the pristine scratch copy validates"

# Control F — a one-line edit of a PUBLISHED seed. It is a YAML comment, so the
# file still parses and the immutability ledger is the only thing that can refuse
# it: exactly the shape of the #1210 defect this check exists for.
before="$(sha256sum "$seed_copy" | cut -d' ' -f1)"
printf '\n# planted drift (check-profiles negative control)\n' >> "$seed_copy"
after="$(sha256sum "$seed_copy" | cut -d' ' -f1)"
if [ "$before" = "$after" ]; then
  echo "check-profiles: FAIL — the plant changed nothing; the control proves nothing" >&2
  exit 1
fi

out_f="$(python3 "$work/profiles/validate.py" 2>&1)"
rc_f=$?
named=1
case "$out_f" in *"IMMUTABLE"*) ;; *) named=0 ;; esac
case "$out_f" in *"coder 1.0.0"*) ;; *) named=0 ;; esac
case "$out_f" in *"seeds/coder.1.0.0.yaml"*) ;; *) named=0 ;; esac

if [ "$rc_f" -eq 1 ] && [ "$named" -eq 1 ]; then
  echo "  OK    control F: the mutated published seed is refused by name (rc=$rc_f, ledger immutability)"
else
  echo "check-profiles: FAIL — control F did not refuse the mutated published seed by name (rc=$rc_f, named=$named)" >&2
  printf '%s\n' "$out_f" | tail -8 | sed 's/^/        /' >&2
  exit 1
fi

echo "check-profiles: OK — the registry/profiles suite is green and the immutability ledger still refuses a mutated published seed"
exit 0
