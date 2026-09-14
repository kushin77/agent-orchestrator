#!/usr/bin/env bash
# check-gateway-catalog-parity.sh — every registered gateway provider adapter
# must have a gateway-owned module-catalog entry, and every catalog module must
# name a registered provider (issue #349).
#
# The catalog under gateway/catalog/modules/ is the machine-readable menu of
# what the Model Gateways pillar ships. A shipped adapter with no catalog entry
# (an invisible provider) and a catalog entry with no adapter behind it (a
# phantom provider) are both defects — neither is a skip (no-false-green
# doctrine, GR-12).
#
# The registered provider set is read FROM THE REGISTRY
# (gateway/providers/registry.py, PROVIDER_NAMES), never derived by globbing
# filenames, so shared plumbing (base.py, contract.py, registry.py, ...) is
# correctly excluded from the parity set. A module declares the adapter it backs
# through `distribution.package` = `gateway.providers.<provider>`; the provider
# id is that suffix (so the module id need not equal the provider id — the
# `claude-anthropic` module backs the `anthropic` provider).
#
# The gate then runs its own negative control: it copies the module set to a
# scratch tree and mutates it in BOTH directions — removing a module, and adding
# an orphan module — asserting the copy really changed and requiring the
# validator to refuse each mutant. If a mutant passes, this gate reports FAIL —
# a check that cannot fail is a formality.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
# CANNOT-ASSESS must never be reported as a pass.
#
# Usage: bash scripts/check-gateway-catalog-parity.sh [--modules-dir DIR]
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

modules_dir="$root/gateway/catalog/modules"
if [ "${1:-}" = "--modules-dir" ]; then
  if [ -z "${2:-}" ]; then
    echo "check-gateway-catalog-parity: CANNOT-ASSESS — --modules-dir needs a path" >&2
    exit 2
  fi
  modules_dir="$2"
elif [ -n "${1:-}" ]; then
  echo "check-gateway-catalog-parity: CANNOT-ASSESS — unknown argument $1" >&2
  exit 2
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-gateway-catalog-parity: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

# validate <modules-dir> — registered providers vs catalog modules.
# 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
validate() {
  python3 - "$root" "$1" <<'PY'
import json
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
modules_dir = Path(sys.argv[2]).resolve()
prefix = "gateway.providers."

# Registered providers come FROM THE REGISTRY, never from a filename glob.
sys.path.insert(0, str(root / "gateway"))
try:
    from providers.registry import PROVIDER_NAMES
except Exception as exc:  # noqa: BLE001 - any import failure is CANNOT-ASSESS
    print(
        "check-gateway-catalog-parity: CANNOT-ASSESS — cannot import the "
        f"provider registry: {exc}",
        file=sys.stderr,
    )
    raise SystemExit(2)

registered = set(PROVIDER_NAMES)
if not registered:
    print(
        "check-gateway-catalog-parity: CANNOT-ASSESS — the provider registry "
        "reports no registered providers",
        file=sys.stderr,
    )
    raise SystemExit(2)

if not modules_dir.is_dir():
    print(
        f"check-gateway-catalog-parity: FAIL — no catalog modules directory at "
        f"{modules_dir}",
        file=sys.stderr,
    )
    raise SystemExit(1)

modules = {}
findings = []
for entry in sorted(modules_dir.iterdir()):
    if not entry.is_dir():
        continue
    manifest = entry / "module.json"
    try:
        rel = manifest.relative_to(root)
    except ValueError:
        rel = manifest
    if not manifest.is_file():
        findings.append(f"{entry.name}/: no module.json")
        continue
    try:
        doc = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        findings.append(f"{rel}: unreadable module.json ({exc})")
        continue
    package = (doc.get("distribution") or {}).get("package")
    provider = None
    if isinstance(package, str) and package.startswith(prefix):
        provider = package[len(prefix):]
    if provider is None and doc.get("id") in registered:
        provider = doc.get("id")
    modules[entry.name] = (provider, str(rel))

covered = {p for p, _ in modules.values() if p}
for missing in sorted(registered - covered):
    findings.append(
        f"registered provider {missing!r} has no catalog module (expected "
        f"gateway/catalog/modules/<id>/module.json with distribution.package "
        f"{prefix + missing!r})"
    )
for module_name, (provider, rel) in sorted(modules.items()):
    if provider is None:
        findings.append(
            f"{rel}: declares no registered gateway provider "
            f"(distribution.package must be {prefix!r} + the provider id)"
        )
    elif provider not in registered:
        findings.append(
            f"{rel}: declares provider {provider!r}, which is not registered"
        )

if findings:
    for finding in findings:
        print(f"  FAIL  {finding}", file=sys.stderr)
    raise SystemExit(1)

print(
    f"  OK    {len(registered)} registered provider(s) each have a catalog "
    f"module; {len(modules)} module(s) each name a registered provider"
)
raise SystemExit(0)
PY
}

validate "$modules_dir"
rc=$?
case "$rc" in
  0) : ;;
  1)
    echo "check-gateway-catalog-parity: FAIL — the provider catalog is not in parity with the registry (see findings above)" >&2
    exit 1
    ;;
  *)
    echo "check-gateway-catalog-parity: CANNOT-ASSESS — validator returned $rc" >&2
    exit 2
    ;;
esac

# --- negative control: the gate must be able to fail in both directions ------
if ! command -v sha256sum >/dev/null 2>&1; then
  echo "check-gateway-catalog-parity: CANNOT-ASSESS — sha256sum not found (cannot prove the mutation)" >&2
  exit 2
fi

work="/tmp/ao349.$$.$(date +%s)"
if ! mkdir -p "$work"; then
  echo "check-gateway-catalog-parity: CANNOT-ASSESS — cannot create a scratch directory" >&2
  exit 2
fi
trap 'rm -rf "$work"' EXIT

count_modules() {
  find "$1" -mindepth 2 -maxdepth 2 -name module.json 2>/dev/null | wc -l
}
base_count="$(count_modules "$modules_dir")"
if [ "$base_count" -eq 0 ]; then
  echo "check-gateway-catalog-parity: CANNOT-ASSESS — no module.json under $modules_dir" >&2
  exit 2
fi

# Mutation A — remove one module (a registered provider loses its entry).
cp -R "$modules_dir" "$work/modules-a"
removed_dir="$(find "$work/modules-a" -mindepth 1 -maxdepth 1 -type d | LC_ALL=C sort | head -1)"
if [ -z "$removed_dir" ]; then
  echo "check-gateway-catalog-parity: CANNOT-ASSESS — no module directory to remove" >&2
  exit 2
fi
removed_name="$(basename "$removed_dir")"
rm -rf "$removed_dir"
if [ "$(count_modules "$work/modules-a")" -ge "$base_count" ]; then
  echo "check-gateway-catalog-parity: CANNOT-ASSESS — the removal mutation did not change the input" >&2
  exit 2
fi
out_a="$(validate "$work/modules-a" 2>&1)"
rc_a=$?
if [ "$rc_a" -eq 1 ] && printf '%s\n' "$out_a" | grep -q "has no catalog module"; then
  echo "  OK    negative control A: removing module '$removed_name' is refused ($(count_modules "$work/modules-a") of $base_count entries remain)"
else
  echo "check-gateway-catalog-parity: FAIL — negative control A passed; removing a module was not caught (the gate cannot fail)" >&2
  printf '%s\n' "$out_a" >&2
  exit 1
fi

# Mutation B — add an orphan module (a catalog entry with no adapter).
cp -R "$modules_dir" "$work/modules-b"
mkdir -p "$work/modules-b/zzz-orphan"
cat > "$work/modules-b/zzz-orphan/module.json" <<'JSON'
{
  "schema": "cmr.module/v1",
  "id": "zzz-orphan",
  "name": "zzz-orphan",
  "distribution": { "language": "python", "package": "gateway.providers.zzz-orphan" }
}
JSON
if [ "$(count_modules "$work/modules-b")" -le "$base_count" ]; then
  echo "check-gateway-catalog-parity: CANNOT-ASSESS — the orphan mutation did not change the input" >&2
  exit 2
fi
out_b="$(validate "$work/modules-b" 2>&1)"
rc_b=$?
if [ "$rc_b" -eq 1 ] && printf '%s\n' "$out_b" | grep -q "zzz-orphan"; then
  echo "  OK    negative control B: an orphan catalog module is refused ($(count_modules "$work/modules-b") of $base_count entries)"
else
  echo "check-gateway-catalog-parity: FAIL — negative control B passed; an orphan module was not caught (the gate cannot fail)" >&2
  printf '%s\n' "$out_b" >&2
  exit 1
fi

echo "check-gateway-catalog-parity: OK — the provider catalog is in parity with the registry, and both mutants are refused"
exit 0
