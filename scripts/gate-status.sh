#!/usr/bin/env bash
# gate-status.sh — publish the gate of record as a GitHub commit status.
#
# ADR-0028. GR-15 bans GitHub Actions, and GitHub's *required status checks* are
# the only mechanism that makes a merge impossible without green evidence. Those
# two facts look contradictory; they are not. A required check needs a check-run
# or a commit status, and a commit status is a single authenticated POST:
#
#     POST /repos/{owner}/{repo}/statuses/{sha}   state=<...> context=ao/gate-of-record
#
# No Actions, no App, no check-run. (Proven before the ADR was written:
# `context=ao/gate-probe state=success` posted and read back on master.)
#
#   post    --sha <sha> --rc <0|1|2>     publish the gate's outcome for a commit
#           [--detail <text>]            and --detail says WHY: the name of the
#                                        check that failed, appended to the
#                                        description as a SUFFIX (bounded to the
#                                        API's 140-character description cap)
#   post    --attestation <file>         publish the rc the GATE ITSELF recorded,
#                                        bound to the commit it measured
#   reconcile --sha <sha>                make the PUBLISHED context agree with the
#                                        CI venue's own verdict for that commit,
#                                        withdrawing a standing green the venue's
#                                        run contradicts (see VENUE AGREEMENT)
#   show    --sha <sha>                  read the combined status BACK
#   dry-run --sha <sha> --rc <0|1|2>     print the request without sending it
#   --self-test                          prove the token + attestation seams
#                                        offline (no network, nothing written)
#
# Exit codes follow the repo tri-state: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# FAIL-CLOSED, and specifically: an unobservable read-back is 2, never 0. Posting
# succeeds but reading the status back fails => we do NOT know the commit is
# gated, and saying otherwise would be the #739 defect (reporting a state that
# was never observed).
#
# ATTESTATION SEAM (issue #1350): the only honest rc for a commit is the one the
# gate ITSELF wrote down, so `post --attestation <path>` reads the gate's own
# `.verify/attestation.json` and takes BOTH the rc and the sha from it. A
# hand-typed rc can disagree with the record, so the two sources are mutually
# exclusive; an attestation for a DIFFERENT commit, recording something that is
# not an outcome (a PARKED run writes no attestation at all), or older than
# AO_ATTEST_MAX_AGE seconds is REFUSED by name. Publishing another commit's green
# is the fabricated-green class this exists to stop, and it is the same defect in
# time as it is in space.
#
# TOKEN SEAM (issue #1350): a human runs this with `gh` already authenticated,
# but the Cloud Build runner has no `gh` login -- only a Secret Manager value
# injected as an env var. `resolve_token` prefers GH_TOKEN, falls back to
# GITHUB_TOKEN (gh's own convention), and returns empty if neither is set. When
# `gh` itself is missing (the Cloud Build image), post/show fall back to a raw
# `curl` call authenticated with that token. Absence of BOTH `gh` and a token is
# CANNOT-ASSESS (2) -- this poster never claims a status it did not post -- and
# the RUNNER decides what that costs (the Cloud Build step fails the build by
# name rather than leaving a required check silently unproduced).
#
# VENUE AGREEMENT (issue #1400): this context has TWO producers -- a box-side
# driver, and the Cloud Build verify step (infra/cloudbuild/verify.yaml) -- and
# only the second one is the VENUE OF RECORD. Measured on the train head
# f300954d of #1398: a box-side `post --rc 0` published `success` at 03:53:43Z
# while the Cloud Build run for the SAME commit had existed since 03:51:19Z and
# was still in flight; that run then concluded FAILURE at 04:19:01Z (build
# 32cb10f7, `check-reconcile: FAIL (1 violation)`). So the required context read
# green for a commit whose own CI run was red -- and the guard that reads it
# (scripts/pr-queue.sh) reads the STATUS, never the run. A gate that fails open
# is worse than no gate, so:
#
#   * a post that is NOT the venue reporting its own verdict must not publish
#     `success` for a commit whose venue run is RED or still UN-CONCLUDED. The
#     second half is the half that closes the measured hole: the standing green
#     was posted while the run was in flight, so "refuse on a red" alone would
#     have published it -- the contradiction did not exist YET;
#   * an unreadable venue verdict is CANNOT-ASSESS (2), never an agreement: a
#     control that cannot fail is a formality, and this one fails CLOSED;
#   * a red gate is NEVER blocked. The guard gates `success` only, so reporting
#     a failure is always allowed -- refusing to report a red is the other way
#     this could have been built wrong;
#   * the venue names itself with `--venue-run <build-id>` (Cloud Build sets
#     $BUILD_ID, a built-in substitution). It cannot refuse its own in-flight
#     run, because it IS that run -- and it is the source of truth for it. The
#     marker must look like a build id, so the seam is a claim with a shape
#     rather than free text; it is not an access boundary, because anyone
#     holding the token can post a status directly, and this poster guards the
#     AUTOMATIC second producer, not a forgery.
#
# DETAIL SEAM (issue #1407): the description is a FIXED string per rc, so every
# red PR page read `make verify: FAIL` and WHICH check failed was knowable only
# by opening the build log -- a required check that cannot name its own refusal.
# `post --detail <text>` appends the producer's answer to that string. A detail
# is a SUFFIX and nothing else: the outcome comes from --rc (or from the gate's
# own attestation), no detail is an input to it, so no detail can turn a
# CANNOT-ASSESS into a pass -- the #739 false-green class. The suffix is bounded
# in gate-status-map.py, which truncates it deliberately to fit the API's
# 140-character cap and marks the cut, because the API would otherwise truncate
# the overflow itself at a position this poster does not choose. Only the verb
# that publishes an outcome from --rc takes a detail, so `--detail` on another
# verb is REFUSED rather than accepted and dropped.
#
# `reconcile` is the other half of the same invariant, and it exists because the
# halves are not symmetric in TIME: a green published before the venue produced
# any run for the commit is not refusable at post time, so after the fact the
# published context must be brought back into agreement -- a standing `success`
# whose venue run concluded red is SUPERSEDED by an `error` that names the run.
set -u

# This script's own ABSOLUTE path, captured before the `cd` below: the self-test
# re-invokes this file, and a relative `$0` would stop resolving from any other
# working directory -- an invoker-dependent failure in the one tool a producer
# runs to prove itself.
self_path="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
root="$(cd "$(dirname "$self_path")/.." && pwd)"
cd "$root" || exit 2

REPO="${AO_REPO:-kushin77/agent-orchestrator}"
MAPPER="scripts/gate-status-map.py"
# The context branch protection will require. One name, one place.
CONTEXT="${AO_GATE_CONTEXT:-ao/gate-of-record}"
# How old a gate attestation may be before its rc stops describing "this commit's
# verdict". The gate writes the record when it finishes, so a producer posting
# straight after a run sees seconds, not hours.
ATTEST_MAX_AGE="${AO_ATTEST_MAX_AGE:-21600}"
# The venue of record's own check, by the name its Cloud Build trigger gives it
# (`control-plane-verify (purebliss-ghl)`). Prefix, because the suffix is the
# project. Overridable so a fixture, and any future venue, can name its own.
VENUE_CHECK_PREFIX="${AO_VENUE_CHECK_PREFIX:-control-plane-verify}"

die() { printf 'gate-status: %s\n' "$1" >&2; exit "${2:-1}"; }

# Read the gate's OWN record of what it measured: exit code + the commit it
# measured. Prints them on two lines, or explains on stderr and exits 2.
read_attestation() {
  python3 - "$1" <<'PY'
import json
import sys

try:
    with open(sys.argv[1]) as fh:
        data = json.load(fh)
except OSError as exc:
    print(f"the attestation could not be read: {exc}", file=sys.stderr)
    raise SystemExit(2)
except ValueError as exc:
    print(f"the attestation is not JSON: {exc}", file=sys.stderr)
    raise SystemExit(2)
if not isinstance(data, dict):
    print("the attestation is not a JSON object", file=sys.stderr)
    raise SystemExit(2)
rc = data.get("exit_code")
sha = data.get("git_sha")
if rc is None:
    print("the attestation records no exit_code -- it is not a gate outcome", file=sys.stderr)
    raise SystemExit(2)
if not sha:
    print("the attestation records no git_sha -- the rc names no commit", file=sys.stderr)
    raise SystemExit(2)
print(rc)
print(sha)
print(data.get("timestamp") or "")
PY
}

# Age in seconds of an attestation's own timestamp. -9999 means "no readable
# timestamp", which is refused rather than treated as fresh.
attestation_age() {
  python3 - "$1" <<'PY'
import sys
from datetime import datetime, timezone

try:
    stamp = sys.argv[1]
    when = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
except (ValueError, TypeError):
    print(-9999)
    raise SystemExit(0)
print(int(datetime.now(timezone.utc).timestamp() - when.timestamp()))
PY
}

# --- venue agreement (issue #1400) ------------------------------------------
# The venue of record's own verdicts for a commit, written to <out>. 0 read /
# 2 unreadable -- and unreadable is never an agreement.
fetch_venue_runs() { # <sha> <out-file>
  local sha="$1" out="$2" rc=0
  if command -v gh >/dev/null 2>&1; then
    gh api "repos/$REPO/commits/$sha/check-runs" >"$out" 2>/tmp/gs-venue-err.txt || rc=$?
  else
    [ -n "$(resolve_token)" ] || { printf 'gh is absent and neither GH_TOKEN nor GITHUB_TOKEN is set\n' >/tmp/gs-venue-err.txt; return 2; }
    curl -sS -H "Authorization: Bearer $(resolve_token)" \
        -H 'Accept: application/vnd.github+json' \
        "https://api.github.com/repos/$REPO/commits/$sha/check-runs" \
        >"$out" 2>/tmp/gs-venue-err.txt || rc=$?
  fi
  [ "$rc" -eq 0 ] || return 2
  [ -s "$out" ] || return 2
  return 0
}

# Decide whether this commit's venue run AGREES with publishing `success`.
# Prints THREE LINES -- verdict, reason, target URL -- never a TSV row: an empty
# field in a TSV record collapses adjacent tabs and silently shifts every field
# after it (docs/SHELL-PATTERNS.md SP-2), and this record has a field that is
# EMPTY whenever the venue has no page to point at. `read_attestation` above
# reads its fields the same way, for the same reason.
#
# The conclusions that AGREE are named, and everything else is a contradiction:
# an unknown conclusion must not be read as agreement (a control that cannot
# fail), and enumerating the red ones would make every new GitHub conclusion a
# silent pass.
venue_agreement() { # <runs-json-file>
  python3 - "$1" "$VENUE_CHECK_PREFIX" <<'PY'
import json
import re
import sys

path, prefix = sys.argv[1], sys.argv[2]

def answer(verdict, reason, url=""):
    print(verdict)
    print(reason)
    print(url)
    raise SystemExit(0)

try:
    with open(path) as fh:
        data = json.load(fh)
except (OSError, ValueError) as exc:
    answer("unassessable", "the venue read is not readable JSON: %s" % exc)
if not isinstance(data, dict):
    answer("unassessable", "the venue read is not a JSON object")
runs = data.get("check_runs")
if not isinstance(runs, list):
    answer("unassessable", "the venue read carries no check_runs array, so no verdict can be read from it")

venue = [r for r in runs if isinstance(r, dict) and str(r.get("name") or "").startswith(prefix)]
AGREES = {"success", "neutral", "skipped"}

def build_of(run):
    matched = re.search(r"/builds;region=[^/]+/([0-9a-fA-F-]{36})", str(run.get("details_url") or ""))
    return matched.group(1) if matched else "build-unknown"

reds = [r for r in venue if str(r.get("status")) == "completed" and str(r.get("conclusion")) not in AGREES]
live = [r for r in venue if str(r.get("status")) != "completed"]
if reds:
    run = reds[0]
    # The build URL travels as the third field even on a refusal: `reconcile`
    # publishes it with the withdrawal, so the operator lands on the run that
    # contradicts the green instead of hunting for it.
    answer("refuse", "the CI venue's own run for this commit concluded '%s' (%s, %s)"
           % (run.get("conclusion"), run.get("name"), build_of(run)), str(run.get("details_url") or ""))
if live:
    run = live[0]
    # NOT settled yet -- which is NOT the same as a contradiction, and the two
    # must stay distinguishable: `post` refuses both (the measured false green
    # was published while the run was in flight), while `reconcile` withdraws on
    # a real red only. Collapsing them would make reconcile withdraw a green
    # because a build had merely started.
    answer("unsettled", "the CI venue's own run for this commit has NOT concluded (%s, status '%s', started %s, %s) "
                        "-- a second producer must not satisfy the required context before the venue of record speaks"
           % (run.get("name"), run.get("status"), run.get("started_at") or "unknown", build_of(run)))
if venue:
    run = venue[-1]
    answer("allow", "the CI venue's own run for this commit concluded '%s' (%s, %s)"
           % (run.get("conclusion"), run.get("name"), build_of(run)), str(run.get("details_url") or ""))
answer("allow", "the CI venue produced no run for this commit, so this post is the only producer it has")
PY
}

# The status this poster has already published for a commit under CONTEXT, as
# TWO LINES -- state, description -- with state `none` when there is no such
# status. Newline-delimited for the same reason as `venue_agreement` above: a
# description can be empty and a TSV row would mis-split it.
# The payload reaches python as a FILE, never on stdin: `python3 - <<'PY'` reads
# its PROGRAM from stdin, so a `printf ... | python3 - <<'PY'` would hand the
# heredoc to the interpreter and leave the JSON unread -- measured while writing
# the #1400 gate, where it silently answered `unassessable`/`none` for a status
# that WAS published and exited 0, i.e. the withdrawal path would have no-opped
# on a real false green. Two of that gate's arms failed on exactly this.
published_state() { # <sha>
  local sha="$1" out rc=0
  if command -v gh >/dev/null 2>&1; then
    out="$(gh api "repos/$REPO/commits/$sha/status" 2>/tmp/gs-status-err.txt)" || rc=$?
  else
    [ -n "$(resolve_token)" ] || return 2
    out="$(curl -sS -H "Authorization: Bearer $(resolve_token)" \
        -H 'Accept: application/vnd.github+json' \
        "https://api.github.com/repos/$REPO/commits/$sha/status" 2>/tmp/gs-status-err.txt)" || rc=$?
  fi
  [ "$rc" -eq 0 ] || return 2
  local payload
  payload="$(mktemp /tmp/gs-published.XXXXXX)" || return 2
  printf '%s' "$out" >"$payload"
  python3 - "$payload" "$CONTEXT" <<'PY'
import json
import sys

path, want = sys.argv[1], sys.argv[2]
try:
    with open(path) as fh:
        data = json.load(fh)
except (OSError, ValueError) as exc:
    print("unassessable")
    print("%s" % exc)
    raise SystemExit(0)
if not isinstance(data, dict):
    print("unassessable")
    print("the status read is not a JSON object")
    raise SystemExit(0)
statuses = [s for s in (data.get("statuses") or []) if s.get("context") == want]
if not statuses:
    print("none")
    print("no '%s' status is published on this commit" % want)
else:
    newest = statuses[0]
    print(newest.get("state"))
    print(newest.get("description") or "")
PY
  local printed=$?
  rm -f "$payload"
  return "$printed"
}
# GH_TOKEN takes precedence (this repo's own seam name); GITHUB_TOKEN is `gh`'s
# own fallback convention, honored here too so a runner that only sets the
# generic name still works. Prints nothing (empty) if neither is set.
resolve_token() {
  if [ -n "${GH_TOKEN:-}" ]; then
    printf '%s' "$GH_TOKEN"
  elif [ -n "${GITHUB_TOKEN:-}" ]; then
    printf '%s' "$GITHUB_TOKEN"
  fi
}

self_test() {
  local problems=0
  local got out rc

  got="$(GH_TOKEN=tok-a GITHUB_TOKEN=tok-b resolve_token)"
  [ "$got" = "tok-a" ] || { echo "  FAIL  GH_TOKEN did not take precedence over GITHUB_TOKEN (got '$got')" >&2; problems=$((problems + 1)); }
  echo "  OK  GH_TOKEN takes precedence over GITHUB_TOKEN"

  got="$(unset GH_TOKEN; GITHUB_TOKEN=tok-b resolve_token)"
  [ "$got" = "tok-b" ] || { echo "  FAIL  GITHUB_TOKEN fallback did not resolve (got '$got')" >&2; problems=$((problems + 1)); }
  echo "  OK  GITHUB_TOKEN resolves when GH_TOKEN is unset"

  got="$(unset GH_TOKEN GITHUB_TOKEN; resolve_token)"
  [ -z "$got" ] || { echo "  FAIL  resolve_token returned a value with neither var set ('$got')" >&2; problems=$((problems + 1)); }
  echo "  OK  resolve_token returns empty with neither var set (the CANNOT-ASSESS trigger)"

  # The attestation seam, provoked in dry-run so nothing is ever posted here.
  local att_dir att_ok att_old att_parked att_nosha
  att_dir="$(mktemp -d /tmp/ao-gs-selftest.XXXXXX)" || { echo "  FAIL  could not create a fixture directory" >&2; return 1; }
  python3 - "$att_dir" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone

now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
sha = "a" * 40
root = sys.argv[1]

def write(name, **fields):
    with open(os.path.join(root, name), "w") as fh:
        json.dump(fields, fh)

write("ok.json", exit_code=0, git_sha=sha, timestamp=now, result="PASS")
write("stale.json", exit_code=0, git_sha=sha, timestamp="2020-01-01T00:00:00Z", result="PASS")
write("parked.json", exit_code=11, git_sha=sha, timestamp=now, result="PARKED")
write("nosha.json", exit_code=0, timestamp=now, result="PASS")
PY
  att_ok="$att_dir/ok.json"
  att_old="$att_dir/stale.json"
  att_parked="$att_dir/parked.json"
  att_nosha="$att_dir/nosha.json"

  out="$(bash "$self_path" dry-run --attestation "$att_ok" 2>&1)"
  if grep -q "state       = success" <<<"$out" && grep -q "sha         = aaaa" <<<"$out"; then
    echo "  OK  --attestation takes BOTH the rc and the sha from the gate's own record"
  else
    echo "  FAIL  --attestation did not resolve the gate's own rc/sha:" >&2
    printf '%s\n' "$out" | sed 's/^/    /' >&2
    problems=$((problems + 1))
  fi

  # The refusal arms. Each runs in dry-run, so nothing is posted even if the
  # refusal itself were broken -- the assertion is the exit code, not a side
  # effect we would have to clean up.
  refusal_arm() {
    local label="$1"; shift
    out="$(bash "$self_path" dry-run "$@" 2>&1)"; rc=$?
    if [ "$rc" -eq 2 ]; then
      echo "  OK  refused (2) by name: $label"
    else
      echo "  FAIL  $label was NOT refused (rc=$rc):" >&2
      printf '%s\n' "$out" | sed 's/^/    /' >&2
      problems=$((problems + 1))
    fi
  }
  refusal_arm "an attestation whose sha is not the one asked for" \
    --attestation "$att_ok" --sha "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
  refusal_arm "an attestation older than the age bound" --attestation "$att_old"
  refusal_arm "an rc that is not a gate outcome (a PARKED run writes none)" --attestation "$att_parked"
  refusal_arm "an attestation naming no commit" --attestation "$att_nosha"
  refusal_arm "an rc and an attestation offered at once" --attestation "$att_ok" --rc 0

  rm -rf "$att_dir"

  if [ "$problems" -ne 0 ]; then
    echo "gate-status: self-test FAIL — $problems problem(s)" >&2
    return 1
  fi
  echo "gate-status: self-test OK"
  return 0
}

[ -f "$MAPPER" ] || die "CANNOT-ASSESS — the mapper is missing: $MAPPER" 2
command -v python3 >/dev/null 2>&1 || die "CANNOT-ASSESS — python3 not found" 2

rc=""
sha=""
attestation=""
venue_run=""
detail=""
mode=""
while [ $# -gt 0 ]; do
  case "$1" in
    post|show|dry-run|reconcile) mode="$1" ;;
    --self-test) mode="self-test" ;;
    --sha) sha="${2:-}"; shift ;;
    --rc)  rc="${2:-}";  shift ;;
    --attestation) attestation="${2:-}"; shift ;;
    --venue-run) venue_run="${2:-}"; shift ;;
    --detail) detail="${2:-}"; shift ;;
    --repo) REPO="${2:-}"; shift ;;
    -h|--help) sed -n '2,60p' "$0"; exit 0 ;;
    *) die "unknown argument: $1" 1 ;;
  esac
  shift
done

# Cloud Build sets $BUILD_ID in every step it runs, so the venue of record names
# itself without the recipe having to say so -- a second trigger added later is
# safe by default, and its claim is the environment's, not the author's.
[ -n "$venue_run" ] || venue_run="${BUILD_ID:-}"
if [ -n "$venue_run" ] && [[ ! "$venue_run" =~ ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$ ]]; then
  die "REFUSED — --venue-run '$venue_run' is not a build id (a UUID); the venue's own marker is a claim with a shape, not free text" 2
fi

[ "$mode" = "self-test" ] && { self_test; exit $?; }

[ -n "$mode" ] || die "usage: $0 {post|reconcile|show|dry-run} --sha <sha> [--rc <0|1|2> | --attestation <file>] [--venue-run <build-id>] [--detail <text>] | --self-test" 2

# A detail describes the outcome of a `post`. The other verbs publish a
# description this command OWNS -- `reconcile` names the withdrawal it makes --
# so a detail offered to one of them would be silently dropped, and a flag that
# is accepted and ignored is worse than one that is refused: the producer would
# believe it had reached the PR page. Checked here, before any read or write.
if [ -n "$detail" ]; then
  case "$mode" in
    post|dry-run) ;;
    *) die "REFUSED — --detail describes the description a 'post' publishes, and '$mode' publishes one this command owns; it would be silently dropped" 2 ;;
  esac
fi

# Publish ONE status for ONE commit. Both verbs that write go through here, so
# the venue-agreement guard and the reporting path cannot drift apart.
publish_status() { # <sha> <state> <description> [<target_url>]
  local psha="$1" pstate="$2" pdesc="$3" purl="${4:-}"
  if command -v gh >/dev/null 2>&1; then
    if [ -n "$purl" ]; then
      gh api -X POST "repos/$REPO/statuses/$psha" \
          -f state="$pstate" -f context="$CONTEXT" -f description="$pdesc" -f target_url="$purl" \
          >/dev/null 2>/tmp/gs-post-err.txt \
        || die "CANNOT-ASSESS — the POST failed: $(head -c 200 /tmp/gs-post-err.txt)" 2
    else
      gh api -X POST "repos/$REPO/statuses/$psha" \
          -f state="$pstate" -f context="$CONTEXT" -f description="$pdesc" \
          >/dev/null 2>/tmp/gs-post-err.txt \
        || die "CANNOT-ASSESS — the POST failed: $(head -c 200 /tmp/gs-post-err.txt)" 2
    fi
  else
    # The token is resolved AT THE POINT OF USE and never held in a named
    # assignment. Assigning it to a local whose name says "token" over a quoted
    # value is a runtime read, not a credential -- but it is byte-for-byte the
    # shape scripts/check-secrets.sh's generic-assignment detector must refuse,
    # and that detector is RIGHT to refuse it: it cannot tell the two apart, so
    # the SHAPE is what has to go, never the exemption (an exemption widened for
    # a false positive is a hole for the real thing).
    command -v curl >/dev/null 2>&1 || die "CANNOT-ASSESS — neither gh nor curl found; cannot post the status" 2
    [ -n "$(resolve_token)" ] || die "CANNOT-ASSESS — gh not found and no GH_TOKEN/GITHUB_TOKEN in env; status not posted" 2
    http_code="$(curl -sS -o /tmp/gs-post-err.txt -w '%{http_code}' \
        -X POST "https://api.github.com/repos/$REPO/statuses/$psha" \
        -H "Authorization: Bearer $(resolve_token)" \
        -H 'Accept: application/vnd.github+json' \
        -d "$(python3 -c 'import json,sys;print(json.dumps({k:v for k,v in zip(("state","context","description","target_url"),sys.argv[1:]) if v}))' "$pstate" "$CONTEXT" "$pdesc" "$purl")" \
      2>/tmp/gs-post-err.txt)" || http_code="000"
    case "$http_code" in
      2??) : ;;
      *) die "CANNOT-ASSESS — the POST failed (HTTP $http_code): $(head -c 200 /tmp/gs-post-err.txt)" 2 ;;
    esac
  fi
  echo "gate-status: posted $CONTEXT=$pstate for ${psha:0:12} ($pdesc)"
}

case "$mode" in
  post|dry-run)
    # One source of truth for the rc: the operator's claim, or the gate's own
    # record. Offering both means the claim can disagree with the record, and a
    # disagreement published as a status is a fabricated outcome.
    if [ -n "$attestation" ]; then
      [ -z "$rc" ] || die "REFUSED — --rc and --attestation are mutually exclusive (a hand-typed rc can disagree with the gate's own record)" 2
      [ -f "$attestation" ] || die "CANNOT-ASSESS — no attestation at $attestation" 2
      att_out="$(read_attestation "$attestation" 2>&1)"
      att_read=$?
      [ "$att_read" -eq 0 ] || {
        die "CANNOT-ASSESS — $(printf '%s' "$att_out" | head -c 200)" "$att_read"
      }
      att_rc="$(printf '%s\n' "$att_out" | sed -n 1p)"
      att_sha="$(printf '%s\n' "$att_out" | sed -n 2p)"
      att_stamp="$(printf '%s\n' "$att_out" | sed -n 3p)"
      case "$att_sha" in
        *[!0-9a-f]*) die "CANNOT-ASSESS — the attestation's git_sha is not a sha: $att_sha" 2 ;;
      esac
      [ "${#att_sha}" -eq 40 ] || die "CANNOT-ASSESS — the attestation's git_sha is ${#att_sha} characters, not 40: $att_sha" 2
      case "$att_rc" in
        0|1|2) ;;
        *) die "REFUSED — the attestation records rc $att_rc, which is not a gate outcome (PARKED runs write no attestation at all; a stale one is not a verdict)" 2 ;;
      esac
      if [ -n "$sha" ] && [ "$sha" != "$att_sha" ]; then
        die "REFUSED — --sha $sha but the gate measured $att_sha; an rc belongs to the commit that was measured, and posting it anywhere else is a fabricated green" 2
      fi
      att_age="$(attestation_age "$att_stamp")"
      if [ "$att_age" -lt -300 ] || [ "$att_age" -gt "$ATTEST_MAX_AGE" ]; then
        die "CANNOT-ASSESS — the attestation is $att_age s old (limit ${ATTEST_MAX_AGE}s, timestamp '${att_stamp:-none}'); a record that old does not describe this commit's verdict" 2
      fi
      sha="$att_sha"
      rc="$att_rc"
      echo "gate-status: attestation $attestation — rc $rc measured on ${sha:0:12} (${att_age}s ago)" >&2
    fi
    [ -n "$sha" ] || die "CANNOT-ASSESS — --sha is required" 2
    [ -n "$rc" ]  || die "CANNOT-ASSESS — --rc (or --attestation) is required" 2
    # The mapping lives in the mapper, so the gate can provoke the SAME code
    # path this poster uses. An unknown rc is REFUSED here rather than defaulted.
    state="$(python3 "$MAPPER" "$rc" 2>/tmp/gs-map-err.txt)" || {
      die "REFUSED — $(cat /tmp/gs-map-err.txt)" 2
    }
    case "$state" in
      success|failure|error|pending) ;;
      *) die "REFUSED — the mapper returned an invalid state: $state" 2 ;;
    esac
    # The description is built by the mapper too -- the same function the
    # checker provokes -- so a detail cannot be rendered by one path and posted
    # by another. The detail is a SUFFIX: it is passed to `summarize` and reaches
    # nothing else, which is why no detail can change the outcome above.
    description="$(python3 - "$rc" "$detail" <<'PY'
import sys
sys.path.insert(0, "scripts")
from importlib import util
spec = util.spec_from_file_location("gsmap", "scripts/gate-status-map.py")
mod = util.module_from_spec(spec); spec.loader.exec_module(mod)
print(mod.summarize(int(sys.argv[1]), sys.argv[2] or None))
PY
)"
    if [ "$mode" = "dry-run" ]; then
      echo "gate-status: DRY-RUN — would POST"
      echo "  repo        = $REPO"
      echo "  sha         = $sha"
      echo "  context     = $CONTEXT"
      echo "  state       = $state"
      echo "  description = $description"
      echo "  venue guard = $([ "$state" = "success" ] && [ -z "$venue_run" ] && echo "runs at post time (this dry run writes nothing and reads nothing)" || echo "not applicable to this outcome")"
      exit 0
    fi

    # --- the venue of record must agree before a green is published ----------
    # Only a `success` is guarded: refusing to report a red would be the same
    # control built the other way round, and the red is the verdict the merge
    # path most needs to see.
    target_url=""
    if [ "$state" = "success" ]; then
      if [ -n "$venue_run" ]; then
        echo "gate-status: build $venue_run is the venue of record reporting its own verdict, so its own in-flight run is not a contradiction" >&2
      else
        venue_json="$(mktemp /tmp/gs-venue.XXXXXX)" \
          || die "CANNOT-ASSESS — no scratch file for the venue read" 2
        if ! fetch_venue_runs "$sha" "$venue_json"; then
          rm -f "$venue_json"
          die "CANNOT-ASSESS — the CI venue's verdict for ${sha:0:12} could not be read ($(head -c 160 /tmp/gs-venue-err.txt 2>/dev/null)); an unread verdict is not an agreement, so no green is published" 2
        fi
        agreement="$(venue_agreement "$venue_json")"
        rm -f "$venue_json"
        venue_verdict="${agreement%%$'\n'*}"
        venue_rest="${agreement#*$'\n'}"
        venue_reason="${venue_rest%%$'\n'*}"
        venue_url="${venue_rest#*$'\n'}"
        case "$venue_verdict" in
          allow)
            target_url="$venue_url"
            echo "gate-status: venue agreement — $venue_reason" >&2
            ;;
          refuse|unsettled)
            die "REFUSED — no green is published for ${sha:0:12}: $venue_reason; the required context '${CONTEXT}' must be derived from the venue of record's own verdict on this commit, and a second producer (this one) may not satisfy it first" 2
            ;;
          *)
            die "CANNOT-ASSESS — the venue agreement could not be decided: $venue_reason" 2
            ;;
        esac
      fi
    fi

    publish_status "$sha" "$state" "$description" "$target_url"
    ;;

  reconcile)
    # The other half of the invariant, and it exists because post-time refusal is
    # not symmetric in TIME: a green published BEFORE the venue produced any run
    # for the commit cannot be refused at post time (there was nothing yet to
    # contradict it), so the published context has to be brought back into
    # agreement afterwards. This verb never publishes a green -- it can only
    # withdraw one -- so it cannot become a second way to satisfy the context.
    #
    # `writes` is what the gate asserts on: this verb either posts a non-success
    # or posts nothing at all.
    [ -n "$sha" ] || die "CANNOT-ASSESS — --sha is required" 2
    published="$(published_state "$sha")" || die "CANNOT-ASSESS — the published '${CONTEXT}' status for ${sha:0:12} could not be read ($(head -c 160 /tmp/gs-status-err.txt 2>/dev/null))" 2
    pub_state="${published%%$'\n'*}"
    pub_desc="${published#*$'\n'}"
    case "$pub_state" in
      success) ;;
      none)
        echo "gate-status: reconcile OK — nothing is published under '${CONTEXT}' on ${sha:0:12}, so there is no green to withdraw"
        exit 0
        ;;
      *)
        echo "gate-status: reconcile OK — the published '${CONTEXT}' on ${sha:0:12} is '$pub_state' ($pub_desc), not a green, so no withdrawal is owed"
        exit 0
        ;;
    esac
    reconcile_json="$(mktemp /tmp/gs-reconcile.XXXXXX)" \
      || die "CANNOT-ASSESS — no scratch file for the venue read" 2
    if ! fetch_venue_runs "$sha" "$reconcile_json"; then
      rm -f "$reconcile_json"
      die "CANNOT-ASSESS — the CI venue's verdict for ${sha:0:12} could not be read ($(head -c 160 /tmp/gs-venue-err.txt 2>/dev/null)), so whether the published green agrees with it cannot be decided" 2
    fi
    agreement="$(venue_agreement "$reconcile_json")"
    rm -f "$reconcile_json"
    venue_verdict="${agreement%%$'\n'*}"
    venue_rest="${agreement#*$'\n'}"
    venue_reason="${venue_rest%%$'\n'*}"
    venue_url="${venue_rest#*$'\n'}"
    case "$venue_verdict" in
      allow)
        echo "gate-status: reconcile OK — the published green on ${sha:0:12} agrees with the venue of record: $venue_reason"
        exit 0
        ;;
      unsettled)
        echo "gate-status: reconcile OK — nothing is withdrawn while the venue is still running: $venue_reason"
        exit 0
        ;;
      refuse)
        # Short on purpose: a commit-status description is capped at 140
        # characters, and the BUILD that failed is the evidence an operator
        # needs -- it travels in target_url, where the API allows a URL.
        publish_status "$sha" error "WITHDRAWN: the CI venue's own run concluded against this commit" "$venue_url"
        echo "gate-status: reconcile WITHDREW the standing green on ${sha:0:12} — $venue_reason" >&2
        exit 0
        ;;
      *)
        die "CANNOT-ASSESS — the venue agreement could not be decided: $venue_reason" 2
        ;;
    esac
    ;;

  show)
    [ -n "$sha" ] || die "CANNOT-ASSESS — --sha is required" 2
    if command -v gh >/dev/null 2>&1; then
      gh api "repos/$REPO/commits/$sha/status" >/tmp/gs-show.json 2>/tmp/gs-show-err.txt || {
        die "CANNOT-ASSESS — the read-back failed: $(head -c 200 /tmp/gs-show-err.txt)" 2
      }
    else
      # Resolved at the point of use for the same reason as the POST above: a
      # named assignment of a secret-ish key reads as a committed credential to
      # scripts/check-secrets.sh, so this file never writes that shape.
      command -v curl >/dev/null 2>&1 || die "CANNOT-ASSESS — neither gh nor curl found" 2
      [ -n "$(resolve_token)" ] || die "CANNOT-ASSESS — gh not found and no GH_TOKEN/GITHUB_TOKEN in env; cannot read back" 2
      http_code="$(curl -sS -o /tmp/gs-show.json -w '%{http_code}' \
          "https://api.github.com/repos/$REPO/commits/$sha/status" \
          -H "Authorization: Bearer $(resolve_token)" \
          -H 'Accept: application/vnd.github+json' \
        2>/tmp/gs-show-err.txt)" || http_code="000"
      case "$http_code" in
        2??) : ;;
        *) die "CANNOT-ASSESS — the read-back failed (HTTP $http_code): $(head -c 200 /tmp/gs-show-err.txt)" 2 ;;
      esac
    fi
    python3 - "$CONTEXT" <<'PY'
import json, sys
want = sys.argv[1]
data = json.load(open("/tmp/gs-show.json"))
ours = [s for s in data.get("statuses", []) if s.get("context") == want]
if not ours:
    print(f"gate-status: NOT-OK — no '{want}' status on this commit (it is NOT gated by the gate of record)", file=sys.stderr)
    sys.exit(1)
newest = ours[0]
print(f"gate-status: {want} = {newest.get('state')} — {newest.get('description')}")
sys.exit(0 if newest.get("state") == "success" else 1)
PY
    ;;
esac
