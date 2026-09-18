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
#   show    --sha <sha>                  read the combined status BACK
#   dry-run --sha <sha> --rc <0|1|2>     print the request without sending it
#   --self-test                          prove the token seam offline (no network)
#
# Exit codes follow the repo tri-state: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# FAIL-CLOSED, and specifically: an unobservable read-back is 2, never 0. Posting
# succeeds but reading the status back fails => we do NOT know the commit is
# gated, and saying otherwise would be the #739 defect (reporting a state that
# was never observed).
#
# TOKEN SEAM (issue #1350): a human runs this with `gh` already authenticated,
# but the Cloud Build runner has no `gh` login -- only a Secret Manager value
# injected as an env var. `resolve_token` prefers GH_TOKEN, falls back to
# GITHUB_TOKEN (gh's own convention), and returns empty if neither is set. When
# `gh` itself is missing (the Cloud Build image), post/show fall back to a raw
# `curl` call authenticated with that token. Absence of BOTH `gh` and a token is
# CANNOT-ASSESS, never a hard failure -- the runner must not fail its build
# BECAUSE the secret has not been created yet.
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

REPO="${AO_REPO:-kushin77/agent-orchestrator}"
MAPPER="scripts/gate-status-map.py"
# The context branch protection will require. One name, one place.
CONTEXT="${AO_GATE_CONTEXT:-ao/gate-of-record}"

die() { printf 'gate-status: %s\n' "$1" >&2; exit "${2:-1}"; }

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
  local got

  got="$(GH_TOKEN=tok-a GITHUB_TOKEN=tok-b resolve_token)"
  [ "$got" = "tok-a" ] || { echo "  FAIL  GH_TOKEN did not take precedence over GITHUB_TOKEN (got '$got')" >&2; problems=$((problems + 1)); }
  echo "  OK  GH_TOKEN takes precedence over GITHUB_TOKEN"

  got="$(unset GH_TOKEN; GITHUB_TOKEN=tok-b resolve_token)"
  [ "$got" = "tok-b" ] || { echo "  FAIL  GITHUB_TOKEN fallback did not resolve (got '$got')" >&2; problems=$((problems + 1)); }
  echo "  OK  GITHUB_TOKEN resolves when GH_TOKEN is unset"

  got="$(unset GH_TOKEN GITHUB_TOKEN; resolve_token)"
  [ -z "$got" ] || { echo "  FAIL  resolve_token returned a value with neither var set ('$got')" >&2; problems=$((problems + 1)); }
  echo "  OK  resolve_token returns empty with neither var set (the CANNOT-ASSESS trigger)"

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
mode=""
while [ $# -gt 0 ]; do
  case "$1" in
    post|show|dry-run) mode="$1" ;;
    --self-test) mode="self-test" ;;
    --sha) sha="${2:-}"; shift ;;
    --rc)  rc="${2:-}";  shift ;;
    --repo) REPO="${2:-}"; shift ;;
    -h|--help) sed -n '2,24p' "$0"; exit 0 ;;
    *) die "unknown argument: $1" 1 ;;
  esac
  shift
done

[ "$mode" = "self-test" ] && { self_test; exit $?; }

[ -n "$mode" ] || die "usage: $0 {post|show|dry-run} --sha <sha> [--rc <0|1|2>] | --self-test" 2

case "$mode" in
  post|dry-run)
    [ -n "$sha" ] || die "CANNOT-ASSESS — --sha is required" 2
    [ -n "$rc" ]  || die "CANNOT-ASSESS — --rc is required" 2
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
      token="$(resolve_token)"
      [ -n "$token" ] || die "CANNOT-ASSESS — gh not found and no GH_TOKEN/GITHUB_TOKEN in env; status not posted" 2
      command -v curl >/dev/null 2>&1 || die "CANNOT-ASSESS — neither gh nor curl found; cannot post the status" 2
      http_code="$(curl -sS -o /tmp/gs-post-err.txt -w '%{http_code}' \
          -X POST "https://api.github.com/repos/$REPO/statuses/$sha" \
          -H "Authorization: Bearer $token" \
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
      token="$(resolve_token)"
      [ -n "$token" ] || die "CANNOT-ASSESS — gh not found and no GH_TOKEN/GITHUB_TOKEN in env; cannot read back" 2
      command -v curl >/dev/null 2>&1 || die "CANNOT-ASSESS — neither gh nor curl found" 2
      http_code="$(curl -sS -o /tmp/gs-show.json -w '%{http_code}' \
          "https://api.github.com/repos/$REPO/commits/$sha/status" \
          -H "Authorization: Bearer $token" \
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
