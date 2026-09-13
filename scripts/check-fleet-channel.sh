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

for mutant in bad-type bad-tier bad-thinking missing-role sister-issues misaddressed ack-no-correlation brain-acks escalate-missing-correlation escalate-from-brain escalate-bad-severity control-from-sister control-bad-action; do
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

rm -rf "$work"

if [ "$fail" -gt 0 ]; then
  echo "check-fleet-channel: FAIL ($fail violation(s))" >&2
  exit 1
fi
echo "check-fleet-channel: OK — contract enforced, all mutants refused"
exit 0
