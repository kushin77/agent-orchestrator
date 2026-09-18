# PR-RUNNER.md — the shared-services PR runner

`fleet/runner/` (issue #1343, parent #1295) is the institutionalised form of
the 2026-09-18 prototype that ran as nohup shell scripts in `~/ao-runner` on
192.168.168.42: on a schedule it verifies every open PR head that lacks
evidence, publishes `ao/gate-of-record` for the exact head sha, and merges the
greens through the guarded entrypoint once master + PR are proven green
together. **The runner merges; sessions open PRs.**

## What one cycle does (`python3 fleet/runner/cli.py run --once [--apply]`)

0. **Host role.** `AO_RUNNER_HOST_ROLE` (declared in
   `infra/fleet/env_contract.py`, `infra/env/registry.yaml`) must be `primary`;
   anything else is `host-role-not-primary` (CANNOT-ASSESS, rc 2). The rung is
   installed only on the shared-services pair, and this is the second wall.
1. **Preconditions by name** (lesson 5): `gh` present and authenticated, else
   `gh-unauthenticated`; a missing `gcloud` is a NOTE (live Cloud Builds
   cannot be listed or cancelled, check-runs are still read through `gh`).
2. **`fleet/gatelock.py prune --apply`** (lesson 7): leaked gate-lock permits
   from killed runs are pruned before any verify; a prune that cannot reach a
   verdict plans NO verify this cycle (`gatelock-prune-failed`).
3. **One locked fetch** of `origin/master` with an explicit refspec (lesson 8)
   gives the master tip.
4. **Gather**: open non-draft PRs + head shas; evidence per `(pr, sha)` from
   the `control-plane-verify` check-run, the `ao/gate-of-record` status and the
   local markers; live builds; the hold set.
5. **Plan** (`fleet/runner/plan.py`, pure) → ordered actions.
6. **Execute**: cancel stale builds → verify up to the fan-out width in
   parallel, each in its own held worktree → merge the greens.

The width is declared, never a literal: `AO_RUNNER_CAPACITY` (env contract;
empty = `min(8, nproc // 2)`), `--capacity` overrides per run. Before each
fan-out `fleet/runner/capacity.py` backs it off — never below 1 — when the
1-minute load exceeds nproc (`capacity-backoff:load:<load>/<nproc>`) or
MemAvailable is under `AO_RUNNER_MEM_FLOOR_GB` (default 8,
`capacity-backoff:memory:<gb><floor>`); the `capacity` ledger row carries
declared/effective/reason and `status` prints it.

Merges are **dry-run unless `--apply`**: the merged-tree seam runs and
`scripts/merge-pr.sh` runs in its own dry-run mode. The verify + post half is
always real (a verify writes nothing to the repository but a commit status).

## Evidence ranking (lesson 5)

| rank | source | read from |
|---|---|---|
| 0 | `cloud-build` | the `control-plane-verify` check-run for the head sha |
| 1 | `gate-status` | the `ao/gate-of-record` commit status read back for the sha |
| 2 | `local-marker` | `.fleet/runner/local-green/<pr>-<sha>` written by `verify.py` |

Evidence is keyed by `(pr, head sha)`, never by PR (lesson 1). Any green is
green; the **best-ranked** green is the basis and every other record is kept
beside it, so a host-env red on the box cannot block a merge Cloud Build
proved. CANNOT-ASSESS, EXPIRED, CANCELLED and PARKED are terminal-not-green:
the head is **re-queued by name, never awaited** (lesson 2). RED is a verdict
and is not re-run. A merged-tree record whose `base_tip` is not the current
master tip is `merged-tree-stale` and set aside (lesson 3).

## The merge (lessons 3 and 4)

`fleet/runner/merge.py` consumes `scripts/pr-queue.sh --check-merged-tree
<pr> --head <sha> --against-base origin/master` (#1332) — never a copy of it —
and refuses by name (`merged-tree-unverified:<pr>`, `merged-tree-red:<check>`,
or `merged-tree-seam-missing:<pr>` when the checkout predates #1332). Then it
runs `scripts/merge-pr.sh --pr <pr>`, the ONE merge verb: the squash guard
runs first (`squash-message-would-drop-trailer`), and the trailer /
`Closes #<n>` are its business (#1266). After an applied merge the landed tip
is classified by `governance.isolation.trailer.classify_commit`; a finding is
`landed-tip-noncompliant:<sha>` and stops the cycle.

## Worktrees, fetches, locks (lessons 6-8)

Each verify runs in `.fleet/runner/worktrees/<pr>-<sha12>`, a detached
worktree the runner **holds with an open fd for the whole run** and removes
itself afterwards — `scripts/prune-worktrees.sh`'s content-equivalence rule
(#1335) reaps an unheld detached tree, and the prototype lost verifies that
way. Every `git fetch` goes through `.fleet/runner/fetch.lock` and names
explicit refspecs (`+refs/heads/master:refs/remotes/origin/master`,
`+refs/pull/N/head:refs/remotes/origin/pr/N`), so two fetches cannot race on
`cannot lock ref` and `origin/master` exists on a detached checkout.

## Holds and status (lesson 9)

```bash
python3 fleet/runner/cli.py hold 1234 --reason "owner review"   # out of verify and merge
python3 fleet/runner/cli.py unhold 1234
python3 fleet/runner/cli.py status   # per PR: last verify, post, merge, await, block — by name
python3 fleet/runner/cli.py plan     # the actions for this cycle, nothing executed
python3 fleet/runner/cli.py plan --fixture fleet/runner/fixtures/lesson-1-stale-head.json
```

Every step is a row in `.fleet/runner/ledger.jsonl` (`cycle-start`, `plan`,
`verify`, `post`, `post-skipped`, `merged-tree`, `merge`, `merge-dry-run`,
`refuse`, `cancel-stale`, `await`, `hold`, `stop`, `cannot-assess`,
`cycle-end`); `status` answers from the ledger, not from memory.

## The status (lesson 10)

`scripts/gate-status.sh post --sha <head> --rc <0|1|2>` is the poster; the
context it writes (`ao/gate-of-record`), `scripts/gate-status-map.py`'s
`CONTEXT`, `fleet/runner/model.py`'s `GATE_CONTEXT` and
`governance/platform/branch-protection.yaml`'s `required_status_contexts` are
one string — asserted by `scripts/check-gate-status.sh` and
`scripts/check-pr-runner.sh`. A PARKED verify (rc 10/11) is not an outcome and
is never posted; any other unknown rc is posted as CANNOT-ASSESS (`error`),
never a pass.

## The schedule

`config/fleet-jobs.json` declares the `runner` job (`ao-fleet-runner`, every
3 minutes, singleton) **role-gated**: `enabled: false` plus
`enabled_when: {env: AO_RUNNER_HOST_ROLE, equals: primary}`. `fleet/cron.py`
renders the line only where the env contract says `primary`, with the role
inline (`env AO_RUNNER_HOST_ROLE=primary ...`); everywhere else the job is
declared, reconciled and never installed.

### Owner steps on the pair (192.168.168.42)

The pair's git clone is `~/ao-verify-repo` (`~/agent-orchestrator` there is a
NON-git copy); its interpreter is the `~/ao-verify-venv` venv (python 3.14).

```bash
export PATH=$HOME/ao-verify-venv/bin:$HOME/.local/bin:/snap/bin:$PATH
cd ~/ao-verify-repo && git fetch origin master && git checkout master && git pull --ff-only
AO_RUNNER_HOST_ROLE=primary python3 fleet/runner/cli.py run --once          # dry-run merges; verifies + posts
AO_RUNNER_HOST_ROLE=primary python3 fleet/runner/cli.py run --once --apply  # merges the greens
AO_RUNNER_HOST_ROLE=primary python3 fleet/cron.py install                   # schedule the rung
python3 fleet/cron.py status                                                # the ao-fleet-runner line is installed
pkill -f 'ao-runner/host-verify.sh'; pkill -f 'ao-runner/host-merge.sh'; rm -rf ~/ao-runner   # retire the prototype
```

The container image sets `AO_RUNNER_HOST_ROLE=primary` on the primary service
only (the compose file's env), never on a standby. The role is set per command
or in the rung's own line — never exported into a shell where `make verify`
runs: the flag-only readers (`infra/fleet/healthz.py`, `check-fleet-jobs.sh`,
the image inventory) derive the four-rung set with `enabled_jobs(manifest)`
and no environment, while `install`/`render`/`reconcile`/`status` pass the
ambient environment and are the only role-aware paths.

## The gate

`scripts/check-pr-runner.sh` (auto-wired by `scripts/discover-checks.sh`):
the lesson 1-5 and 7 fixtures refuse by name through the real cli; the
lesson 6-9 controls run under fakes with pytest's exit codes mapped
individually; the lesson-10 context parity is asserted; and two mutants of a
scratch copy — the stale-head skip dropped, the merged-tree check dropped —
are both caught. The lessons themselves are `LESSON-0009`..`LESSON-0014`
(closed on landed foundations) and `SUGGEST-0014`..`SUGGEST-0017` (open until
this PR's squash sha lands, then closed as lessons) in
`governance/lessons/ledger.jsonl`, all under `RCA-0019`.
