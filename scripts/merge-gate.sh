#!/usr/bin/env bash
# merge-gate.sh — the pre-merge contract for agent-orchestrator (issue #29).
#
# Wires the AO-GR-11/GR-12 doctrine ("merge only on green verify with actual
# output attached") into one executable a PR must pass before it may merge.
# A merge without evidence is rejected; a merge whose gate reports
# CANNOT-ASSESS is a merge without a verdict and is equally rejected.
#
# CONTRACT (run, the default):
#   1. REFUSE on a dirty working tree — an attestation names a COMMIT, and a
#      tree that is not committed is not what anyone else will ever fetch
#      (gate-attest doctrine). Commit first, then gate the commit.
#   2. verify        — scripts/verify.sh verify       (8-check composite)
#   3. drift         — scripts/check-drift.sh          (suite manifest coherence)
#   4. tests         — scripts/run-pytest-suites.sh    (every suite, isolated)
#   5. negative-ctls — scripts/check-negative-controls.sh (guard honesty)
#   6. policy-schema — scripts/check-policy-schema.sh
#   7. pr-contract   — scripts/check-pr-contract.sh     (Refs trailer +
#      Closes/AI-assistance/pre-existing-red). Runs ONLY when a PR body exists:
#      set AO_PR_NUMBER (read the body + range via gh) or AO_PR_BODY_FILE +
#      AO_PR_RANGE. With neither set the signal is OMITTED (with a note), never
#      skipped-to-green — an ordinary working checkout has no PR body, and
#      wiring that into every local run would false-red it (issue #311).
#   8. On green, write .verify/merge-attestation.json naming THIS commit.
#
#   Exit 0 = MERGE-GATE PASS (all signals OK, attestation written).
#   Exit 1 = NOT-OK (a signal failed, or the tree is dirty).
#   Exit 2 = CANNOT-ASSESS (a signal reached no verdict — never a pass).
#
# OTHER MODES:
#   --wiring     integrity check used by `make gate`: every gate component
#                exists/executable AND merge-gate can block (--self-test).
#   --self-test  negative control: proves the merge gate refuses when it must
#                (simulated dirty tree -> run() must exit nonzero).
#
# Usage:
#   bash scripts/merge-gate.sh             # full pre-merge contract
#   bash scripts/merge-gate.sh --wiring    # gate-wiring integrity check
#   bash scripts/merge-gate.sh --self-test # negative control
set -u

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 2

verify_dir="$root/.verify"
mkdir -p "$verify_dir"

mode="${1:-run}"

# --- shared helpers ---------------------------------------------------------
log_and_run() { # <label> <cmd...>  -> rc
  local label="$1"; shift
  echo "== merge-gate: $label =="
  bash -c "$*" 2>&1
  return $?
}

rc_to_status() { # mirrors guardrails/honesty/tristate.from_exit_code
  case "$1" in
    0) printf 'OK' ;;
    1) printf 'NOT-OK' ;;
    *) printf 'CANNOT-ASSESS' ;;
  esac
}

tree_is_dirty() { # -> 0 dirty, 1 clean  (MERGE_GATE_FORCE_DIRTY=1 forces dirty)
  [ "${MERGE_GATE_FORCE_DIRTY:-0}" = "1" ] && return 0
  [ -n "$(git status --porcelain 2>/dev/null)" ]
}

# A gate PARK is box contention, not a verdict (issue #956). `scripts/verify.sh
# verify` exits 10 (this worktree already held), 11 (box-wide cap reached) or 12
# (permit store unusable) BEFORE running a single check and without touching any
# attestation. A merge gate that reads that as a failure refuses a green lane
# merely because the box was busy. So the verify step is retried, bounded and
# with a fixed wait (never exponential), and a real verdict (0 PASS / 1 NOT-OK)
# or a genuine CANNOT-ASSESS (anything other than 10/11/12) is returned
# immediately. After the budget the last PARK code is returned, which the step
# maps to CANNOT-ASSESS — a merge never proceeds on a PARK (no-false-green).
run_verify_step() { # -> scripts/verify.sh's own exit code
  local attempt rc
  local max_attempts="${MERGE_GATE_VERIFY_ATTEMPTS:-8}"
  local wait_seconds="${MERGE_GATE_VERIFY_WAIT:-20}"
  rc=11
  for attempt in $(seq 1 "$max_attempts"); do
    bash scripts/verify.sh verify
    rc=$?
    if [ "$rc" -eq 0 ] || [ "$rc" -eq 1 ]; then
      return "$rc"
    fi
    if [ "$rc" -ne 10 ] && [ "$rc" -ne 11 ] && [ "$rc" -ne 12 ]; then
      return "$rc"
    fi
    # PARK (10/11/12): contention, retry with a fixed wait (AO-GR-21: bounded).
    if [ "$attempt" -lt "$max_attempts" ]; then
      sleep "$wait_seconds"
    fi
  done
  return "$rc"
}

# --- run (default): the full pre-merge contract ------------------------------
run_contract() {
  if tree_is_dirty; then
    echo "merge-gate: REFUSED — the working tree is dirty." >&2
    echo "  An attestation names a COMMIT. Commit your changes first, then run the merge gate on the commit." >&2
    exit 1
  fi

  sha="$(git rev-parse HEAD 2>/dev/null || echo unknown)"
  branch="$(git branch --show-current 2>/dev/null || echo unknown)"
  ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

  # each step exits 0 (OK) / 1 (NOT-OK) / 2 (CANNOT-ASSESS) per issue #28
  declare -a names=() statuses=() rcs=()
  local overall=0
  step() { # <label> <cmd...>
    local label="$1"; shift
    names+=("$label")
    "$@" 2>&1 | tee -a "$verify_dir/merge-gate.log"
    local rc="${PIPESTATUS[0]}"
    rcs+=("$rc")
    statuses+=("$(rc_to_status "$rc")")
    if [ "$rc" -ne 0 ]; then overall=1; fi
  }

  : > "$verify_dir/merge-gate.log"
  step verify    run_verify_step
  step drift     bash scripts/check-drift.sh
  step tests     bash scripts/run-pytest-suites.sh
  step negative-controls bash scripts/check-negative-controls.sh
  step policy-schema bash scripts/check-policy-schema.sh

  # pr-contract is the PR-time/merge-boundary signal (issue #311). A PR body
  # only exists at that boundary; in an ordinary working checkout there is
  # none, so the signal is OMITTED — never skipped-to-green and never a
  # false-red — unless the caller supplies the PR context.
  if [ -n "${AO_PR_NUMBER:-}" ]; then
    step pr-contract bash scripts/check-pr-contract.sh --pr "$AO_PR_NUMBER"
  elif [ -n "${AO_PR_BODY_FILE:-}" ]; then
    step pr-contract bash scripts/check-pr-contract.sh --body-file "$AO_PR_BODY_FILE" --range "${AO_PR_RANGE:-origin/master..HEAD}"
  else
    echo "== merge-gate: pr-contract omitted — no PR body at this boundary (set AO_PR_NUMBER or AO_PR_BODY_FILE to enforce) =="
  fi

  # aggregate: any NOT-OK fails; any CANNOT-ASSESS keeps from PASS; all OK passes
  local result="OK"
  local s
  for s in "${statuses[@]}"; do
    if [ "$s" = "NOT-OK" ]; then result="NOT-OK"; break; fi
    if [ "$s" = "CANNOT-ASSESS" ]; then result="CANNOT-ASSESS"; fi
  done
  if [ "$result" = "NOT-OK" ]; then exit_code=1
  elif [ "$result" = "CANNOT-ASSESS" ]; then exit_code=2
  else exit_code=0; fi

  # The attestation must say WHO verified (AO-GR-13): the session identity the
  # isolation layer exports, falling back to the OS user — never the git author,
  # because conflating author and verifier is exactly the self-attestation the
  # rule forbids. A run outside a lane still records *some* accountable identity.
  verified_by="${AO_AGENT_ID:-$(id -un 2>/dev/null || echo unknown)}"
  verification_session="${AO_SESSION_ID:-}"

  cat > "$verify_dir/merge-attestation.json" <<EOF
{
  "gate": "merge-gate",
  "result": "$result",
  "exit_code": $exit_code,
  "commit": "$sha",
  "branch": "$branch",
  "timestamp": "$ts",
  "verified_by": "$verified_by",
  "verification_session": "$verification_session",
  "checks": [
$(for i in "${!names[@]}"; do
  printf '    {"name": "%s", "rc": %s, "status": "%s"}' "${names[$i]}" "${rcs[$i]}" "${statuses[$i]}"
  if [ "$i" -lt "$((${#names[@]} - 1))" ]; then printf ','; fi
  printf '\n'
done)  ]
}
EOF

  echo ""
  echo "== merge-gate signal summary =="
  for i in "${!names[@]}"; do
    printf '  %-18s %-14s rc=%s\n' "${statuses[$i]}" "${names[$i]}" "${rcs[$i]}"
  done
  echo ""
  echo "evidence: $verify_dir/merge-attestation.json"
  if [ "$exit_code" -eq 0 ]; then
    echo "MERGE-GATE: PASS ($sha)"
  else
    echo "MERGE-GATE: $result ($sha)" >&2
  fi
  exit "$exit_code"
}

# --- wiring: everything `make gate`/`make merge-gate`/qa-loop needs ----------
wiring_check() {
  local missing=0
  local f
  # Repo convention runs every gate script through `bash <script>` (pre-existing
  # scripts like verify.sh are intentionally not +x), so wiring requires each
  # component to EXIST; runnability is proven functionally by the self-test
  # below and by `make gate` executing every signal end-to-end.
  for f in scripts/gate.sh scripts/verify.sh scripts/run-pytest-suites.sh \
           scripts/check-drift.sh scripts/check-policy-schema.sh \
           scripts/check-negative-controls.sh scripts/qa-loop.sh \
           scripts/merge-gate.sh scripts/check-pr-contract.sh; do
    if [ -f "$f" ]; then
      :
    else
      echo "merge-gate --wiring: MISSING $f" >&2
      missing=$((missing + 1))
    fi
  done
  if [ ! -f scripts/pytest-suites.txt ]; then
    echo "merge-gate --wiring: MISSING scripts/pytest-suites.txt" >&2
    missing=$((missing + 1))
  fi
  if [ "$missing" -gt 0 ]; then
    echo "merge-gate --wiring: FAIL — $missing gate component(s) missing" >&2
    exit 1
  fi
  if bash "$root/scripts/merge-gate.sh" --self-test >/dev/null 2>&1; then
    echo "merge-gate --wiring: OK — all gate components present; merge gate can block"
    exit 0
  fi
  echo "merge-gate --wiring: FAIL — --self-test did not pass (merge gate cannot block)" >&2
  exit 1
}

# --- self-test: negative control ---------------------------------------------
self_test() {
  # A merge gate that never refuses is a formality. Force a dirty tree and
  # require the contract to refuse.
  if MERGE_GATE_FORCE_DIRTY=1 bash "$root/scripts/merge-gate.sh" run >/dev/null 2>&1; then
    echo "merge-gate --self-test: FAIL — contract passed on a dirty tree (no-false-green violation)" >&2
    exit 1
  fi
  echo "merge-gate --self-test: PASS — negative control confirmed (refuses on a dirty tree)"
  exit 0
}

case "$mode" in
  run)         run_contract ;;
  --wiring)    wiring_check ;;
  --self-test) self_test ;;
  *) echo "usage: bash scripts/merge-gate.sh [run|--wiring|--self-test]" >&2; exit 2 ;;
esac
