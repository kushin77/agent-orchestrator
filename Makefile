
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
issue-claims issue-template fleet-channel finops-chooser fleet-contract fleet-runbook fleet-deploy-locally conformance surface-class

## gate-of-record: make verify
## This composite gate orchestrated by scripts/verify.sh is the gate of record.
## Each of its named check targets (shell-syntax, json-lint, …) is orchestrated by
## scripts/verify.sh and named in REQUIRED_CHECKS via gate.py; the check can fail,
## can be scoped, and can acquire exceptions. Read gate.py for the exception carve-outs.
## https://github.com/kushin77/agent-orchestrator/issues/143

verify:
	@bash scripts/verify.sh $(ATTESTATION)

lint: shell-syntax python-syntax yaml-lint json-lint docs-lint

## shell-syntax: bash -n on every .sh file
shell-syntax:
	@bash scripts/check-shell-syntax.sh

## python-syntax: python -m py_compile on every .py file + import graph consistency
python-syntax:
	@bash scripts/check-python-syntax.sh

## yaml-lint: yamllint every .yaml/.yml file
yaml-lint:
	@bash scripts/check-yaml-lint.sh

## json-lint: validate every .json/.jsonc/.json5 file
json-lint:
	@bash scripts/check-json-lint.sh

## docs-lint: orphaned docs, dangling links, stale example commands
docs-lint:
	@bash scripts/check-docs.sh

## gate-coverage: are all gate checks exercised in verify.sh?
gate-coverage:
	@bash scripts/check-gate-coverage.sh

## issue-claims: does the board reflect what the issues claim?
issue-claims:
	@bash scripts/check-board-mirror.sh

## codeowners: .github/CODEOWNERS covers every lane
codeowners:
	@bash scripts/check-codeowners.sh

## squash-message: lint git squash-message for a clean history
squash-message:
	@bash scripts/check-squash-message.sh

## chronological-dispatch: do issues belong to the lane they are assigned?
chronological-dispatch:
	@bash scripts/check-chronological-dispatch.sh

## issue-template: issues are filed with all required fields
issue-template:
	@bash scripts/check-issue-template.sh

## fleet-channel: is the fleet registry current?
fleet-channel:
	@bash scripts/check-fleet-registry.sh

## finops-chooser: is a model choice operator-aware?
finops-chooser:
	@bash scripts/check-finops-chooser.sh

## fleet-contract: does the contract match the implementation?
fleet-contract:
	@bash scripts/check-fleet-contract.sh

## fleet-runbook: can a brand-new laptop run every step?
fleet-runbook:
	@bash scripts/check-fleet-runbook.sh

## fleet-deploy-locally: staging gate for the deploy flow (see docs/OPERATOR.md)
fleet-deploy-locally:
	@bash scripts/check-fleet-deploy-locally.sh

## conformance: the board and the filing path
conformance:
	@bash scripts/check-board-conformance.sh

## surface-class: does each surface sit at or above its declared class?
surface-class:
	@bash scripts/check-surface-class.sh

## gate: _gate is the composed gate — the sum of every individual check
gate: verify

## merge-gate: pre-merge validation gate (issue #143, stage 18)
##
## This is the final gate before a merge lands. It is NOT supposed to be run
## inside the PR/CI context — it is run at merge time, against the working tree
## of the person/agent/operator merging.
##
## This gate must NOT fail on deviations that are *reported* (remediation
## issues exist to fix them) — it can only fail on *errors* (no-false-green
## doctrine). The distinction is: would an operator manually fix it right now,
## or is there an automated lane assigned to fix it later?
##
## Note: a calibrated board is green even when there are open deviations. When
## an issue's declared class cannot hold (missing a `pillar:` it expects), the
## gate *reports* it (not a red X), and a remediation ticket owns walking it
## down — the same reasoning `conformance.sh` already applies (a calibrated
## backlog of deviations is reported, not a red gate no lane can fix alone).
##
## Mechanics:
##   1. shell-syntax: run on all shell scripts in the tree
##   2. python-syntax: parse all Python files, check imports
##   3. yaml-lint: all YAML files
##   4. json-lint: all JSON files
##   5. docs-lint: the docs tree for orphans and dangling links
##   6. the board: is in-scope work classified, and does each class hold?
##   7. the filing path: can an unclassified issue still be filed? (#320)
##   8. the lessons: RCA entries complete, lessons learnt, traces logged
##   9. remediation tickets: every violation is tracked, owned, and scheduled
##  10. issue-claims: the board (as GitHub sees it) matches what the repo claims
##  11. codeowners: every file in the tree is routed to a named owner
##  12. squash-message: the Makefile's `.gitmessage` contract holds
##
## So far it is RED when shell/python/yaml/json fail (actual syntax errors), when
## the board cannot be read, when the filing path is broken, when a lesson is
## missing its trace, when a remediation issue is missing (violation w/o owner),
## when the board and GitHub disagree, or when codeowners is incomplete.

merge-gate: verify

qa-loop:
	@for file in scripts/check-*.sh; do \
		echo "Testing $$file..." && bash "$$file" || exit 1; \
	done

tests:
	@python3 -m pytest -xvs

e2e:
	@bash e2e/run.sh

fleet-parity:
	@bash scripts/check-fleet-parity.sh

shell:
	@bash

.DEFAULT_GOAL := help

help:
	@printf 'Usage:\n  make [target]\n\nTargets:\n'
	@grep -E '^##' Makefile | sed 's/^## /  /'

# Use this to update (rather than append) the board report after changes land.
board-project:
	@bash scripts/update-board-project.sh
