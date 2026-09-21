#!/usr/bin/env bash
# check-knowledge-index.sh — the institutional knowledge index must be valid
# (issue #139; the provoked half is issue #1198).
#
# WHAT THIS IS
#   The indexer's own `validate` is the check: it rebuilds the catalogue, verifies
#   every item's provenance, applies the secret policy to indexed assets, and
#   requires each mandatory kind to be covered. It reports, but does not fail on,
#   unreachable CMR-hub kinds (the submodule is absent in a clean checkout) and on
#   drift — those are honest gaps and refresh signals, not violations.
#
# WHY IT IS SHAPED LIKE THIS (issue #1198)
#   This gate used to be nine lines of dispatch around `validate` and declared no
#   provocation of its own failure path. It was the only gate in the ten-mechanism
#   table of the futureproof capstone (`governance/futureproof/e2e.py`, #1193) that
#   declared neither a negative control, a self-test nor a mutation, so the
#   capstone graded the mechanism DECLARED-ONLY and carried a named exemption for
#   it. A gate that cannot fail is a formality (GR-12 / AO-GR-4), so this gate now
#   proves itself on EVERY run, before it looks at the repository at all.
#
#   It materialises the catalogue's OWN sources into a scratch tree — read from
#   `governance/knowledge/sources.py`, never from a list written here, so the
#   provocation cannot drift from the catalogue it tests — builds the recorded
#   catalogue into that twin, and then changes exactly ONE input at a time. Every
#   half is driven through the SAME function the repository run uses
#   (`check_root`), so what is proven is the code path that runs, never a copy:
#
#     1. the clean twin is ACCEPTED — `knowledge-index: OK`;
#     2. one REQUIRED source removed is REFUSED BY NAME (code
#        `required-source-missing`, naming the catalogued pattern) — rc 1;
#     3. one whole REQUIRED KIND removed is REFUSED BY NAME (code
#        `required-kind-empty`, naming the kind) — rc 1;
#     4. one OPTIONAL source removed is NOT refused — rc 0. This is the half that
#        makes 2 and 3 mean anything: without it, a rule that refused ANY absence
#        would pass, and this gate's own promise — an unreachable CMR-hub kind is
#        an honest gap, not a violation — would be untested;
#     5. a tree with no recorded catalogue, assessed with the catalogue required,
#        and a tree that is not a directory, are BOTH CANNOT-ASSESS (rc 2) —
#        never a pass.
#
#   Halves 2-4 choose their target from the catalogue AT RUN TIME (the first
#   required source, the first required kind, the first optional source that
#   matches anything), and a plant that matched NO file is CANNOT-ASSESS rather
#   than a silent pass: a mutation that changed nothing proves nothing.
#
#   Half 2 must also NOT report `required-kind-empty`, and the self-test asserts
#   that: it is what keeps two distinct refusal rules from being one rule wearing
#   two names.
#
# EXIT CONTRACT (the repo's honesty tri-state, guardrails/honesty)
#   0  OK              validate passed on the assessed tree AND the self-test passed
#   1  NOT-OK          validate reported an error, or the self-test failed
#   2  CANNOT-ASSESS   no python3, no scratch directory, no recorded catalogue for
#                      the repository run, or a bad invocation — NEVER a pass
#
# Usage:
#   bash scripts/check-knowledge-index.sh                the gate: self-test, then this tree
#   bash scripts/check-knowledge-index.sh --self-test    the provocation alone
#   bash scripts/check-knowledge-index.sh --root DIR     assess exactly DIR, no self-test
set -u

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)" || exit 2
scratch=""
LAST_OUT=""
EFFECTIVE_DROP=""
unproven=0
controls=0
mode="gate"
assess_root="$root"

# One global scratch and one EXIT trap, the shape this repo asks for: armed once,
# fired once, `|| true`-safe so a cleanup failure cannot mask the gate's exit code.
cleanup() { [ -n "$scratch" ] && rm -rf "$scratch" || true; }
trap cleanup EXIT

usage() {
  cat <<'USAGE'
Usage:
  bash scripts/check-knowledge-index.sh                the gate: self-test, then this tree
  bash scripts/check-knowledge-index.sh --self-test    the provocation alone
  bash scripts/check-knowledge-index.sh --root DIR     assess exactly DIR, no self-test
Exit codes: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    -h | --help)
      usage
      exit 0
      ;;
    --self-test)
      mode="self-test"
      shift
      ;;
    --root)
      if [ "$#" -lt 2 ]; then
        printf 'check-knowledge-index: CANNOT-ASSESS — --root needs a directory\n' >&2
        exit 2
      fi
      assess_root="$2"
      mode="root"
      shift 2
      ;;
    *)
      printf 'check-knowledge-index: CANNOT-ASSESS — unknown argument: %s (see --help)\n' "$1" >&2
      exit 2
      ;;
  esac
done

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-knowledge-index: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

cd "$root" || { echo "check-knowledge-index: CANNOT-ASSESS — cannot enter $root" >&2; exit 2; }

scratch="$(mktemp -d /tmp/ao1198-knowledge-index.XXXXXX)" || {
  echo "check-knowledge-index: CANNOT-ASSESS — cannot create a scratch directory" >&2
  exit 2
}

# --- the ONE code path the repository run and every plant share --------------
# check_root <dir> : run this gate's own `validate` against DIR, leaving the
# transcript in $LAST_OUT and returning the mapped tri-state (0 / 1 / 2). The
# mapping is the whole point of this function: rc 2 — and anything unexpected —
# must never become 0 here, because a gate that reads CANNOT-ASSESS as a pass is
# the very failure mode this gate exists against.
check_root() {
  local dir="$1" rc
  LAST_OUT="$scratch/last-validate.out"
  python3 governance/knowledge/cli.py --root "$dir" validate > "$LAST_OUT" 2>&1
  rc=$?
  case "$rc" in
    0) return 0 ;;
    2) return 2 ;;
    *) return 1 ;;
  esac
}

# --- the plants: the catalogue's own sources, with ONE thing changed --------
# materialise <dest> <selector> — copy the catalogue's sources into DEST except
# the one named by SELECTOR, then print the selector that was ACTUALLY planted as
# `drop=<name>`, so the caller asserts on the name that was planted rather than on
# the name it asked for. SELECTOR is one of:
#   none                    drop nothing (the clean twin)
#   required-source-first   the first REQUIRED source that matches a file
#   required-kind-first     the first REQUIRED KIND whose sources match a file
#   optional-source-first   the first OPTIONAL source that matches a file
materialise() {
  PYTHONDONTWRITEBYTECODE=1 python3 - "$root" "$1" "$2" <<'PY'
import os
import shutil
import sys
from pathlib import Path

real = Path(sys.argv[1])
dest = Path(sys.argv[2])
selector = sys.argv[3]

sys.path.insert(0, os.path.join(sys.argv[1], "governance", "knowledge"))
import indexer
import sources

drop_kind = None
drop_pattern = None
if selector == "none":
    pass
elif selector == "required-source-first":
    for spec in sources.SOURCE_SPECS:
        if spec.required and indexer.iter_matches(real, spec.pattern):
            drop_pattern = spec.pattern
            break
elif selector == "required-kind-first":
    for spec in sources.SOURCE_SPECS:
        if spec.required and indexer.iter_matches(real, spec.pattern):
            drop_kind = spec.kind
            break
elif selector == "optional-source-first":
    for spec in sources.SOURCE_SPECS:
        if not spec.required and indexer.iter_matches(real, spec.pattern):
            drop_pattern = spec.pattern
            break
else:
    sys.stderr.write("unknown selector: %s\n" % selector)
    raise SystemExit(2)

if selector != "none" and drop_kind is None and drop_pattern is None:
    sys.stderr.write(
        "no source for plant %r matched a file in %s — the mutation would change "
        "nothing, so it proves nothing\n" % (selector, real)
    )
    raise SystemExit(2)

copied = 0
dropped = 0
for spec in sources.SOURCE_SPECS:
    matched = indexer.iter_matches(real, spec.pattern)
    if spec.kind == drop_kind or spec.pattern == drop_pattern:
        dropped += len(matched)
        continue
    for match in matched:
        rel = os.path.relpath(match, real)
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(match, target)
        copied += 1

if dropped == 0 and selector != "none":
    sys.stderr.write("plant %r removed no file — it proves nothing\n" % selector)
    raise SystemExit(2)

print("materialised %d source file(s), removed %d" % (copied, dropped))
print("drop=%s" % (drop_kind or drop_pattern or "none"))
PY
}

# plant <dest> <selector> <log> : materialise DEST, then give it the SAME
# recorded catalogue the clean twin carries — so the plants differ from the twin
# by the one source removed and nothing else. Sets EFFECTIVE_DROP.
plant() {
  local dest="$1" selector="$2" log="$3"
  EFFECTIVE_DROP=""
  if ! materialise "$dest" "$selector" > "$log" 2>&1; then
    printf 'check-knowledge-index: CANNOT-ASSESS — the plant %s could not be built:\n' "$selector" >&2
    sed 's/^/        /' "$log" >&2
    return 2
  fi
  EFFECTIVE_DROP="$(sed -n 's/^drop=//p' "$log" | tail -1)"
  mkdir -p "$dest/governance/knowledge"
  if ! cp "$scratch/clean/governance/knowledge/catalog.json" \
      "$dest/governance/knowledge/catalog.json"; then
    echo "check-knowledge-index: CANNOT-ASSESS — the twin's recorded catalogue could not be given to a plant" >&2
    return 2
  fi
  return 0
}

# --- the assertions: each one names what it refused -------------------------
# expect_refused <label> <tree> <needle>... : the mapped verdict must be 1 and
# name EVERY needle. A refusal that does not name the thing it refused is not a
# refusal this gate can be trusted on.
expect_refused() {
  local label="$1" tree="$2" rc=0 needle
  shift 2
  controls=$((controls + 1))
  check_root "$tree" || rc=$?
  if [ "$rc" -ne 1 ]; then
    printf 'check-knowledge-index: FAIL — plant %s returned mapped rc=%s, not a refusal (rc 1)\n' \
      "$label" "$rc" >&2
    sed 's/^/        /' "$LAST_OUT" >&2
    unproven=$((unproven + 1))
    return 1
  fi
  for needle in "$@"; do
    if ! grep -qF -- "$needle" "$LAST_OUT"; then
      printf 'check-knowledge-index: FAIL — plant %s was refused without naming: %s\n' \
        "$label" "$needle" >&2
      sed 's/^/        /' "$LAST_OUT" >&2
      unproven=$((unproven + 1))
      return 1
    fi
  done
  printf '  OK    plant %-22s refused rc=1, naming %s\n' "$label" "$*"
  return 0
}

# expect_accepted <label> <tree> <needle> : the mapped verdict must be 0 and
# carry the needle. This is the vacuity half — a rule that refused an honest gap
# would fail here.
expect_accepted() {
  local label="$1" tree="$2" needle="$3" rc=0
  controls=$((controls + 1))
  check_root "$tree" || rc=$?
  if [ "$rc" -ne 0 ]; then
    printf 'check-knowledge-index: FAIL — %s was refused (mapped rc=%s); the rule over-matches\n' \
      "$label" "$rc" >&2
    sed 's/^/        /' "$LAST_OUT" >&2
    unproven=$((unproven + 1))
    return 1
  fi
  if ! grep -qF -- "$needle" "$LAST_OUT"; then
    printf 'check-knowledge-index: FAIL — %s was accepted without the expected verdict text: %s\n' \
      "$label" "$needle" >&2
    sed 's/^/        /' "$LAST_OUT" >&2
    unproven=$((unproven + 1))
    return 1
  fi
  printf '  OK    %-22s accepted rc=0: %s\n' "$label" "$(tail -1 "$LAST_OUT")"
  return 0
}

# expect_blind <label> <tree> : the mapped verdict must be 2 CANNOT-ASSESS.
expect_blind() {
  local label="$1" tree="$2" rc=0
  controls=$((controls + 1))
  check_root "$tree" || rc=$?
  if [ "$rc" -ne 2 ]; then
    printf 'check-knowledge-index: FAIL — %s returned mapped rc=%s; a missing input must be CANNOT-ASSESS (rc 2), never a pass\n' \
      "$label" "$rc" >&2
    sed 's/^/        /' "$LAST_OUT" >&2
    unproven=$((unproven + 1))
    return 1
  fi
  if ! grep -qF -- "CANNOT-ASSESS" "$LAST_OUT"; then
    printf 'check-knowledge-index: FAIL — %s reached rc 2 without saying CANNOT-ASSESS\n' "$label" >&2
    unproven=$((unproven + 1))
    return 1
  fi
  printf '  OK    %-22s CANNOT-ASSESS rc=2 (never a pass): %s\n' "$label" "$(tail -1 "$LAST_OUT")"
  return 0
}

# expect_silent <label> <code> : the LAST transcript must NOT carry CODE. This is
# what keeps two distinct refusal rules from being one rule wearing two names.
expect_silent() {
  local label="$1" code="$2"
  if grep -qF -- "$code" "$LAST_OUT"; then
    printf 'check-knowledge-index: FAIL — %s also reported %s; the two rules are not distinct\n' \
      "$label" "$code" >&2
    unproven=$((unproven + 1))
    return 1
  fi
  printf '  OK    %-22s did not report %s (the two rules are distinct)\n' "$label" "$code"
  return 0
}

# --- the provocation --------------------------------------------------------
self_test() {
  local tree rc=0
  echo "== the catalogue, provoked =="

  # 1. the clean twin, plus the recorded catalogue every plant below carries.
  #    Built directly rather than through `plant`, which exists to give a MUTANT
  #    the twin's catalogue — a step the twin itself cannot take.
  tree="$scratch/clean"
  if ! materialise "$tree" none > "$scratch/mat-clean.out" 2>&1; then
    echo "check-knowledge-index: CANNOT-ASSESS — the clean twin could not be materialised:" >&2
    sed 's/^/        /' "$scratch/mat-clean.out" >&2
    return 2
  fi
  if ! python3 governance/knowledge/cli.py --root "$tree" build \
      > "$scratch/build.out" 2>&1; then
    echo "check-knowledge-index: CANNOT-ASSESS — the twin's catalogue could not be built:" >&2
    sed 's/^/        /' "$scratch/build.out" >&2
    return 2
  fi
  expect_accepted "clean twin" "$tree" "knowledge-index: OK" || rc=1

  # 2. ONE required source removed -> refused, naming the pattern.
  tree="$scratch/req-source"
  plant "$tree" required-source-first "$scratch/mat-req-source.out" || return 2
  expect_refused "required-source-first" "$tree" \
    "required-source-missing" "$EFFECTIVE_DROP" || rc=1
  expect_silent "required-source-first" "required-kind-empty" || rc=1

  # 3. ONE whole required KIND removed -> refused, naming the kind.
  tree="$scratch/req-kind"
  plant "$tree" required-kind-first "$scratch/mat-req-kind.out" || return 2
  expect_refused "required-kind-first" "$tree" \
    "required-kind-empty" "required kind '$EFFECTIVE_DROP' yielded no items" || rc=1

  # 4. ONE optional source removed -> NOT refused. The vacuity half.
  tree="$scratch/optional"
  plant "$tree" optional-source-first "$scratch/mat-optional.out" || return 2
  expect_accepted "optional-source-first" "$tree" "knowledge-index: OK" || rc=1

  # 5. no recorded catalogue, and not a directory -> CANNOT-ASSESS, never a pass.
  tree="$scratch/no-catalogue"
  mkdir -p "$tree"
  expect_blind "no recorded catalogue" "$tree" || rc=1
  expect_blind "not a directory" "$scratch/does-not-exist" || rc=1

  if [ "$rc" -eq 0 ]; then
    echo "check-knowledge-index: self-test OK — $controls control(s), refused by name, both ways"
  fi
  return "$rc"
}

# --- dispatch ---------------------------------------------------------------
self_rc=0
tree_rc=0

case "$mode" in
  self-test)
    self_test || self_rc=$?
    ;;
  root)
    echo "== the assessed tree =="
    check_root "$assess_root" || tree_rc=$?
    case "$tree_rc" in
      0) echo "  OK    $assess_root: $(tail -1 "$LAST_OUT")" ;;
      2) echo "  CANNOT-ASSESS  $assess_root: $(tail -1 "$LAST_OUT")" >&2 ;;
      *) echo "  FAIL  $assess_root:" >&2; sed 's/^/        /' "$LAST_OUT" >&2 ;;
    esac
    ;;
  gate)
    self_test || self_rc=$?
    echo ""
    echo "== the repository tree =="
    check_root "$root" || tree_rc=$?
    case "$tree_rc" in
      0) echo "  OK    the committed index validates: $(tail -1 "$LAST_OUT")" ;;
      2) echo "  CANNOT-ASSESS  validate reached no verdict on this tree: $(tail -1 "$LAST_OUT")" >&2 ;;
      *) echo "  FAIL  validate refused this tree:" >&2; sed 's/^/        /' "$LAST_OUT" >&2 ;;
    esac
    ;;
esac

if [ "$self_rc" -eq 2 ] || [ "$tree_rc" -eq 2 ]; then
  echo "check-knowledge-index: CANNOT-ASSESS — a half of this gate reached no verdict, and CANNOT-ASSESS is never a pass" >&2
  exit 2
fi
if [ "$unproven" -gt 0 ] || [ "$self_rc" -ne 0 ]; then
  printf 'check-knowledge-index: FAIL — the self-test left %s unproven control(s) (see above)\n' \
    "$unproven" >&2
  exit 1
fi
if [ "$tree_rc" -ne 0 ]; then
  printf 'check-knowledge-index: FAIL — %s is not accepted (see above)\n' \
    "$assess_root" >&2
  exit 1
fi
if [ "$mode" = "root" ]; then
  printf 'check-knowledge-index: OK — %s validates\n' "$assess_root"
else
  printf 'check-knowledge-index: OK — %s negative control(s) refused by name, and the assessed tree validates\n' \
    "$controls"
fi
exit 0
