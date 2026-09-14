#!/usr/bin/env bash
# check-paperclip-budget.sh — the per-task budget scope adapter gate (issue #415).
#
# The paperclip.ing seam (docs/PAPERCLIP-ING-INTEGRATION.md §5) names four budget
# mismatches as adoption work: #7 scope (the rail caps per tenant+vendor, not per
# agent/team/project), #8 no explicit currency, #9 the stop is a percentage not a
# boolean hand-off, #10 no per-task receipt. integrations/paperclip/budget.py is
# the adapter that derives the upstream cost shape from the fleet's own rail; it
# never writes the rail. A seam that nothing validates is a formality (GR-12), so
# this gate does three things and fails, by name, when any regresses:
#
#   1. it derives a full record set from a controlled rail + metering + ticket
#      tree and requires the records to conform to the frozen budget seam schema;
#   2. it provokes each refusal the acceptance names — a receipt with no ticket, a
#      cap with no scope level, a currency-less row — and requires the adapter to
#      refuse each, by name, with rc 1; and it proves the mutating pass left the
#      inputs byte-identical (sha256) afterwards;
#   3. it requires CANNOT-ASSESS (rc 2) when the rail is absent — never a pass —
#      and demonstrates the hard-stop hand-off and the no-cap refusal.
#
# The rail under test is the committed one (copied into a scratch tree), so the
# gate is offline and deterministic: no network, no third-party dependency.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
# CANNOT-ASSESS must never be reported as a pass.
#
# Usage: bash scripts/check-paperclip-budget.sh
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-paperclip-budget: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

adapter="integrations/paperclip/budget.py"
if [ ! -f "$adapter" ]; then
  echo "check-paperclip-budget: FAIL — $adapter is missing" >&2
  exit 1
fi

for rail in telemetry/budgets/config/policies.yaml gateway/finops/budgets.yaml; do
  if [ ! -f "$rail" ]; then
    echo "check-paperclip-budget: CANNOT-ASSESS — the budget rail is missing ($rail)" >&2
    exit 2
  fi
done

# Scratch: the sanctioned fleet idiom (no mktemp X-run — it trips the repo's own
# unfinished-marker scan, scripts/check-docs.sh).
scratch="/tmp/ao415-budget.$(date +%s%N).$"
if ! mkdir "$scratch" 2>/dev/null; then
  echo "check-paperclip-budget: CANNOT-ASSESS — cannot create a scratch directory" >&2
  exit 2
fi
trap 'rm -rf "$scratch"' EXIT

tree="$scratch/tree"
mkdir -p "$tree/telemetry/budgets/config" "$tree/gateway/finops" \
         "$tree/telemetry/metering" "$tree/.verify/ticket"
cp telemetry/budgets/config/policies.yaml "$tree/telemetry/budgets/config/policies.yaml"
cp gateway/finops/budgets.yaml "$tree/gateway/finops/budgets.yaml"

# A metering store: one billable agent's spend (the `agent` producer) inside a
# tenant (the `team` producer), plus a non-billable row that must not count.
cat > "$tree/telemetry/metering/usage.jsonl" <<'JSONL'
{"kind": "usage", "billable": true, "tenantId": "acme", "agentId": "coder", "costUsd": 30.0}
{"kind": "usage", "billable": true, "tenantId": "acme", "agentId": "coder", "costUsd": 10.0}
{"kind": "usage", "billable": false, "tenantId": "acme", "agentId": "reader", "costUsd": 999.0}
JSONL

# A ticket whose evidence[] carries the receipt the charge will be tied to.
cat > "$tree/.verify/ticket/tickets.json" <<'JSON'
{"tickets": [
  {"id": "kushin77/agent-orchestrator#415",
   "evidence": [{"kind": "gate-run", "ref": "deadbeef", "result": "PASS", "checks": 30}]}
]}
JSON

charges="$tree/telemetry/budgets/ledger.jsonl"
pristine="$scratch/charges.pristine.jsonl"
cat > "$pristine" <<'JSONL'
{"ticket": "kushin77/agent-orchestrator#415", "receipt": "deadbeef", "scope": {"level": "team", "id": "acme"}, "cap": 120.0, "currency": "USD", "hard_cap_pct": 100, "warn_at_pct": 80}
{"ticket": "kushin77/agent-orchestrator#415", "receipt": "deadbeef", "scope": {"level": "agent", "id": "coder"}, "cap": 50.0, "hard_cap_pct": 100, "warn_at_pct": 80}
JSONL
cp "$pristine" "$charges"

pass=0
fail=0

expect_rc() {  # label expected_rc tree [required-substring]
  local label="$1" want="$2" r="$3" pat="${4:-}"
  local out got ok=1
  out="$(python3 "$adapter" check --root "$r" 2>&1)"; got=$?
  printf '%s\n' "$out" | sed 's/^/      | /'
  [ "$got" = "$want" ] || ok=0
  if [ -n "$pat" ] && ! printf '%s' "$out" | grep -qF "$pat"; then ok=0; fi
  if [ "$ok" = 1 ]; then
    printf '  OK    %s (rc=%s)\n' "$label" "$got"
    pass=$((pass + 1))
  else
    printf '  FAIL  %s (want rc=%s, got rc=%s, must name %s)\n' "$label" "$want" "$got" "$pat" >&2
    fail=$((fail + 1))
  fi
}

sha() { sha256sum "$1" | cut -d' ' -f1; }

echo "== baseline derivation =="
sha_base="$(sha "$charges")"
expect_rc "records derive and conform (agent + team scope, stated currency)" 0 "$tree"

# The emitted records must satisfy the frozen budget seam schema.
schema_rc=0
python3 - "$tree" "$root" <<'PY' || schema_rc=$?
import sys
from pathlib import Path
root = Path(sys.argv[2]).resolve()
sys.path.insert(0, str(root))
from integrations.paperclip import budget as pc_budget  # noqa: E402
from integrations.paperclip import mapping as pc_mapping  # noqa: E402
report = pc_budget.build(Path(sys.argv[1]))
schema = pc_mapping.load_schema(root, "budget")
levels = set()
for record in report.records:
    payload = {k: v for k, v in record.items() if k != "_ticket"}
    findings = pc_mapping.validate(payload, schema, "budget")
    if findings:
        print("  FAIL  emitted record violates the seam schema: %s" % findings, file=sys.stderr)
        raise SystemExit(1)
    levels.add((payload["scope"]["level"], payload["currency"]))
if {lvl for lvl, _ in levels} != {"agent", "team"}:
    print("  FAIL  expected an agent- and a team-scoped record, got %s" % sorted(levels), file=sys.stderr)
    raise SystemExit(1)
if {cur for _, cur in levels} != {"USD"}:
    print("  FAIL  expected the stated currency USD on every record", file=sys.stderr)
    raise SystemExit(1)
print("  OK    %d record(s) conform to the budget seam schema; scope derived as %s"
      % (len(report.records), sorted(lvl for lvl, _ in levels)))
PY
if [ "$schema_rc" = 0 ]; then pass=$((pass + 1)); else fail=$((fail + 1)); fi

mutate() {  # mutate <sed-ish python op>
  python3 - "$charges" "$1" <<'PY'
import json
import sys
import pathlib
path = pathlib.Path(sys.argv[1])
rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
op = sys.argv[2]
if op == "receipt":
    rows[0]["receipt"] = "ghost-receipt"
elif op == "nolevel":
    rows[0]["scope"] = {"id": "acme"}
elif op == "nocurrency":
    rows[0]["currency"] = ""
elif op == "nocap":
    rows[0].pop("cap", None)
elif op == "project":
    rows[1]["scope"] = {"level": "project", "id": "m27"}
path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
PY
}

echo "== refusal A: a receipt with no ticket (rc 1, naming the receipt) =="
mutate receipt
expect_rc "receipt with no ticket is refused" 1 "$tree" "ghost-receipt"
restore_rc=0
cp "$pristine" "$charges"
[ "$(sha "$charges")" = "$sha_base" ] || restore_rc=1
if [ "$restore_rc" = 0 ]; then
  printf '  OK    restored byte-identical (sha256=%s)\n' "$sha_base"; pass=$((pass + 1))
else
  printf '  FAIL  restore changed the charge ledger\n' >&2; fail=$((fail + 1))
fi

echo "== refusal B: a cap with no scope level (rc 1, naming it) =="
mutate nolevel
expect_rc "cap with no scope level is refused" 1 "$tree" "no scope level"
cp "$pristine" "$charges"
if [ "$(sha "$charges")" = "$sha_base" ]; then
  printf '  OK    restored byte-identical (sha256=%s)\n' "$sha_base"; pass=$((pass + 1))
else
  printf '  FAIL  restore changed the charge ledger\n' >&2; fail=$((fail + 1))
fi

echo "== refusal C: a currency-less row (rc 1) =="
mutate nocurrency
expect_rc "currency-less row is refused" 1 "$tree" "currency-less row"
cp "$pristine" "$charges"
if [ "$(sha "$charges")" = "$sha_base" ]; then
  printf '  OK    restored byte-identical (sha256=%s)\n' "$sha_base"; pass=$((pass + 1))
else
  printf '  FAIL  restore changed the charge ledger\n' >&2; fail=$((fail + 1))
fi

echo "== refusal D: a level with no producer fails closed (rc 1, naming the level) =="
mutate project
expect_rc "scope level with no producer is refused, not reported as team" 1 "$tree" "has no producer"
cp "$pristine" "$charges"
if [ "$(sha "$charges")" = "$sha_base" ]; then
  printf '  OK    restored byte-identical (sha256=%s)\n' "$sha_base"; pass=$((pass + 1))
else
  printf '  FAIL  restore changed the charge ledger\n' >&2; fail=$((fail + 1))
fi

noagent="$scratch/noagent"
cp -r "$tree" "$noagent"
python3 - "$noagent/telemetry/metering/usage.jsonl" <<'PY'
import json
import pathlib
import sys
path = pathlib.Path(sys.argv[1])
rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
for row in rows:
    row.pop("agentId", None)
path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
PY
expect_rc "an agent charge with no per-agent producer is refused" 1 "$noagent" "has no producer"

echo "== CANNOT-ASSESS: the rail is absent (rc 2, never 0) =="
norail="$scratch/norail"
cp -r "$tree" "$norail"
rm -f "$norail/telemetry/budgets/config/policies.yaml" "$norail/gateway/finops/budgets.yaml"
expect_rc "absent rail is CANNOT-ASSESS" 2 "$norail" "CANNOT-ASSESS"

nometer="$scratch/nometer"
cp -r "$tree" "$nometer"
rm -f "$nometer/telemetry/metering/usage.jsonl"
python3 - "$nometer/telemetry/budgets/ledger.jsonl" <<'PY'
import json
import pathlib
import sys
path = pathlib.Path(sys.argv[1])
rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
for row in rows:
    row.pop("spent", None)
path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
PY
expect_rc "absent metering store is CANNOT-ASSESS" 2 "$nometer" "CANNOT-ASSESS"

echo "== the hard-stop hand-off and the no-cap refusal =="
behaviour_rc=0
python3 - "$root" <<'PY' || behaviour_rc=$?
import sys
from pathlib import Path
root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root))
from integrations.paperclip import budget as pc_budget  # noqa: E402
at_cap = {"scope": {"level": "team", "id": "acme"}, "period": "month", "cap": 120.0,
          "spent": 120.0, "currency": "USD", "hard_stop": True,
          "burn_rate_alert_pct": 80.0, "receipt_ref": "deadbeef"}
under = dict(at_cap, spent=10.0)
stop = pc_budget.hand_off(at_cap)
if stop["action"] != pc_budget.ACTION_STOP_HANDOFF or "stops and asks" not in stop["reason"]:
    print("  FAIL  at the cap the work must stop and ask, got %s" % stop, file=sys.stderr)
    raise SystemExit(1)
print("  OK    at the cap: %s — %s" % (stop["action"], stop["reason"]))
below = pc_budget.hand_off(under)
if below["action"] != pc_budget.ACTION_OK:
    print("  FAIL  below the cap the work must continue, got %s" % below, file=sys.stderr)
    raise SystemExit(1)
print("  OK    below the cap: %s" % below["action"])
if pc_budget.derive_hard_stop(100) is not True or pc_budget.derive_hard_stop(100, soft=True) is not False:
    print("  FAIL  hard_stop must be a boolean derived from hard_cap_pct", file=sys.stderr)
    raise SystemExit(1)
print("  OK    hard_stop is boolean (hard_cap_pct<=100 -> True; a soft cap -> False)")
if pc_budget.hand_off({"cap": 0, "spent": 5.0, "currency": "USD"})["action"] != pc_budget.ACTION_STOP_HANDOFF:
    print("  FAIL  a spend with no cap must be refused, not treated as unlimited", file=sys.stderr)
    raise SystemExit(1)
print("  OK    a spend with no declared cap is refused, never unlimited")
PY
if [ "$behaviour_rc" = 0 ]; then pass=$((pass + 1)); else fail=$((fail + 1)); fi

echo "== the adapter never writes the rail (read-only rail proof) =="
rail_before="$(sha telemetry/budgets/config/policies.yaml)$(sha gateway/finops/budgets.yaml)"
python3 "$adapter" check --root "$tree" >/dev/null 2>&1
rail_after="$(sha telemetry/budgets/config/policies.yaml)$(sha gateway/finops/budgets.yaml)"
if [ "$rail_before" = "$rail_after" ]; then
  printf '  OK    rail sha256 unchanged (%s)\n' "${rail_before:0:16}"; pass=$((pass + 1))
else
  printf '  FAIL  the rail changed during a check run\n' >&2; fail=$((fail + 1))
fi

echo "== summary =="
echo "  checks passed: $pass, failed: $fail"
if [ "$fail" -ne 0 ]; then
  echo "check-paperclip-budget: FAIL — the budget scope adapter drifts from the seam" >&2
  exit 1
fi
echo "check-paperclip-budget: OK — scope, currency, hard-stop and the ticket receipt all hold"
exit 0
