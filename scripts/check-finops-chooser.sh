#!/usr/bin/env bash
# check-finops-chooser.sh — FinOps tier/thinking enforcement (M26, issue #164).
#
# The chooser (governance/finops/chooser.py) is the control that keeps a
# subagent from choosing its own model. This gate proves the control BITES
# rather than merely existing:
#
#   * the vocabulary is the harvested one — pinned here, cross-checked against
#     the policy, the fleet message schema, the channel constants and the docs;
#   * the standing directive is accepted and lands the sister on flash/none;
#   * every mutant is REFUSED with a NAMED finding: unknown tier, unknown
#     thinking level, a role above its allowlist, a self-override up and down,
#     and a spawn record that does not reproduce the brain's choice.
#
# If any mutant is accepted, this check FAILS: a chooser that honours an
# override is a formality (GR-12 / AO-GR-19).
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-finops-chooser.sh
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

# ── the pinned harvested vocabulary ────────────────────────────────────────
# Read-only harvest, 2026-09-13 (provenance: docs/CANNIBALIZATION.md §7):
#   tiers:    kushin77/capital-underwriting config/leaderboard/tier-policy.json
#             -> the tiers{} keys (flash, pro, auditor)
#   thinking: kushin77/leaderboard lib/fleet-roster.sh role_effort()
#             -> low | medium | high, plus `none` = the fleet thinking-off state
#             (LB_THINKING_DEEPSEEK=disabled)
# A rename in any one place must fail this gate, not drift silently.
EXPECT_TIERS="flash, pro, auditor"
EXPECT_THINKING="none, low, medium, high"

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-finops-chooser: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

fail=0

for required in \
  governance/finops/policy.json \
  governance/finops/chooser.py \
  governance/finops/README.md \
  fleet/schema/message.schema.json \
  fleet/channel.py \
  fleet/directive.json
do
  if [ -f "$required" ]; then
    echo "  OK    $required present"
  else
    echo "  FAIL  $required is missing" >&2
    fail=$((fail + 1))
  fi
done
if [ "$fail" -gt 0 ]; then
  echo "check-finops-chooser: FAIL ($fail missing file(s))" >&2
  exit 1
fi

chooser="python3 governance/finops/chooser.py"

echo "== vocabulary =="
expected_vocab="$(printf 'tiers: %s\nthinking: %s\n' "$EXPECT_TIERS" "$EXPECT_THINKING")"
actual_vocab="$($chooser vocabulary 2>&1)"
if [ "$actual_vocab" = "$expected_vocab" ]; then
  echo "  OK    chooser vocabulary matches the harvest (tiers: $EXPECT_TIERS | thinking: $EXPECT_THINKING)"
else
  echo "  FAIL  chooser vocabulary drifted from the harvest" >&2
  printf '        expected: %s\n' "$(printf '%s' "$expected_vocab" | tr '\n' ' ')" >&2
  printf '        actual:   %s\n' "$(printf '%s' "$actual_vocab" | tr '\n' ' ')" >&2
  fail=$((fail + 1))
fi

# One vocabulary, four declarations: policy, schema, channel, docs.
if python3 - "$EXPECT_TIERS" "$EXPECT_THINKING" <<'PY'
import json
import sys
from pathlib import Path

expect_tiers = [part.strip() for part in sys.argv[1].split(",")]
expect_thinking = [part.strip() for part in sys.argv[2].split(",")]
problems = []

policy = json.loads(Path("governance/finops/policy.json").read_text(encoding="utf-8"))
if policy["vocabulary"]["tiers"] != expect_tiers:
    problems.append(f"policy.json tiers = {policy['vocabulary']['tiers']}")
if policy["vocabulary"]["thinking_levels"] != expect_thinking:
    problems.append(f"policy.json thinking_levels = {policy['vocabulary']['thinking_levels']}")

schema = json.loads(Path("fleet/schema/message.schema.json").read_text(encoding="utf-8"))
model = schema["properties"]["model"]["properties"]
if model["tier"]["enum"] != expect_tiers:
    problems.append(f"message.schema.json model.tier.enum = {model['tier']['enum']}")
if model["thinking"]["enum"] != expect_thinking:
    problems.append(f"message.schema.json model.thinking.enum = {model['thinking']['enum']}")

sys.path.insert(0, "fleet")
import channel  # noqa: E402

if list(channel.MODEL_TIERS) != expect_tiers:
    problems.append(f"channel.MODEL_TIERS = {list(channel.MODEL_TIERS)}")
if list(channel.THINKING_LEVELS) != expect_thinking:
    problems.append(f"channel.THINKING_LEVELS = {list(channel.THINKING_LEVELS)}")

readme = Path("governance/finops/README.md").read_text(encoding="utf-8")
start, end = "<!-- finops-vocabulary:start -->", "<!-- finops-vocabulary:end -->"
if start not in readme or end not in readme:
    problems.append("governance/finops/README.md carries no finops-vocabulary block")
else:
    block = readme.split(start, 1)[1].split(end, 1)[0].strip().splitlines()
    block = [line.strip() for line in block if line.strip()]
    if block != [f"tiers: {', '.join(expect_tiers)}", f"thinking: {', '.join(expect_thinking)}"]:
        problems.append(f"README vocabulary block = {block}")

if problems:
    print("  FAIL  the FinOps vocabulary disagrees across declarations:", file=sys.stderr)
    for problem in problems:
        print(f"        {problem}", file=sys.stderr)
    raise SystemExit(1)
print("  OK    policy.json, message.schema.json, channel.py and the README agree")
PY
then
  :
else
  fail=$((fail + 1))
fi

work="/tmp/ao164-finops.$$.$(date +%s)"
mkdir "$work" || exit 2
trap 'rm -rf "$work"' EXIT

make_directive() { # make_directive <file> <tier> <thinking>
  printf '{"id":"%s","from":"brain","to":"sister","type":"directive","model":{"tier":"%s","thinking":"%s"},"task":{"issue":164}}\n' \
    "$(basename "$1" .json)" "$2" "$3" > "$1"
}

expect_accept() { # expect_accept <name> <cmd...>
  local name="$1"; shift
  local out rc
  out="$("$@" 2>&1)"; rc=$?
  if [ "$rc" -eq 0 ]; then
    echo "  OK    ACCEPTED $name"
  else
    echo "  FAIL  $name was refused (rc=$rc) — the contract is over-strict" >&2
    printf '%s\n' "$out" | sed 's/^/        /' >&2
    fail=$((fail + 1))
  fi
}

expect_refusal() { # expect_refusal <name> <finding> <cmd...>
  local name="$1" want="$2"; shift 2
  local out rc
  out="$("$@" 2>&1)"; rc=$?
  if [ "$rc" -eq 1 ] && printf '%s' "$out" | grep -q "$want"; then
    echo "  OK    REFUSED $name — $want"
  elif [ "$rc" -eq 0 ]; then
    echo "  FAIL  $name was ACCEPTED (a chooser that honours an override is a formality)" >&2
    printf '%s\n' "$out" | sed 's/^/        /' >&2
    fail=$((fail + 1))
  else
    echo "  FAIL  $name refused with rc=$rc but not the named finding $want" >&2
    printf '%s\n' "$out" | sed 's/^/        /' >&2
    fail=$((fail + 1))
  fi
}

echo "== accept controls =="
expect_accept "the standing directive puts the sister on flash/none" \
  $chooser choose --directive fleet/directive.json --role sister --json
make_directive "$work/sub-flash.json" flash none
expect_accept "a subagent at the tier the brain chose" \
  $chooser choose --directive "$work/sub-flash.json" --role subagent-gate --write "$work/spawn.json"
expect_accept "the recorded spawn re-verified against its directive" \
  $chooser verify-spawn --directive "$work/sub-flash.json" --spawn "$work/spawn.json"
expect_accept "the channel accepts the same standing directive" \
  python3 fleet/channel.py verify --message fleet/directive.json

standing="$($chooser choose --directive fleet/directive.json --role sister 2>/dev/null)"
case "$standing" in
  *flash/none*chosen\ by\ brain*)
    echo "  OK    standing directive spawn is flash/none chosen by the brain" ;;
  *)
    echo "  FAIL  standing directive spawn is not flash/none chosen by the brain: $standing" >&2
    fail=$((fail + 1)) ;;
esac

echo "== refused mutants =="
make_directive "$work/unknown-tier.json" ultra none
expect_refusal "unknown tier (ultra)" FINOPS-UNKNOWN-TIER \
  $chooser choose --directive "$work/unknown-tier.json" --role sister

make_directive "$work/unknown-thinking.json" flash max
expect_refusal "unknown thinking level (max)" FINOPS-UNKNOWN-THINKING \
  $chooser choose --directive "$work/unknown-thinking.json" --role sister

make_directive "$work/sister-pro.json" pro none
expect_refusal "the sister dispatched at pro" FINOPS-ROLE-NOT-ALLOWED \
  $chooser choose --directive "$work/sister-pro.json" --role sister

make_directive "$work/sister-thinking.json" flash high
expect_refusal "the sister dispatched with thinking" FINOPS-ROLE-NOT-ALLOWED \
  $chooser choose --directive "$work/sister-thinking.json" --role sister

printf '%s\n' '{"from":"brain","to":"sister","type":"directive","task":{"issue":164}}' > "$work/no-block.json"
expect_refusal "a directive with no FinOps block" FINOPS-MISSING-MODEL-BLOCK \
  $chooser choose --directive "$work/no-block.json" --role sister

expect_refusal "a subagent escalating its own tier" FINOPS-SELF-ESCALATION \
  $chooser choose --directive "$work/sub-flash.json" --role subagent-gate --request-tier auditor
make_directive "$work/sub-pro.json" pro high
expect_refusal "a subagent downgrading its own tier" FINOPS-SELF-ESCALATION \
  $chooser choose --directive "$work/sub-pro.json" --role subagent-gate --request-tier flash
expect_refusal "a subagent raising its own thinking effort" FINOPS-SELF-ESCALATION \
  $chooser choose --directive "$work/sub-flash.json" --role subagent-gate --request-thinking high
expect_refusal "a subagent lowering its own thinking effort" FINOPS-SELF-ESCALATION \
  $chooser choose --directive "$work/sub-pro.json" --role subagent-gate --request-thinking none
expect_refusal "an unknown requested tier (turbo)" FINOPS-UNKNOWN-TIER \
  $chooser choose --directive "$work/sub-flash.json" --role subagent-gate --request-tier turbo
expect_refusal "an unknown requested thinking level (max)" FINOPS-UNKNOWN-THINKING \
  $chooser choose --directive "$work/sub-flash.json" --role subagent-gate --request-thinking max

python3 - "$work/spawn.json" "$work/spawn-tampered.json" "$work/spawn-self.json" <<'PY'
import json
import sys

record = json.loads(open(sys.argv[1], encoding="utf-8").read())
raised = dict(record)
raised["tier"] = "auditor"
raised["model"] = "deepseek-v4-pro"
with open(sys.argv[2], "w", encoding="utf-8") as handle:
    json.dump(raised, handle)

self_made = dict(record)
self_made["chosen_by"] = "subagent-gate"
with open(sys.argv[3], "w", encoding="utf-8") as handle:
    json.dump(self_made, handle)
PY

expect_refusal "a spawn record with a raised tier" FINOPS-SPAWN-TAMPERED \
  $chooser verify-spawn --directive "$work/sub-flash.json" --spawn "$work/spawn-tampered.json"
expect_refusal "a spawn record the subagent authored itself" FINOPS-SPAWN-TAMPERED \
  $chooser verify-spawn --directive "$work/sub-flash.json" --spawn "$work/spawn-self.json"

if [ "$fail" -gt 0 ]; then
  echo "check-finops-chooser: FAIL ($fail violation(s))" >&2
  exit 1
fi
echo "check-finops-chooser: OK — vocabulary pinned across 4 declarations, tier/thinking enforced, all mutants refused"
exit 0
