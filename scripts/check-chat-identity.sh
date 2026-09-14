#!/usr/bin/env bash
# check-chat-identity.sh — scoped (tenant, agent, conversation) chat credentials
# and structural tenant isolation (issue kushin77/agent-orchestrator#505).
#
# This gate proves the chat identity layer BITES rather than merely existing:
#
#   * the deliverable is present and the suite is green;
#   * the credential layer did not fork the gateway's crypto — the verifier and
#     encoder the chat path uses are the *same function objects* as
#     gateway.mcp.authn's, and the module re-implements no JOSE;
#   * the vocabulary is the merged one: the conversation container is what
#     engine.memory.model.container_id returns, the front-door cookie / purpose
#     / super-admin role match portal.server.sso, and the approval outcome is
#     identity.cpapi.errors.approval_required itself;
#   * every refusal is PROVOKED and reported by name: a foreign tenantId in the
#     body and in a query parameter, an unmapped and an ambiguous client
#     identity, a revoked-but-unexpired credential, a credential presented for
#     another tenant or conversation, a mismatched memory container, a direct
#     write with a pending approval, a proposal on another tenant's resource,
#     minting with no signing key, and verifying with no revocation store;
#   * the cross-scope counter is asserted to *increment* on the mismatch, and
#     engine.memory is asserted to still raise MemoryIsolationError when the
#     chat layer is bypassed — so "the chat path cannot bypass the engine" is a
#     measurement, not a claim.
#
# AO-GR-4 / GR-12 (no false green): the probe driver counts refusals and
# *controls* separately and this script checks both counts against pinned
# expectations. A driver that collapsed — a mutation that made every case
# "refused", or one that made every case "accepted" — fails one of the two
# counts, because the controls must be ACCEPTED on the same code path the
# mutants are refused on. If any refusal is accepted, this check FAILS.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-chat-identity.sh
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

# Pinned expectations: a change to the probe table must change this gate too.
EXPECT_MUTANTS_REFUSED=16
EXPECT_CONTROLS_ACCEPTED=7

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-chat-identity: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

fail=0

# ── 1. the deliverable is present ──────────────────────────────────────────
echo "== deliverable =="
required=(
  identity/chat/__init__.py
  identity/chat/README.md
  identity/chat/errors.py
  identity/chat/credential.py
  identity/chat/binding.py
  identity/chat/frontdoor.py
  identity/chat/isolation.py
  identity/chat/approvals.py
  identity/chat/identity-map.json
  identity/chat/tests/conftest.py
  identity/chat/tests/test_credential.py
  identity/chat/tests/test_binding.py
  identity/chat/tests/test_frontdoor.py
  identity/chat/tests/test_isolation.py
  identity/chat/tests/test_approvals.py
  identity/chat/tests/test_failclosed.py
)
for path in "${required[@]}"; do
  if [ -f "$path" ]; then
    echo "  OK    $path present"
  else
    echo "  FAIL  $path is missing" >&2
    fail=$((fail + 1))
  fi
done
if [ "$fail" -gt 0 ]; then
  echo "check-chat-identity: FAIL ($fail missing file(s))" >&2
  exit 1
fi

# ── 2. the suite is green ──────────────────────────────────────────────────
echo "== pytest identity/chat =="
suite_log="$(python3 -m pytest identity/chat -q -p no:cacheprovider 2>&1)"
suite_rc=$?
summary="$(printf '%s\n' "$suite_log" | tail -3 | grep -E 'passed|failed|error' | tail -1)"
if [ "$suite_rc" -eq 0 ]; then
  echo "  OK    ${summary:-pytest exited 0}"
else
  echo "  FAIL  pytest identity/chat exited $suite_rc" >&2
  printf '%s\n' "$suite_log" | tail -25 | sed 's/^/        /' >&2
  fail=$((fail + 1))
fi

# ── 3. no fork of the merged crypto, and the merged vocabulary ─────────────
echo "== merged contracts (no fork) =="
if python3 - <<'PY'
"""Assert the chat layer consumes the merged lanes instead of re-implementing."""
import inspect
import sys
from pathlib import Path

problems = []

# --- the gateway's credential format and verifier, by object identity ------
from gateway.mcp import authn as gateway_authn
from identity.chat import credential as chat_credential

if chat_credential.authn is not gateway_authn:
    problems.append("identity.chat.credential does not import gateway.mcp.authn itself")
for name in ("verify_token", "session_to_token", "mint_session"):
    if getattr(chat_credential.authn, name) is not getattr(gateway_authn, name):
        problems.append(f"credential.{name} is not gateway.mcp.authn.{name}")

source = Path(chat_credential.__file__).read_text(encoding="utf-8")
for forbidden in ("import hmac", "import base64", "import jwt", "def _sign"):
    if forbidden in source:
        problems.append(f"identity/chat/credential.py re-implements {forbidden!r}")

# --- the engine's container vocabulary ------------------------------------
# Value equality alone is not enough: an implementation that formatted the
# string itself would agree today and drift the moment the engine's container
# naming changed. So the check also proves *delegation* - the name the module
# imported is the engine's function object, and patching that name changes the
# answer the chat helper returns.
import engine.memory.model as engine_model
import identity.chat.isolation as isolation_module
from engine.memory.model import MemoryScope, container_id
from identity.chat.isolation import conversation_container

if isolation_module.container_id is not engine_model.container_id:
    problems.append(
        "identity.chat.isolation does not import engine.memory.model.container_id"
    )
if conversation_container("t", "a", "c") != container_id(
    MemoryScope.SESSION, "t", "a", "c"
):
    problems.append("the chat container is not the engine's container_id()")

_original_container_id = isolation_module.container_id
try:
    isolation_module.container_id = lambda *args, **kwargs: "SENTINEL-CONTAINER"
    delegated = conversation_container("t", "a", "c")
finally:
    isolation_module.container_id = _original_container_id
if delegated != "SENTINEL-CONTAINER":
    problems.append(
        "conversation_container does not delegate to the engine's container_id "
        "(it re-derived the container name itself)"
    )

# --- the front door is the portal's contract ------------------------------
from identity.chat import frontdoor
from identity.sso.tokens import verify_console_session_token
from portal.server import sso as portal_sso

if frontdoor.SESSION_COOKIE != portal_sso.SESSION_COOKIE:
    problems.append("session cookie differs from portal.server.sso")
if frontdoor.CONSOLE_TOKEN_PURPOSE != portal_sso.CONSOLE_TOKEN_PURPOSE:
    problems.append("token purpose differs from portal.server.sso")
if frontdoor.ROOT_ADMIN_ROLE != portal_sso.ROOT_ADMIN_ROLE:
    problems.append("super-admin role differs from portal.server.sso")
if frontdoor.default_console_verifier() is not verify_console_session_token:
    problems.append("the front door does not default to the merged console verifier")

# --- the approval outcome is the control plane's own object ---------------
import identity.cpapi.errors as cpapi_errors
from identity.chat.errors import ApprovalRequired

probe = ApprovalRequired("apr_probe")
wire = probe.as_api_error()
reference = cpapi_errors.approval_required("apr_probe")
if (wire.status, wire.code) != (reference.status, reference.code):
    problems.append("approval_required status/code differ from identity.cpapi.errors")
if wire.details != reference.details:
    problems.append("approval_required details differ from identity.cpapi.errors")

# --- the router's public surface is exactly the declared one -------------
from identity.chat.approvals import ROUTER_PUBLIC_SURFACE, ChatApprovalRouter

surface = {
    name
    for name, member in inspect.getmembers(ChatApprovalRouter, callable)
    if not name.startswith("_")
}
if surface != set(ROUTER_PUBLIC_SURFACE):
    problems.append(
        f"router surface {sorted(surface)} != declared {sorted(ROUTER_PUBLIC_SURFACE)}"
    )

print(f"  OK    merged contracts verified ({len(problems)} problem(s))"
      if not problems else "  FAIL  contract drift:")
for problem in problems:
    print(f"        {problem}", file=sys.stderr)
raise SystemExit(1 if problems else 0)
PY
then
  :
else
  fail=$((fail + 1))
fi

# ── 4. provoke every refusal; assert the counters; keep the controls live ──
echo "== refusal probes (AO-GR-4: each mutant is provoked) =="
probe_log="$(python3 - <<'PY' 2>&1
"""Drive every refusal through the real modules and report it by name.

Each mutant must raise its named refusal. Each control must *succeed* on the
same code path, so a driver that collapsed into "always refused" or "always
accepted" cannot report a clean sheet: the two counts are checked separately by
the caller against pinned expectations.
"""
import sys

from engine.memory.model import MemoryIsolationError, MemoryScope
from engine.memory.store import InMemoryStore
from identity.chat.approvals import ChatApprovalRouter
from identity.chat.binding import IdentityMap
from identity.chat.credential import (
    assert_scope,
    mint_chat_credential,
    verify_chat_credential,
)
from identity.chat.errors import ChatError
from identity.chat.frontdoor import ChatFrontDoor, ChatRequest, assert_no_foreign_tenant
from identity.chat.isolation import ChatIsolation
from identity.cpapi.approvals import ApprovalStore
from identity.cpapi.fakes import FakeClock, fake_rng
from identity.sso.store import InMemoryStore as RevocationStore

TENANT_A, TENANT_B = "tenant-acme", "tenant-globex"
AGENT_A, AGENT_B = "coder", "analyst"
CONV_1, CONV_2 = "conv-1", "conv-2"
CLIENT = "openwebui"
ADA = "ada@acme.example"
MALLORY = "mallory@evil.example"
KEY = bytes(range(32))
NOW = 1_800_000_000

refused = 0
controls = 0
mistakes = []


def credential(tenant=TENANT_A, agent=AGENT_A, conversation=CONV_1, subject="user-1"):
    return mint_chat_credential(
        tenant_id=tenant,
        agent_id=agent,
        conversation_id=conversation,
        signing_key=KEY,
        subject=subject,
        now=NOW,
    )


def expect_refused(name, wanted_code, fn):
    """A mutant must raise its named refusal; anything else is a FAIL."""
    global refused
    try:
        fn()
    except ChatError as exc:
        if exc.code == wanted_code:
            refused += 1
            print(f"  OK    REFUSED {name} — {exc.code}")
        else:
            mistakes.append(f"{name}: refused as {exc.code!r}, expected {wanted_code!r}")
    except Exception as exc:  # noqa: BLE001 - any other outcome is a finding
        mistakes.append(f"{name}: raised {type(exc).__name__}: {exc}")
    else:
        mistakes.append(f"{name}: ACCEPTED-BY-MISTAKE (the control did not bite)")


def expect_accepted(name, fn):
    """A control must succeed, proving the refusals above are not blanket."""
    global controls
    try:
        fn()
    except Exception as exc:  # noqa: BLE001
        mistakes.append(f"{name}: CONTROL-REFUSED-BY-MISTAKE ({type(exc).__name__}: {exc})")
    else:
        controls += 1
        print(f"  OK    ACCEPTED {name}")


# --- rogue credentials, minted the way an attacker would hold them ---------
rogue_tenant = credential(TENANT_B, AGENT_B, CONV_2, subject="user-2")
rogue_conv = credential(TENANT_A, AGENT_A, CONV_2, subject="user-1")
good = credential()

# --- 1. cross-tenant read -------------------------------------------------
store = InMemoryStore()
isolation = ChatIsolation(store)
foreign_entry = store.put(
    tenant_id=TENANT_B,
    agent_id=AGENT_B,
    session_id=CONV_2,
    scope=MemoryScope.SESSION,
    key="turn-1",
    text="globex confidential",
)


def cross_tenant_read():
    try:
        isolation.read(good, memory_id=foreign_entry.memory_id)
    finally:
        assert isolation.cross_scope_denials == 1, (
            f"the cross-scope counter did not increment (got "
            f"{isolation.cross_scope_denials})"
        )
        assert isolation.engine_cross_scope_violations == 1, (
            "engine.memory did not count the crossing, so the chat path bypassed it"
        )


expect_refused("cross-tenant read (tenant B entry, tenant A credential)",
               "conversation_scope_mismatch", cross_tenant_read)

# --- 2. the engine is still the refuser when the chat layer is bypassed ---
# MemoryIsolationError is engine.memory's own class, not a chat refusal, so it
# is provoked and counted explicitly rather than through expect_refused.
try:
    store.get(
        foreign_entry.memory_id,
        tenant_id=TENANT_A,
        agent_id=AGENT_A,
        session_id=CONV_1,
    )
except MemoryIsolationError:
    if store.stats()["cross_scope_violations"] != 2:
        mistakes.append(
            "engine bypass: engine.memory raised but did not count the crossing"
        )
    else:
        refused += 1
        print("  OK    REFUSED direct engine bypass — MemoryIsolationError")
except Exception as exc:  # noqa: BLE001 - any other outcome is a finding
    mistakes.append(f"engine bypass: raised {type(exc).__name__}: {exc}")
else:
    mistakes.append("engine bypass: ACCEPTED-BY-MISTAKE (the chat path can bypass it)")

# --- 3. foreign tenant in the body / query --------------------------------
def request(body=None, query=None):
    return ChatRequest(
        body=body or {},
        query=query or {},
        headers={"Cookie": "os-session-token=t", "X-AO-Chat-Client": CLIENT,
                 "X-Forwarded-Email": ADA},
    )


expect_refused(
    "foreign tenantId in the request body",
    "cross_tenant",
    lambda: assert_no_foreign_tenant(
        request(body={"conversationId": CONV_1, "tenantId": TENANT_B}), good
    ),
)
expect_refused(
    "foreign tenant in a query parameter",
    "cross_tenant",
    lambda: assert_no_foreign_tenant(request(query={"tenant": TENANT_B}), good),
)
expect_refused(
    "a tenant-B credential asserted as tenant A",
    "cross_tenant",
    lambda: assert_scope(rogue_tenant, tenant_id=TENANT_A),
)

# --- 4. client identity --------------------------------------------------
identity_map = IdentityMap.load()
expect_refused(
    "an unmapped external client identity",
    "unmapped_client_identity",
    lambda: identity_map.resolve(client=CLIENT, external_id=MALLORY),
)
expect_refused(
    "an absent client identity (no trusted header)",
    "unmapped_client_identity",
    lambda: identity_map.resolve_request(client=CLIENT, headers={}),
)
expect_refused(
    "an identity declared for two tenants",
    "ambiguous_client_identity",
    lambda: IdentityMap.from_payload(
        {
            "format": 1,
            "bindings": [
                {"client": CLIENT, "externalId": ADA, "tenantId": TENANT_A,
                 "agentId": AGENT_A, "role": "chat-user"},
                {"client": CLIENT, "externalId": ADA, "tenantId": TENANT_B,
                 "agentId": AGENT_B, "role": "chat-user"},
            ],
        }
    ).resolve(client=CLIENT, external_id=ADA),
)

# --- 5. credential lifecycle ---------------------------------------------
revocations = RevocationStore()
expect_refused(
    "a tampered credential signature",
    "invalid_credential",
    lambda: verify_chat_credential(
        good.token[:-2] + ("AA" if not good.token.endswith("AA") else "BB"),
        KEY,
        revocation_store=revocations,
        now=NOW + 1,
    ),
)
expect_refused(
    "a credential presented for another conversation",
    "conversation_scope_mismatch",
    lambda: verify_chat_credential(
        rogue_conv.token,
        KEY,
        revocation_store=revocations,
        conversation_id=CONV_1,
        now=NOW + 1,
    ),
)
revocations.revoke_jti(good.jti, NOW + 2)
expect_refused(
    "a revoked credential that is still unexpired",
    "session_revoked",
    lambda: verify_chat_credential(
        good.token, KEY, revocation_store=revocations, now=NOW + 3
    ),
)
expect_refused(
    "minting a credential with no signing key",
    "signing_key_required",
    lambda: mint_chat_credential(
        tenant_id=TENANT_A, agent_id=AGENT_A, conversation_id=CONV_1, signing_key=b""
    ),
)
expect_refused(
    "verifying with no revocation authority",
    "invalid_credential",
    lambda: verify_chat_credential(good.token, KEY, revocation_store=None, now=NOW + 1),
)
expect_refused(
    "a blank auth-gate session (no anonymous mode)",
    "credential_required",
    lambda: ChatFrontDoor(
        signing_key=KEY, revocation_store=revocations, trusted_keys={"kid": object()}
    ).verify_session("   "),
)

# --- 6. approvals --------------------------------------------------------
approval_store = ApprovalStore(clock=FakeClock(), rng=fake_rng("apr"))
router = ChatApprovalRouter(approval_store)
ran = []


def direct_write():
    try:
        router.propose(good, action="memory.erase", resource=good.container_id)
    except ChatError as pending:
        approval_id = pending.approval_id
    else:
        raise AssertionError("the proposal was not routed to the approval gate")
    router.execute(
        good,
        action="memory.erase",
        resource=good.container_id,
        approval_id=approval_id,
        perform=lambda: ran.append("executed"),
    )


expect_refused("a direct write with a pending approval", "direct_write_refused",
               direct_write)
assert ran == [], "the direct write ran anyway"
expect_refused(
    "a proposal on another tenant's resource",
    "cross_tenant",
    lambda: router.propose(
        good, action="memory.erase", resource=f"session:{TENANT_B}:{AGENT_B}:{CONV_2}"
    ),
)

# --- controls: the same code paths MUST succeed --------------------------
def _propose_and_swallow(router, cred):
    """Proposing is the *expected* path; ApprovalRequired is its outcome."""
    try:
        router.propose(cred, action="memory.erase", resource=cred.container_id)
    except ChatError as exc:
        if exc.code != "approval_required":
            raise


def _approved_execute(router, store, cred, ran):
    """An approved proposal runs exactly once and is consumed."""
    try:
        router.propose(cred, action="memory.erase", resource=cred.container_id)
    except ChatError as exc:
        approval_id = exc.approval_id
    store.decide(approval_id, approve=True, approver="root@acme.example")
    router.execute(
        cred,
        action="memory.erase",
        resource=cred.container_id,
        approval_id=approval_id,
        perform=lambda: ran.append("executed"),
    )


expect_accepted(
    "mint -> verify a credential for its own scope",
    lambda: verify_chat_credential(
        credential().token, KEY, revocation_store=RevocationStore(), now=NOW + 1
    ),
)
expect_accepted(
    "a mapped identity resolving to exactly one tenant",
    lambda: identity_map.resolve(client=CLIENT, external_id=ADA),
)
expect_accepted(
    "the correct container written and read back",
    lambda: ChatIsolation(InMemoryStore()).write(good, key="turn-1", text="hello"),
)
expect_accepted(
    "a request naming the credential's own tenant",
    lambda: assert_no_foreign_tenant(
        request(body={"conversationId": CONV_1, "tenantId": TENANT_A}), good
    ),
)
expect_accepted(
    "proposing an action routes into the approval gate",
    lambda: _propose_and_swallow(router, good),
)
expect_accepted(
    "an approved proposal executing once",
    lambda: _approved_execute(router, approval_store, good, ran),
)
expect_accepted(
    "the owning tenant reading its own entry back",
    lambda: store.get(
        foreign_entry.memory_id, tenant_id=TENANT_B, agent_id=AGENT_B, session_id=CONV_2
    ),
)


print(f"MUTANTS_REFUSED={refused}")
print(f"CONTROLS_ACCEPTED={controls}")
for mistake in mistakes:
    print(f"FINDING {mistake}")
raise SystemExit(1 if mistakes else 0)
PY
)"
probe_rc=$?
printf '%s\n' "$probe_log" | sed 's/^/  /'
mutants="$(printf '%s\n' "$probe_log" | sed -n 's/^MUTANTS_REFUSED=//p' | tail -1)"
controls="$(printf '%s\n' "$probe_log" | sed -n 's/^CONTROLS_ACCEPTED=//p' | tail -1)"

if printf '%s\n' "$probe_log" | grep -q 'BY-MISTAKE'; then
  echo "  FAIL  a refusal was accepted (or a control was refused) — see FINDING above" >&2
  fail=$((fail + 1))
fi
if [ "$probe_rc" -ne 0 ]; then
  echo "  FAIL  the probe driver exited $probe_rc" >&2
  fail=$((fail + 1))
fi
if [ "${mutants:-x}" != "$EXPECT_MUTANTS_REFUSED" ]; then
  echo "  FAIL  refused $mutants mutant(s), expected $EXPECT_MUTANTS_REFUSED" >&2
  fail=$((fail + 1))
fi
if [ "${controls:-x}" != "$EXPECT_CONTROLS_ACCEPTED" ]; then
  echo "  FAIL  accepted $controls control(s), expected $EXPECT_CONTROLS_ACCEPTED" >&2
  fail=$((fail + 1))
fi
if [ "$fail" -eq 0 ]; then
  echo "  OK    $mutants refusal(s) provoked and named, $controls control(s) accepted"
fi

if [ "$fail" -gt 0 ]; then
  echo "check-chat-identity: FAIL ($fail violation(s))" >&2
  exit 1
fi
echo "check-chat-identity: OK — scoped credentials verified by the merged verifier, tenant isolation enforced, every refusal provoked and every control live"
exit 0
