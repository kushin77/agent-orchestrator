# infra/terraform — control-plane environment (flag-gated OFF)

Terraform declarations for the agent-orchestrator control-plane environment.
Every resource is gated by an `enable_*` variable that defaults to `false`
(IaC mandate): with all flags closed the configuration creates **nothing**.

## Reading this environment

| File | Purpose |
|------|---------|
| `versions.tf` / `providers.tf` | Terraform + Google provider pins (offline-validatable). |
| `variables.tf` | Root inputs, including the nine `enable_*` service flags + `deployer_enabled`, all default `false`. |
| `main.tf` | Composes `modules/control-plane-service` once per service, `modules/deployer-sa`, and the `../paperclip/terraform` runtime module. |
| `outputs.tf` | Gated outputs (`service_uris`, `deployer_service_account`, `web_surface_uri`, `paperclip_runtime_uri`) — null while flags are OFF. |
| `backend.tf.example` | GCS remote-state template; copy to `backend.tf` at go-live. |
| `modules/control-plane-service/` | One Cloud Run v2 service, count-gated on `enabled`, internal ingress by default. |
| `modules/web-surface/` | The public web surface (ai.purebliss.app): Cloud Run v2 with public ingress, DNS + Google-managed TLS — count-gated on `enabled`. |
| `modules/deployer-sa/` | The flag-gated deployer service account — the only apply identity. |
| `../paperclip/terraform/` | The self-hosted paperclip runtime (issue #411, ADR-0013): one count-gated Cloud Run v2 service running the pinned image, health-probed at `GET /api/health`. |

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

## Plan with all flags OFF

```bash
terraform plan    # shows zero resources while every flag stays OFF
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
