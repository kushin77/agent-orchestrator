#!/usr/bin/env bash
# Gate orchestrator + attestation for `make verify` / `make gate` (GR-12).
#
# Runs every check in order, tees the full transcript to .verify/verify.log,
# writes .verify/attestation.json (timestamp, host, git sha, branch,
# per-check results, overall exit code) and exits with the REAL aggregate exit
# code. A check that cannot fail is a formality and is rejected
# (no-false-green doctrine). No network and no containers are required.
#
# The check set includes the declared `fleet` pytest suite (`pytest-fleet`).
# The gate of record must exercise the tests it claims to cover: a red fleet
# suite sat on master undetected because this gate ran no pytest at all — only
# `make gate` / `make tests` did (gate gap, issue #331). The remaining declared
# suites are still exercised only by `make gate`; `governance/lessons` is red
# for an unrelated board-hygiene defect (#312).
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
  'python-syntax|bash scripts/check-python-syntax.sh'
  'yaml-lint|python3 scripts/check-yaml.py'
  'json-lint|bash scripts/check-json.sh'
  'docs-lint|bash scripts/check-docs.sh'
  'chronological-dispatch|bash scripts/check-chronological-dispatch.sh'
  'issue-claims|bash scripts/check-issue-claims.sh'
  'fleet-channel|bash scripts/check-fleet-channel.sh'
  'fleet-contract|bash scripts/check-fleet-contract.sh'
  'fleet-runbook|bash scripts/check-fleet-runbook.sh'
  'session-isolation|bash scripts/check-session-isolation.sh'
  'github-lifecycle|bash scripts/check-github-lifecycle.sh'
  'reconcile|bash scripts/check-reconcile.sh'
  'lease-policy|bash scripts/check-lease-policy.sh'
  'fleet-state|bash scripts/check-fleet-state.sh'
  'paperclip-gap-analysis|bash scripts/check-paperclip-gap-analysis.sh'
  'brain-profile|bash scripts/check-brain-profile.sh'
  'knowledge-index|bash scripts/check-knowledge-index.sh'
  'cross-reference|bash scripts/check-cross-reference.sh'
  'conformance|bash scripts/check-conformance.sh'
  'surface-class|bash scripts/check-surface-class.sh'
  'remediation|bash scripts/check-remediation.sh'
  'board-gate|bash scripts/check-board-gate.sh'
  'cross-repo-boundary|bash scripts/check-cross-repo-boundary.sh'
  # audit-read-model (issue #347): the tamper-evident trail served as a
  # read-only, deterministic, filterable read model; verify-chain is OK on an
  # intact chain and refuses a modified / reordered / removed record, and the
  # check mutates a temp copy of the chain, so it cannot pass vacuously.
  'audit-read-model|bash scripts/check-audit-read-model.sh'
  'gateway-catalog-parity|bash scripts/check-gateway-catalog-parity.sh'
  'guardrail-controls|bash scripts/check-guardrail-controls.sh'
  # agent-identity-parity (issue #346): the shared agent-identity schema's
  # closed vocabularies must equal agent-profile.schema.json + catalog.yaml and
  # every seed must validate as a projected identity; the check mutates its own
  # input, so it cannot pass vacuously.
  'agent-identity-parity|bash scripts/check-agent-identity-parity.sh'
  'paperclip-adapter|bash scripts/check-paperclip-integration-adapter.sh'
  'paperclip-canonical-module|bash scripts/check-paperclip-canonical-module.sh'
  'issue-template|bash scripts/check-issue-template.sh'
  'finops-chooser|bash scripts/check-finops-chooser.sh'
  'lessons|bash scripts/check-lessons.sh'
  'paperclip-integration|bash scripts/check-paperclip-integration.sh'
  'ticket-projection|bash scripts/check-ticket-projection.sh'
  # pmo-rollup (issue #403): the PMO views are derived queries over the ticket
  # graph, never a store — no ledger, no surviving cache, no second source of
  # status; the gate provokes an owner-less risk, a RAID set that disagrees with
  # the graph and a rollup from a stale cache, and proves nothing is written.
  'pmo-rollup|bash scripts/check-pmo-rollup.sh'
  'secrets|bash scripts/check-secrets.sh'
  'feature-flags|python3 scripts/check-feature-flags.py'
  'cloudbuild|bash scripts/check-cloudbuild.sh'
  'terraform|bash scripts/check-terraform.sh'
  # EPIC #144 (per-repo agent fleet) governance surfaces — each is tri-state
  # (0 OK / 1 NOT-OK / 2 CANNOT-ASSESS) and proves its own negative control.
  'cto-overlay|bash scripts/check-cto-overlay.sh'
  'authority|bash scripts/check-authority.sh'
  'rollup|bash scripts/check-rollup.sh'
  'fleet-template|bash scripts/check-fleet-template.sh'
  'sme-routing|bash scripts/check-sme-routing.sh'
  'registry-parity|bash scripts/check-registry-parity.sh'
  # EPIC #422 (cross-repo integration gaps) — each tri-state, each proving its
  # own negative control.
  'module-admission|bash scripts/check-module-admission.sh'
  'routing-seam|bash scripts/check-routing-seam.sh'
  'cross-repo-sync|bash scripts/check-cross-repo-sync.sh'
  'cross-repo-lessons|bash scripts/check-cross-repo-lessons.sh'
  'paperclip-budget|bash scripts/check-paperclip-budget.sh'
  # The declared suite manifest (scripts/pytest-suites.txt) is run in full and in
  # isolation by `make gate` / `make tests`; this gate runs the `fleet` suite the
  # same way run-pytest-suites.sh does, so a red fleet test cannot reach master
  # again. pytest exits non-zero on a collection error or on "no tests collected"
  # (rc 5), so the check has no false-green path.
  'pytest-fleet|env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q fleet/tests'
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
