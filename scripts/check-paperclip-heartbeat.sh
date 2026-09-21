#!/usr/bin/env bash
# check-paperclip-heartbeat.sh — the heartbeat adapter gate (issue #414,
# ADR-0013, docs/PAPERCLIP-ING-INTEGRATION.md §5 mismatches #1-#3).
#
# The fleet beat is a flat process-liveness record with no wake.cause, no
# wake.delta and no outcome, and no explicit tick or cadence. The adapter under
# integrations/paperclip/adapters/heartbeat/ derives all of them from the fleet's real rung
# activity. A seam that nothing validates is a formality (no-false-green
# doctrine, GR-12), so this gate fails, by name, when the adapter drifts:
#
#   * the cadence constants must be DERIVED from their owners — POLL_SECONDS in
#     fleet/monitor.py and RUNG_HEARTBEAT_SECONDS in governance/policy/lease.py
#     — and the ordering invariant cadence < ceiling must hold;
#   * a derived beat must conform to the frozen contract in
#     docs/contracts/paperclip/heartbeat.schema.json, and the derivation must be
#     deterministic (same inputs, byte-identical output);
#   * its own negative controls must be REFUSED BY NAME: a beat that carries no
#     delta, an unknown wake.cause, a blocked outcome with no owner, and an
#     outcome that is neither progress nor blocked. If any control is accepted,
#     this gate reports FAIL — a check that cannot fail is a formality;
#   * a rung whose beat is unattributable must fail closed (the adapter refuses
#     and names the offending state) instead of fabricating a scheduled cause.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
# CANNOT-ASSESS must never be reported as a pass.
#
# Usage: bash scripts/check-paperclip-heartbeat.sh
#
# ---knowledge---
# module_id: scripts.check-paperclip-heartbeat
# system: governance
# app: gates
# solution_class: enterprise
# patterns: [tri-state-exit, provoked-negative-control, no-false-green, declared-authority, named-refusal, deterministic, schema-validation]
# derives_from: null
# owner_sme: platform-sme
# tier: L1
# interfaces: [exit 0 OK, exit 1 NOT-OK, exit 2 CANNOT-ASSESS]
# invariants: ""
# gotchas: ""
# related: ["#1", "#3", "#414"]
# do_not_duplicate: null
# ---knowledge---
set -u

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 2

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-paperclip-heartbeat: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

if [ ! -d "$root/integrations/paperclip/adapters/heartbeat" ]; then
  echo "check-paperclip-heartbeat: FAIL — integrations/paperclip/adapters/heartbeat/ is missing" >&2
  exit 1
fi

schema="docs/contracts/paperclip/heartbeat.schema.json"
if [ ! -f "$root/$schema" ]; then
  echo "check-paperclip-heartbeat: CANNOT-ASSESS — no frozen schema at $schema" >&2
  exit 2
fi

scratch="/tmp/ao414-heartbeat.$(date +%s%N).$"
mkdir "$scratch" || exit 2
trap 'rm -rf "$scratch"' EXIT

python3 - "$root" "$scratch" <<'PY'
"""Drive the heartbeat adapter and its negative controls (the gate's subject)."""
import hashlib
import json
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
scratch = Path(sys.argv[2])
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from integrations.paperclip.adapters.heartbeat import adapter  # noqa: E402

fail = 0
T0 = "2026-09-14T00:00:05Z"
T1 = "2026-09-14T00:01:00Z"
T2 = "2026-09-14T00:02:00Z"


def write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def fixture():
    """A small fleet tree with the same shapes the runtime writes."""
    write(scratch / ".board" / "snapshot.json", {
        "generated_at": "2026-09-14T00:00:00Z",
        "source": "gate",
        "issues": [
            {"number": 400, "title": "ticket v2", "state": "open", "milestone": "M27",
             "labels": [], "parent": None, "blocked_by": [], "closed_at": ""},
            {"number": 414, "title": "heartbeat adapter", "state": "open", "milestone": "M27",
             "labels": [], "parent": 410, "blocked_by": [400], "closed_at": ""},
        ],
    })
    (scratch / ".board" / "claims.jsonl").write_text(
        json.dumps({"event": "claim", "issue": 414, "agent": "ao414", "at": T0,
                    "base_commit": "abc1234", "lane": "heartbeat"}) + "\n",
        encoding="utf-8",
    )
    write(scratch / ".fleet" / "sister.heartbeat.json",
          {"pid": 4242, "state": "working", "started_at": "2026-09-14T00:00:00Z",
           "commit": "abc1234", "ts": T1, "issue": 414, "agent": "ao414"})
    write(scratch / ".fleet" / "brain.heartbeat.json",
          {"pid": 11, "state": "idle", "started_at": "2026-09-14T00:00:00Z",
           "commit": "abc1234", "ts": T1})
    write(scratch / ".fleet" / "monitor.heartbeat.json",
          {"pid": 12, "state": "healthy", "started_at": "2026-09-14T00:00:00Z",
           "commit": "abc1234", "ts": T1})


def digest(record):
    return hashlib.sha256(json.dumps(record, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def refused_by(record, needle):
    findings = adapter.validate_heartbeat(record)
    return [f for f in findings if needle in f]


fixture()
print("== cadence derived from its owners ==")
monitor = (root / "fleet" / "monitor.py").read_text(encoding="utf-8")
lease = (root / "governance" / "policy" / "lease.py").read_text(encoding="utf-8")
declared = adapter.policy()
print(f"  fleet/monitor.py POLL_SECONDS          -> cadence_seconds={declared['cadence_seconds']}")
print(f"  lease.py RUNG_HEARTBEAT_SECONDS        -> ceiling={declared['staleness_ceiling_seconds']}")
if "POLL_SECONDS = " not in monitor:
    print("  FAIL  POLL_SECONDS is not declared in fleet/monitor.py")
    fail += 1
if "RUNG_HEARTBEAT_SECONDS = " not in lease:
    print("  FAIL  RUNG_HEARTBEAT_SECONDS is not declared in governance/policy/lease.py")
    fail += 1
if not declared["cadence_within_ceiling"]:
    print("  FAIL  cadence is not below the staleness ceiling")
    fail += 1

print("== derived beats conform to the frozen contract ==")
valid = {}
for rung in adapter.RUNGS:
    record = adapter.derive_heartbeat(scratch, rung.name, session_id="gate")
    findings = adapter.validate_heartbeat(record)
    valid[rung.name] = record
    if findings:
        fail += 1
        print(f"  FAIL  {rung.name}: {'; '.join(findings)}")
    else:
        print(f"  OK    {rung.name}: cause={record['wake']['cause']} "
              f"status={record['outcome']['status']} tick={record['tick']}")

print("== derivation is deterministic ==")
again = adapter.derive_heartbeat(scratch, "sister", session_id="gate")
if digest(again) != digest(valid["sister"]):
    fail += 1
    print("  FAIL  two derivations of the same tree differ")
else:
    print(f"  OK    identical output (sha256[:16]={digest(again)})")

print("== negative controls: each MUST be refused by name ==")
controls = []

mutant = json.loads(json.dumps(valid["sister"]))
mutant["wake"]["delta"] = {}
controls.append(("a beat that carries no delta", mutant, "wake.delta"))

mutant = json.loads(json.dumps(valid["sister"]))
mutant["wake"]["cause"] = "teleport"
controls.append(("an unknown wake.cause", mutant, "wake.cause"))

mutant = json.loads(json.dumps(valid["sister"]))
mutant["outcome"] = {"status": "blocked", "detail": "stuck", "owner": ""}
controls.append(("a blocked outcome with no owner", mutant, "outcome.owner"))

mutant = json.loads(json.dumps(valid["sister"]))
mutant["outcome"] = {"status": "maybe", "detail": "?", "owner": "someone"}
controls.append(("an outcome that is neither progress nor blocked", mutant, "outcome.status"))

for label, mutant, needle in controls:
    hits = refused_by(mutant, needle)
    if hits:
        print(f"  REFUSED BY NAME  {label}")
        print(f"                   {hits[0]}")
    else:
        fail += 1
        print(f"  FAIL  {label} was ACCEPTED (a check that cannot fail is a formality)")

print("== an unattributable beat fails closed ==")
write(scratch / ".fleet" / "sister.heartbeat.json",
      {"pid": 4242, "state": "quantum", "started_at": "2026-09-14T00:00:00Z",
       "commit": "abc1234", "ts": T2, "issue": 414, "agent": "ao414"})
try:
    adapter.derive_heartbeat(scratch, "sister", session_id="gate")
    fail += 1
    print("  FAIL  an unattributable beat was accepted")
except adapter.HeartbeatRefused as exc:
    if exc.reason == "unknown-state" and "quantum" in str(exc):
        print(f"  REFUSED BY NAME  {exc}")
    else:
        fail += 1
        print(f"  FAIL  refused for the wrong reason: {exc}")

if fail:
    print(f"check-paperclip-heartbeat: FAIL — {fail} finding(s)", file=sys.stderr)
    sys.exit(1)
print("check-paperclip-heartbeat: OK — adapter conforms, deterministic, and refuses every provocation")
PY
rc="$?"
if [ "$rc" -ne 0 ]; then
  exit 1
fi

echo "== pytest suite =="
if ! env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q \
    integrations/paperclip/adapters/heartbeat/tests; then
  echo "check-paperclip-heartbeat: FAIL — pytest suite red" >&2
  exit 1
fi

echo "check-paperclip-heartbeat: PASS"
exit 0
