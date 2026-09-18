#!/usr/bin/env bash
# check-notice-acks.sh -- a standing notice is acked by every REGISTERED runtime
# (issue #1269, EPIC #1268; auto-discovered into scripts/verify.sh by filename).
#
# THE RULE
#   A rule between runtimes is a record, and it is only a rule when every
#   registered runtime has acknowledged it. On 2026-09-18 the merge-path rules
#   went out both ways -- a SendMessage broadcast to the Claude sessions, a
#   hand-built mailbox directive to the DeepSeek sister -- and ZERO acks were
#   recorded: nothing in the tree could tell "sent" from "received".
#
# WHAT MAKES IT MECHANICAL
#   The set of runtimes a notice must reach is DERIVED, never typed (see
#   governance/notices/runtime_registry.py): a runtime is an identity bundled by
#   a LIVE AgentPack (registry/packs/releases/*.yaml) that the gateway catalog
#   (gateway/catalog/modules/*/module.json) carries a transport for. Register a
#   runtime and every standing notice requires its ack, with no notice edited. A
#   notice that names its ackers explicitly is REFUSED by name
#   (`hand-maintained-ack-list`), which is the arm that proves the requirement
#   cannot be hand-maintained.
#
# WHY THIS GATE PROVES ITSELF ON EVERY RUN
#   A check that cannot fail is a formality (GR-12), so this gate drives the real
#   CLI -- the same `evaluate` the live arm runs, never a copy of its logic --
#   over a scratch tree it builds and then MUTATES, and it FAILS if any arm
#   behaves as anything other than the refusal it expects:
#
#     1 fixture-planted      a notice nobody has acked       -> rc 1, BOTH named
#     2 fixture-one-silent   one runtime silent, and an ack with no evidence
#                            refused                                  -> rc 1 naming ONLY it
#     3 fixture-clean        the same notice fully acked      -> rc 0, runtimes=2 roles=1
#     4 fixture-unfanned     the copy for one was removed     -> rc 1, by name
#     5 fixture-ghost-ack    an ack from an unregistered id   -> rc 1, by name
#     6 fixture-hand-list    requires_ack names ids           -> rc 1, by name
#     7 ledger-clean/tampered  the chain, and a record edited after the fact
#                                                             -> rc 0, then rc 1 by name
#     8 ledger-broken        a bogus link, a line that is not JSON -> rc 1, by name
#     9 controls             the vocabulary mirrors the code and every refusal is
#                            armed in the script it names
#                                                             -> rc 0, then rc 1 by name
#                            (drifted vocabulary, unknown id, undeclared id, disarmed)
#    10 fixture-empty        no live pack registers anything  -> rc 1 (never 0)
#    11 fixture-unreadable   the registry is removed          -> rc 2 (never 0)
#    12 live-planted/acked   the REAL registry: nothing acked, then all acked
#                                                             -> rc 1 naming all five, then rc 0
#    13 live-tree            this checkout, its live .fleet, its declaration -> rc 0
#    14 suite                governance/notices/tests          -> rc 0
#
#   The fixture's live pack bundles three identities and its catalog carries
#   two, so `runtimes=2 roles=1` in arm 3 is the intersection being measured: a
#   role that rides no transport owes no ack.
#
# EXIT CONTRACT (guardrails/honesty tri-state)
#   0 OK / 1 NOT-OK / 2 CANNOT-ASSESS -- a refusal is never collapsed to a pass,
#   and CANNOT-ASSESS is reported as CANNOT-ASSESS, never as OK.
#
# Usage: bash scripts/check-notice-acks.sh
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)" || exit 2
cd "$root" || exit 2

cli="governance/notices/cli.py"
suite="governance/notices/tests"

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-notice-acks: CANNOT-ASSESS -- python3 not found" >&2
  exit 2
fi
if ! python3 -c 'import yaml' >/dev/null 2>&1; then
  echo "check-notice-acks: CANNOT-ASSESS -- PyYAML is not installed (the pack registry it reads is YAML)" >&2
  exit 2
fi
for required in "$cli" governance/notices/runtime_registry.py governance/notices/notice_records.py registry/packs/releases gateway/catalog/modules fleet/channel.py "$suite"; do
  if [ ! -e "$required" ]; then
    echo "check-notice-acks: CANNOT-ASSESS -- $required is missing" >&2
    exit 2
  fi
done

# One global scratch with one EXIT trap, named without a trailing run of X so the
# docs marker rule and the scratch are not confused (the sibling gates' idiom).
scratch="/tmp/ao-notice-acks.$(date +%s%N).$$"
if ! mkdir "$scratch" 2>/dev/null; then
  echo "check-notice-acks: CANNOT-ASSESS -- cannot create the scratch directory $scratch" >&2
  exit 2
fi
trap 'rm -rf "$scratch"' EXIT

fail=0
out=""
eval_rc=0

run_eval() { # run_eval <root> <fleet> -- sets $out and $eval_rc
  out="$(env PYTHONDONTWRITEBYTECODE=1 python3 "$cli" --root "$1" --fleet "$2" evaluate 2>&1)"
  eval_rc=$?
}

run_cli() { # run_cli <root> <fleet> <verb> [args...] -- sets $out and $eval_rc
  local r="$1" f="$2" v="$3"
  shift 3
  out="$(env PYTHONDONTWRITEBYTECODE=1 python3 "$cli" --root "$r" --fleet "$f" "$v" "$@" 2>&1)"
  eval_rc=$?
}

contains() { # contains <haystack> <needle> -- bash-native, so it cannot kill its producer
  # A pipe into a quiet grep (`... | grep -qF`) exits on its first match and
  # SIGPIPEs the producer while it is still writing, which with pipefail promotes
  # 141 to the pipeline's status and fails OPEN (scripts/check-verdict-contains.sh).
  # This gate compares text, so it uses the native test that check itself uses.
  case "$1" in
    *"$2"*) return 0 ;;
    *) return 1 ;;
  esac
}

expect() { # expect <arm> <wanted-rc> <needle>
  if [ "$eval_rc" -eq "$2" ] && contains "$out" "$3"; then
    printf '  OK    %-20s rc=%s  %s\n' "$1" "$eval_rc" "$3"
  else
    printf '  FAIL  %-20s rc=%s (wanted %s) and the output does not name %s\n' \
      "$1" "$eval_rc" "$2" "$3" >&2
    printf '%s\n' "$out" | sed 's/^/        /' >&2
    fail=$((fail + 1))
  fi
}

expect_not_named() { # expect_not_named <arm> <needle>
  if contains "$out" "$2"; then
    printf '  FAIL  %-20s named %s, which this stage had already satisfied\n' "$1" "$2" >&2
    printf '%s\n' "$out" | sed 's/^/        /' >&2
    fail=$((fail + 1))
  else
    printf '  OK    %-20s rc=%s and it does not name %s\n' "$1" "$eval_rc" "$2"
  fi
}

# --- the scratch tree: a two-release registry, one of them a role -------------
fixture_tree="$scratch/tree"
fixture_fleet="$scratch/fleet"
mkdir -p "$fixture_tree/registry/packs/releases" "$fixture_tree/gateway/catalog/modules" \
  "$fixture_tree/fleet" "$fixture_fleet"

python3 - "$fixture_tree" <<'PY'
"""Build the scratch registry: alpha and beta ride the gateway, gamma does not."""
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

notice="notice-fixture"
run_cli "$fixture_tree" "$fixture_fleet" publish \
  --id "$notice" \
  --subject "the merge path requires green evidence at the verified head" \
  --body "Never merge work that fails its gate." \
  --ref "kushin77/agent-orchestrator#1269"
if [ "$eval_rc" -eq 0 ] && contains "$out" "fanout gamma"; then
  printf '  FAIL  %-20s fanned the notice out to a role\n' "publish" >&2
  fail=$((fail + 1))
elif [ "$eval_rc" -eq 0 ] && contains "$out" "published $notice to 2 registered runtime(s)"; then
  printf '  OK    %-20s published to the two runtimes the registry names\n' "publish"
else
  printf '  FAIL  %-20s rc=%s\n' "publish" "$eval_rc" >&2
  printf '%s\n' "$out" | sed 's/^/        /' >&2
  fail=$((fail + 1))
fi

echo "== 1. a planted notice: nobody acked it, so it is refused by name =="
run_eval "$fixture_tree" "$fixture_fleet"
expect "fixture-planted" 1 "notice-unacked:$notice:alpha"
expect "fixture-planted-beta" 1 "notice-unacked:$notice:beta"

echo "== 2. one runtime silent is refused, and the acked one is NOT named =="
run_cli "$fixture_tree" "$fixture_fleet" ack --notice "$notice" --runtime alpha \
  --evidence "   "
expect "ack-without-evidence" 1 "ack-without-evidence:$notice:alpha"
run_cli "$fixture_tree" "$fixture_fleet" ack --notice "$notice" --runtime alpha \
  --evidence "read the notice; the rule is understood"
expect "ack-alpha" 0 "acked $notice by alpha"
run_eval "$fixture_tree" "$fixture_fleet"
expect "fixture-one-silent" 1 "notice-unacked:$notice:beta"
expect_not_named "fixture-one-silent-precision" "notice-unacked:$notice:alpha"

echo "== 3. the clean state: every runtime acked, so the rule is satisfied =="
run_cli "$fixture_tree" "$fixture_fleet" ack --notice "$notice" --runtime beta \
  --evidence "read the notice; the rule is understood"
expect "ack-beta" 0 "acked $notice by beta"
run_eval "$fixture_tree" "$fixture_fleet"
expect "fixture-clean" 0 "runtimes=2 roles=1 notices=1"

echo "== 4. a runtime whose copy was never addressed owes one, by name =="
cp "$fixture_fleet/notices/$notice/pending/beta.json" "$scratch/pending-beta.json"
rm -f "$fixture_fleet/notices/$notice/pending/beta.json"
run_eval "$fixture_tree" "$fixture_fleet"
expect "fixture-unfanned" 1 "notice-not-fanned-out:$notice:beta"
cp "$scratch/pending-beta.json" "$fixture_fleet/notices/$notice/pending/beta.json"

echo "== 5. an ack from a runtime the fleet does not hold proves nothing =="
python3 - "$fixture_fleet/notices/$notice/ack-ghost.json" "$notice" <<'PY'
import json
import pathlib
import sys

pathlib.Path(sys.argv[1]).write_text(
    json.dumps({
        "schema": "notice-ack/v1",
        "notice": sys.argv[2],
        "runtime": "ghost",
        "acked_at": "2026-09-18T20:30:00Z",
        "transport": "mailbox:fleet/channel.py",
        "evidence": "a runtime the registry does not carry",
    }),
    encoding="utf-8",
)
PY
run_eval "$fixture_tree" "$fixture_fleet"
expect "fixture-ghost-ack" 1 "ack-from-unregistered-runtime:$notice:ghost"
rm -f "$fixture_fleet/notices/$notice/ack-ghost.json"

echo "== 6. a notice that names its ackers is refused: the requirement is the registry =="
notice_record="$fixture_fleet/notices/$notice/notice.json"
cp "$notice_record" "$scratch/notice.json"
sha_before="$(sha256sum "$notice_record" | cut -d' ' -f1)"
python3 - "$notice_record" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
record = json.loads(path.read_text(encoding="utf-8"))
record["requires_ack"] = ["alpha"]
path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
sha_after="$(sha256sum "$notice_record" | cut -d' ' -f1)"
if [ "$sha_before" = "$sha_after" ]; then
  printf '  FAIL  %-20s the mutation did not change the record (a no-op mutant proves nothing)\n' \
    "fixture-hand-list" >&2
  fail=$((fail + 1))
fi
run_eval "$fixture_tree" "$fixture_fleet"
expect "fixture-hand-list" 1 "hand-maintained-ack-list:$notice"
cp "$scratch/notice.json" "$notice_record"

echo "== 7. the ledger: chained, and a record edited after the fact is named =="
ledger_file="$fixture_fleet/notices/ledger.jsonl"
run_cli "$fixture_tree" "$fixture_fleet" ledger --verify
expect "ledger-clean" 0 "chains and every record matches its digest"
cp "$fixture_fleet/notices/$notice/ack-beta.json" "$scratch/ack-beta.json"
printf ' ' >> "$fixture_fleet/notices/$notice/ack-beta.json"
run_cli "$fixture_tree" "$fixture_fleet" ledger --verify
expect "ledger-digest" 1 "ledger-digest-mismatch:$notice:notices/"
cp "$scratch/ack-beta.json" "$fixture_fleet/notices/$notice/ack-beta.json"
cp "$ledger_file" "$scratch/ledger.jsonl"

python3 - "$ledger_file" <<'PY'
"""Break the chain link on the first entry: a ledger edited after the fact."""
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
lines = path.read_text(encoding="utf-8").splitlines()
first = json.loads(lines[0])
first["prevHash"] = "f" * 64
lines[0] = json.dumps(first, sort_keys=True)
path.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
run_cli "$fixture_tree" "$fixture_fleet" ledger --verify
expect "ledger-chain-broken" 1 "ledger-chain-broken:seq=1"
cp "$scratch/ledger.jsonl" "$ledger_file"
printf 'this line is not JSON\n' >> "$ledger_file"
run_cli "$fixture_tree" "$fixture_fleet" ledger --verify
expect "ledger-malformed" 1 "ledger-malformed:"
cp "$scratch/ledger.jsonl" "$ledger_file"
run_cli "$fixture_tree" "$fixture_fleet" ledger --verify
expect "ledger-restored" 0 "chains and every record matches its digest"

echo "== 8. the declaration: mirrored by the code, and every refusal armed =="
controls_dir="$scratch/controls"
mkdir -p "$controls_dir"
run_cli "$root" "$scratch/controls-fleet" controls
expect "controls-clean" 0 "each armed"

python3 - "$root/governance/notices/controls.yaml" "$controls_dir" <<'PY'
"""Three mutants of the declaration: drifted vocabulary, unknown id, disarmed refusal."""
import pathlib
import sys

import yaml

source = pathlib.Path(sys.argv[1])
out = pathlib.Path(sys.argv[2])
document = yaml.safe_load(source.read_text(encoding="utf-8"))

names = ["selector-drift.yaml", "unknown-refusal.yaml", "undeclared-refusal.yaml", "disarmed.yaml"]

first = dict(document)
first["selector"] = "whoever-remembers"
(out / names[0]).write_text(yaml.safe_dump(first, sort_keys=False), encoding="utf-8")

second = dict(document)
second["refusals"] = list(second["refusals"]) + [{
    "id": "no-such-refusal",
    "rule": "declared while the code never reports it",
    "names_in_the_finding": "nothing",
    "provoked_by": "scripts/check-notice-acks.sh",
}]
(out / names[1]).write_text(yaml.safe_dump(second, sort_keys=False), encoding="utf-8")

third = dict(document)
third["refusals"] = [entry for entry in third["refusals"]
                     if entry["id"] != "ack-runtime-mismatch"]
(out / names[2]).write_text(yaml.safe_dump(third, sort_keys=False), encoding="utf-8")

fourth = dict(document)
refusals = list(fourth["refusals"])
refusals[0] = dict(refusals[0])
refusals[0]["provoked_by"] = "scripts/check-shell-syntax.sh"
fourth["refusals"] = refusals
(out / names[3]).write_text(yaml.safe_dump(fourth, sort_keys=False), encoding="utf-8")
PY
run_cli "$root" "$scratch/controls-fleet" controls --controls "$controls_dir/selector-drift.yaml"
expect "controls-mirror-drift" 1 "controls-mirror-drift:selector"
run_cli "$root" "$scratch/controls-fleet" controls --controls "$controls_dir/unknown-refusal.yaml"
expect "refusal-unknown" 1 "refusal-unknown:no-such-refusal"
run_cli "$root" "$scratch/controls-fleet" controls --controls "$controls_dir/undeclared-refusal.yaml"
expect "refusal-undeclared" 1 "refusal-undeclared:ack-runtime-mismatch"
run_cli "$root" "$scratch/controls-fleet" controls --controls "$controls_dir/disarmed.yaml"
expect "refusal-not-provoked" 1 "refusal-not-provoked:"

run_cli "$root" "$scratch/controls-fleet" controls --controls "$controls_dir/no-such-file.yaml"
expect "controls-unreadable" 2 "CANNOT-ASSESS"

echo "== 9. nothing registered is a refusal, never a pass =="
roles_only="$scratch/roles-only"
mkdir -p "$roles_only/registry/packs/releases" "$roles_only/gateway/catalog/modules" "$roles_only/fleet"
python3 - "$roles_only" <<'PY'
import json
import pathlib
import sys

tree = pathlib.Path(sys.argv[1])
(tree / "registry/packs/releases/paused-pack.1.0.0.yaml").write_text(
    "schema: agent-pack/v1\n"
    "id: paused-pack\n"
    "version: 1.0.0\n"
    "lifecycle: paused\n"
    "contents:\n"
    "  profile:\n"
    "  - ref: delta@1.0.0\n",
    encoding="utf-8",
)
module = tree / "gateway/catalog/modules/delta"
module.mkdir(parents=True, exist_ok=True)
(module / "module.json").write_text(
    json.dumps({
        "id": "delta",
        "class": ["model-gateway", "provider", "delta"],
        "distribution": {"package": "gateway.providers.delta"},
    }),
    encoding="utf-8",
)
(tree / "fleet/channel.py").write_text("# the mailbox\n", encoding="utf-8")
PY
run_eval "$roles_only" "$scratch/roles-fleet"
expect "fixture-empty" 1 "empty-runtime-registry"

echo "== 10. the registry removed is CANNOT-ASSESS, never an empty registry =="
unreadable_tree="$scratch/unreadable"
cp -R "$fixture_tree" "$unreadable_tree"
rm -rf "$unreadable_tree/registry/packs/releases"
run_eval "$unreadable_tree" "$fixture_fleet"
expect "fixture-unreadable" 2 "CANNOT-ASSESS"

echo "== 11-12. the REAL registry: nothing acked is refused, all acked is satisfied =="
live_fleet="$scratch/live-fleet"
live_notice="notice-live-planted"
run_cli "$root" "$live_fleet" publish \
  --id "$live_notice" \
  --subject "the merge path requires green evidence at the verified head" \
  --body "Never merge work that fails its gate." \
  --ref "kushin77/agent-orchestrator#1269"
expect "live-publish" 0 "published $live_notice"
unacked_lines=0
for runtime in claude deepseek hermes ollama paperclip; do
  if contains "$out" "fanout $runtime ->"; then
    unacked_lines=$((unacked_lines + 1))
  fi
done
if [ "$unacked_lines" -eq 5 ]; then
  printf '  OK    %-20s fanned out to all five registered runtimes\n' "live-publish-fanout"
else
  printf '  FAIL  %-20s fanned out to %s of the 5 registered runtimes\n' \
    "live-publish-fanout" "$unacked_lines" >&2
  printf '%s\n' "$out" | sed 's/^/        /' >&2
  fail=$((fail + 1))
fi
run_eval "$root" "$live_fleet"
expect "live-planted" 1 "notice-unacked:$live_notice:hermes"
for runtime in claude deepseek ollama paperclip; do
  expect "live-planted-$runtime" 1 "notice-unacked:$live_notice:$runtime"
done

for runtime in claude deepseek hermes ollama paperclip; do
  run_cli "$root" "$live_fleet" ack --notice "$live_notice" --runtime "$runtime" \
    --evidence "read the notice; the rule is understood"
  if [ "$eval_rc" -ne 0 ]; then
    printf '  FAIL  %-20s rc=%s for %s\n' "live-ack" "$eval_rc" "$runtime" >&2
    printf '%s\n' "$out" | sed 's/^/        /' >&2
    fail=$((fail + 1))
  fi
done
run_eval "$root" "$live_fleet"
expect "live-acked" 0 "notice-acks: OK"

echo "== 13. this checkout, its live .fleet and its declaration: read-only, and reported =="
run_cli "$root" "$scratch/live-tree-fleet" controls
expect "live-controls" 0 "each armed"
run_eval "$root" "$root/.fleet"
expect "live-tree" 0 "notice-acks: runtimes="
printf '%s\n' "$out" | sed 's/^/        /'

echo "== 14. the suite this gate names =="
if env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q "$suite" >"$scratch/suite.log" 2>&1; then
  printf '  OK    %-20s %s\n' "suite" "$(tail -1 "$scratch/suite.log")"
else
  printf '  FAIL  %-20s %s\n' "suite" "$(tail -1 "$scratch/suite.log")" >&2
  sed 's/^/        /' "$scratch/suite.log" >&2
  fail=$((fail + 1))
fi

if [ "$fail" -gt 0 ]; then
  echo "check-notice-acks: FAIL ($fail violation(s))" >&2
  exit 1
fi
echo "check-notice-acks: OK -- every registered runtime acked every standing notice, and each refusal above was provoked by name"
exit 0
