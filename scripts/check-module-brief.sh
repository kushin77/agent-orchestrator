#!/usr/bin/env bash
# check-module-brief.sh — the module-brief gate (issue #447).
#
# The paperclip reporting half (integrations/paperclip/reporting/) composes each
# mandatory module's distributable brief: what every repo must carry, at which
# pin, and whether it is current. A brief nothing validates is a formality
# (GR-12 / AO-GR-4), so this gate:
#
#   * regenerates the brief and requires the committed artifact
#     (docs/MODULE-BRIEF.md) to be byte-identical to a fresh composition — a
#     hand-edited brief, or one left stale by a registry change, is refused by
#     name (BRIEF-STALE);
#   * composes twice over one revision and requires byte-identical output
#     (determinism is an acceptance criterion, not a nicety);
#   * asserts the document is honest: exactly the authority's three states plus
#     the `not-a-module` refusal (never a fourth state), every mandatory module
#     briefed with pin/rev/assets/seed-per-asset/health/board-ref/drift, and a
#     `target-pending` module never rendered as shipped;
#   * checks the capability contract: the persona declaration must name the
#     capability and grant the tools it needs — declared and usable must not
#     diverge;
#   * requires every claim in the brief to resolve to a registry row or a cited
#     path, and refuses a claim that resolves to nothing BY NAMING ITS LINE
#     (the "runnable but not truthful" failure mode, kushin77/deepseek#117);
#   * PROVOKES each acceptance refusal in a scratch copy of the tree (never in
#     the lane tree and never in the read-only pinned submodule) and requires
#     every one to be refused by name: a module with no pin, a consumer asset
#     with no seed, a mandatory module reported as shipped while the registry
#     says pending, a capability whose tool the allowlist does not grant, an
#     uncited claim, and a stale artifact. Each file mutation proves it LANDED
#     (sha256 before != after) before the refusal is credited — a mutation that
#     never applied would otherwise certify nothing;
#   * holds the surface to the `enterprise` rung's machine evidence (issue #592,
#     parent #590), MEASURED with the class gate's own function — `controls`
#     (the declared claim-resolution policy, `claim-policy.json`), `audit` (the
#     append-only trail, `audit.py`) and `schema` (`brief.schema.json`) — and
#     refuses by name when any of them stops being present;
#   * proves each of those three is WIRED, not decorative: it doctors the frozen
#     schema and requires the composer to refuse `BRIEF-SCHEMA-INVALID`, doctors
#     the policy's refusal codes and requires the composer's refusals to change
#     with them, refuses a policy that claims pending may render as shipped, and
#     requires one audit record naming the line per run — with the trail proven
#     append-only across two runs;
#   * returns CANNOT-ASSESS (2) — never a pass — when the pinned hub catalog is
#     absent, and proves that path with a provoked hub-less tree.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-module-brief.sh
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

hub="vendor/CMR"
cli="integrations/paperclip/reporting/cli.py"

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-module-brief: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

# A clean clone has no submodule content, but a worktree cut from an existing
# checkout still holds the pinned submodule objects in the shared object store,
# so the hub can be materialised OFFLINE: `git submodule update --init` performs
# no network fetch (the gitlink SHA is already in the superproject's history and
# the module is cloned locally from the checkout's own object store). This makes
# the gate assess the pinned hub on a default checkout instead of skipping. When
# the objects genuinely are absent (a true fresh clone, no network), the init
# fails and the honest answer remains CANNOT-ASSESS — never a pass.
if [ ! -f "$hub/catalog/mandatory.tsv" ] || [ ! -d "$hub/catalog/modules" ]; then
  init_out="$(git submodule update --init vendor/CMR 2>&1)"
  init_rc=$?
  if [ "$init_rc" -ne 0 ] || [ ! -f "$hub/catalog/mandatory.tsv" ] || [ ! -d "$hub/catalog/modules" ]; then
    echo "check-module-brief: CANNOT-ASSESS — the pinned hub catalog is absent" >&2
    echo "        expected $hub/catalog/mandatory.tsv and $hub/catalog/modules" >&2
    printf '        %s\n' "$init_out" >&2
    echo "        offline self-initialise failed (rc=$init_rc); run 'git submodule update --init vendor/CMR' and re-run" >&2
    exit 2
  fi
  echo "check-module-brief: initialised vendor/CMR offline (it was uninitialised in this worktree)"
fi

ok=0
fail=0

echo "== the lane's own files =="
for required in \
  integrations/paperclip/reporting/__init__.py \
  integrations/paperclip/reporting/model.py \
  integrations/paperclip/reporting/capability.py \
  integrations/paperclip/reporting/composer.py \
  integrations/paperclip/reporting/cli.py \
  integrations/paperclip/reporting/README.md \
  integrations/paperclip/reporting/policy.py \
  integrations/paperclip/reporting/brief_schema.py \
  integrations/paperclip/reporting/audit.py \
  integrations/paperclip/reporting/claim-policy.json \
  integrations/paperclip/reporting/brief.schema.json \
  registry/personas/cards/paperclip.yaml \
  registry/profiles/seeds/paperclip.1.1.0.yaml \
  docs/MODULE-BRIEF.md
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
  echo "check-module-brief: FAIL ($fail missing file(s))" >&2
  exit 1
fi

# Every check below runs in `$work` only — never in the lane tree, and never in
# the read-only pinned submodule.
work="/tmp/m28-447-mb.$$.$(date +%s)"
mkdir "$work" || exit 2
trap 'rm -rf "$work"' EXIT

sha_of() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | cut -d' ' -f1
  else
    shasum -a 256 "$1" | cut -d' ' -f1
  fi
}

expect_ok() { # expect_ok <label> <cmd...>
  local label="$1"
  shift
  local out rc
  out="$("$@" 2>&1)"
  rc=$?
  if [ "$rc" -eq 0 ]; then
    echo "  OK    $label"
    ok=$((ok + 1))
  else
    echo "  FAIL  $label (expected rc=0, got rc=$rc)" >&2
    printf '%s\n' "$out" | sed 's/^/        /' >&2
    fail=$((fail + 1))
  fi
}

expect_refusal() { # expect_refusal <label> <named finding> <cmd...>
  local label="$1"
  local want="$2"
  shift 2
  local out rc
  out="$("$@" 2>&1)"
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

expect_cannot_assess() { # expect_cannot_assess <label> <cmd...>
  local label="$1"
  shift
  local out rc
  out="$("$@" 2>&1)"
  rc=$?
  if [ "$rc" -eq 2 ] && printf '%s' "$out" | grep -qF "CANNOT-ASSESS"; then
    echo "  OK    $label exits 2 (CANNOT-ASSESS), never 0"
    ok=$((ok + 1))
  else
    echo "  FAIL  $label: expected rc=2 and CANNOT-ASSESS (got rc=$rc)" >&2
    printf '%s\n' "$out" | sed 's/^/        /' >&2
    fail=$((fail + 1))
  fi
}

mutate() { # mutate <file> <python body> <label>
  local target="$1"
  local program="$2"
  local label="$3"
  local before after rc
  before="$(sha_of "$target")"
  python3 - "$target" "$program" <<'PY'
import sys

path, program = sys.argv[1], sys.argv[2]
source = open(path, encoding="utf-8").read()
namespace = {"source": source}
exec(program, namespace)  # noqa: S102 - the mutation is the point
out = namespace["source"]
if not isinstance(out, str):
    print("mutation produced no source string", file=sys.stderr)
    raise SystemExit(2)
if out == source:
    print("mutation did not change the source — it would certify nothing", file=sys.stderr)
    raise SystemExit(3)
with open(path, "w", encoding="utf-8") as handle:
    handle.write(out)
raise SystemExit(0)
PY
  rc=$?
  if [ "$rc" -ne 0 ]; then
    echo "  FAIL  the mutation for '$label' did not apply (python rc=$rc)" >&2
    fail=$((fail + 1))
    return 1
  fi
  after="$(sha_of "$target")"
  if [ "$before" = "$after" ]; then
    echo "  FAIL  the mutation for '$label' left $(basename "$target") unchanged" >&2
    fail=$((fail + 1))
    return 1
  fi
  echo "  OK    mutation LANDED ($label): $(basename "$target") sha256 ${before:0:12}… -> ${after:0:12}…"
  ok=$((ok + 1))
  return 0
}

echo "== the brief is clean on the tree as committed =="
expect_ok "the artifact is byte-identical to a fresh composition, and every claim resolves" \
  python3 "$cli" check

echo "== the claims resolve, and there are claims to resolve =="
expect_ok "every claim cites a registry row or a cited path" python3 "$cli" claims

echo "== the declaration grants what the capability uses =="
expect_ok "the persona declares module-brief with the read-only access it needs" \
  python3 "$cli" capability

echo "== determinism: two compositions over one revision =="
python3 "$cli" compose > "$work/a.md" 2> "$work/a.err"
rc_a=$?
python3 "$cli" compose > "$work/b.md" 2> "$work/b.err"
rc_b=$?
if [ "$rc_a" -eq "$rc_b" ] && cmp -s "$work/a.md" "$work/b.md"; then
  echo "  OK    byte-identical ($(wc -c < "$work/a.md") bytes, sha256 $(sha_of "$work/a.md"))"
  ok=$((ok + 1))
else
  echo "  FAIL  two compositions over one revision differ (rc=$rc_a vs $rc_b)" >&2
  diff -u "$work/a.md" "$work/b.md" | head -20 | sed 's/^/        /' >&2
  fail=$((fail + 1))
fi
if [ "$rc_a" -ne 0 ]; then
  echo "  FAIL  compose exited $rc_a on the tree as committed" >&2
  sed 's/^/        /' "$work/a.err" >&2
  fail=$((fail + 1))
fi

echo "== the document is honest =="
if python3 - "$work/a.md" <<'PY'
import sys

text = open(sys.argv[1], encoding="utf-8").read()
problems = []
states = ("registered-mandatory", "target-pending", "catalog-module-not-mandatory")

for state in states:
    if "`{}`".format(state) not in text:
        problems.append("the document never names the state %r" % state)
if "`not-a-module`" not in text:
    problems.append("the document never names the not-a-module refusal")
for forbidden in ("fourth state", "assumed-open", "assumed-closed"):
    if forbidden in text:
        problems.append("the document asserts %r" % forbidden)

# Every registered mandatory module is briefed with every acceptance field.
required_rows = (
    "owning repo",
    "mandatory status",
    "pin",
    "rev",
    "consumer assets",
    "health",
    "board ref",
    "drift",
)
for row in required_rows:
    if "| {} |".format(row) not in text:
        problems.append("no %r row was rendered" % row)

# Pending is never rendered as shipped.
pending = [line for line in text.splitlines() if "`target-pending`" in line and line.startswith("| mandatory status |")]
if not pending:
    problems.append("no target-pending module was rendered at all")
for line in pending:
    if "not shipped" not in line or "shipped: false" not in line:
        problems.append("a target-pending module is not rendered as unshipped: %s" % line.strip())
if "shipped: true" in text:
    problems.append("the document contains 'shipped: true', which no entry may claim here")

# Distribution stays with the existing channel, and nothing new is pushed.
for marker in ("controller/standards-sync.sh", "controller/standards-manifest.txt"):
    if marker not in text:
        problems.append("the distribution channel %r is not named" % marker)
if "adds no second push mechanism" not in text:
    problems.append("the document does not state that no second push mechanism was added")

if problems:
    print("  FAIL  the brief is not honest:", file=sys.stderr)
    for problem in problems:
        print("        %s" % problem, file=sys.stderr)
    raise SystemExit(1)
print("  OK    three states + the refusal, every acceptance field, pending unshipped, channel named")
PY
then
  ok=$((ok + 1))
else
  fail=$((fail + 1))
fi

# ---------------------------------------------------------------------------
# the surface's own enterprise evidence, measured by the class gate's own rule
# ---------------------------------------------------------------------------
echo "== the surface's own enterprise evidence (issue #592) =="
# Measured with the class gate's OWN function, never with a second opinion: a
# surface whose evidence the class gate cannot see is a surface declared above
# its evidence, which is the failure this lane exists to fix. A tree that has
# lost the measurement function is CANNOT-ASSESS, never a pass.
if [ ! -f governance/conformance/surfaces.py ]; then
  echo "check-module-brief: CANNOT-ASSESS — the class gate's measurement function is absent" >&2
  echo "        expected governance/conformance/surfaces.py" >&2
  exit 2
fi
cat > "$work/evidence.py" <<'PY'
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, "governance/conformance")
import surfaces as S  # noqa: E402

surface = "integrations/paperclip/reporting"
evidence = S.measure_path_evidence(Path("."), surface)
print(
    "  OK    {} measures {}".format(
        surface, {name: evidence[name] for name in sorted(evidence)}
    )
)
missing = [name for name in ("controls", "audit", "schema") if not evidence.get(name)]
if missing:
    print(
        "  FAIL  {} ships no {} — the surface sits below its declared class".format(
            surface, ", ".join(missing)
        ),
        file=sys.stderr,
    )
    raise SystemExit(1)
PY
expect_ok "controls (claim-policy.json), audit (audit.py) and schema (brief.schema.json) are all present" \
  python3 "$work/evidence.py"

# ---------------------------------------------------------------------------
# provocations — scratch trees only
# ---------------------------------------------------------------------------
new_tree() { # new_tree <dir> ; echoes the tree path
  local tree="$1"
  mkdir -p "$tree" || return 1
  mkdir -p "$tree/vendor/CMR" "$tree/.board" "$tree/scripts" \
    "$tree/registry/personas/cards" "$tree/registry/profiles/seeds" \
    "$tree/governance" "$tree/integrations" || return 1
  cp -a "$root/integrations/paperclip" "$tree/integrations/paperclip" || return 1
  cp -a "$root/governance/modules" "$tree/governance/modules" || return 1
  cp -a "$root/registry/personas/cards/paperclip.yaml" \
    "$tree/registry/personas/cards/paperclip.yaml" || return 1
  cp -a "$root/registry/profiles/seeds/paperclip.1.1.0.yaml" \
    "$tree/registry/profiles/seeds/paperclip.1.1.0.yaml" || return 1
  cp -a "$root/.board/snapshot.json" "$tree/.board/snapshot.json" || return 1
  cp -a "$root/scripts/check-module-brief.sh" "$tree/scripts/check-module-brief.sh" || return 1
  cp -a "$root/module.json" "$tree/module.json" 2>/dev/null || true
  cp -a "$hub/catalog" "$tree/vendor/CMR/catalog" || return 1
  cp -a "$hub/templates" "$tree/vendor/CMR/templates" || return 1
  cp -a "$hub/controller" "$tree/vendor/CMR/controller" || return 1
  # Stale bytecode in the tree under test can shadow the mutation and fake a
  # pass (see the repo's gate traps): purge it, and never write more.
  find "$tree" -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true
  return 0
}

echo "== a scratch tree composes clean (the provocations below are not always-red) =="
tree="$work/clean"
new_tree "$tree" || exit 2
scratch_cli="$tree/integrations/paperclip/reporting/cli.py"
expect_ok "a scratch copy of the tree composes clean and its claims resolve" \
  python3 "$scratch_cli" claims --repo "$tree"

echo "== the scratch tree really is the tree under test =="
if python3 - "$tree" <<'PY'
import sys
from pathlib import Path

tree = Path(sys.argv[1]).resolve()
sys.dont_write_bytecode = True
sys.path.insert(0, str(tree))
from governance.modules import model, registry  # noqa: E402

resolved = {Path(model.__file__).resolve(), Path(registry.__file__).resolve()}
outside = [p for p in resolved if not str(p).startswith(str(tree))]
if outside:
    print("  FAIL  the tree under test imported %s instead of the scratch copy" % outside, file=sys.stderr)
    raise SystemExit(1)
print("  OK    governance.modules resolved inside the scratch tree (%s)" % sorted(str(p.relative_to(tree)) for p in resolved))
PY
then
  ok=$((ok + 1))
else
  fail=$((fail + 1))
fi

echo "== provoked refusals (scratch trees only; the lane tree and vendor/CMR are never written) =="

# 1. a module with no pin
tree="$work/no-pin"
new_tree "$tree" || exit 2
if mutate "$tree/vendor/CMR/catalog/modules/code-indexing/module.json" \
  'import json
data = json.loads(source)
data["versions"]["latest"] = None
source = json.dumps(data, indent=2) + "\n"' "a mandatory module with no pin"; then
  expect_refusal "a mandatory module with no pin" "BRIEF-MODULE-NO-PIN: code-indexing" \
    python3 "$tree/integrations/paperclip/reporting/cli.py" compose --repo "$tree"
fi

# 2. a consumer asset with no seed
tree="$work/no-seed"
new_tree "$tree" || exit 2
before="$(sha_of "$tree/vendor/CMR/templates/module/tokens.json")"
rm -f "$tree/vendor/CMR/templates/module/tokens.json"
if [ ! -f "$tree/vendor/CMR/templates/module/tokens.json" ]; then
  echo "  OK    mutation LANDED (the shared-frontend seed removed): templates/module/tokens.json sha256 ${before:0:12}… -> absent"
  ok=$((ok + 1))
  expect_refusal "a consumer asset with no seed" "BRIEF-ASSET-NO-SEED: shared-frontend" \
    python3 "$tree/integrations/paperclip/reporting/cli.py" compose --repo "$tree"
else
  echo "  FAIL  the seed removal for 'a consumer asset with no seed' did not land" >&2
  fail=$((fail + 1))
fi

# 3. a pending module reported as shipped
tree="$work/pending-shipped"
new_tree "$tree" || exit 2
if python3 - "$tree" "$work/pending.json" <<'PY'
import json
import sys

tree, out = sys.argv[1], sys.argv[2]
sys.dont_write_bytecode = True
sys.path.insert(0, tree)
from governance.modules import registry  # noqa: E402

doc = registry.build(tree, tree + "/vendor/CMR")
for entry in doc["modules"]:
    if entry["state"] == "target-pending":
        entry["shipped"] = True
        break
else:
    print("no target-pending entry to doctor", file=sys.stderr)
    raise SystemExit(1)
with open(out, "w", encoding="utf-8") as handle:
    json.dump(doc, handle, indent=2)
print("  OK    mutation LANDED (a target-pending entry now reports shipped: true): %s" % out)
PY
then
  ok=$((ok + 1))
  expect_refusal "a mandatory module reported as shipped while the registry says pending" \
    "BRIEF-PENDING-RENDERED-SHIPPED: pmo" \
    python3 "$tree/integrations/paperclip/reporting/cli.py" compose --repo "$tree" \
      --registry "$work/pending.json"
else
  echo "  FAIL  the pending-rendered-as-shipped mutation did not land" >&2
  fail=$((fail + 1))
fi

# 4. a capability whose tool the allowlist does not grant
tree="$work/no-tool"
new_tree "$tree" || exit 2
if mutate "$tree/registry/personas/cards/paperclip.yaml" \
  'source = source.replace("  - file_read\n  - file_write\n", "  - file_write\n")' \
  "the card stops granting file_read"; then
  expect_refusal "a capability whose tool the allowlist does not grant" \
    "BRIEF-CAPABILITY-TOOL-UNGRANTED: file_read" \
    python3 "$tree/integrations/paperclip/reporting/cli.py" capability --repo "$tree"
fi

# 5. a capability the card does not declare
tree="$work/no-capability"
new_tree "$tree" || exit 2
if mutate "$tree/registry/personas/cards/paperclip.yaml" \
  'source = source.replace("  - memory-ops\n  - module-brief\n", "  - memory-ops\n")' \
  "the card stops declaring the capability"; then
  expect_refusal "a capability the card does not declare" \
    "BRIEF-CAPABILITY-UNDECLARED: module-brief" \
    python3 "$tree/integrations/paperclip/reporting/cli.py" capability --repo "$tree"
fi

# 6. a claim that resolves to nothing (the line is named)
tree="$work/uncited"
new_tree "$tree" || exit 2
if python3 - "$tree" <<'PY'
import sys

tree = sys.argv[1]
sys.dont_write_bytecode = True
sys.path.insert(0, tree)
from integrations.paperclip.reporting import policy as claim_policy  # noqa: E402
from integrations.paperclip.reporting.model import ClaimBook, claim_findings  # noqa: E402

book = ClaimBook()
book.add("# Module brief")
book.claim(
    "| pin | 1.2.3 | (none) |",
    subject="code-indexing",
    fact="pin",
    value="1.2.3",
    citations=(),
)
findings = claim_findings(
    book.claims,
    repo_root=tree,
    hub_root=tree + "/vendor/CMR",
    ids=("code-indexing",),
    policy=claim_policy.load(tree + "/integrations/paperclip/reporting"),
)
if not findings:
    print("  FAIL  an uncited claim was accepted", file=sys.stderr)
    raise SystemExit(1)
rendered = findings[0].render()
if "BRIEF-CLAIM-UNRESOLVED: code-indexing" not in rendered or "docs/MODULE-BRIEF.md:2" not in rendered:
    print("  FAIL  the uncited claim was not refused by name and line: %s" % rendered, file=sys.stderr)
    raise SystemExit(1)
print("  OK    REFUSED an uncited claim — %s" % rendered)
PY
then
  ok=$((ok + 1))
else
  fail=$((fail + 1))
fi

# 7. a stale artifact
tree="$work/stale"
new_tree "$tree" || exit 2
mkdir -p "$tree/docs"
python3 "$tree/integrations/paperclip/reporting/cli.py" compose --repo "$tree" \
  --out "$tree/docs/MODULE-BRIEF.md" > "$work/freeze.out" 2>&1
if [ -s "$tree/docs/MODULE-BRIEF.md" ]; then
  printf '\nhand-edited after freezing\n' >> "$tree/docs/MODULE-BRIEF.md"
  before="$(sha_of "$tree/docs/MODULE-BRIEF.md")"
  echo "  OK    mutation LANDED (the frozen artifact was hand-edited): sha256 ${before:0:12}…"
  ok=$((ok + 1))
  expect_refusal "a hand-edited (stale) artifact" "BRIEF-STALE: docs/MODULE-BRIEF.md" \
    python3 "$tree/integrations/paperclip/reporting/cli.py" check --repo "$tree"
else
  echo "  FAIL  the artifact could not be frozen in the scratch tree" >&2
  sed 's/^/        /' "$work/freeze.out" >&2
  fail=$((fail + 1))
fi

# 8. a brief the frozen schema no longer accepts (issue #592)
tree="$work/schema-drift"
new_tree "$tree" || exit 2
if mutate "$tree/integrations/paperclip/reporting/brief.schema.json" \
  'import json
data = json.loads(source)
data["properties"]["summary"]["required"] = data["properties"]["summary"]["required"] + ["refused-names"]
source = json.dumps(data, indent=2) + "\n"' \
  "the frozen schema stops being satisfied by the brief the composer emits"; then
  expect_refusal "a brief the frozen schema no longer accepts" 'BRIEF-SCHEMA-INVALID: $.summary' \
    python3 "$tree/integrations/paperclip/reporting/cli.py" compose --repo "$tree"
fi

# 9-12. the claim policy and the audit trail are WIRED, not decorative.
#
# Composing from a registry document whose mandatory module claims a seed that is
# not there makes a CLAIM cite a path that resolves nowhere — the one shape whose
# resolution is the policy's business.
tree="$work/policy"
new_tree "$tree" || exit 2
unresolved_doc="$work/unresolved-registry.json"
unresolved_id="$work/unresolved-id.txt"
if python3 - "$tree" "$unresolved_doc" "$unresolved_id" <<'PY'
import json
import sys

tree, out, idfile = sys.argv[1], sys.argv[2], sys.argv[3]
sys.dont_write_bytecode = True
sys.path.insert(0, tree)
from governance.modules import registry  # noqa: E402

doc = registry.build(tree, tree + "/vendor/CMR")
for entry in doc["modules"]:
    if entry["state"] == "registered-mandatory":
        entry["assets"][0]["seed"] = "templates/module/not-here.json"
        entry["assets"][0]["seed_present"] = True
        chosen = entry["id"]
        break
else:
    print("  FAIL  no registered-mandatory module to doctor", file=sys.stderr)
    raise SystemExit(1)
with open(out, "w", encoding="utf-8") as handle:
    json.dump(doc, handle, indent=2)
with open(idfile, "w", encoding="utf-8") as handle:
    handle.write(chosen)
print("  OK    mutation LANDED (asset of %r claims a seed that is not there): %s" % (chosen, out))
PY
then
  ok=$((ok + 1))
else
  echo "  FAIL  the unresolved-claim mutation did not land" >&2
  fail=$((fail + 1))
fi

# 9. the unresolved-claim refusal comes out under the DECLARED code ...
if [ -f "$unresolved_id" ]; then
  expect_refusal "a claim citing a path that is not there, under the declared code" \
    "BRIEF-CLAIM-UNRESOLVED: $(cat "$unresolved_id")" \
    python3 "$tree/integrations/paperclip/reporting/cli.py" compose \
      --repo "$tree" --registry "$unresolved_doc"
fi

# 10. ... and under the code the POLICY declares, which is how the code is proven
# to read the artifact rather than restate it.
if mutate "$tree/integrations/paperclip/reporting/claim-policy.json" \
  'import json
data = json.loads(source)
data["resolution"]["unresolved"]["code"] = "BRIEF-CLAIM-UNRESOLVED-MUT"
source = json.dumps(data, indent=2) + "\n"' \
  "the claim policy renames the unresolved-claim refusal"; then
  if [ -f "$unresolved_id" ]; then
    expect_refusal "the same claim refuses under the code the POLICY declares" \
      "BRIEF-CLAIM-UNRESOLVED-MUT: $(cat "$unresolved_id")" \
      python3 "$tree/integrations/paperclip/reporting/cli.py" compose \
        --repo "$tree" --registry "$unresolved_doc"
  fi
fi

# 11. the pending rule's refusal code is the policy's too
tree="$work/policy-pending"
new_tree "$tree" || exit 2
pending_doc="$work/policy-pending-registry.json"
if python3 - "$tree" "$pending_doc" <<'PY'
import json
import sys

tree, out = sys.argv[1], sys.argv[2]
sys.dont_write_bytecode = True
sys.path.insert(0, tree)
from governance.modules import registry  # noqa: E402

doc = registry.build(tree, tree + "/vendor/CMR")
for entry in doc["modules"]:
    if entry["state"] == "target-pending":
        entry["shipped"] = True
        break
else:
    print("  FAIL  no target-pending entry to doctor", file=sys.stderr)
    raise SystemExit(1)
with open(out, "w", encoding="utf-8") as handle:
    json.dump(doc, handle, indent=2)
print("  OK    mutation LANDED (a target-pending entry now reports shipped: true): %s" % out)
PY
then
  ok=$((ok + 1))
else
  echo "  FAIL  the pending-rendered-as-shipped mutation did not land" >&2
  fail=$((fail + 1))
fi
if mutate "$tree/integrations/paperclip/reporting/claim-policy.json" \
  'import json
data = json.loads(source)
data["pending"]["refusals"]["rendered_shipped"] = "BRIEF-PENDING-RENDERED-SHIPPED-MUT"
source = json.dumps(data, indent=2) + "\n"' \
  "the claim policy renames the pending-rendered-as-shipped refusal"; then
  expect_refusal "pending rendered as shipped refuses under the code the POLICY declares" \
    "BRIEF-PENDING-RENDERED-SHIPPED-MUT: pmo" \
    python3 "$tree/integrations/paperclip/reporting/cli.py" compose \
      --repo "$tree" --registry "$pending_doc"
fi

# 12. a policy that lets pending render as shipped is not assessable at all
tree="$work/policy-shipped"
new_tree "$tree" || exit 2
if mutate "$tree/integrations/paperclip/reporting/claim-policy.json" \
  'import json
data = json.loads(source)
data["pending"]["never_rendered_as_shipped"] = False
source = json.dumps(data, indent=2) + "\n"' \
  "the claim policy declares pending may render as shipped"; then
  expect_cannot_assess "a claim policy that lets pending render as shipped" \
    python3 "$tree/integrations/paperclip/reporting/cli.py" compose --repo "$tree"
fi

# 13. the audit trail: one record per composed run, the line named, append-only
tree="$work/audit"
new_tree "$tree" || exit 2
trail="$work/audit-trail.jsonl"
rm -f "$trail"
expect_refusal "the first recorded run (the trail is created by the run)" \
  "BRIEF-CLAIM-UNRESOLVED:" \
  python3 "$tree/integrations/paperclip/reporting/cli.py" compose \
    --repo "$tree" --registry "$unresolved_doc" --audit "$trail"
if [ -f "$trail" ]; then
  first_bytes="$(wc -c < "$trail")"
  first_record="$(head -1 "$trail")"
  expect_refusal "the second recorded run (the trail must APPEND, never overwrite)" \
    "BRIEF-CLAIM-UNRESOLVED:" \
    python3 "$tree/integrations/paperclip/reporting/cli.py" compose \
      --repo "$tree" --registry "$unresolved_doc" --audit "$trail"
  if python3 - "$trail" "$unresolved_id" "$first_record" "$first_bytes" <<'PY'
import json
import sys

trail, idfile, first_record, first_bytes = sys.argv[1:5]
module_id = open(idfile, encoding="utf-8").read().strip()
raw = open(trail, encoding="utf-8").read()
lines = [line for line in raw.splitlines() if line.strip()]
records = [json.loads(line) for line in lines]
problems = []
if len(records) != 2:
    problems.append("one record per composed run means 2 records, found %d" % len(records))
if records and lines[0] != first_record:
    problems.append("the first run's record changed — the trail is not append-only")
if len(raw) <= int(first_bytes):
    problems.append("the trail did not grow (still %s bytes)" % first_bytes)
if len(records) == 2 and records[0] != records[1]:
    problems.append("two runs over one revision recorded different things")
if records:
    last = records[-1]
    if last["resolved"] != last["claims"] - last["unresolved"]:
        problems.append("the record's resolved/unresolved counts do not add up: %s" % last)
    named = [f for f in last["findings"] if "BRIEF-CLAIM-UNRESOLVED: %s" % module_id in f]
    if not named:
        problems.append("no record names the refusing claim: %s" % last["findings"])
    elif "docs/MODULE-BRIEF.md:" not in named[0] or "templates/module/not-here.json" not in named[0]:
        problems.append("the record does not name the line and the citation: %s" % named[0])
if problems:
    print("  FAIL  the audit trail is not what the issue pins:", file=sys.stderr)
    for problem in problems:
        print("        %s" % problem, file=sys.stderr)
    raise SystemExit(1)
print("  OK    two runs appended two identical records; each names the refusing claim line")
PY
  then
    ok=$((ok + 1))
  else
    fail=$((fail + 1))
  fi
else
  echo "  FAIL  the run recorded nothing: no audit trail at $trail" >&2
  fail=$((fail + 1))
fi

# 14. CANNOT-ASSESS is a real answer, not a pass
tree="$work/no-hub"
new_tree "$tree" || exit 2
rm -rf "$tree/vendor/CMR"
expect_cannot_assess "a tree with no hub catalog" \
  python3 "$tree/integrations/paperclip/reporting/cli.py" compose --repo "$tree"

if [ "$fail" -ne 0 ]; then
  echo "check-module-brief: FAIL — $fail of $((ok + fail)) check(s) failed" >&2
  exit 1
fi
echo "check-module-brief: OK — $ok check(s) passed (artifact frozen and current, claims resolve, capability granted, enterprise evidence present and wired, every acceptance refusal provoked by name)"
exit 0
