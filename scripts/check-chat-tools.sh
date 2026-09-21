#!/usr/bin/env bash
# check-chat-tools.sh — the read-only enterprise tool family + the citations
# envelope (issue #504, EPIC #500; ADR-0012 / ADR-0014 / ADR-0018 / ADR-0023).
#
# Issue #504 adds ten grounded-chat reads over authorities this platform
# already owns. This gate proves the *controls bite* rather than merely
# existing, and it names every refusal it provokes:
#
#   * the declarations are the ones the family claims (eight new tools, the two
#     reused ids, the seven base declarations unchanged, the allowlist
#     vocabulary and the callable registry in agreement);
#   * the family reads REAL authorities (a live `.board/snapshot.json` read, a
#     live `ao.bridge/v1` manifest read and a live agent-roster read), so a
#     green result cannot come from a gate that exercises nothing;
#   * provocation A: a cross-tenant session is refused (403) and audited;
#   * provocation B: a caller-supplied tenant argument is refused — and the
#     control is proved sensitive by disabling the guard and watching the same
#     call succeed;
#   * provocation C: a fabricated source id is refused by the citations
#     envelope — proved sensitive the same way;
#   * provocation D: the declared-fake index is refused on a production path
#     (ADR-0018 §2/§3) while fixture mode still reaches it (so the control is
#     not passing merely because nothing is reachable);
#   * provocation E: an absent authority yields NO_DATA with a reason, never an
#     empty success (AO-GR-19);
#   * provocation F: no tool in the family writes a byte.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS (the family module is
# not importable, or the fixture authorities cannot be built, so the controls
# cannot be judged — an unjudgeable control is never a pass).
#
# Usage: bash scripts/check-chat-tools.sh
#
# ---knowledge---
# module_id: scripts.check-chat-tools
# system: governance
# app: gates
# solution_class: enterprise
# patterns: [tri-state-exit, provoked-negative-control, no-false-green, offline-hermetic, named-refusal, schema-validation]
# derives_from: null
# owner_sme: qa-sme
# tier: L1
# interfaces: [exit 0 OK, exit 1 NOT-OK, exit 2 CANNOT-ASSESS]
# invariants: ""
# gotchas: ""
# related: ["#500", "#504"]
# do_not_duplicate: null
# ---knowledge---
set -u

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-chat-tools: CANNOT-ASSESS - python3 not found" >&2
  exit 2
fi

for required in \
  gateway/mcp/enterprise.py \
  gateway/mcp/fixtures.py \
  gateway/mcp/grounding.py \
  gateway/mcp/sources.py \
  gateway/mcp/tools.py \
  gateway/mcp/model.py \
  gateway/mcp/kb.py \
  telemetry/ledger/store.py \
  engine/memory/prompt_cache.py \
  docs/decision-records/ADR-0018-codeidx-consumption-and-index-authority.md
do
  if [ ! -f "$root/$required" ]; then
    echo "check-chat-tools: CANNOT-ASSESS - $required is missing, so the family's declared authorities cannot be judged" >&2
    exit 2
  fi
done

exec python3 - "$root" <<'PY'
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

root = sys.argv[1]
sys.path.insert(0, os.path.join(root, "gateway"))
sys.path.insert(0, root)

try:
    from mcp import authn, enterprise, fixtures, grounding, sources
    from mcp.audit import HashChainAuditLog
    from mcp.authz import AuthzDecision
    from mcp.gateway import MCPToolGateway
    from mcp.model import MCP_ALLOWLIST_KEYS
    from mcp.protocol import AUTHZ_DENIED, INVALID_PARAMS
    from mcp.tools import build_registry
except Exception as exc:  # pragma: no cover - the CANNOT-ASSESS path
    sys.stderr.write(
        "check-chat-tools: CANNOT-ASSESS - the gateway/mcp authority is not "
        "importable (%r), so no control can be judged\n" % (exc,)
    )
    raise SystemExit(2)

fail = 0


def ok(message):
    print("  OK    %s" % message)


def note_fail(message):
    global fail
    fail += 1
    print("  FAIL  %s" % message, file=sys.stderr)


def cand(label, outcome):
    """Check one outcome: ``(good, detail)``; a FAIL names the control."""
    good, detail = outcome
    if good:
        ok("%s: %s" % (label, detail))
    else:
        note_fail("%s: %s" % (label, detail))
    return good


class _AllowAll:
    """An authz guard that allows every call (isolates the controls under test)."""

    def authorize(self, session, permission):
        return AuthzDecision(allowed=True, permission=permission)


SIGNING_KEY = b"\x00" * 32


def mint(tenant, agent, tools):
    session = authn.mint_session(
        tenant, agent, SIGNING_KEY, allowed_tools=tuple(tools), ttl_seconds=3600
    )
    return authn.session_to_token(session, SIGNING_KEY)


def tree_state(target):
    state = {}
    for base, _dirs, files in os.walk(str(target)):
        for name in files:
            path = os.path.join(base, name)
            with open(path, "rb") as handle:
                state[os.path.relpath(path, target)] = hashlib.sha256(
                    handle.read()
                ).hexdigest()
    return state


try:
    work = Path(tempfile.mkdtemp(prefix="ao504-gate-"))
    fixtures.write_fixture_authorities(work)
    fake_backend = sources.KbFixtureSource(str(work))._backend_or_none()
except Exception as exc:  # pragma: no cover - the CANNOT-ASSESS path
    sys.stderr.write(
        "check-chat-tools: CANNOT-ASSESS - the hermetic authorities cannot be "
        "built (%r); the fixture ledger chain is written by the ledger's own "
        "store, so no control can be judged without it\n" % (exc,)
    )
    raise SystemExit(2)

if fake_backend is None:  # pragma: no cover - the CANNOT-ASSESS path
    sys.stderr.write(
        "check-chat-tools: CANNOT-ASSESS - the declared fake index backend "
        "(gateway/mcp/kb.py) is not importable, so provocation D cannot be judged\n"
    )
    raise SystemExit(2)

catalog = fixtures.fixture_catalog(work)
audit = HashChainAuditLog()
gateway = MCPToolGateway(
    build_registry(catalog), authz=_AllowAll(), audit=audit, signing_key=SIGNING_KEY
)
acme = mint("acme", "agent-a", enterprise.CHAT_FAMILY_NAMES)

# --------------------------------------------------------------------------- #
print("== declarations ==")
# --------------------------------------------------------------------------- #
registry = build_registry()
names = set(registry.names())
cand(
    "the family",
    (
        len(enterprise.ENTERPRISE_TOOL_NAMES) == 8
        and set(enterprise.ENTERPRISE_TOOL_NAMES) <= names,
        "eight new read-only tools are declared and registered",
    ),
)
cand(
    "the reused pair",
    (
        set(enterprise.REUSED_READ_TOOLS) == {"kb.query", "kb.freshness"} <= names,
        "kb.query / kb.freshness are the issue #20 declarations, not re-declared",
    ),
)
cand(
    "the allowlist vocabulary",
    (
        set(MCP_ALLOWLIST_KEYS) == names,
        "MCP_ALLOWLIST_KEYS and the callable registry name the same %d tools"
        % len(names),
    ),
)
cand(
    "the base declarations",
    (
        registry.require("platform.whoami").description
        == "Return the resolved tenant context of the current session (identity echo)."
        and registry.require("kb.summary").description
        == "Counts + shape of this tenant's KB graph snapshot.",
        "the issue #20 declarations are unchanged (composed beside, never rewritten)",
    ),
)
cand(
    "the argument vocabulary",
    (
        all(
            not (set(schema["properties"]) & set(sources.TENANT_ARGUMENT_KEYS))
            for schema in enterprise.enterprise_schemas().values()
        ),
        "no tool declares a tenant-selector argument",
    ),
)
cand(
    "the write vocabulary",
    (
        enterprise.WRITE_TOOLS == (),
        "WRITE_TOOLS is empty: the family has no write path (ADR-0023)",
    ),
)

# --------------------------------------------------------------------------- #
print("== the family reads real authorities (the gate is not vacuous) ==")
# --------------------------------------------------------------------------- #
live = sources.SourceCatalog.from_repo_root(root, mode=sources.MODE_PRODUCTION)
board = live.call("board", "search", state="OPEN", limit=5)
cand(
    "a live board read",
    (
        board.is_ok and len(board.fragments) >= 1,
        "read %d ticket(s) from .board/snapshot.json at revision %s"
        % (len(board.fragments), board.fragments[0].revision if board.is_ok else "-"),
    ),
)
fleet = live.call("fleet", "snapshot")
cand(
    "a live bridge read",
    (
        fleet.is_ok and len(fleet.fragments) >= 4,
        "read %d ao.bridge/v1 family row(s), each with its own revision"
        % len(fleet.fragments),
    ),
)
agents = live.call("registry", "list")
cand(
    "a live registry read",
    (
        agents.is_ok and len(agents.fragments) >= 5,
        "read %d agent profile(s) through the versioned bridge family"
        % (len(agents.fragments) if agents.is_ok else 0),
    ),
)

# --------------------------------------------------------------------------- #
print("== provocation A: a cross-tenant session is refused ==")
# --------------------------------------------------------------------------- #
before = len(audit)
cross = gateway.call_tool("fleet.snapshot", {}, session_token=acme, tenant_id="globex")
denials = [
    event
    for event in audit.events()[before:]
    if event["event"] == "tool_call_denied" and event["status"] == "authz"
]
cand(
    "the cross-tenant refusal",
    (
        cross.get("error", {}).get("code") == AUTHZ_DENIED
        and len(denials) == 1
        and denials[0]["tenantId"] == "acme",
        "403 (%s) and exactly one audited tool_call_denied/authz record"
        % cross.get("error", {}).get("code"),
    ),
)

# --------------------------------------------------------------------------- #
print("== provocation B: a caller-supplied tenant argument is refused ==")
# --------------------------------------------------------------------------- #
tenant_arg = gateway.call_tool(
    "ledger.tail", {"limit": 5, "tenant": "globex"}, session_token=acme
)
cand(
    "the tenant-argument refusal",
    (
        tenant_arg.get("error", {}).get("code") == INVALID_PARAMS
        and "refuses a caller-supplied tenant selector"
        in tenant_arg["error"]["message"],
        "refused by name: %s" % tenant_arg.get("error", {}).get("message", "")[:80],
    ),
)

# …and the control is sensitive: with the guard disabled the call lands.
original_validate = enterprise._validate_arguments
try:
    enterprise._validate_arguments = lambda tool, arguments, declared: {
        keyword: arguments[name]
        for name, (keyword, _kind) in declared.items()
        if name in arguments
    }
    mutated = gateway.call_tool(
        "ledger.tail", {"limit": 5, "tenant": "globex"}, session_token=acme
    )
finally:
    enterprise._validate_arguments = original_validate
cand(
    "the tenant-argument control bites",
    (
        "error" not in mutated,
        "with the argument guard disabled the same call is accepted, so the "
        "refusal above is the guard's doing (a control that cannot fail is a "
        "formality)",
    ),
)

# --------------------------------------------------------------------------- #
print("== provocation C: a fabricated source id is refused ==")
# --------------------------------------------------------------------------- #
fabricated = grounding.CitationsEnvelope(
    citations=(
        grounding.Citation(
            source_id="board:#504@deadbeefdeadbeef",
            family="board",
            authority=".board/snapshot.json",
            revision="deadbeefdeadbeef",
            kind="ticket",
        ),
    )
)


def fabrication_refused():
    try:
        fabricated.validate(["board:#504@c0ffee00c0ffee00"])
    except grounding.CitationError:
        return True
    return False


cand(
    "the fabricated-citation refusal",
    (
        fabrication_refused(),
        "a citation naming a source the turn never read is refused by name",
    ),
)
original_check = grounding.CitationsEnvelope.validate
try:
    grounding.CitationsEnvelope.validate = lambda self, known: None
    sensitive = not fabrication_refused()
finally:
    grounding.CitationsEnvelope.validate = original_check
cand(
    "the fabricated-citation control bites",
    (
        sensitive,
        "with the validator neutered the fabrication is accepted, so the refusal "
        "above is the validator's doing",
    ),
)

# the model half of the same control
turn = grounding.GroundingAssembler(catalog).assemble(
    grounding.GroundingRequest(
        delta="What is the state of ticket 504?",
        needs=(grounding.Need("board", "ticket", "#504", (("number", 504),)),),
    )
)
ungranted_refused = False
try:
    turn.verify_response(["budget:acme@0000000000000000"])
except grounding.CitationError:
    ungranted_refused = True
cand(
    "the model is held to what it was given",
    (
        ungranted_refused and turn.status == sources.STATUS_OK,
        "a response citing a source it was not given is refused, while the turn's "
        "own cited fragment verifies",
    ),
)

# --------------------------------------------------------------------------- #
print("== provocation D: the declared fake index is refused on a live path ==")
# --------------------------------------------------------------------------- #
production = fixtures.fixture_catalog(work, mode=sources.MODE_PRODUCTION)
fake = production.call("kb-fixture", "query", repo="acme/payments")
fixture_mode = fixtures.fixture_catalog(work, mode=sources.MODE_FIXTURE)
reachable = fixture_mode.call("kb-fixture", "query", repo="acme/payments")
cand(
    "the declared-fake refusal",
    (
        fake.is_no_data
        and "fixture-only" in fake.reason
        and "ADR-0018" in fake.reason
        and reachable.is_ok,
        "production refuses the declared fake (fixture-only / ADR-0018) while "
        "fixture mode still reaches it",
    ),
)
real = live.call("codeidx", "query")
cand(
    "the real code index is declared unreachable",
    (
        real.is_no_data and "kushin77/code-indexing" in real.reason,
        "the real compiler-accurate index is reported NO_DATA by name, not "
        "answered from a local mirror",
    ),
)

# --------------------------------------------------------------------------- #
print("== provocation E: an absent authority is NO_DATA, never an empty success ==")
# --------------------------------------------------------------------------- #
missing = gateway.call_tool("ticket.get", {"number": 999999}, session_token=acme)
payload = (
    json.loads(missing["result"]["content"][0]["text"]) if "result" in missing else {}
)
cand(
    "the NO_DATA answer",
    (
        payload.get("status") == sources.STATUS_NO_DATA
        and payload.get("count") == 0
        and payload.get("fragments") == []
        and bool(str(payload.get("reason", "")).strip())
        and missing.get("result", {}).get("isError") is False,
        "status=NO_DATA with a reason naming the absence (never an empty ok)",
    ),
)
empty_sources = sources.SourceCatalog.from_repo_root(
    str(Path(work) / "empty"), mode=sources.MODE_PRODUCTION
)
empty_turn = grounding.GroundingAssembler(empty_sources).assemble(
    grounding.GroundingRequest(
        delta="anything",
        needs=(grounding.Need("board", "ticket", "#1", (("number", 1),)),),
    )
)
cand(
    "the grounding NO_DATA",
    (
        empty_turn.status == sources.STATUS_NO_DATA
        and bool(empty_turn.why())
        and "[NO_DATA]" in empty_turn.static_text,
        "a turn over an absent authority reports NO_DATA and says why",
    ),
)

# --------------------------------------------------------------------------- #
print("== provocation F: no tool in the family writes ==")
# --------------------------------------------------------------------------- #
arguments = {
    "ticket.get": {"number": 504},
    "ticket.search": {"state": "OPEN"},
    "budget.status": {},
    "ledger.tail": {"limit": 2},
    "ledger.verify": {},
    "agent.list": {},
    "agent.status": {"agent_id": "claude"},
    "fleet.snapshot": {},
}
before_state = tree_state(work)
errors = [
    tool
    for tool, args in sorted(arguments.items())
    if "error" in gateway.call_tool(tool, args, session_token=acme)
]
after_state = tree_state(work)
cand(
    "the read-only family",
    (
        not errors and after_state == before_state,
        "all %d tools answered and the authority tree is byte-identical afterwards"
        % len(arguments),
    ),
)
cand(
    "the audit ledger",
    (
        audit.verify()[0] == len(audit) and len(audit) >= 2,
        "%d hash-chained tool-call record(s), chain verified" % len(audit),
    ),
)

if fail:
    print("check-chat-tools: FAIL (%d violation(s))" % fail, file=sys.stderr)
    raise SystemExit(1)
print(
    "check-chat-tools: OK - the read-only enterprise family is declared, "
    "tenant-scoped and audited; the fake index is refused on a live path; two "
    "refusals are mutation-proved sensitive; no tool writes"
)
raise SystemExit(0)
PY
