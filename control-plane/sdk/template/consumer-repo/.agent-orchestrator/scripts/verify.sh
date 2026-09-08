#!/usr/bin/env bash
# Consumer-repo born-compliance gate (issue #41) — the consumer-side `make
# verify`.  Gate of record for a governed consumer repo:
#
#   1. pack.json is valid JSON and a well-formed ao.pack/v1 agent-pack
#      manifest (schema, id, version, name, non-empty managed instructions);
#   2. sync.yaml parses as ao.sync-config/v1 with a source endpoint + auth
#      token env + a targets list;
#   3. every managed instruction layer exists, carries its managed marker on
#      line one, and matches the recorded sha256 in the drift manifest (a
#      locally edited auto-synced layer is a failure — born-compliant);
#   4. the local (tenant-owned) layer exists and is marked local;
#   5. the root AGENTS.md wires every instruction layer.
#
# Every check produces a REAL exit code (no-false-green): a missing file, an
# invalid manifest, a drifted managed layer, or an unwired layer fails the
# gate.  Run from the consumer repo root:
#
#     make verify
#     bash .agent-orchestrator/scripts/verify.sh
set -u

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo_root" || exit 1
ao_dir="$repo_root/.agent-orchestrator"
manifest="$ao_dir/instructions/MANIFEST"

fail=0
say_ok() { printf '  OK    %s\n' "$1"; }
say_fail() { printf '  FAIL  %s\n' "$1" >&2; fail=$((fail + 1)); }

echo "== agent-orchestrator consumer-repo compliance =="

# --- 1. pack manifest (ao.pack/v1) ----------------------------------------
if python3 - "$ao_dir/pack.json" <<'PY' >/dev/null 2>&1
import json
import sys

pack = json.load(open(sys.argv[1], encoding="utf-8"))
assert pack.get("schema") == "ao.pack/v1", "schema must be ao.pack/v1"
for key in ("id", "version", "name"):
    assert isinstance(pack.get(key), str) and pack[key], f"pack.json {key} is missing"
instructions = pack.get("instructions")
assert isinstance(instructions, list) and instructions, "pack.json instructions must be non-empty"
assert any(i.get("managed") for i in instructions), "pack.json must declare a managed instruction layer"
PY
then
  say_ok "pack.json valid (ao.pack/v1)"
else
  say_fail "pack.json is not a valid ao.pack/v1 manifest"
fi

# --- 2. sync config (ao.sync-config/v1) -------------------------------------
if python3 - "$ao_dir/sync.yaml" <<'PY' >/dev/null 2>&1
import sys

import yaml

cfg = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
assert cfg.get("schema") == "ao.sync-config/v1", "schema must be ao.sync-config/v1"
assert cfg.get("source", {}).get("endpoint", "").startswith("https://"), "source.endpoint must be https"
assert cfg.get("auth", {}).get("tokenEnv"), "auth.tokenEnv must be set"
targets = cfg.get("targets")
assert isinstance(targets, list) and targets, "targets must be non-empty"
assert any(t.get("managed") for t in targets), "targets must include a managed layer"
PY
then
  say_ok "sync.yaml valid (ao.sync-config/v1)"
else
  say_fail "sync.yaml is not a valid ao.sync-config/v1 config"
fi

# --- 3. instruction layers (managed parity + local present) ------------------
if [ ! -f "$manifest" ]; then
  say_fail "drift manifest missing: $manifest"
else
  managed_targets="$(
    python3 - "$ao_dir/sync.yaml" <<'PY'
import sys

import yaml

cfg = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
for target in cfg["targets"]:
    print(f"{target['managed']}\t{target['marker']}\t{target['path']}")
PY
  )" || { say_fail "could not read sync.yaml targets"; managed_targets=""; }

  while IFS=$'\t' read -r is_managed marker path; do
    [ -n "$path" ] || continue
    if [ ! -f "$path" ]; then
      say_fail "instruction layer missing: $path"
      continue
    fi
    first_line="$(head -n 1 "$path")"
    if [ "$is_managed" = "True" ]; then
      case "$first_line" in
        *"$marker"*) : ;;
        *)
          say_fail "managed layer marker missing on $path"
          continue
          ;;
      esac
      recorded="$(awk -v p="$path" '$1 == p { print $2; exit }' "$manifest")"
      if [ -z "$recorded" ]; then
        say_fail "managed layer not in drift manifest: $path"
        continue
      fi
      actual="$(sha256sum "$path" | awk '{ print $1 }')"
      if [ "$actual" = "$recorded" ]; then
        say_ok "managed layer in parity: $path"
      else
        say_fail "managed layer drifted (run make sync): $path"
      fi
    else
      case "$first_line" in
        *"$marker"*) say_ok "local layer present: $path" ;;
        *) say_fail "local layer marker missing on $path" ;;
      esac
    fi
  done <<<"$managed_targets"
fi

# --- 4. AGENTS.md wires the instruction layers ------------------------------
if [ ! -f "$repo_root/AGENTS.md" ]; then
  say_fail "AGENTS.md missing at repo root"
else
  wiring_ok=0
  while IFS=$'\t' read -r _is_managed _marker path; do
    [ -n "$path" ] || continue
    if grep -qF "$path" "$repo_root/AGENTS.md"; then
      wiring_ok=$((wiring_ok + 1))
    else
      say_fail "AGENTS.md does not wire instruction layer: $path"
    fi
  done <<<"$managed_targets"
  if [ "$wiring_ok" -gt 0 ]; then
    say_ok "AGENTS.md wires the instruction layers"
  fi
fi

# --- summary ----------------------------------------------------------------
if [ "$fail" -ne 0 ]; then
  echo "compliance: $fail problem(s)" >&2
  exit 1
fi
echo "compliance: OK — governed consumer repo is born-compliant"
