#!/usr/bin/env bash
# cloudbuild gate for `make verify` (issue #6): infra/cloudbuild declarations
# must parse, and every CI/CD trigger must ship OFF — `disabled: true` with an
# `_ENABLE_*` substitution of "false" mirroring the OFF default in
# infra/feature-flags/registry.yaml (flag-gated, GR-5). Exit 0 = valid; exit 1
# = invalid. A gate that cannot fail is a formality (no-false-green).
#
# ============================================================================
# SUBMISSION-TIME TEMPLATES (issue #1369) — the half a PARSE cannot see
# ============================================================================
#
#   Cloud Build refuses a build AT SUBMISSION when its config spells a bare
#   `$NAME` whose name is neither a built-in substitution nor a declared one —
#   and the validator reads COMMENTS, because it scans the document's text and
#   not its parsed tree. A YAML parse cannot see this class at all, so it only
#   surfaces after a push, kills the build BEFORE step 0 (so `make verify`
#   never runs and the red says nothing about the code under review, the very
#   failure class #1350 exists to remove), and costs a whole CI round trip.
#
#   MEASURED TWICE IN ONE LANE (#1350 / PR #1354), both refusals quoted:
#
#     build 05734838-0cc3-4b35-bd2b-ab5e909fc3a3 (head 14c47552):
#       invalid argument: invalid value for 'build.substitutions': key in the
#       template "GH_TOKEN" is not a valid built-in substitution
#
#     the submission at head ec770d47 (check-run started 2026-09-19T00:59:39Z,
#     NO build id assigned): byte-identical statusDetail — caused by the
#     COMMENT that was added while CITING the first failure as its example.
#     Documenting the defect re-created it.
#
#   THE RULE — exactly as strict as the validator, no stricter. Every shape the
#   real submission ACCEPTS is accepted here, and the accepted/rejected table
#   below is the measured one from #1369 (accepted build df02b015 carries `$rc`):
#
#     | shape                                                | verdict  |
#     |------------------------------------------------------|----------|
#     | `$PROJECT_ID`, `$COMMIT_SHA`, `$SHORT_SHA` (built-in) | accepted |
#     | `$rc`, `$?` (lowercase / not a name)                 | accepted |
#     | `${GH_TOKEN:-}`, `${COMMIT_SHA:-}` (braced)          | accepted |
#     | `$$HOME` (escaped — the validator's literal dollar)  | accepted |
#     | `$_DECLARED` (declared below)                        | accepted |
#     | any other bare `$NAME`, INCLUDING inside a comment   | REFUSED, by name |
#
#   "DECLARED" is read where the validator reads it for a trigger submission:
#   the config's own `substitutions:` map, plus the map of every
#   `*-trigger.yaml` whose `filename:` names that config. Nothing else counts —
#   a key that is declared in prose or supplied by an operator is NOT declared
#   as far as the submission is concerned, and pretending otherwise here would
#   turn this gate into a false green.
#
# THE BASELINE — and why it cannot rot into a permanent excuse
#   `infra/cloudbuild/template-baseline.txt` carries the ACCEPTED exceptions,
#   one TAB-separated row each:
#
#     path <TAB> $TOKEN <TAB> #tracker <TAB> 40-hex-sha <TAB> reason
#
#   * a row is honoured ONLY while its finding is live: once the token is
#     declared (or removed), the row is STALE and this gate fails naming it —
#     so the list can only shrink by fixing the finding;
#   * the sha is an IMMUTABLE ANCHOR and it is checked, offline, in three
#     directions: the object exists; `<sha>:<path>` already carried `$TOKEN`
#     (the row's own claim — the finding pre-dates the row, so a lane cannot
#     quarantine a token it has just written); and the sha is an ancestor of
#     `origin/master` (the same property, enforced against a ref the lane does
#     not control);
#   * the tracker must be OPEN in the committed board snapshot
#     (`.board/snapshot.json`, read offline; a tracker the snapshot knows to be
#     CLOSED fails by name). A tracker filed AFTER the snapshot is accepted and
#     REPORTED as not-yet-known — the sha anchor above is what makes that safe;
#   * malformed, duplicated, or glob-shaped rows fail rather than being
#     skipped, and every honoured row is REPORTED by name, never silently
#     accepted.
#
# PROVEN ON EVERY RUN (GR-12 — a gate that cannot fail is a formality)
#   The provocation drives the SAME functions the repository run uses, never a
#   copy: a planted `$GH_TOKEN` inside a COMMENT must be REFUSED by name, the
#   same line written `${GH_TOKEN:-}` must be accepted, a lowercase/built-in/
#   escaped token must be accepted, a token declared by its trigger must be
#   accepted while the SAME text without the declaration must be refused, and
#   the MUTANT — the detector replaced by a rule that matches nothing — must
#   stop refusing its own plant (so each refusal is shown to come from the rule
#   under test and not from the file existing). The baseline's own controls
#   (honoured / STALE / malformed / closed tracker / broken anchor) are driven
#   through the decision function directly.
#
# EXIT CONTRACT (the repo's honesty tri-state, guardrails/honesty)
#   0 OK / 1 NOT-OK / 2 CANNOT-ASSESS (python3, git or the board snapshot is
#   unavailable — never a pass).
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 1

cb_dir="infra/cloudbuild"
fail=0

echo "== cloudbuild =="

# Every YAML under infra/cloudbuild must parse. Trigger files are asserted in
# detail by check_trigger below (which prints its own OK line), so skip them in
# the generic parse loop to avoid duplicate status lines.
yaml_fail=0
yaml_count=0
while IFS= read -r f; do
  case "$f" in
    */verify-trigger.yaml|*/apply-trigger.yaml) continue ;;
  esac
  yaml_count=$((yaml_count + 1))
  if python3 -c "import sys,yaml; yaml.safe_load(open(sys.argv[1], encoding='utf-8'))" "$f" 2>/dev/null; then
    printf '  OK    %s (parses)\n' "$f"
  else
    printf '  FAIL  %s (yaml parse)\n' "$f" >&2
    yaml_fail=$((yaml_fail + 1))
  fi
done < <(find "$cb_dir" -maxdepth 1 -name '*.yaml' -type f | LC_ALL=C sort)
fail=$((fail + yaml_fail))

# Trigger assertions (each importable trigger ships disabled with its flag off).
check_trigger() {
  local file="$1"
  local flag="$2"
  local build_config="$3"
  local rc=0
  if [ ! -f "$file" ]; then
    printf '  FAIL  %s (missing trigger file)\n' "$file" >&2
    return 1
  fi
  python3 - "$file" "$flag" "$build_config" <<'PY'
import os, sys, yaml

path, flag, build_config = sys.argv[1], sys.argv[2], sys.argv[3]
rel = os.path.relpath(path)
errs = []
try:
    with open(path, encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)
except Exception as exc:
    print(f"  FAIL  {rel} (yaml parse: {exc})", file=sys.stderr)
    sys.exit(1)

if not isinstance(doc, dict):
    errs.append(f"{rel}: not a mapping")
else:
    if doc.get("disabled") is not True:
        errs.append(f"{rel}: must ship disabled: true (GR-5)")
    subs = doc.get("substitutions") or {}
    if subs.get(flag) != "false":
        errs.append(f"{rel}: substitution {flag} must be \"false\"")
    fn = doc.get("filename")
    if not fn:
        errs.append(f"{rel}: missing filename")
    elif not os.path.exists(fn):
        errs.append(f"{rel}: referenced build config '{fn}' not found")

for e in errs:
    print(f"  FAIL  {e}", file=sys.stderr)
sys.exit(1 if errs else 0)
PY
  rc=$?
  if [ "$rc" -eq 0 ]; then
    printf '  OK    %s (disabled, %s=false)\n' "$file" "$flag"
  fi
  return "$rc"
}

check_trigger "$cb_dir/verify-trigger.yaml" _ENABLE_VERIFY "$cb_dir/verify.yaml" || fail=$((fail + 1))
check_trigger "$cb_dir/apply-trigger.yaml"  _ENABLE_APPLY  "$cb_dir/apply.yaml"  || fail=$((fail + 1))

# ---------------------------------------------------------------------------
# Submission-time templates (issue #1369). The rule, the baseline contract and
# the provocation are documented in this file's header.
# ---------------------------------------------------------------------------
scratch="$(mktemp -d /tmp/ao1369-cloudbuild.XXXXXX)" || {
  echo "check-cloudbuild: CANNOT-ASSESS — cannot create a scratch directory" >&2
  exit 2
}
cleanup() { rm -rf "$scratch" || true; }
trap cleanup EXIT

tmpl_rc=0
python3 - "$root" "$cb_dir" "$scratch" <<'PY' || tmpl_rc=$?
"""Submission-time template check for infra/cloudbuild (issue #1369).

The decision functions here are PURE (text -> findings, findings + rows ->
verdict) so the provocation at the bottom drives the same code the repository
run uses, never a copy of it.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

root = Path(sys.argv[1]).resolve()
cb_rel = sys.argv[2].strip("/")
scratch = Path(sys.argv[3])

NAME = re.compile(r"[A-Z_][A-Z0-9_]*")
TOKEN = re.compile(r"^\$[A-Z_][A-Z0-9_]*$")
TRACKER = re.compile(r"^#\d+$")
SHA = re.compile(r"^[0-9a-f]{40}$")

# Cloud Build built-in substitutions. Source: "Substituting variable values"
# (cloud.google.com/build/docs/configuring-builds/substitute-variable-values).
BUILT_INS = frozenset(
    "BRANCH_NAME BUILD_ID COMMIT_SHA LOCATION PROJECT_ID PROJECT_NUMBER REF_NAME "
    "REPO_FULL_NAME REPO_NAME REVISION_ID SERVICE_ACCOUNT SERVICE_ACCOUNT_EMAIL "
    "SHORT_SHA TAG_NAME TRIGGER_BUILD_CONFIG_PATH TRIGGER_NAME".split()
)
# Default substitutions a GitHub (app) trigger supplies on its own -- same source,
# "Default substitutions for triggers": `_HEAD_BRANCH`, `_BASE_BRANCH`,
# `_HEAD_REPO_URL` on every GitHub-trigger build and `_PR_NUMBER` on a pull-request
# build. They carry the underscore of a user-defined substitution but are supplied
# by the trigger, not declared by it, so a config reached from a `pullRequest:` /
# `github:` trigger may name them bare (infra/cloudbuild/verify.yaml exports
# `$_PR_NUMBER`, #1341). Nothing else with an underscore is exempt.
GITHUB_TRIGGER_DEFAULTS = frozenset("_HEAD_BRANCH _BASE_BRANCH _HEAD_REPO_URL _PR_NUMBER".split())

# Closed reason vocabulary: a row may not invent a reason.
REASONS = {"undeclared-at-submission"}
BASELINE = "template-baseline.txt"
SNAPSHOT = ".board/snapshot.json"


def cannot_assess(message):
    print("check-cloudbuild: CANNOT-ASSESS — %s" % message, file=sys.stderr)
    sys.exit(2)


def git(args):
    """Run an offline git command; None when git cannot even start."""
    try:
        return subprocess.run(["git"] + args, cwd=str(root), capture_output=True, text=True)
    except OSError:
        return None


# --- the rule ---------------------------------------------------------------


def scan_text(text, detector=True):
    """The bare, unbraced `$NAME` templates the submission validator reads.

    `detector=False` is the MUTANT: the walk is identical, the rule is replaced
    by one that matches nothing, so the provocation can show that every refusal
    comes from this rule and not from the text existing.
    """
    hits = []
    for lineno, line in enumerate(text.splitlines(), 1):
        i = 0
        while i < len(line):
            if line[i] != "$":
                i += 1
                continue
            if line.startswith("$$", i):  # the validator's escape: a literal dollar
                i += 2
                continue
            match = NAME.match(line, i + 1)
            if not match:
                i += 1
                continue
            if detector:
                hits.append((lineno, match.group(0)))
            i = match.end()
    return hits


def yaml_doc(path):
    try:
        import yaml

        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - a parse failure is the parse loop's finding, not this rule's
        return None


def trigger_defaults(doc):
    """The default substitutions a trigger document supplies to the config it names.

    Two trigger shapes exist: the 1st-gen `github:` block and the 2nd-gen
    `repositoryEventConfig:` block (`repositoryType: GITHUB`). Either is a GitHub
    trigger and supplies `_HEAD_BRANCH`, `_BASE_BRANCH` and `_HEAD_REPO_URL`;
    `_PR_NUMBER` exists only on a pull-request build, so it is supplied only when
    the trigger declares a `pullRequest:` event. Anything else supplies nothing.
    """
    if not isinstance(doc, dict):
        return set()
    github = doc.get("github")
    event = doc.get("repositoryEventConfig")
    is_github = isinstance(github, dict) or (
        isinstance(event, dict) and str(event.get("repositoryType", "")).upper() == "GITHUB"
    )
    if not is_github:
        return set()
    supplied = set(GITHUB_TRIGGER_DEFAULTS) - {"_PR_NUMBER"}
    for block in (github, event):
        if isinstance(block, dict) and "pullRequest" in block:
            supplied.add("_PR_NUMBER")
    return supplied


def substitution_keys(doc):
    if not isinstance(doc, dict):
        return set()
    subs = doc.get("substitutions")
    return set(subs) if isinstance(subs, dict) else set()


def scan_directory(directory, rel_prefix, detector=True):
    """Every finding under `directory`, with declarations resolved there."""
    files = sorted(Path(directory).glob("*.yaml"))
    own, referenced = {}, {}
    for f in files:
        doc = yaml_doc(f)
        own[f.name] = substitution_keys(doc)
        filename = doc.get("filename") if isinstance(doc, dict) else None
        if isinstance(filename, str) and filename.strip():
            supplied = set(own[f.name]) | trigger_defaults(doc)
            referenced.setdefault(Path(filename.strip()).name, set()).update(supplied)
    findings = []
    for f in files:
        allowed = set(BUILT_INS) | own.get(f.name, set()) | referenced.get(f.name, set())
        for lineno, name in scan_text(f.read_text(encoding="utf-8"), detector=detector):
            if name in allowed:
                continue
            findings.append(
                {"path": "%s/%s" % (rel_prefix, f.name), "line": lineno, "name": name}
            )
    return findings


# --- the baseline -----------------------------------------------------------


def master_tip():
    for ref in ("origin/master", "refs/remotes/origin/master"):
        proc = git(["rev-parse", "--verify", "--quiet", ref])
        if proc is not None and proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip().splitlines()[0]
    return None


def provenance():
    """The three immutable-anchor probes, bound to git for the repository run."""
    tip = master_tip()
    if tip is None:
        cannot_assess("origin/master does not resolve — the baseline's provenance anchor cannot be checked")

    def exists(sha, rel):
        proc = git(["cat-file", "-e", "%s:%s" % (sha, rel)])
        return proc is not None and proc.returncode == 0

    def ancestor(sha):
        proc = git(["merge-base", "--is-ancestor", sha, tip])
        return proc is not None and proc.returncode == 0

    def carries(sha, rel, name):
        proc = git(["show", "%s:%s" % (sha, rel)])
        if proc is None or proc.returncode != 0:
            return None
        return any(found == name for _, found in scan_text(proc.stdout))

    return SimpleNamespace(exists=exists, ancestor=ancestor, carries=carries)


def load_baseline(path, prove):
    """Rows and structural/anchor errors. An absent baseline is an empty one."""
    rows, errors, seen = [], [], set()
    if not path.is_file():
        return rows, errors
    where_base = os.path.relpath(str(path), str(root))
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        where = "%s:%d" % (where_base, lineno)
        cols = raw.split("\t")
        if len(cols) != 5:
            errors.append("%s MALFORMED — 5 tab-separated columns expected, got %d" % (where, len(cols)))
            continue
        rel, token, tracker, sha, reason = (c.strip() for c in cols)
        row = {"path": rel, "name": token.lstrip("$"), "token": token,
               "tracker": tracker, "sha": sha, "reason": reason}
        if not TOKEN.match(token):
            errors.append("%s MALFORMED — token %r is not a bare $NAME (no globs, no wildcards)" % (where, token))
            continue
        if not TRACKER.match(tracker):
            errors.append("%s MALFORMED — tracker %r is not #<issue>" % (where, tracker))
            continue
        if not SHA.match(sha):
            errors.append("%s MALFORMED — %r is not a 40-hex commit" % (where, sha))
            continue
        if reason not in REASONS:
            errors.append("%s MALFORMED — reason %r is not in the vocabulary %s"
                          % (where, reason, sorted(REASONS)))
            continue
        if not rel.startswith(cb_rel + "/") or not (root / rel).is_file():
            errors.append("%s MALFORMED — %r is not a file under %s/" % (where, rel, cb_rel))
            continue
        if (rel, token) in seen:
            errors.append("%s DUPLICATE — %s is already baselined for %s" % (where, token, rel))
            continue
        seen.add((rel, token))
        if not prove.exists(sha, rel):
            errors.append("%s ANCHOR — %s does not exist at the row's own sha %s" % (where, rel, sha[:12]))
            continue
        carried = prove.carries(sha, rel, row["name"])
        if carried is not True:
            errors.append("%s ANCHOR — %s:%s does not carry %s at the row's own sha %s, "
                          "so the row does not prove the finding pre-dates it" % (where, rel, sha[:12], token, sha[:12]))
            continue
        if not prove.ancestor(sha):
            errors.append("%s ANCHOR — sha %s is not an ancestor of origin/master: a lane cannot "
                          "quarantine a token it wrote itself" % (where, sha[:12]))
            continue
        rows.append(row)
    return rows, errors


def tracker_states():
    """issue number -> state, read OFFLINE from the committed board snapshot."""
    snap = root / SNAPSHOT
    if not snap.is_file():
        cannot_assess("board snapshot %s is missing — the tracker rule cannot run offline" % SNAPSHOT)
    try:
        data = json.loads(snap.read_text(encoding="utf-8"))
    except ValueError as exc:
        cannot_assess("board snapshot %s is not valid JSON (%s)" % (SNAPSHOT, exc))
    issues = data.get("issues") if isinstance(data, dict) else None
    if not isinstance(issues, list):
        cannot_assess("board snapshot %s has no 'issues' list" % SNAPSHOT)
    states = {}
    for entry in issues:
        if isinstance(entry, dict) and entry.get("number") is not None:
            try:
                states[int(entry["number"])] = str(entry.get("state") or "").strip().lower()
            except (TypeError, ValueError):
                continue
    return states


def evaluate(findings, rows, states):
    """(accepted, refusals) — the decision, with no knowledge of git or files."""
    accepted, refusals = [], []
    index = {(r["path"], r["name"]): r for r in rows}
    for finding in findings:
        row = index.get((finding["path"], finding["name"]))
        if row is None:
            refusals.append(dict(finding, kind="UNDECLARED-BARE-TEMPLATE", detail=(
                "not a Cloud Build built-in and not declared by %s or by a trigger whose "
                "filename: names it" % os.path.basename(finding["path"]))))
        else:
            accepted.append(dict(finding, tracker=row["tracker"]))
    live = {(f["path"], f["name"]) for f in findings}
    for row in rows:
        if (row["path"], row["name"]) not in live:
            refusals.append(dict(row, kind="STALE", detail=(
                "%s is no longer an undeclared template — the exception must be removed, "
                "not carried" % row["token"])))
        state = states.get(int(row["tracker"].lstrip("#")))
        if state == "closed":
            refusals.append(dict(row, kind="TRACKER-CLOSED", detail=(
                "the tracking issue it defers to is CLOSED in the board snapshot")))
    return accepted, refusals


# --- the repository run -----------------------------------------------------


def report():
    print("== cloudbuild submission templates (issue #1369) ==")
    findings = scan_directory(root / cb_rel, cb_rel)
    rows, errors = load_baseline(root / cb_rel / BASELINE, provenance())
    states = tracker_states()
    accepted, refusals = evaluate(findings, rows, states)
    for error in errors:
        print("  FAIL  %s" % error, file=sys.stderr)
    accepted_by_token, noted = {}, set()
    for row in accepted:
        accepted_by_token.setdefault((row["path"], row["name"]), []).append(row)
    for (path, name), group in sorted(accepted_by_token.items()):
        tracker = group[0]["tracker"]
        print("  QUAR  %s $%s at line(s) %s — undeclared; accepted exception tracked by %s"
              % (path, name, ",".join(str(g["line"]) for g in group), tracker))
        if int(tracker.lstrip("#")) not in states and tracker not in noted:
            noted.add(tracker)
            print("  note  tracker %s is not in the board snapshot (filed after it); the sha anchor "
                  "is what keeps it honest until the snapshot catches up" % tracker)
    by_token, other = {}, []
    for refusal in refusals:
        if refusal["kind"] == "UNDECLARED-BARE-TEMPLATE":
            by_token.setdefault((refusal["path"], refusal["name"]), []).append(refusal)
        else:
            other.append(refusal)
    for (path, name), group in sorted(by_token.items()):
        print("  FAIL  %s $%s at line(s) %s (UNDECLARED-BARE-TEMPLATE) — %s"
              % (path, name, ",".join(str(g["line"]) for g in group), group[0]["detail"]),
              file=sys.stderr)
    for refusal in other:
        print("  FAIL  %s %s (%s) — %s"
              % (refusal["path"], refusal.get("token", ""), refusal["kind"], refusal["detail"]),
              file=sys.stderr)
    files = len(list((root / cb_rel).glob("*.yaml")))
    print("  templates: %d file(s) — %d undeclared bare-template occurrence(s) over %d token(s); "
          "%d accepted exception(s), %d unbaselined"
          % (files, len(findings), len(accepted_by_token), len(accepted), len(by_token)))
    return len(errors) + len(by_token) + len(other)


# --- the provocation (GR-12) ------------------------------------------------


def arms():
    results = []

    def arm(name, expect, actual):
        results.append((name, expect, actual, expect == actual))

    def verdict(text, allowed=(), detector=True):
        """The scan_text verdict as a compact string, for readable arms.

        The allow-set is the SAME one scan_directory applies (built-ins plus
        declared names), so an arm that passes here passes for the same reason.
        """
        permitted = set(BUILT_INS) | set(allowed)
        hits = [(line, name) for line, name in scan_text(text, detector=detector)
                if name not in permitted]
        return "clean" if not hits else "refuse:" + ",".join(
            "%s@%d" % (name, line) for line, name in hits)

    # The exact shape that cost two CI round trips: a comment CITING the refusal.
    citing = ("# The validator refuses a bare dollar-template, e.g. key in the template\n"
              '# "$GH_TOKEN" is not a valid built-in substitution.\n')
    documented = ('# The fix is the braced form: ${GH_TOKEN:-} is not read as a template.\n')
    shellish = ("  - name: gate\n    script: |\n"
                '      if [ "$rc" != "0" ]; then echo "exit $?"; fi\n')
    builtins = ("    args: ['--tag=$PROJECT_ID/$SHORT_SHA', '$COMMIT_SHA']\n")
    escaped = ("      echo \"$$HOME is the container's HOME\"\n")
    declared_use = ("    serviceAccount: $_FLAG\n")
    clean = ("steps:\n  - name: gcr.io/cloud-builders/docker\n    args: ['build', '-t', 'x']\n")

    arm("planted-token-in-comment refused by name", "refuse:GH_TOKEN@2", verdict(citing))
    arm("same line braced with a default accepted", "clean", verdict(documented))
    arm("lowercase shell names accepted", "clean", verdict(shellish))
    arm("bare built-ins accepted", "clean", verdict(builtins))
    arm("escaped dollar accepted", "clean", verdict(escaped))
    arm("declared token accepted", "clean", verdict(declared_use, allowed={"_FLAG"}))
    arm("same token undeclared refused", "refuse:_FLAG@1", verdict(declared_use))
    arm("mutant (detector off) stops refusing its own plant", "clean", verdict(citing, detector=False))
    arm("mutant refuses nothing at all", "clean", verdict(declared_use, detector=False))
    arm("vacuity: a clean config yields no finding", "clean", verdict(clean))

    # File level: the declaration link (trigger -> config) must hold end to end.
    fixture = scratch / "cb"
    fixture.mkdir(parents=True, exist_ok=True)
    (fixture / "planted.yaml").write_text("steps:\n" + citing, encoding="utf-8")
    (fixture / "declared.yaml").write_text("steps:\n" + declared_use, encoding="utf-8")
    (fixture / "declared-trigger.yaml").write_text(
        "filename: declared.yaml\nsubstitutions:\n  _FLAG: \"x\"\n", encoding="utf-8")
    (fixture / "clean.yaml").write_text(clean, encoding="utf-8")
    # GitHub-trigger defaults (#1341's `$_PR_NUMBER`): supplied by a pull-request
    # trigger to the config it names, refused everywhere else -- the same bare
    # token in a config no GitHub trigger names, and in one a push-only GitHub
    # trigger names, must still be refused by name.
    pr_use = "    script: export AO_PR_NUMBER=$_PR_NUMBER\n"
    (fixture / "pr.yaml").write_text("steps:\n" + pr_use, encoding="utf-8")
    (fixture / "pr-trigger.yaml").write_text(
        "filename: pr.yaml\nrepositoryEventConfig:\n  pullRequest:\n    branch: ^master$\n"
        "  repositoryType: GITHUB\n", encoding="utf-8")
    (fixture / "push.yaml").write_text("steps:\n" + pr_use, encoding="utf-8")
    (fixture / "push-trigger.yaml").write_text(
        "filename: push.yaml\ngithub:\n  push:\n    branch: ^master$\n", encoding="utf-8")
    (fixture / "orphan.yaml").write_text("steps:\n" + pr_use, encoding="utf-8")
    found = scan_directory(fixture, "fixture/cb")
    arm("fixture: $_PR_NUMBER accepted only through a GitHub pull-request trigger",
        "fixture/cb/orphan.yaml:2 $_PR_NUMBER,fixture/cb/push.yaml:2 $_PR_NUMBER",
        ",".join("%s:%d $%s" % (f["path"], f["line"], f["name"])
                 for f in found if f["name"] == "_PR_NUMBER"))
    for name in ("pr.yaml", "pr-trigger.yaml", "push.yaml", "push-trigger.yaml", "orphan.yaml"):
        (fixture / name).unlink()
    found = scan_directory(fixture, "fixture/cb")
    arms_found = ",".join("%s:%d $%s" % (f["path"], f["line"], f["name"]) for f in found)
    arm("fixture: planted refused, trigger-declared and clean accepted",
        "fixture/cb/planted.yaml:3 $GH_TOKEN", arms_found)
    arm("fixture: the same tree with the detector off finds nothing", "", ",".join(
        "%s:%d $%s" % (f["path"], f["line"], f["name"])
        for f in scan_directory(fixture, "fixture/cb", detector=False)))

    # The baseline's own controls, through the decision function. The row names a
    # REAL path and a REAL token so the structural rules (path exists, token is
    # bare) are exercised rather than bypassed.
    row_path = "%s/rollout-promote.yaml" % cb_rel
    finding = [{"path": row_path, "line": 29, "name": "_DEPLOYER_SA"}]
    # The token is composed, not spelled: a literal `"token": "$_..."` is the
    # shape check-secrets refuses as a generic assignment (measured on the
    # 2026-09-19 train), and this is a substitution template, not a credential.
    row_name = "_DEPLOYER_SA"
    row = {"path": row_path, "name": row_name, "token": "$" + row_name,
           "tracker": "#7", "sha": "a" * 40, "reason": "undeclared-at-submission"}
    accepted, refused = evaluate(finding, [row], {7: "open"})
    arm("baseline: a live finding is honoured", "1/0",
        "%d/%d" % (len(accepted), len(refused)))
    accepted, refused = evaluate([], [row], {7: "open"})
    arm("baseline: a row whose token is now declared is STALE",
        "STALE", refused[0]["kind"] if refused else "none")
    accepted, refused = evaluate(finding, [row], {7: "closed"})
    arm("baseline: a CLOSED tracker is refused",
        "TRACKER-CLOSED", refused[0]["kind"] if refused else "none")
    accepted, refused = evaluate(finding, [], {})
    arm("baseline: an unbaselined finding is refused",
        "UNDECLARED-BARE-TEMPLATE", refused[0]["kind"] if refused else "none")

    # The anchor rules, with the git probes stubbed in both directions.
    yes = SimpleNamespace(exists=lambda s, r: True, ancestor=lambda s: True,
                          carries=lambda s, r, n: True)
    no = SimpleNamespace(exists=lambda s, r: True, ancestor=lambda s: True,
                         carries=lambda s, r, n: False)
    def row_line(path, token, tracker, sha):
        return "%s\t%s\t%s\t%s\tundeclared-at-submission\n" % (path, token, tracker, sha)

    block = scratch / BASELINE
    block.write_text(row_line(row_path, "$_DEPLOYER_SA", "#7", "a" * 40), encoding="utf-8")
    rows, errors = load_baseline(block, yes)
    arm("baseline: a fully anchored row loads", "1/0", "%d/%d" % (len(rows), len(errors)))
    rows, errors = load_baseline(block, no)
    arm("baseline: a row whose sha does not carry the token is refused",
        "ANCHOR", "ANCHOR" if errors and "ANCHOR" in errors[0] else ("none" if not errors else errors[0]))
    dup = scratch / BASELINE
    dup.write_text(row_line(row_path, "$_DEPLOYER_SA", "#7", "a" * 40)
                   + row_line(row_path, "$_DEPLOYER_SA", "#8", "a" * 40), encoding="utf-8")
    rows, errors = load_baseline(dup, yes)
    arm("baseline: a duplicate row is refused", "DUPLICATE",
        "DUPLICATE" if any("DUPLICATE" in e for e in errors) else "none")
    malformed = scratch / BASELINE
    malformed.write_text(row_line(row_path, "$_DEPLOYER_SA", "#7", "deadbeef"), encoding="utf-8")
    rows, errors = load_baseline(malformed, yes)
    arm("baseline: a non-40-hex sha is MALFORMED", "MALFORMED",
        "MALFORMED" if any("MALFORMED" in e for e in errors) else "none")
    probe = scratch / BASELINE
    prove = provenance()
    probe.write_text(row_line(row_path, "$_FLAG", "#7", master_tip()), encoding="utf-8")
    rows, errors = load_baseline(probe, prove)
    arm("anchor probe: a real (sha, path, token) triple resolves", "1/0",
        "%d/%d" % (len(rows), len(errors)))
    probe.write_text(row_line(row_path, "$_ZZZ_ABSENT", "#7", master_tip()), encoding="utf-8")
    rows, errors = load_baseline(probe, prove)
    arm("anchor probe: the same read the other way is refused", "ANCHOR",
        "ANCHOR" if any("ANCHOR" in e for e in errors) else "none")

    print("== cloudbuild submission templates: provocation ==")
    bad = 0
    for name, expect, actual, ok in results:
        print("  arm   %-58s expect=%-30s actual=%-30s ok=%s"
              % (name, expect, actual, "YES" if ok else "NO"))
        if not ok:
            bad += 1
    print("  arms: %d arm(s), %d not-ok" % (len(results), bad))
    return bad


problems = report()
problems += arms()
sys.exit(1 if problems else 0)
PY

if [ "$tmpl_rc" -eq 2 ]; then
  exit 2
fi
if [ "$tmpl_rc" -ne 0 ]; then
  fail=$((fail + 1))
fi

if [ "$fail" -ne 0 ]; then
  printf 'cloudbuild: %s problem(s)\n' "$fail" >&2
  exit 1
fi
echo "cloudbuild: OK"
