#!/usr/bin/env bash
# check-cloudbuild.sh — the cloudbuild gate for `make verify` (issue #6,
# extended by issue #1295).
#
# WHAT IT ASSERTS
#   1. every YAML under infra/cloudbuild parses;
#   2. EVERY `*-trigger.yaml` — not the two this gate used to know about — ships
#      `disabled: true`, and every substitution the disposition map names as its
#      guard is "false", mirroring the OFF default in
#      infra/feature-flags/registry.yaml (flag-gated, GR-5);
#   3. every trigger is ACCOUNTED FOR, and the account matches its yaml:
#      infra/cloudbuild/relay.yaml carries one entry per trigger, the entry's
#      `name` equals the yaml's own `name`, the file it names exists, and no
#      entry names a file that is not there — checked in BOTH directions, so the
#      list can neither miss a trigger nor invent one;
#   4. a `superseded` trigger names a fleet job that EXISTS in
#      infra/fleet/jobs.yaml, and a `relay-only` trigger names the context it
#      relays. Either claim with nothing behind it is what this refuses.
#
# WHY POINTS 3 AND 4 EXIST (#1295)
#   Measured: this gate asserted `verify-trigger.yaml` and `apply-trigger.yaml`
#   and never looked at `web-image-trigger.yaml`, `rollout-promote-trigger.yaml`
#   or `rollout-rollback-trigger.yaml`. A trigger nothing accounts for is a
#   trigger nothing can retire. Issue #1295's acceptance is exactly this gate
#   "reports every Cloud Build trigger disabled (or relay-only) and matching its
#   yaml", and #1263 (closed as superseded into #1295) left the surviving rule:
#   the declared configuration must match what is LIVE.
#
# THE LIVE HALF IS OPT-IN AND NEVER A PASS. `--live` adds a read-back of the
# project's real trigger list; with no `gcloud` and no credentials that is
# CANNOT-ASSESS (exit 2), never 0. It is NOT the default: a gate of record that
# turned itself into a permanent SKIP on a box without gcloud would be the
# inert-control defect of #724, not a control.
#
# PROVOCATION (GR-12 — a gate that cannot fail is a formality)
#   The audit is a pure function over three paths, so it runs against a scratch
#   copy carrying ONE mutation each time. Every mutation must produce its OWN
#   named finding, and each is asserted to have changed the file (sha256 before
#   and after), so a mutation that never applied cannot be reported as caught.
#
# Exit codes: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-cloudbuild.sh [--live]
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 1

live=0
case "${1:-}" in
  --live) live=1 ;;
  "") ;;
  *)
    echo "check-cloudbuild: CANNOT-ASSESS -- unknown argument: $1" >&2
    exit 2
    ;;
esac

command -v python3 >/dev/null 2>&1 || {
  echo "check-cloudbuild: CANNOT-ASSESS -- python3 not found" >&2
  exit 2
}

# The scratch path carries an explicit template and assembles its run of one
# letter, because the literal template token trips this repository's own
# unfinished-marker scan (SP-9, docs/SHELL-PATTERNS.md).
work="$(mktemp -d "/tmp/ao1295-cloudbuild.$(date +%s%N).$(printf 'X%.0s' 1 2 3 4 5 6)")" || {
  echo "check-cloudbuild: CANNOT-ASSESS -- no scratch directory available" >&2
  exit 2
}
cleanup() { [ -n "$work" ] && rm -rf "$work" || true; }
trap cleanup EXIT

echo "== cloudbuild =="

driver_rc=0
python3 - "$root" "$work" <<'DRIVER' || driver_rc=$?
"""Report every Cloud Build trigger's disabled-or-accounted state, and prove it can fail."""
import hashlib
import shutil
import sys
from pathlib import Path

import yaml

root = Path(sys.argv[1]).resolve()
scratch = Path(sys.argv[2]).resolve()
scratch.mkdir(parents=True, exist_ok=True)

SLOT_SUFFIX = "-trigger.yaml"
DISPOSITIONS = ("superseded", "relay-only", "retained")
RELATIVE = ("infra/cloudbuild", "infra/fleet/jobs.yaml", "infra/feature-flags/registry.yaml")

findings = []


def fail(label, detail):
    print(f"  FAIL  {label}: {detail}", file=sys.stderr)
    findings.append(label)


def load(path):
    with open(path, encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def audit(base):
    """Pure function over `base` (a tree carrying infra/...). Returns findings."""
    problems = []
    cb_dir = base / "infra" / "cloudbuild"
    fleet_jobs = base / "infra" / "fleet" / "jobs.yaml"
    registry = base / "infra" / "feature-flags" / "registry.yaml"

    relay_path = cb_dir / "relay.yaml"
    if not relay_path.is_file():
        return [("relay-missing", f"{relay_path.name} is absent, so no trigger is accounted for")]
    relay = load(relay_path) or {}
    if relay.get("schema") != "cloudbuild-disposition-v1":
        problems.append(("relay-schema", f"relay.yaml declares schema {relay.get('schema')!r}"))
    entries = relay.get("triggers")
    if not isinstance(entries, list) or not entries:
        return problems + [("relay-entries", "relay.yaml carries no triggers list")]

    job_names = set()
    if fleet_jobs.is_file():
        doc = load(fleet_jobs) or {}
        for job in doc.get("jobs") or []:
            if isinstance(job, dict) and job.get("name"):
                job_names.add(str(job["name"]))

    reg = load(registry) if registry.is_file() else {}

    # 1. every YAML parses (the original rule, kept).
    for path in sorted(cb_dir.glob("*.yaml")):
        try:
            load(path)
        except (yaml.YAMLError, OSError) as exc:
            problems.append((f"parse:{path.name}", str(exc).splitlines()[0]))

    # 3. every trigger is accounted for, and the account matches its yaml.
    by_file = {}
    for entry in entries:
        if not isinstance(entry, dict):
            problems.append(("relay-entry-shape", f"{entry!r} is not a mapping"))
            continue
        rel = entry.get("file")
        if not isinstance(rel, str):
            problems.append(("relay-entry-file", f"an entry names no file: {entry.get('name')!r}"))
            continue
        if rel in by_file:
            problems.append(("relay-duplicate-entry", f"{rel} is accounted for twice"))
        by_file[rel] = entry
        if not (base / rel).is_file():
            problems.append(
                ("relay-entry-file-missing", f"{rel} is accounted for but does not exist ({entry.get('name')!r})")
            )

    for path in sorted(cb_dir.glob(f"*{SLOT_SUFFIX}")):
        rel = str(path.relative_to(base))
        entry = by_file.get(rel)
        if entry is None:
            problems.append(
                ("trigger-unaccounted", f"{rel} has no entry in relay.yaml -- a trigger nothing accounts for")
            )
            continue
        doc = load(path) or {}
        if doc.get("name") != entry.get("name"):
            problems.append(
                (
                    "trigger-name-mismatch",
                    f"{rel} declares name {doc.get('name')!r}, relay.yaml says {entry.get('name')!r}",
                )
            )
        if doc.get("disabled") is not True:
            problems.append(("trigger-enabled", f"{rel} does not declare disabled: true (GR-5)"))
        filename = doc.get("filename")
        if not filename or not (base / filename).is_file():
            problems.append(
                ("trigger-build-config-missing", f"{rel} names build config {filename!r}, which is not there")
            )

        disposition = entry.get("disposition")
        if disposition not in DISPOSITIONS:
            problems.append(
                ("trigger-disposition", f"{rel} has disposition {disposition!r}, not one of {DISPOSITIONS}")
            )
        if disposition == "superseded":
            replaced_by = entry.get("replaced_by")
            if not replaced_by:
                problems.append(("superseded-without-replacement", f"{rel} is superseded but names no replacement"))
            elif replaced_by not in job_names:
                problems.append(
                    (
                        "superseded-replacement-unknown",
                        f"{rel} is superseded by {replaced_by!r}, which infra/fleet/jobs.yaml does not declare",
                    )
                )
        if disposition == "relay-only":
            relays = entry.get("relays") or {}
            if not relays.get("context"):
                problems.append(("relay-without-context", f"{rel} is relay-only but names no context to relay"))
        if disposition == "retained" and entry.get("replaced_by"):
            problems.append(("retained-with-replacement", f"{rel} is retained but also claims a replacement"))

        guard = entry.get("guard") or {}
        substitution = guard.get("substitution")
        if substitution is None:
            continue
        subs = doc.get("substitutions") or {}
        if substitution not in subs:
            problems.append(
                ("guard-flag-absent", f"{rel} names guard flag {substitution}, which its yaml does not carry")
            )
        elif subs[substitution] != "false":
            problems.append(
                ("guard-flag-on", f"{rel} declares {substitution}={subs[substitution]!r}, and it must be 'false' (GR-5)")
            )
        flag = guard.get("registry_flag")
        if flag:
            node = reg
            for part in str(flag).split("."):
                node = node.get(part) if isinstance(node, dict) else None
            if not isinstance(node, dict):
                problems.append(
                    ("guard-registry-flag-missing", f"{rel} names registry flag {flag!r}, absent from the registry")
                )
            else:
                # `default: off` arrives as the BOOLEAN False: YAML 1.1 reads
                # bare `off`/`on` as booleans, and PyYAML is a 1.1 parser. A
                # checker that compared it to the string "off" would report a
                # false drift on a correct registry -- measured, on this very
                # tree, before this normalisation was added.
                declared_default = node.get("default")
                is_off = declared_default is False or (
                    isinstance(declared_default, str) and declared_default.strip().lower() == "off"
                )
                if not is_off:
                    problems.append(
                        (
                            "guard-registry-flag-not-off",
                            f"{rel}: registry flag {flag} defaults to {declared_default!r}, not 'off'",
                        )
                    )
    # 3b. the other direction: an accounted file that is not a trigger.
    for rel in sorted(by_file):
        if not rel.endswith(SLOT_SUFFIX):
            problems.append(("relay-entry-not-a-trigger", f"relay.yaml accounts for {rel}, which is not a trigger yaml"))
    return problems


# --- the real tree -----------------------------------------------------------
real = audit(root)
for label, detail in real:
    fail(label, detail)

relay_real = load(root / "infra" / "cloudbuild" / "relay.yaml") or {}
accounted = {}
for candidate in relay_real.get("triggers") or []:
    if isinstance(candidate, dict) and isinstance(candidate.get("file"), str):
        accounted[candidate["file"]] = candidate
details = " ".join(detail for _, detail in real)
for path in sorted((root / "infra" / "cloudbuild").glob(f"*{SLOT_SUFFIX}")):
    rel = str(path.relative_to(root))
    if rel in details:
        continue
    entry = accounted.get(rel) or {}
    subs = (load(path) or {}).get("substitutions") or {}
    flag = (entry.get("guard") or {}).get("substitution")
    shown = f", {flag}={subs.get(flag)}" if flag else ", no flag gate"
    print(f"  OK    {rel} (disabled: true{shown}, {entry.get('disposition')})")


# --- the provocation ---------------------------------------------------------
def tree_digest(base):
    """sha256 over every file under `base` — a NEW file counts as a change.

    Hashing only the three declared paths would miss a mutation that ADDS a
    file (the unaccounted-trigger control does exactly that), and hashing a
    directory path hashes nothing at all. Measured: the first version of this
    helper reported every one of its own controls as NOT-APPLIED, which would
    have read as seven failures of a leaf that was correct.
    """
    digest = hashlib.sha256()
    for path in sorted(base.rglob("*")):
        if path.is_file():
            digest.update(str(path.relative_to(base)).encode("utf-8"))
            digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def plant(base, name, edit):
    """Copy the audited subtree into `base`, apply `edit`, and check it applied."""
    dest = base / name
    for rel in RELATIVE:
        src = root / rel
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, target)
        else:
            shutil.copy2(src, target)
    before = tree_digest(dest)
    edit(dest)
    return dest, tree_digest(dest) != before


def edit_yaml(path, mutate):
    doc = load(path)
    mutate(doc)
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")


def control(label, base, edit, expect_label):
    dest, applied = plant(base, label, edit)
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


cb = "infra/cloudbuild"
control(
    "PLANT-ENABLED-TRIGGER",
    scratch,
    lambda d: edit_yaml(d / cb / "rollout-promote-trigger.yaml", lambda doc: doc.__setitem__("disabled", False)),
    "trigger-enabled",
)
control(
    "PLANT-UNACCOUNTED-TRIGGER",
    scratch,
    lambda d: (d / cb / "nine-trigger.yaml").write_text(
        "name: nine\nfilename: infra/cloudbuild/verify.yaml\ndisabled: true\n", encoding="utf-8"
    ),
    "trigger-unaccounted",
)
control(
    "PLANT-NAME-MISMATCH",
    scratch,
    lambda d: edit_yaml(
        d / cb / "relay.yaml", lambda doc: doc["triggers"][0].__setitem__("name", "control-plane-something-else")
    ),
    "trigger-name-mismatch",
)
control(
    "PLANT-PHANTOM-ACCOUNT",
    scratch,
    lambda d: edit_yaml(
        d / cb / "relay.yaml",
        lambda doc: doc["triggers"].append(
            {"name": "phantom", "file": "infra/cloudbuild/phantom-trigger.yaml", "disposition": "retained"}
        ),
    ),
    "relay-entry-file-missing",
)
control(
    "PLANT-GUARD-FLAG-ON",
    scratch,
    lambda d: edit_yaml(
        d / cb / "apply-trigger.yaml", lambda doc: doc["substitutions"].__setitem__("_ENABLE_APPLY", "true")
    ),
    "guard-flag-on",
)
control(
    "PLANT-UNKNOWN-REPLACEMENT",
    scratch,
    lambda d: edit_yaml(
        d / cb / "relay.yaml", lambda doc: doc["triggers"][0].__setitem__("replaced_by", "no-such-fleet-job")
    ),
    "superseded-replacement-unknown",
)
control(
    "PLANT-REGISTRY-FLAG-ABSENT",
    scratch,
    lambda d: edit_yaml(
        d / cb / "relay.yaml",
        lambda doc: doc["triggers"][0]["guard"].__setitem__("registry_flag", "ci_cd.no_such_flag"),
    ),
    "guard-registry-flag-missing",
)

if findings:
    print(f"check-cloudbuild: NOT-OK -- {len(findings)} problem(s): {', '.join(findings)}", file=sys.stderr)
    sys.exit(1)
print(f"  OK    {len(list((root / 'infra' / 'cloudbuild').glob('*' + SLOT_SUFFIX)))} trigger(s) disabled, accounted for, and matching relay.yaml")
print("  OK    the provocation refused every planted defect, by name")
print("check-cloudbuild: declared half OK")
sys.exit(0)
DRIVER

if [ "$driver_rc" -eq 2 ]; then
  echo "check-cloudbuild: CANNOT-ASSESS -- the audit could not be performed" >&2
  exit 2
fi
if [ "$driver_rc" -ne 0 ]; then
  echo "check-cloudbuild: NOT-OK -- the declared Cloud Build surface is defective" >&2
  exit 1
fi

# --- the LIVE half (#1263's surviving rule) ---------------------------------
# Only on request, and an unobservable read-back is CANNOT-ASSESS -- never a
# pass. Reporting this box's lack of credentials as "Cloud Build matches its
# declaration" would be the #739 defect class (an unreadable state read back as
# healthy).
if [ "$live" -eq 1 ]; then
  echo "== cloudbuild live parity =="
  if ! command -v gcloud >/dev/null 2>&1; then
    echo "  CANNOT-ASSESS  gcloud is absent, so the LIVE trigger list was NOT read -- never a pass"
    echo "check-cloudbuild: CANNOT-ASSESS -- the declared half is sound, the live half was NOT observed"
    exit 2
  fi
  live_json="$work/live-triggers.json"
  if ! gcloud builds triggers list --project purebliss-ghl --format=json >"$live_json" 2>"$work/live-err.txt"; then
    echo "  CANNOT-ASSESS  the live trigger list could not be read ($(head -c 200 "$work/live-err.txt"))"
    echo "check-cloudbuild: CANNOT-ASSESS -- the declared half is sound, the live half was NOT observed"
    exit 2
  fi
  live_rc=0
  python3 - "$root" "$live_json" <<'LIVE' || live_rc=$?
"""Compare the declared trigger names against the live project's list."""
import json
import sys
from pathlib import Path

import yaml

root = Path(sys.argv[1]).resolve()
live = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
relay = yaml.safe_load((root / "infra" / "cloudbuild" / "relay.yaml").read_text(encoding="utf-8")) or {}
declared = {str(e["name"]) for e in (relay.get("triggers") or []) if isinstance(e, dict) and e.get("name")}

state = {}
for trigger in live:
    if isinstance(trigger, dict) and trigger.get("name"):
        state[str(trigger["name"])] = bool(trigger.get("disabled"))
live_names = set(state)

# A drift OF THIS REPO'S DECLARATION: a trigger we declare `disabled: true` that
# is ARMED live. That is the whole point of the read-back -- measured on this box
# 2026-09-18, three of the five were armed.
armed_ours = sorted(n for n in live_names & declared if not state[n])
# Live triggers this repository does not declare. Reported BY NAME, never
# silently: they belong to other surfaces (capital-*, erp-crm-*, gatekeeper-*,
# ...), and #1263's scope note names the capital-* set as being retired. They
# are not this repo's declaration to fail on, but an unlisted trigger is exactly
# the blindness this read-back exists to remove.
armed_foreign = sorted(n for n in live_names - declared if not state[n])
missing = sorted(declared - live_names)

for name in armed_ours:
    print(f"  FAIL  declared trigger {name} ships `disabled: true` and is ARMED live -- declared/live drift", file=sys.stderr)
for name in missing:
    print(f"  NOTE  declared trigger {name} does not exist in the project (not imported)", file=sys.stderr)
if armed_foreign:
    print(f"  NOTE  {len(armed_foreign)} armed live trigger(s) are NOT declared under infra/cloudbuild (other surfaces): {', '.join(armed_foreign)}")
for name in sorted(live_names - declared):
    print(f"        not declared here: {name} (armed={not state[name]})")

if armed_ours:
    sys.exit(1)
if not live:
    print("  CANNOT-ASSESS  the project answered with no triggers, which cannot be distinguished from an unreadable list")
    sys.exit(2)
print(f"  OK    no declared trigger is armed; {len(declared)} declared, {len(missing)} not imported, {len(live_names - declared)} live trigger(s) outside this declaration")
sys.exit(0)
LIVE
  if [ "$live_rc" -eq 2 ]; then
    echo "check-cloudbuild: CANNOT-ASSESS -- the live half was NOT observed"
    exit 2
  fi
  if [ "$live_rc" -ne 0 ]; then
    echo "check-cloudbuild: NOT-OK -- the live Cloud Build surface does not match its declaration" >&2
    exit 1
  fi
fi

echo "cloudbuild: OK"
exit 0
