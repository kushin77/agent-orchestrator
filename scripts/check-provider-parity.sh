#!/usr/bin/env bash
# check-provider-parity.sh — flag UNEXPLAINED capability/feature drift between
# the Claude and DeepSeek provider declarations (issue #1194).
#
# Claude (orchestrator) and DeepSeek (analytics worker) are DELIBERATELY
# asymmetric — this gate does not force literal parity. It only requires that
# every asymmetric item carry an inline rationale marker at the point of
# declaration, so a future reviewer can tell "someone forgot to port this" from
# "this is role-based by design" without re-running the 2026-09-17 audit by hand.
#
# Compared inputs:
#   1. gateway/catalog/modules/{claude-anthropic,deepseek}/module.json
#      `features[].id` sets, in full (both default-on base wiring, e.g.
#      messages-api/chat-completions/system-lifting, and default-off optional
#      toggles, e.g. prompt-caching/thinking-effort). A default-on feature
#      unique to one provider is usually just protocol shape (Anthropic
#      Messages API vs OpenAI-compatible chat/completions), but the gate does
#      not assume that — it still requires the same rationale marker in `desc`,
#      so a genuinely-forgotten port is caught the same way an unported
#      optional flag would be.
#   2. registry/profiles/seeds/{claude,deepseek}.1.0.0.yaml `capabilitySet` and
#      `toolAllowlist` lists.
#   3. registry/personas/cards/{claude,deepseek}.yaml `capabilitySet` and
#      `toolAllowlist` lists.
#
# An item present on one side and absent on the other is fine IFF:
#   - JSON (module.json): its `desc` field contains one of the rationale
#     markers (case-insensitive): "role-only", "provider-specific",
#     "claude-specific", "deepseek-specific" — matching the convention
#     module.json already uses (see claude-anthropic's prompt-caching /
#     thinking-effort features).
#   - YAML (seeds/personas): the list item carries a trailing inline comment
#     containing "role-only:" or "provider-specific:".
#
# Anything else is unexplained drift -> FAIL, naming the item and both files.
#
# The gate runs a negative control on a scratch copy: an unmarked item is
# injected into one side and the validator must refuse it by name, so this
# check cannot pass vacuously.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-provider-parity: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

validate() {
  # $1 = root to validate against (real tree or a scratch copy)
  python3 - "$1" <<'PY'
import json
import re
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()

MARKER_JSON = re.compile(r"role-only|provider-specific|claude-specific|deepseek-specific", re.I)
MARKER_YAML = re.compile(r"role-only\s*:|provider-specific\s*:", re.I)

findings = []


def load_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        findings.append(f"{path}: unreadable ({exc})")
        return None


def all_features(doc):
    out = {}
    for feat in doc.get("features", []) or []:
        if not isinstance(feat, dict):
            continue
        out[feat.get("id")] = feat.get("desc", "") or ""
    return out


def compare_features(path_a, path_b, label):
    doc_a, doc_b = load_json(path_a), load_json(path_b)
    if doc_a is None or doc_b is None:
        return
    feats_a, feats_b = all_features(doc_a), all_features(doc_b)
    for item, desc in feats_a.items():
        if item not in feats_b and not MARKER_JSON.search(desc):
            findings.append(
                f"{label}: feature '{item}' in {path_a} has no counterpart "
                f"in {path_b} and no rationale marker in its desc"
            )
    for item, desc in feats_b.items():
        if item not in feats_a and not MARKER_JSON.search(desc):
            findings.append(
                f"{label}: feature '{item}' in {path_b} has no counterpart "
                f"in {path_a} and no rationale marker in its desc"
            )


ITEM_RE = re.compile(r"^\s*-\s*([^\s#]+)\s*(#\s*(.*))?$")
KEY_RE = re.compile(r"^(\S[^:]*):\s*$")


def list_with_comments(path, key):
    """Return {item: trailing_comment} for a top-level `key:` YAML list."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        findings.append(f"{path}: unreadable ({exc})")
        return {}
    out = {}
    in_key = False
    for line in lines:
        m = KEY_RE.match(line)
        if m:
            in_key = m.group(1) == key
            continue
        if in_key:
            if line.strip() == "" or line.startswith((" ", "\t")):
                if line.strip() == "":
                    continue
                if line.lstrip().startswith("#"):
                    # a standalone comment line inside the block; not the end
                    continue
                im = ITEM_RE.match(line)
                if im:
                    out[im.group(1)] = im.group(3) or ""
                    continue
                # any other non-list content ends this key's block
                in_key = False
            else:
                in_key = False
    return out


def compare_yaml_list(path_a, path_b, key, label):
    a = list_with_comments(path_a, key)
    b = list_with_comments(path_b, key)
    for item, comment in a.items():
        if item not in b and not MARKER_YAML.search(comment):
            findings.append(
                f"{label}: {key} item '{item}' in {path_a} has no counterpart "
                f"in {path_b} and no inline rationale marker"
            )
    for item, comment in b.items():
        if item not in a and not MARKER_YAML.search(comment):
            findings.append(
                f"{label}: {key} item '{item}' in {path_b} has no counterpart "
                f"in {path_a} and no inline rationale marker"
            )


compare_features(
    root / "gateway/catalog/modules/claude-anthropic/module.json",
    root / "gateway/catalog/modules/deepseek/module.json",
    "module.json features",
)

for key in ("capabilitySet", "toolAllowlist"):
    compare_yaml_list(
        root / "registry/profiles/seeds/claude.1.0.0.yaml",
        root / "registry/profiles/seeds/deepseek.1.0.0.yaml",
        key,
        "profile seeds",
    )
    compare_yaml_list(
        root / "registry/personas/cards/claude.yaml",
        root / "registry/personas/cards/deepseek.yaml",
        key,
        "persona cards",
    )

if findings:
    for f in findings:
        print(f"  FAIL  {f}", file=sys.stderr)
    raise SystemExit(1)

print("  OK    no unexplained provider capability drift found")
raise SystemExit(0)
PY
}

validate "$root"
rc=$?
case "$rc" in
  0) : ;;
  1)
    echo "check-provider-parity: FAIL — unexplained Claude/DeepSeek capability drift (see findings above)" >&2
    exit 1
    ;;
  *)
    echo "check-provider-parity: CANNOT-ASSESS — validator returned $rc" >&2
    exit 2
    ;;
esac

# --- negative control: prove the gate can actually fail, and cannot pass on a
# --- broken copy or on a comment line silently swallowing later items -------
scratch_root="$(mktemp -d "${TMPDIR:-/tmp}/ao1194.XXXXXX")" || {
  echo "check-provider-parity: CANNOT-ASSESS — cannot create a scratch directory" >&2
  exit 2
}
trap 'rm -rf "$scratch_root"' EXIT

seed_tree() {
  # seed_tree <dir> — a fresh, untouched copy of the compared inputs.
  local dir="$1"
  mkdir -p "$dir/registry/profiles/seeds" "$dir/registry/personas/cards" \
    "$dir/gateway/catalog/modules/claude-anthropic" "$dir/gateway/catalog/modules/deepseek"
  cp "$root/registry/profiles/seeds/claude.1.0.0.yaml" "$dir/registry/profiles/seeds/claude.1.0.0.yaml"
  cp "$root/registry/profiles/seeds/deepseek.1.0.0.yaml" "$dir/registry/profiles/seeds/deepseek.1.0.0.yaml"
  cp "$root/registry/personas/cards/claude.yaml" "$dir/registry/personas/cards/claude.yaml"
  cp "$root/registry/personas/cards/deepseek.yaml" "$dir/registry/personas/cards/deepseek.yaml"
  cp "$root/gateway/catalog/modules/claude-anthropic/module.json" "$dir/gateway/catalog/modules/claude-anthropic/module.json"
  cp "$root/gateway/catalog/modules/deepseek/module.json" "$dir/gateway/catalog/modules/deepseek/module.json"
}

# Control E — an untouched scratch copy must still be OK (proves the controls
# below aren't passing because the copy itself is broken).
work_e="$scratch_root/e-pristine"
seed_tree "$work_e"
out_e="$(validate "$work_e" 2>&1)"
rc_e=$?
if [ "$rc_e" -eq 0 ]; then
  echo "  OK    control E: pristine scratch copy is green"
else
  echo "check-provider-parity: CANNOT-ASSESS — the pristine scratch copy did not validate OK (rc=$rc_e); the negative controls below would be meaningless" >&2
  printf '%s\n' "$out_e" >&2
  exit 2
fi

# Mutation A — an unmarked capability injected into the claude seed.
work_a="$scratch_root/a-unmarked-capability"
seed_tree "$work_a"
python3 - "$work_a/registry/profiles/seeds/claude.1.0.0.yaml" <<'PY'
import sys
from pathlib import Path
p = Path(sys.argv[1])
text = p.read_text(encoding="utf-8")
needle = "capabilitySet:\n"
assert needle in text, "fixture missing capabilitySet: key"
text = text.replace(needle, needle + "  - zzz-unexplained-mutant\n", 1)
p.write_text(text, encoding="utf-8")
PY
out_a="$(validate "$work_a" 2>&1)"
rc_a=$?
if [ "$rc_a" -eq 1 ] && [[ "$out_a" == *"zzz-unexplained-mutant"* ]]; then
  echo "  OK    control A: an unmarked injected YAML capability is refused by name"
else
  echo "check-provider-parity: FAIL — control A passed; an unmarked drift item was not caught (the gate cannot fail)" >&2
  printf '%s\n' "$out_a" >&2
  exit 1
fi

# Mutation B — an unmarked feature injected into the deepseek module.json, in
# its own scratch copy (independent of mutation A).
work_b="$scratch_root/b-unmarked-feature"
seed_tree "$work_b"
python3 - "$work_b/gateway/catalog/modules/deepseek/module.json" <<'PY'
import json
import sys
from pathlib import Path
p = Path(sys.argv[1])
doc = json.loads(p.read_text(encoding="utf-8"))
doc["features"].append({
    "id": "zzz-unexplained-json-mutant",
    "default": "on",
    "flags": ["deepseek.zzz"],
    "desc": "no rationale here on purpose",
})
p.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
PY
out_b="$(validate "$work_b" 2>&1)"
rc_b=$?
if [ "$rc_b" -eq 1 ] && [[ "$out_b" == *"zzz-unexplained-json-mutant"* ]]; then
  echo "  OK    control B: an unmarked injected module.json feature is refused by name"
else
  echo "check-provider-parity: FAIL — control B passed; an unmarked drift feature was not caught (the gate cannot fail)" >&2
  printf '%s\n' "$out_b" >&2
  exit 1
fi

# Mutation C — an unmarked item preceded by a standalone comment line in the
# same list block; the parser must not let the comment line terminate the
# block and hide the item that follows.
work_c="$scratch_root/c-comment-then-unmarked"
seed_tree "$work_c"
python3 - "$work_c/registry/profiles/seeds/deepseek.1.0.0.yaml" <<'PY'
import sys
from pathlib import Path
p = Path(sys.argv[1])
text = p.read_text(encoding="utf-8")
needle = "capabilitySet:\n"
assert needle in text, "fixture missing capabilitySet: key"
injected = needle + "  # a standalone comment inside the list block\n  - zzz-unexplained-after-comment\n"
text = text.replace(needle, injected, 1)
p.write_text(text, encoding="utf-8")
PY
out_c="$(validate "$work_c" 2>&1)"
rc_c=$?
if [ "$rc_c" -eq 1 ] && [[ "$out_c" == *"zzz-unexplained-after-comment"* ]]; then
  echo "  OK    control C: an unmarked item after a standalone comment line is still refused by name"
else
  echo "check-provider-parity: FAIL — control C passed; a standalone comment line hid a later unmarked item (the gate cannot fail)" >&2
  printf '%s\n' "$out_c" >&2
  exit 1
fi

echo "check-provider-parity: OK — no unexplained Claude/DeepSeek capability drift, and all negative controls hold"
exit 0
