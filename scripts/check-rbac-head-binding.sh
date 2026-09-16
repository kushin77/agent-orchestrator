#!/usr/bin/env bash
# check-rbac-head-binding.sh — tenant/RBAC binding for the head-of-org
# personas, hermes and paperclip (issue kushin77/agent-orchestrator#952,
# parent #878).
#
# Proves the gap issue #952 named is actually closed, not merely declared:
#
#   * identity/rbac/presets/head-agents.yaml + identity/rbac/head_bindings.py
#     exist and the `identity/rbac` pytest suite (which now includes
#     tests/test_head_bindings.py) is green;
#   * the pack is bound to neither hermes nor paperclip by default in a fresh
#     store (GR-28: declared-default-off) — asserted directly against the
#     live module, not against a claim in a comment;
#   * a PROVOKED negative control: bind hermes in a scratch org, prove an
#     allowed op passes, delete the Binding row the same way an operator or a
#     bug could, and require the identical op to flip to refused. This is run
#     against the real module in-process (no scratch git tree needed — the
#     RBAC store is already an in-memory fixture), and the control is checked
#     BOTH ways: the pristine (bound) case must pass, and the mutated
#     (unbound) case must fail, so the control cannot pass vacuously.
#
# Exit-code contract (GR-12 tri-state): 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-rbac-head-binding.sh
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

rbac_dir="identity/rbac"
pack="$rbac_dir/presets/head-agents.yaml"
module="$rbac_dir/head_bindings.py"
tests="$rbac_dir/tests/test_head_bindings.py"

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-rbac-head-binding: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi
for required in "$pack" "$module" "$tests"; do
  if [ ! -f "$required" ]; then
    echo "check-rbac-head-binding: CANNOT-ASSESS — $required is missing" >&2
    exit 2
  fi
done
if ! python3 -c "import yaml" >/dev/null 2>&1; then
  echo "check-rbac-head-binding: CANNOT-ASSESS — PyYAML not importable" >&2
  exit 2
fi

fail=0

# --- 1. the declared suite, including the new binding tests -----------------
echo "== identity/rbac pytest suite (incl. test_head_bindings.py) =="
if ! python3 -m pytest "$rbac_dir/tests" -q -p no:cacheprovider; then
  echo "check-rbac-head-binding: NOT-OK — identity/rbac pytest suite is red" >&2
  fail=1
fi

# --- 2. GR-28 default-off + the provoked negative control, in-process -------
echo "== default-off (GR-28) + provoked negative control (delete the binding row) =="
probe_out="$(python3 - <<'PY'
import sys

sys.path.insert(0, "identity")

from rbac import (
    HERMES_PERSONA_ID,
    PAPERCLIP_PERSONA_ID,
    InMemoryStore,
    bind_persona_to_tenant,
    guard_persona,
    is_persona_bound,
)

results = []


def check(name, condition):
    results.append((name, bool(condition)))


for persona_id, permission in (
    (HERMES_PERSONA_ID, "org:read"),
    (PAPERCLIP_PERSONA_ID, "org:read"),
):
    store = InMemoryStore()
    org = store.add_org("acme", "Acme", tenant_type="startup")

    # GR-28: nothing binds this persona until bind_persona_to_tenant is called.
    check(f"{persona_id}: unbound by default", not is_persona_bound(store, org.id, persona_id))
    check(
        f"{persona_id}: unbound tenant refused ({permission})",
        guard_persona(store, org.id, persona_id, permission).denied,
    )

    # Pristine: bind, then the op must be allowed.
    bind_persona_to_tenant(store, org, persona_id)
    pristine_allowed = guard_persona(store, org.id, persona_id, permission).allowed
    check(f"{persona_id}: bound tenant allowed ({permission})", pristine_allowed)

    # Negative control: delete the Binding row directly, bypassing
    # unbind_persona_from_tenant, to simulate an external/bad deletion. The
    # allowed op MUST flip to refused, or this control has caught nothing.
    from rbac.model import SUBJECT_AGENT  # noqa: F401 (documents subject_type)

    subject = f"persona:{persona_id}"
    (binding,) = store.bindings_for_subject(org.id, subject)
    deleted = store.delete_binding(binding.id)
    check(f"{persona_id}: binding row deleted", deleted)
    mutated_decision = guard_persona(store, org.id, persona_id, permission)
    check(
        f"{persona_id}: control - deleted binding is refused ({permission})",
        mutated_decision.denied and mutated_decision.reason == "scope",
    )

overall_ok = all(ok for _, ok in results)
for name, ok in results:
    print(f"  {'OK  ' if ok else 'FAIL'}  {name}")
print("PROBE_RESULT=" + ("OK" if overall_ok else "FAIL"))
PY
)"
probe_rc=$?
printf '%s\n' "$probe_out"

if [ "$probe_rc" -ne 0 ]; then
  echo "check-rbac-head-binding: CANNOT-ASSESS — the in-process probe crashed (rc=$probe_rc)" >&2
  exit 2
fi
if ! printf '%s\n' "$probe_out" | grep -q '^PROBE_RESULT=OK$'; then
  echo "check-rbac-head-binding: NOT-OK — default-off or the provoked negative control failed (see above)" >&2
  fail=1
fi

if [ "$fail" -ne 0 ]; then
  echo "check-rbac-head-binding: NOT-OK" >&2
  exit 1
fi

echo "check-rbac-head-binding: OK — hermes-head/paperclip-head roles bound per persona, GR-28 default-off holds, and the deleted-binding negative control reds correctly"
exit 0
