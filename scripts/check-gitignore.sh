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
# WHAT IS CHECKED (three checks, each able to fail on its own)
#   1. COVERAGE — every directory on the gate's declared runtime-state list is
#      ignored by the tracked `.gitignore`. The list is declared HERE, not read
#      back out of `.gitignore`, so deleting a rule fails this gate by name
#      instead of shrinking the list along with itself. A match contributed by
#      `.git/info/exclude` or a global excludes file does NOT count: only the
#      committed `.gitignore` is reviewable, so only it can be the answer.
#   2. ENFORCEMENT — no untracked, unignored path may exist under a declared
#      runtime-state root. This is the half that catches state written to a path
#      no rule covers, which is why the roots are declared wider than the
#      covered list. Each offender is named; that path is what `git add -A`
#      would commit.
#   3. HISTORY — a root declared wholly generated may hold no tracked file at
#      all. A tracked file under `.verify/` is runtime state already committed.
#      This rule has NO exceptions. The one named carve-out that ever existed
#      (#1139, for the attestation schema parked inside `.verify/`) was removed
#      by #1146 once #1143 relocated that schema out of the root; the
#      declaration section below records why it was removed, not kept.
#
# MEASURED CONSEQUENCE (2026-09-15, `master` @ 84afa90, worktree ao-625-08cee8aa)
#   Tracked code writes runtime state to `.board/claims/`, `.fleet/waves/` and
#   `.fleet/board-reports.json`, and `.gitignore` covers none of the three. They
#   are deliberately NOT on the declared coverage list: a gate that declares them
#   required while their rules are absent could never be green, and this gate's
#   own lane owns the gate, not `.gitignore`. They are caught by check 2 the
#   moment such a file exists, by name — which is what happens on a tree where
#   the state is live. Extending `.gitignore` to cover them is a `.gitignore`
#   change and belongs to a different lane.
#
#   Check 2 also means this gate is only green on a tree whose runtime state is
#   actually ignored: a shared dev checkout carrying an uncovered `.board/claims/`
#   correctly reports NOT-OK. A lane worktree cut from `master` is clean.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS. No network access.
#   CANNOT-ASSESS (2) is reserved for an input that cannot be read at all — no
#   git, a root that is not a directory, a root that is not a git work tree, or
#   a `git ls-files` that fails. A MISSING `.gitignore` is NOT CANNOT-ASSESS: the
#   property under test is definitively false when there is no `.gitignore`, so
#   that is NOT-OK. No branch of this script returns 2 for "could not tell".
#
# WIRED INTO `make verify`. `scripts/discover-checks.sh` (#698) auto-discovers
# every `scripts/check-*.sh`, and this one is not denylisted in
# `scripts/check-denylist.txt` — `discover_check_scripts` prints
# `gitignore|bash scripts/check-gitignore.sh`. When this file was first written
# the explicit check list meant an unregistered script was inert; #698 replaced
# that with discovery. Corrected here because a comment claiming a live gate is
# inert is the doc-vs-reality drift this gate itself exists to refuse.
#
# Usage: bash scripts/check-gitignore.sh
#   AO_GITIGNORE_ROOT=<dir> assesses <dir> instead of the tree this script lives
#   in. It exists so the coverage half can be provoked against a COPY; the
#   override is announced on stderr and echoed in the verdict, so a leaked value
#   cannot silently redirect the assessment.
set -u

# --- the declaration -------------------------------------------------------
# Directories whose contents are runtime state and must therefore be ignored by
# the tracked `.gitignore`. Measured covered at the commit named in the header;
# this list is the gate's own declaration, so it does not move when
# `.gitignore` does.
state_dirs=(
  .verify
  .board/locks
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
)

# Roots under which no untracked, unignored path may exist. Wider than
# `state_dirs` on purpose: this is the net for state written outside the
# declared list.
runtime_roots=(.board .fleet .verify)

# Roots whose entire content is generated, so a tracked file under one is
# runtime state that already reached history.
wholly_generated=(.verify)

# Named exceptions: NONE, deliberately (issue #1146).
#
# This list ever held exactly one entry, and that entry was the gate being
# weakened to reach green. The chain, measured from history:
#
#   * `scripts/verify.sh` must attest against a JSON Schema — a checked-in
#     contract, real source (#882, closing #1001).
#   * Its lane put that schema at `.verify/attestation.schema.json`, i.e. inside
#     the one root this gate declares wholly generated. Check 3 then failed on
#     it, correctly: a tracked file under a generated root is runtime state that
#     already reached history.
#   * #1139 resolved that by carving the single path out of check 3 by name
#     (`wholly_generated_exceptions`), arguing in its own message that this was
#     narrower than "weakening the root declaration itself". Narrower, but still
#     a weakness: rule 3 became conditionally absolute, and a tracked file under
#     a generated root had a way to print OK.
#   * #1143 then did the honest fix — relocated the schema to
#     `governance/isolation/attestation.schema.json` and restored a plain,
#     un-negated `.verify/` ignore — which left this list excusing a path that
#     no longer exists.
#
# The list is removed rather than emptied. An exception with no subject is a
# hole held open for a file that is now correctly outside the root, and keeping
# it would re-legalise precisely the state the relocation was performed to end.
# Acceptance 2 of #1146 asks for the reason to be recorded in the file that
# asserts the check; this is that record. Check 3 is now absolute — any tracked
# file under a wholly-generated root fails by name, with no way to be excused.

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
script_root="$(find_repo_root)"
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

# --- 1. coverage -----------------------------------------------------------
echo "== 1. declared runtime-state directories are ignored by .gitignore =="
if [ ! -f "$root/.gitignore" ]; then
  printf '  FAIL  .gitignore (absent — none of the %s declared state directories is covered)\n' \
    "${#state_dirs[@]}" >&2
  fail=$((fail + 1))
else
  for dir in "${state_dirs[@]}"; do
    # Git matches an ignore pattern against a path without the path existing, so
    # this asks the real matcher about the state directory instead of
    # re-implementing `.gitignore` semantics here.
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

# --- verdict ---------------------------------------------------------------
echo
if [ "$fail" -ne 0 ]; then
  printf 'check-gitignore: FAIL — %s problem(s); runtime state under %s is not safely ignored\n' \
    "$fail" "${runtime_roots[*]}" >&2
  exit 1
fi
if [ "$overridden" = "1" ]; then
  printf 'check-gitignore: OK — %s/%s declared state directories ignored, no untracked unignored path under %s (root overridden: %s)\n' \
    "$covered" "${#state_dirs[@]}" "${runtime_roots[*]}" "$root"
else
  printf 'check-gitignore: OK — %s/%s declared state directories ignored, no untracked unignored path under %s\n' \
    "$covered" "${#state_dirs[@]}" "${runtime_roots[*]}"
fi
exit 0
