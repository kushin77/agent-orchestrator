# agent-orchestrator — gate of record
#
# `make verify` is the honest composite gate (GR-12), orchestrated by
# scripts/verify.sh with attestation. Every check produces a REAL exit code
# and can genuinely fail: shell syntax errors, unparseable YAML/JSON, broken
# doc links, missing foundation files, unfinished markers, leaked secrets,
# feature flags that ship ON, cloudbuild triggers that ship enabled, or
# terraform fmt/validate failures all fail the gate. A gate that cannot fail
# is a formality and is rejected (fleet no-false-green doctrine).
#
# The gate writes .verify/verify.log + .verify/attestation.json (the verify
# record / attestation). Run `make verify` before every PR and every merge;
# paste its output as evidence (GR-12). No network and no containers required.

SHELL := /bin/bash

# The browser console's bind address (issue #763). Loopback by default ON PURPOSE:
# the console has no login of its own and establishes a session only from a
# verified auth-gate RS256 token — with no JWKS mirror it refuses every session.
# Binding a reachable interface is therefore an explicit operator decision
# (CONSOLE_HOST=0.0.0.0), and a tunnel/proxy in front of it is a new
# infrastructure surface that ships flag-gated OFF (GR-5). See
# docs/OPERATOR-ACCESS.md §4.
CONSOLE_HOST ?= 127.0.0.1
CONSOLE_PORT ?= 8787
ATTESTATION ?= .verify/attestation.json

.PHONY: help verify lint gate merge-gate qa-loop tests e2e fleet-parity \
        shell-syntax python-syntax yaml-lint json-lint docs-lint gate-coverage codeowners squash-message chronological-dispatch \
issue-claims issue-template fleet-channel finops-chooser fleet-contract fleet-runbook fleet-vocabulary session-isolation github-lifecycle reconcile lease-policy fleet-state knowledge-index knowledge-index-build erp-module paperclip-gap-analysis paperclip-integration cross-reference cross-repo-boundary audit-read-model gateway-catalog-parity guardrail-controls paperclip-adapter agent-identity-parity paperclip-canonical-module paperclip-auth paperclip diagrams codeidx monitoring-declaration capability-registers chat \
        brain-profile conformance lessons ticket pmo pmo-dispatch secrets feature-flags cloudbuild terraform tf-fmt surface-class \
        tf-validate shellcheck gitleaks pre-commit install-hooks worktrees scratch-safety web-image-dryrun \
        remediation remediation-scan remediation-dispatch \
        capacity-gate tagging epic-focus capability-drift conformance-change-set board-gate ao-ssh-access \
        control-verbs control-audit control-functions cockpit operator console operator-access operator-terminal

.DEFAULT_GOAL := help

## help — list targets with one-line descriptions
help:
	@echo "agent-orchestrator — gate of record"
	@echo ""
	@echo "Usage: make <target>"
	@echo ""
	@echo "Targets:"
	@echo "  help          List targets with one-line descriptions"
	@echo "  verify        Gate of record (with attestation); run before every PR/merge"
	@echo "  lint          Shell + YAML + JSON + docs (no secret scan)"
	@echo "  gate          Full QA gate: verify + policy-schema + guard"
	@echo "                negative-controls + per-suite tests + drift +"
	@echo "                merge-gate wiring (tri-state, attestation)"
	@echo "  merge-gate    Pre-merge contract: clean tree + verify + full"
	@echo "                per-suite tests + controls + policy-schema"
	@echo "  qa-loop       fix -> verify -> re-check until the gate is green"
	@echo "  tests         Run every declared pytest suite in isolation"
	@echo "  e2e           Run only the capstone e2e suite (also in verify, #525)"
	@echo "  paperclip     Run every paperclip boundary/adapter gate in one shot"
	@echo "  shellcheck    Run shellcheck on scripts/ (skipped if not installed)"
	@echo "  gitleaks      Run gitleaks with .gitleaks.toml (skipped if absent)"
	@echo "  pre-commit    Run pre-commit on all files (skipped if absent)"
	@echo "  install-hooks OPT IN: install scripts/git-hooks/pre-commit (the file-lease"
	@echo "                enforcement hook) into this checkout. Dry run by default;"
	@echo "                AO_HOOKS_APPLY=1 writes. docs/LEASE-HOOK.md (issue #1541)"
	@echo ""
	@echo "Operator surfaces (issue #763; docs/OPERATOR-ACCESS.md):"
	@echo "  operator      Report every operator surface, then open the live view"
	@echo "                (tmux; fails loudly, naming the reason, if tmux is absent)"
	@echo "  console       Serve the browser console (python3 -m portal.server.main,"
	@echo "                127.0.0.1:8787 by default; fails closed with no JWKS)"
	@echo ""
	@echo "Fine-grained checks (used by verify):"
	@echo "  shell-syntax  bash -n on every *.sh outside vendor/"
	@echo "  python-syntax py_compile on every tracked *.py outside vendor/ —"
	@echo "                a syntax-broken fleet module must not pass the gate"
	@echo "  yaml-lint     Parse every .yml/.yaml outside vendor/ (PyYAML)"
	@echo "  json-lint     Validate every *.json outside vendor/"
	@echo "  docs-lint     Foundation files + md links + whitespace + markers"
	@echo "  codeowners    Declared ownership map (#1073): default + one rule per"
	@echo "                pillar/cross-cutting dir, every rule naming a real path"
	@echo "  chronological-dispatch  Governance docs declare dependency-ordered"
	@echo "                issue selection (GR-20, cannot drift to kanban picking)"
	@echo "  issue-claims  Claim-time order enforcement (#157): ledger replayed"
	@echo "                against the board snapshot + self-control mutants"
	@echo "  issue-template  Issue-brief contract (#165): the required fields +"
	@echo "                canonical FinOps vocab; a dropped field is refused"
	@echo "  fleet-channel Steering channel (M26 #162): message contract + the"
	@echo "                DSv4FNone standing directive, all mutants refused"

	@echo "  finops-chooser  FinOps chooser (M26 #164): the harvested tier/thinking"
	@echo "                vocabulary, enforced — a subagent cannot pick its own tier"


	@echo "  fleet-contract  Session-fleet steering contract (M26 #161): roles,"
	@echo "                six directive verbs, envelope fields and trust rules"
	@echo "                are declared and map onto the shipped channel"

	@echo "  fleet-runbook  Bootstrap runbook gate (M26 #166): the only human step"
	@echo "                is the model switch, and the recovery paths stay documented;"
	@echo "                also provokes each capability-drift case (#319)"

	@echo "  capability-drift  Capability drift (#319): report the capabilities each"
	@echo "                running rung does NOT implement, naming every one (a control"
	@echo "                that shipped but is not live is a silently absent control)"

	@echo "  session-isolation  Lane isolation (M26 #263): one session identity per"
	@echo "                issue, its own worktree on issue-<n>, a worktree-scoped"
	@echo "                commit signature, and a ticket ref on every commit"

	@echo "  github-lifecycle  End-to-end closure (M26 #269): every artifact a work"
	@echo "                item creates reaches a terminal state, one provoked"
	@echo "                violation per closure invariant"

	@echo "  brain-profile  Brain profile gate (M26 #160): the elite profile"
	@echo "                declares mission/KB/floors/controls/escalation and the"
	@echo "                brain derives its doctrine from it, not from a copy"

	@echo "  knowledge-index  Institutional knowledge index (#139): provenance,"
	@echo "                coverage and secret policy over the indexed assets"

	@echo "  erp-module    ERP module foundation (EPIC #645 #646): the manifest's"
	@echo "                frozen shape, the catalogue's schemas/provenance/single"
	@echo "                store, and the indexer proven to serve the catalogue"
	@echo "  conformance   CMR class/pattern/template enforcement (#140): every"
	@echo "                milestoned issue classified; mandates checked on the diff"
	@echo "  surface-class Per-surface target solution-class (#351): every surface"
	@echo "                meets the rung it declares, or the gate fails"
	@echo "  tagging       Tag authority (#1175): one declared vocabulary per tag"
	@echo "                dimension, borrowed vocabularies proven equal to their"
	@echo "                authorities, every named gate resolved, and all eleven"
	@echo "                refusals provoked by name with clean twins accepted"
	@echo "  lessons       RCA + lessons enforcement (#141): every incident has an"
	@echo "                RCA, every action recorded, every lesson evidenced"
	@echo "  fleet-state   Unified fleet-state projection (#323): lanes + sessions +"
	@echo "                claims + journals + directives joined per item; exits"
	@echo "                non-zero when anything is orphaned, shelved or wedged"
	@echo "  agent-identity-parity  Shared agent-identity schema (#346): the closed"
	@echo "                vocabularies must equal the AgentProfile schema and the"
	@echo "                catalog, and every seed must validate as an identity"
	@echo "  secrets       Mechanical secret scan (always on)"
	@echo "  worktrees     Reclaim stale lane worktrees (dry run; --apply via"
	@echo "                scripts/prune-worktrees.sh). Keeps dirty, in-use and"
	@echo "                unpreserved worktrees; /tmp ones are the RAM hazard."
	@echo "  feature-flags Feature-flag registry: every surface defaults OFF"
	@echo "  cloudbuild    infra/cloudbuild YAML parses; triggers ship disabled"
	@echo "  web-image-dryrun  Validate the web-surface image build (dry-run/local build only, no push)"
	@echo "  terraform     infra/terraform fmt + offline validate (SKIP if absent)"
	@echo "  tf-fmt        terraform fmt -check only"
	@echo "  tf-validate   offline terraform validate only"
	@echo "  scratch-safety  Scratch-space guard (#488): refuses a self-appending"
	@echo "                log (the 14.8 GB tmpfs incident), an oversize scratch"
	@echo "                file, a near-full scratch filesystem, a worktree on the"
	@echo "                tmpfs and a copy that wrote 0 bytes; the live machine"
	@echo "                verdict it prints is advisory (docs/SCRATCH-SPACE-DISCIPLINE.md)"

## verify — gate of record (orchestrated by scripts/verify.sh, with attestation)
verify:
	@bash scripts/verify.sh verify

## verify-attestation — the same gate of record, but with AO_GATE_VENUE=attestation
## (#1620): check-worktree-cap / check-reconcile-orphans stop treating
## orphan-branch / orphan-issue-lane / worktree-cap-exceeded as advisory (a
## bare `make verify` reports them as NOTE, since they measure box-wide state
## shared with every OTHER concurrent session on the machine, not this
## checkout's own diff) and enforce them for real. Use this — never plain
## `make verify` — right before `make master-attestation`, since that
## attestation is meant to speak for the WHOLE box's hygiene, not just one
## lane's diff.
##   make verify-attestation && make master-attestation
verify-attestation:
	@AO_GATE_VENUE=attestation bash scripts/verify.sh verify

.PHONY: verify-attestation

## worktrees — reclaim stale lane worktrees (dry run by default)
worktrees:
	@bash scripts/prune-worktrees.sh

## finops — CFO office KPI gate: injected-prompt byte ceiling
## (registry/personas/offices/cfo/cost-policy.yaml). Auto-discovered into
## `make verify` as scripts/check-finops-kpi.sh; this target lets it run
## standalone (docs/cfo/PROMPT-REDUCTION-PLAN.md).
finops:
	@bash scripts/check-finops-kpi.sh

## repo-settings — read back the live repo merge-message policy against the
## declaration (governance/platform/repo-settings.yaml, issue #1138); pass
## REPO_SETTINGS_ARGS=apply to PATCH the declared fields onto the live repo
## (idempotent).
repo-settings:
	@bash scripts/repo-settings.sh $(or $(REPO_SETTINGS_ARGS),verify)

## operator — the operator's way in, one command (issue #763): report every
## operator surface (the PRIMARY control plane, the override terminal, the live
## view, the browser console), then start the rungs that are missing and attach
## to the fleet tmux session. Delegates to fleet/run-fleet.sh (== `python3
## fleet/control.py live`), so the layout has exactly ONE definition and this
## target reimplements none of it. Honest, not optimistic: with no tmux it exits
## non-zero, names the reason, and points at `python3 fleet/console.py` — it
## never prints a success it cannot evidence. `make operator
## OPERATOR_ARGS=--dry-run` prints the tmux commands and builds nothing.
operator:
	@bash scripts/operator.sh $(OPERATOR_ARGS)

## console — serve the browser console (issue #763): the real server,
## `python3 -m portal.server.main`, on 127.0.0.1:8787 by default. It binds
## loopback and fails closed: the console has no login of its own, it establishes
## a session only from a verified auth-gate RS256 token, and with no JWKS mirror
## (PORTAL_AUTH_GATE_JWKS_FILE) it refuses every session. Reaching it from
## elsewhere means binding a reachable interface (CONSOLE_HOST=0.0.0.0) AND
## putting the auth gate in front — docs/OPERATOR-ACCESS.md §4. A tunnel or
## reverse proxy in front of it is a new infrastructure surface: declared in
## code, flag-gated OFF (GR-5), never a console click.
console:
	@bash scripts/console.sh --host $(CONSOLE_HOST) --port $(CONSOLE_PORT)
## lint — shell + YAML + JSON + docs (no secret scan)
lint: shell-syntax python-syntax yaml-lint json-lint docs-lint squash-message chronological-dispatch issue-claims epic-focus capacity-gate issue-template fleet-channel finops-chooser fleet-contract fleet-runbook fleet-vocabulary operator-access session-isolation github-lifecycle reconcile lease-policy fleet-state brain-profile knowledge-index lessons ao-ssh-access operator-terminal codeowners tagging
	@echo ""
	@echo "lint: OK"

## gate — full QA gate (issue #29): verify composite + policy-schema + guard
## negative-controls + per-suite isolated tests + drift + merge-gate wiring.
## Honest tri-state aggregation (issue #28) + .verify/gate-attestation.json.
gate:
	@bash scripts/gate.sh gate

## merge-gate — pre-merge contract (issue #29): refuses on a dirty tree, runs
## verify + drift + every declared pytest suite in isolation + guard
## negative-controls + policy-schema, and writes .verify/merge-attestation.json.
merge-gate:
	@bash scripts/merge-gate.sh run

## qa-loop — continuous fix -> verify -> re-check until the gate is green
qa-loop:
	@bash scripts/qa-loop.sh

## tests — run every declared pytest suite in isolation (per-suite, issue #29)
tests:
	@bash scripts/run-pytest-suites.sh

## fleet-parity — fleet-cron dual-run parity harness + evidence (issue #714,
## EPIC #706 D6): runs the pytest suite, then the real harness (local vs
## container persona, dry-run watchdog/prune/reconcile, idempotency under a
## simulated lost-lock race), and writes .verify/fleet-parity.json
fleet-parity:
	@env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q infra/fleet/tests
	@python3 infra/fleet/parity.py --ticks 3

## e2e — the capstone end-to-end suite alone (issue #525). The identical command
## is an entry in `make verify` (scripts/verify.sh), so a red Definition-of-Done
## proof can no longer reach master; this target is the convenience runner.
e2e:
	@env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q e2e/tests

## shell-syntax — bash -n on every *.sh outside vendor/
shell-syntax:
	@bash scripts/check-shell-syntax.sh

## python-syntax — py_compile on every tracked *.py outside vendor/, via the
## repo's own check script (the recipe DELEGATES; it never re-implements the
## compile). This target was referenced by `lint` and by the help text but had
## no recipe, so GNU make treated it as already satisfied and `make lint`
## silently skipped the Python syntax check it advertised — a live false-green
## (GR-12). Restored by #1202.
python-syntax:
	@bash scripts/check-python-syntax.sh

## yaml-lint — parse every .yml/.yaml outside vendor/ with PyYAML
yaml-lint:
	@python3 scripts/check-yaml.py

## json-lint — validate every *.json outside vendor/
json-lint:
	@bash scripts/check-json.sh

## docs-lint — foundation files, markdown links, whitespace, unfinished markers
docs-lint:
	@bash scripts/check-docs.sh

## codeowners — declared ownership map (issue #1073): default + one rule per
## pillar/cross-cutting dir, every rule naming a real path, PROVOKED
codeowners:
	@bash scripts/check-codeowners.sh

## gate-coverage — every delivered artifact must be invoked by a gate (issue
## #526): a scripts/check-*.sh no gate file names, or a declared pytest suite no
## gate names as a target, fails by name unless it is a live entry in the
## reviewed scripts/gate-coverage-baseline.txt (which is checked in both
## directions, so a stale entry fails too)
gate-coverage:
	@bash scripts/check-gate-coverage.sh

## squash-message — refuse a squash-merge message that would drop the ticket
## trailer (issue #1102, parent #878): renders the SAME message `gh pr merge
## --squash` would compose and runs it through the shared trailer predicate
## (governance/isolation/trailer.py) via a passing fixture + two failing
## mutants, so it cannot pass vacuously
squash-message:
	@bash scripts/check-squash-message.sh --self-test
	@bash scripts/check-pr-queue-squash-guard.sh

## chronological-dispatch — governance docs must declare dependency-ordered
## issue selection (GR-20); a doc-only rule is advisory, so this gate fails it
chronological-dispatch:
	@bash scripts/check-chronological-dispatch.sh

## issue-claims — claim-time order enforcement (issue #157): the ledger is
## replayed against the committed board snapshot and every violation fails; the
## audit always runs its own mutants, so it cannot pass vacuously
issue-claims:
	@bash scripts/check-issue-claims.sh

## epic-focus — the active-epic resolver (epic #707, lane F1/#716): the fleet
## focuses on exactly ONE epic; the check refuses a missing resolver and drives
## the resolver + focus-schema self-control mutants, so it cannot pass vacuously
epic-focus:
	@bash scripts/check-epic-focus.sh

## capacity-gate — max-agents ON and bounded (epic #707, lane F3/#718):
## effective = min(pool, disjoint ready lanes, resource ceiling). Each of the
## three bounds is PROVOKED (the excess is held, and the relaxed input is
## admitted, so the two paths cannot share an exit code), the loop is asserted
## wired to the gate, and BOTH halves are mutation-proved
capacity-gate:
	@bash scripts/check-capacity-gate.sh

## issue-template — the fleet issue-brief contract (issue #165): every required
## field present and the FinOps vocabularies canonical; a dropped field fails
issue-template:
	@bash scripts/check-issue-template.sh

## fleet-channel — steering channel (M26, issue #162): the message contract and
## the DSv4FNone standing directive; every mutant message must be refused
fleet-channel:
	@bash scripts/check-fleet-channel.sh


## finops-chooser — FinOps tier/thinking enforcement (M26 #164): the harvested
## vocabulary pinned across the policy, schema, channel and docs; a subagent
## cannot choose its own tier, and a spawn that does not reproduce the brain's
## choice is refused (all mutants must be refused)
finops-chooser:
	@bash scripts/check-finops-chooser.sh

## fleet-vocabulary — the fleet's role-vocabulary gate (issue #777, code prose
## swept by #923): the glossary, the code's constants and the envelope schema
## must agree; a retired role in a schema-2 envelope is refused by name; the
## write seam is single-emit and negotiated; the normative surfaces AND
## fleet/*.py comments/docstrings use the current terms, with a retired name
## permitted only inside a marked legacy-gloss region or as an artifact name
fleet-vocabulary:
	@bash scripts/check-fleet-vocabulary.sh


## fleet-contract — session-fleet steering contract (M26, issue #161): the
## contract must declare the roles, the six directive verbs, the envelope fields
## and the trust rules, and every verb must map onto a shipped message type;
## the check mutates its own input, so it cannot pass vacuously
fleet-contract:
	@bash scripts/check-fleet-contract.sh


## fleet-runbook — session-fleet bootstrap runbook gate (M26, issue #166): the
## only human step is the model switch, and the recovery paths (claim TTL
## take-over, mailbox backlog, dispatcher rc=2) stay documented; capability
## drift (#319) adds the restart step and a provoked report per case; the check
## mutates its own input, so it cannot pass vacuously
fleet-runbook:
	@bash scripts/check-fleet-runbook.sh

## capability-drift — the capabilities each running rung does not implement
## (#319): the repository declares the set its code provides in
## fleet/channel.py, each rung declares its own in its beat, and this report
## names every declared capability a rung is missing — a rung on HEAD that is
## missing one is NOT repairable by a restart
capability-drift:
	@python3 fleet/watchdog.py capabilities


## session-isolation — institutional lane isolation (issue #263): a session
## identity is minted per issue, the lane is its own worktree on branch
## issue-<n>, the signature is worktree-scoped (never the shared config), and
## every commit the session authored references its ticket; the check provokes
## each violation for real, so it cannot pass vacuously
session-isolation:
	@bash scripts/check-session-isolation.sh


## github-lifecycle — end-to-end closure (issue #269): every artifact a work
## item creates reaches a terminal state (PR merged at verified evidence, branch
## deleted, claim released, directive consumed, issue closed with evidence, lane
## reclaimed); the check provokes one violation per invariant and cross-checks
## the provoked set against the model, so it cannot fall behind the vocabulary
github-lifecycle:
	@bash scripts/check-github-lifecycle.sh


## reconcile — orphan reconciliation (issue #304): per-session heartbeats, a TTL
## sweep, and a three-way teardown decided by where the orphan's work lives -
## landed lanes are reclaimed, server-preserved ones parked, and lanes whose work
## exists nowhere else are shelved with their worktree, branch and claim intact
reconcile:
	@bash scripts/check-reconcile.sh


## lease-policy — one declared policy for every fleet lease and TTL (issue #322):
## the rung heartbeat, session heartbeat, session/reconcile TTL, claim TTL and
## reap threshold, snapshot staleness and directive lifetime are declared once in
## governance/policy/lease.py with their ordering invariants written as
## machine-checkable relations; every consumer reads the policy, a module that
## restates a value fails the gate, and every invariant must be refutable by a
## named mutation (a session TTL below the rung heartbeat is refused)
lease-policy:
	@bash scripts/check-lease-policy.sh


# isolation-landed — the ticket-trailer rule over LANDED history (issue #287), and the
# wire that issue #1542 CONFIRMED rather than added. The check is wired into `make verify`
# by the DISCOVERY layer (`scripts/discover-checks.sh`, #698), which appends every
# `scripts/check-*.sh` that is not disabled by name in `scripts/check-denylist.txt`; it is
# therefore absent from this file and from `scripts/verify.sh`'s explicit `checks=()`
# array BY DESIGN, and `grep -n check-isolation-landed Makefile` returning nothing is the
# EXPECTED result — not evidence of dead code. A hand-added `verify.sh` entry would be a
# DUPLICATE registration of the same check name, which the gate of record refuses by name.
# The sibling targets above (session-isolation, reconcile, lease-policy) are hand-run
# conveniences, not the wire; every one of them would be discovered with or without them.
# Measured at b895e396 (2026-09-20, issue #1542): `discover_check_scripts` yields
# `isolation-landed|bash scripts/check-isolation-landed.sh` (position 95 of 189); it is
# absent from the denylist; `scripts/check-gate-coverage.sh`'s #698 marker rule reports it
# `check scripts WIRED=194 UNWIRED=0` (rc 0); and the check itself exits 0 —
# `isolation-enforce: OK — 39 recorded legacy, 0 unrecorded, 0 stale in HEAD`. Its
# negative control is real and was reproduced outside the check's own harness: a synthetic
# landed commit carrying no ticket trailer is refused rc 1 naming
# `commit-missing-ticket-trailer`, while its compliant twin in the same repo shape is
# accepted rc 0.

## paperclip-gap-analysis — sourced paperclip.ing gap analysis (issue #368): the
## GR-10 provenance, the four-way namesake disambiguation, the six capability
## families and the cannibalize-vs-build table must all be present, and the check
## mutates its own input, so it cannot pass vacuously
paperclip-gap-analysis:
	@bash scripts/check-paperclip-gap-analysis.sh


## gateway-catalog-parity — every registered gateway provider adapter has a
## gateway-owned module-catalog entry and every catalog module names a registered
## provider (issue #349); the check reads the provider set from the registry
## (never a filename glob) and mutates its own input in both directions, so it
## cannot pass vacuously
gateway-catalog-parity:
	@bash scripts/check-gateway-catalog-parity.sh
## agent-identity-parity — one shared, versioned agent-identity schema is the
## single source of truth for both repos (issue #346): the shared schema's
## closed vocabularies must EQUAL agent-profile.schema.json + catalog.yaml and
## the status enum must be the union of both repos, every seed is projected to
## the identity view and validated (a free-text capability or an
## out-of-vocabulary status is refused), and the check mutates a scratch copy
## and requires each mutant to be refused, so it cannot pass vacuously
agent-identity-parity:
	@bash scripts/check-agent-identity-parity.sh

## provider-parity — flag UNEXPLAINED Claude/DeepSeek capability drift (issue
## #1194): does not force literal parity (Claude=orchestrator,
## DeepSeek=analytics worker differ by design) but requires every asymmetric
## flag-gated module.json feature and every asymmetric capabilitySet/
## toolAllowlist entry in the seeds/persona cards to carry an inline rationale
## marker; the check mutates a scratch copy with an unmarked item and requires
## it to be refused by name, so it cannot pass vacuously
provider-parity:
	@bash scripts/check-provider-parity.sh

## guardrail-controls — server-side guardrail control semantics (issue #343):
## every control defaults OFF, an unknown control is refused, a toggle writes
## exactly one append-only audit record and flips observable state a second
## reader sees, the Portkey-style 246 PASSED / 446 BLOCKED vocabulary is closed,
## and a control that ships ON is refused (self-mutating negative control), so
## it cannot pass vacuously
guardrail-controls:
	@bash scripts/check-guardrail-controls.sh


## fleet-state — unified fleet-state projection (issue #323): one read-only
## command joins lanes, session heartbeats, closure journals, claims and
## authorisation directives per work item, exits non-zero when anything is
## orphaned, shelved or wedged, and reports a disagreement between two stores
## instead of silently resolving it; the gate exercises one fixture item in each
## state and provokes the failure path, so the projection cannot pass vacuously
fleet-state:
	@bash scripts/check-fleet-state.sh


## knowledge-index — the institutional knowledge index must be valid (issue #139):
## every item's provenance complete, secret policy clean, mandatory kinds covered
knowledge-index:
	@bash scripts/check-knowledge-index.sh

## knowledge-index-build — regenerate the catalogue + report on demand (the ops
## runner invokes the same command on its schedule)
knowledge-index-build:
	@python3 governance/knowledge/cli.py build

## erp-module — the ERP module foundation gate (EPIC #645, issue #646): the
## manifest's frozen shape (`mandatory: true`, flag OFF, `data_source: indexer`),
## the catalogue's schemas, provenance, cross-references and single-store rule,
## with every refusal provoked, and the indexer proven to serve the catalogue
erp-module:
	@bash scripts/check-erp-module.sh

## tagging — the tag authority gate (issue #1175): every tag's dimension and
## value are declared once, every vocabulary that already has an authority here
## is BORROWED (never re-declared) and proven still equal to it, every gate a
## rule names resolves to a real Makefile target or verify check, and each of
## the eleven declared refusals is provoked by a real mutant with its clean twin
## accepted, so none of them can be a formality. Also wired into `make verify`
## by auto-discovery.
tagging:
	@bash scripts/check-tagging.sh

## cross-reference — cross-reference spine (EPIC #138, issue #384): the
## catalogue's typed relationships are valid (closed vocabulary, resolvable
## endpoints), every cmr-refs: marker resolves, and the spine rebuilds
## deterministically; the check mutates its own input, so it cannot pass
## vacuously
cross-reference:
	@bash scripts/check-cross-reference.sh

## ticket — ticket projection (M26 #401): the paperclip ticket is the single
## join node (ADR-0014) — the board snapshot, the claim ledger, the lessons
## register, the budget rail and the gate attestation are projected into one
## deterministic, rebuildable ticket graph; one writer per field is enforced
## against contract v2's frozen authority{} map, an unresolvable reference fails
## naming the file and line, and the check provokes each refusal for real, so it
## cannot pass vacuously
ticket:
	@bash scripts/check-ticket-projection.sh

## pmo — PMO rollup + RAID as derived views over the ticket graph (#403,
## CMR PROGRAM-MANAGEMENT): deps/lanes/report/raid/aging are queries over the
## ticket projection, never a store — no new ledger, no cache that survives a
## rebuild, no second source of status; every view runs offline and exits
## tri-state, and the check provokes each refusal (owner-less risk, a RAID set
## that disagrees with the graph, a rollup from a stale cache) for real
pmo:
	@bash scripts/check-pmo-rollup.sh

## pmo-dispatch — the priority/dispatch engine over the same ticket graph
## (issue #403 follow-on): `priority` derives one explainable score per open
## task (P-level + aging tier + blocking fan-out + unblocked-readiness + owner
## capacity, every term cited to its ledger source in governance/pmo/policy.yaml,
## an SLA term with no committed ledger carried as `unsourced` rather than
## fabricated); `dispatch` turns the top of that order into a lane-collision-free
## wave, each task attached to an SME profile, model tier and Agent brief
## skeleton. Read-only by default — `--apply` (never run here) is the only path
## that writes anything, and it writes one idempotent PMO comment + label.
pmo-dispatch:
	@python3 governance/pmo/cli.py priority
	@python3 governance/pmo/cli.py dispatch --wave 1

## conformance — CMR class/pattern/template enforcement (issue #140): every
## milestoned issue must be classified, and the class it declares must hold
conformance:
	@bash scripts/check-conformance.sh

## conformance-change-set — check the current diff against the mandates
## (GR-15 code-native automation; new infrastructure ships flag-gated OFF)
conformance-change-set:
	@python3 governance/conformance/cli.py change-set

## surface-class — per-surface target solution-class enforcement (issue #351):
## every product surface declares a rung of the CMR ladder and the gate fails
## while a surface sits below its declaration; a declared class raised above the
## surface's evidence is refused by name (self-mutating negative control)
surface-class:
	@bash scripts/check-surface-class.sh

## remediation — auto-generate violator remediation issues from policy drift
## (issue #142): board conformance findings must produce a well-formed
## remediation payload (title/labels/owner-lane/corrective-steps/policy-ref/
## evidence) or the scan itself must resolve; only CANNOT-ASSESS fails the
## gate, the same reasoning check-conformance.sh already applies (a calibrated
## backlog of deviations is reported, not a red gate no lane can fix alone)
remediation:
	@bash scripts/check-remediation.sh

## remediation-scan — auto-generate violator remediation issues from policy
## drift (issue #142): runs the conformance board + change-set checks and
## turns each finding into a remediation-issue payload (title, labels, owner
## lane, corrective steps, policy reference, evidence), deduped/merged across
## repeats, writes .verify/remediation-report.json. Offline (no GitHub I/O) —
## the "on changes" hook; run it after any change that could drift the board.
remediation-scan:
	@python3 governance/remediation/cli.py scan

## remediation-dispatch — scan, then create/update the actual GitHub issues
## for anything not already tracked, escalating SLA/severity breaches to the
## governance board (the ops runner invokes this on its schedule, the same
## precedent as knowledge-index-build — network-touching, so it stays a make
## target rather than a new GitHub Actions workflow, GR-15).
remediation-dispatch:
	@python3 governance/remediation/cli.py route --apply

## lessons — RCA + lessons enforcement (issue #141): an incident with no RCA,
## an RCA with no traceable origin or corrective action, an action that is not
## recorded or names no owner, and a closed lesson with no commit evidence all
## fail the gate; the historical backlog is reported with its remediation
lessons:
	@bash scripts/check-lessons.sh

## paperclip-integration — integration seam gate (#370, M26): the mode decision
## (ADR-0013), its index row, the seam doc's heartbeat/ticket/budget contracts
## and their JSON Schemas are enforced, with a self-mutating negative control.
paperclip-integration:
	@bash scripts/check-paperclip-integration.sh

## paperclip-adapter — the paperclip.ing adapter gate (#428, ADR-0013): the
## mapper's heartbeat/ticket/budget records must conform to the three seam
## schemas and the client's request shapes (kept offline through the fixture
## transport) must hold; a dropped required field or a value outside a closed
## vocabulary is refused by name, with a self-mutating negative control.
paperclip-adapter:
	@bash scripts/check-paperclip-integration-adapter.sh

## paperclip-canonical-module — the canonical-home guard for the paperclip
## boundary adapter (issue #448): integrations/paperclip/ is the single canonical
## module and a second top-level paperclip module is refused by name (with a
## self-mutating negative control, so the guard cannot pass vacuously).
paperclip-canonical-module:
	@bash scripts/check-paperclip-canonical-module.sh

## paperclip — convenience target (issue #420, the wiring lane): run every
## paperclip boundary/adapter gate (scripts/check-paperclip-*.sh) in one shot.
## Each gate keeps its OWN name in scripts/verify.sh's checks=() array — this
## target is for fast local iteration, not a second gate of record. It runs in
## a subshell with `set -e`, so the first gate that fails stops the run.
paperclip:
	@set -e; for s in scripts/check-paperclip-*.sh; do \
		printf '== %s ==\n' "$$s"; \
		bash "$$s"; \
	done; \
	echo "paperclip: OK"

## diagrams — the diagrams blueprint consumer surface (EPIC #461, GR-18): the
## read-only ADR-0017 evidence[] projection gate (#465) and the SSOT
## declaration-conformance gate (#464), in one shot. Each keeps its OWN name in
## scripts/verify.sh's checks=() array — this target is for fast local
## iteration, not a second gate of record. (The declaration gate is
## CANNOT-ASSESS until vendor/CMR is initialised: git submodule update --init
## vendor/CMR.)
diagrams:
	@bash scripts/check-paperclip-diagrams.sh
	@bash scripts/check-diagrams-declaration.sh

## codeidx — the consumed codeidx indexer surface (EPIC #472): the mandatory
## root .mcp.json asset and its gdc-manifest pin (#475), the flag-gated real
## backend seam (#476), and the pre-fetched context-pack consumption (#477), in
## one shot. Each keeps its OWN name in scripts/verify.sh's checks=() array —
## this target is for fast local iteration, not a second gate of record. (The
## surface gate derives its expectation from the vendored seed, so it is
## CANNOT-ASSESS until vendor/CMR is initialised:
## git submodule update --init vendor/CMR.)
codeidx:
	@bash scripts/check-codeidx-surface.sh
	@bash scripts/check-codeidx-backend.sh
	@bash scripts/check-context-pack-consumption.sh

## monitoring-declaration — the monitoring declaration surface (EPIC #494;
## issue #499 is the wiring lane): the repo's monitoring integration
## (`module.json`, the flat {id,type} shape ADR-0022 D3 froze, no invented pin
## key) and its producer/consumer boundary (`docs/OBSERVABILITY.md`) must BOTH
## be declared; the check stages a deliberately damaged scratch copy of each and
## requires it to be refused BY NAME, so it cannot pass vacuously
monitoring-declaration:
	@bash scripts/check-monitoring-declaration.sh

## capability-registers — the capability-register surfaces (EPIC #462 diagrams,
## EPIC #473 codeidx): the two register-grammar enforcement gates (#469, #480)
## and the two board-reconciliation trackers (#468, #479), in one shot. Each
## keeps its OWN name in scripts/verify.sh's checks=() array — this target is
## for fast local iteration, not a second gate of record. Unlike the `diagrams`
## and `codeidx` targets above, these four are offline and deterministic: no
## network, no `vendor/CMR` submodule and no vendored seed, so they run for real
## in a fresh worktree rather than reporting CANNOT-ASSESS.
capability-registers:
	@bash scripts/check-diagrams-capability-register.sh
	@bash scripts/track-diagrams-capabilities.sh
	@bash scripts/check-codeidx-capability-register.sh
	@bash scripts/track-codeidx-capabilities.sh

## chat — the conversational surface (EPIC #500, M30): the six chat gates
## delivered by the sibling lanes — #504 (grounding, the read-only enterprise
## tool family), #505 (identity), #506 (FinOps), #507 (guardrails), #508 (UX)
## and #509 (eval) — in one shot. Each keeps its OWN name in scripts/verify.sh's
## checks=() array; this target is for fast local iteration, not a second gate
## of record. All six are offline and deterministic (no network, no `vendor/CMR`
## submodule), so they run for real in a fresh worktree rather than reporting
## CANNOT-ASSESS. scripts/check-chat-surface.sh, the seventh gate named by
## issue #502, is NOT listed among them here: it is owned by #503, which has
## since shipped it (PR #564), and issue #568 wired it into the gate of record
## (scripts/verify.sh checks=()), so it runs for real on every `make verify`.
## This target stays the six-gate convenience runner, not a second gate.
chat:
	@bash scripts/check-chat-tools.sh
	@bash scripts/check-chat-identity.sh
	@bash scripts/check-chat-finops.sh
	@bash scripts/check-chat-guardrails.sh
	@bash scripts/check-chat-ux.sh
	@bash scripts/check-chat-eval.sh

## paperclip-auth — cross-boundary auth for the paperclip seam (issue #412,
## ADR-0013/ADR-0012): agent identity is minted/verified from the fleet's own
## registry (no second identity store), human identity maps onto the board
## session path, and the upstream X-Paperclip-Run-Id is bridged to the fleet's
## correlation_id; every negative control (expired/unknown/wrong-company /
## replayed run id / missing Authorization / authenticated-but-not-allowed, the
## last a 403 never a 404) is provoked and refused by name, so the gate cannot
## pass vacuously.
paperclip-auth:
	@bash scripts/check-paperclip-auth.sh

## board-gate — governance board enforcement gate (issue #143): re-runs
## knowledge-index, conformance, lessons and remediation for real and refuses
## to report ok unless every one of them does; applies timeboxed board
## exceptions and escalates repeated violations (see governance/board/CHARTER.md)
board-gate:
	@python3 governance/board/cli.py check

## cross-repo-boundary — cross-repo execution boundary (issue #388): the main
## board snapshot carries no body, so this gate reads the committed
## boundary-specific export and refuses a foreign-repo backlog item filed here
## (NG4); the 11 legacy children are quarantined by name while their tracker is
## open, and a snapshot whose records lack a body is CANNOT-ASSESS, never OK
cross-repo-boundary:
	@bash scripts/check-cross-repo-boundary.sh

## control-verbs — the control-verb vocabulary is closed and cross-referenced (RC-2 #553)
control-verbs:
	@bash scripts/check-control-verbs.sh

## control-audit — exactly-once control with the audit record + refusal path (RC-4 #555)
control-audit:
	@bash scripts/check-control-audit.sh

## control-functions — every cockpit function declared once (RC-10 #565)
control-functions:
	@bash scripts/check-control-functions.sh

## operator-access — the operator way in (issue #763): docs/OPERATOR-ACCESS.md
## names every operator surface with the exact command that reaches it (the A2A
## control channel, the override terminal's 18 verbs, `make operator`, the
## browser console and its auth requirement), the two targets exist and delegate
## to the real implementation, `make operator` FAILS LOUDLY when the box cannot
## host the live view (no tmux) instead of printing a success it cannot
## evidence, and the console's loopback default + fail-closed session check are
## driven rather than asserted. Each surface is stripped from a copy in turn and
## the gate must notice, so it cannot pass vacuously.
operator-access:
	@bash scripts/check-operator-access.sh

## ao-ssh-access — the remote operator SSH route (issue #771): the ingress merge
## must never drop a live rule (mutation-proved against a neutered copy), a dry
## run must send no mutating request (driven against a read-only stub), no estate
## identifier, email or token may be hardcoded (a missing id is refused by name),
## and `--apply` must refuse while the surface ships flag-gated OFF (GR-5).
## Offline: the stub binds loopback and no Cloudflare call is ever made.
ao-ssh-access:
	@bash scripts/check-ao-ssh-access.sh
## operator-terminal — the browser IT-terminal behind the SSO session (issue
## #774): one link (`/console`) that composes the fleet projection (read) and
## the closed control-verb family (steer) behind the caller's own
## os-session-token. Ships flag-gated OFF (surfaces.operator_terminal), so while
## the flag is off `/console` answers 404 feature_disabled before AuthN. The
## check proves the flag gate, the session reuse, the offline read half and the
## closed steer half (out-of-vocabulary refused, capability-less refused with no
## audit, allowed steer audited) — each provoked, so it cannot pass vacuously.
operator-terminal:
	@bash scripts/check-operator-terminal.sh

## cockpit — the terminal cockpit (RC-11 #566): one frame, then exit. A client
## of the RC-3 API and the authenticated SSE streams that renders only what the
## declared function registry names; ships flag-gated OFF (surfaces.cockpit),
## so while the flag is off this target exits non-zero with the named FLAG_OFF
## condition -- an unpromoted surface is absent, never silently healthy.
cockpit:
	@python3 control-plane/cockpit/cockpit/__main__.py --once

## audit-read-model — the read-only, filterable audit read model (issue #347):
## the tamper-evident trail served as a deterministic read model with
## verify-chain semantics; an intact chain is OK, a modified / reordered /
## removed / truncated record is NOT-OK named by position, an unparseable chain
## is CANNOT-ASSESS (never a pass), an unknown filter field is refused, and the
## module exposes no write path (proved by parsing its source). Every tamper is
## provoked on a temp copy with a self-mutating negative control.
audit-read-model:
	@bash scripts/check-audit-read-model.sh

## secrets — mechanical secret scan (always on, no external tool dependency)
secrets:
	@bash scripts/check-secrets.sh

## feature-flags — feature-flag registry: every surface defaults OFF (issue #6)
feature-flags:
	@python3 scripts/check-feature-flags.py

## cloudbuild — infra/cloudbuild YAML parses; every trigger ships disabled (issue #6)
cloudbuild:
	@bash scripts/check-cloudbuild.sh

## terraform — infra/terraform fmt + offline validate (issue #6; SKIP if absent)
terraform:
	@bash scripts/check-terraform.sh all

## web-image-dryrun — validate the web-surface image build config with no
## push and no GCP mutation (issue #606/#607, gap 1). `gcloud builds submit`
## has NO `--dry-run` flag (verified: `gcloud builds submit --help` lists no
## such option) — invoking it at all submits a real build, which this target
## must never do (GR-5, no ad-hoc apply/deploy). So this target only (1)
## validates infra/cloudbuild/web-image.yaml parses and its trigger ships
## disabled (scripts/check-cloudbuild.sh, already in `make verify`), then (2)
## proves portal/Dockerfile actually builds with a local `docker build` —
## no push, no Artifact Registry, no gcloud call.
web-image-dryrun:
	@echo "== web-image-dryrun: config validation (no GCP call) =="
	@bash scripts/check-cloudbuild.sh
	@if command -v docker >/dev/null 2>&1; then \
		echo "== web-image-dryrun: local docker build (proves portal/Dockerfile builds; no push) =="; \
		docker build --file=portal/Dockerfile --tag=web-surface:dryrun-local .; \
	else \
		echo "== web-image-dryrun: docker absent, config validation only =="; \
	fi

## tf-fmt — terraform fmt -check -recursive on infra/terraform
## (visible SKIP if the terraform binary is not installed)
tf-fmt:
	@bash scripts/check-terraform.sh fmt

## tf-validate — offline terraform validate of infra/terraform
## (visible SKIP if terraform is absent or no local provider cache exists)
tf-validate:
	@bash scripts/check-terraform.sh validate

## scratch-safety — scratch-space guard (issue #488). A single 14.8 GB agent
## scratch log filled this box's /tmp, a 16 GB tmpfs (RAM), and stopped every
## parallel lane at once; the knock-on was worse than the disk, because `cp`
## wrote a 0-byte "backup" and restoring from it truncated a source file to
## empty. This check refuses each defect BY NAME (SCRATCH-SELF-APPEND,
## SCRATCH-FILE-OVERSIZE, SCRATCH-SPACE-NEAR-FULL, SCRATCH-TMP-WORKTREE,
## SCRATCH-EMPTY-COPY), proves every refusal with its own provoked control, and
## lints the repo's tracked *.sh — so it cannot pass vacuously. The live machine
## verdict it prints is ADVISORY: the machine-level guard
## (~/laptop-manage/bin/scratch-guard, on a timer) owns that verdict.
scratch-safety:
	@bash scripts/check-scratch-safety.sh

## shellcheck — optional lint (not part of verify; skipped if not installed)
shellcheck:
	@if command -v shellcheck >/dev/null 2>&1; then \
		shellcheck scripts/*.sh; \
	else \
		echo "shellcheck: not installed (skipped)"; \
	fi

## gitleaks — optional secrets scan with .gitleaks.toml (not part of verify)
gitleaks:
	@if command -v gitleaks >/dev/null 2>&1; then \
		gitleaks detect --no-git --source . -c .gitleaks.toml --redact; \
	else \
		echo "gitleaks: not installed (skipped)"; \
	fi

## pre-commit — optional, runs all pre-commit hooks (not part of verify)
pre-commit:
	@if command -v pre-commit >/dev/null 2>&1; then \
		pre-commit run --all-files; \
	else \
		echo "pre-commit: not installed (skipped)"; \
	fi

## install-hooks — OPT IN: install scripts/git-hooks/pre-commit into this checkout
## (issue #1541). That hook refuses a commit that stages a file leased to another
## live claim — the git front-end of the claim/file-lease system in
## governance/dispatch. Installing a hook changes what `git commit` does for EVERY
## commit here, so nothing installs it as a side effect (`make verify` does not), and
## this target is a DRY RUN until you ask for the write:
##   make install-hooks                      # show exactly what would be installed
##   AO_HOOKS_APPLY=1 make install-hooks     # install it
## The destination defaults to this checkout's COMMON hooks dir (`git rev-parse
## --git-common-dir`/hooks), which is the dir git reads for every linked worktree of
## it; HOOKS_DEST overrides it. An existing hook that is not this repo's is never
## overwritten unless AO_HOOKS_FORCE=1. The hook FAILS OPEN (docs/LEASE-HOOK.md).
## Exit codes: 0 OK / 1 NOT-OK (a foreign hook would be clobbered) / 2 CANNOT-ASSESS.
HOOKS_SRC ?= scripts/git-hooks/pre-commit
HOOKS_DEST ?=
install-hooks:
	@src="$(HOOKS_SRC)"; \
	common="$$(git rev-parse --git-common-dir 2>/dev/null)" || common=".git"; \
	dest="$(HOOKS_DEST)"; \
	if [ -z "$$dest" ]; then dest="$$common/hooks"; fi; \
	target="$$dest/pre-commit"; \
	if [ ! -f "$$src" ]; then \
		echo "install-hooks: CANNOT-ASSESS — $$src does not exist" >&2; exit 2; \
	fi; \
	if [ "$(AO_HOOKS_APPLY)" != "1" ]; then \
		echo "install-hooks: DRY RUN (nothing written) — a git hook changes what every commit here does"; \
		echo "  would install: $$src -> $$target"; \
		echo "  that is git's COMMON hooks dir (git rev-parse --git-common-dir): the hook then"; \
		echo "  fires for every linked worktree of this checkout, not just this one"; \
		if [ -f "$$target" ]; then \
			echo "  note: $$target already exists (sha256 $$(sha256sum "$$target" | cut -c1-12))"; \
		fi; \
		echo "  carry on with: AO_HOOKS_APPLY=1 make install-hooks"; \
		exit 0; \
	fi; \
	mkdir -p "$$dest" || exit 1; \
	if [ -f "$$target" ] && [ "$(AO_HOOKS_FORCE)" != "1" ] && ! cmp -s "$$target" "$$src"; then \
		echo "install-hooks: REFUSED — $$target exists and is not this repo's hook; refusing to clobber it" >&2; \
		echo "  move it aside, or re-run with AO_HOOKS_FORCE=1" >&2; \
		exit 1; \
	fi; \
	cp "$$src" "$$target" || exit 1; \
	chmod 0755 "$$target" || exit 1; \
	echo "install-hooks: installed $$target"; \
	echo "  (git's COMMON hooks dir — this hook now fires for every linked worktree of this checkout)"; \
	echo "  bypass: git commit --no-verify, or AO_LEASE_HOOK_OVERRIDE=\"<why>\" (docs/LEASE-HOOK.md)"

.PHONY: install-hooks

## land — land ONE issue's lane end to end (issue #764): push -> PR (Closes #<n>,
## AI-assistance declared) -> pre-merge contract -> merge decision -> squash-merge
## -> branch delete -> lifecycle close. The code-native path the ops runner and
## cron drive: no console, no human step, and never a workflow file (GR-15).
## DRY RUN BY DEFAULT — it prints exactly what it would do and changes nothing
## remotely; a real landing requires the explicit opt-in AO_LAND_APPLY=1.
## The merge is refused unless the pre-merge attestation is green AND names the
## commit being merged (governance/merge decides; scripts/check-landing.sh proves
## the refusal). Re-running on a landed lane is a no-op that reports the terminal
## state. Exit codes: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
##   make land ISSUE=764                     # dry run: show the plan
##   AO_LAND_APPLY=1 make land ISSUE=764     # land it for real
## PR=<n> instead of ISSUE=<n> lands an ALREADY-OPEN PR under the
## single-developer method (issue #1675, AGENTS.md rule 10): squash-guard ->
## gh pr merge --squash (close-out is automatic via the PR's own Closes
## trailer) -> reclaim (scripts/land.sh). No verify/attest step in this path
## (advisory, other venue).
##   make land PR=1675                       # dry run
##   AO_LAND_APPLY=1 make land PR=1675       # land it for real
land:
	@if [ -n "$(PR)" ]; then \
		bash scripts/land.sh "$(PR)"; \
	else \
		bash scripts/land-lane.sh --issue "$(ISSUE)"; \
	fi

.PHONY: land

## master-attestation — publish master's own health after a green `make
## verify` run at origin/master's head (RCA 2026-09-17 fix #5 follow-up,
## #1114): `make land` already writes this after every FLEET-DRIVEN merge,
## but an OPERATOR's manual `gh pr merge` never goes through `land()`, so
## master's head can move with nothing publishing a fresh verdict —
## fleet/brain.py's dispatch pre-check then reads a stale-head attestation as
## CANNOT-ASSESS until the next fleet land. Run this right after `make
## verify` while the checkout still sits at origin/master's head; it is a
## no-op (prints why, exits 0) on a lane head or a red verify. NOT wired into
## `make verify`/scripts/verify.sh itself — that file is held by an open PR
## (#1127) at the time this target was added; this is the seam a follow-up
## hooks scripts/verify.sh into once #1127 lands.
##   make verify-attestation && make master-attestation
master-attestation:
	@python3 governance/landing/cli.py write-master-attestation --attestation "$(ATTESTATION)"

.PHONY: master-attestation
