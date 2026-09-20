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
# CONTRACT (harvested — matches vendor/CMR/ops/retry.sh and check-runaway-guard.sh)
#   * attempt budget  AO_GH_MAX_ATTEMPTS (default 3)
#   * backoff         min(AO_GH_BASE_DELAY * 2**(n-1), 300) seconds
#   * dead-letter     after the budget: `gh-bounded: DEAD-LETTER …`, exit 75
#   * RATE LIMIT is TERMINAL — a `RATE_LIMIT` / `rate limit` / `secondary rate` error is
#     NEVER retried (retrying it is the defect); it dead-letters immediately.
#   * optional success cache: AO_GH_CACHE_DIR (keyed by sha1 of argv) — a re-run is a no-op.
#
# USAGE
#   source scripts/lib/gh-bounded.sh
#   gh_bounded issue create -R owner/repo --title … --body-file … || exit 75
#   gh_bounded api repos/owner/repo/issues/1 --jq .state
#
# SHELL-PATTERNS: this is the ONLY sanctioned retry loop around gh. A raw
# `for … do gh …; sleep …; done` around gh is refused by `scripts/check-gh-bounded.sh`.
gh_bounded() {
  local max="${AO_GH_MAX_ATTEMPTS:-3}"
  local base="${AO_GH_BASE_DELAY:-5}"
  local gh_bin="${GH:-gh}"
  local cache="${AO_GH_CACHE_DIR:-}"
  local key="" attempt=1 delay out rc

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
      delay=$((base * (1 << (attempt - 1))))
      [ "$delay" -gt 300 ] && delay=300
      sleep "$delay"
    fi
    attempt=$((attempt + 1))
  done
  printf 'gh-bounded: DEAD-LETTER — %d attempts exhausted for: gh %s\n' "$max" "$*" >&2
  return 75
}
