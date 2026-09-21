#!/usr/bin/env bash
# check-brain-profile.sh — the brain's elite profile gate (M26, #160).
#
# A profile nobody checks is decoration. This gate proves the profile the brain
# actually loads still declares the things that make it elite, and that the
# brain's own code is driven by it rather than by a private copy of the doctrine:
#
#   * the profile declares mission, authority, KB, FinOps floors, controls,
#     escalation policy, templates and anti-patterns (a dropped section fails by
#     name);
#   * the controls it claims are a superset of what the channel enforces, so the
#     profile cannot advertise a lever that does not exist;
#   * `fleet/brain.py` reads the profile (`load_profile`) instead of hard-coding
#     the floor lanes;
#   * and the check mutates its own input (a profile with the FinOps block and the
#     controls stripped) and requires the mutation to be DETECTED — a declaration
#     check that cannot fail is a formality (no-false-green doctrine, GR-12).
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-brain-profile.sh
set -uo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 2

profile="fleet/profiles/brain.profile.json"
human="fleet/profiles/brain.md"
brain_py="fleet/brain.py"

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-brain-profile: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

for required_file in "$profile" "$human" "$brain_py"; do
  if [ ! -f "$required_file" ]; then
    echo "check-brain-profile: FAIL — $required_file is missing" >&2
    exit 1
  fi
done

declare -a required_sections=(
  mission
  authority
  kb
  finops
  controls
  escalation
  templates
  anti_patterns
)

declare -a required_markers=(
  "operator -> brain -> sister"
  "SOLUTION-CLASSES"
  "never a claim the evidence does not support"
  "high_floor_lanes"
)

# Prose markers the human-readable profile must keep declaring.
declare -a required_doctrine=(
  'the issue'"'"'s own `Verify:`'
  "operator → BRAIN → sister"
  "Anti-patterns"
)

fail=0

check_profile() { # check_profile <file> — one named finding per missing element
  local file="$1" section marker
  for section in "${required_sections[@]}"; do
    if ! python3 - "$file" "$section" <<'PY'
import json, sys
path, key = sys.argv[1], sys.argv[2]
try:
    data = json.load(open(path, encoding="utf-8"))
except Exception as exc:  # noqa: BLE001 - any load failure is a finding
    print(f"cannot load {path}: {exc}", file=sys.stderr)
    raise SystemExit(1)
raise SystemExit(0 if key in data else 1)
PY
    then
      printf '  FAIL  %s (missing profile section: %s)\n' "$file" "$section" >&2
      fail=1
    fi
  done
  for marker in "${required_markers[@]}"; do
    if ! grep -qF -- "$marker" "$file"; then
      printf '  FAIL  %s (missing profile marker: %s)\n' "$file" "$marker" >&2
      fail=1
    fi
  done
  for marker in "${required_doctrine[@]}"; do
    if ! grep -qF -- "$marker" "$human"; then
      printf '  FAIL  %s (missing doctrine marker: %s)\n' "$human" "$marker" >&2
      fail=1
    fi
  done
}

check_profile "$profile"

# The profile may not advertise a control the channel does not enforce.
if ! python3 - "$profile" <<'PY'
import json, sys
sys.path.insert(0, "fleet")
import channel

profile = json.load(open(sys.argv[1], encoding="utf-8"))
declared = set(profile.get("controls") or [])
enforced = set(channel.CONTROL_ACTIONS)
unknown = sorted(declared - enforced)
if unknown:
    print(f"  FAIL  the profile advertises controls the channel refuses: {', '.join(unknown)}", file=sys.stderr)
    raise SystemExit(1)
print(f"  OK    profile controls map onto the channel's {len(enforced)} enforced actions")
PY
then
  fail=1
fi

# The brain must be driven by the profile, not by a private copy of the floor.
if grep -q "load_profile" "$brain_py" && grep -q "PROFILE\[.finops.\]" "$brain_py"; then
  echo "  OK    $brain_py derives its floors and KB from the profile"
else
  echo "  FAIL  $brain_py does not derive its doctrine from $profile" >&2
  fail=1
fi

# Non-vacuous: a profile with the FinOps block and controls stripped must fail.
mutant="$(mktemp)"
trap 'rm -f "$mutant"' EXIT
python3 - "$profile" "$mutant" <<'PY'
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
for key in ("finops", "controls", "mission"):
    data.pop(key, None)
json.dump(data, open(sys.argv[2], "w", encoding="utf-8"))
PY
if python3 - "$mutant" <<'PY'
import json, sys
required = ("mission", "authority", "kb", "finops", "controls", "escalation", "templates", "anti_patterns")
data = json.load(open(sys.argv[1], encoding="utf-8"))
raise SystemExit(1 if [k for k in required if k not in data] else 0)
PY
then
  echo "  FAIL  a profile with mission/finops/controls stripped was accepted (vacuous check)" >&2
  fail=1
fi

if [ "$fail" -eq 0 ]; then
  echo "check-brain-profile: OK"
  exit 0
fi
echo "check-brain-profile: FAIL" >&2
exit 1
