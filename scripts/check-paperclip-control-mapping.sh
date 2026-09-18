#!/usr/bin/env bash
# check-paperclip-control-mapping.sh — the control-mapping gate (issue #1262,
# EPIC #1254).
#
# `integrations/paperclip/control_mapping.py` is the correspondence table between
# the fleet's RC-2 control vocabulary (`control-plane/control/verbs.yaml`) and
# upstream paperclip.ing's route inventory. Its whole safety property is that a
# declared verb CANNOT go unclassified: `build_table` refuses an unclassified verb
# BY NAME rather than defaulting it to "no upstream counterpart", because
# "nobody has looked at this yet" and "upstream serves nothing for it" are
# different statements.
#
# That property was real and INVISIBLE. The registry grew to 63 verbs while the
# table classified 49, so `build_table` raised on `fleet.drop` and the suite was
# red on master — 6 failed, 80 passed, 10 errors — while `make verify` stayed
# green: `integrations/paperclip` is declared in `scripts/pytest-suites.txt`, but
# no gate named it, so only the `scripts/run-pytest-suites.sh` manifest sweep
# reached it, and `make verify` does not run that sweep. A control nothing runs
# is a formality (GR-12).
#
# So this gate has two halves, and either one alone would be a formality:
#
#   * the SUITE runs here, so the mapping cannot be red while the gate of record
#     stays green;
#   * the REFUSAL is PROVOKED. One row is REMOVED (asserted, never a no-op) from a
#     scratch copy of the table, and the parity build must refuse the now
#     unclassified verb BY NAME — while the pristine copy is refused nothing. A
#     gate that never exercises the refusal can pass by observing nothing.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
# A non-zero pytest exit is never collapsed into "the tests failed": pytest 2
# (interrupted), 3 (internal error), 4 (usage error) and 5 (no tests collected)
# all mean the harness could not render a verdict at all, which is CANNOT-ASSESS,
# not NOT-OK. CANNOT-ASSESS is never reported as a pass.
#
# Usage:
#   bash scripts/check-paperclip-control-mapping.sh
#   bash scripts/check-paperclip-control-mapping.sh --table <control_mapping.py>
#
# `--table` judges ONE mapping file and nothing else (no suite run, no
# provocation): rc 0 when it builds, rc 1 when a declared verb is refused by name
# or a row has gone stale, rc 2 when the file or its tree cannot be read. It is
# the seam a plant is measured through — the internal provocation below uses it
# too, so the control and the seam cannot drift apart.
set -u

verb="${AO_CONTROL_MAPPING_VERB:-fleet.drop}"
table=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --table)
      if [ "$#" -lt 2 ]; then
        echo "check-paperclip-control-mapping: CANNOT-ASSESS — --table needs a path" >&2
        exit 2
      fi
      table="$2"; shift 2;;
    --table=*)
      table="${1#--table=}"; shift;;
    -h|--help)
      echo "usage: bash scripts/check-paperclip-control-mapping.sh [--table <control_mapping.py>]"
      exit 0;;
    *)
      echo "check-paperclip-control-mapping: CANNOT-ASSESS — unknown argument: $1" >&2
      exit 2;;
  esac
done

self="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-paperclip-control-mapping: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

suite="integrations/paperclip/tests/test_control_mapping.py"
module="integrations/paperclip/control_mapping.py"
for required in "$suite" "$module" control-plane/control/cli.py control-plane/control/verbs.yaml; do
  if [ ! -e "$required" ]; then
    echo "check-paperclip-control-mapping: CANNOT-ASSESS — $required is missing" >&2
    exit 2
  fi
done

# The mktemp template is assembled at run time: a literal run of the suffix
# character would trip the docs-lint unfinished-marker scan over *.sh files.
scratch_suffix="$(printf 'X%.0s' 1 2 3 4 5 6)"
scratch="$(mktemp -d "/tmp/ao1262mapping.$scratch_suffix")" || {
  echo "check-paperclip-control-mapping: CANNOT-ASSESS — cannot create a scratch directory" >&2
  exit 2
}
trap 'rm -rf "$scratch"' EXIT

# --- the scratch harness ------------------------------------------------------
#
# parity.py builds the table for one tree and reports the outcome as an exit code:
# 0 built, 1 a declared verb was refused by name (or a row went stale), 2 the tree
# could not be read. It is the ONE place the table is judged, so the control below
# and the `--table` seam cannot disagree about what a refusal looks like.
cat > "$scratch/parity.py" <<'PY'
"""Build the control-mapping table for one tree; report a refusal by name.

Exit codes: 0 built / 1 a declared verb was refused by name or a row is stale /
2 the tree could not be read at all.
"""
import sys
from pathlib import Path

sys.dont_write_bytecode = True

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root))

try:
    from integrations.paperclip import control_mapping as cm
except Exception as exc:
    print("CANNOT-ASSESS  the mapping module did not import: %s: %s"
          % (type(exc).__name__, exc))
    raise SystemExit(2)

try:
    registry = cm.load_verbs(root)
except Exception as exc:
    print("CANNOT-ASSESS  the RC-2 registry did not load: %s: %s"
          % (type(exc).__name__, exc))
    raise SystemExit(2)

declared = [str(entry["id"]) for entry in registry.get("verbs") or []]
try:
    rows = cm.build_table(root, registry=registry)
except cm.UnclassifiedControlVerb as exc:
    for verb_id in declared:
        if "'%s'" % verb_id in str(exc):
            print("REFUSED  control-verb-unclassified %s" % verb_id)
            break
    else:
        print("REFUSED  control-verb-unclassified <unnamed>")
    print("  %s" % exc)
    raise SystemExit(1)
except Exception as exc:
    print("CANNOT-ASSESS  build_table raised %s: %s" % (type(exc).__name__, exc))
    raise SystemExit(2)

print("built  %d row(s) for %d declared verb(s)" % (len(rows), len(declared)))
if len(rows) != len(declared):
    print("CANNOT-ASSESS  the table does not cover the registry")
    raise SystemExit(2)

stale = cm.stale_rows(registry)
if stale:
    print("REFUSED  control-verb-stale-row %s" % ", ".join(stale))
    raise SystemExit(1)

counts = {}
for row in rows:
    counts[row.state] = counts.get(row.state, 0) + 1
print("states  %s" % "  ".join("%s=%d" % (key, counts[key]) for key in sorted(counts)))
raise SystemExit(0)
PY

# mutate.py removes ONE row from a copy of the table, and asserts that it did: a
# removal that changed nothing would make the control below vacuous.
cat > "$scratch/mutate.py" <<'PY'
"""Remove one verb's row from a copy of the opinions table — asserted, never a no-op."""
import ast
import sys
from pathlib import Path

source, verb, dest = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
text = source.read_text(encoding="utf-8")

opinions = None
for node in ast.walk(ast.parse(text)):
    if isinstance(node, ast.AnnAssign):
        target, value = node.target, node.value
    elif isinstance(node, ast.Assign):
        target, value = node.targets[0], node.value
    else:
        continue
    if isinstance(target, ast.Name) and target.id == "_OPINIONS":
        opinions = value
        break
if not isinstance(opinions, ast.Dict):
    raise SystemExit("mutate: no _OPINIONS dict literal in %s" % source)

key = value = None
for candidate, entry in zip(opinions.keys, opinions.values):
    if isinstance(candidate, ast.Constant) and candidate.value == verb:
        key, value = candidate, entry
        break
if key is None:
    raise SystemExit("mutate: %r has no row in %s" % (verb, source))

lines = text.splitlines(keepends=True)
del lines[key.lineno - 1:value.end_lineno]
mutant = "".join(lines)
if mutant == text:
    raise SystemExit("mutate: removing %r changed nothing" % verb)
if '"%s"' % verb in mutant:
    raise SystemExit("mutate: %r is still in the mutant" % verb)
dest.write_text(mutant, encoding="utf-8")
print("mutant  removed the %r row (%d -> %d lines)"
      % (verb, len(text.splitlines()), len(mutant.splitlines())))
PY

# build_tree <dest> <table-file> — the smallest tree the table needs to build
# itself from: the RC-2 loader and its registry, the seam closure, and the
# paperclip modules. Nothing here is written into the repo tree.
build_tree() {
  local dest="$1" table_file="$2"
  mkdir -p "$dest/control-plane/control" "$dest/integrations/_seam" \
    "$dest/integrations/paperclip" || return 1
  cp control-plane/control/cli.py control-plane/control/verbs.yaml \
    "$dest/control-plane/control/" || return 1
  cp integrations/_seam/*.py "$dest/integrations/_seam/" || return 1
  cp integrations/paperclip/*.py "$dest/integrations/paperclip/" || return 1
  cp "$table_file" "$dest/integrations/paperclip/control_mapping.py" || return 1
  find "$dest" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null
  return 0
}

parity() { env PYTHONDONTWRITEBYTECODE=1 python3 "$scratch/parity.py" "$1"; }

controls=0
unproven=0
cannot=0

# ctl_rc <label> <expected-rc> [needle] <cmd...>
ctl_rc() {
  local label="$1" expected="$2" needle="" out rc
  shift 2
  case "${1:-}" in
    --naming) needle="$2"; shift 2;;
  esac
  controls=$((controls + 1))
  out="$("$@" 2>&1)"
  rc=$?
  if [ "$rc" -ne "$expected" ]; then
    printf '  FAIL  control %-38s rc=%s, expected %s\n' "$label" "$rc" "$expected" >&2
    printf '%s\n' "$out" | sed 's/^/        /' >&2
    unproven=$((unproven + 1))
    return 0
  fi
  if [ -n "$needle" ]; then
    case "$out" in
      *"$needle"*) ;;
      *)
        printf '  FAIL  control %-38s exited %s without naming %s\n' \
          "$label" "$rc" "$needle" >&2
        printf '%s\n' "$out" | sed 's/^/        /' >&2
        unproven=$((unproven + 1))
        return 0;;
    esac
    printf '  OK    control %-38s rc=%s, naming %s\n' "$label" "$rc" "$needle"
  else
    printf '  OK    control %-38s rc=%s\n' "$label" "$rc"
  fi
  printf '%s\n' "$out" | sed 's/^/        /'
  return 0
}

# --- `--table` mode: judge ONE mapping file, and nothing else -----------------
if [ -n "$table" ]; then
  if [ ! -f "$table" ]; then
    echo "check-paperclip-control-mapping: CANNOT-ASSESS — no such table: $table" >&2
    exit 2
  fi
  echo "== the table under test: $table =="
  build_tree "$scratch/tree" "$table" || {
    echo "check-paperclip-control-mapping: CANNOT-ASSESS — could not build the scratch tree" >&2
    exit 2
  }
  rc=0
  parity "$scratch/tree" || rc=$?
  case "$rc" in
    0) echo "check-paperclip-control-mapping: OK — every declared verb has a row"; exit 0;;
    1) echo "check-paperclip-control-mapping: NOT-OK — the table refused a declared verb" >&2; exit 1;;
    *) echo "check-paperclip-control-mapping: CANNOT-ASSESS — the table was not evaluated" >&2; exit 2;;
  esac
fi

# --- the real table ----------------------------------------------------------
echo "== the committed table =="
build_tree "$scratch/tree" "$module" || {
  echo "check-paperclip-control-mapping: CANNOT-ASSESS — could not build the scratch tree" >&2
  exit 2
}
rc=0
parity "$scratch/tree" > "$scratch/real.out" 2>&1 || rc=$?
if [ "$rc" -eq 0 ]; then
  echo "  OK    every declared verb has a row, built from a tree that is not the repo"
  sed 's/^/        /' "$scratch/real.out"
elif [ "$rc" -eq 1 ]; then
  echo "  FAIL  the committed table refuses a declared verb:" >&2
  sed 's/^/        /' "$scratch/real.out" >&2
  unproven=$((unproven + 1))
else
  echo "  FAIL  the committed table could not be evaluated (rc=$rc) — CANNOT-ASSESS" >&2
  sed 's/^/        /' "$scratch/real.out" >&2
  cannot=$((cannot + 1))
fi

# --- the suite, with the tri-state mapping -----------------------------------
echo "== the suite =="
suite_rc=0
env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q "$suite" \
  > "$scratch/suite.out" 2>&1 || suite_rc=$?
sed 's/^/        /' "$scratch/suite.out"
case "$suite_rc" in
  0) echo "  OK    the control-mapping suite passed" ;;
  1)
    echo "  FAIL  the control-mapping suite failed (rc=1)" >&2
    unproven=$((unproven + 1));;
  *)
    echo "  FAIL  the suite could not render a verdict (pytest rc=$suite_rc) — CANNOT-ASSESS" >&2
    cannot=$((cannot + 1));;
esac

# --- the refusal, provoked ---------------------------------------------------
echo "== the refusal, provoked =="
pristine="$scratch/pristine.py"
cp "$module" "$pristine" || exit 2

# 1. the clean copy is refused nothing — without this, a gate that refuses
#    everything would read as a passing control.
build_tree "$scratch/clean" "$pristine" || {
  echo "check-paperclip-control-mapping: CANNOT-ASSESS — could not build the clean tree" >&2
  exit 2
}
ctl_rc "clean copy builds" 0 parity "$scratch/clean"

# 2. one row REMOVED, and the refusal must name the verb.
if ! python3 "$scratch/mutate.py" "$pristine" "$verb" "$scratch/mutant.py" \
  > "$scratch/mutant.out" 2>&1; then
  echo "check-paperclip-control-mapping: CANNOT-ASSESS — could not plant the mutant:" >&2
  sed 's/^/        /' "$scratch/mutant.out" >&2
  exit 2
fi
sed 's/^/        /' "$scratch/mutant.out"
cp "$scratch/mutant.py" "$scratch/tree/integrations/paperclip/control_mapping.py"
ctl_rc "row removed is refused" 1 --naming "REFUSED  control-verb-unclassified $verb" \
  parity "$scratch/tree"

# 3. the same plant, through the `--table` seam this gate offers an operator —
#    so the control and the seam cannot drift apart.
ctl_rc "gate --table <mutant> refuses" 1 \
  --naming "REFUSED  control-verb-unclassified $verb" bash "$self" --table "$scratch/mutant.py"
ctl_rc "gate --table <pristine> builds" 0 bash "$self" --table "$pristine"

# 4. the two ways this gate must NOT answer "pass": an unreadable table, and an
#    invocation it does not understand.
ctl_rc "gate --table <missing> cannot assess" 2 bash "$self" --table "$scratch/absent.py"
ctl_rc "unknown argument cannot assess" 2 bash "$self" --nonsense

expected_controls=6
if [ "$controls" -ne "$expected_controls" ]; then
  echo "check-paperclip-control-mapping: FAIL — expected $expected_controls controls, ran $controls" >&2
  unproven=$((unproven + 1))
fi

if [ "$unproven" -ne 0 ]; then
  echo "check-paperclip-control-mapping: FAIL — $unproven check(s) did not hold" >&2
  exit 1
fi
if [ "$cannot" -ne 0 ]; then
  echo "check-paperclip-control-mapping: CANNOT-ASSESS — $cannot check(s) rendered no verdict" >&2
  exit 2
fi

echo "check-paperclip-control-mapping: OK — the table classifies every declared verb, $controls control(s) exercised (including the refusal, provoked by name)"
exit 0
