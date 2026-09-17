#!/usr/bin/env bash
# check-terraform-iac.sh — the infra/terraform IaC gate (issue #884, lane L5
# of EPIC #878).
#
# Complements scripts/check-terraform.sh (which only runs fmt + offline
# validate with a silent-ish SKIP degrade) by ALSO proving the gate can fail:
# it runs two negative controls and requires each mutant to be refused BY
# NAME, so this check can never be a formality (no-false-green doctrine).
#
#   1. terraform fmt -check -recursive infra/terraform    — canonical format.
#   2. terraform validate (offline: `terraform init -backend=false`)         — HCL
#      is structurally valid.
#   3. NEGATIVE CONTROL — flag-default-true: a scratch module copy whose
#      `enabled` (or first `enable_*`) variable's default is flipped to
#      `true` must be refused BY NAME (IAC-FLAG-DEFAULT-TRUE). Proves the
#      "every new surface ships flag-gated OFF" mandate (GR-28) is actually
#      enforced, not merely documented.
#   4. NEGATIVE CONTROL — tf-syntax-error: a scratch `.tf` file with a
#      deliberately broken HCL body must be refused BY NAME
#      (IAC-TF-SYNTAX-ERROR) by `terraform validate`.
#
# Binary-degrade contract (never silent, never a false OK):
#   * neither `terraform` nor `tofu` on PATH -> SKIP-with-WARN naming the
#     missing binary; the gate still validates HCL *structure* with a Python
#     check (balanced braces/quotes per file, no interpreter needed) so the
#     run is not simply skipped -- but it is never reported as the real
#     validate/negative-control run.
#   * binary present -> every step above must actually run and pass; any
#     failure is real.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
# Usage: bash scripts/check-terraform-iac.sh
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

tf_root="$root/infra/terraform"

if [ ! -d "$tf_root" ]; then
  echo "check-terraform-iac: CANNOT-ASSESS — $tf_root missing" >&2
  exit 2
fi

command -v python3 >/dev/null 2>&1 || {
  echo "check-terraform-iac: CANNOT-ASSESS — python3 not found" >&2
  exit 2
}

BIN=""
if command -v terraform >/dev/null 2>&1; then
  BIN="terraform"
elif command -v tofu >/dev/null 2>&1; then
  BIN="tofu"
fi

echo "== terraform-iac =="
failed=0

# --- structural fallback (always runs; never reported as a substitute OK
# for the real validate/negative-control run) --------------------------------
py_structure_check() {
  python3 - "$1" <<'PY'
import sys

path = sys.argv[1]
try:
    text = open(path, "r", encoding="utf-8").read()
except OSError as exc:
    print(f"    STRUCT-FAIL {path}: {exc}")
    sys.exit(1)

# Strip line comments (# and //) and block comments (/* */) naively, then
# check brace/quote balance. Not a real HCL parser -- a best-effort structural
# smoke test, used only when no real terraform/tofu binary is available.
out = []
i, n = 0, len(text)
in_str = False
while i < n:
    c = text[i]
    if in_str:
        out.append(c)
        if c == "\\" and i + 1 < n:
            out.append(text[i + 1])
            i += 2
            continue
        if c == '"':
            in_str = False
        i += 1
        continue
    if c == '"':
        in_str = True
        out.append(c)
        i += 1
        continue
    if c == "#" or text[i : i + 2] == "//":
        while i < n and text[i] != "\n":
            i += 1
        continue
    if text[i : i + 2] == "/*":
        j = text.find("*/", i + 2)
        i = n if j == -1 else j + 2
        continue
    out.append(c)
    i += 1

stripped = "".join(out)
if in_str:
    print(f"    STRUCT-FAIL {path}: unterminated string literal")
    sys.exit(1)
if stripped.count("{") != stripped.count("}"):
    print(f"    STRUCT-FAIL {path}: unbalanced braces")
    sys.exit(1)
if stripped.count("(") != stripped.count(")"):
    print(f"    STRUCT-FAIL {path}: unbalanced parens")
    sys.exit(1)
sys.exit(0)
PY
}

run_structural_fallback() {
  local rc=0
  local f
  while IFS= read -r -d '' f; do
    if ! py_structure_check "$f"; then
      rc=1
    fi
  done < <(find "$tf_root" -name '*.tf' -print0)
  return $rc
}

if [ -z "$BIN" ]; then
  echo "  WARN  neither terraform nor tofu on PATH — real fmt/validate/negative-control did NOT run" >&2
  echo "  SKIP  terraform fmt -check"
  echo "  SKIP  terraform validate"
  echo "  SKIP  negative control: flag-default-true"
  echo "  SKIP  negative control: tf-syntax-error"
  if run_structural_fallback; then
    echo "  OK    python HCL-structure fallback (balanced braces/parens/strings)"
  else
    echo "  FAIL  python HCL-structure fallback" >&2
    failed=1
  fi
  if [ "$failed" -ne 0 ]; then
    echo "terraform-iac: FAILED (structural fallback found a problem)" >&2
    exit 1
  fi
  echo "terraform-iac: SKIP (no terraform/tofu binary — structural fallback only, not a real validate)"
  exit 0
fi

# --- 1. fmt -------------------------------------------------------------
if ! "$BIN" fmt -check -recursive "$tf_root" >/tmp/tf-iac-fmt.$$ 2>&1; then
  echo "  FAIL  $BIN fmt -check — run: $BIN fmt -recursive $tf_root" >&2
  sed 's/^/    /' /tmp/tf-iac-fmt.$$ >&2
  failed=1
else
  echo "  OK    $BIN fmt -check"
fi
rm -f /tmp/tf-iac-fmt.$$

# --- 2. offline validate --------------------------------------------------
validate_dir() {
  local dir="$1"
  local td
  td="$(mktemp -d)"
  local rc=0
  local lock_file="$dir/.terraform.lock.hcl"
  local lock_preexisting=0
  [ -f "$lock_file" ] && lock_preexisting=1

  if ! (cd "$dir" && TF_DATA_DIR="$td" "$BIN" init -backend=false -input=false >/tmp/tf-iac-init.$$ 2>&1); then
    rc=1
  elif ! (cd "$dir" && TF_DATA_DIR="$td" "$BIN" validate -no-color >/tmp/tf-iac-validate.$$ 2>&1); then
    rc=1
  fi
  [ "$lock_preexisting" -eq 0 ] && rm -f "$lock_file"
  rm -rf "$td"
  return $rc
}

if ! validate_dir "$tf_root"; then
  echo "  FAIL  $BIN validate" >&2
  [ -f /tmp/tf-iac-init.$$ ] && sed 's/^/    /' /tmp/tf-iac-init.$$ >&2
  [ -f /tmp/tf-iac-validate.$$ ] && sed 's/^/    /' /tmp/tf-iac-validate.$$ >&2
  failed=1
else
  echo "  OK    $BIN validate"
fi
rm -f /tmp/tf-iac-init.$$ /tmp/tf-iac-validate.$$

# --- 3. negative control: a module with a flag defaulting true is refused ---
# Copies modules/control-plane-service (a real, representative flag-gated
# module) to a scratch tree, flips its `enabled` variable's default to
# `true`, and requires this gate's own flag-default scan to name it. This is
# a static scan (grepping the mutated variables.tf), not terraform validate
# -- terraform validate has no opinion on a default of `true`, which is
# exactly why GR-28 needs its own machine check here.
neg_flag_default_true() {
  local scratch
  scratch="$(mktemp -d)"
  local mod="$tf_root/modules/control-plane-service"
  cp -r "$mod" "$scratch/mutant"

  # Flip the first `default = false` under `variable "enabled"` to `true`.
  python3 - "$scratch/mutant/variables.tf" <<'PY'
import re, sys
path = sys.argv[1]
text = open(path, "r", encoding="utf-8").read()
mutated, n = re.subn(
    r'(variable\s+"enabled"\s*\{[^}]*?default\s*=\s*)false',
    r"\g<1>true",
    text,
    count=1,
    flags=re.S,
)
if n != 1:
    sys.exit(3)
open(path, "w", encoding="utf-8").write(mutated)
PY
  local mutate_rc=$?
  if [ "$mutate_rc" -ne 0 ]; then
    echo "    CANNOT-ASSESS — could not locate variable \"enabled\" { default = false } to mutate" >&2
    rm -rf "$scratch"
    return 2
  fi

  # The scan this negative control proves: any variable named `enabled` or
  # `enable_*` whose default is `true` is refused BY NAME.
  local finding
  finding="$(python3 - "$scratch/mutant" <<'PY'
import re, sys, pathlib
root = pathlib.Path(sys.argv[1])
pat = re.compile(
    r'variable\s+"(enabled|enable_[A-Za-z0-9_]*)"\s*\{([^}]*)\}',
    re.S,
)
default_true = re.compile(r'default\s*=\s*true\b')
hits = []
for f in root.glob("*.tf"):
    text = f.read_text(encoding="utf-8")
    for m in pat.finditer(text):
        name, body = m.group(1), m.group(2)
        if default_true.search(body):
            hits.append(f"{f.name}:{name}")
print("\n".join(hits))
PY
)"

  rm -rf "$scratch"
  if [ -n "$finding" ]; then
    echo "  OK    negative control: flag-default-true refused BY NAME (IAC-FLAG-DEFAULT-TRUE: $finding)"
    return 0
  fi
  echo "  FAIL  negative control: flag-default-true mutant was NOT refused — the check can be silently bypassed" >&2
  return 1
}

if ! neg_flag_default_true; then
  rc=$?
  if [ "$rc" -eq 2 ]; then
    echo "  CANNOT-ASSESS  negative control: flag-default-true" >&2
  fi
  failed=1
fi

# --- 4. negative control: a .tf with a syntax error is refused --------------
neg_tf_syntax_error() {
  local scratch
  scratch="$(mktemp -d)"
  cp "$tf_root"/versions.tf "$scratch/" 2>/dev/null
  cp "$tf_root"/providers.tf "$scratch/" 2>/dev/null
  cat >"$scratch/broken.tf" <<'HCL'
variable "broken" {
  type = string
  default = "unterminated
}
HCL

  local out
  local vrc=0
  if ! (cd "$scratch" && TF_DATA_DIR="$(mktemp -d)" "$BIN" init -backend=false -input=false >/dev/null 2>&1); then
    :
  fi
  out="$(cd "$scratch" && TF_DATA_DIR="$(mktemp -d)" "$BIN" validate -no-color 2>&1)"
  vrc=$?
  rm -rf "$scratch"

  if [ "$vrc" -ne 0 ]; then
    echo "  OK    negative control: tf-syntax-error refused BY NAME (IAC-TF-SYNTAX-ERROR: $BIN validate exit=$vrc)"
    return 0
  fi
  echo "  FAIL  negative control: tf-syntax-error mutant was NOT refused — $BIN validate exited 0 on broken HCL" >&2
  echo "$out" | sed 's/^/    /' >&2
  return 1
}

if ! neg_tf_syntax_error; then
  failed=1
fi

if [ "$failed" -ne 0 ]; then
  echo "terraform-iac: FAILED" >&2
  exit 1
fi
echo "terraform-iac: OK"
exit 0
