#!/usr/bin/env bash
# check-finops-kpi.sh — FinOps prompt-byte-ceiling gate (CFO office,
# docs/cfo/PROMPT-REDUCTION-PLAN.md).
#
# Measures the injected-prompt byte budget this repo can actually see: the
# sum of `wc -c` over the repo-tracked instruction files named in
# registry/personas/offices/cfo/cost-policy.yaml (`kpi.files`), and fails if
# that sum exceeds `kpi.ceilingBytes`. It deliberately does NOT claim to
# measure harness-injected surfaces (skill listings, MCP instruction blocks,
# tool schemas) — see docs/cfo/PROMPT-REDUCTION-PLAN.md "Headline finding"
# for why no file-based gate can reach those.
#
# Exit-code contract (guardrails/honesty tri-state, issue #28): 0 OK / 1
# NOT-OK / 2 CANNOT-ASSESS. Missing policy file or missing tracked file is
# CANNOT-ASSESS, never a pass; over ceiling is NOT-OK.
#
# Usage: bash scripts/check-finops-kpi.sh
#
# ---knowledge---
# module_id: scripts.check-finops-kpi
# system: governance
# app: gates
# solution_class: enterprise
# patterns: [self-proving-gate, ratchet-metric]
# derives_from: null
# owner_sme: platform-sme
# tier: L1
# interfaces: [exit 0 OK, exit 1 NOT-OK, exit 2 CANNOT-ASSESS]
# invariants: "a missing policy file or missing tracked file is CANNOT-ASSESS, never a pass"
# gotchas: "dependency-free grep/sed parse of cost-policy.yaml -- no PyYAML"
# related: ["#1580"]
# do_not_duplicate: null
# ---knowledge---
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

policy="registry/personas/offices/cfo/cost-policy.yaml"
if [ ! -f "$policy" ]; then
  echo "check-finops-kpi: CANNOT-ASSESS — $policy not found" >&2
  exit 2
fi

# Extract the file list and ceiling without a YAML dependency: the policy's
# `kpi.files` block and `ceilingBytes` scalar are simple enough to parse with
# grep/sed (kept intentionally dependency-free so this check has no
# CANNOT-ASSESS path on a missing PyYAML).
mapfile -t files < <(sed -n '/^  files:/,/^  [a-zA-Z]/p' "$policy" | sed -n 's/^ *- *//p')
ceiling="$(sed -n 's/^ *ceilingBytes: *//p' "$policy" | head -n1)"

if [ "${#files[@]}" -eq 0 ] || [ -z "${ceiling:-}" ]; then
  echo "check-finops-kpi: CANNOT-ASSESS — could not parse kpi.files/ceilingBytes from $policy" >&2
  exit 2
fi

total=0
missing=0
for f in "${files[@]}"; do
  if [ ! -f "$f" ]; then
    echo "check-finops-kpi: CANNOT-ASSESS — tracked file '$f' (from $policy) not found" >&2
    missing=1
    continue
  fi
  bytes="$(wc -c < "$f" | tr -d ' ')"
  total=$((total + bytes))
done

if [ "$missing" -eq 1 ]; then
  exit 2
fi

echo "check-finops-kpi: measured ${total} bytes across ${#files[@]} file(s), ceiling ${ceiling}"

if [ "$total" -gt "$ceiling" ]; then
  echo "check-finops-kpi: NOT-OK — injected-prompt byte budget grew (${total} > ${ceiling}); update cost-policy.yaml ceilingBytes only with a documented reason, never silently" >&2
  exit 1
fi

exit 0
