#!/usr/bin/env bash
# check-paperclip-secrets.sh — the per-agent secret vault gate (issue #417).
#
# The fleet's first-class secret primitive is a GSM-backed reference/rotation
# VIEW: it NAMES a secret (its GSM path, the agent and scope it belongs to, when
# it was last rotated and which agent consumes it) and never carries the value.
# Secret Manager stays the store of record. A primitive nothing validates is a
# formality (no-false-green doctrine, GR-12), so this gate fails, by name, when
# the view drifts from the four rules:
#
#   * a value carried instead of a path reference  -> refused, naming the key
#     (GR-6; the value is never read, echoed or put in a finding);
#   * a second store introduced                    -> refused, naming the store
#     (upstream's store is NOT our authority — ADR-0012);
#   * an unscoped read                             -> refused, naming the scope
#     (a read requires an authenticated, scoped caller);
#   * an orphaned (consumer-less) secret           -> reported, naming its path
#     (a secret with no consumer is never silently kept).
#
# It also runs its own negative control: it takes the deterministic view built
# from the tree, breaks it three times — a value added, the store re-pointed, a
# consumer dropped — asserts each mutation changed the bytes (and the sha256),
# requires the validator to refuse/report each by name, and re-validates the
# rebuilt baseline so the control leaves nothing mutated. The read guard is
# exercised once negatively (unscoped, must be refused) and once positively (the
# same read with the scope must succeed and return a reference, not a value): a
# guard that only ever refuses is as uninformative as one that never does.
#
# No credential is ever created, read, written or printed by this gate — the
# declarations it exercises are shapes-only placeholders under the reserved
# `example` project.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
# CANNOT-ASSESS must never be reported as a pass.
#
# Usage: bash scripts/check-paperclip-secrets.sh
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-paperclip-secrets: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

if [ ! -d "$root/integrations/paperclip/adapters/secrets" ]; then
  echo "check-paperclip-secrets: FAIL — integrations/paperclip/adapters/secrets/ is missing" >&2
  exit 1
fi

schema="integrations/paperclip/adapters/secrets/schema/secret.schema.json"
if [ ! -f "$schema" ]; then
  echo "check-paperclip-secrets: CANNOT-ASSESS — no view schema at $schema" >&2
  exit 2
fi

catalog="integrations/paperclip/adapters/secrets/catalog/secrets.json"
if [ ! -f "$catalog" ]; then
  echo "check-paperclip-secrets: CANNOT-ASSESS — no declaration catalog at $catalog" >&2
  exit 2
fi

# validate <root> [view.json] — 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
# With no view file the view is built from the tree (the projection). With a
# view file, only that document is validated (used by the negative control).
validate() {
  python3 - "$@" <<'PY'
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
view_path = Path(sys.argv[2]) if len(sys.argv) > 2 else None
sys.path.insert(0, str(root))

from integrations.paperclip.adapters.secrets import vault  # noqa: E402
from integrations.paperclip.adapters.secrets.model import SecretError  # noqa: E402

try:
    if view_path is not None:
        import json
        view = json.loads(view_path.read_text(encoding="utf-8"))
        findings = vault.validate_view(view, root=root)
    else:
        document = vault.load_catalog(root)
        view = vault.build_view(root)
        findings = vault.validate_view(view, root=root)
        findings.extend(vault.catalog_findings(document))
except SecretError as exc:
    print(f"  FAIL  {exc}", file=sys.stderr)
    raise SystemExit(1)
except Exception as exc:  # noqa: BLE001 — an unreadable tree is NOT-OK
    print(f"  FAIL  the view could not be built: {type(exc).__name__}", file=sys.stderr)
    raise SystemExit(1)

if findings:
    for finding in findings:
        print(f"  FAIL  {finding}", file=sys.stderr)
    raise SystemExit(1)

count = len(view.get("secrets") or [])
print(
    f"  OK    {count} secret(s) named by GSM path: one store of record, no value carried, "
    "every secret has a consumer"
)
raise SystemExit(0)
PY
}

rc=0
validate "$root" || rc=$?
case "$rc" in
  0) : ;;
  1)
    echo "check-paperclip-secrets: FAIL — the secret view drifts from the primitive" >&2
    exit 1
    ;;
  *)
    echo "check-paperclip-secrets: CANNOT-ASSESS — validator returned $rc" >&2
    exit 2
    ;;
esac

# --- negative control ---------------------------------------------------------
scratch="/tmp/ao417-secrets.$(date +%s%N).$"
if ! mkdir "$scratch" 2>/dev/null; then
  echo "check-paperclip-secrets: CANNOT-ASSESS — cannot create a scratch directory" >&2
  exit 2
fi
trap 'rm -rf "$scratch"' EXIT

# The mutation payload is an obviously-fake placeholder; it is never printed,
# never written into the repository and never read by the validator.
fake="fake-placeholder-not-a-credential"

shas="$(python3 - "$root" "$scratch" "$fake" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
scratch = Path(sys.argv[2])
fake = sys.argv[3]
sys.path.insert(0, str(root))

from integrations.paperclip.adapters.secrets import vault  # noqa: E402


def dump(obj, name):
    payload = json.dumps(obj, indent=2, sort_keys=True).encode("utf-8")
    (scratch / name).write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


view = vault.build_view(root)
base_sha = dump(view, "baseline.json")

# mutation A: carry a VALUE where a name belongs.
mutant_a = json.loads(json.dumps(view))
mutant_a["secrets"][0]["value"] = fake
sha_a = dump(mutant_a, "mutant_value.json")

# mutation B: re-point a record at a SECOND store.
mutant_b = json.loads(json.dumps(view))
mutant_b["secrets"][0]["store"] = "upstream-paperclip-secret-store"
sha_b = dump(mutant_b, "mutant_store.json")

# mutation C: drop a consumer — an orphaned secret.
mutant_c = json.loads(json.dumps(view))
mutant_c["secrets"][0]["consumer"] = None
sha_c = dump(mutant_c, "mutant_orphan.json")

print(f"BASE_SHA={base_sha}")
print(f"MUT_VALUE_SHA={sha_a}")
print(f"MUT_STORE_SHA={sha_b}")
print(f"MUT_ORPHAN_SHA={sha_c}")
PY
)"

base_sha="$(printf '%s\n' "$shas" | sed -n 's/^BASE_SHA=//p')"
mut_value_sha="$(printf '%s\n' "$shas" | sed -n 's/^MUT_VALUE_SHA=//p')"
mut_store_sha="$(printf '%s\n' "$shas" | sed -n 's/^MUT_STORE_SHA=//p')"
mut_orphan_sha="$(printf '%s\n' "$shas" | sed -n 's/^MUT_ORPHAN_SHA=//p')"

if [ -z "$base_sha" ] || [ -z "$mut_value_sha" ] || [ -z "$mut_store_sha" ] || [ -z "$mut_orphan_sha" ]; then
  echo "check-paperclip-secrets: CANNOT-ASSESS — could not build the mutants" >&2
  printf '%s\n' "$shas" >&2
  exit 2
fi
if [ "$base_sha" = "$mut_value_sha" ] || [ "$base_sha" = "$mut_store_sha" ] || [ "$base_sha" = "$mut_orphan_sha" ]; then
  echo "check-paperclip-secrets: CANNOT-ASSESS — a mutation did not change the bytes" >&2
  printf '%s\n' "$shas" >&2
  exit 2
fi

out_a="$(validate "$root" "$scratch/mutant_value.json" 2>&1)"
rc_a=$?
out_b="$(validate "$root" "$scratch/mutant_store.json" 2>&1)"
rc_b=$?
out_c="$(validate "$root" "$scratch/mutant_orphan.json" 2>&1)"
rc_c=$?

if [ "$rc_a" -eq 1 ] && printf '%s\n' "$out_a" | grep -qF "forbidden key 'value'"; then
  echo "  OK    negative control A: a value carried instead of a path reference is refused by name"
else
  echo "check-paperclip-secrets: FAIL — negative control A passed; a carried value was not refused" >&2
  printf '%s\n' "$out_a" >&2
  exit 1
fi

if [ "$rc_b" -eq 1 ] && printf '%s\n' "$out_b" | grep -qF "is not the store of record"; then
  echo "  OK    negative control B: a second store is refused by name"
else
  echo "check-paperclip-secrets: FAIL — negative control B passed; a second store was not refused" >&2
  printf '%s\n' "$out_b" >&2
  exit 1
fi

if [ "$rc_c" -eq 1 ] && printf '%s\n' "$out_c" | grep -qF "no consumer"; then
  echo "  OK    negative control C: an orphaned (consumer-less) secret is reported by name"
else
  echo "check-paperclip-secrets: FAIL — negative control C passed; an orphan was not reported" >&2
  printf '%s\n' "$out_c" >&2
  exit 1
fi

# --- the read guard -----------------------------------------------------------
# A read requires an authenticated, scoped caller. The target is derived from
# the committed catalog so the provocation tracks the tree rather than a literal.
target="$(python3 - "$root" <<'PY'
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root))

from integrations.paperclip.adapters.secrets import vault  # noqa: E402

refs = vault.refs_from(vault.load_catalog(root))
print(refs[0].scope if refs else "")
print(refs[0].gsm_path if refs else "")
PY
)"
target_scope="$(printf '%s\n' "$target" | sed -n '1p')"
target_path="$(printf '%s\n' "$target" | sed -n '2p')"

if [ -z "$target_scope" ] || [ -z "$target_path" ]; then
  echo "check-paperclip-secrets: CANNOT-ASSESS — no declared secret to exercise the read guard" >&2
  exit 2
fi

out_d="$(python3 -m integrations.paperclip.adapters.secrets.cli read --path "$target_path" --principal unbound-session 2>&1)"
rc_d=$?
if [ "$rc_d" -eq 1 ] && printf '%s\n' "$out_d" | grep -qF "lacks scope '$target_scope'"; then
  echo "  OK    negative control D: an unscoped read is refused, naming the scope"
else
  echo "check-paperclip-secrets: FAIL — negative control D passed; an unscoped read was served" >&2
  printf '%s\n' "$out_d" >&2
  exit 1
fi

out_e="$(python3 -m integrations.paperclip.adapters.secrets.cli read --path "$target_path" \
  --principal gate-session --scope "$target_scope" 2>&1)"
rc_e=$?
if [ "$rc_e" -eq 0 ] && printf '%s\n' "$out_e" | grep -qF "$target_path" \
  && ! printf '%s\n' "$out_e" | grep -qF '"value"'; then
  echo "  OK    positive control D: the same read with the scope returns the reference, not a value"
else
  echo "check-paperclip-secrets: FAIL — the scoped read did not return the reference" >&2
  printf '%s\n' "$out_e" >&2
  exit 1
fi

# --- restore ------------------------------------------------------------------
restore_sha="$(python3 - "$root" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root))

from integrations.paperclip.adapters.secrets import vault  # noqa: E402

payload = json.dumps(vault.build_view(root), indent=2, sort_keys=True).encode("utf-8")
print(hashlib.sha256(payload).hexdigest())
PY
)"

if [ "$restore_sha" != "$base_sha" ]; then
  echo "check-paperclip-secrets: FAIL — the projection is not deterministic across the control" >&2
  exit 1
fi

echo "  OK    projection is deterministic: the rebuilt view hashes identically (${base_sha:0:12})"
echo "  OK    no value is carried anywhere: the declarations hold names, scopes and rotation only"
echo "check-paperclip-secrets: OK — the secret view names GSM paths, keeps one store of record"
exit 0
