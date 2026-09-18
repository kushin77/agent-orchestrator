#!/usr/bin/env bash
# check-spawn-envelope.sh — the spawn-envelope gate (issue #793).
#
# THE DEFECT THIS EXISTS FOR
#   `governance/**` is a large, well-tested surface — claim ledger, lane
#   isolation, lifecycle close-out, reconcile, runaway guard, capacity, gate
#   admission — and NONE of it was applied by the act of spawning. The remote
#   path inlined governance as PROMPT PROSE inside `fleet/terminal.py`, where
#   nothing could check that a spawn had carried it, and the local path shared
#   none of it. Measured, on this box: nine worktrees running a gate, eight of
#   them running exactly ONE, and one running TWENTY-SIX. AO-GR-22 ("at most one
#   composite gate per worktree") was declared in a document and enforced
#   nowhere, which is why it held for eight lanes and failed silently for the
#   ninth. So a rule that is only in a doc cannot refuse anything.
#
# WHAT IS PROVEN HERE, AND HOW
#   1. The module exists, is declared institutionally, is WIRED into the gate of
#      record AND into the suite manifest, and both spawn paths consume it — with
#      no second copy of the governance prose left in `fleet/terminal.py`.
#   2. A spawn that cannot present a well-formed envelope is REFUSED, by name,
#      fail-closed with its own exit code (78, distinct from the 0/1/2 tri-state)
#      — provoked for EVERY required field, one at a time, at the CLI.
#   3. The LOCAL path is driven for real, against a scratch root with its own
#      board and its own claim: it is ADMITTED there, and REFUSED (naming
#      `claim.owner`) when that claim is absent.
#   4. The FLEET path builds the same document and renders it through the same
#      producer: `fleet/terminal.py::build_prompt` is driven for real, and its
#      output must EQUAL the envelope's rendering — not merely mention it. A
#      malformed envelope must refuse the spawn before any child exists.
#   5. `fleet/watchdog.py::run_in_flight()` decides on the RUN MARKER'S OWN
#      evidence: a leftover marker of a crashed run (loop pid alive, no child, a
#      stale beat) must NOT hold the lock, a live child must, and a heartbeat
#      saying `idle` beside a live child is REPORTED rather than silently losing.
#   6. The one-gate-per-worktree bound is provoked with a REAL second gate: the
#      envelope names the worktree, and a second `scripts/gate-lock.sh acquire` in
#      that same worktree must be REFUSED (rc 10) naming the holder.
#
# Every scratch artifact lives under a unique /tmp directory, and every gate-lock
# call gets its own permit store via `AO_GATE_LOCK_ROOT`, so this gate never
# touches the box's real permit store, the shared checkout's `.fleet/`, or a
# sibling lane's lock state.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-spawn-envelope.sh
set -uo pipefail

# The gate of record must not leave bytecode caches in the tree it is judging.
export PYTHONDONTWRITEBYTECODE=1

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

required_files=(
  "governance/spawn/__init__.py"
  "governance/spawn/model.py"
  "governance/spawn/sources.py"
  "governance/spawn/render.py"
  "governance/spawn/liveness.py"
  "governance/spawn/cli.py"
  "governance/spawn/README.md"
  "governance/spawn/tests/test_spawn_model.py"
  "governance/spawn/tests/test_consumption.py"
  "fleet/terminal.py"
  "fleet/watchdog.py"
  "scripts/gate-lock.sh"
)
for required in "${required_files[@]}"; do
  if [ ! -f "$required" ]; then
    echo "check-spawn-envelope: FAIL — $required is missing" >&2
    exit 1
  fi
done

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-spawn-envelope: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

fail=0

# --- 1. the contract is declared institutionally ---------------------------
# The rule lives in the execution plan (the lane-ownership document), in the gate
# of record, and in the suite manifest. A module nobody registers is inert, and
# an inert control is the exact defect this gate was filed for.
declare -a declarations=(
  "docs/EXECUTION-PLAN.md|spawn envelope|governance/spawn|check-spawn-envelope.sh|AO-GR-22"
  "scripts/verify.sh|scripts/check-spawn-envelope.sh"
  "scripts/pytest-suites.txt|governance/spawn"
  "fleet/terminal.py|spawn.produce(|spawn.render.prompt("
  "fleet/watchdog.py|spawn_liveness.runs_in_flight("
)

missing_declarations() {
  local file="$1" marker missing=0
  shift
  for marker in "$@"; do
    if ! grep -qF -- "$marker" "$file"; then
      printf '  FAIL  %s (missing declaration: %s)\n' "$file" "$marker" >&2
      missing=1
    fi
  done
  return "$missing"
}

for entry in "${declarations[@]}"; do
  IFS='|' read -r -a parts <<< "$entry"
  if missing_declarations "${parts[0]}" "${parts[@]:1}"; then
    echo "  OK    ${parts[0]} declares the spawn contract"
  else
    fail=$((fail + 1))
  fi
done

# --- 2. the governance prose has exactly ONE home --------------------------
# A second copy is how the local and fleet regimes drifted apart in the first
# place, so its absence is checked by name rather than trusted.
prose_leaks=(
  "STANDING MANDATE"
  "GATE OF RECORD: run issue"
  "LEAVE EVERY ARTIFACT TERMINAL"
  "Do exactly this, nothing else"
)
for sentence in "${prose_leaks[@]}"; do
  if grep -qF -- "$sentence" fleet/terminal.py; then
    echo "  FAIL  fleet/terminal.py still inlines governance prose: $sentence" >&2
    fail=$((fail + 1))
  fi
done
if [ "$fail" -eq 0 ]; then
  echo "  OK    the spawn prose has one home (governance/spawn/render.py)"
fi

# --- 3. the suite is declared AND reached from this gate -------------------
if grep -qxF "governance/spawn" scripts/pytest-suites.txt; then
  echo "  OK    scripts/pytest-suites.txt declares governance/spawn"
else
  echo "  FAIL  scripts/pytest-suites.txt does not declare governance/spawn" >&2
  fail=$((fail + 1))
fi

if ! env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q governance/spawn/tests >/dev/null 2>&1; then
  echo "  FAIL  governance/spawn/tests does not pass (it carries the fleet-path equality proof)" >&2
  fail=$((fail + 1))
else
  echo "  OK    governance/spawn/tests passes (the fleet prompt IS the rendered envelope)"
fi

# --- 4. the live proofs ----------------------------------------------------
work="/tmp/spawn-envelope.$$.$(date +%s)"
if ! mkdir -p "$work" 2>/dev/null; then
  echo "check-spawn-envelope: CANNOT-ASSESS — cannot create a scratch directory" >&2
  exit 2
fi
trap 'rm -rf "$work"' EXIT

if python3 - "$root" "$work" <<'PY'
"""Live proofs for the spawn envelope (issue #793).

Everything here drives the REAL entrypoints — `governance/spawn/cli.py` and
`scripts/gate-lock.sh` — against a scratch root and a scratch permit store: this
gate never touches the repository's own board, its ledger, or the box's real
gate permits.
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.dont_write_bytecode = True

repo = Path(sys.argv[1]).resolve()
work = Path(sys.argv[2]).resolve()

sys.path.insert(0, str(repo))
sys.path.insert(0, str(repo / "fleet"))
sys.path.insert(0, str(repo / "governance" / "dispatch"))

from governance.spawn import model, render  # noqa: E402

CLI = repo / "governance" / "spawn" / "cli.py"
GATE_ENTRY = repo / "scripts" / "gate-lock.sh"
ISOLATION = repo / "governance" / "isolation" / "cli.py"
ISSUE = 793
LANE = "spawn-envelope"
AGENT = "spawn-gate"

problems = []


def check(label, condition, detail=""):
    if condition:
        print(f"  OK    {label}", flush=True)
    else:
        problems.append(label)
        print(f"  FAIL  {label}{(' — ' + detail) if detail else ''}", file=sys.stderr, flush=True)


def note(label, detail):
    print(f"  note  {label}: {' '.join(str(detail).split())}", flush=True)


def stamp():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def scratch_board(root, *, claim=True):
    """A root with its OWN board: focus, snapshot, and a REAL claim event."""
    import claims as claim_ledger

    board = root / ".board"
    board.mkdir(parents=True, exist_ok=True)
    (board / "focus.json").write_text(
        json.dumps(
            {"active_epic": 708, "activated_at": stamp(), "wave_cap": 12, "max_agents": 0, "pooled": []}
        ),
        encoding="utf-8",
    )
    (board / "snapshot.json").write_text(
        json.dumps(
            {
                "generated_at": stamp(),
                "source": "check-spawn-envelope",
                "issues": [
                    {
                        "number": ISSUE,
                        "title": "spawn envelope gate fixture",
                        "state": "OPEN",
                        "milestone": "",
                        "labels": [],
                        "parent": 708,
                        "blocked_by": [],
                        "cross_refs": [],
                        "closed_at": None,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    if claim:
        claim_ledger.append_event(
            claim_ledger.ClaimEvent(
                event="claim", issue=ISSUE, agent=AGENT, at=stamp(), lane=LANE
            ),
            board / "claims",
        )
    return board


def session_env(worktree, store):
    """A REAL minted identity — `governance/isolation/cli.py env`, not a literal.

    The gate's own permit store travels with it, so the envelope's permit names
    the very store the later double-gate provocation is refused from.
    """
    result = subprocess.run(
        [sys.executable, str(ISOLATION), "env", "--issue", str(ISSUE), "--agent", AGENT,
         "--lane", LANE, "--json"],
        cwd=str(repo),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SystemExit(f"CANNOT-ASSESS — isolation env failed: {result.stderr.strip()}")
    env = dict(json.loads(result.stdout))
    env["AO_WORKTREE"] = str(worktree)
    env["AO_GATE_LOCK_ROOT"] = str(store)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def spawn_cli(args, root, env):
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        cwd=str(root),
        capture_output=True,
        text=True,
        env={**os.environ, **env},
    )


# --- 3a. the LOCAL path is driven for real, against its OWN board ----------
local_root = work / "local-root"
local_wt = work / "local-wt"
local_wt.mkdir(parents=True, exist_ok=True)
gate_store = work / "gate-store"
gate_store.mkdir(parents=True, exist_ok=True)
scratch_board(local_root)
local_env = session_env(local_wt, gate_store)

admitted = spawn_cli(
    ["open", "--issue", str(ISSUE), "--lane", LANE, "--agent", AGENT,
     "--root", str(local_root), "--worktree", str(local_wt), "--no-mint", "--no-claim", "--json"],
    local_root,
    local_env,
)
check(
    "the local spawn path ADMITS against its own root's board (rc 0)",
    admitted.returncode == model.EXIT_OK,
    f"rc={admitted.returncode} {admitted.stderr.strip()[-200:]}",
)
try:
    document = json.loads(admitted.stdout)
except json.JSONDecodeError:
    document = {}
    print("  FAIL  the admitted local spawn printed no document", file=sys.stderr)
    problems.append("the admitted local spawn printed no document")

check(
    "the admitted document is a well-formed envelope",
    bool(document) and model.validate(document) == [],
    str(model.validate(document))[:200] if document else "no document",
)
check(
    "the document carries the CLAIM the scratch ledger actually holds",
    document.get("claim", {}).get("owner") == AGENT,
    json.dumps(document.get("claim")),
)
check(
    "the document carries the EPIC from the board it was given",
    document.get("focus", {}).get("epic") == 708,
    json.dumps(document.get("focus")),
)
check(
    "the document names its own spawn path (local) and the trailer for its issue",
    document.get("spawn", {}).get("path") == "local"
    and str(document.get("trailer", "")).endswith(f"#{ISSUE}"),
    f"path={document.get('spawn', {}).get('path')} trailer={document.get('trailer')}",
)
note("the local spawn wrote", (local_root / ".fleet" / "spawn"))

# --- 3b. the same root WITHOUT the claim is REFUSED, by name ---------------
bare_root = work / "bare-root"
bare_wt = work / "bare-wt"
bare_wt.mkdir(parents=True, exist_ok=True)
(bare_root / ".board").mkdir(parents=True, exist_ok=True)

refused = spawn_cli(
    ["open", "--issue", str(ISSUE), "--lane", LANE, "--agent", AGENT,
     "--root", str(bare_root), "--worktree", str(bare_wt), "--no-mint", "--no-claim"],
    bare_root,
    local_env,
)
check(
    "a local spawn with no claim in its ledger is REFUSED (rc 78)",
    refused.returncode == model.EXIT_REFUSED,
    f"rc={refused.returncode} {refused.stdout.strip()[:200]}",
)
check(
    "the refusal names the empty field (`claim.owner`), not just \"malformed\"",
    "claim.owner" in refused.stderr,
    refused.stderr.strip()[-300:],
)
check(
    "the refusal is invisible to the pass/fail tri-state (78 is neither 0, 1 nor 2)",
    model.EXIT_REFUSED not in (model.EXIT_OK, model.EXIT_NOT_OK, model.EXIT_CANNOT_ASSESS),
)
note("the refusal was", refused.stderr.strip().splitlines()[0] if refused.stderr.strip() else "")

# --- 3c. EVERY required field is refused BY NAME, one at a time ------------
if document:
    target = work / "envelope.json"
    target.write_text(model.dumps(document), encoding="utf-8")
    unnamed = []
    for field in model.REQUIRED_FIELDS:
        result = spawn_cli(["check", "--file", str(target), "--without", field], local_root, local_env)
        if result.returncode != model.EXIT_REFUSED or field not in result.stderr:
            unnamed.append(f"{field} (rc={result.returncode})")
    check(
        f"every one of the {len(model.REQUIRED_FIELDS)} required fields is refused BY NAME",
        not unnamed,
        "; ".join(unnamed),
    )
    # The composer's refusals: a present field whose VALUE cannot be a real spawn.
    mutations = {
        "schema": lambda d: d.update(schema="spawn-envelope/v0"),
        "trailer": lambda d: d.update(trailer="Refs kushin77/agent-orchestrator#1"),
        "session.branch": lambda d: d["session"].update(branch="feature/whatever"),
        "capacity": lambda d: d["capacity"].update(assessed=False, problems=["unmeasurable"]),
        "budget.cap": lambda d: d["budget"].update(cap=0),
        "spawn.path": lambda d: d["spawn"].update(path="teleport"),
    }
    wrong = []
    for field, mutate in mutations.items():
        doc = json.loads(model.dumps(document))
        mutate(doc)
        bad = work / f"mutant-{field.replace('.', '-')}.json"
        bad.write_text(json.dumps(doc), encoding="utf-8")
        result = spawn_cli(["check", "--file", str(bad)], local_root, local_env)
        if result.returncode != model.EXIT_REFUSED or field not in result.stderr:
            wrong.append(f"{field} (rc={result.returncode})")
    check(
        "a well-formed-but-wrong document is refused by the field at fault",
        not wrong,
        "; ".join(wrong),
    )

    # --- 3d. the FLEET path builds the SAME document and renders it --------
    # `fleet/terminal.py::build_prompt` is driven for real, with its sources
    # pointed at the very document the local path produced. Its output must EQUAL
    # the producer's rendering of that document — a paraphrase fails.
    import terminal  # noqa: E402  (the loop script, imported as it is run)

    from governance.spawn import sources  # noqa: E402

    fields = {key: document[key] for key in model.REQUIRED_FIELDS}
    fields["spawn"] = document["spawn"]
    original_collect = sources.collect
    sources.collect = lambda **_: dict(fields)
    try:
        directive = {
            "id": "check-spawn-envelope-directive",
            "model": {"tier": "flash", "thinking": "none"},
            "task": {"issue": ISSUE, "lane": LANE},
            "body": "gate fixture order",
            "control": None,
        }
        prompt = terminal.build_prompt(directive, AGENT, local_wt, local_env, {})
        fleet_document = terminal.build_envelope(directive, AGENT, local_wt, local_env, {})
        expected = render.prompt(
            fleet_document,
            standing=terminal.load_standing_body(),
            directive_body="gate fixture order",
            directive_id="check-spawn-envelope-directive",
            tier="flash",
            thinking="none",
            context_block="",
        )
        check(
            "the FLEET prompt IS the envelope's rendering, byte for byte",
            prompt == expected,
            f"lengths {len(prompt)} vs {len(expected)}",
        )
        check(
            "the fleet path renders the SAME document the local path produced",
            {k: v for k, v in fleet_document.items() if k != "produced_at"}
            == {k: v for k, v in document.items() if k != "produced_at"},
            "the two spawn paths disagree about what a spawn carries",
        )
        # The refusal, on the fleet path, BEFORE any child exists.
        sources.collect = lambda **_: {k: v for k, v in fields.items() if k != "claim"}
        refused_rc, refused_out = terminal.run_once(
            directive, "claude -p", 5.0, False, AGENT, local_wt, local_env,
            {"dispatch": {"runner": "claude -p"}}, {},
        )
        check(
            "the loop REFUSES a malformed envelope with its own exit code, spawning nothing",
            refused_rc == terminal.RC_REFUSED and "spawn refused" in refused_out,
            f"rc={refused_rc} {refused_out[:200]}",
        )
        check(
            "the loop's refusal names the field at fault (`claim`)",
            "claim" in refused_out,
            refused_out[:200],
        )
        sources.collect = lambda **_: {k: v for k, v in fields.items() if k != "verify"}
        refused_rc, refused_out = terminal.run_once(
            directive, "claude -p", 5.0, False, AGENT, local_wt, local_env,
            {"dispatch": {"runner": "claude -p"}}, {},
        )
        check(
            "a second, different field is refused the same way (`verify`)",
            refused_rc == terminal.RC_REFUSED and "verify" in refused_out,
            f"rc={refused_rc} {refused_out[:200]}",
        )
    finally:
        sources.collect = original_collect

# --- 3e. the watchdog decides on the MARKER'S OWN evidence -----------------
import watchdog  # noqa: E402

runs = work / "runs"
runs.mkdir(parents=True, exist_ok=True)
import os as _os  # noqa: E402

leftover = runs / "leftover.json"
leftover.write_text(
    json.dumps({"issue": 234, "pid": _os.getpid(), "child_pid": None, "ts": "2026-09-15T00:00:00Z"}),
    encoding="utf-8",
)
original_runs = watchdog.RUNS_DIR
watchdog.RUNS_DIR = runs
try:
    check(
        "a leftover marker of a crashed run does NOT hold the drift lock (loop pid alive, no child)",
        watchdog.run_in_flight() is False,
        "the marker's loop pid alone kept the lock",
    )
    leftover.write_text(
        json.dumps({"issue": 234, "pid": _os.getpid(), "child_pid": _os.getpid(),
                    "ts": "2026-09-15T00:00:00Z"}),
        encoding="utf-8",
    )
    check(
        "a LIVE child does hold it — the evidence that needs no clock",
        watchdog.run_in_flight() is True,
    )
    beat = work / "sister.heartbeat.json"
    beat.write_text(json.dumps({"pid": _os.getpid(), "state": "idle", "runs": 0}), encoding="utf-8")
    report = watchdog.spawn_liveness.contradiction(watchdog.read_beat(beat), runs)
    check(
        "a heartbeat saying `idle` beside a live child is REPORTED, never a silent win",
        isinstance(report, str) and "CONTRADICTION" in report,
        str(report),
    )
finally:
    watchdog.RUNS_DIR = original_runs

# --- 3f. the one-gate bound is provoked with a REAL second gate ------------
bound_wt = Path(document["worktree"]) if document else local_wt
bound_wt.mkdir(parents=True, exist_ok=True)


def gate(*args, **overrides):
    env = {**os.environ, "AO_GATE_LOCK_ROOT": str(gate_store), "PYTHONDONTWRITEBYTECODE": "1"}
    env.update({key: str(value) for key, value in overrides.items()})
    return subprocess.run(
        ["bash", str(GATE_ENTRY), *args], capture_output=True, text=True, env=env, timeout=60
    )


first = gate("acquire", "--worktree", str(bound_wt), "--issue", str(ISSUE), "--owner-pid", str(_os.getpid()))
check(
    "a gate in the worktree the ENVELOPE names is admitted (rc 0)",
    first.returncode == 0 and "ADMITTED" in first.stdout,
    f"rc={first.returncode} {first.stderr.strip()[:200]}",
)
second = gate("acquire", "--worktree", str(bound_wt), "--issue", str(ISSUE), "--owner-pid", str(_os.getpid()))
check(
    "a REAL second gate in that same worktree is REFUSED (rc 10)",
    second.returncode == 10 and "REFUSED" in second.stderr,
    f"rc={second.returncode} {second.stderr.strip()[:200]}",
)
check(
    "the refusal names the holder, so the bound is evidence and not a sentence",
    "holds it now" in second.stderr or "pid " in second.stderr,
    second.stderr.strip()[:200],
)
check(
    "the envelope STATES the bound whose refusal was just provoked (AO-GR-22)",
    "AO-GR-22" in str(document.get("gate", {}).get("bound", ""))
    and "one composite gate per worktree" in str(document.get("gate", {}).get("bound", "")),
    str(document.get("gate")),
)
check(
    "the envelope's permit names the store the refusal came from",
    document.get("capacity", {}).get("permit", {}).get("store") == str(gate_store),
    str(document.get("capacity", {}).get("permit")),
)
check(
    "the envelope's permit names the very LOCK the second gate was refused on",
    document.get("capacity", {}).get("permit", {}).get("lock")
    == str(gate_store / "worktrees" / (document.get("capacity", {}).get("permit", {}).get("worktree_key", "") + ".lock")),
    str(document.get("capacity", {}).get("permit")),
)
released = gate("release", "--worktree", str(bound_wt), "--owner-pid", str(_os.getpid()))
check(
    "the first gate releases the slot, so the refusal was the bound and not a leak",
    released.returncode == 0 and "RELEASED" in released.stdout,
    released.stdout.strip()[:200],
)

if problems:
    print(f"  ({len(problems)} live proof(s) failed)", file=sys.stderr)
    raise SystemExit(1)
raise SystemExit(0)
PY
then
  :
else
  fail=$((fail + 1))
fi

if [ "$fail" -gt 0 ]; then
  echo "check-spawn-envelope: FAIL ($fail violation(s))" >&2
  exit 1
fi
echo "check-spawn-envelope: OK — one versioned envelope from one producer is consumed by both spawn paths, every required field is refused BY NAME with its own exit code, the local path is admitted and refused for real against its own board, the fleet prompt IS that envelope's rendering, run_in_flight() decides on the marker's own evidence and reports the contradiction, and a real second gate in the envelope's worktree is refused (AO-GR-22)"
exit 0
