# Go-live promotion attempt — epic #607 (2026-09-16)

Actor: `deployer-sa` (session `issue-607-promotions`, AI-assistance: Claude Code).
Scope: promote every flag in `infra/rollout/go-live-plan.yaml`, strict-by-phase,
off → canary → gradual, using only `python3 -m infra.rollout.cli ...`.

## Result: every promotion refused before any state changed

Two independent, structural blockers were found and reproduced against a
scratch copy of the state file (never committed, working tree left clean).
Both stop **every** flag at the first step; no flag reached `canary` in the
committed `rollout-state.yaml`.

### 1. The engine refuses to persist a promoted flag into `rollout-state.yaml`

`infra/rollout/model.py::validate_rollout_state_doc` — invoked by both
`RolloutEngine.load` (`infra/rollout/engine.py`) and the gate
(`infra/rollout/checks/check_rollout.py`) — rejects any flag whose committed
`stage` is not `"off"`:

```
flag '<name>' must default to off, got '<stage>'
```

Reproduced: `python3 -m infra.rollout.cli promote ci_cd.verify_trigger --to
canary --verify-green --actor deployer-sa --state-out <scratch>/state-after.yaml`
succeeds and writes a mutated file; reloading that file with
`RolloutEngine.load(...)` raises `RolloutError: flag 'ci_cd.verify_trigger'
must default to off, got 'canary'`, and running
`infra/rollout/checks/check_rollout.py` against that file as
`rollout-state.yaml` fails with the same message (exit 1).

Consequence: **the committed `rollout-state.yaml` structurally cannot record
a promoted flag.** Committing a CLI-written state file with any flag above
`off` would (a) make every subsequent `cli` invocation refuse to load the
repo's own state, and (b) fail `check_rollout.py` / the rollout lane's own
gate. This is not a policy choice this session can route around — hand-editing
the file is explicitly out of scope, and the CLI itself enforces the same
invariant it would be asked to violate.

Note: `infra/rollout/README.md`'s header comment on `rollout-state.yaml`
("rejects any flag whose current stage is not `off`, so a new surface can
never ship on by accident") and `infra/rollout/GO-LIVE-RUNBOOK.md`'s
acceptance check (`grep -E 'stage: "full"' infra/rollout/rollout-state.yaml`
"shows every promoted flag at `full`") **contradict each other** as currently
written: the runbook's acceptance criterion cannot be satisfied by the file
the model layer validates. Flagging this rather than silently picking a side.

### 2. No real canary health signal exists to gate `canary → gradual`

Independent of (1): `infra/rollout/GO-LIVE-RUNBOOK.md` requires
`--canary-health-ok` to be "measured, not claimed." Preconditions confirm
nothing is deployed — `infra/cloudbuild/apply-trigger.yaml` is
`disabled: true`, `ci_cd.apply_trigger` is off in the flag registry — so
there is no live traffic slice anywhere to measure. Passing
`--canary-health-ok` here would fabricate a signal, which the task
explicitly forbids. Every flag would stop at `canary` even if (1) were not
blocking.

## What was NOT done

- No flag's `stage` in `infra/rollout/rollout-state.yaml` was changed.
- No approval-as-code files were minted (`grant-approval` was not run for any
  flag — canary/gradual are policy-auto-approved per
  `infra/rollout/stage-model.yaml`, so none were needed for the attempted
  step, and `full` requires a human approver distinct from `deployer-sa`,
  which this session cannot supply).
- `infra/cloudbuild/apply-trigger.yaml` was not touched.
- No `terraform apply` / `gcloud builds submit` was run.
- `services.web` / `services.portal` were not reached (blocked upstream by
  finding 1 before phase ordering was even relevant); their real-image
  dependency (#606) was not exercised.

## Verify evidence

`make verify` observed `PARKED (rc 11)` under the box-wide gate cap
(`AO-GR-22`, issue #724) for the duration of this session's polling window;
retried per the runbook's own PARKED-retry convention. See PR description for
the final observed status at submission time.

## Owner action required to proceed

1. Resolve the contradiction in finding 1: either `rollout-state.yaml`'s
   validation is relaxed for committed *current* state (diverging from its
   documented "never ship on by accident" invariant), or the promotion
   pipeline's real current state is intended to live somewhere other than
   this committed file (e.g. a runtime/state-store path passed via
   `--state-out`, never checked into `master`) and `GO-LIVE-RUNBOOK.md`'s
   acceptance grep is corrected to match. This is an engine/doc design
   decision, not something this session can resolve unilaterally.
2. Stand up the real canary health signal path the runbook assumes
   (`--canary-health-ok` needs a genuine measurement) before any flag can
   leave `canary`.
3. Once (1) is resolved, re-run the promotion ladder from `off` through
   `canary` for phase 0 first (`ci_cd.verify_trigger`, `ci_cd.apply_trigger`),
   then each later phase in `go-live-plan.yaml` order, per
   `GO-LIVE-RUNBOOK.md`.
4. `full` and the downstream apply stay human-gated regardless; see the PR's
   "Owner steps remaining" section for the literal commands.
