#!/usr/bin/env bash
# check-dispatch-reconcile.sh — `sent` is not `done` (issue #796).
#
# THE DEFECT THIS EXISTS FOR
#   `fleet/brain.py::dispatch` refused on marker EXISTENCE, and the marker it
#   wrote (`write_marker(marker, order, "sent")`) was permanent. At-most-once is
#   correct for DELIVERY; the defect was that a directive which completed and one
#   which DIED (quarantined, dead-lettered, a run whose `child_pid` was never set)
#   were indistinguishable, because the only surviving evidence was that the send
#   happened. Measured 2026-09-15: the brain reported itself idle for 1535s while
#   its own idle path found three ready issues (#132/#133/#786) — every one
#   suppressed for ever by `.fleet/brain/dispatched/advance-*.json`, and nothing
#   printed. For #786 the terminal artifact
#   `.fleet/dead-letter/brain-directive-38136664….json` (attempts: 5, the cap)
#   proved the directive had been retired while its marker still read "sent".
#
# WHAT IS PROVEN (against the real modules, not a description of them)
#   The driver drives the REAL `fleet/markers.py` and `fleet/brain.py` against a
#   scratch runtime root (`AO_FLEET_DIR`), a scratch board snapshot and a scratch
#   claim ledger — no live fleet, no board fetch, no network. Both halves are
#   provoked:
#     * a marker whose issue is OPEN with no live claim, no live run and no queued
#       directive is classified STALE and re-armed (counted), and the re-arm
#       actually sends through the real channel — so the issue leaves the welded
#       state;
#     * a marker with LIVE evidence (a fresh run beat, a live claim, a queued
#       directive) stays suppressed and is never re-armed. Asserting the first
#       half alone would pass a "reconciler" that simply ignored the marker set,
#       which is why this half is a probe of its own;
#     * the re-arm is BOUNDED: the budget is #723's harvested one, and the end
#       state is `dead` — parked WITH THE ISSUE NAMED, plus the verb that lifts it;
#     * an unreadable store is CANNOT-ASSESS and never re-arms;
#     * the live idle path (`advance_ready`) prints both a re-arm and a
#       suppression naming the marker state — never a silent `continue`;
#     * `.fleet/sent/` (an ARCHIVE) is never read as "still in flight" — reading
#       it would suppress every order for ever;
#     * the LEDGER is asked in the funnel: an order for a unit a LIVE claim holds is
#       refused by name and reaches neither the marker set nor the channel, while an
#       open, unclaimed unit still dispatches (#861, acceptance item 3 of #366);
#     * a stranded authorisation whose issue is CLOSED with no change of its own
#       (superseded) is retirable and the move is STAMPED with the reason, the
#       successor and the board edition — and an OPEN issue is still REFUSED, which
#       is #821's invariant and the negative control that stops the new mode being a
#       bypass (#861).
#
# PROVOCATION (the gate must be able to fail — GR-12, no-false-green)
#   Four mutants, each applied by literal replacement to a scratch copy of the tree
#   (the replacement is asserted to have happened, so a mutation that never applied
#   cannot be reported as "the gate caught it"):
#     * NEVER-STALE     — the re-arm grace is set beyond any age, so nothing is
#       ever stale; the driver MUST fail STALE-REARMED;
#     * LIVENESS-IGNORED — the live evidence is ignored, so a running directive is
#       re-armed; the driver MUST fail LIVE-SUPPRESSED;
#     * CLAIM-GUARD-REMOVED — the ledger guard is taken out of the funnel, so a
#       second authorisation is minted for a unit another lane holds; the driver
#       MUST fail DISPATCH-REFUSES-CLAIMED;
#     * SUPERSESSION-BYPASS — the OPEN-issue refusal is removed from the retire
#       mode's contract, so the new terminal verb retires live work; the driver MUST
#       fail RETIRE-REFUSES-OPEN.
#   The mutant tree is a full layout: `fleet/` is always a copy, the mutated file's
#   own top-level directory (`fleet/` or `governance/`) is a copy too, and the rest
#   is symlinked — because `fleet/brain.py` resolves its own repo root from its file
#   location, so a flat copy would fail to import and the provocation would prove
#   nothing.
#
# No network. No writes outside the scratch directory. Exit-code contract:
# 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-dispatch-reconcile.sh
set -uo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-dispatch-reconcile: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

for required in fleet/markers.py fleet/brain.py fleet/channel.py fleet/runaway.py \
                governance/lifecycle/directive.py fleet/tests/test_dispatch_markers.py \
                scripts/verify.sh; do
  if [ ! -f "$required" ]; then
    echo "check-dispatch-reconcile: FAIL — $required is missing" >&2
    exit 1
  fi
done

# The scratch tree name is the sanctioned fleet idiom, NOT a `mktemp` template: a
# template whose placeholder is a run of one letter trips this repo's OWN
# unfinished-marker scan, so the gate would fail docs-lint. `mkdir` without `-p`
# refuses loudly instead of silently reusing another run's tree.
work="/tmp/ao-dispatch-reconcile.$(date +%s%N).$"
mkdir "$work" 2>/dev/null || {
  echo "check-dispatch-reconcile: CANNOT-ASSESS — no scratch directory available" >&2
  exit 2
}
cleanup() { rm -rf "$work"; }
trap cleanup EXIT

fail=0
ok()  { printf '  OK    %s\n' "$1"; }
bad() { printf '  FAIL  %s\n' "$1" >&2; fail=$((fail + 1)); }
note() { printf '  NOTE  %s\n' "$1"; }

digest() { sha256sum "$1" | awk '{print $1}'; }

# --- 1. the wiring is declared where it actually runs ------------------------
plumbing=(
  "fleet/brain.py|markers.reconcile|the idle path reconciles the marker set"
  "fleet/brain.py|markers.reconcile_reference|the wave path reconciles each child it is about to dispatch"
  "fleet/brain.py|markers.FleetProbe|the reconciler is driven by the live fleet's own answers"
  "fleet/brain.py|record.armed|dispatch proceeds past a marker only for a re-arm"
  "fleet/brain.py|record.terminal|a finished order is refused as terminal, not as a duplicate"
  "fleet/brain.py|not dispatched — |a suppressed candidate is PRINTED, never a silent continue"
  "fleet/markers.py|runaway.cap_or_default|the re-arm budget is #723's harvested K"
  "fleet/markers.py|runaway.backoff_delay|the spacing is #723's harvested formula"
  "fleet/markers.py|TERMINAL_STATES = (COMPLETED, DEAD)|the terminal states are declared in one place"
  "fleet/markers.py|REARM_GRACE_SECONDS|delivery is not instantaneous: a fresh marker is not stale"
  "fleet/markers.py|def rearm(|a park is reversible BY NAME, never a weld"
  "fleet/brain.py|def live_claim_refusal(|the brain asks the LEDGER before minting a second order (#861)"
  "fleet/brain.py|refusal = live_claim_refusal(number)|and asks it in the funnel every directive passes through"
  "governance/lifecycle/directive.py|def _supersession_refusal(|the retire mode's whole contract is one named refusal function (#861)"
  "governance/lifecycle/directive.py|is OPEN on the board; a directive for live work is not retired by|the #821 negative control is enforced where it is declared"
  "governance/lifecycle/directive.py|payload[\"retired\"] = stamp|a retirement is stamped into the record it moves"
)
for entry in "${plumbing[@]}"; do
  IFS='|' read -r file marker label <<< "$entry"
  if grep -qF -- "$marker" "$file"; then
    ok "$label ($file: $marker)"
  else
    bad "$label — $file no longer carries: $marker"
  fi
done

# Criteria 3 and 4, in the shell, because they are about SHAPE rather than
# behaviour: the duplicate-only handler is what made "in flight" and "dead" read
# the same, and re-arming without a live check is the marker-ignoring reconciler.
if grep -qF -- 'if message.startswith(DUPLICATE_SUPPRESSED):' fleet/brain.py; then
  bad "advance/dispatch still branches on the DUPLICATE prefix alone — that is the silent-suppression shape"
else
  ok "no caller branches on the duplicate prefix alone (the terminal refusal is handled too)"
fi
if grep -qF -- 'live_claim or live_run or in_flight' fleet/markers.py; then
  ok "staleness is decided by live evidence (claim, run, queued directive), not by the marker alone"
else
  bad "fleet/markers.py no longer asks the three liveness questions — staleness may be marker-only again"
fi

# The gate must be WIRED (criterion 6): `check-gate-coverage.sh` reads the gate
# files for a literal repo-relative path, so this asserts the same thing from the
# other side — a check script that only its own author ever runs is inert.
if grep -qF -- 'scripts/check-dispatch-reconcile.sh' scripts/verify.sh; then
  ok "wired: scripts/verify.sh invokes this check by path"
else
  bad "scripts/check-dispatch-reconcile.sh is invoked by NO gate — check-gate-coverage.sh will fail it"
fi

# --- 2. the driver (shared by the real tree and both mutants) ----------------
driver="$work/driver.py"
cat > "$driver" <<'PY'
"""Provoke the dispatch-marker reconciliation; PASS only when both halves hold.

Run as: driver.py <fleet-root> <repo-root> <state-dir>

`fleet-root` is the `fleet/` package under test (the real one, or a mutated copy);
`repo-root` is the real repo (used for the channel and for the flat `claims`/
`snapshot` imports); `state-dir` is the scratch `AO_FLEET_DIR`; `gov-root` is the
tree `governance/` is imported from — the real repo, or the scratch tree holding
the mutated `governance/lifecycle/directive.py`. Every probe prints PASS/FAIL BY
NAME, so a mutant run shows exactly which property broke.
"""

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

fleet_root = Path(sys.argv[1]).resolve()
repo_root = Path(sys.argv[2]).resolve()
state = Path(sys.argv[3]).resolve()
# Absent means "the real tree", so every existing caller of this driver keeps the
# meaning it had; a mutant run passes the scratch tree instead.
gov_root = Path(sys.argv[4]).resolve() if len(sys.argv) > 4 else repo_root
state.mkdir(parents=True, exist_ok=True)
# Every relative default in this driver (`.board/claims`, the marker directory)
# must resolve INSIDE the scratch tree: a driver that read the live ledger would
# be measuring the operator's fleet, not the code under test.
os.chdir(state)

sys.dont_write_bytecode = True
shutil.rmtree(fleet_root / "__pycache__", ignore_errors=True)
sys.path.insert(0, str(fleet_root))
sys.path.insert(0, str(repo_root / "governance" / "dispatch"))
# Last, so it is searched FIRST for the `governance` package while the flat
# `claims`/`snapshot` modules still resolve from the dispatch directory above.
sys.path.insert(0, str(gov_root))

os.environ["AO_FLEET_DIR"] = str(state)
os.environ["AO_RUNAWAY_ATTEMPTS"] = "3"
os.environ["AO_RUNAWAY_BACKOFF"] = "30"

import brain  # noqa: E402
import claims as claims_mod  # noqa: E402
import markers  # noqa: E402
import snapshot as snapshot_mod  # noqa: E402

# The mailbox that owns the retired order is imported from `gov_root`, so a mutant
# of `governance/lifecycle/directive.py` is the module actually under test.
from governance.lifecycle import directive as directive_mod  # noqa: E402

CAP = int(os.environ["AO_RUNAWAY_ATTEMPTS"])
OLD = 3600.0
MARKERS = state / "brain" / "dispatched"
failures: list[str] = []


def probe(name: str, hold: bool, detail: str = "") -> bool:
    print(f"  probe {name}: {'PASS' if hold else 'FAIL'}" + (f" — {detail}" if detail else ""))
    if not hold:
        failures.append(name)
    return hold


def board(*open_issues: int, closed: tuple[int, ...] = ()) -> Path:
    """A committed-board snapshot in the shape `snapshot.load` reads."""
    issues = [
        {
            "number": number,
            "title": f"#{number}",
            "state": "CLOSED" if number in closed else "OPEN",
            "milestone": "",
            "labels": [],
            "parent": None,
            "blocked_by": [],
            "cross_refs": [],
            "closed_at": None,
        }
        for number in list(open_issues) + list(closed)
    ]
    path = state / "board.json"
    path.write_text(
        json.dumps({"generated_at": markers.now_iso(), "source": "check-dispatch-reconcile", "issues": issues}) + "\n",
        encoding="utf-8",
    )
    return path


def plant(reference: str, issue: int, *, attempts: int = 0, state_name: str = markers.SENT, ts: str | None = None) -> Path:
    """An old marker: what the brain wrote last night and never looked at again."""
    path = markers.path_for(reference, MARKERS)
    markers.write(
        path,
        markers.Marker(
            reference=reference,
            issue=issue,
            state=state_name,
            ts=ts or markers.now_iso(time.time() - OLD),
            attempts=attempts,
            sent_at=markers.now_iso(time.time() - OLD),
        ),
    )
    return path


def live_run(issue: int, *, child_pid: int | None = None, fresh: bool = True) -> None:
    run = state / "runs" / f"brain-directive-{issue:06d}.json"
    run.parent.mkdir(parents=True, exist_ok=True)
    run.write_text(
        json.dumps(
            {
                "issue": issue,
                "pid": os.getpid(),  # the loop's own pid: alive, and NOT evidence
                "child_pid": child_pid,
                "ts": markers.now_iso() if fresh else markers.now_iso(time.time() - OLD),
            }
        )
        + "\n",
        encoding="utf-8",
    )


def queued(reference: str, *, mailbox: str = "inbox") -> None:
    directory = state / mailbox
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{reference}.json").write_text(
        json.dumps({"id": reference, "correlation_id": reference, "type": "directive", "task": {"issue": 1}}) + "\n",
        encoding="utf-8",
    )


def sent_directives() -> list[dict]:
    """Every directive the REAL channel queued for the sister in this run."""
    directory = state / "inbox"
    if not directory.is_dir():
        return []
    return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(directory.glob("*.json"))]


def order(reference: str, issue: int) -> dict:
    return {
        "type": "directive",
        "id": reference,
        "task": {"issue": issue, "lane": "", "title": f"#{issue}"},
        "body": "reconcile probe",
    }


def reconcile(*, references: set[str] | None = None, ledger: Path | None = None) -> list:
    probe_ = markers.FleetProbe(board=SCRATCH_BOARD, ledger=ledger)
    return markers.reconcile(probe_, directory=MARKERS, references=references, cap=CAP, base=30)


board(132, 133, 134, 135, 136, 137, 138, 139, 140, 141, 143, 144, 145, 146, closed=(142, 147))
# The board the probe reads is the SAME artifact the dispatch-time closure guard
# reads (`brain.BOARD_PATH`), loaded through the real loader — so "open" here and
# "may dispatch" there cannot disagree.
SCRATCH_BOARD = snapshot_mod.load(state / "board.json")
brain.BOARD_PATH = state / "board.json"
# The module under test is `fleet_root`; the TRANSPORT is the real channel, so a
# mutant cannot pass by mutating the thing that would have to refuse it.
brain.CHANNEL = str(fleet_root / "channel.py")

# 0. the vocabulary itself, because every probe below is phrased in it
probe(
    "STATES-DECLARED",
    markers.STATES == ("sending", "sent", "in-flight", "completed", "dead")
    and markers.TERMINAL_STATES == ("completed", "dead"),
    f"states={markers.STATES} terminal={markers.TERMINAL_STATES}",
)

# 1. HALF ONE — a dead directive's marker is stale, counted, and re-armed
plant("advance-132", 132)
findings = reconcile()
rearmed = markers.read(markers.path_for("advance-132", MARKERS))
probe(
    "STALE-REARMED",
    len(findings) == 1
    and findings[0].verdict == markers.RE_ARMED
    and findings[0].issue == 132
    and rearmed.attempts == 1
    and rearmed.armed
    and rearmed.evidence.get("live_run") is False,
    f"verdict={[f.verdict for f in findings]} attempts={rearmed.attempts} armed={bool(rearmed.rearm)}",
)
send = brain.dispatch(order("advance-132", 132))
queued_for_sister = [directive for directive in sent_directives() if directive.get("correlation_id") == "advance-132"]
probe(
    "REARM-SENDS",
    send[0] is True and len(queued_for_sister) == 1,
    f"the re-armed issue must leave the welded state: ok={send[0]} queued={len(queued_for_sister)}",
)
print(f"  NOTE  {findings[0].render()}")

# 2. the delivery guarantee, at BOTH layers (criterion 4)
again = brain.dispatch(order("advance-132", 132))
replay = subprocess.run(
    [
        "python3",
        str(fleet_root / "channel.py"),
        "send",
        "--message",
        json.dumps(brain.build_directive(order("advance-132", 132))),
    ],
    capture_output=True,
    text=True,
    env={**os.environ, "AO_FLEET_DIR": str(state), "PYTHONDONTWRITEBYTECODE": "1"},
)
probe(
    "AT-MOST-ONCE",
    again[0] is False
    and brain.suppressed(again[1])
    and len(sent_directives()) == 1
    and replay.returncode != 0
    and "replay" in (replay.stdout + replay.stderr).lower(),
    f"marker refused={not again[0]} inbox={len(sent_directives())} channel replay rc={replay.returncode}",
)

# 3. HALF TWO — live evidence keeps the suppression (a fresh run beat)
plant("advance-133", 133)
live_run(133)
findings = reconcile(references={"advance-133"})
marker = markers.read(markers.path_for("advance-133", MARKERS))
blocked = brain.dispatch(order("advance-133", 133))
probe(
    "LIVE-SUPPRESSED",
    findings
    and findings[0].verdict == markers.LIVE
    and marker.state == markers.IN_FLIGHT
    and marker.armed is False
    and blocked[0] is False
    and len(sent_directives()) == 1,
    f"verdict={[f.verdict for f in findings]} state={marker.state} armed={bool(marker.rearm)}",
)

# 4. HALF TWO — a live CLAIM suppresses too (the ledger is really read)
plant("advance-134", 134)
ledger = state / "claims"
claims_mod.append_event(
    claims_mod.ClaimEvent(event="claim", issue=134, agent="ao796-gate", at=markers.now_iso(), lane="lane-796"),
    path=ledger,
)
findings = reconcile(references={"advance-134"}, ledger=ledger)
probe(
    "LIVE-CLAIM-SUPPRESSED",
    findings and findings[0].verdict == markers.LIVE,
    f"a live claim in the ledger must suppress: {[f.verdict for f in findings]}",
)

# 5. HALF TWO — a QUEUED directive suppresses; an ARCHIVED one does not
plant("advance-140", 140)
queued("advance-140")
findings = reconcile(references={"advance-140"})
plant("advance-141", 141)
queued("advance-141", mailbox="sent")  # `.fleet/sent` is an ARCHIVE (measured: 118 files, most also in done/)
archived = reconcile(references={"advance-141"})
probe(
    "QUEUE-SUPPRESSES-ARCHIVE-DOES-NOT",
    findings
    and findings[0].verdict == markers.LIVE
    and archived
    and archived[0].verdict == markers.RE_ARMED,
    f"queued={[f.verdict for f in findings]} archived-only={[f.verdict for f in archived]}",
)

# 6. the budget is the harvested one, and the end state is PARKED WITH THE ISSUE
plant("advance-135", 135, attempts=CAP)
findings = reconcile(references={"advance-135"})
parked = markers.read(markers.path_for("advance-135", MARKERS))
refused = brain.dispatch(order("advance-135", 135))
settled = reconcile(references={"advance-135"})
probe(
    "PARKED-NAMED",
    findings
    and findings[0].verdict == markers.PARKED
    and parked.state == markers.DEAD
    and "#135" in findings[0].render()
    and "markers.py rearm" in findings[0].detail
    and refused[0] is False
    and refused[1].startswith(brain.PARKED_SUPPRESSED)
    and settled == [],
    f"verdict={[f.verdict for f in findings]} state={parked.state} refused={refused[1][:60]}",
)
print(f"  NOTE  {findings[0].render()}")

# 7. a RETIRED directive parks by correlation, and only by correlation
plant("advance-136", 136)
entry = state / "dead-letter" / "brain-directive-dead.json"
entry.parent.mkdir(parents=True, exist_ok=True)
entry.write_text(
    json.dumps({"reason": "claim refused: stale — snapshot is 36.6m old", "envelope": {"correlation_id": "advance-136", "task": {"issue": 136}}}) + "\n",
    encoding="utf-8",
)
plant("advance-137", 137)  # the SAME retired directive's issue, a DIFFERENT order
retired = reconcile(references={"advance-136"})
other = reconcile(references={"advance-137"})
probe(
    "RETIRED-PARKED-BY-REFERENCE",
    retired and retired[0].verdict == markers.PARKED and other and other[0].verdict == markers.RE_ARMED,
    f"retired={[f.verdict for f in retired]} other-order-same-issue={[f.verdict for f in other]}",
)

# 8. an unreadable store is CANNOT-ASSESS, never a licence to re-arm
plant("advance-999", 999)  # an issue the committed board does not carry
unknown = reconcile(references={"advance-999"})
probe(
    "CANNOT-ASSESS-NEVER-REARMS",
    unknown
    and unknown[0].verdict == markers.CANNOT_ASSESS
    and markers.read(markers.path_for("advance-999", MARKERS)).armed is False,
    f"verdict={[f.verdict for f in unknown]}",
)

# 9. a CLOSED issue makes the marker terminal — the one good reason to stop
plant("advance-142", 142)
done = reconcile(references={"advance-142"})
probe(
    "CLOSED-IS-TERMINAL",
    done
    and done[0].verdict == markers.COMPLETED
    and markers.read(markers.path_for("advance-142", MARKERS)).state == markers.COMPLETED,
    f"verdict={[f.verdict for f in done]}",
)

# 10. the bound is mechanical: K re-arms, then parked — with a synthetic clock
bound_path = plant("advance-143", 143)
moment = time.time()
granted = 0
verdicts = []
for _ in range(12):
    verdict = markers.judge(markers.read(bound_path), markers.FleetProbe(board=SCRATCH_BOARD, ledger=ledger), moment=moment, cap=CAP, base=30)
    verdicts.append(verdict.verdict)
    if verdict.marker is not None:
        markers.write(bound_path, verdict.marker)
    if verdict.verdict == markers.RE_ARMED:
        granted += 1
        current = markers.read(bound_path)
        markers.write(
            bound_path,
            markers.Marker(**{**current.payload(), "rearm": None, "sent_at": markers.now_iso(moment), "ts": markers.now_iso(moment)}),
        )
    moment += 400
probe(
    "BUDGET-BOUNDED",
    granted == CAP and markers.read(bound_path).state == markers.DEAD,
    f"K={CAP} re-arms then parked; granted={granted} states={verdicts}",
)

# 11. the WIRED idle path: a re-arm is printed and the issue is dispatched
class _Issue:
    def __init__(self, number: int):
        self.number = number
        self.title = f"#{number}"


ready_set: list[_Issue] = []
brain.snapshot_mod.github_records = lambda repo: []
brain.snapshot_mod.build_snapshot = lambda records, source: SCRATCH_BOARD
brain.order.advance_candidates = lambda snapshot, claimed: list(ready_set)


before = len(sent_directives())
plant("advance-138", 138)
ready_set = [_Issue(138)]
captured = io.StringIO()
with contextlib.redirect_stdout(captured):
    advanced = brain.advance_ready()
stdout = captured.getvalue()
probe(
    "ADVANCE-REARM-PRINTS-AND-SENDS",
    advanced == [138]
    and len(sent_directives()) == before + 1
    and "RE-ARMED advance-138" in stdout,
    f"advanced={advanced} queued={len(sent_directives()) - before} printed={[line for line in stdout.splitlines() if 'RE-ARMED' in line][:1]}",
)

# 12. the WIRED idle path: a live directive is suppressed AND the suppression is
#     printed NAMING the state — the silent `continue` was the whole defect
before = len(sent_directives())
plant("advance-139", 139)
live_run(139, child_pid=os.getpid())
ready_set = [_Issue(139)]
captured = io.StringIO()
with contextlib.redirect_stdout(captured):
    advanced = brain.advance_ready()
stdout = captured.getvalue()
suppressed_line = [line for line in stdout.splitlines() if "not dispatched" in line]
probe(
    "SUPPRESSION-IS-PRINTED-WITH-THE-STATE",
    advanced == []
    and len(sent_directives()) == before
    and suppressed_line
    and "state=in-flight" in suppressed_line[0],
    f"advanced={advanced} queued={len(sent_directives()) - before} line={suppressed_line[:1]}",
)

# 13. the LEDGER guard in the funnel (#861, acceptance item 3 of #366): a unit a
#     LIVE claim holds gets no second authorisation. The guard sits in
#     `dispatch()` — the one funnel every directive passes through — so the wave
#     path and an operator-called dispatch are covered, not only the idle path,
#     which already filtered its own candidate list by the live claims.
guard_ledger = state / "guard-claims"
claims_mod.append_event(
    claims_mod.ClaimEvent(
        event="claim", issue=144, agent="subagent-861", at=markers.now_iso(), lane="lane-861"
    ),
    path=guard_ledger,
)
brain.CLAIMS_LEDGER = guard_ledger
before = len(sent_directives())
claimed = brain.dispatch(order("advance-144", 144))
probe(
    "DISPATCH-REFUSES-CLAIMED",
    claimed[0] is False
    and claimed[1].startswith(brain.ISSUE_CLAIMED)
    and "#144" in claimed[1]
    and "subagent-861" in claimed[1]
    and "no sent-marker" in claimed[1]
    and len(sent_directives()) == before
    and not markers.path_for("advance-144", MARKERS).exists(),
    f"refused={not claimed[0]} report={claimed[1][:90]} queued={len(sent_directives()) - before}",
)

#     ... and the SAME funnel still sends when no claim holds the unit. Without
#     this half a guard that refused every order would satisfy the probe above —
#     which is a weld, not a guard.
unclaimed = brain.dispatch(order("advance-145", 145))
probe(
    "DISPATCH-SENDS-WHEN-UNCLAIMED",
    unclaimed[0] is True and len(sent_directives()) == before + 1,
    f"sent={unclaimed[0]} queued={len(sent_directives()) - before} report={unclaimed[1][:70]}",
)

# 14. the `.fleet/sent/` terminal mode, and the #821 invariant it must NOT bypass
#     (#861). The lifecycle mailbox is separate from the fleet's own spool: it is
#     written and moved only by `governance/lifecycle/directive.py`, and a stranded
#     authorisation whose issue is closed WITH NO change of its own (superseded) is
#     one no close-out run can ever collect. The mode added for it is deliberately
#     NARROWER than the landed gate, and the refusal of an OPEN issue is the
#     mandatory negative control: without it the new verb is a bypass.
def strand(directive_id: str, issue: int) -> Path:
    """A stranded authorisation: an order still sitting in `sent/`."""
    sent, _ = directive_mod.mailboxes(state)
    sent.mkdir(parents=True, exist_ok=True)
    path = sent / f"{directive_id}.json"
    path.write_text(
        json.dumps(
            {
                "id": directive_id,
                "ts": markers.now_iso(),
                "type": "directive",
                "from": "brain",
                "to": "sister",
                "task": {"kind": "work", "issue": issue, "lane": "lane-861"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return path


# The closure oracle is the REAL one, read from the board artifact this driver
# planted, through the real freshness bar (`governance.policy.lease`).
oracle = directive_mod.board_closure(state / "board.json")
_, lifecycle_done = directive_mod.mailboxes(state)

open_strand = strand("d-open", 146)  # #146 is OPEN on the board
try:
    directive_mod.retire_superseded(
        state, "d-open", 147, landed={}, closed=oracle.closed, board=oracle.generated_at
    )
    bypass = ""
except directive_mod.DirectiveRefused as exc:
    bypass = str(exc)
probe(
    "RETIRE-REFUSES-OPEN",
    "is OPEN" in bypass
    and "#821" in bypass
    and open_strand.exists()
    and not (lifecycle_done / "d-open.json").exists(),
    f"message={bypass[:110]}",
)

# The gate that was already there is unmoved: an order whose change has not landed
# stays in `sent/` exactly as it did before this lane touched the module.
live_strand = strand("d-live", 145)
try:
    directive_mod.consume(state, "d-live", {145: False})
    gate_refusal = ""
except directive_mod.DirectiveRefused as exc:
    gate_refusal = str(exc)
probe(
    "ORDINARY-GATE-REFUSES-LIVE",
    "has not landed" in gate_refusal
    and live_strand.exists()
    and not (lifecycle_done / "d-live.json").exists(),
    f"message={gate_refusal[:110]}",
)

# ... and the case the mode EXISTS for: closed on the board with no change of its
# own and a NAMED successor. The move is stamped, so the terminal copy is evidence
# rather than an unexplained disappearance.
superseded = strand("d-superseded", 147)  # #147 is CLOSED on the board
try:
    retired = directive_mod.retire_superseded(
        state, "d-superseded", 148, landed={}, closed=oracle.closed, board=oracle.generated_at
    )
    retired_error = ""
except directive_mod.DirectiveRefused as exc:
    retired, retired_error = "", str(exc)
moved = lifecycle_done / "d-superseded.json"
stamp = json.loads(moved.read_text(encoding="utf-8")).get("retired", {}) if moved.exists() else {}
probe(
    "RETIRE-SUPERSEDED-PERMITS",
    "retired 1 directive(s) for #147" in retired
    and not superseded.exists()
    and stamp.get("reason") == "superseded"
    and stamp.get("superseded_by") == 148
    and stamp.get("board") == oracle.generated_at,
    f"detail={retired[:70] or retired_error} stamp={stamp}",
)

# A closure oracle that is STALE is CANNOT-ASSESS, never a closed issue: an oracle
# that can be arbitrarily old fails OPEN, which is worse than having none.
stale_board = state / "stale-board.json"
stale_board.write_text(
    json.dumps(
        {
            "generated_at": markers.now_iso(time.time() - 48 * 3600),
            "source": "check-dispatch-reconcile",
            "issues": [{"number": 147, "title": "#147", "state": "CLOSED"}],
        }
    )
    + "\n",
    encoding="utf-8",
)
try:
    directive_mod.board_closure(stale_board)
    stale_refusal = ""
except directive_mod.DirectiveRefused as exc:
    stale_refusal = str(exc)
probe(
    "RETIRE-REFUSES-STALE-BOARD",
    "m old (bar" in stale_refusal and "snapshot --from-github" in stale_refusal,
    f"message={stale_refusal[:110]}",
)

if failures:
    print(f"PROBES: FAIL ({', '.join(failures)})")
    raise SystemExit(1)
print("PROBES: PASS")
PY

[ -s "$driver" ] || {
  echo "check-dispatch-reconcile: CANNOT-ASSESS — the driver was not written" >&2
  exit 2
}

# --- 3. the real tree --------------------------------------------------------
# Every file a mutant may touch is digested: a provocation that leaked into the
# REAL tree would otherwise be reported as a clean run.
mutated_files="fleet/markers.py fleet/brain.py governance/lifecycle/directive.py"
digest_all() { for file in $mutated_files; do digest "$file"; done; }
sha_before="$(digest_all)"
echo "== dispatch reconcile: the real tree =="
if python3 "$driver" "$root/fleet" "$root" "$work/state-real" > "$work/real.log" 2>&1; then
  grep -E "probe .*: (PASS|FAIL)|^  NOTE|PROBES:" "$work/real.log" || true
  ok "the real tree re-arms a stale marker, suppresses a live one, parks a spent budget, refuses a second order for a claimed unit, and retires a superseded strand while refusing a live one"
else
  cat "$work/real.log" >&2
  bad "the reconciliation failed against the real tree (see the probes above)"
fi

# --- 4. provocation: a mutation the gate MUST refuse, by name ----------------
# Each mutant is a full LAYOUT (a copy of `fleet/` beside symlinks to the real
# `governance/` and `.board/`), because `fleet/brain.py` resolves its repo root
# from its own file location: a flat copy could not import `claims`/`order`/
# `snapshot` at all, and the provocation would prove nothing about this code.
provoke() { # provoke <label> <target-rel-path> <anchor> <replacement> <expected-probe>
  label="$1"; target="$2"; anchor="$3"; replacement="$4"; expected="$5"
  scratch="$work/tree-$label"
  top="${target%%/*}"   # `fleet` or `governance`: the tree the mutation lives in
  mkdir -p "$scratch" 2>/dev/null
  cp -R "$root/fleet" "$scratch/fleet" 2>/dev/null
  # The rest of the layout is symlinked, never copied: `fleet/brain.py` imports
  # `governance/dispatch/{claims,order,snapshot}` and loads the routing policy from
  # `registry/`, and it resolves that root from its own file location — a flat copy
  # would fail to import at all, so the provocation would prove nothing about this
  # code. `fleet/` is always a copy, and so is the mutated file's OWN top-level
  # tree (a `governance/` mutant must be importable as the copy, or the driver
  # would silently test the real module and report a clean run).
  for entry in "$root"/*; do
    name="$(basename "$entry")"
    [ "$name" = "fleet" ] && continue
    [ "$name" = "$top" ] && continue
    ln -s "$entry" "$scratch/$name" 2>/dev/null
  done
  if [ "$top" != "fleet" ]; then
    cp -R "$root/$top" "$scratch/$top" 2>/dev/null
  fi
  # Dotfiles are not globbed, and the committed board is one of the things the
  # dispatch-time closure guard reads through a relative root.
  ln -s "$root/.board" "$scratch/.board" 2>/dev/null
  if [ ! -f "$scratch/$target" ]; then
    bad "could not stage the $label mutant (no scratch copy of $target)"
    return
  fi
  if ! python3 - "$scratch/$target" "$anchor" "$replacement" <<'PY'
import sys
from pathlib import Path

path, anchor, replacement = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
source = path.read_text(encoding="utf-8")
count = source.count(anchor)
if count != 1:
    print(f"the mutation anchor matched {count} times, not once", file=sys.stderr)
    raise SystemExit(3)
mutated = source.replace(anchor, replacement)
if mutated == source:
    print("the mutation did not change the source", file=sys.stderr)
    raise SystemExit(3)
path.write_text(mutated, encoding="utf-8")
print("MUTANT-APPLIED:", path.name, anchor.splitlines()[0], "->", replacement.splitlines()[0])
PY
  then
    bad "the $label mutation could not be applied (see the reason above)"
    return
  fi
  echo "== dispatch reconcile: provocation $label (must fail $expected) =="
  # `$scratch` is handed to the driver as the GOVERNANCE root as well, so a mutant
  # of `governance/lifecycle/directive.py` is the module under test; for a
  # `fleet/`-only mutant that path is a symlink to the real tree, so the call is
  # the same one either way.
  if python3 "$driver" "$scratch/fleet" "$root" "$work/state-$label" "$scratch" > "$work/$label.log" 2>&1; then
    bad "$label was NOT refused — the gate cannot see the defect it exists for"
  elif grep -q "probe $expected: FAIL" "$work/$label.log"; then
    grep -E "probe .*: (PASS|FAIL)|PROBES:" "$work/$label.log"
    ok "$label was refused by name ($expected)"
  else
    cat "$work/$label.log" >&2
    bad "$label failed, but not for the reason the provocation targets ($expected)"
  fi
}

provoke "NEVER-STALE" "fleet/markers.py" \
  'REARM_GRACE_SECONDS = 300.0' \
  'REARM_GRACE_SECONDS = 10 ** 12' \
  "STALE-REARMED"

provoke "LIVENESS-IGNORED" "fleet/markers.py" \
  'if live_claim or live_run or in_flight:' \
  'if False:' \
  "LIVE-SUPPRESSED"

# The ledger guard must be able to fail too (GR-12): with it taken out of the
# funnel, a second authorisation is minted for a unit another lane is already
# working — the duplicate order #861 exists to stop.
provoke "CLAIM-GUARD-REMOVED" "fleet/brain.py" \
  '        refusal = live_claim_refusal(number)
        if refusal is not None:
            return False, refusal' \
  '        # MUTANT: the ledger guard is taken out of the funnel
        pass' \
  "DISPATCH-REFUSES-CLAIMED"

# ... and so must the #821 negative control. With the OPEN-issue refusal removed,
# the terminal verb retires work that is still live: the bypass the control exists
# to make impossible, and the probe that names it is RETIRE-REFUSES-OPEN.
provoke "SUPERSESSION-BYPASS" "governance/lifecycle/directive.py" \
  '    if not state:
        return (
            f"#{issue} is OPEN on the board; a directive for live work is not retired by "' \
  '    if False:  # MUTANT: the #821 negative control is removed
        return (
            f"#{issue} is OPEN on the board; a directive for live work is not retired by "' \
  "RETIRE-REFUSES-OPEN"

# The permit direction must be provable as well, or RETIRE-SUPERSEDED-PERMITS
# could be satisfied by a mode that never retires anything.
provoke "SUPERSESSION-NEVER-PERMITS" "governance/lifecycle/directive.py" \
  'SUPERSESSION_REASONS = (REASON_SUPERSEDED,)' \
  'SUPERSESSION_REASONS = ()' \
  "RETIRE-SUPERSEDED-PERMITS"

# --- 5. the real tree was never mutated, and the suite proves the same -------
sha_after="$(digest_all)"
if [ "$sha_before" = "$sha_after" ]; then
  ok "the real tree is byte-identical before and after the provocations ($mutated_files)"
else
  bad "a provocation modified the real tree ($sha_before -> $sha_after)"
fi

if env -u AO_FLEET_DIR PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q \
     fleet/tests/test_dispatch_markers.py > "$work/pytest.log" 2>&1; then
  ok "the reconciler's own suite passes ($(tail -n 1 "$work/pytest.log" | tr -d '\r'))"
else
  tail -n 20 "$work/pytest.log" >&2
  bad "fleet/tests/test_dispatch_markers.py is not green"
fi

echo
if [ "$fail" -eq 0 ]; then
  echo "check-dispatch-reconcile: OK"
  exit 0
fi
echo "check-dispatch-reconcile: NOT-OK ($fail finding(s))" >&2
exit 1
