#!/usr/bin/env bash
# check-rollout-projection.sh — the projection gate (issue #967).
#
# WHAT IT GATES. The ordered go-live ladder records a promotion in
# `infra/rollout/live-state.yaml`; the console decides whether a surface answers
# from `infra/feature-flags/registry.yaml`'s `surfaces.<name>.default` (read by
# `portal/server/fleet.py`, with the runtime rollback overlay on top). Nothing in
# the repo turned the first into the second, so a completed, owner-approved
# go-live could leave the surface dark until a human remembered to edit the
# declaration by hand — the last manual step between a promotion and a served
# surface. `infra/rollout/projection.py` is the declared coupling; this gate is
# what makes a promotion that has not been projected **detectable** rather than
# assumed, and it is why the manual step is now a named, gated one.
#
# WHAT IT PROVES, with the ACTUAL line printed beside every expectation:
#
#   1. the real tree is projected — every promotion the live state records is
#      reflected in the declaration the console reads;
#   2. NEGATIVE CONTROL: a genuine, sandboxed promotion the declaration does NOT
#      reflect IS reported, BY NAME, by `projection --check` (rc 1);
#   3. POSITIVE CONTROL: after `projection --write` the same sandbox is clean AND
#      the portal's own reader answers "on" from the projected declaration while
#      the shipped one answers "off" — i.e. the served surface reflects the
#      promotion with no hand edit, proven through the reader the console uses;
#   4. the projection is SURGICAL: exactly the two declaration lines change, the
#      file keeps its line count and its comments, a second run is byte-identical
#      (idempotent), and a no-op project does not rewrite the file at all;
#   5. a promotion that owes no served surface is reported BY NAME with its reason
#      (never skipped in silence), and
#   6. CONTROL: `--write` REFUSES (rc 2, CANNOT-ASSESS, nothing written) when the
#      declaration line it would project into cannot be located — the fail-closed
#      half, because a projector that guesses is worse than a manual step;
#   7. the committed declarations are byte-identical before and after this gate
#      (sha256) — everything above ran in a scratch sandbox, so there is nothing
#      to restore.
#
# Exit codes: 0 clean, 1 a finding, 2 CANNOT-ASSESS (the gate could not run or a
# control could not be driven — never reported as a pass).
#
# Usage: bash scripts/check-rollout-projection.sh

set -u

GATE="rollout-projection"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 2

PY="${PYTHON:-python3}"
REGISTRY_REL="infra/feature-flags/registry.yaml"
LIVE_STATE_REL="infra/rollout/live-state.yaml"
REGISTRY="$ROOT/$REGISTRY_REL"
LIVE_STATE="$ROOT/$LIVE_STATE_REL"

# The promotion the controls drive: a flag the ladder declares, whose served
# surface the registry declares OFF, so the projection has something to move.
CONTROL_FLAG="services.org_chart"
CONTROL_SURFACE="org_chart"
# A flag with no served-surface partner, for the "reported, not skipped" control.
EXEMPT_FLAG="ci_cd.verify_trigger"

scratch="/tmp/ao967-projection.$$.$(date +%s%N)"
failures=0

note() { printf '%s\n' "$*"; }
ok() { printf '  ok    %s\n' "$*"; }
fail() {
  printf '  FAIL  %s\n' "$*"
  failures=$((failures + 1))
}
cannot_assess() {
  printf ' %s: CANNOT-ASSESS -- %s\n' "$GATE" "$*" >&2
  printf ' %s: CANNOT-ASSESS -- %s\n' "$GATE" "$*"
  exit 2
}

# expect_rc <label> <expected> <actual> — prints EXPECTED beside ACTUAL always.
expect_rc() {
  local label="$1" expected="$2" actual="$3"
  printf '  EXPECT %s: rc=%s\n' "$label" "$expected"
  printf '  ACTUAL %s: rc=%s\n' "$label" "$actual"
  if [ "$actual" = "$expected" ]; then
    ok "$label"
  else
    fail "$label (expected rc=$expected, got rc=$actual)"
  fi
}

# expect_line <label> <needle> <file> — prints EXPECTED beside the ACTUAL line.
expect_line() {
  local label="$1" needle="$2" file="$3"
  printf '  EXPECT %s: a line containing: %s\n' "$label" "$needle"
  local actual
  actual="$(grep -F -- "$needle" "$file" | head -1)"
  if [ -n "$actual" ]; then
    printf '  ACTUAL %s: %s\n' "$label" "$actual"
    ok "$label"
  else
    printf '  ACTUAL %s: (no line matched; showing what the run printed)\n' "$label"
    sed -n '1,12p' "$file" | sed 's/^/    /'
    fail "$label (no line containing '$needle')"
  fi
}

cleanup() {
  if [ -n "${scratch:-}" ] && [ -d "${scratch:-}" ]; then
    rm -rf "$scratch"
  fi
}
trap cleanup EXIT

mkdir -p "$scratch" || cannot_assess "cannot create a scratch sandbox at $scratch"

note "== $GATE: the projection from a promotion to the declaration the console reads =="
note "  scratch sandbox: $scratch"

# --------------------------------------------------------------------------- #
# 0. the committed declarations, before anything runs
# --------------------------------------------------------------------------- #
state_before="$(sha256sum "$REGISTRY" "$LIVE_STATE")"

# --------------------------------------------------------------------------- #
# 1. the real tree is projected
# --------------------------------------------------------------------------- #
note "== 1. the committed tree =="
real_log="$scratch/real.log"
env PYTHONDONTWRITEBYTECODE=1 "$PY" -m infra.rollout.projection --check >"$real_log" 2>&1
real_rc=$?
real_summary="$(sed -n '1p' "$real_log")"
printf '  ACTUAL real-tree summary: %s\n' "$real_summary"
expect_line "real tree reports its promotion count" "promotion(s) recorded" "$real_log"
expect_line "real tree is clean (nothing unprojected)" "projection: OK" "$real_log"
expect_line "real tree records 0 unprojected promotions" "0 unprojected" "$real_log"
expect_rc "real tree --check" 0 "$real_rc"

# --------------------------------------------------------------------------- #
# 2. a genuine promotion, driven into the sandbox by the real CLI
# --------------------------------------------------------------------------- #
note "== 2. a sandboxed promotion (the real ladder CLI, nothing of the repo touched) =="
sandbox="$scratch/repo"
mkdir -p "$sandbox/infra"
cp -a "$ROOT/infra/rollout" "$sandbox/infra/rollout"
cp -a "$ROOT/infra/feature-flags" "$sandbox/infra/feature-flags"

sandbox_registry="$sandbox/$REGISTRY_REL"
sandbox_live_state="$sandbox/$LIVE_STATE_REL"

env PYTHONDONTWRITEBYTECODE=1 "$PY" -m infra.rollout.cli grant-approval "$CONTROL_FLAG" \
  --to canary --approver auditor-sme --approval-id "approval-$CONTROL_SURFACE-canary" \
  --approvals-dir "$sandbox/infra/rollout/approvals" >"$scratch/grant.log" 2>&1
grant_rc=$?
if [ "$grant_rc" -ne 0 ]; then
  cannot_assess "the ladder refused to record an approval for $CONTROL_FLAG (rc=$grant_rc); see $scratch/grant.log"
fi

env PYTHONDONTWRITEBYTECODE=1 "$PY" -m infra.rollout.cli promote "$CONTROL_FLAG" \
  --root "$sandbox" --to canary --actor deployer-sa --approval "approval-$CONTROL_SURFACE-canary" \
  --approvals-dir "$sandbox/infra/rollout/approvals" \
  --audit-log "$sandbox/infra/rollout/promotion-audit.jsonl" \
  --live-state-out "$sandbox_live_state" >"$scratch/promote.log" 2>&1
promote_rc=$?
if [ "$promote_rc" -ne 0 ]; then
  cannot_assess "the ladder refused to promote $CONTROL_FLAG in the sandbox (rc=$promote_rc); see $scratch/promote.log"
fi
printf '  ACTUAL promote: %s\n' "$(sed -n '1p' "$scratch/promote.log")"
expect_line "the sandbox live state records the promoted stage" "stage: canary" "$sandbox_live_state"

# --------------------------------------------------------------------------- #
# 3. NEGATIVE CONTROL — the check must report the promotion as unprojected
# --------------------------------------------------------------------------- #
note "== 3. NEGATIVE CONTROL: an unprojected promotion is reported BY NAME =="
unprojected_log="$scratch/unprojected.log"
env PYTHONDONTWRITEBYTECODE=1 "$PY" -m infra.rollout.projection --check --root "$sandbox" \
  >"$unprojected_log" 2>&1
unprojected_rc=$?
expect_rc "the check fires on an unprojected promotion" 1 "$unprojected_rc"
expect_line "the finding names the flag AND the declaration behind" \
  "UNPROJECTED $CONTROL_FLAG -> declaration surfaces.$CONTROL_SURFACE.default is 'off' while live-state records stage 'canary'" \
  "$unprojected_log"
expect_line "the run reports the plan as failed, not clean" "projection: FAILED" "$unprojected_log"

# --------------------------------------------------------------------------- #
# 4. POSITIVE CONTROL — the projection, and the served surface it turns on
# --------------------------------------------------------------------------- #
note "== 4. POSITIVE CONTROL: the projection closes it, and the console's reader agrees =="
write_log="$scratch/write.log"
env PYTHONDONTWRITEBYTECODE=1 "$PY" -m infra.rollout.projection --write --root "$sandbox" \
  >"$write_log" 2>&1
write_rc=$?
expect_rc "the projection applies" 0 "$write_rc"
expect_line "the projection names the declaration line it moved" \
  "PROJECTED surfaces.$CONTROL_SURFACE.default: 'off' -> 'on'" "$write_log"
expect_line "the projection records the reviewed-decision flag too" \
  "PROJECTED surfaces.$CONTROL_SURFACE.promoted: 'false' -> 'true'" "$write_log"

projected_log="$scratch/projected.log"
env PYTHONDONTWRITEBYTECODE=1 "$PY" -m infra.rollout.projection --check --root "$sandbox" \
  >"$projected_log" 2>&1
projected_rc=$?
expect_rc "the check is clean after the projection" 0 "$projected_rc"
expect_line "the projected sandbox is clean" "projection: OK" "$projected_log"

# The console's OWN reader, over the projected declaration and over the shipped
# one: the answer must come from the projection, not from ambient state.
reader_log="$scratch/reader.log"
env PYTHONDONTWRITEBYTECODE=1 "$PY" - "$ROOT" "$sandbox" "$REGISTRY" "$CONTROL_SURFACE" >"$reader_log" 2>&1 <<'PY'
import sys

repo, sandbox, shipped, surface = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
sys.path.insert(0, repo)
from portal.server import fleet

projected = fleet.read_surface_default(
    sandbox, registry_path=sandbox + "/infra/feature-flags/registry.yaml", surface=surface
)
shipped_default = fleet.read_surface_default(sandbox, registry_path=shipped, surface=surface)
print("portal.server.fleet.read_surface_default(projected registry) = " + projected)
print("portal.server.fleet.read_surface_default(shipped registry) = " + shipped_default)
if projected != "on" or shipped_default != "off":
    raise SystemExit(1)
PY
reader_rc=$?
expect_rc "the console's own reader turns the surface on (projected) and off (shipped)" 0 "$reader_rc"
expect_line "the served surface reflects the promotion with no hand edit" \
  "read_surface_default(projected registry) = on" "$reader_log"
expect_line "the shipped declaration is still off (attribution)" \
  "read_surface_default(shipped registry) = off" "$reader_log"

# The write is surgical: two declaration lines replaced in place, nothing else.
shipped_lines="$(wc -l <"$REGISTRY")"
projected_lines="$(wc -l <"$sandbox_registry")"
diff_log="$scratch/projection.diff"
diff -u "$REGISTRY" "$sandbox_registry" >"$diff_log" 2>&1
removed_lines="$(grep -cE '^-[^-]' "$diff_log")"
added_lines="$(grep -cE '^\+[^+]' "$diff_log")"
printf '  ACTUAL line count shipped=%s projected=%s; lines removed=%s added=%s\n' \
  "$shipped_lines" "$projected_lines" "$removed_lines" "$added_lines"
printf '  ACTUAL the lines it moved:\n'
grep -E '^[+-][^+-]' "$diff_log" | sed 's/^/    /'
expect_rc "the projection replaces exactly 2 declaration lines (2 removed)" "2" "$removed_lines"
expect_rc "the projection replaces exactly 2 declaration lines (2 added)" "2" "$added_lines"
expect_line "the first moved line is the served declaration" "+    default: on" "$diff_log"
expect_line "the second moved line is the reviewed-decision flag" "+    promoted: true" "$diff_log"
expect_rc "the projection adds no line and removes none" "$shipped_lines" "$projected_lines"
if grep -qF -- 'code-enforced rather than advisory.' "$sandbox_registry"; then
  printf '  ACTUAL the comment above the projected entry: present\n'
  ok "the projection preserves the comments it was not asked to touch"
else
  printf '  ACTUAL the comment above the projected entry: MISSING\n'
  fail "the projection dropped a comment (a re-emitted document would)"
fi

# Idempotence: a second write must be a byte-identical no-op.
idem_before="$(sha256sum "$sandbox_registry")"
env PYTHONDONTWRITEBYTECODE=1 "$PY" -m infra.rollout.projection --write --root "$sandbox" \
  >"$scratch/write2.log" 2>&1
idem_rc=$?
idem_after="$(sha256sum "$sandbox_registry")"
printf '  ACTUAL second write rc=%s, sha256 unchanged=%s\n' \
  "$idem_rc" "$([ "$idem_before" = "$idem_after" ] && printf yes || printf no)"
if [ "$idem_before" = "$idem_after" ] && [ "$idem_rc" -eq 0 ]; then
  ok "the projection is idempotent (second run byte-identical)"
else
  fail "the projection is not idempotent (rc=$idem_rc)"
fi
expect_line "a no-op project says so rather than rewriting the file" \
  "already projected - 0 declaration line(s) to write" "$scratch/write2.log"

# --------------------------------------------------------------------------- #
# 5. a promotion that owes no served surface is reported BY NAME
# --------------------------------------------------------------------------- #
note "== 5. a promotion with no served-surface partner is named, not skipped =="
env PYTHONDONTWRITEBYTECODE=1 "$PY" -m infra.rollout.cli grant-approval "$EXEMPT_FLAG" \
  --to canary --approver auditor-sme --approval-id "approval-exempt-canary" \
  --approvals-dir "$sandbox/infra/rollout/approvals" >"$scratch/grant2.log" 2>&1
env PYTHONDONTWRITEBYTECODE=1 "$PY" -m infra.rollout.cli promote "$EXEMPT_FLAG" \
  --root "$sandbox" --to canary --actor deployer-sa --approval "approval-exempt-canary" \
  --approvals-dir "$sandbox/infra/rollout/approvals" \
  --audit-log "$sandbox/infra/rollout/promotion-audit.jsonl" \
  --live-state-out "$sandbox_live_state" >"$scratch/promote2.log" 2>&1
exempt_promote_rc=$?
if [ "$exempt_promote_rc" -ne 0 ]; then
  cannot_assess "the ladder refused to promote $EXEMPT_FLAG in the sandbox; see $scratch/promote2.log"
fi
exempt_log="$scratch/exempt.log"
env PYTHONDONTWRITEBYTECODE=1 "$PY" -m infra.rollout.projection --check --root "$sandbox" \
  >"$exempt_log" 2>&1
exempt_rc=$?
expect_rc "an exempt promotion is not a finding" 0 "$exempt_rc"
expect_line "the exempt promotion is named with its reason" "EXEMPT $EXEMPT_FLAG (" "$exempt_log"

# --------------------------------------------------------------------------- #
# 6. CONTROL — the projector refuses when it cannot locate the declaration line
# --------------------------------------------------------------------------- #
note "== 6. CONTROL: --write refuses (CANNOT-ASSESS) when the line cannot be located =="
mutant="$scratch/mutant"
mkdir -p "$mutant/infra"
cp -a "$sandbox/infra/rollout" "$mutant/infra/rollout"
cp -a "$ROOT/infra/feature-flags" "$mutant/infra/feature-flags"
cp -a "$sandbox_live_state" "$mutant/$LIVE_STATE_REL"

env PYTHONDONTWRITEBYTECODE=1 "$PY" - "$mutant/$REGISTRY_REL" "$CONTROL_SURFACE" >"$scratch/mutate.log" 2>&1 <<'PY'
import sys

path, surface = sys.argv[1], sys.argv[2]
lines = open(path, encoding="utf-8").read().splitlines(keepends=True)
inside_section = False
inside_entry = False
dropped = 0
out = []
for line in lines:
    bare = line.rstrip("\r\n")
    if bare == "surfaces:":
        inside_section = True
        out.append(line)
        continue
    if inside_section and bare and not bare.startswith(" ") and ":" in bare:
        inside_section = False
    if inside_section and bare == "  %s:" % surface:
        inside_entry = True
        out.append(line)
        continue
    if inside_entry and bare.startswith("  ") and not bare.startswith("   "):
        inside_entry = False
    if inside_entry and bare.startswith("    default:"):
        dropped += 1
        continue
    out.append(line)
if dropped != 1:
    print("the mutant anchor moved: expected exactly 1 default line in surfaces.%s, dropped %d" % (surface, dropped))
    raise SystemExit(3)
open(path, "w", encoding="utf-8").write("".join(out))
print("the mutant drops the 'default:' line of surfaces.%s (anchor located once)" % surface)
PY
mutate_rc=$?
printf '  ACTUAL mutant builder: rc=%s %s\n' "$mutate_rc" "$(sed -n '1p' "$scratch/mutate.log")"
if [ "$mutate_rc" -eq 3 ]; then
  cannot_assess "the control's mutation anchor moved ($(sed -n '1p' "$scratch/mutate.log"))"
elif [ "$mutate_rc" -ne 0 ]; then
  fail "the mutant builder failed (rc=$mutate_rc)"
else
  ok "the mutant builder located the anchor exactly once"
fi

refuse_log="$scratch/refuse.log"
env PYTHONDONTWRITEBYTECODE=1 "$PY" -m infra.rollout.projection --write --root "$mutant" \
  >"$refuse_log" 2>&1
refuse_rc=$?
expect_rc "the projector refuses rather than guessing" 2 "$refuse_rc"
expect_line "the refusal names the declaration it could not locate" \
  "CANNOT-ASSESS" "$refuse_log"
expect_line "the refusal names the surface" "surfaces.$CONTROL_SURFACE" "$refuse_log"
mutant_before="$(sha256sum "$mutant/$REGISTRY_REL")"
printf '  ACTUAL the refused run left the mutant registry at %s\n' "${mutant_before%% *}"
expect_line "the refused run wrote nothing" "declares no 'default:' line to project into" "$refuse_log"

# --------------------------------------------------------------------------- #
# 7. the committed declarations are untouched
# --------------------------------------------------------------------------- #
note "== 7. the committed declarations, after everything above =="
state_after="$(sha256sum "$REGISTRY" "$LIVE_STATE")"
if [ "$state_before" = "$state_after" ]; then
  printf '  ACTUAL %s\n' "$(sha256sum "$REGISTRY")"
  printf '  ACTUAL %s\n' "$(sha256sum "$LIVE_STATE")"
  ok "the committed declarations are byte-identical (nothing to restore)"
else
  printf '  ACTUAL before/after:\n%s\n%s\n' "$state_before" "$state_after"
  fail "this gate mutated the committed declarations"
fi

# --------------------------------------------------------------------------- #
note "== summary =="
if [ "$failures" -eq 0 ]; then
  note "$GATE: OK -- the committed tree is projected; an unprojected promotion is reported by name, the projection turns the surface on through the console's own reader, and it refuses when it cannot locate the line."
  exit 0
fi
note "$GATE: $failures control(s)/finding(s) failed"
exit 1
