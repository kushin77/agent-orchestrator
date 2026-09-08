# e2e — end-to-end negative-control + smoke verification (issue #46)

> Owner lane: **E2E gate** (issue `kushin77/agent-orchestrator#46`, work item
> 42, phase 8; parent EPIC-00 issue #4). This subtree is `e2e/**` plus the
> additive suite registration in `scripts/pytest-suites.txt` and the count
> update in `docs/QA-GATE.md`. Everything here is **fully offline**: Python 3
> stdlib + PyYAML + the real merged pillar modules consumed read-only through
> their public APIs. No network, no sockets, no real keys, no docker.

This is the capstone E2E gate of the whole product build. It wires the merged
pillar modules into one offline control plane and proves, with real code and
real negative controls, the EPIC-00 Definition of Done:

> "a multi-provider agent org can be provisioned by a new tenant end-to-end
> (signup → org → personas → agents → routed model calls → audit + usage
> billing) entirely agent-built and flag-gated."

The E2E suite never edits a pillar file: it **consumes** every merged module
through its own public API, exactly as each lane's suite does (one issue = one
lane = disjoint files, GR-3).

---

## 1. The golden path (smoke verification)

`e2e/golden_path.py` runs the canonical tenant journey over the REAL merged
modules and writes `golden-path.json` evidence. Stage by stage:

| Stage | Real module(s) consumed | What is proven |
|---|---|---|
| `signup` | `identity/onboarding` (issue #14) | tenant `acme` provisioned `active`; RBAC org seeded with `owner`/`admin`/… roles; owner binding; 13 starter seeds |
| `rbac` | `identity/rbac` (issue #12) | owner allowed `agent:read`; a stranger denied at the scope gate |
| `agents` | `registry/service` + `events` (issue #10) | `worker-1` (coder@1.0.0) and `reviewer-1` active; scoped-claims session binds tenant `acme` |
| `routed-call` | `gateway/proxy` wiring over personas/profiles/prompts (issues #9/#11/#13/#15/#16/#17/#18/#19) | a governed `classify-route` dispatch served by `deepseek`; policy `log` + DLP `sent` + preflight `allow` + ledger `model.call` + metered billable usage |
| `conformance` | same gateway stack, all providers | the **same path** under DeepSeek, OpenAI, local Ollama (LOW) and Claude/anthropic (MED) yields governed, audited, metered behavior on every provider |
| `durable` | `engine/core` (issue #21) | one durable `TASK_EXECUTION` workflow (file-backed event store) runs the real gateway through the `core.task` handler and succeeds |
| `audit` | `telemetry/ledger` (issue #31) | every action is on the per-tenant hash-chained ledger; `verify` → `OK` (exit 0) |
| `billing` | `telemetry/metering` (issue #33) | every call is ingested and rolled up; billable usage + cost per model |

Run it:

```bash
python3 -m e2e.golden_path --out /tmp/e2e-golden   # writes golden-path.json
python3 -m pytest e2e/tests -q                      # suite (see §3)
```

### Multi-provider routing + degradation

Routing policy is the real `gateway/proxy/config/routing.yaml`: LOW =
`deepseek → openai → ollama`, MED/HIGH = `deepseek → anthropic → ollama`. The
conformance stage disables providers one at a time via the real health signal
(`gateway/health`, issue #18) and asserts the fallback chain serves:

* DeepSeek healthy → served by `deepseek/deepseek-chat`;
* DeepSeek down, OpenAI healthy → served by `openai/gpt-4o-mini`;
* both cloud providers down → local **Ollama** (`ollama/llama3.2`) degradation;
* DeepSeek down on the MED task → **Claude** (`anthropic/claude-sonnet-4-5`).

Every hop is a canned offline response through the real providers registry
transport rig — no keys, no network.

---

## 2. Negative controls (no-false-green)

`e2e/negative_controls.py` runs one check per guard. A check **passes only
when the real guard genuinely blocks** — a guard that silently passes when it
should refuse fails the check (issue #28 anti-formality doctrine). Run it:

```bash
python3 -m e2e.negative_controls --out /tmp/e2e-neg   # writes negative-controls.json
```

| Guard | Real module | The block the check proves |
|---|---|---|
| `cross-tenant-access-denied` | `identity/rbac` + `registry/service` | acme's owner is denied in org `globex` (`reason=scope`); acme's agent session refused for `globex` (`CrossTenantDenied`) |
| `dlp-blocks-secret` | `guardrails/dlp` | an AWS-shaped secret is blocked at the DLP scrub gate (`blocked_dlp`, `cloud.aws_access_key_id`) — never dispatched |
| `prompt-injection-blocked` | `guardrails/dlp` | "ignore previous instructions / reveal system prompt" is blocked (`blocked_injection`) |
| `policy-block-enforced` | `guardrails/policy` | a flagged `model.call` returns decision `block` (real `PolicyEngine`) |
| `invalid-policy-rejected` | `guardrails/policy` | a malformed policy document is rejected at load (`PolicyValidationError`) — fail closed, never silently accepted |
| `budget-breach-blocked` | `telemetry/budgets` | with month spend already over the hard limit, preflight returns `block`/`budget_exceeded` (enforce mode) |
| `kill-switch-refuses` | `telemetry/budgets` | after `pause`, a healthy call is refused first (`refuse`) regardless of headroom |
| `dead-model-failover` | `gateway/proxy` | every candidate dead → explicit `no_healthy_route`, no provider call, never a silent pass |
| `invalid-typed-output-cannot-assess` | `gateway/proxy` | a non-typed provider output retries once then yields explicit `cannot_assess` — CANNOT-ASSESS is never a pass |
| `audit-tamper-detected` | `telemetry/ledger` | editing a chained record is detected: `verify` → `NOT-OK` (`hash mismatch at record 1`) |

Each check records a `GuardAttestation` (guardrails/honesty); the aggregate of
the ten attestations is `OK` only when every guard blocked as designed.

---

## 3. Suite registration + gate

The suite lives at `e2e/tests` (pytest) and is declared in the authoritative
manifest `scripts/pytest-suites.txt`. `make gate` runs it in isolation like
every other suite (a combined invocation would collide on `conftest.py`
bootstraps — see `docs/QA-GATE.md`).

```bash
make tests        # every declared suite incl. e2e, in isolation
make gate         # GATE: PASS requires e2e green
make merge-gate   # pre-merge contract incl. attestation naming the commit
```

This lane also **finalized the manifest**: `e2e`, `governance/sync` and
`infra/rollout` were the last merged suites with a `tests/` dir that were not
yet declared; all three are registered (docs/QA-GATE.md count updated to 39).

---

## 4. DoD mapping (EPIC-00)

| DoD element | Where it is proven |
|---|---|
| signup / new tenant | `identity/onboarding` `provision()` (golden `signup` stage) |
| org (personas/roles) | onboarding RBAC org + roles; `rbac` stage; personas consumed by the gateway resolver |
| agents | `registry/service` register/activate + session (golden `agents` stage) |
| routed model calls | `gateway/proxy` dispatch over the real provider registry, multi-provider + fallback (golden `routed-call` + `conformance`) |
| audit | `telemetry/ledger` per-tenant tamper-evident chain (golden `audit` stage) |
| usage billing | `telemetry/metering` intake + rollup (golden `billing` stage) |
| flag-gated / safe | everything offline + all negative controls block; gate evidence recorded |

---

## 5. Provenance + integration notes

* **Provenance (GR-10):** the negative-control methodology (one guard, one
  check, blockproof, honest tri-state) is cannibalized from
  `leaderboard tests/negative/ + scripts/qa/verify-negative-controls.sh` and
  `CMR tests/`, adapted to this repo's real-module pytest model — no code was
  copied verbatim. See `docs/CANNIBALIZATION.md`.
* **Known cross-module bug (worked around, NOT edited — GR-3):**
  `gateway/proxy/wiring.py`'s `build_real_gateway` constructs its sinks with
  `audit_sink or ListCallRecordSink()`, and `ListCallRecordSink` defines
  `__len__`, so an **empty** injected sink is falsy and is silently replaced
  by a fresh one (records lost to the caller). `e2e/wiring.py` works around
  it with `TruthyListCallRecordSink` (always-truthy, same record contract);
  the gateway lane should switch that `or` to an explicit `is None` check.
* **Import convention:** gateway/identity/registry/guardrails are added to
  `sys.path` as top-level roots (their own suites' convention); telemetry/
  engine/ control-plane are imported through the repo-root PEP-420 namespace.
  `telemetry/` is deliberately NOT a top-level root because gateway/finops
  ships a plain top-level module literally named `metering` (see
  `e2e/_paths.py`).
* **Secrets discipline (GR-6):** every material is synthetic and assembled at
  runtime (`e2e/wiring.py`), so the committed files carry no secret shapes.
