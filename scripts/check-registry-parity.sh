#!/usr/bin/env bash
# Registry <-> canonical CMR catalog parity gate (issue #145).
#
# Fails when the agent-orchestrator registry's DECLARED profile/persona
# vocabulary (the closed enums in registry/profiles/agent-profile.schema.json
# and registry/personas/persona-card.schema.json) drifts from the canonical CMR
# role catalog (vendor/CMR/onboarding/agent-profiles/role.schema.json plus the
# vendor/CMR/catalog/ directory), in EITHER direction.
#
# Tri-state exit code (guardrails/honesty issue #28):
#   0  OK             registry vocabulary == canonical CMR vocabulary
#   1  NOT-OK         drift, or the two registry schemas disagree
#   2  CANNOT-ASSESS  canonical source absent/unreadable (e.g. the vendor/CMR
#                     submodule is unpopulated in a fresh worktree) — NEVER 0,
#                     because an unreadable source is not agreement.
#
# No network. All arguments are forwarded to registry/parity/parity.py
# (notably --cmr-root DIR, --json, --self-test, --registry-root DIR).
#
# NOTE: this gate is intentionally NOT wired into `make verify` yet; the
# orchestrator indexes it after merge (see docs/REGISTRY-PROVENANCE.md).
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-registry-parity: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

exec python3 "$root/registry/parity/parity.py" "$@"
