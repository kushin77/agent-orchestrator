#!/usr/bin/env bash
# check-erp-module.sh — the ERP module foundation gate (EPIC #645, issue #646).
#
# WHY THIS EXISTS. ERP-01 stands up a module whose claim is that every datum it
# knows is served by the knowledge indexer and that no second store of ERP domain
# facts exists. Both claims read green while they rot: a manifest that stops
# declaring `mandatory`, a flag default or `data_source: indexer` still parses; a
# catalogue file that loses its provenance still parses; a README that restates a
# catalogue fact is prose nobody re-measures. So the claims are measured here, in
# three separable parts, and each one can genuinely fail.
#
# WHAT IT MEASURES
#   1. THE DECLARATION, on the live tree — the manifest against its frozen schema
#      (where `mandatory: true`, the flag's OFF default and `data_source: indexer`
#      are constrained by the schema itself, not by prose), every catalogue file
#      against its schema, provenance complete AND identical to the manifest's
#      single declaration, every cross-reference resolved, every declared indexer
#      glob registered verbatim in governance/knowledge/sources.py, the single-
#      store rule, and the docs index's reference to the gap analysis.
#      `integrations/erp/catalog/cli.py verify`, exit 0.
#   2. THE REFUSALS, provoked — the suite mutates a SCRATCH copy and requires each
#      refusal by name, with a clean copy refused nothing, so no rule above can be
#      a formality. It also proves the schema cannot become a decoration: a schema
#      using a keyword the repository's validator cannot enforce yields
#      CANNOT-ASSESS, never a pass.
#   3. THE INDEXER, actually serving the module — `validate` is green with the ERP
#      sources registered, and a query for a declared document type returns the
#      CATALOGUE FILE that declares it. That last part is the whole point of
#      ERP-01: the module's information comes from the indexer, and a module whose
#      catalogue is not indexed cannot answer the query.
#
# The provocation for a restated fact reads its fact OUT OF THE CATALOGUE
# (`cli.py vocabulary`) instead of hard-coding one: a check that carries its own
# copy of the data it checks is a second store with extra steps.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
# CANNOT-ASSESS must never be reported as a pass.
#
# Usage: bash scripts/check-erp-module.sh
#
# ---knowledge---
# module_id: scripts.check-erp-module
# system: governance
# app: gates
# solution_class: enterprise
# patterns: [tri-state-exit, provoked-negative-control, named-refusal, schema-validation]
# derives_from: null
# owner_sme: qa-sme
# tier: L1
# interfaces: [exit 0 OK, exit 1 NOT-OK, exit 2 CANNOT-ASSESS]
# invariants: ""
# gotchas: ""
# related: ["#645", "#646"]
# do_not_duplicate: null
# ---knowledge---
set -u

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 2

cli="integrations/erp/catalog/cli.py"
suite="integrations/erp/catalog/tests"
indexer="governance/knowledge/cli.py"

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-erp-module: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi
for required in "$cli" "$indexer"; do
  if [ ! -f "$required" ]; then
    echo "check-erp-module: CANNOT-ASSESS — $required is missing" >&2
    exit 2
  fi
done

fail=0
cannot=0

echo "== the module's declaration =="
python3 "$cli" verify
rc=$?
case "$rc" in
  0)
    echo "  OK    the declaration validates: manifest schema, catalogue schemas,"
    echo "        provenance, cross-references, indexer registration, single store"
    ;;
  2) echo "  CANNOT-ASSESS  the declaration could not be assessed" >&2; cannot=1 ;;
  *) echo "  FAIL  the declaration does not validate" >&2; fail=1 ;;
esac

echo "== the refusals, provoked =="
if [ ! -d "$suite" ]; then
  echo "  FAIL  $suite is missing — no rule above is provoked" >&2
  fail=1
elif python3 -m pytest -p no:cacheprovider -q "$suite"; then
  echo "  OK    every refusal is provoked and named, and a clean copy is refused nothing"
else
  echo "  FAIL  a provoked refusal did not bite" >&2
  fail=1
fi

echo "== the indexer carries the module =="
python3 "$indexer" validate
rc=$?
case "$rc" in
  0) echo "  OK    the knowledge index validates with the ERP sources registered" ;;
  2) echo "  CANNOT-ASSESS  the knowledge index could not be assessed" >&2; cannot=1 ;;
  *) echo "  FAIL  the knowledge index is NOT-OK with the ERP sources registered" >&2; fail=1 ;;
esac

echo "== the indexer serves the module =="
# The issue's own acceptance check: a query for a declared document type must
# return the ERP catalogue entry, not merely something. `sales order` is the
# phrase the issue names; the query is a substring match over the indexed
# metadata, so the assertion is that the returned set CONTAINS the catalogue file
# that declares the type.
query_out="$(python3 "$indexer" query --text "sales order" --kind pattern-template 2>&1)"
rc=$?
if [ "$rc" -ne 0 ]; then
  printf '%s\n' "$query_out" | tail -n 20
  echo "  FAIL  the indexer returned no match for a declared document type (rc=$rc)" >&2
  fail=1
elif printf '%s\n' "$query_out" | python3 -c '
import json
import sys

payload = json.load(sys.stdin)
paths = [entry["path"] for entry in payload.get("results", [])]
catalogue = [path for path in paths if path.startswith("integrations/erp/catalog/")]
print("  matches: %d; ERP catalogue entries among them: %d" % (len(paths), len(catalogue)))
for path in catalogue:
    print("    %s" % path)
if not catalogue:
    print(
        "  FAIL  the module catalogue is not what answered the query — its facts are not"
        " served by the indexer",
        file=sys.stderr,
    )
    raise SystemExit(1)
'
then
  echo "  OK    the answer to the query is the catalogue file that declares the document type"
else
  echo "  FAIL  the indexer does not serve the module catalogue" >&2
  fail=1
fi

if [ "$fail" -ne 0 ]; then
  echo "erp-module: FAIL" >&2
  exit 1
fi
if [ "$cannot" -ne 0 ]; then
  echo "erp-module: CANNOT-ASSESS" >&2
  exit 2
fi
echo "erp-module: OK"
exit 0
