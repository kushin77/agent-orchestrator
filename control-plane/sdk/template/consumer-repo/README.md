# Consumer-repo template — governed agents in YOUR repository (issue #41)

A copy-and-run template that lets a tenant embed **governed agents** into their
own repository or app, **born-compliant**: the agent pack is declared, the
governed instruction layers are auto-synced from the platform, and a local
`make verify` gate enforces parity so the repo cannot silently drift out of
compliance.

> Part of the `control-plane/sdk` subtree (issue `kushin77/agent-orchestrator#41`,
> EPIC-00 #4).  Contract overview + the 5-minute quickstart:
> [`../../README.md`](../../README.md).  This directory IS the template a
> tenant copies into its own repo.

## What a tenant gets

```text
consumer-repo/                    <- copy this directory into your repository
├── AGENTS.md                     # instruction layering (wires all three layers)
├── Makefile                      # make verify / make sync
├── .gitignore
└── .agent-orchestrator/
    ├── pack.json                 # agent-pack manifest (ao.pack/v1)
    ├── sync.yaml                 # consumer-side sync wiring (ao.sync-config/v1)
    ├── instructions/
    │   ├── MANIFEST              # sha256 drift manifest (managed layers)
    │   ├── 00-platform.md        # governed platform layer (auto-synced)
    │   ├── 10-governed-agents.md # governed agent-pack layer (auto-synced)
    │   └── 20-local.md           # tenant-owned local layer (never synced)
    └── scripts/
        ├── verify.sh             # born-compliance gate (make verify)
        └── sync.sh               # sync wiring (--check offline, --fetch live)
```

## Instruction layering (acceptance criterion 2)

| Layer | Synced | Local edits | Purpose |
|---|---|---|---|
| `00-platform.md` | platform → repo | detected → fail | governed agent-operating rules |
| `10-governed-agents.md` | platform → repo | detected → fail | the declared agent pack + dispatch/MCP contract |
| `20-local.md` | never | free | your repository's own conventions |

The managed layers are **read-only at the consumer side**.  `make verify`
compares each managed file's sha256 against the `MANIFEST`; a drift (someone
edited an auto-synced file locally, or a sync is overdue) is a hard failure.
`make sync` refreshes the managed layers from the platform endpoint declared
in `sync.yaml` and regenerates the manifest.

## The 5-minute path

1. **Copy the template** into your repository:
   ```bash
   cp -r control-plane/sdk/template/consumer-repo/* <your-repo>/
   ```
2. **Declare your pack** — edit `.agent-orchestrator/pack.json` (id, version,
   upstream, agents, instruction paths).
3. **Point sync at your agent-org** — edit `.agent-orchestrator/sync.yaml`
   (`source.endpoint` + `auth.tokenEnv`).
4. **Verify born-compliance**:
   ```bash
   make verify
   ```
5. **Call your governed agents** with the consumer SDK (Python or TypeScript) —
   see the SDK quickstart in [`../../README.md`](../../README.md).  Every call
   carries a short-lived per-tenant session token (env/callback), never a key.

## Auth (acceptance criterion 3)

The session token is provided at runtime via the environment variable named by
`sync.yaml` → `auth.tokenEnv` (default `AGENTORCH_SESSION_TOKEN`) or an
injected callback.  Nothing in this template hardcodes or stores a credential.

## Offline behaviour

`make verify` and `make sync` (default `--check`) are fully offline: they
validate the manifests and local parity.  Only `make sync ARGS=--fetch`
performs a live refresh (requires network + the session token).

## Provenance

See [`../../README.md`](../../README.md).  This template adapts the
born-compliant module/consumer-repo patterns of the sources indexed there
(CMR module/shell templates + controller scaffolders, leaderboard
consumer-repo/hub-instance/prompts templates, shared-frontend module manifest
+ onboarding, CMR ADR-0002/ADR-0009).
