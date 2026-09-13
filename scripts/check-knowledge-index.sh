#!/usr/bin/env bash
# check-knowledge-index.sh — the institutional knowledge index must be valid
# (issue #139).
#
# The indexer's own `validate` is the check: it rebuilds the catalogue, verifies
# every item's provenance, applies the secret policy to indexed assets, and
# requires each mandatory kind to be covered. It reports, but does not fail on,
# unreachable CMR-hub kinds (submodule absent) and on drift — those are honest
# gaps and refresh signals, not violations.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS (no python3, no catalogue).
# CANNOT-ASSESS must never read as a pass.
#
# Usage: bash scripts/check-knowledge-index.sh
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-knowledge-index: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

python3 governance/knowledge/cli.py validate
rc=$?
case "$rc" in
  0) exit 0 ;;
  2) exit 2 ;;
  *) exit 1 ;;
esac
