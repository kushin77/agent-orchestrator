#!/usr/bin/env bash
# check-tier-vocabulary.sh — one declared authority for the model-tier vocabulary
# (issue #1494, residual row R5 of the #1458 register).
#
# THE DEFECT THIS CLOSES
#   The platform's model-tier ladder ("LOW", "MED", "HIGH", "MAX") was re-declared
#   as a literal in FIVE modules (`gateway/providers/contract.py`,
#   `governance/authority/model.py`, `identity/onboarding/model.py`,
#   `integrations/hermes/mapping.py`, `portal/server/chat.py`) plus a sixth shape in
#   `gateway/limits/fingerprint.py`, and the FinOps tier names (flash/pro/auditor)
#   were restated in two more (`registry/chat/labels.py`, `fleet/channel.py`). The
#   copies AGREED — which is exactly why nothing failed when one of them drifted:
#   two lists that agree today are not one list.
#
# THE AUTHORITIES (borrowed, never re-declared)
#   * the ladder -> `registry/profiles/catalog.yaml` ``tiers``, read through its one
#     reader `registry/profiles/tiers.py`;
#   * the FinOps names -> `governance/finops/policy.json` ``vocabulary.tiers``, read
#     through the reader that already owns that policy
#     (`governance/finops/chooser.py`).
#   `governance/cto-overlay/overlay.py` declares a THIRD thing — evidence tiers
#   (experimental/standard/critical) — a different concept, out of scope BY NAME
#   rather than silently folded in.
#
# WHAT IT MEASURES — four arms, and the last two are what make the others mean
# anything: a borrow that reads its own values can never fail.
#   1. the authority READS, and refuses an authority that is absent/empty/duplicated
#      BY NAME (an empty ladder is an unreadable authority, not a platform with no
#      tiers — every membership check downstream would judge by accident);
#   2. NO SECOND DECLARATION: a sequence literal (tuple/list/set/frozenset) holding a
#      declared vocabulary anywhere in the eleven product trees is REFUSED BY NAME,
#      with file and line. A DICT literal keyed by the ladder is NOT refused and NOT
#      silent: it is REPORTED, because a keyed map maps the vocabulary onto models
#      and may legitimately cover a subset (`gateway/providers/config.py`);
#   3. every consumer READS its authority: each consumer's exported value equals the
#      authority at runtime (imported, not grepped), and each consumer's source NAMES
#      the reader it derives the value from;
#   4. the read FOLLOWS the authority rather than mirroring it: in scratch trees the
#      authority is MUTATED and the consumer re-imported there must answer the
#      MUTATED ladder — with a clean-tree control, so the difference is the proof.
#      Six of the eight sites are mutation-proven; `portal/server/chat.py` and
#      `fleet/channel.py` are LINK-proven only (their value equals the authority and
#      their source names the reader), because folding their transitive import graph
#      (`infra.rollout`, `governance.dispatch.model`) into a scratch tree would copy
#      most of the repository. The check says which is which, out loud.
#
# THE PROVOCATION IS BOTH WAYS (GR-12): a planted seventh copy must be refused by
# name, and the clean tree must be accepted. A check whose two paths collapse into
# one exit code is a formality.
#
# SCOPE, stated because a scan is only honest about what it looked at: `*.py` under
# the product trees below — `vendor/` is a pinned submodule and not ours to lint, and
# `tests/` holds expectations, not declarations. The YAML and JSON authorities
# themselves and the schemas that mirror them (`agent-profile.schema.json`,
# `governance/authority/schema.json`) are DATA; the catalog<->schema parity gate is
# `registry/profiles/validate.py`'s job, not this check's.
#
# Exit-code contract (repo tri-state): 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-tier-vocabulary.sh
set -u

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 2

ladder_authority="registry/profiles/catalog.yaml"
ladder_reader="registry/profiles/tiers.py"
finops_authority="governance/finops/policy.json"

# --- required inputs: absent => CANNOT-ASSESS, never a pass -------------------
for required in \
  "$ladder_authority" \
  "$ladder_reader" \
  "$finops_authority" \
  governance/finops/chooser.py \
  gateway/providers/contract.py \
  gateway/limits/fingerprint.py \
  governance/authority/model.py \
  identity/onboarding/model.py \
  integrations/hermes/mapping.py \
  portal/server/chat.py \
  registry/chat/labels.py \
  fleet/channel.py \
  e2e/wiring.py
do
  if [ ! -f "$required" ]; then
    echo "check-tier-vocabulary: CANNOT-ASSESS — $required is missing" >&2
    exit 2
  fi
done

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-tier-vocabulary: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi
if ! python3 -c 'import yaml' >/dev/null 2>&1; then
  echo "check-tier-vocabulary: CANNOT-ASSESS — PyYAML is not importable; the ladder" \
       "authority $ladder_authority is YAML and cannot be read without it" >&2
  exit 2
fi

fail=0
work="/tmp/ao1494-tier-vocabulary.$$.$(date +%s)"
mkdir -p "$work" || exit 2
trap 'rm -rf "$work"' EXIT

report_fail() { printf '  FAIL  %s\n' "$1" >&2; fail=$((fail + 1)); }

before_sha="$(sha256sum "$ladder_authority" "$finops_authority" | awk '{print $1}' | tr '\n' ' ')"

# ============================================================================ #
# 1. the authority reads — and refuses an authority it cannot read
# ============================================================================ #
echo "== 1. the declared authority reads =="
arm1_out="$(python3 - "$root" "$ladder_authority" <<'PY'
import sys
from pathlib import Path

root, relpath = Path(sys.argv[1]), sys.argv[2]
sys.path.insert(0, str(root))
from registry.profiles import tiers  # noqa: E402

try:
    reader = tiers.authority()
except tiers.TierVocabularyRefused as exc:
    print(f"  FAIL  the reader refused the real authority: {exc}")
    raise SystemExit(1)
import yaml  # noqa: E402

document = yaml.safe_load((root / relpath).read_text(encoding="utf-8"))
declared = tuple((document.get("tiers") or {}).keys())
print(f"  {'OK  ' if reader == declared and reader else 'FAIL'} the reader answers the catalog: "
      f"{list(reader)}")
if not reader:
    print("  FAIL  the authority answered an EMPTY ladder")
    raise SystemExit(1)
if reader != declared:
    print(f"  FAIL  the reader answers {list(reader)}, the catalog declares {list(declared)}")
    raise SystemExit(1)
if list(tiers.rank().values()) != list(range(len(reader))) or list(tiers.rank()) != list(reader):
    print(f"  FAIL  rank() is not the declaration order: {tiers.rank()}")
    raise SystemExit(1)
print(f"  OK    rank() is the declaration order: {tiers.rank()}")
raise SystemExit(0)
PY
)"; arm1_rc=$?
printf '%s\n' "$arm1_out"
if [ "$arm1_rc" -eq 0 ]; then
  echo "  OK    the ladder authority reads, in declaration order"
else
  report_fail "the ladder authority did not read (rc=$arm1_rc)"
fi

if ! python3 - "$root" "$work/refusals" <<'PY'
import shutil
import sys
from pathlib import Path

root, work = Path(sys.argv[1]), Path(sys.argv[2])
sys.path.insert(0, str(root))
from registry.profiles import tiers  # noqa: E402

problems = []


def refuses(what, prepare):
    """A named precondition broken on a COPY: the reader must refuse, by name."""
    scratch = work / what
    (scratch / "registry" / "profiles").mkdir(parents=True, exist_ok=True)
    shutil.copy(root / tiers.CATALOG_RELPATH, scratch / tiers.CATALOG_RELPATH)
    shutil.copy(root / "registry/profiles/tiers.py", scratch / "registry/profiles/tiers.py")
    prepare(scratch / tiers.CATALOG_RELPATH)
    tiers.clear_cache()
    try:
        got = tiers.authority(scratch)
    except tiers.TierVocabularyRefused as exc:
        print(f"  OK    REFUSED {what}: {str(exc)[:88]}")
        return
    except Exception as exc:  # noqa: BLE001 - any other failure is a finding too
        problems.append(f"{what}: raised {type(exc).__name__} instead of TierVocabularyRefused")
        return
    problems.append(f"{what}: answered {got} instead of refusing")


refuses("absent", lambda path: path.unlink())
refuses("empty", lambda path: path.write_text("tiers: {}\n", encoding="utf-8"))
refuses("duplicate", lambda path: path.write_text(
    "tiers:\n  LOW: {model: flash}\n  LOW: {model: pro}\n", encoding="utf-8"))
refuses("no-tiers", lambda path: path.write_text("memoryScopes: {}\n", encoding="utf-8"))
refuses("not-yaml", lambda path: path.write_text("tiers: [\n", encoding="utf-8"))
tiers.clear_cache()
for problem in problems:
    print(f"  FAIL  {problem}")
raise SystemExit(1 if problems else 0)
PY
then
  report_fail "a broken authority was not refused by name (see the FAIL lines above)"
fi

# ============================================================================ #
# 2. no second declaration in the product trees
# ============================================================================ #
scan_trees() { # scan_trees <root> [<label>]
  python3 - "$@" <<'PY'
"""Refuse a second DECLARATION of either vocabulary, by name, with line.

A declared ladder is a sequence literal — a tuple/list/set, or `frozenset({...})` /
`set([...])` / `tuple([...])` — whose elements are exactly one vocabulary's ids. The
walk is over the AST, not the text: a string that mentions the ids in prose, a dict
KEYED by them and an import of the reader are all not declarations.
"""
import ast
import sys
from pathlib import Path

root = Path(sys.argv[1])
label = sys.argv[2] if len(sys.argv) > 2 else root.name

TREES = ("registry", "gateway", "engine", "guardrails", "telemetry", "identity",
         "integrations", "control-plane", "portal", "fleet", "e2e")
VOCABULARIES = (
    ("the tier ladder", ("LOW", "MED", "HIGH", "MAX")),
    ("the FinOps tier names", ("flash", "pro", "auditor")),
)
WRAPPERS = {"frozenset", "set", "tuple", "list"}
SKIP_DIRS = {".git", "vendor", "__pycache__", ".verify", "tests", "fixtures",
             "node_modules", ".fleet"}


def literal_strings(node):
    """The literal strings of a sequence node, or None when it is not one."""
    if not isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return None
    out = []
    for element in node.elts:
        if not isinstance(element, ast.Constant) or not isinstance(element.value, str):
            return None
        out.append(element.value)
    return out


refused, noted, scanned = [], [], 0
for tree in TREES:
    base = root / tree
    if not base.is_dir():
        continue
    for path in sorted(base.rglob("*.py")):
        rel = path.relative_to(root)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        try:
            module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError) as exc:
            refused.append(f"{rel}: cannot be parsed as Python ({exc})")
            continue
        scanned += 1
        for node in ast.walk(module):
            found = literal_strings(node)
            if found is None and isinstance(node, ast.Call) \
                    and isinstance(node.func, ast.Name) and node.func.id in WRAPPERS \
                    and len(node.args) == 1:
                found = literal_strings(node.args[0])
            if found is None:
                continue
            for what, ids in VOCABULARIES:
                if len(found) == len(ids) and set(found) == set(ids):
                    refused.append(f"{rel}:{node.lineno} declares {what}"
                                   f" ({', '.join(repr(i) for i in found)})"
                                   " — read the authority instead")
        for node in ast.walk(module):
            if not isinstance(node, ast.Dict) or any(k is None for k in node.keys):
                continue
            keys = {k.value for k in node.keys
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)}
            for what, ids in VOCABULARIES:
                if set(ids) <= keys:
                    noted.append(f"{rel}:{node.lineno} maps {what} onto values")

for finding in sorted(set(refused)):
    print(f"REFUSED {finding}")
for finding in sorted(set(noted)):
    print(f"NOTE    {finding} (a keyed map, not a ladder: allowed to cover a subset)")
print(f"SCANNED {scanned} product module(s) under {label}")
raise SystemExit(1 if refused else 0)
PY
}

echo "== 2. no second declaration in the product trees =="
scan_out="$(scan_trees "$root" "the product trees")"; scan_rc=$?
printf '%s\n' "$scan_out" | sed 's/^/  /'
if [ "$scan_rc" -eq 0 ]; then
  echo "  OK    no product module re-declares either vocabulary"
else
  report_fail "a product module re-declares a vocabulary (refused above, by name)"
fi
if [[ $scan_out != *SCANNED\ [1-9]* ]]; then
  report_fail "the scan read no product module at all — a scan that cannot find the tree is not a pass"
fi

echo "== 2b. the provocation: a planted seventh copy is refused by name =="
planted="$work/planted"
mkdir -p "$planted/identity/onboarding"
printf '%s\n' \
  '"""A planted seventh copy (the gate control)."""' \
  '' \
  'MODEL_TIERS = ("LOW", "MED", "HIGH", "MAX")' > "$planted/identity/onboarding/planted_copy.py"
planted_out="$(scan_trees "$planted" "the planted tree")"; planted_rc=$?
printf '%s\n' "$planted_out" | sed 's/^/  /'
if [ "$planted_rc" -eq 1 ] \
   && [[ $planted_out == *"REFUSED identity/onboarding/planted_copy.py:3"* ]]; then
  echo "  OK    the planted copy is refused BY NAME and LINE"
else
  report_fail "the planted copy was not refused by name (rc=$planted_rc) — a detector that cannot fail is a formality"
fi
if [ "$scan_rc" -eq 0 ] && [ "$planted_rc" -eq 1 ]; then
  echo "  OK    the two paths DIFFER: the clean tree passes, the planted copy is refused"
else
  report_fail "the clean and planted paths collapsed (clean rc=$scan_rc, planted rc=$planted_rc)"
fi

# ============================================================================ #
# 3. every consumer READS its authority (imported, not grepped)
# ============================================================================ #
echo "== 3. every consumer reads its authority =="
if ! python3 - "$root" <<'PY'
import importlib
import sys
from pathlib import Path

root = Path(sys.argv[1])
for extra in (str(root), str(root / "gateway"), str(root / "fleet")):
    if extra not in sys.path:
        sys.path.insert(0, extra)

CATALOG, FINOPS = "catalog", "finops"
# module, source path, attribute, authority, shape, the reader the source must NAME
CONSUMERS = (
    ("providers.contract", "gateway/providers/contract.py",
     "TIERS", CATALOG, "tuple", "registry.profiles.tiers"),
    ("gateway/limits/fingerprint.py", "gateway/limits/fingerprint.py",
     "CANONICAL_TIERS", CATALOG, "set", "registry.profiles.tiers"),
    ("governance.authority.model", "governance/authority/model.py",
     "TIERS", CATALOG, "tuple", "registry.profiles.tiers"),
    ("governance.authority.model", "governance/authority/model.py",
     "TIER_RANK", CATALOG, "rank", "registry.profiles.tiers"),
    ("identity.onboarding.model", "identity/onboarding/model.py",
     "MODEL_TIERS", CATALOG, "tuple", "registry.profiles.tiers"),
    ("integrations.hermes.mapping", "integrations/hermes/mapping.py",
     "MODEL_TIERS", CATALOG, "tuple", "registry.profiles.tiers"),
    ("portal.server.chat", "portal/server/chat.py",
     "TIERS", CATALOG, "tuple", "registry.profiles.tiers"),
    ("registry.chat.labels", "registry/chat/labels.py",
     "TIERS", FINOPS, "tuple", "governance.finops"),
    ("fleet.channel", "fleet/channel.py",
     "MODEL_TIERS", FINOPS, "tuple", "governance.finops"),
)

tiers = importlib.import_module("registry.profiles.tiers")
finops = importlib.import_module("governance.finops.chooser")
authorities = {
    CATALOG: tuple(tiers.authority()),
    FINOPS: tuple(finops.vocabulary(finops.load_policy())[0]),
}

problems = 0
for module_name, source_rel, attribute, side, shape, reader in CONSUMERS:
    authority = authorities[side]
    if shape == "rank":
        expected = {tier: index for index, tier in enumerate(authority)}
    elif shape == "set":
        expected = set(authority)
    else:
        expected = authority
    try:
        if module_name.endswith(".py"):
            # a module whose PACKAGE cannot be imported here (gateway/providers
            # __init__ drags its provider config in): load the file itself, so the
            # value under test is still the one this tree ships.
            import importlib.util

            spec = importlib.util.spec_from_file_location("consumer_by_path", Path(root / module_name))
            module = importlib.util.module_from_spec(spec)
            sys.modules["consumer_by_path"] = module
            spec.loader.exec_module(module)
        else:
            module = importlib.import_module(module_name)
        got = getattr(module, attribute)
    except BaseException as exc:  # noqa: BLE001 - an unimportable consumer is a finding
        print(f"  FAIL  {module_name}.{attribute} could not be read: "
              f"{type(exc).__name__}: {exc}")
        problems += 1
        continue
    equal = got == expected
    named = reader in (Path(root) / source_rel).read_text(encoding="utf-8")
    print(f"  {'OK  ' if equal else 'FAIL'} {module_name}.{attribute}"
          f" == the {side} authority ({len(got)} of {len(expected)}, {type(got).__name__})")
    if not equal:
        print(f"        got {got!r}, authority {expected!r}")
        problems += 1
    if not named:
        print(f"  FAIL  {source_rel} does not name {reader} — the value is not derived"
              " from the reader, however equal it happens to be today")
        problems += 1
if problems:
    print(f"  {problems} finding(s)")
raise SystemExit(1 if problems else 0)
PY
then
  report_fail "a consumer does not read its authority (see the FAIL lines above)"
else
  echo "  OK    9 declared values across 8 sites equal their authority, and each source names its reader"
fi

# ============================================================================ #
# 4. the read FOLLOWS the authority — mutate it and re-import the consumer
# ============================================================================ #
echo "== 4. the read follows the authority, not a mirror of it =="

fold() { # fold <tree> — a minimal tree carrying an authority and its consumers
  local tree="$1"
  mkdir -p "$tree/registry/profiles" "$tree/gateway/providers" "$tree/gateway/limits" \
           "$tree/governance/authority" "$tree/governance/finops" "$tree/registry/chat" \
           "$tree/identity/onboarding" "$tree/integrations"
  cp "$ladder_authority" "$tree/$ladder_authority"
  cp "$ladder_reader" "$tree/$ladder_reader"
  cp "$finops_authority" "$tree/$finops_authority"
  cp governance/finops/chooser.py "$tree/governance/finops/chooser.py"
  cp gateway/providers/contract.py "$tree/gateway/providers/contract.py"
  cp gateway/limits/fingerprint.py "$tree/gateway/limits/fingerprint.py"
  cp governance/authority/model.py "$tree/governance/authority/model.py"
  cp identity/onboarding/model.py "$tree/identity/onboarding/model.py"
  cp registry/chat/labels.py "$tree/registry/chat/labels.py"
  # the hermes adapter's own import graph (its seam loader + audit + policy), copied
  # whole: a partial copy would fail for a reason that is not this check's subject.
  rm -rf "$tree/integrations/hermes" "$tree/integrations/_seam"
  cp -a integrations/hermes "$tree/integrations/hermes"
  cp -a integrations/_seam "$tree/integrations/_seam"
}

read_consumer() { # read_consumer <tree> <module or path> <attribute>
  # Read from INSIDE the tree (cwd = the tree), so nothing outside it can answer for
  # its authority: the reader resolves its repository root from its own __file__.
  ( cd "$1" || exit 4
    python3 - "$2" "$3" <<'PY'
import importlib
import importlib.util
import sys
from pathlib import Path

target, attribute = sys.argv[1], sys.argv[2]
if target.endswith(".py"):
    spec = importlib.util.spec_from_file_location("mutant_consumer", Path(target))
    module = importlib.util.module_from_spec(spec)
    sys.modules["mutant_consumer"] = module
    spec.loader.exec_module(module)
else:
    module = importlib.import_module(target)
got = getattr(module, attribute)
if isinstance(got, dict):
    print(" ".join(got))
elif isinstance(got, (set, frozenset)):
    # a set has no declaration order of its own: print it in sorted order, and the
    # expectations below are written in that same order.
    print(" ".join(sorted(got)))
else:
    print(" ".join(str(item) for item in got))
PY
  )
}

clean_tree="$work/follows-clean"
mutant_tree="$work/follows-mutant"
fold "$clean_tree"
fold "$mutant_tree"

mutate() { # mutate <tree> — rename the ladder's top rung, and the FinOps 'pro'
  python3 - "$1" <<'PY'
import json
import sys
from pathlib import Path

tree = Path(sys.argv[1])
catalog = tree / "registry/profiles/catalog.yaml"
before = catalog.read_text(encoding="utf-8")
after = before.replace("\n  MAX:", "\n  MAXQ:", 1)
assert after != before, "the ladder mutation did not match the authority"
catalog.write_text(after, encoding="utf-8")

policy_path = tree / "governance/finops/policy.json"
policy = json.loads(policy_path.read_text(encoding="utf-8"))
assert "pro" in policy["vocabulary"]["tiers"], "the FinOps mutation found no 'pro' tier"
policy["vocabulary"]["tiers"] = [
    "pro-q" if tier == "pro" else tier for tier in policy["vocabulary"]["tiers"]]
policy["tier_rank"] = {
    ("pro-q" if key == "pro" else key): rank for key, rank in policy["tier_rank"].items()}
policy_path.write_text(json.dumps(policy, indent=2) + "\n", encoding="utf-8")
print("mutated the COPY: catalog MAX -> MAXQ, FinOps pro -> pro-q")
PY
}
mutate "$mutant_tree" | sed 's/^/  /'

# target | attribute | what the clean control answers | what a FOLLOWER answers
# (a SET-valued consumer prints in sorted order: HIGH LOW MAX MED)
FOLLOWERS="
gateway/providers/contract.py|TIERS|LOW MED HIGH MAX|LOW MED HIGH MAXQ
gateway/limits/fingerprint.py|CANONICAL_TIERS|HIGH LOW MAX MED|HIGH LOW MAXQ MED
governance/authority/model.py|TIERS|LOW MED HIGH MAX|LOW MED HIGH MAXQ
identity/onboarding/model.py|MODEL_TIERS|LOW MED HIGH MAX|LOW MED HIGH MAXQ
integrations.hermes.mapping|MODEL_TIERS|LOW MED HIGH MAX|LOW MED HIGH MAXQ
registry/chat/labels.py|TIERS|flash pro auditor|flash pro-q auditor
"
followed=0
while IFS='|' read -r target attribute clean_want mutant_want; do
  [ -z "$target" ] && continue
  clean="$(read_consumer "$clean_tree" "$target" "$attribute" 2>&1)"; clean_rc=$?
  mutant="$(read_consumer "$mutant_tree" "$target" "$attribute" 2>&1)"; mutant_rc=$?
  if [ "$clean_rc" -ne 0 ]; then
    report_fail "$target:$attribute could not be read from the clean control tree ($clean)"
    continue
  fi
  if [ "$clean" != "$clean_want" ]; then
    report_fail "$target:$attribute in the clean control tree answered '$clean', expected '$clean_want'"
    continue
  fi
  if [ "$mutant_rc" -ne 0 ]; then
    report_fail "$target:$attribute FAILED once its authority was mutated ($mutant) — it did not follow it"
    continue
  fi
  if [ "$mutant" != "$mutant_want" ]; then
    report_fail "$target:$attribute did NOT follow the authority: answered '$mutant', the mutated authority says '$mutant_want'"
    continue
  fi
  echo "  OK    $target:$attribute follows its authority (clean '$clean' -> mutated '$mutant')"
  followed=$((followed + 1))
done <<< "$FOLLOWERS"

if [ "$followed" -eq 6 ]; then
  echo "  OK    6 of 6 mutation-proven consumers followed; 2 mutants against 2 authorities"
else
  report_fail "only $followed of 6 consumers followed a mutated authority"
fi
echo "  NOTE  portal/server/chat.py and fleet/channel.py are LINK-proven only (value == the"
echo "        authority, source names the reader): folding their transitive import graph"
echo "        (infra.rollout, governance.dispatch.model) would copy most of the repository"

after_sha="$(sha256sum "$ladder_authority" "$finops_authority" | awk '{print $1}' | tr '\n' ' ')"
if [ "$before_sha" = "$after_sha" ]; then
  echo "  OK    the REAL authorities are byte-identical after the mutation (the mutant was a copy)"
else
  report_fail "the mutation reached the REAL authority — refusing ('$before_sha' -> '$after_sha')"
fi

if [ "$fail" -eq 0 ]; then
  echo "check-tier-vocabulary: OK — one declared authority per vocabulary (the ladder in" \
       "$ladder_authority via $ladder_reader; the FinOps names in $finops_authority via" \
       "governance/finops/chooser.py), 6 consumers proved to FOLLOW it under mutation and 2" \
       "proved to read it, and a planted seventh copy is refused by name"
  exit 0
fi
echo "check-tier-vocabulary: FAIL ($fail finding(s))" >&2
exit 1
