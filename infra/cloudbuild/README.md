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
| `web-image-trigger.yaml` | Importable push trigger for `web-image.yaml` (disabled by default). |

## Flag-gate (GR-5) — everything ships OFF

| Trigger | Registry flag | Trigger state | Build substitution |
|---------|---------------|---------------|--------------------|
| `control-plane-verify` | `ci_cd.verify_trigger` | `disabled: true` | `_ENABLE_VERIFY: "false"` |
| `control-plane-apply`  | `ci_cd.apply_trigger`  | `disabled: true` | `_ENABLE_APPLY: "false"` |

`scripts/check-cloudbuild.sh` (wired into `make verify`) asserts: every YAML
here parses, both triggers are `disabled: true`, the `_ENABLE_*` substitutions
mirror the OFF default, and no config declares a bare `$NAME` template the
**submission-time** validator would refuse (below).

## Submission-time templates (issue #1369)

A YAML parse cannot see this class: Cloud Build refuses a build **at submission**
when a config spells a bare `$NAME` that is neither a built-in substitution nor a
declared one, and it reads **comments** too. Measured twice in one lane (#1350 /
PR #1354), costing two ~20-minute CI round trips — the second caused by the
comment written *while citing* the first failure. It kills the build before step
0, so `make verify` never runs and the red says nothing about the code under
review.

The check refuses every undeclared bare `$NAME`, by file and line, and accepts
exactly what the validator accepts: built-ins (`$PROJECT_ID`, `$COMMIT_SHA`,
`$SHORT_SHA`), lowercase shell names (`$rc`, `$?`), braced forms
(`${GH_TOKEN:-}`), escaped `$$`, and any name declared by the config's own
`substitutions:` map or by the `substitutions:` map of a `*-trigger.yaml` whose
`filename:` names that config.

Accepted exceptions live in `template-baseline.txt` — one row per token, each
naming its tracking issue and an immutable 40-hex anchor. A row is honoured only
while its finding is live: declaring the key makes the row **STALE** and the gate
fails until it is deleted, so the list can only shrink by fixing the finding.

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
| `_ENABLE_ERP_MODULE` | `services.erp_module` | `enable_erp_module` | `"false"` |

The rendered posture is not taken on trust: the `workbook_surface_flags` output
in `infra/terraform/outputs.tf` reports all five workbook values in the
plan/apply log, and `erp_module_enabled` reports the ERP module's, so a
promotion that reached the trigger but not the plan is visible as `false`
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

## Portal go-live: build → promote (#607)

Issue #607 (child of the #606 remediation) closes gap 1 of the #607/#606
go-live: the web-surface (`portal/`) image build. This section is the map of
what that build fills, what it does NOT do, and the exact human steps that
remain — no step in this section runs or applies anything.

### What `web-image.yaml` + `web-image-trigger.yaml` produce

- `infra/cloudbuild/web-image.yaml` builds `portal/Dockerfile` and pushes
  `$_AR_REPO/$_IMAGE:$_TAG` (and is invoked with `_AR_REPO=us-central1-docker.pkg.dev/purebliss-ghl/ao-images`,
  `_IMAGE=portal`, `_TAG=$SHORT_SHA` at go-live) to Artifact Registry in
  project `purebliss-ghl`. The tag gate refuses an empty or `latest` tag —
  every pushed image is immutable and traceable to a commit.
- `infra/cloudbuild/web-image-trigger.yaml` is the importable push trigger for
  that build, mirroring `verify-trigger.yaml` / `apply-trigger.yaml`: ships
  `disabled: true`, fires on push to `master` once imported.
- The built reference fills `infra/terraform/modules/web-surface/variables.tf`
  `image` (no default — required at apply), assembled in
  `infra/terraform/main.tf` as `local.web_image` from `var.project_id` +
  `var.web_image_tag`. `web_image_tag` currently defaults to
  `"0000..."` — "no build promoted yet".

### What this does NOT do

- It does not deploy anything. `web-surface` stays `enabled: false`
  (`infra/terraform/modules/web-surface/variables.tf`) until a reviewed
  phase 7 go-live promotes `services.web` / `services.portal`.
- It does not enable the apply trigger, promote any registry flag, or touch
  `infra/rollout/rollout-state.yaml`.
- Building the image is a prerequisite for the web-surface module to have a
  real `image` value at apply time — it is not itself the go-live.

### Validate locally, no GCP mutation

```bash
make web-image-dryrun   # gcloud builds submit --dry-run, or a local
                         # `docker build -f portal/Dockerfile .` fallback
```

### Remaining human steps to go live (owner-approved only)

1. Import `web-image-trigger.yaml` with `disabled: false` (or run the build
   once manually with the deployer SA) so a real `$_AR_REPO/$_IMAGE:$SHORT_SHA`
   image exists in Artifact Registry; set `web_image_tag` to that commit sha.
2. Promote **phase 0** (`ci_cd.verify_trigger`, `ci_cd.apply_trigger`) through
   `infra/rollout/stage-model.yaml`'s stages
   (`off → canary → gradual → full`), each transition gated by
   `verify_green` + `approval_code` + `audit_record`
   (`to_canary`/`to_gradual`/`to_full` policy-auto-approve covers `canary`
   and `gradual`; the final promotion to `full` is always human-approved —
   `policy_auto_approve` deliberately excludes it).
3. `infra/rollout/go-live-plan.yaml`'s `promotion_order: strict-by-phase`
   requires phases 1-6 to have already gone live before **phase 7**
   (`services.portal`, `services.web`) may promote — same
   off→canary→gradual→full stage model, same `approval_code` requirement at
   the final step.
4. Only then import `apply-trigger.yaml` with `disabled: false` and
   `_ENABLE_APPLY: "true"` so the deployer-SA `apply.yaml` pipeline (the only
   apply route, GR-5) can materialize the enabled web-surface module with the
   real image.

None of the above runs from this lane; this lane only produces the
flag-gated-OFF build config and its documentation.

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
