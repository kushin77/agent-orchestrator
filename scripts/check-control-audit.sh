#!/usr/bin/env bash
# check-control-audit.sh — one effect, one record, and a refusal that says so
# (issue #555, RC-4 of EPIC #551).
#
# RC-4's three acceptances, each provoked rather than merely asserted:
#
#   1. a REPLAYED command id yields exactly ONE ledger record and hands the
#      caller the ORIGINAL receipt, without reaching the lever a second time;
#   2. an UNREACHABLE lever returns 503 and writes NO record;
#   3. a STOLEN or REORDERED command id is refused, and the thief is not handed
#      the receipt.
#
# The suite proves (1)-(3) through the real route. This gate then MUTATES a
# scratch copy to break each acceptance in turn and REQUIRES the named test to
# go red — so a pass here cannot be vacuous (GR-12: a check that cannot fail is
# a formality). The repository's own files are never modified: the controls act
# on a copy, and each control is reset before the next one.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS (no python3, no pytest).
# CANNOT-ASSESS must never read as a pass.
#
# Usage: bash scripts/check-control-audit.sh
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-control-audit: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

module="portal/server/control_audit.py"
suite="portal/tests/test_control_audit.py"
wiring="portal/server/control_api.py"
for required in "$module" "$suite" "$wiring" control-plane/control/verbs.yaml; do
  if [ ! -f "$required" ]; then
    echo "check-control-audit: CANNOT-ASSESS — $required is missing" >&2
    exit 2
  fi
done

if ! python3 -c 'import pytest' >/dev/null 2>&1; then
  echo "check-control-audit: CANNOT-ASSESS — pytest is not importable" >&2
  exit 2
fi

# The three acceptances, by the test that owns each. Named here so that deleting
# a case is a FAILURE rather than a quieter pass: a suite that keeps passing
# because its cases are gone is the failure mode this whole gate exists against.
accept_1="test_a_replay_writes_one_record_and_hands_back_the_original_receipt"
accept_2="test_an_unreachable_lever_returns_503_and_writes_no_record"
accept_3="test_a_stolen_command_id_is_refused_without_the_receipt"
accept_3b="test_a_reordered_command_id_is_refused"

# --- the real check ---------------------------------------------------------
echo "== the audit suite (the three acceptances, through the real route) =="
for case in "$accept_1" "$accept_2" "$accept_3" "$accept_3b"; do
  if ! grep -qE "^def ${case}\(" "$suite"; then
    echo "check-control-audit: FAIL — $suite no longer declares $case" >&2
    exit 1
  fi
done
echo "  OK    all four acceptance cases are declared in $suite"

if ! PYTHONDONTWRITEBYTECODE=1 python3 -m pytest "$suite" -q -p no:cacheprovider; then
  echo "check-control-audit: FAIL — the audit suite is red" >&2
  exit 1
fi

# --- the negative controls --------------------------------------------------
# Each breaks one acceptance in a scratch copy of the repository and requires
# the named test to go red. The scratch directory is built from an explicit
# timestamp template: the docs-lint scanner reads a run of three capital X's as
# an unfinished marker, so the usual mktemp placeholder form is not usable here.
if ! command -v sha256sum >/dev/null 2>&1; then
  echo "check-control-audit: CANNOT-ASSESS — sha256sum not found (the controls verify their own restoration)" >&2
  exit 2
fi

work="${TMPDIR:-/tmp}/control-audit.$(date +%s%N)"
repo="$work/repo"
if ! mkdir -p "$repo" 2>/dev/null; then
  echo "check-control-audit: CANNOT-ASSESS — cannot create a scratch directory" >&2
  exit 2
fi
trap 'rm -rf "$work"' EXIT

# Only the trees the suite imports are copied. `fleet/` is carried because one
# case compares this module's stream shape against the fleet's own writer in a
# subprocess; `control-plane/` carries the verb registry the surface reads.
for tree in portal telemetry identity control-plane fleet; do
  if ! cp -a "$tree" "$repo/$tree" 2>/dev/null; then
    echo "check-control-audit: CANNOT-ASSESS — cannot copy $tree into the scratch tree" >&2
    exit 2
  fi
done

fail=0
ran=0

snapshot() { sha256sum "$1" | cut -d' ' -f1; }

# reset_copy <relative-path>: restore one file in the scratch tree from the
# pristine working tree, so each control starts from the real source.
reset_copy() {
  cp "$1" "$repo/$1" || return 1
}

# check_red <label> <relative-path> <sha-before> <pytest -k needle>
check_red() {
  local label="$1" rel="$2" before="$3" needle="$4"
  ran=$((ran + 1))
  local target="$repo/$rel"
  local after
  after="$(snapshot "$target")"
  if [ "$before" = "$after" ]; then
    echo "  FAIL  $label: the mutation changed nothing — the control would prove nothing" >&2
    fail=1
    return
  fi

  local out rc
  rm -rf "$repo/portal/tests/__pycache__" "$repo/portal/server/__pycache__"
  out="$(cd "$repo" && PYTHONDONTWRITEBYTECODE=1 \
        python3 -m pytest "portal/tests/test_control_audit.py" -k "$needle" \
        -q -p no:cacheprovider 2>&1)"
  rc=$?

  if [ "$rc" -ne 0 ] && printf '%s' "$out" | grep -qF "$needle"; then
    echo "  OK    $label (red, naming $needle)"
  else
    echo "  FAIL  $label: expected a red run naming '$needle', got rc=$rc" >&2
    printf '%s\n' "$out" | tail -6 | sed 's/^/          /' >&2
    fail=1
  fi
}

echo "== negative controls (each acceptance broken in a scratch copy) =="

# -- control 1: a replay is no longer refused, so it reaches the lever twice --
reset_copy "$module" || { echo "check-control-audit: CANNOT-ASSESS — cannot reset $module" >&2; exit 2; }
before="$(snapshot "$repo/$module")"
python3 - "$repo/$module" <<'PY'
import io, sys

path = sys.argv[1]
src = io.open(path, encoding="utf-8").read()
old = (
    "            recorded = self._applied.get(command.id)\n"
    "            if recorded is not None:\n"
    "                return self._refusal(command, recorded)\n"
)
new = "            recorded = None\n"
assert old in src, "the already-applied branch in begin() was not found"
io.open(path, "w", encoding="utf-8").write(src.replace(old, new, 1))
PY
if [ "$?" -ne 0 ]; then
  echo "  FAIL  a replay is refused: the mutator could not apply (the source moved)" >&2
  fail=1
else
  check_red "a replay is refused and never re-applied" "$module" "$before" "$accept_1"
fi

# -- control 2: an unreachable lever writes a record it did not earn ----------
reset_copy "$wiring" || { echo "check-control-audit: CANNOT-ASSESS — cannot reset $wiring" >&2; exit 2; }
before="$(snapshot "$repo/$wiring")"
python3 - "$repo/$wiring" <<'PY'
import io, sys

path = sys.argv[1]
src = io.open(path, encoding="utf-8").read()
old = (
    "        except LeverUnreachable as exc:\n"
    '            raise ApiError(503, "lever_unreachable", str(exc)) from exc\n'
)
new = (
    "        except LeverUnreachable as exc:\n"
    "            self.commands.finish(\n"
    "                command,\n"
    "                self._effect(\n"
    "                    command,\n"
    '                    LeverResult(argv=(), exit_code=2, stdout="", stderr=str(exc)),\n'
    "                ),\n"
    "            )\n"
    '            raise ApiError(503, "lever_unreachable", str(exc)) from exc\n'
)
assert old in src, "the unreachable-lever branch in _deliver() was not found"
io.open(path, "w", encoding="utf-8").write(src.replace(old, new, 1))
PY
if [ "$?" -ne 0 ]; then
  echo "  FAIL  an unreachable lever writes no record: the mutator could not apply" >&2
  fail=1
else
  check_red "an unreachable lever writes no record" "$wiring" "$before" "$accept_2"
fi

# -- control 3: every spent id is answered as a replay, receipt included ------
reset_copy "$module" || { echo "check-control-audit: CANNOT-ASSESS — cannot reset $module" >&2; exit 2; }
before="$(snapshot "$repo/$module")"
python3 - "$repo/$module" <<'PY'
import io, sys

path = sys.argv[1]
src = io.open(path, encoding="utf-8").read()
old = (
    "        if recorded.binding and recorded.binding == command_binding(command):\n"
)
new = "        if True:\n"
assert old in src, "the replay branch in _refusal() was not found"
io.open(path, "w", encoding="utf-8").write(src.replace(old, new, 1))
PY
if [ "$?" -ne 0 ]; then
  echo "  FAIL  a stolen id is refused without the receipt: the mutator could not apply" >&2
  fail=1
else
  check_red "a stolen id is refused without the receipt" "$module" "$before" "$accept_3"
  # The same defect answers a reordered id as a replay too, so the second case
  # of acceptance 3 is required to go red as well — both halves, one mutation.
  reset_copy "$module" || { echo "check-control-audit: CANNOT-ASSESS — cannot reset $module" >&2; exit 2; }
  before="$(snapshot "$repo/$module")"
  python3 - "$repo/$module" <<'PY'
import io, sys

path = sys.argv[1]
src = io.open(path, encoding="utf-8").read()
old = (
    "        if recorded.binding and recorded.binding == command_binding(command):\n"
)
new = "        if True:\n"
assert old in src, "the replay branch in _refusal() was not found"
io.open(path, "w", encoding="utf-8").write(src.replace(old, new, 1))
PY
  check_red "a reordered id is refused without the receipt" "$module" "$before" "$accept_3b"
fi

# -- the controls must have all run -----------------------------------------
if [ "$ran" -eq 0 ]; then
  echo "check-control-audit: CANNOT-ASSESS — no control ran, so nothing was proven" >&2
  exit 2
fi

if [ "$fail" -ne 0 ]; then
  echo "check-control-audit: FAIL — $ran control(s) run, at least one did not go red" >&2
  exit 1
fi

echo "check-control-audit: OK — the three acceptances hold, and all $ran negative controls go red"
exit 0
