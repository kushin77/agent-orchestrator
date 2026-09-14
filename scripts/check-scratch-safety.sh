#!/usr/bin/env bash
# check-scratch-safety.sh — the scratch-space guard (issue #488).
#
# THE MEASURED INCIDENT (2026-09-14)
#   One agent scratch driver did exactly this:
#       L=/tmp/ao412.makeverify.log
#       env -C "$W" make verify >> "$L" 2>&1
#       tail -6 "$L" >> "$L"                # <-- self-referential append
#   On this box /tmp is tmpfs (RAM). The log reached 14,837,231,616 bytes
#   (14.8 GB) and /tmp sat at 100% of its 16 GB: a hard stop for EVERY parallel
#   lane at once, invisible until something unrelated broke. The knock-on did
#   more damage than the disk -- `cp` wrote a 0-byte "backup", and restoring
#   from that backup truncated a source file to empty.
#
# WHY THE SELF-APPEND IS A LOGIC BUG, NOT A STYLE NIT
#   `tail -6 "$L" >> "$L"` reads and writes the SAME path in one command. It
#   cannot converge under repetition: every run at least doubles the log. The
#   repo's own tracked tooling is clean (109 tracked *.sh, zero hits), but the
#   defect lives in *agent scratch drivers*, which no repo tracks and no gate
#   linted until this one.
#
# WHAT THIS GATE REFUSES, BY NAME (each detector provoked by --self-test)
#   SCRATCH-SELF-APPEND      a shell line that reads and writes the same path
#                            or variable in one command (the incident itself)
#   SCRATCH-FILE-OVERSIZE    a single scratch file past the cap -- the 14.8 GB
#                            case, caught at 256 MB instead
#   SCRATCH-SPACE-NEAR-FULL  the scratch filesystem at or over the usage
#                            ceiling (the tmpfs size is the constraint, not the
#                            size of any one file)
#   SCRATCH-TMP-WORKTREE     a registered git worktree living on the scratch
#                            tmpfs (code-indexing#158/#159; this box carried 23)
#   SCRATCH-EMPTY-COPY       a copy that wrote 0 bytes over a non-empty source
#                            -- the 0-byte backup that later truncated a file
#   SCRATCH-COPY-FAILED      the copy itself reported a failure
#   SCRATCH-SHORT-COPY       the copy landed a different byte count
#   SCRATCH-COPY-MISMATCH    the copy landed different content (sha256)
#
# DIVISION OF LABOUR: REPO GATE vs MACHINE GUARD (issue #488, item 1)
#   The MACHINE-level detector is ~/laptop-manage/bin/scratch-guard, on its own
#   timer. It protects every repo at once and is the only thing that can see the
#   whole tmpfs, including other lanes' scratch. This repo CONSUMES its verdict
#   read-only and does not reimplement it.
#   The REPO gate owns the half a repo can honestly assert: its own artifacts,
#   deterministically and offline. The live machine verdict is therefore
#   ADVISORY here (a NOTE, never a red gate) -- a gate that turns red because a
#   neighbour filled the tmpfs reddens an unrelated diff and teaches people to
#   ignore the gate. `--scan` is the verb that REFUSES by name, for an agent or
#   the ops runner that wants the verdict now. See
#   docs/SCRATCH-SPACE-DISCIPLINE.md.
#
# EXIT CONTRACT (guardrails/honesty tri-state, consumed not redefined)
#   0 OK / 1 NOT-OK / 2 CANNOT-ASSESS. Offline, deterministic, no network.
#   bash -n clean; no `set -e` -- a diagnostic must never abort its caller.
#
# Usage:
#   bash scripts/check-scratch-safety.sh                   # the gate
#   bash scripts/check-scratch-safety.sh --self-test       # prove the detectors fire
#   bash scripts/check-scratch-safety.sh --lint [PATH]...  # default: tracked *.sh
#   bash scripts/check-scratch-safety.sh --scan [ROOT]     # refuses by name
#   bash scripts/check-scratch-safety.sh --verify-copy SRC DST
#   bash scripts/check-scratch-safety.sh --copy SRC DST    # copy, then verify
#
# Env seams (the only ones; each exists so a control can construct its input):
#   SG_MAX_FILE_MB  per-file scratch cap            (default 256)
#   SG_MAX_PCT      filesystem usage ceiling        (default 75)
#   SG_FAIL_PCT     emergency usage ceiling         (default 90)
#   SG_ROOT         scratch root for --scan         (default /tmp)
#   SG_REPO         repo whose worktrees are listed (default: this repo)
#   SG_DF           a file holding `df -P`-shaped output: the *measurement*
#                   seam for the space detector, so the near-full refusal is
#                   provable offline without root (the live path runs on every
#                   gate invocation and uses the same parser)
#   SCRATCH_GUARD   path to the machine guard       (default ~/laptop-manage/bin)
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2
self="$root/scripts/$(basename "${BASH_SOURCE[0]}")"

MAX_FILE_MB="${SG_MAX_FILE_MB:-256}"
MAX_PCT="${SG_MAX_PCT:-75}"
FAIL_PCT="${SG_FAIL_PCT:-90}"
SCAN_ROOT="${SG_ROOT:-/tmp}"
SCAN_REPO="${SG_REPO:-$root}"
DF_FIXTURE="${SG_DF:-}"
GUARD_BIN="${SCRATCH_GUARD:-${HOME:-/root}/laptop-manage/bin/scratch-guard}"
DOC_REF="docs/SCRATCH-SPACE-DISCIPLINE.md"

MODE="gate"
LINT_PATHS=()
COPY_SRC=""
COPY_DST=""

usage() {
  sed -n '/^# Usage:/,/^# Env seams/p' "$self" | sed 's/^# \{0,1\}//'
}

while [ $# -gt 0 ]; do
  case "$1" in
    --gate)        MODE="gate"; shift ;;
    --self-test)   MODE="self-test"; shift ;;
    --lint)
      MODE="lint"; shift
      while [ $# -gt 0 ]; do LINT_PATHS+=("$1"); shift; done
      ;;
    --scan)
      MODE="scan"; shift
      if [ $# -gt 0 ]; then SCAN_ROOT="$1"; shift; fi
      ;;
    --copy)
      MODE="copy"; shift
      if [ $# -gt 0 ]; then COPY_SRC="$1"; shift; fi
      if [ $# -gt 0 ]; then COPY_DST="$1"; shift; fi
      ;;
    --verify-copy)
      MODE="verify-copy"; shift
      if [ $# -gt 0 ]; then COPY_SRC="$1"; shift; fi
      if [ $# -gt 0 ]; then COPY_DST="$1"; shift; fi
      ;;
    -h|--help)   usage; exit 0 ;;
    *)           echo "check-scratch-safety: unknown argument: $1" >&2; exit 2 ;;
  esac
done

# ── helpers ──────────────────────────────────────────────────────────────────
cannot_assess() {
  echo "check-scratch-safety: CANNOT-ASSESS -- $*" >&2
  exit 2
}

file_size() { stat -c %s -- "$1" 2>/dev/null || true; }

# self_append_lines <file> — print "<lineno>\t<line>" for every line that reads
# and writes the SAME path or variable around an append redirect.
#
# The predicate is deliberately narrow: one line, one `>>`, and the redirect
# target must also occur BEFORE the redirect on that same line. A mere append to
# a log assigned on an earlier line is NOT this defect (that is the unbounded
# capture the lint reports as a NOTE) -- only the self-referential form is
# provably non-convergent.
#
# Quotes and braces are stripped before matching so `"$L"`, `$L` and `${L}` all
# compare equal; a single-quoted '$L' is a literal and does not interpolate, so
# it is left alone (it is stripped here too, which can only over-report a line
# that names the same token twice).
#
# A heredoc body is DATA, not a shell line. This file quotes the incident driver
# verbatim in its own self-test, and a matcher that cannot tell data from code
# fires on its own fixture and reddens the gate of record. Skipping a data body
# is the fix that keeps this file fully scanned for real code (issue #488).
self_append_lines() {
  awk -v q="'" '
    # A heredoc opener (`<<EOS`, `<<-EOS`, `<<EOS`) starts a data body; the body
    # runs until a line equal to the delimiter. Quotes are stripped from a copy
    # used only for this detection, so the predicate below is unchanged.
    function heredoc_open(line,   m) {
      if (!match(line, /<<-?[A-Za-z_][A-Za-z0-9_]*/)) return ""
      m = substr(line, RSTART, RLENGTH)
      sub(/^<<-?/, "", m)
      return m
    }
    {
      raw = $0
      s = $0
      gsub(/{/, "", s); gsub(/}/, "", s); gsub(/"/, "", s)
      sub(/^[ \t]+/, "", s)

      hs = s
      gsub(q, "", hs)

      if (heredoc != "") {
        if (hs == heredoc) heredoc = ""
        next
      }
      h = heredoc_open(hs)
      if (h != "") { heredoc = h; next }

      if (s ~ /^#/) next
      i = index(s, ">>")
      if (i == 0) next
      rest = substr(s, i + 2)
      sub(/^[ \t]+/, "", rest)
      if (!match(rest, /^[^ \t;|&<>]+/)) next
      tok = substr(rest, 1, RLENGTH)
      if (tok !~ /^\$/ && tok !~ /\//) next
      before = substr(s, 1, i - 1)
      if (index(before, tok) > 0) printf "%d\t%s\n", NR, raw
    }
  ' "$1" 2>/dev/null
}

# unbounded_capture <file> — appends to a variable-named log with no size cap
# anywhere in the file. Advisory only: a cap is a judgement call.
unbounded_capture() {
  grep -qE '>>[[:space:]]*"?[$]?[{]?[A-Za-z_]' "$1" 2>/dev/null || return 1
  grep -qE 'head -c|tail -c|truncate|MAX_BYTES|logrotate|dd if=|split ' "$1" 2>/dev/null && return 1
  return 0
}

# tracked_shell_files — every tracked *.sh outside vendor/ and .research/.
tracked_shell_files() {
  git ls-files '*.sh' 2>/dev/null | grep -vE '^(vendor|\.research)/' || true
}

# expand_lint_paths <path...> — a directory expands to its *.sh, a file is used.
expand_lint_paths() {
  local p
  for p in "$@"; do
    if [ -d "$p" ]; then
      find "$p" -maxdepth 1 -type f -name '*.sh' 2>/dev/null | LC_ALL=C sort
    elif [ -f "$p" ]; then
      printf '%s\n' "$p"
    fi
  done
}

# ── detector 1: the self-appending log ───────────────────────────────────────
lint_paths() {
  local files=0 hits=0 notes=0 n line f
  local -a unbounded=()
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    files=$((files + 1))
    while IFS=$'\t' read -r n line; do
      [ -n "${n:-}" ] || continue
      printf '  FAIL  SCRATCH-SELF-APPEND %s:%s: %s\n' "$f" "$n" "$line"
      hits=$((hits + 1))
    done < <(self_append_lines "$f")
    if unbounded_capture "$f"; then
      notes=$((notes + 1))
      if [ "${#unbounded[@]}" -lt 3 ]; then unbounded+=("$f"); fi
    fi
  done < <(expand_lint_paths "$@")

  printf '  lint: %s file(s) scanned\n' "$files"
  if [ "$hits" -ne 0 ]; then
    printf '  FAIL  %s self-appending line(s) across %s file(s)\n' "$hits" "$files" >&2
  else
    printf '  ok    no SCRATCH-SELF-APPEND in %s file(s)\n' "$files"
  fi
  if [ "$notes" -ne 0 ]; then
    printf '  NOTE  %s file(s) append to a log with no visible size cap (advisory): %s\n' \
      "$notes" "${unbounded[*]}"
  fi
  [ "$hits" -eq 0 ] || return 1
  return 0
}

# ── detector 2: the scratch root ─────────────────────────────────────────────
# df_pct <root> — "used% inodes% fs mount" from df, or from the SG_DF fixture.
df_pct() {
  local line
  if [ -n "$DF_FIXTURE" ]; then
    [ -f "$DF_FIXTURE" ] || return 1
    line="$(awk 'NR==2' "$DF_FIXTURE" 2>/dev/null)"
  else
    line="$(df -P "$1" 2>/dev/null | awk 'NR==2')"
  fi
  [ -n "$line" ] || return 1
  printf '%s\n' "$line" | awk '{gsub(/%/, "", $5); print $5, $6}'
}

df_inode_pct() {
  local line
  if [ -n "$DF_FIXTURE" ]; then
    [ -f "$DF_FIXTURE" ] || return 1
    line="$(awk 'NR==2' "$DF_FIXTURE" 2>/dev/null)"
  else
    line="$(df -iP "$1" 2>/dev/null | awk 'NR==2')"
  fi
  [ -n "$line" ] || return 1
  printf '%s\n' "$line" | awk '{gsub(/%/, "", $5); print $5}'
}

# scan_root <root> <advisory:0|1> -- findings are printed either way, but only a
# non-advisory scan fails: the gate must not redden on the machine's state.
scan_root() {
  local r="$1" advisory="$2" rc=0 mb f pct="" ipct="" fs="" line wt p="  FAIL  "
  local -a wts=()
  if [ ! -d "$r" ]; then
    if [ "$advisory" = 1 ]; then
      printf '    note  scratch root is not a directory: %s\n' "$r"
      return 0
    fi
    cannot_assess "scratch root is not a directory: $r"
  fi
  [ "$advisory" = 1 ] && p="    note  "

  while IFS= read -r f; do
    [ -n "$f" ] || continue
    mb=$(( $(file_size "$f") / 1048576 ))
    printf '%sSCRATCH-FILE-OVERSIZE: %s MB single scratch file (cap %s MB): %s\n' \
      "$p" "$mb" "$MAX_FILE_MB" "$f"
    rc=1
  done < <(find "$r" -maxdepth 1 -type f -size "+${MAX_FILE_MB}M" 2>/dev/null | LC_ALL=C sort)

  if line="$(df_pct "$r")"; then
    pct="${line%% *}"
    fs="${line##* }"
    if [ "${pct:-}" -ge "$FAIL_PCT" ] 2>/dev/null; then
      printf '%sSCRATCH-SPACE-NEAR-FULL: %s%% of %s used (emergency ceiling %s%%) -- every writer on this filesystem is about to fail\n' \
        "$p" "$pct" "$fs" "$FAIL_PCT"
      rc=1
    elif [ "${pct:-}" -ge "$MAX_PCT" ] 2>/dev/null; then
      printf '%sSCRATCH-SPACE-NEAR-FULL: %s%% of %s used (ceiling %s%%)\n' \
        "$p" "$pct" "$fs" "$MAX_PCT"
      rc=1
    fi
    if ipct="$(df_inode_pct "$r")" && [ "${ipct:-}" -ge "$FAIL_PCT" ] 2>/dev/null; then
      printf '%sSCRATCH-SPACE-NEAR-FULL: %s%% of %s inodes used (emergency ceiling %s%%)\n' \
        "$p" "$ipct" "$fs" "$FAIL_PCT"
      rc=1
    fi
  fi

  if [ -e "$SCAN_REPO/.git" ]; then
    while IFS= read -r line; do
      case "$line" in
        "worktree "*)
          wt="${line#worktree }"
          case "$wt" in "$r"/*) wts+=("$wt") ;; esac
          ;;
      esac
    done < <(git -C "$SCAN_REPO" worktree list --porcelain 2>/dev/null)
  fi
  if [ "${#wts[@]}" -gt 0 ]; then
    printf '%sSCRATCH-TMP-WORKTREE: %s registered git worktree(s) on %s -- costs RAM and inodes and vanishes on reboot; move them to disk and remove with: git worktree remove <path>\n' \
      "$p" "${#wts[@]}" "$r"
    rc=1
  fi

  if [ "$advisory" = 1 ]; then
    printf '    note  %s: %s%% used, %s worktree(s) on it\n' \
      "$r" "${pct:-unknown}" "${#wts[@]}"
    return 0
  fi
  return "$rc"
}

# ── detector 3: the copy that never landed ───────────────────────────────────
# verify_copy <src> <dst> <remove-on-refusal:0|1>
verify_copy() {
  local src="$1" dst="$2" remove="$3" ssize dsize asha bsha
  if [ -z "$src" ] || [ -z "$dst" ]; then
    cannot_assess "usage: --copy SRC DST / --verify-copy SRC DST"
  fi
  ssize="$(file_size "$src")"
  [ -n "$ssize" ] || cannot_assess "source does not exist: $src"
  dsize="$(file_size "$dst")"

  if [ -z "$dsize" ]; then
    printf '  FAIL  SCRATCH-COPY-MISSING: the copy produced no file: %s (source %s, %s bytes)\n' \
      "$dst" "$src" "$ssize"
    return 1
  fi
  if [ "$ssize" -eq 0 ]; then
    printf '  note  source is empty (0 bytes); an empty copy is correct: %s\n' "$src"
    return 0
  fi
  if [ "$dsize" -eq 0 ]; then
    printf '  FAIL  SCRATCH-EMPTY-COPY: the copy wrote 0 bytes over a %s-byte source: %s\n' \
      "$ssize" "$dst"
    if [ "$remove" = 1 ]; then
      rm -f -- "$dst"
      printf '    note  removed the 0-byte destination: a restore from it is how the 14.8 GB incident truncated a source file to empty\n'
    fi
    return 1
  fi
  if [ "$dsize" -ne "$ssize" ]; then
    printf '  FAIL  SCRATCH-SHORT-COPY: the copy landed %s bytes of a %s-byte source: %s\n' \
      "$dsize" "$ssize" "$dst"
    [ "$remove" = 1 ] && rm -f -- "$dst"
    return 1
  fi
  if [ "$ssize" -le 8388608 ]; then
    asha="$(sha256sum -- "$src" 2>/dev/null | awk '{print $1}')"
    bsha="$(sha256sum -- "$dst" 2>/dev/null | awk '{print $1}')"
    if [ -n "$asha" ] && [ "$asha" != "$bsha" ]; then
      printf '  FAIL  SCRATCH-COPY-MISMATCH: %s has different content from %s (same %s bytes)\n' \
        "$dst" "$src" "$ssize"
      [ "$remove" = 1 ] && rm -f -- "$dst"
      return 1
    fi
  fi
  printf '  ok    verified copy: %s (%s bytes, matches %s)\n' "$dst" "$dsize" "$src"
  return 0
}

do_copy() {
  local src="$1" dst="$2" cp_out cp_rc=0 vrc=0
  [ -n "$src" ] && [ -n "$dst" ] || cannot_assess "usage: --copy SRC DST"
  [ -f "$src" ] || cannot_assess "source does not exist: $src"
  cp_out="$(cp -- "$src" "$dst" 2>&1)"
  cp_rc=$?
  if [ -n "$cp_out" ]; then printf '    cp: %s\n' "$cp_out"; fi
  if [ "$cp_rc" -ne 0 ]; then
    printf '  FAIL  SCRATCH-COPY-FAILED: cp exited %s for %s -> %s\n' "$cp_rc" "$src" "$dst"
  fi
  verify_copy "$src" "$dst" 1 || vrc=1
  [ "$cp_rc" -eq 0 ] && [ "$vrc" -eq 0 ] && return 0
  return 1
}

# ── self-test: prove every detector CAN fire (a gate that cannot fail is decor)
selftest_tmp=""
trap '[ -n "$selftest_tmp" ] && rm -rf -- "$selftest_tmp"' EXIT

expect() { # expect <want: fire|quiet> <label> <needle> <text>
  local want="$1" label="$2" needle="$3" text="$4"
  case "$text" in
    *"$needle"*)
      if [ "$want" = fire ]; then
        printf '    ok    %s\n' "$label"
        return 0
      fi
      printf '    FAIL  %s -- it must NOT refuse (%s)\n' "$label" "$needle" >&2
      return 1
      ;;
  esac
  if [ "$want" = fire ]; then
    printf '    FAIL  %s -- expected the refusal to name %s\n' "$label" "$needle" >&2
    return 1
  fi
  printf '    ok    %s\n' "$label"
  return 0
}

run_self_test() {
  local tmp out rc=0
  # Scratch: the sanctioned fleet idiom (a bare mktemp X-run trips the repo's own
  # unfinished-marker scan, scripts/check-docs.sh -- the same trap
  # scripts/check-rollup.sh and scripts/check-paperclip-diagrams.sh document).
  tmp="/tmp/check-scratch-safety.selftest.$(date +%s%N).$$"
  if ! mkdir "$tmp" 2>/dev/null; then
    cannot_assess "cannot create a scratch dir at $tmp"
  fi
  selftest_tmp="$tmp"

  printf '  self-test: constructing each violation and requiring the refusal\n'

  # 1. SCRATCH-SELF-APPEND -- the incident's own driver, verbatim.
  cat > "$tmp/driver.sh" <<'EOS'
#!/usr/bin/env bash
L=/tmp/ao412.makeverify.log
: > "$L"
env -C "$W" make verify >> "$L" 2>&1
tail -6 "$L" >> "$L"
EOS
  out="$(env SG_ROOT="$tmp" "$self" --lint "$tmp" 2>&1)"
  expect fire "SCRATCH-SELF-APPEND fires on the incident's driver, naming the line" \
    "FAIL  SCRATCH-SELF-APPEND" "$out" || rc=1
  case "$out" in *"driver.sh:5"*) ;; *) printf '    FAIL  the refusal does not name driver.sh:5\n' >&2; rc=1 ;; esac
  rm -f "$tmp/driver.sh"

  # 1b. ...and a correct driver must not be refused (no false green either way).
  printf '#!/usr/bin/env bash\nL=/tmp/y.log\n: > "$L"\nenv make verify > "$L" 2>&1\ntail -6 "$L"\n' \
    > "$tmp/clean.sh"
  out="$(env SG_ROOT="$tmp" "$self" --lint "$tmp" 2>&1)"
  expect quiet "a correct driver is not refused" "FAIL  SCRATCH-SELF-APPEND" "$out" || rc=1
  rm -f "$tmp/clean.sh"

  # 1c. ...and a driver that appears only as DATA -- quoted inside a heredoc -- is
  # not refused. A heredoc body is not a shell line, and a matcher that cannot
  # tell data from code fires on its own fixture: this guard did exactly that to
  # itself, failing its own lint and the gate of record (issue #488).
  cat > "$tmp/quoted.sh" <<'EOS'
cat > /tmp/inner.sh <<'EOS2'
L=/tmp/z.log
tail -6 "$L" >> "$L"
EOS2
EOS
  out="$(env SG_ROOT="$tmp" "$self" --lint "$tmp" 2>&1)"
  expect quiet "a driver quoted as DATA in a heredoc is not refused" \
    "FAIL  SCRATCH-SELF-APPEND" "$out" || rc=1
  rm -f "$tmp/quoted.sh"

  # 2. SCRATCH-FILE-OVERSIZE -- sparse, so the control costs no space.
  truncate -s 2M "$tmp/bigfile" 2>/dev/null || dd if=/dev/zero of="$tmp/bigfile" bs=1M count=2 status=none
  out="$(env SG_MAX_FILE_MB=1 SG_ROOT="$tmp" "$self" --scan "$tmp" 2>&1)"
  expect fire "SCRATCH-FILE-OVERSIZE fires above the cap" \
    "FAIL  SCRATCH-FILE-OVERSIZE" "$out" || rc=1
  rm -f "$tmp/bigfile"

  # 3. SCRATCH-SPACE-NEAR-FULL -- the measurement seam: a df-shaped fixture at
  #    96% (the incident's condition). The live path parses real df output with
  #    this same code, and runs on every gate invocation.
  printf 'Filesystem 1024-blocks Used Available Capacity Mounted on\ntmpfs 16777216 16106127 671089 96%% /tmp\n' \
    > "$tmp/df.full"
  out="$(env SG_DF="$tmp/df.full" SG_ROOT="$tmp" "$self" --scan "$tmp" 2>&1)"
  expect fire "SCRATCH-SPACE-NEAR-FULL fires at 96% of the tmpfs" \
    "FAIL  SCRATCH-SPACE-NEAR-FULL" "$out" || rc=1
  printf 'Filesystem 1024-blocks Used Available Capacity Mounted on\ntmpfs 16777216 1677722 15104894 10%% /tmp\n' \
    > "$tmp/df.ok"
  out="$(env SG_DF="$tmp/df.ok" SG_ROOT="$tmp" "$self" --scan "$tmp" 2>&1)"
  expect quiet "a 10%-used tmpfs is not refused" "FAIL  SCRATCH-SPACE-NEAR-FULL" "$out" || rc=1
  rm -f "$tmp/df.full" "$tmp/df.ok"

  # 4. SCRATCH-EMPTY-COPY -- a REAL reproduction, not a synthetic state: with a
  #    write-size limit the copy cannot land its bytes and leaves an empty file,
  #    which is exactly the post-state the incident's `cp` produced on a full
  #    tmpfs. The guard must refuse it BY NAME and remove the 0-byte artifact.
  head -c 4096 /dev/zero > "$tmp/src.bin"
  out="$(env SG_ROOT="$tmp" bash -c 'ulimit -c 0; ulimit -f 0; exec "$0" --copy "$1" "$2"' \
    "$self" "$tmp/src.bin" "$tmp/dst.ok" 2>&1)"
  expect fire "SCRATCH-EMPTY-COPY fires when the copy cannot land its bytes" \
    "FAIL  SCRATCH-EMPTY-COPY" "$out" || rc=1
  if [ -e "$tmp/dst.ok" ]; then
    printf '    FAIL  the 0-byte destination survived -- a restore from it would truncate the source\n' >&2
    rc=1
  else
    printf '    ok    the 0-byte destination was removed\n'
  fi

  # 4b. a copy that does land must be verified, not refused.
  rm -f "$tmp/dst.ok"
  out="$(env SG_ROOT="$tmp" "$self" --copy "$tmp/src.bin" "$tmp/dst.ok" 2>&1)" || {
    printf '    FAIL  a correct copy was refused: %s\n' "$out" >&2; rc=1; }
  expect quiet "a copy that lands is accepted" "FAIL" "$out" || rc=1
  if [ "$(file_size "$tmp/dst.ok")" = "4096" ]; then
    printf '    ok    the accepted copy carries all 4096 bytes\n'
  else
    printf '    FAIL  the accepted copy is not the source (%s bytes)\n' "$(file_size "$tmp/dst.ok")" >&2
    rc=1
  fi
  rm -f "$tmp/dst.ok" "$tmp/src.bin"

  # 5. SCRATCH-TMP-WORKTREE -- a worktree registered inside the scratch root.
  mkdir -p "$tmp/repo" || true
  if git init -q "$tmp/repo" 2>/dev/null; then
    ( cd "$tmp/repo" && git config user.email t@example.invalid && git config user.name t \
      && git commit -qm seed --allow-empty 2>/dev/null ) || true
    git -C "$tmp/repo" worktree add -q --detach "$tmp/repo/lane" 2>/dev/null || true
    out="$(env SG_REPO="$tmp/repo" SG_ROOT="$tmp" "$self" --scan "$tmp" 2>&1)"
    expect fire "SCRATCH-TMP-WORKTREE fires on a worktree inside the scratch root" \
      "FAIL  SCRATCH-TMP-WORKTREE" "$out" || rc=1
    git -C "$tmp/repo" worktree remove --force "$tmp/repo/lane" 2>/dev/null || true
  fi
  rm -rf "$tmp/repo"

  if [ "$rc" -ne 0 ]; then
    printf '  self-test: FAIL -- a detector did not fire (or fired wrongly)\n' >&2
    return 1
  fi
  printf '  self-test: OK -- every detector refuses by name, and none over-fires\n'
  return 0
}

# ── modes ────────────────────────────────────────────────────────────────────
case "$MODE" in
  self-test)
    run_self_test
    ;;

  lint)
    if [ "${#LINT_PATHS[@]}" -eq 0 ]; then
      if ! git rev-parse --git-dir >/dev/null 2>&1; then
        cannot_assess "not inside a git worktree, and no path was given to --lint"
      fi
      while IFS= read -r f; do
        [ -n "$f" ] && LINT_PATHS+=("$f")
      done < <(tracked_shell_files)
      if [ "${#LINT_PATHS[@]}" -eq 0 ]; then
        cannot_assess "no tracked *.sh files to lint"
      fi
    fi
    lint_paths "${LINT_PATHS[@]}"
    ;;

  scan)
    scan_root "$SCAN_ROOT" 0
    ;;

  verify-copy)
    verify_copy "$COPY_SRC" "$COPY_DST" 0
    ;;

  copy)
    do_copy "$COPY_SRC" "$COPY_DST"
    ;;

  gate)
    fail=0
    echo "== scratch-safety: the guard's own detectors =="
    run_self_test || fail=1

    echo "== scratch-safety: the repo's tracked tooling =="
    if ! git rev-parse --git-dir >/dev/null 2>&1; then
      cannot_assess "not inside a git worktree (cannot attest a commit)"
    fi
    while IFS= read -r f; do
      [ -n "$f" ] && LINT_PATHS+=("$f")
    done < <(tracked_shell_files)
    if [ "${#LINT_PATHS[@]}" -eq 0 ]; then
      cannot_assess "no tracked *.sh files to lint"
    fi
    lint_paths "${LINT_PATHS[@]}" || fail=1

    echo "== scratch-safety: the machine, ADVISORY (not gating; the machine guard owns this verdict) =="
    scan_root "$SCAN_ROOT" 1
    if [ -x "$GUARD_BIN" ]; then
      guard_out="$("$GUARD_BIN" --check 2>&1)"
      guard_rc=$?
      printf '    note  %s --check exited %s\n' "$GUARD_BIN" "$guard_rc"
      printf '%s\n' "$guard_out" | sed 's/^/          /'
    else
      printf '    note  machine guard not installed at %s (see %s)\n' "$GUARD_BIN" "$DOC_REF"
    fi

    echo ""
    if [ "$fail" -ne 0 ]; then
      echo "check-scratch-safety: FAIL -- a scratch-safety detector refused" >&2
      exit 1
    fi
    echo "check-scratch-safety: OK -- detectors proven, no self-appending log in the tracked tooling"
    echo "  the live machine verdict above is advisory; run --scan to have it refused by name"
    ;;

  *) echo "check-scratch-safety: unknown mode: $MODE" >&2; exit 2 ;;
esac
