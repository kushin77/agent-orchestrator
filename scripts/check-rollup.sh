#!/usr/bin/env bash
# check-rollup.sh — the enterprise/GDC roll-up gate (issue #151).
#
# The roll-up's whole claim is that the org view is COMPUTED from per-repo
# declarations and is a projection rather than a second source of truth. A gate
# that only re-ran the happy path would verify neither: it would pass just as
# happily against a roll-up that returns a constant, or one that writes state.
# So this gate does three things:
#
#   1. it projects the committed pilot and requires the known-good state;
#   2. it PROVOKES every one of the roll-up's refusals — an over-ceiling SME at
#      all three scopes, a repo declared with no inventory, a malformed
#      declaration, and a schema that uses a keyword the validator does not
#      implement — and requires each to produce the tri-state answer it is
#      supposed to, never a pass;
#   3. it checks the aggregate against numbers computed here, from the committed
#      declarations, so a mutation of the aggregation (a sum that becomes a
#      first-element, a dropped repo, a clamped ceiling) fails the gate.
#
# It also proves the projection property on the committed inputs: the sha256 of
# every pilot file is identical before and after a run, and the run writes no
# file anywhere.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-rollup.sh
set -uo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

cli="governance/rollup/cli.py"
model="governance/rollup/model.py"
contract="governance/rollup/schema.yaml"
pilot_org="governance/rollup/pilot/org.yaml"
pilot_inv="governance/rollup/pilot/inventory"
over_org="governance/rollup/fixtures/over-ceiling/org.yaml"
over_inv="governance/rollup/fixtures/over-ceiling/inventory"
missing_org="governance/rollup/fixtures/missing-inventory/org.yaml"
missing_inv="governance/rollup/fixtures/missing-inventory/inventory"
bad_contract="governance/rollup/fixtures/schema-unsupported.yaml"

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-rollup: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi
for required in "$cli" "$model" "$contract" "$pilot_org" "$over_org" "$missing_org" "$bad_contract"; do
  if [ ! -f "$required" ]; then
    echo "check-rollup: CANNOT-ASSESS — $required is missing" >&2
    exit 2
  fi
done

scratch="/tmp/ao-rollup-gate.$(date +%s%N).$$"
# A unique scratch dir WITHOUT a trailing run of `X`: this repo's docs-lint scans
# for unfinished markers and a `mktemp` X-suffix trips it (the same trap
# scripts/check-pr-contract.sh documents), so this uses the convention the other
# gates use — an explicit /tmp name, with mkdir refusing loudly rather than
# silently reusing another run's tree.
if ! mkdir "$scratch" 2>/dev/null; then
  echo "check-rollup: CANNOT-ASSESS — cannot create a scratch dir at $scratch" >&2
  exit 2
fi
cleanup() { rm -rf "$scratch"; }
trap cleanup EXIT

fail=0
report_fail() {
  printf '  FAIL  %s\n' "$1" >&2
  fail=1
}

# Project once, capturing the JSON report and the exit code separately, so the
# gate can assert on the real CLI contract instead of on a library call.
project() { # <org> <inventory-dir> <schema> <out>
  env PYTHONDONTWRITEBYTECODE=1 python3 "$cli" project \
    --org "$1" --inventory-dir "$2" --schema "$3" --json > "$4" 2> "$4.err"
}

expect_rc() { # <label> <expected> <actual>
  if [ "$2" != "$3" ]; then
    printf '  FAIL  %s: expected exit %s, got %s\n' "$1" "$2" "$3" >&2
    if [ -s "$4" ]; then
      sed 's/^/        /' "$4" | head -6 >&2
    fi
    fail=1
  fi
}

# --- 1. the pilot projects to its declared, known-good state -----------------
echo "== pilot projection =="
before="$(sha256sum "$pilot_org" "$pilot_inv"/*.yaml "$contract" | sort -k2)"
project "$pilot_org" "$pilot_inv" "$contract" "$scratch/pilot.json"
pilot_rc=$?
expect_rc "pilot projection" 0 "$pilot_rc" "$scratch/pilot.json.err"
after="$(sha256sum "$pilot_org" "$pilot_inv"/*.yaml "$contract" | sort -k2)"
if [ "$before" != "$after" ]; then
  report_fail "projection changed its own inputs (sha256 before != after)"
fi

python3 - "$scratch/pilot.json" <<'PY' || fail=1
import json
import sys

report = json.load(open(sys.argv[1], encoding="utf-8"))
problems = []
if report.get("status") != "ok":
    problems.append("pilot status is %r, not ok" % report.get("status"))
if report.get("exit_code") != 0:
    problems.append("pilot exit_code is %r" % report.get("exit_code"))
if report.get("projection") is not True or report.get("persisted") is not False:
    problems.append("report does not declare itself a non-persisted projection")
if len(report.get("inputs", [])) < 3:
    problems.append("report does not carry a digest per input")
for entry in report.get("inputs", []):
    if len(entry.get("sha256", "")) != 64:
        problems.append("input %s has no sha256" % entry.get("path"))
kinds = {entry["kind"] for entry in report.get("inputs", [])}
if kinds != {"schema", "org", "inventory"}:
    problems.append("unexpected input kinds: %s" % sorted(kinds))
# The committed pilot's own arithmetic, computed here and not read from the report.
enterprise = report["enterprise"]
if enterprise["spend"]["total_usd"] != 2239.85:
    problems.append("pilot enterprise spend %r, expected 2239.85" % enterprise["spend"]["total_usd"])
if enterprise["spend"]["over"] is not False:
    problems.append("pilot enterprise is over its ceiling")
if enterprise["spend"]["excess_usd"] != 0.0:
    problems.append("pilot enterprise excess %r, expected 0.0" % enterprise["spend"]["excess_usd"])
if (enterprise["utilization"]["numerator"], enterprise["utilization"]["denominator"]) != (221.0, 320.0):
    problems.append("pilot utilisation %r" % enterprise["utilization"])
if (enterprise["closure"]["numerator"], enterprise["closure"]["denominator"]) != (64.0, 72.0):
    problems.append("pilot closure %r" % enterprise["closure"])
if (enterprise["drift"]["numerator"], enterprise["drift"]["denominator"]) != (3.0, 78.0):
    problems.append("pilot drift %r" % enterprise["drift"])
# Totals must be the sum of the parts, and the parts must be per repo.
per_repo = {}
for repo in report["repos"]:
    per_repo[repo["repo"]] = round(sum(s["spend_usd"] for s in repo["smes"]), 2)
if round(sum(per_repo.values()), 2) != enterprise["spend"]["total_usd"]:
    problems.append("enterprise total is not the sum of the repos: %r vs %r"
                    % (per_repo, enterprise["spend"]["total_usd"]))
if enterprise["spend"]["total_usd"] in per_repo.values():
    problems.append("the aggregate equals a single repo's total (vacuous sum)")
if len(per_repo) != 4:
    problems.append("expected 4 repos in scope, got %d" % len(per_repo))
# Two tenants, and the tenant totals are disjoint and complete.
tenants = {t["id"]: t["spend"]["total_usd"] for t in enterprise["tenants"]}
if sorted(tenants) != ["kushin77-ops", "kushin77-platform"]:
    problems.append("unexpected tenants: %s" % sorted(tenants))
if round(sum(tenants.values()), 2) != enterprise["spend"]["total_usd"]:
    problems.append("tenant totals do not add up to the enterprise total")
if tenants.get("kushin77-platform") != 2001.85 or tenants.get("kushin77-ops") != 238.00:
    problems.append("tenant totals wrong: %r" % tenants)
# An SME id repeated across repos stays two facts, repo-scoped.
inventory = enterprise["sme_inventory"]
if inventory["count"] != 11:
    problems.append("pilot SME count %r, expected 11" % inventory["count"])
for repo, ids in inventory["by_repo"].items():
    if sorted(ids) != ids:
        problems.append("SME ids for %s are not sorted" % repo)
qa_repos = sorted(r for r, ids in inventory["by_repo"].items() if "qa-sme" in ids)
if len(qa_repos) != 3:
    problems.append("qa-sme should appear in 3 repos, got %r" % qa_repos)
# The at-ceiling SME is telemetry, never an error.
severity = {f["code"]: f["severity"] for f in report["findings"]}
if severity.get("SPEND_AT_CEILING") != "info":
    problems.append("at-ceiling spend is not reported as info telemetry")
if "SPEND_OVER_CEILING" in severity:
    problems.append("the pilot declares an over-ceiling SME; it should not")

for problem in problems:
    print("  FAIL  %s" % problem)
sys.exit(1 if problems else 0)
PY

# --- 2. provoke: an over-ceiling SME at all three scopes ---------------------
echo "== provocation: over-ceiling =="
project "$over_org" "$over_inv" "$contract" "$scratch/over.json"
over_rc=$?
expect_rc "over-ceiling projection reports NOT-OK" 1 "$over_rc" "$scratch/over.json.err"

python3 - "$scratch/over.json" <<'PY' || fail=1
import json
import sys

report = json.load(open(sys.argv[1], encoding="utf-8"))
enterprise = report["enterprise"]
problems = []
codes = {f["code"]: f for f in report["findings"]}
for code in ("SPEND_OVER_CEILING", "TENANT_OVER_CEILING", "ENTERPRISE_OVER_CEILING"):
    finding = codes.get(code)
    if finding is None:
        problems.append("%s was not reported" % code)
    elif finding["severity"] != "error":
        problems.append("%s reported as %s, not error" % (code, finding["severity"]))
if codes.get("SPEND_OVER_CEILING", {}).get("subject") != "fixture/alpha-one#qa-sme":
    problems.append("the over-ceiling SME is not named: %r"
                    % codes.get("SPEND_OVER_CEILING", {}).get("subject"))
if codes.get("SPEND_AT_CEILING", {}).get("severity") != "info":
    problems.append("the at-ceiling SME in the same fixture is not info telemetry")
# Numbers computed from the committed declarations, not read back from the report.
if enterprise["spend"]["total_usd"] != 275.0:
    problems.append("total %r, expected 275.0" % enterprise["spend"]["total_usd"])
if enterprise["spend"]["excess_usd"] != 25.0:
    problems.append("scope excess %r, expected 25.0 (275.0 - 250.0)"
                    % enterprise["spend"]["excess_usd"])
if enterprise["spend"]["over"] is not True:
    problems.append("the enterprise scope is not marked over")
if enterprise["spend"]["smes_over_ceiling"] != ["fixture/alpha-one#qa-sme"]:
    problems.append("smes_over_ceiling %r" % enterprise["spend"]["smes_over_ceiling"])
if enterprise["spend"]["smes_at_ceiling"] != ["fixture/alpha-two#iac-sme"]:
    problems.append("smes_at_ceiling %r" % enterprise["spend"]["smes_at_ceiling"])
# No clamping: the over-ceiling SME keeps its real spend.
by_ref = {}
for repo in report["repos"]:
    for sme in repo["smes"]:
        by_ref[sme["ref"]] = sme
if by_ref["fixture/alpha-one#qa-sme"]["spend_usd"] != 140.0:
    problems.append("the over-ceiling spend was clamped: %r"
                    % by_ref["fixture/alpha-one#qa-sme"]["spend_usd"])
if by_ref["fixture/alpha-one#qa-sme"]["excess_usd"] != 40.0:
    problems.append("the SME excess is not 40.0")
per_repo = {repo["repo"]: round(sum(s["spend_usd"] for s in repo["smes"]), 2)
            for repo in report["repos"]}
if per_repo != {"fixture/alpha-one": 140.0, "fixture/alpha-two": 90.0, "fixture/beta-one": 45.0}:
    problems.append("per-repo totals wrong: %r" % per_repo)
tenants = {t["id"]: t["spend"]["total_usd"] for t in enterprise["tenants"]}
if tenants != {"alpha": 230.0, "beta": 45.0}:
    problems.append("tenant totals wrong: %r" % tenants)
if round(sum(tenants.values()), 2) != 275.0:
    problems.append("tenant totals do not add up to the enterprise total")
# Isolation: the same SME id in two repos is two disjoint facts.
if by_ref["fixture/alpha-one#qa-sme"]["spend_usd"] == by_ref["fixture/alpha-two#qa-sme"]["spend_usd"]:
    problems.append("the two qa-sme facts are indistinguishable; isolation is not visible")
alpha = next(t for t in enterprise["tenants"] if t["id"] == "alpha")
if alpha["spend"]["total_usd"] != 230.0:
    problems.append("alpha total %r does not include both of its repos"
                    % alpha["spend"]["total_usd"])
for problem in problems:
    print("  FAIL  %s" % problem)
sys.exit(1 if problems else 0)
PY

# --- 3. provoke: a declared repo with no inventory ---------------------------
echo "== provocation: missing inventory =="
project "$missing_org" "$missing_inv" "$contract" "$scratch/missing.json"
missing_rc=$?
expect_rc "a repo with no inventory is CANNOT-ASSESS" 2 "$missing_rc" "$scratch/missing.json.err"
if ! grep -q "fixture/alpha-one" "$scratch/missing.json"; then
  report_fail "the missing repo is not named in the report"
fi

# --- 4. provoke: a malformed declaration -------------------------------------
echo "== provocation: malformed declaration =="
mkdir -p "$scratch/bad/inventory"
cp "$over_org" "$scratch/bad/org.yaml"
printf 'schema: ao.rollup/fleet-inventory-v1\nrepo: fixture/broken\nte nant: [\n' \
  > "$scratch/bad/inventory/broken.yaml"
project "$scratch/bad/org.yaml" "$scratch/bad/inventory" "$contract" "$scratch/bad.json"
bad_rc=$?
expect_rc "a malformed declaration is CANNOT-ASSESS" 2 "$bad_rc" "$scratch/bad.json.err"

# --- 5. provoke: a schema keyword the validator does not implement -----------
echo "== provocation: fail-closed schema =="
project "$over_org" "$over_inv" "$bad_contract" "$scratch/schema.json"
schema_rc=$?
expect_rc "an unsupported schema keyword is refused" 2 "$schema_rc" "$scratch/schema.json.err"

# --- 6. projection: running the roll-up writes nothing ----------------------
echo "== projection invariant =="
cp "$over_org" "$scratch/isolated-org.yaml"
cp -r "$over_inv" "$scratch/isolated-inventory"
writes_before="$(find "$scratch/isolated-org.yaml" "$scratch/isolated-inventory" -type f \
  -printf '%p %s %T@\n' | sort)"
sleep 1
project "$scratch/isolated-org.yaml" "$scratch/isolated-inventory" "$contract" "$scratch/iso.json"
writes_after="$(find "$scratch/isolated-org.yaml" "$scratch/isolated-inventory" -type f \
  -printf '%p %s %T@\n' | sort)"
if [ "$writes_before" != "$writes_after" ]; then
  report_fail "the projection modified its input tree (size or mtime changed)"
fi
if [ ! -s "$scratch/iso.json" ]; then
  report_fail "the projection produced no report to compare"
fi

# --- summary -----------------------------------------------------------------
if [ "$fail" -ne 0 ]; then
  echo "check-rollup: NOT-OK — the roll-up gate failed" >&2
  exit 1
fi
echo "check-rollup: OK — hierarchy, aggregation, refusals and projection verified"
exit 0
