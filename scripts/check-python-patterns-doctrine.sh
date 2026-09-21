#!/usr/bin/env bash
# check-python-patterns-doctrine.sh — conformance gate for docs/PYTHON-PATTERNS.md (issue #1429).
#
# WHAT THIS IS
#   docs/PYTHON-PATTERNS.md is deliberately NOT a source-scan doctrine: its own
#   "measured refusal" section shows that every static formulation of PP-1 (the
#   #506 date bomb) either finds nothing, keys off incidental text, or refuses
#   the repair as loudly as the defect. So there is no scripts/check-*.sh that
#   greps Python source for PP-1, PP-2, PP-3 or PP-4 — shipping one would be the
#   exact formality the doc's own §"Using this canon" (c) refuses (GR-12).
#
#   What CAN be mechanized, and what this gate does, is the doc's OWN claims
#   about itself:
#
#     1. python-patterns-enforcement-live — PP-1's row claims its enforcer is
#        control `turn-date-scope` in scripts/check-chat-finops.sh, wired by
#        path in scripts/verify.sh. Rename either and this fails BY NAME.
#     2. python-patterns-declared-has-owner — every row in the DECLARED table
#        (PP-2, PP-3, PP-4) names a measured count AND an issue that owns its
#        retirement (`#NNNN`). A declared pattern with no owner is an unowned
#        commitment nobody is accountable for closing — which is the defect
#        this rule exists to name.
#     3. python-patterns-ids-unique — every `PP-N` id used anywhere in the doc
#        resolves to exactly one row, in the enforced table (PP-1) or the
#        DECLARED table (PP-2..), never both and never zero.
#     4. python-patterns-no-scan-script — the doc's own claim ("no
#        scripts/check-python-patterns.sh ships") stays true: no OTHER checker
#        in scripts/ CLAIMS TO BE a PP-1..PP-4 source scan. A claim is a
#        DECLARATION — the refused artifact's own name, or a PP-N id inside the
#        checker's machine-readable `# ---knowledge---` identity header (the
#        block where a gate declares what it is). A prose CITATION of the
#        doctrine inside an unrelated gate's body (e.g. check-peer-check.sh's
#        comment naming the date-bomb pattern) is a cross-reference, not a
#        claim, and must NOT fire. The former whole-file grep could not tell a
#        citation from a claim, so it fired on the unmutated tree and proved
#        nothing — #1862. This checker's own name is exempted (it is not that
#        scan; see the file's own header).
#
# WHY IT IS SHAPED LIKE THIS (self-test, provoked both ways)
#   A gate that cannot fail is a formality, so `--self-test` plants a mutated
#   COPY of the doc in a scratch dir and asserts:
#     - the mutant (a renamed control, or a DECLARED row missing its issue ref,
#       or a PP id with no owning row) is refused BY NAME;
#     - the unmutated, real doc produces NO finding for that same rule
#       (vacuity — a rule that fires on the real doc is not a check, it is
#       noise).
#   Rule 4 is provoked in a scratch scripts/ venue (its scan dir is a
#   parameter, so nothing is written into the repository tree) with BOTH halves:
#   a checker that CLAIMS the scan (the refused artifact name, or a PP-N id in
#   its knowledge header) must RED, while a mere CITATION of the doctrine in a
#   body comment, and an empty venue, must stay silent. The real tree is covered
#   by the run's own rule-4 pass, which refuses BY NAME. That citation/claim
#   discrimination is the property #1862 restores.
#   `--self-test` runs before the repository is examined, on every invocation.
#
# EXIT CONTRACT (the repo's honesty tri-state)
#   0  OK              self-test passed and the real doc conforms
#   1  NOT-OK          self-test failed, or a real conformance rule was refused
#   2  CANNOT-ASSESS   docs/PYTHON-PATTERNS.md missing, scripts/verify.sh or
#                       scripts/check-chat-finops.sh missing, no scratch dir,
#                       or a bad invocation — never a pass
#
# Usage:
#   bash scripts/check-python-patterns-doctrine.sh              self-test + tree
#   bash scripts/check-python-patterns-doctrine.sh --self-test   provocation alone
#   bash scripts/check-python-patterns-doctrine.sh --doc FILE    scan exactly this doc
#
# ---knowledge---
# module_id: scripts.check-python-patterns-doctrine
# system: governance
# app: gates
# solution_class: enterprise
# patterns: [tri-state-exit, provoked-negative-control, self-proving-gate, no-false-green, declared-authority, named-refusal]
# derives_from: null
# owner_sme: qa-sme
# tier: L1
# interfaces: [exit 0 OK, exit 1 NOT-OK, exit 2 CANNOT-ASSESS]
# invariants: ""
# gotchas: ""
# related: ["#506", "#1429"]
# do_not_duplicate: null
# ---knowledge---
set -u

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
ROOT="$(find_repo_root)"
DOC="$ROOT/docs/PYTHON-PATTERNS.md"
VERIFY="$ROOT/scripts/verify.sh"
FINOPS="$ROOT/scripts/check-chat-finops.sh"
SELF="$ROOT/scripts/check-python-patterns-doctrine.sh"

MODE="tree"
DOC_OVERRIDE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --self-test) MODE="self-test" ;;
    --doc) shift; DOC_OVERRIDE="${1:-}" ;;
    *) echo "check-python-patterns-doctrine: unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

if ! command -v grep >/dev/null 2>&1; then
  echo "check-python-patterns-doctrine: CANNOT-ASSESS — grep missing" >&2
  exit 2
fi

# ---------------------------------------------------------------------------
# The four rules, run against an arbitrary doc path + arbitrary verify/finops
# paths, so the self-test and the real run share the same code.
# ---------------------------------------------------------------------------

# rule 1: the enforcement PP-1 claims must still exist and be wired.
rule_enforcement_live() {
  local doc="$1" verify="$2" finops="$3"
  if [ ! -f "$finops" ]; then
    echo "REFUSED PP-1 enforcement-live: $finops does not exist"
    return 1
  fi
  if ! grep -q "turn-date-scope" "$finops"; then
    echo "REFUSED PP-1 enforcement-live: control 'turn-date-scope' not found in $finops"
    return 1
  fi
  if [ ! -f "$verify" ] || ! grep -q "check-chat-finops\.sh" "$verify"; then
    echo "REFUSED PP-1 enforcement-live: check-chat-finops.sh not wired by path in $verify"
    return 1
  fi
  return 0
}

# rule 2: every DECLARED row (PP-2..) names a measured count and an owning issue.
rule_declared_has_owner() {
  local doc="$1"
  local in_table=0
  local line ppid rc=0
  while IFS= read -r line; do
    case "$line" in
      "## Declared, measured, not enforced"*) in_table=1 ;;
      "## "*) [ "$in_table" -eq 1 ] && [ "$line" != "## Declared, measured, not enforced" ] && in_table=0 ;;
    esac
    if [ "$in_table" -eq 1 ] && [[ "$line" =~ ^\|[[:space:]]*\`PP-[0-9]+ ]]; then
      ppid="$(printf '%s' "$line" | grep -oE 'PP-[0-9]+' | head -1)"
      if ! [[ "$line" =~ \#[0-9]+ ]]; then
        echo "REFUSED $ppid declared-has-owner: DECLARED row has no owning issue (#NNNN)"
        rc=1
      fi
    fi
  done < "$doc"
  return "$rc"
}

# rule 3: every PP-N id used anywhere resolves to exactly one row (enforced xor declared).
rule_ids_unique() {
  local doc="$1"
  local rc=0
  local all_ids enforced_ids declared_ids
  all_ids="$(grep -oE 'PP-[0-9]+' "$doc" | sort -u)"
  enforced_ids="$(awk '/^## The pattern$/{f=1;next}/^## /{f=0}f' "$doc" | grep -oE '^\| *`PP-[0-9]+`' | grep -oE 'PP-[0-9]+' | sort -u)"
  declared_ids="$(awk '/^## Declared, measured, not enforced$/{f=1;next}/^## /{f=0}f' "$doc" | grep -oE '^\| *`PP-[0-9]+`' | grep -oE 'PP-[0-9]+' | sort -u)"
  local id
  for id in $all_ids; do
    local in_e=0 in_d=0
    case $'\n'"$enforced_ids"$'\n' in *$'\n'"$id"$'\n'*) in_e=1 ;; esac
    case $'\n'"$declared_ids"$'\n' in *$'\n'"$id"$'\n'*) in_d=1 ;; esac
    if [ "$in_e" -eq 0 ] && [ "$in_d" -eq 0 ]; then
      echo "REFUSED $id ids-unique: $id is referenced but has no row in either table"
      rc=1
    elif [ "$in_e" -eq 1 ] && [ "$in_d" -eq 1 ]; then
      echo "REFUSED $id ids-unique: $id has a row in BOTH the enforced and DECLARED tables"
      rc=1
    fi
  done
  return "$rc"
}

# rule 4: no checker in <scan_dir> CLAIMS TO BE a PP-1..PP-4 python source
# scan. A claim is a DECLARATION, not a citation: either the refused artifact's
# own name, or a PP-N id inside the checker's `# ---knowledge---` identity
# header — the machine-readable block where a gate declares what it is. A
# doctrine reference in an unrelated gate's body is a cross-reference, not a
# claim, and does not fire (#1862: the former whole-file grep could not tell the
# two apart, so it fired on the unmutated tree and proved nothing).
# The scan dir is a parameter so the self-test can plant in its own scratch
# venue and never write into the repository tree.
rule_no_scan_script() {
  local dir="$1"
  local rc=0
  local f base header
  for f in "$dir"/check-*.sh; do
    [ -f "$f" ] || continue
    [ "$f" = "$SELF" ] && continue
    base="${f##*/}"
    # (i) the exact artifact the doc refuses to ship, by name.
    if [ "$base" = "check-python-patterns.sh" ]; then
      echo "REFUSED PP-1 no-scan-script: $f is the very source scan docs/PYTHON-PATTERNS.md refuses to ship"
      rc=1
      continue
    fi
    # (ii) a checker that DECLARES itself the scan: a PP-N id in its identity
    #      header. Body prose is a citation of the doctrine, never a claim.
    header="$(awk 'c==2{exit} /^# ---knowledge---/{c++} c>=1{print}' "$f")"
    if printf '%s\n' "$header" | grep -qE 'PP-[0-9]'; then
      echo "REFUSED PP-1 no-scan-script: $f declares a PP-N id in its knowledge header — docs/PYTHON-PATTERNS.md says no source scan ships"
      rc=1
    fi
  done
  return "$rc"
}

run_rules() {
  local doc="$1" verify="$2" finops="$3"
  local rc=0 out
  out="$(rule_enforcement_live "$doc" "$verify" "$finops")"; [ -n "$out" ] && { echo "$out"; rc=1; }
  out="$(rule_declared_has_owner "$doc")"; [ -n "$out" ] && { echo "$out"; rc=1; }
  out="$(rule_ids_unique "$doc")"; [ -n "$out" ] && { echo "$out"; rc=1; }
  out="$(rule_no_scan_script "$ROOT/scripts")"; [ -n "$out" ] && { echo "$out"; rc=1; }
  return "$rc"
}

# ---------------------------------------------------------------------------
# Self-test: mutate a scratch copy of the real doc three ways, each must be
# refused by name; the unmutated real doc must be silent for that same rule.
# ---------------------------------------------------------------------------
self_test() {
  if [ ! -f "$DOC" ]; then
    echo "check-python-patterns-doctrine: CANNOT-ASSESS — $DOC missing" >&2
    return 2
  fi
  local scratch=""
  scratch="$(mktemp -d /tmp/check-python-patterns-doctrine.XXXXXX 2>/dev/null)" || { echo "check-python-patterns-doctrine: CANNOT-ASSESS — no scratch dir" >&2; return 2; }
  SELFTEST_SCRATCH="$scratch"
  trap 'rm -rf "$SELFTEST_SCRATCH"' EXIT

  local fail=0

  # --- probe A: rename the enforcing control -> rule 1 must fire on the mutant,
  #     and be silent on the real doc + real finops file.
  if [ -f "$FINOPS" ]; then
    local finops_mutant="$scratch/finops_mutant.sh"
    sed 's/turn-date-scope/turn-day-window-renamed/g' "$FINOPS" > "$finops_mutant"
    if rule_enforcement_live "$DOC" "$VERIFY" "$finops_mutant" >/dev/null; then
      echo "check-python-patterns-doctrine: SELF-TEST FAILED — enforcement-live did not fire on a renamed control" >&2
      fail=1
    fi
    if ! rule_enforcement_live "$DOC" "$VERIFY" "$FINOPS" >/dev/null; then
      echo "check-python-patterns-doctrine: SELF-TEST FAILED — enforcement-live fired on the real, unmutated finops gate (vacuity)" >&2
      fail=1
    fi
  else
    echo "check-python-patterns-doctrine: CANNOT-ASSESS — $FINOPS missing" >&2
    return 2
  fi

  # --- probe B: strip the issue ref from a DECLARED row -> rule 2 must fire.
  local doc_mutant_b="$scratch/doc_mutant_b.md"
  awk '
    /^## Declared, measured, not enforced$/ { intable=1 }
    /^## / && !/^## Declared, measured, not enforced$/ { intable=0 }
    intable && /^\| *`PP-3`/ { gsub(/#[0-9]+/, "issue-tbd") }
    { print }
  ' "$DOC" > "$doc_mutant_b"
  if rule_declared_has_owner "$doc_mutant_b" >/dev/null; then
    echo "check-python-patterns-doctrine: SELF-TEST FAILED — declared-has-owner did not fire when an owning issue was stripped" >&2
    fail=1
  fi
  if ! rule_declared_has_owner "$DOC" >/dev/null; then
    echo "check-python-patterns-doctrine: SELF-TEST FAILED — declared-has-owner fired on the real, unmutated doc (vacuity)" >&2
    fail=1
  fi

  # --- probe C: add a PP-id reference with no row anywhere -> rule 3 must fire.
  local doc_mutant_c="$scratch/doc_mutant_c.md"
  { cat "$DOC"; printf '\n\nSee also `PP-99`, a pattern with no row.\n'; } > "$doc_mutant_c"
  if rule_ids_unique "$doc_mutant_c" >/dev/null; then
    echo "check-python-patterns-doctrine: SELF-TEST FAILED — ids-unique did not fire on an orphan PP-99 reference" >&2
    fail=1
  fi
  if ! rule_ids_unique "$DOC" >/dev/null; then
    echo "check-python-patterns-doctrine: SELF-TEST FAILED — ids-unique fired on the real, unmutated doc (vacuity)" >&2
    fail=1
  fi

  # --- probe D: rule 4 must fire ONLY on a checker that CLAIMS TO BE the
  #     PP-1..PP-4 scan, and stay silent on a mere citation and on an empty
  #     venue (#1862). The plant venue is a scratch scripts/ dir, so the arm
  #     never writes into the repository tree — an earlier shape planted in
  #     $ROOT/scripts and its cleanup deleted an externally planted
  #     check-python-patterns.sh before tree mode could see it.
  local sdir="$scratch/scripts"
  mkdir -p "$sdir"

  # D1: the refused artifact, by name -> must fire.
  printf '#!/usr/bin/env bash\n# the source scan #1028 asked for\n' > "$sdir/check-python-patterns.sh"
  if rule_no_scan_script "$sdir" >/dev/null; then
    echo "check-python-patterns-doctrine: SELF-TEST FAILED — no-scan-script did not fire on the refused artifact check-python-patterns.sh" >&2
    fail=1
  fi
  rm -f "$sdir/check-python-patterns.sh"

  # D2: a checker declaring the scan in its identity header -> must fire, under
  #     a name the refused-name arm cannot catch (the header arm alone).
  printf '#!/usr/bin/env bash\n# check-pp-claim.sh\n#\n# ---knowledge---\n# module_id: scripts.check-python-patterns\n# system: governance\n# app: gates\n# patterns: [source-scan]\n# related: ["PP-1"]\n# ---knowledge---\n' > "$sdir/check-pp-claim.sh"
  if rule_no_scan_script "$sdir" >/dev/null; then
    echo "check-python-patterns-doctrine: SELF-TEST FAILED — no-scan-script did not fire on a checker declaring the PP-N scan in its knowledge header" >&2
    fail=1
  fi
  rm -f "$sdir/check-pp-claim.sh"

  # D3: a mere CITATION of the doctrine in a body comment -> must stay silent
  #     (the property #1862 restores: a claim is not a citation).
  printf '#!/usr/bin/env bash\n# a hardcoded day collapses to a false green (docs/PYTHON-PATTERNS.md PP-1)\n' > "$sdir/check-pp-citation.sh"
  if ! rule_no_scan_script "$sdir" >/dev/null; then
    echo "check-python-patterns-doctrine: SELF-TEST FAILED — no-scan-script fired on a mere citation of the doctrine (it cannot tell a claim from a citation)" >&2
    fail=1
  fi
  rm -f "$sdir/check-pp-citation.sh"

  # D4: an empty venue -> must stay silent (rule 4 is not "matches everything").
  if ! rule_no_scan_script "$sdir" >/dev/null; then
    echo "check-python-patterns-doctrine: SELF-TEST FAILED — no-scan-script fired with no plant present (vacuity)" >&2
    fail=1
  fi

  # The REAL tree is asserted silent by this run's own rule-4 pass (run_rules),
  # which refuses BY NAME if any checker in scripts/ claims the scan. Keeping the
  # self-test inside the scratch venue stops it racing a plant — and stops it
  # deleting one: an earlier shape wrote its plant into $ROOT/scripts and cleaned
  # it up, so it destroyed an externally planted check-python-patterns.sh before
  # tree mode could see it (measured: a planted refused artifact read as rc=0).

  [ "$fail" -eq 0 ]
}

main() {
  self_test
  local st_rc=$?
  if [ "$st_rc" -eq 2 ]; then
    return 2
  fi
  if [ "$st_rc" -ne 0 ]; then
    echo "check-python-patterns-doctrine: NOT-OK — self-test did not prove the gate" >&2
    return 1
  fi

  if [ "$MODE" = "self-test" ]; then
    echo "check-python-patterns-doctrine: OK — self-test passed"
    return 0
  fi

  local doc="${DOC_OVERRIDE:-$DOC}"
  if [ ! -f "$doc" ]; then
    echo "check-python-patterns-doctrine: CANNOT-ASSESS — $doc missing" >&2
    return 2
  fi

  local findings
  findings="$(run_rules "$doc" "$VERIFY" "$FINOPS")"
  if [ -n "$findings" ]; then
    echo "$findings"
    echo "check-python-patterns-doctrine: NOT-OK — docs/PYTHON-PATTERNS.md's own claims do not hold" >&2
    return 1
  fi

  echo "check-python-patterns-doctrine: OK — docs/PYTHON-PATTERNS.md's enforcement claim, DECLARED ownership, id coverage and no-scan-script promise all hold"
  return 0
}

main
exit $?
