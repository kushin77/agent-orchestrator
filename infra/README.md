# infra — Infrastructure as Code (cross-cutting)

Owner lane: **infra** (foundation issue #6; IaC mandate GR-5). See
[`../docs/EXECUTION-PLAN.md`](../docs/EXECUTION-PLAN.md).

## Purpose

Everything declared, nothing clicked: Terraform + Cloud Build definitions for
the control plane. New infra ships **flag-gated OFF** by default and is applied
by the deployer, never the console (IaC mandate).

## Planned contents

- Terraform modules and environments (`infra/terraform/…`).
- Cloud Build pipelines (`infra/cloudbuild/…`).
- Private worker pool / egress, per environment.

## Status

Placeholder scaffold from issue #5. IaC/CI/CD build-out is issue #6.
