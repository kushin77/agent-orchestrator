#!/usr/bin/env bash
# check-marker-scan.sh — the marker rule's provocation, ENFORCED (issue #804).
#
# WHY THIS CHECK EXISTS
#   Issue #804: the marker rule in scripts/check-docs.sh ended in a trailing word
#   boundary with NO leading one, so it matched the TAIL of any run of three or
#   more capital X — and `mktemp` REQUIRES a template ending in at least three X.
#   The canonical scratch idiom
#
#       work="$(mktemp -d /tmp/cbp.XXXXXX)" || exit 2
#
#   failed `docs-lint` in every file that scanner reads, so a legitimate shell
#   file tripped a gate whose message names a marker instead of a placeholder.
#   The rule now carries a LEADING boundary on the token branch.
#
# THE PROVOCATION IS THE POINT
#   A rule that accepts a template and finds no marker is indistinguishable from
#   a rule that matches nothing. Two layers prove the rule fires BOTH ways:
#
#     1. `scripts/check-docs.sh --self-test` asserts both halves against the
#        shipping rule, each with a mutant that must move the verdict.
#     2. this check mutates the ARTEFACT: it copies scripts/check-docs.sh,
#        rewrites the pattern literal in the copy, EXECUTES the copy, and
#        requires the verdict to move —
#          * the DEFECTIVE rule (trailing boundary only, the one #804 measured)
#            must REFUSE the canonical template, so "the template is accepted"
#            can fail;
#          * a rule that matches NOTHING must ACCEPT the real markers, so "a real
#            marker is refused" can fail.
#   The copy is a scratch file, never the tracked one, and what runs is the
#   artefact's own text — not a quoted expectation about it.
#
# THE FILE CANNOT SPELL A MARKER (and that is the fix, not a nuisance)
#   This script is one of the files the rule reads, so every marker it plants is
#   assembled from fragments (`TO''DO`) rather than written literally — the same
#   discipline the rule's own literal follows. #804 measured the opposite: the
#   first fix removed the template and kept a comment explaining the trap, and
#   the COMMENT kept the gate red. A rule that cannot tolerate its own
#   documentation keeps firing on it.
#
# Exit: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS. Offline, deterministic, no network.
#
# Usage: bash scripts/check-marker-scan.sh
set -u

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 2

# Every marker this check plants is assembled here, so this file spells none of
# them literally.
M_1='TO''DO'
M_2='FIX''ME'
M_3='HA''CK'
M_T='XX''X'

fail=0
scratch="$(mktemp -d /tmp/ao-marker-scan.XXXXXX)" || {
  echo 'check-marker-scan: CANNOT-ASSESS — no scratch directory' >&2
  exit 2
}
trap "rm -rf '$scratch'" EXIT

echo "== the marker rule, provoked against the artefact =="

# --- 1. the shipping rule's own two halves (delegated, not re-implemented) ---
bash scripts/check-docs.sh --self-test
selftest_rc=$?
if [ "$selftest_rc" -eq 2 ]; then
  echo "check-marker-scan: CANNOT-ASSESS — scripts/check-docs.sh --self-test could not assess (rc 2)" >&2
  exit 2
elif [ "$selftest_rc" -ne 0 ]; then
  echo "check-marker-scan: FAIL — scripts/check-docs.sh --self-test is NOT-OK (rc $selftest_rc)" >&2
  fail=1
fi

# --- 2. the fixtures -------------------------------------------------------
fx="$scratch/fixtures"
mkdir -p "$fx" || exit 2
printf 'work="$(mktemp -d /tmp/cbp.XXXXXX)" || exit 2\n' > "$fx/template.sh"
printf '# %s: planted by check-marker-scan.sh\n' "$M_1" > "$fx/one.sh"
printf '# %s: planted by check-marker-scan.sh\n' "$M_2" > "$fx/two.sh"
printf '# %s: planted by check-marker-scan.sh\n' "$M_3" > "$fx/three.sh"

# --- 3. the SHIPPING artefact, over the fixtures ---------------------------
# The copy under $scratch/scripts/ is the shipped text, unmodified: the rule the
# gate runs, not a paraphrase of it. `--markers` takes explicit files, so no git
# repository is needed for the rule to be exercised.
mkdir -p "$scratch/scripts/lib" || exit 2
cp scripts/check-docs.sh "$scratch/scripts/check-docs.sh" || exit 2
cp scripts/lib/common.sh "$scratch/scripts/lib/common.sh" || exit 2

if bash "$scratch/scripts/check-docs.sh" --markers "$fx/template.sh" > "$scratch/ship-template.out" 2>&1; then
  echo "  OK  the SHIPPED rule accepts a canonical six-X template"
else
  echo "check-marker-scan: FAIL — the shipped rule still refuses the canonical template" >&2
  sed 's/^/    /' "$scratch/ship-template.out" >&2
  fail=1
fi

bash "$scratch/scripts/check-docs.sh" --markers "$fx/one.sh" "$fx/two.sh" "$fx/three.sh" > "$scratch/ship-markers.out" 2>&1
ship_markers_rc=$?
if [ "$ship_markers_rc" -eq 0 ]; then
  echo "check-marker-scan: FAIL — the shipped rule refused NO marker; it matches nothing" >&2
  fail=1
elif [ "$ship_markers_rc" -ne 1 ]; then
  echo "check-marker-scan: FAIL — the shipped rule exited $ship_markers_rc on marker fixtures (expected 1)" >&2
  sed 's/^/    /' "$scratch/ship-markers.out" >&2
  fail=1
else
  named=0
  for f in one.sh two.sh three.sh; do
    grep -qF "$fx/$f" "$scratch/ship-markers.out" && named=$((named + 1))
  done
  if [ "$named" -eq 3 ]; then
    echo "  OK  a real marker in each of the three spellings is refused, by name"
  else
    echo "check-marker-scan: FAIL — the shipped rule refused markers but NAMED only $named of 3" >&2
    sed 's/^/    /' "$scratch/ship-markers.out" >&2
    fail=1
  fi
fi

# --- 4. the artefact MUTATED: each half must be able to fail ---------------
# The mutation rewrites the ONE line that holds the rule and REFUSES to write a
# copy when that line is not found exactly once, so a refactor of the artefact
# makes this control red instead of silently becoming a no-op.
mutate() { # $1 = source, $2 = destination, $3 = replacement pattern literal
  python3 - "$1" "$2" "$3" <<'PY'
import re
import sys

src, dst, pattern = sys.argv[1], sys.argv[2], sys.argv[3]
with open(src, encoding="utf-8") as fh:
    text = fh.read()
# The replacement is a FUNCTION, not a template string: `re.sub` processes
# backslash escapes in a replacement template, so a template would have written
# the rule's own `\b` into the copy as a literal BACKSPACE — a mutant that is not
# the rule under test, and a check that cannot see it (measured: the first
# version of this control reported "the defective rule ACCEPTED the template").
new, count = re.subn(
    r"(?m)^mk_pattern=.*$",
    lambda _match: 'mk_pattern="%s"' % pattern,
    text,
)
if count != 1:
    sys.stderr.write("mutate: expected exactly one mk_pattern assignment, found %d\n" % count)
    sys.exit(1)
with open(dst, "w", encoding="utf-8") as fh:
    fh.write(new)
PY
}

# The rule #804 measured: the token branch carries a trailing boundary and no
# leading one, so it matches the tail of the template's run of X.
defect="($M_1|$M_2|$M_3|$M_T)\\b"
if mutate scripts/check-docs.sh "$scratch/scripts/mutant-defect.sh" "$defect"; then
  bash "$scratch/scripts/mutant-defect.sh" --markers "$fx/template.sh" > "$scratch/mutant-defect.out" 2>&1
  mutant_defect_rc=$?
  if [ "$mutant_defect_rc" -eq 0 ]; then
    echo "check-marker-scan: FAIL — the DEFECTIVE rule ACCEPTED the template; 'the template is accepted' cannot fail" >&2
    fail=1
  elif grep -qF "$fx/template.sh" "$scratch/mutant-defect.out"; then
    echo "  OK  the DEFECTIVE rule refuses the template, by name — half 1 is not vacuous"
  else
    echo "check-marker-scan: FAIL — the mutant went non-zero without naming the template" >&2
    sed 's/^/    /' "$scratch/mutant-defect.out" >&2
    fail=1
  fi
else
  echo "check-marker-scan: FAIL — could not mutate the artefact: the rule literal was not found exactly once" >&2
  fail=1
fi

if mutate scripts/check-docs.sh "$scratch/scripts/mutant-vacuous.sh" 'ZZQ_this_rule_matches_nothing'; then
  bash "$scratch/scripts/mutant-vacuous.sh" --markers "$fx/one.sh" "$fx/two.sh" "$fx/three.sh" > "$scratch/mutant-vacuous.out" 2>&1
  mutant_vacuous_rc=$?
  if [ "$mutant_vacuous_rc" -eq 0 ]; then
    echo "  OK  a rule that matches NOTHING accepts them — half 2 is not vacuous"
  else
    echo "check-marker-scan: FAIL — a rule matching nothing still refused a marker (rc $mutant_vacuous_rc)" >&2
    sed 's/^/    /' "$scratch/mutant-vacuous.out" >&2
    fail=1
  fi
else
  echo "check-marker-scan: FAIL — could not mutate the artefact: the rule literal was not found exactly once" >&2
  fail=1
fi

# --- 5. the wiring: this check must actually run in `make verify` ----------
wired="$(bash -c 'source scripts/discover-checks.sh; discover_check_scripts' 2>/dev/null || true)"
# Bash-native (#868): a piped `grep -q` under `set -o pipefail` reports ABSENT for
# text that is PRESENT once the report passes the pipe buffer, and in this polarity
# that skips the branch which proves the check is wired — it fails OPEN.
wired_ok=0
case "$wired" in
  *'marker-scan|bash scripts/check-marker-scan.sh'*) wired_ok=1 ;;
esac
if grep -q 'discover-checks.sh' scripts/verify.sh && [ "$wired_ok" -eq 1 ]; then
  echo "  OK  scripts/verify.sh discovers this check (marker-scan) — wired the moment it lands"
else
  echo "check-marker-scan: FAIL — scripts/verify.sh does not discover this check; it would be inert" >&2
  fail=1
fi

if grep -qE '^[[:space:]]*(marker-scan|check-marker-scan\.sh)[[:space:]]*$' scripts/check-denylist.txt 2>/dev/null; then
  echo "check-marker-scan: FAIL — this check is DENYLISTED by name, so the provocation would never run" >&2
  fail=1
else
  echo "  OK  this check is not denylisted"
fi

if [ "$fail" -ne 0 ]; then
  echo "check-marker-scan: FAIL — see the named failure(s) above" >&2
  exit 1
fi
echo "check-marker-scan: OK — both halves of the marker rule are proven against the artefact"
exit 0
