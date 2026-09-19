# Rollout + rollback — `gateway`

Path: `gateway`. Model gateways (Claude, DeepSeek, Copilot, Gemini, Ollama)
behind a shared contract.

## Rollout

Gateway packages ship on `master` merge; `gateway/sme-routing/policies` and
`gateway/limits/config.py` are read at process start, so a routing/limit
change needs a process restart, not just a file merge. Catalog parity between
the gateway's declared model list and the registry's is enforced pre-merge by
`scripts/check-gateway-catalog-parity.sh` — a merge that breaks parity fails
CI before it ever reaches a running gateway.

## Detection

- `bash scripts/check-gateway-catalog-parity.sh` — re-run against the live
  tree to confirm the deployed catalog still matches the registry.
- `gateway/health/policy.py` — the health policy a gateway process evaluates
  per upstream; a policy breach shows as the gateway marking a model
  unhealthy and routing away from it (that is *working* backpressure, not a
  bug — distinguish it from a gateway process itself being down).
- `gateway/limits/config.py` limit breaches surface as 429s from the gateway,
  not silent drops — a spike in 429s with no corresponding upstream-provider
  incident points at a bad limits rollout.
- `gateway/sync/live.py` — the live-sync module; if the gateway's view of
  model availability stops updating, this is the first place to check for a
  stuck sync loop.

## Rollback

1. **Bad routing/limits config**: these are files under `gateway/`, not
   external flags — `git revert <commit>` the config change on `master`,
   redeploy, and re-run `scripts/check-gateway-catalog-parity.sh` to confirm
   parity is restored before declaring the rollback complete.
2. **Bad health policy causing false-unhealthy routing**: revert
   `gateway/health/policy.py` to the prior commit; a restart is required
   since the policy is read at process start, not polled.
3. **A single upstream model gateway misbehaving** (not the whole surface):
   the health policy's own backpressure (marking that model unhealthy) is the
   fast mitigation — no code change needed — while the root cause is fixed
   under a separate PR.

Affected: every caller routed through the gateway (portal, engine, any agent
invocation) for the affected model(s); a full-surface rollback affects every
model behind the gateway, a per-model health-policy mitigation affects only
that provider's traffic.
