#!/usr/bin/env bash
# check-hermes-integration.sh — the hermes peer-integration surface gate (issue #942).
#
# Hermes is the fleet's orchestration peer (ADR-0012); paperclip is its
# reporting peer. Until issue #942 the `integrations` surface root declared
# paperclip at `faang` and hermes nowhere, so hermes could sit below no class at
# all. This gate is the hermes half of that boundary: it holds the read-only
# projection in `integrations/hermes/` to its own contract, so a surface that is
# now declared can never sit below its declaration silently.
#
# WHAT IT CHECKS
#   1. the `check` verb conforms: the projection validates against the inline
#      CONTRACT and the cross-source consistency rules (capability-set parity,
#      tier parity, tier coverage) — tri-state 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS;
#   2. determinism: two independent `project` processes emit byte-identical
#      documents (same input -> same sha256, twice);
#   3. a mutation control: a phantom capability is planted in a scratch copy of
#      the surface; the checker must refuse it BY NAME (rc 1, naming
#      `phantom-ops`), and the copy must restore byte-identical;
#   4. the test suite passes (the negative control test included).
#
# Offline by construction: `check` and `project` are pure functions of the tree
# and never import the transport; the only network path (HttpTransport) lives in
# the `probe` verb, which this gate never calls. The adapter does import the seam
# it shares with `integrations/paperclip/` (issue #1208,
# `integrations/_seam/`), so the scratch tree below carries that package too —
# without it the mutation control would measure a missing module rather than the
# mutation.
#
# Exit-code contract (repo convention, GR-12): 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
set -u

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 2

checker="integrations/hermes/cli.py"
suite="integrations/hermes/tests"

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-hermes-integration: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi
if [ ! -f "$checker" ]; then
  echo "check-hermes-integration: CANNOT-ASSESS — no hermes adapter CLI at $checker" >&2
  exit 2
fi
if ! command -v sha256sum >/dev/null 2>&1; then
  echo "check-hermes-integration: CANNOT-ASSESS — sha256sum not found (cannot prove the mutation)" >&2
  exit 2
fi

