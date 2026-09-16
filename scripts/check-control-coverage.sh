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
#   only 2 of the 9 rules were *cited by name* in a gate check.
#
#   That is a CITATION measure, not a COVERAGE measure, and the difference is the
#   point. The controls largely exist: check-authority.sh says "separation of duties",
#   check-chat-guardrails.sh says "DLP egress … injection defense",
#   check-audit-read-model.sh says "tamper-evident ledger", and guardrails/isolation's
#   scope-gate suite fails closed on a foreign row while its deliberately-leaky fixture
#   proves the scanner reports a planted leak. What did not exist is the EDGE: nothing
#   in the repository connected a rule to the control that enforces it, so no reviewer
#   could walk from rule to control, and no gate could notice a rule losing its control.
#   This check is that edge.
#
#   The first draft of this map got that wrong in an instructive way: it recorded
#   AO-GR-15 (tenant isolation) as a GAP on the strength of an empty
#   `grep test_tenant_isolation scripts/pytest-suites.txt` — a grep for the test's
#   FILENAME against a manifest that declares MODULE DIRECTORIES (`telemetry/ledger`,
#   `guardrails/isolation`). An empty grep for the wrong pattern is not evidence of
#   absence. Both suites are declared and both are gate-run; that is why a suite
#   control is now asserted against the manifest rather than grepped for by name.
#
#   An enterprise buyer does not purchase "we have tenant isolation"; they purchase the
#   control, its owner, and its test — and a control nobody runs is a formality.
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
#   * a control is EITHER a check script (a `check-*.sh`, or a name a gate invokes)
#     OR a SUITE, written `suite:<dir>`. A suite control is enforced only when
#     scripts/pytest-suites.txt DECLARES it: that manifest is authoritative and
#     drift-checked (scripts/check-drift.sh warns on a suite that exists but was never
#     declared), and scripts/run-pytest-suites.sh runs every declared suite in
#     isolation. A suite that is not declared is a suite no gate runs — and a suite
#     control is worth nothing until the runner itself is shown to be invoked.
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

# A suite control is enforced when the manifest DECLARES it — matched as an exact
# module-directory line, never as a substring, because `telemetry/ledger` and a test
# file inside it are different claims and only the former is what a gate executes.
suite_is_declared() { # suite_is_declared <module-dir>
  local f="$root/scripts/pytest-suites.txt"
  [ -r "$f" ] || return 1
  awk -v want="$1" '
    /^[[:space:]]*#/ { next }
    { sub(/[[:space:]]+$/, "") }
    $0 == want { found = 1 }
    END { exit(found ? 0 : 1) }
  ' "$f"
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
    mapfile -t mods < <(tr ',' '\n' <<<"$modules")
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
    mapfile -t ctrls < <(tr ',' '\n' <<<"$controls")
    for c in "${ctrls[@]}"; do
      c="${c%%[[:space:]]}"; [ -n "$c" ] || continue
      case "$c" in
        suite:*)
          # A suite control: the rule is enforced by a declared pytest suite rather
          # than by one check script. Three things must hold, and the third is the
          # same question the check-script path asks — does a gate RUN it?
          local sd="${c#suite:}"
          if [ ! -d "$root/$sd" ]; then
            map_findings="$map_findings"$'\n'"  $label: $rule names suite '$sd', which is not a directory"
          elif ! suite_is_declared "$sd"; then
            map_findings="$map_findings"$'\n'"  $label: $rule names suite '$sd', which scripts/pytest-suites.txt does not declare (NO GATE RUNS it)"
          elif ! control_is_invoked "run-pytest-suites.sh" "$list"; then
            map_findings="$map_findings"$'\n'"  $label: $rule names suite '$sd', but no gate invokes the suite runner"
          fi
          ;;
        *)
          if [ ! -e "$root/scripts/$c" ]; then
            map_findings="$map_findings"$'\n'"  $label: $rule names control '$c', which is not in scripts/"
          elif ! control_is_invoked "$c" "$list"; then
            map_findings="$map_findings"$'\n'"  $label: $rule names control '$c', which NO GATE RUNS (a formality)"
          fi
          ;;
      esac
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
  # The existing rationales must be captured BEFORE the record is rewritten. The
  # `> "$GAPS"` below truncates the very file the lookup used to read, which silently
  # emptied every recorded reason when this first ran — a gap with no reason is a
  # SILENT gap, so the verify path now refuses one.
  prior=""
  [ -r "$GAPS" ] && prior="$(cat "$GAPS")"
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
      why="$(printf '%s\n' "$prior" | awk -F'\t' -v want="$rule" '$1 == want { print $2; exit }')"
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
  # "A gap is never silent" is this file's whole purpose, so a recorded rule whose
  # reason is missing IS the defect. The first version of --record truncated the
  # record before reading it and emptied every reason; this is what turns that from a
  # quietly thinner file into a failure.
  silent=""
  while IFS=$'\t' read -r grule gwhy; do
    case "$grule" in ''|'#'*) continue ;; esac
    [ -n "$gwhy" ] || silent="$silent $grule"
  done < "$GAPS"
  if [ -n "$silent" ]; then
    printf '  FAIL  a recorded gap carries NO reason (a silent gap):%s\n' "$silent" >&2
    printf '        A gap must name why it is not enforced, or be enforced and dropped.\n' >&2
    fail=1
  fi
  printf '  enforced: %s of %s rule(s)\n' \
    "$(map_rows "$MAP" | awk -F'\t' '$2 == "ENFORCED"' | wc -l)" \
    "$(map_rows "$MAP" | wc -l)"
fi

# --- the vacuity control -----------------------------------------------------
# A gate that cannot fail is a formality, so the assertions are run against MUTATED
# COPIES of the map as well as against the real one. Each provocation also names the
# finding it must produce: "the map was refused" is not sufficient evidence, because a
# refusal for the wrong reason does not show that the assertion under test works.
printf '\n== vacuity control: a control nobody runs must be refused ==\n'
tmp="$(mktemp -d)" || { echo "check-control-coverage: CANNOT-ASSESS — mktemp failed" >&2; exit 2; }
trap 'rm -rf "$tmp"' EXIT

mutate() { # mutate <src> <dst> <rule> <new-controls-value>
  python3 - "$1" "$2" "$3" "$4" <<'PY'
import sys, pathlib
src, dst = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
rule, value = sys.argv[3], sys.argv[4]
lines = src.read_text().splitlines(keepends=True)
out, hit = [], 0
for line in lines:
    if line.startswith(rule + "\t"):
        parts = line.rstrip("\n").split("\t")
        parts[-1] = value
        line = "\t".join(parts) + "\n"
        hit += 1
    out.append(line)
if hit != 1:
    print(f"CONTROL-SETUP-BROKEN: {rule} row matched {hit} times", file=sys.stderr)
    raise SystemExit(3)
dst.write_text("".join(out))
PY
}

provoke() { # provoke <label> <rule> <mutated-controls> <the-finding-it-must-produce>
  local label="$1" rule="$2" value="$3" want="$4"
  if ! mutate "$MAP" "$tmp/mutated.tsv" "$rule" "$value"; then
    printf '  FAIL  the control could not be built (%s)\n' "$label" >&2
    exit 2
  fi
  assert_map "$tmp/mutated.tsv" "$SPINE" "control"
  if [ -n "$map_findings" ] && contains "$map_findings" "$want"; then
    printf '  OK    %s\n' "$label"
    printf '%s\n' "$map_findings" | sed '/^$/d' | head -1 | sed 's/^/        /'
  else
    printf '  FAIL  %s — accepted, so this check cannot fail\n' "$label" >&2
    fail=1
  fi
}

# The check-script path has TWO assertions — the control exists, and a gate runs it —
# and each needs its own provocation. They are not interchangeable: naming a script
# that is not there exercises the first, and a first draft of this control did exactly
# that while claiming to exercise the second.
provoke "a control script that does not exist is refused" AO-GR-14 "check-that-nobody-runs.sh" "not in scripts/"

# The second assertion needs a REAL script that no gate happens to invoke. Its
# precondition is asserted here, so that if a later change wires that script into a
# gate, this fails with an explanation instead of silently testing the wrong thing.
provocation_target="scan-pr-failures.sh"
if [ ! -e "$root/scripts/$provocation_target" ]; then
  printf '  FAIL  the invocation provocation has no target: scripts/%s is gone\n' "$provocation_target" >&2
  fail=1
elif control_is_invoked "$provocation_target" "$(discovered)"; then
  printf '  FAIL  the invocation provocation is vacuous: a gate now runs scripts/%s — choose another unwired script\n' "$provocation_target" >&2
  fail=1
else
  provoke "a control script that exists but NO GATE runs is refused" AO-GR-14 "$provocation_target" "NO GATE RUNS"
fi

# the suite path: a rule citing a directory the manifest does not declare. `docs` is a
# real directory and is deliberately not a pytest suite, so this exercises the
# declaration assertion rather than the existence one.
provoke "a suite the manifest does not declare is refused" AO-GR-15 "suite:docs" "does not declare"

printf '\n'
if [ "$fail" -ne 0 ]; then
  printf 'check-control-coverage: FAIL\n' >&2
  exit 1
fi
printf 'check-control-coverage: OK — every Part B rule is mapped, every mapped control is invoked by a gate, the document names them all, and the %s recorded gap(s) are shrink-only\n' \
  "$(grep -cE '^AO-GR-' "$GAPS" 2>/dev/null || echo 0)"
exit 0
