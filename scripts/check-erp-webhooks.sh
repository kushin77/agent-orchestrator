#!/usr/bin/env bash
# check-erp-webhooks.sh — the ERP conversion-webhook bridge lane's gate
# (issue #990, EPIC #665).
#
# WHY THIS EXISTS
#   The lane ships the conversion-event bridge (`integrations/erp/webhooks/`):
#   CRM conversion -> GL posting, fail-closed end to end. Its tests pass as a
#   suite, but a suite that no gate NAMES is decorous and inert (GR-12): it
#   runs when someone remembers. This script is the gate that names it.
#   `scripts/discover-checks.sh` (#698) wires it the moment it lands, with no
#   hand-edit to the `checks=()` array in `scripts/verify.sh`.
#
# WHAT IS MEASURED, AND WHY EACH STEP CAN FAIL
#   1. THE SUITE. `integrations/erp/webhooks/tests` runs. A red test fails the
#      gate.
#   2. THE BRIDGE FLAG INVARIANT. The lane ships its own feature flag
#      (`erp-webhooks-bridge`, `integrations/erp/webhooks/flags.py`) defaulting
#      OFF and fail-closed: an absent flag map and an explicit-off map both
#      resolve to off, never on (GR-5 — a new surface ships flag-gated off).
#      The flag is declared in the lane's flags module, not in the central
#      promotion registry (the lane's own docstring says promotion belongs to a
#      reviewed go-live), so this gate measures the module's shipped default
#      directly.
#   3. THE MUTANT — the step that keeps step 2 from being a formality. The
#      flags module is copied to a SCRATCH tree, `DEFAULT_ENABLED = False` is
#      flipped to `True`, and the copied driver is run again. It MUST go
#      non-zero and name the flag default as the failing invariant. The
#      fail-closed default is the lane's central invariant, so a driver that
#      still reports OK with the default flipped on proves nothing about
#      anything else it reports.
#
# THE MUTANT CARRIES ITS OWN NEGATIVE CONTROL. Before judging the mutant's
#   output, the script asserts the mutation actually LANDED (the file's sha256
#   changed). A harness that reports "the gate caught it" while the edit never
#   applied is a false green -- and it has happened in this repository.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.

#
# ---knowledge---
# module_id: scripts.check-erp-webhooks
# system: governance
# app: gates
# solution_class: enterprise
# patterns: [tri-state-exit, provoked-negative-control, no-false-green, feature-flag-gated-off]
# derives_from: null
# owner_sme: qa-sme
# tier: L1
# interfaces: [exit 0 OK, exit 1 NOT-OK, exit 2 CANNOT-ASSESS]
# invariants: ""
# gotchas: ""
# related: ["#665", "#698", "#990"]
# do_not_duplicate: null
# ---knowledge---
set -uo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 2

failures=0
ok()   { printf '  OK    %s\n' "$1"; }
fail() { printf '  FAIL  %s\n' "$1"; failures=$((failures + 1)); }

for tool in python3 sha256sum; do
  command -v "$tool" >/dev/null 2>&1 || { echo "check-erp-webhooks: CANNOT-ASSESS — $tool is not on PATH" >&2; exit 2; }
done
[ -d integrations/erp/webhooks ] || { echo "check-erp-webhooks: CANNOT-ASSESS — integrations/erp/webhooks is absent" >&2; exit 2; }

echo "== erp-webhooks: the suite =="
suite_out="$(env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q integrations/erp/webhooks/tests 2>&1)"
suite_rc=$?
printf '%s\n' "$suite_out" | tail -3 | sed 's/^/  /'
if [ "$suite_rc" -eq 0 ]; then
  ok "the suite passes"
else
  fail "the suite failed (rc=$suite_rc)"
fi

echo
echo "== erp-webhooks: the bridge flag ships off, fail-closed =="
# A unique scratch dir WITHOUT a trailing run of `X`: this repository's docs-lint
# scans for unfinished markers and a `mktemp` X-suffix is one of them, so this
# follows the convention the other gates use — an explicit name built from a
# timestamp and the pid, with `mkdir` refusing loudly rather than silently
# reusing another run's tree.
work="${TMPDIR:-/tmp}/ao-erp-webhooks-mutant.$(date +%s%N).$$"
if ! mkdir "$work" 2>/dev/null; then
  echo "check-erp-webhooks: CANNOT-ASSESS — cannot create a scratch dir at $work" >&2
  exit 2
fi
cleanup() { rm -rf "$work"; }
trap cleanup EXIT

mkdir -p "$work/integrations/erp/webhooks"
cp integrations/erp/webhooks/flags.py "$work/integrations/erp/webhooks/flags.py"

# The driver runs in its own process, reading the copied flags module from the
# scratch tree only (`sys.path` points at the copy, and PYTHONDONTWRITEBYTECODE
# keeps a stale bytecode cache from masking the mutation).
pkgdir="$work/integrations/erp/webhooks"
driver() {
  env PYTHONDONTWRITEBYTECODE=1 python3 - "$pkgdir" <<'PY'
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
import flags

problems = []
if flags.FLAG_ID != "erp-webhooks-bridge":
    problems.append("FLAG_ID is %r" % flags.FLAG_ID)
if flags.DEFAULT_ENABLED is not False:
    problems.append("DEFAULT_ENABLED is %r, not off" % flags.DEFAULT_ENABLED)
if flags.is_enabled(None) is not False:
    problems.append("an absent flag map did not resolve to off (fail-closed broken)")
if flags.is_enabled({flags.FLAG_ID: False}) is not False:
    problems.append("an explicit-off map did not resolve to off")

if problems:
    for problem in problems:
        print("FAIL: %s" % problem)
    raise SystemExit(1)
print("OK: the bridge flag ships off and is fail-closed (absent and explicit-off both resolve to off)")
PY
}

flag_out="$(driver 2>&1)"
flag_rc=$?
printf '%s\n' "$flag_out" | sed 's/^/  /'
if [ "$flag_rc" -eq 0 ]; then
  ok "the bridge flag ships off and is fail-closed"
else
  fail "the flag invariant did not hold (rc=$flag_rc)"
fi

echo
echo "== erp-webhooks: the mutant — the flag default flipped on =="
target="$work/integrations/erp/webhooks/flags.py"
before="$(sha256sum "$target" | cut -d' ' -f1)"

python3 - "$target" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
source = path.read_text(encoding="utf-8")
needle = "DEFAULT_ENABLED = False"
if needle not in source:
    print("MUTATION-FAILED: the flag-default anchor is not in %s" % path, file=sys.stderr)
    raise SystemExit(1)
mutated = source.replace(needle, "DEFAULT_ENABLED = True  # MUTANT: the flag default flipped on", 1)
if mutated == source:
    print("MUTATION-FAILED: the replacement produced identical source", file=sys.stderr)
    raise SystemExit(1)
path.write_text(mutated, encoding="utf-8")
PY
mutation_rc=$?
after="$(sha256sum "$target" | cut -d' ' -f1)"

if [ "$mutation_rc" -ne 0 ] || [ "$before" = "$after" ]; then
  fail "the mutation did not land (rc=$mutation_rc, sha unchanged) - the mutant proves nothing"
else
  ok "the mutation landed (sha ${before:0:12} -> ${after:0:12})"

  mutant_out="$(driver 2>&1)"
  mutant_rc=$?
  printf '%s\n' "$mutant_out" | sed 's/^/  /'

  if [ "$mutant_rc" -eq 0 ]; then
    fail "the mutant driver still reported OK with the flag default flipped on"
  else
    ok "the mutant driver went non-zero (rc=$mutant_rc)"
  fi
  case "$mutant_out" in
    *"not off"*|*"fail-closed broken"*)
      ok "the mutant was refused BY NAME (the flag default is the failing invariant)" ;;
    *)
      fail "the mutant failed for the wrong reason - the flag default is not named" ;;
  esac
fi

echo
if [ "$failures" -gt 0 ]; then
  echo "check-erp-webhooks: FAIL — $failures check(s) failed" >&2
  exit 1
fi
echo "check-erp-webhooks: OK — the suite passes, the bridge flag ships off and fail-closed, and that default is load-bearing"
exit 0
