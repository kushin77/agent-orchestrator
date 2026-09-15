#!/usr/bin/env bash
# check-no-actions.sh — GR-15, ENFORCED (issue #812).
#
# THE RULE
#   AGENTS.md and the CMR fleet doctrine: "No GitHub Actions workflows.
#   Automation is code-native: `make` targets run by the ops runner and cron."
#
# WHY THIS GATE EXISTS
# The rule was declared and enforced by NOTHING. Measured 2026-09-15:
#
#   gh api repos/kushin77/agent-orchestrator/actions/workflows
#     CI Failure Scanner & Auto-Issue Creator | state=active
#     recent runs: 03:11:10Z success, 02:13:33Z success, 01:22:13Z success
#
# The repo had been violating its own doctrine, silently, for as long as that
# workflow existed. No check looked: the scripts that merely *mention*
# `.github/workflows` parse YAML, and `scripts/check-gate-coverage.sh` does not
# treat a workflow as an artifact at all -- so a new workflow was not even
# visible to the registry, let alone refused.
#
# A rule in prose cannot refuse anything. This turns it into a mechanism.
#
# THE PROVOCATION IS THE POINT
# A gate that scans for a pattern and finds nothing is indistinguishable from a
# gate whose scan is broken. So this check does not merely assert "no workflows
# here" -- it PLANTS one and requires the detector to fail BY NAME, then plants a
# baselined one and requires it to be accepted. Asserting only the empty case
# would pass a detector that matches nothing at all.
#
# The provocation writes to a temporary fixture directory (`AO_WORKFLOWS_DIR`),
# never to the real `.github/workflows`, so running `make verify` cannot mutate
# the repository it is checking and cannot leave a stray workflow behind if it is
# interrupted.
#
# Exit codes: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-no-actions.sh
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

WORKFLOWS_DIR="${AO_WORKFLOWS_DIR:-$root/.github/workflows}"
# A deliberate exception must be DECLARED and REASONED, in one place, so it is
# visible rather than silent. Empty by default: GR-15 admits no exception.
BASELINE="${AO_NO_ACTIONS_BASELINE:-$root/scripts/no-actions-baseline.txt}"

fail=0

# The detector. $1 = directory to scan, $2 = baseline file (optional).
# Prints one line per UNBASELINED workflow and returns 1 when any is found.
# Kept as a function so the provocation drives THE SAME code path the real check
# uses -- a proof that exercises a copy proves nothing about the path that runs.
scan() {
  local dir="$1" baseline="$2"
  local found=0
  shopt -s nullglob
  for f in "$dir"/*.yml "$dir"/*.yaml; do
    local name
    name="$(basename "$f")"
    if [ -n "$baseline" ] && [ -f "$baseline" ] && grep -qE "^[[:space:]]*${name//./\\.}([[:space:]]|$)" "$baseline"; then
      continue
    fi
    printf '  UNBASELINED  %s\n' "$f"
    found=$((found + 1))
  done
  shopt -u nullglob
  [ "$found" -eq 0 ]
}

echo "== GR-15: no GitHub Actions workflows =="

# 1. The real repository.
if [ -d "$WORKFLOWS_DIR" ]; then
  if scan "$WORKFLOWS_DIR" "$BASELINE"; then
    echo "  OK  no unbaselined workflow in $WORKFLOWS_DIR"
  else
    printf 'check-no-actions: FAIL — the workflows above violate GR-15;\n' >&2
    printf '                  GR-15 admits automation only as code-native `make` targets run by the\n' >&2
    printf '                  ops runner and cron. Retire the workflow, or declare it in %s\n' "$BASELINE" >&2
    printf '                  with a reason.\n' >&2
    fail=1
  fi
else
  echo "  OK  $WORKFLOWS_DIR does not exist (no workflows can be present)"
fi

# 2. PROVOKED — plant a workflow and require the detector to refuse it BY NAME.
fixture="$(python3 -c 'import tempfile; print(tempfile.mkdtemp(prefix="noactions-"))')" || exit 2
trap 'rm -rf "$fixture"' EXIT

cat > "$fixture/planted-workflow.yml" <<'YAML'
# Planted by scripts/check-no-actions.sh to PROVE the detector fires.
name: planted provocation
on: [push]
jobs:
  noop:
    runs-on: ubuntu-latest
    steps:
      - run: "true"
YAML

out="$(scan "$fixture" "$BASELINE" 2>&1)"
rc=$?
if [ "$rc" -eq 0 ]; then
  echo "check-no-actions: FAIL — a PLANTED workflow was NOT detected; this gate cannot fail" >&2
  fail=1
elif ! printf '%s' "$out" | grep -q "UNBASELINED.*planted-workflow.yml"; then
  echo "check-no-actions: FAIL — the planted workflow was detected but NOT NAMED" >&2
  printf '%s\n' "$out" | sed 's/^/    /' >&2
  fail=1
else
  echo "  OK  a PLANTED workflow is refused, by name:"
  printf '%s\n' "$out" | grep "UNBASELINED" | sed 's/^/      /'
fi

# 2b. The baseline half. A DELIBERATE exception must be ACCEPTED, so the gate is
#     a policy and not a blanket refusal -- and so the provocation above cannot
#     pass merely by refusing everything.
printf '%s\n' "planted-workflow.yml  # deliberate: planted by this gate to prove the baseline half" > "$fixture/baseline.txt"
out="$(scan "$fixture" "$fixture/baseline.txt" 2>&1)"
rc=$?
if [ "$rc" -ne 0 ]; then
  echo "check-no-actions: FAIL — a BASELINED workflow was still refused; the baseline is inert" >&2
  printf '%s\n' "$out" | sed 's/^/    /' >&2
  fail=1
else
  echo "  OK  a BASELINED workflow is accepted (the exception path works)"
fi

# 3. The real baseline must not rot into a fiction: an entry naming no workflow
#    is a stale exemption and is reported.
if [ -f "$BASELINE" ]; then
  while IFS= read -r line; do
    case "$line" in ''|\#*) continue ;; esac
    entry="${line%%[[:space:]]*}"
    if [ ! -e "$WORKFLOWS_DIR/$entry" ]; then
      echo "check-no-actions: FAIL — the baseline exempts '$entry', which does not exist (stale entry)" >&2
      fail=1
    fi
  done < "$BASELINE"
fi

[ "$fail" -eq 0 ] || exit 1
echo "check-no-actions: OK — GR-15 holds, and the detector is PROVEN to fire"
exit 0
