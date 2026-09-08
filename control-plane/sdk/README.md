# control-plane/sdk — Consumer SDK + born-compliant templates

> Owner lane: **control-plane** (issue `kushin77/agent-orchestrator#41`,
> "Consumer SDK + born-compliant templates (TS/Python + MCP)", work item 37,
> phase 7). Parent: EPIC-00 (issue #4). Doctrine:
[`AGENTS.md`](../../AGENTS.md),
[`docs/EXECUTION-PLAN.md`](../../docs/EXECUTION-PLAN.md),
[`docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md),
[`docs/GOLDEN-RULES.md`](../../docs/GOLDEN-RULES.md),
[`docs/CANNIBALIZATION.md`](../../docs/CANNIBALIZATION.md).

This tree ships the **consumer SDK + born-compliant templates**: SDKs
(TypeScript + Python) for the surfaces a governed consumer calls — the model
gateway (typed tasks + streaming), the control-plane usage / audit / policy
API, and the MCP tool gateway — plus a **consumer-repo template** that lets a
tenant embed governed agents into their own repos/apps (born-compliant;
instructions auto-synced). One lane: this subtree is `control-plane/sdk/**`
only; every upstream contract below is **consumed, never redefined**.

Everything the SDK does is **offline by construction**: both SDKs are built on
an injectable transport, and the Python SDK is verified end-to-end with
`pytest` against offline platform doubles implementing the #37/#38 route +
envelope shapes.  No SDK test touches the network and there are no third-party
runtime dependencies.

## What ships here (acceptance criteria)

| Criterion | Where |
|---|---|
| TS + Python SDKs: gateway client (typed tasks, streaming), control-plane client (usage, audit export, policy), MCP client bootstrap | [`python/`](python/README.md) (`aosdk`) + [`typescript/`](typescript/README.md) |
| Consumer-repo template: instruction layering, `make verify`, pack manifest, sync wiring (consumer-side) | [`template/consumer-repo/`](template/consumer-repo/README.md) |
| SDK auth: short-lived tokens via console/session, per-tenant; no hardcoded keys | both SDK `auth` modules (`TokenSource`, env `AGENTORCH_SESSION_TOKEN` / callback) |
| Quickstart: 5-minute "governed agent in your repo" path | this README (§ Quickstart) + [`template/consumer-repo/README.md`](template/consumer-repo/README.md) |

## Tree layout

```text
control-plane/sdk/
├── README.md                    # this file — SDK contract + quickstart + provenance
├── python/                      # Python SDK (offline-verified gate)
│   ├── README.md
│   ├── aosdk/                   # importable package (stdlib only)
│   └── tests/                   # 39-test offline pytest suite + platform doubles
├── typescript/                  # TypeScript SDK (source-complete + type declarations)
│   ├── README.md                # (documents the offline limitation)
│   ├── package.json             # metadata only — no node_modules, zero runtime deps
│   ├── src/                     # typed source (strict TS)
│   └── types/index.d.ts         # hand-maintained type declarations
└── template/
    └── consumer-repo/           # copy-and-run governed consumer template
        ├── README.md
        ├── AGENTS.md            # instruction layering
        ├── Makefile             # make verify / make sync
        └── .agent-orchestrator/ # pack manifest + sync config + instructions
```

## Quickstart — a governed agent in your repo in 5 minutes

1. **Embed the consumer-repo template** in your repository
   (copy `template/consumer-repo/`), declare your pack in
   `.agent-orchestrator/pack.json` and point `sync.yaml` at your agent-org:
   ```bash
   make verify    # born-compliance gate (pack + sync config + layer parity)
   ```
2. **Get a short-lived session token** from your console/session (per tenant)
   and put it in the environment:
   ```bash
   export AGENTORCH_SESSION_TOKEN=<your short-lived session token>
   ```
3. **Call a governed agent** (Python — the offline-verified SDK):
   ```python
   import sys
   sys.path.insert(0, "control-plane/sdk/python")

   from aosdk.auth import TokenSource
   from aosdk.gateway import GatewayClient

   gateway = GatewayClient(my_transport, token_source=TokenSource())
   review = gateway.run("coder-agent", "code-review-verdict", input_={"diff": "..."})
   print(review.outcome, review.content)   # typed content on a served outcome
   ```
4. **Read usage + audit, call MCP tools** from the same repo:
   ```python
   from aosdk.controlplane import ControlPlaneClient
   from aosdk.mcp import McpClient

   control = ControlPlaneClient(my_transport, token_source=TokenSource())
   print(control.my_usage().total_tokens)
   print(len(control.export_audit()))

   mcp = McpClient(my_transport, token_source=TokenSource())
   mcp.initialize()
   print(mcp.call_tool("platform.whoami").text)
   ```

The exact same path is proven offline end-to-end in
`python/tests/test_end_to_end.py` (a runnable, network-free walkthrough), and
the TypeScript SDK mirrors it line-for-line.  The Python SDK is the
**offline-verified gate** for this issue; the TypeScript SDK is delivered
source-complete with a documented offline limitation (no `tsc`/`npm install`
assumed here) — see [`typescript/README.md`](typescript/README.md).

## Consumed contracts (frozen upstream — never redefined)

| Upstream | Consumed by the SDK |
|---|---|
| `gateway/proxy` (issue #16) | `POST /v1/agents/{agentId}/tasks` request fields (`tenantId/taskType/input/complexity/tokens/stream`), the `{status, result, record}` envelope, closed outcomes, typed `content` on served outcomes only, outcome→HTTP status, streaming `{event}` chunks |
| `identity/cpapi` (issue #38) | control-plane envelope `{ok,status,requestId,data,error}`; usage / audit / policy routes |
| `identity/edges` (issue #37) | public route surface the SDK calls (`/v1/tenants/me/usage`, `/v1/tenants/me/audit/export`, task dispatch) |
| `identity/sso` + `registry/service` (issues #35/#10) | session-token claim keys (`tenantId`, `sub`, `exp`, `role`, `allowedTools`, ...); the SDK decodes, the platform verifies |
| `gateway/mcp` (issue #20) | JSON-RPC 2.0 methods (`initialize` `2024-11-05`/`ping`/`tools/list`/`tools/call`), `context.session` + derived `context.tenantId`, text content items |

**Auth doctrine (AC3):** tokens are short-lived, per-tenant session tokens
from the platform; the SDK reads them from the `AGENTORCH_SESSION_TOKEN` env
var or an injected callback (`TokenSource`) and attaches
`Authorization: Bearer` — never a hardcoded key, and no cross-tenant fallback
(a caller cannot read another tenant; the platform enforces scope at the
edge/control plane).

## Verification

- Python SDK suite (offline): `python3 -m pytest control-plane/sdk/python/tests -q -p no:cacheprovider` → **39 passed**, `python3 -m pyflakes ...` → clean.  Proof: `control-plane/sdk/python/README.md` + the PR evidence.
- Consumer-repo template gate (offline): `bash template/consumer-repo/.agent-orchestrator/scripts/verify.sh` → **compliance: OK** (incl. a genuine negative: a drifted managed layer fails).
- Repo gate: `make verify` → green (this lane adds files only under `control-plane/sdk/`).

## Provenance (cannibalized + adapted; GR-10)

Sources verified on disk under `.research/` (read-only mirrors) before reuse;
nothing was copied verbatim — patterns were adapted and tightened around the
fail-closed / born-compliant doctrine (the full index lives in
[`docs/CANNIBALIZATION.md`](../../docs/CANNIBALIZATION.md)):

| Source (repo, path) | Pattern adapted | Where it landed |
|---|---|---|
| `CMR templates/module/` + `templates/shell/` + `templates/frontend/` + `controller/` scaffolders | born-compliant module template: manifest + gate + layering | `template/consumer-repo/` (pack manifest + `make verify`) |
| `leaderboard templates/consumer-repo/` + `templates/hub-instance/` + `templates/prompts/` | consumer-side config/gate/sync shape; prompt/instruction layering | `template/consumer-repo/` instructions + sync wiring |
| `shared-frontend module.json` (`cmr.module/v1`) + `docs/MODULE_ONBOARDING.md` | dual-role manifest + onboarding/sync discipline | `pack.json` (`ao.pack/v1`) + sync wiring |
| `CMR docs/decision-records/ADR-0002` (hybrid distribution) + `ADR-0009` (restricted external consumption) | distribution doctrine: consumer embeds a governed slice; external consumption is restricted and versioned | template + SDK auth (short-lived per-tenant tokens) |
| `saas-rbac` SDK conventions + merged `identity/cpapi/clients` (issue #38) | transport-injected typed client over a frozen envelope | Python + TS SDK client shape |
| merged contracts #16/#20/#35/#37/#38 | vocabulary consumed (see table above), never redefined | the whole SDK |
