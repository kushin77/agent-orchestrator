# infra/paperclip/cloudbuild — the flag-gated deploy path for the runtime

Declarative Cloud Build for the self-hosted paperclip runtime (issue #411,
ADR-0013). Nothing here runs until a reviewed go-live promotes it.

| File | Purpose |
|------|---------|
| `deploy.yaml` | Build config: flag-gate, deploy the pinned image as a Cloud Run service, then probe `GET /api/health` and fail the build when the runtime is absent or unhealthy. |
| `deploy-trigger.yaml` | Importable trigger for `deploy.yaml`. Ships `disabled: true` with `_ENABLE_PAPERCLIP: "false"`. |

## Flag gate (GR-5) — off by default

| Trigger | Registry flag | Trigger state | Build substitution |
|---------|---------------|---------------|--------------------|
| `paperclip-runtime-deploy` | `services.paperclip` | `disabled: true` | `_ENABLE_PAPERCLIP: "false"` |

The build's first step refuses unless `_ENABLE_PAPERCLIP` is exactly `"true"`,
so even a mistaken import deploys nothing while the flag is closed.

## Pinned image, never floating

The image substitution is the exact tag recorded in
[`../release.yaml`](../release.yaml) (`ghcr.io/paperclipai/paperclip:v2026.831.1`).
An upgrade is a reviewed version bump that edits that file and the Terraform
image default together — never a `latest`.

## Health

`deploy.yaml`'s `health` step runs
[`../health/healthcheck.py`](../health/healthcheck.py) against
`GET /api/health`. The step fails the build when the runtime is absent
(connection refused/timeout) or unhealthy (non-2xx or non-JSON). The Terraform
runtime declares the same path as a startup and liveness probe.

```bash
# Reviewed go-live only — never run from a task:
# 1. Flip services.paperclip -> on in infra/feature-flags/registry.yaml.
# 2. Import the trigger with disabled: false.
gcloud builds triggers import --source=infra/paperclip/cloudbuild/deploy-trigger.yaml
```
