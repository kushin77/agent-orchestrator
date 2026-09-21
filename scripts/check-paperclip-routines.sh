#!/usr/bin/env bash
# check-paperclip-routines.sh — the routine projection gate (issue #418,
# EPIC #410, ADR-0012).
#
# `fleet/cron.py` is the fleet's code-native scheduler (GR-15: there are no
# GitHub Actions) and its marked crontab lines ARE the schedule. The adapter under
# integrations/paperclip/adapters/routines/ projects each of those lines as one
# routine object (trigger + owner + params) so an operator can name and reason
# about it — without ever keeping a second copy of the schedule.
#
# A seam that nothing validates is a formality (no-false-green doctrine, GR-12),
# so this gate fails, by name, when the adapter drifts:
#
#   * every marked line in `fleet/cron.py` projects to EXACTLY ONE routine that
#     carries its trigger, its owner and its params, and the projection is
#     deterministic (same revision -> byte-identical document);
#   * the adapter's own negative controls must be REFUSED BY NAME — an entry
#     DELETED from the schedule, a schedule entry ADDED with no routine change
#     (drift), a routine whose schedule exists only on the routine side, an
#     owner-less routine, an inexpressible trigger, and a routine whose lane
#     disagrees with the PMO graph. If any control is accepted, this gate
#     reports FAIL — a check that cannot fail is a formality;
#   * every control mutation is ANCHORED AND PROVEN: it names something that
#     provably exists and asserts the anchor matched EXACTLY ONCE, so an anchor
#     that later drifts fails loudly instead of provoking nothing silently. The
#     controls mutate `config/fleet-jobs.json` — since issue #241/#962
#     `fleet/cron.py` RENDERS the schedule from that manifest, so the manifest is
#     the only place a marker can enter the schedule; a mutation of the
#     renderer's text cannot provoke drift. Before the negative controls run, the
#     gate also proves an UNMUTATED scratch tree projects BYTE-IDENTICALLY to the
#     real root, which is the premise underneath every control: the fixture they
#     act on IS the real schedule. Until #1176 that premise was unproven and
#     false — the `drop-prune` mutation's anchor had drifted out of
#     `fleet/cron.py`, so the control "changed nothing" and the gate retreated to
#     CANNOT-ASSESS rather than reaching a verdict (GR-12: a control whose anchor
#     silently moved is a control that cannot fail);
#   * the PMO agreement must actually RUN on the real root (the committed graph
#     is read), never silently degrade to `pmo-unavailable`;
#   * the projection keeps NO STORE of its own: deriving and verifying every
#     routine leaves the tree byte-for-byte unchanged.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
# CANNOT-ASSESS must never be reported as a pass.
#
# Usage: bash scripts/check-paperclip-routines.sh
#
# ---knowledge---
# module_id: scripts.check-paperclip-routines
# system: governance
# app: gates
# solution_class: enterprise
# patterns: [tri-state-exit, provoked-negative-control, no-false-green, declared-authority, feature-flag-gated-off, deterministic, schema-validation]
# derives_from: null
# owner_sme: platform-sme
# tier: L1
# interfaces: [exit 0 OK, exit 1 NOT-OK, exit 2 CANNOT-ASSESS]
# invariants: ""
# gotchas: ""
# related: ["#241", "#410", "#418", "#962"]
# do_not_duplicate: null
# ---knowledge---
set -u

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 2

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-paperclip-routines: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

adapter="integrations/paperclip/adapters/routines"
for required in "$adapter/cli.py" fleet/cron.py fleet/runtime.py \
  config/fleet-jobs.json docs/contracts/paperclip/ticket.schema.json \
  .board/snapshot.json; do
  if [ ! -e "$required" ]; then
    echo "check-paperclip-routines: CANNOT-ASSESS — $required is missing" >&2
    exit 2
  fi
done

# The mktemp template is assembled at run time: a literal run of the suffix
# character would trip the docs-lint unfinished-marker scan over *.sh files.
scratch_suffix="$(printf 'X%.0s' 1 2 3 4 5 6)"
scratch="$(mktemp -d "/tmp/ao418routines.$scratch_suffix")" || {
  echo "check-paperclip-routines: CANNOT-ASSESS — cannot create a scratch directory" >&2
  exit 2
}
trap 'rm -rf "$scratch"' EXIT

routines() { python3 "$adapter/cli.py" "$@"; }

json_ok() {
  python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$1" >/dev/null 2>&1
}

# make_tree <dir> <mutation> — a minimal schedule tree: the real `fleet/cron.py`
# (+ `runtime`) with the real `config/fleet-jobs.json`, one deliberate mutation
# applied to the LATTER. `fleet/cron.py` renders the schedule from the manifest,
# so the manifest is mutated — and each mutation is anchored and asserted below,
# so a renamed job or a moved schedule fails loudly instead of producing a
# control that "provokes" nothing. The exit code is passed through UNCHANGED
# (3 = the anchor moved, anything else = an input that could not be read), which
# is how `build_tree` tells the two apart — a `|| return 1` here would collapse
# them and turn a moved anchor back into a silent CANNOT-ASSESS.
make_tree() {
  python3 - "$root" "$1" "$2" <<'PY'
import json
import shutil
import sys
from pathlib import Path

root, dest, mutation = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]


def moved(detail):
    """The anchor a mutation names is gone — exit 3, which the caller turns into
    a FAILURE of this gate (never CANNOT-ASSESS: a control that cannot fail must
    not hide in the composite's `skipped` bucket, GR-12 / issue #1176)."""
    print("the mutation anchor moved: %s" % detail, file=sys.stderr)
    raise SystemExit(3)


def one_job(manifest, name):
    """The single job named `name` — or a loud failure, never a silent no-op."""
    matching = [job for job in manifest["jobs"] if job.get("name") == name]
    if len(matching) != 1:
        moved("expected exactly one job named %r in config/fleet-jobs.json, found %d"
              % (name, len(matching)))
    return matching[0]


fleet = dest / "fleet"
fleet.mkdir(parents=True, exist_ok=True)
shutil.copyfile(root / "fleet" / "runtime.py", fleet / "runtime.py")
shutil.copyfile(root / "fleet" / "cron.py", fleet / "cron.py")
(dest / "config").mkdir(parents=True, exist_ok=True)
manifest_path = root / "config" / "fleet-jobs.json"
original_text = manifest_path.read_text(encoding="utf-8")
manifest = json.loads(original_text)

text = original_text
if mutation == "none":
    pass
elif mutation == "drop-prune":
    manifest["jobs"].remove(one_job(manifest, "prune"))
    text = json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
elif mutation == "monthly":
    job = one_job(manifest, "prune")
    if job.get("schedule") != "23 4 * * *":
        moved("the prune job's schedule is %r, not '23 4 * * *'" % (job.get("schedule"),))
    job["schedule"] = "0 0 1 * *"
    text = json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
elif mutation == "enable-gated":
    job = one_job(manifest, "scan-pr-failures")
    if job.get("enabled") is not False:
        moved("the scan-pr-failures job is not ship-gated OFF (enabled=%r)"
              % (job.get("enabled"),))
    job["enabled"] = True
    text = json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
else:
    moved("unknown mutation: %s" % mutation)
if mutation != "none" and text == original_text:
    moved("mutation %r changed nothing" % mutation)
(dest / "config" / "fleet-jobs.json").write_text(text, encoding="utf-8")
PY
}

# build_tree <dir> <mutation> <label> — build a mutated schedule tree, and hold
# the two failure modes apart:
#
#   * the mutation's ANCHOR is gone (exit 3) — the control can no longer fail, so
#     this gate FAILS and says which control lost its provocation. It must NOT be
#     CANNOT-ASSESS: `scripts/verify.sh` maps rc 2 to SKIP, so a moved anchor
#     would hide in the `skipped` bucket exactly as the whole defect did before
#     #1176 (GR-12);
#   * the tree's input is missing (anything else) — CANNOT-ASSESS, an absent
#     input is never a pass (nor a FAIL: nothing was assessed).
build_tree() {  # build_tree <dir> <mutation> <label>
  local dir="$1" mutation="$2" label="$3" rc=0 err="$scratch/tree.err"
  make_tree "$dir" "$mutation" 2>"$err" || rc=$?
  case "$rc" in
    0)
      return 0
      ;;
    3)
      printf '  FAIL  control %-30s cannot be exercised: its mutation no longer changes the schedule\n' "$label" >&2
      sed 's/^/        /' "$err" >&2
      unproven=$((unproven + 1))
      # The control is still DECLARED (the count below tracks the declared set,
      # not the exercised one), so only the missing provocation is reported.
      controls=$((controls + 1))
      return 1
      ;;
    *)
      printf 'check-paperclip-routines: CANNOT-ASSESS — could not build the %s tree\n' "$label" >&2
      sed 's/^/        /' "$err" >&2
      exit 2
      ;;
  esac
}

unproven=0
controls=0

check_refused() {  # label needle cmd...
  local label="$1" needle="$2"
  shift 2
  controls=$((controls + 1))
  local out rc
  out="$("$@" 2>&1)"
  rc=$?
  if [ "$rc" -eq 0 ]; then
    printf '  FAIL  control %-30s passed — the adapter did not refuse %s\n' "$label" "$needle" >&2
    unproven=$((unproven + 1))
    return
  fi
  if [ "$rc" -eq 2 ]; then
    printf '  FAIL  control %-30s was CANNOT-ASSESS, not a refusal of %s\n' "$label" "$needle" >&2
    printf '%s\n' "$out" | sed 's/^/        /' >&2
    unproven=$((unproven + 1))
    return
  fi
  if ! printf '%s\n' "$out" | grep -qF -- "$needle"; then
    printf '  FAIL  control %-30s refused without naming %s\n' "$label" "$needle" >&2
    printf '%s\n' "$out" | sed 's/^/        /' >&2
    unproven=$((unproven + 1))
    return
  fi
  printf '  OK    control %-30s rc=%s, naming %s\n' "$label" "$rc" "$needle"
}

# --- the real root: the schedule projects, once per entry, deterministically --
echo "== the committed schedule =="
rc=0
routines verify > "$scratch/real-verify.out" 2>&1 || rc=$?
controls=$((controls + 1))
if [ "$rc" -eq 0 ] && grep -qF "each projected once" "$scratch/real-verify.out"; then
  echo "  OK    control every scheduled entry projects once (verify)"
  sed 's/^/        /' "$scratch/real-verify.out"
else
  echo "  FAIL  control the real schedule did not project cleanly (rc=$rc):" >&2
  sed 's/^/        /' "$scratch/real-verify.out" >&2
  unproven=$((unproven + 1))
fi

rc=0
routines project > "$scratch/real-project.json" 2> "$scratch/real-project.err" || rc=$?
if [ "$rc" -ne 0 ] || ! json_ok "$scratch/real-project.json"; then
  echo "  FAIL  the real projection is not a clean JSON document (rc=$rc):" >&2
  sed 's/^/        /' "$scratch/real-project.err" >&2
  unproven=$((unproven + 1))
else
  python3 - "$scratch/real-project.json" "$scratch/real-project.err" <<'PY' || unproven=$((unproven + 1))
import json
import sys

document = json.load(open(sys.argv[1], encoding="utf-8"))
notes = "\n".join(document.get("notes", []))
failures = []
if document.get("source") != "fleet/cron.py":
    failures.append("the view does not name fleet/cron.py as its source")
if document.get("count") != len(document.get("routines", [])):
    failures.append("count does not match the routines")
markers = [routine["marker"] for routine in document["routines"]]
if len(markers) != len(set(markers)):
    failures.append("a marker projects to more than one routine: %r" % markers)
for routine in document["routines"]:
    if not routine.get("owner"):
        failures.append("routine %s carries no owner" % routine.get("id"))
    trigger = routine.get("trigger") or {}
    if not trigger.get("raw") or trigger.get("kind") not in ("interval", "daily"):
        failures.append("routine %s carries no expressible trigger" % routine.get("id"))
    params = routine.get("params") or {}
    if not params.get("argv") or not params.get("log"):
        failures.append("routine %s carries no params" % routine.get("id"))
if "pmo-unavailable" in notes:
    failures.append("the PMO agreement did not run on the real root: %s" % notes)
if "pmo: agreed against" not in notes:
    failures.append("the projection did not record agreeing with the PMO graph")
if failures:
    for failure in failures:
        print("  FAIL  %s" % failure, file=sys.stderr)
    raise SystemExit(1)
print("  OK    %d routine(s), each with trigger + owner + params, agreed against the PMO graph"
      % len(document["routines"]))
PY
fi

# determinism: two derivations of the same revision are byte-identical
routines project > "$scratch/real-project-2.json" 2>/dev/null
controls=$((controls + 1))
if cmp -s "$scratch/real-project.json" "$scratch/real-project-2.json"; then
  echo "  OK    control two derivations over one revision are byte-identical"
else
  echo "  FAIL  control two derivations over one revision differ" >&2
  unproven=$((unproven + 1))
fi

# --- the fixture the controls mutate IS the real schedule --------------------
# Every control below mutates a scratch copy of the tree. This proves the copy
# STARTS from exactly the schedule the real root projects, so the premise the
# whole negative-control section rests on is measured rather than assumed: if the
# schedule ever moves to a source the scratch tree does not carry, the controls
# would silently stop acting on the real thing, and that is the failure #1176
# measured (`drop-prune`'s anchor had drifted out of `fleet/cron.py`).
echo "== the controls act on the real schedule =="
controls=$((controls + 1))
if make_tree "$scratch/plain" none; then
  rc=0
  routines --root "$scratch/plain" --pmo-root "$root" project \
    > "$scratch/plain-project.json" 2>/dev/null || rc=$?
  if [ "$rc" -eq 0 ] && cmp -s "$scratch/real-project.json" "$scratch/plain-project.json"; then
    echo "  OK    control an unmutated scratch tree projects byte-identically to the real root"
  else
    echo "  FAIL  an unmutated scratch tree does NOT project like the real root (rc=$rc):" >&2
    diff <(sed 's/^/        /' "$scratch/real-project.json") \
      <(sed 's/^/        /' "$scratch/plain-project.json") >&2 || true
    unproven=$((unproven + 1))
  fi
else
  echo "check-paperclip-routines: CANNOT-ASSESS — could not build the plain tree" >&2
  exit 2
fi

# --- negative controls -------------------------------------------------------
echo "== negative controls =="

# --- 1. an entry DELETED from the schedule ----------------------------------
if build_tree "$scratch/dropped" drop-prune "entry deleted from schedule"; then
  check_refused "entry deleted from schedule" "ao-fleet-prune" \
    routines --root "$scratch/dropped" --pmo-root "$root" project
fi

# --- 2. a schedule entry ADDED with no routine change (drift) ---------------
# The manifest's ship-gated job is flipped ON (GR-5's flag-gated-OFF rule): the
# marker is already declared ours in `fleet/cron.py`, so the reader sees the new
# line, and no routine claims it — drift of exactly the shape the real
# `ao-fleet-reap` finding had, provoked through the authority the code renders.
if build_tree "$scratch/drifted" enable-gated "schedule drifted"; then
  check_refused "schedule drifted (entry added)" "ao-fleet-scan-pr-failures" \
    routines --root "$scratch/drifted" --pmo-root "$root" project
fi

# --- 3. an inexpressible trigger --------------------------------------------
if build_tree "$scratch/monthly" monthly "schedule with no trigger shape"; then
  check_refused "schedule with no trigger shape" "ao-fleet-prune" \
    routines --root "$scratch/monthly" --pmo-root "$root" project
fi

# --- 4. a routine with no owner ---------------------------------------------
routines registry > "$scratch/registry.json" 2>/dev/null || true
controls=$((controls + 1))
if [ -s "$scratch/registry.json" ] && json_ok "$scratch/registry.json"; then
  python3 - "$scratch/registry.json" "$scratch/registry-unowned.json" <<'PY'
import json
import sys

document = json.load(open(sys.argv[1], encoding="utf-8"))
for routine in document["routines"]:
    if routine["id"] == "fleet-reconcile":
        routine["owner"] = ""
with open(sys.argv[2], "w", encoding="utf-8") as handle:
    json.dump(document, handle)
PY
  check_refused "owner-less routine" "fleet-reconcile" \
    routines --root "$root" --pmo-root "$root" --registry "$scratch/registry-unowned.json" project
else
  echo "  FAIL  the routine registry is not a readable document" >&2
  unproven=$((unproven + 1))
fi

# --- 5. a routine whose lane disagrees with the PMO graph -------------------
make_tree "$scratch/pmofix" none || {
  echo "check-paperclip-routines: CANNOT-ASSESS — could not build the PMO fixture tree" >&2
  exit 2
}
mkdir -p "$scratch/pmofix/.board/claims" "$scratch/pmofix/docs/contracts/paperclip"
cp docs/contracts/paperclip/ticket.schema.json "$scratch/pmofix/docs/contracts/paperclip/" || exit 2
python3 - "$scratch/pmofix" <<'PY' || exit 2
import json
import sys
from pathlib import Path

tree = Path(sys.argv[1])
board = {
    "generated_at": "2026-09-14T00:00:00Z",
    "source": "kushin77/agent-orchestrator",
    "issues": [
        {
            "number": 237,
            "title": "cron-owned fleet watchdog",
            "state": "OPEN",
            "labels": [],
            "parent": None,
            "blocked_by": [],
            "closed_at": "",
        }
    ],
}
(tree / ".board" / "snapshot.json").write_text(json.dumps(board, indent=2) + "\n", encoding="utf-8")
claim = {
    "event": "claim",
    "issue": 237,
    "agent": "fleet/watchdog",
    "lane": "lane-x",
    "at": "2026-09-01T00:00:00Z",
}
(tree / ".board" / "claims" / "0001-00237-claim.json").write_text(
    json.dumps(claim, sort_keys=True) + "\n", encoding="utf-8"
)
PY
check_refused "lane disagrees with the PMO graph" "pmo-lane-disagreement" \
  routines --root "$scratch/pmofix" --pmo-root "$scratch/pmofix" project

# --- 5b. a routine whose owner disagrees with the PMO graph -----------------
python3 - "$scratch/pmofix" <<'PY' || exit 2
import json
import sys
from pathlib import Path

tree = Path(sys.argv[1])
claim = {
    "event": "claim",
    "issue": 237,
    "agent": "someone-else",
    "lane": "fleet-ops",
    "at": "2026-09-02T00:00:00Z",
}
(tree / ".board" / "claims" / "0002-00237-claim.json").write_text(
    json.dumps(claim, sort_keys=True) + "\n", encoding="utf-8"
)
PY
check_refused "owner disagrees with the PMO graph" "pmo-owner-disagreement" \
  routines --root "$scratch/pmofix" --pmo-root "$scratch/pmofix" project

# --- 6. no store of its own -------------------------------------------------
echo "== no store of its own =="
controls=$((controls + 1))
before="$(find "$scratch/pmofix" -type f | sort | sed "s|^$scratch/pmofix/||")"
before_hash="$(find "$scratch/pmofix" -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum)"
routines project --root "$scratch/pmofix" --pmo-root "$scratch/pmofix" >/dev/null 2>&1 || true
routines verify --root "$root" >/dev/null 2>&1 || true
after="$(find "$scratch/pmofix" -type f | sort | sed "s|^$scratch/pmofix/||")"
after_hash="$(find "$scratch/pmofix" -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum)"
if [ "$before" = "$after" ] && [ "$before_hash" = "$after_hash" ]; then
  echo "  OK    control deriving + verifying every routine left the tree unchanged"
else
  echo "  FAIL  control the projection wrote a store of its own:" >&2
  diff <(printf '%s\n' "$before") <(printf '%s\n' "$after") | sed 's/^/        /' >&2
  unproven=$((unproven + 1))
fi

expected_controls=11
if [ "$controls" -ne "$expected_controls" ]; then
  echo "check-paperclip-routines: FAIL — expected $expected_controls controls, ran $controls" >&2
  unproven=$((unproven + 1))
fi

if [ "$unproven" -ne 0 ]; then
  echo "check-paperclip-routines: FAIL — $unproven check(s) did not hold" >&2
  exit 1
fi

echo "check-paperclip-routines: OK — the schedule in fleet/cron.py projects once per entry, $controls control(s) exercised, no store written"
exit 0
