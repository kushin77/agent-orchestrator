#!/usr/bin/env bash
# check-healthz-jobset.sh — the /healthz expected job set is DERIVED, and the
# check can FAIL (issue #1148, EPIC #706).
#
# THE DEFECT THIS EXISTS FOR
#   `infra/fleet/healthz.py` compared a decision document's job COUNT against a
#   literal (`EXPECTED_JOBS = 3`) while the schedule declared FOUR enabled jobs
#   (`snapshot-refresh` is ship-gated OFF, #241). So a document that was missing
#   the fourth rung — `ao-fleet-reap` — answered **200**, and the body said
#   `"jobs": 4, "expected_jobs": 3` in the same breath. A check whose pass and
#   fail paths cannot be told apart by the thing it claims to check is a
#   formality (GR-12), and the epic's own acceptance ("200 with all 3 jobs
#   present") carried the same stale constant.
#
# WHAT IS MEASURED (against the real tree and the real HTTP surface, not prose)
#   A. THE SET IS DERIVED, NOT WRITTEN DOWN. `healthz.py` declares no
#      `EXPECTED_JOBS` literal, reads the declaration through the schedule's
#      single owner (`fleet/cron.py`'s `load_manifest` + `enabled_jobs`), and the
#      set it derives agrees with `fleet/cron.MARKERS`, with the crontab
#      `fleet/cron.py render` emits, and with `inventory.yaml`'s
#      `schedule.lines`. The three porting documents name no stale count.
#   B. THE SURFACE CAN FAIL, BOTH HALVES. Over `serve()` + `GET /healthz`: a
#      complete document answers 200, and a document missing ANY ONE enabled
#      rung answers 503 **naming that marker** — every rung, one at a time, so a
#      future fifth rung cannot silently go unchecked.
#   C. THE SET IS READ, NOT REMEMBERED, AND IT FAILS CLOSED. In a scratch copy
#      of the tree: enabling a NEW job makes the surface demand the new marker;
#      an absent, malformed or all-disabled declaration answers 503
#      `cannot-assess` — never 200 on a count it cannot justify, and never a
#      silent 0.
#   D. THE MUTANTS. Two source mutations of the scratch copy, each of which must
#      RED the SAME control B uses: restoring the stale literal shape
#      (`markers[:3]`), and removing the missing-marker check (`missing = []`).
#      A mutation that did not change the file is reported as such (a no-op
#      mutation proves nothing), the mutant must still answer the HEALTHY case
#      200 (a tree that is simply broken proves nothing either), and the restore
#      is asserted byte-identically by sha256.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS. No network access.
#   CANNOT-ASSESS (2) is reserved for an input that cannot be read at all: no
#   python3, a required file missing, no scratch directory, or a schedule
#   declaration that cannot be derived in this checkout.
#
# Usage: bash scripts/check-healthz-jobset.sh
set -uo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 2

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-healthz-jobset: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

for required in infra/fleet/healthz.py infra/fleet/tests/test_healthz.py \
                fleet/cron.py fleet/runtime.py config/fleet-jobs.json \
                infra/fleet/inventory.yaml infra/fleet/README.md \
                docs/FLEET-CRON-PARITY.md; do
  if [ ! -f "$required" ]; then
    echo "check-healthz-jobset: FAIL — $required is missing" >&2
    exit 1
  fi
done

# The scratch tree name follows the sanctioned idiom (no trailing run of one
# capital letter: that literal trips this repo's own docs-lint marker scan, so
# the gate would fail for a reason that looks like nothing).
scratch="/tmp/ao-healthz-jobset.$.$(date +%s%N)"
mkdir "$scratch" 2>/dev/null || {
  echo "check-healthz-jobset: CANNOT-ASSESS — no scratch directory available" >&2
  exit 2
}
TMPD="$scratch"
cleanup() { [ -n "$TMPD" ] && rm -rf "$TMPD" || true; }
trap cleanup EXIT

fail=0
cannot=0
CONTROL_FAILS=0
CONTROL_QUIET=0
ok()   { printf '  OK    %s\n' "$1"; }
bad()  { printf '  FAIL  %s\n' "$1" >&2; fail=$((fail + 1)); }
note() { printf '  NOTE  %s\n' "$1"; }
unmeasured() { printf '  SKIP  %s\n' "$1"; cannot=$((cannot + 1)); }

# ---------------------------------------------------------------------------
# The driver: a thin helper, so the ASSERTIONS live in this file and the same
# ones are run against the real source and against every mutant.
# ---------------------------------------------------------------------------
cat > "$scratch/driver.py" <<'PY'
#!/usr/bin/env python3
"""Helpers for check-healthz-jobset.sh: derivation, document writer, HTTP status."""
from __future__ import annotations

import json
import sys
import threading
import time
import urllib.error
import urllib.request
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

sys.dont_write_bytecode = True


def load(path: str):
    spec = spec_from_file_location("gate_healthz", path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def cmd_derivation(argv: list[str]) -> int:
    markers = load(argv[0]).declared_enabled_markers()
    if not markers:
        print("NONE")
        return 3
    for marker in markers:
        print(marker)
    return 0


def cmd_has_derivation(argv: list[str]) -> int:
    """0 when the module exposes the derivation this gate holds, 3 when it does not."""
    probe = getattr(load(argv[0]), "declared_enabled_markers", None)
    return 0 if callable(probe) else 3


def cmd_write_doc(argv: list[str]) -> int:
    out = Path(argv[0])
    markers = [marker for marker in argv[1:] if marker]
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    out.write_text(
        json.dumps(
            {
                "verdict": "ok",
                "started_at": stamp,
                "finished_at": stamp,
                "jobs": [{"marker": marker, "rc": 0, "disposition": "dry-run"} for marker in markers],
                "state": {"attributable_changes": []},
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return 0


def cmd_status(argv: list[str]) -> int:
    """GET /healthz over a real socket and print STATUS= / BODY= on their own lines."""
    module = load(argv[0])
    ready = threading.Event()
    server = module.serve(0, argv[1], ready)
    ready.wait(5)
    port = server.server_address[1]
    try:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=5) as response:
                code = response.status
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            code = exc.code
            raw = exc.read().decode("utf-8")
    finally:
        server.shutdown()
        server.server_close()
    try:
        payload: dict = json.loads(raw)
    except ValueError:
        payload = {"unparsed": raw}
    print(f"STATUS={code}")
    print("BODY=" + json.dumps(payload, sort_keys=True))
    return 0


def main() -> int:
    if len(sys.argv) < 2:
        return 2
    command, argv = sys.argv[1], sys.argv[2:]
    if command == "derivation":
        return cmd_derivation(argv)
    if command == "has-derivation":
        return cmd_has_derivation(argv)
    if command == "write-doc":
        return cmd_write_doc(argv)
    if command == "status":
        return cmd_status(argv)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
PY
driver="$scratch/driver.py"

# The contract this gate holds: the surface DERIVES its expected set. A module
# that does not expose the derivation is a FAIL, not a SKIP — a gate whose subject
# can vanish into CANNOT-ASSESS is a gate that cannot fail, which is the same
# false-green class this gate exists to close (and it is what makes this gate RED
# against the pre-fix tree, where the removed literal IS the absence).
if ! python3 "$driver" has-derivation "infra/fleet/healthz.py" >/dev/null 2>&1; then
  printf 'check-healthz-jobset: FAIL — infra/fleet/healthz.py does not expose a callable declared_enabled_markers(), so the expected job set cannot be derived and this check cannot certify anything\n' >&2
  exit 1
fi

mapfile -t real_markers < <(python3 "$driver" derivation "infra/fleet/healthz.py" 2>/dev/null)
if [ "${#real_markers[@]}" -eq 0 ]; then
  printf 'check-healthz-jobset: FAIL — the schedule declaration derived no enabled marker in this checkout, so the surface owes nothing and certifies nothing\n' >&2
  exit 1
fi

# one_assert <label> <expected status> <needle|-> <healthz> <doc> <log>
# Reports into CONTROL_FAILS (never into the gate's own `fail`), so the caller
# decides whether the run it drove was the real artifact or a mutant.
one_assert() {
  local label=$1 want=$2 needle=$3 hz=$4 doc=$5 logf=$6
  local out code body
  out="$(python3 "$driver" status "$hz" "$doc" 2>&1)"
  code="$(printf '%s\n' "$out" | sed -n 's/^STATUS=//p')"
  body="$(printf '%s\n' "$out" | sed -n 's/^BODY=//p')"
  if [ -z "$code" ]; then
    printf 'SKIP  %s — could not be measured: %s\n' "$label" "$out" >> "$logf"
    [ "$CONTROL_QUIET" -eq 0 ] && printf '  SKIP  %s — could not be measured\n' "$label" >&2
    cannot=$((cannot + 1))
    return
  fi
  if [ "$code" != "$want" ]; then
    printf 'FAIL  %s — expected HTTP %s, got HTTP %s :: %s\n' "$label" "$want" "$code" "$body" >> "$logf"
    [ "$CONTROL_QUIET" -eq 0 ] && printf '  FAIL  %s — expected HTTP %s, got HTTP %s :: %s\n' "$label" "$want" "$code" "$body" >&2
    CONTROL_FAILS=$((CONTROL_FAILS + 1))
    return
  fi
  if [ "$needle" = "-" ]; then
    # No marker to name: the assertion is the status alone (the complete document).
    printf 'OK    %s (HTTP %s)\n' "$label" "$code" >> "$logf"
    [ "$CONTROL_QUIET" -eq 0 ] && printf '  OK    %s (HTTP %s)\n' "$label" "$code"
    return
  fi
  # `case` is used as the substring test (no fork); the needles here are job
  # markers, which never carry a glob metacharacter.
  case "$body" in
    *"$needle"*)
      printf 'OK    %s (HTTP %s, names %s)\n' "$label" "$code" "$needle" >> "$logf"
      [ "$CONTROL_QUIET" -eq 0 ] && printf '  OK    %s (HTTP %s, names %s)\n' "$label" "$code" "$needle"
      ;;
    *)
      printf 'FAIL  %s — HTTP %s did not name %s :: %s\n' "$label" "$code" "$needle" "$body" >> "$logf"
      [ "$CONTROL_QUIET" -eq 0 ] && printf '  FAIL  %s — HTTP %s did not name %s :: %s\n' "$label" "$code" "$needle" "$body" >&2
      CONTROL_FAILS=$((CONTROL_FAILS + 1))
      ;;
  esac
}

# control <healthz path> <log file> — the assertions the mutants must break.
# The complete document must answer 200, and EVERY enabled rung, removed in
# turn, must answer 503 naming that rung.
control() {
  local hz=$1 logf=$2 marker candidate doc
  local -a rest
  CONTROL_FAILS=0
  : > "$logf"
  doc="$scratch/complete.json"
  python3 "$driver" write-doc "$doc" "${real_markers[@]}" >/dev/null 2>&1
  one_assert "the complete document answers 200" 200 "-" "$hz" "$doc" "$logf"
  for marker in "${real_markers[@]}"; do
    rest=()
    for candidate in "${real_markers[@]}"; do
      if [ "$candidate" != "$marker" ]; then rest+=("$candidate"); fi
    done
    doc="$scratch/short-$marker.json"
    python3 "$driver" write-doc "$doc" "${rest[@]}" >/dev/null 2>&1
    one_assert "a document missing $marker answers 503 naming it" 503 "$marker" "$hz" "$doc" "$logf"
  done
}

# check_one <label> <expected status> <needle|-> <healthz> <doc> — quiet, one-off.
check_one() {
  local label=$1
  CONTROL_FAILS=0
  CONTROL_QUIET=1
  one_assert "$label" "$2" "$3" "$4" "$5" "$scratch/one.log"
  CONTROL_QUIET=0
  if [ "$CONTROL_FAILS" -gt 0 ]; then
    bad "$label"
  else
    ok "$label"
  fi
}

printf '\n== healthz-jobset: A. the expected set is derived, not written down ==\n'

# A1 — no literal expected count (the exact shape that went stale).
if grep -qE '^[[:space:]]*EXPECTED_JOBS[[:space:]]*=' infra/fleet/healthz.py; then
  bad "infra/fleet/healthz.py still declares an EXPECTED_JOBS literal — a second copy of a count that has already gone stale"
else
  ok "infra/fleet/healthz.py declares no EXPECTED_JOBS literal (no second copy of the count)"
fi

# A2 — it reads the declaration THROUGH the schedule's single owner.
healthz_source="$(cat infra/fleet/healthz.py)"
for needle in 'cron.load_manifest()' 'cron.enabled_jobs('; do
  case "$healthz_source" in
    *"$needle"*) ok "the expected set is read through the schedule's owner ($needle)" ;;
    *) bad "infra/fleet/healthz.py does not read the declaration through $needle, so it carries a second copy" ;;
  esac
done

# A3 — the derived set agrees with the installer's own markers and with the
# crontab render, and inventory.yaml's declared line count agrees with both.
marked=0
while IFS= read -r line; do
  case "$line" in
    *'# ao-fleet-'*) marked=$((marked + 1)) ;;
  esac
done < <(env -u AO_FLEET_DIR python3 fleet/cron.py render 2>/dev/null)
declared_lines="$(sed -n 's/^[[:space:]]*lines:[[:space:]]*\([0-9][0-9]*\).*/\1/p' infra/fleet/inventory.yaml | head -1)"
derived="${#real_markers[@]}"

if [ "$marked" = "$derived" ]; then
  ok "fleet/cron.py render emits $marked marked line(s), matching the derived set"
else
  bad "fleet/cron.py render emits $marked marked line(s) but the health surface derives $derived — the schedule and its consumer disagree"
fi
if [ "$declared_lines" = "$derived" ]; then
  ok "infra/fleet/inventory.yaml declares schedule.lines: $declared_lines, matching the derived set"
else
  bad "infra/fleet/inventory.yaml declares schedule.lines: ${declared_lines:-<none>} but the derived set has $derived"
fi

# A4 — the porting documents name no stale schedule size. The counts live in one
# place (the manifest); prose here is a claim someone can re-provoke.
for doc in infra/fleet/README.md infra/fleet/inventory.yaml docs/FLEET-CRON-PARITY.md; do
  if grep -qE 'three crontab|three marked|three rungs|three cron jobs|the third job' "$doc"; then
    bad "$doc still claims a three-rung schedule"
  else
    ok "$doc names no stale three-rung schedule count"
  fi
done

printf '\n== healthz-jobset: B. the surface can fail — both halves, over HTTP ==\n'
control "infra/fleet/healthz.py" "$scratch/control.real.log"
if [ "$CONTROL_FAILS" -eq 0 ]; then
  ok "the real surface accepts the complete document and refuses all $derived single-rung shortfall(s) by name"
else
  bad "the real surface lost $CONTROL_FAILS assertion(s) — the check cannot fail, or fails for the wrong reason"
fi

printf '\n== healthz-jobset: C. the set is READ, not remembered, and it fails closed ==\n'

# A scratch copy of the tree, so the manifest can be changed without touching
# the real one. healthz.py resolves its declaration from its own location, so a
# copy under this root reads THIS root's manifest.
scratch_repo="$scratch/repo"
mkdir -p "$scratch_repo/infra/fleet" "$scratch_repo/fleet" "$scratch_repo/config"
cp infra/fleet/healthz.py "$scratch_repo/infra/fleet/healthz.py"
cp fleet/cron.py fleet/runtime.py "$scratch_repo/fleet/"
cp config/fleet-jobs.json "$scratch_repo/config/fleet-jobs.json"
scratch_hz="$scratch_repo/infra/fleet/healthz.py"
pristine="$scratch/healthz.pristine.py"
cp "$scratch_hz" "$pristine"
sha_before="$(sha256sum "$pristine" | cut -d' ' -f1)"

if [ "$(python3 "$driver" derivation "$scratch_hz" 2>/dev/null | wc -l)" = "$derived" ]; then
  ok "a scratch copy of the tree derives the same $derived-marker set as the real tree"
else
  bad "a scratch copy of the tree derives a different set from the real tree — the control below would not be measuring the real code"
fi

# C1 — enabling a NEW job makes the surface demand it. This is the property a
# remembered count cannot have, and it is what makes the derivation load-bearing.
python3 - "$scratch_repo/config/fleet-jobs.json" <<'PY'
import json
import sys

path = sys.argv[1]
with open(path, encoding="utf-8") as handle:
    manifest = json.load(handle)
manifest["jobs"].append(
    {
        "name": "gate-probe",
        "marker": "ao-fleet-gate-probe",
        "interval": 7,
        "command": "/usr/bin/python3 fleet/watchdog.py run",
        "user": "",
        "log": "gate-probe.log",
        "singleton": True,
        "enabled": True,
    }
)
with open(path, "w", encoding="utf-8") as handle:
    json.dump(manifest, handle, indent=2)
PY
python3 "$driver" write-doc "$scratch/c1.json" "${real_markers[@]}" >/dev/null 2>&1
check_one "C1 a newly enabled job is demanded by the surface (the set is read, not remembered)" \
  503 "ao-fleet-gate-probe" "$scratch_hz" "$scratch/c1.json"

# C2/C3 — fail closed. The document stays complete w.r.t. the real set, so the
# ONLY reason to refuse is the declaration itself.
python3 "$driver" write-doc "$scratch/real-complete.json" "${real_markers[@]}" >/dev/null 2>&1
printf '{"jobs": "this is not a list of jobs"}\n' > "$scratch_repo/config/fleet-jobs.json"
check_one "C2 a malformed declaration fails closed (503 cannot-assess)" \
  503 "fleet/cron.py" "$scratch_hz" "$scratch/real-complete.json"

rm -f "$scratch_repo/config/fleet-jobs.json"
check_one "C3 an absent declaration fails closed (503 cannot-assess)" \
  503 "fleet/cron.py" "$scratch_hz" "$scratch/real-complete.json"

python3 - "$scratch_repo/config/fleet-jobs.json" <<'PY'
import json
import sys

jobs = [
    {
        "name": "watchdog",
        "marker": "ao-fleet-watchdog",
        "interval": 2,
        "command": "/usr/bin/python3 fleet/watchdog.py run",
        "user": "",
        "log": "watchdog.log",
        "singleton": True,
        "enabled": False,
    }
]
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump({"jobs": jobs}, handle, indent=2)
PY
check_one "C4 a declaration with nothing enabled fails closed (503 cannot-assess)" \
  503 "no enabled job" "$scratch_hz" "$scratch/real-complete.json"

printf '\n== healthz-jobset: D. the mutants — restoring the stale shape must RED the same control ==\n'

# Part C changed this tree's declaration on purpose; the mutants must be
# measured against an INTACT one, or their diff would be the broken manifest
# rather than the mutation (this exact confusion was caught by the "a tree that
# is simply broken proves nothing" guard on this gate's first run).
cp config/fleet-jobs.json "$scratch_repo/config/fleet-jobs.json"
if [ "$(python3 "$driver" derivation "$scratch_hz" 2>/dev/null | wc -l)" = "$derived" ]; then
  ok "the scratch declaration was restored before the mutants ($derived marker(s) derived again)"
else
  bad "the scratch declaration was NOT restored — the mutants below would be measured against a broken manifest"
fi

run_mutant() { # <label> <python patch file> <anchor that must appear after the patch>
  local label=$1 patch=$2 after=$3
  local mlog="$scratch/control.mutant.log"
  # A mutation that does not land proves nothing, so the patch failing is itself
  # a finding rather than something to carry on past.
  if ! python3 "$patch" "$scratch_hz"; then
    bad "$label — the mutation did not land (a mutation that never applied proves nothing)"
    return
  fi
  if grep -qF -- "$after" "$scratch_hz"; then
    ok "$label: the mutation landed ($after)"
  else
    bad "$label — the mutation is not present in the file after patching"
    return
  fi
  if [ "$(sha256sum "$scratch_hz" | cut -d' ' -f1)" = "$sha_before" ]; then
    bad "$label — the file is byte-identical after the mutation, so the control below would prove nothing"
    return
  fi

  CONTROL_QUIET=0
  control "$scratch_hz" "$mlog"
  local lost=$CONTROL_FAILS
  if [ "$lost" -eq 0 ]; then
    bad "$label SURVIVED — every assertion the real source passes, the mutant passes too"
  elif ! grep -qF "OK    the complete document answers 200" "$mlog"; then
    bad "$label RED, but it broke the healthy case too — a tree that is simply broken proves nothing"
  elif grep -qF "FAIL  a document missing ao-fleet-reap" "$mlog"; then
    ok "$label RED — $lost assertion(s) lost, including the missing-rung half that names ao-fleet-reap"
  else
    bad "$label RED for a reason that does not name the rung the defect was about"
  fi

  cp "$pristine" "$scratch_hz"
  if [ "$(sha256sum "$scratch_hz" | cut -d' ' -f1)" = "$sha_before" ]; then
    ok "$label restored byte-identically ($sha_before)"
  else
    bad "$label was NOT restored byte-identically"
  fi
}

cat > "$scratch/patch-literal.py" <<'PY'
"""Restore the stale literal shape: expect only the first three enabled rungs."""
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
old = '            known = tuple(str(job["marker"]) for job in cron.enabled_jobs(manifest))\n'
new = '            known = tuple(str(job["marker"]) for job in cron.enabled_jobs(manifest))[:3]\n'
if text.count(old) != 1:
    raise SystemExit(f"anchor matched {text.count(old)} times, not 1")
path.write_text(text.replace(old, new), encoding="utf-8")
PY

cat > "$scratch/patch-dropcheck.py" <<'PY'
"""Remove the missing-marker check, leaving only the count-independent 200 path."""
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
old = "    missing = [marker for marker in expected if marker not in present]\n"
new = "    missing = []\n"
if text.count(old) != 1:
    raise SystemExit(f"anchor matched {text.count(old)} times, not 1")
path.write_text(text.replace(old, new), encoding="utf-8")
PY

run_mutant "M1 the removed literal (expect only the first three rungs)" "$scratch/patch-literal.py" "enabled_jobs(manifest))[:3]"
run_mutant "M2 the missing-marker check deleted" "$scratch/patch-dropcheck.py" "missing = []"

printf '\n'
if [ "$fail" -gt 0 ]; then
  printf 'check-healthz-jobset: FAIL — %d finding(s)\n' "$fail" >&2
  exit 1
fi
if [ "$cannot" -gt 0 ]; then
  printf 'check-healthz-jobset: CANNOT-ASSESS — %d control(s) could not be measured\n' "$cannot" >&2
  exit 2
fi
printf 'check-healthz-jobset: OK — the expected set is derived from the schedule and read live (%d enabled rung(s)); the surface refuses every single-rung shortfall by name, fails closed on an unreadable declaration, and both mutants (the stale literal, the deleted check) RED the same control\n' "$derived"
exit 0
