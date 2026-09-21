#!/usr/bin/env bash
# check-gate-status.sh — prove the gate of record can become a REQUIRED check
# without GitHub Actions (ADR-0028, #803 P0-2).
#
# THE QUESTION THIS ANSWERS
# GitHub's *required status checks* are the only mechanism that makes a merge
# impossible without green evidence. Producing one needs a check-run or a commit
# status -- and GR-15 bans the Actions that would normally produce it. ADR-0028
# resolves that with a commit status posted by the code-native runner. This gate
# proves the mechanism is sound WITHOUT writing to the repository.
#
# WHY IT IS PROVOKED RATHER THAN ASSERTED
# The mapping rc -> status is the whole control. Three ways it can be a formality:
#
#   1. it always returns `success`            -> a red gate merges
#   2. CANNOT-ASSESS collapses into `success` -> a false green (the #739 class:
#      an unreadable HEAD that read back as `healthy`)
#   3. an unknown rc is silently defaulted    -> a control that cannot fail
#
# So the offline provocation asserts all three: every outcome maps, the three
# outcomes stay DISTINGUISHABLE, and an unknown outcome is REFUSED. It drives
# the same mapper (scripts/gate-status-map.py) the poster uses.
#
# The live half is a read-back, and an unobservable read-back is CANNOT-ASSESS
# (exit 2) -- never a pass.
#
# THE HALF THAT WAS MISSING (#1357, measured 2026-09-18)
# Everything above proves the poster's MACHINERY: the two names agree and the
# mapping is sound. None of it proves anything PRODUCES the status. So this gate
# was green on a repository whose required context had no producer at all: branch
# protection required `ao/gate-of-record`, no path that makes the gate-of-record
# verdict posted it, every PR head read BLOCKED, and merges went through only
# because `enforce_admins` is false -- an admin bypass. A required context whose
# producer does not exist is not a control; it is a bypass generator, and the
# gate that polices it must say so BY NAME instead of passing.
#
# So section 5 asks the producing question in TWO halves, and keeps them apart
# because they are answerable from different places (#1394):
#
#   * THE TREE -- does the context have a producer that is REACHED from the path
#     that MAKES the verdict (`infra/cloudbuild/*.yaml`)? Offline, deterministic,
#     and provable. No producer anywhere, or none on that path, is rc 1 BY NAME.
#   * THE LIVE STATE -- is that producer actually PRODUCING? The tree cannot
#     answer this even though it looks like it can: the only field it carries is
#     `infra/cloudbuild/verify-trigger.yaml`'s `disabled:`, and this repository
#     PINS that `true` by policy (scripts/check-cloudbuild.sh requires
#     `disabled: true` on the import stub, GR-5), so a probe that reads it as
#     live state names a condition the tree can never falsify. Measured
#     2026-09-19: the LIVE trigger is ENABLED and the context IS posted, while
#     that same probe answered `CANNOT-ASSESS REQUIRED-BUT-GATED-OFF` -- rc 2,
#     forever, on a healthy repository. A gate that reports a named condition
#     from a stale source of truth is not a control, so the declared value is now
#     an OBSERVATION and the verdict is read from `gh` -- the only half that
#     reality can contradict.
#
# The live half therefore answers one of three things, and never blurs them:
#
#   * the context IS being produced (observed on the commit under test, or on a
#     commit of the default branch inside the AGE window) -> rc 0;
#   * the AGE window WAS READ TO ITS END and holds no observation -> rc 1 BY
#     NAME (a finding about the window, not an inability to assess);
#   * the source could NOT be read, or the window could NOT BE COVERED -> rc 2
#     CANNOT-ASSESS naming the source -- never a green, and never a false named
#     verdict.
#
# THE BOUND ON THAT WINDOW IS AN AGE, NOT A COMMIT COUNT (#1460)
# It was `${AO_GATE_STATUS_WINDOW:-20}` default-branch COMMITS, which on this
# repository is not a recency bound at all: 100 commits are 41.4 hours at the
# measured landing rate, so a 20-commit window is ~8 hours, while the producer's
# own observations are 20.0h and 30.5h apart -- so a healthy producer read as
# `disabled gh-status-absent-in-last-20-commits` with the refusal "the producer
# is genuinely not producing", which is FALSE and sends the reader to the wrong
# remedy. The window is now an AGE (`AO_GATE_STATUS_MAX_AGE_DAYS`, default 7)
# that the walk must COVER before it may report the negative, and the verdict
# names the bound it used. See `live_producer_state` for the measurements.
#
# Exit codes: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# A CHECK THAT CANNOT NAME ITS OWN REFUSAL (#1407)
# The description the required context carries was a FIXED string per rc, so every
# red PR page read `make verify: FAIL` and the failing check's name existed only
# inside the build log. Section 3c provokes the opposite: `post --detail <text>`
# must put the name on the PR page, and must NOT be able to put a PASS there -- a
# detail that can manufacture a green is the #739 class this repository measures
# rather than assumes.
#
# Usage: bash scripts/check-gate-status.sh
#        bash scripts/check-gate-status.sh --producer-probe [--root DIR]
#          answer ONLY the producing question over DIR and exit 0/1/2, so
#          scripts/check-branch-protection.sh drives THIS implementation rather
#          than a second copy of the rule. The live question is asked only when
#          DIR IS this repository's own tree -- a fixture is not the repository
#          the live state describes -- and a foreign tree gets the declared
#          verdict, which is what the four #1357 fixtures assert.
#        bash scripts/check-gate-status.sh --producer-probe --root DIR \
#             --live-state enabled|disabled|unreadable [--live-source S]
#          the provocation seat: stand a live answer in for a fixture. REFUSED
#          on this repository's own tree, so the gate can never be told that its
#          own producer is enabled.
#
# Environment (the live read-back's bounds; every one of them is a BUDGET, and
# exhausting a budget without covering the window is CANNOT-ASSESS, never
# 'not producing'):
#   AO_GATE_STATUS_MAX_AGE_DAYS   the recency bound, in DAYS (default 7)
#   AO_GATE_STATUS_MAX_PAGES      the walk's page budget (default 8 = 800 commits)
#   AO_GATE_STATUS_MAX_PROBES     the REST fallback's probe budget (default 60)
#   AO_GATE_STATUS_WINDOW         RETIRED (#1460): a commit count cannot bound
#                                 'not producing' on a repository that lands
#                                 dozens of commits a day. Setting it prints a
#                                 note; it is not read.
#
# --- end of usage ---
set -u

# --- the probe seam, parsed BEFORE anything else -------------------------
# A provocation that re-implements the rule proves nothing about the rule that
# runs -- the lesson scripts/branch-protection-compare.py records for its
# comparator. So the producing question lives in ONE function here, section 5
# calls it for the repository, and every fixture in both gates calls the same
# function through `--producer-probe`.
probe_mode=0
root_override=""
live_state_override=""
live_source_override=""
while [ $# -gt 0 ]; do
  case "$1" in
    --producer-probe) probe_mode=1 ;;
    --root) root_override="${2:-}"; shift ;;
    # The PROVOCATION input for the live half (#1394). The live producer state
    # is the one input reality can contradict, so it cannot be asserted -- it is
    # READ, from `gh`, inside live_producer_state(). A fixture is not the
    # repository the live state describes, so its arms must be able to stand a
    # live answer in; this is that seat, and it is refused on this repository's
    # OWN tree (see the probe block), so no run of this gate can be told that
    # the producer is enabled.
    --live-state) live_state_override="${2:-}"; shift ;;
    --live-source) live_source_override="${2:-}"; shift ;;
    -h|--help) sed -n '2,/^# --- end of usage ---$/p' "$0"; exit 0 ;;
    *) : ;;
  esac
  shift
done

# The poster's name and its posting verb, assembled from fragments so that this
# checker's own source cannot be its own finding -- and matched on a line whose
# COMMENT has been stripped, because a comment cannot run: a YAML note reading
# "post it with scripts/gate-status.sh" would otherwise certify a producer that
# does not exist, which is the direction that fails OPEN.
fx_name="gate-status"".sh"
fx_verb="po""st"
fx_name_re="${fx_name//./[.]}"
fx_verb_re="(^|[^A-Za-z0-9_])${fx_verb}([^A-Za-z0-9_]|\$)"
# ADR-0028's declared name, consulted only when the poster itself is absent and
# cannot be asked (a fixture, or a tree where the poster was deleted).
fx_fallback_ctx="ao/gate-of-record"
# The paths that MAKE the gate-of-record verdict unattended. This is the runner
# the repo's own headers name (ADR-0028: the code-native runner, no Actions) and
# the one place a producer can be reached for a commit nobody drives by hand.
fx_verdict_dir="infra/cloudbuild"

# A TEST or a DOC is not a producer. A test that calls the poster proves the
# poster CAN be called; it says nothing about whether anything calls it when a
# verdict is made. Counting one would certify the defect away, so this is the
# vacuity guard the fixtures provoke.
#
# Neither is a CHECKER, and neither is the poster itself. A `check-*.sh` that
# plants a fixture naming the poster would otherwise certify its own premise:
# measured while writing this, the policing gates listed THEMSELVES among the
# "producers elsewhere", which is both false and -- because it can never be empty
# again -- a branch of this rule that could no longer fire. `scripts/
# check-shell-patterns.sh` records the same trap for its own source, one level
# over; the fix there is to fragment the shape, and the fix here is to say plainly
# that a checker is not a producer. The poster is excluded for the same reason in
# the other direction: counting a producer as its own producer is circular.
is_producer_path() {
  case "$1" in
    */tests/*|*/test/*|*/docs/*|*/.board/*|*/vendor/*|*/.research/*|*/.git/*) return 1 ;;
  esac
  case "$1" in
    *.md) return 1 ;;
  esac
  case "${1##*/}" in
    test_*|*_test.py|*_test.sh) return 1 ;;
    check-*.sh|check-*.py|gate-status.sh) return 1 ;;
  esac
  return 0
}

# producer_in <file> -- 0 when a NON-COMMENT line invokes the poster.
producer_in() {
  awk -v name="$fx_name_re" -v verb="$fx_verb_re" '
    { line = $0; sub(/#.*/, "", line)
      if (line ~ name && line ~ verb) { found = 1; exit } }
    END { exit(found ? 0 : 1) }' "$1"
}

# verdict_paths <root> <verdict-dir> -- the build config(s) the repo ITSELF names as
# the path that evaluates a PULL REQUEST, one per line as "<path> <enabled|disabled>".
#
# This is the honest definition of "where the PR verdict is made", and it is
# DERIVED rather than invented: a required status check gates a pull request, so
# the path that must produce it is the one a `repositoryEventConfig.pullRequest`
# trigger names in its `filename:`. Two consequences that matter. A build config
# that runs `make verify` for another reason (a rollout promotion) is NOT a PR
# verdict path and is not asked for a producer -- a rule that demanded one would
# red on a healthy repository, and a gate that fires on its own over-broad match
# trains the operator to ignore it. And a trigger that ships flag-gated OFF is
# reported as such rather than silently accepted: a required check whose only
# path is OFF produces nothing, which is the #724 class this repo has already
# been bitten by once.
verdict_paths() {
  python3 - "$1" "$2" <<'PY'
import glob
import os
import sys

try:
    import yaml
except ImportError:
    sys.exit(2)

root, vdir_name = sys.argv[1], sys.argv[2]
vdir = os.path.join(root, vdir_name)
found = False
for trig in sorted(glob.glob(os.path.join(vdir, "*.yaml")) + glob.glob(os.path.join(vdir, "*.yml"))):
    try:
        doc = yaml.safe_load(open(trig)) or {}
    except OSError:
        continue
    except yaml.YAMLError:
        sys.exit(2)
    if not isinstance(doc, dict):
        continue
    event = doc.get("repositoryEventConfig") or {}
    if not event.get("pullRequest"):
        continue
    name = doc.get("filename") or ""
    if not name:
        sys.exit(2)
    found = True
    print("%s %s" % (name, "disabled" if doc.get("disabled") else "enabled"))
if not found:
    sys.exit(3)
PY
}

# reaches_producer <root> <build-config> -- 0 when that config, or a shell script
# its own steps name, invokes the poster. One hop on purpose: a producer two
# scripts deep is not provably reachable from the path that makes the verdict, and
# this gate refuses rather than guessing in the direction that passes.
reaches_producer() {
  rp="$1/$2"
  [ -f "$rp" ] || return 1
  producer_in "$rp" && return 0
  for tok in $(grep -oE '[A-Za-z0-9_./-]+[.]sh' "$rp" 2>/dev/null); do
    case "$tok" in
      /*) cand="$tok" ;;
      *) cand="$1/${tok#./}" ;;
    esac
    [ -f "$cand" ] || continue
    producer_in "$cand" && return 0
  done
  return 1
}

# live_producer_state <root> <context> -- the ONE input reality can contradict:
# is the producer actually producing? Printed as "<state> <source>" on one line.
#
#   enabled    the required context was OBSERVED -- on the commit under test, or
#              on a commit of the default branch inside the AGE window
#   disabled   the AGE window was read TO ITS END and holds no observation: the
#              producer is not producing
#   unreadable the source could not be read, or the window could not be COVERED;
#              a caller must never turn this into either verdict
#
# THE RECENCY BOUND IS AN AGE, NOT A COMMIT COUNT (#1460, measured 2026-09-19)
# The bound used to be `${AO_GATE_STATUS_WINDOW:-20}` COMMITS of the default
# branch, and on a repository that lands as heavily as this one a commit count
# is not a recency bound at all. Measured at `origin/master` = a7518cff: 100
# commits of history are 41.4 hours (about 58 commits/day), so a 20-commit
# window is roughly EIGHT HOURS -- while over the whole 670-commit / 239-day
# history the same 20 commits are about SEVEN DAYS (2.8/day). The same number
# meant "eight hours" or "a week" depending on the week, which is why it cannot
# be the instrument. And the producer's own cadence is measured at gaps of 20.0h
# and 30.5h between observations -- 4 observations in that whole history, the
# last at 66fa3d4d, with a 134-commit gap between two of them -- so the window
# converted "the producer ran 30 commits ago" into "the producer is not
# producing" (REQUIRED-BUT-UNOBSERVED, rc 1) on a repository whose producer was
# demonstrably posting. A finding about the WINDOW, reported as a finding about
# the PRODUCER, sent the reader to the wrong remedy; that is the defect this
# function now refuses to make.
#
# So the walk is bounded by TIME (`AO_GATE_STATUS_MAX_AGE_DAYS`, default 7 --
# 5.5x the largest measured gap, chosen so that a weekend plus a quiet stretch
# cannot manufacture a refusal) and it must COVER that window before it may say
# `disabled`. A walk that ran out of requests, or that a request failed inside,
# is CANNOT-ASSESS (`gh-status-window-incomplete`), never "not producing": that
# guard is the whole reason the false negative cannot come back through the
# bound. The verdict NAMES the bound it used
# (`gh-status-absent-in-last-<D>-days`), because "no producer exists" and "no
# RECENT observation" need different remedies: the tree half of this check
# answers the first, and this half answers only the second.
#
# The commit under test is probed FIRST and can only ever produce a POSITIVE: a
# commit that carries the context IS an observation of the producer, while a
# commit the API does not know -- a lane's not-yet-pushed head -- is not
# evidence about the producer either way. So a failed HEAD probe cannot
# manufacture the negative, and cannot hide it: the walk decides that.
#
# WHY ONE REQUEST PER 100 COMMITS
# `history(since:)` returns the commits inside the window AND each commit's
# `statusCheckRollup` in the SAME request, so covering the window costs 1
# rate-limit point and ~1.0s per 100 commits (measured), against the 0.34s PER
# COMMIT the statuses endpoint costs. `gh api graphql` is therefore the primary
# instrument; if it cannot answer AT ALL, the walk is redone with the REST
# instrument (`commits?since=`, one statuses probe per commit, bounded by
# `AO_GATE_STATUS_MAX_PROBES`) rather than reporting an inability to assess on a
# venue where the older transport is the one that works.
#
# WHY THE LIVE HALF IS A READ-BACK AT ALL, measured 2026-09-19 (#1394): the LIVE trigger is ENABLED and
# the context IS posted -- `gcloud builds triggers describe control-plane-verify`
# answers `control-plane-verify  infra/cloudbuild/verify.yaml` (an empty
# `disabled` field, i.e. enabled), and `ao/gate-of-record` is observed on
# 2adbe48b (twice), 83ff32db and 0dbb0e68 -- while the tree's own
# `infra/cloudbuild/verify-trigger.yaml` declares `disabled: true`, pinned there
# BY THIS REPOSITORY'S POLICY: scripts/check-cloudbuild.sh calls
# `check_trigger "$cb_dir/verify-trigger.yaml" _ENABLE_VERIFY`, which requires
# `disabled is not True -> "must ship disabled: true (GR-5)"`. So the tree
# question "is this path gated off?" can never be falsified from the tree, and a
# probe that reads it as LIVE state names a condition the world contradicts --
# rc 2 forever, on a repository that is healthy. The declared value stays an
# OBSERVATION; the verdict is taken here, from the thing that can be wrong.
lw_query='query($owner: String!, $name: String!, $ref: String!, $n: Int!, $after: String, $since: GitTimestamp) {
  repository(owner: $owner, name: $name) {
    object(expression: $ref) {
      ... on Commit {
        history(first: $n, after: $after, since: $since) {
          pageInfo { hasNextPage endCursor }
          nodes {
            oid
            statusCheckRollup {
              contexts(first: 100) {
                nodes {
                  __typename
                  ... on StatusContext { context }
                  ... on CheckRun { name }
                }
              }
            }
          }
        }
      }
    }
  }
}'

# live_walk_graphql <slug> <ref> <since> <cursor> <n> <ctx> -- one page of the
# walk: "node <sha> <0|1>" per commit, then "next <cursor>|<none>|<unusable>".
# rc 1 when the request did not answer. A page that answered with commits but no
# observation, and said the window ENDS there, is a real answer and rc 0.
live_walk_graphql() {
  lwg_slug="$1"; lwg_ref="$2"; lwg_since="$3"; lwg_cursor="$4"; lwg_n="$5"; lwg_ctx="$6"
  if [ -n "$lwg_cursor" ]; then
    lwg_out="$(gh api graphql \
      -F owner="${lwg_slug%%/*}" -F name="${lwg_slug#*/}" -F ref="$lwg_ref" \
      -F n="$lwg_n" -F after="$lwg_cursor" -F since="$lwg_since" \
      -f query="$lw_query" 2>/dev/null)" || return 1
  else
    lwg_out="$(gh api graphql \
      -F owner="${lwg_slug%%/*}" -F name="${lwg_slug#*/}" -F ref="$lwg_ref" \
      -F n="$lwg_n" -F since="$lwg_since" \
      -f query="$lw_query" 2>/dev/null)" || return 1
  fi
  [ -n "$lwg_out" ] || return 1
  # The program travels in `-c` and the PAYLOAD travels on a pipe, so neither
  # can be mistaken for the other: `python3 - "$ctx" <<PY` would take its
  # PROGRAM from the heredoc and leave `sys.stdin` with nothing to parse, which
  # reads as "no observation" and exits 0 (measured, #1400).
  printf '%s' "$lwg_out" | python3 -c '
import json
import sys

ctx = sys.argv[1]
try:
    doc = json.load(sys.stdin)
    history = doc["data"]["repository"]["object"]["history"]
    nodes = history["nodes"]
    info = history["pageInfo"]
except (ValueError, KeyError, TypeError):
    sys.exit(3)
for node in nodes:
    rollup = node.get("statusCheckRollup") or {}
    seen = [c.get("context") or c.get("name")
            for c in (rollup.get("contexts") or {}).get("nodes") or []]
    print("node %s %d" % (node.get("oid") or "-", 1 if ctx in seen else 0))
end = info.get("endCursor")
if not info.get("hasNextPage"):
    print("next none")
elif end:
    print("next %s" % end)
else:
    # More commits inside the window and no cursor to reach them with: the walk
    # cannot continue, and a walk that cannot continue must never be read as
    # "the window ended here".
    print("next unusable")
' "$lwg_ctx"
}

# live_walk_rest <slug> <ref> <since> <page> <n> <ctx> -- the same page over the
# older transport: the commit list inside the window, then ONE statuses probe
# per commit (which is why this instrument is bounded by
# AO_GATE_STATUS_MAX_PROBES and the GraphQL one is not). Printed as
# "node <sha> <0|1>", then "probes <k>", then "next <page>|<none>".
# rc 1 when a request did not answer: a page whose probes were cut short must
# never be read as "nothing observed".
live_walk_rest() {
  lwr_slug="$1"; lwr_ref="$2"; lwr_since="$3"; lwr_page="$4"; lwr_n="$5"; lwr_ctx="$6"
  lwr_shas="$(gh api \
    "repos/$lwr_slug/commits?sha=$lwr_ref&since=$lwr_since&per_page=$lwr_n&page=$lwr_page" \
    --jq '.[].sha' 2>/dev/null)" || return 1
  lwr_k=0
  for lwr_c in $lwr_shas; do
    lwr_k=$((lwr_k + 1))
    lwr_ctxs="$(gh api "repos/$lwr_slug/commits/$lwr_c/statuses" \
      --jq '[.[].context]|join(",")' 2>/dev/null)" || return 1
    case ",$lwr_ctxs," in
      *",$lwr_ctx,"*) printf 'node %s 1\n' "$lwr_c" ;;
      *) printf 'node %s 0\n' "$lwr_c" ;;
    esac
  done
  printf 'probes %d\n' "$lwr_k"
  if [ "$lwr_k" -ge "$lwr_n" ]; then
    printf 'next %d\n' "$((lwr_page + 1))"
  else
    printf 'next none\n'
  fi
  return 0
}

live_producer_state() {
  lroot="$1"
  lctx="$2"
  if ! command -v gh >/dev/null 2>&1; then
    printf 'unreadable gh-not-installed\n'
    return 0
  fi
  if ! gh auth status >/dev/null 2>&1; then
    printf 'unreadable gh-unauthenticated\n'
    return 0
  fi
  lslugref="$(gh repo view --json nameWithOwner,defaultBranchRef \
    --jq '.nameWithOwner + " " + (.defaultBranchRef.name // "")' 2>/dev/null)"
  lslug=""
  lref=""
  case "$lslugref" in
    *' '*) lslug="${lslugref%% *}"; lref="${lslugref#* }" ;;
  esac
  if [ -z "$lslug" ] || [ -z "$lref" ] || [ "$lref" = "null" ]; then
    printf 'unreadable gh-repo-unresolvable\n'
    return 0
  fi
  ldays="${AO_GATE_STATUS_MAX_AGE_DAYS:-7}"
  case "$ldays" in ''|*[!0-9]*) ldays=7 ;; esac
  [ "$ldays" -lt 1 ] && ldays=1
  [ "$ldays" -gt 90 ] && ldays=90
  lmaxpages="${AO_GATE_STATUS_MAX_PAGES:-8}"
  case "$lmaxpages" in ''|*[!0-9]*) lmaxpages=8 ;; esac
  [ "$lmaxpages" -lt 1 ] && lmaxpages=1
  [ "$lmaxpages" -gt 40 ] && lmaxpages=40
  lmaxprobes="${AO_GATE_STATUS_MAX_PROBES:-60}"
  case "$lmaxprobes" in ''|*[!0-9]*) lmaxprobes=60 ;; esac
  [ "$lmaxprobes" -lt 1 ] && lmaxprobes=1
  lcut="$(python3 -c 'import datetime, sys
print((datetime.datetime.now(datetime.timezone.utc)
       - datetime.timedelta(days=int(sys.argv[1]))).strftime("%Y-%m-%dT%H:%M:%SZ"))' "$ldays" 2>/dev/null)"
  if [ -z "$lcut" ]; then
    printf 'unreadable gh-window-uncomputable\n'
    return 0
  fi
  # The retired seam is NAMED, never silently ignored: an operator who widened
  # the old commit window to make this check pass must be told that what they
  # set is no longer read, and what to set instead.
  if [ -n "${AO_GATE_STATUS_WINDOW:-}" ]; then
    echo "check-gate-status: note — AO_GATE_STATUS_WINDOW is RETIRED (#1460) and is not read: a commit count cannot bound 'not producing' on a repository that lands dozens of commits a day (measured: 100 commits = 41.4h here, so the old 20-commit window was ~8 hours while the producer's own observations are 20.0-30.5h apart). The read-back is bounded by AO_GATE_STATUS_MAX_AGE_DAYS=$ldays instead." >&2
  fi
  # THE COMMIT UNDER TEST -- a POSITIVE-ONLY probe (#1460). A commit that carries
  # the context IS an observation of the producer, so this can only ever answer
  # `enabled`. A commit the API does not know (a lane's not-yet-pushed head) is
  # not evidence about the producer either way, so a failure here is NOT counted
  # as "not producing" and cannot hide one either: the walk decides the negative.
  lhead="$(git -C "$lroot" rev-parse HEAD 2>/dev/null)"
  if [ -n "$lhead" ]; then
    lhead_ctxs="$(gh api "repos/$lslug/commits/$lhead/statuses" \
      --jq '[.[].context]|join(",")' 2>/dev/null)"
    case ",$lhead_ctxs," in
      *",$lctx,"*) printf 'enabled gh-status-observed\n'; return 0 ;;
    esac
  fi
  # THE WALK. Bounded by TIME, and it must COVER the window before it may report
  # the negative; `AO_GATE_STATUS_MAX_PAGES` and `AO_GATE_STATUS_MAX_PROBES` are
  # request budgets, and exhausting one WITHOUT covering the window is an
  # inability to assess, never an observation of absence.
  for linstr in graphql rest; do
    lread=0
    lcovered=0
    lprobes=0
    lfound=0
    lstopped=""
    lpage=1
    lcursor=""
    while [ "$lpage" -le "$lmaxpages" ]; do
      if [ "$linstr" = "graphql" ]; then
        lpage_out="$(live_walk_graphql "$lslug" "$lref" "$lcut" "$lcursor" 100 "$lctx")"
      else
        lpage_out="$(live_walk_rest "$lslug" "$lref" "$lcut" "$lpage" 100 "$lctx")"
      fi
      lprc=$?
      if [ "$lprc" -ne 0 ]; then
        lstopped="request-failed"
        break
      fi
      lread=$((lread + 1))
      lnext=""
      while read -r lkind lfield1 lfield2; do
        case "$lkind" in
          node) [ "$lfield2" = "1" ] && lfound=1 ;;
          probes) lprobes=$((lprobes + lfield1)) ;;
          next) lnext="$lfield1" ;;
        esac
      done <<< "$lpage_out"
      [ "$lfound" -eq 1 ] && break
      if [ -z "$lnext" ] || [ "$lnext" = "none" ]; then
        lcovered=1
        break
      fi
      if [ "$lnext" = "unusable" ]; then
        lstopped="cursor-unusable"
        break
      fi
      if [ "$lprobes" -ge "$lmaxprobes" ]; then
        lstopped="probe-budget"
        break
      fi
      lcursor="$lnext"
      lpage=$((lpage + 1))
    done
    if [ "$lfound" -eq 1 ]; then
      printf 'enabled gh-status-observed\n'
      return 0
    fi
    if [ "$lread" -eq 0 ]; then
      # This instrument could not answer AT ALL, so the walk is redone with the
      # other one rather than reporting an inability to assess on a venue where
      # the older transport is the one that works.
      continue
    fi
    if [ "$lcovered" -eq 1 ]; then
      printf 'disabled gh-status-absent-in-last-%s-days\n' "$ldays"
      return 0
    fi
    if [ "$lstopped" = "probe-budget" ]; then
      printf 'unreadable gh-status-probe-budget-exhausted\n'
      return 0
    fi
    printf 'unreadable gh-status-window-incomplete\n'
    return 0
  done
  printf 'unreadable gh-statuses-unreadable\n'
  return 0
}

# probe_line <required> <verdict> -- the machine-readable first line. `live=` is
# added by #1394 so the caller can act on the live state without re-reading it
# (and without the two halves of the rule being able to disagree).
probe_line() {
  printf 'probe: required=%s context=%s verdict=%s live=%s live_source=%s\n' \
    "$1" "$pctx" "$2" "${live_state:--}" "${live_source:--}"
}

# producer_probe <root> [live-mode] -- the producing question, over one tree.
#   0 the REQUIRED context has a producer on every path that makes the verdict
#     (or nothing is REQUIRED, so no producer is owed)
#   1 REQUIRED-BUT-UNPRODUCED -- a producer path is absent, and the refusal names
#     the context and the missing producer
#   2 CANNOT-ASSESS -- the requirement or the verdict path could not be located,
#     the live producer state could not be read, or (live) nothing is producing
#
# `live-mode` is one of:
#   ask       read the live state from live_producer_state() -- the real answer.
#             On a FOREIGN tree this is the provocation seat for the READ-BACK
#             ITSELF (#1460): the API on PATH may be stubbed, and the walk that
#             is provoked is still the real one. Nothing is asserted here, so
#             this seat cannot tell the gate that its producer is enabled
#   skip      ask nothing: the DECLARED state stands as the verdict. This is what
#             a foreign tree gets: a fixture is not the repository the live state
#             describes, so there is no live fact about it to read (#1394)
#   enabled|disabled|unreadable
#             the provocation seat -- a fixture's live answer, STOOD IN for the
#             walk, for the arms that must keep asserting a state the walk did
#             not read
producer_probe() {
  r="${1:-}"
  lmode="${2:-skip}"
  if [ -z "$r" ] || [ ! -d "$r" ]; then
    printf 'probe: required=unknown context=unknown verdict=unreadable live=-\n'
    echo "check-gate-status: CANNOT-ASSESS — there is no tree to probe: '${r}'" >&2
    return 2
  fi
  pol="$r/governance/platform/branch-protection.yaml"
  if [ ! -f "$pol" ]; then
    printf 'probe: required=unknown context=unknown verdict=unreadable live=-\n'
    echo "check-gate-status: CANNOT-ASSESS — the declared policy is missing: $pol, so what is REQUIRED cannot be read" >&2
    return 2
  fi
  # Both declarations are read: `protection.required_status_checks` is what
  # `branch-protection.sh apply` PUTs to GitHub, and `required_status_contexts`
  # is ADR-0028's name for the same control. The UNION is the honest input --
  # requiring it in either place makes it required in fact.
  required_ctxs="$(python3 - "$pol" <<'PY'
import sys
try:
    import yaml
except ImportError:
    sys.exit(2)
try:
    policy = yaml.safe_load(open(sys.argv[1])) or {}
except OSError:
    sys.exit(2)
protection = policy.get("protection") or {}
checks = protection.get("required_status_checks") or {}
contexts = list(checks.get("contexts") or [])
named = policy.get("required_status_contexts") or []
if isinstance(named, str):
    named = [named]
for c in named:
    if c not in contexts:
        contexts.append(c)
print(" ".join(str(c) for c in contexts))
PY
)" || required_ctxs=""
  if [ -z "$required_ctxs" ] && ! grep -q 'required_status_checks' "$pol"; then
    printf 'probe: required=unknown context=unknown verdict=unreadable live=-\n'
    echo "check-gate-status: CANNOT-ASSESS — $pol could not be parsed, so what is REQUIRED cannot be read (PyYAML missing or the file is unreadable)" >&2
    return 2
  fi

  # The context is taken from the POSTER itself, so the probe cannot be pointed
  # at a different name than the one that is posted; ADR-0028's declared name is
  # the fallback only when the poster is not there to be asked.
  pctx=""
  if [ -f "$r/scripts/$fx_name" ]; then
    pctx="$(grep -oE 'CONTEXT="\$\{AO_GATE_CONTEXT:-[^}]+\}"' "$r/scripts/$fx_name" 2>/dev/null | sed 's/.*:-//;s/}"//')"
  fi
  [ -n "$pctx" ] || pctx="$fx_fallback_ctx"

  required=0
  case " $required_ctxs " in
    *" $pctx "*) required=1 ;;
  esac

  # THE LIVE HALF (#1394). Resolved here, once, from a source that is not the
  # tree, because the tree cannot answer this question and only pretends to.
  live_state=""
  live_source=""
  case "$lmode" in
    ask)
      lres="$(live_producer_state "$r" "$pctx")"
      live_state="${lres%% *}"
      live_source="${lres#* }" ;;
    skip) : ;;
    enabled|disabled|unreadable)
      live_state="$lmode"
      live_source="${3:-provocation-seam}" ;;
    *)
      probe_line unknown unreadable
      echo "check-gate-status: CANNOT-ASSESS — unknown live mode '$lmode'" >&2
      return 2 ;;
  esac

  # The producers that exist anywhere in the tree, and the paths the repo names
  # as the ones that make the PR verdict. Both are computed from the tree, so
  # neither can be told a story.
  sites="$(grep -rl --binary-files=without-match \
             --exclude-dir=.git --exclude-dir=vendor --exclude-dir=.research \
             --exclude-dir=.verify --exclude-dir=.board --exclude-dir=node_modules \
             -e "$fx_name_re" "$r" 2>/dev/null | while read -r f; do
               is_producer_path "$f" && producer_in "$f" && printf '%s\n' "${f#"$r"/}"
             done)"
  vlist="$(verdict_paths "$r" "$fx_verdict_dir")"
  vrc=$?

  if [ "$required" -eq 0 ]; then
    probe_line 0 not-required
    echo "  OK  no status context is REQUIRED by governance/platform/branch-protection.yaml, so no producer is owed and no merge is deadlocked by its absence"
    return 0
  fi
  if [ "$vrc" -ne 0 ] || [ -z "$vlist" ]; then
    probe_line 1 unlocatable
    echo "check-gate-status: CANNOT-ASSESS REQUIRED-BUT-UNLOCATABLE — '$pctx' is REQUIRED, but no path that evaluates a pull request could be located (no trigger under $fx_verdict_dir declares repositoryEventConfig.pullRequest with a filename, or the declarations could not be parsed), so the path that MAKES the PR verdict cannot be identified and no producer can be proven on it; that is never a pass" >&2
    return 2
  fi
  if [ -z "$sites" ]; then
    probe_line 1 unproduced
    echo "check-gate-status: FAIL REQUIRED-BUT-UNPRODUCED — the REQUIRED context '$pctx' has NO PRODUCER: no non-test, non-doc invocation of 'bash scripts/$fx_name $fx_verb' exists anywhere in this tree" >&2
    printf '    the PR verdict is made by: %s\n' "$(printf '%s' "$vlist" | cut -d' ' -f1 | tr '\n' ' ')" >&2
    return 1
  fi
  unproduced=""
  gated_off=""
  while read -r vp vstate; do
    [ -n "$vp" ] || continue
    if [ "$vstate" = "disabled" ]; then
      gated_off="$gated_off$vp
"
    fi
    reaches_producer "$r" "$vp" || unproduced="$unproduced$vp
"
  done <<< "$vlist"
  if [ -n "$unproduced" ]; then
    probe_line 1 unproduced
    echo "check-gate-status: FAIL REQUIRED-BUT-UNPRODUCED — the context '$pctx' is REQUIRED by governance/platform/branch-protection.yaml, but nothing posts it on the path that evaluates a pull request:" >&2
    echo "    required context : $pctx" >&2
    echo "    missing producer : no invocation of 'bash scripts/$fx_name $fx_verb' in" >&2
    printf '      %s\n' $unproduced >&2
    echo "    producers elsewhere in this tree (NOT on that path, so they cannot satisfy the check for a PR head):" >&2
    printf '      %s\n' $sites >&2
    if [ -n "$gated_off" ]; then
      echo "    and the path is flag-gated OFF as well, so it would produce nothing even with a producer:" >&2
      printf '      %s\n' $gated_off >&2
    fi
    echo "    a required check that nothing produces where the PR verdict is made is not a control: every PR head stays BLOCKED and a merge can only proceed through the admin bypass (enforce_admins=false)" >&2
    return 1
  fi
  if [ -n "$gated_off" ]; then
    # The PR verdict path ships DECLARED flag-gated OFF. Whether that is the LIVE
    # truth is a different question, and only the live half can answer it: the
    # declaration is pinned by this repository's own policy (check-cloudbuild.sh
    # requires `disabled: true` on the import stub, GR-5), so it can never be
    # falsified from the tree, and reading it as live state is the #1394 defect.
    case "$live_state" in
      enabled)
        probe_line 1 produced
        echo "  OK  the REQUIRED context '$pctx' has a producer on the path that evaluates a pull request, and the LIVE producer state is '$live_source' — the context IS being produced"
        printf '      OBSERVATION, not a verdict: %s declares the PR verdict path flag-gated OFF.\n' "$gated_off"
        echo "      That declaration is pinned true by this repository's own policy (scripts/check-cloudbuild.sh, GR-5) and is therefore unfalsifiable from the tree; the verdict is taken from the live read-back instead (#1394)."
        return 0 ;;
      disabled)
        # The window WAS covered and it says nothing has been observed. That is
        # not an inability to assess -- it is a FINDING, so it is rc 1 like
        # every other unproduced-context refusal, not rc 2. (rc 2 here would
        # also be the wrong shape for the skip budget: a skip means "this venue
        # cannot answer", and this venue just did.) The wording keeps the two
        # remedies apart (#1460): the tree half above answers "does a producer
        # exist" and this half answers only "has it been OBSERVED in the
        # window", so it says which window and never claims the producer is
        # absent.
        probe_line 1 unobserved
        echo "check-gate-status: FAIL REQUIRED-BUT-UNOBSERVED — '$pctx' is REQUIRED and a producer exists on the PR verdict path, and the live producer state WAS read ('$live_source'), but NO COMMIT OF THE DEFAULT BRANCH INSIDE THAT WINDOW carries the context: the producer has not been OBSERVED in the window, which is a finding about the WINDOW and not a claim that no producer exists (the tree half above proved one is reached where the PR verdict is made). The producer's most recent observation is older than the window, or there is none; until it posts again -- or the window is widened deliberately, with AO_GATE_STATUS_MAX_AGE_DAYS -- the requirement is not satisfiable, and a merge would proceed only through the admin bypass (enforce_admins=false). The window is the verdict's own bound, and it is named in the state above." >&2
        printf '      declared flag-gated OFF as well: %s\n' $gated_off >&2
        return 1 ;;
      unreadable)
        probe_line 1 live-unreadable
        echo "check-gate-status: CANNOT-ASSESS LIVE-PRODUCER-UNREADABLE — '$pctx' is REQUIRED and a producer exists on the PR verdict path, but the live producer state could NOT be read from its source ('$live_source'): neither 'it is producing' nor 'it is gated off' can be claimed, because the source — not the tree — is what decides this, and an unreadable source is never a pass" >&2
        printf '      the declaration this leaves unresolved: %s\n' $gated_off >&2
        return 2 ;;
      *)
        probe_line 1 gated-off
        echo "check-gate-status: CANNOT-ASSESS REQUIRED-BUT-GATED-OFF — '$pctx' is REQUIRED and its producer exists on the PR path, but that trigger ships flag-gated OFF, so nothing produces the check until it is promoted out of band; the requirement is not satisfiable yet, which is never a pass:" >&2
        printf '      %s\n' $gated_off >&2
        echo "      (no live question was asked here — the tree under probe is not the repository the live state describes, so the declaration stands as the only available observation)" >&2
        return 2 ;;
    esac
  fi
  probe_line 1 produced
  echo "  OK  the REQUIRED context '$pctx' has a producer on the path that evaluates a pull request"
  return 0
}

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
own_root="$(find_repo_root)"
root="$own_root"
if [ -n "$root_override" ]; then
  root="$(cd "$root_override" 2>/dev/null && pwd)" || {
    echo "check-gate-status: CANNOT-ASSESS — --root is not a readable directory: $root_override" >&2
    exit 2
  }
fi
if [ "$probe_mode" -eq 1 ]; then
  # THE SAME-TREE RULE (#1394). The live producer state is a fact about THIS
  # repository, so the live question is asked only when the tree under probe IS
  # this repository's tree. A fixture is not: there is no trigger of its own and
  # no live poster, so asking about it would be a category error -- and it would
  # have silently INVERTED the four provocation arms in
  # scripts/check-branch-protection.sh, whose `gatedoff` fixture must keep
  # answering the declared-state verdict. So a foreign tree gets `skip`, and the
  # provocation seat (`--live-state`) supplies a live answer explicitly.
  if [ "$root" = "$own_root" ]; then
    # `ask` IS this tree's behaviour, so being explicit about the READ is not an
    # assertion and is accepted; every asserted value is REFUSED by name.
    case "$live_state_override" in
      ''|ask) producer_probe "$root" ask ;;
      *)
        echo "check-gate-status: CANNOT-ASSESS — --live-state is REFUSED on this repository's OWN tree: the live producer state is READ here, never asserted (#1394)" >&2
        exit 2 ;;
    esac
  else
    case "$live_state_override" in
      '') producer_probe "$root" skip ;;
      ask) producer_probe "$root" ask ;;
      enabled|disabled|unreadable)
        producer_probe "$root" "$live_state_override" "$live_source_override" ;;
      *) echo "check-gate-status: CANNOT-ASSESS — --live-state must be enabled, disabled or unreadable (got '$live_state_override')" >&2
         exit 2 ;;
    esac
  fi
  exit $?
fi
if [ -n "$live_state_override" ]; then
  echo "check-gate-status: CANNOT-ASSESS — --live-state requires --producer-probe: the live state is read on this repository's own tree, never asserted" >&2
  exit 2
fi
cd "$root" || exit 2

MAPPER="scripts/gate-status-map.py"
POSTER="scripts/gate-status.sh"
POLICY="governance/platform/branch-protection.yaml"
fail=0
cannot_assess=0

command -v python3 >/dev/null 2>&1 || { echo "check-gate-status: CANNOT-ASSESS — python3 not found" >&2; exit 2; }

# 1. STRUCTURAL — both halves exist, and the context has ONE name.
[ -f "$MAPPER" ] || { echo "check-gate-status: FAIL — mapper missing: $MAPPER" >&2; exit 1; }
[ -f "$POSTER" ] || { echo "check-gate-status: FAIL — poster missing: $POSTER" >&2; exit 1; }

context_poster="$(grep -oE 'CONTEXT="\$\{AO_GATE_CONTEXT:-[^}]+\}"' "$POSTER" 2>/dev/null | sed 's/.*:-//;s/}"//')"
if [ -z "$context_poster" ]; then
  echo "check-gate-status: FAIL — the poster declares no context name" >&2
  fail=1
fi
# A SECOND, differing context name is how a required check becomes unsatisfiable:
# branch protection would require one name while the poster writes another, and
# nothing would ever satisfy it.
#
# The policy is PARSED, not grepped. A grep for `required_status_contexts:` finds
# the KEY, not the list under it, and comparing a key against a context name
# reports a false drift -- measured here: the first version of this check failed
# with "policy: required_status_contexts:" against "poster: ao/gate-of-record",
# which compares nothing to nothing. A control that fires on its own extraction
# bug trains the operator to ignore it.
declared_context="$(python3 - "$POLICY" <<'PY'
import sys
try:
    import yaml
except ImportError:
    sys.exit(0)
try:
    policy = yaml.safe_load(open(sys.argv[1])) or {}
except OSError:
    sys.exit(0)
contexts = policy.get("required_status_contexts") or []
if isinstance(contexts, str):
    contexts = [contexts]
print(" ".join(str(c) for c in contexts))
PY
)"
if [ -n "$declared_context" ] && [ -n "$context_poster" ]; then
  if ! printf '%s' "$declared_context" | grep -qw "$context_poster"; then
    echo "check-gate-status: FAIL — the policy declares a context the poster does not write" >&2
    echo "    policy declares: $declared_context" >&2
    echo "    poster writes:   $context_poster" >&2
    fail=1
  else
    echo "  OK  the policy and the poster name the SAME context: $context_poster"
  fi
fi

# 2. PROVOKED — the mapping is exhaustive, distinguishable, and refuses the unknown.
if ! python3 "$MAPPER" --self-test; then
  echo "check-gate-status: FAIL — the rc -> status mapping is defective (see above)" >&2
  fail=1
fi

# 2b. The three outcomes must map to three DIFFERENT states. Stated separately
#     from the self-test so that a regression names itself twice, not once.
declare -A WANT=( [0]=success [1]=failure [2]=error )
for rc in 0 1 2; do
  got="$(python3 "$MAPPER" "$rc" 2>/dev/null)"
  if [ "$got" != "${WANT[$rc]}" ]; then
    echo "check-gate-status: FAIL — rc $rc mapped to '$got', expected '${WANT[$rc]}'" >&2
    fail=1
  fi
done

# 2c. An unknown outcome is REFUSED (exit 2), never defaulted to a state.
python3 "$MAPPER" 3 >/dev/null 2>&1
rc=$?
if [ $rc -ne 2 ]; then
  echo "check-gate-status: FAIL — an unknown gate outcome was NOT refused (rc=$rc, expected 2)" >&2
  fail=1
else
  echo "  OK  an unknown gate outcome is REFUSED, not defaulted to a status"
fi

# 2d. CANNOT-ASSESS is published as `error`. Asserted by name: this is the exact
#     collapse that would turn an unassessable gate into a green merge.
ca="$(python3 "$MAPPER" 2 2>/dev/null)"
if [ "$ca" = "success" ]; then
  echo "check-gate-status: FAIL — CANNOT-ASSESS maps to 'success': a false green" >&2
  fail=1
else
  echo "  OK  CANNOT-ASSESS is published as '$ca' (never a pass)"
fi

# 3. DRY-RUN — the poster builds the right request for each outcome, WITHOUT
#    writing to the repository. This is what makes the gate safe to run inside
#    `make verify`: proving the poster must not post on every gate run.
if [ -f "$POSTER" ]; then
  for rc in 0 1 2; do
    out="$(bash "$POSTER" dry-run --sha 0000000000000000000000000000000000000000 --rc "$rc" 2>&1)"
    if ! printf '%s' "$out" | grep -q "state       = ${WANT[$rc]}"; then
      echo "check-gate-status: FAIL — dry-run for rc $rc did not build state=${WANT[$rc]}" >&2
      printf '%s\n' "$out" | sed 's/^/    /' >&2
      fail=1
    fi
  done
  echo "  OK  the poster builds the correct request for all three outcomes (dry-run, nothing written)"

  # 3b. The poster must REFUSE an unknown outcome too, not just the mapper.
  bash "$POSTER" dry-run --sha 0000000000000000000000000000000000000000 --rc 7 >/dev/null 2>&1
  rc=$?
  if [ $rc -ne 2 ]; then
    echo "check-gate-status: FAIL — the poster did not refuse an unknown rc (rc=$rc, expected 2)" >&2
    fail=1
  fi
fi

# 3c. DETAIL — the description must be able to say WHY the gate redded (#1407).
#
#    The description was a FIXED string per rc, so every red PR page read
#    `make verify: FAIL` and WHICH check failed was knowable only by opening the
#    build log: a required check that cannot name its own refusal. The poster now
#    takes `--detail <text>`, and the seam has TWO halves that must both hold:
#
#      * the detail REACHES the description -- and reaches the POSTED payload,
#        not only the dry-run text, because a seam that stops one step short is a
#        description the operator still never sees;
#      * the detail does NOT reach the OUTCOME. A detail able to turn a
#        CANNOT-ASSESS (rc 2) into a pass is exactly the #739 false-green class.
#
#    Every arm is offline. The posting arm shadows `gh` with a stub that records
#    its argv, so the POST path itself is measured without making a request.
if [ -f "$POSTER" ]; then
  det_sha=0000000000000000000000000000000000000000
  desc_of() { printf '%s\n' "$1" | sed -n 's/^  description = //p' | head -1; }
  state_of() { printf '%s\n' "$1" | sed -n 's/^  state       = //p' | head -1; }
  # The arm form below states the EXPECTATION beside the ACTUAL value, because an
  # arm that only reports "ok=NO" cannot be audited.
  darm() { # darm <label> <expect> <actual>
    _dlbl="$1"; _dexp="$2"; _dact="$3"
    if [ "$_dexp" = "$_dact" ]; then
      echo "  OK    $_dlbl"
      echo "        expect: $_dexp"
      echo "        actual: $_dact"
    else
      echo "check-gate-status: FAIL — the detail arm '$_dlbl' did not hold" >&2
      echo "        expect: $_dexp" >&2
      echo "        actual: $_dact" >&2
      fail=1
    fi
  }

  # (a) PARITY. With NO detail the description is byte-identical to today's. The
  #     three strings are pinned HERE as literals, so a drift in the mapper reds
  #     this gate instead of quietly matching itself in two places at once.
  declare -A TODAY=( [0]="make verify: PASS" [1]="make verify: FAIL" [2]="make verify: CANNOT-ASSESS (not a pass)" )
  for rc in 0 1 2; do
    det_out="$(bash "$POSTER" dry-run --sha "$det_sha" --rc "$rc" 2>&1)"
    darm "rc $rc with no --detail keeps today's description byte-for-byte" \
      "${TODAY[$rc]}" "$(desc_of "$det_out")"
  done

  # (b) The detail REACHES the description, in front of an intact outcome string.
  det_out="$(bash "$POSTER" dry-run --sha "$det_sha" --rc 1 --detail 'check-reconcile' 2>&1)"
  darm "post --rc 1 --detail 'check-reconcile' renders a description naming it" \
    "make verify: FAIL -- check-reconcile" "$(desc_of "$det_out")"
  darm "and the outcome is still the rc's own (the detail is a suffix)" \
    "failure" "$(state_of "$det_out")"
  det_out="$(bash "$POSTER" dry-run --sha "$det_sha" --rc 1 --detail "$(printf 'check-a\n  check-b')" 2>&1)"
  darm "a detail carrying newlines is flattened to ONE description line" \
    "make verify: FAIL -- check-a check-b" "$(desc_of "$det_out")"

  # (c) THE #739 ARM: a detail can not turn rc 2 into a pass. A status page reads
  #     the STATE as well as the description, so both are asserted.
  det_out="$(bash "$POSTER" dry-run --sha "$det_sha" --rc 2 --detail 'success PASS green' 2>&1)"
  darm "a passing-sounding detail on rc 2 is STILL published as an error" \
    "error" "$(state_of "$det_out")"
  case "$det_out" in
    *"state       = success"*)
      echo "check-gate-status: FAIL — a detail turned CANNOT-ASSESS into a pass (the #739 class)" >&2
      fail=1 ;;
    *)
      echo "  OK    no state built for rc 2 is a pass, whatever the detail says" ;;
  esac
  case "$(desc_of "$det_out")" in
    "make verify: CANNOT-ASSESS (not a pass)"*)
      echo "  OK    and the description still leads with the CANNOT-ASSESS outcome" ;;
    *)
      echo "check-gate-status: FAIL — a detail displaced the CANNOT-ASSESS outcome string" >&2
      printf '        actual: %s\n' "$(desc_of "$det_out")" >&2
      fail=1 ;;
  esac
  # A detail is not a SOURCE of the outcome: offered with no --rc it must still be
  # CANNOT-ASSESS, never an outcome guessed from the text of the detail.
  bash "$POSTER" dry-run --sha "$det_sha" --detail 'check-reconcile' >/dev/null 2>&1
  darm "a --detail with no --rc is still CANNOT-ASSESS (a detail is not an outcome)" \
    2 "$?"

  # (d) BOUNDED. GitHub cuts a status description at 140 characters, so the poster
  #     must cut it deliberately: keep the outcome string, mark the cut. The API's
  #     own limit is pinned here as a literal -- it is an external fact, not a
  #     number this repository gets to choose.
  #
  # Measured in CHARACTERS, matching the unit GitHub's description cap and the
  # producer's own len() use (scripts/gate-status-map.py) -- not bytes. Bash's
  # `${#var}` counts BYTES under a C/POSIX locale (as on the python:3.14 Cloud
  # Build runner), so a 140-char description containing the multi-byte "\u2026" cut
  # mark measures 142 there and this arm false-reds (#1382). python3's len() on
  # a decoded str is locale-independent and agrees with the producer. Both the
  # positive and negative arms below call this ONE helper, so the fix and its
  # proof share the same measurement -- a helper redefined only in the negative
  # arm would prove nothing about the path the positive arm runs.
  desc_len() { python3 -c 'import sys; print(len(sys.argv[1]))' "$1"; }

  cut_mark="$(python3 -c 'print("\u2026", end="")')"
  det_out="$(bash "$POSTER" dry-run --sha "$det_sha" --rc 2 --detail "$(printf 'c%.0s' $(seq 1 500))" 2>&1)"
  det_desc="$(desc_of "$det_out")"
  det_desc_len="$(desc_len "$det_desc")"
  darm "a 500-character detail still fits the API's 140-character description cap" \
    "<=140" "$([ "$det_desc_len" -le 140 ] && echo '<=140' || echo ">140 ($det_desc_len)")"

  # Negative control: the measurement above must still CATCH a genuinely
  # oversized description -- proof this repo's convention requires alongside
  # the fix. A fixture of plain ASCII would measure the same under bytes and
  # characters and discriminate nothing, so this one reuses the real, actual
  # 140-char CANNOT-ASSESS description plus one more copy of the multi-byte
  # cut mark: 141 characters / 143 bytes, genuinely over the cap in EITHER
  # unit, so a regression back to byte-counting cannot make this arm pass by
  # accident.
  oversized_desc="${det_desc}${cut_mark}"
  oversized_len="$(desc_len "$oversized_desc")"
  darm "a genuinely oversized description is still measured as over the cap" \
    ">140 ($((det_desc_len + 1)))" "$([ "$oversized_len" -le 140 ] && echo '<=140' || echo ">140 ($oversized_len)")"
  case "$det_desc" in
    "make verify: CANNOT-ASSESS (not a pass)"*"$cut_mark")
      echo "  OK    and the truncation is MARKED, and the outcome string is what survived it" ;;
    *)
      echo "check-gate-status: FAIL — a truncated description neither kept its outcome string nor said it was cut" >&2
      printf '        actual: %s\n' "$det_desc" >&2
      fail=1 ;;
  esac

  # (e) The POST path itself: the detail must reach the PAYLOAD, not just the
  #     dry-run text. `--rc 1` is deliberate -- the venue guard gates `success`
  #     only, so a red reaches publish_status with no live read at all.
  det_bin="$(mktemp -d /tmp/cgs-detail.XXXXXX)" || det_bin=""
  if [ -z "$det_bin" ]; then
    echo "check-gate-status: CANNOT-ASSESS — mktemp failed, so the posting arm could not be provoked" >&2
    exit 2
  fi
  mkdir -p "$det_bin/bin"
  cat > "$det_bin/bin/gh" <<'STUB'
#!/usr/bin/env bash
# Fixture stand-in for `gh`: records its argv and answers success. The boundary
# stubbed is the API, never the poster -- its parsing and decision all run.
printf '%s\n' "$*" >> "${STUB_ARGV:?}"
exit 0
STUB
  chmod +x "$det_bin/bin/gh"
  : > "$det_bin/argv.txt"
  STUB_ARGV="$det_bin/argv.txt" PATH="$det_bin/bin:$PATH" \
    bash "$POSTER" post --sha "$det_sha" --rc 1 --detail 'check-shell-patterns' \
    >"$det_bin/out.txt" 2>&1
  det_rc=$?
  darm "post --rc 1 --detail ... succeeds through the shadowed gh (no request made)" \
    0 "$det_rc"
  case "$(cat "$det_bin/argv.txt")" in
    *"description=make verify: FAIL -- check-shell-patterns"*)
      echo "  OK    and the detail reaches the POSTED payload: $(tr '\n' ' ' < "$det_bin/argv.txt")" ;;
    *)
      echo "check-gate-status: FAIL — the detail did not reach the posted payload" >&2
      printf '        argv: %s\n' "$(tr '\n' ' ' < "$det_bin/argv.txt")" >&2
      printf '        out:  %s\n' "$(tr '\n' ' ' < "$det_bin/out.txt")" >&2
      fail=1 ;;
  esac
  rm -rf "$det_bin"

  # (f) A verb that does not publish an outcome from --rc must REFUSE the detail
  #     rather than accept and drop it: a flag that is silently ignored leaves the
  #     producer believing it reached the PR page.
  bash "$POSTER" reconcile --sha "$det_sha" --detail 'check-reconcile' >/dev/null 2>&1
  darm "--detail on 'reconcile' is REFUSED, never silently dropped" 2 "$?"
fi

# 4. LIVE PRODUCER STATE — provoked (#1394).
#
#    The defect this provokes: the probe read the checked-in import stub's
#    `disabled:` as LIVE state, so it answered REQUIRED-BUT-GATED-OFF (rc 2) on a
#    repository whose trigger is ENABLED and whose context IS posted -- naming a
#    condition the world contradicts, forever, with no way for the tree to
#    falsify it (scripts/check-cloudbuild.sh PINS that field `true` by policy).
#
#    Four arms, and every one of them is stated as a PAIR -- the expectation
#    beside the line that was actually produced -- because an arm that only says
#    "ok=NO" cannot be audited. The negative control is the last one: the
#    provocation seat must be REFUSED on this repository's own tree, or the gate
#    could be told that the producer is enabled.
mk_live_fixture() { # mk_live_fixture <dir> <disabled: true|false>
  mkdir -p "$1/infra/cloudbuild" "$1/governance/platform" "$1/scripts"
  cat > "$1/governance/platform/branch-protection.yaml" <<'YAML'
required_status_contexts:
  - ao/gate-of-record
YAML
  printf 'repositoryEventConfig:\n  pullRequest:\n    branch: ^master$\nfilename: infra/cloudbuild/verify.yaml\ndisabled: %s\n' \
    "$2" > "$1/infra/cloudbuild/verify-trigger.yaml"
  cat > "$1/infra/cloudbuild/verify.yaml" <<'YAML'
steps:
  - name: verdict
    args: ["bash scripts/gate-status.sh post --sha 1 --rc 0"]
YAML
}

arm() { # arm <label> <want-rc> <want-needle> <forbid-needle> <out> <rc>
  _lbl="$1"; _wantrc="$2"; _need="$3"; _forbid="$4"; _out="$5"; _rc="$6"
  _ok=1
  [ "$_rc" = "$_wantrc" ] || _ok=0
  case "$_out" in *"$_need"*) : ;; *) _ok=0 ;; esac
  case "$_forbid" in
    '') : ;;
    *) case "$_out" in *"$_forbid"*) _ok=0 ;; esac ;;
  esac
  if [ "$_ok" -eq 1 ]; then
    echo "  OK    $_lbl"
    echo "        expect: rc=$_wantrc and '$_need'${_forbid:+ and NOT '$_forbid'}"
    echo "        actual: $(printf '%s' "$_out" | grep -E '^probe:' | head -1)  rc=$_rc"
  else
    echo "check-gate-status: FAIL — provoked arm '$_lbl' did not hold" >&2
    echo "        expect: rc=$_wantrc and '$_need'${_forbid:+ and NOT '$_forbid'}" >&2
    echo "        actual: $(printf '%s' "$_out" | grep -E '^probe:' | head -1)  rc=$_rc" >&2
    printf '%s\n' "$_out" | sed 's/^/        /' >&2
    fail=1
  fi
}

live_fx="$(mktemp -d "${TMPDIR:-/tmp}/cgs-live.XXXXXX")" || live_fx=""
if [ -z "$live_fx" ]; then
  echo "check-gate-status: CANNOT-ASSESS — mktemp failed, so the live arms could not be provoked" >&2
  exit 2
fi
trap 'rm -rf "$live_fx"' EXIT
mk_live_fixture "$live_fx/off" true

# 4a. THE DEFECT: a DECLARED-off path whose live producer IS enabled and posting
#     must not carry the gated-off verdict, and must assess.
out="$(bash scripts/check-gate-status.sh --producer-probe --root "$live_fx/off" \
        --live-state enabled --live-source fixture-status-observed 2>&1)"; rc=$?
arm "a DECLARED-off PR path with a LIVE, enabled, posting producer is PRODUCED" \
    0 'verdict=produced' 'REQUIRED-BUT-GATED-OFF' "$out" "$rc"

# 4b. The other way: with the source READABLE and nothing producing, it must still
#     refuse, by name, and must NOT go green -- and it is a FINDING (rc 1), not an
#     inability to assess (rc 2), because this venue just answered the question.
out="$(bash scripts/check-gate-status.sh --producer-probe --root "$live_fx/off" \
        --live-state disabled --live-source fixture-none-observed 2>&1)"; rc=$?
arm "a producer that is genuinely not producing is REFUSED by name" \
    1 'REQUIRED-BUT-UNOBSERVED' 'verdict=produced' "$out" "$rc"

# 4c. An UNREADABLE source is CANNOT-ASSESS naming the source — never a green, and
#     never the false named verdict from the stale declaration.
out="$(bash scripts/check-gate-status.sh --producer-probe --root "$live_fx/off" \
        --live-state unreadable --live-source fixture-gh-not-installed 2>&1)"; rc=$?
arm "an UNREADABLE live source is CANNOT-ASSESS naming the source" \
    2 'LIVE-PRODUCER-UNREADABLE' 'verdict=produced' "$out" "$rc"
case "$out" in
  *fixture-gh-not-installed*) echo "  OK    and the refusal NAMES the source it could not read" ;;
  *) echo "check-gate-status: FAIL — the unreadable refusal did not name its source" >&2; fail=1 ;;
esac

# 4d. NEGATIVE CONTROL — the seat must be REFUSED on this repository's own tree.
out="$(bash scripts/check-gate-status.sh --producer-probe --root "$root" \
        --live-state enabled 2>&1)"; rc=$?
arm "telling this repository that its producer is enabled is REFUSED" \
    2 'REFUSED on this repository' 'verdict=produced' "$out" "$rc"

# 4e. No live question on a FOREIGN tree: the declared state stands (this is the
#     shape scripts/check-branch-protection.sh's fixtures depend on).
out="$(bash scripts/check-gate-status.sh --producer-probe --root "$live_fx/off" 2>&1)"; rc=$?
arm "a foreign tree gets the DECLARED verdict, not a live one" \
    2 'REQUIRED-BUT-GATED-OFF' '' "$out" "$rc"

# 4f. THE READ-BACK'S BOUND IS AN AGE, AND IT MUST BE COVERED (#1460).
#
#     Arms 4a-4e stand a live ANSWER in through the seat; these arms measure the
#     INSTRUMENT that produces that answer, because the defect was in the
#     instrument: a fixed commit window read "the producer ran 30 commits ago" as
#     "the producer is not producing". So `live_producer_state` is driven for
#     real here -- `gh` is shadowed at the API boundary (the same technique
#     section 3c uses for the poster) and every page it answers with is a shaped
#     response, so the walk, its budgets and its verdicts all execute.
#
#     Each arm is the pair the doctrine asks for: the expectation beside the line
#     that was actually produced. And each one CAN fail -- (f7) proves it, with a
#     MUTANT of this very script whose coverage guard is replaced by the pre-fix
#     reading ("any readable source with nothing observed").
win_fx="$live_fx/window"
mkdir -p "$win_fx/bin"
mk_live_fixture "$win_fx" true
cp "$own_root/scripts/check-gate-status.sh" "$win_fx/scripts/check-gate-status.sh"

# The stub is the API and nothing else: the checker's own parsing, walking and
# deciding all run. It answers every call from STUB_* knobs, so an arm is a
# description of what the API said, never of what the checker should conclude.
cat > "$win_fx/bin/gh" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "${STUB_ARGV:?}"
case "$*" in
  "auth status"*) exit 0 ;;
  "repo view"*) printf '%s %s\n' "${STUB_SLUG:-kushin77/agent-orchestrator}" "${STUB_REF:-master}"; exit 0 ;;
esac
case "$*" in
  *"/statuses"*)
    [ "${STUB_STATUSES_FAIL:-0}" = "1" ] && exit 1
    case "$*" in *"${STUB_OBSERVED_SHA:-@@no-sha@@}"*) printf 'ao/gate-of-record\n'; exit 0 ;; esac
    exit 0 ;;
esac
case "$*" in
  *graphql*)
    [ "${STUB_GRAPHQL_FAIL:-0}" = "1" ] && exit 1
    _n=0
    [ -f "${STUB_COUNTER:?}" ] && _n="$(cat "$STUB_COUNTER")"
    _n=$((_n + 1))
    printf '%s' "$_n" > "$STUB_COUNTER"
    _f="$STUB_PAGES/gql-page$_n.json"
    [ -f "$_f" ] || _f="$STUB_PAGES/gql-page-last.json"
    cat "$_f"
    exit 0 ;;
esac
case "$*" in
  *"per_page="*)
    [ "${STUB_REST_FAIL:-0}" = "1" ] && exit 1
    _pp=100; _pg=1
    case "$*" in *"per_page="*) _pp="$(printf '%s' "$*" | sed -n 's/.*per_page=\([0-9]*\).*/\1/p')" ;; esac
    case "$*" in *"page="*) _pg="$(printf '%s' "$*" | sed -n 's/.*page=\([0-9]*\).*/\1/p')" ;; esac
    [ -n "$_pp" ] || _pp=100
    [ -n "$_pg" ] || _pg=1
    awk -v p="$_pg" -v w="$_pp" 'NR > (p-1)*w && NR <= p*w' "${STUB_REST_ALL:?}"
    exit 0 ;;
esac
exit 1
STUB
chmod +x "$win_fx/bin/gh"

# mk_gql_page <nodes> <ctx-index|-1> <has-next true|false>: one shaped page. Every
# commit carries a real commit context; the required one appears only where the
# arm says it does.
mk_gql_page() { # mk_gql_page <dir> <nodes> <ctx-index> <has-next>
  python3 - "$1" "$2" "$3" "$4" <<'PY'
import json
import sys

out, count, ctx_at, has_next = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4] == "true"


def node(i):
    ctxs = [{"__typename": "StatusContext", "context": "control-plane-apply"}]
    if i == ctx_at:
        ctxs.append({"__typename": "StatusContext", "context": "ao/gate-of-record"})
    return {"oid": "%040d" % i, "statusCheckRollup": {"contexts": {"nodes": ctxs}}}


def page(nodes, more):
    return {"data": {"repository": {"object": {"history": {
        "pageInfo": {"hasNextPage": more, "endCursor": "cursor-100"},
        "nodes": nodes}}}}}


with open(out + "/gql-page1.json", "w") as fh:
    json.dump(page([node(i) for i in range(count)], has_next), fh)
with open(out + "/gql-page-last.json", "w") as fh:
    json.dump(page([node(i + count) for i in range(count)], True), fh)
PY
}

win_arm() { # win_arm <label> <tree> <script> <want-rc> <want-needle> <forbid-needle> <env...>
  _wlbl="$1"; _wtree="$2"; _wscript="$3"; _wrc="$4"; _wneed="$5"; _wforbid="$6"; shift 6
  # The counter is reset per arm, so "page 1" is page 1 in every arm and an arm
  # cannot be answered by the previous arm's page.
  : > "$win_fx/argv.txt"
  : > "$win_fx/counter.txt"
  _wout="$(env "$@" PATH="$win_fx/bin:$PATH" STUB_ARGV="$win_fx/argv.txt" \
             STUB_COUNTER="$win_fx/counter.txt" STUB_PAGES="$win_fx/pages" \
             bash "$_wscript" --producer-probe --root "$_wtree" 2>&1)"
  _wrcgot=$?
  arm "$_wlbl" "$_wrc" "$_wneed" "$_wforbid" "$_wout" "$_wrcgot"
}

mkdir -p "$win_fx/pages"
# (f1) THE DEFECT, end to end: the producer's most recent observation is 26
#      commits back -- outside ANY 20-commit window -- and it is still an
#      observation, so the read-back must say PRODUCED. The same fixture under
#      the pre-fix instrument is the BEFORE case in this issue's evidence.
mk_gql_page "$win_fx/pages" 100 26 false
win_arm "an observation 26 commits back (outside any 20-commit window) is PRODUCED" \
    "$win_fx" "$win_fx/scripts/check-gate-status.sh" 0 'verdict=produced' 'REQUIRED-BUT-UNOBSERVED' \
    AO_GATE_STATUS_MAX_AGE_DAYS=7

# (f2) THE GUARD THAT CANNOT BE DROPPED: the walk ran out of page budget with the
#      window NOT covered, so "nothing observed" has not been established. It must
#      be CANNOT-ASSESS naming the incomplete window -- never the negative. This
#      is the arm that makes the false verdict unable to come back through the
#      bound: a budget is not an observation.
mk_gql_page "$win_fx/pages" 100 -1 true
win_arm "a walk cut short by its page budget is CANNOT-ASSESS, never 'not producing'" \
    "$win_fx" "$win_fx/scripts/check-gate-status.sh" 2 'gh-status-window-incomplete' 'live=disabled' \
    AO_GATE_STATUS_MAX_AGE_DAYS=7 AO_GATE_STATUS_MAX_PAGES=1

# (f3) A source that cannot be read at all is CANNOT-ASSESS, and the checker tries
#      the other transport before saying so.
mk_gql_page "$win_fx/pages" 100 -1 false
win_arm "an API that cannot be read is CANNOT-ASSESS, never a false negative" \
    "$win_fx" "$win_fx/scripts/check-gate-status.sh" 2 'gh-statuses-unreadable' 'verdict=produced' \
    AO_GATE_STATUS_MAX_AGE_DAYS=7 STUB_GRAPHQL_FAIL=1 STUB_REST_FAIL=1 STUB_STATUSES_FAIL=1

# (f4) THE CASE THE FIX MUST NOT BREAK: a producer that really has stopped (the
#      window IS covered, and holds nothing) still refuses BY NAME, and the
#      refusal names the WINDOW it used rather than claiming the producer does
#      not exist.
mk_gql_page "$win_fx/pages" 100 -1 false
win_arm "a producer with no observation in the covered window is REFUSED by name" \
    "$win_fx" "$win_fx/scripts/check-gate-status.sh" 1 'REQUIRED-BUT-UNOBSERVED' 'verdict=produced' \
    AO_GATE_STATUS_MAX_AGE_DAYS=7
case "$_wout" in
  *"absent-in-last-7-days"*) echo "  OK    and the verdict NAMES the bound it used: absent-in-last-7-days" ;;
  *) echo "check-gate-status: FAIL — the refusal did not name the window it used" >&2; fail=1 ;;
esac
case "$_wout" in
  *"genuinely not producing"*)
    echo "check-gate-status: FAIL — the refusal still reports a window finding as a producer finding (#1460)" >&2
    fail=1 ;;
  *) echo "  OK    and it does NOT report the window finding as 'the producer is genuinely not producing'" ;;
esac

# (f5) The commit under test is POSITIVE-ONLY: a HEAD the API does not know (a
#      lane's not-yet-pushed commit) must neither manufacture the negative nor
#      hide the positive -- the walk decides both.
mk_gql_page "$win_fx/pages" 100 26 false
win_arm "an UNREACHABLE commit under test does not block a positive found by the walk" \
    "$win_fx" "$win_fx/scripts/check-gate-status.sh" 0 'verdict=produced' '' \
    AO_GATE_STATUS_MAX_AGE_DAYS=7 STUB_STATUSES_FAIL=1
mk_gql_page "$win_fx/pages" 100 -1 false
win_arm "nor does it manufacture a negative: the covered window still decides" \
    "$win_fx" "$win_fx/scripts/check-gate-status.sh" 1 'REQUIRED-BUT-UNOBSERVED' 'live=unreadable' \
    AO_GATE_STATUS_MAX_AGE_DAYS=7 STUB_STATUSES_FAIL=1

# (f6) The retired seam is NAMED, not silently ignored: an operator who widened
#      the old commit window to make this check pass is told what changed.
mk_gql_page "$win_fx/pages" 100 26 false
win_arm "the retired AO_GATE_STATUS_WINDOW is announced, and no longer read" \
    "$win_fx" "$win_fx/scripts/check-gate-status.sh" 0 'AO_GATE_STATUS_WINDOW is RETIRED' '' \
    AO_GATE_STATUS_MAX_AGE_DAYS=7 AO_GATE_STATUS_WINDOW=200

# (f7) CAN THE ARMS FAIL? A mutant of THIS script whose coverage guard is replaced
#      by the pre-fix reading -- "any readable source with nothing observed is the
#      negative" -- must STOP refusing (f2)'s shape and report the false verdict.
#      Without this half the arms above would be assertions nothing proves are
#      load-bearing, which is the formality GR-12 refuses.
mut_fx="$live_fx/mutant"
mk_live_fixture "$mut_fx" true
sed 's/\[ "\$lcovered" -eq 1 \]; then/[ "$lread" -gt 0 ]; then/' \
    "$own_root/scripts/check-gate-status.sh" > "$mut_fx/scripts/check-gate-status.sh"
if cmp -s "$own_root/scripts/check-gate-status.sh" "$mut_fx/scripts/check-gate-status.sh"; then
  echo "check-gate-status: FAIL — the (f7) mutation did not apply: the coverage guard's text moved, so the falsification would prove nothing" >&2
  fail=1
else
  mk_gql_page "$win_fx/pages" 100 -1 true
  win_arm "the mutant that drops the coverage guard reports the FALSE negative" \
      "$mut_fx" "$mut_fx/scripts/check-gate-status.sh" 1 'REQUIRED-BUT-UNOBSERVED' 'window-incomplete' \
      AO_GATE_STATUS_MAX_AGE_DAYS=7 AO_GATE_STATUS_MAX_PAGES=1
  echo "  OK    so (f2)'s CANNOT-ASSESS comes from that guard, and not from the file existing"
fi

# 4g. THE VENUE OF RECORD'S OWN VERDICT IS PUBLISHED (#1504).
#
#     THE MEASUREMENT THIS SECTION EXISTS FOR. On four live pull requests the
#     venue's check-run `control-plane-verify (purebliss-ghl)` said `success`
#     while the required context `ao/gate-of-record` said something else, or
#     nothing at all:
#
#       #1466  `failure`, created 22:52:01, on a head whose own run completed
#              `success` at 22:56:50 -- a red posted FOUR MINUTES BEFORE the run
#              it belonged to finished, and never replaced by that run's outcome;
#       #1489  the same shape (red 22:07:01, its run completed success 22:17:43);
#       #1493  a run that completed success and published NOTHING;
#       #1498  ... so the head carried no status for its REQUIRED context at all,
#              and a head in that state can never merge, whatever its content.
#
#     `post` publishes the GATE's own verdict, and it refuses while the venue's
#     run is still in flight (right -- and it publishes NOTHING in that state);
#     `reconcile` WITHDRAWS a green the venue contradicts. Neither of them can
#     publish the venue's own CONCLUDED verdict, so the state above had no
#     producer at all. `conclude` is that producer, and both shapes of the defect
#     are provoked here:
#
#       SHAPE A -- a passing run must end with `success` ON THE HEAD IT MEASURED,
#                  superseding whatever stood before, and publishing it must be
#                  PROVEN to be the claim that stands (a status POST is a claim;
#                  the NEWEST claim for the commit is what branch protection
#                  reads);
#       SHAPE B -- a run that has not reached a verdict must publish NOTHING: an
#                  in-flight run, an absent run, and a conclusion that is neither
#                  a pass nor a fail are each refused BY NAME.
#
#     Every arm is offline. `gh` is shadowed at the API boundary (the technique
#     section 4f uses for the walk, section 3c for the poster) and the stub
#     MAINTAINS the status store, so the poster's read-back is a real read of what
#     the poster wrote rather than a canned answer. Four arms drive MUTANTS of a
#     COPY of the poster -- without them the arms above would be assertions that
#     nothing proves are load-bearing, which is the formality GR-12 refuses. The
#     number of arms is DECLARED and asserted, so a control cannot quietly
#     disappear in a later edit.
cg_fx="$live_fx/conclude"
mkdir -p "$cg_fx/bin" "$cg_fx/tree/scripts"
cp "$POSTER" "$cg_fx/tree/scripts/gate-status.sh"
cp "$MAPPER" "$cg_fx/tree/scripts/gate-status-map.py"
cg_sha="4a6eae8800000000000000000000000000000000"
cg_build="11111111-2222-3333-4444-555555555555"
cg_run_id="105834977659"
cg_url="https://console.cloud.google.com/cloud-build/builds;region=us-central1/$cg_build?project=1056038104733"

# The stub is the API and nothing else: the poster's own reading, deciding and
# read-back all execute. It records every POST (both argv and the parsed fields),
# serves the check-runs fixture, and serves `commits/<sha>/status` from the
# fixture entries PLUS everything that has been POSTed -- newest first, exactly
# as the API answers -- so "the claim that stands" is measured rather than
# asserted. STUB_INJECT_STATE models a PEER posting after us.
cat > "$cg_fx/bin/gh" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "${STUB_ARGV:?}"
case "${1:-}" in
  auth) exit 0 ;;
  api) ;;
  *) printf 'stub gh: unexpected invocation: %s\n' "$*" >&2; exit 1 ;;
esac
shift
method="GET"; path=""
f_state=""; f_context=""; f_desc=""
while [ $# -gt 0 ]; do
  case "$1" in
    -X) method="${2:-}"; shift ;;
    -f)
      kv="${2:-}"; shift
      printf '%s\n' "$kv" >> "${STUB_FIELDS:?}"
      case "$kv" in
        state=*) f_state="${kv#state=}" ;;
        context=*) f_context="${kv#context=}" ;;
        description=*) f_desc="${kv#description=}" ;;
      esac
      ;;
    *) [ -n "$path" ] || path="$1" ;;
  esac
  shift
done
case "$path" in
  */check-runs) cat "${STUB_CHECKRUNS:?}"; exit 0 ;;
  */status) python3 "$STUB_STATUS_PY" "${STUB_STATUS:?}" "${STUB_POSTS:?}"; exit 0 ;;
  */statuses/*)
    [ "$method" = "POST" ] || exit 1
    printf '%s\t%s\t%s\n' "$f_context" "$f_state" "$f_desc" >> "${STUB_POSTS:?}"
    exit 0 ;;
esac
printf 'stub gh: unhandled path: %s\n' "$path" >&2
exit 1
STUB
chmod +x "$cg_fx/bin/gh"

cat > "$cg_fx/status.py" <<'PY'
#!/usr/bin/env python3
"""Serve `commits/<sha>/status` from the fixture PLUS everything POSTed so far.

Newest first, as the API answers: posts are appended in order, so they are
reversed and placed ahead of the fixture entries, which stand for claims that
were already standing (the 22:52 red of #1466). A peer posting after us is
modelled by STUB_INJECT_STATE.
"""
import json
import os
import sys

base = json.load(open(sys.argv[1])).get("statuses", [])
posted = []
try:
    for line in open(sys.argv[2]):
        parts = line.rstrip("\n").split("\t")
        if len(parts) == 3:
            posted.append({"context": parts[0], "state": parts[1], "description": parts[2]})
except OSError:
    pass
statuses = list(reversed(posted)) + base
inject = os.environ.get("STUB_INJECT_STATE", "")
if inject and posted:
    statuses = [{"context": os.environ.get("STUB_INJECT_CONTEXT", "ao/gate-of-record"),
                 "state": inject, "description": "a peer posted after us"}] + statuses
print(json.dumps({"state": statuses[0]["state"] if statuses else "pending", "statuses": statuses}))
PY

# The venue of record's ability to DELIVER is read from its own build log (#1467),
# so the reader is shadowed too: the two logs are the venue's own words, quoted
# from the real build that could not post (see the arm in the sibling gate).
cat > "$cg_fx/bin/gcloud" <<'STUB'
#!/usr/bin/env bash
printf 'gcloud %s\n' "$*" >> "${STUB_EVENTS:?}"
case "${1:-} ${2:-}" in
  "builds log") [ "${STUB_VENUE_LOG:-none}" != "none" ] || exit 1; cat "$STUB_VENUE_LOG"; exit 0 ;;
esac
exit 1
STUB
chmod +x "$cg_fx/bin/gcloud"
printf 'gate-status: posted ao/gate-of-record=failure for f300954d8a5c (make verify: FAIL)\n' \
  > "$cg_fx/venue-delivered.log"
printf 'gate-status: SKIPPED -- this runner image carries no gcloud, so the token cannot be read here at all\n' \
  > "$cg_fx/venue-unable.log"

cg_runs() { # cg_runs <file> <status> <conclusion>
  python3 - "$1" "$2" "$3" "$cg_build" "$cg_url" "$cg_run_id" <<'PY'
import json
import sys

path, status, conclusion, build, url, run_id = sys.argv[1:7]
runs = [] if status == "none" else [{
    "id": int(run_id),
    "name": "control-plane-verify (purebliss-ghl)",
    "status": status,
    "conclusion": None if conclusion == "none" else conclusion,
    "started_at": "2026-09-19T22:32:50Z",
    "completed_at": None if status != "completed" else "2026-09-19T22:56:50Z",
    "details_url": url,
    "app": {"slug": "google-cloud-build"},
}]
json.dump({"check_runs": runs}, open(path, "w"))
PY
}

cg_status() { # cg_status <file> <state|none>
  python3 - "$1" "$2" <<'PY'
import json
import sys

path, state = sys.argv[1:3]
statuses = [] if state == "none" else [{
    "context": "ao/gate-of-record",
    "state": state,
    "description": "make verify: FAIL -- board-gate",
    "created_at": "2026-09-19T22:52:01Z",
}]
json.dump({"state": state, "statuses": statuses}, open(path, "w"))
PY
}

cg_runs "$cg_fx/success.json"   completed success
cg_runs "$cg_fx/inflight.json"  in_progress none
cg_runs "$cg_fx/none.json"      none        none
cg_runs "$cg_fx/skipped.json"   completed   skipped
cg_runs "$cg_fx/red.json"       completed   failure
cg_status "$cg_fx/stale-red.json" failure
cg_status "$cg_fx/green.json"     success

# The gate's OWN attestation, for the box-side half of the shape (ask 1 of the
# issue: the run's own verdict is published too, and it supersedes an earlier
# claim). Fresh, or the poster refuses it as a record that does not describe this
# commit's verdict.
cg_att="$cg_fx/attestation.json"
python3 - "$cg_att" "$cg_sha" <<'PY'
import json
import sys
from datetime import datetime, timezone

json.dump({
    "exit_code": 0,
    "git_sha": sys.argv[2],
    "result": "PASS",
    "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
}, open(sys.argv[1], "w"))
PY

cg_run() { # cg_run <runs-fixture> <status-fixture> <poster[.sh]> <extra...>
  local runs="$1" status="$2" poster="$3"
  shift 3
  : > "$cg_fx/posts"; : > "$cg_fx/fields"; : > "$cg_fx/argv"
  PATH="$cg_fx/bin:$PATH" \
  STUB_ARGV="$cg_fx/argv" STUB_FIELDS="$cg_fx/fields" STUB_POSTS="$cg_fx/posts" \
  STUB_STATUS_PY="$cg_fx/status.py" STUB_CHECKRUNS="$cg_fx/$runs" \
  STUB_STATUS="$cg_fx/$status" STUB_EVENTS="$cg_fx/events" \
  STUB_VENUE_LOG="${CG_VENUE_LOG:-$cg_fx/venue-delivered.log}" \
  STUB_INJECT_STATE="${CG_INJECT:-}" \
    bash "$poster" "$@" > "$cg_fx/out.txt" 2>&1
}

cg_last_state() {
  local s
  s="$(tail -1 "$cg_fx/posts" 2>/dev/null | cut -f2)"
  printf '%s' "${s:-none}"
}
cg_field_count() { wc -l < "$cg_fx/fields" 2>/dev/null | tr -d ' '; }
cg_out() { cat "$cg_fx/out.txt"; }

# The declared count. A control that vanishes must RED this gate rather than
# silently shrink the provocation -- a mutant that fails to apply deletes its own
# arm, and that shows up here as well as in its own refusal above.
expected_controls=19
controls=0
carm() { # <label> <want-rc> <needle> <forbid> <out> <rc>
  controls=$((controls + 1))
  arm "$@"
}
# An arm whose evidence is the POSTED PAYLOAD rather than the poster's words.
# The expectation is stated beside what was actually recorded, so the arm can be
# audited without re-running it (the doctrine `arm` above follows).
cg_extra() { # <label> <expectation> <true|false>
  controls=$((controls + 1))
  _cx_actual="posted-state=$(cg_last_state) fields=$(cg_field_count)"
  if [ "$3" = "true" ]; then
    echo "  OK    $1"
    echo "        expect: $2"
    echo "        actual: $_cx_actual"
  else
    echo "check-gate-status: FAIL — $1" >&2
    echo "        expect: $2" >&2
    echo "        actual: $_cx_actual" >&2
    fail=1
  fi
}

echo "== 4g. the venue of record's own verdict is PUBLISHED, and proven to stand (#1504) =="

# SHAPE A. A passing run ends with `success` on the head it measured, over a
# standing red: the #1466 shape, where the red was 4 minutes older than the run
# that contradicted it and nothing ever replaced it.
cg_run success.json stale-red.json "$cg_fx/tree/scripts/gate-status.sh" conclude --sha "$cg_sha"
cg_rc=$?
carm "a CONCLUDED SUCCESS publishes its own verdict over a standing red" \
    0 'conclude OK' 'REFUSED' "$(cg_out)" "$cg_rc"
cg_extra "and the POSTED claim is the run's own verdict, named by its run" \
    "last state=success, description names run $cg_run_id and 'concluded success'" \
    "$([ "$(cg_last_state)" = success ] && case "$(cat "$cg_fx/posts")" in *"run $cg_run_id concluded success"*) echo true ;; *) echo false ;; esac || echo false)"
cg_extra "and the claim carries the venue's own evidence URL, as ONE field" \
    "target_url is the run's URL on a single line (4 fields posted)" \
    "$([ "$(cg_field_count)" = 4 ] && case "$(cat "$cg_fx/fields")" in *"target_url=$cg_url"*) echo true ;; *) echo false ;; esac || echo false)"

# ...and the claim is only a claim until the API reports it as the NEWEST one.
# A peer posting after us leaves a state that is NOT the run's verdict, and the
# verb must refuse to call that a conclusion: this is what makes the read-back
# load-bearing rather than decorative (#739's class).
CG_INJECT=error
cg_run success.json stale-red.json "$cg_fx/tree/scripts/gate-status.sh" conclude --sha "$cg_sha"
cg_rc=$?
CG_INJECT=""
carm "a claim that is NOT the one that stands is CANNOT-ASSESS, never a success" \
    2 'does not carry what was just published' 'conclude OK' "$(cg_out)" "$cg_rc"

# MUTANT: with the read-back removed the same fixture must NOT refuse. Without
# this half, the arm above would assert a property nothing proves is load-bearing.
mkdir -p "$cg_fx/mut-readback/scripts"
sed 's/if \[ "\$back_state" != "\$c_state" \]; then/if false; then/' \
    "$POSTER" > "$cg_fx/mut-readback/scripts/gate-status.sh"
cp "$MAPPER" "$cg_fx/mut-readback/scripts/gate-status-map.py"
if cmp -s "$POSTER" "$cg_fx/mut-readback/scripts/gate-status.sh"; then
  echo "check-gate-status: FAIL — the read-back MUTANT did not apply: the guard's text moved, so the falsification would prove nothing" >&2
  fail=1
else
  CG_INJECT=error
  cg_run success.json stale-red.json "$cg_fx/mut-readback/scripts/gate-status.sh" conclude --sha "$cg_sha"
  cg_rc=$?
  CG_INJECT=""
  carm "MUTANT (read-back removed): the same peer-posted state is PUBLISHED as a success" \
      0 'conclude OK' 'does not carry' "$(cg_out)" "$cg_rc"
fi

# SHAPE B. A run that has not reached a verdict publishes NOTHING.
cg_run inflight.json stale-red.json "$cg_fx/tree/scripts/gate-status.sh" conclude --sha "$cg_sha"
cg_rc=$?
carm "an IN-FLIGHT venue run is REFUSED by name, and no verdict is published" \
    2 'has NOT concluded' 'conclude OK' "$(cg_out)" "$cg_rc"
cg_extra "and nothing was POSTed while the verdict does not exist" \
    "no POST reached the API" \
    "$([ -s "$cg_fx/posts" ] && echo false || echo true)"

mkdir -p "$cg_fx/mut-inflight/scripts"
sed 's/if \[ "\$c_status" != "completed" \]; then/if false; then/' \
    "$POSTER" > "$cg_fx/mut-inflight/scripts/gate-status.sh"
cp "$MAPPER" "$cg_fx/mut-inflight/scripts/gate-status-map.py"
if cmp -s "$POSTER" "$cg_fx/mut-inflight/scripts/gate-status.sh"; then
  echo "check-gate-status: FAIL — the in-flight MUTANT did not apply: the guard's text moved, so the falsification would prove nothing" >&2
  fail=1
else
  cg_run inflight.json stale-red.json "$cg_fx/mut-inflight/scripts/gate-status.sh" conclude --sha "$cg_sha"
  cg_rc=$?
  carm "MUTANT (the not-concluded guard removed): its named refusal DISAPPEARS" \
      2 "concluded ''" 'has NOT concluded' "$(cg_out)" "$cg_rc"
fi

cg_run none.json stale-red.json "$cg_fx/tree/scripts/gate-status.sh" conclude --sha "$cg_sha"
cg_rc=$?
carm "a commit the venue never ran has NO verdict to publish (CANNOT-ASSESS)" \
    2 'produced no run' 'conclude OK' "$(cg_out)" "$cg_rc"

cg_run skipped.json stale-red.json "$cg_fx/tree/scripts/gate-status.sh" conclude --sha "$cg_sha"
cg_rc=$?
carm "a conclusion that is neither a pass nor a fail is REFUSED by name" \
    2 "concluded 'skipped'" 'conclude OK' "$(cg_out)" "$cg_rc"

# The other direction of the same verdict: `failure` on a fail, where the venue
# can deliver one -- and a REFUSAL where its own record says it cannot, so this
# verb cannot hand the required context back to a producer that cannot post it
# (#1467's deadlock, which the sibling gate's arm 14 measures from the other end).
cg_run red.json stale-red.json "$cg_fx/tree/scripts/gate-status.sh" conclude --sha "$cg_sha"
cg_rc=$?
carm "a CONCLUDED FAILURE is published as the run's own verdict" \
    0 'conclude OK' 'REFUSED' "$(cg_out)" "$cg_rc"
cg_extra "and the failure is the state that was posted" \
    "last state=failure" \
    "$([ "$(cg_last_state)" = failure ] && echo true || echo false)"

CG_VENUE_LOG="$cg_fx/venue-unable.log"
cg_run red.json stale-red.json "$cg_fx/tree/scripts/gate-status.sh" conclude --sha "$cg_sha"
cg_rc=$?
CG_VENUE_LOG=""
carm "a red the venue MEASURED it cannot deliver is REFUSED, not republished" \
    2 'does not show it delivering a verdict' 'conclude OK' "$(cg_out)" "$cg_rc"
cg_extra "and that refusal posted nothing, so #1467's precedence is untouched" \
    "no POST reached the API" \
    "$([ -s "$cg_fx/posts" ] && echo false || echo true)"

# The box-side half of ask 1: the GATE's own attested verdict is published for the
# head it measured, over an earlier claim by an earlier step. `post --attestation`
# is the path the landing driver runs; here it proves the later verdict supersedes
# the earlier one rather than being refused by it.
cg_run success.json stale-red.json "$cg_fx/tree/scripts/gate-status.sh" \
    post --attestation "$cg_att" --sha "$cg_sha"
cg_rc=$?
carm "the GATE's own attested verdict is published over an earlier claim" \
    0 'posted' 'REFUSED' "$(cg_out)" "$cg_rc"
cg_extra "and it is the NEWEST claim for the commit, so it supersedes the red" \
    "last state=success on the commit that was measured" \
    "$([ "$(cg_last_state)" = success ] && echo true || echo false)"

# The field walk (#1470). `venue_agreement` prints MORE than the three fields
# `reconcile` consumes, so the url has to be TAKEN FROM the record rather than
# inherited from its remainder: the pre-#1504 walk shifted (`#*`) instead of
# truncating (`%%`), which on the field it consumed last is the no-op the sibling
# comment on `post` records -- leaving the build, region and project as extra
# LINES inside target_url, an API-invalid value the real API rejects (while this
# repository's own gates, which grep for the URL as a PREFIX, cannot see it). The
# arm is the SHAPE of the POST: exactly four fields, one line each.
cg_run red.json green.json "$cg_fx/tree/scripts/gate-status.sh" reconcile --sha "$cg_sha"
cg_rc=$?
carm "reconcile's withdrawal POST carries exactly its four fields" \
    0 'WITHDREW' '' "$(cg_out)" "$cg_rc"
cg_extra "and its target_url is the venue's URL on ONE line, not four" \
    "4 fields posted" \
    "$([ "$(cg_field_count)" = 4 ] && echo true || echo false)"

mkdir -p "$cg_fx/mut-walk/scripts"
sed 's|^    venue_url=.*$|    venue_url="$venue_rest"|' \
    "$POSTER" > "$cg_fx/mut-walk/scripts/gate-status.sh"
cp "$MAPPER" "$cg_fx/mut-walk/scripts/gate-status-map.py"
if cmp -s "$POSTER" "$cg_fx/mut-walk/scripts/gate-status.sh"; then
  echo "check-gate-status: FAIL — the field-walk MUTANT did not apply: the walk's text moved, so the falsification would prove nothing" >&2
  fail=1
else
  cg_run red.json green.json "$cg_fx/mut-walk/scripts/gate-status.sh" reconcile --sha "$cg_sha"
  cg_rc=$?
  cg_extra "MUTANT (the url's field extraction removed): the withdrawal's target_url swallows the fields after it" \
      "more than 4 fields posted (measured $(cg_field_count))" \
      "$([ "$(cg_field_count)" -gt 4 ] && echo true || echo false)"
fi

if [ "$controls" -ne "$expected_controls" ]; then
  echo "check-gate-status: FAIL — expected $expected_controls controls of 4g, ran $controls" >&2
  fail=1
fi

# 5. PRODUCER — the REQUIRED context must have a PRODUCER (#1357).
#
#    Everything above proves the poster's machinery; this proves the poster is
#    reached when a verdict is made. Measured 2026-09-18 while this section was
#    written: live protection required `ao/gate-of-record`, no path that makes
#    the verdict posted it, and this gate was GREEN -- the inertness was printed
#    as a note ("expected until the poster runs in the runner") and then
#    discarded. The question is asked over the TREE only, so it needs no network
#    and cannot be excused by one; the read-back below is the half that needs the
#    API, and a REQUIRED context that is not observed there is CANNOT-ASSESS.
#
#    Both refusals are provoked offline, in scripts/check-branch-protection.sh --
#    which drives THIS same function through `--producer-probe` -- so each half
#    of the rule is shown to be able to fail, and the negative control (nothing
#    REQUIRED) is shown NOT to fire.
producer_now=0
producer_out="$(producer_probe "$root" ask 2>&1)"
prc=$?
case "$producer_out" in
  *"required=1"*) producer_now=1 ;;
esac
probe_live="-"
producer_live_source="-"
# shellcheck disable=SC2034
live_field="$(printf '%s' "$producer_out" | grep -oE 'live=[^ ]+' | head -1)"
live_src_field="$(printf '%s' "$producer_out" | grep -oE 'live_source=[^ ]+' | head -1)"
live_field="${live_field#live=}"
live_src_field="${live_src_field#live_source=}"
case "$live_field" in
  enabled|disabled|unreadable) probe_live="$live_field" ;;
  *) probe_live="-" ;;
esac
case "$live_src_field" in
  ''|'-') producer_live_source="-" ;;
  *) producer_live_source="$live_src_field" ;;
esac
case "$prc" in
  0) printf '%s\n' "$producer_out" ;;
  1) printf '%s\n' "$producer_out" >&2; fail=1 ;;
  2) printf '%s\n' "$producer_out" >&2; cannot_assess=1 ;;
  *) printf '%s\n' "$producer_out" >&2
     echo "check-gate-status: FAIL — the producer probe returned an unexpected code $prc" >&2
     fail=1 ;;
esac

# 6. LIVE read-back — the half that reality can contradict.
#
#    #1394 replaced the per-HEAD question with a question about the MECHANISM,
#    because the per-head form made this check unable to reach OK on the
#    repository it polices: the required context is posted on the commits a
#    verdict was made for, not on every commit, so "is it on HEAD?" answers
#    "no" for most heads while the producer is demonstrably working. The verdict
#    is now read from the live producer state (section 5's probe already read it
#    -- `live=` carries it, so the two halves cannot disagree):
#
#      enabled    the context IS being produced -> this half passes
#      disabled   the AGE window was covered and holds no observation -> NOT-OK
#                 by name (rc 1): a finding about the window, not an inability
#                 to assess -- and the refusal says which window (#1460)
#      unreadable the source could NOT be read, or the window could not be
#                 covered -> CANNOT-ASSESS naming it
#
#    An unobserved context when nothing is REQUIRED is not a failure at all --
#    no merge is deadlocked by the absence of a check nobody requires.
live_rc=0
if [ "$producer_now" -eq 0 ]; then
  if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
    echo "  note  no $context_poster status observed — the context is not REQUIRED, so no merge is deadlocked by its absence"
  fi
else
  case "$probe_live" in
    enabled)
      echo "  OK  the LIVE producer state was OBSERVED on the repository ($producer_live_source): branch protection REQUIRES '$context_poster' and the context IS being produced, so the read-back half is satisfied by the mechanism rather than by one head" ;;
    disabled)
      echo "  FAIL REQUIRED-BUT-UNOBSERVED — branch protection REQUIRES '$context_poster' and the live window WAS read to its end ($producer_live_source), but no commit of the default branch inside it carries the context: the producer has not been OBSERVED in the window the read-back declares, which is a finding about the WINDOW rather than a claim that no producer exists — so the two remedies are 'make the producer post again' and 'widen AO_GATE_STATUS_MAX_AGE_DAYS deliberately', and until one of them the requirement cannot be satisfied and a merge would proceed only through the admin bypass (enforce_admins=false)"
      fail=1 ;;
    unreadable)
      echo "  CANNOT-ASSESS LIVE-PRODUCER-UNREADABLE — the live producer state could not be read from its source ($producer_live_source), so neither 'produced' nor 'gated off' can be claimed for the REQUIRED context '$context_poster': an unreadable source is never a pass"
      live_rc=2 ;;
    *)
      echo "  CANNOT-ASSESS  the live producer state was not resolved by the producer probe — not a pass"
      live_rc=2 ;;
  esac
fi

if [ "$fail" -ne 0 ]; then
  echo "check-gate-status: NOT-OK — a REQUIRED context has no producer, or the mapping or the poster is defective" >&2
  exit 1
fi
if [ "$cannot_assess" -ne 0 ]; then
  echo "check-gate-status: CANNOT-ASSESS — the mapping and the poster are sound, but the producing path or the LIVE status was NOT observed (see above)" >&2
  exit 2
fi
if [ "$live_rc" -eq 2 ]; then
  echo "check-gate-status: CANNOT-ASSESS — mapping and poster are sound, but the LIVE status was NOT observed"
  exit 2
fi
echo "check-gate-status: OK — the mapping is exhaustive and provoked, the REQUIRED context has a producer where the verdict is made, and the poster builds without writing"
exit 0
