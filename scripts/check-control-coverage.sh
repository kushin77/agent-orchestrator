#!/usr/bin/env bash
#
# check-control-coverage.sh — every platform/SaaS spine rule is traceable to a
# control that can fail (issue #874, child of EPIC #873).
#
# THE DEFECT THIS EXISTS FOR
#   The product's Part B spine (AO-GR-12…20: control plane never executes, independent
#   auditor, separation of duties, tenant isolation, DLP/injection, tamper-evident
#   audit, budgets/kill switch, guard honesty, private by default) declares a
#   **Verify.** block for each rule — and, measured 2026-09-16 on origin/master,
#   **7 of the 9 rules were cited by NO gate check at all**. The controls largely
#   exist (check-authority.sh says "separation of duties"; check-chat-guardrails.sh
#   says "DLP egress … injection defense"; check-audit-read-model.sh says
#   "tamper-evident ledger"), but nothing connects a rule to the control that
#   enforces it. An enterprise buyer does not purchase "we have tenant isolation";
#   they purchase the control, its owner, and its test — and a control nobody runs
#   is a formality.
#
#   Part C already had this machinery for AO-GR-21…27
#   (scripts/check-fleet-durability-rules.sh). This is the same idea for the
#   enterprise spine, where it was missing.
#
# WHAT IT CHECKS  (all against the repository, never against prose)
#   * every Part B rule in docs/GOLDEN-RULES.md has exactly one row in
#     scripts/control-coverage.tsv — no rule missing, no row for a rule that is gone;
#   * the status is one of ENFORCED / PARTIAL / GAP, and a row's shape is complete;
#   * every module path a row names EXISTS;
#   * every control a row names is INVOKED BY A GATE. "A gate" is the five-file
#     gate-invocation universe scripts/check-gate-coverage.sh defines (#526):
#     scripts/verify.sh, the Makefile, scripts/gate.sh, scripts/merge-gate.sh and
#     scripts/qa-loop.sh, plus the checks scripts/discover-checks.sh discovers. The
#     denylist is why this matters: `negative-controls` and `policy-schema` are
#     denylisted from `make verify` precisely because they are
#     gate.sh / merge-gate.sh signals — still run by a gate, just not that one.
#   * docs/CONTROL-COVERAGE.md names every rule (the document cannot silently lose
#     one), and is the human rendering of the same rows;
#   * a rule that is not ENFORCED is recorded in scripts/control-coverage-gaps.tsv,
#     which is SHRINK-ONLY: a new gap fails by name, and a recorded rule that is now
#     ENFORCED is reported as a shrink to re-record.
#
# HOW IT PROVES ITSELF
#   The assertions are one function over a map file. It is run twice: once against
#   the real map (must pass) and once against a copy whose control name is changed to
#   a script nobody runs (must fail). A gate that cannot fail is a formality.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-control-coverage.sh [--root DIR]
#        bash scripts/check-control-coverage.sh --record
set -uo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)" || exit 2
record=0
while [ $# -gt 0 ]; do
  case "$1" in
    --root) root="${2:?--root needs a directory}"; shift 2 ;;
    --record) record=1; shift ;;
    *) printf 'check-control-coverage: unknown argument: %s\n' "$1" >&2; exit 2 ;;
  esac
done

SPINE="$root/docs/GOLDEN-RULES.md"
MAP="$root/scripts/control-coverage.tsv"
GAPS="$root/scripts/control-coverage-gaps.tsv"
DOC="$root/docs/CONTROL-COVERAGE.md"
VERIFY="$root/scripts/verify.sh"
MAKEFILE="$root/Makefile"

contains() { # contains <haystack> <needle>
  case "$1" in
    *"$2"*) return 0 ;;
    *) return 1 ;;
  esac
}

for required in "$SPINE" "$MAP"; do
  if [ ! -r "$required" ]; then
    printf 'check-control-coverage: CANNOT-ASSESS — %s is not readable\n' "${required#"$root"/}" >&2
    exit 2
  fi
done

# --- the two sources of truth ------------------------------------------------
# The RULES come from the spine's Part B, never from the map: a map that invented a
# rule, or dropped one, must not be able to define its own scope.
part_b_rules() {
  awk '/^## Part B/,/^## Part C/' "$1" | sed -nE 's/^### (AO-GR-[0-9]+).*/\1/p'
}

# The CONTROLS that a gate actually runs. Discovered `.sh` checks first, then the
# explicit universe (verify.sh / Makefile), because some controls are not
# `check-*.sh` at all — `check-feature-flags.py` is invoked by name.
discovered() {
  ( cd "$root" && bash -c 'source scripts/discover-checks.sh 2>/dev/null; discover_check_scripts' 2>/dev/null )
}

control_is_invoked() { # control_is_invoked <control-basename> <discovered-list>
  local control="$1" list="$2" f
  contains "$list" "$control" && return 0
  while IFS= read -r f; do
    [ -r "$f" ] && grep -qF -- "$control" "$f" && return 0
  done < <(gate_files)
  return 1
}

# The gate-invocation universe, exactly as scripts/check-gate-coverage.sh defines it
# (#526): a control is invoked when a gate runs it, and a control denylisted from
# `make verify` is still run by gate.sh / merge-gate.sh.
gate_files() {
  printf '%s\n' "$VERIFY" "$MAKEFILE" \
    "$root/scripts/gate.sh" "$root/scripts/merge-gate.sh" "$root/scripts/qa-loop.sh"
}

map_rows() { # map_rows <map-file> -> "rule<TAB>status<TAB>modules<TAB>controls"
  # `$1 ~ /^AO-GR-/` is load-bearing: a header row, or any prose that happens to be
  # tab-separated, must never become a rule the check then fails to find in the spine.
  awk -F'\t' '!/^[[:space:]]*#/ && NF >= 4 && $1 ~ /^AO-GR-/ { print }' "$1"
}

row_for() { # row_for <rule> <map-file>
  map_rows "$2" | awk -F'\t' -v want="$1" '$1 == want { print; exit }'
}

# --- the assertions, over a map file, so the vacuity control can run them ------
map_findings=""
assert_map() { # assert_map <map-file> <spine-file> <label>
  local map="$1" spine="$2" label="$3" list
  list="$(discovered)"
  map_findings=""

  local rule row status modules controls found=0
  while IFS= read -r rule; do
    [ -n "$rule" ] || continue
    row="$(row_for "$rule" "$map")"
    if [ -z "$row" ]; then
      map_findings="$map_findings"$'\n'"  $label: $rule is in the spine but has no row in the control map"
      continue
    fi
    found=$((found + 1))
    status="$(printf '%s' "$row" | cut -f2)"
    modules="$(printf '%s' "$row" | cut -f3)"
    controls="$(printf '%s' "$row" | cut -f4)"
    case "$status" in
      ENFORCED|PARTIAL|GAP) ;;
      *) map_findings="$map_findings"$'\n'"  $label: $rule has status '$status', not ENFORCED/PARTIAL/GAP" ; continue ;;
    esac

    # every named module path must exist
    local m
    IFS=',' read -r -a mods <<< "$modules"
    for m in "${mods[@]}"; do
      m="${m%%[[:space:]]}"; [ -n "$m" ] || continue
      if [ ! -e "$root/$m" ]; then
        map_findings="$map_findings"$'\n'"  $label: $rule names module '$m', which does not exist"
      fi
    done

    # a non-ENFORCED rule must name no control; an ENFORCED one must name >= 1 that
    # a gate actually runs.
    if [ "$status" = "GAP" ]; then
      if [ "$controls" != "-" ] && [ -n "$controls" ]; then
        map_findings="$map_findings"$'\n'"  $label: $rule is GAP but names controls ($controls)"
      fi
      continue
    fi
    if [ "$controls" = "-" ] || [ -z "$controls" ]; then
      map_findings="$map_findings"$'\n'"  $label: $rule is $status but names no control"
      continue
    fi
    local c
    IFS=',' read -r -a ctrls <<< "$controls"
    for c in "${ctrls[@]}"; do
      c="${c%%[[:space:]]}"; [ -n "$c" ] || continue
      if [ ! -e "$root/scripts/$c" ]; then
        map_findings="$map_findings"$'\n'"  $label: $rule names control '$c', which is not in scripts/"
      elif ! control_is_invoked "$c" "$list"; then
        map_findings="$map_findings"$'\n'"  $label: $rule names control '$c', which NO GATE RUNS (a formality)"
      fi
    done
  done < <(part_b_rules "$spine")

  # a row for a rule that is not in Part B is a different rule's claim
  local r
  while IFS= read -r r; do
    [ -n "$r" ] || continue
    if ! contains "$(part_b_rules "$spine")" "$r"; then
      map_findings="$map_findings"$'\n'"  $label: the map has a row for $r, which Part B does not define"
    fi
  done < <(map_rows "$map" | cut -f1)

  [ "$found" -gt 0 ] || map_findings="$map_findings"$'\n'"  $label: no rule was assessed — the spine Part B parsed empty"
  return 0
}

# ---------------------------------------------------------------------------
# --record: shrink-only. It rewrites the gap record FROM the map, and refuses if
# that would ADD a rule the record does not already carry.
# ---------------------------------------------------------------------------
if [ "$record" -eq 1 ]; then
  grown=""
  while IFS=$'\t' read -r rule status _rest; do
    [ -n "$rule" ] || continue
    [ "$status" = "ENFORCED" ] && continue
    if [ -r "$GAPS" ] && ! grep -qE "^${rule}[[:space:]]" "$GAPS"; then
      grown="$grown"$'\n'"  $rule"
    fi
  done < <(map_rows "$MAP")
  if [ -n "$grown" ]; then
    printf 'check-control-coverage: REFUSED — the gap record is shrink-only, and this would ADD:%s\n' "$grown" >&2
    printf 'Enforce the rule first (flip it to ENFORCED in %s), then re-run --record.\n' "scripts/control-coverage.tsv" >&2
    exit 1
  fi
  {
    printf '%s\n' \
      '# Shrink-only record of the platform/SaaS spine rules that are NOT fully' \
      '# enforced (#874). Regenerated by `check-control-coverage.sh --record`, which' \
      '# refuses to add a rule this file does not already carry.' \
      '#' \
      '# Format: <rule><TAB><why it is not enforced>' \
      "# recorded: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    while IFS=$'\t' read -r rule status _rest; do
      [ -n "$rule" ] || continue
      [ "$status" = "ENFORCED" ] && continue
      why="$(awk -F'\t' -v want="$rule" '$1 == want { print $2 }' "$GAPS" 2>/dev/null)"
      printf '%s\t%s\n' "$rule" "$why"
    done < <(map_rows "$MAP")
  } > "$GAPS"
  printf 'check-control-coverage: recorded %s non-enforced rule(s) -> %s\n' \
    "$(map_rows "$MAP" | awk -F'\t' '$2 != "ENFORCED"' | wc -l)" "scripts/control-coverage-gaps.tsv"
  exit 0
fi

# ---------------------------------------------------------------------------
printf 'control-coverage: every platform/SaaS spine rule traceable to a control (#874)\n'
printf '\n== the Part B spine ==\n'
rules="$(part_b_rules "$SPINE")"
printf '  %s rule(s) in Part B: %s\n' "$(printf '%s\n' "$rules" | grep -c .)" "$(printf '%s' "$rules" | tr '\n' ' ')"

printf '\n== the control map ==\n'
assert_map "$MAP" "$SPINE" "map"
fail=0
if [ -n "$map_findings" ]; then
  printf '%s\n' "$map_findings" | sed '/^$/d' >&2
  fail=1
else
  printf '  OK    every rule has a row, every module exists, every control is invoked by a gate\n'
fi

printf '\n== the document ==\n'
if [ ! -r "$DOC" ]; then
  printf '  FAIL  docs/CONTROL-COVERAGE.md is missing — the map has no human rendering\n' >&2
  fail=1
else
  doc_missing=""
  while IFS= read -r rule; do
    [ -n "$rule" ] || continue
    contains "$(cat "$DOC")" "$rule" || doc_missing="$doc_missing $rule"
  done <<< "$rules"
  if [ -n "$doc_missing" ]; then
    printf '  FAIL  docs/CONTROL-COVERAGE.md does not name:%s\n' "$doc_missing" >&2
    fail=1
  else
    printf '  OK    the document names every Part B rule\n'
  fi
fi

printf '\n== the shrink-only gap record ==\n'
if [ ! -r "$GAPS" ]; then
  printf '  FAIL  scripts/control-coverage-gaps.tsv is missing — a gap must never be silent\n' >&2
  fail=1
else
  unrecorded=""
  stale=""
  while IFS=$'\t' read -r rule status _rest; do
    [ -n "$rule" ] || continue
    listed=0
    grep -qE "^${rule}[[:space:]]" "$GAPS" && listed=1
    if [ "$status" != "ENFORCED" ] && [ "$listed" -eq 0 ]; then
      unrecorded="$unrecorded $rule"
    fi
    if [ "$status" = "ENFORCED" ] && [ "$listed" -eq 1 ]; then
      stale="$stale $rule"
    fi
  done < <(map_rows "$MAP")
  if [ -n "$unrecorded" ]; then
    printf '  FAIL  a NEW gap is not recorded:%s\n' "$unrecorded" >&2
    printf '        Record it deliberately, with its reason, or enforce it.\n' >&2
    fail=1
  else
    printf '  OK    every non-enforced rule is recorded\n'
  fi
  if [ -n "$stale" ]; then
    printf '  NOTE  the record can be lowered — re-run with --record:%s\n' "$stale"
  fi
  printf '  enforced: %s of %s rule(s)\n' \
    "$(map_rows "$MAP" | awk -F'\t' '$2 == "ENFORCED"' | wc -l)" \
    "$(map_rows "$MAP" | wc -l)"
fi

# --- the vacuity control -----------------------------------------------------
printf '\n== vacuity control: a control nobody runs must be refused ==\n'
tmp="$(mktemp -d)" || { echo "check-control-coverage: CANNOT-ASSESS — mktemp failed" >&2; exit 2; }
trap 'rm -rf "$tmp"' EXIT
# Mutate a COPY of the map: point AO-GR-14 at a script that does not exist, which is
# the shape of the defect this check exists for (a rule citing a control that is not
# there, or that no gate runs).
python3 - "$MAP" "$tmp/mutated.tsv" <<'PY'
import sys, pathlib
src, dst = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
lines = src.read_text().splitlines(keepends=True)
out, hit = [], 0
for line in lines:
    if line.startswith("AO-GR-14\t"):
        parts = line.rstrip("\n").split("\t")
        parts[-1] = "check-that-nobody-runs.sh"
        line = "\t".join(parts) + "\n"
        hit += 1
    out.append(line)
if hit != 1:
    print(f"CONTROL-SETUP-BROKEN: AO-GR-14 row matched {hit} times", file=sys.stderr)
    raise SystemExit(3)
dst.write_text("".join(out))
PY
setup=$?
if [ "$setup" -ne 0 ]; then
  printf '  FAIL  the control could not be built (rc=%s)\n' "$setup" >&2
  exit 2
fi
assert_map "$tmp/mutated.tsv" "$SPINE" "control"
if [ -n "$map_findings" ]; then
  printf '  OK    the mutated map is refused — the assertions can fail\n'
  printf '%s\n' "$map_findings" | sed '/^$/d' | head -2 | sed 's/^/        /'
else
  printf '  FAIL  a map naming a control NO GATE RUNS was accepted — this check cannot fail\n' >&2
  fail=1
fi

printf '\n'
if [ "$fail" -ne 0 ]; then
  printf 'check-control-coverage: FAIL\n' >&2
  exit 1
fi
printf 'check-control-coverage: OK — every Part B rule is mapped, every mapped control is invoked by a gate, the document names them all, and the %s recorded gap(s) are shrink-only\n' \
  "$(grep -cE '^AO-GR-' "$GAPS" 2>/dev/null || echo 0)"
exit 0
