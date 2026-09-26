#!/usr/bin/env bash
# ============================================================================
# ops/scale-tripwire.sh — scale-up tripwire for the solo-dev merge posture
# (Refs #372, docs/decision-records/ADR-0020-solo-dev-merge-posture.md; the
# authority of the GitHub-account metric was made normative by CMR#1034).
#
# controller/auto-merge.sh currently refuses to auto-merge ANY real PR because
# every open PR on kushin77/CMR is opened by one human owner (kushin77),
# whatever the committing tool/identity inside it. That posture is only valid
# while the contributor base stays solo/near-solo. The moment a SECOND
# genuinely independent identity shows up in numbers (a real second human, a
# vendor bot, several spoke-repo owners), the "no auto-merge is possible
# anyway" assumption stops being true and the merge gate needs re-tightening
# (or, symmetrically, GR-4's carve-out needs to start actually firing for
# those new identities instead of being dead code).
#
# CMR#1034 (2026-09-26) made the metric NORMATIVE: contributor scale is
# measured by ONE authoritative signal, and ONLY it can fire.
#
#   * AUTHORITATIVE — distinct human GitHub accounts with repository access:
#     `GET /repos/{owner}/{repo}/collaborators` (granted repo ACCESS,
#     login-based). Bot/tooling logins — any login ending in `[bot]`,
#     case-insensitive — do NOT count; a bot is not a contributor for
#     coordination purposes. This is kushin77's stated trigger read
#     literally: "the github repo users/contributers hits 10 to the actual
#     repo itself". /collaborators is deliberately used instead of
#     /contributors (commit AUTHORSHIP, which carries the email signal's
#     exact bot/multi-identity inflation problem -- verified live while
#     building #627: /contributors returned kushin77 + dependabot[bot] = 2,
#     /collaborators returned kushin77 alone = 1).
#   * ADVISORY ONLY — NEVER FIRES: the distinct git-commit-author EMAIL count
#     (`git log --format=%ae`). Agent identities inflate it (measured on
#     kushin77/asterisk: ~17 emails against 2 collaborators), and firing on
#     it is a false positive that reinstates governance against a solo dev —
#     the friction CMR#1034 is a bug report against. It is still printed,
#     raw and filtered, so the trend stays visible in every `make verify`
#     run, not just when something fires.
#   * SIGNAL 2 STAYS REMOVED (ADR-0053): onboarded non-hub spokes.tsv rows
#     are registry size, not contributor count.
#   * FAIL-SAFE, UNCHANGED: an unreachable collaborators API FAILS LOUD AND
#     FIRES ("cannot prove solo scale => assume collaborative", the safe
#     direction); SCALE_TRIPWIRE_GH_OPTIONAL=1 is the operator opt-out
#     (ignored under FORCE_STRICT). See the offline paragraph below.
#
# The operator principle this enforces (2026-09-23, quoted in CMR#1034):
#   "in a less than 10 contributor repo there is no friction between agents"
# Below the trigger one agent must not obstruct another — no permission
# prompt, no deny from a scale/permission hook, no approval round-trip.
# There is no second party for the obstruction to protect.
#
# The advisory email count is computed twice — raw, and filtered by the
# AGENT_EMAIL_PATTERNS list defined at the advisory computation below
# (case-insensitive substring match on the email). Measured catches include
# `copilot@local`, `lane-*` / `gate-green lane` addresses, `frontend-sme`,
# `agent@*` addresses, and any `*[bot]`; display names are invisible to
# `%ae`, so tooling whose email carries none of those tokens may remain
# (e.g. `Akushnir Agent`'s bare `akushnir@example.com`, `camp-*`, `w2-*`) —
# accepted, because the filtered count is advisory and never affects firing.
# The list is tunable.
#
# Threshold: 10 (the user's own stated number, docs/decision-records/ADR-0020).
# Crossing it on the AUTHORITATIVE signal (or the fail-safe escalation)
# means: re-tighten controller/auto-merge.sh's self-identity list and
# re-review whether the GR-4 carve-out is now live for real traffic, not
# just fixtures. The advisory email count cannot cross it, at any value.
#
# Authoritative signal / offline behavior (fail-safe direction, explicit):
# this script must work without network (it's consulted by merge-time logic,
# controller/auto-merge.sh:173, which treats ANY non-zero exit here as
# "tripwire fired -> force collaborative posture"). When the GitHub API is
# unreachable (no `gh`, no auth, network down, or a read-only token 403ing
# on the collaborators endpoint, which requires push-level access to read),
# the authoritative signal FAILS LOUD AND FIRES by default -- i.e. treats
# "can't prove we're still solo" as "assume collaborative", the safe
# direction, never a silent pass. This is a deliberate escalation, distinct in its message from a real
# threshold breach, so operators can tell the two apart. It also means an
# environment with no/under-scoped GitHub token will show this script (and
# `make scale-tripwire`/`make verify`) as failing -- that is intentional per
# the instruction that drove this signal, not a bug; set
# SCALE_TRIPWIRE_GH_OPTIONAL=1 to opt a known-offline/no-token environment
# out of that escalation (loud WARN, does not contribute to `fired`) when
# the operator has independently confirmed solo scale another way.
#
# Usage:
#   ops/scale-tripwire.sh [--threshold N] [--repo-root DIR] [--mode AUTO|FORCE_STRICT]
#
# MODES (ADR-0047 D4 — issue #859)
#   AUTO (DEFAULT)  today's behaviour, exactly. Nothing above changes it.
#   FORCE_STRICT    a strictly TIGHTENING override. It can only ever make the
#                   tripwire fire MORE, never less: it makes the
#                   API-unreachable escalation unconditional by ignoring the
#                   SCALE_TRIPWIRE_GH_OPTIONAL operator opt-out. (The former
#                   second lever — counting non-onboarded registered parties
#                   from spokes.tsv — was removed by ADR-0053.) Set with
#                   `--mode FORCE_STRICT` or SCALE_TRIPWIRE_MODE.
#
#   WHY THERE IS NO `FORCE_PERMISSIVE`, DELIBERATELY:
#   A permissive override would invert this script's whole fail-safe — its
#   documented posture is "cannot prove solo scale => assume collaborative and
#   fire" — behind a single ambient env var. ADR-0047 D4 rejects it outright:
#   it would collide with ADR-0026 (separation-of-duties posture) and ADR-0030,
#   and would be an unattributable, unbounded way to switch a
#   security-relevant control off. If a permissive override is ever genuinely
#   needed it must be an owner-authz LEDGER entry per ADR-0035 — attributable
#   and time-bounded — never a config toggle. This script therefore REJECTS
#   any permissive/relaxing mode value with a loud exit 2; that rejection is
#   the control, so do not "helpfully" add one here.
#
# Env overrides (test harness hooks):
#   SCALE_TRIPWIRE_GIT_LOG_FILE     path to a file of emails (one per line) to
#                                    use INSTEAD of `git log --format=%ae`.
#                                    Feeds the ADVISORY count only — an email
#                                    list can no longer fire (CMR#1034).
#   SCALE_TRIPWIRE_SPOKES_FILE      path to a spokes.tsv-shaped file to use
#                                    INSTEAD of channels/spokes.tsv.
#   SCALE_TRIPWIRE_COLLAB_FILE      path to a file of GitHub logins (one per
#                                    line) to use INSTEAD of calling
#                                    `gh api repos/{repo}/collaborators`.
#                                    `*[bot]` logins are excluded from the
#                                    count and reported separately.
#   SCALE_TRIPWIRE_REPO             "owner/repo" to query (default
#                                    kushin77/CMR).
#   SCALE_TRIPWIRE_GH               gh binary override (default: `gh` on
#                                    PATH).
#   SCALE_TRIPWIRE_GH_OPTIONAL      "1" -- do NOT fire merely because the
#                                    collaborators API was unreachable (still
#                                    warns loudly). Default unset (fires).
#                                    Ignored (cannot opt out) under
#                                    FORCE_STRICT -- see MODES above.
#   SCALE_TRIPWIRE_MODE             AUTO (default) | FORCE_STRICT. Same knob
#                                    as --mode; the flag wins.
#
# Exit: 0 below threshold · 1 authoritative threshold crossed OR the
#       API-unreachable escalation (loud) · 2 usage error (including a
#       refused permissive mode).
# ============================================================================
set -euo pipefail

THRESHOLD=10
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE_SET="${SCALE_TRIPWIRE_MODE:-AUTO}"

st_usage() {
  cat <<'EOF'
usage: ops/scale-tripwire.sh [--threshold N] [--repo-root DIR]
                             [--mode AUTO|FORCE_STRICT]

Counts the AUTHORITATIVE contributor-scale signal (distinct human GitHub
accounts with repository access; bot/tooling logins excluded) and fails loudly
if it reaches the threshold (default 10) or if the signal is UNREACHABLE (the
fail-safe escalation, unchanged). The distinct git-committer EMAIL count is
printed as an advisory trend only and can never fire -- CMR#1034.

Modes: AUTO (default, today's behaviour) or FORCE_STRICT (strictly tightening:
cannot opt out of the unreachable-signal escalation). A permissive mode is
deliberately NOT supported (ADR-0047 D4) and is rejected with exit 2.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --threshold) THRESHOLD="${2:-}"; shift 2 ;;
    --threshold=*) THRESHOLD="${1#--threshold=}"; shift ;;
    --repo-root) ROOT="${2:-}"; shift 2 ;;
    --repo-root=*) ROOT="${1#--repo-root=}"; shift ;;
    --mode) MODE_SET="${2:-}"; shift 2 ;;
    --mode=*) MODE_SET="${1#--mode=}"; shift ;;
    --help|-h) st_usage; exit 0 ;;
    *) echo "scale-tripwire: unknown arg '$1'" >&2; st_usage >&2; exit 2 ;;
  esac
done

# Mode normalisation. Permissive/relaxing values are REFUSED, not ignored
# (ADR-0047 D4): silently accepting one would be the fail-safe inversion the
# ADR exists to prevent.
MODE_SET_UPPER="$(printf '%s' "$MODE_SET" | tr '[:lower:]-' '[:upper:]_')"
case "$MODE_SET_UPPER" in
  AUTO) MODE="auto"; MODE_LABEL="AUTO" ;;
  FORCE_STRICT|STRICT) MODE="strict"; MODE_LABEL="FORCE_STRICT" ;;
  FORCE_PERMISSIVE|PERMISSIVE|PERMISSIVE_FORCE)
    echo "scale-tripwire: REFUSED — mode '$MODE_SET' is a permissive/relaxing override, rejected by ADR-0047 D4 (collides with ADR-0026/ADR-0030)." >&2
    echo "scale-tripwire: a permissive override must be an owner-authz ledger entry per ADR-0035 (attributable + time-bounded), never a config toggle." >&2
    exit 2 ;;
  *)
    echo "scale-tripwire: unknown mode '$MODE_SET' — supported: AUTO (default), FORCE_STRICT." >&2
    exit 2 ;;
  esac

if ! [[ "$THRESHOLD" =~ ^[0-9]+$ ]]; then
  echo "scale-tripwire: --threshold must be a non-negative integer, got '$THRESHOLD'" >&2
  exit 2
fi

# Advisory-only email count (CMR#1034): DISTINCT git-commit-author emails in
# THIS repo's history — the former signal 1. Printed raw AND filtered by
# AGENT_EMAIL_PATTERNS (case-insensitive substring match on the email); it can
# NEVER fire, at any count or under any mode. The list is advisory-only and
# tunable — it excludes known agent/tooling identities from the REPORT, never
# from a threshold decision.
AGENT_EMAIL_PATTERNS=( "[bot]" copilot claude agent lane sme )

# Prints the lines of stdin that match NONE of AGENT_EMAIL_PATTERNS
# (case-insensitive substring). Used only for the advisory count.
filter_agent_emails() {
  local line lower pat
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "$line" ]] && continue
    lower="${line,,}"
    for pat in "${AGENT_EMAIL_PATTERNS[@]}"; do
      if [[ "$lower" == *"$pat"* ]]; then
        lower=""
        break
      fi
    done
    if [[ -n "$lower" ]]; then
      printf '%s\n' "$line"
    fi
  done
  return 0
}

if [[ -n "${SCALE_TRIPWIRE_GIT_LOG_FILE:-}" ]]; then
  committer_emails="$(sort -u "$SCALE_TRIPWIRE_GIT_LOG_FILE" | sed '/^$/d')"
else
  committer_emails="$(git -C "$ROOT" log --format='%ae' 2>/dev/null | sort -u | sed '/^$/d')"
fi
committer_count="$(printf '%s\n' "$committer_emails" | sed '/^$/d' | wc -l | tr -d ' ')"
committer_filtered_count="$(printf '%s\n' "$committer_emails" | filter_agent_emails | wc -l | tr -d ' ')"

# Signal 2 (ADR-0053): REMOVED as a counted signal. It used to count every
# ONBOARDED non-hub row in spokes.tsv, but that conflates "repos this owner
# governs" with "people who commit to us" -- a spokes.tsv row for a
# self-owned vendored module or harvest source is not a contributor
# identity. Real headcount is carried by the AUTHORITATIVE signal alone --
# distinct human GitHub accounts with access (CMR#1034; the git-committer
# email count is advisory-only and never fires). spoke_count is kept at 0 (never
# fires) so downstream reporting/output shape is unchanged; see ADR-0053 for
# the rationale and ADR-0020/ADR-0027 for the threshold this preserves.
spoke_count=0

# AUTHORITATIVE signal (issue #627; made the only firing signal by
# CMR#1034): distinct human GitHub-account repo collaborators. `[bot]`
# logins are excluded from the count and reported separately.
collab_status="ok"
collab_count=0
collab_all=""
collab_bots=0
collab_bot_list=""
collab_note=""
# ADR-0051: this script is vendored into every spoke/vendor repo (ADR-0019
# dissection), so "kushin77/CMR" as a hardcoded default is only correct when
# it happens to run in the hub itself. Detect the ACTUAL repo this checkout
# belongs to from its own git remote first; fall back to kushin77/CMR only
# when that can't be determined (detached checkout, no remote, etc) -- an
# explicit SCALE_TRIPWIRE_REPO always wins over both.
gh_repo_detected=""
if [[ -z "${SCALE_TRIPWIRE_REPO:-}" ]]; then
  origin_url="$(git -C "$ROOT" remote get-url origin 2>/dev/null || true)"
  gh_repo_detected="$(printf '%s' "$origin_url" | sed -E 's#^(git@|https://)github\.com[:/]##; s#\.git$##')"
fi
gh_repo="${SCALE_TRIPWIRE_REPO:-${gh_repo_detected:-kushin77/CMR}}"
gh_bin_st="${SCALE_TRIPWIRE_GH:-gh}"

# GitHub admission control (ADR-0046, CMR#857): the single read below routes
# through the fleet's shared budget wrapper (scripts/gh-budget.sh) so a control
# that `make verify` runs every time is counted in the one ledger instead of
# being invisible. STRICTLY READ-ONLY. Wrapper exit 75 = QUEUED (the call was
# deliberately NOT made — fleet cooldown, or quota below the reserve floor) is
# treated exactly like any other "could not query" outcome: the fail-safe
# escalation below still applies (cannot prove solo scale => assume
# collaborative), with the throttle named in the message so it is not mistaken
# for an auth/network failure. CMR_GH_MAX_WAIT is bounded below the outer
# `timeout` so the wrapper returns 75 rather than being killed mid-wait —
# otherwise the queued signal would be lost to SIGTERM and misreported.
GH_BUDGET="${CMR_GH_BUDGET_SH:-$ROOT/scripts/gh-budget.sh}"
QUEUED_RC=75

if [[ -n "${SCALE_TRIPWIRE_COLLAB_FILE:-}" ]]; then
  collab_all="$(sort -u "$SCALE_TRIPWIRE_COLLAB_FILE" | sed '/^$/d')"
elif ! command -v "$gh_bin_st" >/dev/null 2>&1; then
  collab_status="unreachable"
else
  set +e
  # ADR-0051: scripts/gh-budget.sh is a CMR-hub-only budget wrapper (ADR-0046)
  # -- it does not exist in vendored spoke checkouts. Its absence used to read
  # as "unreachable" and fire the fail-safe escalation unconditionally in
  # every spoke, forever. A budget wrapper that is simply not present (not a
  # broken one) is not an API-reachability problem, so call gh directly here
  # instead of treating "no wrapper" as "no GitHub access".
  if [[ -x "$GH_BUDGET" || -f "$GH_BUDGET" ]]; then
    collab_raw="$(timeout 10s env CMR_GH_MAX_WAIT=5 bash "$GH_BUDGET" exec --class read -- \
                   "$gh_bin_st" api "repos/${gh_repo}/collaborators" --paginate --jq '.[].login' 2>/dev/null)"
  else
    collab_raw="$(timeout 10s "$gh_bin_st" api "repos/${gh_repo}/collaborators" --paginate --jq '.[].login' 2>/dev/null)"
  fi
  collab_rc=$?
  set -e
  if [[ "$collab_rc" -eq "$QUEUED_RC" ]]; then
    collab_status="unreachable"
    collab_note=" [QUEUED by the fleet's shared GitHub budget (ADR-0046) — the call was deliberately NOT made: fleet cooldown active, or primary quota below the reserve floor]"
  elif [[ "$collab_rc" -ne 0 ]]; then
    collab_status="unreachable"
  else
    collab_all="$(printf '%s\n' "$collab_raw" | sort -u | sed '/^$/d')"
  fi
fi

# Split the authoritative list: humans count toward the threshold; `[bot]`
# logins are excluded and reported (CMR#1034). Case-insensitive suffix match
# on the literal `[bot]` (lowercase the login first).
if [[ "$collab_status" == "ok" ]]; then
  collab_humans=""
  while IFS= read -r login; do
    [[ -z "$login" ]] && continue
    if [[ "${login,,}" == *"[bot]" ]]; then
      collab_bots=$((collab_bots + 1))
      collab_bot_list="${collab_bot_list:+$collab_bot_list, }$login"
    else
      collab_humans="$collab_humans$login"$'\n'
    fi
  done <<< "$collab_all"
  collab_count="$(printf '%s\n' "$collab_humans" | sed '/^$/d' | wc -l | tr -d ' ')"
fi

echo "scale-tripwire: mode = $MODE_LABEL (AUTO default · FORCE_STRICT only ever tightens)"
echo "scale-tripwire: advisory: distinct git-committer emails = $committer_count raw / $committer_filtered_count excluding agent/tooling identities (never fires; CMR#1034)"
echo "scale-tripwire: signal 2 (onboarded non-hub spokes) REMOVED per ADR-0053 — registry size is not contributor count; see the advisory/authoritative lines above."
if [[ "$collab_status" == "ok" ]]; then
  if [[ "$collab_bots" -gt 0 ]]; then
    echo "scale-tripwire: authoritative: distinct human GitHub accounts with access = $collab_count (threshold $THRESHOLD, repo $gh_repo; excluded $collab_bots bot login(s): $collab_bot_list)"
  else
    echo "scale-tripwire: authoritative: distinct human GitHub accounts with access = $collab_count (threshold $THRESHOLD, repo $gh_repo; excluded 0 bot login(s))"
  fi
else
  echo "scale-tripwire: authoritative: distinct human GitHub accounts with access = UNREACHABLE (repo $gh_repo, gh='$gh_bin_st') — could not query the GitHub API (no gh/no auth/network down/insufficient token scope).$collab_note"
fi

# Firing conditions (CMR#1034): (a) the AUTHORITATIVE signal at/above
# threshold, (b) the fail-safe escalation below (unchanged). The advisory
# email count can never contribute to `fired`.
fired=0
if [[ "$collab_status" == "ok" && "$collab_count" -ge "$THRESHOLD" ]]; then
  echo "scale-tripwire: TRIPWIRE FIRED — human GitHub accounts with access ($collab_count) >= threshold ($THRESHOLD)." >&2
  echo "scale-tripwire: solo-dev merge posture (ADR-0020/ADR-0030, controller/auto-merge.sh) is STALE." >&2
  echo "scale-tripwire: re-review the GR-4/GR-14 carve-out eligibility list and re-tighten the merge gate NOW." >&2
  fired=1
fi
if [[ "$collab_status" == "unreachable" ]]; then
  if [[ "$MODE" != "strict" && "${SCALE_TRIPWIRE_GH_OPTIONAL:-0}" == "1" ]]; then
    echo "scale-tripwire: WARN — GitHub collaborators signal UNREACHABLE; SCALE_TRIPWIRE_GH_OPTIONAL=1 set, NOT escalating (operator opt-out)." >&2
  else
    if [[ "$MODE" == "strict" && "${SCALE_TRIPWIRE_GH_OPTIONAL:-0}" == "1" ]]; then
      echo "scale-tripwire: FORCE_STRICT — ignoring SCALE_TRIPWIRE_GH_OPTIONAL=1: an unresolved signal is never allowed to be opted out under FORCE_STRICT." >&2
    fi
    echo "scale-tripwire: TRIPWIRE FIRED (fail-safe escalation, NOT a proven threshold breach) — GitHub collaborators signal UNREACHABLE for repo $gh_repo.$collab_note" >&2
    echo "scale-tripwire: cannot prove solo scale via the GitHub API right now; failing toward 'assume collaborative' (gates ON) per ADR-0030 follow-up (#627)." >&2
    echo "scale-tripwire: set SCALE_TRIPWIRE_GH_OPTIONAL=1 to opt this environment out if you have independently confirmed solo scale (ignored under FORCE_STRICT)." >&2
    fired=1
  fi
fi

if [[ "$fired" -eq 1 ]]; then
  exit 1
fi

exit 0
