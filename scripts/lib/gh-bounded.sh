#!/usr/bin/env bash
# gh-bounded.sh — the bounded, rate-limit-aware helper every gh retry/poll loop MUST use.
#
# WHY (AO-GR-21 / AGENTS.md rule 18 — bounded work; issue #1550)
#   An ad-hoc `for try in 1..5; do gh issue create …; sleep 10; done` loop retried a
#   RATE-LIMITED GraphQL mutation with no backoff and no dead-letter. Retrying a
#   rate-limit error cannot succeed — it only consumes more budget and AMPLIFIES the
#   exhaustion for every other agent on the box. Measured 2026-09-20: `graphql_rate_limit`
#   for user 18682900 while the REST /rate_limit still reported 5000/5000, blocking every
#   lane's `gh pr view`/`gh pr merge` and `dispatch snapshot`.
#
# CONTRACT
#   * attempt budget  AO_GH_MAX_ATTEMPTS (default 3, must be a positive integer)
#   * backoff         base * 2**(n-1), CAPPED at BACKOFF_CAP_SECONDS, computed WITHOUT a
#                     shift (so a large budget can never wrap to a negative sleep)
#   * dead-letter     after the budget: `gh-bounded: DEAD-LETTER …`, exit 75
#   * RATE LIMIT is TERMINAL — a `RATE_LIMIT` / `rate limit` / `secondary rate` error is
#     NEVER retried (retrying it is the defect); it dead-letters immediately.
#   * a misconfigured budget/base is REFUSED by name (exit 64), never a silent no-op.
#   * optional success cache: AO_GH_CACHE_DIR, keyed by the caller's cwd + argv.
#
# The backoff contract is pinned numerically to the fleet's own cap
# (`BACKOFF_CAP_SECONDS = 300`, the same value in `fleet/runaway.py` and
# `fleet/watchdog.py`) and to the harvested `vendor/CMR/ops/retry.sh` curve
# (`30 60 120 240 300 300`) that `scripts/check-runaway-guard.sh` asserts.
#
# USAGE
#   source scripts/lib/gh-bounded.sh
#   gh_bounded issue create -R owner/repo --title … --body-file … || exit 75
#   gh_bounded api repos/owner/repo/issues/1 --jq .state
#
# SHELL-PATTERNS: enforced by `scripts/check-gh-bounded.sh` — see the
# "Enforced elsewhere" row in docs/SHELL-PATTERNS.md. A raw `for … do gh …; sleep …;
# done` around gh is refused there.
# ---knowledge---
# module_id: scripts.lib.gh-bounded
# system: scripts
# app: lib
# solution_class: pattern
# patterns: []
# derives_from: null
# owner_sme: unassigned
# tier: L1
# interfaces: [gh_bounded, _gh_bounded_delay]
# invariants: ""
# gotchas: ""
# related: []
# do_not_duplicate: null
# ---knowledge---
BACKOFF_CAP_SECONDS=300

gh_bounded() {
  local max="${AO_GH_MAX_ATTEMPTS:-3}"
  local base="${AO_GH_BASE_DELAY:-5}"
  local gh_bin="${GH:-gh}"
  local cache="${AO_GH_CACHE_DIR:-}"
  local key="" attempt=1 delay out rc

  case "$max" in '' | *[!0-9]*) printf 'gh-bounded: REFUSED — AO_GH_MAX_ATTEMPTS must be a positive integer (got %s)\n' "$max" >&2; return 64 ;; esac
  [ "$max" -ge 1 ] || { printf 'gh-bounded: REFUSED — AO_GH_MAX_ATTEMPTS must be >= 1 (got %s)\n' "$max" >&2; return 64; }
  case "$base" in '' | *[!0-9]*) printf 'gh-bounded: REFUSED — AO_GH_BASE_DELAY must be a non-negative integer (got %s)\n' "$base" >&2; return 64 ;; esac

  if [ -n "$cache" ]; then
    mkdir -p "$cache" 2>/dev/null || true
    key="$cache/$(printf '%s' "$*" | sha1sum | cut -d' ' -f1)"
    [ -f "$key" ] && { cat "$key"; return 0; }
  fi

  while [ "$attempt" -le "$max" ]; do
    out="$("$gh_bin" "$@" 2>&1)"; rc=$?
    if [ "$rc" -eq 0 ]; then
      [ -n "$key" ] && printf '%s' "$out" >"$key"
      printf '%s' "$out"
      return 0
    fi
    case "$out" in
      *RATE_LIMIT* | *"rate limit"* | *rate_limit* | *"secondary rate"*)
        printf 'gh-bounded: DEAD-LETTER — attempt %d/%d hit a RATE LIMIT (never retried: retrying amplifies it) :: %s\n' \
          "$attempt" "$max" "${out:0:200}" >&2
        return 75
        ;;
    esac
    printf 'gh-bounded: attempt %d/%d failed (rc=%d): %s\n' "$attempt" "$max" "$rc" "${out:0:200}" >&2
    if [ "$attempt" -lt "$max" ]; then
      delay="$(_gh_bounded_delay "$base" "$attempt")"
      sleep "$delay"
    fi
    attempt=$((attempt + 1))
  done
  printf 'gh-bounded: DEAD-LETTER — %d attempts exhausted for: gh %s\n' "$max" "$*" >&2
  return 75
}

# _gh_bounded_delay <base> <attempt> — base * 2**(attempt-1), capped at
# BACKOFF_CAP_SECONDS, computed WITHOUT a shift: capping as it grows means a large
# budget can never wrap to a negative interval (the reviewer measured the shift wrap).
_gh_bounded_delay() {
  local d="$1" n="$2" k
  [ "$d" -gt "$BACKOFF_CAP_SECONDS" ] && d="$BACKOFF_CAP_SECONDS"
  for ((k = 1; k < n; k++)); do
    [ "$d" -ge "$BACKOFF_CAP_SECONDS" ] && { d="$BACKOFF_CAP_SECONDS"; break; }
    d=$((d * 2))
  done
  [ "$d" -gt "$BACKOFF_CAP_SECONDS" ] && d="$BACKOFF_CAP_SECONDS"
  printf '%s' "$d"
}
