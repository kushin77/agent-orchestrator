#!/usr/bin/env bash
# Registry <-> canonical CMR vocabulary parity gate (issue #145).
#
# Fails when the agent-orchestrator registry's DECLARED profile/persona
# vocabulary (the closed enums in registry/profiles/agent-profile.schema.json
# and registry/personas/persona-card.schema.json) drifts from the canonical CMR
# role vocabulary, in EITHER direction.
#
# Two modes (both tri-state, guardrails/honesty issue #28):
#
#   default (offline)   registry vocabulary  <->  the FROZEN canonical baseline
#                       registry/parity/canonical/cmr-role-vocabulary.json.
#                       Deterministic, no network and no vendor/ dependency, so
#                       it runs in `make verify` on any clone -- including a
#                       fresh worktree where the vendor/CMR submodule is
#                       unpopulated. Exit 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS
#                       (the frozen baseline is absent/unreadable).
#
#   --verify-source     the FROZEN baseline   <->  the LIVE vendor/CMR source
#                       (content sha256 + vocabulary). Reports a stale freeze.
#                       Exit 0 OK / 1 NOT-OK (the source has drifted from the
#                       freeze -- refresh it) / 2 CANNOT-ASSESS (the source is
#                       unavailable, e.g. an unpopulated submodule) -- NEVER 0,
#                       because an unreadable source is not agreement.
#
# No network. All arguments are forwarded to registry/parity/parity.py
# (notably --verify-source, --refresh-baseline, --baseline FILE, --cmr-root DIR,
# --registry-root DIR, --json, --self-test).
#
# NOTE: this gate is intentionally NOT wired into `make verify` yet; the
# orchestrator indexes it after merge (see docs/REGISTRY-PROVENANCE.md). Its
# offline default mode is what makes that wiring safe.
#
# ---knowledge---
# module_id: scripts.check-registry-parity
# system: governance
# app: gates
# solution_class: enterprise
# patterns: [tri-state-exit, provoked-negative-control, self-proving-gate, offline-hermetic, declared-authority, lane-isolation, schema-validation]
# derives_from: null
# owner_sme: qa-sme
# tier: L1
# interfaces: [exit 0 OK, exit 1 NOT-OK, exit 2 CANNOT-ASSESS]
# invariants: ""
# gotchas: ""
# related: ["#28", "#145"]
# do_not_duplicate: null
# ---knowledge---
set -u

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-registry-parity: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

exec python3 "$root/registry/parity/parity.py" "$@"
