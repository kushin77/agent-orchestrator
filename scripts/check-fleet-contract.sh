#!/usr/bin/env bash
# check-fleet-contract.sh — the session-fleet steering contract gate (M26, #161).
#
# The operating model (brain -> fleet brain sister -> epic-focused subagents) is
# only a contract if breaking it fails a check (no-false-green doctrine, GR-12).
# This gate pins the contract document and proves it is still about the transport
# that actually ships:
#
#   * fleet/CONTRACT.md declares the roles, the six directive verbs, the message
#     envelope fields (including correlation_id and nonce), the FinOps block, the
#     DSv4FNone sister setting, the schema it defers to and the ADR that records
#     the transport decision;
#   * the four trust rules are declared as text — removing the trust model is a
#     named failure, not a silent pass;
#   * every declared verb maps onto an envelope type the channel actually
#     implements (verbs and enforcement cannot drift apart);
#   * the transport ADR (ADR-0011) is referenced by the channel itself;
#   * the A2A steering channel is declared the PRIMARY control plane (§7.1,
#     issue #763) — a claim that existed nowhere in this file until then, so a
#     later lane could have downgraded it back to "optional extension" and
#     nothing would have failed. Its markers are pinned by name and the
#     declaration is mutation-proved like the trust model;
#   * the runaway + queue-liveness alarm (#728) is still declared — the latch,
#     the `ack` that is its only clear, and every threshold `fleet/health.py`
#     actually reads, so the knobs cannot drift out of the contract;
#   * and the check mutates its own input (a contract with the trust model
#     stripped, and a contract with the primary-control-plane declaration
#     stripped) and requires each mutation to be DETECTED — a declaration check
#     that cannot fail is a formality.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-fleet-contract.sh
#
# ---knowledge---
# module_id: scripts.check-fleet-contract
# system: governance
# app: gates
# solution_class: enterprise
# patterns: [tri-state-exit, provoked-negative-control, no-false-green, schema-validation]
# derives_from: null
# owner_sme: platform-sme
# tier: L1
# interfaces: [exit 0 OK, exit 1 NOT-OK, exit 2 CANNOT-ASSESS]
# invariants: ""
# gotchas: ""
# related: ["#161", "#728", "#763"]
# do_not_duplicate: null
# ---knowledge---
set -uo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 2

contract="fleet/CONTRACT.md"
schema="fleet/schema/message.schema.json"
adr="docs/decision-records/ADR-0011-session-fleet-transport.md"
channel_py="fleet/channel.py"

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-fleet-contract: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

for required_file in "$contract" "$schema" "$adr" "$channel_py"; do
  if [ ! -f "$required_file" ]; then
    echo "check-fleet-contract: FAIL — $required_file is missing" >&2
    exit 1
  fi
done

# The directive vocabulary: exactly these six verbs (issue #161 scope). Each one
# must be declared in the contract AND must map onto a message type the channel
# implements.
declare -a required_verbs=(
  spawn-epic-agent
  dispatch-issue
  model-directive
  handoff
  halt
  report
)

# Distinctive text every contract revision must keep declaring. Removing a
# concept (or renaming it out of existence) fails this gate by name.
declare -a required_markers=(
  "brain"
  "sister"
  "subagent"
  "correlation_id"
  "nonce"
  "FinOps"
  "thinking"
  "DSv4FNone"
  "fleet/schema/message.schema.json"
  "ADR-0011"
  # The runaway + queue-liveness alarm (issue #728): the contract is silent
  # about it, or it is no longer an alarm.
  "fleet/health.py"
  "LATCHES"
  "ack"
  "runaway"
)

# The trust model, verbatim. These four rules are the contract's teeth: only the
# brain issues directives to the sister, the sister only spawns per a directive,
# subagents report back through the sister, everything else is refused.
declare -a required_rules=(
  "Only the brain may issue directives to the sister."
  "The sister may only spawn subagents per a directive."
  "Subagents report back through the sister."
  "Everything else is refused."
)

# The primary-control-plane declaration (issue #763). Each phrase exists ONLY in
# the §7.1 subsection that makes the A2A steering channel the fleet's primary
# control plane — the claim §7's "extension" heading read against — so removing
# the claim, or renaming the invariants out of the contract, fails by name here.
# Keep every probe on ONE source line: this file strips lines by marker, and a
# probe that spans a wrap is one the mutation control cannot honestly remove.
declare -a required_primary_markers=(
  "A2A is the PRIMARY control plane — not a fallback"
  "à-la-carte reachability invariant"
  "the only authorisation for work"
  "never by silent overwrite"
)

missing_markers_in() { # missing_markers_in <file> <marker...>
  local file="$1"; shift
  local missing=0 marker
  for marker in "$@"; do
    if ! grep -qF -- "$marker" "$file"; then
      printf '  FAIL  %s (missing contract text: %s)\n' "$file" "$marker" >&2
      missing=1
    fi
  done
  return "$missing"
}

missing_from() { # missing_from <file> — one named finding per missing element
  local file="$1" missing=0 rule verb
  missing_markers_in "$file" "${required_markers[@]}" || missing=1
  for rule in "${required_rules[@]}"; do
    if ! grep -qF -- "$rule" "$file"; then
      printf '  FAIL  %s (missing trust rule: %s)\n' "$file" "$rule" >&2
      missing=1
    fi
  done
  for verb in "${required_verbs[@]}"; do
    if ! grep -qF -- "$verb" "$file"; then
      printf '  FAIL  %s (missing directive verb: %s)\n' "$file" "$verb" >&2
      missing=1
    fi
  done
  missing_markers_in "$file" "${required_primary_markers[@]}" || missing=1
  return "$missing"
}

missing_legacy_from() { # missing_legacy_from <file> — everything the gate pinned before #763
  local file="$1" missing=0 rule verb
  missing_markers_in "$file" "${required_markers[@]}" || missing=1
  for rule in "${required_rules[@]}"; do
    if ! grep -qF -- "$rule" "$file"; then
      missing=1
    fi
  done
  for verb in "${required_verbs[@]}"; do
    if ! grep -qF -- "$verb" "$file"; then
      missing=1
    fi
  done
  return "$missing"
}

fail=0

if missing_from "$contract" >/dev/null 2>&1; then
  echo "  OK    $contract declares roles, verbs, envelope fields, trust rules and the primary control plane"
else
  echo "== $contract =="
  missing_from "$contract" || true
  fail=$((fail + 1))
fi

# Every declared verb must map onto a message type the channel implements — the
# vocabulary is useless if the transport cannot express it.
if python3 - "$contract" "${required_verbs[@]}" <<'PY'
import pathlib
import re
import sys

contract = pathlib.Path(sys.argv[1])
required = set(sys.argv[2:])
text = contract.read_text(encoding="utf-8")

if "## 2. Directive vocabulary" not in text:
    print(f"  FAIL  {contract} (missing the directive vocabulary section)", file=sys.stderr)
    raise SystemExit(1)
section = text.split("## 2. Directive vocabulary", 1)[1].split("## 3.", 1)[0]
mapping = dict(re.findall(r"^\|\s*`([a-z][a-z-]*)`\s*\|\s*`([a-z]+)`\s*\|", section, re.MULTILINE))

sys.path.insert(0, str(pathlib.Path("fleet").resolve()))
import channel  # noqa: E402  (repo convention: namespace module, path bootstrapped above)

problems = []
for verb in sorted(required - set(mapping)):
    problems.append(f"verb '{verb}' is not declared in the vocabulary table")
for verb in sorted(set(mapping) - required):
    problems.append(f"unknown verb '{verb}' in the vocabulary table")
for verb, envelope in sorted(mapping.items()):
    if envelope not in channel.MESSAGE_TYPES:
        problems.append(f"verb '{verb}' maps to '{envelope}', which the channel does not implement")

if problems:
    for problem in problems:
        print(f"  FAIL  {contract} ({problem})", file=sys.stderr)
    raise SystemExit(1)
print(f"  OK    all {len(required)} directive verbs map to channel message types")
PY
then
  :
else
  fail=$((fail + 1))
fi

# The transport decision must be referenced by the channel it decides.
if grep -qF -- "ADR-0011" "$channel_py"; then
  echo "  OK    $channel_py references the transport decision (ADR-0011)"
else
  echo "  FAIL  $channel_py does not reference the transport decision (ADR-0011)" >&2
  fail=$((fail + 1))
fi

# The alarm's knobs must be declared in the contract, so documentation cannot
# drift from the code that reads them (issue #728). The names are taken from
# `fleet/health.py` itself, never re-typed here: a knob added to the module and
# left undocumented fails, and a knob documented but no longer read fails too.
if python3 - <<'PY'
import pathlib
import re
import sys

health = pathlib.Path("fleet/health.py").read_text(encoding="utf-8")
contract_text = pathlib.Path("fleet/CONTRACT.md").read_text(encoding="utf-8")
declared = re.findall(r'^ENV_MAX_[A-Z_]+ = "([^"]+)"', health, re.MULTILINE)
if not declared:
    print("  FAIL  fleet/health.py declares no ENV_MAX_* threshold to document", file=sys.stderr)
    raise SystemExit(1)

# A WHOLE-TOKEN match, not a substring: renaming the knob in the contract to
# `..._XX` leaves the old text as a substring, so a `in` test would report the
# renamed knob as documented and the drift control would be vacuous.
def documented(name: str) -> bool:
    return re.search(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", contract_text) is not None


undocumented = [name for name in declared if not documented(name)]
if undocumented:
    for name in undocumented:
        print(f"  FAIL  {name} is read by fleet/health.py but not declared in fleet/CONTRACT.md", file=sys.stderr)
    raise SystemExit(1)
print(f"  OK    all {len(declared)} runaway-alarm threshold(s) are declared in the contract")
PY
then
  :
else
  fail=$((fail + 1))
fi

# Vacuity control: strip the trust model and require the check to notice. A
# declaration check whose pass and fail paths collapse into one exit code is a
# formality, so this gate fails if its own mutation goes undetected.
work="/tmp/fleet-contract.$$.$(date +%s%N)"
if ! mkdir "$work" 2>/dev/null; then
  echo "check-fleet-contract: CANNOT-ASSESS — cannot create a scratch directory" >&2
  exit 2
fi
trap 'rm -rf "$work"' EXIT

grep -vF \
  -e "Only the brain may issue directives to the sister." \
  -e "The sister may only spawn subagents per a directive." \
  -e "Subagents report back through the sister." \
  -e "Everything else is refused." \
  "$contract" > "$work/contract-without-trust-model.md"

if missing_from "$work/contract-without-trust-model.md" >/dev/null 2>&1; then
  echo "  FAIL  vacuity control: removing the trust model went undetected" >&2
  fail=$((fail + 1))
else
  echo "  OK    vacuity control: removing the trust model is detected"
fi

# Vacuity control for the primary-control-plane declaration (issue #763), in the
# same style: strip the claim and require the check to notice. Two conditions, so
# the control cannot pass for the wrong reason — the mutant must FAIL, and it must
# fail *only* for the stripped claim (the rest of the contract has to survive the
# strip), otherwise the detection is collateral and proves nothing about §7.1.
grep -vF \
  -e "${required_primary_markers[0]}" \
  -e "${required_primary_markers[1]}" \
  -e "${required_primary_markers[2]}" \
  -e "${required_primary_markers[3]}" \
  "$contract" > "$work/contract-without-primary-control-plane.md"

if missing_markers_in "$work/contract-without-primary-control-plane.md" \
    "${required_primary_markers[@]}" >/dev/null 2>&1; then
  echo "  FAIL  vacuity control: removing the primary-control-plane declaration went undetected" >&2
  fail=$((fail + 1))
elif ! missing_legacy_from "$work/contract-without-primary-control-plane.md" >/dev/null 2>&1; then
  echo "  FAIL  vacuity control: the strip removed more than the declaration (detection not attributable)" >&2
  fail=$((fail + 1))
else
  echo "  OK    vacuity control: removing the primary-control-plane declaration is detected"
fi

if [ "$fail" -gt 0 ]; then
  echo "check-fleet-contract: FAIL ($fail violation(s))" >&2
  exit 1
fi
echo "check-fleet-contract: OK — contract declares the topology, vocabulary, trust rules and the primary control plane"
exit 0
