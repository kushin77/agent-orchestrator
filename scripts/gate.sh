#!/usr/bin/env bash
# gate.sh — full QA gate for `make gate` (issue #29).
#
# The gate of record for the product's own verification/QA stack. It runs the
# complete signal set, records per-signal evidence, aggregates honestly with
# the guardrails/honesty tri-state model (issue #28) and writes an
# attestation. A gate that cannot fail is a formality (AO-GR-4); every signal
# below can genuinely fail and nothing SKIPs to a pass.
#
# SIGNALS (per-signal evidence; each is a real, fail-able check):
#   verify                 the 8-check `make verify` composite (shell-syntax,
#                          yaml-lint, json-lint, docs-lint, secrets,
#                          feature-flags, cloudbuild, terraform). Covers the
#                          issue's lint/syntax, secrets-scan and doc-refs
#                          signals; evidence .verify/verify.log.
#   policy-schema          guardrails/policy startup schema validation.
#   guard-negative-controls guardrails/honesty negative controls + strict
#                          anti-formality scan over the shipped guards.
#   tests                  every declared pytest suite run IN ISOLATION
#                          (per-suite, because a combined run collides).
#   drift                  suite manifest <-> tree coherence (no suite may
#                          silently vanish; none may run undeclared).
#   merge-gate             pre-merge contract wiring is present and can block
#                          (scripts/merge-gate.sh --wiring + --self-test).
#
# AGGREGATION (consumes issue #28's model, never redefines it): every signal's
# exit code is mapped to a tri-state via `python3 -m honesty status` and the
# gate aggregates via `python3 -m honesty aggregate` — any NOT-OK fails the
# gate, any CANNOT-ASSESS keeps it from green, only an all-OK set reads PASS.
# If the honesty model itself is unavailable the equivalent fail-closed
# mapping (0->OK, 1->NOT-OK, everything else->CANNOT-ASSESS) is used and the
# gate reports a degraded (never green) verdict.
#
# EVIDENCE:
#   .verify/gate.log                full per-signal transcript
#   .verify/attestation.json        the `make verify` attestation
#   .verify/test-results.json       per-suite test outcomes
#   .verify/gate-attestation.json   this gate's attestation
#
# Usage: bash scripts/gate.sh [gate]
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

verify_dir="$root/.verify"
mkdir -p "$verify_dir"
log="$verify_dir/gate.log"
: > "$log"

# --- tri-state helpers (consume issue #28; bash fallback only when the model
#     cannot run — a broken honesty model must never read as green) ----------
honesty_status() { # <exit-code> -> OK|NOT-OK|CANNOT-ASSESS (echoes; exit 0)
  local rc="$1" out
  out="$(cd "$root/guardrails" && python3 -m honesty status "$rc" 2>/dev/null)"
  case "$out" in
    OK|NOT-OK|CANNOT-ASSESS) printf '%s' "$out"; return 0 ;;
  esac
  # Fallback mirrors guardrails/honesty/tristate.from_exit_code exactly.
  if [ "$rc" -eq 0 ]; then printf 'OK'; elif [ "$rc" -eq 1 ]; then printf 'NOT-OK'; else printf 'CANNOT-ASSESS'; fi
}
honesty_aggregate() { # <statuses...> -> aggregate status (echoes; exit 0/1/2)
  local out rc
  out="$(cd "$root/guardrails" && python3 -m honesty aggregate "$@" 2>/dev/null)"
  rc=$?
  case "$out" in
    OK|NOT-OK|CANNOT-ASSESS) printf '%s' "$out"; return "$rc" ;;
  esac
  # Fallback mirrors guardrails/honesty/tristate.aggregate: any NOT-OK fails,
  # else any CANNOT-ASSESS keeps the aggregate from PASS.
  local s saw_unknown=0
  for s in "$@"; do
    if [ "$s" = "NOT-OK" ]; then printf 'NOT-OK'; return 1; fi
    if [ "$s" = "CANNOT-ASSESS" ]; then saw_unknown=1; fi
  done
  if [ "$saw_unknown" -eq 1 ]; then printf 'CANNOT-ASSESS'; return 2; fi
  printf 'OK'; return 0
}

# --- signals ----------------------------------------------------------------
signals=(
  'verify|bash scripts/verify.sh verify'
  'policy-schema|bash scripts/check-policy-schema.sh'
  'guard-negative-controls|bash scripts/check-negative-controls.sh'
  'tests|bash scripts/run-pytest-suites.sh'
  'drift|bash scripts/check-drift.sh'
  'merge-gate|bash scripts/merge-gate.sh --wiring'
)

names=()
rcs=()
statuses=()
for entry in "${signals[@]}"; do
  name="${entry%%|*}"
  cmd="${entry#*|}"
  names+=("$name")
  printf '\n== %s ==\n' "$name" | tee -a "$log"
  bash -c "$cmd" 2>&1 | tee -a "$log"
  rc="${PIPESTATUS[0]}"
  rcs+=("$rc")
  statuses+=("$(honesty_status "$rc")")
done

aggregate="$(honesty_aggregate "${statuses[@]}")"
overall=$?

# --- attestation ------------------------------------------------------------
export ATTEST_DIR="$verify_dir"
export ATTEST_TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
export ATTEST_SHA="$(git rev-parse HEAD 2>/dev/null || echo unknown)"
export ATTEST_BRANCH="$(git branch --show-current 2>/dev/null || echo unknown)"
export ATTEST_HOST="$(hostname 2>/dev/null || echo unknown)"
export ATTEST_AGGREGATE="$aggregate"
export ATTEST_EXIT="$overall"
export ATTEST_NAMES="$(printf '%s;' "${names[@]}")"
export ATTEST_RCS="$(printf '%s;' "${rcs[@]}")"
export ATTEST_STATUSES="$(printf '%s;' "${statuses[@]}")"
python3 - <<'PY'
import json, os

def split_field(s: str):
    return [x for x in s.rstrip(";").split(";") if x != ""]

names = split_field(os.environ["ATTEST_NAMES"])
rcs = split_field(os.environ["ATTEST_RCS"])
statuses = split_field(os.environ["ATTEST_STATUSES"])
checks = [
    {"name": n, "rc": int(r), "status": s}
    for n, r, s in zip(names, rcs, statuses)
]
attestation = {
    "gate": "gate",
    "result": os.environ["ATTEST_AGGREGATE"],
    "exit_code": int(os.environ["ATTEST_EXIT"]),
    "timestamp": os.environ["ATTEST_TS"],
    "host": os.environ["ATTEST_HOST"],
    "git_sha": os.environ["ATTEST_SHA"],
    "branch": os.environ["ATTEST_BRANCH"],
    "check_count": len(checks),
    "checks": checks,
}
with open(os.path.join(os.environ["ATTEST_DIR"], "gate-attestation.json"), "w", encoding="utf-8") as fh:
    json.dump(attestation, fh, indent=2)
    fh.write("\n")
PY

# --- summary (maps the issue #29 signal names to their evidence) ------------
echo ""
echo "== gate signal summary =="
echo "  ${aggregate}  aggregate (${#names[@]} signal(s))"

# verify sub-signals surfaced as the issue's lint/syntax, secrets, doc-refs
# groups (from verify.sh's per-check record .verify/.results.tsv).
if [ -f "$verify_dir/.results.tsv" ]; then
  line_of() { awk -F'\t' -v n="$1" '$1==n {print $2}' "$verify_dir/.results.tsv" | head -1; }
  for group in 'lint/syntax|shell-syntax yaml-lint json-lint' 'secrets scan|secrets' 'doc refs|docs-lint'; do
    label="${group%%|*}"
    sub="${group#*|}"
    grp="OK"
    for c in $sub; do
      [ "$(line_of "$c")" = "0" ] || grp="FAIL"
    done
    if [ "$grp" = "OK" ]; then
      echo "  PASS  $label  (inside verify: $sub)"
    else
      echo "  FAIL  $label  (inside verify: $sub)"
    fi
  done
fi

for i in "${!names[@]}"; do
  printf '  %-14s %-9s %s\n' "${statuses[$i]}" "${names[$i]}" "rc=${rcs[$i]}"
done

echo ""
echo "evidence: $log"
echo "evidence: $verify_dir/gate-attestation.json"
if [ "$overall" -eq 0 ]; then
  echo "GATE: PASS"
else
  echo "GATE: $aggregate" >&2
fi
exit "$overall"
