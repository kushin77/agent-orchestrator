#!/usr/bin/env bash
# check-erp-core-model.sh — the ERP core document model gate (ERP-02, issue #647).
#
# The model has two halves — document-family schemas and workflow data — and the
# only thing that makes them one model is that they cannot drift apart. The
# suite (integrations/erp/core/tests) proves that at unit level; this gate proves
# the same contract a second way, from the filesystem:
#
#   * it runs the module's own suite (whose provocations are the negative
#     controls: a schema whose state enum disagrees with its workflow, a document
#     that jumps a state, a double entry that does not balance);
#   * it then DRIVES the acceptance criteria independently of pytest, against a
#     scratch copy of the tree: the shipped assets must be coherent, a clean copy
#     must be refused NOTHING (so the check cannot be permanently red), and each
#     provoked breakage must be refused BY NAME — a schema with no harvest
#     record, no `$id`, a licence-boundary breach, a workflow whose states drift
#     from its schema's enum, a lifecycle with no workflow, and a provenance
#     record that has gone missing;
#   * and it drives both acceptance refusals end to end: an invalid document is
#     refused while its valid twin is accepted, and a state jump is refused while
#     the declared path is accepted.
#
# Everything is offline and deterministic — no network, no vendor seed — so it
# runs for real in a fresh worktree rather than being recorded as CANNOT-ASSESS.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-erp-core-model.sh
#
# ---knowledge---
# module_id: scripts.check-erp-core-model
# system: governance
# app: gates
# solution_class: enterprise
# patterns: [tri-state-exit, provoked-negative-control, offline-hermetic, named-refusal, lane-isolation, deterministic, schema-validation]
# derives_from: null
# owner_sme: qa-sme
# tier: L1
# interfaces: [exit 0 OK, exit 1 NOT-OK, exit 2 CANNOT-ASSESS]
# invariants: ""
# gotchas: ""
# related: ["#647"]
# do_not_duplicate: null
# ---knowledge---
set -uo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 2

core="integrations/erp/core"

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-erp-core-model: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi
if [ ! -d "$core" ]; then
  echo "check-erp-core-model: CANNOT-ASSESS — $core is missing" >&2
  exit 2
fi

fail=0

echo "== pytest ($core/tests) =="
if ! env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q "$core/tests"; then
  echo "check-erp-core-model: the document-model suite failed" >&2
  fail=$((fail + 1))
fi

echo "== acceptance, driven from the filesystem =="
if ! env PYTHONDONTWRITEBYTECODE=1 CORE_DIR="$core" python3 - <<'PY'
"""Drive the ERP-02 acceptance criteria against a scratch copy of the tree.

Each provocation mutates one artifact in the scratch copy and requires the
coherence check to refuse it *naming the file*. A clean copy is required to be
refused nothing, so the provocations cannot be passing because the check is
always red.
"""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, ".")

from integrations.erp.core import validators
from integrations.erp.core.errors import ErpError
from integrations.erp.core.schema import Validator

CORE = Path(os.environ["CORE_DIR"]).resolve()
SCRATCH = Path(tempfile.mkdtemp(prefix="erp-core-gate-")) / "core"

failures = []


def report(label, ok, detail=""):
    if ok:
        print(f"  OK    {label}")
    else:
        print(f"  FAIL  {label}{': ' + detail if detail else ''}")
        failures.append(label)


def copy_tree():
    if SCRATCH.exists():
        shutil.rmtree(SCRATCH)
    shutil.copytree(CORE, SCRATCH, ignore=shutil.ignore_patterns("__pycache__", "tests"))
    return SCRATCH


def edit_json(path, edit):
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    edit(document)
    Path(path).write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def problems(root):
    return validators.load_model(root).check_assets()


def provoked(label, edit, expect_file, expect_text):
    root = copy_tree()
    edit(root)
    found = problems(root)
    hit = [p for p in found if expect_file in p and expect_text in p]
    report(
        f"{label} is refused, naming {expect_file}",
        bool(hit),
        f"saw {found}" if not hit else "",
    )


# --- the shipped assets are coherent ---------------------------------------

shipped = validators.load_model(CORE)
report("the shipped model has no asset problems", shipped.check_assets() == [],
       str(shipped.check_assets())[:400])
report("ten document families are declared", len(shipped.document_kinds()) == 10,
       str(shipped.document_kinds()))
report("eight lifecycles are declared", len(shipped.workflows) == 8,
       str(shipped.workflows.documents()))

# --- a clean scratch copy is refused nothing --------------------------------

clean_root = copy_tree()
clean = problems(clean_root)
report("a clean scratch copy is refused nothing (the check is not always red)",
       clean == [], str(clean)[:400])

# --- each broken artifact is refused by name --------------------------------

provoked(
    "a schema with no harvest record",
    lambda root: edit_json(
        root / "schemas" / "sales-order.schema.json",
        lambda doc: doc.pop(validators.PROVENANCE_KEY),
    ),
    "schemas/sales-order.schema.json",
    "harvest record",
)

provoked(
    "a schema with no $id",
    lambda root: edit_json(
        root / "schemas" / "party.schema.json", lambda doc: doc.pop("$id")
    ),
    "schemas/party.schema.json",
    "$id",
)

provoked(
    "a harvest that claims upstream code was copied",
    lambda root: edit_json(
        root / "schemas" / "party.schema.json",
        lambda doc: doc[validators.PROVENANCE_KEY]["source"].__setitem__(
            "mode", "copied"
        ),
    ),
    "schemas/party.schema.json",
    "copied or vendored",
)

provoked(
    "a workflow whose states drift from its schema's enum",
    lambda root: (root / "workflows" / "sales-order.yaml").write_text(
        (root / "workflows" / "sales-order.yaml")
        .read_text(encoding="utf-8")
        .replace("completed", "finished"),
        encoding="utf-8",
    ),
    "schemas/sales-order.schema.json",
    "state enum",
)

provoked(
    "a lifecycle with no workflow",
    lambda root: (root / "workflows" / "gl-posting.yaml").unlink(),
    "schemas/gl-posting.schema.json",
    "no workflow",
)

provoked(
    "a provenance record that has gone missing",
    lambda root: edit_json(
        root / validators.PROVENANCE_FILENAME,
        lambda doc: doc["schemas"].pop("item.schema.json"),
    ),
    validators.PROVENANCE_FILENAME,
    "no record for schemas/item.schema.json",
)

# --- the two acceptance refusals, with their accepting twins ----------------

model = validators.load_model(CORE)
order = {
    "doctype": "sales-order",
    "id": "SO-GATE",
    "state": "draft",
    "docstatus": 0,
    "company": "ACME",
    "currency": "USD",
    "transaction_date": "2026-09-15",
    "party": "CUST-1",
    "lines": [{"item_code": "ITEM-1", "qty": 1, "rate": 10.0}],
}

try:
    model.validate_document("sales-order", order)
    valid_refused = None
except ErpError as refusal:
    valid_refused = refusal

broken = dict(order)
broken.pop("party")
try:
    model.validate_document("sales-order", broken)
    invalid_verdict = "accepted"
except ErpError as refusal:
    invalid_verdict = refusal.code

report(
    "an invalid document is refused while its valid twin is accepted",
    valid_refused is None and invalid_verdict == "schema_violation",
    f"valid={valid_refused!r} invalid={invalid_verdict!r}",
)

workflow = model.workflow_for("sales-order")
legal = workflow.next_state("draft", "submit")
try:
    workflow.assert_move("draft", "completed")
    jump = "accepted"
except ErpError as refusal:
    jump = refusal.code

report(
    "a state jump is refused while the declared path is accepted",
    legal == "submitted" and jump == "state_jumped",
    f"legal={legal!r} jump={jump!r}",
)

# --- the invariant a schema cannot express ---------------------------------

unbalanced = {
    "doctype": "gl-posting",
    "id": "GL-GATE",
    "state": "draft",
    "docstatus": 0,
    "company": "ACME",
    "currency": "USD",
    "posting_date": "2026-09-15",
    "voucher_type": "sales-invoice",
    "voucher_id": "SI-1",
    "lines": [
        {"account": "DEBTORS", "debit": 100.0},
        {"account": "REVENUE", "credit": 90.0},
    ],
}
schema_violations = Validator(base_dir=CORE / "schemas").violations(
    unbalanced, model.schema_for("gl-posting")
)
try:
    model.validate_document("gl-posting", unbalanced)
    rule_verdict = "accepted"
except ErpError as refusal:
    rule_verdict = refusal.code

report(
    "the double-entry rule refuses what the schema alone would accept",
    schema_violations == [] and rule_verdict == "unbalanced_posting",
    f"schema={schema_violations} verdict={rule_verdict!r}",
)

shutil.rmtree(SCRATCH.parent, ignore_errors=True)

if failures:
    print(f"check-erp-core-model: {len(failures)} acceptance control(s) failed", file=sys.stderr)
    sys.exit(1)
print("check-erp-core-model: every acceptance control held")
PY
then
  fail=$((fail + 1))
fi

if [ "$fail" -ne 0 ]; then
  echo "check-erp-core-model: FAIL ($fail control group(s) failed)" >&2
  exit 1
fi
echo "check-erp-core-model: OK"
