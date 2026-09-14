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

.PHONY: help verify lint gate merge-gate qa-loop tests \
        shell-syntax python-syntax yaml-lint json-lint docs-lint chronological-dispatch \
        issue-claims issue-template fleet-channel finops-chooser fleet-contract fleet-runbook session-isolation github-lifecycle reconcile lease-policy fleet-state knowledge-index knowledge-index-build paperclip-gap-analysis paperclip-integration paperclip-adapter cross-reference cross-repo-boundary \
        brain-profile conformance lessons secrets feature-flags cloudbuild terraform tf-fmt \
        tf-validate shellcheck gitleaks pre-commit worktrees \
        remediation remediation-scan remediation-dispatch

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
	@echo "  shellcheck    Run shellcheck on scripts/ (skipped if not installed)"
	@echo "  gitleaks      Run gitleaks with .gitleaks.toml (skipped if absent)"
	@echo "  pre-commit    Run pre-commit on all files (skipped if absent)"
	@echo ""
	@echo "Fine-grained checks (used by verify):"
	@echo "  shell-syntax  bash -n on every *.sh outside vendor/"
	@echo "  python-syntax py_compile on every tracked *.py outside vendor/ —"
	@echo "                a syntax-broken fleet module must not pass the gate"
	@echo "  yaml-lint     Parse every .yml/.yaml outside vendor/ (PyYAML)"
	@echo "  json-lint     Validate every *.json outside vendor/"
	@echo "  docs-lint     Foundation files + md links + whitespace + markers"
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
	@echo "  conformance   CMR class/pattern/template enforcement (#140): every"
	@echo "                milestoned issue classified; mandates checked on the diff"
	@echo "  lessons       RCA + lessons enforcement (#141): every incident has an"
	@echo "                RCA, every action recorded, every lesson evidenced"
	@echo "  fleet-state   Unified fleet-state projection (#323): lanes + sessions +"
	@echo "                claims + journals + directives joined per item; exits"
	@echo "                non-zero when anything is orphaned, shelved or wedged"
	@echo "  secrets       Mechanical secret scan (always on)"
	@echo "  worktrees     Reclaim stale lane worktrees (dry run; --apply via"
	@echo "                scripts/prune-worktrees.sh). Keeps dirty, in-use and"
	@echo "                unpreserved worktrees; /tmp ones are the RAM hazard."
	@echo "  feature-flags Feature-flag registry: every surface defaults OFF"
	@echo "  cloudbuild    infra/cloudbuild YAML parses; triggers ship disabled"
	@echo "  terraform     infra/terraform fmt + offline validate (SKIP if absent)"
	@echo "  tf-fmt        terraform fmt -check only"
	@echo "  tf-validate   offline terraform validate only"

## verify — gate of record (orchestrated by scripts/verify.sh, with attestation)
verify:
	@bash scripts/verify.sh verify

## worktrees — reclaim stale lane worktrees (dry run by default)
worktrees:
	@bash scripts/prune-worktrees.sh
## lint — shell + YAML + JSON + docs (no secret scan)
lint: shell-syntax python-syntax yaml-lint json-lint docs-lint chronological-dispatch issue-claims issue-template fleet-channel finops-chooser fleet-contract fleet-runbook session-isolation github-lifecycle reconcile lease-policy fleet-state brain-profile knowledge-index lessons
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

## shell-syntax — bash -n on every *.sh outside vendor/
shell-syntax:
	@bash scripts/check-shell-syntax.sh

## yaml-lint — parse every .yml/.yaml outside vendor/ with PyYAML
yaml-lint:
	@python3 scripts/check-yaml.py

## json-lint — validate every *.json outside vendor/
json-lint:
	@bash scripts/check-json.sh

## docs-lint — foundation files, markdown links, whitespace, unfinished markers
docs-lint:
	@bash scripts/check-docs.sh

## chronological-dispatch — governance docs must declare dependency-ordered
## issue selection (GR-20); a doc-only rule is advisory, so this gate fails it
chronological-dispatch:
	@bash scripts/check-chronological-dispatch.sh

## issue-claims — claim-time order enforcement (issue #157): the ledger is
## replayed against the committed board snapshot and every violation fails; the
## audit always runs its own mutants, so it cannot pass vacuously
issue-claims:
	@bash scripts/check-issue-claims.sh

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

## paperclip-gap-analysis — sourced paperclip.ing gap analysis (issue #368): the
## GR-10 provenance, the four-way namesake disambiguation, the six capability
## families and the cannibalize-vs-build table must all be present, and the check
## mutates its own input, so it cannot pass vacuously
paperclip-gap-analysis:
	@bash scripts/check-paperclip-gap-analysis.sh


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

## cross-reference — cross-reference spine (EPIC #138, issue #384): the
## catalogue's typed relationships are valid (closed vocabulary, resolvable
## endpoints), every cmr-refs: marker resolves, and the spine rebuilds
## deterministically; the check mutates its own input, so it cannot pass
## vacuously
cross-reference:
	@bash scripts/check-cross-reference.sh

## conformance — CMR class/pattern/template enforcement (issue #140): every
## milestoned issue must be classified, and the class it declares must hold
conformance:
	@bash scripts/check-conformance.sh

## conformance-change-set — check the current diff against the mandates
## (GR-15 code-native automation; new infrastructure ships flag-gated OFF)
conformance-change-set:
	@python3 governance/conformance/cli.py change-set

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

## tf-fmt — terraform fmt -check -recursive on infra/terraform
## (visible SKIP if the terraform binary is not installed)
tf-fmt:
	@bash scripts/check-terraform.sh fmt

## tf-validate — offline terraform validate of infra/terraform
## (visible SKIP if terraform is absent or no local provider cache exists)
tf-validate:
	@bash scripts/check-terraform.sh validate

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
