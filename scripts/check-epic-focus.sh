#!/usr/bin/env bash
# check-epic-focus.sh — the active-epic resolver gate (epic #707, lane F1/#716).
#
# The single-epic focus is only binding if a gate can genuinely fail on it
# (GR-12 / AO-GR-19): a doc-only rule is advisory. This check refuses when the
# resolver is MISSING, and then drives the resolver + schema self-control
# mutants — if any mutant passes, the check FAILS, so it cannot pass vacuously.
#
# It also validates the committed `.board/focus.json` against the focus schema
# (a corrupt board is a NOT-OK, never a silent default).
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS (no python3).
#
# Usage: bash scripts/check-epic-focus.sh
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-epic-focus: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

# (a) The resolver must exist. A missing resolver is an explicit failure, never
# a skip: the focus contract is unimplemented.
if [ ! -f governance/dispatch/focus.py ]; then
  echo "check-epic-focus: FAIL — governance/dispatch/focus.py is missing" >&2
  exit 1
fi

# (b) Resolver + schema mutants, then the committed board focus.
python3 governance/dispatch/cli.py focus --self-control
rc=$?
case "$rc" in
  0) exit 0 ;;
  2) exit 2 ;;
  *) exit 1 ;;
esac
