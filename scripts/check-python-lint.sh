#!/usr/bin/env bash
# check-python-lint.sh — dead code stops being invisible (issue #1203, parent #1201).
#
# THE GAP THIS CLOSES
#   `make verify` compiled the Python (`scripts/check-python-syntax.sh`) but never
#   LINTED it. Compiling proves a file parses; it says nothing about a name that
#   is imported and never used, a local that is computed and thrown away, or an
#   annotation name that exists only because `from __future__ import annotations`
#   deferred it to a string. The 2026-09-17 deep review (#1201) measured 81
#   pyflakes findings in the three biggest pillars alone; none of them was
#   gate-visible, which is why the register that lists them exists at all.
#
# WHAT IT MEASURES
#   One linter, over the eleven product trees below (`vendor/` is not among them:
#   a pinned submodule is not ours to lint). The tool is `pyflakes` — the semantic
#   checker alone, whose entire output is this gate's subject — and
#   `ruff check --select F` is the DECLARED FALLBACK when pyflakes is absent,
#   reporting the same F-codes. Findings are named by path, line and message, and
#   any enforced finding fails the gate.
#
# TWO SCOPES NARROWER THAN "EVERY LINE", EACH BECAUSE THE TOOL IS WRONG THERE
#   Both are declared here, both are REPORTED on every run (a suppressed finding
#   is never silent), and neither is a baseline file that can rot:
#
#   1. `# noqa` IS HONOURED. The source already declares its intentional unused
#      imports this way, and this repository's doctrine is that a declaration must
#      be visible and reasoned — so the gate reads it. It has to: pyflakes has NO
#      `# noqa` support at all, unlike every linter in its own ecosystem, so
#      without this the gate would demand the deletion of imports the source
#      explicitly marks deliberate. MEASURED while writing this (#1203):
#      `portal/tests/test_erp_module_surface.py` imports the pytest fixture
#      `browser` with `# noqa: E402,F401  (the CDP harness)` and deleting it
#      breaks the very tests the import serves; `import telemetry.metering
#      # noqa: E402,F401` is a required `sys.modules` cache, not dead code.
#      A bare `# noqa` excuses the line; `# noqa: E402,F401` excuses only those
#      codes (the standard reading).
#
#   2. IN TEST MODULES ONLY UNDEFINED NAMES ARE ENFORCED. A pytest fixture is
#      imported for its NAME — the framework resolves it — so
#      `from conftest import AGENT_A` is used even when the module never mentions
#      `AGENT_A` again, and a fixture taken as a test parameter is shadowed by
#      that parameter, which pyflakes cannot see. Unused-import findings there are
#      false positives by construction. Undefined names are real bugs in a test
#      too, so those stay enforced. What this rule defers is counted and printed,
#      never swallowed.
#
# THE PROVOCATION IS THE POINT (GR-12)
#   A scan that reports nothing is indistinguishable from a scan that is broken,
#   so this check builds three fixtures OUTSIDE the repository and requires the
#   detector to behave in all three: an unused import must be REFUSED BY NAME
#   (exit 1 + the refusal string); the same import carrying `# noqa: F401` must be
#   DECLARED rather than enforced (proving the declaration path is the only way
#   anything is excused, and that it is not a blanket mute); and a clean twin must
#   be ACCEPTED. A detector that cannot fail is a formality; one that refuses
#   everything is no more honest.
#
# EXIT CONTRACT (guardrails/honesty tri-state, consumed not redefined)
#   0 OK               the linter ran and left nothing enforced
#   1 NOT-OK           at least one enforced finding (named), or the provocation
#                      did not behave — a detector that cannot fail is a formality
#   2 CANNOT-ASSESS    neither `pyflakes` nor `ruff` is installed, so the question
#                      cannot be answered — never a pass
#
# Offline, deterministic, no network, no containers.
#
# Usage: bash scripts/check-python-lint.sh
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

# The product trees. Literal on purpose: a glob would silently widen the scan, and
# a tree dropped from this list would silently narrow it.
trees=(
  registry
  gateway
  engine
  guardrails
  telemetry
  identity
  integrations
  control-plane
  portal
  fleet
  e2e
)

# --- the linter --------------------------------------------------------------
lint_tool=""
if command -v pyflakes >/dev/null 2>&1; then
  lint_tool="pyflakes"
elif command -v ruff >/dev/null 2>&1; then
  lint_tool="ruff"
else
  echo "check-python-lint: CANNOT-ASSESS — neither pyflakes nor ruff is installed, so Python dead code cannot be assessed (install one and re-run)" >&2
  exit 2
fi

# detect <path>... -> the linter's findings on stdout; rc 0 clean / 1 findings /
# 2 cannot-assess. ONE function, so the provocation drives the same path the real
# scan uses: a proof that exercised a copy would prove nothing about what runs.
detect() {
  local out rc
  if [ "$#" -eq 0 ]; then
    return 2
  fi
  if [ "$lint_tool" = "pyflakes" ]; then
    out="$(pyflakes "$@" 2>&1)"
    rc=$?
  else
    out="$(ruff check --select F --no-cache --output-format concise "$@" 2>&1)"
    rc=$?
  fi
  if [ -n "$out" ]; then
    printf '%s\n' "$out"
  fi
  return "$rc"
}

# adjudicate <findings-file> <root> -> one line per finding, prefixed
#   ENFORCED      the gate fails
#   DECLARED      excused by the line's own `# noqa`
#   NOT-ENFORCED  a test module, and the finding is not an undefined name
# and a final `TALLY <enforced> <declared> <deferred>` line.
adjudicate() {
  python3 - "$1" "$2" <<'PY'
import re
import sys
from pathlib import Path

findings_path, root = sys.argv[1], Path(sys.argv[2])

FINDING = re.compile(r"^(?P<path>[^:]+):(?P<line>\d+):(?P<col>\d+): (?P<msg>.*)$")

# pyflakes prints prose, not codes, so the code is derived from the message --
# the closed set this gate can actually encounter.
CODE_BY_MESSAGE = (
    ("undefined name", "F821"),
    ("imported but unused", "F401"),
    ("redefinition of unused", "F811"),
    ("local variable", "F841"),
    ("f-string is missing placeholders", "F541"),
    ("unable to detect undefined names", "F403"),
    ("may be undefined, or defined from star imports", "F405"),
)

NOQA = re.compile(r"#\s*noqa(?::[ \t]*(?P<codes>[A-Za-z0-9_, ]+))?")


def code_for(message):
    for needle, code in CODE_BY_MESSAGE:
        if needle in message:
            return code
    return ""


def line_text(path, lineno):
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            for index, text in enumerate(handle, 1):
                if index == lineno:
                    return text
    except OSError:
        return ""
    return ""


def is_test_module(path):
    return any(part == "tests" or part.endswith("_tests") for part in Path(path).parts)


def declared(path, lineno, code):
    match = NOQA.search(line_text(path, lineno))
    if not match:
        return False
    codes = match.group("codes")
    if not codes:
        return True
    listed = {part.strip() for part in codes.split(",") if part.strip()}
    return code in listed


enforced = declared_n = deferred = 0
for raw in Path(findings_path).read_text(encoding="utf-8", errors="replace").splitlines():
    match = FINDING.match(raw)
    if not match:
        if raw.strip():
            print("RAW           %s" % raw)
        continue
    path = match.group("path")
    lineno = int(match.group("line"))
    message = match.group("msg")
    code = code_for(message)
    if declared(path, lineno, code):
        declared_n += 1
        print("DECLARED      %s (excused by the line's own # noqa)" % raw)
        continue
    if is_test_module(path) and "undefined name" not in message:
        deferred += 1
        print("NOT-ENFORCED  %s (test module: only undefined names are enforced there)" % raw)
        continue
    enforced += 1
    print("ENFORCED      %s" % raw)
print("TALLY %d %d %d" % (enforced, declared_n, deferred))
PY
}

# contains <haystack> <needle> -- bash-native containment, NOT `| grep -q`
# (#852): `grep -q` exits on its FIRST match, killing the producer with SIGPIPE,
# and `set -o pipefail` then promotes that 141 to the status of the whole
# pipeline -- so a negated test reports ABSENT for text that IS present and the
# branch that proves a control works is the branch that gets skipped. REFUSED by
# name by scripts/check-verdict-contains.sh if written the other way.
contains() {
  case "$1" in
    *"$2"*) return 0 ;;
    *) return 1 ;;
  esac
}

# --- the provocation (self-test) --------------------------------------------
probe_dir="$(mktemp -d /tmp/check-python-lint.XXXXXX)"
scan_file="$(mktemp /tmp/check-python-lint-findings.XXXXXX)"
trap 'rm -rf "$probe_dir" "$scan_file"' EXIT

printf 'import os\n\n\ndef f() -> int:\n    return 1\n' > "$probe_dir/plant-unused-import.py"
printf 'import os  # noqa: F401\n\n\ndef f() -> int:\n    return 1\n' > "$probe_dir/plant-declared.py"
printf 'import os\n\n\ndef f() -> int:\n    return len(os.sep)\n' > "$probe_dir/clean-twin.py"

prov_fail=0

probe_out="$(detect "$probe_dir/plant-unused-import.py")"
probe_rc=$?
if [ "$probe_rc" -ne 1 ]; then
  printf 'check-python-lint: FAIL — the provocation was not refused: a planted unused import exited %s, not 1 (a detector that cannot fail is a formality)\n' "$probe_rc" >&2
  prov_fail=1
fi
if ! contains "$probe_out" 'plant-unused-import.py'; then
  printf 'check-python-lint: FAIL — the provocation was not refused BY NAME: the output does not name plant-unused-import.py\n' >&2
  prov_fail=1
fi
if ! contains "$probe_out" 'imported but unused'; then
  printf 'check-python-lint: FAIL — the provocation was refused without the expected refusal string ("imported but unused")\n' >&2
  prov_fail=1
fi
printf '%s\n' "$probe_out" > "$probe_dir/plant.findings"
plant_verdict="$(adjudicate "$probe_dir/plant.findings" "$root" | tail -1)"
if [ "$plant_verdict" != "TALLY 1 0 0" ]; then
  printf 'check-python-lint: FAIL — a planted unused import was not ENFORCED (adjudication said "%s", expected "TALLY 1 0 0")\n' "$plant_verdict" >&2
  prov_fail=1
fi

declared_out="$(detect "$probe_dir/plant-declared.py")"
printf '%s\n' "$declared_out" > "$probe_dir/declared.findings"
declared_verdict="$(adjudicate "$probe_dir/declared.findings" "$root" | tail -1)"
if [ "$declared_verdict" != "TALLY 0 1 0" ]; then
  printf 'check-python-lint: FAIL — an import carrying `# noqa: F401` was not DECLARED (adjudication said "%s", expected "TALLY 0 1 0"): the line is the only thing this gate may excuse\n' "$declared_verdict" >&2
  prov_fail=1
fi

clean_out="$(detect "$probe_dir/clean-twin.py")"
clean_rc=$?
if [ "$clean_rc" -ne 0 ]; then
  printf 'check-python-lint: FAIL — the clean twin was refused (rc %s): a detector that refuses everything is as blind as one that refuses nothing\n' "$clean_rc" >&2
  printf '%s\n' "$clean_out" >&2
  prov_fail=1
fi

if [ "$prov_fail" -ne 0 ]; then
  echo "check-python-lint: NOT-OK — the detector failed its own provocation, so a clean result cannot be trusted" >&2
  exit 1
fi

# --- the real scan ----------------------------------------------------------
# A declared tree that is missing is NAMED rather than silently skipped: a scan
# that quietly narrowed and still reported green is the false green this gate
# exists to prevent.
missing=()
present=()
for tree in "${trees[@]}"; do
  if [ -d "$tree" ]; then
    present+=("$tree")
  else
    missing+=("$tree")
  fi
done
if [ "${#present[@]}" -eq 0 ]; then
  echo "check-python-lint: CANNOT-ASSESS — none of the declared product trees exists in this checkout" >&2
  exit 2
fi

if [ "${#missing[@]}" -gt 0 ]; then
  printf 'check-python-lint: NOTE — declared tree(s) absent from this checkout and therefore not scanned: %s\n' "${missing[*]}" >&2
fi

detect "${present[@]}" > "$scan_file"
scan_rc=$?

if [ "$scan_rc" -ne 0 ] && [ "$scan_rc" -ne 1 ]; then
  printf 'check-python-lint: CANNOT-ASSESS — %s exited %s; the tree could not be assessed (never a pass)\n' "$lint_tool" "$scan_rc" >&2
  cat "$scan_file" >&2
  exit 2
fi

adjudicated="$(adjudicate "$scan_file" "$root")"
tally="$(printf '%s\n' "$adjudicated" | grep '^TALLY ' | tail -1)"
enforced_n="$(printf '%s' "$tally" | awk '{print $2}')"
declared_n="$(printf '%s' "$tally" | awk '{print $3}')"
deferred_n="$(printf '%s' "$tally" | awk '{print $4}')"
enforced_n="${enforced_n:-0}"
declared_n="${declared_n:-0}"
deferred_n="${deferred_n:-0}"

if [ "$declared_n" -gt 0 ] || [ "$deferred_n" -gt 0 ]; then
  printf '%s\n' "$adjudicated" | grep -E '^(DECLARED|NOT-ENFORCED) ' >&2
fi

if [ "$enforced_n" -gt 0 ]; then
  printf '%s\n' "$adjudicated" | grep '^ENFORCED ' >&2
  printf 'check-python-lint: NOT-OK — %s enforced finding(s) of dead or unusable Python (unused imports/locals, undefined names); repair them, do not baseline them away (%s declared with # noqa, %s deferred in test modules)\n' \
    "$enforced_n" "$declared_n" "$deferred_n" >&2
  exit 1
fi

printf 'check-python-lint: OK (%s, %s tree(s)) — %s declared with # noqa, %s deferred (test modules: only undefined names are enforced there)\n' \
  "$lint_tool" "${#present[@]}" "$declared_n" "$deferred_n"
exit 0
