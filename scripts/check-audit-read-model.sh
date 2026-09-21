#!/usr/bin/env bash
# check-audit-read-model.sh — the audit read model must be a real, read-only,
# filterable projection over the tamper-evident ledger (issue #347).
#
# The audit surface is where the SaaS shows "who did what": the shell's Audit
# view consumes this read model through the paperclip integration. A read model
# that is not deterministic, that exposes a write path, that refuses a legal
# filter, or — worst — that reports an intact chain while a record was modified,
# reordered or removed, is exactly the false green this repo's doctrine forbids
# (GR-12). This gate provokes every such failure for real.
#
# What it asserts (all offline, no network, no containers):
#   1. the read model is deterministic: two runs over one input are identical;
#   2. verify_chain() is OK on an intact chain;
#   3. a modified record is NOT-OK and the broken record is named;
#   4. a reordered record is NOT-OK;
#   5. a removed record is NOT-OK;
#   6. a silently truncated trail is NOT-OK once the trusted tail is supplied;
#   7. an unparseable chain is CANNOT-ASSESS — never a pass;
#   8. a filter naming an unknown field, or an unknown severity, is refused;
#   9. the read model exposes no mutating name and calls only the ledger's read
#      API (proved by parsing the source, not by grepping text).
# Every tamper is provoked on a TEMP COPY; the committed ledger is never touched.
#
# The gate then runs its own negative control: a "must-be-intact" checker is
# run first on the intact ledger (must be rc 0, so it cannot pass vacuously) and
# then on a tampered copy (must be rc 1 and name the record). If the mutant
# passes, this gate reports FAIL — a check that cannot fail is a formality.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
# CANNOT-ASSESS must never be reported as a pass.
#
# Usage: bash scripts/check-audit-read-model.sh
#
# ---knowledge---
# module_id: scripts.check-audit-read-model
# system: governance
# app: gates
# solution_class: enterprise
# patterns: [tri-state-exit, provoked-negative-control, no-false-green, offline-hermetic, named-refusal, deterministic]
# derives_from: null
# owner_sme: qa-sme
# tier: L1
# interfaces: [exit 0 OK, exit 1 NOT-OK, exit 2 CANNOT-ASSESS]
# invariants: ""
# gotchas: ""
# related: ["#347"]
# do_not_duplicate: null
# ---knowledge---
set -u

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 2

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-audit-read-model: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi
if ! command -v sha256sum >/dev/null 2>&1; then
  echo "check-audit-read-model: CANNOT-ASSESS — sha256sum not found (cannot prove the mutation)" >&2
  exit 2
fi
if [ ! -f telemetry/audit/read_model.py ]; then
  echo "check-audit-read-model: CANNOT-ASSESS — no telemetry/audit/read_model.py" >&2
  exit 2
fi

work="/tmp/ao347.$$.$(date +%s)"
if ! mkdir -p "$work" 2>/dev/null; then
  echo "check-audit-read-model: CANNOT-ASSESS — cannot create a scratch directory" >&2
  exit 2
fi
trap 'rm -rf "$work"' EXIT

python3 - "$root" "$work" <<'PY'
import ast
import json
import os
import shutil
import sys

root, work = sys.argv[1], sys.argv[2]
sys.path.insert(0, os.path.join(root, "telemetry", "audit"))
sys.path.insert(0, os.path.join(root, "telemetry"))

import read_model  # noqa: E402
from ledger import open_ledger  # noqa: E402
from read_model import ChainVerdict, FilterError  # noqa: E402

failures = []


def check(ok, message):
    if ok:
        print("  OK    " + message)
    else:
        failures.append(message)
        print("  FAIL  " + message, file=sys.stderr)


SEED = [
    ("acme", "user:alice", "model.call", "gateway/proxy", None, "2026-09-08T10:00:00Z"),
    ("acme", "agent:w1", "registry.register", "registry/agents/w1", "ev:1", "2026-09-08T10:01:00Z"),
    ("acme", "agent:w1", "policy.deny", "gateway/proxy", None, "2026-09-08T10:02:00Z"),
    ("beta", "user:bob", "model.call", "gateway/proxy", None, "2026-09-08T11:00:00Z"),
]


def build(directory):
    store = open_ledger(directory)
    for tenant, actor, action, resource, evidence, ts in SEED:
        store.append(
            tenant, actor=actor, action=action, resource=resource,
            evidence=evidence, ts=ts,
        )


def chain(directory, tenant="acme"):
    return os.path.join(directory, tenant + ".jsonl")


def lines(path):
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read().splitlines()


def write(path, rows):
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(rows) + "\n")


def data_indices(rows):
    return [i for i, row in enumerate(rows) if row.strip() and not row.startswith("#")]


def alter(path, old, new):
    rows = lines(path)
    for i in data_indices(rows):
        if old in rows[i]:
            rows[i] = rows[i].replace(old, new, 1)
            write(path, rows)
            return
    raise AssertionError("anchor not found: " + old)


def reorder(path):
    rows = lines(path)
    positions = data_indices(rows)
    first, second = positions[0], positions[1]
    rows[first], rows[second] = rows[second], rows[first]
    write(path, rows)


def drop(path, index):
    rows = lines(path)
    del rows[data_indices(rows)[index]]
    write(path, rows)


def corrupt(path):
    rows = lines(path)
    rows.append("{this is not json")
    write(path, rows)


def copy(tag):
    target = os.path.join(work, tag)
    shutil.copytree(os.path.join(work, "ledger"), target)
    return target


def store_methods_called(source):
    called = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        base = node.func.value
        if isinstance(base, ast.Attribute) and base.attr in ("_store", "store"):
            called.add(node.func.attr)
        elif isinstance(base, ast.Name) and base.id in ("_store", "store"):
            called.add(node.func.attr)
    return called


# --- build the trail and the read model ----------------------------------- #
build(os.path.join(work, "ledger"))
model = read_model.open_read_model(os.path.join(work, "ledger"))

# --- 1. determinism ------------------------------------------------------- #
first = json.dumps(model.filter(), sort_keys=True)
second = json.dumps(model.filter(), sort_keys=True)
check(first == second, "filter is deterministic under identical input")
check(
    json.dumps(model.stats(), sort_keys=True) == json.dumps(model.stats(), sort_keys=True),
    "stats is deterministic under identical input",
)

# --- 2. an intact chain verifies OK --------------------------------------- #
verdict = model.verify_chain()
check(
    verdict.status == ChainVerdict.OK and verdict.is_pass and verdict.exit_code == 0,
    "verify_chain is OK on an intact chain (exit 0)",
)
anchor = model.trusted_tail()

# --- 3-7. every tamper class is detected, none silently skipped ------------ #
altered = copy("altered")
alter(chain(altered), '"model.call"', '"model.read"')
verdict = read_model.open_read_model(altered).verify_chain()
check(
    verdict.status == ChainVerdict.NOT_OK
    and verdict.tenants["acme"]["brokenAt"] == 1
    and "hash mismatch" in verdict.tenants["acme"]["detail"],
    "a MODIFIED record is NOT-OK and the record is named (acme record 1)",
)

reordered = copy("reordered")
reorder(chain(reordered))
verdict = read_model.open_read_model(reordered).verify_chain()
check(
    verdict.status == ChainVerdict.NOT_OK
    and "record 1" in verdict.tenants["acme"]["detail"],
    "a REORDERED record is NOT-OK and the record is named (acme record 1)",
)

removed = copy("removed")
drop(chain(removed), 1)
verdict = read_model.open_read_model(removed).verify_chain()
check(
    verdict.status == ChainVerdict.NOT_OK
    and verdict.tenants["acme"]["brokenAt"] == 2
    and "seq" in verdict.tenants["acme"]["detail"],
    "a REMOVED record is NOT-OK and the position is named (acme record 2)",
)

truncated = copy("truncated")
drop(chain(truncated), -1)
verdict = read_model.open_read_model(truncated).verify_chain(expected=anchor)
check(
    verdict.status == ChainVerdict.NOT_OK
    and "does not match expected" in verdict.tenants["acme"]["detail"],
    "a TRUNCATED trail is NOT-OK against the trusted tail anchor",
)

shattered = copy("shattered")
corrupt(chain(shattered))
verdict = read_model.open_read_model(shattered).verify_chain()
check(
    verdict.status == ChainVerdict.CANNOT_ASSESS
    and verdict.is_pass is False
    and verdict.exit_code == 2
    and bool(verdict.findings()),
    "an UNPARSEABLE chain is CANNOT-ASSESS, never a pass (exit 2)",
)

# --- 8. the filter vocabulary is closed ----------------------------------- #
try:
    model.filter(severity="emergency")
    check(False, "an unknown severity is refused")
except FilterError:
    check(True, "an unknown severity is refused")
try:
    model.filter(nonsense="x")
    check(False, "a filter with an unknown field is refused")
except FilterError as exc:
    check("nonsense" in str(exc), "a filter with an unknown field is refused by name")

# --- 9. read-only is structural ------------------------------------------- #
public = sorted(name for name in dir(model) if not name.startswith("_"))
mutating = ("append", "update", "delete", "write", "create", "remove", "rechain",
            "insert", "mutate", "put", "patch", "drop")
offenders = [name for name in public for verb in mutating if verb in name.lower()]
check(offenders == [], "the read model exposes no mutating name")
called = store_methods_called(open(read_model.__file__, encoding="utf-8").read())
check(
    called == {"records", "tenant_ids", "tail_state", "verify"},
    "the read model calls only the ledger's read API (%s)" % ",".join(sorted(called)),
)

if failures:
    for finding in failures:
        print("  FAIL  " + finding, file=sys.stderr)
    raise SystemExit(1)
print("  OK    read model: deterministic, intact-OK, every tamper refused, read-only")
raise SystemExit(0)
PY
rc=$?
case "$rc" in
  0) : ;;
  1)
    echo "check-audit-read-model: FAIL — the audit read model violated its contract (see above)" >&2
    exit 1
    ;;
  *)
    echo "check-audit-read-model: CANNOT-ASSESS — validator returned $rc" >&2
    exit 2
    ;;
esac

if [ ! -d "$work/ledger" ]; then
  echo "check-audit-read-model: CANNOT-ASSESS — the validator built no ledger to control against" >&2
  exit 2
fi

# --- negative control ------------------------------------------------------ #
# Vacuity control: a checker that asserts "the chain is intact" must pass on the
# intact ledger (so it cannot be a stuck failure) and must refuse a tampered
# copy BY NAME. The mutation must change the bytes and the sha256; if the mutant
# still passes, this gate reports FAIL — a check that cannot fail is a formality.
cat > "$work/checker.py" <<'PY'
import os
import sys

root, ledger = sys.argv[1], sys.argv[2]
sys.path.insert(0, os.path.join(root, "telemetry", "audit"))
sys.path.insert(0, os.path.join(root, "telemetry"))

import read_model  # noqa: E402

verdict = read_model.open_read_model(ledger).verify_chain()
if verdict.is_pass:
    for tenant, entry in sorted(verdict.tenants.items()):
        print("INTACT tenant=%s seq=%s" % (tenant, entry.get("seq")))
    raise SystemExit(0)
for entry in verdict.findings():
    print(
        "FINDING tenant=%s record=%s %s"
        % (entry.get("tenant"), entry.get("brokenAt"), entry.get("detail"))
    )
raise SystemExit(1 if verdict.status == "NOT-OK" else 2)
PY

# The checker must certify the intact ledger (a checker that always fails is
# not a control).
intact_out="$(python3 "$work/checker.py" "$root" "$work/ledger" 2>&1)"
intact_rc=$?
if [ "$intact_rc" -ne 0 ]; then
  echo "check-audit-read-model: CANNOT-ASSESS — the negative-control checker rejects an intact chain" >&2
  printf '%s\n' "$intact_out" >&2
  exit 2
fi

cp -r "$work/ledger" "$work/mutant-ledger"
before="$(sha256sum "$work/mutant-ledger/acme.jsonl" | awk '{print $1}')"
python3 - "$work/mutant-ledger/acme.jsonl" <<'PY'
import sys

path = sys.argv[1]
text = open(path, encoding="utf-8").read()
needle = '"model.call"'
if text.count(needle) != 1:
    raise SystemExit(2)
open(path, "w", encoding="utf-8").write(text.replace(needle, '"model.read"', 1))
PY
mutate_rc=$?
if [ "$mutate_rc" -ne 0 ]; then
  echo "check-audit-read-model: CANNOT-ASSESS — could not build the negative control" >&2
  exit 2
fi
after="$(sha256sum "$work/mutant-ledger/acme.jsonl" | awk '{print $1}')"
if [ "$before" = "$after" ]; then
  echo "check-audit-read-model: CANNOT-ASSESS — the mutation did not change the file" >&2
  exit 2
fi

mutant_out="$(python3 "$work/checker.py" "$root" "$work/mutant-ledger" 2>&1)"
mutant_rc=$?
if [ "$mutant_rc" -eq 1 ] \
  && printf '%s\n' "$mutant_out" | grep -qF -- "tenant=acme" \
  && printf '%s\n' "$mutant_out" | grep -qF -- "record=1"; then
  echo "  OK    negative control: a tampered record is refused by name (acme record 1)"
else
  echo "check-audit-read-model: FAIL — negative control passed; a modified record was not caught (the gate cannot fail)" >&2
  printf '%s\n' "$mutant_out" >&2
  exit 1
fi

echo "  OK    negative control: the mutation changed the chain bytes"
printf '        before=%s\n        after =%s\n' "$before" "$after"
echo "check-audit-read-model: OK — deterministic read-only projection, every tamper refused, negative control refused"
exit 0
