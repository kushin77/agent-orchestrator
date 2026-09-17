# AgentConsole go-live — the repeatable recipe

> **Class: `pattern`** (reusable, idiomatic, with a documented example and where
> it applies) · **iac**: hosting, secrets and fronting are declared, never
> clicked. Companion: [`AGENTCONSOLE-HOSTING.md`](AGENTCONSOLE-HOSTING.md) (the
> contract), [`EDGE-CUTOVER.md`](EDGE-CUTOVER.md) (the fronting decision),
> [`../contrib/shared-services/agentconsole.compose.yml`](../contrib/shared-services/agentconsole.compose.yml)
> (the declared overlay). The gate that keeps this from regressing is
> `scripts/check-agentconsole-hosting.sh`.

This is the recipe that took `ai.purebliss.app` from the legacy portal to the
fleet single-pane-of-glass on 2026-09-17 (epic #607). It is written to be
**re-run**, not improvised: every step names its input and its proof, and every
irreversible step has a rollback.

## The one thing to get right first: the host

The live host is the **shared-services cluster** (`192.168.168.42`), *not* GCP
Cloud Run — that route is RETIRED. The console therefore runs **on that host**:
`192.168.168.42` **cannot reach** the machine where the fleet state lives (every
TCP port is filtered), so a console placed beside the state is unreachable by
the tunnel. State flows one way — from the fleet host **to** the console host.

## Recipe

1. **Build the image** (repo root is the context; the boot path needs
   `cryptography`, not just PyYAML):
   ```bash
   docker build --network=host -f portal/Dockerfile -t agent-orchestrator-console:local .
   ```
   Prove it can serve its own CMD before shipping:
   ```bash
   docker run --rm --entrypoint python3 agent-orchestrator-console:local \
     -c "import cryptography, yaml; print(cryptography.__version__, yaml.__version__)"
   ```
2. **Transfer it** to the host (no registry dependency):
   ```bash
   docker save agent-orchestrator-console:local | gzip -1 \
     | ssh akushnir@192.168.168.42 'gunzip | docker load'
   ```
3. **Stage the secrets** — a *mirror* of the auth gate's published JWKS (public
   by construction) and the allowlist. Neither is a secret in the sense of a
   credential, but neither is committed (GR-6):
   ```bash
   curl -sS https://os.purebliss.app/auth/.well-known/jwks.json -o auth-gate-jwks.json
   # the allowlist is READ from the OS shell, never invented:
   ssh akushnir@192.168.168.42 \
     'docker inspect shared-services-os --format "{{range .Config.Env}}{{println .}}{{end}}" | grep ROOT_ADMIN_EMAILS'
   ```
4. **Run the service** — the declared overlay, as the host has no compose
   plugin:
   ```bash
   docker run -d --name shared-services-agentconsole --restart unless-stopped \
     --network shared-services-net -p 0.0.0.0:18286:8080 --cpus 0.50 --memory 512m \
     -e PORTAL_AUTH_GATE_JWKS_FILE=/etc/ao/auth-gate-jwks.json \
     -e ROOT_ADMIN_EMAILS="<the OS shell's own allowlist>" \
     -e AO_FLEET_DIR=/var/lib/ao/fleet -e AO_LEDGER_DIR=/var/lib/ao/ledger \
     -v /home/akushnir/agent-orchestrator/.fleet:/var/lib/ao/fleet \
     -v /home/akushnir/agent-orchestrator/.portal/control/ledger:/var/lib/ao/ledger \
     -v /home/akushnir/agent-orchestrator/.board:/app/.board:ro \
     -v /home/akushnir/agentconsole/auth-gate-jwks.json:/etc/ao/auth-gate-jwks.json:ro \
     agent-orchestrator-console:local
   ```
5. **Feed it the fleet state** — the console projects `AO_FLEET_DIR`; keep it
   current with a one-way push (the image excludes this state, so an unmounted
   console draws an *empty* fleet):
   ```bash
   rsync -a --delete --ignore-errors .fleet .board .portal \
     akushnir@192.168.168.42:/home/akushnir/agent-orchestrator/
   ```
   Installed as `/home/akushnir/agentconsole/sync-state-to-host.sh` on a 4-minute
   cron.
6. **Promote the surfaces** — the console reads `infra/feature-flags/registry.yaml`
   `surfaces.<name>.default` **at boot**, and the three it composes must move
   **together**:
   `surfaces.fleet_projection`, `surfaces.remote_control`,
   `surfaces.operator_terminal` → `default: on` + `promoted: true`. Then rebuild
   (step 1) so the promoted registry is in the image and redeploy (step 4).
7. **Cut the fronting** — **merge** the tunnel ingress rule for the hostname;
   never replace the array (a blind rewrite deletes every other hostname the
   tunnel serves):
   ```
   ai.purebliss.app  https://192.168.168.42:8493  ->  http://192.168.168.42:18286
   ```
   The Cloudflare token comes from Secret Manager (`cloudflare-api-token`),
   never a file. Backup the previous config first — that *is* the rollback.
8. **Verify at the edge** (the acceptance):
   ```bash
   curl -s -o /dev/null -w '%{http_code}\n' https://ai.purebliss.app/api/healthz        # 200
   curl -s -o /dev/null -w '%{http_code}\n' https://ai.purebliss.app/api/fleet/snapshot # 401
   curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' https://ai.purebliss.app/console  # 302 /auth/login
   ```

## Rollback

Re-apply the backed-up tunnel ingress config (one PUT) — the previous origin
(`:8493`) is the legacy portal, and no zone change or record deletion is needed.
`docker rm -f shared-services-agentconsole` removes the service; the state push
cron is inert without it.

## Why a gate, not just this page

`docs/` is advisory until a check reads it (GR-29). `scripts/check-agentconsole-hosting.sh`
asserts steps 1, 2, 3, 4, 6 and this page's own live-state claim, offline, on
every `make verify` — and proves it can fail by mutating one property per rule
on every run. The RCA that produced it is
`governance/lessons/rca/RCA-0007-*.md`.
