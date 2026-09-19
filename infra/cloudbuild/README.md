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

## Declared vs live: the gate compares the declarations to what actually runs (#1415)

`disabled: true` in a `*-trigger.yaml` is a **declaration**. Until #1415 the gate
read only the declarations, so it was green while all three control-plane
triggers were ENABLED — a control that reads one side of a comparison cannot
fail, and one that fails *open* is worse than none.

The live side is **recorded, not called**:

| what | where |
|---|---|
| the live inventory | `infra/cloudbuild/live-triggers.json` — carries the project, the instant, the account and the exact read-only command that produced it |
| the accepted exceptions | `infra/cloudbuild/live-baseline.txt` — `name <TAB> #tracker <TAB> reason`, honoured only while the disagreement is live |

Refresh it (read-only — it mutates nothing):

```bash
gcloud builds triggers list --project=purebliss-ghl --format=json > infra/cloudbuild/live-triggers.json
```

The three disagreements it names, and nothing else:

- the declaration ships `disabled: true` while the live trigger is **ENABLED**;
- the declared trigger **shape** differs from the live one (a 1st-gen `github:`
  block cannot describe a 2nd-gen trigger);
- a live trigger for this repository has **no declaration** at all.

A declaration with **no** live trigger is not a finding: these files are importable
templates, and nothing requires a template to be live.

Measured 2026-09-19, as committed in `infra/cloudbuild/live-triggers.json`: three
triggers live for this repository, all three **ENABLED**, all three declared
`disabled: true`. The gate therefore reports three *baselined* disagreements
tracked by **#1465** (the owner issue for the residual live work — not #1415,
whose PR closes it, because a row must not outlive its tracker), and fails **by
name** on any disagreement that is not in the baseline. Bring a live trigger in
line and its row goes **STALE** — the gate fails naming it until the row is
deleted, so the baseline can only shrink by fixing the drift.

## Why this exists (issue #6)

The repo gate of record is `make verify`, runnable with no network and no
containers. When a real GCP project exists (a later deploy concern), the
`verify` trigger gives PRs a code-native CI status; the `apply` trigger is the
only way infrastructure changes reach GCP — executed by the deployer SA, never
by a human in a console.

## The Cloud Build venue does NOT post `ao/gate-of-record` (issue #1415)

`ao/gate-of-record` is required on `master` (#1342) and has exactly **one**
producer: the box-side runner rung, publishing through `scripts/verify.sh` →
the one poster, `scripts/gate-status.sh post`. It used to have two: #1354 added a
poster step to `verify.yaml`, so this venue published a **second** status for the
same commit while the runner published the first. Two producers of one required
context is the drift class #1342/#1382 exists for — and both described the same
`make verify` run.

**The poster step is gone.** `verify.yaml`'s `verify` step runs the gate of
record (via `scripts/verify.sh verify` — the same entrypoint `make verify` uses),
captures its rc, and exits with it. Nothing in that step reads a secret, and
nothing in it can fail for a reason other than the gate's own verdict.

### What the removed step measured — the record a re-introduction must not lose

*Everything below in this subsection describes the step #1415 removed. It is kept
because each item cost a CI round trip, and a future poster in this venue would
hit both of them again.*

#### The commit under review came from the gate's own record, not the trigger

`$COMMIT_SHA` is a built-in Cloud Build populates for **push/tag** triggers; on
a `pull_request`-triggered build it is **empty**. Measured on build
`e6df118d-a395-477d-957b-9603be327b86`: the build's own checkout log reads
`GitCommit: df02b015...` while the step saw the built-in empty — so the earlier
config skipped the POST and reported that the commit under review was
unresolvable when it was in fact perfectly well known. Requiring that built-in
withdrew the producer at exactly the moment the check became required.

`make verify` writes `.verify/attestation.json`, which holds **the sha it
measured** and **the rc it reached**, and Cloud Build's `FETCHSOURCE` checks out
that same commit. The poster therefore takes both from the record:

- a PARKED or crashed gate writes no attestation, so there is no verdict to
  publish and the step exits `2` by name rather than inventing one;
- when `$COMMIT_SHA` *is* populated (push/tag triggers) it is passed alongside
  as a **cross-check** — a disagreement is refused, because an rc belongs only
  to the commit that was measured;
- the step resolves the record with a `dry-run` **before** the token boundary is
  consulted, so the sha this run is about is readable in the build log instead of
  merely claimed.

`scripts/gate-status.sh` owns the tri-state mapping (0 OK / 1 NOT-OK /
2 CANNOT-ASSESS → success / failure / error; CANNOT-ASSESS is never posted as
success). Cloud Build has no `gh` login, so the `verify` step reads the PAT from
Secret Manager (`gcloud secrets versions access`) and exports it as `GH_TOKEN`
for the poster, which falls back from `gh` to a raw authenticated `curl` call
when `gh` is absent.

#### An unreadable token skipped the POST — the gate still ran and still reported its rc

The build must not spend the gate's verdict to publish it. Cloud Build resolves
`availableSecrets` **before any step runs**, so declaring the PAT there made an
absent secret fail the whole build at step 0: `make verify` never ran, and the
red build said nothing about the code under review (measured: build
`27b692b8-0da5-483b-89d9-8f4e348dd806`, `Secret [ao-gate-status-token] not found
or has no versions`). Issue #1350 settles the order: the gate always runs and
always reports its own rc, and a token that cannot be read is logged and skipped
— without failing the step. The step exits with the **gate's** rc, so a skipped
POST is never mistaken for a verdict on the code, and a red gate still fails the
build (a POST can never turn one green). The one path that stays CANNOT-ASSESS
is the opposite case: a token that *was* available and a POST that then failed
produced no check where one was possible, so that exits `2` by name.

**There are TWO independent boundaries, and the step names the one that blocked
it** — an unqualified "no secret" message would become false the moment the
secret exists on a runner that cannot read it:

| message | meaning | remedy |
|---|---|---|
| `SKIPPED -- this runner image carries no gcloud, so the token cannot be read here at all` | the step's image is `python:3.14`, which has **no gcloud** (measured: `docker run --rm python:3.14 bash -lc 'command -v gcloud'` → empty) | a step/image change, i.e. the **venue shape** tracked by #1361 — *not* creating the secret |
| `SKIPPED -- no ao-gate-status-token secret` | gcloud is present, the secret could not be read | create the secret + grant the build SA (below) |

The image is deliberately **not** changed here. The step's image decides which
checks can assess in this venue, and that shape is #1361's, not this change's:
swapping it to a cloud-sdk image to pick up gcloud would change which of the 12
venue-limited checks answer `CANNOT-ASSESS` in CI.


The trade is named rather than hidden: **while the secret is absent, this build
produces no `ao/gate-of-record` status**, so the required check has no
*automatic* producer and merges ride the operator override — the gap recorded in
`docs/RELEASE-PLAN.md` §4/§5.

#### Ordering that applied to the removed step

1. **secret** — create `ao-gate-status-token` and grant the build SA read
   access (commands below);
2. **a runner that can read it** — the `verify` step's image must carry
   `gcloud`, which `python:3.14` does not (see the table above). That is a venue
   question, tracked by #1361, so creating the secret alone does **not** give
   this build a producer;
3. **trigger** — promote the `verify` trigger out of `disabled: true`
   (the existing flag-gate, unchanged by this change);
4. **observation** — read the posted status back with
   `bash scripts/gate-status.sh show --sha <sha>`.

Until all four hold, this repository's `ao/gate-of-record` check has no
*automatic* producer, and the fleet merges under the documented operator
override (`enforce_admins: false` in
`governance/platform/branch-protection.yaml`) rather than by satisfying it.
That gap is recorded in `docs/RELEASE-PLAN.md` §4/§5, not hidden here.

**None of that is needed now (#1415).** The runner rung produces the context, the
venue posts nothing, and `ao-gate-status-token` is no longer read by any build —
so the secret's remaining role is to be **deleted** once the declarations have
held for 7 days (see "Owner step" below).

### The same producer, run locally (no GH_TOKEN needed)

The poster is the producer; the runner only supplies the rc. Locally (where
`gh` is already authenticated) the identical, repeatable sequence is:

```bash
bash scripts/verify.sh verify                                    # the gate, in a lane worktree
bash scripts/gate-status.sh post --attestation .verify/attestation.json
bash scripts/gate-status.sh show --sha "$(git rev-parse HEAD)"   # read it BACK
```

`post --attestation` takes **both** the rc and the sha from the gate's own
record (`.verify/attestation.json`), so no rc is ever typed by hand: an
attestation for a different commit, recording something that is not an outcome
(a PARKED run writes none), or older than `AO_ATTEST_MAX_AGE` (default 6h) is
refused by name. `--self-test` proves those refusals offline, in dry-run.

### The venue of record has to agree before a green is published (issue #1400)

`ao/gate-of-record` had **two** producers: a box-side driver (a landing/train
runner with `gh` already authenticated) and the `verify` step above. Only the
second one was the **venue of record**, and until #1400 nothing made the first
one check whether the second disagreed. **Since #1415 the venue posts nothing, so
the box-side driver is the single producer** — the guard below stays in
`scripts/gate-status.sh` because a venue that posts is one `verify.yaml` edit
away, and its check (`scripts/check-gate-status-venue-agreement.sh`) still
provokes every arm. Measured on the train head `f300954d` of #1398:

| moment (UTC) | what happened |
|---|---|
| `03:51:19` | box-side `post --rc 0` publishes `ao/gate-of-record = success` |
| `03:51:19` | the Cloud Build run for the **same** sha opens its check-run `control-plane-verify (purebliss-ghl)` |
| `03:53:43` | box-side `post --rc 0` again — this is the status that **stands** |
| `04:19:01` | that run concludes **FAILURE** (`check-reconcile: FAIL (1 violation)`, build `32cb10f7`) |

So the required check read green for a commit whose own CI run was red, and the
guard that consumes it (`scripts/pr-queue.sh`) reads the **status**, never the
run. `scripts/gate-status.sh` now refuses that, in one place both producers go
through:

- a post that is **not** the venue reporting its own verdict does not publish
  `success` while the venue's run for that commit is **red** — or still
  **running**. The second half is the half that closes the measured case: the
  standing green was posted *while the run was in flight*, so "refuse on a red"
  alone would still have published it;
- an **unreadable** venue verdict is `CANNOT-ASSESS`, never an agreement — the
  control fails closed;
- a **red gate is never blocked**: the guard gates `success` only, so reporting a
  failure is always allowed;
- the venue names itself with `--venue-run "$BUILD_ID"` (the line above), because
  its own run is still in flight while it posts. Cloud Build exports that
  built-in into every step, so the marker is the environment's claim rather than
  an author's.
- `scripts/gate-status.sh reconcile --sha <sha>` is the other half, and it exists
  because the halves are not symmetric in **time**: a green published before the
  venue produced any run for the commit cannot be refused at post time. The verb
  withdraws a standing green (`state=error`, naming the build in `target_url`) —
  it can only ever publish a **non-success**, so it cannot become a second way to
  satisfy the context.

The honest consequence for a box-side driver: it must **wait for the venue's run
to conclude** before a green of its own is accepted. `scripts/check-gate-status-venue-agreement.sh`
(wired into `make verify` by the discovery layer) provokes every arm of this —
the measured case, the contradicting red, the healthy path, the venue's own
report, an unreadable verdict, a conclusion nobody enumerated, and a **mutant
with the guard removed**, which must restore the false green.

### Owner step — DELETE the token secret and the triggers (not run by this task, tracked by #1465)

None of this runs from a lane. Every command below is a **live mutation** of the
project, so it is an OWNER step, and the deletion half is additionally gated on
**the declarations having held for 7 days** (#1415 step 4) — it cannot be
done today, and no lane may do it. It is tracked by **#1465**, which is also the
tracker the rows in `live-baseline.txt` name: a row must stay justified by an
OPEN issue, so it cannot point at #1415, whose PR closes it.

```bash
# 1. Bring the three declarations' live state in line with what the repo declares
#    (they currently read ENABLED while every *-trigger.yaml ships disabled: true):
gcloud builds triggers update control-plane-verify      --region=us-central1 --no-disabled
#    ^ the flag is the MUTATION form; the DE-ENERGISE form is:
gcloud builds triggers update control-plane-verify      --region=us-central1 --disabled
gcloud builds triggers update control-plane-web-image   --region=us-central1 --disabled
gcloud builds triggers update control-plane-apply       --region=us-central1 --disabled

# 2. After 7 days with the declarations holding (#1415 step 4), delete them:
gcloud builds triggers delete control-plane-verify      --region=us-central1 --quiet
gcloud builds triggers delete control-plane-web-image   --region=us-central1 --quiet
gcloud builds triggers delete control-plane-apply       --region=us-central1 --quiet

# 3. And the Secret Manager token the removed poster read (nothing reads it now):
gcloud secrets delete ao-gate-status-token --quiet

# 4. Re-record the live inventory, which is what turns this file's baseline rows
#    STALE and forces their deletion (they may not outlive the drift):
gcloud builds triggers list --project=purebliss-ghl --format=json   # -> infra/cloudbuild/live-triggers.json
```

Do step (4) **after** step (1) or (2): while the three live triggers still read
ENABLED, the baseline rows in `infra/cloudbuild/live-baseline.txt` are live and
this gate stays green; as soon as they are disabled or deleted, those rows are
STALE and `make verify` fails naming them until they are removed. That is the
point — the exception cannot outlive the drift it excuses.


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
