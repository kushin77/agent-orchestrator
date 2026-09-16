# Go-live runbook — fleet surfaces to production at ai.purebliss.app

The operator-facing runbook for epic #607. It turns the measured, verified
deploy-side gaps into one mechanical, owner-approved sequence. Nothing in this
file runs by itself; every promotion below is gated by the stage model
(`infra/rollout/stage-model.yaml`: `verify_green` + `approval_code` +
`audit_record` per transition) and the deployer-SA apply pipeline
(`infra/cloudbuild/apply.yaml`) is the **only** apply route (GR-5 — no console
clicks, no ad-hoc `terraform apply`).

Readiness evidence this runbook builds on: child #618's audit (rollout
conformance `check-rollout: OK`, promotion-refusal negative controls proven,
apply/web-image fail-closed configs quoted, `make verify` PASS 81 of 85 at the
master head the audit named).

## Pre-conditions (measure, do not assume)

- [ ] Web image wired (child #617 merged): the placeholder is gone
      (`git grep -n "Placeholder until the web build" origin/master -- infra/` clean)
      and `web_image_tag` is the documented commit-sha convention.
- [ ] `make verify` PASS on `origin/master` at the go-live start commit
      (attestation `git_sha` quoted in the audit trail).
- [ ] `infra/rollout/rollout-state.yaml`: every flag still `stage: "off"`
      (this file stays the declared-default document forever - GR-28 - and
      never records a promotion; see `infra/rollout/live-state.yaml` below).
- [ ] `infra/rollout/live-state.yaml`: `flags: {}` (nothing promoted yet).
- [ ] `infra/cloudbuild/apply-trigger.yaml`: `disabled: true`.
- [ ] `infra/feature-flags/registry.yaml`: `ci_cd.apply_trigger` off.

## Promotion ladder (per flag)

Every flag travels `off → canary → gradual → full` (strict-forward, no jumps,
`infra/rollout/stage-model.yaml`). For each flag, for each step:

```bash
# 1. The operator grants an approval (the approval_code gate).
python3 -m infra.rollout.cli grant-approval <flag> --to <stage> \
  --approver <operator> --approval-id <unique-id> --approvals-dir <ledger-dir>

# 2. The deployer consumes it; verify_green + audit_record are enforced here.
#    --live-state-out is what persists the promoted stage (issue #914): the
#    committed infra/rollout/rollout-state.yaml NEVER records a promotion
#    (its validator still refuses any flag above off, unchanged) -
#    infra/rollout/live-state.yaml is the only file that does, and its own
#    validator requires --audit-record to point at a real file under
#    infra/rollout/audit/ or infra/rollout/approvals/.
python3 -m infra.rollout.cli promote <flag> --to <stage> \
  --approval <unique-id> --actor deployer-sa \
  [--canary-health-ok] [--gradual-complete] --verify-green \
  --live-state-out infra/rollout/live-state.yaml \
  --audit-record audit/<the-transition-audit-record>.md
```

- `→ gradual` additionally requires `--canary-health-ok` (measured, not claimed).
- `→ full` additionally requires `--gradual-complete`; `gradual` dwells 24h
  (ramp 10→25→50→100) per the stage model.
- A promotion refused without an approval is the design working, not a defect:
  `promote … → promote blocked: missing gate signal: approval_code` (proven in
  #618).

## Phase 0 — foundations (IaC + CI/CD)

Promote through the ladder, in order:

1. `ci_cd.verify_trigger` → full
2. `ci_cd.apply_trigger` → full

Then, and only then, import the apply trigger with the apply path enabled:

```bash
# cloudbuild trigger import with disabled:false and _ENABLE_APPLY:"true"
# (the repo's code-native CI/CD route — GR-15: no GitHub Actions).
```

The apply pipeline stays fail-closed until `_ENABLE_APPLY` is deliberately
`"true"` (its gate-flag step refuses otherwise — quoted in #618).

## Phases 1–6 — service ladder (strict-by-phase)

`infra/rollout/go-live-plan.yaml` enforces `promotion_order: strict-by-phase`:
phase 7 may not go-live before every earlier phase. Promote each to `full`
through the same ladder, in order:

`services.registry` → `services.gateway` → `services.engine` →
`services.guardrails` → `services.telemetry` → `services.identity`

## Phase 7 — control plane + portal + public web UI

1. `services.portal` → full
2. `services.web` → full

The now-enabled web surface deploys through the deployer-SA apply pipeline;
`infra/terraform` builds the web-surface service from the real image
(`us-central1-docker.pkg.dev/<project>/ao-images/portal:<commit-sha>`, assembled
from `project_id` + `web_image_tag` — see child #617).

## Acceptance (epic #607)

- [ ] `grep -E 'stage: "full"' infra/rollout/live-state.yaml` shows every
      promoted flag at `full` (issue #914: `rollout-state.yaml` stays the
      declared-default document and never carries a promoted stage -
      `live-state.yaml` is the committed record of what is actually live).
- [ ] `grep -E "_ENABLE_APPLY" infra/cloudbuild/apply-trigger.yaml` shows
      `"true"`.
- [ ] `ai.purebliss.app` serves the fleet single-pane-of-glass
      (`portal/server/fleet.py` routes), not the legacy seeded demo state.

## Rollback

`rollback_rules: mode auto, trigger health_failure, target "off"` — a health
failure rolls the failed flag back to `off` automatically and audited. Manual
rollback of any flag re-runs the same CLI in reverse direction, with its own
approval + audit record (strict-forward blocks silent jumps).

## Audit trail

Every transition appends to the audit log. The go-live closes with the full
trail quoted: approvals, promotions, the apply pipeline run log, and the
acceptance greps above.
