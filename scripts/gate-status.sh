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
#   post    --attestation <file>         publish the rc the GATE ITSELF recorded,
#                                        bound to the commit it measured
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
mode=""
while [ $# -gt 0 ]; do
  case "$1" in
    post|show|dry-run) mode="$1" ;;
    --self-test) mode="self-test" ;;
    --sha) sha="${2:-}"; shift ;;
    --rc)  rc="${2:-}";  shift ;;
    --attestation) attestation="${2:-}"; shift ;;
    --repo) REPO="${2:-}"; shift ;;
    -h|--help) sed -n '2,36p' "$0"; exit 0 ;;
    *) die "unknown argument: $1" 1 ;;
  esac
  shift
done

[ "$mode" = "self-test" ] && { self_test; exit $?; }

[ -n "$mode" ] || die "usage: $0 {post|show|dry-run} --sha <sha> [--rc <0|1|2> | --attestation <file>] | --self-test" 2

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
    description="$(python3 - "$rc" <<'PY'
import sys
sys.path.insert(0, "scripts")
from importlib import util
spec = util.spec_from_file_location("gsmap", "scripts/gate-status-map.py")
mod = util.module_from_spec(spec); spec.loader.exec_module(mod)
print(mod.summarize(int(sys.argv[1])))
PY
)"
    if [ "$mode" = "dry-run" ]; then
      echo "gate-status: DRY-RUN — would POST"
      echo "  repo        = $REPO"
      echo "  sha         = $sha"
      echo "  context     = $CONTEXT"
      echo "  state       = $state"
      echo "  description = $description"
      exit 0
    fi

    if command -v gh >/dev/null 2>&1; then
      gh api -X POST "repos/$REPO/statuses/$sha" \
          -f state="$state" -f context="$CONTEXT" -f description="$description" \
          >/dev/null 2>/tmp/gs-post-err.txt \
        || die "CANNOT-ASSESS — the POST failed: $(head -c 200 /tmp/gs-post-err.txt)" 2
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
          -X POST "https://api.github.com/repos/$REPO/statuses/$sha" \
          -H "Authorization: Bearer $(resolve_token)" \
          -H 'Accept: application/vnd.github+json' \
          -d "$(python3 -c 'import json,sys; print(json.dumps({"state": sys.argv[1], "context": sys.argv[2], "description": sys.argv[3]}))' "$state" "$CONTEXT" "$description")" \
        2>/tmp/gs-post-err.txt)" || http_code="000"
      case "$http_code" in
        2??) : ;;
        *) die "CANNOT-ASSESS — the POST failed (HTTP $http_code): $(head -c 200 /tmp/gs-post-err.txt)" 2 ;;
      esac
    fi
    echo "gate-status: posted $CONTEXT=$state for ${sha:0:12} ($description)"
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
