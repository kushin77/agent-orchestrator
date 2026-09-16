#!/usr/bin/env bash
# check-gitignore.sh — runtime-state ignore coverage is ENFORCED, not
# conventional (issue #625).
#
# THE DEFECT THIS EXISTS FOR
#   Runtime state under `.board/`, `.fleet/` and `.verify/` is kept out of git
#   by a `.gitignore` that a human is trusted to keep correct. Nothing checked
#   it. So the coverage was a convention: a state directory could lose its rule,
#   or state could be written to a path no rule ever covered, and the only thing
#   between that state and a `git add -A` commit was somebody noticing. Committing
#   it moves a machine's live claim ledger / heartbeats / attestations into
#   permanent history — the diff becomes a moving dispatch signal, and every
#   later reader treats it as evidence.
#
# WHAT IS CHECKED (four checks, each able to fail on its own)
#   1. COVERAGE — every path on the gate's declared runtime-state list is ignored
#      by the tracked `.gitignore`. The list is declared HERE, not read back out
#      of `.gitignore`, so deleting a rule fails this gate by name instead of
#      shrinking the list along with itself. It has two halves: `state_dirs`
#      (directories of runtime state) and `state_files` (individual runtime-state
#      files, which a directory probe cannot speak for). A match contributed by
#      `.git/info/exclude` or a global excludes file does NOT count: only the
#      committed `.gitignore` is reviewable, so only it can be the answer.
#   2. ENFORCEMENT — no untracked, unignored path may exist under a declared
#      runtime-state root. This is the half that catches state written to a path
#      no rule covers, which is why the roots are declared wider than the
#      covered list. Each offender is named; that path is what `git add -A`
#      would commit.
#   3. HISTORY — a root declared wholly generated may hold no tracked file at
#      all. A tracked file under `.verify/` is runtime state already committed.
#   4. NOT-HIDDEN — no TRACKED file may be matched by an ignore rule. A rule that
#      reaches committed history is the realistic regression of widening the state
#      coverage at all, and `.board/` is where it bites: this repo tracks
#      `.board/claims.jsonl`, `.board/snapshot.json`, `.board/focus.json` and
#      `.board/boundary-snapshot.json`, so a blanket `.board/` rule — or a glob
#      like `.board/claims*` — hides four committed files at once.
#      MEASURED, and stated because the intuitive opposite is wrong: dropping the
#      trailing slash from `.board/claims/` does NOT hide `claims.jsonl`.
#      `.board/claims` and `.board/claims/` both leave it alone, because the
#      pattern names `claims`, not `claims.jsonl`. The trailing slash is chosen
#      for precision (it confines the rule to a directory) — it is not what
#      protects the ledger. This check is.
#      It asks `git ls-files -c -i --exclude-standard`, NOT `git check-ignore`,
#      because `check-ignore` declines to report an already-tracked path at all:
#      the wrong tool would make this check vacuously green on the one input it
#      exists for.
#
# MEASURED CONSEQUENCE — and the gap this paragraph used to describe (measured
# 2026-09-15 at `master` @ 84afa90, worktree ao-625-08cee8aa; CLOSED by #848)
#   Tracked code wrote runtime state to `.board/claims/`, `.fleet/waves/` and
#   `.fleet/board-reports.json`, and `.gitignore` covered none of the three. This
#   paragraph used to declare the three deliberately NOT on the coverage list.
#   That was honest at the time — a gate that declares a rule required while the
#   rule is absent can never be green — but it left the debt in prose, and prose
#   is not a gate. The cost was measured: every lane that followed `AGENTS.md`
#   (claim the issue, then run the gate) was red on check 2 for a file the repo's
#   own tracked writer had just created, and each paid a diagnosis cycle.
#
#   Issue #848 paid the debt. `.gitignore` covers all three, and every entry
#   below was MEASURED as written by tracked code before it was declared here —
#   a path is on this list because a named tracked module writes it, never
#   because a lane wanted it covered:
#
#     .board/claims/             governance/dispatch/claims.py (DEFAULT_CLAIMS_DIR)
#     .fleet/waves/              fleet/brain.py (WAVES); read by fleet/monitor.py
#     .fleet/attempts/           fleet/runaway.py (the AO-GR-21 attempt counter)
#     .fleet/dead-letter/        fleet/runaway.py (the terminal artifact)
#     .fleet/board-reports.json  governance/lifecycle/report.py (BoardReporter)
#     .fleet/runner-hold.json    fleet/terminal.py (#733 preflight hold)
#     .fleet/reconcile.log       fleet/cron.py (RECONCILE_LOG)
#     .fleet/queue-liveness.json fleet/monitor.py (#695, its one write)
#     .fleet/paused              fleet/terminal.py (the operator pause flag)
#
#   Deliberately NOT declared, and why: `.fleet/open-eye.sh`, `.fleet/ping.*`,
#   `.fleet/poll.*` and `.fleet/operator-poll.*`. No tracked module writes them;
#   `fleet/monitor.py` names `open-eye.sh` as the ad-hoc script its TRACKED rung
#   replaced. They are legacy operator scratch, and check 2 is right to name them
#   as debris in a tree that carries them — the remedy is deletion, not a rule
#   that would make stale scratch a permanent part of the ignore contract.
#
#   Check 2 still means this gate is green only on a tree whose runtime state is
#   actually ignored: a lane worktree cut from `master` is clean, and — since
#   #848 — so is one that has taken a claim.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS. No network access.
#   CANNOT-ASSESS (2) is reserved for an input that cannot be read at all — no
#   git, a root that is not a directory, a root that is not a git work tree, or
#   a `git ls-files` that fails. A MISSING `.gitignore` is NOT CANNOT-ASSESS: the
#   property under test is definitively false when there is no `.gitignore`, so
#   that is NOT-OK. No branch of this script returns 2 for "could not tell".
#
# WIRED INTO `make verify` (issue #630): `scripts/verify.sh` discovers every
# `scripts/check-*.sh` through `scripts/discover-checks.sh` and appends it, so
# this gate runs as the `gitignore` check with no hand-edit to the `checks=()`
# array. `verify.sh` reads the tri-state contract below: 1 fails the run, 2 is
# recorded as SKIP and named in the attestation.
#
# Usage: bash scripts/check-gitignore.sh
#   AO_GITIGNORE_ROOT=<dir> assesses <dir> instead of the tree this script lives
#   in. It exists so the coverage half can be provoked against a COPY; the
#   override is announced on stderr and echoed in the verdict, so a leaked value
#   cannot silently redirect the assessment.
set -u

# --- the declaration -------------------------------------------------------
# Directories whose contents are runtime state and must therefore be ignored by
# the tracked `.gitignore`. Every entry is here because a NAMED tracked module
# writes it (writer -> path in the header); the list is the gate's own
# declaration, so it does not move when `.gitignore` does.
state_dirs=(
  .verify
  .board/locks
  .board/claims
  .fleet/inbox
  .fleet/outbox
  .fleet/sent
  .fleet/done
  .fleet/brain
  .fleet/runs
  .fleet/reported
  .fleet/lanes
  .fleet/lifecycle
  .fleet/sessions
  .fleet/watchdog
  .fleet/waves
  .fleet/attempts
  .fleet/dead-letter
)

# Individual runtime-state FILES. A separate list because a directory probe
# cannot speak for them: `git check-ignore` on `<file>/ao-ignore-probe` requires
# the path to be a directory, so a file-shaped rule looks uncovered under the
# `state_dirs` loop and would quietly drop out of the declaration. Same
# discipline as `state_dirs`: every entry has a named tracked writer, and none
# of them is tracked history that a rule must not hide.
state_files=(
  .fleet/board-reports.json
  .fleet/runner-hold.json
  .fleet/reconcile.log
  .fleet/queue-liveness.json
  .fleet/paused
)

# Roots under which no untracked, unignored path may exist. Wider than
# `state_dirs` on purpose: this is the net for state written outside the
# declared list.
runtime_roots=(.board .fleet .verify)

# Roots whose entire content is generated, so a tracked file under one is
# runtime state that already reached history.
wholly_generated=(.verify)

script_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
root="$script_root"
overridden=0
if [ -n "${AO_GITIGNORE_ROOT:-}" ]; then
  root="$AO_GITIGNORE_ROOT"
  overridden=1
fi

if ! command -v git >/dev/null 2>&1; then
  echo "check-gitignore: CANNOT-ASSESS — git not found" >&2
  exit 2
fi
if [ ! -d "$root" ]; then
  echo "check-gitignore: CANNOT-ASSESS — root is not a directory: $root" >&2
  exit 2
fi
if [ "$(git -C "$root" rev-parse --is-inside-work-tree 2>/dev/null)" != "true" ]; then
  echo "check-gitignore: CANNOT-ASSESS — root is not a git work tree: $root" >&2
  exit 2
fi

if [ "$overridden" = "1" ]; then
  printf 'check-gitignore: NOTE — root overridden by AO_GITIGNORE_ROOT: %s\n' "$root" >&2
fi

fail=0
covered=0
declared_total=$(( ${#state_dirs[@]} + ${#state_files[@]} ))

# --- 1. coverage -----------------------------------------------------------
echo "== 1. declared runtime-state paths are ignored by .gitignore =="
if [ ! -f "$root/.gitignore" ]; then
  printf '  FAIL  .gitignore (absent — none of the %s declared runtime-state paths is covered)\n' \
    "$declared_total" >&2
  fail=$((fail + 1))
else
  # Git matches an ignore pattern against a path without the path existing, so
  # each probe asks the real matcher instead of re-implementing `.gitignore`
  # semantics here. A directory is probed THROUGH a child path, so a rule is
  # exercised the way a file actually written under it would be.
  for dir in "${state_dirs[@]}"; do
    probe="$dir/ao-ignore-probe"
    match=""
    if match="$(git -C "$root" check-ignore -v --no-index -- "$probe" 2>/dev/null | head -n 1)" &&
      [ -n "$match" ]; then
      source_path="${match%%:*}"
      remainder="${match#*:}"
      lineno="${remainder%%:*}"
      pattern="${remainder#*:}"
      pattern="${pattern%%$'\t'*}"
      case "$source_path" in
        .gitignore | */.gitignore)
          printf '  OK    %s ignored by %s:%s (%s)\n' "$dir/" "$source_path" "$lineno" "$pattern"
          covered=$((covered + 1))
          ;;
        *)
          printf '  FAIL  %s ignored only by %s, not by the tracked .gitignore\n' \
            "$dir/" "$source_path" >&2
          fail=$((fail + 1))
          ;;
      esac
    else
      printf '  FAIL  %s declared runtime state, not ignored by .gitignore\n' "$dir/" >&2
      fail=$((fail + 1))
    fi
  done
  # Files are probed directly: a directory rule cannot speak for them, and
  # `.fleet/board-reports.json` is the case that proved it (issue #848).
  for file in "${state_files[@]}"; do
    match=""
    if match="$(git -C "$root" check-ignore -v --no-index -- "$file" 2>/dev/null | head -n 1)" &&
      [ -n "$match" ]; then
      source_path="${match%%:*}"
      remainder="${match#*:}"
      lineno="${remainder%%:*}"
      pattern="${remainder#*:}"
      pattern="${pattern%%$'\t'*}"
      case "$source_path" in
        .gitignore | */.gitignore)
          printf '  OK    %s ignored by %s:%s (%s)\n' "$file" "$source_path" "$lineno" "$pattern"
          covered=$((covered + 1))
          ;;
        *)
          printf '  FAIL  %s ignored only by %s, not by the tracked .gitignore\n' \
            "$file" "$source_path" >&2
          fail=$((fail + 1))
          ;;
      esac
    else
      printf '  FAIL  %s declared runtime state, not ignored by .gitignore\n' "$file" >&2
      fail=$((fail + 1))
    fi
  done
fi

# --- 2. enforcement --------------------------------------------------------
echo
echo "== 2. no untracked, unignored path under a runtime-state root =="
for candidate in "${runtime_roots[@]}"; do
  listing="$(git -C "$root" ls-files --others --exclude-standard -- "$candidate" 2>/dev/null)"
  rc=$?
  if [ "$rc" -ne 0 ]; then
    echo "check-gitignore: CANNOT-ASSESS — git ls-files failed for $candidate (rc=$rc)" >&2
    exit 2
  fi
  if [ -z "$listing" ]; then
    printf '  OK    %s holds no untracked, unignored path\n' "$candidate/"
  else
    while IFS= read -r offender; do
      [ -n "$offender" ] || continue
      printf '  FAIL  %s (untracked and not ignored — a git add would carry this runtime state into history)\n' \
        "$offender" >&2
      fail=$((fail + 1))
    done <<<"$listing"
  fi
done

# --- 3. history ------------------------------------------------------------
echo
echo "== 3. a wholly generated root holds no tracked file =="
for candidate in "${wholly_generated[@]}"; do
  tracked="$(git -C "$root" ls-files -- "$candidate" 2>/dev/null)"
  rc=$?
  if [ "$rc" -ne 0 ]; then
    echo "check-gitignore: CANNOT-ASSESS — git ls-files failed for $candidate (rc=$rc)" >&2
    exit 2
  fi
  if [ -z "$tracked" ]; then
    printf '  OK    %s holds 0 tracked file(s)\n' "$candidate/"
  else
    while IFS= read -r offender; do
      [ -n "$offender" ] || continue
      printf '  FAIL  %s (tracked under a wholly generated root — runtime state already in history)\n' \
        "$offender" >&2
      fail=$((fail + 1))
    done <<<"$tracked"
  fi
done

# --- 4. no tracked file is hidden ------------------------------------------
echo
echo "== 4. no tracked file is matched by an ignore rule =="
ignored_tracked="$(git -C "$root" ls-files -c -i --exclude-standard 2>/dev/null)"
rc=$?
if [ "$rc" -ne 0 ]; then
  echo "check-gitignore: CANNOT-ASSESS — git ls-files -i failed (rc=$rc)" >&2
  exit 2
fi
if [ -z "$ignored_tracked" ]; then
  printf '  OK    0 tracked file(s) are matched by an ignore rule\n'
else
  while IFS= read -r offender; do
    [ -n "$offender" ] || continue
    printf '  FAIL  %s (tracked AND ignored — an ignore rule reaches a committed file, so it is hidden from review rather than carried by it; a blanket `.board/` or a glob `.board/claims*` is the shape that does this)\n' \
      "$offender" >&2
    fail=$((fail + 1))
  done <<<"$ignored_tracked"
fi

# --- verdict ---------------------------------------------------------------
echo
if [ "$fail" -ne 0 ]; then
  printf 'check-gitignore: FAIL — %s problem(s); runtime state under %s is not safely ignored\n' \
    "$fail" "${runtime_roots[*]}" >&2
  exit 1
fi
if [ "$overridden" = "1" ]; then
  printf 'check-gitignore: OK — %s/%s declared runtime-state paths ignored, no untracked unignored path under %s, no tracked file ignored (root overridden: %s)\n' \
    "$covered" "$declared_total" "${runtime_roots[*]}" "$root"
else
  printf 'check-gitignore: OK — %s/%s declared runtime-state paths ignored, no untracked unignored path under %s, no tracked file ignored\n' \
    "$covered" "$declared_total" "${runtime_roots[*]}"
fi
exit 0
