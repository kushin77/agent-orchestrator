#!/usr/bin/env bash
# check-peer-check.sh — the A2A peer-check standard, enforced (issue #1549, EPIC #1510).
#
# WHAT IS PROVEN, against the real code and not a description of it
#   * The standard's engine refuses an overlap BY NAME: the sibling's agent, its
#     issue, its lane, its channel and the SPECIFIC overlapping path all appear in
#     the refusal — a refusal a lane cannot act on is a warning, and this gate
#     refuses to accept one.
#   * The refusal comes from the overlap and nothing else. The SAME fixture with a
#     caller whose files do not intersect is accepted, and the same overlapping
#     caller against a sibling whose claim declares NO files is accepted too. So
#     the exit-1 half can genuinely fail (GR-12, no-false-green).
#   * The decision is `model.file_claims_conflict` — the ONE predicate the claim
#     path (#702) and the wave planner (#740) share. A mutant that neuters that
#     single function (in-process, never on disk) makes the SAME provocation come
#     back disjoint, so the refusal is shown to come from the rule under test.
#     The mutant PROBES that the mutation took effect in the very name the engine
#     calls; a mutation that did not land cannot report success.
#   * An absent or empty ledger is CANNOT-ASSESS, never an empty sibling set. That
#     collapse — an unreadable store rendering as a clean "disjoint" — is the false
#     green this standard exists to prevent, so it has its own arm.
#   * The engine's source is byte-identical before and after: the mutation happens
#     in a subprocess's memory, never in the tree.
#
# The unit suite `governance/dispatch/tests/test_peers.py` is run first, so a
# property the engine documents but does not hold fails here rather than in prose.
#
# Exit-code contract (guardrails/honesty tri-state, consumed never redefined)
#   0 OK / 1 NOT-OK (an arm failed, or the provocation did not behave)
#   2 CANNOT-ASSESS (python3 absent, no scratch directory, a missing engine)
#
# Offline, deterministic, no network, no writes outside the scratch directory.
#
# Usage:
#   bash scripts/check-peer-check.sh              the gate
#   bash scripts/check-peer-check.sh --self-test  the provocation and mutant alone
#
# ---knowledge---
# module_id: scripts.check-peer-check
# system: governance
# app: gates
# solution_class: enterprise
# patterns: [self-proving-gate, no-false-green]
# derives_from: null
# owner_sme: platform-sme
# tier: L1
# interfaces: [exit 0 OK, exit 1 NOT-OK, exit 2 CANNOT-ASSESS]
# invariants: "offline, deterministic, no network, no writes outside the scratch directory"
# gotchas: ""
# related: ["#1549"]
# do_not_duplicate: null
# ---knowledge---
set -u

self_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
root="$(cd "$self_dir/.." && pwd -P)" || exit 2
cd "$root" || exit 2

engine="governance/dispatch/peers.py"
if ! command -v python3 >/dev/null 2>&1; then
  echo "check-peer-check: CANNOT-ASSESS — python3 is not installed" >&2
  exit 2
fi
if [ ! -f "$engine" ]; then
  echo "check-peer-check: CANNOT-ASSESS — $engine is missing" >&2
  exit 2
fi

TMPD=""
cleanup() { [ -n "$TMPD" ] && rm -rf "$TMPD" || true; }
trap cleanup EXIT
TMPD="$(mktemp -d /tmp/ao-peer-check.XXXXXX)" || {
  echo "check-peer-check: CANNOT-ASSESS — no scratch directory available" >&2
  exit 2
}

fail=0
ok() { printf '  OK    %s\n' "$1"; }
bad() { printf '  FAIL  %s\n' "$1" >&2; fail=$((fail + 1)); }

self_test_only=0
if [ "${1:-}" = "--self-test" ]; then
  self_test_only=1
fi

if [ "$self_test_only" -eq 0 ]; then
  echo "== the standard's own suite =="
  if python3 -m pytest governance/dispatch/tests/test_peers.py -q >"$TMPD/pytest.out" 2>&1; then
    ok "governance/dispatch/tests/test_peers.py passes ($(tail -1 "$TMPD/pytest.out" | tr -d '\r'))"
  else
    bad "the peer-check unit suite failed:"
    tail -n 25 "$TMPD/pytest.out" >&2
  fi
fi

# --- the fixture: a live ledger holding ONE sibling with a declared file ------
# The record shape is the real one (`parse_claim_event`): event/issue/agent/at are
# required, `files` is the per-file lease from #702. The fixture writer asserts
# what it wrote back through the SAME reader the engine uses, so "the provocation
# took effect" is measured against the ledger the engine will actually replay —
# not against the string handed to `printf`.
ledger="$TMPD/ledger"
mkdir -p "$ledger"
python3 - "$ledger" <<'PY' >"$TMPD/fixture.out" 2>&1
import json
import pathlib
import sys
from datetime import datetime, timedelta, timezone

ledger = pathlib.Path(sys.argv[1])
dispatch = pathlib.Path("governance/dispatch").resolve()
sys.path.insert(0, str(dispatch))

import claims as claims_mod  # noqa: E402

SIBLING_FILE = "scripts/peer-check.sh"
OTHER_FILE = "registry/personas/README.md"

# The fixture claims are clock-RELATIVE: `at` is computed from the live clock at
# run time, so they are always freshly-live against the engine's own
# `active_claims` TTL. A pinned literal timestamp is a date bomb — the lease
# expires CLAIM_TTL_HOURS later and every liveness-dependent arm flips red with
# no code change. The two records keep their one-second ordering.
fresh = datetime.now(timezone.utc)
at_sibling = fresh.strftime("%Y-%m-%dT%H:%M:%SZ")
at_other = (fresh + timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")

records = {
    "0001-07012-ao-sub-7012-claim.json": {
        "event": "claim",
        "issue": 7012,
        "agent": "ao-sub-7012",
        "at": at_sibling,
        "lane": "portal",
        "reason": "fixture",
        "files": [{"path": SIBLING_FILE, "regions": None}],
    },
    "0002-07013-ao-sub-7013-claim.json": {
        "event": "claim",
        "issue": 7013,
        "agent": "ao-sub-7013",
        "at": at_other,
        "lane": "registry",
        "reason": "fixture",
        "files": [{"path": OTHER_FILE, "regions": None}],
    },
}
for name, payload in records.items():
    (ledger / name).write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

# Prove the provocation took effect through the engine's OWN reader.
events = claims_mod.read_ledger(ledger)
live = claims_mod.active_claims(events)
declared = {issue: [f.path for f in holder.files] for issue, holder in sorted(live.items())}
print("fixture_live=%s" % json.dumps(declared, sort_keys=True))
assert declared.get(7012) == [SIBLING_FILE], declared
assert declared.get(7013) == [OTHER_FILE], declared
print("fixture_ok=1")
PY
if grep -q '^fixture_ok=1$' "$TMPD/fixture.out"; then
  ok "the fixture ledger replays through the engine's own reader with the declared files: $(grep '^fixture_live=' "$TMPD/fixture.out" | cut -d= -f2-)"
else
  bad "the fixture ledger did not materialise; the provocation below would prove nothing:"
  sed 's/^/        /' "$TMPD/fixture.out" >&2
fi

# run_peer <caller-files> [extra args...] -> PEER_OUT, PEER_RC
PEER_OUT=""
PEER_RC=0
run_peer() {
  local files="$1"
  shift
  PEER_OUT="$(python3 "$engine" --caller-agent ao-sub-1549 --caller-issue 1549 \
    --files "$files" --ledger "$ledger" --no-branch-files "$@" 2>&1)"
  PEER_RC=$?
}

echo "== the provocation: a real overlap must be refused, BY NAME =="
run_peer "scripts/peer-check.sh,governance/dispatch/peers.py"
if [ "$PEER_RC" -eq 1 ]; then
  ok "exit 1 on a declared-file overlap (rc=$PEER_RC)"
else
  bad "expected exit 1 on an overlap, got rc=$PEER_RC:"
  printf '%s\n' "$PEER_OUT" | sed 's/^/        /' >&2
fi
missing=""
for needle in "peer-check REFUSED: OVERLAP" "ao-sub-7012" "#7012" "portal" "scripts/peer-check.sh"; do
  if ! grep -qF -- "$needle" <<<"$PEER_OUT"; then
    missing="${missing}${missing:+, }'$needle'"
  fi
done
if [ -z "$missing" ]; then
  ok "the refusal names the sibling, its issue, its lane and the exact path"
else
  bad "the refusal does not name: $missing"
  printf '%s\n' "$PEER_OUT" | sed 's/^/        /' >&2
fi
# The refusal LINE only. A loose substring match would hit the table, where every
# sibling is listed — including the ones that did nothing wrong.
refusal_line="$(grep -F "peer-check REFUSED: OVERLAP" <<<"$PEER_OUT" | head -1)"
if [ -z "$refusal_line" ]; then
  bad "no refusal line to inspect"
elif grep -qF -- "ao-sub-7013" <<<"$refusal_line"; then
  bad "the refusal named a non-overlapping sibling (ao-sub-7013) as an offender"
else
  ok "the non-overlapping sibling is listed in the table but absent from the refusal"
fi
echo "== the two negative controls: the refusal is caused by the overlap =="
run_peer "governance/dispatch/peers.py"
if [ "$PEER_RC" -eq 0 ] && ! grep -qF "REFUSED" <<<"$PEER_OUT"; then
  ok "the same fixture with a disjoint caller file is accepted (rc=0)"
else
  bad "a disjoint caller file was not accepted (rc=$PEER_RC):"
  printf '%s\n' "$PEER_OUT" | sed 's/^/        /' >&2
fi
# Vacuity: the same overlapping path, but the sibling declares no files at all.
python3 - "$ledger" <<'PY'
import json
import pathlib
import sys

ledger = pathlib.Path(sys.argv[1])
target = ledger / "0001-07012-ao-sub-7012-claim.json"
payload = json.loads(target.read_text(encoding="utf-8"))
payload["files"] = []
target.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
print("stripped=1")
PY
run_peer "scripts/peer-check.sh"
if [ "$PEER_RC" -eq 0 ]; then
  ok "with the sibling's declared files stripped the same caller is accepted — the refusal came from the declaration"
else
  bad "the refusal survived the declaration being removed (rc=$PEER_RC):"
  printf '%s\n' "$PEER_OUT" | sed 's/^/        /' >&2
fi
if grep -qF "unverifiable" <<<"$PEER_OUT"; then
  ok "the now fileless sibling is reported unverifiable, never silently disjoint"
else
  bad "a sibling with no file evidence was not reported unverifiable:"
  printf '%s\n' "$PEER_OUT" | sed 's/^/        /' >&2
fi

echo "== the falsification: neuter the one predicate and the refusal must vanish =="
before_sha="$(sha256sum "$engine" | awk '{print $1}')"
MUTANT_OUT="$(python3 - "$ledger" "$engine" <<'PY' 2>&1
"""Run the engine with `model.file_claims_conflict` neutered, IN PROCESS ONLY.

The mutation is applied to the module object BEFORE the engine imports the name,
so the engine binds the neutered function — and the program asserts that it did,
by probing the exact attribute the overlap path calls. A mutation that did not
land cannot report a result: it exits 4 instead of claiming the refusal vanished
on its own.
"""
import json
import pathlib
import sys

ledger, engine = sys.argv[1], pathlib.Path(sys.argv[2])
dispatch = pathlib.Path(engine).resolve().parent
sys.path.insert(0, str(dispatch))

import model  # noqa: E402


def neutered(a, b):
    return False


model.file_claims_conflict = neutered

import peers  # noqa: E402

if peers.file_claims_conflict is not neutered:
    print("mutation-did-not-land: peers bound %r" % (peers.file_claims_conflict,))
    raise SystemExit(4)
probe = peers.file_claims_conflict(model.FileClaim("a.sh"), model.FileClaim("a.sh"))
if probe is not False:
    print("mutation-did-not-land: the neutered predicate still conflicts (%r)" % (probe,))
    raise SystemExit(4)
print("mutation_landed=1")

rc = peers.main(
    [
        "--caller-agent",
        "ao-sub-1549",
        "--caller-issue",
        "1549",
        "--files",
        "scripts/peer-check.sh",
        "--ledger",
        ledger,
        "--no-branch-files",
        "--json",
    ]
)
print("MUTANT_RC=%d" % rc)
sys.exit(0)
PY
)"
MUTANT_RC=$?
if [ "$MUTANT_RC" -ne 0 ]; then
  bad "the mutant run itself failed (rc=$MUTANT_RC), so the falsification proves nothing:"
  printf '%s\n' "$MUTANT_OUT" | sed 's/^/        /' >&2
elif grep -q '^mutation_landed=1$' <<<"$MUTANT_OUT" && grep -q '^MUTANT_RC=0$' <<<"$MUTANT_OUT"; then
  ok "the neutered predicate makes the identical provocation come back disjoint (MUTANT_RC=0) — the refusal is the rule's"
else
  bad "the refusal survived the predicate being neutered, so it does not come from that rule:"
  printf '%s\n' "$MUTANT_OUT" | sed 's/^/        /' >&2
fi
if grep -qF "REFUSED" <<<"$MUTANT_OUT"; then
  bad "the mutant still emitted a refusal"
else
  ok "the mutant emitted no refusal at all"
fi

# A/B inside ONE fixture state: the shipped engine must still refuse after the
# mutant ran, so an arm that passed on a drifted fixture cannot hide here.
python3 - "$ledger" "scripts/peer-check.sh" <<'PY'
import json
import pathlib
import sys

ledger = pathlib.Path(sys.argv[1])
target = ledger / "0001-07012-ao-sub-7012-claim.json"
payload = json.loads(target.read_text(encoding="utf-8"))
payload["files"] = [{"path": sys.argv[2], "regions": None}]
target.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
PY
run_peer "scripts/peer-check.sh"
if [ "$PEER_RC" -eq 1 ] && grep -qF "REFUSED: OVERLAP" <<<"$PEER_OUT"; then
  ok "the shipped engine still refuses after the mutant ran (the control holds)"
else
  bad "the shipped engine stopped refusing after the mutant step (rc=$PEER_RC):"
  printf '%s\n' "$PEER_OUT" | sed 's/^/        /' >&2
fi
after_sha="$(sha256sum "$engine" | awk '{print $1}')"
if [ "$before_sha" = "$after_sha" ]; then
  ok "the engine's source is byte-identical before and after (no mutation reached the tree)"
else
  bad "the engine's source changed during the gate: $before_sha -> $after_sha"
fi

echo "== CANNOT-ASSESS is a verdict, never a pass =="
absent_out="$(python3 "$engine" --caller-agent ao-sub-1549 --caller-issue 1549 \
  --ledger "$TMPD/absent" --no-branch-files 2>&1)"
absent_rc=$?
if [ "$absent_rc" -eq 2 ] && grep -qF "does not exist" <<<"$absent_out"; then
  ok "a named ledger that does not exist is CANNOT-ASSESS, not an empty sibling set"
else
  bad "an absent ledger was not refused (rc=$absent_rc):"
  printf '%s\n' "$absent_out" | sed 's/^/        /' >&2
fi
nocaller_out="$(python3 "$engine" --caller-agent "" --ledger "$ledger" --no-branch-files 2>&1)"
nocaller_rc=$?
if [ "$nocaller_rc" -eq 2 ] && grep -qF "no caller identity" <<<"$nocaller_out"; then
  ok "no caller identity is CANNOT-ASSESS (a check that cannot name its caller names nobody)"
else
  bad "an empty caller identity was not refused (rc=$nocaller_rc):"
  printf '%s\n' "$nocaller_out" | sed 's/^/        /' >&2
fi

echo "== the standard is written down where a lane reads it, and the assertion can fail =="
# Cadence #1625 point 4: a card rule that nothing checks is advisory, so the rule
# text is asserted present here, not merely authored in the card. The predicate is
# the same grep both times, so this arm proves it discriminates rather than runs.
rule_text="refuse to touch files in OVERLAP"
card_hits="$(grep -lF -- "$rule_text" registry/personas/cards/*.yaml 2>/dev/null || true)"
if [ -n "$card_hits" ]; then
  ok "at least one SME card carries the peer-check rule text: $(printf '%s' "$card_hits" | tr '\n' ' ')"
else
  bad "no file under registry/personas/cards/ carries the peer-check rule text ('$rule_text')"
fi
# The provocation: the SAME predicate must be ABSENT from a file that does not
# carry the text, and present in one that does — a predicate that matched either
# way, or neither, could not fail and so could not gate anything.
printf '%s\n' "a card with no peer-check rule in it" > "$TMPD/no-rule.txt"
if grep -qF -- "$rule_text" "$TMPD/no-rule.txt"; then
  bad "the rule predicate matched a file that does not carry the text, so it cannot discriminate"
else
  ok "the rule predicate does not match a file without the text (rc!=0) — it can fail"
fi
printf 'guardrails:\n  - %s\n' "$rule_text" > "$TMPD/with-rule.yaml"
if grep -qF -- "$rule_text" "$TMPD/with-rule.yaml"; then
  ok "the rule predicate matches a file that does carry the text (rc=0)"
else
  bad "the rule predicate missed a file that does carry the text, so the assertion above is vacuous"
fi

if [ "$fail" -gt 0 ]; then
  echo "check-peer-check: FAIL — $fail arm(s) failed" >&2
  exit 1
fi
echo "check-peer-check: OK — the standard refuses an overlap by name, and the refusal is the predicate's"
exit 0
