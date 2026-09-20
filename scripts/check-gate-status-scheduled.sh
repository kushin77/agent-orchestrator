#!/usr/bin/env bash
# check-gate-status-scheduled.sh -- the verb that publishes the CI venue of
# record's own concluded verdict must have a SCHEDULED invoker, and the schedule
# must be PROVED rather than declared (issue #1506; the same class as #830).
#
# THE DEFECT THIS EXISTS FOR
#   `scripts/gate-status.sh conclude` is the only path that can publish the
#   venue of record's own verdict for a commit whose run concluded AFTER the
#   box-side producer was (correctly) refused -- `post` publishes the GATE's
#   verdict, `reconcile` WITHDRAWS a green the venue contradicts, and neither can
#   produce the state a head needs when the run concludes late. The verb shipped
#   with #1504, provoked and correct, and NOTHING RAN IT:
#
#     $ grep -rn "gate-status.sh conclude" --include=*.sh --include=*.py . | wc -l  ->  0 in a live path
#     $ grep -rn "gate-status" config/fleet-jobs.json | wc -l                        ->  0
#
#   A head nothing publishes for is a head `scripts/pr-queue.sh` cannot merge: it
#   waits for a human while the venue's own verdict sits unread. #1504 measured
#   four such heads in one landing cycle ("venue=queued, published=none"); #1506
#   is the lane that gives the verb a schedule.
#
# WHY THE INVOKER IS THE OPS RUNNER RUNG
#   The venue cannot schedule it: `infra/cloudbuild/verify.yaml` posts nothing by
#   design (#1415) and its image carries neither `gh` nor `gcloud`
#   (#1350/#1361), so a venue that cannot reach the API cannot be the scheduled
#   publisher of its own verdict. The code-native rung that owns publishing the
#   gate of record for a PR head -- `ao-fleet-runner` in `config/fleet-jobs.json`
#   -- is therefore the invoker, and the verb is owned beside `post` in
#   `fleet/runner/verify.py`, because the two answer ONE question (which verdict
#   STANDS for this head) and a second owner would be a second producer of one
#   required context (#1357/#1415).
#
# WHAT IS PROVEN (against the real declaration, and against mutants of it)
#   1. the DECLARATION is the schedule: the rung exists in the manifest, carries
#      the marker the reconciler recognises, declares a five-field cron schedule
#      and a log, and the renderer EMITS its line under the role contract the
#      manifest itself names (`AO_RUNNER_HOST_ROLE=primary`) -- so "declared" and
#      "installed on the host that runs the fleet" are the same act;
#   2. the VERB is reached from the rung: the command's own entrypoint, or a
#      module it NAMES on its own first hop (the one-hop rule
#      `check-gate-status.sh`'s `reaches_producer` already applies to build
#      configs), invokes `bash scripts/gate-status.sh conclude` on a line that is
#      not a comment -- and the poster's own usage declares that verb;
#   3. the PRODUCER QUESTION counts the invoker (#1506's ask 3). The question is
#      driven for real (`--producer-probe`), never re-implemented, and it is
#      provoked BOTH ways: a tree whose only producer candidate is the file the
#      rung reaches is NAMED among its producers, while the same tree with that
#      file moved behind a `check-*.sh` name -- the obvious placement, and the
#      one the rule excludes because a checker is not a producer -- is not. So
#      the placement is load-bearing and measured, not a name without a producer;
#   4. the PR VERDICT PATH is untouched: a tree carrying the real
#      `infra/cloudbuild/verify.yaml` and `scripts/verify.sh` still answers
#      `produced`, so this lane adds a scheduled invoker without weakening the
#      control that already holds;
#   5. five mutations are REFUSED BY NAME -- the rung dropped, its command
#      repointed at a command that reaches no verb, its schedule dropped, its
#      role contract removed, and the invocation itself dropped. Each mutation is
#      asserted to have changed the bytes it edited (sha256 before/after), so a
#      mutation that never applied cannot be reported as caught;
#   6. the declaration is left BYTE-IDENTICAL: the gate reads it and writes
#      nothing, so a green here cannot be a green that edited what it grades.
#
# No network. Nothing outside the scratch tree is written. Exit-code contract:
# 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Test seam: GATE_STATUS_SCHEDULED_MANIFEST=<path> assesses another declaration
# instead of `config/fleet-jobs.json` (the SG_*/CHECK_DENYLIST/
# REAP_BRANCHES_MANIFEST pattern the sibling gates use).
#
# Usage: bash scripts/check-gate-status-scheduled.sh
# ---knowledge---
# module_id: scripts.check-gate-status-scheduled
# system: scripts
# app: scripts
# solution_class: pattern
# patterns: []
# derives_from: null
# owner_sme: unassigned
# tier: L1
# interfaces: [cleanup]
# invariants: ""
# gotchas: ""
# related: []
# do_not_duplicate: null
# ---knowledge---
set -uo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-gate-status-scheduled: CANNOT-ASSESS -- python3 not found" >&2
  exit 2
fi

manifest="${GATE_STATUS_SCHEDULED_MANIFEST:-$root/config/fleet-jobs.json}"
if [ ! -f "$manifest" ]; then
  echo "check-gate-status-scheduled: NOT-OK -- rung-declared: $manifest is missing, so the schedule's own definition of the invoker is gone" >&2
  exit 1
fi

for required in "$root/fleet/cron.py" "$root/fleet/runner/cli.py" "$root/fleet/runner/verify.py" \
                "$root/scripts/gate-status.sh" "$root/scripts/check-gate-status.sh"; do
  if [ ! -f "$required" ]; then
    echo "check-gate-status-scheduled: FAIL -- ${required#"$root"/} is missing, so the schedule cannot be proved to reach the verb" >&2
    exit 1
  fi
done

# The scratch tree name is the sanctioned fleet idiom (pid + clock), never a
# template whose placeholder is a run of one letter -- that literal is a match
# for this repo's own unfinished-marker scan. `mkdir` without `-p` refuses
# loudly instead of silently reusing another run's tree.
work="/tmp/ao1506-gate-status-scheduled.$$.$(date +%s%N)"
mkdir "$work" 2>/dev/null || {
  echo "check-gate-status-scheduled: CANNOT-ASSESS -- no scratch directory available" >&2
  exit 2
}
cleanup() { rm -rf "$work"; }
trap cleanup EXIT

# The producer question is driven from a COPY of the real checker placed OUTSIDE
# every fixture: the checker treats a `--root` that is not its own tree as
# foreign (the same-tree rule, #1394), so the fixtures are judged with the live
# question skipped and the tree half measured -- which is the half this check is
# about, and the half that needs no network.
cp "$root/scripts/check-gate-status.sh" "$work/checker.sh" || exit 2

python3 - "$root" "$manifest" "$work" "$work/checker.sh" <<'PY'
"""Prove the verb that publishes the venue of record's concluded verdict is
SCHEDULED by the ops runner rung, and that the producer question counts it."""
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

repo_root = Path(sys.argv[1]).resolve()
manifest_path = Path(sys.argv[2]).resolve()
scratch = Path(sys.argv[3]).resolve()
checker = Path(sys.argv[4]).resolve()
scratch.mkdir(parents=True, exist_ok=True)

sys.dont_write_bytecode = True
sys.path.insert(0, str(repo_root / "fleet"))

import cron  # noqa: E402

ENTRYPOINT = "fleet/runner/cli.py"
INVOKER_MODULE = "fleet/runner/verify.py"
POSTER = "scripts/gate-status.sh"
VERB = "conclude"
MARKER = "ao-fleet-runner"
ROLE_ENV = "AO_RUNNER_HOST_ROLE"
ROLE = "primary"

failures: list[str] = []


def probe(name, hold, detail=""):
    print("  probe %s: %s%s" % (name, "PASS" if hold else "FAIL", (" -- " + detail) if detail else ""))
    if not hold:
        failures.append(name)
    return hold


def sha_of_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def invocation_line(text: str, verb: str) -> str:
    """The non-comment line that invokes the poster with `verb`, or ''.

    The comment is stripped first, because a comment cannot run: a note reading
    "publish it with scripts/gate-status.sh" would otherwise certify an invoker
    that does not exist, which is the direction that fails OPEN.
    """
    for line in text.splitlines():
        code = re.sub(r"#.*", "", line)
        if POSTER in code and verb in code:
            return line.strip()
    return ""


def rung_jobs(manifest: dict) -> list[dict]:
    return [job for job in cron.manifest_jobs(manifest) if ENTRYPOINT in str(job.get("command") or "")]


def one_hop_files(root: Path, rel: str) -> list[Path]:
    """The files the rung's command reaches in ONE hop -- the repo's own rule.

    `check-gate-status.sh:reaches_producer` resolves a build config's producer as
    "the config itself, or a `.sh` the config's own steps NAME". The Python
    analogue is the import graph's first edge: the entrypoint, plus every module
    the entrypoint imports BY NAME. A producer two modules deep is deliberately
    not searched for, because it is not provably reachable from the command the
    schedule runs.
    """
    entry = root / rel
    named: list[Path] = [entry] if entry.is_file() else []
    if not entry.is_file():
        return named
    text = entry.read_text(encoding="utf-8", errors="replace")
    for match in re.finditer(r"^\s*from\s+([A-Za-z_][\w.]*)\s+import\s+([^\n]+)$", text, re.M):
        module, names = match.group(1), match.group(2)
        if not module.startswith("fleet.runner"):
            continue
        if module != "fleet.runner":
            candidate = root / (module.replace(".", "/") + ".py")
            if candidate.is_file():
                named.append(candidate)
        for name in re.findall(r"[A-Za-z_]\w*", names):
            candidate = root / "fleet" / "runner" / (name + ".py")
            if candidate.is_file():
                named.append(candidate)
    for match in re.finditer(r"^\s*import\s+([A-Za-z_][\w.]*)\s*$", text, re.M):
        module = match.group(1)
        if module.startswith("fleet.runner"):
            candidate = root / (module.replace(".", "/") + ".py")
            if candidate.is_file():
                named.append(candidate)
    seen: set[Path] = set()
    out: list[Path] = []
    for path in named:
        if path not in seen:
            seen.add(path)
            out.append(path)
    return out


def verb_reach(root: Path, label: str) -> tuple[list[str], str]:
    """Every named refusal for the rung's reached VERB. [] means the verb is reached."""
    files = one_hop_files(root, ENTRYPOINT)
    if not files:
        return [
            "schedule-runs-the-invoker: the rung's command names %s, which does not exist in %s, "
            "so the schedule runs nothing that can publish" % (ENTRYPOINT, label)
        ], ""
    for path in files:
        found = invocation_line(path.read_text(encoding="utf-8", errors="replace"), VERB)
        if found:
            return [], "%s :: %s" % (path.relative_to(root), found)
    return [
        "verb-invoked: nothing the rung's command reaches in one hop invokes '%s %s' on a line that "
        "runs, so the schedule names a command that cannot publish the venue's concluded verdict"
        % (POSTER, VERB)
    ], ""


def assess(manifest: dict, label: str) -> list[str]:
    """Every named refusal for ONE declaration. [] means the invoker is scheduled."""
    problems: list[str] = []
    jobs = rung_jobs(manifest)
    if not jobs:
        return [
            "rung-declared: no job in %s invokes %s, so the verb that publishes the venue of record's "
            "concluded verdict is scheduled by nothing and waits for a human" % (label, ENTRYPOINT)
        ]
    if len(jobs) > 1:
        problems.append(
            "rung-declared: %d jobs in %s invoke %s; one verb has ONE scheduled invoker, or the "
            "schedule has two owners" % (len(jobs), label, ENTRYPOINT)
        )
    job = jobs[0]
    if str(job.get("marker") or "") != MARKER:
        problems.append(
            "rung-marker: the job that invokes %s carries marker %r, not %r, so the reconciler cannot "
            "recognise the line it installs" % (ENTRYPOINT, job.get("marker"), MARKER)
        )
    schedule = str(job.get("schedule") or "")
    if len(schedule.split()) != 5 or not re.fullmatch(r"[0-9*/,\- ]+", schedule):
        problems.append(
            "rung-scheduled: the job's schedule %r is not a five-field cron expression, so no "
            "installer renders a line for it" % schedule
        )
    condition = job.get("enabled_when")
    if not isinstance(condition, dict) or str(condition.get("env")) != ROLE_ENV or str(condition.get("equals")) != ROLE:
        problems.append(
            "rung-installed: the job is not installed by the role contract (%s=%s), so it is declared "
            "on every host and rendered by none" % (ROLE_ENV, ROLE)
        )
    if not str(job.get("log") or ""):
        problems.append("rung-log: the job declares no log, so its refusals would be unreadable")
    return problems


# --- 0. the declared file must be readable, or the gate cannot assess --------
try:
    declared_bytes = manifest_path.read_bytes()
except OSError as exc:
    print("check-gate-status-scheduled: NOT-OK -- rung-declared: %s could not be read (%s)" % (manifest_path, exc), file=sys.stderr)
    sys.exit(1)
declared_sha = sha_of_bytes(declared_bytes)
try:
    manifest = json.loads(declared_bytes.decode("utf-8"))
except json.JSONDecodeError as exc:
    print("check-gate-status-scheduled: CANNOT-ASSESS -- %s is not valid JSON (%s)" % (manifest_path, exc), file=sys.stderr)
    sys.exit(2)

# --- 1. the real declaration schedules the invoker ---------------------------
real_problems = assess(manifest, str(manifest_path))
probe("REAL-RUNG-SCHEDULED", real_problems == [], "; ".join(real_problems) or "no problems")
for problem in real_problems:
    print("  refusal %s" % problem)

real_verb_problems, real_verb_where = verb_reach(repo_root, str(manifest_path))
probe("REAL-VERB-REACHED-FROM-THE-RUNG", real_verb_problems == [], real_verb_where or "; ".join(real_verb_problems))
for problem in real_verb_problems:
    print("  refusal %s" % problem)

# The renderer is the installer's single writer: the declaration is installed
# only where the role contract says so, and that rendering is what proves the
# rung WOULD FIRE rather than merely existing.
rung_job = rung_jobs(manifest)[0] if rung_jobs(manifest) else {}
primary = cron.render_lines(cron.enabled_jobs(manifest, env={ROLE_ENV: ROLE}))
standby = cron.render_lines(cron.enabled_jobs(manifest, env={ROLE_ENV: "standby"}))
primary_rung = [entry for entry in primary if entry.rstrip().endswith("# " + MARKER)]
probe(
    "RENDER-EMITS-THE-RUNG-LINE",
    len(primary_rung) == 1 and ENTRYPOINT in primary_rung[0] and len(standby) == len(primary) - 1,
    "%d line(s) with %s=%s, %d with standby" % (len(primary_rung), ROLE_ENV, ROLE, len(standby)),
)
probe(
    "RENDERED-LINE-IS-SINGLETON-WRAPPED",
    bool(primary_rung) and "flock -n -E 99" in primary_rung[0],
    primary_rung[0][:120] if primary_rung else "no rung line to render",
)

# The poster must still DECLARE the verb the schedule reaches: a command that
# names a verb the poster does not have is a name without a producer.
poster_text = (repo_root / POSTER).read_text(encoding="utf-8", errors="replace")
probe(
    "POSTER-DECLARES-THE-VERB",
    bool(re.search(r"^#\s+%s\s+--sha" % VERB, poster_text, re.M)),
    "the poster's own usage lists '%s --sha <sha>'" % VERB,
)

# --- 2. the producer QUESTION counts the invoker (#1506 ask 3) ---------------
def fixture(dest: Path, producer_body: str, producer_rel: str, with_path: bool, promoted: bool = False) -> Path:
    """A foreign tree whose only producer candidate is `producer_rel`."""
    (dest / "governance" / "platform").mkdir(parents=True, exist_ok=True)
    (dest / "infra" / "cloudbuild").mkdir(parents=True, exist_ok=True)
    (dest / producer_rel).parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(repo_root / "governance" / "platform" / "branch-protection.yaml",
                    dest / "governance" / "platform" / "branch-protection.yaml")
    (dest / "infra" / "cloudbuild" / "pr-trigger.yaml").write_text(
        "name: fixture-pr-verify\ndisabled: %s\nrepositoryEventConfig:\n  pullRequest:\n"
        "    branch: ^master$\nfilename: infra/cloudbuild/verify.yaml\n" % ("false" if promoted else "true"),
        encoding="utf-8")
    (dest / "scripts").mkdir(parents=True, exist_ok=True)
    (dest / POSTER).write_text(
        'CONTEXT="${AO_GATE_CONTEXT:-ao/gate-of-record}"\n', encoding="utf-8")
    (dest / producer_rel).write_text(producer_body, encoding="utf-8")
    if with_path:
        # The REAL verdict path, both halves: the build config the repo ships and
        # the script its own steps name. Copied, never paraphrased -- a fixture
        # that paraphrased them would measure the paraphrase.
        shutil.copyfile(repo_root / "infra" / "cloudbuild" / "verify.yaml",
                        dest / "infra" / "cloudbuild" / "verify.yaml")
        shutil.copyfile(repo_root / "scripts" / "verify.sh", dest / "scripts" / "verify.sh")
    else:
        (dest / "infra" / "cloudbuild" / "verify.yaml").write_text(
            "steps:\n  - id: verify\n    entrypoint: bash\n    args:\n      - -lc\n      - |\n        make verify\n",
            encoding="utf-8")
    return dest


def ask(fixture_root: Path) -> tuple[int, str]:
    proc = subprocess.run(
        ["bash", str(checker), "--producer-probe", "--root", str(fixture_root)],
        cwd=str(scratch), capture_output=True, text=True)
    return proc.returncode, proc.stdout + proc.stderr


invoker_source = (repo_root / INVOKER_MODULE).read_text(encoding="utf-8", errors="replace")
counted = fixture(scratch / "counted", invoker_source, INVOKER_MODULE, with_path=False)
rc, out = ask(counted)
probe(
    "QUESTION-COUNTS-THE-INVOKER",
    rc == 1 and "REQUIRED-BUT-UNPRODUCED" in out and INVOKER_MODULE in out,
    "rc=%s; the question names %s among the producers elsewhere in the tree" % (rc, INVOKER_MODULE),
)

# The negative half: the same tree, with the invocation moved behind a
# `check-*.sh` name -- the placement the rule EXCLUDES, because a checker is not
# a producer and a checker certifying its own premise is the vacuity trap
# `scripts/check-gate-status.sh` names for itself. It must NOT be counted.
moved = fixture(scratch / "moved", invoker_source, "scripts/check-gate-status-conclude.sh", with_path=False)
rc, out = ask(moved)
probe(
    "A-CHECKER-INVOKER-IS-NOT-COUNTED",
    rc == 1 and "has NO PRODUCER" in out and "check-gate-status-conclude.sh" not in out,
    "rc=%s; a checker that invokes the poster is not a producer, so the placement is load-bearing" % rc,
)

# The path half is NOT weakened by this lane: the real verdict path keeps its
# own producer. The fixture models the trigger PROMOTED (`disabled: false`),
# because a foreign tree gets no live answer -- on this repository the same
# question is answered from the live state (`live=enabled`, #1394), and the
# declaration alone would answer `gated-off`, which is a fact about the DECLARED
# state rather than about this lane.
onpath = fixture(scratch / "onpath", "# fixture: this file is not a producer\n", "fleet/runner/noop.py", with_path=True, promoted=True)
rc, out = ask(onpath)
probe(
    "PR-VERDICT-PATH-STILL-PRODUCED",
    rc == 0 and "verdict=produced" in out,
    "rc=%s; the real verify.yaml + scripts/verify.sh still answer 'produced' with the trigger promoted" % rc,
)

# --- 3. the mutations the gate must refuse, by name --------------------------
def semantic_sha(value) -> str:
    """The manifest's MEANING, not its formatting: a re-`indent` is not a mutation."""
    return sha_of_bytes(json.dumps(value, sort_keys=True).encode("utf-8"))


def mutate_declaration(label: str, expected: str, edit) -> None:
    """Mutate a SCRATCH COPY, prove the mutation TOOK, then refuse it by name.

    The proof is over the declaration's MEANING (`semantic_sha`), so rewriting the
    file with different indentation cannot be mistaken for a mutation that applied
    -- a mutation that never applied cannot be counted as caught.
    """
    clone = json.loads(declared_bytes.decode("utf-8"))
    edit(clone)
    if semantic_sha(clone) == semantic_sha(manifest):
        probe("MUTANT-%s-APPLIED" % label, False, "the mutation did not change the declaration")
        return
    probe("MUTANT-%s-APPLIED" % label, True,
          "manifest sha256 %s -> %s" % (semantic_sha(manifest)[:12], semantic_sha(clone)[:12]))
    path = scratch / ("declaration-%s.json" % label)
    path.write_bytes(json.dumps(clone, indent=2).encode("utf-8") + b"\n")
    problems = assess(json.loads(path.read_bytes().decode("utf-8")), str(path))
    named = [p for p in problems if p.startswith(expected + ":")]
    probe("MUTANT-%s-REFUSED" % label, bool(named),
          named[0] if named else "expected a %s refusal, ACTUAL: %s" % (expected, problems or "no refusal"))


def mutate_verb(label: str, replacements) -> None:
    """Mutate a scratch copy of the TREE the rung reaches, and refuse it by name."""
    tree = scratch / ("tree-%s" % label)
    if tree.exists():
        shutil.rmtree(tree)
    shutil.copytree(repo_root / "fleet", tree / "fleet",
                    ignore=shutil.ignore_patterns("__pycache__", "tests", "fixtures"))
    target = tree / INVOKER_MODULE
    before = target.read_text(encoding="utf-8")
    edited = before
    for old, new in replacements:
        edited = edited.replace(old, new)
    target.write_text(edited, encoding="utf-8")
    if edited == before:
        probe("MUTANT-%s-APPLIED" % label, False, "the mutation did not change %s" % INVOKER_MODULE)
        return
    probe("MUTANT-%s-APPLIED" % label, True,
          "%s sha256 %s -> %s" % (INVOKER_MODULE, sha_of_bytes(before.encode())[:12], sha_of_bytes(edited.encode())[:12]))
    problems, where = verb_reach(tree, str(tree))
    named = [p for p in problems if p.startswith("verb-invoked:")]
    probe("MUTANT-%s-REFUSED" % label, bool(named),
          named[0] if named else "expected a verb-invoked refusal, ACTUAL: %s" % (where or problems or "no refusal"))


def job_edit(edit):
    def apply(clone):
        for job in clone["jobs"]:
            if ENTRYPOINT in str(job.get("command") or ""):
                edit(job)
    return apply


mutate_declaration(
    "RUNG-DROPPED",
    "rung-declared",
    lambda clone: clone.__setitem__(
        "jobs", [j for j in clone["jobs"] if ENTRYPOINT not in str(j.get("command") or "")]),
)
mutate_declaration("SCHEDULE-DROPPED", "rung-scheduled", job_edit(lambda job: job.pop("schedule", None)))
mutate_declaration("ROLE-CONTRACT-REMOVED", "rung-installed", job_edit(lambda job: job.pop("enabled_when", None)))
# Repointing the rung at a command that does NOT run the entrypoint is the same
# refusal as dropping it, and it must be named the same way: the invoker is
# identified by the command the schedule runs, not by the marker alone.
mutate_declaration(
    "RUNG-REPOINTED-AWAY-FROM-THE-INVOKER",
    "rung-declared",
    job_edit(lambda job: job.__setitem__("command", "/usr/bin/python3 fleet/prune.py run --apply")),
)
# ...and the invocation itself, dropped from the file the rung reaches.
mutate_verb(
    "VERB-DROPPED",
    [('["bash", "scripts/gate-status.sh", "conclude", "--sha", sha]',
      '["bash", "scripts/no-such-poster.sh", "publish", "--sha", sha]')],
)

# --- 4. the declared file is left byte-identical ----------------------------
after_sha = sha_of_bytes(manifest_path.read_bytes())
print("  declaration sha256 before: %s" % declared_sha)
print("  declaration sha256 after:  %s" % after_sha)
probe("DECLARATION-LEFT-BYTE-IDENTICAL", after_sha == declared_sha, str(manifest_path))

if failures:
    print("check-gate-status-scheduled: FAILED probe(s): %s" % ", ".join(failures))
    sys.exit(1)
print("check-gate-status-scheduled: all probes PASS")
PY

rc=$?
if [ "$rc" -eq 2 ]; then
  echo "check-gate-status-scheduled: CANNOT-ASSESS -- the declaration could not be read, or the producer question could not be driven" >&2
  exit 2
fi
if [ "$rc" -ne 0 ]; then
  echo "check-gate-status-scheduled: NOT-OK -- the verb that publishes the venue of record's concluded verdict is not provably scheduled; see the refusal(s) above" >&2
  exit 1
fi

echo "check-gate-status-scheduled: OK -- the ao-fleet-runner rung is the declared schedule, the verb it reaches is the poster's own, and the producer question counts the file that invokes it (a checker-shaped invoker is provably NOT counted)"
exit 0
