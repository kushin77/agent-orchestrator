# infra/terraform — control-plane environment

Terraform declarations for the agent-orchestrator control-plane environment.
Every resource is gated by an `enable_*` variable. Following the 2026-09-21
reversal `policy-gr5-enabled-by-default` (AO-GR-6) those flags now default to
`true` — 18 of 19; `enable_erp_module` is the one **recorded exception**
(#1955, it carries its own dedicated promotion gate). Flipping a flag to
`false` is a deliberate, reviewed act that declares nothing for that surface.

## Reading this environment

| File | Purpose |
|------|---------|
| `versions.tf` / `providers.tf` | Terraform + Google provider pins (offline-validatable). |
| `variables.tf` | Root inputs, including the `enable_*` service flags + `deployer_enabled`; the service flags default `true` per AO-GR-6 (`enable_erp_module` the recorded exception, #1955). |
| `main.tf` | Composes `modules/control-plane-service` once per service, `modules/deployer-sa`, and the `../paperclip/terraform` runtime module. |
| `outputs.tf` | Gated outputs (`service_uris`, `deployer_service_account`, `web_surface_uri`, `paperclip_runtime_uri`) — null while flags are OFF. |
| `backend.tf.example` | GCS remote-state template; copy to `backend.tf` at go-live. |
| `modules/control-plane-service/` | One Cloud Run v2 service, count-gated on `enabled`, internal ingress by default. |
| `modules/web-surface/` | The public web surface (ai.purebliss.app): Cloud Run v2 with public ingress, DNS + Google-managed TLS — count-gated on `enabled`. |
| `modules/deployer-sa/` | The flag-gated deployer service account — the only apply identity. |
| `modules/fleet-cron/` | The fleet-cron container PAIR on the shared-services on-prem HA cluster (issue #900, EPIC #706), `for_each`-gated on `enabled` (root: `count`-gated on `enable_fleet_cron`) — SSH `null_resource` + `remote-exec`, never a docker provider, never a secret on a command line (GR-6). |
| `../paperclip/terraform/` | The self-hosted paperclip runtime (issue #411, ADR-0013): one count-gated Cloud Run v2 service running the pinned image, health-probed at `GET /api/health`. |

`google_artifact_registry_repository.ao_images` (declared directly in
`main.tf`) is deliberately **unconditional** — a build precondition (issue
#606's web build pushes into it), not a promoted surface, so it holds no
traffic, costs nothing idle and grants no IAM. It is the one resource that
still appears in `terraform plan` output with every `enable_*` flag OFF; see
the comment above it in `main.tf` for the full reasoning.

### Deployed-surface coverage (issue #884, lane L5 of EPIC #878)

Every surface this repo actually deploys has a module here, each behind its
own `enable_*` flag — defaulting ON per AO-GR-6, except `enable_erp_module`
(the recorded exception, #1955):

| Deployed surface | Module | Flag |
|---|---|---|
| control-plane services (registry/gateway/engine/guardrails/telemetry/identity/portal) | `modules/control-plane-service` (`for_each`) | `enable_registry` … `enable_portal` |
| public web surface (portal + shared-frontend) | `modules/web-surface` (owned by lane #881 — reference only, not touched by this lane) | `enable_web` |
| self-hosted paperclip runtime | `../paperclip/terraform` | `enable_paperclip` |
| fleet-cron container pair (shared-services HA cluster) | `modules/fleet-cron` | `enable_fleet_cron` |
| deployer service account (the only apply identity) | `modules/deployer-sa` | `deployer_enabled` |

**Residual (not covered by Terraform, tracked separately, not closed by this
lane):** the fleet cron/scheduler's *host-level* systemd/crontab wiring on
the shared-services nodes and the drain-then-freeze cutover sequencing are
EPIC #706's own runbook (`infra/fleet/README.md`), not a Terraform-managed
resource — #706 stays open. `.github/workflows/` does not exist in this repo
yet, so `scripts/check-terraform-iac.sh` (below) is not wired into a CI
workflow; that wiring is residual for whichever lane adds CI.

The promotion state of every service is recorded in
[`../feature-flags/registry.yaml`](../feature-flags/registry.yaml); the gate
(`scripts/check-feature-flags.py`) keeps this directory and the registry in
lock-step.

## Local validation (no network, no apply)

```bash
cd infra/terraform
terraform fmt -check -recursive .            # format is canonical
# offline validate via the local provider cache:
TF_PLUGIN_CACHE_DIR="$HOME/.terraform.d/plugin-cache" \
  TF_DATA_DIR="$(mktemp -d)" \
  terraform init -backend=false -input=false
TF_DATA_DIR="$(mktemp -d)" terraform validate
```

`make verify` wraps fmt + offline validate (`scripts/check-terraform.sh`) and
degrades to a visible `SKIP` when terraform or a provider cache is absent.

`scripts/check-terraform-iac.sh` (issue #884) is the stricter IaC gate: fmt +
validate plus two negative controls — a module whose `enable_*`/`enabled`
flag defaults `true` is refused BY NAME (`IAC-FLAG-DEFAULT-TRUE`), and a
`.tf` file with broken HCL is refused BY NAME (`IAC-TF-SYNTAX-ERROR`) — so
the gate can be proven to fail, not just to pass (no-false-green doctrine).
When neither `terraform` nor `tofu` is on `PATH` it prints a visible
`SKIP`+`WARN` naming the missing binary and falls back to a Python
brace/paren/quote structural check — it never reports the real validate as
having passed when it did not run.

```bash
bash scripts/check-terraform-iac.sh
```

## Plan with all flags OFF

```bash
terraform plan    # only google_artifact_registry_repository.ao_images shows;
                   # every flag-gated surface adds/changes zero resources
```

## Promote one service (go-live, reviewed)

1. Flip the service flag ON (e.g. `-var=enable_registry=true`) **and** record
   the promotion in the registry; supply a real `image`.
2. `make verify` (green) then `terraform plan` — reviewed by the PR.
3. Merge; the flag-gated Cloud Build apply pipeline applies as the deployer SA
   (see [`../cloudbuild/README.md`](../cloudbuild/README.md)).

Never run `terraform apply` by hand and never from a console.

## Provenance

Adapted from `kushin77/CMR` `infra/terraform/**` (flag-gated module pattern,
count-gated resources, offline verify contract) — fleet-internal reuse, see
[`../README.md`](../README.md) provenance table.
