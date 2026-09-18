# Go-live runbook — fleet surfaces to production at ai.purebliss.app

The operator-facing runbook for epic #607. It is ONE command per phase, not the
~62-86 manual CLI invocations it used to be: the ordered driver
[`go_live.py`](go_live.py) reads the plan, computes the phase order, enforces
it, drives each flag through the adjacent stage ladder, refuses without the
owner's approval codes, enforces the declared 24h hold, writes a real audit
record per transition, and is safe to re-run after a partial run.

## Hard floor — never violated by anything in this runbook

- **No console clicks, no ad-hoc `terraform apply`** (GR-5). The only apply
  route is [`infra/cloudbuild/apply.yaml`](../cloudbuild/apply.yaml) running as
  the deployer service account.
- **No credential or secret in the tree** — the pipelines resolve `$_DEPLOYER_SA`
  by substitution at go-live; nothing here embeds one.
- **No promotion without the owner's approval code.** The driver refuses BEFORE
  it writes anything; a promotion is never "mostly approved".
- **Nothing in this runbook has been executed for real.** Every command below is
  prepared code; the offline rehearsal (`--preflight`, `--dry-run`) is what has
  actually been run.

## The ONE external boundary (what this repository cannot supply)

1. **A GCP project with the deployer service account provisioned** and the
   Cloud Build triggers imported (`infra/cloudbuild/*-trigger.yaml` ship
   `disabled: true`; importing them enabled is a deliberate operator act).
2. **The owner's approval codes** — one approval-as-code record per
   (flag, target stage), granted by an approver **distinct** from the executing
   actor (AO-GR-14). The driver resolves them from `infra/rollout/approvals/`;
   nothing in this repository can mint one.
3. **The operator's health verdict** for the canary (`--canary-health-ok`). The
   driver does not measure health; it refuses to move a flag past canary
   without that attestation.

## Pre-conditions (measure, do not assume)

- [ ] Web image wired (child #617 merged): the placeholder is gone
      (`git grep -n "Placeholder until the web build" origin/master -- infra/` clean)
      and `web_image_tag` is the documented commit-sha convention.
- [ ] `make verify` PASS on `origin/master` at the go-live start commit
      (attestation `git_sha` quoted in the audit trail).
- [ ] [`rollout-state.yaml`](rollout-state.yaml): every flag still `stage: "off"`
      (this file stays the declared-default document forever — GR-28 — and
      never records a promotion).
- [ ] [`live-state.yaml`](live-state.yaml): `flags: {}` (nothing promoted yet).
- [ ] `infra/cloudbuild/apply-trigger.yaml`: `disabled: true`.
- [ ] `infra/feature-flags/registry.yaml`: `ci_cd.apply_trigger` off.

## Step 0 — prove readiness offline (no writes, no cloud call)

```bash
python3 infra/rollout/go_live.py --preflight
```

Exit codes are tri-state: **0** the run is ordered and lawful, **1** refused
(strict-by-phase, a recorded stage above its declared target, or
`--require-approvals` with a code missing), **2** a declaration or the recorded
evidence cannot be assessed (unparseable plan, a live-state entry whose audit
record is missing, a broken audit chain). The report lists every planned
transition, the pending holds, and an `OWNER-GATED:` block naming each
transition whose approval code is absent.

## Step 1 — the owner grants the approval codes

One record per (flag, target stage), in `infra/rollout/approvals/`:

```bash
python3 -m infra.rollout.cli grant-approval services.registry \
  --to full --approver owner-kushin77 --approval-id ao-2026-09-16-registry-full \
  --approvals-dir infra/rollout/approvals
```

`canary` and `gradual` are policy-auto-approved on green verification evidence
([`stage-model.yaml`](stage-model.yaml) `policy_auto_approve`), so the owner
writes only the codes that reach `full` — the human-gated final promotion. The
approver must differ from the executing actor (`--actor deployer-sa`).

## Step 2 — phase 0 (foundations: IaC + CI/CD)

```bash
python3 infra/rollout/go_live.py --phase 0 --canary-health-ok \
  --actor deployer-sa --approvals-dir infra/rollout/approvals
```

Then, and only then, import the apply trigger with the apply path enabled:

```bash
# cloudbuild trigger import with disabled:false and _ENABLE_APPLY:"true"
# (the repo's code-native CI/CD route — GR-15: no GitHub Actions).
```

The apply pipeline stays fail-closed until `_ENABLE_APPLY` is deliberately
`"true"` (its gate-flag step refuses otherwise — quoted in #618).

## Step 3 — phases 1-6 (the service ladder)

```bash
python3 infra/rollout/go_live.py --phase 1-6 --canary-health-ok \
  --actor deployer-sa --approvals-dir infra/rollout/approvals
```

`promotion_order: strict-by-phase` is ENFORCED BY THE DRIVER: phase N+1 is
refused while any earlier phase's flag is short of its declared go-live stage,
and the refusal names the blocking flag. Phases 0-8 can also be driven in one
pass (`--phase 0 --phase 1-6 --phase 7 --phase 8`), which is what the single
command is for.

## Step 4 — phase 7 (control plane + portal + public web UI)

```bash
python3 infra/rollout/go_live.py --phase 7 --canary-health-ok \
  --actor deployer-sa --approvals-dir infra/rollout/approvals
```

The now-enabled web surface deploys through the deployer-SA apply pipeline;
`infra/terraform` builds the web-surface service from the real image
(`us-central1-docker.pkg.dev/<project>/ao-images/portal:<commit-sha>`, assembled
from `project_id` + `web_image_tag` — see child #617).

## Step 5 — the declared 24h hold: re-run the SAME command

[`stage-model.yaml`](stage-model.yaml) declares `gradual.ramp.dwell: 24h`. The
driver measures that hold from `since` — the timestamp of the recorded
transition INTO `gradual` — so a run that enters a hold reports `waiting` with
the exact earliest resumption time and exits 1 (the requested scope is not
complete). Re-running the identical command after the hold resumes exactly
where it stopped: a flag already at its target is skipped, and nothing is
promoted twice.

```bash
# tomorrow, or any time after the reported "earliest" timestamp
python3 infra/rollout/go_live.py --phase 0 --canary-health-ok \
  --actor deployer-sa --approvals-dir infra/rollout/approvals
```

## After each promotion — project it into the declaration the console serves (issue #967)

The ladder records a **flag's** stage in [`live-state.yaml`](live-state.yaml);
the console decides whether a surface answers from
`infra/feature-flags/registry.yaml`'s `surfaces.<name>.default`
(`portal/server/fleet.py`, with the runtime rollback overlay on top). The
coupling between the two is declared in [`projection.py`](projection.py), and a
promotion that has not been carried across it is **detectable rather than
assumed**:

```bash
# 1. is anything the ladder promoted still unprojected? (names each one)
python3 -m infra.rollout.projection --check

# 2. the projection itself — review the diff and commit it (the PR is the audit record)
python3 -m infra.rollout.projection --write
```

Exit codes are tri-state: **0** every promotion is projected, **1** one or more
is unprojected (reported by name, with the declaration behind it), **2** a
declaration could not be read or the line to project into could not be located —
the projector refuses rather than guessing, and a refusal writes nothing.

`--write` touches exactly the named entry's own `default:` line (and its
`promoted:` flag), leaving every comment and unrelated row byte-identical; a
second run is a no-op. The gate of record is
[`../../scripts/check-rollout-projection.sh`](../../scripts/check-rollout-projection.sh),
part of `make verify`: it drives a genuine sandboxed promotion and asserts that
the check reports it by name, that after `--write` the console's own reader
answers `on` from the projected declaration (and `off` from the shipped one),
and that `--write` refuses when the line cannot be located.

## Acceptance (epic #607)

```bash
# every promoted flag is recorded at full in the LIVE state (issue #914:
# rollout-state.yaml stays the declared-default document and never carries a
# promoted stage - live-state.yaml is the committed record of what is live)
grep -nE '^[[:space:]]+stage: "?full"?$' infra/rollout/live-state.yaml

# every promotion the record carries is projected into the declaration the
# console serves (issue #967) - exit 0 means nothing is left unprojected
python3 -m infra.rollout.projection --check

# the driver agrees: every requested transition is promoted and recorded
python3 infra/rollout/go_live.py --preflight

# and this lane's own gate is green
python3 infra/rollout/checks/check_rollout.py

# the apply trigger is enabled only at this point
grep -E "_ENABLE_APPLY" infra/cloudbuild/apply-trigger.yaml

# the surface serves the fleet single-pane-of-glass, not the legacy seed
# (portal/server/fleet.py routes)
```

**Corrected acceptance criterion.** #619's issue body asks for
`grep -E 'stage: "full"' infra/rollout/rollout-state.yaml`. That grep became
UNSATISFIABLE with #914: `rollout-state.yaml` is the declared-default document
(GR-28) and its validator refuses any flag above `off`, so a grep that passed
there would mean the run had corrupted the defaults file. The correct file is
[`live-state.yaml`](live-state.yaml) — the only committed record of what is
actually live — and the grep above is written to match what the writer actually
emits (an unquoted `full`; `from_stage: 'off'` IS quoted, because YAML 1.1
would otherwise read `off` as a boolean).

## Rollback

`rollback_rules: mode auto, trigger health_failure, target "off"` — a health
failure rolls the failed flag back to `off` automatically and audited, and the
rolled-back flag's live-state entry is simply omitted (a rollback never leaves
a stale exposure behind). Manual rollback re-runs the same CLI in reverse,
with its own audit record; strict-forward blocks silent jumps.

```bash
python3 -m infra.rollout.cli rollback services.registry \
  --reason manual_rollback --actor deployer-sa \
  --audit-log infra/rollout/audit/promotion-audit.jsonl \
  --live-state-out infra/rollout/live-state.yaml
```

## Audit trail

Every transition appends to the hash-chained log
(`infra/rollout/audit/promotion-audit.jsonl`) and writes a record file that the
new live-state entry names — written BEFORE that entry, so the gate can never
find a promoted flag with no provable trail. The format, the writers, and the
rule that a record is evidence (command + real output) rather than a claim are
in [`audit/README.md`](audit/README.md). The go-live closes with the full trail
quoted: approvals, promotions, the apply pipeline run log, and the acceptance
output above.
