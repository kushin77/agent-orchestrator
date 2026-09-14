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
#   * the PMO agreement must actually RUN on the real root (the committed graph
#     is read), never silently degrade to `pmo-unavailable`;
#   * the projection keeps NO STORE of its own: deriving and verifying every
#     routine leaves the tree byte-for-byte unchanged.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
# CANNOT-ASSESS must never be reported as a pass.
#
# Usage: bash scripts/check-paperclip-routines.sh
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-paperclip-routines: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

adapter="integrations/paperclip/adapters/routines"
for required in "$adapter/cli.py" fleet/cron.py fleet/runtime.py \
  docs/contracts/paperclip/ticket.schema.json .board/snapshot.json; do
  if [ ! -e "$required" ]; then
    echo "check-paperclip-routines: CANNOT-ASSESS — $required is missing" >&2
    exit 2
  fi
done

scratch="$(mktemp -d /tmp/ao418routines.XXXXXX)" || {
  echo "check-paperclip-routines: CANNOT-ASSESS — cannot create a scratch directory" >&2
  exit 2
}
trap 'rm -rf "$scratch"' EXIT

routines() { python3 "$adapter/cli.py" "$@"; }

json_ok() {
  python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$1" >/dev/null 2>&1
}

# make_tree <dir> <mutation> — a minimal schedule tree: the real `fleet/cron.py`
# (+ `runtime`) with one deliberate mutation applied. The mutation is asserted to
# have changed the text, so a renamed source string fails loudly instead of
# producing a control that "provokes" nothing.
make_tree() {
  python3 - "$root" "$1" "$2" <<'PY' || return 1
import shutil
import sys
from pathlib import Path

root, dest, mutation = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
fleet = dest / "fleet"
fleet.mkdir(parents=True, exist_ok=True)
shutil.copyfile(root / "fleet" / "runtime.py", fleet / "runtime.py")
original = (root / "fleet" / "cron.py").read_text(encoding="utf-8")
text = original
if mutation == "none":
    pass
elif mutation == "drop-prune":
    text = text.replace("        prune_line(),\n", "")
elif mutation == "monthly":
    text = text.replace('PRUNE_SCHEDULE = "23 4 * * *"', 'PRUNE_SCHEDULE = "0 0 1 * *"')
elif mutation == "add-extra":
    text = text.replace(
        "MARKERS = (MARKER, PRUNE_MARKER, RECONCILE_MARKER)",
        'MARKERS = (MARKER, PRUNE_MARKER, RECONCILE_MARKER, "ao-fleet-extra")',
    )
    text = text.replace(
        "        reconcile_line(interval),\n    ]",
        "        reconcile_line(interval),\n"
        '        "*/5 * * * * cd /tmp && /usr/bin/python3 fleet/extra.py run '
        '>> /tmp/extra.log 2>&1 # ao-fleet-extra",\n    ]',
    )
else:
    raise SystemExit("unknown mutation: %s" % mutation)
if mutation != "none" and text == original:
    raise SystemExit("mutation %r changed nothing — the source string moved" % mutation)
(fleet / "cron.py").write_text(text, encoding="utf-8")
PY
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

# --- negative controls -------------------------------------------------------
echo "== negative controls =="

# --- 1. an entry DELETED from the schedule ----------------------------------
make_tree "$scratch/dropped" drop-prune || {
  echo "check-paperclip-routines: CANNOT-ASSESS — could not build the dropped-entry tree" >&2
  exit 2
}
check_refused "entry deleted from schedule" "ao-fleet-prune" \
  routines --root "$scratch/dropped" --pmo-root "$root" project

# --- 2. a schedule entry ADDED with no routine change (drift) ---------------
make_tree "$scratch/drifted" add-extra || {
  echo "check-paperclip-routines: CANNOT-ASSESS — could not build the drifted tree" >&2
  exit 2
}
check_refused "schedule drifted (entry added)" "ao-fleet-extra" \
  routines --root "$scratch/drifted" --pmo-root "$root" project

# --- 3. an inexpressible trigger --------------------------------------------
make_tree "$scratch/monthly" monthly || {
  echo "check-paperclip-routines: CANNOT-ASSESS — could not build the monthly tree" >&2
  exit 2
}
check_refused "schedule with no trigger shape" "ao-fleet-prune" \
  routines --root "$scratch/monthly" --pmo-root "$root" project

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

expected_controls=10
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
