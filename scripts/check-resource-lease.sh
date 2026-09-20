#!/usr/bin/env bash
# check-resource-lease.sh — the live-resource lease registry (issue #1545).
#
# THE DEFECT THIS EXISTS FOR
#   Contention on a live RESOURCE — terraform state, a Cloudflare phase, a
#   hand-started container — was unmediated. The dispatch claim ledger leases
#   FILES; nothing leased "the thing being mutated". The known incident is a
#   two-owner Cloudflare phase (capital-underwriting vs. shared-services):
#   both sides pushed a phase of one surface, and each push silently reverted
#   the other's. `governance/dispatch/resource_lease.py` is the claim subtype
#   that would have refused the second holder; `infra/cloudflare/ao-ssh-access.sh`
#   is the first mutating entrypoint wired to consult it.
#
# WHAT IS PROVEN (against the real artifacts, and against provoked violations)
#   * POLICY     — the per-type TTLs are the declared ones in
#                  `governance/policy/lease.py` (read, not restated: the probe
#                  compares against the policy OBJECT), and a state lease is
#                  longer than a phase lease (an apply outlives a phase);
#   * SHAPE      — every record the CLI writes validates against the frozen
#                  shape `$defs/resourceClaim`, and a record the shape does not
#                  describe is refused BY NAME (so the arm is not vacuous);
#   * LIVE CHECK — a second holder of a leased resource is REFUSED, and the
#                  refusal quotes the holder and its expiry (one holder's own
#                  re-acquire renews instead: one owner, not two);
#   * NEGATIVE   — an UNLEASED resource proceeds (the refusal cannot be
#                  satisfied by a registry that refuses everything);
#   * TTL        — a lapsed lease frees the resource, and the new holder's record
#                  names the holder it took over from;
#   * FAIL-CLOSED— a malformed ledger is CANNOT-ASSESS (exit 2), never "free";
#   * THE GUARD  — a refused guard NEVER runs the command it wraps (observed by
#                  wrapping a command that would leave a trace), and a granted
#                  guard runs it and releases the lease;
#   * THE ROUTE  — the real mutating path of `ao-ssh-access.sh` is driven in a
#                  scratch tree with the surface flag ON: with a FOREIGN holder
#                  holding the phase it refuses BY NAME and never reaches the
#                  API, and with the SAME holder it gets past the lease and does
#                  reach the API. The pair is the differential that shows the
#                  refusal is the lease and not the fixture;
#   * TERRAFORM  — the guard wraps a REAL `terraform plan` (provider-free config,
#                  plan only, never apply — GR-5): refused while leased with no
#                  plan artifact written, and it plans normally when free. SKIPs
#                  visibly if terraform is not installed;
#   * HISTORY    — the ledger is covered by the tracked `.gitignore`, so the
#                  runtime state cannot reach history by a plain `git add`.
#
# Exit-code contract (the repo's honesty tri-state): 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-resource-lease.sh
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

module="governance/dispatch/resource_lease.py"
route="infra/cloudflare/ao-ssh-access.sh"
ledger_rel=".board/resource-claims.jsonl"

for required in "$module" "$route" governance/policy/lease.py governance/dispatch/dispatch.schema.json; do
  if [ ! -f "$required" ]; then
    echo "check-resource-lease: FAIL — $required is missing" >&2
    exit 1
  fi
done

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-resource-lease: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

work="$(mktemp -d /tmp/check-resource-lease.XXXXXX)" || {
  echo "check-resource-lease: CANNOT-ASSESS — cannot create a scratch directory" >&2
  exit 2
}
cleanup() { rm -rf "$work"; }
trap cleanup EXIT

fail=0
note() { printf '  OK    %s\n' "$1"; }
problem() { printf '  FAIL  %s\n' "$1" >&2; fail=$((fail + 1)); }

if ! python3 "$module" self-control >"$work/self-control.out" 2>&1; then
  problem "the module's own self-control failed: $(tail -n 3 "$work/self-control.out" | tr '\n' ' ')"
else
  note "the module's self-control holds every refusal and every allowance"
fi

# ── the declared policy is the single source of the TTL, per resource type ──
echo "== the TTL is declared once, per resource type, in the policy =="
cat > "$work/policy-probe.py" <<'PY'
"""Compare the live TTLs against the policy OBJECT, not against a literal."""

import sys

sys.dont_write_bytecode = True
sys.path.insert(0, "governance/dispatch")
import resource_lease as rl  # noqa: E402

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("OK    " if ok else "FAIL  ") + name + (" " + detail if detail and not ok else ""))
    if not ok:
        failures.append(name)


state = rl.ttl_seconds("tf-state:onprem")
phase = rl.ttl_seconds("cloudflare-phase:3")
check(
    "the-state-ttl-is-the-declared-policy-value",
    state == rl.lease.RESOURCE_CLAIM_TTL_SECONDS["tf-state"],
    f"=> {state} != policy {rl.lease.RESOURCE_CLAIM_TTL_SECONDS['tf-state']}",
)
check(
    "the-phase-ttl-is-the-declared-policy-value",
    phase == rl.lease.RESOURCE_CLAIM_TTL_SECONDS["cloudflare-phase"],
    f"=> {phase} != policy {rl.lease.RESOURCE_CLAIM_TTL_SECONDS['cloudflare-phase']}",
)
check(
    "a-state-lease-outlives-a-phase-lease",
    state > phase,
    f"=> state={state}s phase={phase}s",
)
check(
    "an-undeclared-type-gets-the-declared-default",
    rl.ttl_seconds("hand-started-container:pg") == rl.lease.RESOURCE_CLAIM_TTL_DEFAULT_SECONDS,
    "=> the default is not the declared one",
)

try:
    rl.resource_type("onprem")
    check("a-resource-id-with-no-type-is-refused", False, "=> it was ACCEPTED")
except rl.ResourceClaimRefused as refused:
    check("a-resource-id-with-no-type-is-refused", refused.reason == rl.REASON_MALFORMED_RESOURCE)

raise SystemExit(1 if failures else 0)
PY
if python3 "$work/policy-probe.py" >"$work/policy.out" 2>&1; then
  while IFS= read -r line; do note "${line#OK    }"; done < <(grep '^OK ' "$work/policy.out")
else
  problem "the declared policy is not what the registry reads: $(grep -m2 '^FAIL ' "$work/policy.out" | tr '\n' ' ')"
fi

# ── the frozen record shape ─────────────────────────────────────────────────
echo "== the ledger's records are the frozen shape =="
live="$work/live.jsonl"
python3 "$module" acquire --resource tf-state:onprem --holder '#1545' --lane live-resource-lease-registry \
  --issue 1545 --ledger "$live" >/dev/null 2>&1
python3 "$module" release --resource tf-state:onprem --holder '#1545' --ledger "$live" >/dev/null 2>&1
cat > "$work/shape-probe.py" <<'PY'
"""Validate what the CLI actually wrote against the frozen dispatch shape."""

import json
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, "governance/dispatch")
import schema as dispatch_schema  # noqa: E402

failures: list[str] = []
rows = [json.loads(line) for line in open(sys.argv[1], encoding="utf-8") if line.strip()]
if [row["event"] for row in rows] != ["acquire", "release"]:
    failures.append("the-ledger-recorded-both-events")
for row in rows:
    problems = dispatch_schema.problems(row, dispatch_schema.SHAPE_RESOURCE_CLAIM)
    if problems:
        failures.append(f"the-record-matches-the-frozen-shape {problems}")
bogus = dict(rows[0]) if rows else {}
bogus["event"] = "bogus"
if not dispatch_schema.problems(bogus, dispatch_schema.SHAPE_RESOURCE_CLAIM):
    failures.append("a-record-the-shape-does-not-describe-is-refused")
print("OK    the-ledger-recorded-both-events" if "the-ledger-recorded-both-events" not in failures else "FAIL  the-ledger-recorded-both-events")
print("OK    the-record-matches-the-frozen-shape" if not any(f.startswith("the-record") for f in failures) else "FAIL  the-record-matches-the-frozen-shape")
print("OK    a-record-the-shape-does-not-describe-is-refused" if "a-record-the-shape-does-not-describe-is-refused" not in failures else "FAIL  a-record-the-shape-does-not-describe-is-refused")
raise SystemExit(1 if failures else 0)
PY
if python3 "$work/shape-probe.py" "$live" >"$work/shape.out" 2>&1; then
  while IFS= read -r line; do note "${line#OK    }"; done < <(grep '^OK ' "$work/shape.out")
else
  problem "the ledger does not match the frozen shape: $(grep -m2 '^FAIL ' "$work/shape.out" | tr '\n' ' ')"
fi

# ── the live check: a second holder is refused, by name ─────────────────────
echo "== the acceptance live check: a second holder is refused, naming the holder =="
led="$work/tf.jsonl"
python3 "$module" acquire --resource tf-state:onprem --holder '#1545' --ledger "$led" >/dev/null 2>&1
second_out="$(python3 "$module" acquire --resource tf-state:onprem --holder '#1546' --ledger "$led" 2>&1)"
second_rc=$?
if [ "$second_rc" -eq 1 ] && printf '%s' "$second_out" | grep -q '#1545'; then
  note "a second holder is refused (exit ${second_rc}) quoting the holder: $(printf '%s' "$second_out" | sed 's/^resource-lease: //')"
else
  problem "the second holder was not refused quoting the holder (rc=${second_rc}): ${second_out}"
fi
if printf '%s' "$second_out" | grep -q 'expires 20'; then
  note "the refusal quotes the lease's expiry, so the refused caller knows how long to wait"
else
  problem "the refusal does not quote the expiry: ${second_out}"
fi
if [ "$(grep -c 'acquire' "$led")" -eq 1 ]; then
  note "the refusal wrote no second claim (the ledger still holds one acquire)"
else
  problem "the refused claim still wrote to the ledger: $(grep -c 'acquire' "$led") acquires"
fi
renew_out="$(python3 "$module" acquire --resource tf-state:onprem --holder '#1545' --ledger "$led" 2>&1)"
if [ "$?" -eq 0 ] && printf '%s' "$renew_out" | grep -q 'renewed by its own holder'; then
  note "the SAME holder re-acquires as a renewal, not a refusal (one owner, not two)"
else
  problem "the same holder was not allowed to renew: ${renew_out}"
fi

echo "== the negative control: an unleased resource proceeds =="
free_out="$(python3 "$module" acquire --resource tf-state:staging --holder '#1546' --ledger "$led" 2>&1)"
free_rc=$?
if [ "$free_rc" -eq 0 ] && printf '%s' "$free_out" | grep -q 'ACQUIRED tf-state:staging'; then
  note "an unleased resource proceeds (exit ${free_rc}), so the refusal above cannot be 'it refuses everything'"
else
  problem "an unleased resource did not proceed (rc=${free_rc}): ${free_out}"
fi

# ── the TTL frees the resource (driven at an injected instant) ──────────────
echo "== a lapsed lease frees the resource =="
cat > "$work/ttl-probe.py" <<'PY'
"""A lease that lapses must free the resource, and the taker-over must say so."""

import sys
from datetime import datetime, timedelta, timezone

sys.dont_write_bytecode = True
sys.path.insert(0, "governance/dispatch")
import resource_lease as rl  # noqa: E402

failures: list[str] = []
ledger = sys.argv[1]
base = datetime(2026, 9, 20, 12, 0, 0, tzinfo=timezone.utc)
rl.acquire("tf-state:onprem", "#1545", path=ledger, now=base)
lapsed = base + timedelta(seconds=rl.ttl_seconds("tf-state:onprem") + 1)
if rl.holder_of("tf-state:onprem", path=ledger, now=base) is None:
    failures.append("the-lease-is-live-before-its-ttl")
else:
    print("OK    the-lease-is-live-before-its-ttl")
if rl.holder_of("tf-state:onprem", path=ledger, now=lapsed) is not None:
    failures.append("a-lapsed-lease-frees-the-resource")
else:
    print("OK    a-lapsed-lease-frees-the-resource")
taken = rl.acquire("tf-state:onprem", "#1546", path=ledger, now=lapsed)
if "taken over from #1545" not in str(taken.get("reason")):
    failures.append("the-taker-over-record-names-the-lapsed-holder")
else:
    print("OK    the-taker-over-record-names-the-lapsed-holder")
raise SystemExit(1 if failures else 0)
PY
if python3 "$work/ttl-probe.py" "$work/ttl.jsonl" >"$work/ttl.out" 2>&1; then
  while IFS= read -r line; do note "${line#OK    }"; done < <(grep '^OK ' "$work/ttl.out")
else
  problem "the TTL does not free the resource: $(grep -m2 '^FAIL ' "$work/ttl.out" | tr '\n' ' ')"
fi

# ── fail closed: a ledger that cannot be read is never "free" ───────────────
echo "== a ledger that cannot be read is CANNOT-ASSESS, never 'free' =="
malformed="$work/malformed.jsonl"
printf '{"event":"acquire","resource_id":"tf-state:onprem"}\n' > "$malformed"
malformed_rc="$(python3 "$module" held --resource tf-state:onprem --ledger "$malformed" >/dev/null 2>&1; printf '%s' "$?")"
if [ "$malformed_rc" -eq 2 ]; then
  note "a malformed ledger is exit 2 (CANNOT-ASSESS), never 0/1"
else
  problem "a malformed ledger answered with exit ${malformed_rc}, expected 2"
fi
absent_rc="$(python3 "$module" held --resource tf-state:onprem --ledger "$work/absent.jsonl" >/dev/null 2>&1; printf '%s' "$?")"
if [ "$absent_rc" -eq 1 ]; then
  note "an ABSENT ledger is 'free' (exit 1 from held), which is not the same answer as unreadable"
else
  problem "an absent ledger answered with exit ${absent_rc}, expected 1 (free)"
fi

# ── the guard: a refused guard never runs the command ──────────────────────
echo "== the guard runs the wrapped command only when it holds the lease =="
guard_led="$work/guard.jsonl"
marker="$work/the-command-ran"
python3 "$module" acquire --resource tf-state:onprem --holder '#1545' --ledger "$guard_led" >/dev/null 2>&1
if python3 "$module" guard --resource tf-state:onprem --holder '#1546' --ledger "$guard_led" -- \
  python3 -c "open('$marker','w').write('ran')" >"$work/guard-refused.out" 2>&1; then
  problem "the guard ran a command while another holder held the resource"
else
  if [ -e "$marker" ]; then
    problem "the refused guard started the command anyway (the trace exists)"
  else
    note "a refused guard never starts the command (no trace), and exits non-zero"
  fi
fi
if python3 "$module" guard --resource tf-state:staging --holder '#1546' --ledger "$guard_led" -- \
  python3 -c "open('$marker','w').write('ran')" >"$work/guard-granted.out" 2>&1 && [ -e "$marker" ]; then
  note "a granted guard runs the command (the trace exists)"
else
  problem "a granted guard did not run the command: $(tail -n 2 "$work/guard-granted.out" | tr '\n' ' ')"
fi
if [ "$(python3 "$module" held --resource tf-state:staging --ledger "$guard_led" >/dev/null 2>&1; printf '%s' "$?")" -eq 1 ]; then
  note "the granted guard releases its lease afterwards, so the resource is free again"
else
  problem "the granted guard left the lease held"
fi

# ── the route: the real mutating path consults the lease ───────────────────
echo "== the real mutating path is refused by the lease, before it touches the API =="
scratch="$work/route-tree"
surface="remote_ssh_access"
mkdir -p "$scratch"
for rel in "$route" infra/cloudflare/ingress.py portal/server/__init__.py portal/server/fleet.py \
  portal/server/surface_state.py infra/rollout/registry_projection.py "$module" governance/policy/lease.py; do
  mkdir -p "$scratch/$(dirname "$rel")"
  if ! cp "$rel" "$scratch/$rel"; then
    problem "the scratch route tree could not copy ${rel}"
  fi
done
if [ -f portal/__init__.py ]; then cp portal/__init__.py "$scratch/portal/__init__.py"; fi
cat > "$work/flag-fixture.py" <<'PY'
"""Copy the committed registry and promote one surface, so --apply gets past the flag."""

import sys
from pathlib import Path

import yaml

source, destination, surface = sys.argv[1:4]
document = yaml.safe_load(Path(source).read_text(encoding="utf-8"))
entry = (document.get("surfaces") or {}).get(surface)
if not isinstance(entry, dict):
    print(f"the registry declares no surfaces.{surface}", file=sys.stderr)
    raise SystemExit(1)
entry["default"] = "on"
entry["promoted"] = True
Path(destination).parent.mkdir(parents=True, exist_ok=True)
Path(destination).write_text(yaml.safe_dump(document, sort_keys=False, allow_unicode=True), encoding="utf-8")
PY
if ! python3 "$work/flag-fixture.py" infra/feature-flags/registry.yaml \
  "$scratch/infra/feature-flags/registry.yaml" "$surface" 2>"$work/flag-fixture.err"; then
  problem "the ON fixture registry could not be built: $(tail -n 2 "$work/flag-fixture.err" | tr '\n' ' ')"
else
  route_ledger="$work/route.jsonl"
  python3 "$module" acquire --resource "cloudflare-phase:${surface}" --holder operator:someone-else \
    --ledger "$route_ledger" >/dev/null 2>&1

  run_route() { # run_route <holder> <out-file>
    (
      cd "$scratch" || exit 9
      AO_CF_API_BASE="http://127.0.0.1:1" \
      CF_API_TOKEN="stub-token-for-the-offline-probe" \
      CF_ACCOUNT_ID="stub-account" CF_ZONE_ID="stub-zone" CF_TUNNEL_ID="stub-tunnel" \
      AO_SSH_HOSTNAME="ssh.example.test" AO_SSH_ORIGIN_HOST="192.0.2.10" AO_SSH_ORIGIN_PORT="22" \
      AO_SSH_ACCESS_EMAILS="operator@example.invalid" \
      AO_RESOURCE_CLAIM_LEDGER="$route_ledger" AO_RESOURCE_CLAIM_HOLDER="$1" \
        bash infra/cloudflare/ao-ssh-access.sh --apply
    ) >"$2" 2>&1
    printf '%s' "$?"
  }

  foreign_rc="$(run_route operator:me "$work/route-foreign.out")"
  if [ "$foreign_rc" -eq 1 ] && grep -q 'operator:someone-else' "$work/route-foreign.out"; then
    note "with a FOREIGN holder holding the phase, the route refuses by name (exit ${foreign_rc})"
  else
    problem "the route did not refuse a foreign holder (rc=${foreign_rc}): $(tail -n 2 "$work/route-foreign.out" | tr '\n' ' ')"
  fi
  if grep -q 'Failed to connect' "$work/route-foreign.out"; then
    problem "the refused route still reached the API, so the lease is checked too late"
  else
    note "the refused route never reached the API (no connection attempt in its output)"
  fi

  same_rc="$(run_route operator:someone-else "$work/route-same.out")"
  if [ "$same_rc" -ne 1 ] && grep -q 'Failed to connect' "$work/route-same.out"; then
    note "the differential: the SAME holder gets past the lease and does reach the API (exit ${same_rc})"
  else
    problem "the same holder did not get past the lease (rc=${same_rc}): $(tail -n 2 "$work/route-same.out" | tr '\n' ' ')"
  fi
  if grep -q '"resource_id":"cloudflare-phase:' "$route_ledger"; then
    note "the route's own run wrote the phase lease to the ledger it was pointed at"
  else
    problem "the route wrote no lease record: the wiring is not being exercised"
  fi
fi

# ── terraform: the guard wraps a REAL plan (never an apply, GR-5) ───────────
echo "== the guard wraps a real terraform plan (plan only) =="
if ! command -v terraform >/dev/null 2>&1; then
  echo "  SKIP  terraform is not installed, so the plan arm cannot run here (never a pass)"
else
  tfdir="$work/tf"
  mkdir -p "$tfdir"
  cat > "$tfdir/main.tf" <<'TF'
variable "state_scope" {
  type    = string
  default = "onprem"
}

output "planned_state_scope" {
  value = var.state_scope
}
TF
  plan_cmd=(terraform -chdir="$tfdir" plan -input=false -lock=false -no-color -out=tfplan)
  if TF_DATA_DIR="$tfdir/.tfdata" TF_PLUGIN_CACHE_DIR="${TF_PLUGIN_CACHE_DIR:-$HOME/.terraform.d/plugin-cache}" \
    terraform -chdir="$tfdir" init -backend=false -input=false -no-color >"$work/tf-init.out" 2>&1; then
    tf_ledger="$work/tf-guard.jsonl"
    python3 "$module" acquire --resource tf-state:onprem --holder '#1545' --ledger "$tf_ledger" >/dev/null 2>&1
    rm -f "$tfdir/tfplan"
    if TF_DATA_DIR="$tfdir/.tfdata" python3 "$module" guard --resource tf-state:onprem --holder '#1546' \
      --ledger "$tf_ledger" -- "${plan_cmd[@]}" >"$work/tf-refused.out" 2>&1; then
      problem "the guard ran the plan while another holder held tf-state:onprem"
    elif [ -e "$tfdir/tfplan" ]; then
      problem "the refused guard planned anyway (the plan artifact exists)"
    else
      note "a second session's terraform plan on the leased tf-state is refused, and no plan artifact was written"
    fi
    free_rc=0
    TF_DATA_DIR="$tfdir/.tfdata" python3 "$module" guard --resource tf-state:staging --holder '#1546' \
      --ledger "$tf_ledger" -- "${plan_cmd[@]}" >"$work/tf-free.out" 2>&1 || free_rc=$?
    if [ "$free_rc" -eq 0 ] && [ -e "$tfdir/tfplan" ]; then
      note "an unleased tf-state plans normally under the guard (exit 0, plan artifact written)"
    else
      problem "the unleased plan under the guard failed (rc=${free_rc}): $(tail -n 3 "$work/tf-free.out" | tr '\n' ' ')"
    fi
  else
    problem "terraform init could not run offline, so the plan arm is unproven: $(tail -n 2 "$work/tf-init.out" | tr '\n' ' ')"
  fi
fi

# ── the ledger is runtime state and must not reach history ─────────────────
echo "== the ledger is covered by the tracked .gitignore =="
if git check-ignore -q "$ledger_rel" 2>/dev/null; then
  note "${ledger_rel} is ignored, so the live holding state cannot enter history by a plain git add"
else
  problem "${ledger_rel} is NOT ignored: the runtime lease ledger would be committable"
fi

echo ""
if [ "$fail" -gt 0 ]; then
  echo "check-resource-lease: FAIL — $fail finding(s)" >&2
  exit 1
fi
echo "check-resource-lease: OK — a second holder is refused by name and never mutates; the TTL frees a lapsed lease; a refused guard never runs its command; the route and a real terraform plan both consult the registry"
exit 0
