#!/usr/bin/env bash
# check-epic-focus.sh — the active-epic resolver + out-of-epic pool gate
# (epic #707, lane F1/#716 and lane F6/#721).
#
# The single-epic focus is only binding if a gate can genuinely fail on it
# (GR-12 / AO-GR-19): a doc-only rule is advisory. This check refuses when the
# resolver is MISSING, and then drives the resolver + schema self-control
# mutants — if any mutant passes, the check FAILS, so it cannot pass vacuously.
#
# It also drives the POOL self-control (lane F6, #721): the pool is where an
# `out-of-epic-pooled` refusal parks deferred work, and it is only honest if a
# drain reports what it drained and a malformed line is rejected. A reader that
# skipped a malformed line would turn a corrupt pool into an empty one — and
# "the pool is empty" reads as "nothing was ever deferred".
#
# It also validates the committed `.board/focus.json` against the focus schema
# (a corrupt board is a NOT-OK, never a silent default).
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS (no python3).
#
# Usage: bash scripts/check-epic-focus.sh
set -u

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 2

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-epic-focus: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

# (a) The resolver and the pool must exist. A missing module is an explicit
# failure, never a skip: the focus contract is unimplemented.
for module in governance/dispatch/focus.py governance/dispatch/pool.py; do
  if [ ! -f "$module" ]; then
    echo "check-epic-focus: FAIL — $module is missing" >&2
    exit 1
  fi
done

# (b) Resolver + schema mutants, then the committed board focus.
echo "== the active-epic resolver =="
python3 governance/dispatch/cli.py focus --self-control
rc=$?
case "$rc" in
  0) ;;
  2) exit 2 ;;
  *) exit 1 ;;
esac

# (c) The out-of-epic pool: a reader that cannot reject a corrupt rail, or a
# drain that drops without reporting, is a formality.
echo "== the out-of-epic pool =="
python3 governance/dispatch/cli.py pool --self-control
rc=$?
case "$rc" in
  0) exit 0 ;;
  2) exit 2 ;;
  *) exit 1 ;;
esac
