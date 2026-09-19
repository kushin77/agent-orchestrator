#!/usr/bin/env bash
# check-lane-record.sh -- the lane brief and the lane result are ONE record
# (issue #1270, EPIC #1268; auto-discovered into scripts/verify.sh by filename).
#
# THE RULE
#   A lane is given a brief and returns a result, and both are the SAME shape for
#   every runtime. The brief carries the files it owns, the gates it must run and
#   the worktree it was assigned; the result carries the commit, the files it
#   touched, the tail of every gate, whether it is mergeable, the squash exit code
#   and the worktree it actually ran in. Nothing is prose any more, so the merge
#   loop reads the record. Measured on 2026-09-18: a result touching a file
#   outside its scope, and a lane working in a shared checkout it was not
#   assigned, were both sentences in a hand-back -- and both are refusals here.
#
# WHY THIS GATE PROVES ITSELF ON EVERY RUN
#   A check that cannot fail is a formality (GR-12), so this gate drives the real
#   CLI -- the same `evaluate` the live arm runs, never a copy of its logic --
#   over a scratch tree it builds and then MUTATES, and it FAILS if any arm
#   behaves as anything other than the refusal it expects:
#
#      1 registry      the runtime set is DERIVED: alpha and beta ride the
#                      gateway, gamma is a role and owes nothing   -> rc 0
#      2 declaration   the declaration mirrors the code and names the gate that
#                      arms every refusal                           -> rc 0, then
#                      drift / unknown / undeclared / unarmed / unreadable -> rc 1
#      3 clean pair    a brief and its matching result               -> rc 0
#      4 shape         a runtime writes an unallowed kind, an old version, or
#                      omits a field                               -> rc 1 by name
#      5 tree          a stray file, a name that disagrees with its record, a
#                      file that is not JSON, a file that cannot be read -> rc 1
#      6 vocabulary    a runtime the registry does not carry          -> rc 1
#      7 pair          the result's runtime is not the brief's; it ran elsewhere
#                      than assigned; it ran in the shared checkout; it touched a
#                      file outside the owned set                  -> rc 1 by name
#      8 gates         a scoped gate with no tail; a tail for an unscoped gate;
#                      an empty tail; a brief demanding an unallowed result
#                      field; a result omitting one its brief demanded; mergeable
#                      with a red squash; a bad sha; a zoneless stamp -> rc 1
#      9 precision     the clean pair is NOT refused, a brief with no result is a
#                      lane in flight, and a brief is not refused for lacking the
#                      RESULT's fields -- so the rule does not match everything
#     10 mutant        `pair_problems`' containment is widened on a COPY of the
#                      module (its own file only) and the SAME out-of-scope
#                      control must then be ADMITTED, while another rule still
#                      fires -- so the refusal comes from the rule under test
#     11 cannot-assess a records tree that was NAMED and cannot be read -> rc 2,
#                      never 0
#     12 live tree     this checkout and its own fleet state         -> reported
#     13 suite         governance/lane-record/tests                  -> rc 0
#
# EXIT CONTRACT (guardrails/honesty tri-state)
#   0 OK / 1 NOT-OK / 2 CANNOT-ASSESS -- a refusal is never collapsed to a pass,
#   and CANNOT-ASSESS is reported as CANNOT-ASSESS, never as OK.
#
# Usage: bash scripts/check-lane-record.sh
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)" || exit 2
cd "$root" || exit 2

cli="governance/lane-record/cli.py"
suite="governance/lane-record/tests"

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-lane-record: CANNOT-ASSESS -- python3 not found" >&2
  exit 2
fi
if ! python3 -c 'import yaml' >/dev/null 2>&1; then
  echo "check-lane-record: CANNOT-ASSESS -- PyYAML is not installed (the runtime registry it reads is YAML)" >&2
  exit 2
fi
for required in "$cli" governance/lane-record/lane_record.py governance/lane-record/controls.py \
                governance/lane-record/controls.yaml governance/lane-record/schema/lane-record.schema.json \
                governance/notices/runtime_registry.py registry/packs/releases \
                gateway/catalog/modules fleet/channel.py "$suite"; do
  if [ ! -e "$required" ]; then
    echo "check-lane-record: CANNOT-ASSESS -- $required is missing" >&2
    exit 2
  fi
done

# One global scratch with one EXIT trap, on an explicit template (the docs marker
# rule reads a bare run of three capital X, so the scratch is built from a name
# and a clock instead).
scratch="/tmp/ao1270-lane-record.$(date +%s%N).$$"
if ! mkdir "$scratch" 2>/dev/null; then
  echo "check-lane-record: CANNOT-ASSESS -- cannot create the scratch directory $scratch" >&2
  exit 2
fi
trap 'rm -rf "$scratch"' EXIT

fail=0
out=""
rc=0

run() { # run <argv...> -- sets $out and $rc; drives the REAL cli
  out="$(env PYTHONDONTWRITEBYTECODE=1 python3 "$cli" "$@" 2>&1)"
  rc=$?
}

contains() { # contains <haystack> <needle> -- bash-native, so it cannot kill its producer
  # A pipe into a quiet grep exits on its first match and SIGPIPEs the producer
  # while it is still writing, which with pipefail promotes 141 to the pipeline's
  # status and fails OPEN. This gate compares text, so it uses the native test.
  case "$1" in
    *"$2"*) return 0 ;;
    *) return 1 ;;
  esac
}

expect() { # expect <arm> <wanted-rc> <needle>
  if [ "$rc" -eq "$2" ] && contains "$out" "$3"; then
    printf '  OK    %-24s rc=%s  %s\n' "$1" "$rc" "$3"
  else
    printf '  FAIL  %-24s rc=%s (wanted %s) and the output does not name %s\n' \
      "$1" "$rc" "$2" "$3" >&2
    printf '%s\n' "$out" | sed 's/^/        /' >&2
    fail=$((fail + 1))
  fi
}

expect_not_named() { # expect_not_named <arm> <needle>
  if contains "$out" "$2"; then
    printf '  FAIL  %-24s named %s, which this stage had already satisfied\n' "$1" "$2" >&2
    printf '%s\n' "$out" | sed 's/^/        /' >&2
    fail=$((fail + 1))
  else
    printf '  OK    %-24s rc=%s and it does not name %s\n' "$1" "$rc" "$2"
  fi
}

# --- the scratch tree: a live pack bundling three identities, two carried ------
tree="$scratch/tree"
mkdir -p "$tree/registry/packs/releases" "$tree/gateway/catalog/modules" "$tree/fleet"
worktrees="$scratch/worktrees"
mkdir -p "$worktrees/alpha" "$worktrees/beta"

python3 - "$tree" <<'PY'
"""A live pack bundling alpha, beta and gamma; the gateway carries two of them."""
import json
import pathlib
import sys

tree = pathlib.Path(sys.argv[1])
(tree / "registry/packs/releases/fixture-pack.1.0.0.yaml").write_text(
    "schema: agent-pack/v1\n"
    "id: fixture-pack\n"
    "version: 1.0.0\n"
    "lifecycle: live\n"
    "contents:\n"
    "  profile:\n"
    "  - ref: alpha@1.0.0\n"
    "  - ref: beta@1.0.0\n"
    "  - ref: gamma@1.0.0\n",
    encoding="utf-8",
)
for identity in ("alpha", "beta"):
    module = tree / "gateway/catalog/modules" / identity
    module.mkdir(parents=True, exist_ok=True)
    (module / "module.json").write_text(
        json.dumps({
            "id": identity,
            "class": ["model-gateway", "provider", identity],
            "distribution": {"package": "gateway.providers.%s" % identity},
        }),
        encoding="utf-8",
    )
(tree / "fleet/channel.py").write_text(
    "# the mailbox: the transport of record (ADR-0011)\n", encoding="utf-8"
)
PY

# --- the records builder: one variant per arm ---------------------------------
cat > "$scratch/mk.py" <<'PY'
"""Build exactly one variant of a lane-record pair, so each arm mutates ONE thing."""
import json
import pathlib
import shutil
import sys

records = pathlib.Path(sys.argv[1])
issue, lane, worktree, variant = int(sys.argv[2]), sys.argv[3], sys.argv[4], sys.argv[5]
installed = pathlib.Path(sys.argv[6]) if len(sys.argv) > 6 else None

OWNED = ["governance/alpha/a.py", "governance/alpha/b.py"]
SCOPED = ["verify", "alpha-suite"]
SHA = "0123456789abcdef0123456789abcdef01234567"


def brief(**over):
    document = {
        "schema": "lane-record/v1",
        "kind": "brief",
        "issue": issue,
        "lane": lane,
        "runtime": "alpha",
        "ts": "2026-09-18T20:00:00Z",
        "owned_files": list(OWNED),
        "scoped_gates": list(SCOPED),
        "forbidden_verbs": ["gh pr merge"],
        "assigned_worktree": worktree,
        "report_shape": {"fields": ["sha", "worktree"]},
    }
    document.update(over)
    return document


def result(**over):
    document = {
        "schema": "lane-record/v1",
        "kind": "result",
        "issue": issue,
        "lane": lane,
        "runtime": "alpha",
        "ts": "2026-09-18T20:30:00Z",
        "sha": SHA,
        "files_touched": list(OWNED),
        "gate_tails": {"verify": "VERIFY-RC=0", "alpha-suite": "3 passed"},
        "mergeable": True,
        "squash_rc": 0,
        "worktree": worktree,
    }
    document.update(over)
    return document


def pair_for(variant):
    if variant == "clean":
        return brief(), result()
    if variant == "unreadable":
        return brief(), result()
    if variant == "brief-only":
        return brief(), None
    if variant == "result-only":
        return None, result()
    if variant == "kind-unknown":
        return brief(kind="handback"), None
    if variant == "schema-version":
        return brief(schema="lane-record/v2"), None
    if variant == "missing-field":
        document = brief()
        del document["owned_files"]
        return document, None
    if variant == "half-field":
        # The RESULT half's field on a BRIEF: the halves must be told apart.
        return brief(files_touched=list(OWNED)), None
    if variant == "runtime-unregistered":
        return brief(runtime="gamma"), None
    if variant == "runtime-mismatch":
        return brief(), result(runtime="beta")
    if variant == "path-invalid":
        return brief(owned_files=["/etc/passwd"]), None
    if variant == "worktree-mismatch":
        return brief(), result(worktree="/tmp/ao1270-elsewhere")
    if variant == "worktree-shared":
        return brief(), result(worktree=str(installed))
    if variant == "file-outside-scope":
        return brief(), result(files_touched=list(OWNED) + ["docs/not-ours.md"])
    if variant == "tail-missing":
        return brief(), result(gate_tails={"verify": "VERIFY-RC=0"})
    if variant == "tail-unscoped":
        tails = {"verify": "VERIFY-RC=0", "alpha-suite": "3 passed", "invented": "ok"}
        return brief(), result(gate_tails=tails)
    if variant == "tail-empty":
        tails = {"verify": "VERIFY-RC=0", "alpha-suite": "   "}
        return brief(), result(gate_tails=tails)
    if variant == "shape-unallowed":
        return brief(report_shape={"fields": ["sha", "stdout"]}), None
    if variant == "field-missing":
        return brief(report_shape={"fields": ["sha", "refs"]}), result()
    if variant == "squash-not-green":
        return brief(), result(squash_rc=1)
    if variant == "sha-malformed":
        return brief(), result(sha="HEAD")
    if variant == "ts-malformed":
        return brief(ts="2026-09-18 20:00"), None
    if variant == "filename-mismatch":
        return brief(issue=issue + 1), None
    raise SystemExit("mk.py: unknown variant %r" % variant)


if records.exists():
    shutil.rmtree(records)
records.mkdir(parents=True)
if variant == "naming":
    (records / "loose.json").write_text("{}", encoding="utf-8")
    raise SystemExit(0)

if variant == "not-json":
    (records / str(issue)).mkdir(parents=True)
    (records / str(issue) / ("%s.brief.json" % lane)).write_text("{not json", encoding="utf-8")
    raise SystemExit(0)

head, tail = pair_for(variant)
directory = records / str(issue)
directory.mkdir(parents=True)
if head is not None:
    (directory / ("%s.brief.json" % lane)).write_text(json.dumps(head, indent=1), encoding="utf-8")
if tail is not None:
    (directory / ("%s.result.json" % lane)).write_text(json.dumps(tail, indent=1), encoding="utf-8")
if variant == "unreadable":
    (directory / ("%s.brief.json" % lane)).chmod(0)
PY

build() { # build <variant> -- rebuilds the records tree for one arm
  records="$scratch/records"
  python3 "$scratch/mk.py" "$records" 4242 alpha "$worktrees/alpha" "$1" "$tree" || exit 2
}

echo "== 1. the runtime set is DERIVED: alpha and beta ride the gateway, gamma is a role =="
run --root "$tree" runtimes
expect "registry-derived" 0 "runtimes=2 -- alpha, beta"
expect_not_named "registry-role-owed-nothing" "gamma"

echo "== 2. the declaration against the code, and against this gate =="
run --root "$root" controls
expect "controls-armed" 0 "lane-record-controls: version=lane-record/v1 kinds=2"
expect "every-declared-refusal-is-armed" 0 "findings=0"
controls_dir="$scratch/controls"
mkdir -p "$controls_dir"
python3 - "$root/governance/lane-record/controls.yaml" "$controls_dir" <<'PY'
import pathlib
import sys

import yaml

source = pathlib.Path(sys.argv[1])
out = pathlib.Path(sys.argv[2])
document = yaml.safe_load(source.read_text(encoding="utf-8"))

drift = dict(document)
drift["version"] = "lane-record/v9"
(out / "drift.yaml").write_text(yaml.safe_dump(drift, sort_keys=False), encoding="utf-8")

unknown = dict(document)
unknown["refusals"] = list(document["refusals"]) + [{
    "id": "no-such-refusal",
    "rule": "declared while the code never reports it",
    "names_in_the_finding": "nothing",
    "provoked_by": "scripts/check-lane-record.sh",
}]
(out / "unknown.yaml").write_text(yaml.safe_dump(unknown, sort_keys=False), encoding="utf-8")

undeclared = dict(document)
undeclared["refusals"] = [
    entry for entry in document["refusals"] if entry["id"] != "lane-worktree-mismatch"
]
(out / "undeclared.yaml").write_text(yaml.safe_dump(undeclared, sort_keys=False), encoding="utf-8")

unarmed = dict(document)
refusals = [dict(entry) for entry in document["refusals"]]
refusals[0]["provoked_by"] = "scripts/check-shell-syntax.sh"
unarmed["refusals"] = refusals
(out / "unarmed.yaml").write_text(yaml.safe_dump(unarmed, sort_keys=False), encoding="utf-8")
PY
run --root "$root" controls --controls "$controls_dir/drift.yaml"
expect "controls-mirror-drift" 1 "controls-mirror-drift:version"
run --root "$root" controls --controls "$controls_dir/unknown.yaml"
expect "refusal-unknown" 1 "refusal-unknown:no-such-refusal"
run --root "$root" controls --controls "$controls_dir/undeclared.yaml"
expect "refusal-undeclared" 1 "refusal-undeclared:lane-worktree-mismatch"
run --root "$root" controls --controls "$controls_dir/unarmed.yaml"
expect "refusal-not-provoked" 1 "refusal-not-provoked:lane-record-set-unreadable"
run --root "$root" controls --controls "$controls_dir/no-such-file.yaml"
expect "controls-unreadable" 2 "CANNOT-ASSESS"

echo "== 3. a brief and its matching result: the same shape, and green =="
build clean
run --root "$tree" evaluate --records "$scratch/records"
expect "clean-pair" 0 "lane-record: records=2 briefs=1 results=1 pairs=1 runtimes=2"

echo "== 4. the shape: an unallowed kind, an old version, a missing field =="
build kind-unknown
run --root "$tree" evaluate --records "$scratch/records"
expect "kind-unknown" 1 "lane-record-kind-unknown:'handback'"
build schema-version
run --root "$tree" evaluate --records "$scratch/records"
expect "schema-version" 1 "lane-record-schema-version:'lane-record/v2'"
build missing-field
run --root "$tree" evaluate --records "$scratch/records"
expect "missing-field" 1 "lane-record-malformed:/owned_files"
mut_schema="$scratch/lane-record.drift.json"
python3 - "$root/governance/lane-record/schema/lane-record.schema.json" "$mut_schema" <<'PY'
import json
import pathlib
import sys

document = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
document["$defs"]["record"]["properties"]["kind"]["enum"] = ["brief", "result", "handback"]
pathlib.Path(sys.argv[2]).write_text(json.dumps(document, indent=1), encoding="utf-8")
PY
run --root "$tree" --schema "$mut_schema" evaluate --records "$scratch/records"
expect "schema-mirror-drift" 1 'lane-schema-mirror-drift:$defs.record.properties.kind.enum'

echo "== 5. the tree: a stray file, a disagreeing name, JSON that is not, a file that cannot be read =="
build naming
run --root "$tree" evaluate --records "$scratch/records"
expect "naming" 1 "lane-record-naming:loose.json"
build filename-mismatch
run --root "$tree" evaluate --records "$scratch/records"
expect "filename-mismatch" 1 "lane-record-filename-mismatch:4242/alpha.brief.json"
build not-json
run --root "$tree" evaluate --records "$scratch/records"
expect "not-json" 1 "lane-record-not-json:4242/alpha.brief.json"
build unreadable
run --root "$tree" evaluate --records "$scratch/records"
expect "unreadable-record" 1 "lane-record-unreadable:4242/alpha.brief.json"
chmod 644 "$scratch/records/4242/alpha.brief.json" 2>/dev/null || true

echo "== 6. the vocabulary: a runtime the registry does not carry =="
build runtime-unregistered
run --root "$tree" evaluate --records "$scratch/records"
expect "runtime-unregistered" 1 "lane-runtime-unregistered:gamma"

echo "== 7. the pair: another runtime, another worktree, the shared checkout, a wider scope =="
build runtime-mismatch
run --root "$tree" evaluate --records "$scratch/records"
expect "runtime-mismatch" 1 "lane-runtime-mismatch:4242/alpha"
build worktree-mismatch
run --root "$tree" evaluate --records "$scratch/records"
expect "worktree-mismatch" 1 "lane-worktree-mismatch:/tmp/ao1270-elsewhere"
build worktree-shared
run --root "$tree" evaluate --records "$scratch/records"
expect "worktree-shared" 1 "lane-worktree-shared:$tree"
build file-outside-scope
run --root "$tree" evaluate --records "$scratch/records"
expect "file-outside-scope" 1 "lane-file-outside-scope:docs/not-ours.md"
build path-invalid
run --root "$tree" evaluate --records "$scratch/records"
expect "path-invalid" 1 "lane-file-path-invalid:/etc/passwd"

echo "== 8. the gates, the report shape and the merge claim =="
build tail-missing
run --root "$tree" evaluate --records "$scratch/records"
expect "gate-tail-missing" 1 "lane-gate-tail-missing:alpha-suite"
build tail-unscoped
run --root "$tree" evaluate --records "$scratch/records"
expect "gate-tail-unscoped" 1 "lane-gate-tail-unscoped:invented"
build tail-empty
run --root "$tree" evaluate --records "$scratch/records"
expect "gate-tail-empty" 1 "lane-gate-tail-empty:alpha-suite"
build shape-unallowed
run --root "$tree" evaluate --records "$scratch/records"
expect "report-shape-unallowed" 1 "lane-report-shape-unallowed:stdout"
build field-missing
run --root "$tree" evaluate --records "$scratch/records"
expect "report-field-missing" 1 "lane-report-field-missing:refs"
build squash-not-green
run --root "$tree" evaluate --records "$scratch/records"
expect "squash-not-green" 1 "lane-squash-not-green:1"
build sha-malformed
run --root "$tree" evaluate --records "$scratch/records"
expect "sha-malformed" 1 "lane-sha-malformed:HEAD"
build ts-malformed
run --root "$tree" evaluate --records "$scratch/records"
expect "timestamp-malformed" 1 "lane-timestamp-malformed:2026-09-18 20:00"

echo "== 9. precision: the rule does not match everything =="
build result-only
run --root "$tree" evaluate --records "$scratch/records"
expect "result-without-brief" 1 "lane-result-no-brief:4242/alpha"
build brief-only
run --root "$tree" evaluate --records "$scratch/records"
expect "brief-without-result" 0 "lane-record: records=1 briefs=1 results=0 pairs=0"
expect_not_named "in-flight-not-refused" "lane-result-no-brief"
build half-field
run --root "$tree" evaluate --records "$scratch/records"
expect "halves-told-apart" 1 "lane-record-malformed:/files_touched"
build clean
run --root "$tree" evaluate --records "$scratch/records"
expect "clean-again" 0 "lane-record: records=2 briefs=1 results=1 pairs=1"
expect_not_named "clean-names-nothing" "lane-file-outside-scope"
expect_not_named "clean-names-no-squash-red" "lane-squash-not-green"

echo "== 10. the mutant: widen the containment on a copy, and the control must be ADMITTED =="
mutant="$scratch/mutant/lane-record"
mkdir -p "$mutant"
cp -R governance/lane-record/. "$mutant/"
before="$(grep -c 'for item in brief.get("owned_files")' "$mutant/lane_record.py")"
sed -i 's/for item in brief.get("owned_files")/for item in result.get("files_touched")/' \
  "$mutant/lane_record.py"
after="$(grep -c 'for item in result.get("files_touched")' "$mutant/lane_record.py")"
if [ "$before" -ne 1 ] || [ "$after" -ne 1 ]; then
  printf '  FAIL  %-24s the mutation did not apply (before=%s after=%s), so the arm would test nothing\n' \
    "mutant-applied" "$before" "$after" >&2
  fail=$((fail + 1))
else
  printf '  OK    %-24s the containment rule was widened on a copy of the module\n' "mutant-applied"
  build file-outside-scope
  out="$(env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$root" python3 "$mutant/cli.py" \
    --root "$tree" evaluate --records "$scratch/records" 2>&1)"
  rc=$?
  expect "mutant-admits-control" 0 "lane-record: records=2 briefs=1 results=1 pairs=1"
  expect_not_named "mutant-admits-the-out-of-scope-file" "lane-file-outside-scope"
  build squash-not-green
  out="$(env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$root" python3 "$mutant/cli.py" \
    --root "$tree" evaluate --records "$scratch/records" 2>&1)"
  rc=$?
  expect "mutant-still-refuses-another-rule" 1 "lane-squash-not-green:1"
fi

echo "== 11. a records tree that was NAMED and cannot be read is CANNOT-ASSESS, never a pass =="
run --root "$tree" evaluate --records "$scratch/no-such-records"
expect "records-unreadable" 2 "CANNOT-ASSESS"
expect_not_named "records-unreadable-not-ok" "lane-record: records=0"

echo "== 12. this checkout, its own fleet state: read-only, and reported with its counts =="
run --root "$root" evaluate
expect "live-tree" 0 "records="
printf '%s\n' "$out" | sed 's/^/        /'

echo "== 13. the suite this gate names =="
if env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q "$suite" \
    >"$scratch/suite.log" 2>&1; then
  printf '  OK    %-24s %s\n' "suite" "$(tail -1 "$scratch/suite.log")"
else
  printf '  FAIL  %-24s %s\n' "suite" "$(tail -1 "$scratch/suite.log")" >&2
  sed 's/^/        /' "$scratch/suite.log" >&2
  fail=$((fail + 1))
fi

if [ "$fail" -gt 0 ]; then
  echo "check-lane-record: FAIL ($fail violation(s))" >&2
  exit 1
fi
echo "check-lane-record: OK -- a brief and its result are one record for every runtime, and every refusal above was provoked by name"
exit 0
