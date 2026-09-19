#!/usr/bin/env bash
# check-fleet-channel.sh — the steering channel gate (M26, issue #162).
#
# The brain steers the sister session through the file-mailbox channel. This
# gate proves the channel enforces the steering contract instead of merely
# carrying messages:
#
#   * fleet/schema/message.schema.json parses as JSON
#   * the standing directive (fleet/directive.json) validates against the
#     contract and carries the DSv4FNone FinOps block (flash / none)
#   * every mutant message must be REFUSED: unknown type, bad tier, bad
#     thinking, missing role, a sister-issued directive, a directive not
#     addressed to the sister
#   * the queue-liveness runaway alarm (issue #728) is RAISED by this gate — not
#     only by an ad-hoc command — and it LATCHES: provoked, then re-read with the
#     excursion gone, it is still raised and still names the directive and the
#     worktree responsible, until `ack` clears it and a second `ack` is refused.
#     If the alarm clears itself, this gate FAILS.
#
# If any mutant passes, this check FAILS (an enforcement channel that accepts
# invalid traffic is a formality — GR-12 / AO-GR-19).
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-fleet-channel.sh
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

channel="python3 fleet/channel.py"
schema="fleet/schema/message.schema.json"
directive="fleet/directive.json"

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-fleet-channel: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

if [ ! -f "$schema" ]; then
  echo "check-fleet-channel: FAIL — $schema is missing" >&2
  exit 1
fi

python3 - "$schema" <<'PY' || { echo "check-fleet-channel: FAIL — schema is not valid JSON" >&2; exit 1; }
import json, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    json.load(handle)
PY

if [ ! -f "$directive" ]; then
  echo "check-fleet-channel: FAIL — $directive is missing (the sister has no standing orders)" >&2
  exit 1
fi

fail=0
work="/tmp/fleet-gate.$$.$(date +%s)"
mkdir "$work" || exit 2

# The standing directive must be valid and must carry the DSv4FNone block.
if $channel verify --message "$directive" >/dev/null 2>&1; then
  echo "  OK    standing directive validates"
else
  echo "  FAIL  standing directive does not validate against the contract" >&2
  fail=$((fail + 1))
fi
python3 - "$directive" <<'PY' || fail=$((fail + 1))
import json, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    data = json.load(handle)
model = data.get("model") or {}
if model.get("tier") != "flash" or model.get("thinking") != "none":
    print("  FAIL  standing directive does not carry the DSv4FNone FinOps block (flash/none)", file=sys.stderr)
    raise SystemExit(1)
print("  OK    standing directive carries DSv4FNone (flash, thinking none)")
PY

# Mutants: each must be refused. A mutant that passes = the gate fails.
make_mutant() { # make_mutant <name> <json>
  printf '%s\n' "$2" > "$work/$1.json"
}
make_mutant "bad-type"      '{"from":"brain","to":"sister","type":"replan"}'
make_mutant "bad-tier"      '{"from":"brain","to":"sister","type":"directive","model":{"tier":"ultra","thinking":"none"}}'
make_mutant "bad-thinking"  '{"from":"brain","to":"sister","type":"directive","model":{"tier":"flash","thinking":"max"}}'
make_mutant "missing-role"  '{"from":"brain","type":"directive"}'
make_mutant "sister-issues" '{"from":"sister","to":"brain","type":"directive"}'
make_mutant "misaddressed"  '{"from":"brain","to":"subagent-x","type":"directive"}'
make_mutant "ack-no-correlation" '{"from":"sister","to":"brain","type":"result"}'
make_mutant "brain-acks" '{"from":"brain","to":"sister","type":"ack","correlation_id":"directive-0001"}'
make_mutant "escalate-missing-correlation" '{"from":"sister","to":"brain","type":"escalate","severity":"warn"}'
make_mutant "escalate-from-brain" '{"from":"brain","to":"brain","type":"escalate","correlation_id":"directive-0001","severity":"warn"}'
make_mutant "escalate-bad-severity" '{"from":"sister","to":"brain","type":"escalate","correlation_id":"directive-0001","severity":"max"}'
make_mutant "control-from-sister" '{"from":"sister","to":"brain","type":"directive","control":"poke"}'
make_mutant "control-bad-action" '{"from":"brain","to":"sister","type":"directive","control":"fly"}'
# Per-runtime allowlists for verbs, skills and secrets (issue #1273, parent
# #1268). fleet.override is irreversible and NOT in deepseek-executor's
# allowed_runtimes (control-plane/control/verbs.yaml) — a real, provokable
# refusal, not a fixture the validator could pass vacuously.
make_mutant "verb-not-allowed" '{"schema":2,"from":"director","to":"dispatcher","type":"directive","runtime":"deepseek-executor","task":{"kind":"work","issue":1,"verb":"fleet.override"}}'
make_mutant "skill-not-allowed" '{"schema":2,"from":"director","to":"dispatcher","type":"directive","runtime":"deepseek-executor","task":{"kind":"work","issue":1,"skill":"board-sync-plugin"}}'
make_mutant "secret-not-allowed" '{"schema":2,"from":"director","to":"dispatcher","type":"directive","runtime":"deepseek-executor","task":{"kind":"work","issue":1,"secret":"projects/example/secrets/agent-x-signing-key"}}'
# Laundering: deepseek-executor is denied fleet.override, so it asks a
# permitted runtime (claude-session, the sender) to run it "on its behalf" —
# refused under the ORIGINATING runtime's name, not the intermediary's.
make_mutant "verb-laundering" '{"schema":2,"from":"director","to":"dispatcher","type":"directive","runtime":"claude-session","on_behalf_of":"deepseek-executor","task":{"kind":"work","issue":1,"verb":"fleet.override"}}'

for mutant in bad-type bad-tier bad-thinking missing-role sister-issues misaddressed ack-no-correlation brain-acks escalate-missing-correlation escalate-from-brain escalate-bad-severity control-from-sister control-bad-action verb-not-allowed skill-not-allowed secret-not-allowed verb-laundering; do
  if $channel verify --message "$work/$mutant.json" >/dev/null 2>&1; then
    echo "  FAIL  mutant '$mutant' was accepted (the channel cannot refuse invalid traffic)" >&2
    fail=$((fail + 1))
  else
    echo "  OK    mutant '$mutant' refused"
  fi
done

# A valid correlated result must be accepted (the contract is not over-strict).
printf '%s\n' '{"from":"sister","to":"brain","type":"result","correlation_id":"directive-0001"}' > "$work/valid-result.json"
if $channel verify --message "$work/valid-result.json" >/dev/null 2>&1; then
  echo "  OK    a valid correlated result validates"
else
  echo "  FAIL  a valid correlated result was refused (over-strict contract)" >&2
  fail=$((fail + 1))
fi

printf '%s\n' '{"from":"sister","to":"brain","type":"escalate","correlation_id":"directive-0001","severity":"critical"}' > "$work/valid-escalate.json"
if $channel verify --message "$work/valid-escalate.json" >/dev/null 2>&1; then
  echo "  OK    a valid escalation validates"
else
  echo "  FAIL  a valid escalation was refused (over-strict contract)" >&2
  fail=$((fail + 1))
fi

printf '%s\n' '{"from":"brain","to":"sister","type":"directive","control":"refresh"}' > "$work/valid-control.json"
if $channel verify --message "$work/valid-control.json" >/dev/null 2>&1; then
  echo "  OK    a valid control directive validates"
else
  echo "  FAIL  a valid control directive was refused (over-strict contract)" >&2
  fail=$((fail + 1))
fi

# Each allowlist refusal must name the offender, not just fail generically.
check_needle() { # check_needle <mutant> <needle>
  out="$($channel verify --message "$work/$1.json" 2>&1)"
  if printf '%s' "$out" | grep -qF "$2"; then
    echo "  OK    mutant '$1' named '$2'"
  else
    echo "  FAIL  mutant '$1' did not name '$2':" >&2
    printf '%s\n' "$out" | sed 's/^/          /' >&2
    fail=$((fail + 1))
  fi
}
check_needle "verb-not-allowed" "verb-not-allowed:deepseek-executor:fleet.override"
check_needle "skill-not-allowed" "skill-not-allowed:deepseek-executor:board-sync-plugin"
check_needle "secret-not-allowed" "secret-not-allowed:deepseek-executor:projects/example/secrets/agent-x-signing-key"
check_needle "verb-laundering" "laundering:deepseek-executor:fleet.override"

# A verb the runtime IS allowed must still pass (the gate is not over-strict).
printf '%s\n' '{"schema":2,"from":"director","to":"dispatcher","type":"directive","runtime":"claude-session","task":{"kind":"work","issue":1,"verb":"fleet.override"}}' > "$work/valid-verb-allowed.json"
if $channel verify --message "$work/valid-verb-allowed.json" >/dev/null 2>&1; then
  echo "  OK    a verb the runtime IS allowed still validates"
else
  echo "  FAIL  a permitted runtime/verb pair was refused (over-strict contract)" >&2
  fail=$((fail + 1))
fi

# ── the runaway alarm is a SIGNAL, and it LATCHES (issue #728) ──────────────
#
# The runaway that motivated the attempt cap (#723) — 49 concurrent `make
# verify` runs on one box — was caught by a human NOTICING it, and the epic's own
# lesson is that a transient excursion which clears itself is not an alarm. So
# the health signal's queue-liveness facet is exercised here, in the gate of
# record rather than only by an ad-hoc command, and the LATCH is provoked in the
# open:
#
#   1. a deep inbox (over `AO_RUNAWAY_INBOX_DEPTH`) raises the alarm, and it
#      NAMES the directive and the worktree responsible — the directive from the
#      inbox, the worktree through the claim's `directive_id` and the lane record;
#   2. the excursion is then REMOVED and the alarm re-read: it must still be
#      raised (the latch). If it cleared, this gate FAILS — an alarm that clears
#      silently is a dashboard, not an alarm (GR-12: a control whose pass and
#      fail paths collapse is a formality);
#   3. `ack` clears it, a second `ack` is refused, and the cleared alarm exits 0.
#
# Everything runs in a scratch fleet directory: the alarm must never write into
# the live `.fleet/` of a running loop.
alarm_fleet="$work/alarm/fleet"
alarm_repo="$work/alarm/repo"
alarm_worktree="/home/akushnir/ao-worktrees/ao-728-48a78fdf"
mkdir -p "$alarm_fleet/inbox" "$alarm_repo/.board/claims" "$alarm_repo/.fleet/lanes" || fail=$((fail + 1))

python3 - "$alarm_fleet" "$alarm_repo" "$alarm_worktree" <<'PY' || fail=$((fail + 1))
import json, pathlib, sys
from datetime import datetime, timezone

fleet, repo, worktree = (pathlib.Path(arg) for arg in sys.argv[1:4])
stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

# Four pending directives: two more than the depth cap set below. The first
# carries the issue the lane record and the claim name.
for index, name in enumerate(("directive-728-a", "directive-728-b", "directive-728-c", "directive-728-d")):
    envelope = {
        "id": name,
        "from": "brain",
        "to": "sister",
        "type": "directive",
        "ts": stamp,
        "nonce": f"nonce-{name}",
    }
    if index == 0:
        envelope["task"] = {"kind": "work", "issue": 728}
    (fleet / "inbox" / f"{name}.json").write_text(json.dumps(envelope), encoding="utf-8")

claim = {
    "event": "claim",
    "issue": 728,
    "agent": "check-fleet-channel",
    "at": stamp,
    "lane": "fleet-lane",
    "directive_id": "directive-728-a",
    "directive_from": "brain",
}
(repo / ".board" / "claims" / "00000000000000000001-00728-check-claim.json").write_text(
    json.dumps(claim), encoding="utf-8"
)
lane = {
    "session_id": "checkfleet728",
    "issue": 728,
    "agent_id": "check-fleet-channel",
    "lane": "fleet-lane",
    "branch": "issue-728",
    "worktree": str(worktree),
    "repo_slug": "kushin77/agent-orchestrator",
}
(repo / ".fleet" / "lanes" / "checkfleet728.json").write_text(json.dumps(lane), encoding="utf-8")
PY

run_alarm() {
  env AO_RUNAWAY_INBOX_DEPTH=2 python3 fleet/health.py alarm \
    --fleet-dir "$alarm_fleet" --repo-root "$alarm_repo" --ledger "$alarm_repo/.board/claims"
}

if run_alarm > "$work/alarm/raised.json" 2>&1; then
  raised_rc=0
else
  raised_rc=$?
fi
if [ "$raised_rc" -eq 2 ] \
  && grep -qF '"latched": true' "$work/alarm/raised.json" \
  && grep -qF 'directive-728-a' "$work/alarm/raised.json" \
  && grep -qF "$alarm_worktree" "$work/alarm/raised.json"; then
  echo "  OK    runaway alarm raised (exit 2) and named the directive + worktree responsible"
else
  echo "  FAIL  runaway alarm did not raise and name the culprit (rc=$raised_rc)" >&2
  sed 's/^/        /' "$work/alarm/raised.json" >&2
  fail=$((fail + 1))
fi

# The excursion clears. The alarm must NOT.
rm -f "$alarm_fleet"/inbox/*.json
if run_alarm > "$work/alarm/latched.json" 2>&1; then
  latched_rc=0
else
  latched_rc=$?
fi
if [ "$latched_rc" -eq 2 ] \
  && grep -qF '"measured": false' "$work/alarm/latched.json" \
  && grep -qF '"latched": true' "$work/alarm/latched.json" \
  && grep -qF 'directive-728-a' "$work/alarm/latched.json"; then
  echo "  OK    the alarm LATCHED — still raised, still naming the culprit, with the excursion gone"
else
  echo "  FAIL  the alarm cleared itself when the excursion did (a dashboard, not an alarm)" >&2
  sed 's/^/        /' "$work/alarm/latched.json" >&2
  fail=$((fail + 1))
fi

if python3 fleet/health.py ack --fleet-dir "$alarm_fleet" --by check-fleet-channel > "$work/alarm/acked.json" 2>&1; then
  if run_alarm > "$work/alarm/cleared.json" 2>&1; then
    echo "  OK    ack cleared the latch — the alarm is no longer raised"
  else
    echo "  FAIL  the latch was acknowledged but the alarm is still raised" >&2
    sed 's/^/        /' "$work/alarm/cleared.json" >&2
    fail=$((fail + 1))
  fi
else
  echo "  FAIL  ack refused to clear a raised latch" >&2
  fail=$((fail + 1))
fi

if python3 fleet/health.py ack --fleet-dir "$alarm_fleet" >/dev/null 2>&1; then
  echo "  FAIL  a second ack succeeded — the verb is a no-op, not an acknowledgement" >&2
  fail=$((fail + 1))
else
  echo "  OK    acknowledging nothing is refused"
fi

rm -rf "$work"

if [ "$fail" -gt 0 ]; then
  echo "check-fleet-channel: FAIL ($fail violation(s))" >&2
  exit 1
fi
echo "check-fleet-channel: OK — contract enforced, all mutants refused"
exit 0
