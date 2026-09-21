#!/usr/bin/env bash
# check-paperclip-diagrams.sh — the diagrams blueprint projection gate (issue #465).
#
# ADR-0017 (issue #463, EPIC #461) froze where a diagram signal lives on a
# ticket: it rides the ticket's structured `evidence[]` as the additive v2
# receipt `{kind, ref, result, checks}` — never a new `facets.diagrams`, never a
# new ticket `kind`. integrations/paperclip/diagrams.py is the read-only
# producer of that signal over the existing seam Transport. A shape nothing
# validates is a formality (GR-12), so this gate reads the blueprint offline
# through the fixture transport and fails, by name, when any rule regresses:
#
#   1. it projects the committed blueprint fixture and requires every emitted
#      receipt to be exactly the frozen v2 shape and to validate against the
#      ticket schema's `evidence_receipt` definition;
#   2. it asserts the projection introduces no `facets` key and no new ticket
#      `kind` — the closed sets stay closed (ADR-0014 / ADR-0017);
#   3. it proves the plan is deterministic: a byte-identical sha256 over two
#      runs, and the same sha256 after a negative-control run;
#   4. it provokes each refusal the acceptance names and requires the offender to
#      be named — an empty/missing fixture, a Finding with no resource id, a
#      Finding whose declared and live attributes are EQUAL (never drift), a
#      false-positive drift, a status outside the closed vocabulary, and a
#      `403`/`404`-class response;
#   5. it mutates the exact assertion anchor in a scratch copy of the adapter and
#      requires the mutation to be observable (the false-positive drift is then
#      NOT refused), proving the gate can fail — while the real adapter file is
#      left byte-identical.
#
# Offline by construction: only `FixtureTransport` is exercised; the live
# `HttpTransport` is never built here, so the gate never touches the network and
# pulls no third-party dependency.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
# CANNOT-ASSESS must never be reported as a pass.
#
# Usage: bash scripts/check-paperclip-diagrams.sh
#
# ---knowledge---
# module_id: scripts.check-paperclip-diagrams
# system: governance
# app: gates
# solution_class: enterprise
# patterns: [tri-state-exit, provoked-negative-control, no-false-green, offline-hermetic, named-refusal, deterministic, schema-validation]
# derives_from: null
# owner_sme: platform-sme
# tier: L1
# interfaces: [exit 0 OK, exit 1 NOT-OK, exit 2 CANNOT-ASSESS]
# invariants: ""
# gotchas: ""
# related: ["#461", "#463", "#465"]
# do_not_duplicate: null
# ---knowledge---
set -u

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 2

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-paperclip-diagrams: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

adapter="integrations/paperclip/diagrams.py"
clean="integrations/paperclip/tests/fixtures/diagrams.json"
if [ ! -f "$adapter" ]; then
  echo "check-paperclip-diagrams: FAIL — $adapter is missing" >&2
  exit 1
fi
if [ ! -f "$clean" ]; then
  echo "check-paperclip-diagrams: CANNOT-ASSESS — the fixture is missing ($clean)" >&2
  exit 2
fi

# Scratch: the sanctioned fleet idiom (a bare mktemp X-run trips the repo's own
# unfinished-marker scan, scripts/check-docs.sh).
scratch="/tmp/ao465-diagrams.$(date +%s%N).$$"
if ! mkdir "$scratch" 2>/dev/null; then
  echo "check-paperclip-diagrams: CANNOT-ASSESS — cannot create a scratch directory" >&2
  exit 2
fi
trap 'rm -rf "$scratch"' EXIT

# The provoked fixtures: one blueprint state per refusal this gate must witness.
python3 - "$scratch" <<'PY'
import json
import sys
from pathlib import Path

out = Path(sys.argv[1])
PATH = "/api/companies/acme/diagrams"


def write(name, body, *, status=200):
    doc = {"responses": [{"method": "GET", "path": PATH, "status": status, "body": body}]}
    (out / name).write_text(json.dumps(doc), encoding="utf-8")


def payload(findings, *, content_hash="sha256:abc", rendered="docs/diagrams/live.svg"):
    blueprint = {"content_hash": content_hash}
    if rendered is not None:
        blueprint["rendered"] = rendered
    return {"blueprint": blueprint, "findings": findings}


(out / "empty.json").write_text(json.dumps({"responses": []}), encoding="utf-8")
write("no-resource.json", payload([{"attribute": "replicas", "declared": 3, "live": 1}]))
write(
    "equal-aligned.json",
    payload([{"resource": "engine/core/state.py", "attribute": "timeout",
              "declared": "30s", "live": "30s", "status": "aligned"}]),
)
write(
    "false-positive.json",
    payload([{"resource": "engine/core/state.py", "attribute": "timeout",
              "declared": "30s", "live": "30s", "status": "drift"}]),
)
write(
    "bad-status.json",
    payload([{"resource": "gateway/proxy/router.py", "declared": "3", "live": "1",
              "status": "frobnicated"}]),
)
write("forbidden.json", {"error": "forbidden"}, status=403)
write("not-found.json", {"error": "not found"}, status=404)
PY

pass=0
fail=0

expect() {  # label want_rc fixture [required-substring]
  local label="$1" want="$2" fx="$3" pat="${4:-}"
  local out got ok=1
  out="$(python3 "$adapter" check --fixture "$fx" --company acme 2>&1)"; got=$?
  printf '%s\n' "$out" | sed 's/^/      | /'
  [ "$got" = "$want" ] || ok=0
  if [ -n "$pat" ] && ! printf '%s' "$out" | grep -qF "$pat"; then ok=0; fi
  if [ "$ok" = 1 ]; then
    printf '  OK    %s (rc=%s)\n' "$label" "$got"
    pass=$((pass + 1))
  else
    printf '  FAIL  %s (want rc=%s, got rc=%s, must name %s)\n' "$label" "$want" "$got" "$pat" >&2
    fail=$((fail + 1))
  fi
}

plan_sha() {  # fixture -> sha256 of the projection's stdout (stderr discarded)
  python3 "$adapter" check --fixture "$1" --company acme 2>/dev/null | sha256sum | cut -d' ' -f1
}

echo "== baseline projection =="
expect "the committed blueprint projects (blueprint + render + drift receipts)" 0 "$clean"

echo "== the emitted receipts validate against the frozen v2 schema =="
schema_rc=0
python3 - "$root" "$clean" <<'PY' || schema_rc=$?
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root))
from integrations.paperclip import diagrams, mapping  # noqa: E402

report = diagrams.project_fixture(Path(sys.argv[2]), company_id="acme")
problems = []
if report.exit_code() != 0:
    problems.append(f"baseline exit_code={report.exit_code()} findings={report.findings}")
schema = diagrams.receipt_schema(root)
for item in report.evidence:
    receipt = item["receipt"]
    if set(receipt) != {"kind", "ref", "result", "checks"}:
        problems.append(f"receipt is not the frozen shape: {receipt}")
    problems += mapping.validate(receipt, schema, "receipt")
if "facets" in diagrams.render(report):
    problems.append("the projection emitted a `facets` key (ADR-0017 forbids it)")
ticket = mapping.load_schema(root, "ticket")
if set(ticket["properties"]["facets"]["properties"]) != {"lessons", "raid", "budget"}:
    problems.append("the closed facet set moved")
if set(ticket["properties"]["kind"]["enum"]) != {
    "task", "incident", "rca", "corrective-action", "lesson", "suggestion"
}:
    problems.append("the closed ticket kind vocabulary moved")
print(f"  receipts={len(report.evidence)} schema/closed-set problems={len(problems)}")
for problem in problems:
    print(f"      FAIL  {problem}")
raise SystemExit(1 if problems else 0)
PY
if [ "$schema_rc" = 0 ]; then
  echo "  OK    every receipt is the frozen v2 shape; no facet, no new kind"
  pass=$((pass + 1))
else
  echo "  FAIL  the projected shape does not conform" >&2
  fail=$((fail + 1))
fi

echo "== determinism =="
sha_first="$(plan_sha "$clean")"
sha_second="$(plan_sha "$clean")"
if [ -n "$sha_first" ] && [ "$sha_first" = "$sha_second" ]; then
  printf '  OK    two runs are byte-identical (sha256 %s)\n' "$sha_first"
  pass=$((pass + 1))
else
  printf '  FAIL  two runs differ (%s vs %s)\n' "$sha_first" "$sha_second" >&2
  fail=$((fail + 1))
fi

echo "== negative controls (each refused by name) =="
expect "an empty fixture is CANNOT-ASSESS (names the fixture)" 2 "$scratch/empty.json" "field: fixture"
expect "a missing fixture is CANNOT-ASSESS (names the fixture)" 2 "$scratch/does-not-exist.json" "field: fixture"
expect "a Finding with no resource id is refused (names resource)" 1 "$scratch/no-resource.json" "field: resource"
expect "a status outside the vocabulary is refused (names status)" 1 "$scratch/bad-status.json" "field: status"
expect "a false-positive drift is refused (names status)" 1 "$scratch/false-positive.json" "field: status"
expect "a 403 response is CANNOT-ASSESS (names http-status)" 2 "$scratch/forbidden.json" "field: http-status"
expect "a 404 response is CANNOT-ASSESS (names http-status)" 2 "$scratch/not-found.json" "field: http-status"

# The most important control: equal attributes are NOT drift.
equal_rc=0
equal_out="$(python3 "$adapter" check --fixture "$scratch/equal-aligned.json" --company acme 2>&1)" || equal_rc=$?
if [ "$equal_rc" = 0 ] \
   && printf '%s' "$equal_out" | grep -qF '"ref": "engine/core/state.py"' \
   && printf '%s' "$equal_out" | grep -qF '"result": "PASS"' \
   && ! printf '%s' "$equal_out" | grep -qF '"result": "FAIL"'; then
  echo "  OK    equal declared/live attributes are aligned, never drift (rc=0, PASS only)"
  pass=$((pass + 1))
else
  printf '  FAIL  equal attributes were reported as drift (rc=%s)\n' "$equal_rc" >&2
  printf '%s\n' "$equal_out" | sed 's/^/      | /' >&2
  fail=$((fail + 1))
fi

echo "== the plan is unchanged by a negative-control run =="
sha_after_controls="$(plan_sha "$clean")"
if [ "$sha_after_controls" = "$sha_first" ]; then
  printf '  OK    the plan sha256 is unchanged after the controls (%s)\n' "$sha_after_controls"
  pass=$((pass + 1))
else
  printf '  FAIL  the plan changed after the controls (%s -> %s)\n' "$sha_first" "$sha_after_controls" >&2
  fail=$((fail + 1))
fi

echo "== mutation control (the gate can fail) =="
sha_adapter_before="$(sha256sum "$adapter" | cut -d' ' -f1)"
mutated="$scratch/diagrams.mutated.py"
cp "$adapter" "$mutated"
python3 - "$mutated" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
anchor = "if status != actual:"
if text.count(anchor) != 1:
    raise SystemExit("the assertion anchor is not unique; the gate cannot be trusted")
path.write_text(text.replace(anchor, "if False:  # mutated assertion anchor"), encoding="utf-8")
PY
sha_mutated="$(sha256sum "$mutated" | cut -d' ' -f1)"
mut_rc=0
PYTHONPATH="$root" python3 "$mutated" check --fixture "$scratch/false-positive.json" --company acme \
  >"$scratch/mutated.out" 2>&1 || mut_rc=$?
sha_adapter_after="$(sha256sum "$adapter" | cut -d' ' -f1)"
if [ "$sha_mutated" != "$sha_adapter_before" ] && [ "$mut_rc" = 0 ] \
   && [ "$sha_adapter_after" = "$sha_adapter_before" ]; then
  printf '  OK    anchoring on `if status != actual:` is load-bearing: mutated copy no longer refuses (rc=%s), real adapter byte-identical\n' "$mut_rc"
  pass=$((pass + 1))
else
  printf '  FAIL  the mutation control did not behave (mutated sha changed=%s, mutated rc=%s, adapter unchanged=%s)\n' \
    "$([ "$sha_mutated" != "$sha_adapter_before" ] && echo yes || echo no)" "$mut_rc" \
    "$([ "$sha_adapter_after" = "$sha_adapter_before" ] && echo yes || echo no)" >&2
  fail=$((fail + 1))
fi

echo "== the adapter is green again after the control =="
expect "the committed blueprint projects again (rc=0)" 0 "$clean"

echo
printf 'check-paperclip-diagrams: %s passed, %s failed\n' "$pass" "$fail"
if [ "$fail" -ne 0 ]; then
  exit 1
fi
exit 0
