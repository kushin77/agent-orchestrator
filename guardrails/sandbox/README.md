# guardrails/sandbox — Sandboxed agent tool execution (per-category security profiles)

Issue **#58** (EPIC-00, Phase 4, guardrails pillar; follow-up to spike **#48**).
Every agent tool call (file/exec/network/db/cloud/git/docker/k8s) runs under a
security profile selected **per tool category** - `restricted` / `standard` /
`privileged` - with a **fail-closed default of `restricted`** for any unknown
category. The contract is declared as JSON Schema and instantiated as YAML; a
`SandboxExecutor` resolves the per-category profile and dispatches to an
**injectable runtime** (an offline enforcement model in tests/CI; real Docker
and Firecracker runtimes are DECLARED behind a flag that ships OFF). An
additive seam wires the sandbox into the MCP tool gateway (`gateway/mcp`,
issue #20) so tool Execute routes through the sandbox and is audited.

Everything here is **offline** (stdlib + PyYAML), lives strictly under
`guardrails/sandbox/**`, and is **adapted - not copied -** from the elevatedIQ
`services/ai-chatbot-orchestrator/internal/sandbox` pattern (see
[Provenance](#8-provenance)). No docker daemon, no Firecracker, no network.

## 1. The contract (tool categories → security profile)

### 1.1 Security profiles (`profiles.yaml`, schema `profiles.schema.json`)

| Profile | rootfs | no_new_privs | capDrop | network | user | CPU quota | mem | PIDs | timeout |
|---|---|---|---|---|---|---|---|---|---|
| `restricted` | read-only | on | `ALL` | `none` | `1000:1000` | 50000 µs (50%) | 512 MiB | 100 | 30 s |
| `standard` | read-only | on | `ALL` | `bridge` | `1000:1000` | 100000 µs (100%) | 1024 MiB | 200 | 60 s |
| `privileged` | writable | on | `SYS_ADMIN`, `SYS_PTRACE` | `host` | `root` | 200000 µs (200%) | 2048 MiB | 500 | 120 s |

`cpuQuota` is the Linux CFS CPU quota in microseconds per 100 ms period. Every
profile field is validated against `security-profile.schema.json`
(`additionalProperties: false`, closed `networkMode` enum, positive quotas).

### 1.2 Category map (`categories.yaml`, schema `categories.schema.json`)

| Category | Profile | Rationale |
|---|---|---|
| `file` | `restricted` | file ops: no network, read-only rootfs |
| `exec` | `restricted` | command execution: no network, read-only rootfs |
| `network` | `standard` | needs network via a private bridge |
| `db` | `standard` | database tools |
| `cloud` | `standard` | cloud tools (need network) |
| `git` | `standard` | git operations (need network) |
| `docker` | `privileged` | needs the docker socket |
| `k8s` | `standard` | kubernetes tools |
| *(unknown)* | `restricted` | **fail closed** - unknown never means less safe |

A tool whose category is **not** in the closed map (or cannot be determined)
resolves to `defaultProfile: restricted` - the elevatedIQ `GetProfileForCategory`
fail-closed behaviour.

## 2. Directory layout

| Path | Purpose |
|---|---|
| `model.py` | `SecurityProfile`, `ExecutionRequest`, `ExecutionResult`, closed vocabularies |
| `errors.py` | One exception per failure surface (denial / disabled / config / runtime) |
| `schema.py` | Minimal honest JSON Schema (draft-07 subset) validator + loaders |
| `*.schema.json` | Contract schemas (`security-profile`, `profiles`, `categories`, `microvm`) |
| `profiles.yaml` / `categories.yaml` | The instantiated contract (validated on load) |
| `catalog.py` | `ProfileCatalog` / `CategoryMap` loaders, fail-closed resolution |
| `runtime.py` | `Runtime` protocol: `name` / `enabled` / `run(request, profile)` |
| `offline.py` | `OfflineRuntime`: deterministic in-process enforcement model |
| `docker.py` | Docker runtime - **DECLARED, flag-gated OFF** + pure `docker_run_flags` |
| `firecracker.py` | Firecracker microVM executor - **DECLARED, flag-gated OFF** (schema + seam) |
| `executor.py` | `SandboxExecutor`: per-category defaults, fail-closed dispatch, optional audit |
| `mcp_seam.py` | Additive MCP wiring: `sandboxed_tool(...)` factory (gateway/mcp imported read-only) |
| `demo.py` | Offline walkthrough / self-check (exits nonzero on any failure) |
| `tests/` | pytest suite incl. every negative + the MCP integration proof |
| `README.md` | This contract document |

## 3. Executor and the injectable runtime seam

`SandboxExecutor.execute(request)` is the decision surface:

1. **Refuses** unless the executor itself is enabled (no unsandboxed execution).
2. **Resolves the profile** - an explicit `profile_name` override, else the
   per-category default from `categories.yaml`, else the map's fail-closed
   default (`restricted`) for an unknown category. An unknown explicit
   override also fails closed to `restricted`.
3. **Refuses** unless the injected runtime is enabled (flag-gated-OFF
   doctrine, AO-GR-6): a disabled sandbox never degrades into an unsandboxed
   run.
4. **Dispatches** to the injected runtime, which enforces the profile's
   isolation axes and returns an `ExecutionResult` (accepted or denied, with
   the reason naming the enforced axis).
5. **Audits** (optional mcp-shaped sink): one `tool_call` record per execution
   with who/what/tenant/result.

The executor never executes a tool itself; every refusal is a hard error or a
denied result - a denial is never turned into a silent success.

### 3.1 The runtime seam

A `Runtime` is *the thing that actually isolates a tool execution*. The default
deployment wires a real Docker runtime (or a Firecracker microVM) **behind a
flag that ships OFF**; every runtime refuses to `run` while `enabled` is False.
Tests and CI inject `OfflineRuntime`.

### 3.2 OfflineRuntime (the offline fake, used by tests/CI/demo)

`OfflineRuntime` models, in-process, the isolation a real runtime would
enforce. Every decision is derived **from the profile fields themselves** (not
a parallel table), so editing `profiles.yaml` changes enforcement here:
`networkMode: none` denies a network-requiring request; a dropped capability
(or `capDrop: ALL`) denies a request that asks for it; `noNewPrivs` denies
privilege escalation; `readOnlyRootfs` denies rootfs writes; the CPU/memory/
PID/timeout quotas are enforced as upper bounds. An accepted request returns a
deterministic simulated completion. It is explicitly **not** a real sandbox -
it exists so the executor seam, the profile resolution and the fail-closed
rules are fully testable offline.

### 3.3 Docker runtime (DECLARED, flag-gated OFF)

`DockerRuntime` ships disabled (`enabled=False`). `docker_run_flags(profile)`
is a pure, offline 1:1 profile→`docker run` flag mapping (read-only rootfs,
no-new-privileges, cap-drop, network mode, user, workdir, cpu/memory/PID
quotas) and is unit-tested without a daemon. Enabling the flag is a deployment
decision behind its own IaC flag; this offline repository never invokes docker.

### 3.4 Firecracker microVM executor (DECLARED, flag-gated OFF)

Per the acceptance criteria the optional Firecracker executor ships as a
**declared seam only** (no real lifecycle): `microvm.schema.json` + the
`MicroVmSpec` model (kernel/rootfs/vcpu/mem/state/network/jailer), a
deterministic `microvm_limits(profile)` derivation, and an OFF-by-default
`FirecrackerMicroVmRuntime` whose `run`/`start` refuse. A deployment that turns
the flag on wires the real Firecracker lifecycle (the elevatedIQ
`internal/firecracker` reference pattern) at that seam.

## 4. Wiring into the MCP tool gateway (issue #20)

`gateway/mcp` is **merged and read-only** for this lane. Sandboxing is wired
**strictly additively**, per the lane constraint:

- `sandbox.mcp_seam.sandboxed_tool(...)` builds an ordinary mcp `ToolDefinition`
  (declared capability with a JSON schema) whose handler routes every call
  through a `SandboxExecutor` before acting. Register it into the gateway's
  `ToolRegistry` exactly like any other tool.
- A **denial** raises `SandboxDeniedError`, which the gateway's normal error
  path converts into an `isError` result and an audit record (status `error`).
- An **accepted** execution returns the sandbox result payload and is audited
  status `ok`.
- The gateway's append-only ledger is untouched; the executor's optional audit
  sink (the **same mcp audit shape** - consumed, never redefined) adds an
  execution-level record.

The exact integration point, end to end, is proven by
[`tests/test_mcp_integration.py`](tests/test_mcp_integration.py): a real
`MCPToolGateway` with additively-registered sandbox tools, driven over
`handle_message`, showing network-category execution allowed under `standard`,
unknown-category + capability-escalation requests denied under `restricted`,
and the gateway's own unchanged behaviour (unknown tool → `-32601`,
informational tools) still green. `gateway/mcp/**` is not modified.

## 5. Audit shape (consumed, not redefined)

The sandbox writes through the same append-only, hash-chained **mcp-shaped**
ledger contract as `gateway/mcp` (issue #20 / `registry/events`, issue #10):
one JSON object per line, monotonic `seq`, RFC 3339 `ts`, `prevHash → hash`
sha256 chain. The sandbox emits `tool_call` records with status `ok`/`error`
and a `detail` carrying `tool` / `category` / `profile` / `operation` /
`outcome` (`accepted` | `sandbox_denied`) / `reason` / `sandbox` output, plus
`tenantId` / `agentId` / `actor` when identity is supplied. The same ledger
shape is shared, not redefined.

## 6. Negative tests (fail closed, no false green)

Every denial in the suite is paired with a positive control - the same request
under the profile that permits it is accepted - so the enforcement is provably
real:

- **restricted blocks network** - a network-requiring `exec` call is denied
  (`networkMode: none`); the same call under `network` (standard, bridge) is
  accepted.
- **restricted blocks capability escalation** - a `SYS_ADMIN` request is
  denied (`capDrop: ALL`); a `NET_ADMIN` request under privileged (which only
  drops `SYS_ADMIN`/`SYS_PTRACE`) is accepted.
- **read-only rootfs** denies rootfs writes (privileged is the positive
  control); **no-new-privs** denies privilege escalation.
- **resource quotas** (CPU / memory / PIDs / timeout) are enforced as upper
  bounds with same-request positive controls at the higher ceiling.
- **unknown category → restricted** (network op denied), including through the
  real MCP gateway path.
- **flag-gated-OFF honesty** - Docker/Firecracker runtimes (and a disabled
  executor/runtime) refuse to run; enabling the flag does not fake a success
  (declared-seam error instead).
- The validator and profile/category loaders genuinely fail on bad documents
  (unknown network mode, extra keys, missing fields, out-of-range quotas).

## 7. Running and verifying

```bash
# from the repo root
python3 -m pytest guardrails/sandbox/tests -q -p no:cacheprovider   # sandbox suite
python3 guardrails/sandbox/demo.py                                  # offline demo self-check
python3 -m pytest gateway/mcp/tests -q -p no:cacheprovider          # untouched sibling (green)
make verify                                                         # repo gate of record
```

## 8. Provenance

Pattern **adapted** (not copied) from `kushin77/elevatedIQ@develop`
`services/ai-chatbot-orchestrator/internal/sandbox/{profiles,docker}.go` +
`internal/firecracker/*` - harvested by spike #48 (`docs/spikes/48-elevatediq-deep-extract.md`,
verdict PATTERN) and opened as issue #58 because no backlog guardrail issue
(DLP/egress #27, policy #26, honesty #28, isolation #30) isolates agent tool
execution. This lane adapts the **contract** (three profiles + `CategoryProfiles`
map + fail-closed default) and the executor/runtime seam into Python under
`guardrails/sandbox/`; the Go implementation is not copied. Audit + MCP
contracts are consumed from `gateway/mcp` (issue #20) and `registry/events`
(issue #10), never redefined. See `docs/CANNIBALIZATION.md` (issue #8) for the
fleet cannibalization index.

## 9. Governance rules

1. **Fail closed** - an unknown category, an unknown explicit profile, a
   corrupt contract or a disabled runtime all refuse; none widens the sandbox.
2. **No unsandboxed execution** - there is no code path that runs a tool
   without an enabled executor and an enabled runtime.
3. **Flag-gated OFF** - every real runtime ships disabled; enabling is a
   deployment decision behind its own IaC flag (AO-GR-6).
4. **No false green** - every negative has a positive control; runtimes and the
   validator genuinely fail on violations.
5. **Consume, don't redefine** - audit and MCP shapes come from their owning
   surfaces (`gateway/mcp`, `registry/events`); this lane only consumes them.
