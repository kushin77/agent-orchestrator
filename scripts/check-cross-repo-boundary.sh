#!/usr/bin/env bash
# check-cross-repo-boundary.sh — cross-repo execution-boundary gate (issue #388).
#
# The detector (governance/board/boundary.py) flags a backlog item filed on this
# board whose body declares a foreign repo — work that must be handed to another
# repo's board, never done from here (NG4). This gate wires it into `make verify`
# the honest way:
#
#   * it reads the committed .board/boundary-snapshot.json — a boundary-specific
#     export that carries each issue's body (the main .board/snapshot.json has no
#     body, which the detector reads as a false green by missing field);
#   * it applies the legacy quarantine baseline
#     (governance/board/boundary-baseline.json), honoured only while the issue
#     tracking each entry is open — a quarantine whose tracker closed is a stale
#     finding, and a NEW cross-repo child is never excused;
#   * a snapshot whose records lack `body` is CANNOT-ASSESS (exit 2), never OK;
#   * it runs a provoked negative control on mutated copies of the snapshot, so a
#     check that cannot fail cannot pass (GR-12).
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
# CANNOT-ASSESS must never be reported as a pass.
#
# Usage: bash scripts/check-cross-repo-boundary.sh
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

snapshot=".board/boundary-snapshot.json"
baseline="governance/board/boundary-baseline.json"
cli="governance/board/cli.py"

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-cross-repo-boundary: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi
if [ ! -f "$snapshot" ]; then
  echo "check-cross-repo-boundary: CANNOT-ASSESS — boundary snapshot not found: $snapshot (run python3 governance/board/cli.py export-boundary)" >&2
  exit 2
fi
if [ ! -f "$baseline" ]; then
  echo "check-cross-repo-boundary: FAIL — boundary baseline not found: $baseline" >&2
  exit 1
fi
if [ ! -f "$cli" ]; then
  echo "check-cross-repo-boundary: FAIL — $cli is missing" >&2
  exit 1
fi

run_check() {
  # $1 = snapshot path, $2 = baseline path, $3 = "1" to self-heal a stale
  # snapshot first (issue #1631); forwards the gate's own exit code.
  python3 - "$root" "$1" "$2" "${3:-}" <<'PY'
import sys
from pathlib import Path

root = Path(sys.argv[1])
sys.path.insert(0, str(root / "governance" / "board"))

from cli import run_boundary_check  # noqa: E402

raise SystemExit(run_boundary_check(sys.argv[2], sys.argv[3], refresh=(sys.argv[4] == "1")))
PY
}

# --- 1. the real check, self-healing a stale snapshot on a SCRATCH copy -----
# A stale committed snapshot self-heals via the ONE export verb before this
# fails CANNOT-ASSESS (issue #1631) — never a silent OK-with-zero-findings on
# rotted input. The self-heal runs against a scratch copy, never the tracked
# file itself: refreshing the committed .board/boundary-snapshot.json is a
# board-artifact refresh (export-boundary) and must not ride in this gate.
heal_scratch="$(mktemp -d)"
cp "$snapshot" "$heal_scratch/boundary-snapshot.json"
run_check "$heal_scratch/boundary-snapshot.json" "$baseline" 1
real_rc=$?
rm -rf "$heal_scratch"
if [ "$real_rc" -ne 0 ]; then
  echo "check-cross-repo-boundary: FAIL — the committed boundary snapshot is not clean (rc=$real_rc)" >&2
  exit 1
fi
echo "  OK    committed boundary snapshot is clean"

# --- 2. provoked negative control (mutation of a copy) -----------------------
work="/tmp/cross-repo-boundary.$(date +%s%N)"
if ! mkdir -p "$work" 2>/dev/null; then
  echo "check-cross-repo-boundary: CANNOT-ASSESS — cannot create a scratch directory" >&2
  exit 2
fi
trap 'rm -rf "$work"' EXIT

python3 - "$work" "$snapshot" "$baseline" <<'PY'
import datetime
import json
import pathlib
import sys

work = pathlib.Path(sys.argv[1])
snapshot = pathlib.Path(sys.argv[2])
# These controls provoke the BOUNDARY-LOGIC detector, not the freshness gate
# (issue #1631) — restamp generated_at to now so a real-world stale committed
# snapshot never masks what these three mutations are meant to prove.
_now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

# (a) a fresh, non-quarantined cross-repo child must be refused (rc 1).
fresh = json.loads(snapshot.read_text(encoding="utf-8"))
fresh["generated_at"] = _now
fresh_items = fresh.get("items", []) if isinstance(fresh, dict) else []
fresh_items.append(
    {
        "number": 9999,
        "title": "provoked foreign child",
        "state": "open",
        "labels": [],
        "milestone": "",
        "parent": None,
        "blocked_by": [],
        "closed_at": "",
        "body": "## Repo\nprovoked-foreign-repo\n",
    }
)
(work / "fresh-child.json").write_text(json.dumps(fresh, indent=2) + "\n", encoding="utf-8")

# (b) a snapshot with `body` stripped must be CANNOT-ASSESS, never OK (rc 2).
stripped = json.loads(snapshot.read_text(encoding="utf-8"))
stripped["generated_at"] = _now
stripped_items = stripped.get("items", []) if isinstance(stripped, dict) else []
for item in stripped_items:
    item.pop("body", None)
(work / "no-body.json").write_text(json.dumps(stripped, indent=2) + "\n", encoding="utf-8")

# (c) a quarantine whose tracker closed must be refused as stale (rc 1).
# The tracker to close is read from the live baseline, never hardcoded: a
# baseline that shrinks (issue #1722) must not silently defang this control
# by naming a tracker (e.g. #358) the baseline no longer references.
baseline_doc = json.loads(pathlib.Path(sys.argv[3]).read_text(encoding="utf-8"))
trackers = {
    int(e["tracked_by"].lstrip("#"))
    for e in baseline_doc.get("quarantine", []) or []
    if str(e.get("tracked_by", "")).lstrip("#").isdigit()
}
stale = json.loads(snapshot.read_text(encoding="utf-8"))
stale["generated_at"] = _now
stale_items = stale.get("items", []) if isinstance(stale, dict) else []
for item in stale_items:
    if item.get("number") in trackers:
        item["state"] = "closed"
(work / "stale-tracker.json").write_text(json.dumps(stale, indent=2) + "\n", encoding="utf-8")
PY

run_check "$work/fresh-child.json" "$baseline" >/dev/null 2>&1
if [ "$?" -ne 1 ]; then
  echo "check-cross-repo-boundary: FAIL — a non-quarantined cross-repo child did not fail the gate (expected rc 1)" >&2
  exit 1
fi
echo "  OK    injected non-quarantined child is refused (rc 1)"

run_check "$work/no-body.json" "$baseline" >/dev/null 2>&1
if [ "$?" -ne 2 ]; then
  echo "check-cross-repo-boundary: FAIL — a bodyless snapshot was not CANNOT-ASSESS (expected rc 2)" >&2
  exit 1
fi
echo "  OK    bodyless snapshot is CANNOT-ASSESS (rc 2, never OK)"

run_check "$work/stale-tracker.json" "$baseline" >/dev/null 2>&1
if [ "$?" -ne 1 ]; then
  echo "check-cross-repo-boundary: FAIL — a closed tracker did not make the quarantine stale (expected rc 1)" >&2
  exit 1
fi
echo "  OK    a closed tracker makes the quarantine stale (rc 1)"

exit 0
