#!/usr/bin/env bash
# Gate orchestrator + attestation for `make verify` / `make gate` (GR-12).
#
# Runs every check in order, tees the full transcript to .verify/verify.log,
# writes .verify/attestation.json (timestamp, host, git sha, branch,
# per-check results, overall exit code) and exits with the REAL aggregate exit
# code. A check that cannot fail is a formality and is rejected
# (no-false-green doctrine). No network and no containers are required.
#
# Tri-state, honest (GR-12): each check's exit code is 0 = PASS, 1 = NOT-OK (a
# real defect -- the run goes red), 2 = CANNOT-ASSESS. A check that says it
# genuinely could not assess is recorded as SKIP -- never a pass and never a
# failure -- and is named in the summary and in the attestation, so a skip can
# never hide. Only a definite NOT-OK (or an unexpected code) fails the run.
#
# The check set includes the declared `fleet` pytest suite (`pytest-fleet`) and
# the declared capstone `e2e` suite (`e2e`).
# The gate of record must exercise the tests it claims to cover: a red fleet
# suite sat on master undetected because this gate ran no pytest at all — only
# `make gate` / `make tests` did (gate gap, issue #331). The capstone E2E suite
# had the same gap: it was declared in scripts/pytest-suites.txt, but only `make
# gate` / `make tests` ran it, so the EPIC-00 Definition-of-Done proof could be
# skipped outright (issue #525). The remaining declared suites are still
# exercised only by `make gate`; `governance/lessons` is red for an unrelated
# board-hygiene defect (#312).
#
# ADMISSION CONTROL (issue #724). The operator measured 49 concurrent `make
# verify` runs, 43 of them stacked in two worktrees, ~16 hours of duplicated
# work. This gate therefore admits work instead of assuming it: it takes an
# exclusive lock on its OWN worktree and one box-wide permit BEFORE it discovers
# a check or touches `.verify/`, and parks (running nothing) if either is
# unavailable. The control is self-applying — it is these lines, not a snippet an
# operator pastes — so no gate run can bypass it. It is proven end to end by
# scripts/check-gate-lock.sh, which starts a real second `scripts/verify.sh` in
# one worktree and requires it to be refused by name.
#
# Usage: scripts/verify.sh [verify|gate]
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 1

mode="${1:-verify}"

# --- admission control (issue #724) -----------------------------------------
# One composite gate per worktree, bounded box-wide by a permit store outside
# every workspace. This runs BEFORE any check is discovered and BEFORE `.verify/`
# is touched, so a gate that cannot get a permit is PARKED: it runs no check and
# it overwrites no previous attestation. The exit codes are deliberately OUTSIDE
# this gate's own 0/1/2 tri-state (0 PASS / 1 NOT-OK / 2 CANNOT-ASSESS) so a
# parked run can never be read as a pass or as a failure: 10 another gate holds
# this worktree, 11 the box-wide cap is reached, 12 the permit store is unusable.
bash "$root/scripts/gate-lock.sh" acquire --worktree "$root" --mode "$mode" \
  --owner-pid $$
lock_rc=$?
if [ "$lock_rc" -ne 0 ]; then
  case "$lock_rc" in
    10) echo "verify: PARKED (rc 10, not a pass and not a failure) — another gate already holds this worktree; nothing was run and no attestation was touched" >&2 ;;
    11) echo "verify: PARKED (rc 11, not a pass and not a failure) — the box-wide gate cap is reached; nothing was run and no attestation was touched" >&2 ;;
    *) echo "verify: CANNOT-ASSESS (rc $lock_rc, not a pass and not a failure) — the gate permit store is unusable; nothing was run and no attestation was touched" >&2 ;;
  esac
  exit "$lock_rc"
fi
# Only a gate that ACQUIRED installs the release traps: a refused gate exits
# above, before these lines, so it can never release a lock it does not own
# (and `release` refuses anyway when the lock belongs to another live gate). The
# traps cover EXIT — the normal path — and INT/TERM/HUP, each mapped to a real
# exit code so the EXIT trap runs and the lock is released. A gate killed
# outright cannot run any trap; its holder watches this shell's pid and releases
# the moment the shell is gone, SIGKILL included.
trap 'bash "$root/scripts/gate-lock.sh" release --worktree "$root" --owner-pid $$ >/dev/null 2>&1' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

verify_dir="$root/.verify"
mkdir -p "$verify_dir"
log="$verify_dir/verify.log"
results_tsv="$verify_dir/.results.tsv"
: > "$log"
: > "$results_tsv"

# name|command — every command is an honest gate (real exit code, can fail).
checks=(
  'shell-syntax|bash scripts/check-shell-syntax.sh'
  'python-syntax|bash scripts/check-python-syntax.sh'
  'yaml-lint|python3 scripts/check-yaml.py'
  'json-lint|bash scripts/check-json.sh'
  'docs-lint|bash scripts/check-docs.sh'
  # squash-message (issue #1102, parent #878): a detector nothing calls is
  # advisory. This self-test proves scripts/check-squash-message.sh can fail
  # before the merge-path refusal (governance/lifecycle/cli.py,
  # scripts/pr-queue.sh) is trusted to call it for real.
  'squash-message|bash scripts/check-squash-message.sh --self-test'
  # gate-coverage (issue #526, RCA of EPIC #524): the gate registry was not
  # self-checking. A new `scripts/check-*.sh` that nobody registers is inert,
  # and a suite declared in scripts/pytest-suites.txt that no gate names is only
  # reached by the manifest sweep -- so coverage was opt-in and nothing
  # complained. This check names BOTH classes of ungated artifact and fails on
  # any that is not in a live, reasoned baseline entry
  # (scripts/gate-coverage-baseline.txt); the baseline is checked in both
  # directions, so a stale entry (its artifact is wired now, or gone) fails too
  # and the list cannot rot into a fiction. It is registered here deliberately:
  # this array is explicit, so the detector is itself inert until it is named.
  'gate-coverage|bash scripts/check-gate-coverage.sh'
  # branch-protection (epic #803, P0-3): the platform-level enforcement of the
  # gate of record. Measured 2026-09-15: `master` was NOT protected (the API
  # answered 404) while `AGENTS.md` claimed it was "protected by convention" and
  # that branch protection as code "ships with issue #6". The gate of record was
  # excellent and no platform control required anyone to run it. This check
  # compares the LIVE protection against the DECLARED policy and is PROVOKED
  # offline (a matching fixture must pass; removed protection must be caught and
  # named; an unprotected branch must be caught). An unobservable live state is
  # CANNOT-ASSESS (exit 2), never a pass -- found by mutation, not by review: the
  # first version printed "not a pass" and then exited 0.
  'branch-protection|bash scripts/check-branch-protection.sh'
  # repo-settings (issue #1138, parent #803): the platform-level enforcement
  # of the squash-merge message policy. Measured: the live repo setting was
  # `squash_merge_commit_message=COMMIT_MESSAGES`, concatenating every
  # per-commit message onto the squash commit instead of using the PR body --
  # burying the ticket trailer mid-body and redding check-isolation-landed on
  # every wave (#1119 #1044 #1121 #1126 #1103, four more baselined in #1130).
  # This check compares the LIVE settings against the DECLARED policy and is
  # PROVOKED offline (a matching fixture must pass; a reverted message policy
  # must be caught and named), mirroring branch-protection's own gate. An
  # unobservable live state is CANNOT-ASSESS (exit 2), never a pass.
  'repo-settings|bash scripts/check-repo-settings.sh'
  # gate-status (epic #803 P0-2, ADR-0028): GitHub's required status checks are
  # the only mechanism that makes a merge impossible without green evidence, and
  # producing one normally needs the GitHub Actions that GR-15 bans. ADR-0028
  # resolves that with a commit STATUS posted by the code-native runner -- no
  # Actions, no App, no check-run (proven: context=ao/gate-probe posted and read
  # back on master). This check proves the rc -> status mapping offline and
  # without writing anything: the mapping is exhaustive, the three gate outcomes
  # stay DISTINGUISHABLE, an unknown outcome is REFUSED rather than defaulted,
  # and CANNOT-ASSESS is published as `error` -- never as a pass, which is the
  # false-green class of #739. Requiring the context in branch protection is a
  # SEPARATE deliberate step: requiring one that nothing posts yet would block
  # every merge (#724: an inert control whose own gate could not see it).
  'gate-status|bash scripts/check-gate-status.sh'
  # no-actions (issue #812, GR-15): "No GitHub Actions workflows" was declared in
  # AGENTS.md and the fleet doctrine and enforced by NOTHING. Measured: the repo
  # was running `ci-failure-scanner.yml`, state=active, on a 10-minute cron --
  # violating its own rule silently -- because no gate looked. The scripts that
  # merely mention `.github/workflows` parse YAML, and gate-coverage does not
  # treat a workflow as an artifact at all, so a new one was not even visible.
  # This check ENFORCES the rule and is PROVOKED: it plants a workflow in a
  # temporary fixture and requires the detector to refuse it BY NAME, and plants
  # a baselined one and requires it to be ACCEPTED -- asserting only the empty
  # case would pass a detector that matches nothing.
  'no-actions|bash scripts/check-no-actions.sh'
  # cpapi-spec-drift (issue #816): `identity/cpapi/openapi.yaml` is a PUBLISHED
  # contract -- its own description says it is "the contract the generated clients
  # in `clients/` are built against". Measured 2026-09-15 it matched the router
  # exactly (29 routes / 29 paths, zero drift both ways) and NOTHING kept it that
  # way: no gate compared the two, so a route added, renamed or removed in code
  # would invalidate the contract silently. A contract that has drifted is worse
  # than no contract, because a consumer trusts it.
  #
  # Provoked in BOTH directions, because drift fails the consumer two ways: a
  # route in the CODE but not the SPEC under-documents (a client cannot call a
  # feature that exists), and a path in the SPEC but not the CODE over-promises
  # (a client generates against a route that 404s). A comparator reporting only
  # one class would look healthy while the other rotted.
  'cpapi-spec-drift|bash scripts/check-cpapi-spec-drift.sh'
  'chronological-dispatch|bash scripts/check-chronological-dispatch.sh'
  'issue-claims|bash scripts/check-issue-claims.sh'
  'dispatch-queue|bash scripts/check-dispatch-queue.sh'
  'epic-focus|bash scripts/check-epic-focus.sh'
  # capacity-gate (epic #707, lane F3/#718): the fan-out default is the MAXIMUM,
  # bounded by three real limits — effective = min(pool, disjoint ready lanes,
  # resource ceiling). Unbounded maximum is a verify storm (measured: 49
  # concurrent gates, 43 stacked in two worktrees, ~16h), and an unbounded
  # *claim* is a formality, so each of the three bounds is PROVOKED: the check
  # builds the input that would exceed it and requires the excess HELD *and the
  # holding bound named*, then requires the relaxed input ADMITTED — the two
  # paths cannot collapse into one exit code. It asserts the loop actually calls
  # the gate (no bare `active >= pool` remains) and mutation-proves both halves:
  # a resolution that cannot bind must turn the controls red, and removing the
  # loop's call must turn the wiring assertion red.
  'capacity-gate|bash scripts/check-capacity-gate.sh'
  'fleet-channel|bash scripts/check-fleet-channel.sh'
  'fleet-contract|bash scripts/check-fleet-contract.sh'
  'fleet-runbook|bash scripts/check-fleet-runbook.sh'
  'fleet-vocabulary|bash scripts/check-fleet-vocabulary.sh'
  # operator-access (issue #763): every surface an operator can use existed but
  # was undiscoverable — A2A read as an optional "extension", the 18 override
  # verbs were unnamed as an operator surface, and the console binds loopback so
  # it is unreachable from anywhere else. The check pins the access doc (one
  # probe group per surface), proves the two one-command targets delegate rather
  # than reimplement, DRIVES the honesty rule (tmux off PATH must fail loudly,
  # naming the reason, not report a success it cannot evidence), drives the
  # console's loopback default and its fail-closed session refusal from the
  # shipped modules, and strips each surface from a copy of the doc in turn — so
  # a surface that stops being named cannot pass silently.
  'operator-access|bash scripts/check-operator-access.sh'
  # operator-terminal (issue #774): the browser IT-terminal behind the SSO
  # session — one link (`/console`) that composes the fleet projection (read)
  # and the closed control-verb family (steer) behind the caller's own
  # os-session-token. The check proves the flag gate runs BEFORE AuthN (404
  # feature_disabled while surfaces.operator_terminal is off), the one link
  # reuses the session (unauthenticated -> /auth/login, authenticated -> the
  # view), the read half is served offline from a redirected runtime, and the
  # steer half is the closed vocabulary — an out-of-vocabulary action is
  # refused 422, a caller without the capability is refused 403 with no audit,
  # and an allowed steer lands on the audit rail. The flag gate is
  # mutation-proved (flipping the flag to "on" makes the reader say "on").
  'operator-terminal|bash scripts/check-operator-terminal.sh'
  # agentconsole-hosting (issue #1029, parent #607): the go-live was DECLARED for
  # months and never EXERCISED — the declarations said "nothing is live" and the
  # image recipe had never been run (its only builder was the retired Cloud Run
  # route), so a defect INSIDE the artifact was invisible (portal/Dockerfile
  # installed PyYAML but not cryptography, so the container exited 1 at boot
  # instead of serving its own CMD). This check asserts the hosting contract
  # offline — the image recipe's deps + CMD, the overlay's declared
  # container/port/network/health/mounts, the three composed surfaces promoted
  # TOGETHER (a partial promotion 404s half the console), the hosting doc naming
  # the LIVE host and the retirement (and no longer claiming it is undeployed),
  # and the fail-closed env contract — and proves it can fail by mutating one
  # property per rule on every run. Registered here deliberately: the array is
  # explicit, so the gate is itself inert until it is named.
  'agentconsole-hosting|bash scripts/check-agentconsole-hosting.sh'
  # runner-preflight (issue #733): the fleet could not spawn a single subagent
  # because the loop inherited cron's minimal PATH, and it failed per directive
  # per cycle (a runaway amplifier). The check names the resolution, the hold and
  # the CANNOT-ASSESS timeout semantics, drives the REAL loop in a scratch tree
  # with the runner off PATH and off HOME (exactly one escalation, nothing
  # dispatched, the queue held), and mutation-proves it with two mutants of the
  # real loop — the preflight neutralised, and the hold removed.
  'runner-preflight|bash scripts/check-fleet-runner-preflight.sh'
  # fleet-drift (issue #739, AO-GR-25): the code-drift detector compared the
  # running loop's commit to the *shared checkout's* HEAD, so when the checkout
  # was itself behind, both sides were the same stale commit and a loop running
  # pre-fix code reported `healthy` — the control that exists to catch a merged
  # fix never reaching the fleet could not fire. It also failed open: `head !=
  # "unknown"` made an unreadable HEAD read as healthy. The check proves drift is
  # measured against origin/master (the measured case: running == local, !=
  # remote ⇒ drifted), that an unreadable baseline is CANNOT-ASSESS and exit 2,
  # that the operator line names both commits, and mutation-proves it with two
  # mutants of the real classifier — the local-HEAD baseline restored, and the
  # fail-open guard restored.
  'fleet-drift|bash scripts/check-fleet-drift.sh'
  # watchdog-bounded (issue #773, AO-GR-21): drift detection is only half a
  # control — the other half is a remedy that can change what was compared. #739
  # made the watchdog compare the running commit to origin/master and RESPAWN on a
  # mismatch, but a respawn re-executes the same checkout: when the mismatch was
  # the CHECKOUT being behind, the watchdog took an action that could not change
  # the value it compared and repeated it without bound — measured on the live
  # fleet as 132 `drifted … — respawned` decisions, 45 clean stops, a brain never
  # older than 60s, and zero work done. The check names the two cases separately
  # (`checkout-behind` is repaired by a fast-forward, proven against a real git
  # checkout, never by a blind respawn), proves the remedy is bounded by an attempt
  # cap with backoff that escalates ONCE and then parks, proves a busy rung is
  # recorded pending rather than dropped every tick, and mutation-proves itself
  # with two mutants of the real source — the cap removed, and the local-HEAD
  # distinction removed — each required to diverge on a probe whose value must
  # change (a happy-path assertion cannot pass it).
  'watchdog-bounded|bash scripts/check-watchdog-bounded.sh'
  # spawn-envelope (issue #793): governance is DECLARED in governance/** but was
  # not ENFORCED AT SPAWN — the remote path inlined it as prompt prose nothing
  # could check, and the local path shared none of it, so one lane ran 26 gates
  # in one worktree while every other lane ran exactly one, and the fleet's own
  # liveness evidence contradicted itself. This check requires the envelope to be
  # a PRECONDITION: every required field is provoked missing and refused BY NAME
  # with its own exit code (78), the local path is driven for real against its own
  # board (admitted, and refused when its claim is absent), the fleet prompt must
  # EQUAL the envelope's rendering, run_in_flight() must decide on the run
  # marker's own evidence, and the one-gate bound is provoked with a real second
  # gate (AO-GR-22).
  'spawn-envelope|bash scripts/check-spawn-envelope.sh'
  'session-isolation|bash scripts/check-session-isolation.sh'
  'github-lifecycle|bash scripts/check-github-lifecycle.sh'
  'reconcile|bash scripts/check-reconcile.sh'
  # fleet-freeze (issue #715/#902): the D7 cutover drain flag and the real
  # crontab must never disagree — `.fleet/freeze.flag` set while a fleet-cron
  # line is still active is the split-brain state the cutover policy forbids;
  # the check's classifier is provoked with a synthetic active/commented pair.
  'fleet-freeze|bash scripts/check-fleet-freeze.sh'
  # dispatch-reconcile (issue #796): `sent` was treated as `done` — a dispatched
  # directive's marker was permanent, so a directive that DIED (quarantined,
  # dead-lettered, a phantom run) suppressed its issue for ever while the brain
  # reported itself idle with that issue in its own ready set (measured: idle
  # 1535s, 63 open issues, three ready and all three suppressed). The check drives
  # the real reconciler against a scratch runtime, and PROVOKES both halves: a
  # marker for an open issue with no live claim/run/directive must be re-armed
  # (counted, bounded, then parked by name), while a marker with live evidence must
  # stay suppressed — asserting the first half alone would pass a reconciler that
  # ignores the marker set. Two mutants (never-stale, liveness-ignored) must each
  # fail by name.
  'dispatch-reconcile|bash scripts/check-dispatch-reconcile.sh'
  # dead-letter (issue #754): the runaway guard (#723) retires a directive the
  # LOOP can prove is unrunnable; a peer agent or an operator must be able to say
  # so OVER THE CONTROL CHANNEL, not by `mv`-ing an order out of `.fleet/inbox/`
  # while the loop reads it. This check drives `control:drop` against the real
  # tree and proves the two callers — the automatic path and the verb — write the
  # SAME record shape from one implementation, with a negative control that an
  # undropped directive is still returned. Two mutants (the dropper removed, the
  # order left in the inbox) must each be refused BY NAME.
  'dead-letter|bash scripts/check-dead-letter.sh'
  # runaway-guard (issue #723, epic #708): the bounded-runaway gate itself was
  # on disk but unwired — the epic's own Verify section names this script and
  # requires it to provoke a runaway and FAIL, but `checks=()` had zero
  # matches, so the gate was inert per this file's own gate-coverage doctrine.
  # The script proves the attempt cap persists across a restart, the harvested
  # backoff is pinned, held paths share one counter, and a directive is
  # dead-lettered after K attempts — against the real tree and two mutated
  # scratch copies (CAP-DISABLED, BACKOFF-FLAT) that it must refuse BY NAME.
  'runaway-guard|bash scripts/check-runaway-guard.sh'
  'lease-policy|bash scripts/check-lease-policy.sh'
  'fleet-state|bash scripts/check-fleet-state.sh'
  'paperclip-gap-analysis|bash scripts/check-paperclip-gap-analysis.sh'
  'brain-profile|bash scripts/check-brain-profile.sh'
  'knowledge-index|bash scripts/check-knowledge-index.sh'
  'cross-reference|bash scripts/check-cross-reference.sh'
  'conformance|bash scripts/check-conformance.sh'
  'surface-class|bash scripts/check-surface-class.sh'
  'remediation|bash scripts/check-remediation.sh'
  'board-gate|bash scripts/check-board-gate.sh'
  'cross-repo-boundary|bash scripts/check-cross-repo-boundary.sh'
  # audit-read-model (issue #347): the tamper-evident trail served as a
  # read-only, deterministic, filterable read model; verify-chain is OK on an
  # intact chain and refuses a modified / reordered / removed record, and the
  # check mutates a temp copy of the chain, so it cannot pass vacuously.
  'audit-read-model|bash scripts/check-audit-read-model.sh'
  'gateway-catalog-parity|bash scripts/check-gateway-catalog-parity.sh'
  'guardrail-controls|bash scripts/check-guardrail-controls.sh'
  'guardrail-head-policy|bash scripts/check-guardrail-head-policy.sh'
  # agent-identity-parity (issue #346): the shared agent-identity schema's
  # closed vocabularies must equal agent-profile.schema.json + catalog.yaml and
  # every seed must validate as a projected identity; the check mutates its own
  # input, so it cannot pass vacuously.
  'agent-identity-parity|bash scripts/check-agent-identity-parity.sh'
  # provider-parity (issue #1194): flag UNEXPLAINED Claude/DeepSeek capability
  # drift without forcing literal parity (roles differ by design); every
  # asymmetric flag-gated module.json feature and capabilitySet/toolAllowlist
  # entry must carry an inline rationale marker, and the check mutates a
  # scratch copy with an unmarked item, so it cannot pass vacuously.
  'provider-parity|bash scripts/check-provider-parity.sh'
  # rbac-head-binding (issue #952): tenant/RBAC binding for the head-of-org
  # personas (hermes, paperclip) — identity/rbac/presets/head-agents.yaml +
  # identity/rbac/head_bindings.py; GR-28 default-off is asserted live and the
  # check deletes a bound persona's Binding row and requires the identical op
  # to flip to refused, so it cannot pass vacuously.
  'rbac-head-binding|bash scripts/check-rbac-head-binding.sh'
  'paperclip-adapter|bash scripts/check-paperclip-integration-adapter.sh'
  'paperclip-canonical-module|bash scripts/check-paperclip-canonical-module.sh'
  # paperclip-auth (issue #412, ADR-0013): the cross-boundary auth seam in
  # integrations/paperclip/auth/ must mint/verify agent identity from the
  # registry, map a human onto the board session path, bridge the run id, and
  # refuse every negative control by name (incl. 403-not-404); the check runs
  # the boundary suite and provokes each refusal, so it cannot pass vacuously.
  'paperclip-auth|bash scripts/check-paperclip-auth.sh'
  # EPIC-410 paperclip parity adapters (issue #420, the wiring lane): the
  # adapter gates below are registered here deliberately — a check that only
  # runs when someone remembers to run it is a formality, not a gate. Each
  # carries its own self-mutating negative control (a scratch-copy mutant the
  # gate must refuse BY NAME, so it cannot pass vacuously) and each subject
  # adapter's pytest suite is declared in scripts/pytest-suites.txt so `make
  # gate` runs it in isolation.
  'paperclip-approvals|bash scripts/check-paperclip-approvals.sh'
  'paperclip-deploy|bash scripts/check-paperclip-deploy.sh'
  'paperclip-heartbeat|bash scripts/check-paperclip-heartbeat.sh'
  'paperclip-openapi|bash scripts/check-paperclip-openapi.sh'
  'paperclip-secrets|bash scripts/check-paperclip-secrets.sh'
  'paperclip-skills|bash scripts/check-paperclip-skills.sh'
  # paperclip-diagrams (issue #465, completing #420): the diagrams blueprint
  # projection adapter landed before #420's wiring branch was cut and was still
  # not registered when the wiring lane ran, so it was missed twice. It has the
  # same self-mutating negative-control shape; its tests live inside the already
  # declared integrations/paperclip suite (integrations/paperclip/tests).
  'paperclip-diagrams|bash scripts/check-paperclip-diagrams.sh'
  # paperclip-routines (issue #418, completing #420): the routines adapter
  # landed AFTER #420's branch was cut, so the wiring lane could not register
  # it then. It is the same shape as its siblings; its suite is declared in
  # scripts/pytest-suites.txt (integrations/paperclip/adapters/routines).
  'paperclip-routines|bash scripts/check-paperclip-routines.sh'
  'issue-template|bash scripts/check-issue-template.sh'
  'finops-chooser|bash scripts/check-finops-chooser.sh'
  'lessons|bash scripts/check-lessons.sh'
  'pr-queue|bash scripts/check-pr-queue.sh'
  'paperclip-integration|bash scripts/check-paperclip-integration.sh'
  'ticket-projection|bash scripts/check-ticket-projection.sh'
  # pmo-rollup (issue #403): the PMO views are derived queries over the ticket
  # graph, never a store — no ledger, no surviving cache, no second source of
  # status; the gate provokes an owner-less risk, a RAID set that disagrees with
  # the graph and a rollup from a stale cache, and proves nothing is written.
  'pmo-rollup|bash scripts/check-pmo-rollup.sh'
  'secrets|bash scripts/check-secrets.sh'
  'feature-flags|python3 scripts/check-feature-flags.py'
  'cloudbuild|bash scripts/check-cloudbuild.sh'
  'terraform|bash scripts/check-terraform.sh'
  # scratch-safety (issue #488): one agent scratch log reached 14.8 GB and filled
  # this box's /tmp -- a 16 GB tmpfs, i.e. RAM -- stopping every parallel lane at
  # once; the knock-on was worse than the disk, because `cp` wrote a 0-byte
  # "backup" and restoring from it truncated a source file to empty. The check
  # proves each of its refusals with a provoked control -- SCRATCH-SELF-APPEND
  # (the incident's own driver), SCRATCH-FILE-OVERSIZE, SCRATCH-SPACE-NEAR-FULL,
  # SCRATCH-TMP-WORKTREE and SCRATCH-EMPTY-COPY -- and lints the repo's tracked
  # *.sh, so it cannot pass vacuously. The live machine verdict it prints is
  # ADVISORY by design: a gate that reddens because a NEIGHBOUR filled the tmpfs
  # reddens an unrelated diff, and the machine-level guard (its own timer) owns
  # that verdict; `--scan` is the verb that refuses by name on demand.
  'scratch-safety|bash scripts/check-scratch-safety.sh'
  # EPIC #144 (per-repo agent fleet) governance surfaces — each is tri-state
  # (0 OK / 1 NOT-OK / 2 CANNOT-ASSESS) and proves its own negative control.
  'cto-overlay|bash scripts/check-cto-overlay.sh'
  'authority|bash scripts/check-authority.sh'
  'rollup|bash scripts/check-rollup.sh'
  'fleet-template|bash scripts/check-fleet-template.sh'
  'sme-routing|bash scripts/check-sme-routing.sh'
  'registry-parity|bash scripts/check-registry-parity.sh'
  # EPIC #422 (cross-repo integration gaps) — each tri-state, each proving its
  # own negative control.
  'module-admission|bash scripts/check-module-admission.sh'
  'routing-seam|bash scripts/check-routing-seam.sh'
  'cross-repo-sync|bash scripts/check-cross-repo-sync.sh'
  'cross-repo-lessons|bash scripts/check-cross-repo-lessons.sh'
  'paperclip-budget|bash scripts/check-paperclip-budget.sh'
  'metering-parity|bash scripts/check-metering-parity.sh'
  # #445 (the M28 tail): the ecosystem module registry — mandatory status read
  # from the hub catalog, three honest states, membership refused by name, a
  # no-vendoring finding, deterministic rebuilds. It returns CANNOT-ASSESS (rc 2)
  # when `vendor/CMR` is not initialised, never a pass.
  'module-registry|bash scripts/check-module-registry.sh'
  # cmr-pin (issue #949, parent #878): the root `cmr-pin.yaml` the CMR module
  # template prescribes (vendor/CMR/templates/module/cmr-pin.yaml) was absent.
  # This gate validates it against the hub's own schema
  # (vendor/CMR/sync/cmr-pin.schema.json, via vendor/CMR/sync/validate-cmr-pin.py)
  # and refuses DRIFT: bundle_ref must equal the live `vendor/CMR` submodule
  # HEAD, proved by a provoked negative control (CMR-PIN-DRIFT) in a scratch
  # copy, never the real pin.
  'cmr-pin|bash scripts/check-cmr-pin.sh'
  # #447 (the M28 tail): the paperclip reporting agent's module brief — the
  # artifact composes only from #445's registry, every claim resolves to a
  # registry row or a cited hub path, Pending is never rendered as shipped, and
  # the capability the persona declares is one its allowlist actually grants.
  'module-brief|bash scripts/check-module-brief.sh'
  # EPIC #461 (the diagrams chain): diagrams-declaration (#464) proves the
  # architecture.yaml / gdc-manifest.yaml seeds conform to the vendored CMR
  # contract. (Its sibling, the #465 ADR-0017 projection gate
  # `paperclip-diagrams`, is already wired above with its #420 siblings.) This
  # check is tri-state: it is CANNOT-ASSESS (rc 2, visibly SKIPped) until
  # `git submodule update --init vendor/CMR` has run -- the normal state of a
  # fresh worktree -- while a genuinely wrong declaration (rc 1) still fails.
  'diagrams-declaration|bash scripts/check-diagrams-declaration.sh'
  # EPIC #472 (the codeidx consumption chain): the three gates that keep the
  # consumed indexer surface from silently rotting. `codeidx-surface` (#475)
  # proves the root .mcp.json carries the indexer server entry in the vendored
  # seed's shape AND that the root gdc-manifest.yaml still declares the
  # `code-indexing.mcp` pin; `codeidx-backend` (#476) proves the real indexer
  # backend is flag-gated OFF, labelled per path, and that an unreachable indexer
  # degrades explicitly instead of inventing results; `context-pack-consumption`
  # (#477) proves assemble_prefix consumes a pre-fetched codeidx pack as opaque
  # bytes from the published contract and leaves the absent-pack bytes exactly as
  # they were. `codeidx-surface` derives its expectation from the vendored seed,
  # so like its #461 sibling above it is CANNOT-ASSESS (rc 2, visibly SKIPped)
  # until `git submodule update --init vendor/CMR` has run -- the normal state of
  # a fresh worktree -- while a missing/drifted .mcp.json or a dropped pin (rc 1)
  # still fails.
  'codeidx-surface|bash scripts/check-codeidx-surface.sh'
  'codeidx-backend|bash scripts/check-codeidx-backend.sh'
  'context-pack-consumption|bash scripts/check-context-pack-consumption.sh'
  # e2e (issue #525): the capstone end-to-end suite. e2e/README.md calls this
  # subtree the gate of the whole product build — it proves the EPIC-00
  # Definition of Done (signup -> org -> personas -> agents -> routed model
  # calls -> audit + usage billing, across all six providers, with 10 real
  # negative controls in e2e/negative_controls.py). It was declared in
  # scripts/pytest-suites.txt but ran only under `make gate` / `make tests`,
  # which nothing enforces, so the DoD proof could be skipped and a broken DoD
  # could reach master. It is wired as an ordinary PASS/FAIL check — a real
  # failure is rc 1, never a SKIP — because the suite is offline, deterministic
  # and green, so the CANNOT-ASSESS third state does not apply.
  # PYTHONDONTWRITEBYTECODE + `-p no:cacheprovider` keep the gate clear of the
  # __pycache__ state that can make a later run read a stale module instead of
  # the tree under test.
  'e2e|env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q e2e/tests'
  # EPIC #494 (the monitoring program; issue #499 is the wiring lane): the
  # declaration gate (#496, ADR-0022) proves BOTH halves of the monitoring
  # declaration are real -- `module.json` carries exactly one flat
  # `{ "id": "prometheus", "type": "monitoring" }` integration (the shape
  # ADR-0022 D3 froze, with no invented pin key) AND `docs/OBSERVABILITY.md`
  # names the producer/consumer boundary (producer SSOT, Prometheus-plane
  # owner, capability tie-back, OTLP/HTTP push, the signal-to-ticket rule, the
  # DIFFERENT human-surface gap and the exposition lane). The check stages a
  # deliberately damaged scratch copy of each declared file and REQUIRES it to
  # be refused by name, so it cannot pass vacuously. It is registered here
  # deliberately: this array is explicit, so a new scripts/check-*.sh is never
  # auto-discovered and an unwired gate is a formality, not a gate.
  'monitoring-declaration|bash scripts/check-monitoring-declaration.sh'
  # EPIC #462 (diagrams) / EPIC #473 (codeidx) -- the capability registers: the
  # four enforcement checks delivered by #468/#469 and #479/#480, registered here
  # because this array is explicit and nothing is auto-discovered, so an unwired
  # check enforces nothing (GR-12). `diagrams-capability-register` (#469) and
  # `codeidx-capability-register` (#480) enforce each register's own row grammar
  # (header, cell count, status/owner vocabularies, ref shape) and provoke their
  # negative controls; `diagrams-capability-tracker` (#468) and
  # `codeidx-capability-tracker` (#479) reconcile every row's `ref` against the
  # RECORDED board snapshot, so a row that drifted from the board is surfaced by
  # name instead of rotting. Unlike their `diagrams-declaration` (#464) and
  # `codeidx-surface` (#475) siblings above, these four are offline and
  # deterministic -- no network, no `vendor/CMR` submodule and no vendored seed
  # -- so they RUN for real (rc 0) in a fresh worktree and are never recorded as
  # CANNOT-ASSESS.
  'diagrams-capability-register|bash scripts/check-diagrams-capability-register.sh'
  'diagrams-capability-tracker|bash scripts/track-diagrams-capabilities.sh'
  'codeidx-capability-register|bash scripts/check-codeidx-capability-register.sh'
  'codeidx-capability-tracker|bash scripts/track-codeidx-capabilities.sh'
  # EPIC-09 / #703 (the shared-frontend mandatory module, GR-18 /
  # CMR:ONBOARD-0003): the third mandatory module's onboarding. The gate proves
  # the root `tokens.json` is the vendored `--os-` twin at the pinned rev
  # (ace748f4 / v0.2.0, sha256 pinned in the lane), that `gdc-manifest.yaml`
  # still carries all three mandatory pins (`code-indexing.mcp`,
  # `diagrams.blueprint`, `shared-frontend.tokens`), and that BOTH root assets
  # are the render of governance/onboarding/shared-frontend/instance.yaml — with
  # a provoked control for every refusal (deleted twin, drifted twin, dropped
  # pin, unknown tenant, unparseable lane input), so it cannot pass vacuously.
  # Registered here rather than baselined, because a baseline row is refused for
  # a NEWLY delivered artifact: `scripts/check-gate-coverage.sh` fails "a row for
  # an artifact that did not exist at the baseline's own last-touched commit", so
  # wiring is the only honest route for a new check. Offline and deterministic:
  # no `vendor/CMR` submodule is required (the pinned digest is the authority and
  # the vendored seed is a cross-check that runs when the submodule IS
  # initialised), so it READS OK (rc 0) in a fresh worktree rather than SKIPping.
  'shared-frontend-onboarding|bash scripts/check-shared-frontend-onboarding.sh'
  # EPIC #500 (M30 — the enterprise chat surface in the Single Pane of Glass):
  # the six conversational-surface gates delivered by the sibling chat lanes.
  # They are registered here because this array is explicit and nothing is
  # auto-discovered, so a `scripts/check-*.sh` that is not named here is inert —
  # it runs only if someone remembers to run it, which is the formality GR-12
  # forbids. All six are offline and deterministic (no network, no `vendor/CMR`
  # submodule, no vendored seed), so unlike the `diagrams-declaration` /
  # `codeidx-surface` gates above they RUN for real (rc 0) in a fresh worktree
  # and are never recorded as CANNOT-ASSESS. Each also provokes its own negative
  # controls, so a green result cannot come from a gate that exercises nothing.
  # NB: the seventh gate named by issue #502, `check-chat-surface.sh`, was NOT
  # registered when this lane was written because it did not exist yet — it is
  # owned by #503, which has since landed (PR #564). It is registered directly
  # below by issue #568, which wired it and repaired the red gate of record its
  # landing caused. Naming that gap was honest; a stub would have been an
  # invented check, which is worse than a named gap.
  'chat-tools|bash scripts/check-chat-tools.sh'            # #504 grounding lane
  'chat-identity|bash scripts/check-chat-identity.sh'      # #505 identity lane
  'chat-finops|bash scripts/check-chat-finops.sh'          # #506 FinOps lane
  'chat-guardrails|bash scripts/check-chat-guardrails.sh'  # #507 guardrails lane
  'chat-ux|bash scripts/check-chat-ux.sh'                  # #508 UX lane
  'chat-eval|bash scripts/check-chat-eval.sh'              # #509 eval lane
  # EPIC #500 (the enterprise chat surface), issue #568: the seventh chat gate.
  # #503 (PR #564) shipped `scripts/check-chat-surface.sh` -- the OpenAI-/
  # Ollama-compatible contract and its flag gate -- but nothing invoked it, so
  # the `gate-coverage` detector (#526) failed it BY NAME and the gate of record
  # went red on master: an artifact no gate runs is a formality (GR-12). Its six
  # siblings (`chat-eval`, `chat-finops`, `chat-guardrails`, `chat-identity`,
  # `chat-tools`, `chat-ux`) are wired by the #502 lane; this one landed after
  # that lane's PR was opened, which is why it needed its own fix. Offline and
  # deterministic -- no network, no `vendor/CMR` seed -- so it RUNS for real.
  'chat-surface|bash scripts/check-chat-surface.sh'
  # The declared suite manifest (scripts/pytest-suites.txt) is run in full and in
  # isolation by `make gate` / `make tests`; this gate runs the `fleet` suite the
  # same way run-pytest-suites.sh does, so a red fleet test cannot reach master
  # again. pytest exits non-zero on a collection error or on "no tests collected"
  # (rc 5), so the check has no false-green path.
  'pytest-fleet|env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q fleet/tests'
  # EPIC #500 (issue #513): the conversation transcript store. Wired here for
  # the same reason `pytest-fleet` is: a suite nothing names is decorous but
  # inert, and `check-gate-coverage` refuses a newly *declared* suite that no
  # gate names -- declaring it without wiring it would fail the gate of record.
  # Offline, deterministic, stdlib only: no network, no model, no vendor seed.
  'pytest-conversation|env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q engine/conversation/tests'
  # EPIC #500 (issue #579): the chat SERVING surface's own suite. It shipped
  # with #503 and nothing ever named or declared it, so 50 passing tests ran in
  # no gate at all -- check-drift.sh saw it only at WARN. Wired here for the same
  # reason as pytest-fleet: a suite nothing names is decorous but inert.
  'pytest-chat|env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q gateway/chat/tests'
  # EPIC #551 (the remote control command center): the three control-plane
  # gates delivered by RC-2 (#553), RC-4 (#555) and RC-10 (#565). Each is
  # offline and deterministic and provokes its own negative controls; they
  # are registered here deliberately because this array is explicit and an
  # unwired check is inert (GR-12). Wiring them retires their baseline rows
  # (tracker #559).
  'control-verbs|bash scripts/check-control-verbs.sh'
  'control-audit|bash scripts/check-control-audit.sh'
  'control-functions|bash scripts/check-control-functions.sh'
  # EPIC #551 (the remote control command center): the four control-plane
  # pytest suites (RC-2 #553, RC-5 #556, RC-10 #565, RC-11 #566). Each is
  # offline and deterministic. They are named here deliberately: a suite that
  # is declared in scripts/pytest-suites.txt but named by no gate is refused
  # by check-gate-coverage, which never grandfathers a newly declared one.
  'pytest-control|env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q control-plane/control/tests'
  'pytest-control-cli|env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q control-plane/cli/tests'
  'pytest-control-functions|env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q control-plane/functions/tests'
  'pytest-cockpit|env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q control-plane/cockpit/tests'
  # Issue #771 (the remote operator SSH route, GR-5): two registrations for one
  # lane. `ao-ssh-access` is the route's gate -- the ingress merge must never
  # drop a live rule (mutation-proved against a neutered copy), a dry run must
  # send no mutating request (driven against a read-only stub), no estate
  # identifier or token may be in the tree, and `--apply` must refuse while the
  # surface is OFF. `pytest-cloudflare` is its suite: a suite declared in
  # scripts/pytest-suites.txt that no gate NAMES is refused by
  # check-gate-coverage, which never grandfathers a newly declared one, so it
  # gets its own row (the `pytest-control` precedent) rather than being named
  # only from inside the check. Both are offline and deterministic -- the stub
  # binds 127.0.0.1 and no Cloudflare call happens anywhere -- so they RUN for
  # real in a fresh worktree rather than being recorded as CANNOT-ASSESS.
  'ao-ssh-access|bash scripts/check-ao-ssh-access.sh'
  'pytest-cloudflare|env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q infra/cloudflare/tests'
  # erp-module (EPIC #645, issue #646): the ERP module's foundation. Two claims
  # that read green while they rot -- that every datum the module knows is served
  # by the knowledge indexer, and that no second store of ERP domain facts
  # exists -- are made measurable here. The manifest's frozen schema constrains
  # `mandatory: true`, the flag's OFF default and `data_source: indexer`; every
  # catalogue file is schema-validated with provenance identical to the
  # manifest's one declaration; every cross-reference resolves; every declared
  # indexer glob is registered verbatim; a fact restated on the declaration
  # surface is refused by name; and a query for a declared document type must
  # return the CATALOGUE FILE that declares it. Every refusal is written as a
  # provocation in the suite this check runs, and a clean copy must be refused
  # nothing, so no rule in it is a formality.
  'erp-module|bash scripts/check-erp-module.sh'
  # erp-core-model (EPIC #645, ERP-02 / issue #647): the core document model —
  # the document families as JSON Schema plus each lifecycle as workflow data —
  # is only a *model* if its two halves cannot drift, so this check runs the
  # module's suite and then drives the acceptance criteria a second way, from
  # the filesystem, against a scratch copy of the tree. A clean copy must be
  # refused nothing (the check cannot be permanently red) and each provoked
  # breakage must be refused BY NAME: a schema with no harvest record, no `$id`,
  # a harvest claiming upstream code was copied (the upstream is GPL-3.0 and a
  # pattern source only), a workflow whose states drift from its schema's enum,
  # a lifecycle with no workflow, and a provenance record that has gone missing.
  # Both acceptance refusals are driven with their accepting twins: an invalid
  # document refused while its valid twin is accepted, and a state jump refused
  # while the declared path is accepted. Naming the suite's pytest target here is
  # also what makes the suite covered rather than merely present. Offline and
  # deterministic — no network, no vendor seed — so it RUNS for real rather than
  # being recorded as CANNOT-ASSESS.
  'erp-core-model|bash scripts/check-erp-core-model.sh'
  'lane-collision|bash scripts/check-lane-collision.sh'
  # codeowners (issue #1073, cites #803 row 12 — platform-level enforcement of a
  # declared control): `.github/CODEOWNERS` did not exist, so the five-pillar +
  # cross-cutting map AGENTS.md and docs/ARCHITECTURE.md declare had no reviewer
  # mapping and nothing would notice drift — a new pillar with no rule, a rule
  # left behind naming a renamed/removed directory, or a typo'd owner token
  # GitHub silently ignores. This check is STRUCTURAL (default rule present,
  # every pillar present in the tree has an explicit rule, every rule names a
  # real path, every owner token well-formed) and PROVOKED against the same
  # comparator function with four fixtures (valid / missing-pillar /
  # stale-path / malformed-owner), each required to fail BY NAME.
  'codeowners|bash scripts/check-codeowners.sh'
  # EPIC #878 flip (issue #883/#878): five suites this lane declared in
  # scripts/pytest-suites.txt as part of raising every product surface's
  # `declared_class` to `elite` (the live_sync evidence). A suite declared in
  # the manifest but named by no gate is refused by check-gate-coverage, which
  # never grandfathers a newly declared one -- so each gets the `pytest-*`
  # wiring precedent (`pytest-fleet`, `pytest-conversation`, `pytest-control`)
  # rather than being named only from inside an unrelated per-surface check.
  # Offline and deterministic (no network, no vendor seed), so each RUNS for
  # real in a fresh worktree.
  'pytest-gateway-sync|env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q gateway/sync/tests'
  'pytest-registry-sync|env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q registry/sync/tests'
  'pytest-module-registry-sync|env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q governance/modules/sync/tests'
  'pytest-hermes-sync|env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q integrations/hermes/sync/tests'
  'pytest-governance-controls|env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q governance/controls/tests'
)

# --- check auto-discovery (#698) ---------------------------------------------
# A NEW `scripts/check-*.sh` is wired the moment it lands, with no hand-edit to
# the array above — this ends the #559 sole-writer serialization on this list.
# The explicit array stays the source of truth for the entries whose check NAME
# or command differs from the filename convention (renamed checks, `*.py`
# checks, tracker scripts, pytest suites). Every other `scripts/check-*.sh` is
# discovered here — its name derived from its filename (`check-X.sh` -> `X`) —
# and appended. `scripts/check-denylist.txt` disables a check BY NAME (never
# silently: the discovery layer reports each denylisted name on stderr). A
# discovered check whose script is already referenced in the explicit array is
# not re-added, so the array's names, commands and order are preserved exactly.
source "$root/scripts/discover-checks.sh"

already_wired=""
for entry in "${checks[@]}"; do
  already_wired="${already_wired}${entry#*|}|"
done

mapfile -t discovered < <(discover_check_scripts)
for entry in "${discovered[@]}"; do
  script_path="${entry#*|}"      # "bash scripts/check-X.sh"
  base="${script_path##*/}"      # "check-X.sh"
  if printf '%s' "$already_wired" | grep -qF "scripts/$base"; then
    continue
  fi
  checks+=("$entry")
done

# --- duplicate-registration guard (issue #499) -------------------------------
# `checks=()` is an explicit list that every wiring lane appends to, so two
# lanes can register the SAME name (measured on this board: a re-added
# `cross-reference` entry). A duplicate is a gate defect, not a harmless no-op:
# it re-runs a check and hides that two lanes claim the same surface. The
# duplicates are computed and named BEFORE anything runs, recorded in the
# attestation as `duplicates` ({} when the list is clean) and fail the run, so a
# wedged check list can never attest green.
duplicates_tsv="$verify_dir/.duplicates.tsv"
: > "$duplicates_tsv"
for entry in "${checks[@]}"; do
  printf '%s\n' "${entry%%|*}"
done | LC_ALL=C sort | uniq -c | awk '$1 > 1 {print $2"\t"$1}' > "$duplicates_tsv"

overall=0
if [ -s "$duplicates_tsv" ]; then
  while IFS=$'\t' read -r dup_name dup_count; do
    printf '  FAIL  check name %s is registered %s times (one writer per wave on this list)\n' \
      "$dup_name" "$dup_count" >&2
  done < "$duplicates_tsv"
  echo "verify: duplicate check name(s) registered -- the check list is wedged" >&2
  overall=1
fi

check_out_dir="$verify_dir/.check-out"
mkdir -p "$check_out_dir"
for entry in "${checks[@]}"; do
  name="${entry%%|*}"
  cmd="${entry#*|}"
  printf '\n== %s ==\n' "$name" | tee -a "$log"
  # Per-check output is captured separately (issue #882) so the attestation can
  # carry an evidence tail for THIS check, not the whole run's log.
  check_out="$check_out_dir/${name//\//_}.txt"
  check_start="$(date +%s)"
  bash -c "$cmd" 2>&1 | tee -a "$log" "$check_out"
  rc="${PIPESTATUS[0]}"
  check_end="$(date +%s)"
  duration=$((check_end - check_start))
  printf '%s\t%s\t%s\t%s\n' "$name" "$rc" "$duration" "$check_out" >> "$results_tsv"
  # Honest tri-state (GR-12 / guardrails/honesty). 0 = PASS; 1 = NOT-OK, a real
  # defect, and the run fails; 2 = CANNOT-ASSESS -> SKIP. A check that says it
  # genuinely could not assess (e.g. the pinned vendor/CMR submodule is absent in
  # a fresh worktree, so the vendored contract is unreadable) must not paint the
  # whole gate red for every lane AND must not be counted as a pass: it is
  # recorded as SKIP and named in the summary and the attestation, so a skip can
  # never hide. Any other rc (unexpected, timeout 124, killed 137) fails.
  if [ "$rc" -ne 0 ] && [ "$rc" -ne 2 ]; then
    overall=1
  fi
done

# --- attestation ------------------------------------------------------------
export ATTEST_DIR="$verify_dir"
export ATTEST_TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
export ATTEST_SHA="$(git rev-parse HEAD 2>/dev/null || echo unknown)"
export ATTEST_BRANCH="$(git branch --show-current 2>/dev/null || echo unknown)"
export ATTEST_HOST="$(hostname 2>/dev/null || echo unknown)"
export ATTEST_RESULT="$overall"
export ATTEST_MODE="$mode"
export ATTEST_VERIFIED_BY="${AO_AGENT_ID:-$(id -un 2>/dev/null || echo unknown)}"
export ATTEST_VERIFICATION_SESSION="${AO_SESSION_ID:-}"
export ATTEST_RESULTS_TSV="$results_tsv"
export ATTEST_DUPLICATES_TSV="$duplicates_tsv"
export ATTEST_RUN_ID="${ATTEST_TS}-$$"
python3 - <<'PY'
import json, os

attest_dir = os.environ["ATTEST_DIR"]
results_tsv = os.environ["ATTEST_RESULTS_TSV"]
duplicates_tsv = os.environ["ATTEST_DUPLICATES_TSV"]

# Tri-state per check (issue #882, same rc contract as the gate loop itself):
# rc 0 -> OK, rc 2 -> WARN (CANNOT-ASSESS, never reported as a pass), anything
# else -> FAIL. A red is never reported OK -- that mapping is enforced here,
# not left to a caller to get right later.
_VERDICT = {"0": "OK", "2": "WARN"}


def verdict_for(rc: str) -> str:
    return _VERDICT.get(rc, "FAIL")


checks = []
with open(results_tsv, encoding="utf-8") as fh:
    for line in fh:
        line = line.rstrip("\n")
        if not line:
            continue
        fields = line.split("\t")
        name, rc = fields[0], fields[1]
        duration = float(fields[2]) if len(fields) > 2 and fields[2] else 0.0
        out_path = fields[3] if len(fields) > 3 else ""
        status = "PASS" if rc == "0" else ("SKIP" if rc == "2" else "FAIL")
        evidence_tail = ""
        if out_path and os.path.isfile(out_path):
            try:
                with open(out_path, encoding="utf-8", errors="replace") as ofh:
                    tail_lines = ofh.readlines()[-20:]
                evidence_tail = "".join(tail_lines)
            except OSError:
                evidence_tail = ""
        checks.append(
            {
                "name": name,
                "rc": int(rc),
                "status": status,
                "verdict": verdict_for(rc),
                "duration": duration,
                "evidence_tail": evidence_tail,
            }
        )

skipped = [c["name"] for c in checks if c["status"] == "SKIP"]

# Overall verdict = the worst of the per-check verdicts, never better than any
# check it ran (issue #882's no-false-green requirement).
_RANK = {"OK": 0, "WARN": 1, "FAIL": 2}
overall_verdict = "OK"
for c in checks:
    if _RANK[c["verdict"]] > _RANK[overall_verdict]:
        overall_verdict = c["verdict"]

# Duplicate check names: a name registered twice is a gate defect (two lanes
# wrote the same surface), so it is recorded here verbatim -- {} when clean.
duplicates: dict[str, int] = {}
with open(duplicates_tsv, encoding="utf-8") as fh:
    for line in fh:
        line = line.rstrip("\n")
        if not line:
            continue
        dup_name, dup_count = line.split("\t", 1)
        duplicates[dup_name] = int(dup_count)

overall = int(os.environ["ATTEST_RESULT"])
attestation = {
    "run_id": os.environ["ATTEST_RUN_ID"],
    "gate": "verify",
    "mode": os.environ["ATTEST_MODE"],
    "result": "PASS" if overall == 0 else "FAIL",
    "overall_verdict": overall_verdict,
    "exit_code": overall,
    "timestamp": os.environ["ATTEST_TS"],
    "host": os.environ["ATTEST_HOST"],
    "git_sha": os.environ["ATTEST_SHA"],
    "branch": os.environ["ATTEST_BRANCH"],
    "verified_by": os.environ["ATTEST_VERIFIED_BY"],
    "verification_session": os.environ["ATTEST_VERIFICATION_SESSION"],
    "check_count": len(checks),
    "skipped": len(skipped),
    "skipped_checks": skipped,
    "duplicates": duplicates,
    "checks": checks,
}
path = os.path.join(attest_dir, "attestation.json")
with open(path, "w", encoding="utf-8") as fh:
    json.dump(attestation, fh, indent=2)
    fh.write("\n")
PY

# --- attestation self-validation (issue #882) --------------------------------
# Written EVEN ON FAILURE (above) so a red run still carries evidence; validated
# here so a malformed attestation is itself a gate defect, not a silent hole. A
# schema violation here is not allowed to hide behind an otherwise-green run:
# it forces the whole gate to FAIL (no-false-green doctrine, GR-12).
#
# A MISSING schema is a defect in THIS tree, not an environment condition
# (issue #1146). The schema is a tracked contract — it moved out of the
# generated `.verify/` root to `governance/isolation/attestation.schema.json`
# — and it ships in the same commit as this line, so a tree has both or
# neither. The guard here used to be `[ -f "$attestation_schema" ] && ...`,
# which made deleting the schema the way to turn this control off while the
# gate stayed green. A control that fails OPEN is worse than no control: the
# attestation would go unvalidated and nothing would say so, which is exactly
# the false green this block exists to refuse. It now fails CLOSED, by name.
# Only a missing `python3` still skips the run — an environment precondition
# the rest of the gate already requires.
attestation_schema="$root/governance/isolation/attestation.schema.json"
if [ ! -f "$attestation_schema" ]; then
  echo "verify: attestation schema is missing at $attestation_schema -- it is a tracked contract, so the gate cannot validate the attestation it just wrote (NOT-OK, fails closed)" >&2
  overall=1
elif command -v python3 >/dev/null 2>&1; then
  if ! python3 "$root/scripts/lib/validate-attestation.py" \
      "$verify_dir/attestation.json" "$attestation_schema" >>"$log" 2>&1; then
    echo "verify: attestation.json failed schema validation (see $log) -- the gate cannot attest a shape it did not itself produce correctly" >&2
    overall=1
  fi
fi

# --- summary ----------------------------------------------------------------
# A SKIP is counted and named here so it can never hide (a skip is not a pass).
passed=0
failed=0
skipped=0
skipped_names=""
total=0
for entry in "${checks[@]}"; do
  name="${entry%%|*}"
  total=$((total + 1))
  rc="$(awk -F'\t' -v n="$name" '$1==n {print $2}' "$results_tsv" | head -1)"
  case "${rc:-1}" in
    0) passed=$((passed + 1)) ;;
    2) skipped=$((skipped + 1)); skipped_names="${skipped_names}${skipped_names:+, }${name}" ;;
    *) failed=$((failed + 1)) ;;
  esac
done

skip_note=""
if [ "$skipped" -gt 0 ]; then
  skip_note=", $skipped skipped: $skipped_names"
fi

if [ -s "$duplicates_tsv" ]; then
  echo "verify: duplicate check name(s): $(awk -F'\t' '{printf "%s%s", sep, $1; sep=", "}' "$duplicates_tsv")" >&2
fi

echo ""
if [ "$overall" -eq 0 ]; then
  echo "verify: PASS ($passed of $total checks$skip_note)"
  echo "attestation: $verify_dir/attestation.json"
  if [ "$mode" = "gate" ]; then
    echo "GATE: PASS"
  fi
else
  echo "verify: FAIL ($failed of $total checks failed$skip_note)" >&2
  echo "attestation: $verify_dir/attestation.json" >&2
  if [ "$mode" = "gate" ]; then
    echo "GATE: FAIL" >&2
  fi
fi

exit "$overall"
