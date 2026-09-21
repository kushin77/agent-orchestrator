#!/usr/bin/env bash
# check-dead-letter.sh — the A2A dead-letter-by-protocol gate (issue #754).
#
# THE DEFECT THIS EXISTS FOR
#   The runaway fix (#723) retires a directive the LOOP can prove is unrunnable.
#   But an operator — or, more to the point, a PEER AGENT — that can see an order
#   is wedged had no way to say so. The only route was out of band: `mv` the
#   order out of `.fleet/inbox/` while the loop was mid-`watch`. That races the
#   reader, loses the attempt history and the reason, bypasses the channel (no
#   ack, no audit record) and is impossible for an agent to do at all. The
#   control channel already existed and was already validated; it was missing
#   exactly one verb.
#
# WHAT IS PROVEN (against the real tree, not a description of it)
#   1. the verbs exist where they run: `drop` and `dead-letter` are in the
#      channel's validated vocabulary, `apply_control` maps them to verdicts, and
#      the loop acts on the verdicts;
#   2. `control:drop` RETIRES the named directive — it leaves the inbox, it lands
#      in the dead-letter store with a durable record, and `channel watch` will
#      never return it again;
#   3. the NEGATIVE CONTROL: a directive that was NOT dropped is still returned.
#      Asserting only the dropped half would pass even if `watch` had stopped
#      returning anything at all;
#   4. the VERB and the AUTOMATIC path write the SAME record shape, from one
#      implementation — `runaway.dead_letter()` is the only writer, and
#      `RECORD_FIELDS` is the shape both callers produce;
#   5. a drop that names no target is REFUSED by the transport and REPORTED by
#      the loop — never a silent no-op;
#   6. the mailbox is read back by VERB (`dead-letter --directive`), not by an
#      operator walking the runtime directory.
#
# PROVOCATION (the gate must be able to fail — GR-12, no-false-green)
#   The driver runs against the real `fleet/` tree and then against TWO scratch
#   copies of it, each carrying ONE mutation applied by literal replacement. Each
#   replacement is ASSERTED to have landed (anchor unique, source changed) BEFORE
#   the mutant runs, so a mutation that never applied cannot be reported as "the
#   gate caught it" — the measured trap that makes a mutation proof vacuous:
#     * DROPPER-REMOVED — the record omits `dropped_by`, so the two callers can no
#       longer be told apart; the driver MUST fail the probe RECORD-SHAPE-EQUAL.
#     * ORDER-NOT-MOVED — `dead_letter` stops moving the order out of the inbox;
#       the driver MUST fail probe ORDER-LEAVES-INBOX.
#   Neither mutant breaks the *happy* path to the same value: each is asserted to
#   DIVERGE from the real implementation's output at its own probe, so the proof
#   cannot be satisfied by asserting only on the unmutated behaviour.
#
# No network. No writes outside the scratch directory. Exit-code contract:
# 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-dead-letter.sh
#
# ---knowledge---
# module_id: scripts.check-dead-letter
# system: governance
# app: gates
# solution_class: enterprise
# patterns: [tri-state-exit, provoked-negative-control, no-false-green, named-refusal, bounded-work]
# derives_from: null
# owner_sme: qa-sme
# tier: L1
# interfaces: [exit 0 OK, exit 1 NOT-OK, exit 2 CANNOT-ASSESS]
# invariants: ""
# gotchas: ""
# related: ["#723", "#754"]
# do_not_duplicate: null
# ---knowledge---
set -uo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 2

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-dead-letter: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

for required in fleet/runaway.py fleet/terminal.py fleet/channel.py fleet/control.py \
                fleet/runtime.py fleet/tests/test_dead_letter.py; do
  if [ ! -f "$required" ]; then
    echo "check-dead-letter: FAIL — $required is missing" >&2
    exit 1
  fi
done

# A plain-name scratch directory (NOT a `mktemp` template): a template whose
# placeholder is a run of one letter trips this repo's own unfinished-marker
# scan, which would fail docs-lint. `mkdir` without `-p` refuses loudly rather
# than silently reusing another run's tree.
work="/tmp/ao-dead-letter.$(date +%s%N).$"
mkdir "$work" 2>/dev/null || {
  echo "check-dead-letter: CANNOT-ASSESS — no scratch directory available" >&2
  exit 2
}
cleanup() { rm -rf "$work"; }
trap cleanup EXIT

fail=0
ok()  { printf '  OK    %s\n' "$1"; }
bad() { printf '  FAIL  %s\n' "$1" >&2; fail=$((fail + 1)); }
note() { printf '  NOTE  %s\n' "$1"; }

digest() { sha256sum "$1" | awk '{print $1}'; }

sha_before="$(digest fleet/runaway.py)"

# --- 1. the wiring is declared where it actually runs ------------------------
plumbing=(
  "fleet/channel.py|\"drop\",|the drop verb is in the validated control vocabulary"
  "fleet/channel.py|\"dead-letter\",|the dead-letter (list) verb is in the vocabulary"
  "fleet/channel.py|DIRECTIVE_CONTROLS = (\"drop\",)|drop is declared as needing a target directive"
  "fleet/terminal.py|if action == \"drop\":|apply_control maps drop to a verdict"
  "fleet/terminal.py|elif control_outcome == \"drop\":|the loop acts on the drop verdict"
  "fleet/terminal.py|control_target_directive_id|the target directive is resolved separately from the control's own id"
  "fleet/terminal.py|dropped_by=sender|the drop ACKS and names the sender"
  "fleet/terminal.py|def drop_directive(|there is ONE retire implementation both callers use"
  "fleet/terminal.py|dropped_by=\"runaway-guard\"|the automatic path shares that implementation"
  "fleet/runaway.py|RECORD_FIELDS = (|the record shape both callers produce is declared once"
  "fleet/runaway.py|dropped_by: str = \"runaway-guard\"|the store takes the dropper as a parameter, not a second code path"
  "fleet/control.py|cmd_drop|the operator verb is registered"
  "fleet/control.py|_send_control(\"drop\"|the verb travels the EXISTING control path"
)
for entry in "${plumbing[@]}"; do
  IFS='|' read -r file marker label <<< "$entry"
  if grep -qF -- "$marker" "$file"; then
    ok "$label ($file)"
  else
    bad "$label — $file no longer carries: $marker"
  fi
done

# --- 2. the driver (shared by the real tree and both mutants) ----------------
driver="$work/driver.py"
cat > "$driver" <<'PY'
"""Provoke A2A dead-lettering; PASS only when the verb retires, records and acks.

Run as: driver.py <fleet-root> <repo-root> <state-dir>

Every probe prints PASS/FAIL by NAME, so the caller (and a mutant run) can see
exactly which property broke. The driver imports `channel`, `runaway` and
`terminal` from <fleet-root>, so pointing it at a mutated scratch copy runs the
mutation under test.
"""

import contextlib
import importlib.util
import io
import json
import os
import shutil
import sys
from argparse import Namespace
from pathlib import Path

fleet_root = Path(sys.argv[1]).resolve()
repo_root = Path(sys.argv[2]).resolve()
state = Path(sys.argv[3]).resolve()
state.mkdir(parents=True, exist_ok=True)

sys.dont_write_bytecode = True
shutil.rmtree(fleet_root / "__pycache__", ignore_errors=True)
sys.path.insert(0, str(fleet_root))   # channel / runaway / terminal / runtime
sys.path.insert(0, str(repo_root))    # governance.policy.lease, for channel

os.environ["AO_FLEET_DIR"] = str(state)
os.environ["AO_RUNAWAY_ATTEMPTS"] = "1"
os.environ["AO_RUNAWAY_BACKOFF"] = "30"

import channel  # noqa: E402
import runaway  # noqa: E402
import terminal  # noqa: E402

failures: list[str] = []


def probe(name: str, hold: bool, detail: str = "") -> bool:
    print(f"  probe {name}: {'PASS' if hold else 'FAIL'}" + (f" — {detail}" if detail else ""))
    if not hold:
        failures.append(name)
    return hold


def plant(directive_id: str, issue: int = 754) -> Path:
    inbox = state / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    path = inbox / f"{directive_id}.json"
    path.write_text(
        json.dumps(
            {
                "id": directive_id,
                "ts": "2026-09-14T00:00:00Z",
                "type": "directive",
                "from": "brain",
                "to": "sister",
                "model": {"tier": "flash", "thinking": "none"},
                "task": {"kind": "work", "issue": issue, "lane": "fleet"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def clear_inbox() -> None:
    """Every probe starts from a known mailbox — leftovers would fake a result."""
    inbox = state / "inbox"
    if not inbox.exists():
        return
    for path in inbox.glob("*.json"):
        path.unlink()


def watch() -> tuple[int, str]:
    """Run the REAL `channel watch` once; return (exit code, stdout)."""
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        rc = channel.cmd_watch(Namespace(timeout_seconds=0.05, interval=0.01, skip=[]))
    return rc, captured.getvalue()


def returned_id(out: str) -> str | None:
    if "{" not in out:
        return None
    try:
        return json.loads(out[out.index("{"): out.rindex("}") + 1]).get("id")
    except (ValueError, json.JSONDecodeError):
        return None


# The loop shells out to `channel.py` (consume/report); the driver never wants
# that to touch a real mailbox, and `report_once`/`stream_run_event` are stubbed
# below for the same reason.
terminal.stream_run_event = lambda *a, **k: None
terminal.report_once = lambda *a, **k: True

# --- probe 1: the verb retires the order, records it, and watch refuses it ----
clear_inbox()
plant("d-wedged")
plant("d-healthy")
outcome = terminal.apply_control("drop", {"task": {"directive": "d-wedged"}}, "subagent-x")
probe("VERB-EXISTS", outcome == "drop", f"apply_control('drop') -> {outcome!r}")

terminal.drop_directive("d-wedged", 754, "its work already landed", dropped_by="brain")

probe(
    "ORDER-LEAVES-INBOX",
    not (state / "inbox" / "d-wedged.json").exists(),
    "the dropped order is still in the inbox",
)
rc, out = watch()
probe(
    "WATCH-NEVER-RETURNS-IT",
    rc == channel.EXIT_OK and returned_id(out) == "d-healthy",
    f"watch rc={rc} returned {returned_id(out)!r} (d-healthy must still come back)",
)
probe(
    "UNDROPPED-STILL-RETURNED",
    returned_id(out) == "d-healthy",
    "the negative control: an undropped directive must still be dispatched",
)

# --- probe 2: the record names the reason and the dropper --------------------
record = runaway.record_shape(state, "d-wedged")
probe("RECORD-HAS-FIELDS", set(runaway.RECORD_FIELDS) <= set(record),
      f"missing: {sorted(set(runaway.RECORD_FIELDS) - set(record))}")
probe("RECORD-NAMES-DROPPER", record.get("dropped_by") == "brain",
      f"dropped_by={record.get('dropped_by')!r}")
probe("RECORD-NAMES-REASON", record.get("reason") == "its work already landed",
      f"reason={record.get('reason')!r}")

# --- probe 3: one implementation, two callers, the SAME shape ---------------
clear_inbox()
plant("d-auto")
terminal.guard_retire("d-auto", 754, "claim refused: refused")
auto = runaway.record_shape(state, "d-auto")
verb = runaway.record_shape(state, "d-wedged")
probe(
    "RECORD-SHAPE-EQUAL",
    set(auto) == set(verb) == set(runaway.RECORD_FIELDS),
    f"auto={sorted(auto)} verb={sorted(verb)}",
)
probe("AUTO-NAMES-GUARD", auto.get("dropped_by") == "runaway-guard",
      f"auto dropped_by={auto.get('dropped_by')!r}")
probe("VERB-NAMES-SENDER", verb.get("dropped_by") == "brain",
      f"verb dropped_by={verb.get('dropped_by')!r}")

# --- probe 4: a targetless drop is refused, never a silent no-op ------------
problems = channel.validate(
    {"from": "brain", "to": "sister", "type": "directive", "control": "drop"}
)
probe("TARGETLESS-DROP-REFUSED", any("must name the task.directive" in p for p in problems),
      f"validate said {problems}")
ok_control = channel.validate(
    {"from": "brain", "to": "sister", "type": "directive", "control": "drop",
     "task": {"directive": "d-wedged"}}
)
probe("VALID-DROP-ACCEPTED", ok_control == [], f"validate said {ok_control}")
probe("TARGET-IS-NOT-THE-CONTROL-ID",
      terminal.control_target_directive_id({"task": {"directive": "../../etc/passwd"}}) is None,
      "a target that is not a safe mailbox name was accepted")

# --- probe 5: the mailbox is read by verb -----------------------------------
captured = io.StringIO()
with contextlib.redirect_stdout(captured):
    rc = runaway.main(["--fleet-dir", str(state), "dead-letter"])
printed = captured.getvalue()
probe("MAILBOX-BY-VERB", rc == runaway.EXIT_OK and "d-wedged" in printed,
      f"dead-letter rc={rc}")

print()
print(f"PROBES: {len(failures)} failed of 13")
if failures:
    print("FAILED: " + ", ".join(failures))
    raise SystemExit(1)
raise SystemExit(0)
PY

# --- 3. the real tree must PASS every probe ---------------------------------
echo "== dead-letter: the real tree =="
if python3 "$driver" "$root/fleet" "$root" "$work/state-real" > "$work/real.log" 2>&1; then
  grep -E "probe .*: (PASS|FAIL)|PROBES:" "$work/real.log" || true
  ok "the real tree passes every probe"
else
  cat "$work/real.log" >&2
  bad "the real tree failed a probe — see above"
fi

# --- 4. provocation: each mutant must be refused BY NAME --------------------
provoke() {
  local label="$1" anchor="$2" replacement="$3" expected="$4"
  local scratch="$work/scratch-$label"
  mkdir "$scratch" 2>/dev/null || {
    bad "could not create the scratch tree for $label"
    return
  }
  cp fleet/*.py "$scratch/" 2>/dev/null
  if [ ! -f "$scratch/runaway.py" ]; then
    bad "could not stage the $label mutant (no scratch copy of fleet/)"
    return
  fi
  if ! python3 - "$scratch/runaway.py" "$anchor" "$replacement" <<'PY'
import hashlib
import sys
from pathlib import Path

path, anchor, replacement = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
source = path.read_text(encoding="utf-8")
before = hashlib.sha256(source.encode()).hexdigest()
if source.count(anchor) != 1:
    print(f"the mutation anchor matched {source.count(anchor)} times, not once", file=sys.stderr)
    raise SystemExit(3)
mutated = source.replace(anchor, replacement)
after = hashlib.sha256(mutated.encode()).hexdigest()
if mutated == source or before == after:
    print("the mutation did not change the source (sha256 unchanged)", file=sys.stderr)
    raise SystemExit(3)
path.write_text(mutated, encoding="utf-8")
print(f"MUTANT-APPLIED: sha256 {before[:12]} -> {after[:12]}")
PY
  then
    bad "the $label mutation could not be applied (see the reason above)"
    return
  fi
  echo "== dead-letter: provocation $label (must fail $expected) =="
  if python3 "$driver" "$scratch" "$root" "$work/state-$label" > "$work/$label.log" 2>&1; then
    bad "$label was NOT refused — the gate cannot see the defect it exists for"
  # `expected` is a '|'-separated set: the provocation is satisfied when AT LEAST
  # ONE named probe fails. A mutation is allowed to break more than its headline
  # probe — indeed DROPPER-REMOVED breaks all three dropper assertions, which is
  # correct and stronger — but it may never be refused on a probe it did not
  # target, or "the gate failed for some reason" would be mistaken for proof.
  elif grep -qE "probe ($expected): FAIL" "$work/$label.log"; then
    grep -E "probe .*: (PASS|FAIL)|PROBES:|^FAILED:" "$work/$label.log"
    ok "$label was refused by name ($expected)"
  else
    cat "$work/$label.log" >&2
    bad "$label failed, but not for the reason the provocation targets ($expected)"
  fi
}

provoke "DROPPER-REMOVED" \
  '        "dropped_by": dropped_by,' \
  '        "dropped_by": None,' \
  "RECORD-NAMES-DROPPER|RECORD-SHAPE-EQUAL|AUTO-NAMES-GUARD|VERB-NAMES-SENDER"

provoke "ORDER-NOT-MOVED" \
  '    try:
        source.unlink()
    except OSError:
        pass
    return target' \
  '    return target' \
  "ORDER-LEAVES-INBOX"

# --- 5. the tree was never mutated, and the suite proves the same -----------
sha_after="$(digest fleet/runaway.py)"
if [ "$sha_before" = "$sha_after" ]; then
  ok "the real fleet/runaway.py is byte-identical before and after the provocations"
else
  bad "a provocation modified the real tree ($sha_before -> $sha_after)"
fi

if env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q \
     fleet/tests/test_dead_letter.py > "$work/pytest.log" 2>&1; then
  ok "the dead-letter suite passes ($(tail -n 1 "$work/pytest.log" | tr -d '\r'))"
else
  tail -n 20 "$work/pytest.log" >&2
  bad "fleet/tests/test_dead_letter.py is not green"
fi

# --- 6. the honest note about the wiring ------------------------------------
if grep -qF 'bash scripts/check-dead-letter.sh' scripts/verify.sh 2>/dev/null; then
  ok "scripts/verify.sh runs this check"
else
  note "scripts/verify.sh does not name this check yet — wire it as:"
  note "  'dead-letter|bash scripts/check-dead-letter.sh'"
  note "(check-gate-coverage refuses a newly delivered scripts/check-*.sh that no gate file invokes,"
  note " so the gate of record stays red until that one line lands)"
fi

echo ""
if [ "$fail" -eq 0 ]; then
  echo "check-dead-letter: OK — drop retires by protocol, one record shape for both callers, and a disabled retire is refused"
  exit 0
fi
echo "check-dead-letter: NOT-OK — $fail check(s) failed" >&2
exit 1
