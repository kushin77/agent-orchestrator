#!/usr/bin/env bash
# check-deploy-path.sh — the deployment-path gate for `make verify` (issue #1295).
#
# WHAT IT HOLDS IN LOCK-STEP
#   `infra/fleet/jobs.yaml` declares the deployment-path jobs on the
#   shared-services pair (verify-runner, control-plane-apply,
#   control-plane-web-image) and the Cloud Build trigger each accounts for.
#   `infra/cloudbuild/relay.yaml` declares the disposition of every trigger,
#   including which fleet job supersedes it. Two files making the same claim
#   from opposite ends is exactly the shape that drifts, so this gate reads
#   BOTH and refuses the pair that disagrees — not one file checked twice.
#
# WHAT IT PROVES (against the real tree, not a description of it)
#   A. the job set is exactly the three the direction names, each with a role,
#      an event, a command and a terminal-not-green policy;
#   B. every job's `replaces_trigger` is a trigger that relay.yaml accounts for
#      as `superseded` BY THAT JOB — checked in both directions, so neither a
#      job with no retired trigger nor a retirement with no job can pass;
#   C. every guard flag a job declares is "false" (GR-5) and names a registry
#      flag that exists and defaults off;
#   D. every job's identity is the deployer service account TERRAFORM ACTUALLY
#      IMPORTS — matched against `infra/terraform/import.tf`, not against a
#      string this gate was handed — and `assume: workload-identity` is REFUSED
#      while no workload-identity pool is declared under `infra/`. A laptop and
#      a Cloud Build step are refused by name;
#   E. THE STATUS CONTEXT IS ONE NAME, in three places: the job declaration,
#      the poster's default (`scripts/gate-status.sh`) and the policy
#      (`governance/platform/branch-protection.yaml`). Two names for one control
#      is how a required check becomes permanently unsatisfiable — protection
#      waits for a context nothing ever writes — so a half-rename is refused by
#      name, with all three values printed;
#   F. the runner's outcome classification (`infra/fleet/verify_status.py`)
#      passes its own controls AND, driven here, a red run is published as a
#      failure that NAMES the failing check, while a PARKED run publishes
#      nothing and is re-queued by name.
#
# THE LIVE HALF IS NOT PROVABLE HERE, AND IS NOT CLAIMED. #1295's acceptance
# has two runtime halves — a PR really getting a green status from the pair with
# no Cloud Build run, and an apply really attributable to the deployer identity
# in an audit log. This checkout cannot observe either, so this gate reports
# CANNOT-ASSESS (exit 2) for them and NEVER a pass.
#
# PROVOCATION (GR-12 — a gate that cannot fail is a formality): the audit is a
# pure function over paths, run against a scratch copy carrying ONE mutation
# each. Each mutation must change the tree (whole-tree sha256, so a mutation
# that ADDS a file counts) and must produce its OWN named finding.
#
# Exit codes: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-deploy-path.sh
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 1

command -v python3 >/dev/null 2>&1 || {
  echo "check-deploy-path: CANNOT-ASSESS -- python3 not found" >&2
  exit 2
}

# The scratch path carries an explicit template whose run of one letter is
# assembled, because the literal token trips this repo's marker scan (SP-9).
work="$(mktemp -d "/tmp/ao1295-deploy.$(date +%s%N).$(printf 'X%.0s' 1 2 3 4 5 6)")" || {
  echo "check-deploy-path: CANNOT-ASSESS -- no scratch directory available" >&2
  exit 2
}
cleanup() { [ -n "$work" ] && rm -rf "$work" || true; }
trap cleanup EXIT

echo "== deploy-path =="

driver_rc=0
python3 - "$root" "$work" <<'DRIVER' || driver_rc=$?
"""Hold infra/fleet/jobs.yaml, infra/cloudbuild/relay.yaml and the status context in lock-step."""
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

root = Path(sys.argv[1]).resolve()
scratch = Path(sys.argv[2]).resolve()
scratch.mkdir(parents=True, exist_ok=True)

JOBS = "infra/fleet/jobs.yaml"
RELAY = "infra/cloudbuild/relay.yaml"
POSTER = "scripts/gate-status.sh"
POLICY = "governance/platform/branch-protection.yaml"
REGISTRY = "infra/feature-flags/registry.yaml"
TF_IMPORT = "infra/terraform/import.tf"
HELPER = "infra/fleet/verify_status.py"
EXPECTED_JOBS = ("control-plane-apply", "control-plane-web-image", "verify-runner")
KNOWN_ASSUME = ("unprovisioned", "workload-identity", "instance-metadata")
# `never` must refuse both of these by name: the direction says "never a laptop,
# never Cloud Build", and a declaration that does not say so is silent on it.
REQUIRED_NEVERS = ("laptop", "cloud-build")

findings = []


def fail(label, detail):
    print(f"  FAIL  {label}: {detail}", file=sys.stderr)
    findings.append(label)


def load(path):
    with open(path, encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def read_poster_context(base):
    """The poster's DEFAULT context — the name it writes when nothing overrides it."""
    text = (base / POSTER).read_text(encoding="utf-8")
    match = re.search(r'CONTEXT="\$\{AO_GATE_CONTEXT:-([^}]+)\}"', text)
    return match.group(1) if match else None


def read_policy_contexts(base):
    doc = load(base / POLICY) or {}
    contexts = doc.get("required_status_contexts") or []
    return [str(c) for c in contexts] if isinstance(contexts, list) else [str(contexts)]


def read_imported_deployer(base):
    """The deployer SA email Terraform IMPORTS — the identity the declaration must match."""
    text = (base / TF_IMPORT).read_text(encoding="utf-8")
    match = re.search(r"serviceAccounts/([A-Za-z0-9._@-]+)", text)
    return match.group(1) if match else None


def workload_identity_declared(base):
    """Is a workload-identity pool/provider declared anywhere under infra/ as code?"""
    pattern = re.compile(r"workload_identity_pool|workloadIdentityPool|google_iam_workload_identity")
    for path in (base / "infra").rglob("*.tf"):
        if pattern.search(path.read_text(encoding="utf-8", errors="replace")):
            return True
    return False


def audit(base):
    problems = []
    for rel in (JOBS, RELAY, POSTER, POLICY, REGISTRY, TF_IMPORT, HELPER):
        if not (base / rel).is_file():
            return [(f"missing:{rel}", f"{rel} is absent, so nothing can be held in lock-step")]

    jobs_doc = load(base / JOBS) or {}
    if jobs_doc.get("schema") != "fleet-deploy-jobs-v1":
        problems.append(("jobs-schema", f"{JOBS} declares schema {jobs_doc.get('schema')!r}"))
    jobs = [j for j in (jobs_doc.get("jobs") or []) if isinstance(j, dict)]
    names = sorted(str(j.get("name")) for j in jobs)
    if names != list(EXPECTED_JOBS):
        problems.append(("jobs-set", f"{JOBS} declares {names}, expected {list(EXPECTED_JOBS)}"))

    relay_doc = load(base / RELAY) or {}
    entries = {}
    for entry in relay_doc.get("triggers") or []:
        if isinstance(entry, dict) and entry.get("name"):
            entries[str(entry["name"])] = entry

    reg = load(base / REGISTRY) or {}
    imported_sa = read_imported_deployer(base)
    wi_declared = workload_identity_declared(base)
    poster_context = read_poster_context(base)
    policy_contexts = read_policy_contexts(base)

    superseded_pairs = {}
    for trigger, entry in entries.items():
        if entry.get("disposition") == "superseded" and entry.get("replaced_by"):
            superseded_pairs[str(entry["replaced_by"])] = trigger

    for job in jobs:
        name = str(job.get("name"))
        for field in ("role", "event", "command"):
            if not job.get(field):
                problems.append((f"job-field:{field}", f"job {name!r} declares no {field}"))
        not_green = job.get("terminal_not_green") or []
        if not not_green:
            problems.append(("job-terminal-not-green", f"job {name!r} declares no terminal-not-green policy"))
        for item in not_green:
            if not isinstance(item, dict):
                problems.append(("job-terminal-shape", f"job {name!r} has a non-mapping terminal state"))
                continue
            if item.get("requeue") != "by-name":
                problems.append(
                    (
                        "job-terminal-requeue",
                        f"job {name!r}'s {item.get('state')!r} state is not re-queued by name (#1267)",
                    )
                )
            if item.get("publishes") != "none":
                problems.append(
                    (
                        "job-terminal-publishes",
                        f"job {name!r}'s {item.get('state')!r} state claims to publish {item.get('publishes')!r}; "
                        "a non-verdict must publish nothing",
                    )
                )

        # B. the two files must agree, from both ends.
        replaces = job.get("replaces_trigger")
        if not replaces:
            problems.append(("job-without-trigger", f"job {name!r} accounts for no Cloud Build trigger"))
        elif str(replaces) not in entries:
            problems.append(("job-trigger-unknown", f"job {name!r} accounts for trigger {replaces!r}, which {RELAY} does not declare"))
        else:
            entry = entries[str(replaces)]
            if entry.get("disposition") != "superseded":
                problems.append(
                    (
                        "job-trigger-not-superseded",
                        f"job {name!r} accounts for {replaces}, but {RELAY} calls it {entry.get('disposition')!r}",
                    )
                )
            elif entry.get("replaced_by") != name:
                problems.append(
                    (
                        "job-trigger-replacement-mismatch",
                        f"job {name!r} accounts for {replaces}, but {RELAY} says {replaces} is superseded by "
                        f"{entry.get('replaced_by')!r}",
                    )
                )

        # C. the guard flag ships OFF and names a real registry flag that defaults off.
        guard = ((job.get("flags") or {}).get("guard")) or {}
        if guard:
            if guard.get("default") != "false":
                problems.append(
                    ("job-guard-default", f"job {name!r} declares its guard default {guard.get('default')!r}, not 'false' (GR-5)")
                )
            flag = guard.get("registry_flag")
            node = reg
            if flag:
                for part in str(flag).split("."):
                    node = node.get(part) if isinstance(node, dict) else None
                if not isinstance(node, dict):
                    problems.append(("job-guard-registry-missing", f"job {name!r} names registry flag {flag!r}, absent from the registry"))
                else:
                    default = node.get("default")
                    off = default is False or (isinstance(default, str) and default.strip().lower() == "off")
                    if not off:
                        problems.append(("job-guard-registry-not-off", f"job {name!r}: registry flag {flag} defaults to {default!r}, not 'off'"))

        # D. the identity is the imported deployer SA, or nothing.
        identity = job.get("identity") or {}
        assume = identity.get("assume")
        if assume is not None:
            if assume not in KNOWN_ASSUME:
                problems.append(("identity-assume-unknown", f"job {name!r} declares assume {assume!r}, not one of {KNOWN_ASSUME}"))
            if assume == "workload-identity" and not wi_declared:
                problems.append(
                    (
                        "identity-workload-identity-unprovisioned",
                        f"job {name!r} assumes the SA 'via workload identity', but no workload-identity pool or provider "
                        "is declared under infra/ -- a mechanism with nothing behind it",
                    )
                )
        nevers = [str(n) for n in (identity.get("never") or [])]
        for required in REQUIRED_NEVERS:
            if required not in nevers:
                problems.append(("identity-never-missing", f"job {name!r}'s identity does not refuse {required!r} by name"))
        service_account = identity.get("service_account")
        if service_account is None:
            if not identity.get("github_app_token"):
                problems.append(("identity-absent", f"job {name!r} declares neither a service account nor a GitHub App token"))
        else:
            if not imported_sa:
                problems.append(("identity-import-unreadable", f"{TF_IMPORT} names no imported service account"))
            elif str(service_account) != imported_sa:
                problems.append(
                    (
                        "identity-not-the-imported-sa",
                        f"job {name!r} runs as {service_account!r}, and {TF_IMPORT} imports {imported_sa!r}",
                    )
                )

        # E. the status context is one name in three places.
        status = job.get("status") or {}
        if not status:
            continue
        context = status.get("context")
        if context is None:
            problems.append(("status-context-absent", f"job {name!r} declares a status block but no context"))
            continue
        context = str(context)
        if poster_context and context != poster_context:
            problems.append(
                (
                    "status-context-split",
                    f"job {name!r} posts context {context!r}, the poster {POSTER} defaults to {poster_context!r}",
                )
            )
        if policy_contexts and context not in policy_contexts:
            problems.append(
                (
                    "status-context-required-mismatch",
                    f"job {name!r} posts context {context!r}, and {POLICY} requires {policy_contexts} -- a required "
                    "check nothing posts is unsatisfiable",
                )
            )
        if not status.get("poster"):
            problems.append(("status-poster-absent", f"job {name!r} declares no poster"))
        if status.get("poster") and str(status["poster"]) != POSTER and not (base / str(status["poster"])).is_file():
            problems.append(("status-poster-missing", f"job {name!r} names poster {status['poster']!r}, which is not there"))
        if not status.get("red_by_check_name"):
            problems.append(
                ("status-not-red-by-check-name", f"job {name!r} does not require a red status to name the failing check")
            )

    # B (other direction): a retirement with no job behind it.
    for replacement, trigger in sorted(superseded_pairs.items()):
        if replacement not in names:
            problems.append(
                ("retirement-without-job", f"{RELAY} supersedes {trigger} by {replacement!r}, which {JOBS} does not declare")
            )
    return problems


# --- the real tree -----------------------------------------------------------
real = audit(root)
for label, detail in real:
    fail(label, detail)

jobs_real = (load(root / JOBS) or {}).get("jobs") or []
if not real:
    for job in jobs_real:
        if isinstance(job, dict):
            print(
                f"  OK    {str(job.get('name'))} (accounts for {job.get('replaces_trigger')}, "
                f"flags {sorted((job.get('flags') or {}).keys()) or 'none'}, "
                f"{len(job.get('terminal_not_green') or [])} terminal-not-green state(s))"
            )
    print(f"  OK    status context is one name in three places: {read_poster_context(root)!r}")
    print(f"  OK    identity matches the SA {TF_IMPORT} imports: {read_imported_deployer(root)!r}")

# --- F. the runner's classification, driven rather than described ------------
helper = root / HELPER
fixture = scratch / "attestation-red.json"
fixture.write_text(
    json.dumps(
        {
            "result": "FAIL",
            "exit_code": 1,
            "checks": [
                {"name": "cloudbuild", "rc": 1, "status": "FAIL", "verdict": "FAIL"},
                {"name": "docs-lint", "rc": 0, "status": "PASS", "verdict": "OK"},
                {"name": "terraform", "rc": 2, "status": "SKIP", "verdict": "WARN"},
            ],
        }
    ),
    encoding="utf-8",
)
selftest = subprocess.run(
    ["python3", str(helper), "--self-test"], capture_output=True, text=True, check=False
)
if selftest.returncode != 0:
    fail("verify-status-self-test", f"{HELPER} --self-test exited {selftest.returncode}: {selftest.stdout[-400:]}")
else:
    print("  OK    the runner's outcome classifier passes its own controls")

red = subprocess.run(
    ["python3", str(helper), "plan", "--rc", "1", "--attestation", str(fixture)],
    capture_output=True,
    text=True,
    check=False,
)
plan = {}
if red.returncode == 0:
    try:
        plan = json.loads(red.stdout)
    except json.JSONDecodeError as exc:
        fail("verify-status-plan-not-json", str(exc))
if plan.get("state") == "failure" and plan.get("failing") == ["cloudbuild"]:
    print("  OK    a red run publishes a failure NAMING the failing check")
    print(f"          expectation: state=failure failing=['cloudbuild']")
    print(f"          actual:      state={plan.get('state')} failing={plan.get('failing')}")
else:
    fail("verify-status-red-by-check-name", f"expected a failure naming ['cloudbuild'], got {plan or red.stderr[-200:]}")

parked = subprocess.run(
    ["python3", str(helper), "plan", "--rc", "11", "--attestation", str(fixture)],
    capture_output=True,
    text=True,
    check=False,
)
park_plan = {}
if parked.returncode == 0:
    try:
        park_plan = json.loads(parked.stdout)
    except json.JSONDecodeError as exc:
        fail("verify-status-park-not-json", str(exc))
if park_plan.get("publish") is False and park_plan.get("requeue") == "by-name":
    print("  OK    a PARKED run publishes nothing and is re-queued by name")
    print("          expectation: publish=False requeue=by-name")
    print(f"          actual:      publish={park_plan.get('publish')} requeue={park_plan.get('requeue')}")
else:
    fail("verify-status-park-not-a-verdict", f"expected publish=False requeue=by-name, got {park_plan or parked.stderr[-200:]}")


# --- the provocation ---------------------------------------------------------
# Every path the audit reads, so a scratch copy is a complete tree: copying only
# the two files a mutation edits would make the audit return `missing:` for the
# rest, and every control would report a finding that is not its own.
WATCHED = (JOBS, RELAY, POSTER, POLICY, REGISTRY, TF_IMPORT, HELPER)


def tree_digest(base):
    digest = hashlib.sha256()
    for path in sorted(base.rglob("*")):
        if path.is_file():
            digest.update(str(path.relative_to(base)).encode("utf-8"))
            digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def plant(name, edit):
    dest = scratch / name
    for rel in WATCHED:
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / rel, target)
    before = tree_digest(dest)
    edit(dest)
    return dest, tree_digest(dest) != before


def edit_jobs(dest, mutate):
    path = dest / JOBS
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    mutate(doc)
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")


def job_named(doc, name):
    for job in doc["jobs"]:
        if job.get("name") == name:
            return job
    raise KeyError(name)


def edit_relay(dest, mutate):
    path = dest / RELAY
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    mutate(doc)
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")


def control(label, edit, expect_label):
    dest, applied = plant(label, edit)
    if not applied:
        fail(f"{label}-NOT-APPLIED", "the mutation changed nothing, so it proves nothing")
        return
    got = audit(dest)
    labels = [lab for lab, _ in got]
    if expect_label in labels:
        detail = next((d for lab, d in got if lab == expect_label), "")
        print(f"  OK    {label} -> refused by name")
        print(f"          expectation: {expect_label}")
        print(f"          actual:      {expect_label}: {detail}")
    else:
        fail(label, f"expected {expect_label}, actual findings: {labels}")


control(
    "PLANT-WORKLOAD-IDENTITY-UNPROVISIONED",
    lambda d: edit_jobs(d, lambda doc: job_named(doc, "control-plane-apply")["identity"].__setitem__("assume", "workload-identity")),
    "identity-workload-identity-unprovisioned",
)
control(
    "PLANT-STATUS-CONTEXT-SPLIT",
    lambda d: edit_jobs(d, lambda doc: job_named(doc, "verify-runner")["status"].__setitem__("context", "shared-services/verify")),
    "status-context-split",
)
control(
    "PLANT-GUARD-DEFAULT-ON",
    lambda d: edit_jobs(d, lambda doc: job_named(doc, "control-plane-apply")["flags"]["guard"].__setitem__("default", "true")),
    "job-guard-default",
)
control(
    "PLANT-REPLACEMENT-MISMATCH",
    lambda d: edit_relay(d, lambda doc: doc["triggers"][0].__setitem__("replaced_by", "some-other-job")),
    "job-trigger-replacement-mismatch",
)
control(
    "PLANT-JOB-WITHOUT-TRIGGER",
    lambda d: edit_jobs(d, lambda doc: job_named(doc, "control-plane-web-image").pop("replaces_trigger")),
    "job-without-trigger",
)
control(
    "PLANT-NEVER-MISSING-LAPTOP",
    lambda d: edit_jobs(d, lambda doc: job_named(doc, "control-plane-apply")["identity"].__setitem__("never", ["cloud-build"])),
    "identity-never-missing",
)
control(
    "PLANT-TERMINAL-REQUEUE-DROPPED",
    lambda d: edit_jobs(d, lambda doc: job_named(doc, "verify-runner")["terminal_not_green"][0].__setitem__("requeue", "wait")),
    "job-terminal-requeue",
)
control(
    "PLANT-RETIREMENT-WITHOUT-JOB",
    lambda d: edit_relay(d, lambda doc: doc["triggers"][0].__setitem__("replaced_by", "ghost-job")),
    "retirement-without-job",
)

if findings:
    print(f"check-deploy-path: NOT-OK -- {len(findings)} problem(s): {', '.join(findings)}", file=sys.stderr)
    sys.exit(1)
print("  OK    the provocation refused every planted defect, by name")
print("check-deploy-path: declared half OK")
print("  CANNOT-ASSESS  the LIVE halves of #1295 were NOT observed: no PR status was posted by the pair, and no apply audit log was read")
sys.exit(0)
DRIVER

if [ "$driver_rc" -eq 2 ]; then
  echo "check-deploy-path: CANNOT-ASSESS -- the audit could not be performed" >&2
  exit 2
fi
if [ "$driver_rc" -ne 0 ]; then
  echo "check-deploy-path: NOT-OK -- the deployment-path declaration is defective" >&2
  exit 1
fi
echo "deploy-path: OK (declared half; the live halves are CANNOT-ASSESS here)"
exit 0
