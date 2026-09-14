#!/usr/bin/env bash
# check-module-registry.sh — the ecosystem module registry gate (issue #445).
#
# The registry (governance/modules/) exists so the ecosystem has ONE honest view
# of every module — mandatory status, consumer assets, pin/rev, owning repo,
# board ref and a health probe — assembled by reference from the hub's own
# registry. A registry nothing validates is a formality (GR-12), so this gate:
#
#   * builds the registry twice over one revision and requires byte-identical
#     output (determinism is an acceptance criterion, not a nicety);
#   * asserts the document is honest: exactly three states (never two),
#     `not-a-module` is a refusal and not a fourth state, every entry carries
#     every acceptance field, every health probe is well-formed and a probe that
#     did not run never reports `ok`, and a `target-pending` entry is never
#     reported as shipped and always names its blocking hub issue;
#   * reconciles the inventory against the hub's own two surfaces — the
#     mandatory registry (catalog/mandatory.tsv) and the module flags
#     (catalog/modules/*/module.json) — so the registry cannot disagree with the
#     authority it claims to read, and cannot report a module the hub does not
#     carry;
#   * PROVOKES each acceptance refusal in a scratch copy of the hub (never in the
#     lane tree, and never in the read-only pinned submodule) and requires every
#     one to be refused BY NAME: a duplicated module id, a mandatory module whose
#     consumer asset has no seed, an unregistered-but-mandatory module in BOTH
#     directions, a declared target that landed without being registered
#     mandatory, a hub module manifest copied in-tree, a module distribution
#     package carried in-tree, a registry reference pointing outside the hub,
#     and membership claimed for a name the catalog does not carry;
#   * DRIVES the three artifacts that judge a build (issue #591) through the CLI,
#     so an artifact the code ignores cannot ship as decoration: a declared
#     acceptance policy that files a refusal code as `recorded`, one that omits a
#     code the registry emits, one whose authority would let a claim confer
#     membership, a frozen schema the emitted document cannot satisfy, and an
#     append-only audit trail that must grow in place while recording every
#     judged name exactly once — with the clean run still green, because a gate
#     that is only ever red proves nothing;
#   * returns CANNOT-ASSESS (2) — never a pass — when the pinned hub catalog is
#     absent, and proves that path with a provoked empty hub.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-module-registry.sh
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

hub="vendor/CMR"
cli="python3 governance/modules/cli.py"
# A relative --hub is resolved against --repo (the hub is a property of the
# repository), so the scratch-repo probes below pass the absolute hub root.
hub_abs="$root/$hub"

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-module-registry: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

# A clean clone has no submodule, so there is no authority to read: the honest
# answer is CANNOT-ASSESS, never a pass.
if [ ! -f "$hub/catalog/mandatory.tsv" ] || [ ! -d "$hub/catalog/modules" ]; then
  echo "check-module-registry: CANNOT-ASSESS — the pinned hub catalog is absent" >&2
  echo "        expected $hub/catalog/mandatory.tsv and $hub/catalog/modules" >&2
  echo "        run 'git submodule update --init vendor/CMR' and re-run" >&2
  exit 2
fi

ok=0
fail=0

echo "== the lane's own files =="
for required in \
  governance/modules/__init__.py \
  governance/modules/model.py \
  governance/modules/hub.py \
  governance/modules/registry.py \
  governance/modules/vendoring.py \
  governance/modules/health.py \
  governance/modules/cli.py \
  governance/modules/targets.json \
  governance/modules/controls.yaml \
  governance/modules/policy.py \
  governance/modules/module-registry.schema.json \
  governance/modules/schema.py \
  governance/modules/audit.py \
  governance/modules/README.md \
  docs/MODULE-REGISTRY.md
do
  if [ -f "$required" ]; then
    echo "  OK    $required present"
    ok=$((ok + 1))
  else
    echo "  FAIL  $required is missing" >&2
    fail=$((fail + 1))
  fi
done
if [ "$fail" -gt 0 ]; then
  echo "check-module-registry: FAIL ($fail missing file(s))" >&2
  exit 1
fi

work="/tmp/m28-445-mr.$$.$(date +%s)"
mkdir "$work" || exit 2
trap 'rm -rf "$work"' EXIT

hash_of() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | cut -d' ' -f1
  else
    shasum -a 256 "$1" | cut -d' ' -f1
  fi
}

echo "== the registry builds clean =="
if $cli verify > "$work/verify.out" 2> "$work/verify.err"; then
  echo "  OK    $($cli verify 2>/dev/null | tail -1)"
  ok=$((ok + 1))
else
  rc=$?
  echo "  FAIL  the registry is not clean on the tree as committed (rc=$rc)" >&2
  sed 's/^/        /' "$work/verify.out" >&2
  sed 's/^/        /' "$work/verify.err" >&2
  fail=$((fail + 1))
fi

echo "== determinism: two builds over one revision =="
$cli build > "$work/a.json" 2> "$work/a.err"
rc_a=$?
$cli build > "$work/b.json" 2> "$work/b.err"
rc_b=$?
if [ "$rc_a" -eq "$rc_b" ] && cmp -s "$work/a.json" "$work/b.json"; then
  echo "  OK    byte-identical ($(wc -c < "$work/a.json") bytes, sha256 $(hash_of "$work/a.json"))"
  ok=$((ok + 1))
else
  echo "  FAIL  two builds over one revision differ (rc=$rc_a vs $rc_b)" >&2
  diff -u "$work/a.json" "$work/b.json" | head -20 | sed 's/^/        /' >&2
  fail=$((fail + 1))
fi
if [ "$rc_a" -ne 0 ]; then
  echo "  FAIL  build exited $rc_a on the tree as committed" >&2
  sed 's/^/        /' "$work/a.err" >&2
  fail=$((fail + 1))
fi

echo "== the document is honest =="
if python3 - "$work/a.json" <<'PY'
import json
import sys

doc = json.load(open(sys.argv[1], encoding="utf-8"))
states = doc.get("states")
problems = []

if states != ["registered-mandatory", "target-pending", "catalog-module-not-mandatory"]:
    problems.append("states = %r (there are exactly three, specified in that order)" % (states,))
if doc.get("membership_refusal") != "not-a-module":
    problems.append("membership_refusal is %r, not 'not-a-module'" % (doc.get("membership_refusal"),))
if doc.get("membership_refusal") in (states or []):
    problems.append("'not-a-module' is being reported as a fourth state")

required = (
    "id", "state", "shipped", "mandatory", "consumer_assets", "owning_repo",
    "pin", "rev", "board_ref", "reference", "health",
)
statuses = ("ok", "not-run", "unreachable", "no-tag", "absent", "unknown")
modules = doc.get("modules") or []
refused = doc.get("not_modules") or []
if not modules or not refused:
    problems.append("the document is vacuous (no module entries, or no refusals)")
for entry in modules:
    if entry.get("state") not in (states or []):
        problems.append("%s: state %r is not one of the three" % (entry.get("id"), entry.get("state")))
    if entry.get("state") == "target-pending":
        if entry.get("shipped") is not False:
            problems.append("%s: target-pending is reported as shipped" % entry["id"])
        if not entry.get("blocking"):
            problems.append("%s: target-pending names no blocking hub issue" % entry["id"])
for entry in refused:
    if entry.get("state") != "not-a-module":
        problems.append("%s: a refused name carries state %r" % (entry.get("id"), entry.get("state")))
    if entry.get("membership") != "refused":
        problems.append("%s: a refused name does not record its refusal" % entry.get("id"))
    if not entry.get("detail"):
        problems.append("%s: a refused name carries no detail" % entry.get("id"))
for entry in modules + refused:
    for key in required:
        if key not in entry:
            problems.append("%s: missing the acceptance field %r" % (entry.get("id"), key))
    spec = entry.get("health") or {}
    if spec.get("status") == "ok":
        problems.append("%s: a probe this build never ran reports 'ok'" % entry.get("id"))
    if spec.get("status") not in statuses:
        problems.append("%s: unknown probe status %r" % (entry.get("id"), spec.get("status")))
    if not spec.get("reason"):
        problems.append("%s: the health probe carries no reason" % entry.get("id"))

for state in states or []:
    if state not in (doc.get("summary") or {}):
        problems.append("state %r is declared but not counted" % state)
if (doc.get("summary") or {}).get("not-a-module") != len(doc.get("not_modules") or []):
    problems.append("the refused count does not match the not_modules list")

if problems:
    print("  FAIL  the registry document is dishonest:", file=sys.stderr)
    for problem in problems:
        print("        %s" % problem, file=sys.stderr)
    raise SystemExit(1)
print("  OK    three states, a separate refusal, every acceptance field, honest probes")
print("        inventory: %s" % ", ".join("%s=%d" % kv for kv in sorted(doc["summary"].items())))
PY
then
  ok=$((ok + 1))
else
  fail=$((fail + 1))
fi

echo "== the inventory is reconciled against the hub's two surfaces =="
if python3 - "$work/a.json" "$hub" <<'PY'
import json
import sys
from pathlib import Path

doc = json.load(open(sys.argv[1], encoding="utf-8"))
hub = Path(sys.argv[2])
modules = {entry["id"]: entry for entry in doc["modules"]}
problems = []

flags = {}
for directory in sorted((hub / "catalog" / "modules").iterdir()):
    manifest = directory / "module.json"
    if not manifest.is_file():
        continue
    data = json.loads(manifest.read_text(encoding="utf-8"))
    flags[str(data["id"])] = bool(data.get("mandatory"))

rows = set()
for line in (hub / "catalog" / "mandatory.tsv").read_text(encoding="utf-8").splitlines():
    if not line.strip() or line.lstrip().startswith("#"):
        continue
    cols = [col.strip() for col in line.split("\t")]
    if cols == ["id", "module", "consumer_assets", "reason"]:
        continue
    rows.add(cols[0])

mandatory = {module_id for module_id, flag in flags.items() if flag}
if rows != mandatory:
    problems.append(
        "mandatory.tsv and the module flags disagree: %s" % sorted(rows ^ mandatory)
    )
for module_id, flag in flags.items():
    entry = modules.get(module_id)
    if entry is None:
        problems.append("%s: in the hub catalog but absent from the registry" % module_id)
        continue
    expected = "registered-mandatory" if flag else "catalog-module-not-mandatory"
    if entry["state"] != expected:
        problems.append("%s: state %r, the hub says %r" % (module_id, entry["state"], expected))
    if flag:
        if not entry["consumer_assets"]:
            problems.append("%s: a registered mandatory module lists no consumer assets" % module_id)
        for asset in entry["assets"]:
            if not asset["seed_present"]:
                problems.append("%s: consumer asset %r resolves to no seed" % (module_id, asset["asset"]))
for module_id, entry in modules.items():
    if entry["state"] in ("registered-mandatory", "catalog-module-not-mandatory"):
        if module_id not in flags:
            problems.append("%s: reported as registered, the hub catalog carries no entry" % module_id)
    elif entry["state"] == "target-pending" and module_id in flags:
        problems.append("%s: reported target-pending, the hub catalog registers it" % module_id)
for entry in doc["not_modules"]:
    if entry["id"] in flags:
        problems.append("%s: refused as not-a-module, yet the hub catalog registers it" % entry["id"])

if problems:
    print("  FAIL  the registry disagrees with the hub it reads:", file=sys.stderr)
    for problem in problems:
        print("        %s" % problem, file=sys.stderr)
    raise SystemExit(1)
print("  OK    %d catalog module(s), %d mandatory row(s), %d declared name(s) reconciled"
      % (len(flags), len(rows), len(modules) + len(doc["not_modules"])))
PY
then
  ok=$((ok + 1))
else
  fail=$((fail + 1))
fi

echo "== CANNOT-ASSESS is a real answer, not a pass =="
mkdir -p "$work/empty-hub"
$cli verify --hub "$work/empty-hub" > "$work/nohub.out" 2> "$work/nohub.err"
rc=$?
if [ "$rc" -eq 2 ] && grep -q "CANNOT-ASSESS" "$work/nohub.err"; then
  echo "  OK    an absent hub catalog exits 2 (CANNOT-ASSESS), never 0"
  ok=$((ok + 1))
else
  echo "  FAIL  an absent hub catalog exited $rc (expected 2)" >&2
  sed 's/^/        /' "$work/nohub.err" >&2
  fail=$((fail + 1))
fi

new_hub() { # new_hub <dir>
  mkdir -p "$1/catalog" "$1/templates/module" "$1/guardrails" || return 1
  cp -a "$hub/catalog/modules" "$1/catalog/modules" || return 1
  cp -a "$hub/catalog/mandatory.tsv" "$1/catalog/mandatory.tsv" || return 1
  if [ -d "$hub/templates/module" ]; then
    cp -a "$hub/templates/module/." "$1/templates/module/" || return 1
  fi
}

expect_refusal() { # expect_refusal <label> <named finding> <hub dir>
  local label="$1" want="$2" hubdir="$3"
  local out rc
  out="$($cli verify --hub "$hubdir" 2>&1)"
  rc=$?
  if [ "$rc" -eq 1 ] && printf '%s' "$out" | grep -qF -- "$want"; then
    echo "  OK    REFUSED $label — $want"
    ok=$((ok + 1))
  else
    echo "  FAIL  $label: expected rc=1 and the named refusal '$want' (got rc=$rc)" >&2
    printf '%s\n' "$out" | sed 's/^/        /' >&2
    fail=$((fail + 1))
  fi
}

expect_vendor_refusal() { # expect_vendor_refusal <label> <named finding> <repo dir>
  local label="$1" want="$2" repodir="$3"
  local out rc
  out="$($cli vendoring --repo "$repodir" --hub "$hub_abs" 2>&1)"
  rc=$?
  if [ "$rc" -eq 1 ] && printf '%s' "$out" | grep -qF -- "$want"; then
    echo "  OK    REFUSED $label — $want"
    ok=$((ok + 1))
  else
    echo "  FAIL  $label: expected rc=1 and '$want' (got rc=$rc)" >&2
    printf '%s\n' "$out" | sed 's/^/        /' >&2
    fail=$((fail + 1))
  fi
}

echo "== provoked refusals (scratch hubs only; the lane tree and vendor/CMR are never written) =="

scratch="$work/hub-dup"
new_hub "$scratch" || exit 2
cp -a "$scratch/catalog/modules/shared-frontend" "$scratch/catalog/modules/zz-shared-frontend-copy"
expect_refusal "a duplicated module id" "MODULE-DUPLICATE-ID: shared-frontend" "$scratch"

scratch="$work/hub-noseed"
new_hub "$scratch" || exit 2
rm -f "$scratch/templates/module/tokens.json"
expect_refusal "a mandatory consumer asset with no seed" "MODULE-ASSET-NO-SEED: shared-frontend" "$scratch"

scratch="$work/hub-tsv-only"
new_hub "$scratch" || exit 2
printf 'ghost-module\tghost-module\tghost.json\tprovoked by the gate\n' >> "$scratch/catalog/mandatory.tsv"
expect_refusal "unregistered-but-mandatory (registry surface)" "MODULE-UNREGISTERED-MANDATORY: ghost-module" "$scratch"

scratch="$work/hub-flag-only"
new_hub "$scratch" || exit 2
if python3 - "$scratch/catalog/modules/erp-crm/module.json" <<'PY'
import json
import sys

path = sys.argv[1]
data = json.load(open(path, encoding="utf-8"))
data["mandatory"] = True
with open(path, "w", encoding="utf-8") as handle:
    json.dump(data, handle, indent=2)
    handle.write("\n")
PY
then
  expect_refusal "unregistered-but-mandatory (flags surface)" "MODULE-UNREGISTERED-FLAG: erp-crm" "$scratch"
else
  echo "  FAIL  could not provoke the flags-side drift" >&2
  fail=$((fail + 1))
fi

scratch="$work/hub-target"
new_hub "$scratch" || exit 2
mkdir -p "$scratch/catalog/modules/pmo"
printf '%s\n' '{"schema":"cmr.module/v1","id":"pmo","name":"pmo","source":{"repo":"kushin77/pmo"}}' \
  > "$scratch/catalog/modules/pmo/module.json"
expect_refusal "a declared target that landed without being registered mandatory" \
  "MODULE-TARGET-LANDED-NOT-MANDATORY: pmo" "$scratch"

echo "== provoked vendoring refusals (scratch repos only) =="
scratch="$work/repo-clean"
mkdir -p "$scratch"
out="$($cli vendoring --repo "$scratch" --hub "$hub_abs" 2>&1)"
rc=$?
if [ "$rc" -eq 0 ]; then
  echo "  OK    a clean scratch tree refuses nothing (the vendor check is not always-red)"
  ok=$((ok + 1))
else
  echo "  FAIL  a clean scratch tree was refused (rc=$rc)" >&2
  printf '%s\n' "$out" | sed 's/^/        /' >&2
  fail=$((fail + 1))
fi

scratch="$work/repo-copy"
mkdir -p "$scratch/shared-frontend"
cp "$hub/catalog/modules/shared-frontend/module.json" "$scratch/shared-frontend/module.json"
expect_vendor_refusal "a hub module manifest copied in-tree" "VENDOR-SOURCE-IN-TREE: shared-frontend" "$scratch"

scratch="$work/repo-package"
mkdir -p "$scratch/codeidx"
expect_vendor_refusal "a module distribution package carried in-tree" "VENDOR-IN-TREE-PACKAGE: codeidx" "$scratch"

scratch="$work/repo-submodule"
mkdir -p "$scratch"
printf '[submodule "vendor/CMR"]\n\tpath = vendor/CMR\n[submodule "hermes"]\n\tpath = third_party/hermes\n' \
  > "$scratch/.gitmodules"
expect_vendor_refusal "an extra vendored submodule path" "VENDOR-EXTRA-SUBMODULE: third_party/hermes" "$scratch"

if python3 - "$work/a.json" "$work/registry-outside.json" <<'PY'
import json
import sys

doc = json.load(open(sys.argv[1], encoding="utf-8"))
doc["modules"][0]["reference"]["path"] = "../../elsewhere/module.json"
with open(sys.argv[2], "w", encoding="utf-8") as handle:
    json.dump(doc, handle, indent=2)
PY
then
  out="$($cli vendoring --hub "$hub" --registry "$work/registry-outside.json" 2>&1)"
  rc=$?
  if [ "$rc" -eq 1 ] && printf '%s' "$out" | grep -qF "VENDOR-PATH-OUTSIDE-HUB:"; then
    echo "  OK    REFUSED a registry reference pointing outside the hub — VENDOR-PATH-OUTSIDE-HUB:"
    ok=$((ok + 1))
  else
    echo "  FAIL  a registry reference outside the hub was not refused (rc=$rc)" >&2
    printf '%s\n' "$out" | sed 's/^/        /' >&2
    fail=$((fail + 1))
  fi
else
  echo "  FAIL  could not provoke the out-of-hub reference" >&2
  fail=$((fail + 1))
fi

echo "== the three artifacts that judge a build (issue #591) =="

# The policy, the frozen schema and the audit trail are only worth shipping if the
# code path reads them. Every provocation below drives a *scratch mutant* through
# the CLI — the lane tree is never edited — and requires the refusal BY NAME: a
# mutant the registry ignored would prove the artifact decorative.
expect_cannot_assess() { # expect_cannot_assess <label> <want> <want2|-> <cli args...>
  local label="$1" want="$2" want2="$3"
  shift 3
  local out rc shown="$want"
  [ "$want2" = "-" ] || shown="$shown / $want2"
  out="$($cli verify "$@" 2>&1)"
  rc=$?
  if [ "$rc" -eq 2 ] && printf '%s' "$out" | grep -qF -- "$want" \
    && { [ "$want2" = "-" ] || printf '%s' "$out" | grep -qF -- "$want2"; }
  then
    echo "  OK    REFUSED $label — $shown"
    ok=$((ok + 1))
  else
    echo "  FAIL  $label: expected rc=2 and '$shown' (got rc=$rc)" >&2
    printf '%s\n' "$out" | sed 's/^/        /' >&2
    fail=$((fail + 1))
  fi
}

# The defaults are the packaged artifacts: the report names the policy it read and
# the schema it enforced, so "the artifact was used" is observed rather than assumed.
if grep -qF "governance/modules/controls.yaml" "$work/verify.out" \
  && grep -qF "governance/modules/module-registry.schema.json" "$work/verify.out"; then
  echo "  OK    the packaged policy and schema judged the build (named in the verify report)"
  ok=$((ok + 1))
else
  echo "  FAIL  the verify report does not name the packaged policy/schema" >&2
  sed 's/^/        /' "$work/verify.out" >&2
  fail=$((fail + 1))
fi

policy_mutant() { # policy_mutant <name> <python body reading `data`>
  python3 - "governance/modules/controls.yaml" "$work/$1" "$2" <<'PY'
import sys
import yaml

src, dst, body = sys.argv[1], sys.argv[2], sys.argv[3]
with open(src, encoding="utf-8") as handle:
    data = yaml.safe_load(handle)
namespace = {"data": data}
exec(body, namespace)
with open(dst, "w", encoding="utf-8") as handle:
    yaml.safe_dump(data, handle, sort_keys=False)
PY
}

recorded="$work/controls-recorded.yaml"
if policy_mutant controls-recorded.yaml '
for condition in data["conditions"]:
    if "MODULE-DUPLICATE-ID" in condition.get("codes", []):
        condition["disposition"] = "recorded"
'; then
  expect_cannot_assess "a policy that files a refusal code as recorded" \
    "MODULE-DUPLICATE-ID" "fatal" --policy "$recorded"
else
  echo "  FAIL  could not write the recorded-disposition policy mutant" >&2
  fail=$((fail + 1))
fi

undeclared="$work/controls-undeclared.yaml"
if policy_mutant controls-undeclared.yaml '
for condition in data["conditions"]:
    condition["codes"] = [
        code for code in condition.get("codes", []) if code != "VENDOR-EXTRA-SUBMODULE"
    ]
'; then
  expect_cannot_assess "a policy that does not declare a code the registry emits" \
    "VENDOR-EXTRA-SUBMODULE" "declares no condition" --policy "$undeclared"
else
  echo "  FAIL  could not write the undeclared-code policy mutant" >&2
  fail=$((fail + 1))
fi

claiming="$work/controls-claim.yaml"
if policy_mutant controls-claim.yaml '
data["authority"]["claim_effect"] = "confers-membership"
'; then
  expect_cannot_assess "a policy that would let a claim confer membership" \
    "membership-not-inferred-from-a-claim" "claim_effect" --policy "$claiming"
else
  echo "  FAIL  could not write the claim-authority policy mutant" >&2
  fail=$((fail + 1))
fi

narrowed="$work/schema-narrowed.json"
if python3 - "governance/modules/module-registry.schema.json" "$narrowed" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    data = json.load(handle)
data["properties"]["states"]["items"] = {"enum": ["registered-mandatory"]}
with open(sys.argv[2], "w", encoding="utf-8") as handle:
    json.dump(data, handle, indent=2)
PY
then
  expect_cannot_assess "a schema the emitted document cannot satisfy" \
    "frozen schema" "states" --schema "$narrowed"
else
  echo "  FAIL  could not write the narrowed schema mutant" >&2
  fail=$((fail + 1))
fi

echo "== the audit trail is append-only, and records every judgment =="
trail="$work/trail.jsonl"
$cli verify --audit "$trail" > "$work/trail1.out" 2>&1
rc_one=$?
cp -f "$trail" "$work/trail1.copy"
$cli verify --audit "$trail" > "$work/trail2.out" 2>&1
rc_two=$?
if [ "$rc_one" -eq 0 ] && [ "$rc_two" -eq 0 ]; then
  if python3 - "$work/a.json" "$work/trail1.copy" "$trail" <<'PY'
import json
import sys

doc = json.load(open(sys.argv[1], encoding="utf-8"))
first = open(sys.argv[2], "rb").read()
second = open(sys.argv[3], "rb").read()
keys = {
    "schema", "kind", "subject", "code", "state",
    "condition", "disposition", "detail", "source",
}
problems = []

if not first:
    problems.append("the first append wrote nothing")
if not second.startswith(first) or len(second) != 2 * len(first):
    problems.append(
        "the trail was not appended to in place: %d byte(s), then %d"
        % (len(first), len(second))
    )

records = []
for number, line in enumerate(second.decode("utf-8").splitlines(), start=1):
    if not line.strip():
        continue
    try:
        record = json.loads(line)
    except ValueError as exc:
        problems.append("line %d is not JSON: %s" % (number, exc))
        continue
    if set(record) != keys:
        problems.append("line %d carries %s" % (number, sorted(set(record) ^ keys)))
    if record.get("schema") != "ao.module-registry/audit-v1":
        problems.append("line %d carries schema %r" % (number, record.get("schema")))
    if record.get("kind") not in ("refusal", "judgment"):
        problems.append("line %d carries kind %r" % (number, record.get("kind")))
    if record.get("disposition") not in ("fatal", "recorded"):
        problems.append("line %d carries disposition %r" % (number, record.get("disposition")))
    records.append(record)

half = len(records) // 2
if records[:half] != records[half:]:
    problems.append("the two appends did not record the same judgments")
expected = sorted(
    [entry["id"] for entry in doc["modules"]]
    + [entry["id"] for entry in doc["not_modules"]]
)
if sorted(record["subject"] for record in records[:half]) != expected:
    problems.append("the trail and the document judge different names")
if len(set(record["subject"] for record in records[:half])) != half:
    problems.append("a judged name was recorded twice")

if problems:
    print("  FAIL  the audit trail is not what it claims:", file=sys.stderr)
    for problem in problems:
        print("        %s" % problem, file=sys.stderr)
    raise SystemExit(1)
print(
    "  OK    %d record(s) appended twice, in place, every judged name recorded once"
    % half
)
PY
  then
    ok=$((ok + 1))
  else
    fail=$((fail + 1))
  fi
else
  echo "  FAIL  the trail probe did not run clean (rc=$rc_one / $rc_two)" >&2
  for trail_run in "$work/trail1.out" "$work/trail2.out"; do
    sed 's/^/        /' "$trail_run" >&2
  done
  fail=$((fail + 1))
fi

# One refusal, one record: the drift hub is reused, and the *delta* the trail
# attributes to the refusal must be exactly one refusal record — with the
# judgments still recorded beside it.
trail_refusals="$work/trail-refusals.jsonl"
$cli verify --hub "$work/hub-tsv-only" --audit "$trail_refusals" > "$work/trail3.out" 2>&1
rc_three=$?
if [ "$rc_three" -eq 1 ]; then
  if python3 - "$trail_refusals" <<'PY'
import json
import sys

records = [
    json.loads(line)
    for line in open(sys.argv[1], encoding="utf-8")
    if line.strip()
]
refusals = [record for record in records if record["kind"] == "refusal"]
judgments = [record for record in records if record["kind"] == "judgment"]
problems = []

if len(refusals) != 1:
    problems.append("expected exactly one refusal record, found %d" % len(refusals))
else:
    if refusals[0]["code"] != "MODULE-UNREGISTERED-MANDATORY":
        problems.append("the refusal record names %r" % refusals[0]["code"])
    if refusals[0]["subject"] != "ghost-module":
        problems.append("the refusal record names subject %r" % refusals[0]["subject"])
    if refusals[0]["disposition"] != "fatal":
        problems.append("the refusal record carries disposition %r" % refusals[0]["disposition"])
    if not refusals[0]["condition"]:
        problems.append("the refusal record names no declared condition")
if not judgments:
    problems.append("the judged names were not recorded beside the refusal")

if problems:
    print("  FAIL  one refusal did not append exactly one judgement:", file=sys.stderr)
    for problem in problems:
        print("        %s" % problem, file=sys.stderr)
    raise SystemExit(1)
print(
    "  OK    one refusal appended exactly one refusal record (%s), with %d judgment(s) beside it"
    % (refusals[0]["code"], len(judgments))
)
PY
  then
    ok=$((ok + 1))
  else
    fail=$((fail + 1))
  fi
else
  echo "  FAIL  the drift hub was not NOT-OK (rc=$rc_three; expected 1)" >&2
  sed 's/^/        /' "$work/trail3.out" >&2
  fail=$((fail + 1))
fi

echo "== membership is refused, not inferred =="
scratch="$work/repo-claim"
mkdir -p "$scratch"
printf '%s\n' '{"schema":"cmr.module/v1","id":"claimant","submodules":[{"id":"not-a-module-anywhere","repo":"kushin77/nope","admission":"declared","request":"kushin77/nope#1"}]}' \
  > "$scratch/module.json"
out="$($cli membership not-a-module-anywhere --repo "$scratch" --hub "$hub_abs" 2>&1)"
rc=$?
if [ "$rc" -eq 1 ] && printf '%s' "$out" | grep -qF "not-a-module: not-a-module-anywhere" \
  && printf '%s' "$out" | grep -qF "does not confer membership"; then
  echo "  OK    REFUSED membership for a claimed name the catalog does not carry — not-a-module: not-a-module-anywhere"
  ok=$((ok + 1))
else
  echo "  FAIL  a claimed name the catalog does not carry was not refused (rc=$rc)" >&2
  printf '%s\n' "$out" | sed 's/^/        /' >&2
  fail=$((fail + 1))
fi

out="$($cli membership shared-frontend 2>&1)"
rc=$?
if [ "$rc" -eq 0 ] && printf '%s' "$out" | grep -qF "registered-mandatory: shared-frontend"; then
  echo "  OK    a registered module resolves to its state — registered-mandatory: shared-frontend"
  ok=$((ok + 1))
else
  echo "  FAIL  a registered module did not resolve (rc=$rc)" >&2
  printf '%s\n' "$out" | sed 's/^/        /' >&2
  fail=$((fail + 1))
fi

if [ "$fail" -ne 0 ]; then
  echo "check-module-registry: FAIL — $fail of $((ok + fail)) check(s) failed" >&2
  exit 1
fi
echo "check-module-registry: OK — $ok check(s) passed (registry clean, deterministic, reconciled, every refusal provoked by name, policy/schema/trail driven through the CLI)"
exit 0
