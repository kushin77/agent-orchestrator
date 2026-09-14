#!/usr/bin/env bash
# check-codeidx-backend.sh — the flag-gated real codeidx backend gate (issue #476,
# EPIC #472, ADR-0018).
#
# The declared in-memory graph is the OFFLINE FIXTURE, not a silent production
# answer; the real kushin77/code-indexing backend is a flag-gated OPTION that
# serves the same IndexBackend protocol (definitions / references / search /
# query / freshness) unchanged. A seam nothing validates is a formality
# (no-false-green doctrine, GR-12), so this gate fails, BY NAME, on each way the
# seam stops being honest:
#
#   * the flag does not default OFF                          -> names the flag anchor;
#   * a fidelity note is wrong for its path (a real answer mislabelled
#     "fake", or a degraded answer mislabelled "real")        -> names the note;
#   * a declared tool is not served from the real backend     -> names the tool;
#   * a degraded answer does not say so (silent fallback)     -> names the field;
#   * a tenant with no real index returns another's rows      -> names the field;
#   * a required negative control is not refused              -> names the control.
#
# It runs its own negative control: it strips one tool's recorded fixture entry
# and requires the validator to refuse it, naming that tool. A check that cannot
# fail is a formality, so if the mutant passes this gate reports FAIL.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS (an unimportable gateway
# or a missing fixture must never be reported as a pass, and never aggregated
# into one).
#
# Offline and deterministic: stdlib only (plus the gateway itself). No network,
# no running indexer — the recorded fixture stands in for the indexer, so the
# live path is never exercised here. Usage: bash scripts/check-codeidx-backend.sh
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

kb_module="gateway/mcp/kb.py"
gateway_dir="gateway"
fixture="gateway/mcp/tests/fixtures/codeidx_recorded.json"
test_module="gateway/mcp/tests/test_mcp_codeidx_backend.py"

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-codeidx-backend: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

# The seam itself is what is being assessed: an absent module or fixture is
# CANNOT-ASSESS (2), never a pass.
for f in "$kb_module" "$fixture" "$test_module"; do
  if [ ! -f "$f" ]; then
    echo "check-codeidx-backend: CANNOT-ASSESS — $f is missing" >&2
    exit 2
  fi
done

# validate <root> <fixture> -> 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS
validate() {
  python3 - "$@" <<'PY'
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
fixture_path = pathlib.Path(sys.argv[2])
sys.path.insert(0, str(root / "gateway"))

findings = []
cannot_assess = []

# The flag must be assessed with the environment cleared (an operator's env
# must not flip the gate's own assertion).
_flag_env = "AO_MCP_CODEIDX_ENABLED"
import os  # noqa: E402

os.environ.pop(_flag_env, None)

try:
    from mcp import authn
    from mcp.authz import AuthzDecision
    from mcp.gateway import MCPToolGateway
    from mcp import kb
    from mcp.tools import build_registry
except Exception as exc:  # noqa: BLE001 - any import failure is CANNOT-ASSESS
    print(f"  CANNOT-ASSESS  gateway/mcp is not importable ({exc})", file=sys.stderr)
    raise SystemExit(2)

try:
    records = json.loads(fixture_path.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError) as exc:
    print(f"  CANNOT-ASSESS  recorded fixture unreadable ({exc})", file=sys.stderr)
    raise SystemExit(2)

if not isinstance(records, dict) or not records:
    print("  CANNOT-ASSESS  recorded fixture is empty", file=sys.stderr)
    raise SystemExit(2)


def seed_declared(*, enabled):
    registry = kb.KbRegistry(codeidx_enabled=enabled)
    tenant_kb = kb.TenantKb(tenant_id="acme")
    index = kb.RepoIndex(
        repo="acme/payments",
        modules=["payments.api", "payments.core"],
        commit="abc123",
        indexed_at="2026-09-08T00:00:00Z",
    )
    index.add_symbol(
        kb.Symbol(
            name="charge",
            repo="acme/payments",
            kind="function",
            path="src/payments/core.py",
            line=12,
        )
    )
    tenant_kb.add_repo(index)
    registry.put(tenant_kb)
    return registry


# --- A. the flag defaults OFF (GR-5) ----------------------------------------
if kb.DEFAULT_CODEIDX_ENABLED is not False:
    findings.append(
        "the codeidx backend flag does not default OFF (anchor "
        "'DEFAULT_CODEIDX_ENABLED' is not False)"
    )
if kb.env_codeidx_enabled({}) is not False:
    findings.append(
        "env_codeidx_enabled({}) does not default OFF (anchor 'AO_MCP_CODEIDX_ENABLED')"
    )
if kb.KbRegistry().codeidx_enabled is not False:
    findings.append("KbRegistry() does not default OFF (the declared path is not the default)")

# --- B. fidelity notes are accurate and distinct per path -------------------
if len({kb.FIDELITY_NOTE, kb.CODEIDX_FIDELITY_NOTE, kb.DEGRADED_FIDELITY_NOTE}) != 3:
    findings.append("the three fidelity notes are not distinct (paths cannot be told apart)")
if "fake" not in kb.FIDELITY_NOTE:
    findings.append("the declared fidelity note no longer says the index is fake (field 'fidelity_note')")
if "real" not in kb.CODEIDX_FIDELITY_NOTE:
    findings.append("the real-index fidelity note does not say the index is real (field 'fidelity_note')")
if "unreachable" not in kb.DEGRADED_FIDELITY_NOTE:
    findings.append("the degraded fidelity note does not name the unreachable indexer (field 'fidelity_note')")

# --- C. every declared tool is served from the real backend -----------------
real_registry = seed_declared(enabled=True)
real_registry.opt_in("acme", kb.RecordedCodeidxClient(records))
real_backend = real_registry.backend_for("acme")
real_calls = {
    "definitions": lambda b: b.definitions("charge"),
    "references": lambda b: b.references("charge"),
    "search": lambda b: b.search("post"),
    "query": lambda b: b.query(repo="acme/payments"),
    "freshness": lambda b: b.freshness("acme/payments"),
}
for tool in kb.CODEIDX_TOOLS:
    body = real_calls[tool](real_backend)
    if body.get("source") != kb.SOURCE_CODEIDX:
        findings.append(
            f"tool '{tool}' is not served from the real codeidx backend "
            f"(field 'source' is {body.get('source')!r})"
        )
    elif body.get("fidelity_note") != kb.CODEIDX_FIDELITY_NOTE:
        findings.append(f"tool '{tool}' carries the wrong fidelity note for the real path")

# --- D. the declared fixture is the default answer --------------------------
default_body = seed_declared(enabled=False).backend_for("acme").definitions("charge")
if default_body.get("source") != kb.SOURCE_DECLARED:
    findings.append("the declared fixture is not the default answer source (field 'source')")
if default_body.get("fidelity_note") != kb.FIDELITY_NOTE:
    findings.append("a declared answer carries the wrong fidelity note (field 'fidelity_note')")

# --- E. an unreachable indexer degrades EXPLICITLY and says so --------------
degraded_registry = seed_declared(enabled=True)
degraded_registry.opt_in(
    "acme", kb.RecordedCodeidxClient(records, unavailable=("definitions",))
)
degraded_body = degraded_registry.backend_for("acme").definitions("charge")
if degraded_body.get("degraded") is not True:
    findings.append("an unreachable indexer does not mark the answer degraded (field 'degraded')")
elif degraded_body.get("source") != kb.SOURCE_DECLARED:
    findings.append("a degraded answer does not name the declared fixture (field 'source')")
elif "definitions" not in str(degraded_body.get("degradeReason")):
    findings.append("a degraded answer does not name the unreachable tool (field 'degradeReason')")
elif degraded_body.get("fidelity_note") != kb.DEGRADED_FIDELITY_NOTE:
    findings.append("a degraded answer carries the wrong fidelity note (field 'fidelity_note')")

# --- F. no cross-tenant fallback --------------------------------------------
shared_registry = seed_declared(enabled=True)
shared_registry.opt_in("globex", kb.RecordedCodeidxClient(records))
acme_backend = shared_registry.backend_for("acme")
if not isinstance(acme_backend, kb.MemoryKbBackend):
    findings.append("a tenant with no real index did not stay on the declared fixture (cross-tenant fallback)")
if acme_backend.search("post").get("count") != 0:
    findings.append("a tenant with no real index returned another tenant's rows (field 'count')")

# --- G. the five required negative controls, through the real gateway -------
SIGNING_KEY = bytes(range(32))
TOOL_IDS = (
    "code.definitions",
    "code.references",
    "code.search",
    "kb.query",
    "kb.freshness",
    "kb.summary",
)


class AllowAll:
    def authorize(self, session, permission):
        return AuthzDecision(allowed=True, permission=permission)


class DenyPermission:
    def authorize(self, session, permission):
        return AuthzDecision(
            allowed=False, permission=permission, reason="permission", code="denied"
        )


class CountingClient(kb.RecordedCodeidxClient):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.calls = 0

    def call_tool(self, name, arguments):
        self.calls += 1
        return super().call_tool(name, arguments)


def token(allowed=TOOL_IDS, tenant="acme"):
    session = authn.mint_session(tenant, "code-agent", SIGNING_KEY, allowed_tools=tuple(allowed))
    return authn.session_to_token(session, SIGNING_KEY)


def enabled_registry(client=None):
    registry = seed_declared(enabled=True)
    registry.opt_in("acme", client if client is not None else kb.RecordedCodeidxClient(records))
    return registry


gateway = MCPToolGateway(
    build_registry(), kb_registry=enabled_registry(), authz=AllowAll(), signing_key=SIGNING_KEY
)

# 1. unknown tool -> -32601, naming the tool.
unknown = gateway.call_tool("kb.delete_everything", {}, session_token=token())
if unknown.get("error", {}).get("code") != -32601:
    findings.append("control 1: an unknown tool is not refused with -32601 (field 'name')")
elif "kb.delete_everything" not in unknown["error"]["message"]:
    findings.append("control 1: the unknown-tool refusal does not name the tool")

# 2. malformed envelope -> -32600.
malformed = gateway.handle_message(["not", "an", "object"])
if malformed is None or malformed.get("error", {}).get("code") != -32600:
    findings.append("control 2: a malformed envelope is not refused with -32600 (field 'jsonrpc')")
elif "JSON object" not in malformed["error"]["message"]:
    findings.append("control 2: the malformed-envelope refusal does not name the envelope")

# 3. cross-tenant (no fallback), naming the tenant.
cross = gateway.call_tool("kb.summary", {}, session_token=token(), tenant_id="globex")
if cross.get("error", {}).get("code") != -32003:
    findings.append("control 3: a cross-tenant session is not refused (field 'tenantId')")
elif "globex" not in cross["error"]["message"]:
    findings.append("control 3: the cross-tenant refusal does not name the tenant")

# 4. indexer unreachable -> degrade and say so, never invent.
gw_unreachable = MCPToolGateway(
    build_registry(),
    kb_registry=enabled_registry(kb.RecordedCodeidxClient(records, unavailable=("definitions",))),
    authz=AllowAll(),
    signing_key=SIGNING_KEY,
)
unreachable = gw_unreachable.call_tool(
    "code.definitions", {"symbol": "charge"}, session_token=token()
)
try:
    unreachable_body = json.loads(unreachable["result"]["content"][0]["text"])
except (KeyError, IndexError, json.JSONDecodeError):
    unreachable_body = {}
if unreachable_body.get("degraded") is not True or unreachable_body.get("source") != kb.SOURCE_DECLARED:
    findings.append("control 4: an unreachable indexer did not degrade explicitly (field 'degraded')")
elif "definitions" not in str(unreachable_body.get("degradeReason")):
    findings.append("control 4: the degradation does not name the unreachable tool (field 'degradeReason')")

# 5. authorized=false -> denied, and the indexer is never reached.
counting = CountingClient(records)
gw_denied = MCPToolGateway(
    build_registry(), kb_registry=enabled_registry(counting), authz=DenyPermission(),
    signing_key=SIGNING_KEY,
)
denied = gw_denied.call_tool("code.search", {"q": "charge"}, session_token=token())
if denied.get("error", {}).get("code") != -32003:
    findings.append("control 5: an unauthorized call is not refused (field 'authorized')")
elif denied["error"].get("data", {}).get("reason") != "permission":
    findings.append("control 5: the denial does not preserve the permission cause (field 'reason')")
elif counting.calls != 0:
    findings.append("control 5: the real indexer was reached before authorization (a second enforcement path)")

# --- verdict ----------------------------------------------------------------
if cannot_assess:
    for finding in cannot_assess:
        print(f"  CANNOT-ASSESS  {finding}", file=sys.stderr)
    raise SystemExit(2)

if findings:
    for finding in findings:
        print(f"  FAIL  {finding}", file=sys.stderr)
    raise SystemExit(1)

print(
    "  OK    flag OFF by default; all "
    f"{len(kb.CODEIDX_TOOLS)} declared tool(s) served from the real backend; "
    "per-path fidelity notes accurate; degradation explicit; 5 negative controls refused"
)
raise SystemExit(0)
PY
}

rc=0
validate "$root" "$fixture" || rc=$?
case "$rc" in
  0) : ;;
  1)
    echo "check-codeidx-backend: FAIL — the codeidx backend seam is not honest in full" >&2
    exit 1
    ;;
  2)
    echo "check-codeidx-backend: CANNOT-ASSESS — the seam could not be read" >&2
    exit 2
    ;;
  *)
    echo "check-codeidx-backend: CANNOT-ASSESS — validator returned $rc" >&2
    exit 2
    ;;
esac

# --- the codeidx test module must pass (offline, recorded fixture) ----------
echo "== pytest gateway/mcp/tests/test_mcp_codeidx_backend.py =="
if ! python3 -m pytest "$test_module" -q -p no:cacheprovider; then
  echo "check-codeidx-backend: FAIL — the codeidx backend test module is red" >&2
  exit 1
fi

# --- internal negative control ----------------------------------------------
# Strip one tool's recorded fixture entry and require the validator to refuse it,
# naming that tool. A gate that cannot fail is a formality (GR-12).
work="/tmp/ao476-codeidx.$$.$(date +%s)"
if ! mkdir "$work" 2>/dev/null; then
  echo "check-codeidx-backend: CANNOT-ASSESS — cannot create a scratch directory" >&2
  exit 2
fi
trap 'rm -rf "$work"' EXIT

mutant="$work/codeidx_recorded.mutant.json"
if ! python3 - "$fixture" "$mutant" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    records = json.load(handle)
records.pop("definitions", None)
with open(sys.argv[2], "w", encoding="utf-8") as handle:
    json.dump(records, handle, indent=2)
    handle.write("\n")
PY
then
  echo "check-codeidx-backend: CANNOT-ASSESS — could not build the negative control" >&2
  exit 2
fi

if ! python3 - "$fixture" "$mutant" <<'PY'
import hashlib
import sys

a = open(sys.argv[1], "rb").read()
b = open(sys.argv[2], "rb").read()
if a == b or hashlib.sha256(a).hexdigest() == hashlib.sha256(b).hexdigest():
    raise SystemExit(1)
PY
then
  echo "check-codeidx-backend: FAIL — negative-control mutation changed nothing" >&2
  exit 1
fi

mutant_out="$(validate "$root" "$mutant" 2>&1)"
mutant_rc=$?
if [ "$mutant_rc" -eq 1 ] && printf '%s\n' "$mutant_out" | grep -q "tool 'definitions' is not served from the real codeidx backend"; then
  echo "  OK    negative control: a stripped fixture entry is refused and named"
  echo "check-codeidx-backend: OK — the codeidx backend seam is flag-gated, labelled, and refused by name when broken"
  exit 0
fi

echo "check-codeidx-backend: FAIL — negative control passed; a stripped fixture entry was not refused by name" >&2
printf '%s\n' "$mutant_out" >&2
exit 1
