#!/usr/bin/env bash
# check-gate-status.sh — prove the gate of record can become a REQUIRED check
# without GitHub Actions (ADR-0028, #803 P0-2).
#
# THE QUESTION THIS ANSWERS
# GitHub's *required status checks* are the only mechanism that makes a merge
# impossible without green evidence. Producing one needs a check-run or a commit
# status -- and GR-15 bans the Actions that would normally produce it. ADR-0028
# resolves that with a commit status posted by the code-native runner. This gate
# proves the mechanism is sound WITHOUT writing to the repository.
#
# WHY IT IS PROVOKED RATHER THAN ASSERTED
# The mapping rc -> status is the whole control. Three ways it can be a formality:
#
#   1. it always returns `success`            -> a red gate merges
#   2. CANNOT-ASSESS collapses into `success` -> a false green (the #739 class:
#      an unreadable HEAD that read back as `healthy`)
#   3. an unknown rc is silently defaulted    -> a control that cannot fail
#
# So the offline provocation asserts all three: every outcome maps, the three
# outcomes stay DISTINGUISHABLE, and an unknown outcome is REFUSED. It drives
# the same mapper (scripts/gate-status-map.py) the poster uses.
#
# The live half is a read-back, and an unobservable read-back is CANNOT-ASSESS
# (exit 2) -- never a pass.
#
# Exit codes: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-gate-status.sh
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

MAPPER="scripts/gate-status-map.py"
POSTER="scripts/gate-status.sh"
POLICY="governance/platform/branch-protection.yaml"
fail=0

command -v python3 >/dev/null 2>&1 || { echo "check-gate-status: CANNOT-ASSESS — python3 not found" >&2; exit 2; }

# 1. STRUCTURAL — both halves exist, and the context has ONE name.
[ -f "$MAPPER" ] || { echo "check-gate-status: FAIL — mapper missing: $MAPPER" >&2; exit 1; }
[ -f "$POSTER" ] || { echo "check-gate-status: FAIL — poster missing: $POSTER" >&2; exit 1; }

context_poster="$(grep -oE 'CONTEXT="\$\{AO_GATE_CONTEXT:-[^}]+\}"' "$POSTER" 2>/dev/null | sed 's/.*:-//;s/}"//')"
if [ -z "$context_poster" ]; then
  echo "check-gate-status: FAIL — the poster declares no context name" >&2
  fail=1
fi
# A SECOND, differing context name is how a required check becomes unsatisfiable:
# branch protection would require one name while the poster writes another, and
# nothing would ever satisfy it.
#
# The policy is PARSED, not grepped. A grep for `required_status_contexts:` finds
# the KEY, not the list under it, and comparing a key against a context name
# reports a false drift -- measured here: the first version of this check failed
# with "policy: required_status_contexts:" against "poster: ao/gate-of-record",
# which compares nothing to nothing. A control that fires on its own extraction
# bug trains the operator to ignore it.
declared_context="$(python3 - "$POLICY" <<'PY'
import sys
try:
    import yaml
except ImportError:
    sys.exit(0)
try:
    policy = yaml.safe_load(open(sys.argv[1])) or {}
except OSError:
    sys.exit(0)
contexts = policy.get("required_status_contexts") or []
if isinstance(contexts, str):
    contexts = [contexts]
print(" ".join(str(c) for c in contexts))
PY
)"
if [ -n "$declared_context" ] && [ -n "$context_poster" ]; then
  if ! printf '%s' "$declared_context" | grep -qw "$context_poster"; then
    echo "check-gate-status: FAIL — the policy declares a context the poster does not write" >&2
    echo "    policy declares: $declared_context" >&2
    echo "    poster writes:   $context_poster" >&2
    fail=1
  else
    echo "  OK  the policy and the poster name the SAME context: $context_poster"
  fi
fi

# 2. PROVOKED — the mapping is exhaustive, distinguishable, and refuses the unknown.
if ! python3 "$MAPPER" --self-test; then
  echo "check-gate-status: FAIL — the rc -> status mapping is defective (see above)" >&2
  fail=1
fi

# 2b. The three outcomes must map to three DIFFERENT states. Stated separately
#     from the self-test so that a regression names itself twice, not once.
declare -A WANT=( [0]=success [1]=failure [2]=error )
for rc in 0 1 2; do
  got="$(python3 "$MAPPER" "$rc" 2>/dev/null)"
  if [ "$got" != "${WANT[$rc]}" ]; then
    echo "check-gate-status: FAIL — rc $rc mapped to '$got', expected '${WANT[$rc]}'" >&2
    fail=1
  fi
done

# 2c. An unknown outcome is REFUSED (exit 2), never defaulted to a state.
python3 "$MAPPER" 3 >/dev/null 2>&1
rc=$?
if [ $rc -ne 2 ]; then
  echo "check-gate-status: FAIL — an unknown gate outcome was NOT refused (rc=$rc, expected 2)" >&2
  fail=1
else
  echo "  OK  an unknown gate outcome is REFUSED, not defaulted to a status"
fi

# 2d. CANNOT-ASSESS is published as `error`. Asserted by name: this is the exact
#     collapse that would turn an unassessable gate into a green merge.
ca="$(python3 "$MAPPER" 2 2>/dev/null)"
if [ "$ca" = "success" ]; then
  echo "check-gate-status: FAIL — CANNOT-ASSESS maps to 'success': a false green" >&2
  fail=1
else
  echo "  OK  CANNOT-ASSESS is published as '$ca' (never a pass)"
fi

# 3. DRY-RUN — the poster builds the right request for each outcome, WITHOUT
#    writing to the repository. This is what makes the gate safe to run inside
#    `make verify`: proving the poster must not post on every gate run.
if [ -f "$POSTER" ]; then
  for rc in 0 1 2; do
    out="$(bash "$POSTER" dry-run --sha 0000000000000000000000000000000000000000 --rc "$rc" 2>&1)"
    if ! printf '%s' "$out" | grep -q "state       = ${WANT[$rc]}"; then
      echo "check-gate-status: FAIL — dry-run for rc $rc did not build state=${WANT[$rc]}" >&2
      printf '%s\n' "$out" | sed 's/^/    /' >&2
      fail=1
    fi
  done
  echo "  OK  the poster builds the correct request for all three outcomes (dry-run, nothing written)"

  # 3b. The poster must REFUSE an unknown outcome too, not just the mapper.
  bash "$POSTER" dry-run --sha 0000000000000000000000000000000000000000 --rc 7 >/dev/null 2>&1
  rc=$?
  if [ $rc -ne 2 ]; then
    echo "check-gate-status: FAIL — the poster did not refuse an unknown rc (rc=$rc, expected 2)" >&2
    fail=1
  fi
fi

# 4. LIVE read-back — is this commit actually carrying the status? Offline is
#    CANNOT-ASSESS (2), never a pass.
#
#    Read back the commit the ADR's mechanism was proven on. Absence is NOT a
#    failure here: a commit that predates the poster is legitimately ungated, and
#    the context is not yet required (that is a separate, deliberate step --
#    requiring a context nothing posts would deadlock every merge, which is
#    exactly the #724 defect: an inert control with a gate that could not see it).
live_rc=0
if ! command -v gh >/dev/null 2>&1; then
  echo "  CANNOT-ASSESS gh-unauthenticated: gh is not installed — install gh and run 'gh auth login'"
  live_rc=2
elif ! gh auth status >/dev/null 2>&1; then
  echo "  CANNOT-ASSESS gh-unauthenticated: gh is installed but not authenticated — run 'gh auth login'"
  live_rc=2
elif bash "$POSTER" show --sha "$(git rev-parse HEAD)" >/tmp/cgs-live.log 2>&1; then
  echo "  OK  the LIVE read-back found $context_poster on this commit"
else
  rc=$?
  case "$rc" in
    1) echo "  note  no $context_poster status on HEAD — expected until the poster runs in the runner" ;;
    2) echo "  CANNOT-ASSESS  the status could NOT be read back (API unreachable) — not a pass"
       live_rc=2 ;;
    *) echo "  note  read-back returned $rc" ;;
  esac
fi

if [ "$fail" -ne 0 ]; then
  echo "check-gate-status: NOT-OK — the mapping or the poster is defective" >&2
  exit 1
fi
if [ "$live_rc" -eq 2 ]; then
  echo "check-gate-status: CANNOT-ASSESS — mapping and poster are sound, but the LIVE status was NOT observed"
  exit 2
fi
echo "check-gate-status: OK — the mapping is exhaustive, provoked and the poster builds without writing"
exit 0
