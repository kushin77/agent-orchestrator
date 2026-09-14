# Paperclip-ing deployment runbook — the self-hosted runtime (issue #411)

**Operational.** How the self-hosted upstream paperclip runtime is declared,
started, stopped, upgraded and health-checked, and what the fleet does while it
is down. The integration mode is fixed by
[ADR-0013](decision-records/ADR-0013-paperclip-ing-integration.md) — adopt the
CLI, run it as its **own process** beside the control plane, integrate over its
HTTP API across a process boundary. The seam itself is frozen in
[PAPERCLIP-ING-INTEGRATION.md](PAPERCLIP-ING-INTEGRATION.md); this doc is the
process half.

## 1. What is deployed, and where

Everything lives under [`../infra/paperclip/`](../infra/paperclip/README.md) and
is **declared** — Terraform plus a flag-gated Cloud Build pipeline. There is no
console path and no ad-hoc apply (GR-5).

| Artifact | Role |
|---|---|
| [`release.yaml`](../infra/paperclip/release.yaml) | The pinned upstream release + GR-10 provenance. |
| [`terraform/`](../infra/paperclip/terraform) | The runtime module: one count-gated Cloud Run v2 service running the pinned image, with a startup + liveness probe on `GET /api/health`. |
| [`cloudbuild/deploy.yaml`](../infra/paperclip/cloudbuild/deploy.yaml) | The deploy pipeline: flag-gate → deploy → health probe. |
| [`health/healthcheck.py`](../infra/paperclip/health/healthcheck.py) | The `GET /api/health` probe. |
| [`../scripts/check-paperclip-deploy.sh`](../scripts/check-paperclip-deploy.sh) | The gate that proves the flag, pin, probe and no-vendoring assertions hold. |

### The flag — `enable_paperclip`, OFF by default

The runtime ships behind `enable_paperclip`, wired exactly like every other
surface: declared in
[`../infra/terraform/variables.tf`](../infra/terraform/variables.tf) with
`default = false`, instantiated in
[`../infra/terraform/main.tf`](../infra/terraform/main.tf), and recorded in
[`../infra/feature-flags/registry.yaml`](../infra/feature-flags/registry.yaml)
as `services.paperclip` (`default: off`). While the flag is closed the runtime
is inert: `terraform plan` shows zero resources and the trigger is disabled.

### The pin — exact tag, never floating

The upstream release is **pinned** and recorded in `release.yaml`:

| Field | Value |
|---|---|
| Upstream repository | `paperclipai/paperclip` (MIT) |
| Pinned version | **v2026.831.1** |
| Upstream release date | 2026-09-02 |
| Retrieval date | 2026-09-14 |
| OCI reference | `ghcr.io/paperclipai/paperclip:v2026.831.1` |

An upgrade is a deliberate version bump that edits that record and the Terraform
image default together — never a floating `latest`.

## 2. Start the runtime

Flag-gated and reviewed; never run from a task.

```bash
# 1. Reviewed go-live: flip services.paperclip -> on in the registry, and pass
#    -var=enable_paperclip=true at plan/apply time.
make verify                       # green gate of record
# 2. Import the deploy trigger with disabled: false (after review).
gcloud builds triggers import --source=infra/paperclip/cloudbuild/deploy-trigger.yaml
# 3. The pipeline runs: flag-gate -> deploy pinned image -> health probe.
```

The pipeline's flag-gate step refuses unless `_ENABLE_PAPERCLIP` is exactly
`"true"`, so importing the trigger while the flag is closed deploys nothing.

## 3. Health-check it

```bash
# The same probe the deploy pipeline runs after a deploy:
python3 infra/paperclip/health/healthcheck.py \
  --base "$PAPERCLIP_BASE_URL" --path /api/health --timeout 10
```

Exit codes: **0** healthy (2xx + JSON body) · **1** absent or unhealthy
(connection refused / timeout / non-2xx / non-JSON) · **2** cannot-assess. The
probe is a real `GET /api/health`, not a hope: the deploy build fails, and the
Cloud Run startup + liveness probes fail, when the process is absent or
unhealthy.

## 4. Stop the runtime

Stopping the runtime is a flag decision, not a console click:

```bash
# 1. Flip services.paperclip -> off in the registry (a reviewed PR).
# 2. Apply the flag OFF through the flag-gated pipeline; the count-gated
#    service is removed. Nothing in the live loop is affected.
```

## 5. Upgrade the runtime

An upgrade is a deliberate version bump in one reviewed PR:

```bash
# 1. Edit infra/paperclip/release.yaml: pin -> version / released / retrieved,
#    and the image reference.
# 2. Edit the image default in infra/paperclip/terraform/variables.tf to match.
# 3. make verify (the deploy gate refuses a floating tag), review, merge.
# 4. Re-run the deploy pipeline; the health probe gates the rollout.
```

## 6. While the process is DOWN

**The fleet keeps running.** Availability of the upstream runtime is an
assumption to *manage*, never a dependency of the live loop (ADR-0013
Consequences). The seam is an adapter over the live loop, not a participant in
it:

- dispatch, claims, budgets and audit remain authoritative in `fleet/` and
  `governance/` — they do not call upstream;
- the operator surface (the upstream UI/CLI) is degraded while the process is
  down; the fleet TUI (`fleet/console.py`) is unaffected;
- the heartbeat / ticket / budget seam is a contract with a real availability
  assumption to manage, so a down runtime is a page, never a stopped fleet.

There are **no two authoritative engines** (ADR-0012): the runtime is an
external operator surface mapped over the seam.

## 7. Provenance (GR-10)

Nothing from `paperclipai/paperclip` is vendored into this tree. Upstream is
consumed as a pinned OCI image at deploy time; the only upstream references are
the pin and the provenance record. `scripts/check-paperclip-deploy.sh` asserts
this (and the flag, the pin and the health probe), and refuses a mutation of any
of them by name.
