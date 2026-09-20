#!/usr/bin/env bash
# check-gate-publish.sh — the gate of record's OWN producers, and their power to
# REFUSE (issue #1382).
#
# THE DEFECT THIS EXISTS FOR
#   `ao/gate-of-record` is a REQUIRED status check on `master` (#1342). A required
#   status check gates a PULL REQUEST while a status is posted for a COMMIT, so the
#   requirement is satisfiable only if something posts the context for the commits
#   a PR head is judged on -- and, because a SQUASH landing creates a commit no
#   pre-merge status can ever describe, for the commit that LANDS as well. The
#   poster already existed (`scripts/gate-status.sh post`, ADR-0028) and the Cloud
#   Build PR-verdict path already called it, but the ORDINARY LANE PATH -- the path
#   every lane takes, the gate of record run in its own worktree -- published
#   nothing, and the landing seam published nothing for the commit it landed.
#   Measured 2026-09-19: `scripts/check-gate-status.sh` read the live producer
#   state on this repository and found the context observed on NONE of the last 20
#   commits of `master` (REQUIRED-BUT-UNOBSERVED) while branch protection REQUIRED
#   it -- 17 of 17 open PRs BLOCKED, 13 of them carrying no status at all. A
#   required check whose producer covers neither the lane path nor the landing is
#   not a control; it is a merge freeze.
#
# THE TWO QUESTIONS, AND WHY BOTH ARE ASKED
#   * WIRING -- `scripts/verify.sh` (the gate of record itself) and
#     `scripts/merge-pr.sh` (the landing seam) must each invoke the poster on a
#     NON-COMMENT line. A producer that is merely *described* is not a producer,
#     so the predicate is provoked on a copy with the invocation stripped: the arm
#     is shown to be able to fail, not just to pass.
#   * BEHAVIOUR -- a producer is only a control if it can REFUSE. Every arm below
#     drives the REAL file in a scratch venue, and each of the three load-bearing
#     arms is shown to be so by a MUTANT it must catch: the park exit removed (a
#     permitless run must not post), the published rc pinned to 0 (a red run must
#     not read green), and the landed-green gate dropped (an ungated PR must not
#     land green). A control whose arms survive their own mutants is a formality.
#
# WHOSE SURFACE THIS TOUCHES, AND WHOSE IT DOES NOT
#   Nothing here edits `scripts/gate-status.sh`, `scripts/gate-status-map.py` or
#   `scripts/check-gate-status.sh`: the poster and its checker belong to another
#   lane (#1407) and this check CONSUMES them exactly as they are. The one rule
#   this file owns -- "a non-comment line of those two producers invokes the
#   poster" -- is therefore defined HERE, and every shape is assembled from
#   fragments, for two reasons: a checker that spells the shape it forbids either
#   hides it or reds itself, and a `check-*.sh` must never certify its own premise
#   (a checker is not a producer).
#
# NOTHING HERE CAN POST TO THE REPOSITORY, AND NOTHING HERE RUNS THE COMPOSITE GATE
#   Every venue carries a RECORDING stand-in for `scripts/gate-status.sh` -- it
#   records its argv and exits with a scripted rc -- and the landing venue drives a
#   recording fake `gh`. The single seat that runs the REAL poster does so with
#   `gh` ABSENT from PATH, which is the refusal it exists to provoke. Each venue
#   also redirects `AO_GATE_LOCK_ROOT` to its own scratch directory and neuters the
#   explicit `checks=()` array to a single fixture, so no box-wide admission permit
#   is taken and an arm costs seconds rather than a full gate run.
#
# EXIT CONTRACT (the repo's honesty tri-state)
#   0 OK / 1 NOT-OK / 2 CANNOT-ASSESS — a venue that could not be staged is never
#   a pass, and a missing producer is refused BY NAME.
#
# Usage: bash scripts/check-gate-publish.sh
set -u

own_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)" || exit 2

# --- the one shape, assembled from fragments --------------------------------
fx_name="gate-status"".sh"
fx_verb="po""st"
fx_name_re="${fx_name//./[.]}"
fx_verb_re="(^|[^A-Za-z0-9_])${fx_verb}([^A-Za-z0-9_]|\$)"

fail=0
cannot_assess=0

# ONE scratch variable and ONE EXIT trap, `|| true`-safe so that a cleanup
# failure cannot mask this gate's own exit code.
fx=""
cleanup() { [ -n "$fx" ] && rm -rf "$fx" || true; }
trap cleanup EXIT

# producer_in <file> -- 0 when a NON-COMMENT line invokes the poster. A comment
# cannot run, so a note reading "post it with scripts/gate-status.sh" is not a
# producer; that is the direction that fails OPEN, which is why the comment is
# stripped before the match rather than after it.
producer_in() {
  awk -v name="$fx_name_re" -v verb="$fx_verb_re" '
    { line = $0; sub(/#.*/, "", line)
      if (line ~ name && line ~ verb) { found = 1; exit } }
    END { exit(found ? 0 : 1) }' "$1"
}

producer_fx=""
producer_verify="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/scripts/verify.sh"
producer_merge="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/scripts/merge-pr.sh"
for producer_file in "$producer_verify" "$producer_merge"; do
  if [ ! -f "$producer_file" ]; then
    echo "check-gate-publish: CANNOT-ASSESS — ${producer_file##*/} is missing, so whether the REQUIRED context has a producer on that path cannot be asked" >&2
    exit 2
  fi
done
for producer_file in "$producer_verify" "$producer_merge"; do
  if producer_in "$producer_file"; then
    echo "  OK  ${producer_file##*/} names the poster (bash scripts/$fx_name $fx_verb) on a non-comment line -- the STATIC half; whether a RUN reaches it is what the behavioural arms below prove"
  else
    echo "check-gate-publish: FAIL REQUIRED-BUT-UNPRODUCED — ${producer_file##*/} does NOT invoke the poster: the path that produces the gate of record on the ordinary lane run / the landing seam publishes nothing, so the required context can be REQUIRED and unproducible at the same time (#1382)" >&2
    fail=1
  fi
done

# The wiring arms are provoked on a COPY: the invocation is stripped and the
# predicate must refuse it, or the arm above proves only that the file exists.
producer_strip_fx="$(mktemp -d "/tmp/cgs-producer.$(printf 'X%.0s' 1 2 3 4 5 6)" 2>/dev/null)" || producer_strip_fx=""
if [ -z "$producer_strip_fx" ]; then
  echo "check-gate-publish: CANNOT-ASSESS — no scratch directory for the producer provocation" >&2
  exit 2
fi
producer_fx="$producer_strip_fx"
mkdir -p "$producer_fx/scripts"
for producer_file in "$producer_verify" "$producer_merge"; do
  producer_base="${producer_file##*/}"
  sed -E "s#bash \"?\\\$(root|\\\$root)/scripts/${fx_name_re}\"? +${fx_verb}#STRIPPED_producer_invocation#" \
    "$producer_file" > "$producer_fx/scripts/$producer_base"
  if producer_in "$producer_fx/scripts/$producer_base"; then
    echo "check-gate-publish: FAIL — the producer provocation did not strip the invocation from $producer_base, so the wiring arm above cannot be shown to be able to fail" >&2
    fail=1
  else
    echo "  OK  the wiring arm is load-bearing: with the invocation stripped from $producer_base, the predicate refuses it"
  fi
done

if ! command -v git >/dev/null 2>&1; then
  echo "check-gate-publish: CANNOT-ASSESS — git is not available, so the producer's behaviour cannot be provoked in a venue" >&2
  exit 2
fi

python3 - "$own_root" "$producer_fx/venue" <<'PY'
"""Drive the REAL scripts/verify.sh and scripts/merge-pr.sh in scratch venues
and assert that the gate of record's producers can refuse (issue #1382).

Nothing here can post against the real repository: each venue carries a
RECORDING stand-in for `scripts/gate-status.sh`, and the landing venue drives a
recording fake `gh`.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
work = Path(sys.argv[2]).resolve()
work.mkdir(parents=True, exist_ok=True)

SURFACE = (
    "scripts/verify.sh",
    "scripts/gate-lock.sh",
    "scripts/discover-checks.sh",
    "scripts/lib/skip-ratchet.py",
    "scripts/lib/validate-attestation.py",
    "governance/isolation/attestation.schema.json",
    "fleet/gatelock.py",
    "fleet/lease.py",
)
REAL_POSTER = ("scripts/gate-status.sh", "scripts/gate-status-map.py")
LANDING = (
    "scripts/merge-pr.sh",
    "scripts/check-squash-message.sh",
    "scripts/pr-queue.sh",
    "governance/isolation/trailer.py",
    "governance/isolation/__init__.py",
)
ARMS = []


def arm(label, ok, detail=""):
    ARMS.append((label, bool(ok)))
    print("  %s  %s" % ("OK   " if ok else "FAIL ", label))
    if detail:
        print("        %s" % str(detail).splitlines()[0][:300])


def staged(what, why):
    print("check-gate-publish: CANNOT-ASSESS — %s: %s" % (what, why), file=sys.stderr)
    raise SystemExit(2)


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)


def run(argv, cwd, extra=None):
    env = dict(os.environ)
    env.update({
        "AO_GATE_LOCK_ROOT": str(work / "permits"),
        "AO_GATE_MAX_CONCURRENT": "1",
        "AO_AGENT_ID": "gate-status-producer-fixture",
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    if extra:
        env.update(extra)
    try:
        proc = subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True,
                              timeout=300, env=env)
    except (OSError, subprocess.SubprocessError) as exc:
        staged("the fixture could not be run", exc)
    return proc.returncode, proc.stdout + proc.stderr


def write(path, text, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(mode)


def copy_surface(venue, extra=()):
    for rel in SURFACE + tuple(extra):
        source = root / rel
        if not source.is_file():
            staged("a venue cannot be staged", "%s is missing from this tree" % rel)
        target = venue / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)


def neuter(venue, rel="scripts/verify.sh"):
    """Replace the explicit `checks=()` array with an empty one -- and PROVE the
    rest of the file is untouched, so the venue runs the real orchestrator."""
    path = venue / rel
    text = path.read_text(encoding="utf-8")
    try:
        start = text.index("\nchecks=(\n")
        end = text.index("\n)\n", start)
    except ValueError:
        staged("the venue cannot be neutered", "the `checks=()` anchor moved in %s" % rel)
    body = text[:start] + "\nchecks=()\n" + text[end + 3:]
    if body.replace("\nchecks=()\n", text[start:end + 3], 1) != text:
        staged("the venue is not the real file", "%s changed beyond its check array" % rel)
    path.write_text(body, encoding="utf-8")


def venue(name, fixture_rc=None, poster="stub", entries=(), moves=False):
    path = work / name
    shutil.rmtree(path, ignore_errors=True)
    copy_surface(path, REAL_POSTER if poster == "real" else ())
    neuter(path)
    if fixture_rc is not None:
        body = ("#!/usr/bin/env bash\nset -u\necho 'check-producer-fixture: rc %d'\nexit %d\n"
                % (fixture_rc, fixture_rc))
        if moves:
            # A check that COMMITS while the gate is running -- the measured
            # shape of a lane's tree moving under its own run (#1310/#1356).
            body = ("#!/usr/bin/env bash\nset -u\nfx_root=\"$(dirname \"$0\")/..\"\n"
                    "printf 'the tree moved under the run\\n' > \"$fx_root/moved.txt\"\n"
                    "git -C \"$fx_root\" add moved.txt >/dev/null 2>&1\n"
                    "git -C \"$fx_root\" -c user.name=fixture -c user.email=f@ao.invalid "
                    "-c commit.gpgsign=false commit -q -m 'the tree moves mid-run' >/dev/null 2>&1\n"
                    "echo 'check-producer-fixture: rc %d'\nexit %d\n" % (fixture_rc, fixture_rc))
        write(path / "scripts" / "check-producer-fixture.sh", body, 0o755)
    write(path / "scripts" / "skip-budget.json",
          json.dumps({"schema": "ao.verify.skip-budget/v1", "entries": list(entries)}, indent=2) + "\n")
    if poster == "stub":
        write(path / "scripts" / "gate-status.sh", STUB, 0o755)
    subprocess.run(["git", "init", "-q", str(path)], capture_output=True)
    git(path, "add", "-A")
    proc = git(path, "-c", "user.name=fixture", "-c", "user.email=f@ao.invalid",
               "-c", "commit.gpgsign=false", "commit", "-q", "-m", "fixture")
    if proc.returncode != 0:
        staged("the venue cannot be staged", "its fixture commit failed: %s" % proc.stderr.strip())
    return path


def head_of(path):
    return git(path, "rev-parse", "HEAD").stdout.strip()


STUB = """#!/usr/bin/env bash
# A RECORDING stand-in for scripts/gate-status.sh: it records its own argv and
# exits with a scripted rc, so nothing here can post a real status.
set -u
printf '%s\\n' "$*" >> "$AO_CGS_STUB_LOG"
case "${1:-}" in
  show) exit "${AO_CGS_STUB_SHOW_RC:-0}" ;;
  post)
    if [ "${AO_CGS_STUB_POST_RC:-0}" != "0" ]; then
      printf 'gate-status: CANNOT-ASSESS -- gh not found and no GH_TOKEN/GITHUB_TOKEN in env; status not posted\\n' >&2
      exit "$AO_CGS_STUB_POST_RC"
    fi
    printf 'gate-status: posted ao/gate-of-record for a fixture (recording stand-in)\\n'
    exit 0 ;;
esac
exit 0
"""

FAKE_GH = """#!/usr/bin/env bash
# A RECORDING fake gh for the landing arms. Never touches the network.
#   pr view   -> the JSON at $AO_CGS_GH_VIEW_JSON
#   pr merge  -> records the PR number in $AO_CGS_GH_MERGE_LOG, exits $AO_CGS_GH_MERGE_RC
#   api ...   -> the REST transport scripts/merge-pr.sh uses (issue #1569). The
#                stand-in does not implement jq: a GET of the pull answers with
#                the fixture at $AO_CGS_GH_REST_PULL, which carries the renamed
#                keys BOTH of the apply path's REST reads look up. The merge
#                endpoint records the PR number in the same log and exits the
#                same $AO_CGS_GH_MERGE_RC, so one knob scripts the outcome on
#                either transport, and the head-ref DELETE records the branch it
#                was asked to delete in $AO_CGS_GH_DELETE_LOG.
set -u
if [ "${1:-}" = "pr" ] && [ "${2:-}" = "view" ]; then
  printf '%s' "$AO_CGS_GH_VIEW_JSON"
  exit 0
fi
if [ "${1:-}" = "pr" ] && [ "${2:-}" = "merge" ]; then
  printf '%s\\n' "${3:-}" >> "$AO_CGS_GH_MERGE_LOG"
  exit "${AO_CGS_GH_MERGE_RC:-0}"
fi
if [ "${1:-}" = "api" ]; then
  shift
  method="GET"
  if [ "${1:-}" = "-X" ]; then
    method="${2:-GET}"
    shift 2
  fi
  url="${1:-}"
  if [ $# -gt 0 ]; then shift; fi
  case "$method $url" in
    "GET "*"/pulls/"*)
      cat "$AO_CGS_GH_REST_PULL"
      exit 0
      ;;
    "PUT "*"/pulls/"*"/merge")
      rest_pr="${url##*/pulls/}"
      printf '%s\\n' "${rest_pr%%/*}" >> "$AO_CGS_GH_MERGE_LOG"
      printf '{"merged":true,"sha":"1111111111111111111111111111111111111111"}\\n'
      exit "${AO_CGS_GH_MERGE_RC:-0}"
      ;;
    "DELETE "*"/git/refs/heads/"*)
      printf '%s\\n' "${url##*/git/refs/heads/}" >> "$AO_CGS_GH_DELETE_LOG"
      exit 0
      ;;
  esac
  echo "fake gh: unexpected api invocation: $method $url" >&2
  exit 1
fi
echo "fake gh: unexpected invocation: $*" >&2
exit 1
"""


def run_verify(path, extra=None):
    log = path / "stub.log"
    log.write_text("", encoding="utf-8")
    env = {"AO_CGS_STUB_LOG": str(log)}
    if extra:
        env.update(extra)
    rc, out = run(["bash", "scripts/verify.sh", "verify"], path, env)
    return rc, out, [x for x in log.read_text(encoding="utf-8").splitlines() if x.strip()]


def posts(recorded):
    return [x for x in recorded if x.startswith("post")]


def shows(recorded):
    return [x for x in recorded if x.startswith("show")]


def notes(out):
    return [x for x in out.splitlines() if "NOTE" in x]


def lock(action, path):
    proc = subprocess.run(
        ["bash", "scripts/gate-lock.sh", action, "--worktree", str(path)]
        + (["--mode", "verify", "--owner-pid", str(os.getpid())] if action == "acquire" else []),
        cwd=str(path), capture_output=True, text=True,
        env={**os.environ, "AO_GATE_LOCK_ROOT": str(work / "permits"),
             "AO_GATE_MAX_CONCURRENT": "1", "PYTHONDONTWRITEBYTECODE": "1"})
    if action == "acquire" and proc.returncode != 0:
        staged("the fixture could not hold its own worktree lock", proc.stdout + proc.stderr)


def mutate(path, rel, old, new):
    """Plant a mutant. A moved anchor is a FAILED ARM, not a crash: the control
    must still REPORT, by name, what it could not prove -- a check that dies
    before printing its refusal is a check whose refusal nobody reads (measured
    here: the first version raised SystemExit(2) and the run ended CANNOT-ASSESS
    while six arms had silently not run).
    """
    target = path / rel
    text = target.read_text(encoding="utf-8")
    if old not in text:
        return False
    target.write_text(text.replace(old, new, 1), encoding="utf-8")
    return True


def planted(ok):
    return "planted" if ok else "NOT PLANTED (the anchor moved, so this arm could not be shown to bite)"


# The publish seam this control pins. Spelled ONCE so the mutants and the
# anchors cannot drift apart, and so a moved anchor fails ONE arm by name rather
# than the whole run.
PUBLISH_CALL = 'publish_gate_of_record "$ATTEST_SHA" "$overall" "$total" "$skipped"'
PUBLISH_CALL_RC0 = 'publish_gate_of_record "$ATTEST_SHA" 0 "$total" "$skipped"'
PUBLISH_CALL_RC2 = 'publish_gate_of_record "$ATTEST_SHA" 2 "$total" "$skipped"'


# --- the venue of record's ability to DELIVER (issue #1467) -------------------
#
# THE DEFECT: the poster refused to publish a green for a commit whose venue run
# was red -- and the venue of record STRUCTURALLY cannot publish anything, because
# its runner image carries neither `gh` nor `gcloud`, so its own poster step exits
# 2 for want of a credential. The ordered rule then had no satisfier at either
# end: the local producer "may not satisfy it FIRST", and the venue cannot go at
# all. Every PR head stayed BLOCKED, and the only way to land was the admin bypass
# (`enforce_admins=false`) that scripts/check-branch-protection.sh exists to
# prevent. Measured 2026-09-19 from the venue's OWN build log
# (38ece2de-1412-4ae8-acd7-fe7fd2055e2, quoted in CAP_LOG_UNABLE below).
#
# THE REMEDY: a venue that concluded red loses its precedence ONLY when the
# venue's own record for that run says it could not deliver. Both directions that
# must NOT happen are provoked here, because either one is a defect:
#   * a venue that CAN deliver keeps its precedence -- a green published over a red
#     it stood behind is the #739/#1400 false-green class;
#   * a record that cannot be READ keeps it too (fail closed) -- "I could not read
#     the venue" must never become "therefore the venue does not matter".
#
# NOTHING HERE TOUCHES THE NETWORK OR THE REPOSITORY: both seams are stand-ins on
# PATH (a recording `gh`, and a `gcloud` serving a fixture log), and the poster is
# driven in a scratch venue with the whole PATH replaced.
CAP_BUILD = "9f8e7d6c-5b4a-4392-8170-6f5e4d3c2b1a"
CAP_URL = ("https://console.cloud.google.com/cloud-build/builds;region=us-central1/"
           "%s?project=1056038104733" % CAP_BUILD)
CAP_SHA = "390ddd77a67fccd484047fa6c9f04259db79b985"

CAP_GH = """#!/usr/bin/env bash
# A RECORDING stand-in for `gh`. It records its argv, serves the check-runs the
# poster reads, and records every POST -- so an arm can assert BOTH the decision
# and the fact that nothing was posted. It never touches the network.
set -u
printf '%s\\n' "$*" >> "${CAP_EVENTS:?}"
case "$*" in
  *"-X POST"*) exit 0 ;;
  *"/check-runs"*) cat "${CAP_RUNS:?}"; exit 0 ;;
  *"/status"*) cat "${CAP_STATUS:?}"; exit 0 ;;
esac
exit 1
"""

CAP_GCLOUD = """#!/usr/bin/env bash
# A RECORDING stand-in for `gcloud` -- the only reader of the venue of record's
# own build log. `CAP_VENUE_LOG=none` makes the read fail, which is how an
# unreadable venue record is provoked.
set -u
printf 'gcloud %s\\n' "$*" >> "${CAP_EVENTS:?}"
case "${1:-} ${2:-}" in
  "builds log")
    [ "${CAP_VENUE_LOG:-none}" != "none" ] || exit 1
    cat "$CAP_VENUE_LOG"; exit 0 ;;
esac
exit 1
"""

# The venue's own words, quoted from the real build the defect was measured on.
# BOTH lines are there, in the order the venue produced them: the gate's own
# publisher speaks first and the recipe's post step last, which is why the
# classifier quotes the LAST refusal rather than the first.
CAP_LOG_UNABLE = (
    "verify: NOTE -- the gate of record was NOT published for 390ddd77a67f (the poster exited 2): "
    "gate-status: CANNOT-ASSESS - gh not found and no GH_TOKEN/GITHUB_TOKEN in env; status not posted\n"
    "gate-status: SKIPPED -- this runner image carries no gcloud, so the token cannot be read\n"
    "  here at all: creating ao-gate-status-token is NOT sufficient for this venue. The gate\n"
    "  verdict is NOT posted (issue #1350; the venue shape is #1361).\n")
CAP_LOG_DELIVERED = "gate-status: posted ao/gate-of-record=failure for 390ddd77a67f (make verify: FAIL)\n"
CAP_LOG_SILENT = "check-shell-patterns: OK -- nothing to report\n"

# Every tool the poster needs. Spelled out rather than inherited, because the
# `gcloud=False` arm has to build a PATH that is complete EXCEPT for the one tool
# whose absence is the point -- and an inherited PATH would find the real one.
CAP_TOOLS = ("bash", "sh", "cat", "tail", "head", "sed", "awk", "grep", "git", "python3",
             "mktemp", "dirname", "basename", "date", "hostname", "id", "mkdir", "rm",
             "sort", "uniq", "wc", "tr", "env", "tee", "chmod", "cp", "mv", "find", "xargs",
             "timeout")


def cap_venue(name, status="completed", conclusion="failure", venue_log=None, gcloud=True):
    """A scratch venue holding the REAL poster plus stand-ins for both seams."""
    path = work / name
    shutil.rmtree(path, ignore_errors=True)
    (path / "scripts").mkdir(parents=True)
    for rel in ("scripts/gate-status.sh", "scripts/gate-status-map.py"):
        shutil.copyfile(root / rel, path / rel)
    runs = [] if status == "none" else [{
        "name": "control-plane-verify (purebliss-ghl)",
        "status": status,
        "conclusion": None if conclusion == "none" else conclusion,
        "started_at": "2026-09-19T15:04:18Z",
        "completed_at": None if status != "completed" else "2026-09-19T16:07:44Z",
        "details_url": CAP_URL,
        "app": {"slug": "google-cloud-build"},
    }]
    write(path / "runs.json", json.dumps({"check_runs": runs}) + "\n")
    write(path / "status.json", '{"state": "pending", "statuses": []}')
    if venue_log is not None:
        write(path / "venue.log", venue_log)
    bins = path / "bin"
    bins.mkdir(parents=True, exist_ok=True)
    for tool in CAP_TOOLS:
        which = shutil.which(tool)
        if which:
            (bins / tool).symlink_to(which)
    write(bins / "gh", CAP_GH, 0o755)
    if gcloud:
        write(bins / "gcloud", CAP_GCLOUD, 0o755)
    return path


def cap_run(path, *args):
    events = path / "events.log"
    events.write_text("", encoding="utf-8")
    venue_log = path / "venue.log"
    rc, out = run(["bash", "scripts/gate-status.sh", *args], path, {
        "PATH": str(path / "bin"),
        "CAP_EVENTS": str(events),
        "CAP_RUNS": str(path / "runs.json"),
        "CAP_STATUS": str(path / "status.json"),
        "CAP_VENUE_LOG": str(venue_log) if venue_log.exists() else "none",
        "GH_TOKEN": "",
        "GITHUB_TOKEN": "",
    })
    recorded = [x for x in events.read_text(encoding="utf-8").splitlines() if x.strip()]
    return rc, out, recorded


def cap_posts(recorded):
    return [x for x in recorded if "-X POST" in x]


def cap_reads_venue(recorded):
    return [x for x in recorded if x.startswith("gcloud ")]


def venue_delivery_arms():
    """The venue of record's precedence, and the measurement that scopes it."""
    # 1. THE DEFECT AND THE REMEDY. The venue concluded red, and its OWN record
    #    says it could not deliver -- so the required context has no other
    #    producer, and this run's attested verdict is published. The venue's red
    #    rides in target_url rather than being erased.
    path = cap_venue("cap-unable", venue_log=CAP_LOG_UNABLE)
    rc, out, rec = cap_run(path, "post", "--sha", CAP_SHA, "--rc", "0")
    made = cap_posts(rec)
    arm("a red venue measured UNABLE TO DELIVER no longer deadlocks the required "
        "context: this run's own green is published, and the venue's red is linked",
        rc == 0 and len(made) == 1 and "state=success" in made[0]
        and "target_url=%s" % CAP_URL in made[0]
        and "VENUE UNABLE TO DELIVER" in out and "no gcloud" in out,
        "rc=%s posts=%r" % (rc, made))

    # 2. THE GUARD, UNCHANGED, for a venue that CAN deliver. Its own record shows
    #    it posted, so its red is a VERDICT and no green may be published over it.
    #    Arm 7 proves this arm bites.
    path = cap_venue("cap-delivered", venue_log=CAP_LOG_DELIVERED)
    rc, out, rec = cap_run(path, "post", "--sha", CAP_SHA, "--rc", "0")
    arm("a red venue whose own record shows it DELIVERED keeps its precedence: no "
        "green is published over a verdict it stood behind",
        rc == 2 and not cap_posts(rec) and "concluded 'failure'" in out and CAP_BUILD in out,
        "rc=%s posts=%r" % (rc, cap_posts(rec)))

    # 3. FAIL CLOSED -- no reader. The venue record cannot be read (there is no
    #    gcloud in this venue at all), so there is no measurement, so no waiver.
    path = cap_venue("cap-unreadable", venue_log=CAP_LOG_UNABLE, gcloud=False)
    rc, out, rec = cap_run(path, "post", "--sha", CAP_SHA, "--rc", "0")
    arm("an UNREADABLE venue record is CANNOT-ASSESS, never a waiver: no green, and "
        "the refusal still names the venue and its build",
        rc == 2 and not cap_posts(rec) and not cap_reads_venue(rec)
        and "concluded 'failure'" in out and CAP_BUILD in out,
        "rc=%s posts=%r reads=%r" % (rc, cap_posts(rec), cap_reads_venue(rec)))

    # 4. FAIL CLOSED -- read, but silent. Neither a POST nor a refusal: silence is
    #    not evidence that the venue does not matter.
    path = cap_venue("cap-silent", venue_log=CAP_LOG_SILENT)
    rc, out, rec = cap_run(path, "post", "--sha", CAP_SHA, "--rc", "0")
    arm("a venue record that says NEITHER is 'unassessable', not a waiver: no green",
        rc == 2 and not cap_posts(rec) and cap_reads_venue(rec),
        "rc=%s posts=%r" % (rc, cap_posts(rec)))

    # 5. THE MEASURED #1400 HOLE, STILL CLOSED. A run in flight may yet deliver, so
    #    its precedence is meaningful whatever its image carries -- and there is no
    #    record to read yet, so none is read.
    path = cap_venue("cap-inflight", status="in_progress", conclusion="none")
    rc, out, rec = cap_run(path, "post", "--sha", CAP_SHA, "--rc", "0")
    arm("a venue run still IN FLIGHT still refuses, and no venue record is read",
        rc == 2 and not cap_posts(rec) and not cap_reads_venue(rec) and "has NOT concluded" in out,
        "rc=%s posts=%r reads=%r" % (rc, cap_posts(rec), cap_reads_venue(rec)))

    # 6. AGREEMENT, unchanged.
    path = cap_venue("cap-green", conclusion="success")
    rc, out, rec = cap_run(path, "post", "--sha", CAP_SHA, "--rc", "0")
    made = cap_posts(rec)
    arm("a venue that AGREES publishes the green carrying the venue's evidence URL",
        rc == 0 and len(made) == 1 and "target_url=%s" % CAP_URL in made[0],
        "rc=%s posts=%r" % (rc, made))

    # 7. THE NEGATIVE CONTROLS. A red gate reports red and a CANNOT-ASSESS gate
    #    reports error; neither reads the venue, and neither is ever published as
    #    success. A red is ALWAYS allowed to speak -- refusing to report one is the
    #    same control built the wrong way round.
    path = cap_venue("cap-red", venue_log=CAP_LOG_UNABLE)
    rc, out, rec = cap_run(path, "post", "--sha", CAP_SHA, "--rc", "1")
    made = cap_posts(rec)
    arm("NEGATIVE CONTROL: a RED gate publishes 'failure' and can never read as a pass",
        rc == 0 and len(made) == 1 and "state=failure" in made[0] and "state=success" not in made[0]
        and not cap_reads_venue(rec),
        "rc=%s posts=%r" % (rc, made))
    path = cap_venue("cap-ca", venue_log=CAP_LOG_UNABLE)
    rc, out, rec = cap_run(path, "post", "--sha", CAP_SHA, "--rc", "2")
    made = cap_posts(rec)
    arm("NEGATIVE CONTROL: CANNOT-ASSESS publishes 'error', never success, and reads "
        "no venue record -- so no venue can talk it into a pass",
        rc == 0 and len(made) == 1 and "state=error" in made[0] and not cap_reads_venue(rec),
        "rc=%s posts=%r" % (rc, made))

    # 8. The venue record's EMPTY FIELDS. A venue that produced no run leaves the
    #    URL and all three coordinates blank, and a blank field in a multi-line
    #    record is what shifts every later field onto the previous one's value
    #    (docs/SHELL-PATTERNS.md SP-2) -- the no-run path published the refusal's
    #    REASON TEXT as the status's target_url until this arm's change.
    path = cap_venue("cap-norun", status="none", conclusion="none")
    rc, out, rec = cap_run(path, "post", "--sha", CAP_SHA, "--rc", "0")
    made = cap_posts(rec)
    arm("a commit the venue never ran publishes a green with NO target_url -- the "
        "reason text is not shifted into the URL field",
        rc == 0 and len(made) == 1 and "target_url=" not in made[0],
        "rc=%s posts=%r" % (rc, made))

    # 9-10. THE FALSIFICATIONS. A control is a formality until it is shown to be
    #       able to fail, and the direction that fails OPEN is the one that
    #       publishes a green over a verdict the venue stood behind.
    mutant = cap_venue("cap-mutant-open", venue_log=CAP_LOG_DELIVERED)
    ok = mutate(mutant, "scripts/gate-status.sh",
                '            if [ "$cap_verdict" != "cannot" ]; then\n',
                '            if [ "cannot" != "cannot" ]; then\n')
    rc, out, rec = cap_run(mutant, "post", "--sha", CAP_SHA, "--rc", "0") if ok else (0, "", [])
    made = cap_posts(rec)
    arm("MUTANT waiver-made-unconditional IS CAUGHT (a green published over a red the "
        "venue DELIVERED -- the #739/#1400 class arm 2 exists to refuse)",
        ok and bool(made) and "state=success" in made[0],
        "%s rc=%s posts=%r" % (planted(ok), rc, made))

    mutant = cap_venue("cap-mutant-closed", venue_log=CAP_LOG_SILENT)
    ok = mutate(mutant, "scripts/gate-status.sh",
                '                cap_verdict="unassessable"\n',
                '                cap_verdict="cannot"\n')
    rc, out, rec = cap_run(mutant, "post", "--sha", CAP_SHA, "--rc", "0") if ok else (0, "", [])
    made = cap_posts(rec)
    arm("MUTANT unreadable-record-treated-as-a-waiver IS CAUGHT (fail closed is what "
        "keeps arm 3 and arm 4 honest)",
        ok and bool(made), "%s rc=%s posts=%r" % (planted(ok), rc, made))


def verify_arms():
    green = venue("green", 0)
    sha = head_of(green)
    rc, out, rec = run_verify(green)
    arm("a green run posts exactly one status, rc 0, for the commit it measured",
        rc == 0 and len(posts(rec)) == 1 and "--rc 0" in posts(rec)[0] and sha in posts(rec)[0]
        and not notes(out), "rc=%s posts=%r" % (rc, posts(rec)))

    red = venue("red", 1)
    rc, out, rec = run_verify(red)
    arm("a RED run posts rc 1, never 0 -- a red gate cannot read as a pass",
        rc == 1 and len(posts(rec)) == 1 and "--rc 1" in posts(rec)[0],
        "rc=%s posts=%r" % (rc, posts(rec)))

    skipped = venue("skipped", 2, entries=[{
        "check": "producer-fixture", "kind": "standing-gap", "issue": 1382,
        "reason": "the venue's only check answers rc 2 on purpose"}])
    rc, out, rec = run_verify(skipped)
    arm("a run that assessed NOTHING (every check SKIPped) posts nothing, and says which",
        not posts(rec) and any("every check in this run was SKIPped" in x for x in notes(out)),
        "rc=%s notices=%r" % (rc, notes(out)[:1]))

    empty = venue("no-checks")
    rc, out, rec = run_verify(empty)
    arm("a run that discovered NO check at all posts nothing, and names that reason",
        not posts(rec) and any("discovered NO check at all" in x for x in notes(out)),
        "rc=%s notices=%r" % (rc, notes(out)[:1]))

    parked = venue("parked", 0)
    lock("acquire", parked)
    rc, out, rec = run_verify(parked)
    arm("a PARKED run posts NOTHING and exits 10 (it never reaches the producer)",
        rc == 10 and not rec, "rc=%s poster-corpus=%r" % (rc, rec))
    lock("release", parked)

    refusing = venue("poster-refuses", 0)
    rc, out, rec = run_verify(refusing, {"AO_CGS_STUB_POST_RC": "2"})
    arm("a poster that cannot publish leaves the run's OWN verdict alone (rc 0) and is named",
        rc == 0 and len(posts(rec)) == 1 and "NOT published" in out,
        "rc=%s attempt=%r" % (rc, posts(rec)))

    real = venue("real-poster", 0, poster="real")
    bins = work / "bins-no-gh"
    bins.mkdir(parents=True, exist_ok=True)
    for tool in ("bash", "sh", "cat", "tail", "head", "sed", "awk", "grep", "git", "python3",
                 "mktemp", "dirname", "basename", "date", "hostname", "id", "mkdir", "rm",
                 "sort", "uniq", "wc", "tr", "env", "tee", "chmod", "cp", "mv", "find", "xargs"):
        which = shutil.which(tool)
        if which and not (bins / tool).exists():
            (bins / tool).symlink_to(which)
    rc, out, rec = run_verify(real, {"PATH": str(bins), "GH_TOKEN": "", "GITHUB_TOKEN": ""})
    arm("with gh ABSENT the REAL poster refuses BY NAME and the run keeps its own rc",
        rc == 0 and "NOT published" in out and "the poster exited 2" in out,
        "rc=%s notices=%r" % (rc, notes(out)[:1]))

    mutant_park = venue("mutant-park", 0)
    ok = mutate(mutant_park, "scripts/verify.sh", '  exit "$lock_rc"\n',
                '  : # MUTANT: the park exit removed\n')
    lock("acquire", mutant_park)
    rc, out, rec = run_verify(mutant_park) if ok else (0, "", [])
    arm("MUTANT park-exit-removed IS CAUGHT (a run that never got a permit posts)",
        ok and (bool(posts(rec)) or rc != 10),
        "%s rc=%s posts=%r" % (planted(ok), rc, posts(rec)))
    lock("release", mutant_park)

    mutant_rc = venue("mutant-rc", 1)
    ok = mutate(mutant_rc, "scripts/verify.sh", PUBLISH_CALL, PUBLISH_CALL_RC0)
    rc, out, rec = run_verify(mutant_rc) if ok else (0, "", [])
    arm("MUTANT rc-pinned-to-0 IS CAUGHT (a red run posting rc 0)",
        ok and bool(posts(rec)) and "--rc 0" in posts(rec)[0],
        "%s rc=%s posts=%r" % (planted(ok), rc, posts(rec)))

    # THE STATIC PREDICATE'S LIMIT, AND ITS PERMANENT PROVOCATION. `producer_in`
    # asks whether the file NAMES the poster -- so a file that DEFINES a publisher
    # and never CALLS it still satisfies the wiring arm, because the invocation
    # text sits in the function body. That is exactly the defect this issue is
    # about ("nothing on the ordinary lane path calls it") wearing the arm's own
    # clothes, and it is why the behavioural arms are the load-bearing ones. The
    # arm below removes ONLY the call -- the definition stays, so the file still
    # names the poster and the wiring arm above would still pass -- and requires
    # that a green run then publishes NOTHING. It is the permanent form of the
    # falsification run this change was validated with.
    uncalled = venue("defined-not-called", 0)
    ok = mutate(uncalled, "scripts/verify.sh", PUBLISH_CALL + "\n", "")
    rc, out, rec = run_verify(uncalled) if ok else (0, "", [])
    arm("a publisher that is DEFINED but never CALLED publishes nothing "
        "(the static arm cannot see this; the behaviour arms can)",
        ok and not posts(rec), "%s rc=%s posts=%r" % (planted(ok), rc, posts(rec)))

    # A verdict belongs to the commit the CHECKS ran against. A tree that moves
    # mid-run is the measured #1310/#1356 shape, and a status for the later commit
    # resting on a verdict reached on the earlier one is the fabricated green with
    # a producer attached -- so the arm's fixture COMMITS while the gate runs.
    moved = venue("moved-tree", 0, moves=True)
    rc, out, rec = run_verify(moved)
    arm("a tree that MOVED under the run publishes NOTHING and names the class",
        not posts(rec) and any("MOVED under this run" in x for x in notes(out)),
        "rc=%s posts=%r" % (rc, posts(rec)))

    # The rc the producer publishes must BE the run's verdict. A CANNOT-ASSESS
    # outcome (rc 2) is not a verdict of the gate of record, so it must not become
    # a status at all: publishing it as `error` would be defensible for the VENUE
    # OF RECORD (which owns exactly that mapping) but wrong here, where the run
    # simply did not assess enough to publish anything -- and publishing it as
    # `success` is the false-green class. The mutant below pins the argument to 2
    # so the guard is shown to bite; if it were removed, a status no run measured
    # would be posted for the commit.
    mutant_rc2 = venue("mutant-rc2", 0)
    ok = mutate(mutant_rc2, "scripts/verify.sh", PUBLISH_CALL, PUBLISH_CALL_RC2)
    rc, out, rec = run_verify(mutant_rc2) if ok else (0, "", [])
    arm("an outcome that is not a gate verdict is NOT published as a status",
        ok and not posts(rec) and any("is not a gate verdict" in x for x in notes(out)),
        "%s rc=%s posts=%r" % (planted(ok), rc, posts(rec)))


def poster_arms():
    """The ONE thing this producer delegates: the poster's rc -> state mapping.

    It is driven through the REAL poster's `dry-run`, which builds the request and
    writes nothing. A producer publishing through a mapper that turned
    CANNOT-ASSESS into `success` would be a false green with a producer attached --
    the #739 class, one layer down -- so the two arms are stated separately: the
    table, and the direction that must never collapse.
    """
    want = {0: "success", 1: "failure", 2: "error"}
    got = {}
    for rc in (0, 1, 2):
        code, out = run(["bash", "scripts/gate-status.sh", "dry-run",
                         "--sha", "0" * 40, "--rc", str(rc)], root)
        state = ""
        for line in out.splitlines():
            if line.strip().startswith("state") and "=" in line:
                state = line.split("=", 1)[1].strip()
        got[rc] = (code, state)
    arm("the poster's rc->state table is the one this producer publishes through",
        all(got[rc][1] == want[rc] for rc in want), "got=%r" % (got,))
    arm("CANNOT-ASSESS is built as 'error', NEVER as success",
        got[2][1] == "error" and got[2][1] != "success", "state(rc=2)=%r" % got[2][1])


def landing_venue(name, state="MERGED", head_green=True, merge_rc=0):
    path = work / name
    shutil.rmtree(path, ignore_errors=True)
    copy_surface(path, LANDING)
    write(path / "scripts" / "gate-status.sh", STUB, 0o755)
    write(path / "bin" / "gh", FAKE_GH, 0o755)
    subprocess.run(["git", "init", "-q", str(path)], capture_output=True)
    git(path, "add", "-A")
    git(path, "-c", "user.name=fixture", "-c", "user.email=f@ao.invalid",
        "-c", "commit.gpgsign=false", "commit", "-q", "-m", "base")
    base = head_of(path)
    git(path, "remote", "add", "origin", str(path))
    git(path, "update-ref", "refs/remotes/origin/master", base)
    git(path, "checkout", "-q", "-b", "fixture-head")
    write(path / "fixture-change.txt", "a change that lands\n")
    git(path, "add", "-A")
    git(path, "-c", "user.name=fixture", "-c", "user.email=f@ao.invalid",
        "-c", "commit.gpgsign=false", "commit", "-q", "-m", "the head that is judged")
    head = head_of(path)
    git(path, "checkout", "-q", "master")
    view = json.dumps({
        "title": "fix(gate): a fixture title",
        "body": "What changed.\n\nRefs kushin77/agent-orchestrator#1382\n",
        "baseRefName": "master",
        "headRefName": "fixture-head",
        "headRefOid": head,
        "state": state,
        "mergeCommit": {"oid": "1" * 40},
    })
    # the REST fixture (issue #1569): the apply path reads the pull over `gh api`
    # and then resolves the head ref for its post-merge branch delete from the
    # same endpoint. This venue's origin remote IS this path, so that is also the
    # slug the delete is authorised against -- the fixture NAMES it rather than
    # leaving it absent, so the delete is judged on a readable value.
    write(path / "rest-pull.json", json.dumps({
        "baseRefName": "master",
        "headRefOid": head,
        "headRef": "fixture-head",
        "headRepo": str(path),
    }), 0o644)
    return path, view, "1" * 40, head, head_green, merge_rc


def deletes(path):
    return [x for x in (path / "delete.log").read_text(encoding="utf-8").splitlines() if x.strip()]


def run_landing(path, view, head_green, merge_rc):
    log = path / "stub.log"
    log.write_text("", encoding="utf-8")
    merge_log = path / "merge.log"
    merge_log.write_text("", encoding="utf-8")
    delete_log = path / "delete.log"
    delete_log.write_text("", encoding="utf-8")
    rc, out = run(["bash", "scripts/merge-pr.sh", "--pr", "1382"], path, {
        "PATH": "%s:%s" % (path / "bin", os.environ.get("PATH", "")),
        "AO_CGS_STUB_LOG": str(log),
        "AO_CGS_STUB_SHOW_RC": "0" if head_green else "1",
        "AO_CGS_GH_VIEW_JSON": view,
        "AO_CGS_GH_MERGE_LOG": str(merge_log),
        "AO_CGS_GH_MERGE_RC": str(merge_rc),
        "AO_CGS_GH_REST_PULL": str(path / "rest-pull.json"),
        "AO_CGS_GH_DELETE_LOG": str(delete_log),
        "AO_MERGE_APPLY": "1",
        "AO_QUEUE_VERIFY_MERGED": "1",
        "AO_QUEUE_VERIFY_CMD": "exit 0",
    })
    return rc, out, [x for x in log.read_text(encoding="utf-8").splitlines() if x.strip()]


def landing_arms():
    path, view, landed, head, green, mrc = landing_venue("landed")
    rc, out, rec = run_landing(path, view, green, mrc)
    arm("a landed merge posts exactly one status, rc 0, for the commit that LANDED",
        rc == 0 and len(posts(rec)) == 1 and landed in posts(rec)[0] and "--rc 0" in posts(rec)[0]
        and any(head in x for x in shows(rec)), "rc=%s posts=%r" % (rc, posts(rec)))
    arm("a landed merge deletes the merged head branch over REST, once (issue #1569)",
        deletes(path) == ["fixture-head"], "deletes=%r rc=%s" % (deletes(path), rc))

    path, view, landed, head, green, mrc = landing_venue("ungated")
    rc, out, rec = run_landing(path, view, False, mrc)
    arm("an UNGATED PR stays BLOCKED: no green is published for the landed commit",
        rc == 0 and not posts(rec) and any(head in x for x in shows(rec))
        and any("not observed green on the PR head" in x for x in notes(out)),
        "rc=%s posts=%r" % (rc, posts(rec)))

    path, view, landed, head, green, mrc = landing_venue("nothing-landed", state="OPEN", merge_rc=1)
    rc, out, rec = run_landing(path, view, green, 1)
    arm("a merge that did NOT land publishes nothing",
        rc == 1 and not posts(rec) and any("nothing landed to publish for" in x for x in notes(out)),
        "rc=%s posts=%r" % (rc, posts(rec)))
    arm("a merge that did NOT land deletes no branch",
        deletes(path) == [], "deletes=%r rc=%s" % (deletes(path), rc))

    path, view, landed, head, green, mrc = landing_venue("mutant-ungated")
    ok = mutate(path, "scripts/merge-pr.sh", '  if [ "$post_rc" -ne 0 ]; then\n',
                '  if false; then\n')
    rc, out, rec = run_landing(path, view, False, mrc) if ok else (0, "", [])
    arm("MUTANT landed-green-gate-dropped IS CAUGHT (an ungated PR landing green)",
        ok and bool(posts(rec)) and any(landed in x for x in posts(rec)),
        "%s rc=%s posts=%r" % (planted(ok), rc, posts(rec)))


verify_arms()
poster_arms()
landing_arms()
venue_delivery_arms()
failed = [label for label, ok in ARMS if not ok]
if failed:
    print("check-gate-publish: FAIL — %d producer control(s) did not hold: %s"
          % (len(failed), "; ".join(failed)), file=sys.stderr)
    raise SystemExit(1)
print("  OK  %d producer control(s) held, mutants included: the gate of record's "
      "producers on the ordinary lane path and the landing seam can REFUSE, and the "
      "venue of record's precedence is kept for every venue that can deliver one"
      % len(ARMS))
PY
producer_rc=$?
case "$producer_rc" in
  0) ;;
  2) echo "check-gate-publish: CANNOT-ASSESS — the producers could not be provoked (see above), so whether they can refuse is not established" >&2
     cannot_assess=1 ;;
  *) echo "check-gate-publish: FAIL — the gate of record's producers are defective or cannot refuse (see above)" >&2
     fail=1 ;;
esac

if [ "$fail" -ne 0 ]; then
  echo "check-gate-publish: NOT-OK — the gate of record has no producer on the ordinary lane path or on the landing seam, or a producer of it cannot REFUSE" >&2
  exit 1
fi
if [ "$cannot_assess" -ne 0 ]; then
  echo "check-gate-publish: CANNOT-ASSESS — the producers could not be provoked in a venue, so whether they can refuse is NOT established" >&2
  exit 2
fi
echo "check-gate-publish: OK — the gate of record publishes its own verdict from the ordinary lane path (scripts/verify.sh) and from the landing seam (scripts/merge-pr.sh); a park posts nothing, an ungated PR stays unproduced, no run publishes an outcome it did not measure, a venue with no gh does not become a red gate, and the venue of record's precedence is kept for every venue that can deliver one"
exit 0
