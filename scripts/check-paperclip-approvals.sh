#!/usr/bin/env bash
# check-paperclip-approvals.sh — the approvals projection gate (issue #416, EPIC #410).
#
# Upstream paperclip.ing models an approval as a first-class object; the fleet
# already holds the *authority* (a brain directive, a claim event, the control
# verb) but had no object to act on. integrations/paperclip/adapters/approvals/ is that
# projection, and the EPIC's rule is decisive: an approval is a PROJECTION of an
# existing authority, never a second one. A projection that nothing validates is
# a formality (no-false-green doctrine, GR-12), so this gate fails, by name, when
# the adapter drifts:
#
#   * the kind -> authority map still names exactly the fleet surfaces
#     (hire -> governance/dispatch, top-up -> .fleet/sent, override ->
#     fleet/control.py), and a kind with no authority behind it is refused;
#   * a clean tree projects and verifies, and is DETERMINISTIC (two projections
#     over one revision are byte-identical);
#   * the adapter has NO STORE — no write path in its production modules, so an
#     approval's state can only change by changing the authority it names;
#   * the adapter provokes its own failures (negative_control.py): a kind with no
#     authority, a projection with no record, a removed record, a double
#     approval and an unauthorised decider must each be refused by name.
#
# The gate also drives an independent CLI-level control: a mutated tree (an item
# granted twice) must make `cli.py verify` exit non-zero AND name the offender.
# If the mutant passes, this gate reports FAIL — a check that cannot fail is a
# formality.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
# CANNOT-ASSESS must never be reported as a pass.
#
# Usage: bash scripts/check-paperclip-approvals.sh
#
# ---knowledge---
# module_id: scripts.check-paperclip-approvals
# system: governance
# app: gates
# solution_class: enterprise
# patterns: [tri-state-exit, provoked-negative-control, no-false-green, named-refusal, deterministic, schema-validation]
# derives_from: null
# owner_sme: platform-sme
# tier: L1
# interfaces: [exit 0 OK, exit 1 NOT-OK, exit 2 CANNOT-ASSESS]
# invariants: ""
# gotchas: ""
# related: ["#410", "#416"]
# do_not_duplicate: null
# ---knowledge---
set -u

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 2

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-paperclip-approvals: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

adapter="integrations/paperclip/adapters/approvals"
if [ ! -d "$adapter" ]; then
  echo "check-paperclip-approvals: FAIL — $adapter/ is missing" >&2
  exit 1
fi

fail() { echo "check-paperclip-approvals: FAIL — $1" >&2; exit 1; }

# /tmp is writable and a fixed-name template cannot be recycled by a peer run.
scratch="/tmp/ao416-gate.$(date +%s%N).$"
mkdir "$scratch" || fail "cannot create scratch dir $scratch"
clean="$scratch/clean"
mutated="$scratch/mutated"
rm -rf "$clean" "$mutated"

# --- 1. the declared kind -> authority map --------------------------------
echo "== approvals: authority map =="
for entry in "hire:governance/dispatch" "top-up:.fleet/sent" "override:fleet/control.py"; do
  kind="${entry%%:*}"
  want="${entry#*:}"
  got="$(python3 - "$kind" <<'PY'
import sys
from integrations.paperclip.adapters.approvals.model import authority_for
print(authority_for(sys.argv[1]).surface)
PY
)" || fail "cannot read the authority for kind '$kind'"
  if [ "$got" != "$want" ]; then
    fail "kind '$kind' must name authority '$want', names '$got'"
  fi
  printf '  OK    %-9s -> %s\n' "$kind" "$got"
done

# A kind with no authority behind it must be refused, by name.
if python3 - <<'PY'
import sys
from integrations.paperclip.adapters.approvals.model import ApprovalRefused, authority_for
try:
    authority_for("transfer")
except ApprovalRefused as exc:
    if exc.reason == "no-authority" and "transfer" in exc.detail:
        sys.exit(0)
    sys.exit(1)
sys.exit(1)
PY
then
  echo "  OK    kind with no authority refused by name (transfer)"
else
  fail "a kind with no authority behind it was not refused by name"
fi

# --- 2. no store: the adapter has no write path ---------------------------
echo "== approvals: no second store =="
production_files=""
for name in model.py mapping.py verify.py schema.py cli.py __init__.py; do
  [ -f "$adapter/$name" ] && production_files="$production_files $adapter/$name"
done
[ -n "$production_files" ] || fail "no production modules found under $adapter/"
# shellcheck disable=SC2086
if grep -nE "write_text|os\.replace|shutil\.|mkdir|O_WRONLY|O_CREAT|O_APPEND|json\.dump\(" $production_files >/dev/null 2>&1; then
  # shellcheck disable=SC2086
  grep -nE "write_text|os\.replace|shutil\.|mkdir|O_WRONLY|O_CREAT|O_APPEND|json\.dump\(" $production_files >&2
  fail "the adapter's production modules contain a write path — an approval must have no store of its own"
fi
echo "  OK    $adapter production modules are read-only (no store)"

# --- 3. the negative controls provoke every claimed refusal ----------------
echo "== approvals: negative controls =="
if ! python3 "$adapter/negative_control.py"; then
  fail "the negative controls did not all provoke (GR-12)"
fi

# --- 4. a clean tree projects, verifies, and is deterministic -------------
echo "== approvals: clean-tree project + verify + determinism =="
python3 - "$clean" <<'PY' || fail "cannot build the clean fixture tree"
import sys
from pathlib import Path
from integrations.paperclip.adapters.approvals import fixtures
fixtures.build_tree(Path(sys.argv[1]))
PY

if ! python3 "$adapter/cli.py" --root "$clean" verify >/dev/null 2>&1; then
  python3 "$adapter/cli.py" --root "$clean" verify >&2
  fail "the clean fixture tree did not verify"
fi
first="$(python3 "$adapter/cli.py" --root "$clean" project 2>/dev/null)"
second="$(python3 "$adapter/cli.py" --root "$clean" project 2>/dev/null)"
if [ "$first" != "$second" ]; then
  fail "two projections over one revision differ (non-deterministic)"
fi
count="$(printf '%s' "$first" | python3 -c 'import json,sys; print(len(json.load(sys.stdin)["approvals"]))')"
echo "  OK    clean tree verified; ${count} approval(s); projection deterministic"
if [ "$count" -lt 2 ]; then
  fail "the clean tree projected ${count} approval(s); the fixture must exercise more than one"
fi

# --- 5. an independent CLI-level control: a mutant must fail, by name ------
echo "== approvals: gate control (mutant must fail, by name) =="
python3 - "$clean" "$mutated" <<'PY' || fail "cannot build the mutated fixture tree"
import shutil
import sys
from pathlib import Path
from integrations.paperclip.adapters.approvals import fixtures
source, target = Path(sys.argv[1]), Path(sys.argv[2])
shutil.copytree(source, target)
fixtures.write_record(
    target,
    ".board/claims/claim-416-again.json",
    {"event": "claim", "issue": 416, "agent": "mutant", "at": "2026-09-14T00:08:00Z"},
)
PY

python3 "$adapter/cli.py" --root "$mutated" verify > "$scratch/mutant.out" 2>&1
mutant_rc=$?
if [ "$mutant_rc" -eq 0 ]; then
  fail "a double approval verified clean — the gate cannot fail"
fi
if ! grep -q "double-approval" "$scratch/mutant.out"; then
  cat "$scratch/mutant.out" >&2
  fail "the mutant failed but did not name the offender (double-approval)"
fi
echo "  OK    double approval refused by name (exit $mutant_rc)"

echo "check-paperclip-approvals: OK"
exit 0
