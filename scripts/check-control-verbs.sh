#!/usr/bin/env bash
# check-control-verbs.sh — the control-verb vocabulary is closed, and matches
# the levers it describes (issue #553, EPIC #551).
#
# The registry `control-plane/control/verbs.yaml` is the ONE place a control
# action may be named (ADR-0025 D2 owns the action segment). This gate proves
# two things that a schema alone cannot:
#
#   1. the registry is internally sound — closed effect classes, closed refusal
#      codes, an audit action on every non-read verb and none on a read;
#   2. it CROSS-REFERENCES the five lever files, in both directions, so it can
#      neither omit a verb the fleet can really run nor describe a lever that
#      has disappeared.
#
# It then provokes four defects in a scratch copy and REQUIRES each to be
# refused by name, so the gate cannot pass vacuously (GR-12).
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS (no python3, no PyYAML).
# CANNOT-ASSESS must never read as a pass.
#
# Usage: bash scripts/check-control-verbs.sh
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-control-verbs: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

registry="control-plane/control/verbs.yaml"
cli="control-plane/control/cli.py"
for required in "$registry" "$cli" control-plane/control/schema/verbs.schema.json; do
  if [ ! -f "$required" ]; then
    echo "check-control-verbs: CANNOT-ASSESS — $required is missing" >&2
    exit 2
  fi
done

if ! python3 -c 'import yaml' >/dev/null 2>&1; then
  echo "check-control-verbs: CANNOT-ASSESS — PyYAML is not importable" >&2
  exit 2
fi

# --- the real check ---------------------------------------------------------
echo "== the registry =="
if ! python3 "$cli" validate; then
  echo "check-control-verbs: FAIL — the registry does not validate" >&2
  exit 1
fi

# --- the negative controls --------------------------------------------------
# Each provokes a distinct defect in a scratch copy of the repository's own
# registry and requires the validator to refuse it BY NAME. The real files are
# never touched.
work="$(mktemp -d "${TMPDIR:-/tmp}/control-verbs.XXXXXX")" || {
  echo "check-control-verbs: CANNOT-ASSESS — cannot create a scratch directory" >&2
  exit 2
}
trap 'rm -rf "$work"' EXIT

fail=0
ran=0

# provoke <label> <mutator> <expected-needle>
provoke() {
  local label="$1" mutator="$2" needle="$3"
  ran=$((ran + 1))

  rm -rf "$work/repo"
  mkdir -p "$work/repo/control-plane/control/schema" "$work/repo/fleet" \
           "$work/repo/governance/dispatch" "$work/repo/governance/reconcile" \
           "$work/repo/governance/lifecycle"

  cp "$registry" "$work/repo/$registry"
  cp "$cli" "$work/repo/$cli"
  cp control-plane/control/schema/verbs.schema.json \
     "$work/repo/control-plane/control/schema/verbs.schema.json"
  # The cross-reference direction needs the lever files present, so copy the
  # real ones. They are only ever READ.
  cp fleet/control.py "$work/repo/fleet/control.py"
  cp fleet/channel.py "$work/repo/fleet/channel.py"
  cp governance/dispatch/cli.py "$work/repo/governance/dispatch/cli.py"
  cp governance/reconcile/cli.py "$work/repo/governance/reconcile/cli.py"
  cp governance/lifecycle/cli.py "$work/repo/governance/lifecycle/cli.py"

  local target="$work/repo/$registry"
  local before after
  before="$(sha256sum "$target" | cut -d' ' -f1)"

  if ! python3 -c "
import sys
sys.argv = ['mutate', sys.argv[1]]
$mutator
" "$target"; then
    echo "  FAIL  $label: the mutator could not apply (the registry moved)" >&2
    fail=1
    return
  fi

  after="$(sha256sum "$target" | cut -d' ' -f1)"
  if [ "$before" = "$after" ]; then
    echo "  FAIL  $label: the mutation changed nothing — the control would prove nothing" >&2
    fail=1
    return
  fi

  local out rc
  out="$(cd "$work/repo" && python3 "$cli" validate 2>&1)"
  rc=$?

  if [ "$rc" -eq 1 ] && printf '%s' "$out" | grep -qF "$needle"; then
    echo "  OK    $label (rc=1, naming $needle)"
  else
    echo "  FAIL  $label: expected rc=1 naming '$needle', got rc=$rc" >&2
    printf '%s\n' "$out" | sed 's/^/          /' >&2
    fail=1
  fi
}

echo "== negative controls (each refused by name) =="

provoke "a local verb the registry omits is refused" '
import io, re
p = sys.argv[1]
s = io.open(p, encoding="utf-8").read()
# Drop the fleet.stop entry entirely: fleet/control.py still provides `stop`.
m = re.search(r"\n  - id: fleet\.stop\n(?:.*\n)*?(?=\n  - id:|\Z)", s)
assert m, "fleet.stop entry not found"
io.open(p, "w", encoding="utf-8").write(s[:m.start()] + "\n" + s[m.end():])
' "MISSING: fleet/control.py declares the verb 'stop'"

provoke "an entry with no effect_class is refused" '
import io, re
p = sys.argv[1]
s = io.open(p, encoding="utf-8").read()
s2 = s.replace("  - id: fleet.pause\n", "  - id: fleet.pause\n", 1)
s2 = re.sub(r"(  - id: fleet\.pause\n(?:.*\n)*?)(    effect_class: hold\n)", r"\1", s2, count=1)
assert s2 != s, "effect_class line not removed"
io.open(p, "w", encoding="utf-8").write(s2)
' "effect_class"

provoke "a refusal code outside the closed set is refused" '
import io
p = sys.argv[1]
s = io.open(p, encoding="utf-8").read()
s2 = s.replace("refusals: [401, 403, 409, 503]", "refusals: [401, 403, 599, 503]", 1)
assert s2 != s, "refusal list not found"
io.open(p, "w", encoding="utf-8").write(s2)
' "outside the closed set"

provoke "an entry whose local verb no longer exists is refused" '
import io
p = sys.argv[1]
s = io.open(p, encoding="utf-8").read()
tail = """
  - id: fleet.ghost
    source: fleet/control.py
    local: ghost
    effect_class: hold
    capability: fleet:operate
    audit: fleet.ghost
    idempotent: true
    exposed: true
    refusals: [401, 403, 503]
"""
io.open(p, "w", encoding="utf-8").write(s + tail)
' "ABSENT: the registry declares 'ghost'"

if [ "$fail" -ne 0 ]; then
  echo "check-control-verbs: FAIL — $ran control(s) run, at least one was not refused" >&2
  exit 1
fi

echo "  OK    $ran control(s) exercised, each refused by name"
echo "check-control-verbs: OK — the vocabulary is closed and matches the levers it describes"
