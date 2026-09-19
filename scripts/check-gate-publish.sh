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


def venue(name, fixture_rc=None, poster="stub", entries=()):
    path = work / name
    shutil.rmtree(path, ignore_errors=True)
    copy_surface(path, REAL_POSTER if poster == "real" else ())
    neuter(path)
    if fixture_rc is not None:
        write(path / "scripts" / "check-producer-fixture.sh",
              "#!/usr/bin/env bash\nset -u\necho 'check-producer-fixture: rc %d'\nexit %d\n"
              % (fixture_rc, fixture_rc), 0o755)
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
set -u
if [ "${1:-}" = "pr" ] && [ "${2:-}" = "view" ]; then
  printf '%s' "$AO_CGS_GH_VIEW_JSON"
  exit 0
fi
if [ "${1:-}" = "pr" ] && [ "${2:-}" = "merge" ]; then
  printf '%s\\n' "${3:-}" >> "$AO_CGS_GH_MERGE_LOG"
  exit "${AO_CGS_GH_MERGE_RC:-0}"
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
    return path, view, "1" * 40, head, head_green, merge_rc


def run_landing(path, view, head_green, merge_rc):
    log = path / "stub.log"
    log.write_text("", encoding="utf-8")
    merge_log = path / "merge.log"
    merge_log.write_text("", encoding="utf-8")
    rc, out = run(["bash", "scripts/merge-pr.sh", "--pr", "1382"], path, {
        "PATH": "%s:%s" % (path / "bin", os.environ.get("PATH", "")),
        "AO_CGS_STUB_LOG": str(log),
        "AO_CGS_STUB_SHOW_RC": "0" if head_green else "1",
        "AO_CGS_GH_VIEW_JSON": view,
        "AO_CGS_GH_MERGE_LOG": str(merge_log),
        "AO_CGS_GH_MERGE_RC": str(merge_rc),
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
failed = [label for label, ok in ARMS if not ok]
if failed:
    print("check-gate-publish: FAIL — %d producer control(s) did not hold: %s"
          % (len(failed), "; ".join(failed)), file=sys.stderr)
    raise SystemExit(1)
print("  OK  %d producer control(s) held, mutants included: the gate of record's "
      "producers on the ordinary lane path and the landing seam can REFUSE" % len(ARMS))
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
echo "check-gate-publish: OK — the gate of record publishes its own verdict from the ordinary lane path (scripts/verify.sh) and from the landing seam (scripts/merge-pr.sh); a park posts nothing, an ungated PR stays unproduced, no run publishes an outcome it did not measure, and a venue with no gh does not become a red gate"
exit 0
