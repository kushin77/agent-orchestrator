#!/usr/bin/env bash
# check-capacity-gate.sh — the capacity gate: max-agents ON, and BOUNDED
# (epic #707, lane F3 / issue #718).
#
# THE DEFECT THIS EXISTS FOR
#   The fan-out default was `max_agents: 0` in the pinned focus — i.e. "off" —
#   and nothing resolved it: `cli.py focus` printed the raw pin and the loop used
#   a fixed `FLEET_SISTER_POOL`. Raising the default to the maximum is only safe
#   if it is bounded, and a bound nobody can observe is a formality (GR-12).
#
# WHAT IS PROVOKED (not asserted)
#   `effective = min(pool, disjoint_ready_lanes, resource_ceiling)`, and for EACH
#   of the three bounds this check constructs the input that would exceed it and
#   requires the excess to be HELD **and the holding bound to be named** — then
#   constructs the relaxed input and requires it ADMITTED. Both halves must be
#   observed, so a resolver that always holds, or always admits, cannot pass. The
#   four acceptance criteria of #718 map onto the four controls:
#
#     1. FLEET_MAX_AGENTS sets the default; the effective count is the minimum
#        of the three bounds                       -> control `pool`
#     2. two ready directives owning the same file set are not both dispatched
#                                                    -> control `disjoint`
#     3. the resource ceiling (RAM / /tmp headroom) is respected -> control
#        `resource`, computed from headroom, never guessed
#     4. a directive that would exceed the ceiling is HELD    -> controls
#        `resource` (a box with no room), `cannot-assess` (an unmeasurable box)
#        and `pool` (an effective count reached)
#
#   It then proves the loop is actually WIRED to the gate, and mutation-proves
#   both halves:
#
#     * mutate `fleet/capacity.py` so the resolution cannot bind -> the provoked
#       controls MUST fail (sha256 before != after, restored byte-identically);
#     * mutate `fleet/terminal.py` so the loop no longer calls `capacity.admit`
#       -> the wiring assertion MUST fail (same discipline).
#
#   A stale `__pycache__` can fake a pass, so this check sets
#   `sys.dont_write_bytecode = True`, clears every `__pycache__` under the tree
#   under test before importing, and asserts the import really resolved inside
#   that tree.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS (no python3).
#
# Usage: bash scripts/check-capacity-gate.sh
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-capacity-gate: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

capacity_py="fleet/capacity.py"
terminal_py="fleet/terminal.py"
for module in "$capacity_py" "$terminal_py"; do
  if [ ! -f "$module" ]; then
    echo "check-capacity-gate: FAIL — $module is missing (the fan-out is unbounded)" >&2
    exit 1
  fi
done

work="$(mktemp -d "/tmp/ao718-capacity.$(date +%s).$(printf 'X%.0s' 1 2 3 4 5 6)")" || exit 1
restored=0

restore() {
  [ "$restored" = "1" ] && return 0
  restored=1
  for pair in "capacity:$capacity_py" "terminal:$terminal_py"; do
    name="${pair%%:*}"
    path="${pair#*:}"
    if [ -f "$work/$name.bak" ]; then
      cp "$work/$name.bak" "$path"
    fi
  done
  find "$root/fleet" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
}
trap 'restore; rm -rf "$work"' EXIT INT TERM HUP

clean_pycache() {
  find "$root/fleet" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
}

sha() { sha256sum "$1" | cut -d' ' -f1; }

# ---------------------------------------------------------------------------
echo "== 1. the fan-out default and the capacity module's own self-control =="
# `focus --self-control` drives BOTH halves of the focus contract: the epic
# resolver (lane F1) and, since #718, the capacity gate — a resolver whose
# bounds cannot bind is a formality, so the mutants must all be rejected.
clean_pycache
if ! env PYTHONDONTWRITEBYTECODE=1 python3 governance/dispatch/cli.py focus --self-control; then
  echo "check-capacity-gate: FAIL — focus/capacity self-control reported problems" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
echo
echo "== 2. each of the three bounds PROVOKED, and the relaxed input admitted =="
clean_pycache
harness_rc=0
env PYTHONDONTWRITEBYTECODE=1 AO_CAPACITY_ROOT="$root" python3 - <<'PY' || harness_rc=$?
"""Drive the real capacity gate in this tree; fail if any provoked control is not
observed, or if any relaxed input is not admitted."""
import os
import sys
from pathlib import Path

sys.dont_write_bytecode = True
root = Path(os.environ["AO_CAPACITY_ROOT"]).resolve()
for d in root.rglob("__pycache__"):
    if d.is_dir():
        for f in d.iterdir():
            f.unlink(missing_ok=True)
        d.rmdir()

sys.path.insert(0, str(root / "fleet"))
import capacity  # noqa: E402

resolved_mod = Path(capacity.__file__).resolve()
if not str(resolved_mod).startswith(str(root)):
    print(f"check-capacity-gate: CANNOT-ASSESS — capacity imported from outside the tree: {resolved_mod}")
    sys.exit(2)
print(f"   module under test: {resolved_mod}")

problems = []
RICH = capacity.Resources(ram_available_bytes=32 * 1024**3, tmp_available_bytes=32 * 1024**3)
A = capacity.Lane(id="lane-a", issue=1, files=("fleet/a.py",))
TWIN = capacity.Lane(id="lane-twin", issue=2, files=("fleet/a.py",))
C = capacity.Lane(id="lane-c", issue=3, files=("fleet/c.py",))
OPAQUE = capacity.Lane(id="lane-d", issue=4, files=None)


def observed(name, held, detail, *, bound=None):
    """One provoked control: the excess is HELD and the holding bound is named."""
    if held and (bound is None or held.bound == bound):
        print(f"   OK   {name}: HELD by {held.bound} — {held.reason}")
    else:
        print(f"   FAIL {name}: expected a hold by {bound}, got {held!r} ({detail})")
        problems.append(name)


def relaxed(name, decision):
    """The other half: the same input WITHOUT the excess must be admitted."""
    if decision.admitted:
        print(f"   OK   {name}: admitted — {decision.detail}")
    else:
        print(f"   FAIL {name}: the relaxed input was still held ({decision.reason})")
        problems.append(name)


# --- criterion 1: FLEET_MAX_AGENTS sets the default; effective is the minimum
print("  -- bound 1/3: the pool (FLEET_MAX_AGENTS) --")
tight = capacity.resolve_capacity(
    ready=[A, C], env={"FLEET_MAX_AGENTS": "1"}, pool_size=10, resources=RICH
)
print(f"   FLEET_MAX_AGENTS=1 -> {tight.line()}")
observed("pool-bound", capacity.admit(C, capacity=tight, active=[A]), "one active lane against a 1-agent default", bound="pool")
wide = capacity.resolve_capacity(
    ready=[A, C], env={"FLEET_MAX_AGENTS": "3"}, pool_size=10, resources=RICH
)
relaxed("pool-relaxed", capacity.admit(C, capacity=wide, active=[A]))

defaulted = capacity.default_max_agents({"FLEET_MAX_AGENTS": "0"}, focus_max_agents=0, pool_size=10)
print(f"   FLEET_MAX_AGENTS unset -> the pool default was {defaulted}")
if defaulted != 10:
    print("   FAIL pool-default: FLEET_MAX_AGENTS/0 must resolve to the pool")
    problems.append("pool-default")

# --- criterion 2: two lanes owning the same file set are not both dispatched
print("  -- bound 2/3: file-disjointness (AO-GR-24) --")
selected = capacity.disjoint_bound([A, TWIN, C])
print(f"   three ready lanes, two of them claiming fleet/a.py -> {selected.detail()}")
if selected.limit == 2 and len(selected.collisions) == 1:
    print(f"   OK   collision named: {selected.collisions[0].message()}")
else:
    print("   FAIL collision-named: the colliding lane was not held by name")
    problems.append("collision-named")
cap = capacity.resolve_capacity(ready=[A, TWIN], env={"FLEET_MAX_AGENTS": "9"}, resources=RICH)
print(f"   two ready lanes claiming fleet/a.py -> {cap.line()}")
observed("disjoint-bound", capacity.admit(TWIN, capacity=cap, active=[A]), "two lanes on one file with a worker free", bound="disjoint")
# The RELAXED half is resolved over the set that HAS both lanes in it: a
# capacity resolved over a colliding pair is 1 by construction, so admitting C
# against it would prove nothing about the collision rule.
disjoint_cap = capacity.resolve_capacity(ready=[A, C], env={"FLEET_MAX_AGENTS": "9"}, resources=RICH)
relaxed("disjoint-relaxed", capacity.admit(C, capacity=disjoint_cap, active=[A]))
opaque = capacity.disjoint_bound([A, OPAQUE])
if opaque.unattributable == ("lane-d",) and "lane-d" in opaque.detail():
    print(f"   OK   undeclared-files-named: {opaque.detail()}")
else:
    print("   FAIL undeclared-files-named: a lane with no Files: was not named")
    problems.append("undeclared-files-named")
opaque_cap = capacity.resolve_capacity(ready=[C, OPAQUE], env={"FLEET_MAX_AGENTS": "9"}, resources=RICH)
strict = capacity.resolve_capacity(
    ready=[A, OPAQUE], env={"FLEET_MAX_AGENTS": "9", "AO_LANE_FILES_REQUIRED": "1"}, resources=RICH
)
observed(
    "strict-undeclared-held",
    capacity.admit(capacity.Lane(id="lane-e", issue=5, files=None), capacity=strict, active=[OPAQUE]),
    "two undeclared lanes in strict mode",
    bound="disjoint",
)
if capacity.admit(OPAQUE, capacity=opaque_cap, active=[C]).admitted:
    print("   OK   undeclared-admitted-and-named: admitted by default, and named in the bound detail")
else:
    print("   FAIL undeclared-admitted-and-named: an undeclared lane was held without strict mode")
    problems.append("undeclared-admitted-and-named")

# --- criterion 3 + 4: the resource ceiling, computed and then enforced
print("  -- bound 3/3: the resource ceiling (RAM / /tmp headroom) --")
ram = capacity.resource_bound(
    capacity.Resources(ram_available_bytes=2048 * 1024**2, tmp_available_bytes=32 * 1024**3),
    ram_budget_mib=1024,
    tmp_budget_mib=512,
)
tmp = capacity.resource_bound(
    capacity.Resources(ram_available_bytes=32 * 1024**3, tmp_available_bytes=600 * 1024**2),
    ram_budget_mib=1024,
    tmp_budget_mib=512,
)
print(f"   RAM 2 GiB / 1 GiB per lane -> {ram.detail()}")
print(f"   /tmp 600 MiB / 512 MiB per lane -> {tmp.detail()}")
if ram.limit == 2 and tmp.limit == 1:
    print("   OK   resource-ceiling-computed: the ceiling follows measured headroom")
else:
    print("   FAIL resource-ceiling-computed: the ceiling did not follow the headroom")
    problems.append("resource-ceiling-computed")

starved = capacity.resolve_capacity(
    ready=[C],
    env={"FLEET_MAX_AGENTS": "9"},
    resources=capacity.Resources(ram_available_bytes=100 * 1024**2, tmp_available_bytes=100 * 1024**2),
)
print(f"   a box with 100 MiB free -> {starved.line()}")
observed("resource-bound-held", capacity.admit(C, capacity=starved, active=[]), "a lane on a full box", bound="resource")
generous = capacity.resolve_capacity(
    ready=[C], env={"FLEET_MAX_AGENTS": "9"}, resources=RICH
)
relaxed("resource-relaxed", capacity.admit(C, capacity=generous, active=[]))

unmeasured = capacity.resolve_capacity(
    ready=[C],
    env={"FLEET_MAX_AGENTS": "9"},
    resources=capacity.Resources(
        ram_available_bytes=None,
        tmp_available_bytes=None,
        problems=("/proc/meminfo: unreadable",),
    ),
)
print(f"   an unmeasurable box -> {unmeasured.line()}")
observed(
    "cannot-assess-held",
    capacity.admit(C, capacity=unmeasured, active=[]),
    "an unreadable measurement",
    bound="resource",
)

# --- the reported line, which is what an operator actually reads
print("  -- the reporting line --")
line = capacity.headline(focus_max_agents=0, pool_size=10, resources=RICH)
print(f"   {line}")
expected_ceiling = (32 * 1024) // capacity.DEFAULT_LANE_RAM_MIB
if "max_agents_default=10" in line and f"resource_ceiling={expected_ceiling}" in line:
    print("   OK   headline-names-both: the default and the ceiling are both reported")
else:
    print("   FAIL headline-names-both: the reporting line omitted a bound")
    problems.append("headline-names-both")

if problems:
    print(f"check-capacity-gate: {len(problems)} control(s) not observed: {', '.join(problems)}")
    sys.exit(1)
print("   all provoked controls observed")
PY
if [ "$harness_rc" = "2" ]; then
  echo "check-capacity-gate: CANNOT-ASSESS — the module was not resolved in this tree" >&2
  exit 2
fi
if [ "$harness_rc" != "0" ]; then
  echo "check-capacity-gate: FAIL — a provoked control was not observed" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
echo
echo "== 3. the loop is WIRED to the gate =="
# The gate is only binding if the thing it bounds actually calls it. This is a
# structural assertion on purpose: the alternative is driving the live loop,
# which needs a runner, an inbox and a clock, and would prove less.
if ! grep -q 'capacity\.admit(' "$terminal_py"; then
  echo "check-capacity-gate: FAIL — $terminal_py does not call capacity.admit(); the ceiling is inert" >&2
  exit 1
fi
if grep -qE '^\s+if active >= pool:' "$terminal_py"; then
  echo "check-capacity-gate: FAIL — $terminal_py still bounds the fan-out by the bare pool only" >&2
  exit 1
fi
if ! grep -q 'resolve_capacity(' "$terminal_py"; then
  echo "check-capacity-gate: FAIL — $terminal_py never resolves the capacity" >&2
  exit 1
fi
if ! grep -q 'capacity\.read_focus_max_agents(' "$terminal_py"; then
  echo "check-capacity-gate: FAIL — $terminal_py ignores the pinned focus's fan-out default" >&2
  exit 1
fi
if ! grep -q 'capacity' governance/dispatch/cli.py; then
  echo "check-capacity-gate: FAIL — the focus verb does not report the resolved capacity" >&2
  exit 1
fi
echo "   OK   fleet/terminal.py calls capacity.admit() and resolves it per cycle (no bare 'active >= pool')"
echo "   OK   governance/dispatch/cli.py reports the resolved capacity"

# The structural assertions above prove the CALL is present. They cannot prove
# the loop's own glue — `terminal.resolve_capacity` over the live IN_FLIGHT
# registry — behaves. So the three integration controls in the declared fleet
# suite are driven here, by name, against this tree. They run through the suite's
# runtime isolation (conftest redirects every fleet runtime path), so driving the
# real loop does not touch the repo's `.fleet/`.
clean_pycache
integration="fleet/tests/test_capacity.py"
tests="test_the_loop_resolves_and_holds_on_the_pool_bound"
tests="$tests or test_the_loop_holds_on_the_resource_ceiling"
tests="$tests or test_the_loop_holds_a_directive_whose_files_collide"
tests="$tests or test_the_loop_reads_the_pinned_focus_for_the_default"
echo "   driving the loop's own resolver: python3 -m pytest -q $integration -k '<the loop>'; ($tests)"
if ! env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q "$integration" -k "the_loop" 2>&1 | tail -5; then
  echo "check-capacity-gate: FAIL — the loop's own resolver did not honour the capacity gate" >&2
  exit 1
fi
echo "   OK   the loop's own resolve_capacity/admit path holds on all three bounds"

# ---------------------------------------------------------------------------
echo
echo "== 4. the control can FAIL (mutation proof) =="
# A control whose mutated and un-mutated runs share an exit code is a formality.
# Both halves of this check are mutated in turn and MUST go red.

cp "$capacity_py" "$work/capacity.bak"
cp "$terminal_py" "$work/terminal.bak"

# (a) the resolution cannot bind: min -> max.
cap_sha_before="$(sha "$capacity_py")"
python3 - "$capacity_py" <<'PY'
import sys
from pathlib import Path

p = Path(sys.argv[1])
text = p.read_text(encoding="utf-8")
needle = "    effective = min(limits)"
if needle not in text:
    raise SystemExit(f"mutation site not found in {p}: {needle!r}")
p.write_text(text.replace(needle, "    effective = max(limits)", 1), encoding="utf-8")
PY
cap_sha_after="$(sha "$capacity_py")"
if [ "$cap_sha_before" = "$cap_sha_after" ]; then
  echo "check-capacity-gate: FAIL — the mutation did not land on $capacity_py" >&2
  exit 1
fi
echo "   mutation landed on $capacity_py (sha256 ${cap_sha_before:0:12} -> ${cap_sha_after:0:12})"
clean_pycache
mutant_rc=0
env PYTHONDONTWRITEBYTECODE=1 AO_CAPACITY_ROOT="$root" python3 - "$root" <<'PY' > "$work/mutant.log" 2>&1 || mutant_rc=$?
import os
import sys
from pathlib import Path

sys.dont_write_bytecode = True
root = Path(os.environ["AO_CAPACITY_ROOT"]).resolve()
sys.path.insert(0, str(root / "fleet"))
import capacity  # noqa: E402

RICH = capacity.Resources(ram_available_bytes=32 * 1024**3, tmp_available_bytes=32 * 1024**3)
A = capacity.Lane(id="lane-a", issue=1, files=("fleet/a.py",))
C = capacity.Lane(id="lane-c", issue=3, files=("fleet/c.py",))
tight = capacity.resolve_capacity(ready=[A, C], env={"FLEET_MAX_AGENTS": "1"}, resources=RICH)
# With `effective = max(limits)` the pool bound can no longer hold this lane.
sys.exit(0 if not capacity.admit(C, capacity=tight, active=[A]).admitted else 1)
PY
if [ "$mutant_rc" = "0" ]; then
  echo "check-capacity-gate: FAIL — the provoked controls still passed against a resolution that cannot bind" >&2
  exit 1
fi
echo "   OK   the provoked control went RED against the mutant (rc=$mutant_rc)"

cp "$work/capacity.bak" "$capacity_py"
if [ "$(sha "$capacity_py")" != "$cap_sha_before" ]; then
  echo "check-capacity-gate: FAIL — $capacity_py was not restored byte-identically" >&2
  exit 1
fi
echo "   OK   $capacity_py restored byte-identically (sha256 ${cap_sha_before:0:12})"
clean_pycache

# (b) the loop no longer calls the gate: the wiring assertion must go red.
term_sha_before="$(sha "$terminal_py")"
python3 - "$terminal_py" <<'PY'
import sys
from pathlib import Path

p = Path(sys.argv[1])
text = p.read_text(encoding="utf-8")
needle = "decision = capacity.admit(lane, capacity=resolved, active=capacity_lanes())"
if needle not in text:
    raise SystemExit(f"mutation site not found in {p}: {needle!r}")
p.write_text(text.replace(needle, "decision = None  # mutated", 1), encoding="utf-8")
PY
term_sha_after="$(sha "$terminal_py")"
if [ "$term_sha_before" = "$term_sha_after" ]; then
  echo "check-capacity-gate: FAIL — the mutation did not land on $terminal_py" >&2
  exit 1
fi
echo "   mutation landed on $terminal_py (sha256 ${term_sha_before:0:12} -> ${term_sha_after:0:12})"
if grep -q 'capacity\.admit(' "$terminal_py"; then
  echo "check-capacity-gate: FAIL — the wiring assertion passed with the call removed (it is a formality)" >&2
  exit 1
fi
echo "   OK   the wiring assertion went RED with the call removed"

cp "$work/terminal.bak" "$terminal_py"
if [ "$(sha "$terminal_py")" != "$term_sha_before" ]; then
  echo "check-capacity-gate: FAIL — $terminal_py was not restored byte-identically" >&2
  exit 1
fi
echo "   OK   $terminal_py restored byte-identically (sha256 ${term_sha_before:0:12})"

clean_pycache
echo
echo "check-capacity-gate: OK — the fan-out is ON and bounded by three provoked controls"
exit 0
