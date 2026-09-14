# infra/paperclip — the self-hosted upstream runtime (flag-gated OFF)

The deployment declaration for the upstream paperclip runtime that runs
**beside** the control plane as an external operator surface (issue #411,
[ADR-0013](../../docs/decision-records/ADR-0013-paperclip-ing-integration.md)).
This is the process boundary itself: adopt the CLI, do not embed it, do not
fork it.

Nothing here runs until `enable_paperclip` is promoted. Every artifact is a
declaration — no console clicks, no ad-hoc apply (GR-5).

## Files

| File | Purpose |
|------|---------|
| [`release.yaml`](release.yaml) | The upstream release pin + GR-10 provenance (exact tag, release date, retrieval date, `vendoring: none`). |
| [`terraform/`](terraform) | The runtime module: one count-gated Cloud Run v2 service running the pinned image, with a startup + liveness probe on `GET /api/health`. |
| [`cloudbuild/deploy.yaml`](cloudbuild/deploy.yaml) | The flag-gated deploy pipeline: flag-gate → deploy → **health probe**. |
| [`cloudbuild/deploy-trigger.yaml`](cloudbuild/deploy-trigger.yaml) | The importable trigger; ships `disabled: true` with `_ENABLE_PAPERCLIP: "false"`. |
| [`health/healthcheck.py`](health/healthcheck.py) | The real `GET /api/health` probe (exit 0 healthy / 1 absent-or-unhealthy / 2 cannot-assess). |

## Flag — `enable_paperclip`, OFF by default

The runtime is wired into the root environment exactly like every other
surface:

- [`../terraform/variables.tf`](../terraform/variables.tf) declares
  `enable_paperclip` with `default = false`;
- [`../terraform/main.tf`](../terraform/main.tf) instantiates this module with
  `enabled = var.enable_paperclip`;
- [`../feature-flags/registry.yaml`](../feature-flags/registry.yaml) records
  `services.paperclip` with `default: off`. The `feature-flags` gate keeps the
  variable and the registry row in lock-step.

With the flag closed the runtime module is inert: `terraform plan` shows zero
resources.

## Pin — exact tag, never floating

`release.yaml` pins `v2026.831.1` (released 2026-09-02, retrieved 2026-09-14)
as the OCI reference `ghcr.io/paperclipai/paperclip:v2026.831.1`. An upgrade is
a deliberate version bump in that file and the Terraform image default — never a
`latest`.

## Health — a real check

[`health/healthcheck.py`](health/healthcheck.py) performs `GET /api/health` and
fails when the process is absent or unhealthy. The deploy pipeline runs it as a
build step, and the Terraform runtime declares the same path as a startup and
liveness probe. The gate
([`../../scripts/check-paperclip-deploy.sh`](../../scripts/check-paperclip-deploy.sh))
exercises the probe offline both ways so it cannot pass vacuously.

## No vendoring (GR-10)

Nothing from `paperclipai/paperclip` is copied into this tree. Upstream is
consumed as a pinned OCI image at deploy time; the only upstream references here
are that pin and the provenance record. The gate asserts it.

## Runbook

Start / stop / upgrade / health-check, and what the fleet does while the
process is down: [`../../docs/PAPERCLIP-ING-DEPLOY.md`](../../docs/PAPERCLIP-ING-DEPLOY.md).
