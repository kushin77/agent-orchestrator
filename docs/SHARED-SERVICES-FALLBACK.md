# Shared-services fallback rung (issue #375)

**Status:** frozen contract. **Owner lane:** `gateway/health`.
**Issue:** kushin77/agent-orchestrator#375. **Related:** #18 (fallback chain),
#366 (fleet crash-loop / code-drift deadlock).

## The gap

`gateway/health/fallback.py` resolves a route by walking an ordered **fallback
chain** whose final rung is the local Ollama last resort. When the local
provider rungs (`hermes`, `paperclip`, `ollama`) are *all* unreachable there is
no rung left inside the chain, `ChainRegistry.resolve()` returns `None`, and the
caller fails hard. That hard failure is one of the fleet crash-loop triggers
tracked by #366: a dead local rung turned a transient local outage into a
whole-lane stop.

The shared platform (`kushin77/shared-services`) already ships
`services/model-server/`, `services/mcp-hub/`, and `agents/ollama/` — a
model-serving surface that is *not* part of this repo's own fleet — but nothing
in the gateway health layer routed to it.

## The rung

`chains.sharedServices` in `gateway/health/health.yaml` declares one optional
**post-chain remote rung**:

```yaml
chains:
  localLastResort: ollama/llama3.2
  sharedServices:
    provider: shared-services
    model: llama3.2
    endpointEnv: AO_SHARED_SERVICES_MODEL_URL
```

`ChainRegistry.resolve()` walks the ordered rungs exactly as before. **Only
after every ordered rung is exhausted** does it make **one** bounded attempt at
the shared rung — and only if that rung is itself healthy. Otherwise it returns
`None`, unchanged.

## Why the rung is outside the chain, not in it

`FallbackChain.__post_init__` requires the final rung of a chain to be local
(`rungs[-1].local` is `True`). A remote shared-services rung is not local, so it
cannot be a chain rung without weakening that invariant. Holding it *outside*
the chains keeps the invariant intact and makes the ordering intent explicit:
local last resort first, remote shared platform only as the last resort *after*
the last resort.

## Guardrails

- **Explicit rung.** The shared rung exists only when `chains.sharedServices`
  is declared. Absent, `ChainRegistry.shared_services` is `None` and
  `resolve()` is byte-for-byte the pre-#375 behaviour (backward compatible).
- **Degrade, never crash.** A local outage must degrade the dispatch onto the
  shared platform, not fail the lane hard. `resolve()` returning a rung is the
  degrade signal; it never invents a route.
- **Bounded retry + explicit refusal.** At most one shared-services attempt per
  `resolve()`, no loop. If the shared rung is unhealthy too, `resolve()` returns
  `None` — an explicit refusal the caller still owns, never a silent success.
- **Reachability is a health signal.** The shared rung is subject to the same
  injected `health_lookup` as every other provider; the resolver lands on it
  only when healthy. "Declared" is not "reachable".
- **Endpoint is env-driven.** The endpoint host is never hardcoded in the
  resolution path. `endpointEnv` names the environment variable read at call
  time (`SharedServicesRung.endpoint`); an unset (or empty) variable falls back
  to the documented non-secret default `http://localhost:11434`, the current
  local convention.

## Blast radius contract

The blast radius of a dead local rung is **the single dispatch that needed that
rung** — and nothing else. Concretely, a shared-services degrade must never
become:

- a **global halt** — other dispatches, other lanes, and other routes keep
  flowing; the rung is per-resolution, not per-process;
- **unbounded re-dispatch** — one ordered walk plus one shared attempt, then a
  decision (a rung or `None`), never a retry loop;
- **silent success** — landing on the shared rung is a reported degrade with the
  resolved route, and an unhealthy shared rung yields `None`, never a fabricated
  route;
- a **consumed-but-unreported claim** — resolution does not mutate or release
  lane/issue state; whatever the caller does with a `None` (escalate, park, or
  refuse) it reports, so no claim is ever consumed silently.

## Invariants pinned by tests

`gateway/health/tests/test_shared_services_fallback.py` pins:

1. local rungs down + shared rung healthy -> `resolve()` lands on the
   shared-services rung;
2. all rungs down, shared rung included -> `resolve()` returns `None`;
3. no `sharedServices` declared -> behaviour identical to before the change;
4. the endpoint resolves from the declared env var (env wins; documented default
   when unset);
5. boundedness — exactly one shared-services attempt per `resolve()`.

## Out of scope

- No routing decision for the commercial cloud providers changes; this adds a
  rung strictly *after* the existing ordered chains.
- No network call is made by this layer: reachability arrives through the
  injected health lookup, exactly as for every other provider.
