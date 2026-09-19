# Rollout + rollback — `portal`

Path: `portal`. Admin/tenant control-plane console (server + static +
catalog + tests).

## Rollout

The console ships as ordinary code on `master`: a merge lands
`portal/server/*`, `portal/static/*` and `portal/catalog/*` together, `make
verify` is the pre-merge gate, and the running process is restarted from the
new checkout (`bash scripts/console.sh --port <port>` / `make console`
locally; the deployed console is the `agent-orchestrator-console` image built
from `portal/Dockerfile`, per `docs/AGENTCONSOLE-HOSTING.md`). A surface
*within* the console is promoted independently of code deploys through
`AO_SURFACE_REGISTRY` (a registry file read at boot by `portal/server/fleet.py`)
— flipping a row there and restarting is the promotion act; no redeploy is
needed to turn an already-shipped surface on.

## Detection

- `curl -fsS http://<host>/api/healthz` — liveness; `/api/healthz/ready` —
  readiness.
- `bash scripts/check-portal-offline-spog.sh` reproduces the full status
  matrix (`docs/PORTAL-OFFLINE-DEV.md`) offline: a promoted-but-broken surface
  shows as `401`/`500` where the matrix expects `200`.
- `portal/server/control_audit.py` / `portal_control_audit` records mutating
  admin actions; an unexpected write there is a signal a bad rollout is being
  acted on live.
- Watch for `404 feature_disabled` on a surface expected to be `on` — that is
  itself evidence a rollback overlay engaged unexpectedly (see below), not a
  code break.

## Rollback

Two independent levers, pick the one that matches the failure:

1. **Withdraw the surface without a deploy** (fastest, minutes): write the
   affected surface name into the file named by `AO_SURFACE_STATE` (the
   *rollback overlay*, `portal/server/surface_state.py`, default
   `.rollout/surface-state.json`). It is consulted **before** the registry and
   **before** authentication, so an engaged overlay makes the surface
   `404 feature_disabled` immediately, even though the registry still declares
   it `on`. In production this overlay is written by the `infra/rollout/`
   engine's audited rollback anchor, not by hand.
2. **Revert the code** (a broken handler, a bad static asset, a schema
   change): `git revert <merge-commit-sha>` on `master`, re-run `make verify`,
   redeploy the console image / restart `portal/server/main`. Because the
   console writes no fleet state of its own outside `portal/catalog/` and the
   audit ledger, a revert is safe to run without a data migration.

Affected: every tenant/admin session against the console UI and every caller
of `/api/*`; `/views/*.html` stays served (static layer is ungated) even while
the API is rolled back, so a rollback is visible as "the page loads, the data
does not" — see the status matrix in `docs/PORTAL-OFFLINE-DEV.md`.
