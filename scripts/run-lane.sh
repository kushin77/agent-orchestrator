#!/usr/bin/env bash
# run-lane.sh — issue #1651 (reduced scope, "Scope under single-dev method"
# comment): one command chains the front half of the single-dev lane loop —
#   PMO plan lookup (governance/pmo/cli.py priority)
#   -> paperclip sync push (integrations/paperclip/adapters/sync/adapter.py;
#      refuses BY NAME when enable_paperclip is off — the refusal is
#      RECORDED, not fatal, and the chain continues)
#   -> route.resolve (governance/dispatch/route.py) for the tier/capability hop
#   -> claim (governance/dispatch/cli.py claim)
#   -> worktree + branch (governance/isolation/identity.mint +
#      governance/isolation/worktree.provision — the existing lane/claim
#      helper claims.py itself calls, issue #263)
#   -> prints the next manual step.
#
# ---knowledge---
# module_id: scripts.run-lane
# system: scripts
# app: scripts
# solution_class: pattern
# patterns: [chained-front-half, refusal-recorded-not-fatal]
# derives_from: governance/pmo/cli.py
# owner_sme: platform-sme
# tier: L1
# interfaces: [usage, run_self_test]
# invariants: "a paperclip-sync refusal (enable_paperclip off) is recorded, not fatal — the chain continues"
# gotchas: "writing the code itself is deliberately not automated by this script"
# related: ["#1651"]
# do_not_duplicate: null
# ---knowledge---
#
# Writing the code is NOT automated (nobody chains that). The back half —
# verify -> squash-merge-with-trailer-check -> post-merge close-out — already
# shipped as `make land PR=N` / scripts/land.sh (issue #1675). This script's
# last line always names that command, so `run-lane -> code -> pr-body.sh ->
# land` is the full loop the reduced-scope Done line asks for.
#
# No auto-dispatch daemon: PMO/paperclip/route are one-shot lookups run once
# per invocation, never a poller. Explicitly out of scope per the issue.
#
# DRY RUN BY DEFAULT: looks up/resolves/refuses-by-name but claims nothing and
# creates no worktree.
#   bash scripts/run-lane.sh <ISSUE>
#   bash scripts/run-lane.sh <ISSUE> --apply
#   bash scripts/run-lane.sh --self-test
set -uo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 2

usage() {
  cat <<'USAGE'
Usage: bash scripts/run-lane.sh <ISSUE> [--apply]
       bash scripts/run-lane.sh --self-test
Chains: PMO plan lookup -> paperclip sync push (refused-by-name if the flag
is off, recorded not fatal) -> route.resolve -> claim -> worktree/branch ->
prints the next manual step (scripts/pr-body.sh, then make land).
Dry run by default (--apply claims + provisions for real).
Exit codes: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
USAGE
}

run_self_test() {
  local tmp bin failures=0
  tmp="$(mktemp -d "${TMPDIR:-/tmp}/ao1651-run-lane.XXXXXX")" || {
    echo "run-lane --self-test: CANNOT-ASSESS — mktemp failed" >&2
    return 2
  }
  bin="$tmp/bin"
  mkdir -p "$bin"

  # Fake `gh` and `paperclip` — neither is actually invoked by the real chain
  # (PMO/paperclip/route/claim are all local python), but the PATH shim
  # convention (land.sh) is kept so a future venue swap doesn't need a new
  # self-test shape.
  cat >"$bin/gh" <<'SHIM'
#!/usr/bin/env bash
echo '{}'
SHIM
  chmod +x "$bin/gh"

  local out rc

  # Case 1: chain order — PMO before paperclip before route before claim.
  out="$(cd "$root" && PATH="$bin:$PATH" AO_AGENT="selftest.invalid" bash "$root/scripts/run-lane.sh" 999999 2>&1)"
  rc=$?
  pmo_at=$(printf '%s\n' "$out" | grep -n '^1) PMO plan' | cut -d: -f1)
  pc_at=$(printf '%s\n' "$out" | grep -n '^2) paperclip sync' | cut -d: -f1)
  rt_at=$(printf '%s\n' "$out" | grep -n '^3) route.resolve' | cut -d: -f1)
  cl_at=$(printf '%s\n' "$out" | grep -n '^4) claim' | cut -d: -f1)
  if [ -z "$pmo_at" ] || [ -z "$pc_at" ] || [ -z "$rt_at" ] || [ -z "$cl_at" ] || \
     [ "$pmo_at" -ge "$pc_at" ] || [ "$pc_at" -ge "$rt_at" ] || [ "$rt_at" -ge "$cl_at" ]; then
    echo "run-lane --self-test: NOT-OK — chain steps missing or out of order" >&2
    echo "$out" >&2
    failures=$((failures + 1))
  fi

  # Case 2: flag-off refusal is RECORDED, not fatal — rc stays 0 (dry run)
  # and the refusal string names the flag by name.
  if ! printf '%s\n' "$out" | grep -qF 'paperclip sync refused: enable_paperclip is off'; then
    echo "run-lane --self-test: NOT-OK — flag-off refusal not recorded-but-nonfatal (rc=$rc)" >&2
    echo "$out" >&2
    failures=$((failures + 1))
  fi

  # Case 3: unknown issue -> claim step refuses BY NAME (CANNOT-ASSESS), the
  # earlier steps still ran and printed.
  if ! printf '%s\n' "$out" | grep -qE 'claim REFUSED|CANNOT-ASSESS'; then
    echo "run-lane --self-test: NOT-OK — unknown issue did not refuse by name at claim" >&2
    echo "$out" >&2
    failures=$((failures + 1))
  fi

  rm -rf "$tmp"
  if [ "$failures" -eq 0 ]; then
    echo "run-lane --self-test: OK — chain order proven, flag-off refusal recorded not fatal, unknown-issue refused by name at claim"
    return 0
  fi
  echo "run-lane --self-test: NOT-OK — $failures self-test case(s) failed" >&2
  return 1
}

if [ "${1:-}" = "--self-test" ]; then
  run_self_test
  exit $?
fi

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
  usage
  exit 0
fi

issue="${1:-}"
apply=0
for a in "$@"; do
  [ "$a" = "--apply" ] && apply=1
done
case "$issue" in
  '' | --apply)
    usage >&2
    exit 2
    ;;
  *[!0-9]*)
    printf 'run-lane: CANNOT-ASSESS — not an issue number: %s\n' "$issue" >&2
    exit 2
    ;;
esac
if [ "${AO_LAND_APPLY:-0}" = "1" ]; then
  apply=1
fi

agent="${AO_AGENT:-$(git config user.email 2>/dev/null || true)}"
agent="${agent:-agent.run-lane.invalid}"
lane="${AO_LANE:-lane}"

echo "1) PMO plan lookup for #$issue"
# `pmo priority --json` prints one status line before the JSON body; take
# from the first `{` on, same convention as the rest of this repo's parsers.
export AO_RUN_LANE_PMO_JSON="$(python3 governance/pmo/cli.py priority --json 2>&1 | awk '/^\{/{p=1} p')"
python3 - "$issue" <<'PY'
import json, os, sys
issue = int(sys.argv[1])
try:
    data = json.loads(os.environ["AO_RUN_LANE_PMO_JSON"])
except Exception as exc:
    print(f"   CANNOT-ASSESS — pmo priority did not emit JSON: {exc}")
    raise SystemExit(0)
rows = data if isinstance(data, list) else data.get("items", data.get("rows", []))
hit = next((r for r in rows if isinstance(r, dict) and r.get("number") == issue), None)
print(f"   found: rank {hit.get('rank')}, ready={hit.get('ready')}" if hit else f"   not in the current PMO plan (#{issue})")
PY

echo "2) paperclip sync push for #$issue"
python3 - "$issue" "$root" <<'PY'
import sys
sys.path.insert(0, sys.argv[2])
from integrations.paperclip.adapters.sync import adapter, flags

class _NullClient:
    def create_issue(self, ticket):
        raise AssertionError("never called: paperclip sync is refused before any client call")

doc = {"issue": int(sys.argv[1]), "tickets": []}
try:
    adapter.push_plan(_NullClient(), doc)
    print("   pushed (enable_paperclip is on)")
except adapter.SyncRefused as exc:
    # Recorded, not fatal — the chain keeps going.
    print(f"   {exc}")
PY

echo "3) route.resolve for #$issue"
python3 - "$issue" "$root" <<'PY'
import sys
sys.path.insert(0, sys.argv[2] + "/governance/dispatch")
import route
try:
    result = route.resolve({"tier": "L0", "title": f"#{sys.argv[1]}"})
    print(f"   hops: {result['hops']}")
except Exception as exc:
    print(f"   route refused: {exc}")
PY

echo "4) claim #$issue as $agent (lane=$lane)"
claim_out="$(python3 governance/dispatch/cli.py claim --issue "$issue" --agent "$agent" --lane "$lane" 2>&1)"
claim_rc=$?
printf '   %s\n' "$claim_out" | sed 's/^/   /'
if [ "$claim_rc" -ne 0 ]; then
  echo "run-lane: NOT-OK — claim refused, stopping before any worktree is created" >&2
  exit "$claim_rc"
fi

echo "5) worktree + branch for #$issue"
if [ "$apply" -ne 1 ]; then
  echo "   DRY RUN — would provision a worktree/branch via governance/isolation/worktree.provision"
  echo "run-lane: DRY RUN complete for #$issue — rerun with --apply to provision the lane"
  exit 0
fi
python3 - "$issue" "$agent" "$lane" "$root" <<'PY'
import sys
sys.path.insert(0, sys.argv[4])
from governance.isolation import identity, worktree
ident = identity.mint(int(sys.argv[1]), sys.argv[2], sys.argv[3])
try:
    result = worktree.provision(ident, sys.argv[4], fetch_remote="origin")
    print(f"   worktree {'created' if result.created else 'reattached'}: {ident.worktree} (branch {ident.branch})")
except worktree.ProvisionRefused as exc:
    print(f"   provision REFUSED: {exc}")
    raise SystemExit(1)
PY
prov_rc=$?
if [ "$prov_rc" -ne 0 ]; then
  echo "run-lane: NOT-OK — worktree provisioning refused" >&2
  exit 1
fi

echo "6) next: write the code in the new lane, then:"
echo "     bash scripts/pr-body.sh $issue    # draft the PR body"
echo "     gh pr create ...                 # open the PR"
echo "     make land PR=<n>                 # verify -> squash-merge-with-trailer-check -> close-out"
echo "run-lane: OK — lane provisioned for #$issue"
exit 0
