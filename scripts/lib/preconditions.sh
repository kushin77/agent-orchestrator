#!/usr/bin/env bash
# scripts/lib/preconditions.sh — shared CANNOT-ASSESS precondition helpers.
#
# A gate whose precondition (a tool, an env var, an authenticated `gh`) is
# absent must exit 2 BY NAME, never FAIL (1) and never silently pass (0) —
# and never let the missing precondition surface only in its LIVE half while
# an offline provocation/negative-control half still runs and reports FAIL
# for a comparator it was never able to reach. The rule: when the
# precondition is absent, the WHOLE gate is CANNOT-ASSESS, before any
# provocation runs.
#
# Each helper prints exactly one line, `CANNOT-ASSESS <name>: <what to
# install/set>`, and exits 2 — the name is what the unexplained-skips lane
# (#1199) consumes, so the vocabulary stays closed: `compose-plugin-missing`,
# `gh-unauthenticated`, `gcloud-missing`, `auth-env-missing:<VAR>`,
# `console-mirror-unreachable`.
#
# Usage: source this file, then call the helper as the FIRST thing the gate
# does, before its structural/provoked sections. `exit 2` from a sourced
# function exits the calling shell — call these directly, never inside a
# command substitution ($( )), or the exit would only end the subshell.
#
#   source "$root/scripts/lib/preconditions.sh"
#   require_tool docker compose-plugin-missing
#   require_gh_auth gh-unauthenticated
#   require_env PORTAL_AUTH_GATE_JWKS "auth-env-missing:PORTAL_AUTH_GATE_JWKS"

# require_tool <name> <finding> — the binary must resolve on PATH.
require_tool() {
  local tool="$1" finding="$2"
  command -v "$tool" >/dev/null 2>&1 && return 0
  echo "CANNOT-ASSESS ${finding}: install ${tool} and put it on PATH" >&2
  exit 2
}

# require_env <VAR> <finding> — the named env var must be set and non-empty.
require_env() {
  local var="$1" finding="$2"
  [ -n "${!var:-}" ] && return 0
  echo "CANNOT-ASSESS ${finding}: set \$${var}" >&2
  exit 2
}

# require_gh_auth [finding] — gh must be installed AND authenticated.
# `gh auth status` hits the network; callers that need the result more than
# once should cache it rather than calling this helper repeatedly.
require_gh_auth() {
  local finding="${1:-gh-unauthenticated}"
  if ! command -v gh >/dev/null 2>&1; then
    echo "CANNOT-ASSESS ${finding}: gh is not installed — install gh and run 'gh auth login'" >&2
    exit 2
  fi
  gh auth status >/dev/null 2>&1 && return 0
  echo "CANNOT-ASSESS ${finding}: gh is installed but not authenticated — run 'gh auth login'" >&2
  exit 2
}
