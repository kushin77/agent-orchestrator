#!/usr/bin/env bash
# check-surface-class.sh — per-surface target solution-class gate (issue #351).
#
# Every product surface declares the rung of the CMR ladder it is held to
# (governance/conformance/surfaces.yaml), and this gate fails while a surface
# sits below its declaration. It complements scripts/check-conformance.sh —
# that gate classifies a piece of *work*, this one classifies a *surface*.
#
# A declared surface whose machine evidence is absent, a surface declaring a
# class that is not a rung, a declared path that is missing or escapes the repo,
# a duplicated surface and an existing surface root no surface declares are all
# findings; a declared class whose requirement cannot be machine-checked is
# REPORTED (never silently assumed met).
#
# The gate then runs its own negative control: it copies the policy to a scratch
# tree, raises the first surface's declared class by one rung, and requires the
# checker to refuse the mutant BY NAME. If the mutant passes, this gate reports
# FAIL — a check that cannot fail is a formality (no-false-green doctrine).
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
# CANNOT-ASSESS must never be reported as a pass.
#
# Usage: bash scripts/check-surface-class.sh
#
# ---knowledge---
# module_id: scripts.check-surface-class
# system: governance
# app: gates
# solution_class: enterprise
# patterns: [tri-state-exit, provoked-negative-control, no-false-green, named-refusal]
# derives_from: null
# owner_sme: qa-sme
# tier: L1
# interfaces: [exit 0 OK, exit 1 NOT-OK, exit 2 CANNOT-ASSESS]
# invariants: ""
# gotchas: ""
# related: ["#351"]
# do_not_duplicate: null
# ---knowledge---
set -u

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 2

policy="governance/conformance/surfaces.yaml"
checker="governance/conformance/surfaces.py"

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-surface-class: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi
if [ ! -f "$policy" ]; then
  echo "check-surface-class: CANNOT-ASSESS — no surface policy at $policy" >&2
  exit 2
fi
if [ ! -f "$checker" ]; then
  echo "check-surface-class: CANNOT-ASSESS — no checker at $checker" >&2
  exit 2
fi
if ! command -v sha256sum >/dev/null 2>&1; then
  echo "check-surface-class: CANNOT-ASSESS — sha256sum not found (cannot prove the mutation)" >&2
  exit 2
fi

run_check() {
  # $1 = policy path (relative to $root)
  python3 "$checker" check --root "$root" --policy "$1"
}

base_out="$(run_check "$policy" 2>&1)"
base_rc=$?
printf '%s\n' "$base_out"
case "$base_rc" in
  0) : ;;
  1)
    echo "check-surface-class: FAIL — a surface sits below its declared class (see findings above)" >&2
    exit 1
    ;;
  *)
    echo "check-surface-class: CANNOT-ASSESS — the checker returned $base_rc" >&2
    exit 2
    ;;
esac

# --- negative control: raise a surface's declared class beyond its evidence ---
work="/tmp/ao351.$$.$(date +%s)"
if ! mkdir -p "$work"; then
  echo "check-surface-class: CANNOT-ASSESS — cannot create a scratch directory" >&2
  exit 2
fi
trap 'rm -rf "$work"' EXIT

base_sha="$(sha256sum "$policy" | awk '{print $1}')"

python3 - "$policy" "$work/surfaces.yaml" <<'PY'
import sys
from pathlib import Path

import yaml

source, destination = Path(sys.argv[1]), Path(sys.argv[2])
document = yaml.safe_load(source.read_text(encoding="utf-8"))
ladder = [str(rung) for rung in document.get("ladder") or ()]
surfaces = document.get("surfaces") or []
if not surfaces:
    print(
        "check-surface-class: CANNOT-ASSESS — the policy declares no surface to mutate",
        file=sys.stderr,
    )
    raise SystemExit(2)
entry = surfaces[0]
current = str(entry.get("declared_class") or "")
if current not in ladder or ladder.index(current) + 1 >= len(ladder):
    print(
        "check-surface-class: CANNOT-ASSESS — surface %r is already at the top rung"
        % entry.get("surface"),
        file=sys.stderr,
    )
    raise SystemExit(2)
entry["declared_class"] = ladder[ladder.index(current) + 1]
destination.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
PY
mutate_rc=$?
if [ "$mutate_rc" -ne 0 ]; then
  exit 2
fi

mutant_sha="$(sha256sum "$work/surfaces.yaml" | awk '{print $1}')"
if [ "$mutant_sha" = "$base_sha" ]; then
  echo "check-surface-class: CANNOT-ASSESS — the mutation did not change the policy" >&2
  exit 2
fi

mutant_out="$(run_check "$work/surfaces.yaml" 2>&1)"
mutant_rc=$?
if [ "$mutant_rc" -eq 1 ] && printf '%s\n' "$mutant_out" | grep -q "surface-below-declared-class"; then
  echo "  OK    negative control: a surface declared above its evidence is refused by name"
else
  echo "check-surface-class: FAIL — negative control passed; a surface declared above its evidence was not caught (the gate cannot fail)" >&2
  printf '%s\n' "$mutant_out" >&2
  exit 1
fi

echo "check-surface-class: OK — every declared surface meets its declared class, and the mutant is refused"
exit 0
