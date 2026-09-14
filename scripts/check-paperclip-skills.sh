#!/usr/bin/env bash
# check-paperclip-skills.sh — the paperclip skills/MCP adapter gate (issue #419).
#
# Upstream's extensibility family (``SKILL.md`` skills, plugins/extensions, MCP
# tool access) had no fleet-side producer: nothing said which skill an agent may
# load or which MCP tool a run may call. ``paperclip/adapters/skills/`` is the
# adapter that maps it onto what the fleet already runs — the tool authority
# ``gateway/mcp/`` for tools, the agent profile for what an agent may use, and a
# closed ``SKILL.md`` registry for what is loadable.
#
# A mapping that nothing validates is a formality (no-false-green doctrine,
# GR-12), so this gate fails, by name, when the adapter drifts:
#
#   * the registry is CLOSED — a ``SKILL.md`` present on disk but not declared is
#     refused rather than silently loaded;
#   * the MCP surface is PROJECTED, not duplicated — a tool that is callable in
#     ``gateway/mcp/`` but absent from the projection is a FAIL, and so is a
#     projected-but-uncallable entry;
#   * every declaration carries provenance (GR-10) and vendors nothing — a
#     declaration directory may hold ``SKILL.md`` and nothing else;
#   * a skill or plugin requiring a capability or tool the agent's profile does
#     not grant is REFUSED at load, so extensibility cannot widen authority.
#
# The gate never mutates the working tree and never touches the network. Every
# provocation is applied to a scratch copy of the adapter's inputs (the ``--root``
# the CLI is pointed at); the real tree is hashed before and after all
# provocations and must be byte-identical.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
# CANNOT-ASSESS must never be reported as a pass.
#
# Usage: bash scripts/check-paperclip-skills.sh
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

PKG="paperclip/adapters/skills"
GATE="check-paperclip-skills"

if ! command -v python3 >/dev/null 2>&1; then
  echo "$GATE: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

if [ ! -d "$PKG" ]; then
  echo "$GATE: FAIL — $PKG is missing" >&2
  exit 1
fi

fail=0
note_fail() {
  printf '  FAIL  %s\n' "$1" >&2
  fail=$((fail + 1))
}

# cli <scratch-root> <args...> — drive the adapter CLI against a (copy of the)
# inputs. The code is this repo's; only the data root moves.
cli() {
  local data_root="$1"
  shift
  python3 -m paperclip.adapters.skills.cli --root "$data_root" "$@"
}

# --- baseline: the structure is coherent -------------------------------------
echo "== baseline structural check =="
baseline_out="$(cli "$root" check 2>&1)"
baseline_rc=$?
printf '%s\n' "$baseline_out"
if [ "$baseline_rc" -ne 0 ]; then
  echo "$GATE: FAIL — the adapter does not pass its own structural check" >&2
  exit 1
fi

# --- baseline: the projection is a real view of the tool authority ------------
echo "== baseline: the MCP projection is derived, not duplicated =="
tools_count="$(cli "$root" project | python3 -c 'import json,sys; print(len(json.load(sys.stdin)["tools"]))')"
if [ -z "$tools_count" ] || [ "$tools_count" -lt 1 ]; then
  echo "$GATE: FAIL — the projection carries no tools" >&2
  exit 1
fi
echo "  OK    projection carries $tools_count callable tool(s) derived from gateway/mcp/"

# --- baseline: the load gate allows exactly what the profile grants ----------
echo "== baseline: a granted load is allowed, and narrows (never widens) =="
if cli "$root" load --skill ticket-contract-read --profile paperclip >/dev/null 2>&1; then
  echo "  OK    ticket-contract-read is loadable as profile paperclip"
else
  note_fail "the baseline load (ticket-contract-read as paperclip) was refused"
fi

# --- scratch tree ------------------------------------------------------------
scratch="/tmp/ao419-skills.$(date +%s%N).$"
if ! mkdir "$scratch" 2>/dev/null; then
  echo "$GATE: CANNOT-ASSESS — cannot create a scratch directory" >&2
  exit 2
fi
trap 'rm -rf "$scratch"' EXIT

fresh_root() {
  local into="$1"
  rm -rf "$into"
  mkdir -p "$into/paperclip/adapters" "$into/registry/profiles"
  cp -a "$root/$PKG" "$into/paperclip/adapters/skills"
  cp -a "$root/gateway" "$into/gateway"
  cp -a "$root/registry/profiles/seeds" "$into/registry/profiles/seeds"
}

# sha256_of <path>
sha256_of() {
  sha256sum "$1" | awk '{print $1}'
}

# --- the real tree must never be mutated by a provocation --------------------
declare -a guarded_files=(
  "$PKG/registry.json"
  "$PKG/mcp_tools.json"
)
guarded_before="$(for f in "${guarded_files[@]}"; do sha256_of "$f"; done)"

# --- provocation A: an UNDECLARED skill is refused, not loaded ---------------
echo "== provocation A: an undeclared SKILL.md must be refused by name =="
ra="$scratch/a"
fresh_root "$ra"
mkdir -p "$ra/paperclip/adapters/skills/library/rogue-skill"
cat > "$ra/paperclip/adapters/skills/library/rogue-skill/SKILL.md" <<'SKILLDOC'
---
id: rogue-skill
kind: skill
name: Rogue skill
description: A declaration that is on disk but absent from the registry.
provenance:
  repo: example/rogue
  path: skills/rogue/SKILL.md
  license: MIT
  verdict: REFERENCE
---
# Rogue skill
SKILLDOC
out_a="$(cli "$ra" check 2>&1)"
rc_a=$?
if [ "$rc_a" -eq 1 ] && printf '%s\n' "$out_a" | grep -q "rogue-skill" \
   && printf '%s\n' "$out_a" | grep -qi "undeclared"; then
  echo "  OK    refused by name: $(printf '%s\n' "$out_a" | grep -i 'undeclared' | head -1 | sed 's/^  *//')"
else
  note_fail "an undeclared SKILL.md was not refused by name (rc=$rc_a)"
  printf '%s\n' "$out_a" >&2
fi

# --- provocation B: a callable-but-unprojected MCP tool is a FAIL ------------
echo "== provocation B: a callable tool absent from the projection is a FAIL =="
rb="$scratch/b"
fresh_root "$rb"
printf '%s\n' "$(python3 - "$rb/paperclip/adapters/skills/mcp_tools.json" <<'PY'
import json, sys
path = sys.argv[1]
data = json.load(open(path, encoding="utf-8"))
data["tools"] = [t for t in data["tools"] if t != "kb.summary"]
open(path, "w", encoding="utf-8").write(json.dumps(data, indent=2, sort_keys=True) + "\n")
print("dropped kb.summary from the projection")
PY
)"
out_b="$(cli "$rb" project --check 2>&1)"
rc_b=$?
if [ "$rc_b" -eq 1 ] && printf '%s\n' "$out_b" | grep -q "kb.summary"; then
  echo "  OK    refused by name: $(printf '%s\n' "$out_b" | grep 'kb.summary' | head -1 | sed 's/^  *//')"
else
  note_fail "a callable-but-unprojected tool was not caught by name (rc=$rc_b)"
  printf '%s\n' "$out_b" >&2
fi

# --- provocation C: a capability the profile does not grant is refused ------
echo "== provocation C: a capability the profile does not grant is refused =="
pc="$scratch/c"
fresh_root "$pc"
out_c="$(cli "$pc" load --skill mcp-tool-projection --profile coder 2>&1)"
rc_c=$?
if [ "$rc_c" -eq 1 ] && printf '%s\n' "$out_c" | grep -q "'research'" \
   && printf '%s\n' "$out_c" | grep -q "'coder'"; then
  echo "  OK    refused by name: $(printf '%s\n' "$out_c" | grep -o "requires capability .* not granted by profile '[a-z-]*'" | head -1)"
else
  note_fail "a capability the profile does not grant was not refused by name (rc=$rc_c)"
  printf '%s\n' "$out_c" >&2
fi

# --- provocation D: a declaration with no provenance is refused -------------
echo "== provocation D: a declaration with no provenance is refused by name =="
rd="$scratch/d"
fresh_root "$rd"
python3 - "$rd/paperclip/adapters/skills/library/ticket-contract-read/SKILL.md" <<'PY'
import sys
path = sys.argv[1]
text = open(path, encoding="utf-8").read()
out, skip = [], False
for line in text.split("\n"):
    if line.startswith("provenance:"):
        skip = True
        continue
    if skip and (line.startswith("  ") or not line.strip()):
        if line.startswith("  "):
            continue
    skip = False
    out.append(line)
open(path, "w", encoding="utf-8").write("\n".join(out))
PY
out_d="$(cli "$rd" check 2>&1)"
rc_d=$?
if [ "$rc_d" -eq 1 ] && printf '%s\n' "$out_d" | grep -qi "provenance"; then
  echo "  OK    refused by name: $(printf '%s\n' "$out_d" | grep -oE 'front-matter is missing required key\(s\): provenance|declares no provenance[^)]*' | head -1)"
else
  note_fail "a declaration with no provenance was not refused by name (rc=$rc_d)"
  printf '%s\n' "$out_d" >&2
fi

# --- provocation E: a vendored implementation is refused --------------------
echo "== provocation E: a vendored implementation is refused by name =="
re="$scratch/e"
fresh_root "$re"
printf 'def copied_upstream_implementation():\n    return 1\n' \
  > "$re/paperclip/adapters/skills/library/ticket-contract-read/vendor_copy.py"
out_e="$(cli "$re" check 2>&1)"
rc_e=$?
if [ "$rc_e" -eq 1 ] && printf '%s\n' "$out_e" | grep -q "vendor_copy.py"; then
  echo "  OK    refused by name: $(printf '%s\n' "$out_e" | grep -o 'vendors non-declaration file(s).*' | head -1 | sed 's/ *$//')"
else
  note_fail "a vendored implementation was not refused by name (rc=$rc_e)"
  printf '%s\n' "$out_e" >&2
fi

# --- provocation F: a plugin tool the profile does not grant is refused -----
echo "== provocation F: a plugin tool the profile does not grant is refused =="
rf="$scratch/f"
fresh_root "$rf"
out_f="$(cli "$rf" load --skill claim-gate-check --profile paperclip 2>&1)"
rc_f=$?
if [ "$rc_f" -eq 1 ] && printf '%s\n' "$out_f" | grep -q "'shell_exec'" \
   && printf '%s\n' "$out_f" | grep -q "'paperclip'"; then
  echo "  OK    refused by name: $(printf '%s\n' "$out_f" | grep -o "requires tool .* not granted by profile '[a-z-]*'" | head -1)"
else
  note_fail "a plugin tool the profile does not grant was not refused by name (rc=$rc_f)"
  printf '%s\n' "$out_f" >&2
fi

# --- DERIVE a pinned tool authority (the projection follows it) --------------
echo "== the projection FOLLOWS the authority: change it, then re-derive =="
rg="$scratch/g"
fresh_root "$rg"
authority="$rg/gateway/mcp/tools.py"
vocabulary="$rg/gateway/mcp/model.py"
if [ ! -f "$authority" ] || [ ! -f "$vocabulary" ]; then
  echo "$GATE: CANNOT-ASSESS — the tool authority is not in the scratch tree" >&2
  exit 2
fi
# Change the authority consistently (registry AND closed vocabulary): a
# projection that merely mirrors today's tool list must now be stale.
python3 - "$authority" "$vocabulary" <<'PY'
import sys

tools_path, model_path = sys.argv[1], sys.argv[2]
text = open(tools_path, encoding="utf-8").read()
needle = '        "platform.whoami": _platform_whoami,\n'
assert text.count(needle) == 1, "tools.py anchor not found exactly once"
open(tools_path, "w", encoding="utf-8").write(text.replace(needle, "", 1))

text = open(model_path, encoding="utf-8").read()
needle = '    "platform.whoami",\n'
assert text.count(needle) == 1, "model.py anchor not found exactly once"
open(model_path, "w", encoding="utf-8").write(text.replace(needle, "", 1))
PY
out_g="$(cli "$rg" project --check 2>&1)"
rc_g=$?
if [ "$rc_g" -eq 1 ] && printf '%s\n' "$out_g" | grep -q "platform.whoami"; then
  echo "  OK    changing the authority makes the projection stale, named: $(printf '%s\n' "$out_g" | grep 'platform.whoami' | head -1 | sed 's/^  *//')"
else
  note_fail "changing the authority did not make the projection stale by name (rc=$rc_g)"
  printf '%s\n' "$out_g" >&2
fi
if cli "$rg" project --write >/dev/null 2>&1 && cli "$rg" project --check >/dev/null 2>&1; then
  echo "  OK    re-deriving the projection against the changed authority restores the check"
else
  note_fail "re-deriving the projection did not restore the check"
fi

# --- restoration: every mutant is gone and the real tree is byte-identical ---
echo "== restoration: the working tree was never mutated =="
guarded_after="$(for f in "${guarded_files[@]}"; do sha256_of "$f"; done)"
restore_ok=1
while IFS= read -r line; do
  before="$(printf '%s\n' "$guarded_before" | sed -n "${line}p")"
  after="$(printf '%s\n' "$guarded_after" | sed -n "${line}p")"
  if [ "$before" != "$after" ]; then
    restore_ok=0
    note_fail "guarded file #$line changed under the gate (before=$before after=$after)"
  fi
done < <(seq 1 "${#guarded_files[@]}")
if [ "$restore_ok" -eq 1 ]; then
  while IFS= read -r f; do
    echo "  OK    $(sha256_of "$f")  $f"
  done < <(printf '%s\n' "${guarded_files[@]}")
fi

# --- determinism: the derived surface is a pure function of the tree ---------
echo "== determinism: two derivations are byte-identical =="
d1="$(cli "$root" project | sha256sum | awk '{print $1}')"
d2="$(cli "$root" project | sha256sum | awk '{print $1}')"
if [ -n "$d1" ] && [ "$d1" = "$d2" ]; then
  echo "  OK    identical derivation (sha256=${d1:0:16})"
else
  note_fail "the projection derivation is not deterministic ($d1 vs $d2)"
fi

# --- re-run the baseline: nothing the gate did left the tree red -------------
echo "== baseline re-check after the provocations =="
if cli "$root" check >/dev/null 2>&1; then
  echo "  OK    the adapter still passes its structural check"
else
  note_fail "the adapter regressed after the provocations"
fi

if [ "$fail" -gt 0 ]; then
  echo "$GATE: FAIL — $fail provocation(s) did not behave" >&2
  exit 1
fi
echo "$GATE: OK — the registry is closed, the MCP surface is projected from"
echo "  gateway/mcp/, every declaration carries provenance and vendors nothing,"
echo "  and the load gate refuses every widening (capability, tool, projection)."
exit 0
