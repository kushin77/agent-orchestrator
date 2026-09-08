#!/usr/bin/env bash
# cloudbuild gate for `make verify` (issue #6): infra/cloudbuild declarations
# must parse, and every CI/CD trigger must ship OFF — `disabled: true` with an
# `_ENABLE_*` substitution of "false" mirroring the OFF default in
# infra/feature-flags/registry.yaml (flag-gated, GR-5). Exit 0 = valid; exit 1
# = invalid. A gate that cannot fail is a formality (no-false-green).
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 1

cb_dir="infra/cloudbuild"
fail=0

echo "== cloudbuild =="

# Every YAML under infra/cloudbuild must parse. Trigger files are asserted in
# detail by check_trigger below (which prints its own OK line), so skip them in
# the generic parse loop to avoid duplicate status lines.
yaml_fail=0
yaml_count=0
while IFS= read -r f; do
  case "$f" in
    */verify-trigger.yaml|*/apply-trigger.yaml) continue ;;
  esac
  yaml_count=$((yaml_count + 1))
  if python3 -c "import sys,yaml; yaml.safe_load(open(sys.argv[1], encoding='utf-8'))" "$f" 2>/dev/null; then
    printf '  OK    %s (parses)\n' "$f"
  else
    printf '  FAIL  %s (yaml parse)\n' "$f" >&2
    yaml_fail=$((yaml_fail + 1))
  fi
done < <(find "$cb_dir" -maxdepth 1 -name '*.yaml' -type f | LC_ALL=C sort)
fail=$((fail + yaml_fail))

# Trigger assertions (each importable trigger ships disabled with its flag off).
check_trigger() {
  local file="$1"
  local flag="$2"
  local build_config="$3"
  local rc=0
  if [ ! -f "$file" ]; then
    printf '  FAIL  %s (missing trigger file)\n' "$file" >&2
    return 1
  fi
  python3 - "$file" "$flag" "$build_config" <<'PY'
import os, sys, yaml

path, flag, build_config = sys.argv[1], sys.argv[2], sys.argv[3]
rel = os.path.relpath(path)
errs = []
try:
    with open(path, encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)
except Exception as exc:
    print(f"  FAIL  {rel} (yaml parse: {exc})", file=sys.stderr)
    sys.exit(1)

if not isinstance(doc, dict):
    errs.append(f"{rel}: not a mapping")
else:
    if doc.get("disabled") is not True:
        errs.append(f"{rel}: must ship disabled: true (GR-5)")
    subs = doc.get("substitutions") or {}
    if subs.get(flag) != "false":
        errs.append(f"{rel}: substitution {flag} must be \"false\"")
    fn = doc.get("filename")
    if not fn:
        errs.append(f"{rel}: missing filename")
    elif not os.path.exists(fn):
        errs.append(f"{rel}: referenced build config '{fn}' not found")

for e in errs:
    print(f"  FAIL  {e}", file=sys.stderr)
sys.exit(1 if errs else 0)
PY
  rc=$?
  if [ "$rc" -eq 0 ]; then
    printf '  OK    %s (disabled, %s=false)\n' "$file" "$flag"
  fi
  return "$rc"
}

check_trigger "$cb_dir/verify-trigger.yaml" _ENABLE_VERIFY "$cb_dir/verify.yaml" || fail=$((fail + 1))
check_trigger "$cb_dir/apply-trigger.yaml"  _ENABLE_APPLY  "$cb_dir/apply.yaml"  || fail=$((fail + 1))

if [ "$fail" -ne 0 ]; then
  printf 'cloudbuild: %s problem(s)\n' "$fail" >&2
  exit 1
fi
echo "cloudbuild: OK"
