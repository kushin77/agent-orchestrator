#!/usr/bin/env bash
# check-pr-contract.sh — the PR-body + commit-trailer contract gate (issue #288).
#
# WHY it exists: two obligations of the merge contract had no scaffolding and no
# gate, and were missed on real merges — every commit carries the
# `Refs <owner>/<repo>#<n>` trailer (AGENTS.md rule 1, docs/GOVERNANCE.md section
# 2), and every AI-originated PR declares `AI-assistance: <runtime> (<mode>)`.
# A rule nothing can fail is advisory (GR-29), so this check makes both
# falsifiable.
#
# WHAT it checks, and why `make verify` can run it without false-redding a lane:
#
#   * the PR-template contract (always): `.github/PULL_REQUEST_TEMPLATE.md`
#     exists and declares `Closes`, `AI-assistance:` and the `Reproduce:` block
#     a claimed pre-existing red must quote — remove a marker and this fails;
#   * the trailer predicate, both directions (always): a `Refs …#<n>` woven into
#     a subject line is REFUSED and a standalone trailer line is ACCEPTED, using
#     the same predicate the range check uses, so it cannot pass vacuously;
#   * range enforcement (opt-in): every commit in the range must carry the
#     trailer. In-flight lanes predate this rule, so hard-failing them from
#     `make verify` would false-red legitimate work; run it before merge with
#     `make pr-contract`, or set PR_RANGE;
#   * PR-body enforcement (opt-in): with PR_BODY=<file>, the body must carry
#     `Closes #<n>` and an `AI-assistance:` line.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-pr-contract.sh [<git-range>]
set -uo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

template=".github/PULL_REQUEST_TEMPLATE.md"
range="${1:-${PR_RANGE:-}}"
fail=0

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-pr-contract: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

# --- 1. the PR template declares the contract -------------------------------
if [ ! -f "$template" ]; then
  echo "  FAIL  $template is missing (the merge contract has no scaffolding)" >&2
  fail=$((fail + 1))
else
  for marker in "Closes" "AI-assistance:" "Reproduce:"; do
    if grep -qF -- "$marker" "$template"; then
      echo "  OK    $template declares '$marker'"
    else
      echo "  FAIL  $template does not declare '$marker'" >&2
      fail=$((fail + 1))
    fi
  done
fi

# --- 2. the trailer predicate, proved in both directions --------------------
python3 - <<'PY'
import sys

sys.path.insert(0, ".")
from governance.isolation.audit import commit_carries_trailer

TRAILER = "Refs kushin77/agent-orchestrator#288"
CASES = (
    ("subject-only ref", f"{TRAILER}: wire the contract", False),
    ("real trailer", f"wire the contract\n\n{TRAILER}\n", True),
    ("trailer above a co-author line", f"work\n\n{TRAILER}\n\nCo-authored-by: a <a@agents.invalid>\n", True),
    ("no ref at all", "wire the contract\n", False),
)
wrong = [name for name, message, want in CASES if commit_carries_trailer(message, TRAILER) != want]
if wrong:
    print("trailer predicate wrong for: " + ", ".join(wrong), file=sys.stderr)
    raise SystemExit(1)
raise SystemExit(0)
PY
rc=$?
if [ "$rc" -eq 0 ]; then
  echo "  OK    trailer predicate refuses a subject-only ref and accepts a real trailer"
else
  echo "  FAIL  the trailer predicate is broken (see above)" >&2
  fail=$((fail + 1))
fi

# --- 3. range enforcement (opt-in) ------------------------------------------
if [ -n "$range" ]; then
  python3 - "$range" <<'PY'
import subprocess
import sys

sys.path.insert(0, ".")
from governance.isolation.audit import trailing_ref
from governance.isolation.identity import REPO_SLUG_DEFAULT

FIELD, RECORD = "\x1f", "\x1e"
range_arg = sys.argv[1]
log = subprocess.run(
    ["git", "log", f"--format=%H{FIELD}%B{RECORD}", range_arg],
    capture_output=True,
    text=True,
)
if log.returncode != 0:
    print(f"cannot read git range {range_arg!r}: {log.stderr.strip()}", file=sys.stderr)
    raise SystemExit(2)

missing = []
for chunk in log.stdout.split(RECORD):
    if not chunk.strip():
        continue
    sha, message = chunk.strip("\n").split(FIELD, 1)
    ref = trailing_ref(message)
    if ref is None or ref[0] != REPO_SLUG_DEFAULT:
        missing.append(sha.strip()[:8])
if missing:
    print(
        f"{len(missing)} commit(s) in {range_arg} carry no Refs trailer: {', '.join(missing)}",
        file=sys.stderr,
    )
    raise SystemExit(1)
raise SystemExit(0)
PY
  rc=$?
  if [ "$rc" -eq 0 ]; then
    echo "  OK    every commit in $range carries a Refs trailer"
  elif [ "$rc" -eq 2 ]; then
    echo "check-pr-contract: CANNOT-ASSESS — cannot read the range $range" >&2
    exit 2
  else
    fail=$((fail + 1))
  fi
else
  echo "  SKIP  no commit range given; run 'make pr-contract' before merge"
fi

# --- 4. PR-body enforcement (opt-in) ----------------------------------------
if [ -n "${PR_BODY:-}" ]; then
  if [ ! -f "$PR_BODY" ]; then
    echo "check-pr-contract: CANNOT-ASSESS — PR_BODY=$PR_BODY is not a file" >&2
    exit 2
  fi
  for marker in "Closes #" "AI-assistance:"; do
    if grep -qF -- "$marker" "$PR_BODY"; then
      echo "  OK    $PR_BODY declares '$marker'"
    else
      echo "  FAIL  $PR_BODY does not declare '$marker'" >&2
      fail=$((fail + 1))
    fi
  done
fi

if [ "$fail" -ne 0 ]; then
  printf 'check-pr-contract: FAIL — %s finding(s)\n' "$fail" >&2
  exit 1
fi
echo "check-pr-contract: OK"
