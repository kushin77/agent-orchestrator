#!/usr/bin/env bash
# governance/dupcheck/check-duplicates.sh — FORWARDING STUB. This is NOT the gate.
#
# DISPOSITION (issue #1164). The ADR-0010 no-fork detector moved to
# `scripts/check-duplicates.sh`, where `scripts/discover-checks.sh` auto-wires it
# into `scripts/verify.sh` (#698) and `scripts/check-gate-coverage.sh` reads it as
# WIRED. It moved because a detector inside a package (`governance/dupcheck/`) is
# outside that glob, so no gate ran it and the ADR-0010 rule was advisory.
#
# There is exactly ONE implementation of the rule, and it is the one in
# `scripts/`. This file carries NO copy of it: a second implementation of one rule
# is worse than none, because the two disagree silently and the disagreement stays
# invisible until a real fork slips through the weaker one. That is why this path
# forwards instead of keeping the detector and explaining the duplication away.
#
# The path is kept rather than deleted because it is named by references this lane
# does not own and may not rewrite — decision records ADR-0010 (§ "Duplicate
# detection is automated") and ADR-0031, `docs/CANNIBALIZATION.md`,
# `docs/SURFACE-CLASS.md`, `docs/spikes/47-canonical-copy-ownership.md`, and the
# `governance/knowledge/catalog.json` keyword index. A decision record that names a
# file which no longer exists is a dangling reference with no permitted repair; a
# stub that names the real gate is a redirect. For any invocation, the behaviour
# below is the gate's behaviour — the exit code is the gate's own tri-state.
#
# Exit contract: the gate's — 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: as the gate (see `bash scripts/check-duplicates.sh --help`).
set -u

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
gate="$here/../../scripts/check-duplicates.sh"

if [ ! -f "$gate" ]; then
  printf 'governance/dupcheck/check-duplicates.sh: CANNOT-ASSESS — the gate is not at %s (this path only forwards to it)\n' "$gate" >&2
  exit 2
fi

exec bash "$gate" "$@"
