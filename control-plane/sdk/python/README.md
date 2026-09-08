# Consumer SDK (Python) — `aosdk`

Typed, **transport-injected** Python SDK for the platform surfaces a governed
consumer calls (issue `kushin77/agent-orchestrator#41`, parent EPIC-00 #4).
Part of the `control-plane/sdk` subtree. Doctrine: [`AGENTS.md`](../../../AGENTS.md),
[`docs/EXECUTION-PLAN.md`](../../../docs/EXECUTION-PLAN.md),
[`docs/ARCHITECTURE.md`](../../../docs/ARCHITECTURE.md),
[`docs/GOLDEN-RULES.md`](../../../docs/GOLDEN-RULES.md).
Contract overview + quickstart: [`../README.md`](../README.md).

- **Gateway client** — typed tasks + streaming (`POST /v1/agents/{agentId}/tasks`,
  issue #16 `gateway/proxy` envelope/outcome vocabulary).
- **Control-plane client** — usage, audit export, policy (issues #37 public edge
  + #38 control-plane REST envelope `{ok,status,requestId,data,error}`).
- **MCP client bootstrap** — JSON-RPC 2.0 against the tenant-scoped MCP tool
  gateway (issue #20 `gateway/mcp`: `initialize`/`ping`/`tools/list`/
  `tools/call` with `context.session`).
- **Auth** — short-lived, per-tenant session tokens from the
  `AGENTORCH_SESSION_TOKEN` env var or an injected callback; never a hardcoded key.

Everything is **offline by construction**: every client takes an injected
transport; the tests exercise the SDK end-to-end against offline platform
doubles (`tests/_fakes.py`) implementing the #37/#38 route + envelope shapes.
No SDK test touches the network and there are no third-party runtime deps
(Python 3 stdlib only).

## Package layout

```text
python/
├── README.md            # this file
├── aosdk/               # the importable package (add python/ to sys.path)
│   ├── __init__.py      # public surface
│   ├── auth.py          # short-lived session-token providers (env/callback)
│   ├── envelope.py      # control-plane envelope unwrap ({ok,...,error})
│   ├── errors.py        # closed error taxonomy (ApiError, McpError, ...)
│   ├── model.py         # typed models + closed outcome/claim vocabularies
│   ├── transport.py     # Transport/StreamTransport seams + stdlib HttpTransport
│   ├── gateway.py       # GatewayClient (typed dispatch + streaming)
│   ├── controlplane.py  # ControlPlaneClient (usage, audit export, policy)
│   └── mcp.py           # McpClient (JSON-RPC bootstrap)
└── tests/               # offline pytest suite (39 tests) + platform doubles
    ├── conftest.py
    ├── _fakes.py        # FakeGatewayBackend / FakeControlPlane / FakeMcpTransport
    ├── test_auth.py
    ├── test_gateway_client.py
    ├── test_control_plane_client.py
    ├── test_mcp_client.py
    └── test_end_to_end.py
```

## Auth (AC3 — short-lived tokens, no hardcoded keys)

The SDK consumes **session tokens** issued by the platform (console/session,
per tenant) — it never stores, generates or hardcodes keys.  A token is
provided by a `TokenSource`: an injected callback (which may refresh the
short-lived token on every call) or the `AGENTORCH_SESSION_TOKEN` env var.
Every request attaches `Authorization: Bearer <token>`; verification
(signature, revocation) belongs to the platform edge/control plane.  The SDK
reads only the claims it needs (tenant scoping + expiry) and fails closed when
a token is absent, malformed or expired.

```python
import os
from aosdk.auth import TokenSource, token_from_env

os.environ["AGENTORCH_SESSION_TOKEN"] = "<your short-lived session token>"
source = TokenSource()                       # reads AGENTORCH_SESSION_TOKEN
# or: source = TokenSource(callback=my_refreshing_provider)
```

## Quickstart (offline-verifiable)

Add the SDK directory to `sys.path`, then import:

```python
import sys
sys.path.insert(0, "control-plane/sdk/python")

from aosdk.auth import TokenSource
from aosdk.gateway import GatewayClient
from aosdk.controlplane import ControlPlaneClient
from aosdk.mcp import McpClient

source = TokenSource()   # AGENTORCH_SESSION_TOKEN (or a callback)

# 1) typed gateway dispatch (issue #16)
gateway = GatewayClient(my_http_transport, token_source=source)
result = gateway.run("coder-agent", "code-review-verdict", input_={"diff": "..."})
print(result.outcome, result.content)   # content is the schema-validated object

# 2) streaming dispatch (incremental events, then the terminal result)
for chunk in gateway.stream("coder-agent", "code-review-verdict", input_={"diff": "..."}):
    print(chunk)                        # DispatchEvent ... TaskResult

# 3) control-plane: usage, audit export, policy (issues #37/#38)
control = ControlPlaneClient(my_http_transport, token_source=source)
usage = control.my_usage()              # GET /v1/tenants/me/usage
audit = control.export_audit()          # GET /v1/tenants/me/audit/export
policies = control.list_policies()      # GET /v1/policies

# 4) MCP bootstrap (issue #20)
mcp = McpClient(my_http_transport, token_source=source)
mcp.initialize()
tools = mcp.list_tools()
reply = mcp.call_tool("kb.summary", {"module_id": "core"})
```

The offline doubles live in `tests/_fakes.py` and implement the exact route +
envelope shapes the SDK consumes, so the quickstart path is proven by
`tests/test_end_to_end.py` with zero network.

## Running the offline suite

```bash
# from the repo root
python3 -m pytest control-plane/sdk/python/tests -q -p no:cacheprovider
python3 -m pyflakes control-plane/sdk/python/aosdk control-plane/sdk/python/tests
```

Per the one-issue-one-lane doctrine this lane only adds files under
`control-plane/sdk/`, so the suite is not registered in
`scripts/pytest-suites.txt` (a foundation-owned file); `make verify` stays
green and the suite is exercised here directly.

## Provenance (cannibalized + adapted; GR-10)

See [`../README.md`](../README.md) for the full SDK provenance table.  The
Python client conventions (transport-injected typed client over a frozen
envelope) follow the merged `identity/cpapi/clients` (issue #38) shape and the
`saas-rbac` SDK conventions harvested under `.research/`.
