#!/usr/bin/env bash
# check-runtime-liveness.sh — the runtime-liveness gate (issue #1271).
#
# Every runtime beats through `integrations/paperclip/adapters/heartbeat`
# (`{runtime, commit, state, ts}`), registered in `fleet/runtimes.yaml`. This
# gate gathers the real inputs (beats under `.fleet/runtime-beats/`, git
# distance to `origin/master`, directives in flight under `.fleet/inbox/`) and
# hands them to the PURE judge in `fleet/runtime_liveness.py::judge`, which is what
# `--self-test` provokes directly.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS. Findings are named
# `runtime-stale:<id>`, `runtime-drift:<id>`, `runtime-unregistered:<id>`.
#
# Usage:
#   bash scripts/check-runtime-liveness.sh              # judge the real tree
#   bash scripts/check-runtime-liveness.sh --self-test   # negative controls
set -uo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-runtime-liveness: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

verb="run"
for arg in "$@"; do
  case "$arg" in
    --self-test) verb="self-test" ;;
  esac
done

PYTHONPATH="$root${PYTHONPATH:+:$PYTHONPATH}" python3 -m fleet.runtime_liveness "$verb" --root "$root"
rc=$?

case "$rc" in
  0) echo "check-runtime-liveness: OK" ;;
  1) echo "check-runtime-liveness: NOT-OK" >&2 ;;
  2) echo "check-runtime-liveness: CANNOT-ASSESS" >&2 ;;
  *) echo "check-runtime-liveness: CANNOT-ASSESS — judge exited $rc" >&2; rc=2 ;;
esac
exit "$rc"
