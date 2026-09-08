#!/usr/bin/env bash
# Consumer-side sync wiring (issue #41) — `make sync`.
#
# Refreshes the AUTO-SYNCED (managed) instruction layers of a governed
# consumer repo from the pack source declared in .agent-orchestrator/sync.yaml
# and regenerates the sha256 drift manifest.  Local (tenant-owned) layers are
# never touched.
#
# Modes (offline-safe by default):
#   bash .agent-orchestrator/scripts/sync.sh            # same as --check
#   bash .agent-orchestrator/scripts/sync.sh --check    # dry-run + parity,
#                                                        #   NO network
#   bash .agent-orchestrator/scripts/sync.sh --fetch    # live refresh via
#                                                        #   curl (requires
#                                                        #   network + token)
#
# A live fetch authenticates with the short-lived session token in the env
# var named by sync.yaml auth.tokenEnv — never a hardcoded key.
set -u

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo_root" || exit 1
ao_dir="$repo_root/.agent-orchestrator"
sync_cfg="$ao_dir/sync.yaml"
mode="${1:-check}"

[ -f "$sync_cfg" ] || { echo "sync config missing: $sync_cfg" >&2; exit 1; }

# --- read config once (endpoint, token env, managed targets) ---------------
config="$(
  python3 - "$sync_cfg" <<'PY'
import sys

import yaml

cfg = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
assert cfg.get("schema") == "ao.sync-config/v1", "schema must be ao.sync-config/v1"
source = cfg["source"]
token_env = cfg["auth"]["tokenEnv"]
print(f"endpoint={source['endpoint']}")
print(f"instructions_path={source.get('instructionsPath', '')}")
print(f"token_env={token_env}")
print(f"pack_id={cfg['pack']['id']}")
for target in cfg["targets"]:
    if target.get("managed"):
        print(f"managed={target['layer']}|{target['path']}|{target['marker']}")
PY
)" || { echo "sync.yaml invalid" >&2; exit 1; }

plan() {
  echo "sync plan from $sync_cfg:"
  printf '%s\n' "$config" | while IFS= read -r line; do
    echo "  $line"
  done
}

if [ "$mode" = "--fetch" ]; then
  endpoint="$(printf '%s\n' "$config" | sed -n 's/^endpoint=//p')"
  instructions_path="$(printf '%s\n' "$config" | sed -n 's/^instructions_path=//p')"
  token_env="$(printf '%s\n' "$config" | sed -n 's/^token_env=//p')"
  if ! command -v curl >/dev/null 2>&1; then
    echo "sync: curl is required for a live fetch" >&2
    exit 1
  fi
  token_val="${!token_env:-}"
  if [ -z "$token_val" ]; then
    echo "sync: live fetch requires the session token in env \$$token_env" >&2
    exit 1
  fi
  base="${endpoint}${instructions_path}"
  printf '%s\n' "$config" | sed -n 's/^managed=//p' | while IFS='|' read -r layer path marker; do
    url="$base/$layer"
    echo "sync: fetching $path from $url"
    tmp="$path.sync.tmp"
    if ! curl -fsSL -H "Authorization: Bearer $token_val" "$url" -o "$tmp"; then
      echo "sync: fetch failed for $path" >&2
      rm -f "$tmp"
      exit 1
    fi
    first_line="$(head -n 1 "$tmp")"
    case "$first_line" in
      *"$marker"*) : ;;
      *)
        echo "sync: refused fetched file without managed marker for $path" >&2
        rm -f "$tmp"
        exit 1
        ;;
    esac
    mv "$tmp" "$path"
  done
  rc=$?
  if [ "$rc" -ne 0 ]; then
    exit "$rc"
  fi
  # Regenerate the drift manifest over the managed layers (local untouched).
  manifest="$ao_dir/instructions/MANIFEST"
  tmp_manifest="$manifest.tmp"
  {
    echo "# Managed instruction layer manifest (ao.pack/v1) - <path> <sha256>"
    printf '%s\n' "$config" | sed -n 's/^managed=//p' | while IFS='|' read -r _layer path _marker; do
      printf '%s %s\n' "$path" "$(sha256sum "$path" | awk '{ print $1 }')"
    done
  } > "$tmp_manifest"
  mv "$tmp_manifest" "$manifest"
  echo "sync: drift manifest regenerated"
  exec bash "$ao_dir/scripts/verify.sh"
fi

# --- default: --check (dry-run plan + local parity, no network) ------------
plan
exec bash "$ao_dir/scripts/verify.sh"
