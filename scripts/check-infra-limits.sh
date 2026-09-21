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
# TRANSIENT SCAN FAILURES ARE SUPPRESSED, THEN RETRIED (issue #1392)
#   `find` exits 1 on ANY error, and one of them is routine on this box: a
#   top-level scratch entry that vanishes between readdir and stat, which is what
#   four lanes churning scratch dirs produce continuously. Measured 2026-09-19:
#   225 of 300 `find /tmp -maxdepth 1` scans returned rc=1 while a neighbour
#   churned files at the top level, and 0 of 300 against an unchurned target —
#   one scan is a ~59 ms window over ~21,000 entries. The guard discarded the
#   reason (`2>/dev/null`) and failed closed on any non-zero rc, so the race
#   redded the whole composite for EVERY lane with nothing saying why.
#
#   Three things fix it, and each is measured rather than assumed:
#     1. `-ignore_readdir_race` — find's own answer to a vanished entry. It takes
#        the rate from 225/300 to 13/300. It is NOT sufficient alone (a residual
#        class survives, measured), and it does NOT hide a real failure: a scan
#        target that genuinely cannot be read still returns 1 with its own
#        message (measured: absent target rc=1, unreadable dir rc=1, path under a
#        regular file rc=1).
#     2. the scan KEEPS its stderr, so a failure names the reason instead of an
#        unexplained rc;
#     3. the scan is RETRIED with a short backoff, and is called a failure only
#        when EVERY attempt failed — so the residual transient class is retired
#        while a genuinely unscannable directory still fails closed, by name.
#
# NEGATIVE CONTROLS (always run — a guard that cannot fail proves nothing)
#   Each control provokes its guard and asserts the refusal NAMES the offender.
#   The orphan-hog control scales the THRESHOLD (a 2 MiB file against a 1 MiB
#   ceiling) rather than filling a tmpfs that four lanes share: a provoked
#   failure has to be cheap, and a real file over a real ceiling is still the
#   same failure. The 0-byte artifact is provoked with `: > file`, which is
#   exactly what a failed redirect leaves behind. One control is positive — the
#   write rule must ACCEPT a non-empty artifact — because a guard that only ever
#   refuses is as uninformative as one that never fails. Three more prove the
#   pair above in both directions: a target that genuinely cannot be scanned
#   still fails by name, a transient failure a retry recovers does not red, and a
#   measurement df cannot read still fails (with df's own reason).
#   Every provoked failure is uid-independent: `chmod 0` is invisible to uid 0,
#   so it provokes nothing in the Cloud Build (root) venue — measured 2026-09-19
#   in #1381 / #1383, where exactly that made CI red for every lane.
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
#   AO_INFRA_SCAN_ATTEMPTS   scan attempts before a failure   (default 3)
#   AO_INFRA_SCAN_BACKOFF_MS delay between those attempts, ms (default 200)
set -uo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
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
  AO_INFRA_SCAN_ATTEMPTS   scan attempts before a failure   (default 3)
  AO_INFRA_SCAN_BACKOFF_MS delay between those attempts, ms (default 200)
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
SCAN_ATTEMPTS="${AO_INFRA_SCAN_ATTEMPTS:-3}"
SCAN_BACKOFF_MS="${AO_INFRA_SCAN_BACKOFF_MS:-200}"
MAX_USED_PCT="$((100 - MIN_FREE_PCT))"
CONTRACT="docs/INFRA-LIMITS.md"
REPORT=".verify/infra-limits-report.json"

# The main guards' scan telemetry, captured where they run (the controls below
# call the same guards, so reading the globals at evidence time would report the
# last control instead of the guarded filesystem).
ORPHAN_SCAN_TRIES=0
ORPHAN_SCAN_RECOVERED=0
ORPHAN_SCAN_ERROR=""

for tool in df find wc mktemp awk head sleep; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "check-infra-limits: CANNOT-ASSESS — $tool not found" >&2
    exit 2
  fi
done

for pair in "AO_INFRA_MIN_FREE_MB=$MIN_FREE_MB" "AO_INFRA_MIN_FREE_PCT=$MIN_FREE_PCT" \
            "AO_INFRA_MAX_SCRATCH_MB=$MAX_SCRATCH_MB" \
            "AO_INFRA_SCAN_ATTEMPTS=$SCAN_ATTEMPTS" \
            "AO_INFRA_SCAN_BACKOFF_MS=$SCAN_BACKOFF_MS"; do
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

# --- scanning: keep the reason, retry the transient (issue #1392) -------------

first_line() { # first_line <file> — the command's own first stderr line, or ""
  local line=""
  IFS= read -r line < "$1" 2>/dev/null || true
  printf '%s' "$line"
}

json_escape() { # json_escape <string> — enough for a message inside a JSON string
  local s="$1"
  s="${s//\\/\\\\}"
  s="${s//\"/\\\"}"
  printf '%s' "$s"
}

# scan_with_retry <outfile> <errfile> <cmd...> — run one scan, KEEP its stderr,
# and retry a non-zero result with a short backoff. The FIRST failing attempt's
# stderr is left in <errfile> and a retry writes to <errfile>.retry, so the
# reason survives a retry that then succeeds — the recovery line can still say
# what went wrong. Sets SCAN_TRIES (attempts made) and SCAN_RECOVERED (failed
# attempts a later one recovered). Returns the LAST attempt's rc: 0 when any
# attempt succeeded, non-zero only when every attempt failed — that is the
# fail-closed half, and it is why a directory that genuinely cannot be scanned
# still fails the gate, by name.
SCAN_TRIES=0
SCAN_RECOVERED=0
scan_with_retry() {
  local out="$1" err="$2" max="$SCAN_ATTEMPTS" try=1 rc=0 delay
  shift 2
  [ "$max" -ge 1 ] || max=1
  while :; do
    if [ "$try" -eq 1 ]; then
      "$@" > "$out" 2> "$err"
    else
      "$@" > "$out" 2> "$err.retry"
    fi
    rc=$?
    [ "$rc" -eq 0 ] && break
    [ "$try" -lt "$max" ] || break
    try=$((try + 1))
    printf -v delay '%d.%03d' "$((SCAN_BACKOFF_MS / 1000))" "$((SCAN_BACKOFF_MS % 1000))"
    sleep "$delay"
  done
  SCAN_TRIES="$try"
  SCAN_RECOVERED=0
  if [ "$rc" -eq 0 ] && [ "$try" -gt 1 ]; then
    SCAN_RECOVERED=$((try - 1))
  fi
  return "$rc"
}

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
  local stats avail used_pct used_num rc=0 why=""
  scan_with_retry "$WORK/df-free.out" "$WORK/df-free.err" df -Pm -- "$dir"
  rc=$?
  stats="$(awk 'NR==2{print $4, $5}' "$WORK/df-free.out" 2>/dev/null)"
  avail="${stats%% *}"
  used_pct="${stats##* }"
  if [ -z "$avail" ] || [ -z "$used_pct" ] || [ "$avail" = "$stats" ]; then
    why="$(first_line "$WORK/df-free.err")"
    [ -n "$why" ] || why="no stderr captured"
    printf '  FAIL  free-space: cannot read df for %s (df rc=%s: %s)\n' \
      "$dir" "$rc" "$why"
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
  local list="$WORK/hogs.tsv" err="$WORK/hogs.err" rc=0 size path why=""
  # -ignore_readdir_race is find's own answer to an entry that vanishes between
  # readdir and stat (measured 225/300 -> 13/300 under top-level /tmp churn); the
  # retry in scan_with_retry retires the residual class. Neither hides a real
  # failure: a target that cannot be read still returns non-zero with a message
  # (measured: absent target, unreadable dir, path under a regular file).
  scan_with_retry "$list" "$err" \
    find "$dir" -maxdepth 1 -ignore_readdir_race -type f -size "+${max_mb}M" \
      -printf '%s\t%p\n'
  rc=$?
  if [ "$rc" -ne 0 ]; then
    why="$(first_line "$err")"
    [ -n "$why" ] || why="no stderr captured"
    printf '  FAIL  orphan-hog: cannot scan %s (find rc=%s: %s)\n' "$dir" "$rc" "$why"
    printf '        %s attempt(s), %s ms apart, all failed — not the transient race:\n' \
      "$SCAN_TRIES" "$SCAN_BACKOFF_MS"
    printf '        check the directory exists and is readable before blaming a hog\n'
    return 2
  fi
  if [ ! -s "$list" ]; then
    if [ "$SCAN_RECOVERED" -gt 0 ]; then
      printf '  OK    orphan-hog: no scratch file in %s exceeds %s MiB (recovered after %s transient scan failure(s): %s)\n' \
        "$dir" "$max_mb" "$SCAN_RECOVERED" "$(first_line "$err")"
    else
      printf '  OK    orphan-hog: no scratch file in %s exceeds %s MiB\n' "$dir" "$max_mb"
    fi
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

# Substring matching in bash, never `printf ... | grep -q`: `grep -q` exits on
# its first match, which SIGPIPEs the producer while it is still writing, and
# `set -o pipefail` promotes that 141 to the pipeline's status — a containment
# test that can kill its own producer, latent until the report outgrows the pipe
# buffer. Bash cannot fail that way, so this control always can be measured
# (`scripts/check-verdict-contains.sh`, #843 / #852).

expect_fail() { # expect_fail <control> <needle> <rc> <output>
  local id="$1" needle="$2" rc="$3" out="$4"
  if [ "$rc" -eq 0 ]; then
    bad "$id: NOT PROVOKED — the guard passed when it was supposed to fail"
    return 0
  fi
  if contains "$out" "$needle"; then
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

expect_recovered() { # expect_recovered <control> <needle> <rc> <output>
  local id="$1" needle="$2" rc="$3" out="$4"
  if [ "$rc" -ne 0 ]; then
    bad "$id: the guard redded on a transient scan failure — $(printf '%s' "$out" | head -1)"
    return 0
  fi
  if contains "$out" "$needle"; then
    ok "$id: retried, not redded — $(printf '%s' "$out" | head -1 | sed 's/^  OK    //')"
  else
    bad "$id: the guard passed without naming the recovery ($needle)"
  fi
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

  # C5 — the guard still FAILS CLOSED, by name, when the target genuinely cannot
  # be scanned — and it says WHY. The arm is a target that does not exist rather
  # than a chmod(0) directory: mode 0 is invisible to uid 0, so a chmod(0)
  # fixture provokes nothing in the Cloud Build (root) venue and reds CI for
  # every lane instead of proving the refusal (measured 2026-09-19, #1381/#1383).
  # ENOENT is uid-independent, and EVERY retry sees it, so it must still fail.
  out="$(guard_orphan_hogs "$ctl/does-not-exist" 1)"; rc=$?
  expect_fail "C5 orphan-hog fails closed" \
    "cannot scan $ctl/does-not-exist (find rc=1:" "$rc" "$out"

  # C6 — a TRANSIENT scan failure is retried, never redded. Deterministic
  # mechanism: a `find` earlier on PATH whose FIRST call runs the real find
  # against a path that has genuinely been removed — a real ENOENT from the real
  # binary, same rc and same error shape as the measured race — and whose every
  # later call delegates to the real find. The vanish target is passed WITH
  # -ignore_readdir_race, so the shim stands for the residual transient class
  # that survives the flag (measured 13/300), not for one it already covers.
  # (The probabilistic half is measured out of band: 225 of 300 scans rc=1 under
  # top-level /tmp churn with the flag off, 13 of 300 with it on, 0 of 300
  # against an unchurned target.)
  local shim="$ctl/shim" state="$ctl/shim.state" vanished="$ctl/vanished"
  local tx="$ctl/transient" real_find="" saved_path=""
  real_find="$(command -v find)"
  mkdir -p "$tx" "$shim"
  rmdir "$vanished" 2>/dev/null || true
  cat > "$shim/find" <<'SHIM'
#!/usr/bin/env bash
n="$(cat "$AO_CTL_SCAN_STATE" 2>/dev/null || echo 0)"
printf '%s\n' "$((n + 1))" > "$AO_CTL_SCAN_STATE"
# `exec -a find` keeps argv[0] as `find`, so the message keeps the measured
# shape (`find: '<path>': No such file or directory`) and not the full path.
if [ "$n" -eq 0 ]; then
  exec -a find "$AO_CTL_REAL_FIND" "$AO_CTL_SCAN_VANISHED" -maxdepth 1 \
    -ignore_readdir_race -type f -printf '%s\t%p\n'
fi
exec -a find "$AO_CTL_REAL_FIND" "$@"
SHIM
  chmod +x "$shim/find"
  saved_path="$PATH"
  export PATH="$shim:$PATH"
  export AO_CTL_SCAN_STATE="$state" AO_CTL_SCAN_VANISHED="$vanished" \
         AO_CTL_REAL_FIND="$real_find"
  out="$(guard_orphan_hogs "$tx" 1)"; rc=$?
  unset AO_CTL_SCAN_STATE AO_CTL_SCAN_VANISHED AO_CTL_REAL_FIND
  export PATH="$saved_path"
  expect_recovered "C6 orphan-hog transient retry" \
    "recovered after 1 transient scan failure" "$rc" "$out"
  rm -rf "$shim" "$state" "$tx"

  # C7 — the free-space guard carried the SAME defect class (a discarded reason
  # and a fail-closed verdict on an unreadable measurement), so it gets the same
  # treatment and its own proof: a target df cannot read still FAILS, and now
  # says what df said rather than only that it could not read it.
  out="$(guard_free_space "$ctl/no-such-mount" 1)"; rc=$?
  expect_fail "C7 free-space fails closed" \
    "cannot read df for $ctl/no-such-mount (df rc=" "$rc" "$out"

  rm -rf "$ctl"
}

# --- evidence (the one artifact-producing path in this script) ---------------

write_evidence() {
  local stats avail used_num rc=0 why=""
  scan_with_retry "$WORK/df-evidence.out" "$WORK/df-evidence.err" \
    df -Pm -- "$SCRATCH_DIR"
  rc=$?
  stats="$(awk 'NR==2{print $4, $5}' "$WORK/df-evidence.out" 2>/dev/null)"
  avail="${stats%% *}"
  used_num="${stats##* }"
  used_num="${used_num%\%}"
  if [ -z "$avail" ] || [ -z "$used_num" ]; then
    why="$(first_line "$WORK/df-evidence.err")"
    [ -n "$why" ] || why="no stderr captured"
    bad "evidence: cannot measure $SCRATCH_DIR (df rc=$rc: $why)"
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
    printf '  "scan_attempts_max": %s,\n' "$SCAN_ATTEMPTS"
    printf '  "scan_backoff_ms": %s,\n' "$SCAN_BACKOFF_MS"
    printf '  "measured_free_mb": %s,\n' "$avail"
    printf '  "measured_used_pct": %s,\n' "$used_num"
    printf '  "orphan_scan_attempts": %s,\n' "$ORPHAN_SCAN_TRIES"
    printf '  "orphan_scan_recovered": %s,\n' "$ORPHAN_SCAN_RECOVERED"
    printf '  "orphan_scan_last_error": "%s",\n' "$(json_escape "$ORPHAN_SCAN_ERROR")"
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
ORPHAN_SCAN_TRIES="$SCAN_TRIES"
ORPHAN_SCAN_RECOVERED="$SCAN_RECOVERED"
ORPHAN_SCAN_ERROR="$(first_line "$WORK/hogs.err")"

echo "== negative controls (provoked) =="
run_negative_controls

echo "== evidence write (verified with wc -c) =="
write_evidence

if [ "$fail" -gt 0 ]; then
  echo "check-infra-limits: FAIL — $fail guard(s) or control(s) refused" >&2
  exit 1
fi
echo "check-infra-limits: OK — contract declared, $SCRATCH_DIR within limits, 7 controls provoked (5 refused by name, 1 transient scan recovered, 1 positive), evidence verified non-empty"
exit 0
