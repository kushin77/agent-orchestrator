#!/usr/bin/env bash
# Gate orchestrator + attestation for `make verify` / `make gate` (GR-12).
#
# Runs every check in order, tees the full transcript to .verify/verify.log,
# writes .verify/attestation.json (timestamp, host, git sha, branch,
# per-check results, overall exit code) and exits with the REAL aggregate exit
# code. A check that cannot fail is a formality and is rejected
# (no-false-green doctrine). No network and no containers are required.
#
# Usage: scripts/verify.sh [verify|gate]
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 1

mode="${1:-verify}"

verify_dir="$root/.verify"
mkdir -p "$verify_dir"
log="$verify_dir/verify.log"
results_tsv="$verify_dir/.results.tsv"
: > "$log"
: > "$results_tsv"

# name|command — every command is an honest gate (real exit code, can fail).
checks=(
  'shell-syntax|bash scripts/check-shell-syntax.sh'
  'yaml-lint|python3 scripts/check-yaml.py'
  'json-lint|bash scripts/check-json.sh'
  'docs-lint|bash scripts/check-docs.sh'
  'secrets|bash scripts/check-secrets.sh'
  'feature-flags|python3 scripts/check-feature-flags.py'
  'cloudbuild|bash scripts/check-cloudbuild.sh'
  'terraform|bash scripts/check-terraform.sh'
)

overall=0
for entry in "${checks[@]}"; do
  name="${entry%%|*}"
  cmd="${entry#*|}"
  printf '\n== %s ==\n' "$name" | tee -a "$log"
  bash -c "$cmd" 2>&1 | tee -a "$log"
  rc="${PIPESTATUS[0]}"
  printf '%s\t%s\n' "$name" "$rc" >> "$results_tsv"
  if [ "$rc" -ne 0 ]; then
    overall=1
  fi
done

# --- attestation ------------------------------------------------------------
export ATTEST_DIR="$verify_dir"
export ATTEST_TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
export ATTEST_SHA="$(git rev-parse HEAD 2>/dev/null || echo unknown)"
export ATTEST_BRANCH="$(git branch --show-current 2>/dev/null || echo unknown)"
export ATTEST_HOST="$(hostname 2>/dev/null || echo unknown)"
export ATTEST_RESULT="$overall"
export ATTEST_MODE="$mode"
export ATTEST_RESULTS_TSV="$results_tsv"
python3 - <<'PY'
import json, os

attest_dir = os.environ["ATTEST_DIR"]
results_tsv = os.environ["ATTEST_RESULTS_TSV"]

checks = []
with open(results_tsv, encoding="utf-8") as fh:
    for line in fh:
        line = line.rstrip("\n")
        if not line:
            continue
        name, rc = line.split("\t", 1)
        checks.append({"name": name, "rc": int(rc), "status": "PASS" if rc == "0" else "FAIL"})

overall = int(os.environ["ATTEST_RESULT"])
attestation = {
    "gate": "verify",
    "mode": os.environ["ATTEST_MODE"],
    "result": "PASS" if overall == 0 else "FAIL",
    "exit_code": overall,
    "timestamp": os.environ["ATTEST_TS"],
    "host": os.environ["ATTEST_HOST"],
    "git_sha": os.environ["ATTEST_SHA"],
    "branch": os.environ["ATTEST_BRANCH"],
    "check_count": len(checks),
    "checks": checks,
}
path = os.path.join(attest_dir, "attestation.json")
with open(path, "w", encoding="utf-8") as fh:
    json.dump(attestation, fh, indent=2)
    fh.write("\n")
PY

# --- summary ----------------------------------------------------------------
passed=0
total=0
for entry in "${checks[@]}"; do
  name="${entry%%|*}"
  total=$((total + 1))
  rc="$(awk -F'\t' -v n="$name" '$1==n {print $2}' "$results_tsv" | head -1)"
  if [ "${rc:-1}" = "0" ]; then
    passed=$((passed + 1))
  fi
done

echo ""
if [ "$overall" -eq 0 ]; then
  echo "verify: PASS ($passed of $total checks)"
  echo "attestation: $verify_dir/attestation.json"
  if [ "$mode" = "gate" ]; then
    echo "GATE: PASS"
  fi
else
  echo "verify: FAIL ($((total - passed)) of $total checks failed)" >&2
  echo "attestation: $verify_dir/attestation.json" >&2
  if [ "$mode" = "gate" ]; then
    echo "GATE: FAIL" >&2
  fi
fi

exit "$overall"
