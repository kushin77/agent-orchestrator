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
#        in scripts/ claims to be a PP-1..PP-4 source scan. This checker's own
#        name is exempted (it is not that scan; see the file's own header).
#
# WHY IT IS SHAPED LIKE THIS (self-test, provoked both ways)
#   A gate that cannot fail is a formality, so `--self-test` plants a mutated
#   COPY of the doc in a scratch dir and asserts:
#     - the mutant (a renamed control, or a DECLARED row missing its issue ref,
#       or a PP id with no owning row) is refused BY NAME;
#     - the unmutated, real doc produces NO finding for that same rule
#       (vacuity — a rule that fires on the real doc is not a check, it is
#       noise).
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

# rule 4: no OTHER checker in scripts/ claims to be a PP-1..PP-4 python source scan.
rule_no_scan_script() {
  local rc=0
  local f
  for f in "$ROOT"/scripts/check-*.sh; do
    [ -f "$f" ] || continue
    [ "$f" = "$SELF" ] && continue
    if grep -qE 'PP-[0-9]' "$f"; then
      echo "REFUSED PP-1 no-scan-script: $f references a PP-N id — docs/PYTHON-PATTERNS.md says no source scan ships"
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
  out="$(rule_no_scan_script)"; [ -n "$out" ] && { echo "$out"; rc=1; }
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

  # --- probe D: a scratch checker that claims to scan PP-1 -> rule 4 must fire;
  #     with no such file, rule 4 must be silent (checked against the real tree
  #     inline, since rule 4 scans $ROOT/scripts by design).
  local planted="$ROOT/scripts/check-python-patterns-doctrine-selftest-plant.sh"
  printf '#!/usr/bin/env bash\n# would refuse PP-1 by name\n' > "$planted"
  if rule_no_scan_script >/dev/null; then
    rm -f "$planted"
    echo "check-python-patterns-doctrine: SELF-TEST FAILED — no-scan-script did not fire on a planted PP-1 scanner" >&2
    fail=1
  else
    rm -f "$planted"
  fi
  if ! rule_no_scan_script >/dev/null; then
    echo "check-python-patterns-doctrine: SELF-TEST FAILED — no-scan-script fired with no plant present (vacuity)" >&2
    fail=1
  fi

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
