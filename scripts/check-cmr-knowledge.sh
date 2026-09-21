#!/usr/bin/env bash
# check-cmr-knowledge.sh — the knowledge indexer's live CMR pin gate
# (issue #887, lane L8/#878).
#
# scripts/check-cmr-pin.sh gates the repo-root pin's own shape and drift.
# This gate is narrower and knowledge-specific: it drives the SAME drift
# check through governance/knowledge/live_sync.py — the module the indexer
# actually imports — so a passing check-cmr-pin.sh can never mask a broken
# live_sync consumer (a second, disagreeing implementation would be worse
# than none). It also reports the #132/#133 vendor-compliance gaps
# (governance/knowledge/knowledge_controls.py) as WARN, named, mechanically
# measured from the CMR hub's own generated reports — never re-derived.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
# CANNOT-ASSESS must never be reported as a pass. WARN findings (vendor-
# compliance gaps) do not fail the gate — closing #132/#133 is a residual
# outside this lane's owned files — but they are always printed by name.
#
# Usage: bash scripts/check-cmr-knowledge.sh
set -u

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 2

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-cmr-knowledge: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

if [ ! -d "vendor/CMR" ]; then
  echo "check-cmr-knowledge: CANNOT-ASSESS — vendor/CMR submodule not initialized" >&2
  exit 2
fi

# 1. Drive the real live_sync consumer, in-process (never a re-derived check).
sync_out="$(PYTHONPATH="$root/governance/knowledge" python3 "$root/governance/knowledge/live_sync.py" 2>&1)"
sync_rc=$?
printf '%s\n' "$sync_out"

case "$sync_rc" in
  0) echo "check-cmr-knowledge: live_sync OK — no pin drift" ;;
  2)
    echo "check-cmr-knowledge: CANNOT-ASSESS — live_sync could not assess the pin" >&2
    exit 2
    ;;
  *)
    echo "check-cmr-knowledge: NOT-OK — live_sync refused (see CMR_PIN_DRIFT above)" >&2
    exit 1
    ;;
esac

# 2. Negative control: prove the drift refusal actually fires, by DRIVING
#    live_sync.pinned_bundle() against a mutant pin (never re-asserting the
#    comparison inline — that would be a tautology, not a proof).
nc_out="$(python3 - "$root" <<'PYEOF'
import sys
from pathlib import Path

root = Path(sys.argv[1])
sys.path.insert(0, str(root / "governance" / "knowledge"))
import live_sync  # noqa: E402

live_head = live_sync.live_vendor_head()
mutant_head = "0" * 40
assert mutant_head != live_head, "live HEAD cannot be the null SHA"

import tempfile
import yaml

with (root / "cmr-pin.yaml").open("r", encoding="utf-8") as fh:
    data = yaml.safe_load(fh)
data["bundle_ref"] = mutant_head

with tempfile.TemporaryDirectory() as tmp:
    mutant_path = Path(tmp) / "cmr-pin.yaml"
    with mutant_path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh)
    try:
        live_sync.pinned_bundle(mutant_path, require_no_drift=True)
    except live_sync.PinSyncError as exc:
        print(f"{exc.code}: {exc.message}")
        sys.exit(1 if exc.code == live_sync.CODE_PIN_DRIFT else 3)
    else:
        print("mutant was ACCEPTED — drift refusal did not fire")
        sys.exit(0)
PYEOF
)"
nc_rc=$?

if [ "$nc_rc" -eq 0 ]; then
  echo "check-cmr-knowledge: NOT-OK — negative control: a drift mutant was ACCEPTED by live_sync:" >&2
  echo "$nc_out" >&2
  exit 1
fi
if [ "$nc_rc" -ne 1 ] || [[ "$nc_out" != *"CMR_PIN_DRIFT"* ]]; then
  echo "check-cmr-knowledge: NOT-OK — negative control: mutant was not refused by name (CMR_PIN_DRIFT):" >&2
  echo "$nc_out" >&2
  exit 1
fi
echo "check-cmr-knowledge: negative control confirmed — live_sync refused the drift mutant and named CMR_PIN_DRIFT"

# 3. Vendor-compliance gaps (#132/#133): report, never fail the gate on them.
gap_out="$(PYTHONPATH="$root/governance/knowledge" python3 - <<'PYEOF'
import knowledge_controls as kc

any_open = False
for gap in kc.measure_vendor_compliance_gaps():
    if gap.open:
        any_open = True
        print(f"WARN {kc.CODE_VENDOR_COMPLIANCE_GAP}: {gap.repo} (#{gap.issue}): {len(gap.findings)} finding(s) open")
    else:
        print(f"OK vendor-compliance: {gap.repo} (#{gap.issue}): no findings in CMR hub reports")
PYEOF
)"
printf '%s\n' "$gap_out"

echo "check-cmr-knowledge: OK (vendor-compliance gaps reported above are WARN, not a gate failure — residual: close #132/#133)"
exit 0
