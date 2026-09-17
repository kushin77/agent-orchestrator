#!/usr/bin/env bash
# check-tagging.sh — the tag authority gate (issue #1175).
#
# The tag authority (`governance/tagging/`) declares a closed vocabulary for
# every tag this repository puts on a governed artifact, borrows — never
# re-declares — the vocabularies that already have an authority here, and derives
# the gates a tag set requires along the channel those gates run in (pr / ci /
# cd / ops). This gate is what makes those declarations falsifiable.
#
# Six checks, each carrying a REAL exit code, because a gate that cannot fail is
# a formality (GR-12):
#
#   tagging-lint      the authority's own shape: every dimension well-formed,
#                     every borrowed vocabulary still EQUAL to its authority,
#                     every rule's `when` clause on a declared dimension, every
#                     gate a rule names actually resolvable (make:<target>
#                     against the Makefile, check:<name> against the registry
#                     scripts/verify.sh builds), every document satisfying its
#                     frozen shape, and the declared controls agreeing with the
#                     authority they govern. Rename a gate and this fails BY
#                     NAME rather than describing a pipeline that is gone.
#   tagging-matrix    the tag -> gate matrix committed in docs/TAGGING.md is what
#                     `cli.py matrix` generates — a hand-edited matrix is stale
#                     documentation wearing a generated artifact's clothes.
#   tagging-suite     the module's pytest suite. It is run HERE rather than
#                     declared in scripts/pytest-suites.txt so this gate is the
#                     one that exercises it (a declared-but-unrun suite is the
#                     gap #331 and #525 were both filed for).
#   tagging-refusals  the negative control: every refusal declared in
#                     taxonomy.yaml is provoked by a REAL mutant planted in a
#                     scratch tree, and must be refused BY NAME — with its clean
#                     twin accepted, so a rule that fires on everything is caught
#                     rather than passing as strict. The refusal-id set is
#                     asserted equal to the set of codes the model can raise, so
#                     a refusal declared but never raised fails here too.
#   tagging-artifacts the artifacts round-trip, each with its clean twin: the
#                     frozen shape file declares the four document shapes, the
#                     ledger refuses a malformed row BEFORE the write and reads
#                     past a hand-written garbage line, the live projection names
#                     the item it refuses while an all-clean board is refused
#                     nothing, and a relaxed control is refused by name.
#   tagging-mandate   the CONSTITUTION still declares the rule. A rule only prose
#                     carries is advice (GR-29 / AO-GR-4), so the contract docs —
#                     AGENTS.md, docs/GOLDEN-RULES.md, docs/GOVERNANCE.md,
#                     docs/EXECUTION-PLAN.md, docs/QA-GATE.md — must each declare
#                     the tag authority and its `posture`/`lifecycle` dimensions,
#                     and this check FAILS naming the document AND the marker the
#                     moment one stops. That is what makes the rule institutional
#                     rather than advisory, and it is the same shape
#                     scripts/check-chronological-dispatch.sh uses for rule 14.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
# CANNOT-ASSESS must never be reported as a pass.
#
# Usage: bash scripts/check-tagging.sh
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-tagging: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

# One global scratch with one EXIT trap (SP-1). The provocations write their own
# mutants under this root and `provoke.py` cleans its own subtree; this trap
# removes whatever is left, so a killed run cannot litter the box.
scratch="$(mktemp -d /tmp/check-tagging.XXXXXX)" || {
  echo "check-tagging: CANNOT-ASSESS — cannot create a scratch directory" >&2
  exit 2
}
trap 'rm -rf "$scratch"' EXIT

failures=0
assessed=0
skipped=0

report() {
  printf '  %-9s %s\n' "$1" "$2"
}

# --- check 1: the authority's own shape -------------------------------------
out="$(python3 governance/tagging/cli.py lint 2>&1)"
rc=$?
case "$rc" in
  0)
    assessed=$((assessed + 1))
    report PASS "tagging-lint — $(printf '%s' "$out" | grep -m1 '^tagging-lint:' | head -1)"
    ;;
  1)
    assessed=$((assessed + 1))
    failures=$((failures + 1))
    report FAIL "tagging-lint — the tag authority does not hold"
    printf '%s\n' "$out" | sed 's/^/      /'
    ;;
  *)
    report SKIP "tagging-lint — CANNOT-ASSESS (rc $rc)"
    printf '%s\n' "$out" | sed 's/^/      /'
    skipped=$((skipped + 1))
    ;;
esac

# --- check 2: the committed matrix is the generated one ---------------------
out="$(python3 governance/tagging/cli.py check 2>&1)"
rc=$?
case "$rc" in
  0)
    assessed=$((assessed + 1))
    report PASS "tagging-matrix — docs/TAGGING.md carries the generated matrix"
    ;;
  1)
    assessed=$((assessed + 1))
    failures=$((failures + 1))
    report FAIL "tagging-matrix — the committed matrix is not what the authority generates"
    printf '%s\n' "$out" | sed 's/^/      /'
    ;;
  *)
    report SKIP "tagging-matrix — CANNOT-ASSESS (rc $rc)"
    printf '%s\n' "$out" | sed 's/^/      /'
    skipped=$((skipped + 1))
    ;;
esac

# --- check 3: the module's own suite ----------------------------------------
if python3 -c 'import pytest' >/dev/null 2>&1; then
  out="$(env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q \
    --rootdir="$root" governance/tagging/tests 2>&1)"
  rc=$?
  assessed=$((assessed + 1))
  if [ "$rc" -eq 0 ]; then
    report PASS "tagging-suite — $(printf '%s' "$out" | tail -1)"
  else
    failures=$((failures + 1))
    report FAIL "tagging-suite — the tag authority's suite is red (rc $rc)"
    printf '%s\n' "$out" | tail -25 | sed 's/^/      /'
  fi
else
  report SKIP "tagging-suite — CANNOT-ASSESS (pytest not installed; never a pass)"
  skipped=$((skipped + 1))
fi

# --- check 4: the negative control ------------------------------------------
out="$(AO_TAGGING_SCRATCH="$scratch" python3 governance/tagging/provoke.py 2>&1)"
rc=$?
case "$rc" in
  0)
    assessed=$((assessed + 1))
    report PASS "tagging-refusals — $(printf '%s' "$out" | tail -1)"
    ;;
  1)
    assessed=$((assessed + 1))
    failures=$((failures + 1))
    report FAIL "tagging-refusals — a declared refusal was not provoked by name"
    printf '%s\n' "$out" | sed 's/^/      /'
    ;;
  *)
    report SKIP "tagging-refusals — CANNOT-ASSESS (rc $rc)"
    printf '%s\n' "$out" | sed 's/^/      /'
    skipped=$((skipped + 1))
    ;;
esac

# --- check 5: the artifacts round-trip, each with its clean twin -------------
# The frozen shape file, the append-only ledger and the live projection can each
# pass while doing nothing: a schema nothing validates against is a decoration, a
# ledger that accepts any row is not an audit trail, and a projection over zero
# issues looks exactly like a clean board. So each is driven with its provoked
# half AND its clean twin.
out="$(python3 governance/tagging/artifacts.py 2>&1)"
rc=$?
case "$rc" in
  0)
    assessed=$((assessed + 1))
    report PASS "tagging-artifacts — $(printf '%s' "$out" | tail -1)"
    ;;
  1)
    assessed=$((assessed + 1))
    failures=$((failures + 1))
    report FAIL "tagging-artifacts — an artifact round trip did not hold"
    printf '%s\n' "$out" | sed 's/^/      /'
    ;;
  *)
    report SKIP "tagging-artifacts — CANNOT-ASSESS (rc $rc)"
    printf '%s\n' "$out" | sed 's/^/      /'
    skipped=$((skipped + 1))
    ;;
esac

# --- check 6: the constitution still declares the rule ----------------------
# The behavioural half above can be perfectly green while the rule itself stops
# being constitutional: the gate proves the authority is enforced, not that
# anything SAYS it must be. This check reads the contract docs and fails naming
# the document and the marker that went missing.
out="$(python3 governance/tagging/mandate.py 2>&1)"
rc=$?
case "$rc" in
  0)
    assessed=$((assessed + 1))
    report PASS "tagging-mandate — $(printf '%s' "$out" | tail -1)"
    ;;
  1)
    assessed=$((assessed + 1))
    failures=$((failures + 1))
    report FAIL "tagging-mandate — a contract document stopped declaring the rule"
    printf '%s\n' "$out" | sed 's/^/      /'
    ;;
  *)
    report SKIP "tagging-mandate — CANNOT-ASSESS (rc $rc)"
    printf '%s\n' "$out" | sed 's/^/      /'
    skipped=$((skipped + 1))
    ;;
esac

# --- verdict ----------------------------------------------------------------
echo ""
if [ "$failures" -gt 0 ]; then
  echo "check-tagging: FAIL ($failures of $assessed check(s) failed)"
  exit 1
fi
if [ "$assessed" -eq 0 ]; then
  echo "check-tagging: CANNOT-ASSESS — no check could be assessed (never a pass)"
  exit 2
fi
if [ "$skipped" -gt 0 ]; then
  echo "check-tagging: PASS ($assessed check(s) passed, $skipped skipped — skips are named above, never counted as passes)"
  exit 0
fi
echo "check-tagging: PASS ($assessed of $assessed checks)"
exit 0
