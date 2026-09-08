<!-- managed: ao-agent-pack — AUTO-SYNCED governed layer; do not edit locally (make sync overwrites). -->

# Agent-pack layer — the governed agents embedded in this repo

This layer describes the **agent pack** declared in `.agent-orchestrator/pack.json`.
It is auto-synced from your agent-org's pack registry; local edits are
detected by `make verify` and overwritten by `make sync`.

## Declared agents (pack manifest)

| Agent | Profile seed | Typical tasks |
|---|---|---|
| `coder-agent` | `coder` | code-author, code-review-verdict, test-author, test-run |
| `reviewer-agent` | `reviewer` | code-review, audit, summarize |

Each agent is scoped to exactly one tenant (the agent org) and may only use
the tools/actions its profile capability set allows.  The pack manifest is the
source of truth for what is declared in this repo.

## Calling the gateway from this repo

A governed consumer calls the platform dispatch surface with the consumer SDK
(`control-plane/sdk/python/aosdk` and the TypeScript twin).  Every call:

1. carries a short-lived per-tenant session token (env or callback — never a
   hardcoded key);
2. submits a **published task type** (no ad-hoc unversioned prompts);
3. receives a **typed result**: served outcomes carry schema-validated content;
   every other outcome is explicit (denied/blocked/failed/...).

```python
from aosdk.auth import TokenSource
from aosdk.gateway import GatewayClient

gateway = GatewayClient(transport, token_source=TokenSource())  # AGENTORCH_SESSION_TOKEN
result = gateway.run("coder-agent", "code-review-verdict", input_={"diff": "..."})
```

## MCP tools

Embedded agents may call the tenant-scoped MCP tool gateway for code/KB
indexing and platform tools (`tools/list`, `tools/call` with the session
token in `context.session`).  Unknown tools are never allowed (allowlist, not
denylist).
