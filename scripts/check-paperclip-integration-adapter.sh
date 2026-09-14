#!/usr/bin/env bash
# check-paperclip-integration-adapter.sh — the paperclip.ing adapter gate
# (issue #428, ADR-0013).
#
# ADR-0013 adopts the upstream Paperclip CLI as the operator surface and
# integrates over its HTTP API across a process boundary. The seam doc
# (docs/PAPERCLIP-ING-INTEGRATION.md §5) names eleven mismatches as the
# adoption's work-order; integrations/paperclip/ is the adapter that resolves
# them. A seam that nothing validates is a formality (no-false-green doctrine,
# GR-12), so this gate fails, by name, when the adapter drifts:
#
#   * the mapper's records (heartbeats, tickets, budgets) must conform to the
#     three frozen schemas under docs/contracts/paperclip/ — a dropped required
#     field or a value outside a closed vocabulary is refused, naming the field;
#   * the client's request shapes, exercised through the OFFLINE FixtureTransport,
#     must keep the /api prefix, the company scoping, the `Authorization: Bearer`
#     header and the `X-Paperclip-Run-Id` header on mutating calls only.
#
# The gate never touches the network: every client call goes through
# FixtureTransport, replaying integrations/paperclip/tests/fixtures/api.json.
#
# It also runs its own negative control: it takes the deterministic mapping
# output, breaks it twice — once by dropping a required ticket field, once by
# moving a closed-vocabulary value outside its enum — asserts the mutation
# changed the bytes (and the sha256), and requires the validator to refuse each
# mutant by name. If a mutant passes, this gate reports FAIL — a check that
# cannot fail is a formality.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
# CANNOT-ASSESS must never be reported as a pass.
#
# Usage: bash scripts/check-paperclip-integration-adapter.sh
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-paperclip-adapter: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

if [ ! -d "$root/integrations/paperclip" ]; then
  echo "check-paperclip-adapter: FAIL — integrations/paperclip/ is missing" >&2
  exit 1
fi

fixture="integrations/paperclip/tests/fixtures/api.json"
if [ ! -f "$fixture" ]; then
  echo "check-paperclip-adapter: CANNOT-ASSESS — no fixture at $fixture" >&2
  exit 2
fi

# validate <root> [plan.json] — 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
# With no plan file, the mapper is rebuilt from the tree and the client request
# shapes are exercised through the offline fixture transport. With a plan file,
# only that plan is validated (used by the negative control).
validate() {
  python3 - "$@" <<'PY'
import json
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
plan_path = Path(sys.argv[2]) if len(sys.argv) > 2 else None
sys.path.insert(0, str(root))

from integrations.paperclip import client as pc_client  # noqa: E402
from integrations.paperclip import mapping as pc_mapping  # noqa: E402

failures = []

for kind in pc_mapping.SCHEMA_KINDS:
    schema_file = root / pc_mapping.SCHEMA_DIR / f"{kind}.schema.json"
    if not schema_file.exists():
        print(f"  CANNOT-ASSESS  missing seam schema {schema_file}", file=sys.stderr)
        raise SystemExit(2)

schemas = pc_mapping.load_schemas(root)

if plan_path is not None:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
else:
    plan = pc_mapping.build_plan(root, company_id="acme", session_id="gate-session")

findings = pc_mapping.validate_plan(plan, schemas)
failures.extend(findings)
counts = {s: len(plan.get(s) or []) for s in pc_mapping.SECTION_SCHEMA}
print(
    "  OK    mapping conforms: %d heartbeat(s), %d ticket(s), %d budget(s) vs the three seam schemas"
    % (counts["heartbeats"], counts["tickets"], counts["budgets"])
)

if plan_path is None:
    fixture = root / "integrations/paperclip/tests/fixtures/api.json"
    transport = pc_client.FixtureTransport(fixture, token="tok", run_id="run-1")
    client = pc_client.PaperclipClient(company_id="acme", transport=transport)
    client.health()
    client.openapi()
    for call in (client.agents, client.issues, client.costs, client.approvals,
                 client.activity, client.dashboard):
        call()
    client.create_issue({"id": "kushin77/agent-orchestrator#428"})
    client.update_issue("428", {"status": "done"})
    client.request_topup({"kind": "topup"})
    requests = transport.requests

    if not all(r["path"].startswith("/api") for r in requests):
        failures.append("a client request path does not start with /api")
    scoped = [r for r in requests if "/companies/" in r["path"]]
    if not scoped:
        failures.append("no company-scoped request was exercised")
    if not all(r["path"].startswith("/api/companies/acme/") for r in scoped):
        failures.append("company scoping drifted from /api/companies/acme/")
    if not all(r["headers"].get("Authorization") == "Bearer tok" for r in requests):
        failures.append("Authorization: Bearer <token> is missing on a request")
    mutating = [r for r in requests if r["method"] in pc_client.MUTATING_METHODS]
    if not mutating:
        failures.append("no mutating request was exercised")
    if not all(r["headers"].get("X-Paperclip-Run-Id") == "run-1" for r in mutating):
        failures.append("X-Paperclip-Run-Id is missing on a mutating request")
    reads = [r for r in requests if r["method"] == "GET"]
    if any("X-Paperclip-Run-Id" in r["headers"] for r in reads):
        failures.append("X-Paperclip-Run-Id leaked onto a non-mutating request")
    print(
        "  OK    client shapes offline via FixtureTransport: %d request(s); "
        "/api prefix, company scoping, Bearer auth and run header all held"
        % len(requests)
    )

if failures:
    for finding in failures:
        print("  FAIL  %s" % finding, file=sys.stderr)
    raise SystemExit(1)

print("  OK    offline mapping and transport seam both conform")
raise SystemExit(0)
PY
}

rc=0
validate "$root" || rc=$?
case "$rc" in
  0) : ;;
  1)
    echo "check-paperclip-adapter: FAIL — the adapter drifts from the seam" >&2
    exit 1
    ;;
  *)
    echo "check-paperclip-adapter: CANNOT-ASSESS — validator returned $rc" >&2
    exit 2
    ;;
esac

# --- negative control ---------------------------------------------------------
# Break the deterministic mapping output twice and require the validator to
# refuse each by name, with the bytes (and sha256) genuinely changed. The
# baseline is re-validated afterwards and must hash identically, so the control
# leaves nothing mutated.
scratch="/tmp/ao428-adapter.$(date +%s%N).$"
if ! mkdir "$scratch" 2>/dev/null; then
  echo "check-paperclip-adapter: CANNOT-ASSESS — cannot create a scratch directory" >&2
  exit 2
fi
trap 'rm -rf "$scratch"' EXIT

shas="$(python3 - "$root" "$scratch" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
scratch = Path(sys.argv[2])
sys.path.insert(0, str(root))

from integrations.paperclip import mapping as pc_mapping  # noqa: E402

plan = pc_mapping.build_plan(root, company_id="acme", session_id="gate-session")


def dump(obj, name):
    payload = json.dumps(obj, indent=2, sort_keys=True).encode("utf-8")
    (scratch / name).write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


base_sha = dump(plan, "baseline.json")

# mutation A: drop a required ticket field (owner)
mutant_a = json.loads(json.dumps(plan))
del mutant_a["tickets"][0]["owner"]
sha_a = dump(mutant_a, "mutant_a.json")

# mutation B: move a closed-vocabulary value (status) outside its enum
mutant_b = json.loads(json.dumps(plan))
mutant_b["tickets"][0]["status"] = "started"
sha_b = dump(mutant_b, "mutant_b.json")

print(f"PLAN_SHA={base_sha}")
print(f"MUT_A_SHA={sha_a}")
print(f"MUT_B_SHA={sha_b}")
PY
)"

plan_sha="$(printf '%s\n' "$shas" | sed -n 's/^PLAN_SHA=//p')"
mut_a_sha="$(printf '%s\n' "$shas" | sed -n 's/^MUT_A_SHA=//p')"
mut_b_sha="$(printf '%s\n' "$shas" | sed -n 's/^MUT_B_SHA=//p')"

if [ -z "$plan_sha" ] || [ -z "$mut_a_sha" ] || [ -z "$mut_b_sha" ]; then
  echo "check-paperclip-adapter: CANNOT-ASSESS — could not build the mutants" >&2
  printf '%s\n' "$shas" >&2
  exit 2
fi
if [ "$plan_sha" = "$mut_a_sha" ] || [ "$plan_sha" = "$mut_b_sha" ]; then
  echo "check-paperclip-adapter: CANNOT-ASSESS — a mutation did not change the bytes" >&2
  printf '%s\n' "$shas" >&2
  exit 2
fi

out_a="$(validate "$root" "$scratch/mutant_a.json" 2>&1)"
rc_a=$?
out_b="$(validate "$root" "$scratch/mutant_b.json" 2>&1)"
rc_b=$?

if [ "$rc_a" -eq 1 ] && printf '%s\n' "$out_a" | grep -qF "owner"; then
  echo "  OK    negative control A: dropping the required field 'owner' is refused by name"
else
  echo "check-paperclip-adapter: FAIL — negative control A passed; a dropped required field was not caught" >&2
  printf '%s\n' "$out_a" >&2
  exit 1
fi

if [ "$rc_b" -eq 1 ] && printf '%s\n' "$out_b" | grep -qiF "closed vocabulary"; then
  echo "  OK    negative control B: a status outside the closed vocabulary is refused by name"
else
  echo "check-paperclip-adapter: FAIL — negative control B passed; a closed-vocabulary drift was not caught" >&2
  printf '%s\n' "$out_b" >&2
  exit 1
fi

restore_sha="$(python3 - "$root" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root))

from integrations.paperclip import mapping as pc_mapping  # noqa: E402

plan = pc_mapping.build_plan(root, company_id="acme", session_id="gate-session")
payload = json.dumps(plan, indent=2, sort_keys=True).encode("utf-8")
print(hashlib.sha256(payload).hexdigest())
PY
)"

if [ "$restore_sha" != "$plan_sha" ]; then
  echo "check-paperclip-adapter: FAIL — the mapping is not deterministic across the control" >&2
  echo "  before=$plan_sha" >&2
  echo "  after =$restore_sha" >&2
  exit 1
fi
echo "  OK    mapping restored byte-identical (sha256 $plan_sha)"

echo "check-paperclip-adapter: OK — adapter conforms to the three seam schemas;"
echo "  transport shapes held offline; negative controls A (dropped 'owner') and"
echo "  B (closed-vocabulary status) both refused; mapping deterministic."
exit 0
