# Cloud Build — code-native CI/CD for the agent-orchestrator control plane
#
# Cloud Build is the automation engine (ADR-0011 doctrine: GitHub Actions is
# not used for automation in this fleet). This directory declares the build
# configs and importable triggers. Everything ships **flag-gated OFF** — no
# trigger fires and nothing applies until a reviewed go-live imports the
# trigger with `disabled: false` and flips the matching registry flag.

## Files

| File | Purpose |
|------|---------|
| `verify.yaml` | Build config: run the repo gate (`make verify`) as a CI status. |
| `apply.yaml`  | Build config: flag-gated `terraform apply` as the deployer SA — the ONLY apply route (no console). |
| `web-image.yaml` | Build config: build `portal/Dockerfile` and push the web-surface image to Artifact Registry (fills the `web-surface.image` placeholder). |
| `verify-trigger.yaml` | Importable pull_request trigger for `verify.yaml` (disabled by default). |
| `apply-trigger.yaml`  | Importable push trigger for `apply.yaml` (disabled by default). |

## Flag-gate (GR-5) — everything ships OFF

| Trigger | Registry flag | Trigger state | Build substitution |
|---------|---------------|---------------|--------------------|
| `control-plane-verify` | `ci_cd.verify_trigger` | `disabled: true` | `_ENABLE_VERIFY: "false"` |
| `control-plane-apply`  | `ci_cd.apply_trigger`  | `disabled: true` | `_ENABLE_APPLY: "false"` |

`scripts/check-cloudbuild.sh` (wired into `make verify`) asserts: every YAML
here parses, both triggers are `disabled: true`, and the `_ENABLE_*`
substitutions mirror the OFF default.

## Workbook-surface switches (issue #644, workbook-13)

The five workbook surfaces are declared once in
`infra/feature-flags/registry.yaml` (`services.<name>`) and kept in lock-step
with `infra/terraform/variables.tf` by `scripts/check-feature-flags.py`. This is
where that pair reaches the build: `apply.yaml` passes each switch to
`terraform plan` as an explicit `-var` from its substitution, and the trigger
declares every substitution `"false"`, so the only apply route renders the
posture it was told to render instead of inheriting a default silently.

| Build substitution | Registry flag | Terraform variable | Default |
|--------------------|---------------|--------------------|---------|
| `_ENABLE_ORG_CHART` | `services.org_chart` | `enable_org_chart` | `"false"` |
| `_ENABLE_SKILL_STUDIO` | `services.skill_studio` | `enable_skill_studio` | `"false"` |
| `_ENABLE_TASK_BOARD` | `services.task_board` | `enable_task_board` | `"false"` |
| `_ENABLE_MCP_OUTBOUND` | `services.mcp_outbound` | `enable_mcp_outbound` | `"false"` |
| `_ENABLE_SANDBOX_RUNTIME` | `services.sandbox_runtime` | `enable_sandbox_runtime` | `"false"` |

The rendered posture is not taken on trust: the `workbook_surface_flags` output
in `infra/terraform/outputs.tf` reports all five values in the plan/apply log,
so a promotion that reached the trigger but not the plan is visible as `false`
in the deploy record. `_DEPLOYER_SA` is still supplied at import (never
hard-coded, GR-6), and the apply stays fail-closed behind `_ENABLE_APPLY`.

## Why this exists (issue #6)

The repo gate of record is `make verify`, runnable with no network and no
containers. When a real GCP project exists (a later deploy concern), the
`verify` trigger gives PRs a code-native CI status; the `apply` trigger is the
only way infrastructure changes reach GCP — executed by the deployer SA, never
by a human in a console.

## Web surface (issue #258)

The public web UI served at ai.purebliss.app (`infra/terraform/modules/web-surface`, flag `enable_web`, OFF by default) rides the **same** `apply.yaml`
pipeline: it is declared in Terraform and deployed by the flag-gated apply
route like every other surface. There is no separate apply build config —
`apply.yaml` remains the only apply route, and the web surface ships inert
until its flag is promoted.

## Apply (go-live only — never run from this task)

```bash
# 1. Reviewed go-live: flip ci_cd.apply_trigger -> on in the registry,
#    import the trigger with disabled: false, wire the github connection.
# 2. Validate locally:
make verify
# 3. Materialize (deployer SA, after review — never ad-hoc):
gcloud builds triggers import --source=infra/cloudbuild/apply-trigger.yaml
```

There is no console apply path. If a resource must change, it changes here as a
PR, is verified with `make verify`, and is applied by the pipeline.
