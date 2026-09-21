#!/usr/bin/env bash
# check-fleet-state.sh — the unified fleet-state projection gate (issue #323).
#
# The projection exists to answer, in one invocation, "what is in flight, what is
# orphaned, what is shelved, what is wedged" across the fleet's five stores. A
# projection that cannot be shown to go wrong is decoration, so this gate proves
# the mechanism against fixtures rather than trusting the source:
#
#   * a fixture with one item in EACH state — live, suspect, orphan, shelved and
#     wedged — projects each state from the store that owns it;
#   * a clean fixture exits 0, so the gate's failure path is not the only path;
#   * the orphaned, shelved and wedged items make it exit non-zero AND are NAMED
#     in the output, so the command is usable as a cron/health input;
#   * the self-control mutates a CLEAN fixture into an orphaned one and requires
#     the non-zero exit plus the offending issue number — then restores it and
#     requires the zero exit again (a gate that cannot fail is a formality);
#   * a second store disagreeing with the first is SHOWN, not silently resolved;
#   * the projection is read-only: a content hash of the whole fixture tree is
#     identical before and after.
#
# The projection joins the real stores; the fixtures are a real fixture tree read
# through `--root`, not a stub of the module.
#
# No network is used or required.
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-fleet-state.sh
set -uo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 2

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-fleet-state: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

for required in fleet/state.py fleet/tests/test_state.py docs/FLEET-STATE.md scripts/verify.sh Makefile; do
  if [ ! -f "$required" ]; then
    echo "check-fleet-state: FAIL — $required is missing" >&2
    exit 1
  fi
done

fail=0

# --- 1. the projection is declared institutionally --------------------------
declarations=(
  "docs/FLEET-STATE.md|lanes|sessions|lifecycle|claims|directives"
  "docs/FLEET-STATE.md|Derived, never a copy"
  "docs/FLEET-STATE.md|orphaned, shelved or wedged"
)
for entry in "${declarations[@]}"; do
  IFS='|' read -r -a parts <<< "$entry"
  file="${parts[0]}"
  missing=0
  for marker in "${parts[@]:1}"; do
    if ! grep -qFi -- "$marker" "$file"; then
      printf '  FAIL  %s (missing declaration: %s)\n' "$file" "$marker" >&2
      missing=1
    fi
  done
  if [ "$missing" -eq 0 ]; then
    echo "  OK    $file declares the projection contract"
  else
    fail=$((fail + 1))
  fi
done

# --- 2. the check is wired into the gate of record --------------------------
if grep -qF -- "'fleet-state|bash scripts/check-fleet-state.sh'" scripts/verify.sh; then
  echo "  OK    scripts/verify.sh runs the fleet-state check"
else
  echo "  FAIL  scripts/verify.sh does not run the fleet-state check" >&2
  fail=$((fail + 1))
fi

if grep -qE '^lint:.*[[:space:]]fleet-state([[:space:]]|$)' Makefile; then
  echo "  OK    Makefile lint chain includes fleet-state"
else
  echo "  FAIL  Makefile lint chain does not include fleet-state" >&2
  fail=$((fail + 1))
fi

if grep -qE '^fleet-state:' Makefile; then
  echo "  OK    Makefile defines the fleet-state target"
else
  echo "  FAIL  Makefile has no fleet-state target" >&2
  fail=$((fail + 1))
fi

# --- 3. the mechanism, exercised for real -----------------------------------
work="/tmp/fleet-state.$$.$(date +%s)"
if ! mkdir -p "$work" 2>/dev/null; then
  echo "check-fleet-state: CANNOT-ASSESS — cannot create a scratch directory" >&2
  exit 2
fi
trap 'rm -rf "$work"' EXIT

if ! python3 - "$root" "$work" <<'PY'
"""Fixture proofs for the fleet-state projection (issue #323)."""
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

repo_root = Path(sys.argv[1]).resolve()
work = Path(sys.argv[2])
state_py = repo_root / "fleet" / "state.py"

problems = []


def check(label, condition, detail=""):
    if condition:
        print(f"  OK    {label}")
    else:
        problems.append(label)
        print(f"  FAIL  {label}{(' — ' + detail) if detail else ''}", file=sys.stderr)


def dead_pid():
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    return child.pid


def iso_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def fresh(root):
    for sub in ("lanes", "sessions", "lifecycle", "sent", "done"):
        (root / ".fleet" / sub).mkdir(parents=True, exist_ok=True)
    (root / ".board" / "claims").mkdir(parents=True, exist_ok=True)


def lane(root, issue, session_id, agent, lane_name, branch, worktree):
    write_json(
        root / ".fleet" / "lanes" / f"{session_id}.json",
        {"session_id": session_id, "issue": issue, "agent_id": agent, "lane": lane_name,
         "branch": branch, "worktree": worktree},
    )


def session(root, issue, session_id, agent, lane_name, branch, worktree,
            pid, age_seconds=0.0, state_name="running"):
    write_json(
        root / ".fleet" / "sessions" / f"{session_id}.json",
        {"session_id": session_id, "issue": issue, "agent": agent, "lane": lane_name,
         "worktree": worktree, "branch": branch, "pid": pid,
         "at": time.time() - age_seconds, "state": state_name},
    )


def claim(root, issue, agent, lane_name="", directive_id=""):
    write_json(
        root / ".board" / "claims" / f"{issue:020d}-{issue:05d}-{agent}-claim.json",
        {"event": "claim", "issue": issue, "agent": agent, "at": iso_now(),
         "lane": lane_name, "ttl_hours": 24, "directive_id": directive_id},
    )


def run(root, *extra):
    result = subprocess.run(
        [sys.executable, str(state_py), "--root", str(root), *extra],
        capture_output=True, text=True,
    )
    return result.returncode, result.stdout, result.stderr


def tree_hash(root):
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_file():
            digest.update(str(path.relative_to(root)).encode("utf-8"))
            digest.update(path.read_bytes())
    return digest.hexdigest()


# --- 3a. a clean fixture exits 0 (the failure path is not the only path) ----
clean = work / "clean"
fresh(clean)
lane(clean, 101, "s101", "agent-live", "lane-live", "issue-101", "/w/101")
session(clean, 101, "s101", "agent-live", "lane-live", "issue-101", "/w/101", pid=os.getpid())

before = tree_hash(clean)
rc, out, err = run(clean)
after = tree_hash(clean)
check("a clean fixture exits 0", rc == 0, f"rc={rc} stderr={err.strip()[-200:]}")
check("the clean projection reports OK", "fleet-state: OK" in out)
check("the projection is read-only (fixture tree unchanged)", before == after)

rc_j, out_j, _ = run(clean, "--json")
payload = json.loads(out_j) if out_j.strip().startswith("{") else {}
check("--json emits the projection as JSON", rc_j == 0 and payload.get("blocking") == [])
check("a live item projects as live",
      any(i["issue"] == 101 and i["session_status"] == "live" for i in payload.get("items", [])))

# --- 3b. a disagreement between two stores is SHOWN -------------------------
disagree = work / "disagree"
fresh(disagree)
lane(disagree, 111, "s111", "agent-x", "lane-x", "issue-111-wrong", "/w/111")
session(disagree, 111, "s111", "agent-x", "lane-x", "issue-111", "/w/111", pid=os.getpid())
rc, out, _ = run(disagree)
check("a second store disagreeing makes the projection non-zero", rc == 1, f"rc={rc}")
check("the disagreement is named with both values",
      "lane-session-mismatch" in out and "issue-111-wrong" in out, out[-300:])
check("the disagreeing item is named", "#111" in out)

# --- 3c. one item in each state ---------------------------------------------
each = work / "each"
fresh(each)
lane(each, 201, "s201", "agent-live", "lane-live", "issue-201", "/w/201")
session(each, 201, "s201", "agent-live", "lane-live", "issue-201", "/w/201", pid=os.getpid())
lane(each, 202, "s202", "agent-sus", "lane-sus", "issue-202", "/w/202")
session(each, 202, "s202", "agent-sus", "lane-sus", "issue-202", "/w/202", pid=dead_pid())
lane(each, 203, "s203", "agent-orphan", "lane-orphan", "issue-203", "/w/203")
session(each, 203, "s203", "agent-orphan", "lane-orphan", "issue-203", "/w/203",
        pid=os.getpid(), age_seconds=60 * 60)
lane(each, 204, "s204", "agent-shelved", "lane-shelved", "issue-204", "/w/204")
session(each, 204, "s204", "agent-shelved", "lane-shelved", "issue-204", "/w/204",
        pid=os.getpid(), state_name="shelved")
claim(each, 204, "agent-shelved", "lane-shelved")
claim(each, 205, "agent-wedged")

rc, out, _ = run(each, "--json")
data = json.loads(out)
statuses = {item["issue"]: item["session_status"] for item in data["items"]}
check("one fixture item projects live", statuses.get(201) == "live", str(statuses.get(201)))
check("one fixture item projects suspect", statuses.get(202) == "suspect", str(statuses.get(202)))
check("one fixture item projects orphan", statuses.get(203) == "orphan", str(statuses.get(203)))
check("one fixture item projects shelved", statuses.get(204) == "shelved", str(statuses.get(204)))
finding_codes = {f["code"] for item in data["items"] for f in item["findings"]}
check("the wedged fixture item is a claim with no lane",
      "claim-without-lane" in finding_codes, ",".join(sorted(finding_codes)))
check("orphan, shelved and wedged all block", rc == 1, f"rc={rc}")
check("every blocking item is named",
      set(data["blocking"]) == {203, 204, 205}, str(data["blocking"]))
check("the suspect item does not block", 202 not in data["blocking"])

rc, out, _ = run(each)
check("the human view names the orphaned item with its state",
      "#203" in out and "orphan" in out)
check("the human view names the shelved item", "#204" in out and "shelved" in out)
check("the human view names the wedged item", "#205" in out and "claim-without-lane" in out)

# --- 3d. self-control: the gate can FAIL, and the offender is named ---------
control = work / "control"
fresh(control)
lane(control, 301, "s301", "agent-c", "lane-c", "issue-301", "/w/301")
session(control, 301, "s301", "agent-c", "lane-c", "issue-301", "/w/301", pid=os.getpid())

rc, out, _ = run(control)
check("self-control: the control fixture starts clean (rc 0)", rc == 0, f"rc={rc}")

# Mutate ONE fixture item to deliberately orphaned: the load-bearing provocation.
session(control, 301, "s301", "agent-c", "lane-c", "issue-301", "/w/301",
        pid=os.getpid(), age_seconds=60 * 60)
rc, out, err = run(control)
check("self-control: the provoked orphan makes the projection exit non-zero", rc == 1, f"rc={rc}")
check("self-control: the offending item is NAMED (#301)", "#301" in out, out[-300:])
check("self-control: the offending status is NAMED (orphan)",
      "orphan" in out and "NOT-OK" in out, out[-300:])

# ...and a deliberately SHELVED item is likewise non-zero and named.
session(control, 301, "s301", "agent-c", "lane-c", "issue-301", "/w/301",
        pid=os.getpid(), state_name="shelved")
claim(control, 301, "agent-c", "lane-c")
rc, out, _ = run(control)
check("self-control: the provoked shelved item exits non-zero and is named",
      rc == 1 and "#301" in out and "shelved" in out, f"rc={rc}")

# Restore the fixture to clean and require the zero exit again.
for stale in (control / ".board" / "claims").glob("*.json"):
    stale.unlink()
session(control, 301, "s301", "agent-c", "lane-c", "issue-301", "/w/301",
        pid=os.getpid(), state_name="running")
rc, out, _ = run(control)
check("self-control: restoring the fixture returns the projection to rc 0",
      rc == 0 and "fleet-state: OK" in out, f"rc={rc}")

if problems:
    print(f"check-fleet-state: {len(problems)} proof(s) failed", file=sys.stderr)
    sys.exit(1)
print("check-fleet-state: all fixture proofs passed")
PY
then
  fail=$((fail + 1))
fi

if [ "$fail" -ne 0 ]; then
  printf 'check-fleet-state: NOT-OK — %s problem(s)\n' "$fail" >&2
  exit 1
fi
echo "check-fleet-state: OK"
exit 0
