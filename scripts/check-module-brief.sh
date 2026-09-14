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

# A clean clone has no submodule, so there is no hub catalog and no registry to
# brief from: the honest answer is CANNOT-ASSESS, never a pass.
if [ ! -f "$hub/catalog/mandatory.tsv" ] || [ ! -d "$hub/catalog/modules" ]; then
  echo "check-module-brief: CANNOT-ASSESS — the pinned hub catalog is absent" >&2
  echo "        expected $hub/catalog/mandatory.tsv and $hub/catalog/modules" >&2
  echo "        run 'git submodule update --init vendor/CMR' and re-run" >&2
  exit 2
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

sys.dont_write_bytecode = True
sys.path.insert(0, sys.argv[1])
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
findings = claim_findings(book.claims, repo_root=sys.argv[1], hub_root=sys.argv[1] + "/vendor/CMR", ids=("code-indexing",))
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

# 8. CANNOT-ASSESS is a real answer, not a pass
tree="$work/no-hub"
new_tree "$tree" || exit 2
rm -rf "$tree/vendor/CMR"
expect_cannot_assess "a tree with no hub catalog" \
  python3 "$tree/integrations/paperclip/reporting/cli.py" compose --repo "$tree"

if [ "$fail" -ne 0 ]; then
  echo "check-module-brief: FAIL — $fail of $((ok + fail)) check(s) failed" >&2
  exit 1
fi
echo "check-module-brief: OK — $ok check(s) passed (artifact frozen and current, claims resolve, capability granted, every acceptance refusal provoked by name)"
exit 0
