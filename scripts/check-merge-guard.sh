#!/usr/bin/env bash
# check-merge-guard.sh — prove the lane merge path is GUARDED, offline and
# without merging anything (issue #1145, parent #878).
#
# THE DEFECT THIS EXISTS FOR
#   A trailer-less squash merge reds `check-isolation-landed` on master's own tip
#   and therefore reds `make verify` for EVERY lane. The guard that renders the
#   composed squash message (`scripts/check-squash-message.sh`) was consulted by
#   `scripts/pr-queue.sh`, `governance/lifecycle`'s close-out and
#   `governance/landing` -- but the agent-facing INSTRUCTION told every spawned
#   lane to run `gh pr merge <n> --squash --delete-branch` directly, and that raw
#   path consulted nothing. The leaks measured on 2026-09-17 (#1150, #1171,
#   #1185, #1188, #1191) were all direct owner merges: the shape that instruction
#   produces. `scripts/merge-pr.sh` is the guarded entrypoint; this gate proves
#   the refusal is real AND that the producer instructs it.
#
# WHAT IS PROVOKED (no network, no real gh, nothing merged)
#   1. REFUSAL    -- the guard says NOT-OK => `merge-pr.sh --apply` exits 1,
#                    names `squash-message-would-drop-trailer`, and `gh pr merge`
#                    is NEVER invoked. A refusal that still merges is not a
#                    refusal.
#   2. PROCEED    -- the guard says OK => exit 0 and `gh pr merge <n> --squash`
#                    IS invoked, exactly once. A control whose pass and fail
#                    paths collapse into one exit code is a formality (GR-12).
#   3. CANNOT-ASSESS -- the guard reaches no verdict (rc 2) => `merge-pr.sh`
#                    exits 2 and `gh pr merge` is never invoked. An unreadable
#                    guard is an unknown verdict, never a merge.
#   4. THE PRODUCER -- the instruction surfaces that hand a lane its merge
#                    command must name the guarded entrypoint and must NOT carry
#                    a raw merge instruction. Provoked with a plant: the same
#                    predicate, run over a fixture holding a raw instruction,
#                    must REFUSE it by name -- so the rule cannot pass by
#                    matching nothing.
#   5. NO INPUT     -- a missing guard is CANNOT-ASSESS (exit 2), never OK.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-merge-guard.sh
set -uo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

ENTRY="scripts/merge-pr.sh"
GUARD="scripts/check-squash-message.sh"
#: The files whose PROSE hands a lane a merge command. Deliberately literal: a
#: glob would make the surface set grow silently, which is the defect this
#: section exists to catch.
SURFACES="governance/spawn/render.py fleet/terminal.py"

FAILED=0
fail() { printf '  FAIL  %s\n' "$*" >&2; FAILED=$((FAILED + 1)); }
ok() { printf '  ok    %s\n' "$*"; }

TMPD=""
cleanup() { [ -n "$TMPD" ] && rm -rf "$TMPD" || true; }
trap cleanup EXIT
TMPD="$(mktemp -d /tmp/ao1145-merge-guard.XXXXXX)" || {
  echo "check-merge-guard: CANNOT-ASSESS — no scratch directory" >&2
  exit 2
}

echo "== check-merge-guard: the lane merge path is guarded =="

# --- 0. structural ----------------------------------------------------------
for f in "$ENTRY" "$GUARD" $SURFACES; do
  if [ ! -f "$f" ]; then
    echo "check-merge-guard: CANNOT-ASSESS — $f is missing" >&2
    exit 2
  fi
done
ok "the entrypoint, the guard and every instruction surface exist"

# --- fixtures: a scratch copy of the entrypoint + a stub guard + a fake gh ---
fixture() { # fixture <guard-rc> <label> -> prints scratch entry path
  local guard_rc="$1" label="$2"
  local dir="$TMPD/$label"
  mkdir -p "$dir/scripts" "$TMPD/bin"
  cp "$root/$ENTRY" "$dir/scripts/merge-pr.sh"
  {
    printf '#!/usr/bin/env bash\n'
    printf 'echo "check-squash-message: stub verdict %s" >&2\n' "$label"
    printf 'exit %s\n' "$guard_rc"
  } > "$dir/scripts/check-squash-message.sh"
  chmod +x "$dir/scripts/check-squash-message.sh"
  # A fake gh that records the invocation; it must NEVER be reached on a refusal.
  {
    printf '#!/usr/bin/env bash\n'
    printf 'printf "%%s\\n" "$*" >> "$FAKE_GH_CALLS"\n'
    printf 'exit 0\n'
  } > "$TMPD/bin/gh"
  chmod +x "$TMPD/bin/gh"
  printf '%s' "$dir/scripts/merge-pr.sh"
}

run_entry() { # run_entry <entry> <calls-file> [args...]
  local entry="$1" calls="$2"; shift 2
  : > "$calls"
  FAKE_GH_CALLS="$calls" PATH="$TMPD/bin:$PATH" AO_MERGE_APPLY=0 \
    bash "$entry" "$@" > "$TMPD/out.txt" 2>&1
  echo $?
}

# --- 1. REFUSAL: the guard says NOT-OK -> refuse, and never call gh ---------
entry="$(fixture 1 notok)"
rc="$(run_entry "$entry" "$TMPD/calls-notok.txt" --pr 10 --apply)"
if [ "$rc" -eq 1 ]; then
  ok "REFUSAL: merge-pr.sh exits 1 when the guard reports NOT-OK"
else
  fail "REFUSAL: merge-pr.sh exit=$rc, expected 1"
fi
if grep -q 'squash-message-would-drop-trailer' "$TMPD/out.txt"; then
  ok "REFUSAL: the refusal is named squash-message-would-drop-trailer"
else
  fail "REFUSAL: the refusal is not named squash-message-would-drop-trailer"
fi
if [ -s "$TMPD/calls-notok.txt" ]; then
  fail "REFUSAL: gh was invoked although the guard refused: $(tr '\n' ' ' < "$TMPD/calls-notok.txt")"
else
  ok "REFUSAL: gh pr merge was never invoked"
fi

# --- 2. PROCEED: the guard says OK -> merge, exactly once -------------------
entry="$(fixture 0 ok)"
rc="$(run_entry "$entry" "$TMPD/calls-ok.txt" --pr 10 --apply)"
if [ "$rc" -eq 0 ]; then
  ok "PROCEED: merge-pr.sh exits 0 when the guard reports OK"
else
  fail "PROCEED: merge-pr.sh exit=$rc, expected 0"
fi
if grep -q '^pr merge 10 --squash$' "$TMPD/calls-ok.txt"; then
  ok "PROCEED: gh pr merge 10 --squash was invoked"
else
  fail "PROCEED: gh pr merge was not invoked with the expected argv: $(tr '\n' ' ' < "$TMPD/calls-ok.txt")"
fi
if [ "$(wc -l < "$TMPD/calls-ok.txt")" -eq 1 ]; then
  ok "PROCEED: gh was invoked exactly once"
else
  fail "PROCEED: gh was invoked $(wc -l < "$TMPD/calls-ok.txt") times, expected 1"
fi

# --- 3. CANNOT-ASSESS: no verdict -> refuse, never merge --------------------
entry="$(fixture 2 cannot)"
rc="$(run_entry "$entry" "$TMPD/calls-cannot.txt" --pr 10 --apply)"
if [ "$rc" -eq 2 ]; then
  ok "CANNOT-ASSESS: merge-pr.sh exits 2 when the guard reaches no verdict"
else
  fail "CANNOT-ASSESS: merge-pr.sh exit=$rc, expected 2"
fi
if [ -s "$TMPD/calls-cannot.txt" ]; then
  fail "CANNOT-ASSESS: gh was invoked although the guard had no verdict"
else
  ok "CANNOT-ASSESS: gh pr merge was never invoked"
fi

# --- 3b. bad input is CANNOT-ASSESS, never OK -------------------------------
rc="$(run_entry "$entry" "$TMPD/calls-bad.txt" --pr not-a-number --apply)"
if [ "$rc" -eq 2 ]; then
  ok "BAD INPUT: a non-numeric --pr is CANNOT-ASSESS (rc 2)"
else
  fail "BAD INPUT: a non-numeric --pr gave rc=$rc, expected 2"
fi

# --- 3c. missing --pr is CANNOT-ASSESS -------------------------------------
rc="$(run_entry "$entry" "$TMPD/calls-nopr.txt" --apply)"
if [ "$rc" -eq 2 ]; then
  ok "BAD INPUT: a missing --pr is CANNOT-ASSESS (rc 2)"
else
  fail "BAD INPUT: a missing --pr gave rc=$rc, expected 2"
fi

# --- 4. THE PRODUCER: the instruction surfaces hand out the guarded entry ----
# A raw merge instruction is a command line that invokes the GitHub CLI's merge
# subcommand. Written as a regex so this very file does not contain the literal.
raw_merge_res='gh[[:space:]]+pr[[:space:]]+merge'

scan_surfaces() { # scan_surfaces <file...> -> 0 clean / 1 refused (named)
  local f hits=0
  for f in "$@"; do
    [ -f "$f" ] || continue
    hits="$(grep -nE "$raw_merge_res" "$f" 2>/dev/null || true)"
    if [ -n "$hits" ]; then
      printf '  FAIL  raw-merge-instruction:%s\n' "$f" >&2
      printf '%s\n' "$hits" | sed 's/^/        /' >&2
      return 1
    fi
  done
  return 0
}

if scan_surfaces $SURFACES; then
  ok "PRODUCER: no instruction surface hands a lane a raw merge command"
else
  fail "PRODUCER: an instruction surface still hands a lane a raw merge command"
fi

# The guarded entrypoint must actually be NAMED where the instruction lives, or
# a lane is told to run something that does not exist and improvises.
if grep -q 'merge-pr\.sh' governance/spawn/render.py; then
  ok "PRODUCER: governance/spawn/render.py names the guarded entrypoint"
else
  fail "PRODUCER: governance/spawn/render.py does not name scripts/merge-pr.sh"
fi

# NEGATIVE CONTROL: the same predicate must REFUSE a planted raw instruction --
# asserted only on the clean case, the rule would pass a predicate matching
# nothing. The FAIL printed inside this provocation is EXPECTED (it is the
# refusal being proved), which is why it is framed as one.
echo "== provocation: a planted raw merge instruction must be refused (may FAIL) =="
plant="$TMPD/planted-instruction.txt"
printf 'squash-merge it with `gh pr merge <number> --squash --delete-branch`\n' > "$plant"
if scan_surfaces "$plant" 2> "$TMPD/plant.out"; then
  fail "NEGATIVE CONTROL: a planted raw merge instruction was NOT refused"
else
  if grep -q 'raw-merge-instruction' "$TMPD/plant.out"; then
    ok "NEGATIVE CONTROL: a planted raw merge instruction is refused by name"
  else
    fail "NEGATIVE CONTROL: the planted instruction was refused but not by name"
  fi
fi
# ... and a clean file must still pass, so the rule cannot match everything.
clean="$TMPD/clean-instruction.txt"
printf 'merge it with `bash scripts/merge-pr.sh --pr <number> --apply`\n' > "$clean"
if scan_surfaces "$clean" 2> "$TMPD/clean.out"; then
  ok "NEGATIVE CONTROL: a guarded instruction is accepted (the rule is not blanket)"
else
  fail "NEGATIVE CONTROL: a guarded instruction was wrongly refused"
fi

# --- 5. no input: a missing guard is CANNOT-ASSESS, never OK ----------------
rm -f "$TMPD/ok/scripts/check-squash-message.sh"
rc="$(run_entry "$TMPD/ok/scripts/merge-pr.sh" "$TMPD/calls-noguard.txt" --pr 10 --apply)"
if [ "$rc" -eq 2 ]; then
  ok "NO INPUT: a missing guard is CANNOT-ASSESS (rc 2), never a merge"
else
  fail "NO INPUT: a missing guard gave rc=$rc, expected 2"
fi

echo ""
if [ "$FAILED" -eq 0 ]; then
  echo "check-merge-guard: OK — the merge path refuses a trailer-less squash by name, never merges on a refusal or on no verdict, and the instruction surfaces hand out the guarded entrypoint"
  exit 0
fi
echo "check-merge-guard: NOT-OK — $FAILED finding(s)" >&2
exit 1
