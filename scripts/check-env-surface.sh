#!/usr/bin/env bash
# check-env-surface.sh — the declared environment-variable surface (#944).
#
# The repo had no declared environment surface: no `.env.example`, no
# `ENVIRONMENT.md`, no registry. Variables lived in prose and in code, so one
# could be introduced with no record (the go-live work added `AO_SURFACE_REGISTRY`
# and `AO_EDGE_HOST` exactly that way) and a reader could not enumerate from any
# single place what a deployment needs.
#
# `infra/env/registry.yaml` is that single declaration and `infra/env/surface.py`
# measures it against the tree. This gate is what makes the declaration binding
# rather than decorative: it measures four things, and each refusal below is
# PROVOKED against a mutated declaration (or a planted source) whose unmodified
# twin the same invocation accepts, so no rule can be a formality.
#
#   1. THE DECLARATION IS COMPLETE AND TRUSTWORTHY. A variable a deploy or a local
#      run supplies must be declared with a purpose, whether it is a secret, what
#      supplies it and who reads it; a name declared twice, a `secret` that is not
#      a boolean, a `required_by` outside the closed vocabulary, a reader path
#      that is not in the tree, and an exemption with no reason are each refused
#      BY NAME.
#
#   2. THE DECLARATION AND THE CODE AGREE, IN BOTH DIRECTIONS. A variable the code
#      reads but the declaration does not account for is `undeclared-env-read`
#      (the defect this issue exists for); a declared variable nothing reads is
#      `declared-env-unread` (a stale entry).
#
#   3. THE SCANNER IS NOT A GREP. The console reads its environment through
#      module-level constants (`os.environ.get(JWKS_ENV)` where
#      `JWKS_ENV = "PORTAL_AUTH_GATE_JWKS"`), so a textual scan finds NOTHING in
#      `portal/` while the console reads four variables — a false green of exactly
#      the kind this issue is about. The gate proves the difference: a planted file
#      using the constant idiom is ACCEPTED, and a read the scanner cannot follow
#      (`os.environ.get(name)`) must be RECORDED, with its site count, or it is
#      refused as `indirect-read-not-recorded`.
#
#   4. A DECLARED SECRET IS NEVER CARRIED AS A VALUE (GR-6): a literal for a
#      `secret: true` variable is refused by name, while a reference or a
#      placeholder in the same shape is accepted.
#
#   5. THE DECLARATION HAS ITS OWN TEST SUITE (`infra/env/tests/`). The
#      provocations above drive the CHECKER's refusals; the suite pins the parts
#      they cannot — the schema, every `readers` claim read back, the parser's
#      constant-resolution and the CLI's 0/1/2 contract. The suite is NAMED
#      here literally, on the pytest line below, so `make verify` runs it and
#      `scripts/check-ungated-suites.sh` finds it covered rather than ungated.
#      A suite that is merely declared is not thereby run by any gate.
#
# No network and no containers are required, and nothing is written into the tree:
# every provocation is a scratch copy under /tmp or a planted file outside the
# repository. A gate that dirties the worktree it measures is not a gate.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
# CANNOT-ASSESS must never be reported as a pass.
#
# Usage: bash scripts/check-env-surface.sh
#
# ---knowledge---
# module_id: scripts.check-env-surface
# system: governance
# app: gates
# solution_class: enterprise
# patterns: [tri-state-exit, provoked-negative-control, declared-authority, named-refusal, lane-isolation, schema-validation]
# derives_from: null
# owner_sme: qa-sme
# tier: L1
# interfaces: [exit 0 OK, exit 1 NOT-OK, exit 2 CANNOT-ASSESS]
# invariants: ""
# gotchas: ""
# related: ["#944"]
# do_not_duplicate: null
# ---knowledge---
set -u

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 2

declare -r registry="infra/env/registry.yaml"
declare -r checker="infra/env/surface.py"

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-env-surface: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi
for required in "$registry" "$checker"; do
  if [ ! -f "$required" ]; then
    printf 'check-env-surface: CANNOT-ASSESS — %s is missing, so the surface cannot be measured\n' \
      "$required" >&2
    exit 2
  fi
done

fail=0

# --- 1. the declaration as committed ----------------------------------------
echo "== env-surface =="
base_rc=0
python3 "$checker" check --root "$root" || base_rc=$?
case "$base_rc" in
  0) : ;;
  1)
    echo "check-env-surface: FAIL — the declaration drifts from the tree it measures" >&2
    exit 1
    ;;
  *)
    echo "check-env-surface: CANNOT-ASSESS — the checker returned $base_rc" >&2
    exit 2
    ;;
esac

# --- the provocation harness -------------------------------------------------
# Scratch space: an explicit /tmp path (this box's $TMPDIR is a shared cache that
# is periodically cleaned), removed by the trap on every exit path.
work="/tmp/ao944-env-surface.$$.$(date +%s%N)"
if ! mkdir -p "$work"; then
  echo "check-env-surface: CANNOT-ASSESS — cannot create a scratch directory" >&2
  exit 2
fi
trap 'rm -rf "$work"' EXIT

refuse() { # refuse <name> <expected finding code> <command...>
  local name="$1" expect="$2"
  shift 2
  local out rc=0
  out="$("$@" 2>&1)" || rc=$?
  if [ "$rc" -ne 1 ]; then
    printf '  FAIL  %s: expected a refusal (rc 1), got rc %s\n%s\n' "$name" "$rc" "$out" >&2
    return 1
  fi
  if ! grep -qF -- "$expect" <<<"$out"; then
    printf '  FAIL  %s: refused, but not by name (%s is absent)\n%s\n' "$name" "$expect" "$out" >&2
    return 1
  fi
  printf '  OK    %s is refused by name (%s)\n' "$name" "$expect"
}

accept() { # accept <name> <command...> — the unmodified twin must pass the SAME invocation
  local name="$1"
  shift
  local out rc=0
  out="$("$@" 2>&1)" || rc=$?
  if [ "$rc" -ne 0 ]; then
    printf '  FAIL  %s: the unmodified twin was refused (rc %s)\n%s\n' "$name" "$rc" "$out" >&2
    return 1
  fi
  printf '  OK    %s: the unmodified twin is accepted\n' "$name"
}

# A mutated declaration: <name> <python statement operating on `doc`>.
mutant_registry() {
  local out="$work/$1.yaml" body="$2"
  python3 - "$root/$registry" "$out" "$body" <<'PY'
import pathlib
import sys

import yaml

src, out, body = sys.argv[1], sys.argv[2], sys.argv[3]
doc = yaml.safe_load(pathlib.Path(src).read_text(encoding="utf-8"))
exec(body, {"doc": doc})  # noqa: S102 — the mutation is written here, one statement per control
pathlib.Path(out).write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
PY
  printf '%s' "$out"
}

# The declaration-integrity rules need no tree walk; the parity rules need one.
# Narrowing a PROVOCATION to the rule it targets keeps the gate cheap without
# narrowing the verdict: the unselected `check` above always runs every family.
declare_with() { # declare_with <registry-path>
  python3 "$checker" check-declaration --root "$root" --registry "$1"
}

parity_with() { # parity_with <registry-path>
  python3 "$checker" check-parity --root "$root" --registry "$1"
}

echo "== env-surface: the declaration's own integrity (provoked) =="
integrity_ok=0
refuse "a variable with no usable purpose" "declaration-schema" \
  declare_with "$(mutant_registry nopurpose 'doc["variables"][0]["purpose"] = ""')" || integrity_ok=1
refuse "a secret flag that is not a boolean" "declaration-schema" \
  declare_with "$(mutant_registry strsecret 'doc["variables"][0]["secret"] = "yes"')" || integrity_ok=1
refuse "a variable declared twice" "declaration-schema" \
  declare_with "$(mutant_registry dup 'doc["variables"][1]["name"] = doc["variables"][0]["name"]')" || integrity_ok=1
refuse "a supplier outside the closed vocabulary" "declaration-schema" \
  declare_with "$(mutant_registry badsupplier 'doc["variables"][0]["required_by"] = ["vibes"]')" || integrity_ok=1
refuse "a variable with no recorded reader" "declaration-schema" \
  declare_with "$(mutant_registry noreader 'doc["variables"][0]["readers"] = []')" || integrity_ok=1
refuse "a reader path that is not in the tree" "reader-path-missing" \
  declare_with "$(mutant_registry ghostreader 'doc["variables"][0]["readers"] = ["portal/server/nope.py"]')" || integrity_ok=1
refuse "an exemption with no reason" "exemption-without-reason" \
  declare_with "$(mutant_registry noreason 'doc["exempt"][0]["reason"] = "n/a"')" || integrity_ok=1
accept "the declaration as committed (integrity)" declare_with "$root/$registry" || integrity_ok=1
[ "$integrity_ok" -eq 0 ] || fail=$((fail + 1))

echo "== env-surface: both directions of the parity (provoked) =="
parity_ok=0
refuse "a declared variable nothing reads" "declared-env-unread" \
  parity_with "$(mutant_registry ghostdecl 'doc["variables"].append({"name": "AO_NEVER_READ_ANYWHERE", "purpose": "a variable declared but read by nothing at all", "secret": False, "required_by": ["ops"], "readers": ["README.md"]})')" || parity_ok=1
refuse "an unresolvable read whose site count changed" "indirect-read-not-recorded" \
  parity_with "$(mutant_registry bumped 'doc["indirect_reads"][0]["sites"] = 99')" || parity_ok=1
refuse "an unresolvable read that is recorded nowhere" "indirect-read-not-recorded" \
  parity_with "$(mutant_registry dropped 'doc["indirect_reads"] = doc["indirect_reads"][1:]')" || parity_ok=1
accept "the declaration as committed (parity)" parity_with "$root/$registry" || parity_ok=1
[ "$parity_ok" -eq 0 ] || fail=$((fail + 1))

echo "== env-surface: the scanner is not a grep (provoked) =="
scanner_ok=0
# The console's own idiom: the variable name is a module-level constant, so a
# textual scan would report nothing. This control is what proves the AST is real.
cat >"$work/constant_idiom.py" <<'PY'
import os

JWKS_ENV = "PORTAL_AUTH_GATE_JWKS"
JWKS_FILE_ENV = "PORTAL_AUTH_GATE_JWKS_FILE"
ROOT_ADMIN_ENV = "ROOT_ADMIN_EMAILS"


def environ_mirror() -> str:
    inline = (os.environ.get(JWKS_ENV) or "").strip()
    path = (os.environ.get(JWKS_FILE_ENV) or "").strip()
    return inline or path or (os.environ.get(ROOT_ADMIN_ENV, "") or "")
PY
accept "a read through a module-level constant (the console's idiom)" \
  python3 "$checker" scan-file --root "$root" --file "$work/constant_idiom.py" || scanner_ok=1

cat >"$work/undeclared_read.py" <<'PY'
import os

UNDECLARED_ENV = "AO_TOTALLY_UNDECLARED_BY_ANY_DECLARATION"


def read_it() -> str:
    return os.environ.get(UNDECLARED_ENV, "")
PY
refuse "a variable the declaration does not account for" "undeclared-env-read" \
  python3 "$checker" scan-file --root "$root" --file "$work/undeclared_read.py" || scanner_ok=1

cat >"$work/dynamic_read.py" <<'PY'
import os


def read_it(name: str) -> str:
    return os.environ.get(name, "")
PY
python3 "$checker" scan-file --root "$root" --file "$work/dynamic_read.py" >"$work/dynamic.out" 2>&1
dynamic_rc=$?
if [ "$dynamic_rc" -eq 1 ] && grep -qF "undeclared-env-read" "$work/dynamic.out"; then
  printf '  FAIL  a dynamic read was reported as a NAMED undeclared variable (it is not one)\n' >&2
  scanner_ok=1
else
  echo "  OK    a read with a dynamic name is not mistaken for a named variable"
fi
[ "$scanner_ok" -eq 0 ] || fail=$((fail + 1))

echo "== env-surface: a declared secret is never carried as a value (provoked) =="
secret_ok=0
# Two declared-secret names exist in the registry; the fixture names one of them
# with a value, and the placeholder/indirection twins in the same shape are what
# keep the rule from refusing every mention of the variable.
printf '%s\n' 'KEYDB_PASSWORD=Swordfish-42' >"$work/leak.env"
printf '%s\n' 'KEYDB_PASSWORD=change-me-before-deploy' >"$work/placeholder.env"
printf '%s\n' 'KEYDB_PASSWORD=${KEYDB_PASSWORD:-}' >"$work/indirect.env"
refuse "a literal value for a declared secret" "secret-literal-in-tree" \
  python3 "$checker" scan-secret --root "$root" --file "$work/leak.env" || secret_ok=1
accept "a placeholder in the same shape" \
  python3 "$checker" scan-secret --root "$root" --file "$work/placeholder.env" || secret_ok=1
accept "an indirection from the environment in the same shape" \
  python3 "$checker" scan-secret --root "$root" --file "$work/indirect.env" || secret_ok=1
[ "$secret_ok" -eq 0 ] || fail=$((fail + 1))

# --- 2. the declaration's own test suite -------------------------------------
echo "== env-surface: the declaration's own test suite =="
suite_ok=0
if python3 -c 'import pytest' >/dev/null 2>&1; then
  suite_rc=0
  suite_out="$(env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest infra/env/tests -q -p no:cacheprovider 2>&1)" || suite_rc=$?
  case "$suite_rc" in
    0)
      printf '  OK    the suite passes: %s\n' "$(printf '%s\n' "$suite_out" | tail -1)"
      ;;
    2)
      # pytest exits 2 for INTERRUPTED (SIGINT / KeyboardInterrupt) — the verdict a
      # foreground gate in this box's shared shell gets from a neighbouring lane's
      # Ctrl-C. An interrupted run assessed NOTHING, so it is CANNOT-ASSESS: never
      # a pass, and never a FAIL that sends an operator after a defect that is not
      # there (the lesson issue #1082 records).
      printf 'env-surface: CANNOT-ASSESS — the test suite was interrupted (rc 2), so it assessed nothing\n%s\n' "$suite_out" >&2
      exit 2
      ;;
    5)
      printf '  FAIL  the test suite collected no tests (rc 5), so it proves nothing\n%s\n' "$suite_out" >&2
      suite_ok=1
      ;;
    *)
      printf '  FAIL  the declaration is red against its own suite (rc %s)\n%s\n' "$suite_rc" "$suite_out" >&2
      suite_ok=1
      ;;
  esac
else
  printf 'env-surface: CANNOT-ASSESS — pytest is not importable, so the suite cannot run\n' >&2
  exit 2
fi
[ "$suite_ok" -eq 0 ] || fail=$((fail + 1))

# --- 3. the acceptance criterion the issue names -----------------------------
echo "== env-surface: the issue's own acceptance criterion =="
criterion_ok=0
missing=""
for name in AO_SURFACE_REGISTRY AO_EDGE_HOST PORTAL_AUTH_GATE_JWKS ROOT_ADMIN_EMAILS; do
  if ! grep -qF -- "$name" "$root/$registry"; then
    missing="${missing}${missing:+, }$name"
  fi
done
if [ -n "$missing" ]; then
  printf '  FAIL  the declared env surface does not name: %s\n' "$missing" >&2
  criterion_ok=1
else
  echo "  OK    the declared surface names all four variables the issue names"
fi
[ "$criterion_ok" -eq 0 ] || fail=$((fail + 1))

# --- 4. the wiring ----------------------------------------------------------
echo "== env-surface: the check is wired, not a formality =="
wiring_ok=0
if bash -c 'source scripts/discover-checks.sh >/dev/null 2>&1; discover_check_scripts' |
  grep -q '^env-surface|'; then
  echo "  OK    scripts/verify.sh discovers this check (no hand-edit to the array)"
else
  printf '  FAIL  the check is not discovered by scripts/verify.sh\n' >&2
  wiring_ok=1
fi
if [ -f scripts/check-denylist.txt ] && grep -qE '(^|[[:space:]])env-surface([[:space:]]|$)' scripts/check-denylist.txt; then
  printf '  FAIL  the check is denylisted, so the gate of record does not run it\n' >&2
  wiring_ok=1
else
  echo "  OK    the check is not denylisted"
fi
[ "$wiring_ok" -eq 0 ] || fail=$((fail + 1))

if [ "$fail" -ne 0 ]; then
  echo "check-env-surface: FAIL — the declared environment surface is not holding" >&2
  exit 1
fi
echo "check-env-surface: OK — the declaration is closed over the tree, every refusal is provoked, and the issue's criterion holds"
exit 0
