#!/usr/bin/env bash
# check-budget-authority.sh — the budget-decision authority, enforced (issue #1495).
#
# WHAT THIS IS
#   The gate of record for `gateway/finops/budget-authority.yaml`. The
#   declaration (issue #1495, child lane of the #1458 register residual row R6)
#   names the canonical budget-decision model, records every enforcement-shaped
#   `*Budget*` class with the disposition it carries, and records how each
#   duplicated class name was resolved. This script refuses a tree where the
#   declaration and the code disagree — including the case the row was filed
#   for: a NEW duplicate model name, refused BY NAME.
#
# WHY IT IS SHAPED LIKE THIS
#   A gate that cannot fail is a formality, so this gate proves itself on EVERY
#   run, before it looks at the repository:
#
#     1. a CLEAN TWIN — the declaration's own sources materialised into a
#        scratch tree — must resolve with no findings;
#     2. a PLANTED DUPLICATE class name must be refused, naming the name and
#        both sites;
#     3. a planted UNREGISTERED enforcement-shaped class must be refused;
#     4. a registered class DELETED from the tree must be refused (a register
#        that describes a tree it no longer has is stale, not green);
#     5. a boundary recorded WITHOUT the boundary it owns must be refused (the
#        acceptance criterion's own requirement);
#     6. a collision row that is STILL a duplicate must be refused (a
#        resolution on paper only);
#     7. a DRIFTED borrowed anchor must be refused — this authority declares no
#        policy vocabulary of its own, so a borrowed value that moved is a
#        finding, not a silent re-declaration;
#     8. a borrowed config naming a value the borrowing vocabulary cannot
#        represent must be refused;
#     9. a drifted outcome vocabulary must be refused.
#
#   Every arm drives the SAME `evaluate()` the repository run uses (the
#   provocation is not a copy of the rule), and every arm asserts the refusal
#   by its own CODE and its own needle, then restores the file byte-identically.
#   The arm builder itself is checked: a mutation that changes nothing is a
#   failure, so a vacuous provocation cannot pass as a proven one.
#
# THE CHECKS
#   `python3 gateway/finops/budget_authority.py` is the model; this script is
#   the invocation. It runs no pytest suite on purpose: an auto-discovered
#   check that runs a suite would re-wire that suite into the gate of record
#   and stale the gate-coverage baseline another lane owns.
#
# EXIT CONTRACT (the repo's honesty tri-state)
#   0  OK              the declaration resolves, no unrecorded duplicate
#   1  NOT-OK          a finding refused by name, or a failed self-test
#   2  CANNOT-ASSESS   python3 or the declaration is missing/unusable, or a
#                      bad invocation — never a pass
#
# Usage:
#   bash scripts/check-budget-authority.sh              the gate (self-test + tree)
#   bash scripts/check-budget-authority.sh --self-test  the provocation alone
#   bash scripts/check-budget-authority.sh --list       the declared register
#   bash scripts/check-budget-authority.sh --root DIR   scan another tree
set -u

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)" || exit 2

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-budget-authority: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

exec python3 "$root/gateway/finops/budget_authority.py" --root "$root" "$@"
