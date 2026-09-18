#!/usr/bin/env bash
# check-lane-collision.sh — collision-aware wave planning (issue #740, dispatch
# half of EPIC #708).
#
# THE DEFECT THIS EXISTS FOR
#   Measured 2026-09-14: the fan-out-by-issue dispatch loop produced 27
#   source-file collisions across 14 open-issue lanes — six siblings (#716-722)
#   all editing the same seven files. Nothing computing the ready wave ever
#   checked pairwise file-disjointness against each child's own `Files:`
#   declaration before dispatch.
#
# WHAT IS PROVEN (against the real modules, not a description of them)
#   * `governance/dispatch/order.wave_plan` admits a pairwise file-disjoint
#     subset of a ready wave and REFUSES a later candidate BY NAME:
#     `lane-file-collision: <file> already owned by #<n>`.
#   * `fleet/decompose_policy.wave_problems` (the pre-filing wave validator)
#     carries the same collision check for children about to be filed.
#   * An issue/child with no `Files:` declaration is reported (unverifiable),
#     never silently passed as disjoint.
#   * Both reuse the SAME predicate, `governance/dispatch/model.py`'s
#     `file_claims_conflict` — proven by mutating that one function and
#     watching BOTH callers stop refusing the negative control.
#
# PROVOCATION (GR-12, no-false-green)
#   `file_claims_conflict` is replaced (in a scratch copy of `model.py`, never
#   the real file) with a version that always returns False — i.e. "nothing
#   ever collides". Against that mutant, the SAME two-child negative control
#   that the real module refuses must be ADMITTED (no refusal), so the gate can
#   prove it is capable of failing. The real file is untouched throughout, and
#   its sha256 is asserted identical before and after.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS. No network, no writes
# outside a scratch directory.
set -uo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-lane-collision: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

for required in governance/dispatch/model.py governance/dispatch/order.py \
                fleet/decompose_policy.py \
                governance/dispatch/tests/test_order.py \
                fleet/tests/test_decompose_policy.py; do
  if [ ! -f "$required" ]; then
    echo "check-lane-collision: FAIL — $required is missing" >&2
    exit 1
  fi
done

model_file="governance/dispatch/model.py"
before_sha="$(sha256sum "$model_file" | awk '{print $1}')"

work="/tmp/ao-lane-collision.$(date +%s%N).$$"
mkdir "$work" 2>/dev/null || {
  echo "check-lane-collision: CANNOT-ASSESS — no scratch directory available" >&2
  exit 2
}
cleanup() { rm -rf "$work"; }
trap cleanup EXIT

fail=0
ok()  { printf '  OK    %s\n' "$1"; }
bad() { printf '  FAIL  %s\n' "$1" >&2; fail=$((fail + 1)); }

# --- 1. the real suites pass ---------------------------------------------
if python3 -m pytest governance/dispatch/tests/test_order.py fleet/tests/test_decompose_policy.py -q \
    >"$work/pytest.out" 2>&1; then
  ok "governance/dispatch + fleet decompose-policy wave-collision tests pass"
else
  bad "the wave-collision pytest suites failed:"
  tail -n 20 "$work/pytest.out" >&2
fi

# --- 2. the negative control: two ready children/issues sharing a file -----
control_py="$work/control.py"
cat > "$control_py" <<'PYEOF'
import sys
sys.path.insert(0, "governance/dispatch")
sys.path.insert(0, "fleet")
import order
import decompose_policy
from model import Issue

i1 = Issue(716, "first", milestone="M", files=("Makefile", "scripts/verify.sh"))
i2 = Issue(717, "second", milestone="M", files=("Makefile",))
plan = order.wave_plan([i1, i2])
assert plan.admitted == (716,), plan
assert any(r == "lane-file-collision: Makefile already owned by #716" for r in plan.refusals), plan.refusals

a = {"title": "first", "lane": "fleet", "verify": "pytest -q x", "criterion": "c",
     "files": ["Makefile", "scripts/verify.sh"]}
b = {"title": "second", "lane": "fleet", "verify": "pytest -q y", "criterion": "c",
     "files": ["Makefile"]}
problems = decompose_policy.wave_problems([a, b], cap=12)
assert any(p.startswith("lane-file-collision: Makefile already owned by child 0") for p in problems), problems

# unverifiable child is reported, never silently passed
undeclared = {"title": "third", "lane": "fleet", "verify": "pytest -q z", "criterion": "c", "files": []}
undeclared_problems = decompose_policy.wave_problems([undeclared], cap=12)
assert any("names no Files:" in p for p in undeclared_problems), undeclared_problems

print("negative-control: OK — collision refused by name, unverifiable child reported")
PYEOF

if python3 "$control_py" >"$work/control.out" 2>&1; then
  ok "negative control: both dispatch-point callers refuse the collision BY NAME"
  cat "$work/control.out"
else
  bad "negative control did not refuse the collision as expected:"
  cat "$work/control.out" >&2
fi

# --- 3. mutate the disjointness predicate; the SAME control must then pass --
mutant_dir="$work/mutant"
mkdir -p "$mutant_dir/governance/dispatch" "$mutant_dir/fleet"
cp -r governance/dispatch/. "$mutant_dir/governance/dispatch/"
cp -r fleet/. "$mutant_dir/fleet/"
# `governance/dispatch/model.py` resolves `governance.policy` as a real package
# import (it inserts the repo root onto sys.path), so the mutant tree needs the
# rest of `governance/` alongside its own mutated `dispatch/` — symlinked, not
# copied, since only `model.py` is meant to differ.
ln -s "$root/governance/policy" "$mutant_dir/governance/policy"
touch "$mutant_dir/governance/__init__.py" 2>/dev/null || true

python3 - "$mutant_dir/governance/dispatch/model.py" <<'PYEOF'
import sys
path = sys.argv[1]
src = open(path, encoding="utf-8").read()
marker = "def file_claims_conflict(a: FileClaim, b: FileClaim) -> bool:"
assert marker in src, "file_claims_conflict not found — cannot mutate"
replacement = (
    "def file_claims_conflict(a: FileClaim, b: FileClaim) -> bool:\n"
    "    return False  # MUTATED: lane-collision provocation, never collides\n"
)
start = src.index(marker)
end = src.index("\n\n\n", start)
mutated = src[:start] + replacement + src[end + 3:]
assert mutated != src, "mutation did not change the file"
open(path, "w", encoding="utf-8").write(mutated)
PYEOF

mutant_control="$mutant_dir/control.py"
cat > "$mutant_control" <<'PYEOF'
import sys
sys.path.insert(0, "governance/dispatch")
sys.path.insert(0, "fleet")
import order
from model import Issue

i1 = Issue(716, "first", milestone="M", files=("Makefile",))
i2 = Issue(717, "second", milestone="M", files=("Makefile",))
plan = order.wave_plan([i1, i2])
# The mutant predicate never reports a conflict, so BOTH are admitted:
assert plan.admitted == (716, 717), plan
assert plan.refusals == (), plan.refusals
print("mutant: OK — with the predicate disabled the collision is (wrongly) admitted, proving the gate CAN fail")
PYEOF

if (cd "$mutant_dir" && python3 control.py) >"$work/mutant.out" 2>&1; then
  ok "provocation: disabling the disjointness predicate flips the negative control to admitted (the gate can fail)"
  cat "$work/mutant.out"
else
  bad "the mutant did not behave as expected (the gate may not be able to fail):"
  cat "$work/mutant.out" >&2
fi

# --- 4. the real file was never touched -------------------------------------
after_sha="$(sha256sum "$model_file" | awk '{print $1}')"
if [ "$before_sha" = "$after_sha" ]; then
  ok "$model_file is sha256-identical after the provocation ($after_sha)"
else
  bad "$model_file changed during the provocation (before=$before_sha after=$after_sha)"
fi

if [ "$fail" -eq 0 ]; then
  echo "check-lane-collision: OK"
  exit 0
fi
echo "check-lane-collision: NOT-OK ($fail problem(s))" >&2
exit 1
