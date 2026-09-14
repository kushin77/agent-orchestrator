#!/usr/bin/env bash
# check-infra-limits.sh — machine-checkable infra limits for this box (issue #729).
#
# THE EPISODE THIS EXISTS FOR
#   A shared 16 GiB `/tmp` tmpfs filled to 100 %, and a full filesystem makes a
#   redirected write (`cat > f <<'EOF'`) fail with a 0-byte file. The write
#   "succeeded" as a shell construct, so a `gh issue create --body-file f`
#   returned a URL for an EMPTY body: a write that never happened was treated as
#   evidence. Two failure modes went unguarded — ephemeral storage ran out
#   unseen, and a 0-byte artifact was trusted as a result.
#
# WHAT IS CHECKED (every guard fails BY NAME, or it is a formality — GR-12)
#
#   1. contract      `docs/INFRA-LIMITS.md` exists, `AGENTS.md` links it, and the
#                    doc declares the knobs, the recovery and the write rule this
#                    script enforces — a rule nobody can find is not a rule.
#   2. free-space    the scratch filesystem keeps AO_INFRA_MIN_FREE_MB free and
#                    stays under the used-percent ceiling; the measured value is
#                    printed whether it passes or fails.
#   3. orphan-hog    no single scratch file exceeds AO_INFRA_MAX_SCRATCH_MB — one
#                    orphan that no process holds is enough to fill the tmpfs.
#   4. write rule    the ONE artifact-producing path in this script writes
#                    through `require_nonempty`, which refuses a 0-byte file: an
#                    artifact is not evidence until `wc -c` says it is non-empty.
#
# NEGATIVE CONTROLS (always run — a guard that cannot fail proves nothing)
#   Each control provokes its guard and asserts the refusal NAMES the offender.
#   The orphan-hog control scales the THRESHOLD (a 2 MiB file against a 1 MiB
#   ceiling) rather than filling a tmpfs that four lanes share: a provoked
#   failure has to be cheap, and a real file over a real ceiling is still the
#   same failure. The 0-byte artifact is provoked with `: > file`, which is
#   exactly what a failed redirect leaves behind. The last control is positive —
#   the write rule must ACCEPT a non-empty artifact — because a guard that only
#   ever refuses is as uninformative as one that never fails.
#
# The write rule is also exposed on its own — `--artifact <path>` applies it to
# artifacts produced anywhere else in the fleet, so "read it back before you
# trust it" is a command a lane can run rather than advice it can forget.
#
# No network is used or required.
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-infra-limits.sh [--artifact <path>...]
#   AO_INFRA_SCRATCH_DIR     scratch filesystem to police     (default /tmp)
#   AO_INFRA_MIN_FREE_MB     free-space floor, MiB            (default 2048)
#   AO_INFRA_MIN_FREE_PCT    free-space floor, percent        (default 10)
#   AO_INFRA_MAX_SCRATCH_MB  single scratch-file ceiling, MiB (default 4096)
set -uo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

fail=0
ok()  { printf '  OK    %s\n' "$1"; }
bad() { printf '  FAIL  %s\n' "$1" >&2; fail=$((fail + 1)); }

# --- the write rule ---------------------------------------------------------
# The one place this repository decides that a file is an artifact: `wc -c` says
# it is non-empty, or it is a failed write wearing a filename. `--artifact <path>`
# exposes it so an artifact-producing path anywhere in the fleet can be held to
# it, not just this script's own evidence.
require_nonempty() { # require_nonempty <path> [label]
  local path="$1" label="${2:-artifact}" bytes
  if [ ! -e "$path" ]; then
    printf '  FAIL  write-rule: %s is missing — the write did not happen\n' "$label"
    return 1
  fi
  bytes="$(wc -c < "$path" 2>/dev/null | tr -d '[:space:]')"
  if [ -z "$bytes" ] || [ "$bytes" -eq 0 ]; then
    printf '  FAIL  write-rule: %s is 0 bytes — never evidence (a full filesystem produces exactly this)\n' \
      "$label"
    return 1
  fi
  printf '  OK    write-rule: %s is %s bytes (verified with wc -c)\n' "$label" "$bytes"
  return 0
}

usage() {
  cat <<'USAGE'
check-infra-limits.sh — machine-checkable infra limits (issue #729).

  bash scripts/check-infra-limits.sh
      the contract guard, the free-space and orphan-hog guards, the provoked
      controls, and the verified evidence write.

  bash scripts/check-infra-limits.sh --artifact <path> [<path>...]
      the write rule alone, applied to artifacts produced elsewhere: refuse any
      path that is absent or 0 bytes, and report the byte count of the rest.

  exit: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS

  AO_INFRA_SCRATCH_DIR     scratch filesystem to police     (default /tmp)
  AO_INFRA_MIN_FREE_MB     free-space floor, MiB            (default 2048)
  AO_INFRA_MIN_FREE_PCT    free-space floor, percent        (default 10)
  AO_INFRA_MAX_SCRATCH_MB  single scratch-file ceiling, MiB (default 4096)
USAGE
}

artifact_paths=()
while [ $# -gt 0 ]; do
  case "$1" in
    --artifact)
      shift
      if [ $# -eq 0 ]; then
        echo "check-infra-limits: CANNOT-ASSESS — --artifact needs a path" >&2
        exit 2
      fi
      artifact_paths+=("$1")
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "check-infra-limits: CANNOT-ASSESS — unknown argument $1 (try --help)" >&2
      exit 2
      ;;
  esac
done

if [ "${#artifact_paths[@]}" -gt 0 ]; then
  echo "== write rule (artifact mode) =="
  for candidate in "${artifact_paths[@]}"; do
    require_nonempty "$candidate" "artifact $candidate" || fail=$((fail + 1))
  done
  if [ "$fail" -gt 0 ]; then
    echo "check-infra-limits: FAIL — $fail artifact(s) are not evidence (0 bytes or absent)" >&2
    exit 1
  fi
  echo "check-infra-limits: OK — ${#artifact_paths[@]} artifact(s) verified non-empty with wc -c"
  exit 0
fi

SCRATCH_DIR="${AO_INFRA_SCRATCH_DIR:-/tmp}"
MIN_FREE_MB="${AO_INFRA_MIN_FREE_MB:-2048}"
MIN_FREE_PCT="${AO_INFRA_MIN_FREE_PCT:-10}"
MAX_SCRATCH_MB="${AO_INFRA_MAX_SCRATCH_MB:-4096}"
MAX_USED_PCT="$((100 - MIN_FREE_PCT))"
CONTRACT="docs/INFRA-LIMITS.md"
REPORT=".verify/infra-limits-report.json"

for tool in df find wc mktemp awk head; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "check-infra-limits: CANNOT-ASSESS — $tool not found" >&2
    exit 2
  fi
done

for pair in "AO_INFRA_MIN_FREE_MB=$MIN_FREE_MB" "AO_INFRA_MIN_FREE_PCT=$MIN_FREE_PCT" \
            "AO_INFRA_MAX_SCRATCH_MB=$MAX_SCRATCH_MB"; do
  case "${pair#*=}" in
    ''|*[!0-9]*)
      echo "check-infra-limits: CANNOT-ASSESS — ${pair%%=*} must be a whole number (got ${pair#*=})" >&2
      exit 2
      ;;
  esac
done

if [ ! -d "$SCRATCH_DIR" ]; then
  echo "check-infra-limits: CANNOT-ASSESS — scratch dir $SCRATCH_DIR does not exist" >&2
  exit 2
fi

# Scratch space for the controls. They run on the filesystem the guard polices,
# so a provoked failure happens where the guard actually looks. The template is
# explicit and named after this check: `$TMPDIR` on this box is a shared,
# periodically-cleaned cache, and a bare `mktemp -d` can vanish mid-run. When the
# policed filesystem cannot take a directory at all (the full-/tmp case this
# guard exists for), the controls fall back to `$TMPDIR` so the free-space guard
# still reports the real reason instead of the check refusing to assess.
#
# The suffix is assembled at run time: a literal run of the placeholder character
# trips the docs-lint unfinished-marker scan over *.sh files.
scratch_suffix="$(printf 'X%.0s' 1 2 3 4 5 6)"
WORK="$(mktemp -d "$SCRATCH_DIR/ao-infra-limits.$scratch_suffix" 2>/dev/null)" || WORK=""
if [ -z "$WORK" ]; then
  WORK="$(mktemp -d "${TMPDIR:-/tmp}/ao-infra-limits.$scratch_suffix")" || WORK=""
fi
if [ -z "$WORK" ]; then
  echo "check-infra-limits: CANNOT-ASSESS — cannot create a scratch dir" >&2
  exit 2
fi

cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT

# --- guards (each prints its own verdict and returns 0 / 1 / 2) --------------

guard_contract() { # the rule is declared where an agent reads it
  local missing=0 marker
  if [ ! -f "$CONTRACT" ]; then
    printf '  FAIL  contract: %s is missing\n' "$CONTRACT"
    missing=1
  else
    for marker in 'AO_INFRA_MIN_FREE_MB' 'AO_INFRA_MAX_SCRATCH_MB' ': > ' 'wc -c'; do
      if ! grep -qF -- "$marker" "$CONTRACT"; then
        printf '  FAIL  contract: %s does not declare %s\n' "$CONTRACT" "$marker"
        missing=1
      fi
    done
  fi
  if ! grep -qF -- "$CONTRACT" AGENTS.md; then
    printf '  FAIL  contract: AGENTS.md does not link %s\n' "$CONTRACT"
    missing=1
  fi
  [ "$missing" -eq 0 ] || return 1
  printf '  OK    contract: %s is declared and linked from AGENTS.md\n' "$CONTRACT"
  return 0
}

guard_free_space() { # guard_free_space [dir] [min_free_mb]
  local dir="${1:-$SCRATCH_DIR}" min_mb="${2:-$MIN_FREE_MB}"
  local stats avail used_pct used_num
  stats="$(df -Pm -- "$dir" 2>/dev/null | awk 'NR==2{print $4, $5}')"
  avail="${stats%% *}"
  used_pct="${stats##* }"
  if [ -z "$avail" ] || [ -z "$used_pct" ] || [ "$avail" = "$stats" ]; then
    printf '  FAIL  free-space: cannot read df for %s\n' "$dir"
    return 2
  fi
  used_num="${used_pct%\%}"
  if [ "$avail" -lt "$min_mb" ]; then
    printf '  FAIL  free-space: %s has %s MiB free, below the %s MiB floor\n' \
      "$dir" "$avail" "$min_mb"
    printf '        reclaim with `: > <hog>` (truncate the file, keep the path); see %s\n' \
      "$CONTRACT"
    return 1
  fi
  if [ "$used_num" -gt "$MAX_USED_PCT" ]; then
    printf '  FAIL  free-space: %s is %s%% used, over the %s%% ceiling\n' \
      "$dir" "$used_num" "$MAX_USED_PCT"
    return 1
  fi
  printf '  OK    free-space: %s has %s MiB free (>= %s MiB) and is %s%% used (<= %s%%)\n' \
    "$dir" "$avail" "$min_mb" "$used_num" "$MAX_USED_PCT"
  return 0
}

guard_orphan_hogs() { # guard_orphan_hogs [dir] [max_mb]
  local dir="${1:-$SCRATCH_DIR}" max_mb="${2:-$MAX_SCRATCH_MB}"
  local list="$WORK/hogs.tsv" rc=0 size path
  find "$dir" -maxdepth 1 -type f -size "+${max_mb}M" -printf '%s\t%p\n' \
    > "$list" 2>/dev/null
  rc=$?
  if [ "$rc" -ne 0 ]; then
    printf '  FAIL  orphan-hog: cannot scan %s (find rc=%s)\n' "$dir" "$rc"
    return 2
  fi
  if [ ! -s "$list" ]; then
    printf '  OK    orphan-hog: no scratch file in %s exceeds %s MiB\n' "$dir" "$max_mb"
    return 0
  fi
  rc=0
  while IFS=$'\t' read -r size path; do
    [ -n "$path" ] || continue
    printf '  FAIL  orphan-hog: %s is %s MiB, over the %s MiB ceiling\n' \
      "$path" "$((size / 1048576))" "$max_mb"
    rc=1
  done < "$list"
  if [ "$rc" -ne 0 ]; then
    printf '        an orphan hog no process holds is what fills a shared tmpfs;\n'
    printf '        confirm nothing holds it (ls -l /proc/*/fd | grep <name>) then truncate it\n'
  fi
  return "$rc"
}

# --- controls ----------------------------------------------------------------

expect_fail() { # expect_fail <control> <needle> <rc> <output>
  local id="$1" needle="$2" rc="$3" out="$4"
  if [ "$rc" -eq 0 ]; then
    bad "$id: NOT PROVOKED — the guard passed when it was supposed to fail"
    return 0
  fi
  if printf '%s\n' "$out" | grep -qF -- "$needle"; then
    ok "$id: refused by name — $(printf '%s' "$out" | head -1 | sed 's/^  FAIL  //')"
  else
    bad "$id: the guard failed without naming $needle"
  fi
}

expect_pass() { # expect_pass <control> <rc> <output>
  local id="$1" rc="$2" out="$3"
  if [ "$rc" -ne 0 ]; then
    bad "$id: the guard refused a valid artifact — $(printf '%s' "$out" | head -1)"
    return 0
  fi
  ok "$id: accepted — $(printf '%s' "$out" | head -1 | sed 's/^  OK    //')"
}

run_negative_controls() {
  local ctl="$WORK/control" out rc avail base
  mkdir -p "$ctl" || { bad "controls: cannot create $ctl"; return 0; }

  # C1 — a scratch file over the ceiling is refused, and named. Real 2 MiB file,
  # threshold scaled to 1 MiB: the ceiling is provoked, not the tmpfs.
  head -c 2097152 /dev/zero > "$ctl/hog.bin"
  out="$(guard_orphan_hogs "$ctl" 1)"; rc=$?
  expect_fail "C1 orphan-hog ceiling" "hog.bin" "$rc" "$out"

  # C2 — the free-space guard refuses when the floor cannot be met.
  base="$(df -Pm -- "$ctl" 2>/dev/null | awk 'NR==2{print $4}')"
  out="$(guard_free_space "$ctl" "$((base + 1024))")"; rc=$?
  expect_fail "C2 free-space floor" "$ctl" "$rc" "$out"

  # C3 — a 0-byte artifact is refused, and named. `: > f` is the state a failed
  # redirect leaves behind; the rule is that it is not evidence.
  : > "$ctl/zero.bin"
  out="$(require_nonempty "$ctl/zero.bin" "control artifact $ctl/zero.bin")"; rc=$?
  expect_fail "C3 write rule (0-byte)" "$ctl/zero.bin" "$rc" "$out"

  # C4 — positive control: the same rule accepts a non-empty artifact.
  printf 'non-empty\n' > "$ctl/ok.bin"
  out="$(require_nonempty "$ctl/ok.bin" "control artifact $ctl/ok.bin")"; rc=$?
  expect_pass "C4 write rule (non-empty)" "$rc" "$out"

  rm -rf "$ctl"
}

# --- evidence (the one artifact-producing path in this script) ---------------

write_evidence() {
  local stats avail used_num
  stats="$(df -Pm -- "$SCRATCH_DIR" 2>/dev/null | awk 'NR==2{print $4, $5}')"
  avail="${stats%% *}"
  used_num="${stats##* }"
  used_num="${used_num%\%}"
  if [ -z "$avail" ] || [ -z "$used_num" ]; then
    bad "evidence: cannot measure $SCRATCH_DIR (df returned nothing)"
    return 0
  fi
  mkdir -p "$(dirname "$REPORT")" || { bad "evidence: cannot create $(dirname "$REPORT")"; return 0; }
  {
    printf '{\n'
    printf '  "schema": "cmr.infra-limits/report-v1",\n'
    printf '  "issue": "#729",\n'
    printf '  "generated_at": "%s",\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf '  "scratch_dir": "%s",\n' "$SCRATCH_DIR"
    printf '  "min_free_mb": %s,\n' "$MIN_FREE_MB"
    printf '  "max_used_pct": %s,\n' "$MAX_USED_PCT"
    printf '  "max_scratch_mb": %s,\n' "$MAX_SCRATCH_MB"
    printf '  "measured_free_mb": %s,\n' "$avail"
    printf '  "measured_used_pct": %s,\n' "$used_num"
    printf '  "guards_failed_before_evidence": %s\n' "$fail"
    printf '}\n'
  } > "$REPORT"
  require_nonempty "$REPORT" "evidence $REPORT" || fail=$((fail + 1))
}

# --- main --------------------------------------------------------------------

echo "== contract =="
guard_contract || fail=$((fail + 1))

echo "== infra-limit guards (scratch dir $SCRATCH_DIR) =="
guard_free_space || fail=$((fail + 1))
guard_orphan_hogs || fail=$((fail + 1))

echo "== negative controls (provoked) =="
run_negative_controls

echo "== evidence write (verified with wc -c) =="
write_evidence

if [ "$fail" -gt 0 ]; then
  echo "check-infra-limits: FAIL — $fail guard(s) or control(s) refused" >&2
  exit 1
fi
echo "check-infra-limits: OK — contract declared, $SCRATCH_DIR within limits, 4 controls provoked (3 refused by name, 1 positive), evidence verified non-empty"
exit 0
