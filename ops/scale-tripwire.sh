#!/usr/bin/env bash
# ============================================================================
# ops/scale-tripwire.sh — scale-up tripwire for the solo-dev merge posture
# (Refs #372, docs/decision-records/ADR-0020-solo-dev-merge-posture.md).
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
# This script counts DISTINCT contributor identities from two independent
# signals and FAILS LOUD (non-zero exit + clear message on stdout AND stderr)
# the moment either count reaches the threshold. It never silently no-ops:
# below threshold it still prints the count, so the trend is visible in
# `make verify` output every run, not just when it fires.
#
# Signals counted:
#   1. git commit author emails in THIS repo's history (`git log --format=%ae`)
#      — proxy for "how many distinct committers has CMR itself seen".
#   2. `role != hub AND onboarded=true` rows in channels/spokes.tsv — proxy
#      for "how many external parties CMR has actually taken over" (governed +
#      standards + channel), since each onboarded spoke/vendor is (or will
#      become) its own contributor identity once it opens PRs under CMR's
#      standards. Pending (onboarded=false) seed-list rows are excluded --
#      they are registry entries, not live contributors, and counting them
#      would fire on the seed list alone.
#   3. Distinct GitHub-account REPO COLLABORATORS via the GitHub API
#      (issue #627, ADR-0030 follow-up). kushin77's stated trigger is
#      literally "the github repo users/contributers hits 10 to the actual
#      repo itself" -- signal 1 approximates that with git commit-author
#      EMAILS, which both over-counts (one human running several git
#      identities/agent tool identities -- this repo alone has 3 for one
#      human) and under-counts (a collaborator with granted push access who
#      has not committed yet). Deliberately uses
#      `GET /repos/{owner}/{repo}/collaborators` (granted repo ACCESS,
#      login-based) rather than `/contributors` (commit AUTHORSHIP, which
#      has signal 1's exact bot/multi-identity inflation problem -- verified
#      live on this repo while building this: /contributors returns
#      kushin77 + dependabot[bot] = 2, /collaborators returns kushin77 alone
#      = 1). This is signal 3, ADDED alongside 1 and 2, never a replacement
#      -- ADR-0020/ADR-0026/ADR-0030 all cite the existing threshold
#      semantics and this script stays the single source of truth for it.
#
# Threshold: 10 (the user's own stated number, docs/decision-records/ADR-0020).
# Crossing it on ANY signal means: re-tighten controller/auto-merge.sh's
# self-identity list / re-review whether the GR-4 carve-out is now live for
# real traffic, not just fixtures.
#
# Signal 3 / offline behavior (fail-safe direction, explicit): this script
# must work without network (it's consulted by merge-time logic,
# controller/auto-merge.sh:173, which treats ANY non-zero exit here as
# "tripwire fired -> force collaborative posture"). When the GitHub API is
# unreachable (no `gh`, no auth, network down, or a read-only token 403ing
# on the collaborators endpoint, which requires push-level access to read),
# signal 3 FAILS LOUD AND FIRES by default -- i.e. treats "can't prove we're
# still solo" as "assume collaborative", the safe direction, never a silent
# pass. This is a deliberate escalation, distinct in its message from a real
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
#                   tripwire fire MORE, never less: it (a) counts EVERY
#                   registered non-hub party in spokes.tsv, not only the
#                   onboarded ones (AUTO deliberately excludes pending seed
#                   rows as registry noise; STRICT refuses to assume a
#                   registered party will stay passive), and (b) makes the
#                   signal-3 API-unreachable escalation unconditional by
#                   ignoring the SCALE_TRIPWIRE_GH_OPTIONAL operator opt-out.
#                   Set with `--mode FORCE_STRICT` or SCALE_TRIPWIRE_MODE.
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
#   SCALE_TRIPWIRE_SPOKES_FILE      path to a spokes.tsv-shaped file to use
#                                    INSTEAD of channels/spokes.tsv.
#   SCALE_TRIPWIRE_COLLAB_FILE      path to a file of GitHub logins (one per
#                                    line) to use INSTEAD of calling
#                                    `gh api repos/{repo}/collaborators`.
#   SCALE_TRIPWIRE_REPO             "owner/repo" to query (default
#                                    kushin77/CMR).
#   SCALE_TRIPWIRE_GH               gh binary override (default: `gh` on
#                                    PATH).
#   SCALE_TRIPWIRE_GH_OPTIONAL      "1" -- do NOT fire signal 3 merely
#                                    because the API was unreachable (still
#                                    warns loudly). Default unset (fires).
#                                    Ignored (cannot opt out) under
#                                    FORCE_STRICT -- see MODES above.
#   SCALE_TRIPWIRE_MODE             AUTO (default) | FORCE_STRICT. Same knob
#                                    as --mode; the flag wins.
#
# Exit: 0 below threshold on all signals · 1 threshold crossed OR signal 3
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

Counts distinct contributor identities (git commit author emails; spokes.tsv
non-hub rows; GitHub repo collaborators) and fails loudly if any count reaches
the threshold (default 10) -- signal to re-tighten the solo-dev auto-merge
posture.

Modes: AUTO (default, today's behaviour) or FORCE_STRICT (strictly tightening:
counts non-onboarded registered parties too, and cannot opt out of the signal-3
unreachable escalation). A permissive mode is deliberately NOT supported
(ADR-0047 D4) and is rejected with exit 2.
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

# Signal 1: distinct git commit author emails.
if [[ -n "${SCALE_TRIPWIRE_GIT_LOG_FILE:-}" ]]; then
  committer_count="$(sort -u "$SCALE_TRIPWIRE_GIT_LOG_FILE" | sed '/^$/d' | wc -l | tr -d ' ')"
else
  committer_count="$(git -C "$ROOT" log --format='%ae' 2>/dev/null | sort -u | sed '/^$/d' | wc -l | tr -d ' ')"
fi

# Signal 2 (ADR-0053): REMOVED as a counted signal. It used to count every
# ONBOARDED non-hub row in spokes.tsv, but that conflates "repos this owner
# governs" with "people who commit to us" -- a spokes.tsv row for a
# self-owned vendored module or harvest source is not a contributor
# identity. Real headcount is carried by signal 1 (git-committer emails) and
# signal 3 (GitHub repo collaborators). spoke_count is kept at 0 (never
# fires) so downstream reporting/output shape is unchanged; see ADR-0053 for
# the rationale and ADR-0020/ADR-0027 for the threshold this preserves.
spoke_count=0

# Signal 3: distinct GitHub-account repo collaborators (issue #627).
collab_status="ok"
collab_count=0
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
  collab_count="$(sort -u "$SCALE_TRIPWIRE_COLLAB_FILE" | sed '/^$/d' | wc -l | tr -d ' ')"
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
    collab_count="$(printf '%s\n' "$collab_raw" | sort -u | sed '/^$/d' | wc -l | tr -d ' ')"
  fi
fi

echo "scale-tripwire: mode = $MODE_LABEL (AUTO default · FORCE_STRICT only ever tightens)"
echo "scale-tripwire: distinct git-committer identities  = $committer_count (threshold $THRESHOLD)"
echo "scale-tripwire: signal 2 (onboarded non-hub spokes) REMOVED per ADR-0053 — registry size is not contributor count; see signal 1/3 above."
if [[ "$collab_status" == "ok" ]]; then
  echo "scale-tripwire: distinct GitHub repo collaborators = $collab_count (threshold $THRESHOLD, repo $gh_repo)"
else
  echo "scale-tripwire: distinct GitHub repo collaborators = UNREACHABLE (repo $gh_repo, gh='$gh_bin_st') — could not query the GitHub API (no gh/no auth/network down/insufficient token scope).$collab_note"
fi

fired=0
if [[ "$committer_count" -ge "$THRESHOLD" ]]; then
  echo "scale-tripwire: TRIPWIRE FIRED — git-committer identities ($committer_count) >= threshold ($THRESHOLD)." >&2
  echo "scale-tripwire: solo-dev merge posture (ADR-0020, controller/auto-merge.sh) is STALE." >&2
  echo "scale-tripwire: re-review the GR-4 carve-out eligibility list and re-tighten the merge gate NOW." >&2
  fired=1
fi
if [[ "$collab_status" == "ok" && "$collab_count" -ge "$THRESHOLD" ]]; then
  echo "scale-tripwire: TRIPWIRE FIRED — GitHub repo collaborators ($collab_count) >= threshold ($THRESHOLD)." >&2
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
