#!/usr/bin/env bash
# check-cross-reference.sh — the institutional knowledge cross-reference spine
# must be valid (EPIC #138, issue #384).
#
# The catalogue carries items (nodes); the spine carries typed edges between
# them. A relationship that points at nothing, an edge type outside the closed
# vocabulary, a cmr-refs: marker that is malformed or names an unresolvable
# target, or a spine that rebuilds differently on two runs is a failure — never
# a skip (no-false-green doctrine, GR-12).
#
# The gate also runs its own negative control: it mutates one cmr-refs: target
# in a copy of the convention doc into a bogus id and requires the marker
# validation to refuse it. If the mutant passes, this gate reports FAIL — a
# check that cannot fail is a formality.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
# CANNOT-ASSESS must never be reported as a pass.
#
# Usage: bash scripts/check-cross-reference.sh
#
# ---knowledge---
# module_id: scripts.check-cross-reference
# system: governance
# app: gates
# solution_class: enterprise
# patterns: [tri-state-exit, provoked-negative-control, no-false-green, deterministic]
# derives_from: null
# owner_sme: qa-sme
# tier: L1
# interfaces: [exit 0 OK, exit 1 NOT-OK, exit 2 CANNOT-ASSESS]
# invariants: ""
# gotchas: ""
# related: ["#138", "#384"]
# do_not_duplicate: null
# ---knowledge---
set -u

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 2

catalog="governance/knowledge/catalog.json"
convention="docs/CROSS-REFERENCE-SPINE.md"

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-cross-reference: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

if [ ! -f "$catalog" ]; then
  echo "check-cross-reference: CANNOT-ASSESS — no catalogue at $catalog (run build first)" >&2
  exit 2
fi

if [ ! -f "$convention" ]; then
  echo "check-cross-reference: FAIL — $convention is missing (the convention has no spine)" >&2
  exit 1
fi

python3 - "$root" <<'PY'
import json
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root / "governance" / "knowledge"))

import crossref  # noqa: E402
import indexer  # noqa: E402
from model import RELATIONSHIP_TYPES  # noqa: E402

failures = []

# --- 1. The recorded catalogue carries a valid relationship list -------------
catalog = crossref.load_catalog(root)
if not isinstance(catalog, dict) or "relationships" not in catalog:
    print(
        "  FAIL  governance/knowledge/catalog.json (lacks a relationships list)",
        file=sys.stderr,
    )
    raise SystemExit(1)
relationships = catalog.get("relationships")
if not isinstance(relationships, list):
    print(
        "  FAIL  governance/knowledge/catalog.json (relationships is not a list)",
        file=sys.stderr,
    )
    raise SystemExit(1)

for edge in relationships:
    if not isinstance(edge, dict):
        failures.append("governance/knowledge/catalog.json: relationship is not an object")
        continue
    rel_type = str(edge.get("type", ""))
    from_id = str(edge.get("from_id", ""))
    to_id = str(edge.get("to_id", ""))
    if not from_id or not rel_type or not to_id:
        failures.append(
            "governance/knowledge/catalog.json: relationship %r is missing from_id/type/to_id"
            % (edge,)
        )
        continue
    if rel_type not in RELATIONSHIP_TYPES:
        failures.append(
            "governance/knowledge/catalog.json: edge type %r is outside the closed vocabulary"
            % rel_type
        )
    ok, what = crossref.resolve_target(root, from_id)
    if not ok:
        failures.append(
            "governance/knowledge/catalog.json: %s -%s-> %s: from_id %r does not resolve (%s)"
            % (from_id, rel_type, to_id, from_id, what)
        )
    ok, what = crossref.resolve_target(root, to_id)
    if not ok:
        failures.append(
            "governance/knowledge/catalog.json: %s -%s-> %s: to_id %r does not resolve (%s)"
            % (from_id, rel_type, to_id, to_id, what)
        )

print(
    "  OK    catalogue carries %d relationship(s); types within the closed vocabulary"
    % len(relationships)
)

# --- 2. Every cmr-refs: marker in tracked markdown must resolve ---------------
marker_count = 0
for rel in crossref.iter_tracked_markdown(root):
    try:
        text = (root / rel).read_text(encoding="utf-8")
    except OSError:
        continue
    marker_count += len(crossref.parse_markers(text))
    failures.extend(crossref.marker_findings(root, rel, text))
print(
    "  OK    scanned tracked markdown; %d cmr-refs: marker(s) parsed" % marker_count
)

# --- 3. Determinism: two builds over one revision must agree ------------------
first = indexer.build_index(root, generated_at="fixed")
second = indexer.build_index(root, generated_at="fixed")
first_json = json.dumps(first.as_dict(), indent=2, sort_keys=True)
second_json = json.dumps(second.as_dict(), indent=2, sort_keys=True)
if first_json != second_json:
    failures.append(
        "governance/knowledge/catalog.json: two builds differ (the spine is not deterministic)"
    )
    print("  FAIL  determinism: two builds over one revision differ", file=sys.stderr)
else:
    print("  OK    determinism: two builds over one revision are byte-identical")

if failures:
    for finding in failures:
        print("  FAIL  %s" % finding, file=sys.stderr)
    raise SystemExit(1)

print(
    "  OK    cross-reference spine: relationships valid, markers resolved, builds deterministic"
)
raise SystemExit(0)
PY
rc=$?
case "$rc" in
  0) : ;;
  1)
    echo "check-cross-reference: FAIL — cross-reference spine violated (see above)" >&2
    exit 1
    ;;
  *)
    echo "check-cross-reference: CANNOT-ASSESS — validator returned $rc" >&2
    exit 2
    ;;
esac

# --- negative control ---------------------------------------------------------
# Vacuity control: mutate one cmr-refs: target in a copy of the convention doc
# into a bogus id and require the marker validation to refuse it. The mutation
# must change the text and the sha256; if the mutant still passes, this gate
# reports FAIL — a check that cannot fail is a formality.
work="/tmp/ao138x.$$.$(date +%s)"
if ! mkdir -p "$work" 2>/dev/null; then
  echo "check-cross-reference: CANNOT-ASSESS — cannot create a scratch directory" >&2
  exit 2
fi
trap 'rm -rf "$work"' EXIT

before="$(sha256sum "$convention" | awk '{print $1}')"

python3 - "$root" "$convention" "$work/spine.md" <<'PY'
import re
import sys

src = sys.argv[2]
dst = sys.argv[3]

text = open(src, encoding="utf-8").read()
lines = text.split("\n")
done = False
for i, line in enumerate(lines):
    match = re.match(r"^([ \t]*cmr-refs:[ \t]*)(.*)$", line)
    if not match:
        continue
    rest = match.group(2).strip()
    if not rest:
        continue
    targets = [token.strip() for token in rest.split(",")]
    if not targets:
        continue
    targets[0] = "RCA-9999"
    lines[i] = match.group(1) + ", ".join(targets)
    done = True
    break
if not done:
    raise SystemExit(2)
open(dst, "w", encoding="utf-8").write("\n".join(lines))
PY
mutant_build_rc=$?
if [ "$mutant_build_rc" -ne 0 ]; then
  echo "check-cross-reference: CANNOT-ASSESS — could not build the negative control" >&2
  exit 2
fi

after="$(sha256sum "$work/spine.md" | awk '{print $1}')"
if [ "$before" = "$after" ]; then
  echo "check-cross-reference: CANNOT-ASSESS — mutation did not change the file" >&2
  exit 2
fi
if ! grep -qF -- "RCA-9999" "$work/spine.md"; then
  echo "check-cross-reference: CANNOT-ASSESS — the mutated target is not present" >&2
  exit 2
fi

mutant_out="$(python3 - "$root" "$work/spine.md" <<'PY' 2>&1
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root / "governance" / "knowledge"))

import crossref  # noqa: E402

text = Path(sys.argv[2]).read_text(encoding="utf-8")
findings = crossref.marker_findings(root, "docs/CROSS-REFERENCE-SPINE.md", text)
for finding in findings:
    print("  FAIL  " + finding)
raise SystemExit(1 if findings else 0)
PY
)"
mutant_rc=$?
if [ "$mutant_rc" -eq 1 ] && printf '%s\n' "$mutant_out" | grep -qF -- "RCA-9999"; then
  echo "  OK    negative control: mutating a cmr-refs: target is refused and named"
else
  echo "check-cross-reference: FAIL — negative control passed; a bogus cmr-refs: target was not caught (the gate cannot fail)" >&2
  exit 1
fi

# --- the gate's own scratch must not decide the verdict (#2013) -----------
# The fixture below SEEDS throwaway git repositories, so the inherited git
# environment must be neutralised first: `git -C` does not override an exported
# GIT_DIR, so a caller with one exported makes `git init/add/commit` act on the
# REAL repository instead of the scratch fixture -- measured here as three stray
# `base` commits on this lane's own branch and a stripped `vendor/` gitlink.
# `scripts/lib/unset-git-env.sh` (issue #1642, SP-11) is that remedy.
git_env_lib="$root/scripts/lib/unset-git-env.sh"
if [ ! -r "$git_env_lib" ]; then
  echo "check-cross-reference: CANNOT-ASSESS — scripts/lib/unset-git-env.sh is missing; the fixture block below would inherit GIT_DIR and could write into the repo it judges" >&2
  exit 2
fi
# shellcheck source=scripts/lib/unset-git-env.sh
source "$git_env_lib"
# The guard is an assertion, not a hope: if any repo-routing variable survives it,
# `git -C` would lose to the environment and the fixture below could write into the
# repository this check is judging. Measured on this lane: with GIT_DIR exported,
# `git -C <scratch> init` returns 0 while creating nothing, and the following
# `add -A`/`commit` would land as `base` commits in the real repository.
if [ -n "${GIT_DIR:-}${GIT_WORK_TREE:-}${GIT_INDEX_FILE:-}${GIT_COMMON_DIR:-}${GIT_OBJECT_DIRECTORY:-}" ]; then
  echo "check-cross-reference: FAIL — a git repository environment survived scripts/lib/unset-git-env.sh (GIT_DIR='${GIT_DIR:-}' GIT_WORK_TREE='${GIT_WORK_TREE:-}'); the fixture block would write into the repository it judges" >&2
  exit 1
fi
#
# `make verify` rewrites `.board/snapshot.json` IN PLACE before this check runs:
# scripts/check-dispatch-queue.sh self-heals it through board_selfheal ->
# snapshot.refresh, which truncates at `gh issue list --limit 1000`. Reading that
# working-tree file made this check's verdict a function of whether an earlier
# check had already run -- a refreshed snapshot dropped the closed `issue-4` that
# `catalog.json` still references, and the spine reddened on a tree whose own
# commit was sound. Resolution now reads the COMMITTED revision, so both halves
# are provoked here:
#   * a working-tree refresh that loses a referenced issue must NOT change the
#     verdict (this is the defect),
#   * a COMMITTED snapshot that genuinely lost it must STILL be refused (this is
#     what stops the first half from being vacuous).
scratch_out="$(python3 - "$root" "$work" <<'PY' 2>&1
import json
import subprocess
import sys
from pathlib import Path

repo = Path(sys.argv[1]).resolve()
scratch = Path(sys.argv[2]).resolve()
sys.path.insert(0, str(repo / "governance" / "knowledge"))

import crossref  # noqa: E402


def build(directory, committed, working):
    """A scratch git root whose COMMIT and whose WORKING TREE can differ."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / ".board").mkdir(exist_ok=True)
    target = directory / ".board" / "snapshot.json"

    def payload(numbers):
        return {"schema": "fixture", "issues": [{"number": n} for n in numbers]}

    target.write_text(json.dumps(payload(committed)), encoding="utf-8")
    for argv in (
        ["init", "-q"],
        ["add", "-A"],
        ["-c", "user.email=gate@example.invalid", "-c", "user.name=gate",
         "commit", "-qm", "base"],
    ):
        subprocess.run(["git", "-C", str(directory)] + argv,
                       capture_output=True, check=True)
    # the gate's in-place refresh, reproduced: the WORKING TREE loses an issue
    target.write_text(json.dumps(payload(working)), encoding="utf-8")
    return directory


good = build(scratch / "committed-good", [4, 645], [645])
bad = build(scratch / "committed-bad", [645], [645, 4])

on_disk = json.loads((good / ".board" / "snapshot.json").read_text(encoding="utf-8"))
on_disk_numbers = {issue["number"] for issue in on_disk["issues"]}
ok_good, why_good = crossref.resolve_target(good, "issue-4")
ok_bad, why_bad = crossref.resolve_target(bad, "issue-4")

print("PROVOKED_WORKING_TREE_LOST_4=%d" % (0 if 4 in on_disk_numbers else 1))
print("FIX_WORKTREE_LOSS_IGNORED=%d" % (1 if ok_good else 0))
print("TRUTH_COMMITTED_LOSS_REFUSED=%d" % (1 if not ok_bad else 0))
print("WHY_GOOD=%s" % why_good)
print("WHY_BAD=%s" % why_bad)
PY
)"
if [ $? -ne 0 ]; then
  printf '%s\n' "$scratch_out" | sed 's/^/    /' >&2
  echo "check-cross-reference: CANNOT-ASSESS — could not provoke the gate-scratch control" >&2
  exit 2
fi
unproven=""
for flag in PROVOKED_WORKING_TREE_LOST_4 FIX_WORKTREE_LOSS_IGNORED TRUTH_COMMITTED_LOSS_REFUSED; do
  printf '%s\n' "$scratch_out" | grep -q "^${flag}=1$" || unproven="$unproven $flag"
done
if [ -n "$unproven" ]; then
  printf '%s\n' "$scratch_out" | sed 's/^/    /' >&2
  echo "check-cross-reference: FAIL — the gate-scratch control did not hold:$unproven" >&2
  exit 1
fi
echo "  OK    gate-scratch control: a working-tree refresh that loses a referenced issue is ignored, and a committed loss is still refused"

echo "check-cross-reference: OK — relationships valid, markers resolved, builds deterministic, negative controls refused"
exit 0
