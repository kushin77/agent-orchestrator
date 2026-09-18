#!/usr/bin/env bash
# check-cmr-pin.sh — the root `cmr-pin.yaml` gate (issue #949, parent #878).
#
# The CMR module template (vendor/CMR/templates/module/cmr-pin.yaml) prescribes
# a spoke-side pin record declaring which CMR standards bundle this repo last
# synced (EPIC-26/M23, GH #453). A pin nothing validates is a formality
# (GR-12), so this gate:
#
#   * requires cmr-pin.yaml to exist at the repo root and validates it against
#     vendor/CMR/sync/cmr-pin.schema.json via the hub's own
#     vendor/CMR/sync/validate-cmr-pin.py — the schema is read, never
#     duplicated here;
#   * refuses DRIFT: the pin's `bundle_ref` must equal
#     `git -C vendor/CMR rev-parse HEAD` — the hub submodule this repo is
#     actually pinned to right now. A pin recorded against a hub commit the
#     submodule has since moved past (or never was at) is worse than no pin,
#     because a consumer would trust a stale bundle_ref;
#   * PROVOKES the drift refusal in a scratch copy of the pin (never editing
#     the real cmr-pin.yaml) by mutating bundle_ref to a value that cannot
#     equal the live submodule HEAD, and requires it refused BY NAME
#     (CMR-PIN-DRIFT), so the drift path is proven and not merely asserted.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-cmr-pin.sh
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

# Only the self-invoked negative-control child (CMR_PIN_NEGATIVE_CONTROL=1,
# below) may redirect which pin is measured. Without that guard, anyone could
# set CMR_PIN_FILE to point this gate at a clean pin elsewhere while the
# repo's real root cmr-pin.yaml goes unmeasured — a gate that can be pointed
# away from what it claims to check is a gate that cannot fail (GR-12).
if [ -n "${CMR_PIN_NEGATIVE_CONTROL:-}" ]; then
  pin="${CMR_PIN_FILE:-cmr-pin.yaml}"
else
  pin="cmr-pin.yaml"
fi
validator="vendor/CMR/sync/validate-cmr-pin.py"

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-cmr-pin: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

if [ ! -f "$pin" ]; then
  echo "check-cmr-pin: NOT-OK — $pin not found at repo root" >&2
  exit 1
fi

if [ ! -f "$validator" ]; then
  echo "check-cmr-pin: CANNOT-ASSESS — $validator not present (vendor/CMR submodule not initialized?)" >&2
  exit 2
fi

# 1. Schema validation, via the hub's own validator — never duplicated here.
if ! schema_out="$(python3 "$validator" "$pin" 2>&1)"; then
  echo "check-cmr-pin: NOT-OK — schema validation failed:" >&2
  echo "$schema_out" >&2
  exit 1
fi
echo "check-cmr-pin: $schema_out"

# 2. Drift check: bundle_ref must equal the live submodule HEAD.
live_head="$(git -C vendor/CMR rev-parse HEAD 2>/dev/null)"
if [ -z "$live_head" ]; then
  echo "check-cmr-pin: CANNOT-ASSESS — could not resolve vendor/CMR HEAD" >&2
  exit 2
fi

pinned_ref="$(python3 - "$pin" <<'PYEOF'
import sys, yaml
with open(sys.argv[1], encoding="utf-8") as fh:
    data = yaml.safe_load(fh)
print(data.get("bundle_ref", ""))
PYEOF
)"

if [ "$pinned_ref" != "$live_head" ]; then
  echo "check-cmr-pin: NOT-OK — CMR-PIN-DRIFT: $pin bundle_ref=$pinned_ref != vendor/CMR HEAD=$live_head" >&2
  exit 1
fi
echo "check-cmr-pin: bundle_ref matches vendor/CMR HEAD ($live_head) — no drift"

# 3. Negative control: provoke the drift refusal by DRIVING the real code path
#    above (steps 1-2) against a mutant, never by re-deriving the assertion
#    inline (that would be a tautology, not a proof — GR-12/no-false-green).
#    This self-invokes the gate itself (via CMR_PIN_NEGATIVE_CONTROL, to avoid
#    infinite recursion) on a scratch copy of the pin whose bundle_ref cannot
#    equal the live HEAD, and requires the CHILD PROCESS to actually exit
#    non-zero and name CMR-PIN-DRIFT. A gate whose negative control cannot
#    fail if the drift check were deleted proves nothing.
if [ -z "${CMR_PIN_NEGATIVE_CONTROL:-}" ]; then
    scratch="$(mktemp -d "/tmp/ao877-cmr-pin.$(printf 'X%.0s' 1 2 3 4 5 6)")"

  scratch_pin="$scratch/cmr-pin.yaml"
  python3 - "$pin" "$scratch_pin" "$live_head" <<'PYEOF'
import sys, yaml
src, dst, live_head = sys.argv[1], sys.argv[2], sys.argv[3]
with open(src, encoding="utf-8") as fh:
    data = yaml.safe_load(fh)
# 40 zeros: schema-valid (bundle_ref matches ^[0-9a-f]{40}$), so the mutant
# clears step 1 and actually reaches the step-2 drift comparison, and is
# provably not the live HEAD (a real git SHA is never all zeros).
mutant = "0" * 40
assert mutant != live_head, "live HEAD cannot be the null SHA"
data["bundle_ref"] = mutant
with open(dst, "w", encoding="utf-8") as fh:
    yaml.safe_dump(data, fh)
PYEOF

  nc_out="$(CMR_PIN_NEGATIVE_CONTROL=1 CMR_PIN_FILE="$scratch_pin" bash "$0" 2>&1)"
  nc_rc=$?

  if [ "$nc_rc" -eq 0 ]; then
    echo "check-cmr-pin: NOT-OK — negative control: a drift mutant ($scratch_pin, bundle_ref=all-zero) was ACCEPTED (rc 0); the drift refusal does not fire:" >&2
    echo "$nc_out" >&2
    exit 1
  fi

  if [[ "$nc_out" != *"CMR-PIN-DRIFT"* ]]; then
    echo "check-cmr-pin: NOT-OK — negative control: mutant was refused (rc $nc_rc) but not by name (CMR-PIN-DRIFT missing):" >&2
    echo "$nc_out" >&2
    exit 1
  fi

  echo "check-cmr-pin: negative control confirmed — child gate refused the drift mutant (rc $nc_rc) and named CMR-PIN-DRIFT"
fi

echo "check-cmr-pin: OK"
exit 0
